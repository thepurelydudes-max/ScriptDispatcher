# Codex project guide: Script Dispatcher

Windows desktop scheduler and radio/audio automation app; Python 3.11/3.12, PySide6, PyInstaller. Codebase originated in v1.2.16; this GitHub-ready branch adds day-based job statuses (v1.2.17).

## Before making changes
- Read `README.md`, `dispatcher/ui/main_window.py`, `dispatcher/daily_status.py`, `dispatcher/schedule_engine.py`, and relevant tests.
- Do not remove or reinterpret existing universal-cut parameters, output naming placeholders, saved scheduler config, duplicate processing protection, or both independent archive upload switches.
- Schedule uses a configured timezone (default Europe/Moscow); history timestamp is stored in OS-local time. Test timezone boundaries.
- All tasks must preserve `data/config.json`, `data/secrets.dat`, `data/history.sqlite3`, `data/universal_cut_history` during upgrades. The local `data` directory is untracked.
- Avoid unintended file uploads, network access, deletions, or destructive testing.

## Security
- Never commit tokens, cookies, DPAPI state, local config, radio archives, network passwords or `private/yandex_cookie.txt`.
- `dispatcher/private_defaults.py` must contain public-safe blank defaults only. Do not restore secrets from earlier v1.2.16 ZIP.
- Secrets are supplied through the app Settings dialog and persisted only locally.
- CI builds should not package credentials; review `ScriptDispatcherBuild.spec` when changing packaging.

## Validation
- Run `python -m unittest discover -s tests -v` and `python -m compileall -q app.py dispatcher tests`.
- Windows build: `python -m PyInstaller --noconfirm --clean ScriptDispatcherBuild.spec`.
- CI is configured in `.github/workflows/` for pull request tests and a manually-triggered Windows EXE build.
- The status change must NOT alter schedule firing or duplicate-cut guarantees.
