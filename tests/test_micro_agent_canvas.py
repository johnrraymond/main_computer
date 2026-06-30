from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_canvas_module():
    path = Path(__file__).resolve().parents[1] / "micro_agent_canvas.py"
    spec = importlib.util.spec_from_file_location("micro_agent_canvas_for_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_market_ring_accepts_worker_ui_values():
    canvas = _load_canvas_module()

    assert canvas.normalize_market_ring("3") == "ring-3"
    assert canvas.normalize_market_ring("ring-3") == "ring-3"
    assert canvas.normalize_market_ring("Ring 3 - Public untrusted") == "ring-3"


def test_default_work_payload_matches_worker_ui_market_contract():
    canvas = _load_canvas_module()
    args = SimpleNamespace(
        prompt="hello",
        ring="3",
        capability=None,
        client_node_id="micro-agent-local",
        model="micro-agent-local",
        max_credits="2",
        accept_timeout=10.0,
    )

    payload = canvas.build_work_payload(
        args,
        authorization={},
        hub_status={
            "ok": True,
            "network": {
                "network_key": "dev",
                "chain_id": 42424242,
            },
            "serving_hub": {
                "hub_id": "dev-hub1",
            },
        },
    )

    assert payload["ring"] == "ring-3"
    assert payload["partition"] == "ring-3"
    assert payload["capabilities"] == ["chat.completions"]
    assert payload["required_capabilities"] == ["chat.completions"]
    assert payload["max_price"] == {"amount": "2", "unit": "compute_credit"}
    assert payload["input"]["kind"] == "chat.completions"
    assert payload["metadata"]["hub_chain_id"] == "42424242"


def test_status_summary_handles_stable_topology_shape():
    canvas = _load_canvas_module()

    summary = canvas.extract_hub_status_summary(
        {
            "ok": True,
            "hub_id": {
                "hub_id": "dev-hub1",
                "display_name": "dev-hub1",
                "hub_url": "http://127.0.0.1:8871",
            },
            "serving_hub": {
                "hub_id": "dev-hub1",
            },
            "network": {
                "network_key": "dev",
                "chain_id": "0x28757b2",
            },
            "backend": "foundationdb",
        }
    )

    assert summary["hub_id"] == "dev-hub1"
    assert summary["serving_hub"] == "dev-hub1"
    assert summary["network_key"] == "dev"
    assert summary["chain_id"] == "42424242"
    assert summary["backend"] == "foundationdb"


def test_build_multisession_key_message_is_signed_request_contract():
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address, verify_personal_sign_blob

    private_key = "0x1"
    wallet = private_key_to_address(private_key)
    message = canvas.build_multisession_key_message(
        wallet_address=wallet,
        chain_id="42424242",
        hub_url="http://127.0.0.1:8871",
    )
    blob = canvas.build_personal_sign_blob(
        message=message,
        private_key=private_key,
        wallet_address=wallet,
        chain_id="42424242",
    )

    assert message["purpose"] == "request_multi_session_key"
    assert message["user_slug"].startswith("usr_")
    assert len(message["user_slug"]) >= 32
    assert message["origin"] == "micro-agent-canvas:http://127.0.0.1:8871"

    verified = verify_personal_sign_blob(blob, expected_chain_id="0x28757b2", max_age_minutes=15)
    assert verified["ok"] is True
    assert verified["wallet_address"] == wallet
    assert verified["message"]["user_slug"] == message["user_slug"]


def test_request_fresh_multisession_authorization_posts_request_then_validate(monkeypatch, tmp_path: Path):
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address

    private_key = "0x1"
    wallet = private_key_to_address(private_key)
    calls = []

    def fake_http_json(method, url, payload=None, timeout=15.0):
        calls.append((method, url, payload))
        if url.endswith("/api/hub/v1/credits/multisession-keys/request"):
            signed = payload["signed_request"]
            assert signed["kind"] == "main_computer_multisession_key_request"
            assert signed["message"]["purpose"] == "request_multi_session_key"
            assert signed["message"]["user_slug"].startswith("usr_")
            return 200, {
                "ok": True,
                "cluster_id": "main-computer-dev-stable-hub",
                "key": {"id": "msk_new_active", "status": "active"},
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": "msk_new_active",
                    "key_id": "msk_new_active",
                    "chain_id": "42424242",
                },
            }
        if url.endswith("/api/hub/v1/credits/wallet-funding/import"):
            assert payload["wallet_address"] == wallet
            assert payload["chain_id"] == 42424242
            assert payload["contract_address"] == "0x0000000000000000000000000000000000000001"
            assert payload["tx_hash"].startswith("0x")
            assert len(payload["tx_hash"]) == 66
            assert payload["log_index"] == 0
            assert payload["block_number"] == 1
            assert payload["payment_asset"] == "native"
            assert payload["payment_amount_base_units"] == 2000000000000000000
            assert payload["credits_granted_wei"] == "2000000000000000000"
            assert payload["metadata"]["synthetic_local_dev_receipt"] is True
            return 200, {
                "ok": True,
                "idempotent": False,
                "account": {"available_credit_wei": "2000000000000000000"},
            }
        if url.endswith("/api/hub/v1/credits/multisession-keys/validate"):
            assert payload["wallet_address"] == wallet
            assert payload["required_credit_wei"] == "2000000000000000000"
            assert payload["multisession_authorization"]["multisession_key_id"] == "msk_new_active"
            assert payload["payment_authorization"]["wallet_address"] == wallet
            return 200, {"ok": True, "valid": True, "ready": True}
        raise AssertionError(url)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(canvas, "http_json", fake_http_json)
    args = SimpleNamespace(
        private_key=private_key,
        private_key_file="",
        wallet="",
        max_credits="2",
        msk_lifetime_minutes=10,
    )

    auth = canvas.request_fresh_multisession_authorization(
        args=args,
        hub_url="http://127.0.0.1:8871",
        hub_status={
            "ok": True,
            "network": {"network_key": "dev", "chain_id": "42424242"},
            "serving_hub": {"hub_id": "dev-hub1"},
        },
        settings={},
    )

    assert auth["wallet_address"] == wallet
    assert auth["multisession_key_id"] == "msk_new_active"
    assert auth["key_id"] == "msk_new_active"
    assert auth["chain_id"] == "42424242"
    assert auth["max_authorized_credit_wei"] == "2000000000000000000"
    assert [call[1].rsplit("/", 1)[-1] for call in calls] == ["request", "import", "validate"]


