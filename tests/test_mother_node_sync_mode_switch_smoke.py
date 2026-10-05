from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "mother" / "node_sync_mode_switch_smoke.py"
SPEC = importlib.util.spec_from_file_location("node_sync_mode_switch_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


COMPOSE = """name: mainneta-super1
services:
  mother-genesis-init:
    image: alpine:3.20
    command:
      - sh
      - -ec
      - mkdir -p /var/lib/besu
    volumes:
      - mother-config:/config
      - mother-data:/var/lib/besu
  mainneta-super1:
    image: hyperledger/besu:latest
    command:
      - --data-path=/var/lib/besu
      - --genesis-file=/config/genesis.json
      - --sync-min-peers=0
      - --sync-mode=FULL
      - --snapsync-server-enabled=true
    volumes:
      - mother-config:/config:ro
      - mother-data:/var/lib/besu
volumes:
  mother-config:
  mother-data:
"""


def test_rewrite_full_to_snap_changes_only_sync_profile_semantics() -> None:
    rewritten, current, current_min_peers = mod._rewrite_sync_mode(COMPOSE, "mainneta-super1", "SNAP")
    assert current == "FULL"
    assert current_min_peers == 0
    assert "--sync-mode=SNAP" in rewritten
    assert "--sync-mode=FULL" not in rewritten
    assert "--sync-min-peers=2" in rewritten
    assert "--sync-min-peers=0" not in rewritten
    assert mod._semantic_masked_sha(rewritten, "mainneta-super1") == mod._semantic_masked_sha(COMPOSE, "mainneta-super1")
    assert mod._volume_fingerprint(rewritten, "mainneta-super1") == mod._volume_fingerprint(COMPOSE, "mainneta-super1")


def test_rewrite_retired_genesis_shim_drops_only_impossible_completed_dependency() -> None:
    compose = """name: mainneta-super1
services:
  mother-genesis-init:
    image: alpine:3.20
    command:
      - sh
      - -lc
      - while true; do echo mother-retired-helper-shim >/tmp/mother-retired-helper-shim; sleep 30; done
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mainneta-super1:
    image: hyperledger/besu:latest
    depends_on:
      mother-genesis-init:
        condition: service_completed_successfully
        required: true
    command:
      - --data-path=/var/lib/besu
      - --genesis-file=/config/genesis.json
      - --sync-min-peers=2
      - --sync-mode=SNAP
      - --snapsync-server-enabled=true
    volumes:
      - mother-config:/config:ro
      - mother-data:/var/lib/besu
  mother-super-node-hub:
    image: mainneta-super1-hub:test
    depends_on:
      mainneta-super1:
        condition: service_started
        required: true
      mother-super-node-fdb:
        condition: service_started
        required: true
volumes:
  mother-config:
  mother-data:
"""

    rewritten, current, current_min_peers = mod._rewrite_sync_mode(compose, "mainneta-super1", "FULL")
    parsed = mod.yaml.safe_load(rewritten)

    assert current == "SNAP"
    assert current_min_peers == 2
    assert "depends_on" not in parsed["services"]["mainneta-super1"]
    assert parsed["services"]["mother-super-node-hub"]["depends_on"] == {
        "mainneta-super1": {"condition": "service_started", "required": True},
        "mother-super-node-fdb": {"condition": "service_started", "required": True},
    }
    assert mod._semantic_masked_sha(rewritten, "mainneta-super1") == mod._semantic_masked_sha(compose, "mainneta-super1")


def test_rewrite_real_one_shot_genesis_init_keeps_completed_dependency() -> None:
    compose = COMPOSE.replace(
        "    image: hyperledger/besu:latest\n",
        "    image: hyperledger/besu:latest\n"
        "    depends_on:\n"
        "      mother-genesis-init:\n"
        "        condition: service_completed_successfully\n"
        "        required: true\n",
    )

    rewritten, current, current_min_peers = mod._rewrite_sync_mode(compose, "mainneta-super1", "SNAP")
    parsed = mod.yaml.safe_load(rewritten)

    assert current == "FULL"
    assert current_min_peers == 0
    assert parsed["services"]["mainneta-super1"]["depends_on"]["mother-genesis-init"] == {
        "condition": "service_completed_successfully",
        "required": True,
    }


def test_rewrite_snap_to_full() -> None:
    snap = COMPOSE.replace("--sync-mode=FULL", "--sync-mode=SNAP").replace("--sync-min-peers=0", "--sync-min-peers=2")
    rewritten, current, current_min_peers = mod._rewrite_sync_mode(snap, "mainneta-super1", "FULL")
    assert current == "SNAP"
    assert current_min_peers == 2
    assert "--sync-mode=FULL" in rewritten
    assert "--sync-mode=SNAP" not in rewritten
    assert "--sync-min-peers=0" in rewritten
    assert "--sync-min-peers=2" not in rewritten


def test_rewrite_same_snap_mode_repairs_wrong_min_peers() -> None:
    snap_zero = COMPOSE.replace("--sync-mode=FULL", "--sync-mode=SNAP")
    rewritten, current, current_min_peers = mod._rewrite_sync_mode(snap_zero, "mainneta-super1", "SNAP")
    assert current == "SNAP"
    assert current_min_peers == 0
    assert "--sync-mode=SNAP" in rewritten
    assert "--sync-min-peers=2" in rewritten
    assert "--sync-min-peers=0" not in rewritten


def test_rewrite_refuses_noop() -> None:
    with pytest.raises(mod.SmokeError, match="already configured"):
        mod._rewrite_sync_mode(COMPOSE, "mainneta-super1", "FULL")


def test_rewrite_refuses_destructive_data_reset() -> None:
    unsafe = COMPOSE.replace("mkdir -p /var/lib/besu", "rm -rf /var/lib/besu/*")
    with pytest.raises(mod.SmokeError, match="destructive"):
        mod._rewrite_sync_mode(unsafe, "mainneta-super1", "SNAP")


def test_rewrite_refuses_ambiguous_sync_flags() -> None:
    ambiguous = COMPOSE.replace(
        "      - --snapsync-server-enabled=true",
        "      - --sync-mode=SNAP\n      - --snapsync-server-enabled=true",
    )
    with pytest.raises(mod.SmokeError, match="exactly one --sync-mode"):
        mod._rewrite_sync_mode(ambiguous, "mainneta-super1", "SNAP")



def test_rewrite_refuses_ambiguous_sync_min_peers_flags() -> None:
    ambiguous = COMPOSE.replace(
        "      - --sync-mode=FULL",
        "      - --sync-min-peers=9\n      - --sync-mode=FULL",
    )
    with pytest.raises(mod.SmokeError, match="exactly one --sync-min-peers"):
        mod._rewrite_sync_mode(ambiguous, "mainneta-super1", "SNAP")

def test_runtime_fact_parser_uses_latest_startup() -> None:
    logs = """
# Sync mode: Full
# Sync min peers: 0
Node address 0x1111111111111111111111111111111111111111
Produced #100
# Sync mode: Snap
# Sync min peers: 2
Node address 0x1111111111111111111111111111111111111111
Imported #101
"""
    facts = mod._runtime_facts_from_logs(logs)
    assert facts["runtime_sync_mode"] == "SNAP"
    assert facts["runtime_sync_min_peers"] == 2
    assert facts["node_address"] == "0x1111111111111111111111111111111111111111"
    assert facts["highest_logged_block"] == 101
    assert facts["sync_mode_observations"] == 2
    assert facts["sync_min_peers_observations"] == 2


def test_volume_fingerprint_requires_expected_mounts() -> None:
    fp = mod._volume_fingerprint(COMPOSE, "mainneta-super1")
    assert fp["data_path_mount_present"] is True
    assert fp["config_mount_present"] is True
    assert fp["top_level_volume_names"] == ["mother-config", "mother-data"]



def _service_list_receipt(*uuids: str):
    return {
        "ok": True,
        "status": 200,
        "response_sha256": "deadbeef",
        "payload": [
            {"name": "mainneta-super2", "uuid": uuid}
            for uuid in uuids
        ],
    }


def test_find_exact_service_still_refuses_duplicate_name_without_uuid(monkeypatch) -> None:
    controller = SimpleNamespace(controller_id="coolify-a")
    monkeypatch.setattr(
        mod,
        "_http",
        lambda *_args, **_kwargs: _service_list_receipt("stale-uuid", "live-uuid"),
    )
    with pytest.raises(mod.SmokeError, match="expected exactly one Coolify service named"):
        mod._find_exact_service(
            [controller],
            "mainneta-super2",
            requested_controller=None,
            timeout=1.0,
            max_response_bytes=1024,
        )


def test_find_exact_service_uuid_selects_live_duplicate(monkeypatch) -> None:
    controller = SimpleNamespace(controller_id="coolify-a")
    monkeypatch.setattr(
        mod,
        "_http",
        lambda *_args, **_kwargs: _service_list_receipt("stale-uuid", "live-uuid"),
    )
    selected_controller, record, observations = mod._find_exact_service(
        [controller],
        "mainneta-super2",
        requested_controller=None,
        timeout=1.0,
        max_response_bytes=1024,
        requested_service_uuid="live-uuid",
    )
    assert selected_controller is controller
    assert record["name"] == "mainneta-super2"
    assert record["uuid"] == "live-uuid"
    assert observations[0]["controller_id"] == "coolify-a"


def test_find_exact_service_uuid_must_still_match_requested_node(monkeypatch) -> None:
    controller = SimpleNamespace(controller_id="coolify-a")
    monkeypatch.setattr(
        mod,
        "_http",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": 200,
            "response_sha256": "deadbeef",
            "payload": [{"name": "some-other-node", "uuid": "live-uuid"}],
        },
    )
    with pytest.raises(mod.SmokeError, match="with UUID 'live-uuid'; found none"):
        mod._find_exact_service(
            [controller],
            "mainneta-super2",
            requested_controller=None,
            timeout=1.0,
            max_response_bytes=1024,
            requested_service_uuid="live-uuid",
        )


