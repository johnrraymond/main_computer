from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main_computer.cli import _config_from_args


def _minimal_args(**overrides: object) -> argparse.Namespace:
    values = {
        "workspace": None,
        "provider": None,
        "model": None,
        "ollama_base_url": None,
        "ollama_timeout_s": None,
        "openai_base_url": None,
        "fallback": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CliConfigFromArgsTests(unittest.TestCase):
    def test_preserves_host_drive_environment_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = {
                "MAIN_COMPUTER_WORKSPACE": tempdir,
                "MAIN_COMPUTER_PATH_MODE": "mounted-windows",
                "MAIN_COMPUTER_HOST_OS": "windows",
                "MAIN_COMPUTER_HOST_DRIVE_ROOT": "/host",
                "MAIN_COMPUTER_WINDOWS_DRIVE_MOUNTS": "C=/host/c;D=/host/d",
            }
            with patch.dict(os.environ, env, clear=False):
                config = _config_from_args(_minimal_args())

        self.assertEqual(config.path_mode, "mounted-windows")
        self.assertEqual(config.host_os, "windows")
        self.assertEqual(config.host_drive_root, Path("/host"))
        self.assertEqual(config.windows_drive_mounts, "C=/host/c;D=/host/d")

    def test_arg_workspace_override_keeps_host_drive_environment_fields(self) -> None:
        with tempfile.TemporaryDirectory() as env_workspace, tempfile.TemporaryDirectory() as arg_workspace:
            env = {
                "MAIN_COMPUTER_WORKSPACE": env_workspace,
                "MAIN_COMPUTER_PATH_MODE": "mounted-windows",
                "MAIN_COMPUTER_HOST_OS": "windows",
                "MAIN_COMPUTER_HOST_DRIVE_ROOT": "/host",
            }
            with patch.dict(os.environ, env, clear=False):
                config = _config_from_args(_minimal_args(workspace=arg_workspace))

            self.assertEqual(config.workspace, Path(arg_workspace))
            self.assertEqual(config.path_mode, "mounted-windows")
            self.assertEqual(config.host_os, "windows")
            self.assertEqual(config.host_drive_root, Path("/host"))


    def test_preserves_executor_backend_environment_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env = {
                "MAIN_COMPUTER_WORKSPACE": tempdir,
                "MAIN_COMPUTER_EXECUTOR_ENABLED": "1",
                "MAIN_COMPUTER_EXECUTOR_BACKEND": "wsl",
                "MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION": "MainComputer-maincomputer-010d7428-debug",
                "MAIN_COMPUTER_EXECUTOR_WSL_COMMAND": "wsl.exe",
                "MAIN_COMPUTER_EXECUTOR_ROOT": str(Path(tempdir) / ".main-computer" / "instances" / "debug" / "runtime" / "executor"),
                "MAIN_COMPUTER_INSTALL_MODE": "debug",
                "MAIN_COMPUTER_MODE_LABEL": "Debug",
                "MAIN_COMPUTER_GUIDANCE_LEVEL": "debug",
            }
            with patch.dict(os.environ, env, clear=False):
                config = _config_from_args(_minimal_args())

        self.assertTrue(config.executor_enabled)
        self.assertEqual(config.executor_backend, "wsl")
        self.assertEqual(config.executor_wsl_distribution, "MainComputer-maincomputer-010d7428-debug")
        self.assertEqual(config.executor_wsl_command, "wsl.exe")
        self.assertEqual(config.executor_root, Path(env["MAIN_COMPUTER_EXECUTOR_ROOT"]))
        self.assertEqual(config.install_mode, "debug")
        self.assertEqual(config.mode_label, "Debug")
        self.assertEqual(config.guidance_level, "debug")


if __name__ == "__main__":
    unittest.main()
