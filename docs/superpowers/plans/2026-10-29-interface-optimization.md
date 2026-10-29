# Interface Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for integration; independent file scopes may be delegated. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved daily-use interface improvements with verified behavior and visible results.

**Architecture:** Keep QTableWidget and existing task/scheduler APIs. Add small reusable presentation/view-state helpers, a dedicated history dialog and bounded database queries; the controller integrates the main window and reviews the whole diff.

**Tech Stack:** Python 3.11/3.12, PySide6, SQLite, unittest, offscreen Qt.

**Spec:** `docs/superpowers/specs/2026-10-29-interface-design.md`.

## Global Constraints

- Preserve scheduler firing, legacy task params, both archive switches, filename tokens and duplicate-cut history.
- Preserve data/config.json, data/secrets.dat, data/history.sqlite3 and data/universal_cut_history.
- No real external jobs, network checks, media uploads or desktop log openings in tests.
- Use existing isolated checkout, branch fix/reliability-and-ui; no extra worktree.
- Delegates do not commit; controller integrates/commits/pushes the verified result.

## Review Focus

- A row's buttons must act on its task after sorting/filtering/background changes.
- Pending jobs must be distinguishable from executing jobs without changing execution order.
- Filters must apply before history LIMIT, with scheduler-zone date boundaries and old naive timestamps.
- Corrupt view state and old column schemas must not damage task data or prevent startup.
- Background progress must preserve focus/scroll and avoid per-row/per-keystroke database queries.

### Task 1: History queries and dialog

**Files:** dispatcher/history.py, new dispatcher/ui/history_dialog.py, dispatcher/ui/dialogs.py alias, tests/test_history_interface.py.
**Interfaces:** `HistoryStore.latest_by_task()->dict[str,HistoryRecord]`; extend `recent(limit=200, *, task_id=None, status=None, date_from=None, date_to=None, timezone='Europe/Moscow')` while preserving `recent()` calls. Dates are inclusive ISO dates in the supplied zone. `HistoryDialog(history_or_records, parent=None, *, task_id=None, task_names=None, timezone='Europe/Moscow')`, exported from dialogs for compatibility. Consume shared presentation.STATUS_LABELS and format_history_time.
- [ ] Add failing temporary-store tests for selected-task filtering, status/date-before-limit, aware/naive day boundaries and latest-by-task query.
- [ ] Implement parameterized queries without a schema migration, retaining local old timestamps.
- [ ] Add offscreen tests for selected-task default, task/status/date controls and explicit log-opening action (mock the OS open boundary only).
- [ ] Implement localized history dialog, message detail and local-log action. Run focused tests.

### Task 2: Separate view persistence

**Files:** new dispatcher/ui/view_state.py, tests/test_view_state.py.
**Interfaces:** `load_view_state(path:Path|None=None)->dict`, `save_view_state(state:dict,path:Path|None=None)->None`. Default data/ui_state.json; fields geometry/header/splitter base64 strings and sort_enabled bool, sort_column/sort_order ints, version=1. Loading malformed/unsupported state returns usable defaults; writes use atomic_json_write.
- [ ] Test real roundtrip, corrupt/oversized/invalid types and preservation of unrelated config/history files.
- [ ] Implement safe, bounded parsing and atomic writes without Qt/global settings or credentials. Run focused tests.

### Task 3: Main table and details

**Files:** dispatcher/ui/main_window.py, dispatcher/ui/style.py, new dispatcher/ui/presentation.py, tests/test_interface_optimization.py, existing tests/test_ui_regressions.py fixture/intent updates.
**Interfaces:** Keep task columns 0..6 stable; add inline controls in column 7. Export STATUS_LABELS/STATUS_COLORS through main_window as before, defined in presentation; format_history_time(value,timezone)->str handles naive OS-local/aware values.
- [ ] Add regressions for ID-bound buttons after sorting, toggle persistence, queued/running distinction, per-row progress and meaningful selected details.
- [ ] Add splitter/empty state/shortcuts, compact inline actions and progress, source/result/last-message/last-run/next-run details and local-log button.
- [ ] Add regressions for restoring view state, selection/scroll retention and no history requery while filtering/progress updates.
- [ ] Integrate safe persistence and history dialog; batch last-record reads once per refresh/tick, debounce text search, update runtime cells without table rebuilds.
- [ ] Keep scheduler timing/logic unchanged. Run focused and existing UI suites.

### Integration and delivery

- [ ] Run complete unittest suite, compileall, dependency and GUI/FFmpeg smoke checks.
- [ ] Capture actual Qt screenshots with synthetic local sample tasks at 1500x900 and 1100x700; inspect clipping/actions/detail readability.
- [ ] Independent whole-diff review; reproduce/fix important findings, rerun affected checks.
- [ ] Update documentation and publish the verified changes to the existing review branch; report evidence and platform limits.

## Execution ledger

- Baseline f07dc3c; user expressly requested the listed interface optimization.
- Decision: keep existing table; do not redesign navigation or change production scheduling. Independent history/persistence tasks run in parallel with main-window work.
- History: 12 focused tests pass for filters before LIMIT, scheduler-zone dates, old timestamps, explicit local-log action and batched latest records.
- Persistence: 12 focused tests pass, including corruption/limits and preserving unrelated data.
- Main window: initially 7 expected regressions failed before implementation, then all 8 new tests passed. Actual screenshots at 1500x900 and 1100x700 were inspected and the duplicate status paint corrected.
- Whole-suite pre-review verification: 148 run, 145 passed, 3 native Windows checks skipped; compile and GUI/FFmpeg smoke passed.
- Independent review: P2 background refresh clears selected path text; reproduced RED and corrected by avoiding identical QLineEdit.setText. Defensive refresh-error cleanup also reproduced RED and fixed with unconditional sorting/signal restoration. Main interface tests now 10/10.
- User additionally requested Windows build on GitHub. Preview .1 at adfd7ac started the existing tag-triggered workflow; native Windows tests failed before packaging. Detailed anonymous logs are unavailable, so the workflow now emits real test tracebacks as public job annotations and a step summary while retaining failure status. No tests are skipped or disabled to build.
- Native .2 diagnostics identified open SQLite connections and tests assuming POSIX path spelling/symlink traversal. `_connect` now explicitly commits/rolls back and closes on every exit; two retained-connection regressions observed RED→GREEN. Tests normalize actual filesystem identities without changing byte/content/conflict assertions; an offline module fixture verifies the symlink-parent resource agreement on the current OS.
- Chronological sorting uses timestamps for localized last/next-run columns; the cross-month ordering regression observed RED→GREEN.
- Combined Linux verification after corrections: 153 run, 150 passed, 3 Windows-only checks skipped, compile and whitespace checks clean. Native Windows validation/build rerun pending tag .3.
