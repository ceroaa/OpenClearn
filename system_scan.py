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


# ── Reclaimable-space classifier (lessons learned from real agent-workspace cleanups) ──
# Two categories that dominate reclaimable space in practice:
#   1. regenerable_cache  — caches that rebuild themselves on next use (safe to clear)
#   2. stale_backup       — superseded snapshots/backups (usually reclaimable; verify first)
# Read-only: we only size and recommend. Deletion still goes through the guarded delete flow.

# Caches relative to the user home (+ a couple of well-known system caches).
RECLAIMABLE_CACHE_RELPATHS = (
    "AppData/Local/npm-cache",
    "AppData/Roaming/npm-cache",
    "AppData/Local/pip/Cache",
    ".cache",
    "AppData/Local/Yarn/Cache",
    "AppData/Local/pnpm-cache",
    "AppData/Local/Microsoft/Windows/INetCache",
    "AppData/Local/Temp",
)
RECLAIMABLE_CACHE_SYSTEM = (
    r"C:\Windows\Temp",
    r"C:\Windows\SoftwareDistribution\Download",
)
# Stale-backup name patterns (dirs or files) — superseded snapshots that pile up.
STALE_BACKUP_PATTERNS = (
    "*.pre-migration", "*.bak", "*.bak_*", "*.bak.*", "*.backup-*", "*.backup_*",
    "*_backup_*", "*.old", "*.orig", "*.clobbered*",
)


def get_reclaimable(user_root: Path, backup_scan_depth: int = 1,
                    extra_backup_roots: list[Path] | None = None) -> dict:
    """Classify likely-reclaimable space: regenerable caches + stale backups (read-only).

    Stale backups are scanned over a *curated* set of shallow roots (forgotten backups live
    at a home/project top level, not buried deep) with heavy trees pruned — this keeps the
    scan fast instead of walking all of AppData.
    """
    cache_paths = [str(user_root / rel.replace("/", "\\")) for rel in RECLAIMABLE_CACHE_RELPATHS]
    cache_paths += list(RECLAIMABLE_CACHE_SYSTEM)
    cache_array = "@(" + ",".join(f"'{ps_escape(p)}'" for p in cache_paths) + ")"
    patt_array = "@(" + ",".join(f"'{ps_escape(p)}'" for p in STALE_BACKUP_PATTERNS) + ")"

    backup_roots = [user_root, user_root / ".openclaw", user_root / ".openclaw" / "workspace",
                    user_root / "Desktop", user_root / "Documents"]
    if extra_backup_roots:
        backup_roots += list(extra_backup_roots)
    seen, roots = set(), []
    for r in backup_roots:
        rs = str(r)
        if rs not in seen and r.exists():
            seen.add(rs)
            roots.append(rs)
    roots_array = "@(" + ",".join(f"'{ps_escape(p)}'" for p in roots) + ")"

    script = f"""
$caches = {cache_array}
$cacheOut = @()
foreach($p in $caches){{
  if(Test-Path -LiteralPath $p){{
    $s=(Get-ChildItem -LiteralPath $p -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if($s -gt 0){{ $cacheOut += [pscustomobject]@{{ path=$p; size_bytes=[int64]$s; size_gb=[math]::Round(($s/1GB),3) }} }}
  }}
}}
$roots = {roots_array}
$patterns = {patt_array}
$prune = '*\\node_modules\\*','*\\.git\\*','*\\AppData\\Local\\Programs\\*','*\\site-packages\\*','*\\models\\*'
$bk = @()
$seen = @{{}}
foreach($root in $roots){{
  Get-ChildItem -LiteralPath $root -Recurse -Depth {int(backup_scan_depth)} -Force -ErrorAction SilentlyContinue | Where-Object {{
    $n = $_.Name; $m = $false
    foreach($pat in $patterns){{ if($n -like $pat){{ $m = $true; break }} }}
    $m
  }} | ForEach-Object {{
    $fp = $_.FullName
    if($seen.ContainsKey($fp)){{ return }}
    $skip = $false
    foreach($pp in $prune){{ if($fp -like $pp){{ $skip=$true; break }} }}
    if($skip){{ return }}
    $seen[$fp] = $true
    if($_.PSIsContainer){{ $s=(Get-ChildItem -LiteralPath $fp -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }}
    else {{ $s=$_.Length }}
    if($s -gt 0){{ $bk += [pscustomobject]@{{ path=$fp; name=$_.Name; size_bytes=[int64]$s; size_gb=[math]::Round(($s/1GB),3); last_write_time=([datetime]$_.LastWriteTime).ToString('o') }} }}
  }}
}}
$bk = $bk | Sort-Object size_bytes -Descending | Select-Object -First 50
[pscustomobject]@{{ caches=$cacheOut; stale_backups=$bk }} | ConvertTo-Json -Depth 5 -Compress
"""
    rows = run_powershell_json(script)
    payload = rows[0] if rows else {}
    caches = payload.get("caches") or []
    backups = payload.get("stale_backups") or []
    if isinstance(caches, dict):
        caches = [caches]
    if isinstance(backups, dict):
        backups = [backups]
    for c in caches:
        c["category"] = "regenerable_cache"
        c["safety"] = "safe"
        c["reason"] = "Cache rebuilds itself on next use; safe to clear."
    for b in backups:
        b["category"] = "stale_backup"
        b["safety"] = "review"
        b["reason"] = "Superseded backup/snapshot; usually reclaimable — verify it is not the live copy first."
    caches.sort(key=lambda x: int(x.get("size_bytes", 0)), reverse=True)
    cache_gb = round(sum(int(c.get("size_bytes", 0)) for c in caches) / 1024 ** 3, 3)
    backup_gb = round(sum(int(b.get("size_bytes", 0)) for b in backups) / 1024 ** 3, 3)
    return {
        "regenerable_cache": caches,
        "stale_backups": backups,
        "summary": {
            "reclaimable_cache_gb": cache_gb,
            "reclaimable_backup_gb": backup_gb,
            "reclaimable_total_gb": round(cache_gb + backup_gb, 3),
            "note": "cache=safe to clear; stale_backup=verify-then-clear. Read-only recommendation.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="System-oriented disk growth scan for OpenClearn.")
    parser.add_argument("--user-root", default=str(Path.home()))
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--recent-hours", type=int, default=24)
    parser.add_argument("--min-file-mb", type=int, default=200)
    parser.add_argument("--recent-limit", type=int, default=50)
    parser.add_argument("--include-recent-large-files", action="store_true")
    parser.add_argument("--no-reclaimable", action="store_true",
                        help="skip the reclaimable-space classifier (cache + stale backups)")
    parser.add_argument("--backup-scan-depth", type=int, default=1,
                        help="how deep under each curated root to look for stale backups")
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

    reclaimable = {} if args.no_reclaimable else get_reclaimable(user_root, args.backup_scan_depth)

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
            "reclaimable": not args.no_reclaimable,
        },
        "largest_roots": root_sizes,
        "top_local_subdirs": top_local,
        "top_chrome_userdata_subdirs": top_chrome,
        "recent_large_files": recent,
        "reclaimable_candidates": reclaimable,
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

