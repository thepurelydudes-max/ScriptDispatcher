# Reliability and UI corrective batch

The user asked to continue after the ScriptDispatcher 1.2.17 code audit. This
batch implements the reproduced correctness and responsiveness issues from that
audit; it preserves the existing desktop layout and stored task formats.

Required outcomes:

- Preserve every universal-cut fragment when final names overlap original
  names or collide. Do not remove repeated-run replacement or naming tokens.
- Reserve actual shared input/output directories, including enabled nested
  source profiles, implicit cash, aliases and parent/child folders.
- Release task reservations after initialization/finalization IO failures and
  report errors to the UI. Slow path resolution must not block UI state queries.
- Own task process trees before work starts, and clean descendants before
  releasing resources. Preserve intentionally opened browser windows.
- Preserve every other task field during schedule-only edits, including old
  mode values and parameters unknown to the current editor.
- Normalize OS-local legacy or aware history before scheduler catch-up
  comparisons; preserve daily status reset and occurrence firing semantics.
- Remove minute-by-minute next/previous lookup without changing results.
- Skip audio decoding when CUE contains no requested ranges.
- Correct the completed filter, background focus changes, per-task progress,
  source navigation and blocking access checks.
- Keep data/config.json, data/secrets.dat, data/history.sqlite3 and
  data/universal_cut_history compatible and untouched by development tests.
- Do not publish secrets or user data; ignore local _BACKUPS before use.

Use regression tests with temporary local inputs. Unit tests and offscreen
GUI/FFmpeg checks run under Linux; native Windows Job Object tests and Windows
EXE packaging require Windows validation. No production radio/network tasks
are authorized as part of testing.

Deferred enhancements: visual redesign, new history navigation/filter controls,
batch SQLite caching, and reusing a decoded recording across different export
types. These are separable enhancements after the corrective batch.
