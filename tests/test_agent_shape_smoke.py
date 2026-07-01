from __future__ import annotations

import json
from pathlib import Path

from main_computer.agent_shape_smoke import (
    AgentControlCommand,
    AgentRunSpec,
    AgentShapeSmokeRunner,
    append_jsonl,
    digest_result_from_worker_text,
    evaluate_digest_result,
    make_digest_contract,
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FakeMicroAgentTransport:
    def __init__(self) -> None:
        self.posts = 0
        self.worker_prepared = False
        self.gets = 0
        self.payloads: list[dict] = []

    def _load_hub_identity(self, hub_url: str) -> dict:
        assert hub_url == "http://127.0.0.1:8871"
        return {
            "ok": True,
            "serving_hub": {"hub_id": "dev-hub1"},
            "network": {"network_key": "dev", "chain_id": 42424242},
            "backend": "foundationdb",
        }

    def extract_hub_status_summary(self, status: dict) -> dict:
        return {
            "hub_id": status["serving_hub"]["hub_id"],
            "network_key": status["network"]["network_key"],
            "chain_id": str(status["network"]["chain_id"]),
            "backend": status["backend"],
        }

    def request_fresh_multisession_authorization(self, *, args, hub_url: str, hub_status: dict, settings: dict) -> dict:
        return {
            "wallet_address": "0x" + "1" * 40,
            "multisession_key_id": "msk_requester",
            "key_id": "msk_requester",
            "chain_id": "42424242",
        }

    def build_work_payload(self, args, authorization: dict, hub_status: dict) -> dict:
        assert "Codebase Digest" in args.prompt
        assert args.worker_local_ai_timeout_seconds > 0
        assert args.worker_target_tokens > 0
        return {
            "ring": "ring-3",
            "capabilities": ["chat.completions"],
            "max_price": {"amount": "2", "unit": "compute_credit"},
            "input": {"messages": [{"role": "user", "content": args.prompt}]},
            "metadata": {"agent_shape_smoke": True},
        }

    def http_json(self, method: str, url: str, payload=None, timeout: float = 15.0):
        if method == "POST" and url.endswith("/api/hub/v1/work/requests"):
            self.posts += 1
            self.payloads.append(json.loads(json.dumps(payload or {})))
            if self.posts == 1:
                return 409, {"ok": False, "error": "no_live_worker_available"}
            return 200, {
                "ok": True,
                "status": "accepted",
                "continuation_url": "http://127.0.0.1:8871/api/hub/v1/work/sessions/sess_fake/stream",
            }
        if method == "GET" and url.endswith("/stream"):
            self.gets += 1
            if self.gets == 1:
                return 200, {
                    "ok": True,
                    "status": "accepted",
                    "stream": {
                        "realtime": {
                            "url": "http://127.0.0.1:8871/api/hub/v1/work/sessions/sess_fake/stream?format=sse"
                        }
                    },
                }
            return 200, {
                "ok": True,
                "status": "succeeded",
                "request": {
                    "response": {
                        "content": """# Codebase Digest

## Summary
Worker registration uses requester authorization, worker authorization, Hub dispatch, and live-session streaming.

## Relevant files
- `micro_agent_canvas.py`
- `main_computer/viewport_routes_energy.py`

## State machine
- requester authority checked
- worker availability checked
- paid Hub job dispatched
- terminal result received

## Risks
- worker setup can be stale

## Verification steps
- run the agent shape smoke
- inspect stream.jsonl

## Follow-up tasks
- Add Agent page controls.
- Wire the workflow into Temporal.
"""
                    }
                },
            }
        raise AssertionError((method, url, payload))

    def ensure_local_worker_available(self, *, args, hub_url: str, hub_status: dict) -> bool:
        self.worker_prepared = True
        return True

    def extract_simple_text_result(self, payload: dict) -> str:
        return payload.get("request", {}).get("response", {}).get("content", "")



class TerminalFailureMicroAgentTransport(FakeMicroAgentTransport):
    terminal_status = "failed"
    session_id = "sess_failed"
    failure_reason = "worker crashed during codebase digestion"

    def http_json(self, method: str, url: str, payload=None, timeout: float = 15.0):
        if method == "POST" and url.endswith("/api/hub/v1/work/requests"):
            self.posts += 1
            self.payloads.append(json.loads(json.dumps(payload or {})))
            return 200, {
                "ok": True,
                "status": "accepted",
                "continuation_url": f"http://127.0.0.1:8871/api/hub/v1/work/sessions/{self.session_id}/stream",
            }
        if method == "GET" and url.endswith("/stream"):
            self.gets += 1
            if self.gets == 1:
                return 200, {
                    "ok": True,
                    "status": "accepted",
                    "stream": {
                        "realtime": {
                            "url": f"http://127.0.0.1:8871/api/hub/v1/work/sessions/{self.session_id}/stream?format=sse"
                        }
                    },
                }
            return 200, {
                "ok": True,
                "status": self.terminal_status,
                "error": self.failure_reason,
                "request": {
                    "response": {
                        "content": """# Codebase Digest

## Summary
This is partial text from a failed worker session and must not be accepted.
"""
                    }
                },
            }
        raise AssertionError((method, url, payload))


class FailedTerminalMicroAgentTransport(TerminalFailureMicroAgentTransport):
    pass


class CancelledTerminalMicroAgentTransport(TerminalFailureMicroAgentTransport):
    terminal_status = "cancelled"
    session_id = "sess_cancelled"
    failure_reason = "operator cancelled the worker session"


class StaleTerminalSnapshotMicroAgentTransport(TerminalFailureMicroAgentTransport):
    session_id = "sess_stale_failed"
    failure_reason = "worker failed after realtime terminal event"

    def http_json(self, method: str, url: str, payload=None, timeout: float = 15.0):
        if method == "POST" and url.endswith("/api/hub/v1/work/requests"):
            self.posts += 1
            self.payloads.append(json.loads(json.dumps(payload or {})))
            return 200, {
                "ok": True,
                "status": "accepted",
                "continuation_url": f"http://127.0.0.1:8871/api/hub/v1/work/sessions/{self.session_id}/stream",
            }
        if method == "GET" and url.endswith("/stream"):
            self.gets += 1
            if self.gets == 1:
                return 200, {
                    "ok": True,
                    "status": "accepted",
                    "stream": {
                        "realtime": {
                            "url": f"http://127.0.0.1:8871/api/hub/v1/work/sessions/{self.session_id}/stream?format=sse"
                        }
                    },
                }
            if self.gets == 2:
                return 200, {
                    "ok": True,
                    "status": "running",
                    "request": {
                        "response": {
                            "content": """# Codebase Digest

## Summary
This partial text arrived before the authoritative snapshot caught up.
"""
                        }
                    },
                }
            return 200, {
                "ok": True,
                "status": "failed",
                "error": self.failure_reason,
                "request": {
                    "response": {
                        "content": """# Codebase Digest

## Summary
This partial text arrived before the authoritative snapshot caught up.
"""
                    }
                },
            }
        raise AssertionError((method, url, payload))


def test_agent_shape_smoke_uses_live_hub_transport_shape_and_writes_artifacts(tmp_path: Path) -> None:
    fake = FakeMicroAgentTransport()
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-run",
            repo_root=str(tmp_path),
            pause_after_current_job=True,
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=fake,
    )

    def fake_sse(_url: str, *, deadline: float) -> dict:
        runner.emit("hub_stream_delta", delta="Reading worker registration flow...")
        return {"type": "result", "status": "succeeded"}

    runner._read_sse_stream = fake_sse  # type: ignore[method-assign]

    run_record = runner.run()

    assert run_record["status"] == "paused_after_current_job"
    assert fake.worker_prepared is True
    assert fake.posts == 2
    assert runner.artifacts.run_json.exists()
    assert runner.artifacts.job_json.exists()
    assert runner.artifacts.stream_jsonl.exists()
    assert runner.artifacts.result_json.exists()
    assert runner.artifacts.artifact_md.exists()
    assert runner.artifacts.evaluation_json.exists()
    assert runner.artifacts.next_tasks_json.exists()

    events = [event["event"] for event in _load_jsonl(runner.artifacts.stream_jsonl)]
    assert "requester_authority_checked" in events
    assert "worker_availability_checked" in events
    assert "hub_submit_retry_result" in events
    assert "hub_stream_delta" in events
    assert events.index("hub_stream_delta") < events.index("terminal_result")

    evaluation = _load_json(runner.artifacts.evaluation_json)
    assert evaluation["accepted"] is True
    assert evaluation["pay_decision"] == "pay"

    next_tasks = _load_json(runner.artifacts.next_tasks_json)
    assert next_tasks["tasks"]

    job = _load_json(runner.artifacts.job_json)
    assert job["execution_limits"]["session_timeout"] == 420.0
    assert job["execution_limits"]["worker_local_ai_timeout_seconds"] == 300.0
    assert job["execution_limits"]["worker_target_tokens"] == 192
    assert "Hard limit: 160 words total." in job["prompt"]

    assert fake.payloads
    submitted_payload = fake.payloads[0]
    assert submitted_payload["timeout_seconds"] == 300.0
    assert submitted_payload["worker_timeout_seconds"] == 300.0
    assert submitted_payload["local_ai_timeout_seconds"] == 300.0
    assert submitted_payload["max_runtime_seconds"] == 300.0
    assert submitted_payload["target_tokens"] == 192
    assert submitted_payload["max_output_tokens"] == 192
    assert submitted_payload["metadata"]["worker_local_ai_timeout_seconds"] == 300.0
    assert submitted_payload["metadata"]["worker_target_tokens"] == 192
    assert submitted_payload["metadata"]["provider_options"]["num_predict"] == 192
    assert submitted_payload["metadata"]["ollama_options"]["num_predict"] == 192
    assert submitted_payload["metadata"]["think"] is False
    assert submitted_payload["metadata"]["ollama_think"] is False
    assert submitted_payload["metadata"]["completion_sentinel"] == "AGENT_SHAPE_DIGEST_DONE"
    assert submitted_payload["metadata"]["early_result_sentinel"] == "AGENT_SHAPE_DIGEST_DONE"
    assert submitted_payload["input"]["target_tokens"] == 192
    assert submitted_payload["input"]["max_output_tokens"] == 192
    assert submitted_payload["input"]["provider_options"]["num_predict"] == 192
    assert submitted_payload["input"]["ollama_options"]["num_predict"] == 192
    assert submitted_payload["input"]["think"] is False
    assert submitted_payload["input"]["ollama_think"] is False
    assert submitted_payload["input"]["completion_sentinel"] == "AGENT_SHAPE_DIGEST_DONE"
    assert submitted_payload["input"]["early_result_sentinel"] == "AGENT_SHAPE_DIGEST_DONE"


