from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "coolify_cluster.py"

spec = importlib.util.spec_from_file_location("coolify_cluster", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
coolify_cluster = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coolify_cluster
spec.loader.exec_module(coolify_cluster)


PRIVATE_STATE = """
coolify:
  project_name: Main Computer
  hosts:
    A:
      name: coolify-a
      url: http://198.51.100.10:8000
      api_token: token-a
    B:
      name: coolify-b
      url: http://198.51.100.11:8000
      api_token: token-b
""".lstrip()


PRIVATE_STATE_WITHOUT_PROJECT = """
coolify:
  hosts:
    A:
      name: coolify-a
      url: http://198.51.100.10:8000
      api_token: token-a
    B:
      name: coolify-b
      url: http://198.51.100.11:8000
      api_token: token-b
""".lstrip()


class CoolifyClusterOrchestratorTests(unittest.TestCase):
    def test_preflight_reports_missing_private_state_before_writing_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            missing_state = Path(tmp) / "missing.private.yaml"
            args = coolify_cluster.parse_args(
                [
                    "preflight",
                    "testnet",
                    "--hubs",
                    "testnet-hub1",
                    "--fdb",
                    "testnet-fdb1",
                    "--git-repo",
                    "https://github.com/example/main_computer",
                    "--packet",
                    str(packet_path),
                    "--private-state",
                    str(missing_state),
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            result = coolify_cluster.preflight_result(args, packet)

            self.assertFalse(result["ok"])
            self.assertFalse(packet_path.exists())
            codes = {item["code"] for item in result["preflight"]["problems"]}
            self.assertIn("missing_or_invalid_private_state", codes)
            self.assertIn("python .\\tools\\sync_private_state.py --write --no-live-check", result["preflight"]["next_commands"])

    def test_plan_writes_packet_and_renders_fdb_and_hub_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            state_path = Path(tmp) / "main_computer.private.yaml"
            state_path.write_text(PRIVATE_STATE, encoding="utf-8")
            args = coolify_cluster.parse_args(
                [
                    "plan",
                    "testnet",
                    "--hubs",
                    "testnet-hub1,testnet-hub2,testnet-hub3",
                    "--fdb",
                    "testnet-fdb1,testnet-fdb2,testnet-fdb3",
                    "--git-repo",
                    "https://github.com/example/main_computer",
                    "--packet",
                    str(packet_path),
                    "--private-state",
                    str(state_path),
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            result = coolify_cluster.cluster_plan_or_apply_result(args, packet)

            self.assertTrue(result["ok"])
            self.assertTrue(packet_path.exists())
            self.assertEqual(result["packet"]["path"], str(packet_path))
            self.assertEqual(result["packet"]["enabled_hubs"], ["testnet-hub1", "testnet-hub2", "testnet-hub3"])
            self.assertEqual(result["packet"]["enabled_fdb"], ["testnet-fdb1", "testnet-fdb2", "testnet-fdb3"])
            self.assertIsNotNone(result["stages"]["foundationdb"])
            self.assertIsNotNone(result["stages"]["hub"])
            self.assertEqual(result["preflight"]["context_sources"]["coolify_project_name"], "private-state:coolify.project_name")
            self.assertEqual(result["stages"]["foundationdb"]["plan"]["coolify_project_name"], "Main Computer")
            self.assertEqual(result["stages"]["hub"]["plan"]["coolify_project_name"], "Main Computer")
            self.assertEqual(result["stages"]["foundationdb"]["plan"]["servers"][0]["coolify_url_source"], "private-state:coolify.hosts.A.url")
            self.assertEqual(result["stages"]["hub"]["plan"]["servers"][1]["coolify_url_source"], "private-state:coolify.hosts.B.url")
            self.assertTrue(result["stages"]["hub"]["plan"]["servers"][0]["traefik_dynamic_config"]["installed"])

    def test_preflight_reports_missing_project_before_lower_apply_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            state_path = Path(tmp) / "main_computer.private.yaml"
            state_path.write_text(PRIVATE_STATE_WITHOUT_PROJECT, encoding="utf-8")
            args = coolify_cluster.parse_args(
                [
                    "apply",
                    "testnet",
                    "--hubs",
                    "testnet-hub1,testnet-hub2,testnet-hub3",
                    "--fdb",
                    "testnet-fdb1,testnet-fdb2,testnet-fdb3",
                    "--git-repo",
                    "https://github.com/example/main_computer",
                    "--packet",
                    str(packet_path),
                    "--private-state",
                    str(state_path),
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            result = coolify_cluster.cluster_plan_or_apply_result(args, packet)

            self.assertFalse(result["ok"])
            self.assertFalse(packet_path.exists())
            codes = {item["code"] for item in result["preflight"]["problems"]}
            self.assertIn("missing_coolify_project", codes)
            self.assertTrue(any("coolify.project_name" in command for command in result["preflight"]["next_commands"]))

    def test_no_private_state_requires_explicit_urls_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            args = coolify_cluster.parse_args(
                [
                    "preflight",
                    "testnet",
                    "--hubs",
                    "testnet-hub1,testnet-hub2,testnet-hub3",
                    "--fdb",
                    "testnet-fdb1,testnet-fdb2,testnet-fdb3",
                    "--git-repo",
                    "https://github.com/example/main_computer",
                    "--packet",
                    str(packet_path),
                    "--no-private-state",
                    "--coolify-project-name",
                    "Main Computer",
                    "--set-coolify-url",
                    "coolify-a:http://198.51.100.10:8000",
                    "--set-coolify-url",
                    "coolify-b:http://198.51.100.11:8000",
                    "--set-coolify-token",
                    "coolify-a:token-a",
                    "--set-coolify-token",
                    "coolify-b:token-b",
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            result = coolify_cluster.preflight_result(args, packet)

            self.assertTrue(result["ok"])
            self.assertFalse(packet_path.exists())
            warning_codes = {item["code"] for item in result["preflight"]["warnings"]}
            self.assertIn("private_state_disabled", warning_codes)


    def test_no_traefik_sidecar_is_preserved_in_next_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            state_path = Path(tmp) / "main_computer.private.yaml"
            state_path.write_text(PRIVATE_STATE, encoding="utf-8")
            args = coolify_cluster.parse_args(
                [
                    "preflight",
                    "testnet",
                    "--hubs",
                    "testnet-hub1,testnet-hub2,testnet-hub3",
                    "--fdb",
                    "testnet-fdb1,testnet-fdb2,testnet-fdb3",
                    "--git-repo",
                    "https://github.com/example/main_computer",
                    "--packet",
                    str(packet_path),
                    "--private-state",
                    str(state_path),
                    "--no-traefik-sidecar",
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            result = coolify_cluster.preflight_result(args, packet)

            self.assertTrue(result["ok"])
            self.assertTrue(all("--no-traefik-sidecar" in command for command in result["preflight"]["next_commands"]))



if __name__ == "__main__":
    unittest.main()
