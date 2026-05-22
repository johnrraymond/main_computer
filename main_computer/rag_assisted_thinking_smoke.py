#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from main_computer.activity import ActivityBus
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_harness import run_rag_harness


DEFAULT_PROMPT = (
    "Use RAG-assisted thinking to inspect how the Activity Monitor should show "
    "retrieval, local Ollama thinking calls, and the grounded answer flow. "
    "Do not run Docker in this smoke."
)

DEFAULT_QUERIES = [
    "rag assisted thinking",
    "activity monitor",
    "ollama think",
    "local ai model call",
    "grounded answer",
]


class FakeThinkingProvider:
    """Mock local MoE/Ollama-style thinking provider.

    The fake returns JSON in ChatResponse.content and stores fake private
    thinking in metadata. The smoke verifies that Activity Monitor receives
    model-call activity but does not expose the raw thinking trace.
    """

    name = "fake-ollama"
    model = "fake-moe-thinking-model"

    def __init__(self, *, think: bool | str = "medium") -> None:
        self.think = think
        self.calls = 0
        self.seen_messages: list[list[ChatMessage]] = []

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        self.seen_messages.append(list(messages))

        if self.calls == 1:
            content = {
                "task_type": "rag_assisted_thinking_backend_smoke",
                "goal": "Verify that RAG-assisted thinking can retrieve context and report visible activity events.",
                "needs": [
                    "RAG-assisted thinking backend entrypoint",
                    "Activity Monitor event stream",
                    "Ollama think flag boundary",
                    "safe non-Docker default behavior",
                ],
                "retrieval_queries": DEFAULT_QUERIES,
                "candidate_paths": [
                    "main_computer/rag_assisted_thinking.py",
                    "main_computer/rag_activity.py",
                    "main_computer/rag_harness.py",
                    "main_computer/providers/ollama.py",
                    "main_computer/activity.py",
                ],
                "executor_likely_needed": False,
                "risk": "read_only_analysis",
            }
        else:
            content = {
                "type": "answer",
                "summary": (
                    "Mocked RAG-assisted thinking completed: retrieval supplied the thinking context, "
                    "the local thinking provider was called, and Activity Monitor received correlated events."
                ),
                "evidence": [
                    {
                        "path": "main_computer/rag_assisted_thinking.py",
                        "reason": "Defines the intended backend request mode and policy boundary.",
                    },
                    {
                        "path": "main_computer/rag_activity.py",
                        "reason": "Emits safe RAG and model-call activity into Activity Monitor.",
                    },
                    {
                        "path": "main_computer/rag_harness.py",
                        "reason": "Builds decomposition, retrieval, context brief, and grounded prompt steps.",
                    },
                ],
                "next_step": {
                    "kind": "proposal",
                    "description": (
                        "Wire the real rag_assisted_thinking backend module to this smoke contract, "
                        "then connect the frontend toggle."
                    ),
                    "requires_executor": False,
                    "requires_approval": False,
                },
                "open_questions": [],
            }

        return ChatResponse(
            content=json.dumps(content, sort_keys=True),
            provider=self.name,
            model=self.model,
            metadata={
                "think": self.think,
                "thinking": f"FAKE_INTERNAL_THINKING_TRACE_CALL_{self.calls}",
            },
        )


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def write_fixture_repo(path: Path) -> Path:
    """Create a tiny repo that represents the future backend surface."""

    path.mkdir(parents=True, exist_ok=True)
    (path / "main_computer").mkdir(exist_ok=True)
    (path / "main_computer" / "providers").mkdir(exist_ok=True)
    (path / "tests").mkdir(exist_ok=True)

    (path / "README.md").write_text(
        "# Main Computer fixture\n\n"
        "This fixture describes RAG-assisted thinking with local Ollama MoE models, "
        "Activity Monitor reporting, and safe Docker-disabled defaults.\n",
        encoding="utf-8",
    )

    (path / "main_computer" / "rag_assisted_thinking.py").write_text(
        "from __future__ import annotations\n\n"
        "class RagAssistedThinkingPolicy:\n"
        "    thinking_enabled = True\n"
        "    rag_enabled = True\n"
        "    docker_enabled = False\n\n"
        "def run_rag_assisted_thinking_request(prompt, *, activity_bus=None, provider=None):\n"
        "    \"\"\"Fixture placeholder for the real backend entrypoint.\"\"\"\n"
        "    return {'ok': True, 'mode': 'rag_assisted_thinking'}\n",
        encoding="utf-8",
    )

    (path / "main_computer" / "rag_activity.py").write_text(
        "class RagActivityEmitter:\n"
        "    \"\"\"Emits RAG run, retrieval, context brief, and model-call events.\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )

    (path / "main_computer" / "rag_harness.py").write_text(
        "def run_rag_harness():\n"
        "    \"\"\"Builds task decomposition, retrieval, context brief, and grounded prompts.\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )

    (path / "main_computer" / "providers" / "ollama.py").write_text(
        "class OllamaProvider:\n"
        "    \"\"\"Provider accepts a think flag and returns separate thinking metadata from final content.\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )

    (path / "main_computer" / "activity.py").write_text(
        "class ActivityBus:\n"
        "    \"\"\"Bounded Activity Monitor bus for safe visible machine work.\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )

    (path / "tests" / "test_rag_assisted_thinking.py").write_text(
        "def test_rag_assisted_thinking_activity_contract():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    return path


def event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        status = str(event.get("status") or "none")
        counts[status] = counts.get(status, 0) + 1
    return counts


def activity_summary(bus: ActivityBus, *, limit: int = 240) -> dict[str, Any]:
    live = bus.events(filter_id="live", limit=limit)
    rag = bus.events(filter_id="rag", limit=limit)
    thinking = bus.events(filter_id="thinking", limit=limit)
    ai = bus.events(filter_id="ai", limit=limit)

    return {
        "live_count": len(live),
        "rag_count": len(rag),
        "thinking_count": len(thinking),
        "ai_count": len(ai),
        "status_counts": event_counts(live),
        "rag_titles": [str(event.get("title") or "") for event in rag[:20]],
        "thinking_titles": [str(event.get("title") or "") for event in thinking[:20]],
        "events_preview": [
            {
                "id": event.get("id"),
                "source": event.get("source"),
                "title": event.get("title"),
                "status": event.get("status"),
                "tags": event.get("tags"),
                "data": {
                    key: value
                    for key, value in (event.get("data") or {}).items()
                    if key
                    in {
                        "run_id",
                        "step",
                        "stage",
                        "provider",
                        "model",
                        "raw_thinking_exposed",
                    }
                },
            }
            for event in live[:24]
        ],
    }


def raw_thinking_leaked(events: list[dict[str, Any]]) -> bool:
    serialized = json.dumps(events, sort_keys=True, default=str)
    return "FAKE_INTERNAL_THINKING_TRACE" in serialized


def run_mocked_backend_call(
    *,
    prompt: str,
    repo_dir: Path,
    output_root: Path,
    run_id: str,
    max_context_chars: int,
) -> dict[str, Any]:
    """Mock the future main_computer.rag_assisted_thinking backend call.

    Contract to preserve when the real module lands:

        prompt + repo_dir + activity_bus + thinking provider + RAG enabled
        -> response containing run_id, content, provider/model, and no raw thinking exposure
    """

    provider = FakeThinkingProvider(think="medium")
    bus = ActivityBus(repo_dir)

    result = run_rag_harness(
        prompt=prompt,
        repo_dir=repo_dir,
        queries=DEFAULT_QUERIES,
        output_root=output_root,
        max_context_chars=max_context_chars,
        use_model=True,
        provider=provider,
        run_id=run_id,
        activity_bus=bus,
    )

    live_events = bus.events(filter_id="live", limit=240)
    rag_events = bus.events(filter_id="rag", limit=240)
    thinking_events = bus.events(filter_id="thinking", limit=240)

    retrieved_paths: list[str] = []
    for candidate in result.retrieval.candidates:
        if candidate.path not in retrieved_paths:
            retrieved_paths.append(candidate.path)

    failures: list[str] = []
    warnings: list[str] = []

    if not result.ok:
        failures.append("RAG harness result was not ok.")

    if provider.calls < 2:
        failures.append(f"Expected at least 2 provider calls, got {provider.calls}.")

    if not any(event.get("title") == "RAG run started" for event in rag_events):
        failures.append("Activity Monitor did not receive 'RAG run started'.")

    if not any(
        (event.get("data") or {}).get("step") == "retrieval"
        and event.get("status") == "completed"
        for event in rag_events
    ):
        failures.append("Activity Monitor did not receive a completed retrieval step.")

    if not any(event.get("title") == "Local AI RAG call completed" for event in thinking_events):
        failures.append("Activity Monitor did not receive completed local AI model-call activity.")

    if not any(event.get("title") == "RAG run completed" for event in rag_events):
        failures.append("Activity Monitor did not receive 'RAG run completed'.")

    if raw_thinking_leaked(live_events):
        failures.append("Raw provider thinking leaked into Activity Monitor events.")

    if not retrieved_paths:
        failures.append("No repository paths were retrieved.")

    if not any(path.endswith("rag_assisted_thinking.py") for path in retrieved_paths):
        warnings.append("Backend entrypoint path was not among retrieved paths.")

    response = {
        "ok": not failures,
        "mode": "rag_assisted_thinking_smoke",
        "run_id": result.run_id,
        "content": result.final_plan.get("summary", ""),
        "provider": provider.name,
        "model": provider.model,
        "think": provider.think,
        "raw_thinking_exposed": False,
        "final_plan": result.final_plan,
        "diagnostics_output_dir": result.output_dir,
    }

    report = {
        "ok": not failures,
        "run_id": result.run_id,
        "scenario": "mocked_backend_rag_assisted_thinking",
        "repo_dir": str(repo_dir),
        "output_dir": result.output_dir,
        "prompt": prompt,
        "response": response,
        "activity": activity_summary(bus),
        "retrieved_paths": retrieved_paths,
        "retrieved_path_count": len(retrieved_paths),
        "provider_calls": provider.calls,
        "failures": failures,
        "warnings": warnings,
    }

    report_path = Path(result.output_dir) / "rag_assisted_thinking_smoke_report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json_dumps(report) + "\n", encoding="utf-8")

    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test the planned rag_assisted_thinking backend contract with a mocked "
            "local thinking provider and Activity Monitor events."
        )
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--run-id", default="", help="Defaults to rag_assisted_thinking_smoke_<timestamp>.")
    parser.add_argument("--repo-dir", default="", help="Repository root. Defaults to this project root.")
    parser.add_argument(
        "--real-repo",
        action="store_true",
        help="Scan the real repository instead of a generated fixture repo.",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="Defaults to <repo>/diagnostics_output/rag_assisted_thinking_smoke/<run_id>/rag_runs.",
    )
    parser.add_argument("--max-context-chars", type=int, default=20_000)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero on warnings as well as failures.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    run_id = args.run_id or f"rag_assisted_thinking_smoke_{utc_stamp()}"

    repo_root = Path(args.repo_dir).resolve() if args.repo_dir else repo_root_from_script()
    smoke_root = repo_root / "diagnostics_output" / "rag_assisted_thinking_smoke" / run_id

    if args.real_repo:
        repo_dir = repo_root
    else:
        repo_dir = write_fixture_repo(smoke_root / "fixture_repo")

    output_root = Path(args.output_root).resolve() if args.output_root else smoke_root / "rag_runs"

    try:
        report = run_mocked_backend_call(
            prompt=args.prompt,
            repo_dir=repo_dir,
            output_root=output_root,
            run_id=run_id,
            max_context_chars=max(4_000, int(args.max_context_chars)),
        )
    except Exception as exc:
        print(f"rag_assisted_thinking_smoke failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json_dumps(report))
    else:
        print(f"RAG-assisted thinking smoke: {'PASS' if report['ok'] else 'FAIL'}")
        print(f"Run id: {report['run_id']}")
        print(f"Repo: {report['repo_dir']}")
        print(f"Output: {report['output_dir']}")
        print(f"Report: {report['report_path']}")
        print(f"Provider calls: {report['provider_calls']}")
        print(
            "Activity: "
            f"{report['activity'].get('rag_count', 0)} RAG events, "
            f"{report['activity'].get('thinking_count', 0)} thinking events, "
            f"{report['activity'].get('ai_count', 0)} AI events"
        )
        print(f"Retrieved paths: {', '.join(report['retrieved_paths'][:8]) or '(none)'}")

        if report["warnings"]:
            print("Warnings:")
            for warning in report["warnings"]:
                print(f"  - {warning}")

        if report["failures"]:
            print("Failures:")
            for failure in report["failures"]:
                print(f"  - {failure}")

    if report["failures"]:
        return 1

    if args.strict and report["warnings"]:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())