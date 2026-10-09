from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools import native_mint_control as control


RECIPIENT = "0x1111111111111111111111111111111111111111"


def _genesis() -> dict:
    return {
        "config": {
            "chainId": 42424240,
            "londonBlock": 0,
            "qbft": {
                "blockperiodseconds": 2,
                "epochlength": 30000,
                "requesttimeoutseconds": 4,
            },
        },
        "alloc": {},
        "extraData": "0x00",
        "gasLimit": "0x1fffffffffffff",
    }


def _transitions(genesis: dict) -> list[dict]:
    return genesis["config"]["transitions"]["qbft"]


def test_one_block_native_mint_pulse_has_exact_on_and_off_transitions() -> None:
    original = _genesis()
    result = control._build_open_genesis(
        original,
        current_block=100,
        activation_block=120,
        expiration_block=121,
        recipient=RECIPIENT,
        wei_per_block=123456789,
    )

    assert original.get("config", {}).get("transitions") is None
    assert _transitions(result) == [
        {"block": 120, "blockreward": "123456789", "miningbeneficiary": RECIPIENT},
        {"block": 121, "blockreward": "0", "miningbeneficiary": ""},
    ]
    assert result["alloc"] == original["alloc"]
    assert result["extraData"] == original["extraData"]


def test_bounded_open_window_stages_mandatory_automatic_off_transition() -> None:
    result = control._build_open_genesis(
        _genesis(),
        current_block=100,
        activation_block=150,
        expiration_block=160,
        recipient=RECIPIENT,
        wei_per_block=10**18,
    )
    assert _transitions(result)[0] == {
        "block": 150,
        "blockreward": str(10**18),
        "miningbeneficiary": RECIPIENT,
    }
    assert _transitions(result)[1] == {
        "block": 160,
        "blockreward": "0",
        "miningbeneficiary": "",
    }


def test_native_mint_refuses_nonzero_base_reward() -> None:
    genesis = _genesis()
    genesis["config"]["qbft"]["blockreward"] = "1"
    with pytest.raises(control.NativeMintError, match="native block reward is already active"):
        control._build_open_genesis(
            genesis,
            current_block=100,
            activation_block=120,
            expiration_block=121,
            recipient=RECIPIENT,
            wei_per_block=1,
        )


def test_native_mint_refuses_unowned_future_reward_transition() -> None:
    genesis = _genesis()
    genesis["config"]["transitions"] = {
        "qbft": [{"block": 130, "blockreward": "7", "miningbeneficiary": RECIPIENT}]
    }
    with pytest.raises(control.NativeMintError, match="future QBFT reward/beneficiary transitions already exist"):
        control._build_open_genesis(
            genesis,
            current_block=100,
            activation_block=120,
            expiration_block=121,
            recipient=RECIPIENT,
            wei_per_block=1,
        )


def test_close_before_activation_cancels_future_on_transition() -> None:
    opened = control._build_open_genesis(
        _genesis(),
        current_block=100,
        activation_block=150,
        expiration_block=160,
        recipient=RECIPIENT,
        wei_per_block=99,
    )
    closed, close_block = control._build_close_genesis(
        opened,
        current_block=120,
        active={"activation_block": 150, "expiration_block": 160},
        close_block=125,
    )
    assert close_block == 150
    assert _transitions(closed) == [
        {"block": 150, "blockreward": "0", "miningbeneficiary": ""},
        {"block": 160, "blockreward": "0", "miningbeneficiary": ""},
    ]


def test_close_active_window_adds_future_off_transition() -> None:
    opened = control._build_open_genesis(
        _genesis(),
        current_block=100,
        activation_block=110,
        expiration_block=200,
        recipient=RECIPIENT,
        wei_per_block=99,
    )
    closed, close_block = control._build_close_genesis(
        opened,
        current_block=150,
        active={"activation_block": 110, "expiration_block": 200},
        close_block=170,
    )
    assert close_block == 170
    assert {"block": 170, "blockreward": "0", "miningbeneficiary": ""} in _transitions(closed)
    assert {"block": 200, "blockreward": "0", "miningbeneficiary": ""} in _transitions(closed)


