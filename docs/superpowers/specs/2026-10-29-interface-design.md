# Daily-use interface optimization

The user approved the preceding interface recommendations and requested their
implementation. Keep the Windows/PySide6 task dispatcher, its familiar dark
table and all scheduler, module and user-data behavior compatible.

Chosen approach: improve the existing table and details pane, rather than
introduce a navigation-heavy dashboard or replace Qt widgets wholesale.
The existing operators can keep their workflow while common actions become
available in each row. This is the implementation choice, not a new user
requirement for a particular visual design.

Deliverables:

- Inline run/stop/enable controls bound to task IDs, safe after sorting and
  filtering. Disabled tasks remain manually runnable as before.
- Clear queued/running/stopping/waiting/ready/completed/error/disabled states;
  progress in the task row and selected-task details, with the current step.
- A resizable table/details splitter, useful last-result and next-run details,
  a local-log action, keyboard shortcuts and an empty/filter-result indication.
- A searchable task list, status filters for individual outcomes, preserved
  user selection and scroll position during background updates.
- History defaults to the selected task, supports task/status/date filters,
  localized statuses, full selected message and explicit local-log opening.
  Filtering happens before the result limit; old history remains readable.
- View geometry, column widths, sorting and splitter state persist in a
  separate ignored data/ui_state.json. Corrupt/older view state falls back
  safely; task configuration, secrets and history are not rewritten for view
  changes.
- Batch history reads and update progress cells without rebuilding the whole
  table or requerying history on each keystroke.

Do not contact real network folders, run production tasks or open actual logs
in automated tests. Use temporary stores and offscreen Qt. Produce real Qt
screenshots for visual inspection at normal and smaller window sizes.
Native Windows look-and-feel/EXE execution remains a separate platform check.
