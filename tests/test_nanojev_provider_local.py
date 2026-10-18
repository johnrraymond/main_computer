"""Local NanoJev/CLEF provider contract tests; no Hub integration."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage
from main_computer.providers.nanojev import (
    CLEF_BASE_URL, CLEF_EVALUATE, CLEF_PAIRWISE, NanoJevClefProvider, NanoJevProviderError,
)
from main_computer.router import MainComputer
from tools.nanojev_provider_smoke import main as smoke_main

SHA = "a" * 64


@pytest.fixture
def clef_service():
    events = []
    replies = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, result):
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            events.append(("GET", self.path, None))
            self.respond(replies.get(self.path, {
                "ready": True, "model_loaded_once": True, "model_family": "nanojev-clef",
                "release_name": "CLEF-test", "release_manifest_sha256": SHA,
            }))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            events.append(("POST", self.path, body))
            self.respond(replies[self.path])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", events, replies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _model():
    return {"family": "nanojev-clef", "release_name": "CLEF-test", "release_manifest_sha256": SHA}


def _evaluate_payload():
    return {"states": [{"id": "s", "state": "A tactical problem", "questions": {
        "q": {"type": "choice", "instructions": "Which move?",
              "criteria": {"left": "A", "right": "B"}},
    }}]}


def _evaluate_response():
    return {"states": [{"id": "s", "answers": {"q": {
        "type": "choice", "choice": "right", "value": "right",
        "probabilities": {"left": 0.25, "right": 0.75},
    }}}], "model": _model()}


def test_local_structured_evaluation_preserves_checkpoint(clef_service):
    url, events, replies = clef_service
    replies["/api/evaluate"] = _evaluate_response()
    result = NanoJevClefProvider(base_url=url).solve(
        capability=CLEF_EVALUATE, payload=_evaluate_payload())
    assert result["schema"] == "main-computer.nanojev-solution.v1"
    assert result["result"]["states"][0]["answers"]["q"]["choice"] == "right"
    assert result["model"]["release_manifest_sha256"] == SHA
    assert [e[1] for e in events] == ["/api/health", "/api/evaluate"]


def test_pairwise_batch_preserves_candidates_and_probabilities(clef_service):
    url, events, replies = clef_service
    replies["/api/evaluate-batch"] = {
        "schema": "nanojev.pairwise-batch-result.v1", "model": _model(),
        "results": [{"id": "x", "choice": "b", "candidate_ids": ["a", "b"],
                     "probabilities": [0.1, 0.9], "margin": 0.8}],
    }
    request = {"schema": "nanojev.pairwise-batch.v1", "shared_context": "Context",
               "questions": [{"id": "x", "prompt": "Question", "candidates": [
                   {"id": "a", "text": "A"}, {"id": "b", "text": "B"}]}]}
    solution = NanoJevClefProvider(base_url=url).solve(capability=CLEF_PAIRWISE, payload=request)
    assert solution["result"]["results"][0]["choice"] == "b"
    assert events[-1][1] == "/api/evaluate-batch"


def test_chat_and_unknown_capability_fail_before_network(clef_service):
    url, events, _ = clef_service
    p = NanoJevClefProvider(base_url=url)
    with pytest.raises(NanoJevProviderError, match="not chat.completions"):
        p.chat([ChatMessage(role="user", content="Solve")])
    with pytest.raises(NanoJevProviderError, match="Unsupported"):
        p.solve(capability="chat.completions", payload={})
    assert events == []


def test_bad_checkpoint_fails_closed(clef_service):
    url, _, replies = clef_service
    replies["/api/evaluate"] = _evaluate_response()
    replies["/api/evaluate"]["model"]["release_manifest_sha256"] = "b" * 64
    with pytest.raises(NanoJevProviderError, match="checkpoint changed"):
        NanoJevClefProvider(base_url=url).solve(capability=CLEF_EVALUATE, payload=_evaluate_payload())


def test_malformed_probability_distribution_fails(clef_service):
    url, _, replies = clef_service
    replies["/api/evaluate"] = _evaluate_response()
    replies["/api/evaluate"]["states"][0]["answers"]["q"]["probabilities"] = {"left": 0.8, "right": 0.8}
    with pytest.raises(NanoJevProviderError, match="distribution"):
        NanoJevClefProvider(base_url=url).solve(capability=CLEF_EVALUATE, payload=_evaluate_payload())


def test_unready_service_prevents_computation(clef_service):
    url, events, replies = clef_service
    replies["/api/health"] = {"ready": False}
    with pytest.raises(NanoJevProviderError, match="not ready"):
        NanoJevClefProvider(base_url=url).solve(capability=CLEF_EVALUATE, payload=_evaluate_payload())
    assert [e[1] for e in events] == ["/api/health"]


def test_provider_selection_and_router_solve(clef_service, monkeypatch):
    url, _, replies = clef_service
    replies["/api/evaluate"] = _evaluate_response()
    monkeypatch.setenv("MAIN_COMPUTER_PROVIDER", "nanojev")
    monkeypatch.setenv("MAIN_COMPUTER_NANOJEV_BASE_URL", url)
    monkeypatch.delenv("MAIN_COMPUTER_MODEL", raising=False)
    config = MainComputerConfig.from_env()
    assert config.model == "nanojev-clef"
    computer = MainComputer.build(config)
    assert isinstance(computer.provider, NanoJevClefProvider)
    result = computer.solve(capability=CLEF_EVALUATE, payload=_evaluate_payload())
    assert result["result"]["states"][0]["answers"]["q"]["choice"] == "right"


def test_bad_model_and_insecure_remote_endpoints_rejected():
    with pytest.raises(NanoJevProviderError, match="Unsupported CLEF model"):
        NanoJevClefProvider(model="gemma4")
    with pytest.raises(NanoJevProviderError, match="HTTPS"):
        NanoJevClefProvider(base_url="http://example.org:9766")


def test_local_smoke_cli(clef_service, tmp_path, capsys):
    url, _, replies = clef_service
    replies["/api/evaluate"] = _evaluate_response()
    payload_path = tmp_path / "clef.json"
    payload_path.write_text(json.dumps(_evaluate_payload()), encoding="utf-8")
    assert smoke_main(["--base-url", url, "--capability", CLEF_EVALUATE, "--payload-file", str(payload_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["capability"] == CLEF_EVALUATE
    assert result["result"]["model"]["release_manifest_sha256"] == SHA


def test_managed_lifecycle_proxy_is_provider_default(monkeypatch):
    """Default CLEF traffic goes through the 9765 lazy-manager, not its idle backend."""
    monkeypatch.delenv("MAIN_COMPUTER_NANOJEV_BASE_URL", raising=False)
    assert CLEF_BASE_URL == "http://127.0.0.1:9765"
    assert NanoJevClefProvider().base_url == CLEF_BASE_URL
    assert MainComputerConfig.from_env().nanojev_base_url == CLEF_BASE_URL
