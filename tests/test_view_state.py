from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatcher.ui import view_state


DEFAULT_STATE = {
    "version": 1,
    "geometry": "",
    "header": "",
    "splitter": "",
    "sort_enabled": False,
    "sort_column": 0,
    "sort_order": 0,
}


class ViewStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "ui_state.json"

    def write_json(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_roundtrip_replaces_previous_state_and_creates_parent_directory(self):
        path = self.root / "new-data" / "ui_state.json"
        state = {
            "version": 1,
            "geometry": "AP8BAg==",
            "header": "aGVhZGVy",
            "splitter": "c3BsaXR0ZXI=",
            "sort_enabled": True,
            "sort_column": 6,
            "sort_order": 1,
        }
        view_state.save_view_state({"sort_enabled": False}, path)
        view_state.save_view_state(state, path)
        self.assertEqual(view_state.load_view_state(path), state)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)
        self.assertEqual([entry.name for entry in path.parent.iterdir()], ["ui_state.json"])

    def test_default_path_uses_data_directory_without_global_settings(self):
        with patch("dispatcher.storage.data_dir", return_value=self.root):
            view_state.save_view_state({"sort_enabled": True, "sort_column": 3})
            loaded = view_state.load_view_state()
        self.assertEqual(loaded, {**DEFAULT_STATE, "sort_enabled": True, "sort_column": 3})
        self.assertTrue(self.path.is_file())

    def test_missing_or_unreadable_file_returns_independent_defaults(self):
        first = view_state.load_view_state(self.path)
        self.assertEqual(first, DEFAULT_STATE)
        first["sort_enabled"] = True
        self.assertEqual(view_state.load_view_state(self.path), DEFAULT_STATE)
        self.assertEqual(view_state.load_view_state(self.root), DEFAULT_STATE)
        self.assertFalse(self.path.exists())

    def test_default_directory_access_error_returns_defaults(self):
        with patch("dispatcher.storage.data_dir", side_effect=PermissionError("not readable")):
            self.assertEqual(view_state.load_view_state(), DEFAULT_STATE)

    def test_corrupt_json_encoding_or_excessive_nesting_returns_defaults(self):
        for content in (b"{broken", b"\xff\xfe", b"[" * 1500 + b"]" * 1500):
            with self.subTest(content=content[:20]):
                self.path.write_bytes(content)
                self.assertEqual(view_state.load_view_state(self.path), DEFAULT_STATE)
                self.assertEqual(self.path.read_bytes(), content)

    def test_unsupported_version_or_root_returns_defaults(self):
        for value in (None, [], "state", 1, {},
                      {"version": True, "sort_enabled": True},
                      {"version": "1", "sort_enabled": True},
                      {"version": 2, "sort_enabled": True}):
            with self.subTest(value=value):
                self.write_json(value)
                self.assertEqual(view_state.load_view_state(self.path), DEFAULT_STATE)

    def test_invalid_field_types_are_defaulted_without_losing_valid_fields(self):
        self.write_json({
            "version": 1,
            "geometry": [],
            "header": "aGVhZGVy",
            "splitter": None,
            "sort_enabled": "false",
            "sort_column": True,
            "sort_order": 1.0,
            "settings": {"token": "not-view-state"},
        })
        self.assertEqual(view_state.load_view_state(self.path), {**DEFAULT_STATE, "header": "aGVhZGVy"})

    def test_invalid_base64_is_ignored_for_each_binary_field(self):
        for field in ("geometry", "header", "splitter"):
            for value in ("invalid!", "éééé", "YQ", "YQ==\n", "YR=="):
                with self.subTest(field=field, value=value):
                    self.write_json({"version": 1, field: value, "sort_column": 2})
                    self.assertEqual(view_state.load_view_state(self.path), {**DEFAULT_STATE, "sort_column": 2})

    def test_oversized_file_and_binary_field_return_bounded_state(self):
        self.write_json({"version": 1, "geometry": "AAAA" * 25000, "sort_enabled": True})
        self.assertEqual(view_state.load_view_state(self.path), {**DEFAULT_STATE, "sort_enabled": True})
        self.write_json({"version": 1, "sort_enabled": True, "unknown": " " * 600000})
        self.assertEqual(view_state.load_view_state(self.path), DEFAULT_STATE)

    def test_save_normalizes_fields_and_does_not_mutate_input(self):
        state = {"geometry": "invalid!", "sort_enabled": 1, "sort_column": False,
                 "sort_order": "0", "password": "not-view-state"}
        original = state.copy()
        view_state.save_view_state(state, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), DEFAULT_STATE)
        self.assertEqual(state, original)

    def test_atomic_replace_failure_preserves_original_and_removes_temp_file(self):
        original = b'{"version":1,"sort_column":4}'
        self.path.write_bytes(original)
        with patch("dispatcher.storage.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                view_state.save_view_state({"sort_column": 5}, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual([entry.name for entry in self.root.iterdir()], ["ui_state.json"])

    def test_load_and_save_preserve_config_secrets_and_history(self):
        files = {
            "config.json": b'{"tasks":[{"id":"keep"}]}',
            "secrets.dat": b"opaque-secret-fixture",
            "history.sqlite3": b"opaque-history-fixture",
            "universal_cut_history/hour.json": b'{"processed":true}',
        }
        for name, content in files.items():
            file_path = self.root / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
        self.path.write_bytes(b"{bad-view-state")
        names_before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(view_state.load_view_state(self.path), DEFAULT_STATE)
        self.assertEqual(self.path.read_bytes(), b"{bad-view-state")
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), names_before)
        view_state.save_view_state({"sort_column": 5}, self.path)
        for name, content in files.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), names_before)


if __name__ == "__main__":
    unittest.main()
