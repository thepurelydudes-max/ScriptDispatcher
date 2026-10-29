# Reliability and UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for integration, with independent file scopes delegated to implementers. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix the reproduced file-loss, task-lifecycle, scheduling and interface defects from the approved analysis.

**Architecture:** Preserve the existing modules and saved configuration formats. Fix each subsystem at its existing boundary, add regression tests with temporary inputs, then integrate and review the combined changes. Use the existing isolated cloud checkout and a feature branch, without another worktree.

**Tech Stack:** Python 3.11/3.12, PySide6, unittest, SQLite, FFmpeg; Windows target, Linux offscreen validation.

**Spec:** `docs/superpowers/specs/2026-10-29-reliability-and-ui-design.md`, distilled from the approved `/workspace/artifacts/ScriptDispatcher-analysis.md`; confirmed defects, not a visual redesign.

## Global Constraints

- Preserve `data/config.json`, `data/secrets.dat`, `data/history.sqlite3`, `data/universal_cut_history`.
- Keep filename placeholders, legacy cut modes, duplicate-cut receipts and independent archive switches compatible.
- Do not contact real network folders, upload media, or run users' external tasks during tests.
- All timestamps compared for catch-up must represent the same time zone; legacy naive history means OS-local time.
- No commits from delegated agents; the controller integrates and commits the verified combined result.
- No changes to installed user data. Keep source modifications on `fix/reliability-and-ui`.

## Review Focus

- Renaming a set of files whose destinations overlap their original names must preserve every fragment.
- Exceptions during initialization or history finalization must release task reservations and produce a UI result.
- Sources nested in universal-cut profiles and implicit cash output must participate in conflict detection.
- Schedule-only edits must preserve module parameters and workdir, including legacy values and unknown keys.
- Cancellation must finish owned descendant processes; manually launched browser windows keep their documented behavior.

### Task 1: Safe broadcast processing

**Files:** `dispatcher/modules/broadcast.py`, `tests/test_broadcast_safety.py`.
**Interfaces:** Keep `_universal_cut_rename` signature and existing export functions/receipt behavior.
- [x] Add failing tests for shifted rename chains/swaps and colliding targets; assert all file bytes survive.
- [x] Stage original outputs under unique temporary names before final renames, restoring on failure where safe.
- [x] Parse CUE and determine matching blocks before decoding MP3; retain identical exported boundaries and naming.
- [x] Run focused tests and existing output-name/universal-cut suites.

### Task 2: Runner reservations and path resources

**Files:** `dispatcher/task_runner.py`, new `dispatcher/task_paths.py`, `tests/test_runner_safety.py`.
**Interfaces:** `task_input_paths(task) -> list[tuple[str, str]]`, `task_output_path(task) -> str`, `task_resource_paths(task) -> set[str]`; UI imports the first two, runner imports the third. Include enabled nested sources, effective cash defaults and normalized actual parent folders for file paths. Keep existing task parameter formats.
- [x] Reproduce history.start, mkdir and history.finish failures and verify task can be queued again.
- [x] Make all post-reservation initialization/finalization exception-safe and return an error RunResult.
- [x] Cover shared default cash, nested sources, relative/absolute aliases and parent/child folders in resource conflicts.
- [x] Implement actual-path conflict checks without changing output locations or cleanup policy.
- [x] Run runner tests and existing TaskRunnerTests.

### Task 3: UI correctness and catch-up

**Files:** `dispatcher/ui/main_window.py`, `dispatcher/ui/dialogs.py`, `tests/test_ui_regressions.py`.
**Interfaces:** Consume Task 2 path helpers. Preserve public UI methods where possible.
- [x] Add offscreen failing tests for schedule-only legacy params, success filter, preserved selection/progress, nested source paths and cross-zone catch-up.
- [x] In schedule-only mode change only the schedule. Preserve unknown and legacy parameters.
- [x] Make the visible completed filter include success; preserve selection/tab for scheduled/background starts and completions; track progress per task.
- [x] Use shared path helpers, expose universal-cut default output and check paths in a worker with queued results.
- [x] Convert OS-local naive history or aware history to scheduler time before catch-up comparison; prevent pending tasks from being scheduled twice; guarantee done emission on unexpected worker error.
- [x] Run focused offscreen tests and relevant existing tests.

### Task 4: Owned process trees