def test_wallet_funding_import_payload_includes_bridge_receipt_fields_for_local_dev(tmp_path: Path):
    canvas = _load_canvas_module()

    payload = canvas.build_wallet_funding_import_payload(
        wallet_address="0x" + "1" * 40,
        chain_id="42424242",
        max_credit_wei="2000000000000000000",
        hub_status={"network": {"network_key": "dev", "chain_id": "42424242"}},
        settings={},
        root=tmp_path,
    )

    assert payload["contract_address"] == "0x0000000000000000000000000000000000000001"
    assert payload["tx_hash"].startswith("0x")
    assert len(payload["tx_hash"]) == 66
    assert payload["log_index"] == 0
    assert payload["block_number"] == 1
    assert payload["payment_asset"] == "native"
    assert payload["payment_amount_base_units"] == 2000000000000000000
    assert payload["credits_granted_wei"] == "2000000000000000000"
    assert payload["metadata"]["synthetic_local_dev_receipt"] is True


def test_resolve_requester_private_key_prefers_deployment_smoke_client_wallet(tmp_path: Path, monkeypatch) -> None:
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address

    root = tmp_path
    wallet_dir = root / "runtime" / "deployments" / "dev"
    wallet_dir.mkdir(parents=True)
    private_key = "0x" + "1".zfill(64)
    address = private_key_to_address(private_key)
    wallet_path = wallet_dir / "smoke-client-wallet-42424242.json"
    wallet_path.write_text(
        __import__("json").dumps(
            {
                "schema": "main-computer.smoke-client-wallet.v1",
                "chain_id": 42424242,
                "address": address,
                "private_key": private_key,
                "source": "generated-local-smoke-client",
            }
        ),
        encoding="utf-8",
    )
    (wallet_dir / "latest.json").write_text(
        __import__("json").dumps(
            {
                "schema": "main-computer.deployment.v1",
                "environment": "dev",
                "chain": {"chain_id": 42424242},
                "smoke_client": {
                    "address": address,
                    "wallet_path": "runtime/deployments/dev/smoke-client-wallet-42424242.json",
                    "source": "generated-local-smoke-client",
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("MICRO_AGENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_MICRO_AGENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_SMOKE_CLIENT_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_PAID_REQUESTER_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_REQUESTER_0_PRIVATE_KEY", raising=False)

    args = SimpleNamespace(private_key="", private_key_file="")
    resolved = canvas.resolve_requester_private_key(
        args,
        hub_status={
            "ok": True,
            "network": {"network_key": "dev", "chain_id": "42424242"},
            "serving_hub": {"hub_id": "dev-hub1"},
        },
        settings={},
        root=root,
    )

    assert resolved.private_key == private_key
    assert resolved.wallet_address == address
    assert resolved.source == "deployment smoke-client wallet"
    assert resolved.path.endswith("runtime/deployments/dev/smoke-client-wallet-42424242.json")


def test_extract_simple_text_result_handles_live_session_request_response_shape():
    canvas = _load_canvas_module()

    payload = {
        "status": "succeeded",
        "request": {
            "response": {
                "content": "Purple light guides the waves.",
                "model": "gemma4:26b",
                "provider": "ollama",
            },
            "response_summary": "Purple light guides the waves.",
        },
        "stream": {
            "events": [
                {"type": "accepted", "status": "accepted"},
                {"type": "delta", "delta": "Purple", "content_so_far": "Purple"},
                {
                    "type": "result",
                    "status": "succeeded",
                    "response": {
                        "content": "Purple light guides the waves.",
                        "role": "assistant",
                    },
                },
            ]
        },
    }

    assert canvas.extract_simple_text_result(payload) == "Purple light guides the waves."


def test_extract_simple_text_result_falls_back_to_stream_result_event():
    canvas = _load_canvas_module()

    payload = {
        "status": "succeeded",
        "stream": {
            "events": [
                {"type": "delta", "delta": "Purple", "content_so_far": "Purple"},
                {
                    "type": "result",
                    "status": "succeeded",
                    "content": "Purple light guides the waves.",
                },
            ]
        },
    }

    assert canvas.extract_simple_text_result(payload) == "Purple light guides the waves."


def test_build_local_worker_work_now_payload_matches_dev_live_session_contract():
    canvas = _load_canvas_module()

    args = SimpleNamespace(
        ring="3",
        capability="chat.completions",
        worker_model="gemma4:26b",
        worker_credits_per_token="0.001",
        worker_target_tokens=1024,
        worker_availability_mode="ai_idle",
        auto_worker_seconds=3600,
    )
    wallet = "0x" + "2" * 40
    payload = canvas.build_local_worker_work_now_payload(
        args,
        hub_url="http://127.0.0.1:8871",
        hub_status={
            "ok": True,
            "network": {"network_key": "dev", "chain_id": 42424242},
            "serving_hub": {"hub_id": "dev-hub1"},
        },
        wallet_address=wallet,
        app_url="http://127.0.0.1:8771",
        active_multisession_key_id="msk_worker_active",
    )

    assert payload["action"] == "work-now"
    assert payload["network"] == "dev"
    assert payload["requested_ring"] == "3"
    assert payload["wallet_address"] == wallet
    assert payload["active_multisession_key_id"] == "msk_worker_active"
    assert payload["worker"]["node_id"].startswith("micro-agent-local-worker-")
    assert payload["worker"]["endpoint"] == "http://127.0.0.1:8771"
    assert payload["worker"]["model"] == "gemma4:26b"
    assert payload["worker"]["pricing"]["credits_per_request"] == "1.024"
    assert payload["worker"]["pricing"]["credits_per_request_wei"] == "1024000000000000000"
    assert payload["worker"]["capabilities"]["capabilities"] == ["chat.completions"]
    assert payload["worker"]["availability"]["availability_mode"] == "ai_idle"


def test_ensure_local_worker_available_selects_network_requests_key_and_posts_work_now(monkeypatch):
    canvas = _load_canvas_module()
    from main_computer.multisession_key_signing import private_key_to_address

    private_key = "0x1"
    wallet = private_key_to_address(private_key)
    calls = []

    def fake_http_json(method, url, payload=None, timeout=15.0):
        calls.append((method, url, payload))
        if url.endswith("/api/applications/worker/runtime-status"):
            # First status is not accepting; second status confirms the Work-now setup.
            seen_runtime = sum(1 for _method, prior_url, _payload in calls if prior_url.endswith("/api/applications/worker/runtime-status"))
            if seen_runtime <= 2:
                return 200, {"ok": True, "runtime": {"phase": "not_accepting", "allowed_to_accept": False}}
            return 200, {"ok": True, "runtime": {"phase": "accepting", "allowed_to_accept": True, "hub_status": "available"}}
        if url.endswith("/api/applications/worker/network-session"):
            assert method == "POST"
            assert payload == {"network": "dev", "requested_ring": "3"}
            return 200, {"ok": True, "session": {"connection_status": "connected"}}
        if url.endswith("/api/applications/worker/settings") and method == "GET":
            return 200, {"ok": True, "settings": {"remoteEnabled": False}}
        if url.endswith("/api/applications/worker/settings") and method == "POST":
            settings = payload["settings"]
            assert settings["selectedNetwork"] == "dev"
            assert settings["workerAutoConnectNetwork"] == "dev"
            assert settings["sellerEnabled"] is True
            assert settings["rentalEnabled"] is True
            assert settings["nodeId"].startswith("micro-agent-local-worker-")
            return 200, {"ok": True, "settings": settings}
        if url.endswith("/api/applications/worker/multisession-keys/load"):
            return 200, {"ok": True, "active_key": None, "keys": []}
        if url.endswith("/api/applications/worker/multisession-key/request"):
            signed = payload["signed_request"]
            assert signed["kind"] == "main_computer_multisession_key_request"
            assert signed["message"]["wallet_address"] == wallet
            return 200, {
                "ok": True,
                "key": {"id": "msk_worker_active", "status": "active"},
                "local_cache": {"stored": True, "key": {"id": "msk_worker_active", "status": "active"}},
            }
        if url.endswith("/api/applications/worker/work-now"):
            assert payload["active_multisession_key_id"] == "msk_worker_active"
            assert payload["network"] == "dev"
            assert payload["requested_ring"] == "3"
            assert payload["worker"]["pricing"]["credits_per_request"] == "1.024"
            return 200, {"ok": True, "runtime": {"phase": "accepting", "allowed_to_accept": True}}
        raise AssertionError(url)

    monkeypatch.setattr(canvas, "http_json", fake_http_json)
    monkeypatch.setattr(canvas.time, "sleep", lambda _seconds: None)
    args = SimpleNamespace(
        app="http://127.0.0.1:8771",
        no_auto_worker=False,
        private_key=private_key,
        private_key_file="",
        wallet="",
        ring="3",
        capability="chat.completions",
        worker_model="gemma4:26b",
        worker_credits_per_token="0.001",
        worker_target_tokens=1024,
        worker_availability_mode="ai_idle",
        auto_worker_seconds=3600,
        auto_worker_timeout=1.0,
        msk_lifetime_minutes=10,
    )

    ok = canvas.ensure_local_worker_available(
        args=args,
        hub_url="http://127.0.0.1:8871",
        hub_status={
            "ok": True,
            "network": {"network_key": "dev", "chain_id": 42424242},
            "serving_hub": {"hub_id": "dev-hub1"},
        },
    )

    assert ok is True
    assert [url.rsplit("/", 1)[-1] for _method, url, _payload in calls] == [
        "runtime-status",  # app URL discovery probe
        "runtime-status",  # setup preflight
        "network-session",
        "settings",
        "settings",
        "load",
        "request",
        "work-now",
        "runtime-status",
    ]


def test_local_worker_app_url_candidates_probe_control_app_before_legacy_standalone_worker(monkeypatch, tmp_path: Path):
    canvas = _load_canvas_module()

    monkeypatch.delenv("MAIN_COMPUTER_APP_URL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_CONTROL_URL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_VIEWPORT_URL", raising=False)
    args = SimpleNamespace(app="")

    candidates = canvas.local_worker_app_url_candidates(args, root=tmp_path)

    assert "http://127.0.0.1:8765" in candidates
    assert "http://127.0.0.1:8771" in candidates
    assert candidates.index("http://127.0.0.1:8765") < candidates.index("http://127.0.0.1:8771")


def test_resolve_local_worker_app_url_discovers_control_port_from_start_session(monkeypatch, tmp_path: Path):
    canvas = _load_canvas_module()
    start_session = tmp_path / "runtime" / "start_stop" / "start-session.json"
    start_session.parent.mkdir(parents=True)
    start_session.write_text(
        __import__("json").dumps(
            {
                "environment": {
                    "MAIN_COMPUTER_CONTROL_PORT": "8766",
                }
            }
        ),
        encoding="utf-8",
    )

    calls = []

    def fake_http_json(method, url, payload=None, timeout=15.0):
        calls.append(url)
        if url == "http://127.0.0.1:8766/api/applications/worker/runtime-status":
            return 200, {"ok": True, "runtime": {"phase": "not_accepting"}}
        return 599, {"ok": False, "error": "connection refused"}

    monkeypatch.delenv("MAIN_COMPUTER_APP_URL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_CONTROL_URL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_VIEWPORT_URL", raising=False)
    monkeypatch.setattr(canvas, "http_json", fake_http_json)

    assert canvas.resolve_local_worker_app_url(SimpleNamespace(app=""), root=tmp_path) == "http://127.0.0.1:8766"
    assert calls == ["http://127.0.0.1:8766/api/applications/worker/runtime-status"]


def test_resolve_local_worker_app_url_error_explains_8771_is_legacy(monkeypatch, tmp_path: Path):
    canvas = _load_canvas_module()

    def fake_http_json(method, url, payload=None, timeout=15.0):
        return 599, {"ok": False, "error": "connection refused"}

    monkeypatch.delenv("MAIN_COMPUTER_APP_URL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_CONTROL_URL", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_VIEWPORT_URL", raising=False)
    monkeypatch.setattr(canvas, "http_json", fake_http_json)

    try:
        canvas.resolve_local_worker_app_url(SimpleNamespace(app=""), root=tmp_path)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "http://127.0.0.1:8765" in message
    assert "http://127.0.0.1:8771 is the legacy standalone hub-worker port" in message


def test_worker_offer_endpoint_is_separate_from_discovered_app_control_url():
    canvas = _load_canvas_module()

    payload = canvas.build_local_worker_registration_payload(
        SimpleNamespace(
            capability="chat.completions",
            worker_model="gemma4:26b",
            worker_credits_per_token="0.001",
            worker_target_tokens=1024,
            worker_availability_mode="ai_idle",
            worker_endpoint="",
        ),
        wallet_address="0x" + "3" * 40,
        app_url="http://127.0.0.1:8765",
    )

    assert payload["endpoint"] == "http://127.0.0.1:8771"
