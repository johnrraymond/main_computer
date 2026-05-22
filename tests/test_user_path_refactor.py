from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest import mock

from main_computer.config import MainComputerConfig
from main_computer.git_tools import GitToolsService
from main_computer.harness import HarnessCatalog, harness_workspace


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_SOURCE_FILES = (
    "dev-control.ps1",
    "export-main-computer-test.ps1",
    "main_computer/config.py",
    "main_computer/git_tools.py",
    "main_computer/harness.py",
)

USER_PATH_CLEANUP_FILES = (
    "main_computer/twiddle-v2-docker-names.ps1",
    "tools/debug-onlyoffice-diagnose.ps1",
    "tools/debug-wsl-executor-smoke.ps1",
    "tools/rag_debug_website_golden_path_smoke.py",
    "tools/tmp-install-mathics-slowness-probe.ps1",
    "tools/tmp-pip-upgrade-hang-probe.ps1",
    "tools/tmp-python-bootstrap-diagnose-v2.ps1",
)


class UserPathRefactorTests(unittest.TestCase):
    def test_refactored_runtime_sources_do_not_embed_local_windows_user_paths(self) -> None:
        for rel in RUNTIME_SOURCE_FILES:
            with self.subTest(rel=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn("C:\\Users\\", text)

    def test_diagnostic_tools_do_not_embed_machine_specific_python_or_user_paths(self) -> None:
        leaked_user = "".join(("su", "b", "si"))
        sub_token = "".join(("su", "b"))
        pinned_windowsapps_python = "".join((
            "PythonSoftwareFoundation.Python.",
            "3.12_",
            "3.12.2800.0_",
            "x64__qbz5n2kfra8p0",
        ))
        split_patterns = (
            re.compile(r"""["']""" + re.escape(sub_token) + r"""["']\s*\+\s*["']si["']""", re.IGNORECASE),
            re.compile(r"""["']""" + re.escape(sub_token) + r"""["']\s+["']si["']""", re.IGNORECASE),
        )

        for rel in USER_PATH_CLEANUP_FILES:
            with self.subTest(rel=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                lowered = text.lower()
                self.assertNotIn(leaked_user, lowered)
                self.assertNotIn("c:\\users\\", lowered)
                self.assertNotIn(pinned_windowsapps_python.lower(), lowered)
                for pattern in split_patterns:
                    self.assertIsNone(pattern.search(text))


    def test_config_default_workspace_is_home_derived_when_env_is_unset(self) -> None:
        fake_home = Path("/tmp/main-computer-home")
        with mock.patch.dict(os.environ, {"MAIN_COMPUTER_WORKSPACE": ""}):
            with mock.patch.object(Path, "home", return_value=fake_home):
                config = MainComputerConfig.from_env()

        self.assertEqual(config.workspace, fake_home / "dsl")

    def test_config_workspace_env_still_wins(self) -> None:
        configured = Path("/tmp/configured-workspace")
        with mock.patch.dict(os.environ, {"MAIN_COMPUTER_WORKSPACE": str(configured)}):
            config = MainComputerConfig.from_env()

        self.assertEqual(config.workspace, configured)

    def test_git_tools_default_worktree_is_home_derived_and_env_overridable(self) -> None:
        fake_home = Path("/tmp/main-computer-home")
        with mock.patch.dict(os.environ, {"MAIN_COMPUTER_DEFAULT_MCT_WORKTREE": ""}):
            with mock.patch.object(Path, "home", return_value=fake_home):
                service = GitToolsService(ROOT)
                default_project = service._default_work_project_record()

        self.assertEqual(default_project["path"], str(fake_home / "mct"))

        configured = Path("/tmp/custom-mct")
        with mock.patch.dict(os.environ, {"MAIN_COMPUTER_DEFAULT_MCT_WORKTREE": str(configured)}):
            service = GitToolsService(ROOT)
            default_project = service._default_work_project_record()

        self.assertEqual(default_project["path"], str(configured))

    def test_harness_workspace_is_home_derived_and_env_overridable(self) -> None:
        fake_home = Path("/tmp/main-computer-home")
        with mock.patch.dict(os.environ, {
            "MAIN_COMPUTER_HARNESS_WORKSPACE": "",
            "MAIN_COMPUTER_WORKSPACE": "",
        }):
            with mock.patch.object(Path, "home", return_value=fake_home):
                self.assertEqual(harness_workspace(), fake_home / "dsl")

        configured = Path("/tmp/harness-workspace")
        with mock.patch.dict(os.environ, {"MAIN_COMPUTER_HARNESS_WORKSPACE": str(configured)}):
            catalog = HarnessCatalog()

        self.assertEqual(catalog.projects[0].path, configured / "widget_project_001")
        self.assertEqual(catalog.projects[-1].path, configured / "main_computer_test")

    def test_script_defaults_are_derived_not_hardcoded(self) -> None:
        export_script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")
        dev_script = (ROOT / "dev-control.ps1").read_text(encoding="utf-8")

        self.assertIn('$ArchiveRoot = Join-Path (Split-Path -Parent $SourceRoot) "archive"', export_script)
        self.assertIn('[Environment]::GetFolderPath("UserProfile")', dev_script)
        self.assertIn('Join-Path $userDslRoot ".venv\\Scripts\\python.exe"', dev_script)


if __name__ == "__main__":
    unittest.main()
