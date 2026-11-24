"""Source-backed regression for ONLYOFFICE atomic replace cleanup."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "main_computer" / "viewport_routes_onlyoffice.py"


def atomic_writer():
    source = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == "ViewportOnlyOfficeRoutesMixin")
    fn = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == "_onlyoffice_atomic_write")
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name="Subject", bases=[], keywords=[], body=[fn], decorator_list=[])], type_ignores=[]))
    namespace = {"Path": Path, "os": os, "tempfile": tempfile}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["Subject"]()._onlyoffice_atomic_write


class OnlyofficeAtomicCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="onlyoffice-atomic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "Book.xlsx"
        self.path.write_bytes(b"original")
        self.write = atomic_writer()

    def assert_no_orphans(self):
        self.assertEqual(list(self.root.glob(".Book.xlsx.*.tmp")), [])

    def test_successfully_replaces_workbook(self):
        self.write(self.path, b"replacement")
        self.assertEqual(self.path.read_bytes(), b"replacement")
        self.assert_no_orphans()

    def test_rename_failure_preserves_original_and_removes_temp(self):
        with patch("os.replace", side_effect=OSError("disk rename failure")):
            with self.assertRaises(OSError):
                self.write(self.path, b"replacement")
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assert_no_orphans()

    def test_fsync_failure_preserves_original_and_removes_temp(self):
        with patch("os.fsync", side_effect=OSError("fsync failure")):
            with self.assertRaises(OSError):
                self.write(self.path, b"replacement")
        self.assertEqual(self.path.read_bytes(), b"original")
        self.assert_no_orphans()

    def test_existing_unrelated_temp_files_not_deleted(self):
        other = self.root / "not-my-temp.tmp"
        other.write_bytes(b"keep")
        with patch("os.replace", side_effect=OSError("disk rename failure")):
            with self.assertRaises(OSError):
                self.write(self.path, b"replacement")
        self.assertEqual(other.read_bytes(), b"keep")
        self.assert_no_orphans()


if __name__ == "__main__":
    unittest.main()
