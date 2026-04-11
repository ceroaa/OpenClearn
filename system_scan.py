from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def run_powershell_json(script: str) -> list[dict]:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    raw = (proc.stdout or "").strip()
    if proc.returncode != 0 or not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def ps_escape(path: str) -> str:
    return path.replace("'", "''")


def get_sizes_for_paths(paths: list[Path]) -> list[dict]:
    existing = [str(p) for p in paths if p.exists()]
    if not existing:
        return []
    ps_array = "@(" + ",".join(f"'{ps_escape(p)}'" for p in existing) + ")"
    script = f"""
$paths = {ps_array}
$out = @()
foreach($p in $paths){{
  if(Test-Path -LiteralPath $p){{
    $s=(Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    $out += [pscustomobject]@{{ path=$p; size_bytes=[int64]$s; size_gb=[math]::Round(($s/1GB),3) }}
  }}
}}
$out | ConvertTo-Json -Depth 4 -Compress
"""
    rows = run_powershell_json(script)
    rows.sort(key=lambda x: int(x.get("size_bytes", 0)), reverse=True)
    return rows


def get_top_subdirs(path: Path, top: int) -> list[dict]:
    if not path.exists():
        return []
    script = f"""
$base = '{ps_escape(str(path))}'
$out = @()
Get-ChildItem -LiteralPath $base -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {{
  $d=$_.FullName
  $s=(Get-ChildItem -LiteralPath $d -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  $out += [pscustomobject]@{{ path=$d; name=$_.Name; size_bytes=[int64]$s; size_gb=[math]::Round(($s/1GB),3) }}
}}
$out | Sort-Object size_bytes -Descending | Select-Object -First {int(top)} | ConvertTo-Json -Depth 4 -Compress
"""
    return run_powershell_json(script)


def get_recent_large_files(roots: list[Path], recent_hours: int, min_file_mb: int, limit: int) -> list[dict]:
    existing = [str(p) for p in roots if p.exists()]
    if not existing:
        return []
    ps_array = "@(" + ",".join(f"'{ps_escape(p)}'" for p in existing) + ")"
    script = f"""
$roots = {ps_array}
$cut = (Get-Date).AddHours(-{int(recent_hours)})
$minBytes = {int(min_file_mb)} * 1MB
$hits = @()
foreach($r in $roots){{
  Get-ChildItem -LiteralPath $r -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Length -ge $minBytes -and $_.LastWriteTime -ge $cut }} |
    ForEach-Object {{
      $hits += [pscustomobject]@{{
        path=$_.FullName
        size_bytes=[int64]$_.Length
        size_gb=[math]::Round(($_.Length/1GB),3)
        last_write_time=([datetime]$_.LastWriteTime).ToString('o')
      }}
    }}
}}
$hits | Sort-Object size_bytes -Descending | Select-Object -First {int(limit)} | ConvertTo-Json -Depth 4 -Compress
"""
    return run_powershell_json(script)


def main() -> None:
    parser = argparse.ArgumentParser(description="System-oriented disk growth scan for OpenClearn.")
    parser.add_argument("--user-root", default=str(Path.home()))
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--recent-hours", type=int, default=24)
    parser.add_argument("--min-file-mb", type=int, default=200)
    parser.add_argument("--recent-limit", type=int, default=50)
    parser.add_argument("--include-recent-large-files", action="store_true")
    args = parser.parse_args()

    user_root = Path(args.user_root)
    local = user_root / "AppData" / "Local"
    roaming = user_root / "AppData" / "Roaming"
    openclaw = user_root / ".openclaw"
    ollama = user_root / ".ollama"
    downloads = user_root / "Downloads"
    chrome_user_data = local / "Google" / "Chrome" / "User Data"

    roots = [downloads, local, roaming, openclaw, ollama, chrome_user_data]
    root_sizes = get_sizes_for_paths(roots)
    top_local = get_top_subdirs(local, args.top)
    top_chrome = get_top_subdirs(chrome_user_data, args.top)

    recent = []
    if args.include_recent_large_files:
        recent = get_recent_large_files(
            roots=[downloads, local, openclaw, ollama],
            recent_hours=args.recent_hours,
            min_file_mb=args.min_file_mb,
            limit=args.recent_limit,
        )

    report = {
        "timestamp": now_iso(),
        "runner": "system_scan.py",
        "status": "completed",
        "inputs": {
            "user_root": str(user_root),
            "top": args.top,
            "recent_hours": args.recent_hours,
            "min_file_mb": args.min_file_mb,
            "recent_limit": args.recent_limit,
            "include_recent_large_files": bool(args.include_recent_large_files),
        },
        "largest_roots": root_sizes,
        "top_local_subdirs": top_local,
        "top_chrome_userdata_subdirs": top_chrome,
        "recent_large_files": recent,
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

