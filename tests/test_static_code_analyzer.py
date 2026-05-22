from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from main_computer.cli import build_parser
from main_computer.static_code_analyzer import analyze_path, format_text_report


class StaticCodeAnalyzerTests(unittest.TestCase):
    def test_counts_lines_by_language_and_filters_generated_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "pkg"
            package.mkdir()
            (package / "app.py").write_text("# module\n\nprint('hello')\n", encoding="utf-8")
            (package / "script.ps1").write_text("# setup\nWrite-Host 'hi'\n", encoding="utf-8")
            (package / "config.json").write_text('{\n  "enabled": true\n}\n', encoding="utf-8")
            generated = root / "generated_component_docs" / "nodes"
            generated.mkdir(parents=True)
            (generated / "generated.json").write_text('{"ignored": true}\n', encoding="utf-8")
            venv = root / ".venv" / "Lib"
            venv.mkdir(parents=True)
            (venv / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")

            report = analyze_path(root)

        self.assertFalse(report.export_scope)
        self.assertEqual(report.file_count, 3)
        self.assertEqual(report.total_lines, 8)
        self.assertEqual(report.comment_lines, 2)
        self.assertEqual(report.blank_lines, 1)
        self.assertEqual(report.code_lines, 5)

        by_language = {item.language: item for item in report.language_stats()}
        self.assertEqual(by_language["Python"].total_lines, 3)
        self.assertEqual(by_language["Python"].code_lines, 1)
        self.assertEqual(by_language["PowerShell"].total_lines, 2)
        self.assertEqual(by_language["JSON"].total_lines, 3)
        self.assertNotIn("generated_component_docs/nodes/generated.json", {item.path for item in report.files})

    def test_export_scope_limits_repository_scan_to_export_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "export-main-computer-test.ps1").write_text("# use default analyzer export rules\n", encoding="utf-8")
            package = root / "main_computer"
            package.mkdir()
            (package / "app.py").write_text("print('included')\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_app.py").write_text("def test_app():\n    pass\n", encoding="utf-8")
            debris = root / "not_exported"
            debris.mkdir()
            (debris / "leak.py").write_text("print('should not be analyzed')\n", encoding="utf-8")

            report = analyze_path(root)

        self.assertTrue(report.export_scope)
        self.assertEqual({item.path for item in report.files}, {"export-main-computer-test.ps1", "main_computer/app.py", "tests/test_app.py"})

    def test_export_scope_honors_export_script_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "export-main-computer-test.ps1").write_text("# use default analyzer export rules\n", encoding="utf-8")
            (root / "main_computer").mkdir()
            (root / "main_computer" / "app.py").write_text("print('included')\n", encoding="utf-8")
            (root / "contracts" / "src").mkdir(parents=True)
            (root / "contracts" / "src" / "Token.sol").write_text("contract Token {}\n", encoding="utf-8")
            (root / "contracts" / "out").mkdir(parents=True)
            (root / "contracts" / "out" / "Token.sol").write_text("contract Generated {}\n", encoding="utf-8")
            (root / "tools" / "patching" / "reports").mkdir(parents=True)
            (root / "tools" / "patching" / "reports" / "report.py").write_text("print('ignored')\n", encoding="utf-8")
            (root / "release_reports").mkdir()
            (root / "release_reports" / "report.py").write_text("print('ignored')\n", encoding="utf-8")

            report = analyze_path(root)

        paths = {item.path for item in report.files}
        self.assertIn("main_computer/app.py", paths)
        self.assertIn("contracts/src/Token.sol", paths)
        self.assertNotIn("contracts/out/Token.sol", paths)
        self.assertNotIn("tools/patching/reports/report.py", paths)
        self.assertNotIn("release_reports/report.py", paths)

    def test_export_scope_reads_export_items_from_export_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "export-main-computer-test.ps1").write_text(
                '$exportItems = @(\n  "allowed"\n)\n',
                encoding="utf-8",
            )
            (root / "allowed").mkdir()
            (root / "allowed" / "code.py").write_text("print('included')\n", encoding="utf-8")
            (root / "main_computer").mkdir()
            (root / "main_computer" / "app.py").write_text("print('not in custom export items')\n", encoding="utf-8")

            report = analyze_path(root)

        self.assertEqual({item.path for item in report.files}, {"allowed/code.py"})

    def test_tracks_todo_and_long_line_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "work.py").write_text(
                "# TODO: tighten this\n"
                "value = '" + ("x" * 12) + "'\n",
                encoding="utf-8",
            )

            report = analyze_path(root, long_line_threshold=21)

        self.assertEqual(report.todo_lines, 1)
        self.assertEqual(report.long_lines, 1)
        self.assertEqual(report.top_files_by_lines(1)[0].path, "work.py")

    def test_include_docs_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("# Title\n\ntext\n", encoding="utf-8")

            default_report = analyze_path(root)
            docs_report = analyze_path(root, include_docs=True)

        self.assertEqual(default_report.file_count, 0)
        self.assertEqual(docs_report.file_count, 1)
        self.assertEqual(docs_report.total_lines, 3)

    def test_debug_progress_goes_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")

            stderr = io.StringIO()
            report = analyze_path(root, debug=True, debug_every=1, debug_stream=stderr)

        self.assertEqual(report.file_count, 1)
        debug_text = stderr.getvalue()
        self.assertIn("code-stats: root=", debug_text)
        self.assertIn("analyzed=1", debug_text)

    def test_text_report_starts_with_line_count_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")

            text = format_text_report(analyze_path(root), top=5)

        self.assertIn("Total lines: 1", text)
        self.assertIn("Export scope: disabled", text)
        self.assertIn("By language:", text)
        self.assertIn("app.py: 1 lines", text)
        self.assertIn("Rollup:", text)
        self.assertIn("Size: 1 files contain 1 total lines", text)
        self.assertIn("Counting note:", text)

    def test_cli_code_stats_can_emit_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
            parser = build_parser()
            args = parser.parse_args(["code-stats", str(root), "--format", "json"])

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = args.func(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["summary"]["total_lines"], 1)
        self.assertFalse(payload["summary"]["export_scope"])
        self.assertEqual(payload["rollup"]["headline"], "1 files, 1 total lines, 1 code lines")
        self.assertIn("observations", payload["rollup"])
        self.assertEqual(payload["files"][0]["path"], "app.py")

    def test_cli_debug_keeps_json_stdout_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
            parser = build_parser()
            args = parser.parse_args(["code-stats", str(root), "--format", "json", "--debug", "--debug-every", "1"])

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = args.func(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["summary"]["total_lines"], 1)
        self.assertIn("code-stats: root=", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
