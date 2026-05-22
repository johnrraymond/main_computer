from __future__ import annotations

import contextlib
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from main_computer.cli import build_parser
from main_computer.log_rotator import default_archive_root, main as log_rotator_main, rotate_logs, selected_archive_root_from_args



@contextlib.contextmanager
def pushd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


class LogRotatorTests(unittest.TestCase):
    def test_default_archive_root_is_sibling_archive_logs(self) -> None:
        log_root = Path("work") / "logs"

        archive_root = default_archive_root(log_root)

        self.assertEqual(archive_root, Path("work") / "archive" / "logs")

    def test_rotates_old_files_into_matching_archive_tree(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            base = Path(temp_dir)
            log_root = base / "logs"
            archive_root = base / "archive" / "logs"
            old_log = log_root / "service" / "worker.log"
            fresh_log = log_root / "service" / "current.log"
            old_log.parent.mkdir(parents=True)
            old_log.write_bytes(b"old log\n")
            fresh_log.write_bytes(b"fresh log\n")

            now = time.time()
            os.utime(old_log, (now - 4 * 24 * 60 * 60, now - 4 * 24 * 60 * 60))
            os.utime(fresh_log, (now - 1 * 24 * 60 * 60, now - 1 * 24 * 60 * 60))

            report = rotate_logs(log_root, archive_root=archive_root, now=now)

            archived = archive_root / "service" / "worker.log.zip"
            self.assertEqual(report.rotated_count, 1)
            self.assertEqual(report.error_count, 0)
            self.assertFalse(old_log.exists())
            self.assertTrue(fresh_log.exists())
            self.assertTrue(archived.exists())
            with zipfile.ZipFile(archived) as archive:
                self.assertEqual(archive.namelist(), ["worker.log"])
                self.assertEqual(archive.read("worker.log").decode("utf-8"), "old log\n")

    def test_dry_run_reports_without_moving_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            base = Path(temp_dir)
            log_root = base / "logs"
            old_log = log_root / "system.log"
            old_log.parent.mkdir(parents=True)
            old_log.write_text("old log\n", encoding="utf-8")
            now = time.time()
            os.utime(old_log, (now - 4 * 24 * 60 * 60, now - 4 * 24 * 60 * 60))

            report = rotate_logs(log_root, now=now, dry_run=True)

            self.assertEqual(report.rotated_count, 1)
            self.assertTrue(old_log.exists())
            self.assertFalse((base / "archive" / "logs" / "system.log.zip").exists())

    def test_existing_archive_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            base = Path(temp_dir)
            log_root = base / "logs"
            archive_root = base / "archive" / "logs"
            old_log = log_root / "app.log"
            archived = archive_root / "app.log.zip"
            old_log.parent.mkdir(parents=True)
            archived.parent.mkdir(parents=True)
            old_log.write_text("new old log\n", encoding="utf-8")
            archived.write_bytes(b"existing archive")
            now = time.time()
            os.utime(old_log, (now - 4 * 24 * 60 * 60, now - 4 * 24 * 60 * 60))

            report = rotate_logs(log_root, archive_root=archive_root, now=now)

            self.assertEqual(report.rotated_count, 0)
            self.assertEqual(report.error_count, 1)
            self.assertTrue(old_log.exists())
            self.assertEqual(archived.read_bytes(), b"existing archive")

    def test_cli_parser_exposes_rotate_logs_defaults(self) -> None:
        args = build_parser().parse_args(["rotate-logs"])

        self.assertEqual(args.log_root, "logs")
        self.assertIsNone(args.archive_root)
        self.assertIsNone(args.archive_root_option)
        self.assertEqual(args.max_age_days, 3.0)
        self.assertFalse(args.dry_run)

    def test_cli_parser_accepts_positional_archive_root(self) -> None:
        args = build_parser().parse_args(["rotate-logs", "logs", "../archive/logs", "--dry-run"])

        self.assertEqual(args.log_root, "logs")
        self.assertEqual(args.archive_root, "../archive/logs")
        self.assertEqual(selected_archive_root_from_args(args), "../archive/logs")
        self.assertTrue(args.dry_run)

    def test_cli_parser_accepts_named_archive_root(self) -> None:
        args = build_parser().parse_args(["rotate-logs", "logs", "--archive-root", "../archive/logs"])

        self.assertEqual(args.log_root, "logs")
        self.assertIsNone(args.archive_root)
        self.assertEqual(args.archive_root_option, "../archive/logs")
        self.assertEqual(selected_archive_root_from_args(args), "../archive/logs")

    def test_direct_module_main_prints_dry_run_report(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            base = Path(temp_dir)
            log_root = base / "logs"
            archive_root = base / "archive" / "logs"
            old_log = log_root / "system.log"
            old_log.parent.mkdir(parents=True)
            old_log.write_text("old log\n", encoding="utf-8")
            now = time.time()
            os.utime(old_log, (now - 4 * 24 * 60 * 60, now - 4 * 24 * 60 * 60))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = log_rotator_main([str(log_root), str(archive_root), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Would rotate 1 of 1 scanned files older than 3 days", output.getvalue())
            self.assertIn("system.log.zip", output.getvalue())
            self.assertTrue(old_log.exists())

    def test_default_logs_alias_rotates_generated_documentation_plan_history(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            project_root = Path(temp_dir)
            archive_root = project_root.parent / "archive" / "logs"
            docs_tools_root = project_root / "tools" / "documentation"
            (project_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (project_root / "main_computer").mkdir()
            docs_tools_root.mkdir(parents=True)

            old_plan = docs_tools_root / "plan-20260501T173716Z-1d1d88fcc7.py"
            old_state = docs_tools_root / "plan-20260501T173716Z-1d1d88fcc7.state.json"
            fresh_plan = docs_tools_root / "plan-20260514T173716Z-1d1d88fcc7.py"
            helper = docs_tools_root / "helper.py"

            old_plan.write_text("old generated plan\n", encoding="utf-8")
            old_state.write_text('{"completed": {}}\n', encoding="utf-8")
            fresh_plan.write_text("fresh generated plan\n", encoding="utf-8")
            helper.write_text("source helper, not generated history\n", encoding="utf-8")

            now = time.time()
            old_time = now - 4 * 24 * 60 * 60
            fresh_time = now - 1 * 24 * 60 * 60
            os.utime(old_plan, (old_time, old_time))
            os.utime(old_state, (old_time, old_time))
            os.utime(fresh_plan, (fresh_time, fresh_time))
            os.utime(helper, (old_time, old_time))

            with pushd(project_root):
                report = rotate_logs("logs", archive_root=archive_root, now=now, dry_run=True)

            self.assertEqual(report.log_root, project_root.resolve())
            self.assertEqual(report.scanned_files, 3)
            self.assertEqual(report.rotated_count, 2)
            self.assertEqual(report.error_count, 0)
            archives = {item.archive for item in report.rotated}
            self.assertIn(archive_root / "tools" / "documentation" / f"{old_plan.name}.zip", archives)
            self.assertIn(archive_root / "tools" / "documentation" / f"{old_state.name}.zip", archives)
            self.assertNotIn(archive_root / "tools" / "documentation" / f"{fresh_plan.name}.zip", archives)
            self.assertNotIn(archive_root / "tools" / "documentation" / f"{helper.name}.zip", archives)


    def test_default_logs_alias_rotates_generated_documentation_build_reports(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            project_root = Path(temp_dir)
            archive_root = project_root.parent / "archive" / "logs"
            generated_root = project_root / "generated_component_docs"
            (project_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (project_root / "main_computer").mkdir()
            generated_root.mkdir(parents=True)

            doc_build = generated_root / "doc-build.json"
            doc_health = generated_root / "doc-health.json"
            graph = generated_root / "graph.json"
            manifest = generated_root / "manifest.json"

            doc_build.write_text('{"queue": []}\n', encoding="utf-8")
            doc_health.write_text('{"errors": []}\n', encoding="utf-8")
            graph.write_text('{"nodes": []}\n', encoding="utf-8")
            manifest.write_text('{"entries": []}\n', encoding="utf-8")

            now = time.time()
            old_time = now - 4 * 24 * 60 * 60
            fresh_time = now - 1 * 24 * 60 * 60
            os.utime(doc_build, (old_time, old_time))
            os.utime(doc_health, (old_time, old_time))
            os.utime(graph, (fresh_time, fresh_time))
            os.utime(manifest, (old_time, old_time))

            with pushd(project_root):
                report = rotate_logs("logs", archive_root=archive_root, now=now, dry_run=True)

            archives = {item.archive for item in report.rotated}
            self.assertEqual(report.log_root, project_root.resolve())
            self.assertEqual(report.scanned_files, 3)
            self.assertEqual(report.rotated_count, 2)
            self.assertEqual(report.error_count, 0)
            self.assertIn(archive_root / "generated_component_docs" / "doc-build.json.zip", archives)
            self.assertIn(archive_root / "generated_component_docs" / "doc-health.json.zip", archives)
            self.assertNotIn(archive_root / "generated_component_docs" / "graph.json.zip", archives)
            self.assertNotIn(archive_root / "generated_component_docs" / "manifest.json.zip", archives)

    def test_default_logs_alias_uses_project_profile_when_logs_dir_is_absent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            project_root = Path(temp_dir)
            archive_root = project_root.parent / "archive" / "logs"
            (project_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (project_root / "main_computer").mkdir()
            top_level_log = project_root / "aider.log"
            nested_log = project_root / "generated_component_docs" / "work" / "run-1" / "logs" / "overview.txt"
            fresh_log = project_root / "generated_component_docs" / "work" / "run-2" / "logs" / "current.log"
            non_log = project_root / "README.txt"

            nested_log.parent.mkdir(parents=True)
            fresh_log.parent.mkdir(parents=True)
            top_level_log.write_text("old top-level log\n", encoding="utf-8")
            nested_log.write_text("old nested log\n", encoding="utf-8")
            fresh_log.write_text("fresh nested log\n", encoding="utf-8")
            non_log.write_text("documentation, not a log\n", encoding="utf-8")

            now = time.time()
            old_time = now - 4 * 24 * 60 * 60
            fresh_time = now - 1 * 24 * 60 * 60
            os.utime(top_level_log, (old_time, old_time))
            os.utime(nested_log, (old_time, old_time))
            os.utime(fresh_log, (fresh_time, fresh_time))
            os.utime(non_log, (old_time, old_time))

            with pushd(project_root):
                report = rotate_logs("logs", archive_root=archive_root, now=now, dry_run=True)

            self.assertEqual(report.log_root, project_root.resolve())
            self.assertEqual(report.rotated_count, 2)
            self.assertEqual(report.error_count, 0)
            self.assertEqual(report.scanned_files, 3)
            archives = {item.archive for item in report.rotated}
            self.assertIn(archive_root / "aider.log.zip", archives)
            self.assertIn(
                archive_root / "generated_component_docs" / "work" / "run-1" / "logs" / "overview.txt.zip",
                archives,
            )
            self.assertNotIn(archive_root / "README.txt.zip", archives)
            self.assertTrue(top_level_log.exists())
            self.assertTrue(nested_log.exists())

    def test_default_logs_alias_rotates_old_diagnostic_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            project_root = Path(temp_dir)
            archive_root = project_root.parent / "archive" / "logs"
            (project_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (project_root / "main_computer").mkdir()

            old_report = project_root / "diagnostics_output" / "health" / "diagnostics_report.json"
            old_named_report = project_root / "diagnostics_output_ollama_probe" / "diagnostics_report.json"
            old_harness_report = project_root / "harness_output_widgets" / "viewport" / "summary.json"
            old_component_work = (
                project_root
                / "generated_component_docs"
                / "work"
                / "run-1"
                / "evidence"
                / "overview.json"
            )
            fresh_report = project_root / "diagnostics_output" / "widgets" / "diagnostics_report.json"
            stable_component_doc = project_root / "generated_component_docs" / "nodes" / "overview.html"
            existing_archive_artifact = project_root / "generated_component_docs" / "archive" / "old.html"

            for artifact in (
                old_report,
                old_named_report,
                old_harness_report,
                old_component_work,
                fresh_report,
                stable_component_doc,
                existing_archive_artifact,
            ):
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(f"{artifact.name}\n", encoding="utf-8")

            now = time.time()
            old_time = now - 4 * 24 * 60 * 60
            fresh_time = now - 1 * 24 * 60 * 60
            old_artifacts = (
                old_report,
                old_named_report,
                old_harness_report,
                old_component_work,
                stable_component_doc,
                existing_archive_artifact,
            )
            for artifact in old_artifacts:
                os.utime(artifact, (old_time, old_time))
            os.utime(fresh_report, (fresh_time, fresh_time))

            with pushd(project_root):
                report = rotate_logs("logs", archive_root=archive_root, now=now, dry_run=True)

            archives = {item.archive for item in report.rotated}
            self.assertEqual(report.log_root, project_root.resolve())
            self.assertEqual(report.rotated_count, 4)
            self.assertEqual(report.error_count, 0)
            self.assertEqual(report.scanned_files, 5)
            self.assertIn(
                archive_root / "diagnostics_output" / "health" / "diagnostics_report.json.zip",
                archives,
            )
            self.assertIn(
                archive_root / "diagnostics_output_ollama_probe" / "diagnostics_report.json.zip",
                archives,
            )
            self.assertIn(
                archive_root / "harness_output_widgets" / "viewport" / "summary.json.zip",
                archives,
            )
            self.assertIn(
                archive_root / "generated_component_docs" / "work" / "run-1" / "evidence" / "overview.json.zip",
                archives,
            )
            self.assertNotIn(
                archive_root / "diagnostics_output" / "widgets" / "diagnostics_report.json.zip",
                archives,
            )
            self.assertNotIn(
                archive_root / "generated_component_docs" / "nodes" / "overview.html.zip",
                archives,
            )
            self.assertNotIn(
                archive_root / "generated_component_docs" / "archive" / "old.html.zip",
                archives,
            )

    def test_direct_module_main_uses_project_profile_for_logs_alias(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            project_root = Path(temp_dir)
            archive_root = project_root.parent / "archive" / "logs"
            (project_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (project_root / "main_computer").mkdir()
            old_log = project_root / "generated_component_docs" / "work" / "run-1" / "logs" / "overview.log"
            old_log.parent.mkdir(parents=True)
            old_log.write_text("old project log\n", encoding="utf-8")
            now = time.time()
            old_time = now - 4 * 24 * 60 * 60
            os.utime(old_log, (old_time, old_time))

            output = io.StringIO()
            with pushd(project_root), contextlib.redirect_stdout(output):
                exit_code = log_rotator_main(["logs", str(archive_root), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Would rotate 1 of 1 scanned files older than 3 days", output.getvalue())
            self.assertIn("generated_component_docs", output.getvalue())
            self.assertIn("overview.log.zip", output.getvalue())

    def test_missing_non_alias_log_root_still_errors(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (project_root / "main_computer").mkdir()

            with pushd(project_root):
                with self.assertRaises(FileNotFoundError):
                    rotate_logs("not-the-default-logs-alias", dry_run=True)



if __name__ == "__main__":
    unittest.main()
