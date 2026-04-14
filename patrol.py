from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default or {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default or {}


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


@contextmanager
def patrol_lock(lock_path: Path):
    """File-based lock to prevent concurrent patrol runs."""
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            # Check if PID is still alive
            try:
                os.kill(pid, 0)
                alive = True
            except (OSError, ProcessLookupError):
                alive = False
            if alive:
                raise RuntimeError(f"Another patrol is running (pid={pid}, lock={lock_path})")
        except ValueError:
            pass  # corrupt lock file — take over
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def run_scavenger(base: Path, config_path: Path, mode: str, operation: str, dry_run: bool) -> tuple[int, dict]:
    script = (base / "scavenger.py").resolve()
    cmd = [sys.executable, str(script), "--config", str(config_path), "--mode", mode, "--operation", operation]
    if dry_run:
        cmd.append("--dry-run")
    cp = subprocess.run(
        cmd,
        cwd=str(base),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else ""
    payload: dict = {}
    try:
        payload = json.loads(out)
    except Exception:
        payload = {"raw": out, "stderr": cp.stderr.strip()}
    return cp.returncode, payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["safe", "balanced", "aggressive"], default="balanced")
    parser.add_argument("--operation", choices=["cleanup", "collect", "review"], default="cleanup")
    parser.add_argument("--interval-seconds", type=int, default=1800)
    parser.add_argument("--cycles", type=int, default=1, help="0 means run forever")
    parser.add_argument("--auto-apply", action="store_true")
    parser.add_argument("--apply-threshold-mb", type=int, default=256)
    parser.add_argument("--log-file", default="patrol_reports.jsonl")
    parser.add_argument("--lock-file", default=None, help="Lockfile path to prevent concurrent runs")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    cfg_input = Path(args.config)
    if cfg_input.is_absolute():
        config_path = cfg_input
    elif cfg_input.exists():
        config_path = cfg_input.resolve()
    else:
        config_path = (base / cfg_input).resolve()
    log_path = (base / args.log_file).resolve() if not Path(args.log_file).is_absolute() else Path(args.log_file)
    lock_path = Path(args.lock_file).resolve() if args.lock_file else base / "patrol.lock"

    index = 0
    while True:
        index += 1
        try:
            with patrol_lock(lock_path):
                rc, dry = run_scavenger(base=base, config_path=config_path, mode=args.mode, operation=args.operation, dry_run=True)
                metrics = dry.get("metrics", {}) if isinstance(dry, dict) else {}
                collector = dry.get("collector", {}) if isinstance(dry, dict) else {}
                reclaim_bytes = float(
                    metrics.get("estimated_reclaim_bytes", 0)
                    or collector.get("estimated_reclaim_bytes", 0)
                )
                reclaim_mb = round(reclaim_bytes / 1024 / 1024, 2)
                threshold_ok = reclaim_mb >= float(args.apply_threshold_mb)
                should_apply = bool(args.auto_apply and threshold_ok and rc == 0)
                skip_reason = None
                if not should_apply:
                    if not args.auto_apply:
                        skip_reason = "auto_apply_disabled"
                    elif not threshold_ok:
                        skip_reason = f"below_threshold:{reclaim_mb:.1f}MB<{args.apply_threshold_mb}MB"
                    elif rc != 0:
                        skip_reason = f"dry_run_failed:rc={rc}"

                applied = None
                if should_apply:
                    arc, applied_payload = run_scavenger(
                        base=base,
                        config_path=config_path,
                        mode=args.mode,
                        operation=args.operation,
                        dry_run=False,
                    )
                    applied = {"returncode": arc, "payload": applied_payload}

                event = {
                    "timestamp": now_iso(),
                    "runner": "patrol.py",
                    "cycle_index": index,
                    "mode": args.mode,
                    "operation": args.operation,
                    "dry_run_returncode": rc,
                    "estimated_reclaim_mb": reclaim_mb,
                    "auto_apply": args.auto_apply,
                    "apply_threshold_mb": args.apply_threshold_mb,
                    "should_apply": should_apply,
                    "skip_reason": skip_reason,
                    "dry_run_payload": dry,
                    "applied": applied,
                }
                append_jsonl(log_path, event)
                print(json.dumps(event, ensure_ascii=False))

        except RuntimeError as lock_err:
            event = {
                "timestamp": now_iso(),
                "runner": "patrol.py",
                "cycle_index": index,
                "status": "skipped_locked",
                "reason": str(lock_err),
            }
            append_jsonl(log_path, event)
            print(json.dumps(event, ensure_ascii=False))

        if args.cycles > 0 and index >= args.cycles:
            break
        time.sleep(max(1, args.interval_seconds))


if __name__ == "__main__":
    main()
