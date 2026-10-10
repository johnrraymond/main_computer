"""HTTP contract tests for the real NanoJev endpoint smoke's unattended preparation."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "game_projects/webgl-demo/tools/space_captain_bridge_nanojev_live_endpoint_smoke.py"
spec = importlib.util.spec_from_file_location("bridge_nanojev_auto_prepare_smoke", SCRIPT)
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


class FakeBackend:
    def __init__(self, *, phase="idle", fail_prepare=False, never_ready=False,
                 bad_manager=False, bad_decision=False, fail_after_prepare=False,
                 stale_captain_route=False, missing_captain_method=False,
                 invalid_batch=False, wrong_model_sha=False):
        self.phase = phase
        self.fail_prepare = fail_prepare
        self.never_ready = never_ready
        self.bad_manager = bad_manager
        self.bad_decision = bad_decision
        self.fail_after_prepare = fail_after_prepare
        self.stale_captain_route = stale_captain_route
        self.missing_captain_method = missing_captain_method
        self.invalid_batch = invalid_batch
        self.wrong_model_sha = wrong_model_sha
        self.native_model_calls = 0
        self.route_preflight_count = 0
        self.inference_count = 0
        self.prepares = []
        self.calls = []
        self.status_count = 0
        self.manager_url = ""
        self.lock = threading.Lock()

    def status(self):
        with self.lock:
            self.status_count += 1
            if self.phase == "warming-ai" and not self.never_ready and self.status_count >= 3:
                self.phase = "error" if self.fail_after_prepare else "performance-ready"
            ready = self.phase == "performance-ready"
            return {
                "ok": not (self.phase == "error"), "phase": self.phase,
                "lastError": "checkpoint warmup failed" if self.phase == "error" else "",
                "managerUrl": self.manager_url, "modelLoaded": ready,
                "performance": {"ready": ready, "consecutivePasses": 3 if ready else 0,
                                "requiredConsecutivePasses": 3, "timeStepSeconds": 5},
                "adapter": {"running": ready, "checkpointId": "real-checkpoint" if ready else None,
                            "checkpointSha256": "real-sha" if ready else None},
            }

    def manager(self):
        ready = self.phase == "performance-ready" and not self.bad_manager
        return {"ok": True, "running": ready, "backend_ready": ready,
                "model_loaded": ready, "checkpoint_validated": ready,
                "runtime_state": "ready" if ready else "loading", "container_state": "running"}

    def decision(self, observation):
        assert observation["schema"] == "game.bridgeCaptainObservation.v1"
        assert observation["captainId"] == "captain.beta"
        return {"ok": True, "source": "nanojev-captain-v6",
                "captainId": "captain.beta", "shipId": "ship.beta", "maneuver": "approach",
                "modelReceipt": {"checkpointId": "real-checkpoint",
                                 "checkpointSha256": "wrong" if self.bad_decision else "real-sha"}}


@contextmanager
def fake_servers(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def reply(self, status, payload):
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            fake.calls.append(("GET", self.path))
            if self.path == "/control/status":
                self.reply(200, fake.manager())
            elif self.path == "/api/health":
                self.reply(200, {"ready": True, "model_family": "nanojev-clef",
                    "release_name": "real-checkpoint", "release_manifest_sha256": "real-sha"})
            elif self.path == smoke.GAME_API + "/status":
                self.reply(200, fake.status())
            else:
                self.reply(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = json.loads(raw)
            fake.calls.append(("POST", self.path))
            if self.path == "/api/evaluate-batch":
                fake.native_model_calls += 1
                if fake.invalid_batch:
                    self.reply(200, {"schema": "nanojev.pairwise-batch-result.v1",
                        "model": {"release_name": "real-checkpoint", "release_manifest_sha256": "real-sha"},
                        "results": [{"id": "wrong", "choice": "invented"}]})
                else:
                    assert data["schema"] == "nanojev.pairwise-batch.v1"
                    assert len(data["questions"]) == 10
                    answers = [{"id": q["id"], "choice": q["candidates"][0]["id"], "margin": 0.25}
                               for q in data["questions"]]
                    self.reply(200, {"schema": "nanojev.pairwise-batch-result.v1",
                        "model": {"release_name": "real-checkpoint", "release_manifest_sha256":
                           "invalid-model-sha" if fake.wrong_model_sha else "real-sha"},
                        "results": answers, "metrics": {"model_latency_ms": 50}})
            elif self.path == smoke.GAME_API + "/prepare":
                if fake.fail_prepare:
                    self.reply(409, {"ok": False, "error": "Cannot prepare while battle is running"})
                else:
                    fake.prepares.append(data)
                    fake.phase = "warming-ai"
                    self.reply(200, {"ok": True, "phase": "starting-ai"})
            elif self.path == smoke.GAME_API + "/captain/decide":
                if data.get("schema") == "game.bridgeCaptainRoutePreflight.v1":
                    fake.route_preflight_count += 1
                    if fake.stale_captain_route:
                        self.reply(400, {"ok": False, "error": "Unknown Tactical AI route."})
                    elif fake.missing_captain_method:
                        self.reply(400, {"ok": False, "error": "'TacticalAIService' object has no attribute 'decide_bridge_captain'"})
                    else:
                        self.reply(409, {"ok": False, "error": "BRIDGE_CAPTAIN_OBSERVATION_SCHEMA_REQUIRED"})
                else:
                    fake.inference_count += 1
                    self.reply(200, fake.decision(data))
            else:
                self.reply(404, {"ok": False, "error": "not found"})

    manager_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    game_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.manager_url = f"http://127.0.0.1:{manager_server.server_port}"
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (manager_server, game_server)]
    for t in threads:
        t.start()
    try:
        yield f"http://127.0.0.1:{game_server.server_port}"
    finally:
        for s in (manager_server, game_server):
            s.shutdown()
            s.server_close()
        for t in threads:
            t.join(timeout=2)


def args(base, **changes):
    params = dict(base_url=base, manager_url=None, no_prepare=False, probe_route_only=False, game_route=True,
                  time_step_seconds=5, consecutive_passes=3,
                  warmup_timeout_seconds=120, startup_timeout_seconds=180,
                  request_timeout_seconds=300, ready_timeout_seconds=0.25,
                  decision_timeout_seconds=1.0, http_timeout_seconds=1.0,
                  poll_seconds=0.01)
    params.update(changes)
    return argparse.Namespace(**params)


def paths(fake, method, suffix):
    return sum(method == m and path.endswith(suffix) for m, path in fake.calls)


def test_cold_manager_and_game_prepare_automatically_then_infer():
    fake = FakeBackend()
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is True, result
    assert result["prepared"] is True
    assert result["prepareRequested"] is True
    assert result["decision"]["source"] == "nanojev-captain-v6"
    assert len(fake.prepares) == 1
    assert fake.prepares[0] == {
        "time_step_seconds": 5, "consecutive_passes": 3,
        "warmup_timeout_seconds": 120, "startup_timeout_seconds": 180,
        "request_timeout_seconds": 300,
    }
    assert paths(fake, "GET", "/control/status") >= 2
    assert paths(fake, "GET", "/status") >= 2
    assert paths(fake, "POST", "/prepare") == 1
    assert fake.inference_count == 1
    assert paths(fake, "POST", "/battle/start") == 0
    assert paths(fake, "POST", "/reset") == 0


def test_prepared_model_skips_prepare():
    fake = FakeBackend(phase="performance-ready")
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is True
    assert result["prepareRequested"] is False
    assert paths(fake, "POST", "/prepare") == 0
    assert fake.inference_count == 1


def test_existing_prepare_is_not_restarted():
    fake = FakeBackend(phase="warming-ai")
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is True
    assert paths(fake, "POST", "/prepare") == 0
    assert any(e["event"] == "existing-preparation" for e in result["events"])


def test_no_prepare_is_read_only_and_fails_clearly():
    fake = FakeBackend()
    with fake_servers(fake) as base:
        result = smoke.run(args(base, no_prepare=True))
    assert result["ok"] is False
    assert result["failedStage"] == "prepare"
    assert "--no-prepare" in result["error"]
    assert paths(fake, "POST", "/prepare") == 0
    assert fake.inference_count == 0


def test_warmup_error_includes_actual_service_error():
    fake = FakeBackend(fail_after_prepare=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is False
    assert result["failedStage"] == "wait-ready"
    assert "checkpoint warmup failed" in result["error"]
    assert fake.inference_count == 0


def test_manager_not_verified_rejects_inference():
    fake = FakeBackend(phase="performance-ready", bad_manager=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is False
    assert result["failedStage"] == "verify-checkpoint"
    assert "manager does not confirm" in result["error"]
    assert fake.inference_count == 0


def test_wrong_checkpoint_receipt_never_passes():
    fake = FakeBackend(phase="performance-ready", bad_decision=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is False
    assert result["failedStage"] == "captain-inference"
    assert "checkpoint-backed" in result["error"]


def test_prepare_conflict_surfaces_server_message():
    fake = FakeBackend(fail_prepare=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is False
    assert "Cannot prepare while battle is running" in result["error"]
    assert fake.inference_count == 0


def test_ready_timeout_never_resets_model_or_launches_battle():
    fake = FakeBackend(never_ready=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base, ready_timeout_seconds=0.035))
    assert result["ok"] is False
    assert result["failedStage"] == "wait-ready"
    assert "did not become performance-ready" in result["error"]
    assert paths(fake, "POST", "/reset") == 0
    assert paths(fake, "POST", "/battle/start") == 0
    assert fake.inference_count == 0


def test_main_prints_one_machine_readable_json_document(capsys):
    fake = FakeBackend()
    with fake_servers(fake) as base:
        code = smoke.main(["--base-url", base, "--poll-seconds", "0.01", "--ready-timeout-seconds", "0.5"])
    captured = capsys.readouterr()
    assert code == 0
    report = json.loads(captured.out)
    assert report["ok"] is True
    assert "NanoJev:" not in captured.out


def test_previous_failed_preparation_can_be_retried():
    fake = FakeBackend(phase="error")
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is True, result
    assert result["prepareRequested"] is True
    assert paths(fake, "POST", "/prepare") == 1


def test_current_captain_route_preflight_is_non_inference_and_non_mutating():
    fake = FakeBackend(phase="performance-ready")
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is True, result
    assert result["captainRoutePreflight"]["ok"] is True
    assert result["captainRoutePreflight"]["httpStatus"] == 409
    assert fake.route_preflight_count == 1
    assert fake.inference_count == 1
    assert paths(fake, "POST", "/prepare") == 0


def test_stale_live_viewport_route_fails_before_warmup():
    fake = FakeBackend(stale_captain_route=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is False
    assert result["failedStage"] == "captain-route-preflight"
    assert "MAIN_COMPUTER_VIEWPORT_STALE" in result["error"]
    assert "dev-control.ps1 restart -Mode local" in result["error"]
    assert result["captainRoutePreflight"]["httpStatus"] == 400
    assert result["captainRoutePreflight"]["error"] == "Unknown Tactical AI route."
    assert fake.route_preflight_count == 1
    assert fake.inference_count == 0
    assert paths(fake, "POST", "/prepare") == 0
    assert paths(fake, "POST", "/reset") == 0


def test_stale_service_module_missing_captain_method_is_diagnosed():
    fake = FakeBackend(missing_captain_method=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base))
    assert result["ok"] is False
    assert result["failedStage"] == "captain-route-preflight"
    assert "MAIN_COMPUTER_VIEWPORT_STALE" in result["error"]
    assert fake.inference_count == 0
    assert paths(fake, "POST", "/prepare") == 0


def test_route_only_checks_running_handler_without_starting_model():
    fake = FakeBackend(phase="idle")
    with fake_servers(fake) as base:
        result = smoke.run(args(base, probe_route_only=True))
    assert result["ok"] is True
    assert result["routeOnly"] is True
    assert result["prepared"] is False
    assert result["captainRoutePreflight"]["ok"] is True
    assert fake.route_preflight_count == 1
    assert fake.inference_count == 0
    assert paths(fake, "POST", "/prepare") == 0
    assert paths(fake, "GET", "/control/status") == 0


def test_actual_viewport_route_contract_responds_before_model_readiness(tmp_path):
    """The deployed source accepts the route without preparing NanoJev."""
    from types import SimpleNamespace

    from main_computer.tactical_ai_service import TacticalAIService
    from main_computer.viewport_routes_game import ViewportGameRoutesMixin

    class Handler:
        path = smoke.GAME_API + "/captain/decide"
        server = SimpleNamespace(signal=lambda *a, **kw: None)

        def _read_json(self):
            return {"schema": "game.bridgeCaptainRoutePreflight.v1"}

        def _tactical_ai_service(self):
            return TacticalAIService(tmp_path)

        def _send_json(self, body, status=200):
            self.result = (int(status), body)

    handler = Handler()
    ViewportGameRoutesMixin._handle_tactical_ai_post(handler)
    assert handler.result == (409, {
        "ok": False,
        "error": "BRIDGE_CAPTAIN_OBSERVATION_SCHEMA_REQUIRED",
    })


def test_direct_manager_mode_ignores_missing_app_captain_route_and_evaluates_real_model_contract():
    fake = FakeBackend(stale_captain_route=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base, game_route=False))
    assert result["ok"] is True, result
    assert result["inferenceMode"] == "nanojev-manager-direct"
    assert result["directModelVerified"] is True
    assert result["gameIntegrationVerified"] is False
    assert result["decision"]["source"] == "nanojev-manager-direct-inference"
    assert result["decision"]["modelReceipt"]["checkpointSha256"] == "real-sha"
    assert result["decision"]["gameplayOrderIssued"] is False
    assert fake.native_model_calls == 1
    assert fake.route_preflight_count == 0
    assert fake.inference_count == 0


def test_direct_manager_batch_rejects_invalid_model_results():
    fake = FakeBackend(phase="performance-ready", invalid_batch=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base, game_route=False))
    assert result["ok"] is False
    assert result["failedStage"] == "captain-inference"
    assert "NANOJEV_BATCH_RESULT_COUNT_MISMATCH" in result["error"]
    assert fake.native_model_calls == 1


def test_direct_manager_rejects_checkpoint_mismatch_in_response():
    fake = FakeBackend(phase="performance-ready", wrong_model_sha=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base, game_route=False))
    assert result["ok"] is False
    assert "NANOJEV_RESPONSE_CHECKPOINT_MISMATCH" in result["error"]
    assert fake.native_model_calls == 1


def test_direct_manager_can_auto_prepare_cold_backend_without_main_app_route():
    fake = FakeBackend(stale_captain_route=True)
    with fake_servers(fake) as base:
        result = smoke.run(args(base, game_route=False))
    assert result["ok"] is True, result
    assert result["prepareRequested"] is True
    assert fake.native_model_calls == 1
    assert fake.route_preflight_count == 0
