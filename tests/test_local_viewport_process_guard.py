from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class LocalViewportProcessGuardTests(unittest.TestCase):
    def test_control_script_guards_against_foreign_port_listeners(self) -> None:
        script = (REPO_ROOT / "control-main-computer.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$AllowForeignPortListener", script)
        self.assertIn('Alias("auto-allow")', script)
        self.assertIn("[switch]$AutoAllow", script)
        self.assertIn("[CmdletBinding(PositionalBinding = $false)]", script)
        self.assertIn("[Parameter(ValueFromRemainingArguments = $true)]", script)
        self.assertIn('$argKey -eq "--auto-allow"', script)
        self.assertIn('"-bindhost"', script)
        self.assertIn('"-port"', script)
        self.assertIn('"-workspace"', script)
        self.assertIn("$unexpectedRemainingArgs.Count -gt 0", script)
        self.assertIn("function Assert-NoForeignPortListeners", script)
        self.assertIn("localhost may resolve to IPv6", script)
        self.assertIn("docker stop main-computer-dev-main-computer-1", script)
        self.assertIn(".\\dev-control.ps1 shutdown -Mode local", script)
        self.assertIn("Assert-NoForeignPortListeners -Context", script)

    def test_control_script_redirects_manual_users_to_dev_control(self) -> None:
        script = (REPO_ROOT / "control-main-computer.ps1").read_text(encoding="utf-8")

        self.assertIn("control-main-computer.ps1 is now an internal local-mode helper", script)
        self.assertIn(".\\dev-control.ps1 start -Mode local", script)
        self.assertIn(".\\dev-control.ps1 start -Mode docker", script)
        self.assertIn("Automated callers that intentionally depend on this legacy helper must pass --auto-allow", script)
        self.assertIn("if (-not $AutoAllow)", script)

    def test_docker_viewport_uses_non_conflicting_host_port_by_default(self) -> None:
        compose = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

        self.assertIn("${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765", compose)
        self.assertNotIn('"8765:8765"', compose)


if __name__ == "__main__":
    unittest.main()