**Files:** new `dispatcher/process_tree.py`, `dispatcher/modules/web_tools.py`, integration hooks in `dispatcher/task_runner.py`, `tests/test_process_tree.py`.
**Interfaces:** Keep ownership limited to task processes; external browser opening is not captured. Parent controller integrates runner hooks after Task 2.
- [x] Reproduce a cancelled/expired external launcher leaving a child alive, with temporary scripts only.
- [x] Implement POSIX process groups and Windows Job Object ownership without new dependencies. Establish ownership before launching work; terminate/wait before releasing resources.
- [x] Cover clean finish, cancellation, timeout and initialization failure. Run native-platform integration tests; explicitly report Windows-only checks unrun on Linux.

### Task 5: Fast equivalent schedule lookups and housekeeping

**Files:** `dispatcher/schedule_engine.py`, `tests/test_schedule_lookup.py`, `.gitignore`, README/CHANGELOG.
- [x] Add table-driven tests covering exact-minute exclusion, weekly rollover, empty schedules, intervals, inclusive windows, large intervals, next/previous symmetry and aware inputs.
- [x] Replace minute scans with bounded day lookups and interval arithmetic; preserve occurrence_key and existing semantics.
- [x] Measure the same 100-weekly-schedule workload used in the analysis.
- [x] Exclude `_BACKUPS/`; document fixed behavior and remaining Windows validation.

### Integration and review

- [x] Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v`, compileall, pip check and the offline GUI/FFmpeg smoke helper.
- [x] Obtain independent whole-diff review, resolve important findings and rerun affected tests.
- [ ] Commit the verified changes, publish feature branch for review (existing GitHub publication authorization; do not merge main), report exact outcomes and limits.

## Execution ledger

- Start: baseline `e93fdc2`, clean checkout, 45 passing tests from onboarding. Four independent source scopes delegated; scheduling/integration owned by controller.
- Ruling: execute the approved report now without another approval round; the user explicitly asked to continue. Keep optional visual redesign and broader history features outside this corrective batch.
- Pre-flight: Tasks 2/3 share path helpers; exact signatures agreed with both implementers. Task 4 runner hooks are integrated by the controller only after Task 2 is done, avoiding concurrent writes. Task 5 changes lookup internals, not its public interfaces consumed by Task 3.
- Task 5 schedule lookup: RED saw 1,440 normalizations for each weekly lookup; GREEN 10/10 tests. Benchmark 100 weekly next_run calls 0.00086s versus baseline 1.197s. Backups exclusion added; documentation pending integration.
- Execution adaptation: independent file scopes are delegated in parallel as required by the session's multi-agent instructions; one fresh combined review follows integration.
- Task 1: 9 new broadcast safety tests plus 13 existing naming/history/archive tests pass; rename chain/swap/collision regressions observed RED first. Original sources restricted to assigned module.
- Task 2: 14 scoped runner/path tests pass. Controller additionally reproduced a state-lock stall during network path resolution, moved resolution outside the guard, and verified the cancellation regression RED→GREEN.
- Task 3: 18 offscreen UI regressions pass. Path checks use one bounded daemon worker to avoid holding interpreter exit on blocked network calls; a task's unpersisted terminal result is shown until its scheduler start-day ends.
- Task 4 integration: gated module process startup establishes ownership before releasing work. Native Linux forced-cancel/leftover-descendant/browser-preservation tests pass, including 12 executed process tests; 3 native Windows checks are skipped here. Full runner-to-external integration and whole-suite verification pending.
- Task 5: 160 seeded next/previous results match the former minute-scan semantics; backup ignore and developer/change documentation updated.
- Final review: independent reviewer found P1 cross-source raw exports could overwrite finalized outputs, and P2 shared read-only interpreter/program paths could suppress unrelated jobs. Both were reproduced in new regressions before correction.
- Final correction: each source now exports into a unique staging directory on the output filesystem, then publishes through the collision-aware renamer (optional destination argument; existing calls remain compatible). Template and legacy names share final-name reservations. Existing test codec doubles now materialize the files they claim to export; their assertions remain intact.
- Final correction: executable/interpreter/browser paths remain visible in access checks but are excluded from data-directory locks. An external task without an explicit workdir still reserves its effective program directory. Shared-runtime and external-default-workdir regressions pass.
- Final: minor (deferred): post-exit external stdout draining above the 64 KiB read limit on platforms with larger pipes; the loop remains bounded against descendants continuously writing after the launcher exits. Native Windows default pipe capacity is smaller; no native larger-pipe reproduction was run.
- Verification: 113-test pre-review suite passed with 3 native Windows skips; 10 broadcast safety and 17 runner/path tests pass after the review corrections. Final whole-suite result recorded below before commit.

- Final verification: 116 tests run in 9.028s, 113 passed and 3 native Windows checks skipped; compileall, pip check, offscreen 14-task GUI and real 1000ms FFmpeg cut passed. No application data or live network jobs were used. Both important review findings verified RED→GREEN; no blocking finding remains unresolved.