def test_control_command_add_instruction_reaches_worker_prompt(tmp_path: Path) -> None:
    fake = FakeMicroAgentTransport()
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-command",
            repo_root=str(tmp_path),
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=fake,
    )
    append_jsonl(
        runner.artifacts.commands_jsonl,
        {"type": "add_instruction", "text": "Focus on the Worker page path, not legacy worker-pull."},
    )
    runner._read_sse_stream = lambda _url, deadline: {"type": "result", "status": "succeeded"}  # type: ignore[method-assign]

    runner.run()

    job = _load_json(runner.artifacts.job_json)
    assert "Focus on the Worker page path" in job["prompt"]
    events = _load_jsonl(runner.artifacts.stream_jsonl)
    assert any(event["event"] == "control_command_applied" for event in events)


def test_custom_worker_execution_limits_reach_paid_work_payload(tmp_path: Path) -> None:
    fake = FakeMicroAgentTransport()
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-custom-worker-limits",
            repo_root=str(tmp_path),
            worker_local_ai_timeout_seconds=420.0,
            worker_target_tokens=512,
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=fake,
    )
    runner._read_sse_stream = lambda _url, deadline: {"type": "result", "status": "succeeded"}  # type: ignore[method-assign]

    runner.run()

    submitted_payload = fake.payloads[0]
    assert submitted_payload["timeout_seconds"] == 420.0
    assert submitted_payload["worker_timeout_seconds"] == 420.0
    assert submitted_payload["local_ai_timeout_seconds"] == 420.0
    assert submitted_payload["max_runtime_seconds"] == 420.0
    assert submitted_payload["target_tokens"] == 512
    assert submitted_payload["max_output_tokens"] == 512
    assert submitted_payload["metadata"]["worker_local_ai_timeout_seconds"] == 420.0
    assert submitted_payload["metadata"]["worker_target_tokens"] == 512



