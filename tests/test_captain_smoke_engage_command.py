from __future__ import annotations

import argparse
import json
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse

import main_computer.cli as cli
import main_computer.hub as hub
from main_computer.hub import _worker_pull_response_payload


def test_captain_make_it_so_still_delegates_to_existing_captain_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_captain(argv, *, config, cwd):
        captured["argv"] = list(argv)
        captured["config"] = config
        captured["cwd"] = cwd
        return 0

    monkeypatch.setattr(cli, "run_captain", fake_run_captain)

    args = argparse.Namespace(
        captain_args=["smoke", "john", "luc", "picard", "make", "it", "so"],
        workspace=None,
        provider=None,
        model=None,
        ollama_base_url=None,
        ollama_timeout_s=None,
        openai_base_url=None,
        hub_url=None,
        hub_timeout_s=None,
        hub_client_node_id=None,
        hub_worker_node_id=None,
        hub_worker_endpoint=None,
        hub_credits_per_request=None,
        hub_root=None,
        fallback=False,
    )

    assert cli.cmd_captain(args) == 0
    assert captured["argv"] == ["smoke", "john", "luc", "picard", "make", "it", "so"]


def test_captain_engage_computer_runs_ring3_worker_pull(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProvider:
        def chat(self, messages):
            return ChatResponse(content="Make it so.", provider="fake", model="fake-model", metadata={})

    class FakeComputer:
        provider = FakeProvider()

    def fake_build(config):
        captured["build_config"] = config
        return FakeComputer()

    def fake_worker_pull(config, chat_fn, **kwargs):
        captured["worker_config"] = config
        captured["kwargs"] = kwargs
        captured["chat_result"] = chat_fn([ChatMessage(role="user", content="ping")]).content

    monkeypatch.setattr(cli.MainComputer, "build", fake_build)
    monkeypatch.setattr(cli, "serve_hub_worker_pull", fake_worker_pull)
    monkeypatch.setattr(cli, "_captain_smoke_hub_url", lambda config, explicit_hub_url="": explicit_hub_url or "https://mainnet-hub.greatlibrary.io")

    args = argparse.Namespace(
        captain_args=[
            "smoke",
            "john",
            "luc",
            "picard",
            "engage",
            "computer",
            "--max-requests",
            "1",
            "--poll-interval-s",
            "0.1",
        ],
        workspace=None,
        provider="ollama",
        model="fake-model",
        ollama_base_url=None,
        ollama_timeout_s=None,
        openai_base_url=None,
        hub_url=None,
        hub_timeout_s=None,
        hub_client_node_id=None,
        hub_worker_node_id="local-ring3-worker",
        hub_worker_endpoint=None,
        hub_credits_per_request=1,
        hub_root=None,
        fallback=False,
    )

    assert cli.cmd_captain(args) == 0
    worker_config = captured["worker_config"]
    assert isinstance(worker_config, MainComputerConfig)
    assert worker_config.hub_url == "https://mainnet-hub.greatlibrary.io"
    assert worker_config.hub_worker_node_id == "local-ring3-worker"
    assert captured["kwargs"]["assigned_ring"] == 3
    assert captured["kwargs"]["max_requests"] == 1
    assert captured["kwargs"]["poll_interval_s"] == 0.1
    assert captured["chat_result"] == "Make it so."


def test_worker_pull_response_payload_reads_lease_messages() -> None:
    def chat_fn(messages):
        assert [message.content for message in messages] == ["hello"]
        return ChatResponse(content="engaged", provider="fake", model="fake-model", metadata={"seen": True})

    payload = _worker_pull_response_payload(
        chat_fn=chat_fn,
        lease={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert payload["status"] == "success"
    assert payload["response"]["content"] == "engaged"
    assert payload["response"]["metadata"]["worker_pull_v0"] is True
    assert payload["response"]["metadata"]["seen"] is True


def test_worker_registration_uses_v1_api_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"ok": True, "worker": {"node_id": "local-ring3-worker"}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(hub, "urlopen", fake_urlopen)

    result = hub.register_worker_with_hub(
        hub_url="https://mainnet-hub.greatlibrary.io",
        node_id="local-ring3-worker",
        endpoint="https://worker-pull.main-computer.local/local-ring3-worker",
        model="gemma4:26b",
        models=["gemma4:26b"],
        credits_per_request=1,
        assigned_ring=3,
        execution_mode="worker_pull_v0",
        capabilities={"worker_pull_v0": True},
        pricing={"pricing_type": "fixed_per_call_v0"},
    )

    assert result["ok"] is True
    assert captured["url"] == "https://mainnet-hub.greatlibrary.io/api/hub/v1/workers/register"
    headers = {str(key).lower(): value for key, value in captured["headers"].items()}
    assert headers["accept"] == "application/json"
    assert headers["user-agent"].startswith("main-computer-worker-cli/")
    assert headers["x-main-computer-client"] == "captain-engage-worker"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["assigned_ring"] == 3
    assert body["execution_mode"] == "worker_pull_v0"


def test_captain_engage_computer_reports_registration_error_without_traceback(monkeypatch, capsys) -> None:
    class FakeProvider:
        def chat(self, messages):
            return ChatResponse(content="unused", provider="fake", model="fake-model", metadata={})

    class FakeComputer:
        provider = FakeProvider()

    monkeypatch.setattr(cli.MainComputer, "build", lambda config: FakeComputer())
    monkeypatch.setattr(cli, "_captain_smoke_hub_url", lambda config, explicit_hub_url="": "https://mainnet-hub.greatlibrary.io")

    def fake_worker_pull(*args, **kwargs):
        raise RuntimeError("Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/v1/workers/register with HTTP 403: forbidden")

    monkeypatch.setattr(cli, "serve_hub_worker_pull", fake_worker_pull)

    args = argparse.Namespace(
        captain_args=["smoke", "john", "luc", "picard", "engage", "computer"],
        workspace=None,
        provider="ollama",
        model="fake-model",
        ollama_base_url=None,
        ollama_timeout_s=None,
        openai_base_url=None,
        hub_url=None,
        hub_timeout_s=None,
        hub_client_node_id=None,
        hub_worker_node_id="local-ring3-worker",
        hub_worker_endpoint=None,
        hub_credits_per_request=1,
        hub_root=None,
        fallback=False,
    )

    assert cli.cmd_captain(args) == 2
    out = capsys.readouterr().out
    assert "ERROR: Hub request failed for https://mainnet-hub.greatlibrary.io/api/hub/v1/workers/register" in out
    assert "Traceback" not in out
