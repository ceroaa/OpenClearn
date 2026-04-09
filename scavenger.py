from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ = timezone(timedelta(hours=8))


def now_dt() -> datetime:
    return datetime.now(TZ)


def now_iso() -> str:
    return now_dt().isoformat()


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default or {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default or {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def choose_better_snapshot(a: dict, b: dict) -> dict:
    a_key = (
        int(a.get("refresh_count", 0)),
        int(a.get("strength_score", 0)),
        str(a.get("last_refreshed_at", "")),
    )
    b_key = (
        int(b.get("refresh_count", 0)),
        int(b.get("strength_score", 0)),
        str(b.get("last_refreshed_at", "")),
    )
    return a if a_key >= b_key else b


def dedupe_snapshots(snapshots: list[dict]) -> tuple[list[dict], list[dict]]:
    by_key: dict[tuple[str, str], dict] = {}
    removed: list[dict] = []
    for row in snapshots:
        key = (str(row.get("line_id", "")), str(row.get("capability_signature", "")))
        existing = by_key.get(key)
        if not existing:
            by_key[key] = row
            continue
        keep = choose_better_snapshot(existing, row)
        drop = row if keep is existing else existing
        by_key[key] = keep
        removed.append(drop)
    return list(by_key.values()), removed


def reap_misc_residue(snapshots: list[dict], stale_days: int) -> tuple[list[dict], list[dict]]:
    cutoff = now_dt() - timedelta(days=stale_days)
    kept: list[dict] = []
    removed: list[dict] = []
    for row in snapshots:
        if row.get("line_id") != "misc_line":
            kept.append(row)
            continue
        refreshed = parse_ts(str(row.get("last_refreshed_at", "")))
        refresh_count = int(row.get("refresh_count", 0))
        stale = refreshed is None or refreshed < cutoff
        if stale and refresh_count <= 1:
            removed.append(row)
        else:
            kept.append(row)
    return kept, removed


def dedupe_external_samples(samples: list[dict]) -> tuple[list[dict], list[dict]]:
    by_id: dict[str, dict] = {}
    removed: list[dict] = []
    for row in samples:
        sid = str(row.get("sample_id", ""))
        if sid not in by_id:
            by_id[sid] = row
            continue
        prev = by_id[sid]
        prev_ts = str(prev.get("discovered_at", ""))
        cur_ts = str(row.get("discovered_at", ""))
        if cur_ts > prev_ts:
            by_id[sid] = row
            removed.append(prev)
        else:
            if len(str(row.get("tool_purpose", ""))) > len(str(prev.get("tool_purpose", ""))):
                by_id[sid] = row
                removed.append(prev)
            else:
                removed.append(row)
    merged = list(by_id.values())
    merged.sort(key=lambda x: str(x.get("sample_id", "")))
    return merged, removed


def rollback_stale_trials_guarded(
    records: list[dict],
    trial_timeout_hours: int,
    max_rollbacks: int,
    protect_keywords: list[str],
) -> tuple[list[dict], list[dict]]:
    changed: list[dict] = []
    now = now_dt()
    cutoff = now - timedelta(hours=trial_timeout_hours)

    for row in records:
        if len(changed) >= max_rollbacks:
            break
        if str(row.get("status", "")) != "active_trial":
            continue

        marker = " ".join(
            [
                str(row.get("adjustment_id", "")),
                str(row.get("source_round", "")),
                str(row.get("reason", "")),
            ]
        ).lower()
        if any(k.lower() in marker for k in protect_keywords):
            continue

        trial_end = parse_ts(str(row.get("trial_end", "")))
        ts = parse_ts(str(row.get("timestamp", "")))
        stale = False
        if trial_end:
            stale = trial_end <= now
        elif ts:
            stale = ts <= cutoff
        if not stale:
            continue

        row["status"] = "rolled_back"
        row["evaluation_result"] = "scavenger_timeout_rollback"
        row["rollback_reason"] = "scavenger_stale_active_trial_timeout"
        row["updated_at"] = now_iso()
        changed.append(
            {
                "adjustment_id": row.get("adjustment_id"),
                "source_round": row.get("source_round"),
                "previous_status": "active_trial",
                "new_status": "rolled_back",
            }
        )
    return records, changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stale-days", type=int, default=None)
    parser.add_argument("--trial-timeout-hours", type=int, default=None)
    parser.add_argument("--max-rollbacks", type=int, default=None)
    args = parser.parse_args()

    config = load_json(Path(args.config), {})
    root = Path(config.get("root", ".")).resolve()

    stale_days = args.stale_days if args.stale_days is not None else int(config.get("stale_days", 7))
    trial_timeout_hours = (
        args.trial_timeout_hours
        if args.trial_timeout_hours is not None
        else int(config.get("trial_timeout_hours", 168))
    )
    max_rollbacks = args.max_rollbacks if args.max_rollbacks is not None else int(config.get("max_rollbacks", 50))
    protect_keywords = list(config.get("protect_keywords", ["OPENSPACE-"]))

    snap_path = root / str(config["snapshot_file"])
    ext_path = root / str(config["external_specimen_file"])
    adjust_path = root / str(config["structure_adjustment_file"])
    state_path = root / str(config["state_file"])
    report_path = root / str(config["report_jsonl"])

    snap_payload = load_json(snap_path, {"version": "v1", "updated_at": None, "snapshots": []})
    ext_payload = load_json(ext_path, {"updated_at": None, "samples": []})
    adjust_payload = load_json(adjust_path, {"records": []})

    snapshots = list(snap_payload.get("snapshots", []))
    samples = list(ext_payload.get("samples", []))
    records = list(adjust_payload.get("records", []))

    snapshots_1, removed_dup_snap = dedupe_snapshots(snapshots)
    snapshots_2, removed_misc = reap_misc_residue(snapshots_1, stale_days=stale_days)
    merged_samples, removed_dup_samples = dedupe_external_samples(samples)
    updated_records, rolled_back = rollback_stale_trials_guarded(
        records=records,
        trial_timeout_hours=trial_timeout_hours,
        max_rollbacks=max_rollbacks,
        protect_keywords=protect_keywords,
    )

    snap_payload["snapshots"] = snapshots_2
    snap_payload["updated_at"] = now_iso()
    ext_payload["samples"] = merged_samples
    ext_payload["updated_at"] = now_iso()
    adjust_payload["records"] = updated_records
    adjust_payload["updated_at"] = now_iso()

    if not args.dry_run:
        write_json(snap_path, snap_payload)
        write_json(ext_path, ext_payload)
        write_json(adjust_path, adjust_payload)

    state = {
        "version": "v1",
        "updated_at": now_iso(),
        "status": "completed",
        "dry_run": args.dry_run,
        "metrics": {
            "removed_duplicate_snapshots": len(removed_dup_snap),
            "removed_misc_residue": len(removed_misc),
            "removed_duplicate_samples": len(removed_dup_samples),
            "rolled_back_stale_trials": len(rolled_back),
        },
        "line_distribution_after": dict(Counter(s.get("line_id", "unknown") for s in snapshots_2)),
    }
    if not args.dry_run:
        write_json(state_path, state)
        append_jsonl(report_path, state)

    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
