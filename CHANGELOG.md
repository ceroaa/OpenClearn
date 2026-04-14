# Changelog

All notable changes to OpenClearn are documented in this file.

## v1.4.0 - 2026-04-14

- fix: `delete` operation now respects `--dry-run` flag — previously dry-run was silently ignored and files were actually deleted.
- feat: `scan_stale_files` now includes `age_days` field in each candidate — reviewers can see exactly how old a file is, not just "older than N days".
- feat: `write_review_markdown` now includes a **Summary by Kind** table and **Stale File Age Distribution** section; configurable `max_items` (default 200, was hardcoded 100).
- feat: `patrol.py` now acquires a file-based lockfile before each cycle — prevents two patrol processes from running concurrently and corrupting state files. PID-aware: stale locks from dead processes are automatically cleared.
- feat: `patrol.py` now logs `skip_reason` when not applying — explains whether auto_apply is disabled, reclaim is below threshold, or dry-run failed.
- feat: `patrol.py` now correctly reads `estimated_reclaim_bytes` from both `metrics` and `collector` sections (covers both cleanup and collect/review/delete operation outputs).

## v1.3.0 - 2026-04-13

- docs: add production case note for staged destructive cleanup workflow.
- docs: document lockfile edge case and manual lock release path.
- chore: introduce `VERSION` file for release tracking.
- docs: align README release section to v1.3.

## v1.2.0

- add collect/review/delete workflow around candidate approval.
- add system disk growth scanner (`system_scan.py`).
- add Chrome AI cache cleanup helper (`clean_chrome_ai_cache.py`).
- add agent profile loading and provider/api-key binding metadata.

## v1.1.0

- add provider/key-loaded status into report metadata.

## v1.0.0

- initial autopatrol cleanup with media dedupe and cleanup profiles.
