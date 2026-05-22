from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "project_diagnosis.py"
SPEC = importlib.util.spec_from_file_location("project_diagnosis", SCRIPT_PATH)
assert SPEC is not None
health = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = health
SPEC.loader.exec_module(health)


def make_repo(root: Path) -> None:
    (root / "main_computer").mkdir()
    (root / "main_computer" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    (root / "tools").mkdir()
    (root / "tools" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        "name = \"sample\"\n"
        "[project.scripts]\n"
        "main-computer = \"main_computer.cli:main\"\n",
        encoding="utf-8",
    )
    (root / "main_computer" / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")


class ProjectHealthCheckTests(unittest.TestCase):
    def test_simple_stage_reports_all_source_file_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            make_repo(repo)

            report = health.run_health(repo, stage="simple", max_source_bytes=10_000)

        self.assertTrue(report.ok)
        size_check = next(check for check in report.checks if check.name == "source-file-sizes")
        paths = {item["path"] for item in size_check.detail["files"]}
        self.assertIn("main_computer/__init__.py", paths)
        self.assertIn("tests/test_sample.py", paths)
        self.assertIn("tools/helper.py", paths)

    def test_python_syntax_failure_is_reported_without_writing_pyc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            make_repo(repo)
            bad = repo / "main_computer" / "broken.py"
            bad.write_text("def nope(:\n    pass\n", encoding="utf-8")

            report = health.run_health(repo, stage="simple")
            pycache = repo / "main_computer" / "__pycache__"

        self.assertFalse(report.ok)
        syntax_check = next(check for check in report.checks if check.name == "python-syntax")
        self.assertEqual(syntax_check.status, "fail")
        self.assertFalse(pycache.exists())

    def test_cleanliness_stage_detects_hard_pollution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            make_repo(repo)
            (repo / "main_computer" / "__pycache__").mkdir()
            (repo / "main_computer" / "stale.pyc").write_bytes(b"pollution")

            report = health.run_health(repo, stage="cleanliness")

        pollution = next(check for check in report.checks if check.name == "source-tree-pollution")
        self.assertEqual(pollution.status, "fail")
        hard_paths = {item["path"] for item in pollution.detail["hard"]}
        self.assertIn("main_computer/__pycache__", hard_paths)
        self.assertIn("main_computer/stale.pyc", hard_paths)

    def test_release_reports_are_treated_as_generated_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            make_repo(repo)
            report_dir = repo / "release_reports"
            report_dir.mkdir()
            (report_dir / "rc-20260510-123456Z.tmp").write_text("generated", encoding="utf-8")

            report = health.run_health(repo, stage="cleanliness")

        pollution = next(check for check in report.checks if check.name == "source-tree-pollution")
        self.assertEqual(pollution.status, "pass")


    def test_text_report_is_compact_by_default(self) -> None:
        report = health.HealthReport(
            ok=False,
            stage="all",
            repo_root="/repo",
            elapsed_s=0.01,
            checks=[
                health.HealthCheck(
                    name="source-file-sizes",
                    status="fail",
                    summary="2 source files checked; 1 oversized above 10 B.",
                    detail={
                        "files": [{"path": "main_computer/small.py", "bytes": 5}],
                        "largest_files": [{"path": "main_computer/big.py", "bytes": 100}],
                        "oversized": [{"path": "main_computer/big.py", "bytes": 100}],
                    },
                ),
                health.HealthCheck(
                    name="source-tree-pollution",
                    status="fail",
                    summary="found 1 hard pollution item.",
                    detail={"hard": [{"path": "main_computer/__pycache__", "kind": "directory"}]},
                ),
            ],
        )

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            health.print_text_report(report)

        output = stream.getvalue()
        self.assertIn("[FAIL] source-file-sizes:", output)
        self.assertIn("[FAIL] source-tree-pollution:", output)
        self.assertIn("Run again with --verbose", output)
        self.assertNotIn("main_computer/big.py", output)
        self.assertNotIn("main_computer/__pycache__", output)
        self.assertNotIn('"hard"', output)

    def test_text_report_verbose_includes_diagnostic_details(self) -> None:
        report = health.HealthReport(
            ok=False,
            stage="all",
            repo_root="/repo",
            elapsed_s=0.01,
            checks=[
                health.HealthCheck(
                    name="source-file-sizes",
                    status="fail",
                    summary="2 source files checked; 1 oversized above 10 B.",
                    detail={
                        "files": [{"path": "main_computer/small.py", "bytes": 5}],
                        "largest_files": [{"path": "main_computer/big.py", "bytes": 100}],
                        "oversized": [{"path": "main_computer/big.py", "bytes": 100}],
                    },
                ),
                health.HealthCheck(
                    name="source-tree-pollution",
                    status="fail",
                    summary="found 1 hard pollution item.",
                    detail={"hard": [{"path": "main_computer/__pycache__", "kind": "directory"}]},
                ),
            ],
        )

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            health.print_text_report(report, verbose=True)

        output = stream.getvalue()
        self.assertIn("largest source files:", output)
        self.assertIn("oversized source files:", output)
        self.assertIn("main_computer/big.py", output)
        self.assertIn("main_computer/__pycache__", output)
        self.assertNotIn("Run again with --verbose", output)


    def test_git_status_skips_outside_git_work_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            make_repo(repo)

            check = health.check_git_status(repo, strict_git=True)

        self.assertIn(check.status, {"skip", "warn", "pass", "fail"})
        if check.status == "skip":
            self.assertIn("git", check.summary.lower())


if __name__ == "__main__":
    unittest.main()