def test_failed_hub_terminal_status_rejects_partial_worker_text(tmp_path: Path) -> None:
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-failed-terminal",
            repo_root=str(tmp_path),
            session_timeout=1.0,
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=FailedTerminalMicroAgentTransport(),
    )
    runner._read_sse_stream = lambda _url, deadline: None  # type: ignore[method-assign]

    try:
        runner.run()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected failed terminal Hub session to fail the smoke")

    assert "terminal status failed" in message
    assert "worker crashed during codebase digestion" in message
    assert runner.artifacts.result_json.exists()
    assert not runner.artifacts.artifact_md.exists()
    assert not runner.artifacts.evaluation_json.exists()
    assert not runner.artifacts.next_tasks_json.exists()

    result_record = _load_json(runner.artifacts.result_json)
    assert result_record["terminal_status"] == "failed"
    assert result_record["failure_reason"] == "worker crashed during codebase digestion"
    assert result_record["partial_worker_text_present"] is True
    assert "partial text from a failed worker session" in result_record["partial_worker_text"]
    assert "partial worker output" in result_record["error"]

    run_record = _load_json(runner.artifacts.run_json)
    assert run_record["status"] == "failed"

    events = [event["event"] for event in _load_jsonl(runner.artifacts.stream_jsonl)]
    assert "terminal_result_failed" in events
    assert "workflow_failed" in events


