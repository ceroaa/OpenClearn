# OpenClearn Case - 2026-04-13

## Summary

This case documents a staged destructive cleanup run used in production to continuously remove high-volume hostile/noisy artifacts without deleting everything in one burst.

## Context

- Date: 2026-04-13
- Scope: `scratch/g5_hunting_ground/repos`
- Strategy: slow batch destructive cleanup
- Batch size per run: 600 files

## Execution Snapshot

- Before count: 27,895 files
- Scanned in batch: 600
- Matched: 600
- Removed immediately: 597
- Locked leftovers: 3 Git packfiles
- After count: 27,372 files

## Locked File Handling

Three locked files remained under:

- `.git/objects/pack/*.idx`
- `.git/objects/pack/*.pack`
- `.git/objects/pack/*.rev`

Resolution:

1. Identify failed `ok=false` items from the last report.
2. Retry targeted deletion with force on exact file paths.
3. Verify `exists=False` for all leftovers.

## Why This Matters

- Prevents full-burst deletion risk on large trees.
- Keeps cleanup predictable and resumable.
- Provides a repeatable fallback for lockfile edge cases.
