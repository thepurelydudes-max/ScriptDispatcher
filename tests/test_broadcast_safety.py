"""Offline regressions for lossless export renaming and lazy MP3 decoding."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatcher.models import TaskDefinition
from dispatcher.modules.broadcast import (
    _universal_cut_rename,
    export_between_markers,
    export_marker_blocks,
    export_news,
    run_universal_cut,
)
from dispatcher.modules.common import TaskContext


def make_context(root):
    return TaskContext(
        task=TaskDefinition(id="safety", name="Safety", module="broadcast.universal_cut"),
        settings={}, secrets={}, run_dir=root / "run",
        cancel_event=threading.Event(), log=lambda _text: None,
        progress=lambda _value, _text: None,
    )


class BroadcastRenameTests(unittest.TestCase):
    def test_later_source_export_cannot_overwrite_finalized_source(self):
        for second_filename in ("B_{date-full}_{hour-minute}", "B"):
            with self.subTest(second_filename=second_filename), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                profiles = []
                for name, filename in (("A", "B_{date-full}_{hour-minute}"), ("B", second_filename)):
                    source = root / name
                    source.mkdir()
                    (source / "25-08-2026_11-00.mp3").write_bytes(name.encode())
                    (source / "25-08-2026_11-00.cue").write_text("cue", encoding="utf-8")
                    profiles.append({"name": name, "folder": str(source), "filename": filename})
                ctx = make_context(root)
                ctx.task.params = {
                    "date_mode": "manual", "manual_date": "25-08-2026",
                    "cut_mode": "between", "start_marker": "in", "end_marker": "out",
                    "sources": profiles, "output_dir": str(root / "out"),
                    "internal_bot_enabled": False, "internal_bot_republic_enabled": False,
                }

                def export(_ctx, mp3, cue, output, prefix, _start, _end):
                    path = output / f"{prefix}_{cue.stem}.mp3"
                    path.write_bytes(mp3.read_bytes())
                    return path

                with patch("dispatcher.modules.broadcast.export_between_markers", side_effect=export):
                    result = run_universal_cut(ctx)
                paths = [Path(value) for value in result["files"]]
                self.assertEqual([path.name for path in paths], [
                    "B_25-08-2026_11-00.mp3", "B_25-08-2026_11-00_2.mp3",
                ])
                self.assertTrue(all(path.is_file() for path in paths))
                self.assertEqual([path.read_bytes() for path in paths], [b"A", b"B"])
                self.assertEqual(
                    {path.resolve() for path in (root / "out").iterdir()},
                    {path.resolve() for path in paths},
                )

    def test_shifted_name_chain_preserves_every_fragment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / f"A_25-08-2026_{hour}-00.mp3" for hour in ("11", "12", "13")]
            for path, content in zip(files, (b"eleven", b"twelve", b"thirteen")):
                path.write_bytes(content)
            claimed = set()

            result = _universal_cut_rename(
                make_context(root), files, "A_{date-full}_{hour+1-minute}", "A", claimed,
            )

            self.assertEqual([path.name for path in result], [
                "A_25-08-2026_12-00.mp3", "A_25-08-2026_13-00.mp3", "A_25-08-2026_14-00.mp3",
            ])
            self.assertTrue(all(path.is_file() for path in result), "Every renamed fragment must exist")
            self.assertEqual([path.read_bytes() for path in result], [b"eleven", b"twelve", b"thirteen"])
            self.assertEqual(set(root.iterdir()), set(result))
            self.assertEqual(claimed, {path.name.casefold() for path in result})

    def test_swapped_hour_minute_names_preserve_both_fragments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "A_25-08-2026_11-12.mp3"
            second = root / "A_25-08-2026_12-11.mp3"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            result = _universal_cut_rename(
                make_context(root), [first, second], "A_{date-full}_{minute}-{hour}", "A", set(),
            )

            self.assertEqual(result, [second, first])
            self.assertTrue(all(path.is_file() for path in result), "Both swapped fragments must exist")
            self.assertEqual([path.read_bytes() for path in result], [b"first", b"second"])
            self.assertEqual(set(root.iterdir()), {first, second})

    def test_colliding_targets_preserve_an_export_at_the_first_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "A_25-08-2026_11-00.mp3"
            second = root / "A_25-08-2026_12-00.mp3"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            result = _universal_cut_rename(
                make_context(root), [first, second], "A_25-08-2026_12-00", "A", set(),
            )

            self.assertEqual([path.name for path in result], [
                "A_25-08-2026_12-00.mp3", "A_25-08-2026_12-00_2.mp3",
            ])
            self.assertTrue(all(path.is_file() for path in result), "Both collision-resolved fragments must exist")
            self.assertEqual([path.read_bytes() for path in result], [b"first", b"second"])

    def test_previous_run_output_can_still_be_replaced_and_claims_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "A_25-08-2026_11-00.mp3"
            original.write_bytes(b"new")
            previous = root / "Result_2.mp3"
            previous.write_bytes(b"old")

            result = _universal_cut_rename(
                make_context(root), [original], "Result", "A", {"result.mp3"},
            )

            self.assertEqual(result, [previous])
            self.assertEqual(previous.read_bytes(), b"new")
            self.assertEqual(list(root.iterdir()), [previous])

    def test_final_rename_failure_restores_originals_without_losing_overlapping_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "A_25-08-2026_11-00.mp3"
            second = root / "A_25-08-2026_12-00.mp3"
            target = root / "A_25-08-2026_13-00.mp3"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            replace = Path.replace
            claimed = {"earlier.mp3"}

            def fail_second_final(path, destination):
                if Path(destination) == target:
                    raise OSError("disk unavailable")
                return replace(path, destination)

            with patch.object(Path, "replace", fail_second_final):
                with self.assertRaisesRegex(OSError, "disk unavailable"):
                    _universal_cut_rename(
                        make_context(root), [first, second], "A_{date-full}_{hour+1-minute}", "A", claimed,
                    )

            self.assertTrue(first.is_file(), "The first original must be restored")
            self.assertTrue(second.is_file(), "The second original must be restored")
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")
            self.assertEqual(set(root.iterdir()), {first, second})
            self.assertEqual(claimed, {"earlier.mp3"})


class RecordingAudio:
    """Codec boundary double: records actual selected ranges in output files."""
    decodes = 0
    duration = 120_000

    def __init__(self, ranges=None):
        self.ranges = list(ranges or [])

    @classmethod
    def from_mp3(cls, _path):
        cls.decodes += 1
        return cls()

    @classmethod
    def silent(cls, duration):
        return cls()

    def __len__(self):
        return self.duration

    def __getitem__(self, selection):
        return type(self)([(selection.start, selection.stop)])

    def __iadd__(self, other):
        self.ranges.extend(other.ranges)
        return self

    def export(self, destination, format):
        Path(destination).write_text(repr(self.ranges), encoding="utf-8")


def write_cue(path, entries):
    path.write_text("\n".join(
        f'TRACK {number:02d} AUDIO\nTITLE "{title}"\nINDEX 01 {index}'
        for number, (title, index) in enumerate(entries, start=1)
    ), encoding="utf-8")


class BroadcastDecodeTests(unittest.TestCase):
    def setUp(self):
        RecordingAudio.decodes = 0

    def test_no_matching_blocks_do_not_decode_mp3(self):
        cases = [
            (export_news, ("news", "Новости"), []),
            (export_news, ("news", "Новости", True), []),
            (export_marker_blocks, ("news", "Новости"), []),
            (export_between_markers, ("cut", "влёт", "вылет"), None),
            (export_between_markers, ("cut", "Музыка", "вылет"), None),
        ]
        for exporter, arguments, expected in cases:
            with self.subTest(exporter=exporter.__name__, arguments=arguments):
                RecordingAudio.decodes = 0
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    cue = root / "25-08-2026_11-00.cue"
                    write_cue(cue, [("Музыка", "00:00:00"), ("Реклама", "00:30:00")])
                    with patch("dispatcher.modules.broadcast._audio_segment", return_value=RecordingAudio):
                        result = exporter(make_context(root), cue.with_suffix(".mp3"), cue, root, *arguments)
                    self.assertEqual(result, expected)
                    self.assertEqual(RecordingAudio.decodes, 0)
                    self.assertEqual(list(root.iterdir()), [cue])

    def test_malformed_cue_is_rejected_before_decode(self):
        for exporter, arguments in [
            (export_news, ("news", "Новости")),
            (export_marker_blocks, ("news", "Новости")),
            (export_between_markers, ("cut", "влёт", "вылет")),
        ]:
            with self.subTest(exporter=exporter.__name__):
                RecordingAudio.decodes = 0
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    cue = root / "25-08-2026_11-00.cue"
                    write_cue(cue, [("Новости", "invalid")])
                    with patch("dispatcher.modules.broadcast._audio_segment", return_value=RecordingAudio):
                        with self.assertRaises(ValueError):
                            exporter(make_context(root), cue.with_suffix(".mp3"), cue, root, *arguments)
                    self.assertEqual(RecordingAudio.decodes, 0)

    def test_news_and_marker_exports_keep_fractional_ranges_and_end_of_recording(self):
        cases = [
            (export_news, ["news_25-08-2026_11-00.mp3", "news_25-08-2026_11-01.mp3"],
             ["[(1200, 3400), (3400, 4000)]", "[(70000, 120000)]"]),
            (export_marker_blocks, ["news_25-08-2026_11-00-01.mp3", "news_25-08-2026_11-01-10.mp3"],
             ["[(1200, 4000)]", "[(70000, 120000)]"]),
        ]
        for exporter, names, ranges in cases:
            with self.subTest(exporter=exporter.__name__):
                RecordingAudio.decodes = 0
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    cue = root / "25-08-2026_11-00.cue"
                    write_cue(cue, [
                        ("Музыка", "00:00:00"), ("Новости", "00:01:15"),
                        ("Новости погода", "00:03:30"), ("Музыка", "00:04:00"),
                        ("Новости", "01:10:00"),
                    ])
                    with patch("dispatcher.modules.broadcast._audio_segment", return_value=RecordingAudio):
                        result = exporter(make_context(root), cue.with_suffix(".mp3"), cue, root, "news", "Новости")
                    self.assertEqual([path.name for path in result], names)
                    self.assertEqual([path.read_text(encoding="utf-8") for path in result], ranges)
                    self.assertEqual(RecordingAudio.decodes, 1)

    def test_between_markers_includes_end_marker_and_handles_last_track(self):
        for end_marker, same_marker_twice, entries, expected_range in [
            ("вылет", False, [("влёт", "00:01:15"), ("вылет", "00:04:00"), ("Музыка", "01:10:00")],
             "[(1200, 70000)]"),
            (None, True, [("влёт", "00:01:15"), ("Музыка", "00:04:00"), ("влёт", "01:10:00")],
             "[(1200, 120000)]"),
        ]:
            with self.subTest(same_marker_twice=same_marker_twice):
                RecordingAudio.decodes = 0
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    cue = root / "25-08-2026_11-00.cue"
                    write_cue(cue, entries)
                    with patch("dispatcher.modules.broadcast._audio_segment", return_value=RecordingAudio):
                        result = export_between_markers(
                            make_context(root), cue.with_suffix(".mp3"), cue, root,
                            "cut", "влёт", end_marker, same_marker_twice,
                        )
                    self.assertEqual(result.name, "cut_25-08-2026_11-00.mp3")
                    self.assertEqual(result.read_text(encoding="utf-8"), expected_range)
                    self.assertEqual(RecordingAudio.decodes, 1)


if __name__ == "__main__":
    unittest.main()
