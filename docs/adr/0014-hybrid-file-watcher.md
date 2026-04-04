# ADR 0014: Hybrid File Watcher (inotify + Checksum Polling)

## Status
Implemented

## Context

The file watcher (ADR 0006) used only watchdog's `on_modified` event to detect changes to `flows.json`. This failed silently in two scenarios:

1. **Atomic writes** — Node-RED, vim, nano, and other editors save files by writing to a temp file then renaming it over the target. This produces `on_moved` or `on_created` events, never `on_modified`.
2. **Docker bind mounts** — Linux inotify events from host-side file edits may not propagate into the container, causing the watcher to miss changes entirely.

The result was that directly editing `flows.json` on the host did not trigger a backup.

## Decision

Replace the `on_modified`-only watcher with a hybrid approach in `backup/services/watcher_service.py`:

### 1. Handle all relevant watchdog events

Added `on_created` and `on_moved` handlers alongside `on_modified`. All three delegate to a shared `_handle_potential_change()` method. For `on_moved`, the **destination** path is checked (since a temp file being renamed to `flows.json` is the relevant case).

### 2. Checksum polling fallback

A background polling thread runs alongside the watchdog observer. Every `watch_debounce_seconds` (default 30s, reuses existing config), it:
- Reads `flows.json` and computes its SHA256 checksum
- Compares against the last known checksum stored on the handler
- If changed, resets the debounce timer (same path as an inotify event)

This guarantees detection even when inotify delivers no events at all. The debounce timer and `_on_debounce_complete()` remain the single path to `create_backup()` — no parallel backup paths.

### 3. Comprehensive logging

- **DEBUG**: Every filesystem event (type, src, dest, is_dir), filter decisions (wrong file, directory, watch disabled), debounce timer resets with source (inotify vs polling), each poll cycle result
- **INFO**: Startup config dump (flows_path, watch_enabled, debounce, file exists, initial checksum), polling thread start/stop, change detection via polling, backup outcomes
- **ERROR/EXCEPTION**: Backup failures, unexpected errors in poll cycle

### 4. Startup integrity check

Added `checkintegrity` management command that runs on container startup (in `entrypoint.sh`, after migrations). It removes `BackupRecord` entries whose archive files no longer exist on disk and logs each removal. This prevents orphaned records from cluttering the dashboard after volume path changes or other file loss.

## Consequences

- File changes are reliably detected regardless of how the file is written or whether inotify works on the volume mount
- Worst-case detection latency equals the poll interval (`watch_debounce_seconds`, default 30s) plus the debounce window
- When inotify does work, detection remains near-instant
- No model changes required — poll interval reuses `watch_debounce_seconds`
- Slightly more I/O from periodic SHA256 computation (negligible for a single JSON file)
