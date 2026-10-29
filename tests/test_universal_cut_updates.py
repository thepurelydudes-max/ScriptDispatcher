"""Regression tests for universal cut filename tokens and persistent no-repeat flag."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatcher.models import TaskDefinition
from dispatcher.modules.broadcast import _universal_cut_output_name, run_universal_cut
from dispatcher.modules.common import TaskContext
from dispatcher.modules.registry import defaults_for


def make_ctx(task, root):
    return TaskContext(
        task=task,
        settings={"timezone": "Europe/Moscow"}, secrets={},
        run_dir=root / "run", cancel_event=threading.Event(),
        log=lambda msg: None, progress=lambda value, msg: None,
    )


def build_task(root, *, archived=False, protected=True, source_name="Столица"):
    source = root / "source"
    source.mkdir(exist_ok=True)
    return TaskDefinition(
        id="universal-news", name="Универсальная вырезка",
        module="broadcast.universal_cut",
        params={
            "date_mode": "manual", "manual_date": "25-08-2026",
            "cut_mode": "between", "start_marker": "влёт", "end_marker": "вылет",
            "sources": [{
                "name": source_name, "folder": str(source), "hours": "11,12,13",
                "message": "НОВОСТИ", "filename": "Новости {dd-mm-yy} {hour+1-minute+30}",
            }],
            "output_dir": str(root / "cash"),
            "internal_bot_enabled": archived,
            "internal_bot_republic_enabled": archived,
            "skip_completed_cuts": protected,
            "cleanup_after_send": archived,
        },
    )


def add_input(root, hour):
    source = root / "source"
    stem = f"25-08-2026_{hour:02d}-00"
    (source / f"{stem}.mp3").write_bytes(b"input")
    (source / f"{stem}.cue").write_text("cue", encoding="utf-8")


def fake_export(_ctx, _mp3, cue, output, prefix, _start, _end):
    filename = output / f"{prefix}_{cue.stem}.mp3"
    filename.write_bytes(f"output:{cue.stem}".encode())
    return filename


class TemplateUpdatesTests(unittest.TestCase):
    def test_date_and_hour_offsets_exact_user_examples(self):
        cue = Path("stolica_25-08-2026_11-00.mp3")
        examples = [
            ("{dd-mm-yy}", "25-08-26.mp3"),
            ("{dd-mm}", "25-08.mp3"),
            ("{hour-minute}", "11-00.mp3"),
            ("{hour+1-minute}", "12-00.mp3"),
            ("{hour+1-minute+30}", "12-30.mp3"),
            ("Новости столица {dd-mm-yy} {hour+1-minute+30}", "Новости столица 25-08-26 12-30.mp3"),
        ]
        for template, expected in examples:
            with self.subTest(template=template):
                self.assertEqual(_universal_cut_output_name(template, cue, "С"), expected)

    def test_midnight_and_month_year_rollover(self):
        self.assertEqual(
            _universal_cut_output_name(
                "Новости {dd-mm-yy} {hour+1-minute+30}",
                Path("R_31-12-2026_23-45.mp3"), "R",
            ),
            "Новости 01-01-27 01-15.mp3",
        )
        self.assertEqual(
            _universal_cut_output_name(
                "{dd-mm} {hour-minute+30}",
                Path("R_25-08-2026_11-45.mp3"), "R",
            ),
            "25-08 12-15.mp3",
        )

    def test_inconsistent_offsets_are_rejected_with_date(self):
        with self.assertRaisesRegex(ValueError, "разные сдвиги"):
            _universal_cut_output_name(
                "{date} {hour+1-minute} {hour+2-minute}",
                Path("R_25-08-2026_11-00.mp3"), "R",
            )

    def test_default_no_repeat_flag_disabled_for_compatibility(self):
        self.assertFalse(defaults_for("broadcast.universal_cut")["skip_completed_cuts"])


class PersistentHistoryTests(unittest.TestCase):
    def test_11_12_13_dont_recut_past_hours_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = build_task(root)
            calls = []
            def export(ctx, mp3, cue, output, prefix, start, end):
                calls.append(cue.stem)
                return fake_export(ctx, mp3, cue, output, prefix, start, end)
            with patch("dispatcher.modules.broadcast.data_dir", return_value=root / "data"), \
                 patch("dispatcher.modules.broadcast.export_between_markers", side_effect=export):
                add_input(root, 11)
                first = run_universal_cut(make_ctx(task, root))
                self.assertEqual(len(first["files"]), 1)
                add_input(root, 12)
                second = run_universal_cut(make_ctx(task, root))
                self.assertEqual(len(second["files"]), 1)
                add_input(root, 13)
                third = run_universal_cut(make_ctx(task, root))
                self.assertEqual(len(third["files"]), 1)
                fourth = run_universal_cut(make_ctx(task, root))
                self.assertEqual(fourth["files"], [])
                self.assertIn("уже обработано: 3", fourth["message"])
            self.assertEqual(calls, [
                "25-08-2026_11-00", "25-08-2026_12-00", "25-08-2026_13-00",
            ])
            self.assertEqual(len(list((root / "data" / "universal_cut_history").glob("*.json"))), 3)

    def test_existing_saved_receipts_survive_cash_cleanup_and_two_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = build_task(root, archived=True)
            add_input(root, 11)
            sent = []
            class FakeBot:
                def __init__(self, ctx, channel=None, title=None):
                    self.channel = channel
                def send_group(self, title, files, **kwargs):
                    sent.append((self.channel, [f.name for f in files]))
            # Bot sends in both places, then cash is cleaned by original code.
            with patch("dispatcher.modules.broadcast.data_dir", return_value=root / "data"), \
                 patch("dispatcher.modules.broadcast.InternalBotClient", FakeBot), \
                 patch("dispatcher.modules.broadcast.export_between_markers", side_effect=fake_export):
                first = run_universal_cut(make_ctx(task, root))
                self.assertEqual(len(first["files"]), 1)
                self.assertFalse((root / "cash" / Path(first["files"][0]).name).exists())
                second = run_universal_cut(make_ctx(task, root))
            self.assertEqual(second["files"], [])
            self.assertEqual(len(sent), 2)
            self.assertEqual({x[0] for x in sent}, {"extracted_media", "archive_respublika"})
            self.assertEqual(sent[0][1], ["Новости 25-08-26 12-30.mp3"])

    def test_failed_upload_does_not_create_receipt_and_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = build_task(root, archived=True)
            add_input(root, 11)
            class FakeBot:
                fails = True
                def __init__(self, ctx, channel=None, title=None):
                    pass
                def send_group(self, title, files, **kwargs):
                    if FakeBot.fails:
                        raise RuntimeError("сеть недоступна")
            with patch("dispatcher.modules.broadcast.data_dir", return_value=root / "data"), \
                 patch("dispatcher.modules.broadcast.InternalBotClient", FakeBot), \
                 patch("dispatcher.modules.broadcast.export_between_markers", side_effect=fake_export):
                with self.assertRaisesRegex(RuntimeError, "сеть недоступна"):
                    run_universal_cut(make_ctx(task, root))
                self.assertEqual(list((root / "data" / "universal_cut_history").glob("*.json")), [])
                FakeBot.fails = False
                second = run_universal_cut(make_ctx(task, root))
                self.assertEqual(len(second["files"]), 1)
                third = run_universal_cut(make_ctx(task, root))
                self.assertEqual(third["files"], [])

    def test_different_sources_same_hour_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = build_task(root)
            first = task.params["sources"][0]
            other = root / "other"; other.mkdir()
            task.params["sources"].append({**first, "name": "Республика", "folder": str(other)})
            add_input(root, 11)
            (other / "25-08-2026_11-00.mp3").write_bytes(b"input")
            (other / "25-08-2026_11-00.cue").write_text("cue", encoding="utf-8")
            with patch("dispatcher.modules.broadcast.data_dir", return_value=root / "data"), \
                 patch("dispatcher.modules.broadcast.export_between_markers", side_effect=fake_export):
                run_universal_cut(make_ctx(task, root))
            self.assertEqual(len(list((root / "data" / "universal_cut_history").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
