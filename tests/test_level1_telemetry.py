from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from main_computer.diagnostics import LEVELS
from main_computer.level1_telemetry import collect_level1_telemetry, _service_summary


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
        self.assertEqual(report["known_ports"]["app"], 8765)
        self.assertTrue(any(row.get("pid") == os.getpid() for row in report["processes"]))
        current = next(row for row in report["processes"] if row.get("pid") == os.getpid())
        self.assertIn("viewport-current", current.get("roles", []))
        self.assertIn("memory_human", current)
        self.assertIn("command_preview", current)


    def test_service_summary_separates_listeners_from_time_wait_noise(self) -> None:
        summary = _service_summary(
            {"app": 8765},
            [
                {
                    "service": "app",
                    "port": 8765,
                    "pid": 123,
                    "status": "LISTEN",
                    "local": "127.0.0.1:8765",
                    "remote": "",
                }
            ],
            [
                {
                    "service": "app",
                    "port": 8765,
                    "pid": 123,
                    "status": "LISTEN",
                    "local": "127.0.0.1:8765",
                    "remote": "",
                },
                {
                    "service": "app",
                    "port": 8765,
                    "pid": None,
                    "status": "TIME_WAIT",
                    "local": "127.0.0.1:8765",
                    "remote": "127.0.0.1:60000",
                },
            ],
        )

        self.assertEqual(summary[0]["listener_count"], 1)
        self.assertEqual(summary[0]["activity_count"], 2)
        self.assertEqual(summary[0]["time_wait_count"], 1)
        self.assertEqual(summary[0]["active_activity_count"], 1)
        self.assertEqual(summary[0]["state"], "listening")

    def test_collect_level1_telemetry_handles_missing_psutil_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = collect_level1_telemetry(root, control_root=root, known_ports={}, current_pid=os.getpid())

        self.assertIn("capabilities", report)
        self.assertIn("warnings", report)
        self.assertIsInstance(report["summary"]["process_count"], int)


if __name__ == "__main__":
    unittest.main()
