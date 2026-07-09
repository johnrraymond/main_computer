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



    def test_recreate_hub_stacks_is_forwarded_to_hub_stage_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            args = coolify_cluster.parse_args(
                [
                    "plan",
                    "testnet",
                    "--hubs",
                    "testnet-hub1,testnet-hub2",
                    "--fdb",
                    "testnet-fdb1,testnet-fdb2",
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
                    "--recreate-hub-stacks",
                ]
            )

            hub_args = coolify_cluster.hub_args_for_cluster(args, packet_path)
            fdb_args = coolify_cluster.fdb_args_for_cluster(args, packet_path)

            self.assertTrue(hub_args.recreate_hub_stacks)
            self.assertFalse(hasattr(fdb_args, "recreate_hub_stacks"))


    def test_no_fdb_port_guard_is_forwarded_to_fdb_stage_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            args = coolify_cluster.parse_args(
                [
                    "plan",
                    "testnet",
                    "--hubs",
                    "testnet-hub1,testnet-hub2",
                    "--fdb",
                    "testnet-fdb1,testnet-fdb2",
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
                    "--no-fdb-port-guard",
                ]
            )

            fdb_args = coolify_cluster.fdb_args_for_cluster(args, packet_path)
            hub_args = coolify_cluster.hub_args_for_cluster(args, packet_path)

            self.assertTrue(fdb_args.no_fdb_port_guard)
            self.assertFalse(hasattr(hub_args, "no_fdb_port_guard"))


    def test_service_state_detects_ready_and_unhealthy_tokens(self) -> None:
        ready = coolify_cluster.coolify_service_state({"status": "running", "health": "healthy"})
        blocked = coolify_cluster.coolify_service_state({"status": "running", "health": "unhealthy"})

        self.assertTrue(ready["ready"])
        self.assertFalse(blocked["ready"])
        self.assertIn("unhealthy", blocked["blocked_tokens"])

    def test_apply_waits_for_fdb_ready_before_hub_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            state_path = Path(tmp) / "main_computer.private.yaml"
            state_path.write_text(PRIVATE_STATE, encoding="utf-8")
            args = coolify_cluster.parse_args(
                [
                    "apply",
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
                    str(state_path),
                    "--no-deploy",
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            calls: list[str] = []

            original_run_fdb_stage = coolify_cluster.run_fdb_stage
            original_wait_for_fdb_stage_ready = coolify_cluster.wait_for_fdb_stage_ready
            original_run_hub_stage = coolify_cluster.run_hub_stage

            def fake_run_fdb_stage(stage_args, stage_packet_path):
                del stage_args, stage_packet_path
                calls.append("fdb")
                return {"ok": True, "phases": [{"server": "coolify-a", "service_uuid": "fdb-service"}]}

            def fake_wait_for_fdb_stage_ready(stage_args, fdb_result):
                del stage_args, fdb_result
                calls.append("fdb-ready")
                return {"ok": True, "waited": True}

            def fake_run_hub_stage(stage_args, stage_packet_path):
                del stage_args, stage_packet_path
                calls.append("hub")
                return {"ok": True, "phases": []}

            coolify_cluster.run_fdb_stage = fake_run_fdb_stage
            coolify_cluster.wait_for_fdb_stage_ready = fake_wait_for_fdb_stage_ready
            coolify_cluster.run_hub_stage = fake_run_hub_stage
            try:
                result = coolify_cluster.cluster_plan_or_apply_result(args, packet)
            finally:
                coolify_cluster.run_fdb_stage = original_run_fdb_stage
                coolify_cluster.wait_for_fdb_stage_ready = original_wait_for_fdb_stage_ready
                coolify_cluster.run_hub_stage = original_run_hub_stage

            self.assertTrue(result["ok"])
            self.assertEqual(calls, ["fdb", "fdb-ready", "hub"])
            self.assertEqual(result["stages"]["foundationdb_ready"], {"ok": True, "waited": True})


    def test_fdb_only_apply_skips_fdb_ready_gate_because_no_hub_stage_follows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = Path(tmp) / "testnet-packet.json"
            state_path = Path(tmp) / "main_computer.private.yaml"
            state_path.write_text(PRIVATE_STATE, encoding="utf-8")
            args = coolify_cluster.parse_args(
                [
                    "apply",
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
                    str(state_path),
                    "--fdb-only",
                ]
            )
            packet = coolify_cluster.build_candidate_packet(args)
            calls: list[str] = []

            original_run_fdb_stage = coolify_cluster.run_fdb_stage
            original_wait_for_coolify_service_ready = coolify_cluster.wait_for_coolify_service_ready
            original_run_hub_stage = coolify_cluster.run_hub_stage

            def fake_run_fdb_stage(stage_args, stage_packet_path):
                del stage_args, stage_packet_path
                calls.append("fdb")
                return {"ok": True, "phases": [{"server": "coolify-a", "service_uuid": "fdb-service"}]}

            def fake_wait_for_coolify_service_ready(*args, **kwargs):
                del args, kwargs
                calls.append("low-level-fdb-wait")
                return {"ok": True}

            def fake_run_hub_stage(stage_args, stage_packet_path):
                del stage_args, stage_packet_path
                calls.append("hub")
                return {"ok": True, "phases": []}

            coolify_cluster.run_fdb_stage = fake_run_fdb_stage
            coolify_cluster.wait_for_coolify_service_ready = fake_wait_for_coolify_service_ready
            coolify_cluster.run_hub_stage = fake_run_hub_stage
            try:
                result = coolify_cluster.cluster_plan_or_apply_result(args, packet)
            finally:
                coolify_cluster.run_fdb_stage = original_run_fdb_stage
                coolify_cluster.wait_for_coolify_service_ready = original_wait_for_coolify_service_ready
                coolify_cluster.run_hub_stage = original_run_hub_stage

            self.assertTrue(result["ok"])
            self.assertEqual(calls, ["fdb"])
            self.assertIsNone(result["stages"]["hub"])
            self.assertTrue(result["stages"]["foundationdb_ready"]["skipped"])
            self.assertIn("--fdb-only", result["stages"]["foundationdb_ready"]["reason"])



if __name__ == "__main__":
    unittest.main()
