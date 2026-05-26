from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from main_computer.diagnostics import LEVELS
from main_computer.level1_telemetry import collect_level1_telemetry


class Level1TelemetryTests(unittest.TestCase):
    def test_level1_telemetry_is_diagnostic_level(self) -> None:
        self.assertIn("level-1-telemetry", LEVELS)

    def test_collect_level1_telemetry_includes_current_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".main_computer_viewport.pid").write_text(str(os.getpid()), encoding="utf-8")
            report = collect_level1_telemetry(
                root,
                control_root=root,
                known_ports={"app": 8765, "heartbeat": 8766},
                current_pid=os.getpid(),
            )

        self.assertEqual(report["level"], "level-1-telemetry")
        self.assertIn("summary", report)
        self.assertIn("processes", report)
        self.assertIn("pid_files", report)
        self.assertIn("pid_file_health", report)
        self.assertIn("operator_summary", report)
        self.assertIn("service_summary", report)
        self.assertIn("role_summary", report)
        self.assertIn("top_processes", report)
        self.assertIn("port_activity", report)
        self.assertIn("port_listeners", report)
        self.assertEqual(report["known_ports"]["app"], 8765)
        self.assertIn("known_port_time_wait_count", report["summary"])
        self.assertIn("active_known_port_activity_count", report["summary"])
        self.assertTrue(any(row.get("pid") == os.getpid() for row in report["processes"]))
        current = next(row for row in report["processes"] if row.get("pid") == os.getpid())
        self.assertIn("viewport-current", current.get("roles", []))
        self.assertIn("memory_human", current)
        self.assertIn("command_preview", current)

    def test_collect_level1_telemetry_service_summary_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = collect_level1_telemetry(
                root,
                control_root=root,
                known_ports={"app": 8765, "worker": 8771},
                current_pid=os.getpid(),
            )

        services = {row["service"]: row for row in report["service_summary"]}
        self.assertEqual(services["app"]["port"], 8765)
        self.assertIn(services["worker"]["state"], {"listening", "active-no-listener", "not-observed"})
        self.assertIsInstance(report["operator_summary"]["attention"], list)
        self.assertIsInstance(report["operator_summary"]["next_checks"], list)

    def test_collect_level1_telemetry_handles_missing_psutil_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = collect_level1_telemetry(root, control_root=root, known_ports={}, current_pid=os.getpid())

        self.assertIn("capabilities", report)
        self.assertIn("warnings", report)
        self.assertIn("observations", report)
        self.assertIsInstance(report["summary"]["process_count"], int)


if __name__ == "__main__":
    unittest.main()