def test_cancelled_hub_terminal_status_rejects_partial_worker_text(tmp_path: Path) -> None:
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-cancelled-terminal",
            repo_root=str(tmp_path),
            session_timeout=1.0,
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=CancelledTerminalMicroAgentTransport(),
    )
    runner._read_sse_stream = lambda _url, deadline: None  # type: ignore[method-assign]

    try:
        runner.run()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected cancelled terminal Hub session to fail the smoke")

    assert "terminal status cancelled" in message
    assert "operator cancelled the worker session" in message
    assert runner.artifacts.result_json.exists()
    assert not runner.artifacts.artifact_md.exists()
    assert not runner.artifacts.evaluation_json.exists()
    assert not runner.artifacts.next_tasks_json.exists()

    result_record = _load_json(runner.artifacts.result_json)
    assert result_record["terminal_status"] == "cancelled"
    assert result_record["failure_reason"] == "operator cancelled the worker session"
    assert result_record["partial_worker_text_present"] is True
    assert "partial text from a failed worker session" in result_record["partial_worker_text"]

    events = [event["event"] for event in _load_jsonl(runner.artifacts.stream_jsonl)]
    assert "terminal_result_failed" in events
    assert "workflow_failed" in events
    assert "result_evaluated" not in events
    assert "next_tasks_proposed" not in events


def test_realtime_terminal_event_waits_for_authoritative_failed_snapshot(tmp_path: Path) -> None:
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-stale-terminal-snapshot",
            repo_root=str(tmp_path),
            session_timeout=1.0,
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=StaleTerminalSnapshotMicroAgentTransport(),
    )
    runner._read_sse_stream = lambda _url, deadline: {"type": "failed", "status": "failed"}  # type: ignore[method-assign]

    try:
        runner.run()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected stale realtime failed event to fail after authoritative snapshot catches up")

    assert "terminal status failed" in message
    result_record = _load_json(runner.artifacts.result_json)
    assert result_record["terminal_status"] == "failed"
    assert result_record["failure_reason"] == "worker failed after realtime terminal event"
    assert "authoritative snapshot caught up" in result_record["partial_worker_text"]

    events = [event["event"] for event in _load_jsonl(runner.artifacts.stream_jsonl)]
    assert "session_terminal_event" in events
    assert "session_terminal_snapshot_pending" in events
    assert "terminal_result_failed" in events
    assert "terminal_result" not in events
    assert "result_evaluated" not in events
    assert "next_tasks_proposed" not in events


def test_digest_result_wraps_plain_worker_text_into_contract_artifact() -> None:
    contract = make_digest_contract(
        AgentRunSpec(mission="codebase-digestion", focus="worker-registration", ring="3", max_credits="2")
    )

    result = digest_result_from_worker_text("Worker registration probably involves viewport_routes_energy.py.", contract)
    evaluation = evaluate_digest_result(result)

    assert "# Codebase Digest" in result.artifact_markdown
    assert result.relevant_files
    assert result.follow_up_tasks
    assert evaluation.accepted is True



def test_hub_stream_event_deduplicates_replayed_status_frames(tmp_path: Path) -> None:
    runner = AgentShapeSmokeRunner(
        spec=AgentRunSpec(
            mission="codebase-digestion",
            focus="worker-registration",
            ring="3",
            max_credits="2",
            run_id="test-dedupe",
            repo_root=str(tmp_path),
        ),
        run_root=Path("runtime") / "agent_runs",
        transport=FakeMicroAgentTransport(),
    )

    accepted = {
        "type": "accepted",
        "status": "accepted",
        "seq": 0,
        "session_id": "sess_fake",
        "run_id": "run_fake",
        "request_id": "hub_fake",
    }
    assert runner._record_hub_stream_event(dict(accepted)) is True
    assert runner._record_hub_stream_event(dict(accepted)) is False
    runner._emit_session_status("accepted")
    runner._emit_session_status("accepted")
    runner._record_hub_stream_event({**accepted, "type": "delta", "status": "running", "seq": 1, "delta": "hello"})

    events = [event["event"] for event in _load_jsonl(runner.artifacts.stream_jsonl)]
    assert events.count("hub_stream_event") == 1
    assert events.count("session_status") == 1
    assert events.count("hub_stream_delta") == 1


def test_control_command_validation_rejects_unknown_command() -> None:
    try:
        AgentControlCommand.from_mapping({"type": "launch_missiles"})
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert "unsupported control command type" in message
