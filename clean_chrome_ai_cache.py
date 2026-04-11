from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def dir_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except Exception:
            continue
    return total


def chrome_ai_targets(user_root: Path) -> list[Path]:
    base = user_root / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    names = [
        "OptGuideOnDeviceModel",
        "component_crx_cache",
        "optimization_guide_model_store",
        "screen_ai",
        "SODALanguagePacks",
        "SODA",
        "WasmTtsEngine",
    ]
    return [base / n for n in names]


def kill_chrome() -> dict:
    proc = subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Chrome local AI cache folders safely.")
    parser.add_argument("--user-root", default=str(Path.home()))
    parser.add_argument("--kill-chrome", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    user_root = Path(args.user_root)
    targets = chrome_ai_targets(user_root)
    before = {str(p): dir_size_bytes(p) for p in targets}
    before_total = sum(before.values())

    kill_result = None
    if args.kill_chrome:
        kill_result = kill_chrome()

    deleted: list[str] = []
    failed: list[dict] = []
    if not args.dry_run:
        for p in targets:
            if not p.exists():
                continue
            try:
                shutil.rmtree(p)
                deleted.append(str(p))
            except Exception as exc:
                failed.append({"path": str(p), "error": f"{exc.__class__.__name__}: {exc}"})

    after = {str(p): dir_size_bytes(p) for p in targets}
    after_total = sum(after.values())
    reclaimed = max(0, before_total - after_total)

    report = {
        "timestamp": now_iso(),
        "runner": "clean_chrome_ai_cache.py",
        "status": "completed",
        "dry_run": bool(args.dry_run),
        "kill_chrome": bool(args.kill_chrome),
        "kill_result": kill_result,
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "deleted_paths": deleted,
        "failed": failed,
        "before_bytes": before_total,
        "after_bytes": after_total,
        "reclaimed_bytes": reclaimed,
        "reclaimed_gb": round(reclaimed / (1024**3), 3),
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