def test_topology_evidence_carries_new_genesis_and_hash(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    genesis = _genesis()
    sha = control._sha_bytes(control.canonical_json(genesis))
    baseline_doc = {
        "completed_at": "2026-10-06T00:00:00Z",
        "current_topology": {
            "nodes": ["mainneta-super1"],
            "validator_set": ["0x2222222222222222222222222222222222222222"],
            "services": {"mainneta-super1": {"service_uuid": "abc", "controller_id": "coolify-a"}},
            "genesis_sha256": "0" * 64,
        },
    }
    operation = {
        "network": "mainnet",
        "chain_id": 42424240,
        "operation_id": "native-mint-test",
        "mode": "mint",
        "recipient": RECIPIENT,
        "window": {"activation_block": 120, "expiration_block": 121},
    }
    path = control._write_topology_evidence(
        tmp_path,
        operation=operation,
        baseline_path=baseline,
        baseline_doc=copy.deepcopy(baseline_doc),
        new_genesis=genesis,
        new_sha=sha,
    )
    doc = control._read_json(path)
    assert doc["kind"] == control.TOPOLOGY_EVIDENCE_KIND
    assert doc["genesis_sha256"] == sha
    assert doc["genesis"] == genesis
    assert doc["current_topology"]["genesis_sha256"] == sha


def test_native_mint_operation_kind_is_registered_with_mother() -> None:
    from tools.mother.common.models import OperationIdentity

    identity = OperationIdentity(
        operation_id="native-mint-test",
        request_id="native-mint-control-test",
        network="mainnet",
        operation_kind="MOTHER-OP-NATIVE-MINT",
    )
    assert identity.operation_kind == "MOTHER-OP-NATIVE-MINT"


def _private_genesis_state(*, initial_validator: str, other_validator: str) -> dict:
    return {
        "networks": {
            "mainnet": {
                "chain_id": 42424240,
                "wallets": {
                    "captain": {"address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                    "o1": {"address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
                    "o2": {"address": "0xcccccccccccccccccccccccccccccccccccccccc"},
                    "o3": {"address": "0xdddddddddddddddddddddddddddddddddddddddd"},
                    "deployer": {"address": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},
                },
                "validators": {
                    "mainneta-super1": {"address": initial_validator},
                    "mainnetc-super1": {"address": other_validator},
                },
                "genesis": {
                    "source": "mother-private",
                    "first_topology_mode": "initial",
                    "qbft": {"blockperiodseconds": 2, "epochlength": 30000},
                    "alloc_accounts": [{"ref": "networks.mainnet.wallets.captain"}],
                },
            }
        }
    }


def test_find_genesis_reconstructs_exact_initial_policy_when_historical_artifact_is_absent(tmp_path: Path) -> None:
    initial = "0x2222222222222222222222222222222222222222"
    other = "0x3333333333333333333333333333333333333333"
    private_doc = _private_genesis_state(initial_validator=initial, other_validator=other)
    expected, _ = control._genesis_policy(
        private_doc,
        network="mainnet",
        initial_validator_address=initial,
    )
    expected_sha = control._sha_bytes(control.canonical_json(expected))

    recovered = control._find_genesis(
        tmp_path,
        expected_sha,
        {"current_topology": {"genesis_sha256": expected_sha}},
        private_doc=private_doc,
        network="mainnet",
    )

    assert recovered == expected
    assert initial[2:] in recovered["extraData"]
    assert other[2:] not in recovered["extraData"]


def test_find_genesis_reconstruction_remains_fail_closed_on_hash_mismatch(tmp_path: Path) -> None:
    private_doc = _private_genesis_state(
        initial_validator="0x2222222222222222222222222222222222222222",
        other_validator="0x3333333333333333333333333333333333333333",
    )

    with pytest.raises(control.NativeMintError, match="could not be reconstructed exactly"):
        control._find_genesis(
            tmp_path,
            "f" * 64,
            {"current_topology": {"genesis_sha256": "f" * 64}},
            private_doc=private_doc,
            network="mainnet",
        )


def test_helper_application_resolves_nested_application_uuid_and_exit_status() -> None:
    payload = {
        "uuid": "service123",
        "applications": [
            {
                "uuid": "app123",
                "name": "mother-native-mint-probe-parent12-000001",
                "status": "exited",
            }
        ],
    }
    assert control._helper_application(
        payload,
        "mother-native-mint-probe-parent12-000001",
    ) == ("app123", "mother-native-mint-probe-parent12-000001", "exited")


def test_helper_log_candidates_prefer_application_specific_endpoints() -> None:
    candidates = control._helper_log_candidates("service123", "app123", "helper-name")
    assert candidates[0] == (
        "service-application",
        "/api/v1/services/service123/applications/app123/logs?lines=100&show_timestamps=false",
    )
    assert candidates[1] == (
        "application-resource",
        "/api/v1/applications/app123/logs?lines=100",
    )
    assert candidates[2] == (
        "service-subresource",
        "/api/v1/services/service123/logs?sub_service_name=helper-name&lines=100&show_timestamps=false",
    )


def test_helper_compose_health_requires_success_marker() -> None:
    compose = control._helper_compose(
        helper_name="helper-name",
        parent_service_uuid="parentsvc123",
        mode="probe",
        expected_old_sha="a" * 64,
        new_sha="b" * 64,
    )
    document = control.yaml.safe_load(compose)
    service = document["services"]["helper-name"]
    assert service["healthcheck"]["test"] == [
        "CMD-SHELL",
        'test "$(cat /proc/1/comm)" = "sleep" && test -f /run/mother-helper/result-ok',
    ]
    wrapper = service["command"][0]
    assert "touch /run/mother-helper/result-ok" in wrapper
    assert "touch /run/mother-helper/result-failed" in wrapper
    assert "touch /run/mother-helper/result-ready" in wrapper


def test_helper_log_probe_continues_after_endpoint_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    marker_payload = {"mode": "probe", "ok": True}
    calls: list[str] = []

    def fake_http(_controller, method, endpoint, *, timeout=control.DEFAULT_TIMEOUT, **kwargs):
        assert method == "GET"
        calls.append(endpoint)
        assert timeout <= control.DEFAULT_HELPER_LOG_TIMEOUT_SECONDS
        if "/services/service123/applications/app123/logs" in endpoint:
            raise control.NativeMintError("TEST_TIMEOUT", "simulated timeout")
        if "/applications/app123/logs" in endpoint:
            return {
                "status": 200,
                "ok": True,
                "payload": {"logs": "NATIVE_MINT_HELPER_RESULT=" + control.json.dumps(marker_payload)},
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(control, "_coolify_http", fake_http)
    marker, attempts, _ = control._helper_log_probe(
        object(),
        helper_uuid="service123",
        application_uuid="app123",
        application_name="helper-name",
        timeout=30.0,
    )

    assert marker == marker_payload
    assert attempts[0]["kind"] == "service-application"
    assert attempts[0]["status"] is None
    assert "simulated timeout" in attempts[0]["error"]
    assert attempts[1]["kind"] == "application-resource"
    assert attempts[1]["marker"] is True
    assert len(calls) == 2


def test_run_helper_force_deploys_and_auto_cleans(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class DummyController:
        pass

    controller = DummyController()
    created_name = {"value": ""}
    calls: list[tuple[str, str]] = []
    marker_payload = {
        "mode": "probe",
        "before_sha256": "a" * 64,
        "expected_old_sha256": "a" * 64,
        "ok": True,
    }

    monkeypatch.setattr(control, "resolve_coolify_controller", lambda *args, **kwargs: controller)
    monkeypatch.setattr(
        control,
        "_controller_config",
        lambda *args, **kwargs: {"project_uuid": "project123", "server_uuid": "server123"},
    )

    def fake_http(_controller, method, endpoint, *, body=None, timeout=control.DEFAULT_TIMEOUT, **kwargs):
        calls.append((method, endpoint))
        if method == "POST" and endpoint == "/api/v1/services":
            created_name["value"] = body["name"]
            return {"status": 201, "ok": True, "payload": {"uuid": "service123"}}
        if method == "POST" and endpoint == "/api/v1/deploy":
            assert body == {"uuid": "service123", "force": True}
            return {"status": 200, "ok": True, "payload": {}}
        if method == "GET" and endpoint == "/api/v1/services/service123":
            return {
                "status": 200,
                "ok": True,
                "payload": {
                    "uuid": "service123",
                    "applications": [{
                        "uuid": "app123",
                        "name": created_name["value"],
                        "status": "running:healthy:excluded",
                    }],
                },
            }
        if method == "GET" and endpoint == "/api/v1/services/service123/applications/app123/logs?lines=100&show_timestamps=false":
            return {
                "status": 200,
                "ok": True,
                "payload": {"logs": "NATIVE_MINT_HELPER_RESULT=" + control.json.dumps(marker_payload)},
            }
        if method == "DELETE" and endpoint == "/api/v1/services/service123":
            return {"status": 200, "ok": True, "payload": {}}
        raise AssertionError((method, endpoint))

    monkeypatch.setattr(control, "_coolify_http", fake_http)
    result = control._run_helper(
        private=object(),
        network="mainnet",
        controller_id="coolify-c",
        parent_service_uuid="parentsvc123",
        mode="probe",
        expected_old_sha="a" * 64,
        new_sha="b" * 64,
        wait_seconds=1.0,
    )

    assert result == marker_payload
    assert ("POST", "/api/v1/deploy") in calls
    assert ("DELETE", "/api/v1/services/service123") in calls
    stderr = capsys.readouterr().err
    assert "force-deploying" in stderr
    assert "cleanup controller=coolify-c helper_uuid=service123 http=200" in stderr


def test_run_helper_waits_through_initial_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyController:
        pass

    controller = DummyController()
    created_name = {"value": ""}
    statuses = ["exited", "exited", "running:healthy:excluded"]
    detail_index = {"value": 0}
    log_calls = {"count": 0}
    marker_payload = {"mode": "probe", "ok": True}

    monkeypatch.setattr(control, "resolve_coolify_controller", lambda *args, **kwargs: controller)
    monkeypatch.setattr(control, "_controller_config", lambda *args, **kwargs: {"project_uuid": "p", "server_uuid": "s"})
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)

    def fake_http(_controller, method, endpoint, *, body=None, **kwargs):
        if method == "POST" and endpoint == "/api/v1/services":
            created_name["value"] = body["name"]
            return {"status": 201, "ok": True, "payload": {"uuid": "service123"}}
        if method == "POST" and endpoint == "/api/v1/deploy":
            return {"status": 200, "ok": True, "payload": {}}
        if method == "GET" and endpoint == "/api/v1/services/service123":
            idx = min(detail_index["value"], len(statuses) - 1)
            detail_index["value"] += 1
            return {
                "status": 200,
                "ok": True,
                "payload": {"applications": [{"uuid": "app123", "name": created_name["value"], "status": statuses[idx]}]},
            }
        if method == "DELETE" and endpoint == "/api/v1/services/service123":
            return {"status": 200, "ok": True, "payload": {}}
        raise AssertionError((method, endpoint))

    def fake_log_probe(*args, **kwargs):
        log_calls["count"] += 1
        return marker_payload, [{"kind": "service-application", "marker": True}], "marker"

    monkeypatch.setattr(control, "_coolify_http", fake_http)
    monkeypatch.setattr(control, "_helper_log_probe", fake_log_probe)

    result = control._run_helper(
        private=object(), network="mainnet", controller_id="coolify-c",
        parent_service_uuid="parentsvc123", mode="probe",
        expected_old_sha="a" * 64, wait_seconds=10.0,
    )
    assert result == marker_payload
    assert detail_index["value"] >= 3
    assert log_calls["count"] == 1


def test_cleanup_helpers_only_deletes_native_mint_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class DummyController:
        pass

    private_doc = {
        "networks": {
            "mainnet": {
                "coolify": {
                    "controllers": {
                        "coolify-a": {"enabled": True},
                        "coolify-c": {"enabled": True},
                    }
                }
            }
        }
    }
    monkeypatch.setattr(control, "_load_private", lambda *args, **kwargs: (object(), object(), private_doc))
    monkeypatch.setattr(control, "resolve_coolify_controller", lambda *args, **kwargs: DummyController())
    calls: list[tuple[str, str]] = []

    services = [
        {"uuid": "helper111", "name": "mother-native-mint-probe-abc-1"},
        {"uuid": "helper222", "name": "mother-native-mint-write-def-2"},
        {"uuid": "keep3333", "name": "mainneta-super1"},
    ]

    def fake_http(_controller, method, endpoint, **kwargs):
        calls.append((method, endpoint))
        if method == "GET" and endpoint == "/api/v1/services":
            return {"status": 200, "ok": True, "payload": services}
        if method == "DELETE":
            return {"status": 200, "ok": True, "payload": {}}
        raise AssertionError((method, endpoint))

    monkeypatch.setattr(control, "_coolify_http", fake_http)
    result = control._cleanup_helpers(
        control.argparse.Namespace(network="mainnet", runtime_state_root=str(tmp_path), timeout=1.0, dry_run=False)
    )
    assert result["ok"] is True
    assert result["matched_count"] == 4  # two matching helpers on each of two controllers
    assert result["deleted_count"] == 4
    assert all("keep3333" not in endpoint for method, endpoint in calls if method == "DELETE")


def test_stale_refresh_args_preserve_original_window_spacing() -> None:
    operation = {
        "operation_id": "native-mint-test",
        "mode": "mint",
        "network": "mainnet",
        "recipient": "0x1111111111111111111111111111111111111111",
        "wei_per_block": 123,
        "blocks": 1,
        "prep_block": 1000,
        "window": {"activation_block": 1180, "expiration_block": 1181, "manual_close_block": None},
    }
    args = control._stale_refresh_args(Path("runtime/state"), operation)
    assert args.operation_id == "native-mint-test"
    assert args.activation_lead_blocks == 180
    assert args.amount_wei == "123"
    assert args.to == operation["recipient"]


def test_stale_refresh_refuses_after_genesis_staged(tmp_path: Path) -> None:
    with pytest.raises(control.NativeMintError, match="after genesis staging began"):
        control._refresh_stale_prepared_operation(
            tmp_path,
            {
                "operation_id": "native-mint-test",
                "status": "genesis-staged",
                "window": {"activation_block": 100},
            },
        )


def test_auto_refresh_stale_requires_all_old_and_safe_status() -> None:
    old_sha = "a" * 64
    probes = [{"sha256": old_sha}, {"sha256": old_sha}]
    assert control._can_auto_refresh_stale({"status": "prepared"}, probes, old_sha) is True
    assert control._can_auto_refresh_stale({"status": "rolling-out"}, probes, old_sha) is False
    assert control._can_auto_refresh_stale(
        {"status": "rolling-out"}, probes, old_sha, live_rpc_available=True
    ) is True
    assert control._can_auto_refresh_stale({"status": "genesis-staged"}, probes, old_sha, live_rpc_available=True) is False
    assert control._can_auto_refresh_stale({"status": "prepared"}, [{"sha256": old_sha}, {"sha256": "b" * 64}], old_sha) is False
    assert control._can_auto_refresh_stale({"status": "prepared"}, [], old_sha) is False

def test_service_control_uses_post_stop_without_docker_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyController:
        pass

    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(control, "resolve_coolify_controller", lambda *args, **kwargs: DummyController())

    def fake_http(_controller, method, endpoint, *, body=None, **kwargs):
        calls.append((method, endpoint, body))
        return {"status": 200, "ok": True, "payload": {"message": "queued"}}

    monkeypatch.setattr(control, "_coolify_http", fake_http)
    control._service_control(
        object(),
        network="mainnet",
        target={"node": "mainneta-super1", "controller_id": "coolify-a", "service_uuid": "service123"},
        action="stop",
    )
    assert calls == [
        ("POST", "/api/v1/services/service123/stop?docker_cleanup=false", None)
    ]



def test_service_control_uses_forced_deploy_to_start_existing_chain_service(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyController:
        pass

    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(control, "resolve_coolify_controller", lambda *args, **kwargs: DummyController())

    def fake_http(_controller, method, endpoint, *, body=None, **kwargs):
        calls.append((method, endpoint, body))
        return {"status": 200, "ok": True, "payload": {"message": "queued"}}

    monkeypatch.setattr(control, "_coolify_http", fake_http)
    control._service_control(
        object(),
        network="mainnet",
        target={"node": "mainnetc-super1", "controller_id": "coolify-c", "service_uuid": "service123"},
        action="start",
    )
    assert calls == [
        ("POST", "/api/v1/deploy", {"uuid": "service123", "force": True})
    ]

def test_execution_current_block_prefers_live_rpc_during_rolling_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_rpc_int(_url, method, params=None):
        if method == "eth_chainId":
            return 42424240
        if method == "eth_blockNumber":
            return 133999
        raise AssertionError(method)

    monkeypatch.setattr(control, "_rpc_int", fake_rpc_int)
    block, live = control._execution_current_block(
        "https://rpc.invalid",
        42424240,
        {"status": "rolling-out", "rollout_started_block": 133176, "prep_block": 133000},
    )
    assert block == 133999
    assert live is True


def test_execution_current_block_falls_back_only_when_recovering(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_rpc(*args, **kwargs):
        raise control.NativeMintError("NATIVE_MINT_RPC_FAILED", "offline")

    monkeypatch.setattr(control, "_rpc_int", failed_rpc)
    block, live = control._execution_current_block(
        "https://rpc.invalid",
        42424240,
        {"status": "rolling-out", "rollout_started_block": 133176, "prep_block": 133000},
    )
    assert block == 133176
    assert live is False

    with pytest.raises(control.NativeMintError, match="offline"):
        control._execution_current_block(
            "https://rpc.invalid",
            42424240,
            {"status": "prepared", "prep_block": 133000},
        )