def test_find_exact_service_rejects_unsafe_uuid_before_lookup(monkeypatch) -> None:
    controller = SimpleNamespace(controller_id="coolify-a")
    calls = []
    monkeypatch.setattr(mod, "_http", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(mod.SmokeError, match="--service-uuid is missing or unsafe"):
        mod._find_exact_service(
            [controller],
            "mainneta-super2",
            requested_controller=None,
            timeout=1.0,
            max_response_bytes=1024,
            requested_service_uuid="../../oops",
        )
    assert calls == []


def test_parser_accepts_service_uuid() -> None:
    args = mod.build_parser().parse_args(
        [
            "mainneta-super2",
            "--network",
            "mainnet",
            "--service-uuid",
            "ospzswflvxbzvdavt8hhib2i",
            "--sync-mode",
            "SNAP",
            "--execute-mutations",
        ]
    )
    assert args.service_uuid == "ospzswflvxbzvdavt8hhib2i"
    assert args.sync_mode == "SNAP"
    assert args.execute_mutations is True


def test_compose_patch_body_updates_only_compose_and_disables_instant_deploy() -> None:
    body = mod._compose_patch_body(COMPOSE)
    assert set(body) == {"docker_compose_raw", "instant_deploy"}
    assert body["instant_deploy"] is False
    import base64
    assert base64.b64decode(body["docker_compose_raw"]).decode("utf-8") == COMPOSE
    assert "name" not in body


def test_safe_receipt_surfaces_failed_coolify_payload() -> None:
    receipt = {
        "method": "PATCH",
        "endpoint": "/api/v1/services/live-uuid",
        "ok": False,
        "status": 500,
        "payload": {"message": "duplicate name"},
        "response_sha256": "abc",
        "byte_length": 33,
        "elapsed_ms": 7,
    }
    safe = mod._safe_receipt(receipt)
    assert safe["payload"] == {"message": "duplicate name"}


def test_safe_receipt_does_not_emit_success_payload() -> None:
    receipt = {
        "method": "PATCH",
        "endpoint": "/api/v1/services/live-uuid",
        "ok": True,
        "status": 200,
        "payload": {"uuid": "live-uuid", "name": "mainneta-super2"},
    }
    safe = mod._safe_receipt(receipt)
    assert "payload" not in safe


def test_rewrite_reserializes_multiline_guardian_as_literal_block() -> None:
    compose = r'''name: mainneta-super2
services:
  mainneta-super2:
    image: hyperledger/besu:latest
    command:
      - --data-path=/var/lib/besu
      - --sync-min-peers=0
      - --sync-mode=SNAP
    volumes:
      - mother-config:/config:ro
      - mother-data:/var/lib/besu
  mother-add-node-validator-activation-guardian:
    image: python:3.12-alpine
    command:
      - python
      - -c
      - "import hashlib, http.server, json, os, threading, time, traceback, urllib.request\nRPC = 'http://mainneta-super2:8545'\nEXPECTED_DESIRED = ['0x1111111111111111111111111111111111111111', '0x2222222222222222222222222222222222222222']\ndef encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()\ndef peer_count(): return 2"
volumes:
  mother-config:
  mother-data:
'''
    before = mod.yaml.safe_load(compose)
    rewritten, current, current_min_peers = mod._rewrite_sync_mode(compose, "mainneta-super2", "SNAP")
    after = mod.yaml.safe_load(rewritten)

    assert current == "SNAP"
    assert current_min_peers == 0
    assert after["services"]["mainneta-super2"]["command"][-2:] == [
        "--sync-min-peers=2",
        "--sync-mode=SNAP",
    ]
    assert (
        after["services"]["mother-add-node-validator-activation-guardian"]["command"][2]
        == before["services"]["mother-add-node-validator-activation-guardian"]["command"][2]
    )
    assert "- |-" in rewritten or "- |" in rewritten
    assert '- "import hashlib, http.server' not in rewritten


def _successful_deploy_receipt():
    return {
        "method": "POST",
        "endpoint": "/api/v1/deploy",
        "ok": True,
        "status": 200,
        "response_sha256": "deploy-sha",
        "byte_length": 12,
        "elapsed_ms": 3,
    }


def test_cleanup_safe_redeploy_uses_one_deploy_when_boundary_is_clean(monkeypatch) -> None:
    deploy_calls = []
    monkeypatch.setattr(mod, "_wait_cleanup_clear", lambda *_a, **_k: {"old-cleanup"})
    monkeypatch.setattr(
        mod,
        "_deploy_receipt",
        lambda *_a, **_k: deploy_calls.append(True) or _successful_deploy_receipt(),
    )
    monkeypatch.setattr(
        mod,
        "_watch_switch_attempt",
        lambda *_a, **_k: {
            "status": "healthy",
            "cleanup_uuid": None,
            "observations": [{"attempt": 1}],
            "detail": {"uuid": "service-uuid", "name": "mainneta-super1"},
            "compose": COMPOSE,
            "runtime": {"runtime_sync_mode": "SNAP", "runtime_sync_min_peers": 2},
            "log_attempts": [],
        },
    )

    result = mod._cleanup_safe_redeploy(
        object(),
        cleanup_endpoint="/cleanup",
        service_uuid="service-uuid",
        node="mainneta-super1",
        requested_mode="SNAP",
        requested_min_peers=2,
        poll_seconds=1.0,
        poll_interval_seconds=0.0,
        timeout=1.0,
        log_lines=5000,
        max_response_bytes=1024,
    )

    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert len(deploy_calls) == 1
    assert len(result["deploy_receipts"]) == 1
    assert result["retry_reason"] is None
    assert result["boundary_attempts"] == [{"attempt": 1, "status": "healthy", "cleanup_uuid": None}]


def test_cleanup_safe_redeploy_waits_for_overlap_then_redeploys_once(monkeypatch) -> None:
    baselines = iter([{"old-cleanup"}, {"old-cleanup", "cleanup-race"}])
    deploy_calls = []
    terminal_calls = []

    monkeypatch.setattr(mod, "_wait_cleanup_clear", lambda *_a, **_k: next(baselines))
    monkeypatch.setattr(
        mod,
        "_deploy_receipt",
        lambda *_a, **_k: deploy_calls.append(True) or _successful_deploy_receipt(),
    )
    monkeypatch.setattr(
        mod,
        "_wait_cleanup_terminal",
        lambda *_a, **_k: terminal_calls.append(_a[2] if len(_a) > 2 else _k.get("cleanup_uuid")),
    )

    def watch(*_a, **kwargs):
        if kwargs["attempt"] == 1:
            return {
                "status": "cleanup-overlap",
                "cleanup_uuid": "cleanup-race",
                "observations": [{"attempt": 1, "cleanup_boundary_crossed": True}],
                "detail": None,
                "compose": None,
                "runtime": None,
                "log_attempts": [],
            }
        return {
            "status": "healthy",
            "cleanup_uuid": None,
            "observations": [{"attempt": 2, "cleanup_boundary_crossed": False}],
            "detail": {"uuid": "service-uuid", "name": "mainneta-super1"},
            "compose": COMPOSE,
            "runtime": {"runtime_sync_mode": "SNAP", "runtime_sync_min_peers": 2},
            "log_attempts": [],
        }

    monkeypatch.setattr(mod, "_watch_switch_attempt", watch)

    result = mod._cleanup_safe_redeploy(
        object(),
        cleanup_endpoint="/cleanup",
        service_uuid="service-uuid",
        node="mainneta-super1",
        requested_mode="SNAP",
        requested_min_peers=2,
        poll_seconds=1.0,
        poll_interval_seconds=0.0,
        timeout=1.0,
        log_lines=5000,
        max_response_bytes=1024,
    )

    assert result["ok"] is True
    assert len(deploy_calls) == 2
    assert terminal_calls == ["cleanup-race"]
    assert result["retry_reason"] == "cleanup-boundary-crossed"
    assert [item["status"] for item in result["boundary_attempts"]] == ["cleanup-overlap", "healthy"]


def test_cleanup_safe_redeploy_second_overlap_refuses_third_deploy(monkeypatch) -> None:
    baselines = iter([{"old-cleanup"}, {"old-cleanup", "cleanup-race-1"}])
    deploy_calls = []
    monkeypatch.setattr(mod, "_wait_cleanup_clear", lambda *_a, **_k: next(baselines))
    monkeypatch.setattr(
        mod,
        "_deploy_receipt",
        lambda *_a, **_k: deploy_calls.append(True) or _successful_deploy_receipt(),
    )
    monkeypatch.setattr(mod, "_wait_cleanup_terminal", lambda *_a, **_k: None)
    monkeypatch.setattr(
        mod,
        "_watch_switch_attempt",
        lambda *_a, **kwargs: {
            "status": "cleanup-overlap",
            "cleanup_uuid": f"cleanup-race-{kwargs['attempt']}",
            "observations": [{"attempt": kwargs["attempt"], "cleanup_boundary_crossed": True}],
            "detail": None,
            "compose": None,
            "runtime": None,
            "log_attempts": [],
        },
    )

    result = mod._cleanup_safe_redeploy(
        object(),
        cleanup_endpoint="/cleanup",
        service_uuid="service-uuid",
        node="mainneta-super1",
        requested_mode="SNAP",
        requested_min_peers=2,
        poll_seconds=1.0,
        poll_interval_seconds=0.0,
        timeout=1.0,
        log_lines=5000,
        max_response_bytes=1024,
    )

    assert result["ok"] is False
    assert result["status"] == "cleanup-retry-contaminated"
    assert len(deploy_calls) == 2
    assert [item["status"] for item in result["boundary_attempts"]] == ["cleanup-overlap", "cleanup-overlap"]


def test_watch_switch_attempt_does_not_accept_healthy_when_cleanup_crossed(monkeypatch) -> None:
    controller = object()
    snap_compose = COMPOSE.replace("--sync-mode=FULL", "--sync-mode=SNAP").replace("--sync-min-peers=0", "--sync-min-peers=2")
    monkeypatch.setattr(
        mod,
        "_detail",
        lambda *_a, **_k: {
            "uuid": "service-uuid",
            "name": "mainneta-super1",
            "status": "running:healthy",
            "docker_compose_raw": snap_compose,
        },
    )
    monkeypatch.setattr(
        mod,
        "_logs_text",
        lambda *_a, **_k: ("# Sync mode: Snap\n# Sync min peers: 2\n", []),
    )
    monkeypatch.setattr(
        mod,
        "_cleanup_snapshot",
        lambda *_a, **_k: (
            {"ok": True},
            [{"uuid": "cleanup-race", "status": "running", "finished_at": None}],
        ),
    )

    result = mod._watch_switch_attempt(
        controller,
        cleanup_endpoint="/cleanup",
        cleanup_baseline_ids={"old-cleanup"},
        service_uuid="service-uuid",
        node="mainneta-super1",
        requested_mode="SNAP",
        requested_min_peers=2,
        poll_seconds=1.0,
        poll_interval_seconds=0.0,
        timeout=1.0,
        log_lines=5000,
        max_response_bytes=1024,
        attempt=1,
    )

    assert result["status"] == "cleanup-overlap"
    assert result["cleanup_uuid"] == "cleanup-race"
    assert result["observations"][0]["service_status"] == "running:healthy"
    assert result["observations"][0]["cleanup_boundary_crossed"] is True
