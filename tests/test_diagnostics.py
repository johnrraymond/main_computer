from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from main_computer.config import MainComputerConfig
from main_computer.diagnostics import DiagnosticFailure, DiagnosticRunner, run_from_args
from main_computer.models import ChatResponse


class DiagnosticTests(unittest.TestCase):
    def test_health_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            report = DiagnosticRunner(
                config=MainComputerConfig(workspace=Path.cwd().parent),
                level="health",
                output_dir=Path(temp_dir),
            ).run()

        self.assertTrue(report["ok"])
        self.assertEqual(report["level"], "health")
        self.assertTrue(any(check["name"] == "catalog-loads-projects" for check in report["checks"]))

    def test_server_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            report = DiagnosticRunner(
                config=MainComputerConfig(workspace=Path.cwd().parent),
                level="server",
                output_dir=Path(temp_dir),
            ).run()

        self.assertTrue(report["ok"])
        self.assertTrue(any(check["name"] == "chat-api-roundtrip" for check in report["checks"]))

    def test_run_from_args_defaults(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            args = SimpleNamespace(
                level="health",
                workspace=str(Path.cwd().parent),
                provider=None,
                model=None,
                ollama_base_url=None,
                ollama_timeout_s=None,
                openai_base_url=None,
                url=None,
                output_dir=temp_dir,
                headed=False,
            )
            report = run_from_args(args)

        self.assertTrue(report["ok"])

    def test_run_from_args_accepts_ollama_timeout(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            args = SimpleNamespace(
                level="health",
                workspace=str(Path.cwd().parent),
                provider=None,
                model=None,
                ollama_base_url=None,
                ollama_timeout_s=600.0,
                openai_base_url=None,
                url=None,
                output_dir=temp_dir,
                headed=False,
            )
            report = run_from_args(args)

        self.assertEqual(report["config"]["ollama_timeout_s"], 600.0)

    def test_runner_can_return_failed_report_without_raising(self) -> None:
        def record_failure(runner: DiagnosticRunner) -> None:
            runner._record("forced-failure", False, "health", {"reason": "test"})

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            with patch.object(DiagnosticRunner, "_run_health", record_failure):
                report = DiagnosticRunner(
                    config=MainComputerConfig(workspace=Path.cwd().parent),
                    level="health",
                    output_dir=Path(temp_dir),
                ).run(raise_on_failure=False)

            report_path = Path(temp_dir) / "diagnostics_report.json"
            written = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"][0]["name"], "forced-failure")
        self.assertEqual(written["checks"][0]["name"], "forced-failure")

    def test_runner_attaches_report_to_failure_when_raising(self) -> None:
        def record_failure(runner: DiagnosticRunner) -> None:
            runner._record("forced-failure", False, "health", {"reason": "test"})

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            with patch.object(DiagnosticRunner, "_run_health", record_failure):
                with self.assertRaises(DiagnosticFailure) as failure:
                    DiagnosticRunner(
                        config=MainComputerConfig(workspace=Path.cwd().parent),
                        level="health",
                        output_dir=Path(temp_dir),
                    ).run()

        self.assertIsNotNone(failure.exception.report)
        self.assertIsNotNone(failure.exception.report_path)
        self.assertFalse(failure.exception.report["ok"])

    def test_ollama_visibility_diagnostic_is_special_and_opt_in(self) -> None:
        class FakeOllamaProvider:
            def __init__(self, **kwargs):
                self.model = kwargs["model"]
                self.options = kwargs.get("options")
                self.calls = 0

            def chat(self, messages):
                self.calls += 1
                if self.calls == 1:
                    content = "READY"
                elif self.calls == 2:
                    content = "main_computer, main_computer_test, main_copmputer_production"
                else:
                    content = "TODO.md, README.md, viewport.py, diagnostics.py"
                return ChatResponse(
                    content=content,
                    provider="fake-ollama",
                    model=self.model,
                )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            with patch("main_computer.diagnostics.OllamaProvider", FakeOllamaProvider):
                report = DiagnosticRunner(
                    config=MainComputerConfig(workspace=Path.cwd().parent),
                    level="ollama-visibility",
                    output_dir=Path(temp_dir),
                ).run()

        self.assertTrue(report["ok"])
        self.assertEqual(report["level"], "ollama-visibility")
        check_names = {check["name"] for check in report["checks"]}
        self.assertIn("ollama-single-flight-lock-acquired", check_names)
        self.assertIn("ollama-visibility-primer-responds", check_names)
        self.assertIn("ollama-visibility-project-labels", check_names)
        self.assertIn("ollama-visibility-direct-file-rungs", check_names)
        self.assertIn("ollama-visibility-provider-responds", check_names)
        self.assertIn("ollama-visibility-main-computer-files", check_names)
        self.assertNotIn("browser-widget-harness", check_names)

    def test_ollama_primer_diagnostic_runs_single_fact_ladder(self) -> None:
        class FakeOllamaProvider:
            def __init__(self, **kwargs):
                self.model = kwargs["model"]
                self.options = kwargs.get("options")
                self.calls = 0

            def chat(self, messages):
                self.calls += 1
                responses = ["READY", "COUNT-3", "YES", "YES"]
                return ChatResponse(
                    content=responses[self.calls - 1],
                    provider="fake-ollama",
                    model=self.model,
                )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            with patch("main_computer.diagnostics.OllamaProvider", FakeOllamaProvider):
                report = DiagnosticRunner(
                    config=MainComputerConfig(workspace=Path.cwd().parent),
                    level="ollama-primer",
                    output_dir=Path(temp_dir),
                ).run()

        self.assertTrue(report["ok"])
        check_names = {check["name"] for check in report["checks"]}
        self.assertIn("ollama-single-flight-lock-acquired", check_names)
        self.assertIn("ollama-primer-ready", check_names)
        self.assertIn("ollama-primer-token-count", check_names)
        self.assertIn("ollama-primer-single-label-main-computer", check_names)
        self.assertIn("ollama-primer-single-label-main-computer-test", check_names)
        self.assertNotIn("ollama-visibility-main-computer-files", check_names)

    def test_ollama_probe_records_transport_details(self) -> None:
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps(self.data).encode("utf-8")

        def fake_urlopen(request_or_url, timeout):
            url = request_or_url if isinstance(request_or_url, str) else request_or_url.full_url
            if url.endswith("/api/tags"):
                return FakeResponse({"models": [{"name": "gemma4:26b"}]})
            if url.endswith("/api/generate"):
                return FakeResponse({"response": "READY", "done": True, "eval_count": 1})
            if url.endswith("/api/chat"):
                return FakeResponse({"message": {"content": "READY"}, "done": True, "eval_count": 1})
            raise AssertionError(url)

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            with patch("main_computer.diagnostics.urlopen", fake_urlopen):
                report = DiagnosticRunner(
                    config=MainComputerConfig(workspace=Path.cwd().parent),
                    level="ollama-probe",
                    output_dir=Path(temp_dir),
                ).run()

        self.assertTrue(report["ok"])
        check_names = {check["name"] for check in report["checks"]}
        self.assertIn("ollama-single-flight-lock-acquired", check_names)
        self.assertIn("ollama-probe-tags", check_names)
        self.assertIn("ollama-probe-model-listed", check_names)
        self.assertIn("ollama-probe-generate-completes", check_names)
        self.assertIn("ollama-probe-chat-ready", check_names)
