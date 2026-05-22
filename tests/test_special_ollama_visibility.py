from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.diagnostics import DiagnosticRunner


@unittest.skipUnless(
    os.environ.get("MAIN_COMPUTER_RUN_SPECIAL_OLLAMA_TESTS") == "1",
    "set MAIN_COMPUTER_RUN_SPECIAL_OLLAMA_TESTS=1 to run real Ollama visibility tests",
)
class SpecialOllamaVisibilityTests(unittest.TestCase):
    def test_ollama_transport_probe(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            report = DiagnosticRunner(
                config=MainComputerConfig.from_env(),
                level="ollama-probe",
                output_dir=Path(temp_dir),
            ).run()

        self.assertTrue(report["ok"])
        check_names = {check["name"] for check in report["checks"]}
        self.assertIn("ollama-probe-tags", check_names)
        self.assertIn("ollama-probe-generate-completes", check_names)
        self.assertIn("ollama-probe-chat-ready", check_names)

    def test_ollama_primer_ladder(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            report = DiagnosticRunner(
                config=MainComputerConfig.from_env(),
                level="ollama-primer",
                output_dir=Path(temp_dir),
            ).run()

        self.assertTrue(report["ok"])
        check_names = {check["name"] for check in report["checks"]}
        self.assertIn("ollama-primer-ready", check_names)
        self.assertIn("ollama-primer-token-count", check_names)

    def test_ollama_visibility_of_main_computer_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            report = DiagnosticRunner(
                config=MainComputerConfig.from_env(),
                level="ollama-visibility",
                output_dir=Path(temp_dir),
            ).run()

        self.assertTrue(report["ok"])
        check_names = {check["name"] for check in report["checks"]}
        self.assertIn("ollama-visibility-provider-responds", check_names)
        self.assertIn("ollama-visibility-main-computer-files", check_names)
