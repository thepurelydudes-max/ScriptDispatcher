"""Run the real suite and expose failure details in GitHub's job annotations."""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    os.chdir(ROOT)
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    failed = [*result.failures, *result.errors]
    summary = [f"## Windows unit tests\n\n{result.testsRun} run, {len(result.failures)} failures, "
               f"{len(result.errors)} errors, {len(result.skipped)} skipped.\n"]
    for test, traceback in failed:
        title = str(test).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        detail = traceback[-10000:]
        escaped = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={title}::{escaped}", flush=True)
        summary.append(f"### {test}\n\n```text\n{detail}\n```\n")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("\n".join(summary))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
