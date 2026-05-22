#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

from main_computer.config import MainComputerConfig
from main_computer.docker_executor import DockerExecutor
from main_computer.executor_models import ExecutorRequest, ExecutorResult
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider, OllamaProvider
from main_computer.rag_smoke_framework import RECOMMENDED_SMOKE_CONCEPTS, SmokeOutcome, run_recommended_smoke_suite


@dataclass(frozen=True)
class DockerValidation:
    ok: bool
    status: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConceptLocalAiReview:
    concept_id: str
    concept_name: str
    deterministic_ok: bool
    model_ok: bool
    model_summary: str
    model_risk: str
    parsed: dict[str, Any]
    raw: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalAiDockerSuiteTrace:
    ok: bool
    schema_version: int
    run_id: str
    provider: str
    model: str
    concept_count: int
    deterministic_outcomes: list[dict[str, Any]]
    local_ai_reviews: list[dict[str, Any]]
    docker_validation: dict[str, Any]
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def extract_json_object(text: str) -> dict[str, Any]:
    original = str(text or "")
    text = original.strip()

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    start = text.find("{")
    if start < 0:
        raise ValueError(f"No JSON object found in model output:\n{original}")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
                    return json.loads(repaired)

    raise ValueError(f"Could not parse JSON object from model output:\n{original}")


def get_local_ollama_provider(*, model: str | None, stream_model: bool) -> LLMProvider:
    config = MainComputerConfig.from_env()
    return OllamaProvider(
        model=model or config.model or "gemma4:26b",
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        fallback=bool(stream_model or config.fallback),
        options={"temperature": 0, "num_predict": 192},
        think=False,
    )


def provider_summary(provider: LLMProvider) -> str:
    return f"provider={getattr(provider, 'name', provider.__class__.__name__)} model={getattr(provider, 'model', '')}".strip()


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "ok", "pass", "passed"}


def _review_system_prompt() -> str:
    return (
        "You are a local AI judge for Main Computer RAG smoke tests. "
        "You are validating one deterministic smoke-test outcome at a time. "
        "Use only the JSON supplied by the user. "
        "Return exactly one JSON object and no markdown. "
        "Schema: {"
        '"concept_id":"...",'
        '"ok":true,'
        '"summary":"one short sentence",'
        '"risk":"none|weak_evidence|unsafe|unclear"'
        "}. "
        "Set ok=true only when deterministic_ok is true and the details support the stated concept."
    )


def _review_user_payload(concept_id: str, outcome: SmokeOutcome) -> str:
    return json_dumps(
        {
            "concept_id": concept_id,
            "deterministic_ok": outcome.ok,
            "concept_name": outcome.name,
            "description": outcome.description,
            "details": outcome.details,
        }
    )


def call_local_ai_review(provider: LLMProvider, *, concept_id: str, outcome: SmokeOutcome) -> ConceptLocalAiReview:
    print(f"[rag-local-ai-docker] local AI review: {concept_id} {provider_summary(provider)}", flush=True)
    response: ChatResponse = provider.chat(
        [
            ChatMessage(role="system", content=_review_system_prompt()),
            ChatMessage(role="user", content=_review_user_payload(concept_id, outcome)),
        ]
    )
    raw = str(response.content or "").strip()
    parsed = extract_json_object(raw)
    returned_id = str(parsed.get("concept_id") or "").strip()
    model_ok = _safe_bool(parsed.get("ok"))
    if returned_id and returned_id != concept_id:
        model_ok = False
    return ConceptLocalAiReview(
        concept_id=concept_id,
        concept_name=outcome.name,
        deterministic_ok=outcome.ok,
        model_ok=model_ok,
        model_summary=str(parsed.get("summary") or "").strip(),
        model_risk=str(parsed.get("risk") or "unclear").strip(),
        parsed=parsed,
        raw=raw,
    )


def new_docker_executor(repo_dir: Path) -> DockerExecutor:
    config = MainComputerConfig.from_env()
    executor_root = config.executor_root
    if not executor_root.is_absolute():
        executor_root = repo_dir / executor_root

    return DockerExecutor(
        image=config.executor_image,
        runtime_root=executor_root,
        enabled=True,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )


def docker_status_summary(status: dict[str, Any]) -> str:
    return (
        f"enabled={status.get('enabled')} "
        f"ok={status.get('ok')} "
        f"docker_available={status.get('docker_available')} "
        f"image={status.get('image')} "
        f"runtime_root={status.get('runtime_root')}"
    )


def docker_validation_command() -> str:
    return r"""python - <<'PY'
import json
import os
import sys

trace = json.loads(os.environ["TRACE_JSON"])
failures = []

expected_ids = [
    "hybrid_retrieval",
    "contextual_chunk_enrichment",
    "parent_child_neighbor_expansion",
    "score_threshold_abstention",
    "query_rewrite_multi_query",
    "crag_retrieval_evaluator",
    "self_rag_critique_loop",
    "raptor_tree_hierarchy",
    "graphrag_local_global",
    "repo_map_ast_symbols",
    "file_caps_compaction",
    "retrieved_prompt_injection_guard",
    "precision_recall_goldset",
    "retrieval_trace_artifact",
]

if trace.get("schema_version") != 1:
    failures.append("schema_version must be 1")

if trace.get("concept_count") != len(expected_ids):
    failures.append(f"concept_count must be {len(expected_ids)}")

reviews = trace.get("local_ai_reviews") or []
outcomes = trace.get("deterministic_outcomes") or []
if len(reviews) != len(expected_ids):
    failures.append("every concept must have one local AI review")
if len(outcomes) != len(expected_ids):
    failures.append("every concept must have one deterministic outcome")

review_ids = [item.get("concept_id") for item in reviews]
if review_ids != expected_ids:
    failures.append("local AI reviews are missing expected concept ids or are out of order")

for outcome in outcomes:
    if outcome.get("ok") is not True:
        failures.append(f"deterministic outcome failed: {outcome.get('name')}")

for review in reviews:
    cid = review.get("concept_id")
    if review.get("deterministic_ok") is not True:
        failures.append(f"{cid}: deterministic_ok not preserved in model review")
    if review.get("model_ok") is not True:
        failures.append(f"{cid}: local AI did not accept the supported smoke outcome")
    if not str(review.get("model_summary") or "").strip():
        failures.append(f"{cid}: local AI summary is empty")
    if str(review.get("model_risk") or "").strip().lower() not in {"none", "weak_evidence", "unsafe", "unclear"}:
        failures.append(f"{cid}: local AI risk label is outside the allowed set")

if failures:
    print("RAG_LOCAL_AI_DOCKER_TRACE_FAILED", file=sys.stderr)
    for failure in failures:
        print("- " + failure, file=sys.stderr)
    raise SystemExit(1)

print("RAG_LOCAL_AI_DOCKER_TRACE_OK")
print(json.dumps({"concept_count": len(expected_ids), "provider": trace.get("provider"), "model": trace.get("model")}, sort_keys=True))
PY"""


def run_docker_validation(repo_dir: Path, trace_payload: dict[str, Any]) -> DockerValidation:
    docker_executor = new_docker_executor(repo_dir)
    status = docker_executor.status()
    print(f"[rag-local-ai-docker] docker executor status: {docker_status_summary(status)}", flush=True)

    if not status.get("docker_available"):
        return DockerValidation(
            ok=False,
            status=status,
            error="Docker is required because the local-AI review trace must be validated inside Docker.",
        )

    result: ExecutorResult = docker_executor.run(
        ExecutorRequest(
            command=docker_validation_command(),
            cwd="/workspace",
            timeout_s=30.0,
            network=False,
            input_ids=[],
            artifact_globs=[],
            description="Validate the RAG local-AI review trace inside Docker.",
            env={"TRACE_JSON": json.dumps(trace_payload, sort_keys=True)},
        )
    )

    return DockerValidation(
        ok=result.ok,
        status=status,
        result=result.as_dict(),
        error=None if result.ok else (result.stderr or result.stdout or result.error or "Docker validation failed."),
    )


