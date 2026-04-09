# G5 Scavenger Tool

A practical cleanup tool for AI-heavy workspaces that accumulate duplicate artifacts, stale logs, and endlessly appended workflow records.

This standalone version is extracted from the Xiaoyu G5 pipeline and designed for reuse in other projects.

## Why It Exists

AI coding workflows tend to generate:

1. duplicate records with tiny diffs
2. stale `active_trial` or experiment records
3. low-value residual snapshots that never get reused
4. repeated external specimen entries

Over time this causes storage bloat and noisy state.

## Features

- `dry-run` mode (no writes)
- snapshot dedupe by `(line_id, capability_signature)`
- low-activity stale `misc_line` cleanup
- external specimen dedupe by `sample_id`
- stale trial rollback with:
  - timeout
  - rollback cap per run
  - protected keywords (never rollback)

## Files

- `scavenger.py` - main script
- `config.example.json` - example config
- `run_safe.bat` - safe dry-run launcher (Windows)

## Quick Start

```powershell
cd tools\g5_scavenger
python scavenger.py --config config.example.json --dry-run
```

Then apply:

```powershell
python scavenger.py --config config.example.json
```

## Config

Example fields:

- `root`
- `snapshot_file`
- `external_specimen_file`
- `structure_adjustment_file`
- `state_file`
- `report_jsonl`
- `stale_days`
- `trial_timeout_hours`
- `max_rollbacks`
- `protect_keywords`

All path fields are relative to `root`.

## Safety Notes

- Start with `--dry-run`.
- Keep `max_rollbacks` low (for example 20-50).
- Use `protect_keywords` for critical experiments.

## Suggested GitHub Repo Name

- `ai-workspace-scavenger`
- `agent-state-scavenger`
- `g5-scavenger-tool`