def run_local_ai_docker_suite(
    *,
    repo_dir: Path,
    model: str | None = None,
    stream_model: bool = False,
    output_dir: Path | None = None,
) -> LocalAiDockerSuiteTrace:
    provider = get_local_ollama_provider(model=model, stream_model=stream_model)
    print(f"[rag-local-ai-docker] using {provider_summary(provider)}", flush=True)

    output_dir = output_dir or (repo_dir / "diagnostics_output" / "rag_local_ai_docker")
    output_dir.mkdir(parents=True, exist_ok=True)

    deterministic_outcomes = run_recommended_smoke_suite(output_dir)
    concept_ids = [concept.id for concept in RECOMMENDED_SMOKE_CONCEPTS]
    reviews: list[ConceptLocalAiReview] = []
    failures: list[str] = []

    for concept_id, outcome in zip(concept_ids, deterministic_outcomes):
        try:
            review = call_local_ai_review(provider, concept_id=concept_id, outcome=outcome)
        except Exception as exc:
            review = ConceptLocalAiReview(
                concept_id=concept_id,
                concept_name=outcome.name,
                deterministic_ok=outcome.ok,
                model_ok=False,
                model_summary="",
                model_risk="unclear",
                parsed={"error": str(exc)},
                raw="",
            )
            failures.append(f"{concept_id}: local AI review failed: {exc}")
        reviews.append(review)
        if not outcome.ok:
            failures.append(f"{concept_id}: deterministic smoke outcome failed")
        if not review.model_ok:
            failures.append(f"{concept_id}: local AI review did not pass")

    trace_payload = {
        "schema_version": 1,
        "run_id": "rag_local_ai_docker",
        "provider": getattr(provider, "name", provider.__class__.__name__),
        "model": getattr(provider, "model", ""),
        "concept_count": len(concept_ids),
        "deterministic_outcomes": [outcome.as_dict() for outcome in deterministic_outcomes],
        "local_ai_reviews": [review.as_dict() for review in reviews],
    }
    docker_validation = run_docker_validation(repo_dir, trace_payload)
    if not docker_validation.ok:
        failures.append(f"docker validation failed: {docker_validation.error}")

    trace = LocalAiDockerSuiteTrace(
        ok=not failures,
        schema_version=1,
        run_id="rag_local_ai_docker",
        provider=trace_payload["provider"],
        model=trace_payload["model"],
        concept_count=len(concept_ids),
        deterministic_outcomes=trace_payload["deterministic_outcomes"],
        local_ai_reviews=trace_payload["local_ai_reviews"],
        docker_validation=docker_validation.as_dict(),
        failures=failures,
    )

    trace_path = output_dir / "rag_local_ai_docker_trace.json"
    trace_path.write_text(json_dumps(trace.as_dict()) + "\n", encoding="utf-8")
    print(f"[rag-local-ai-docker] trace={trace_path}", flush=True)
    return trace


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run every recommended RAG smoke concept through local Ollama and Docker trace validation."
    )
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="Repo root. Defaults to current directory.")
    parser.add_argument("--model", default=None, help="Override Ollama model. Defaults to MAIN_COMPUTER_MODEL/config model.")
    parser.add_argument("--stream", action="store_true", help="Use Ollama stream=true fallback mode.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for trace artifacts.")
    parser.add_argument("--dump-json", action="store_true", help="Print the full JSON trace.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    trace = run_local_ai_docker_suite(
        repo_dir=args.repo_dir.resolve(),
        model=args.model,
        stream_model=args.stream,
        output_dir=args.output_dir,
    )

    if args.dump_json or not trace.ok:
        print(json_dumps(trace.as_dict()))
    else:
        print("[rag-local-ai-docker] passed")
        print(f"[rag-local-ai-docker] model={trace.model}")
        print(f"[rag-local-ai-docker] concepts={trace.concept_count}")

    if trace.ok:
        return 0

    print("[rag-local-ai-docker] failed", file=sys.stderr)
    for failure in trace.failures:
        print(f"  - {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
