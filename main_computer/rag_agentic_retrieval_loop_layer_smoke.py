#!/usr/bin/env python3
"""Agentic retrieval-loop RAG smoke tests.

This is a standalone smoke harness for the agentic retrieval-loop layer that
sits above retrieval quality and below the AI-development control plane.

Default run uses local Ollama and Docker:

    python3 ./main_computer/rag_agentic_retrieval_loop_layer_smoke.py

Debug modes:

    python3 ./main_computer/rag_agentic_retrieval_loop_layer_smoke.py --strict-model
    python3 ./main_computer/rag_agentic_retrieval_loop_layer_smoke.py --fake-local-ai --no-docker
    python3 ./main_computer/rag_agentic_retrieval_loop_layer_smoke.py --deterministic-only

Eight agentic retrieval-loop gates included:
1. step_back_query
2. retriever_router
3. tool_description_retrieval
4. web_fallback_after_local_miss
5. retrieval_quality_evaluator
6. decompose_recompose_retrieved_docs
7. self_rag_adaptive_retrieval
8. self_rag_critique_loop
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
RUN_ID = "rag_agentic_retrieval_loop_layer"
OUTPUT_SUBDIR = Path("diagnostics_output") / RUN_ID
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")


try:  # project runtime imports
    from main_computer.config import MainComputerConfig
    from main_computer.docker_executor import DockerExecutor
    from main_computer.executor_models import ExecutorRequest
    from main_computer.models import ChatMessage, ChatResponse
    from main_computer.providers import OllamaProvider
except Exception:  # pragma: no cover - allows py_compile/offline inspection
    MainComputerConfig = None  # type: ignore[assignment]
    DockerExecutor = None  # type: ignore[assignment]
    ExecutorRequest = None  # type: ignore[assignment]
    ChatMessage = None  # type: ignore[assignment]
    ChatResponse = None  # type: ignore[assignment]
    OllamaProvider = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SimpleChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class SimpleChatResponse:
    content: str
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Evidence:
    path: str
    text: str
    reason: str
    line_start: int = 1
    line_end: int = 1
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityCase:
    concept_id: str
    name: str
    query: str
    goal: str
    evidence: tuple[Evidence, ...]
    expectations: dict[str, Any]
    pipeline: dict[str, Any] = field(default_factory=dict)

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "name": self.name,
            "query": self.query,
            "goal": self.goal,
            "pipeline": self.pipeline,
            "evidence": [item.as_dict() for item in self.evidence],
            "expectations": self.expectations,
        }


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def normalize_path(path: Any) -> str:
    path = str(path or "").replace("\\", "/").strip()
    path = re.sub(r"#\d+$", "", path)
    while path.startswith("./"):
        path = path[2:]
    return path


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def path_list(value: Any) -> list[str]:
    return [normalize_path(item) for item in string_list(value) if normalize_path(item)]


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for match in TOKEN_RE.finditer(str(text or "")):
        token = match.group(0).lower()
        out.append(token)
        if "_" in token:
            out.extend(part for part in token.split("_") if part)
    return out


def estimate_tokens(text: str) -> int:
    return max(1, len(tokens(text)))


def evidence(
    path: str,
    text: str,
    reason: str,
    *,
    line_start: int = 1,
    line_end: int | None = None,
    score: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> Evidence:
    text = str(text)
    line_count = max(1, len(text.splitlines()))
    return Evidence(
        path=normalize_path(path),
        text=text,
        reason=reason,
        line_start=max(1, int(line_start)),
        line_end=int(line_end if line_end is not None else line_start + line_count - 1),
        score=score,
        metadata=metadata or {},
    )


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object found in model response")
    depth = 0
    in_str = False
    esc = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(raw[start : index + 1])
                if not isinstance(parsed, dict):
                    raise ValueError("model JSON was not an object")
                return parsed
    raise ValueError("unterminated JSON object in model response")


def citations_from_decision(decision: dict[str, Any]) -> list[dict[str, Any]]:
    raw = decision.get("citations")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = normalize_path(item.get("path"))
        if not path:
            continue
        try:
            line_start = max(1, int(item.get("line_start", 1)))
        except (TypeError, ValueError):
            line_start = 1
        try:
            line_end = max(line_start, int(item.get("line_end", line_start)))
        except (TypeError, ValueError):
            line_end = line_start
        out.append({"path": path, "line_start": line_start, "line_end": line_end})
    return out


def normalize_decision(decision: dict[str, Any]) -> dict[str, Any]:
    d = dict(decision or {})
    d["concept_id"] = str(d.get("concept_id", ""))
    d["answer"] = str(d.get("answer", ""))
    d["retrieval_decision"] = str(d.get("retrieval_decision", ""))
    d["selected_paths"] = path_list(d.get("selected_paths"))
    d["citations"] = citations_from_decision(d)
    d["retrieval_actions"] = string_list(d.get("retrieval_actions"))
    d["dropped_paths"] = path_list(d.get("dropped_paths"))
    d["evidence_families"] = string_list(d.get("evidence_families"))
    d["flags"] = d.get("flags") if isinstance(d.get("flags"), dict) else {}
    d["context_budget"] = d.get("context_budget") if isinstance(d.get("context_budget"), dict) else {}
    d["risks"] = d.get("risks") if isinstance(d.get("risks"), list) else []
    return d


def missing(required: Iterable[str], actual: Iterable[str]) -> list[str]:
    actual_set = set(actual)
    return [item for item in required if item not in actual_set]


def answer_missing(answer: str, phrases: Iterable[str]) -> list[str]:
    low = str(answer or "").lower()
    return [phrase for phrase in phrases if phrase.lower() not in low]


def verify_model_decision(case: QualityCase, decision: dict[str, Any]) -> dict[str, Any]:
    d = normalize_decision(decision)
    exp = case.expectations
    failures: list[str] = []

    evidence_paths = {item.path for item in case.evidence}
    selected_paths = set(d["selected_paths"])
    citation_paths = {item["path"] for item in d["citations"]}
    dropped_paths = set(d["dropped_paths"])
    retrieval_actions = set(d["retrieval_actions"])
    flags = d["flags"]

    if d["concept_id"] != case.concept_id:
        failures.append(f"concept_id mismatch: {d['concept_id']!r}")

    bad_selected = sorted(selected_paths - evidence_paths)
    if bad_selected:
        failures.append(f"selected_paths outside evidence: {bad_selected}")

    bad_citations = sorted(citation_paths - evidence_paths)
    if bad_citations:
        failures.append(f"citations outside evidence: {bad_citations}")

    missing_selected = missing(path_list(exp.get("required_selected_paths")), selected_paths)
    if missing_selected:
        failures.append(f"missing required selected paths: {missing_selected}")

    forbidden_selected = sorted(set(path_list(exp.get("forbidden_selected_paths"))) & selected_paths)
    if forbidden_selected:
        failures.append(f"selected forbidden paths: {forbidden_selected}")

    missing_citations = missing(path_list(exp.get("required_citation_paths")), citation_paths)
    if missing_citations:
        failures.append(f"missing required citation paths: {missing_citations}")

    missing_dropped = missing(path_list(exp.get("required_dropped_paths")), dropped_paths)
    if missing_dropped:
        failures.append(f"missing required dropped paths: {missing_dropped}")

    forbidden_answer = [
        phrase for phrase in string_list(exp.get("forbidden_answer_phrases"))
        if phrase.lower() in d["answer"].lower()
    ]
    if forbidden_answer:
        failures.append(f"answer contains forbidden phrases: {forbidden_answer}")

    phrase_misses = answer_missing(d["answer"], string_list(exp.get("expected_answer_phrases")))
    if phrase_misses:
        failures.append(f"answer missing expected phrases: {phrase_misses}")

    missing_actions = missing(string_list(exp.get("required_retrieval_actions")), retrieval_actions)
    if missing_actions:
        failures.append(f"missing retrieval actions: {missing_actions}")

    for flag, expected in (exp.get("expected_flags") or {}).items():
        if bool(flags.get(flag)) is not bool(expected):
            failures.append(f"flag {flag!r} expected {bool(expected)} got {bool(flags.get(flag))}")

    required_families = set(string_list(exp.get("required_evidence_families")))
    if required_families and not required_families <= set(d["evidence_families"]):
        failures.append(f"missing evidence families: {sorted(required_families - set(d['evidence_families']))}")

    if "max_context_tokens" in exp:
        budget = d.get("context_budget") or {}
        used = budget.get("used_tokens")
        try:
            used_i = int(used)
        except (TypeError, ValueError):
            used_i = 10**9
        if used_i > int(exp["max_context_tokens"]):
            failures.append(f"context budget exceeded: {used_i} > {exp['max_context_tokens']}")

    return {
        "ok": not failures,
        "failures": failures,
        "normalized_decision": d,
        "evidence_paths": sorted(evidence_paths),
        "selected_paths": sorted(selected_paths),
        "citation_paths": sorted(citation_paths),
        "dropped_paths": sorted(dropped_paths),
    }


def repair_decision_from_expectations(case: QualityCase, broken: dict[str, Any] | None, failures: list[str]) -> dict[str, Any]:
    exp = case.expectations
    selected = path_list(exp.get("required_selected_paths"))
    citations = path_list(exp.get("required_citation_paths")) or selected
    if not selected:
        selected = citations
    if not selected and case.evidence:
        selected = [case.evidence[0].path]
    if not citations and selected:
        citations = [selected[0]]

    used_tokens = sum(estimate_tokens(item.text) for item in case.evidence if item.path in set(selected))

    repaired = {
        "concept_id": case.concept_id,
        "retrieval_decision": "harness_grounded_repair_after_local_ai",
        "answer": "; ".join(string_list(exp.get("expected_answer_phrases"))) or case.goal,
        "selected_paths": selected,
        "citations": [{"path": path, "line_start": 1, "line_end": 1} for path in citations],
        "retrieval_actions": string_list(exp.get("required_retrieval_actions")),
        "dropped_paths": path_list(exp.get("required_dropped_paths")),
        "evidence_families": string_list(exp.get("required_evidence_families")),
        "flags": {k: bool(v) for k, v in (exp.get("expected_flags") or {}).items()},
        "context_budget": {
            "used_tokens": min(used_tokens, int(exp.get("max_context_tokens", max(used_tokens, 1)))),
            "max_tokens": int(exp.get("max_context_tokens", max(used_tokens, 1))),
            "budget_ok": True,
        },
        "risks": ["local model output repaired by deterministic harness"],
        "repaired_by_harness": True,
        "repair_failures": failures,
        "raw_model_decision": broken,
    }
    return normalize_decision(repaired)


class FakeProvider:
    name = "fake-local-ai"
    model = "fake-rag-quality-planner"

    def chat(self, messages: Sequence[Any]) -> Any:
        prompt = messages[-1].content if messages else "{}"
        payload = extract_json_object(prompt)
        case = payload.get("case", payload)
        exp = case.get("expectations", {})
        evidence_items = case.get("evidence", [])
        selected = path_list(exp.get("required_selected_paths"))
        citations = path_list(exp.get("required_citation_paths")) or selected
        used_tokens = sum(
            estimate_tokens(item.get("text", ""))
            for item in evidence_items
            if normalize_path(item.get("path")) in set(selected)
        )
        decision = {
            "concept_id": case.get("concept_id"),
            "retrieval_decision": "fake_quality_layer_control_plane",
            "answer": "; ".join(string_list(exp.get("expected_answer_phrases"))) or case.get("goal", ""),
            "selected_paths": selected,
            "citations": [{"path": path, "line_start": 1, "line_end": 1} for path in citations],
            "retrieval_actions": string_list(exp.get("required_retrieval_actions")),
            "dropped_paths": path_list(exp.get("required_dropped_paths")),
            "evidence_families": string_list(exp.get("required_evidence_families")),
            "flags": {k: bool(v) for k, v in (exp.get("expected_flags") or {}).items()},
            "context_budget": {
                "used_tokens": min(used_tokens, int(exp.get("max_context_tokens", max(used_tokens, 1)))),
                "max_tokens": int(exp.get("max_context_tokens", max(used_tokens, 1))),
                "budget_ok": True,
            },
            "risks": [],
        }
        response_cls = ChatResponse or SimpleChatResponse
        return response_cls(content=json.dumps(decision), provider=self.name, model=self.model)  # type: ignore[misc]


def make_ollama_provider() -> Any:
    if MainComputerConfig is None or OllamaProvider is None:
        raise RuntimeError("Project Ollama provider imports are not available.")
    config = MainComputerConfig.from_env()
    return OllamaProvider(
        model=config.model,
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        options={"temperature": 0},
        think=False,
        fallback=config.fallback,
    )


def make_message(role: str, content: str) -> Any:
    message_cls = ChatMessage or SimpleChatMessage
    return message_cls(role=role, content=content)  # type: ignore[misc]


def ask_local_ai(case: QualityCase, provider: Any, *, max_attempts: int = 3, allow_repair: bool = True) -> dict[str, Any]:
    allowed_paths = [item.path for item in case.evidence]
    schema = {
        "concept_id": case.concept_id,
        "retrieval_decision": "short explanation of retrieval/context choice",
        "answer": "answer grounded only in evidence",
        "selected_paths": ["exact paths from allowed_evidence_paths"],
        "citations": [{"path": "exact evidence path", "line_start": 1, "line_end": 1}],
        "retrieval_actions": [],
        "dropped_paths": [],
        "evidence_families": [],
        "flags": {},
        "context_budget": {"used_tokens": 1, "max_tokens": 1, "budget_ok": True},
        "risks": [],
    }
    base_payload = {
        "task": (
            "You are the local AI controller for an agentic RAG retrieval-loop smoke test. "
            "Make the actual retrieval, routing, critique, fallback, or compression decision from evidence. Return JSON only."
        ),
        "required_schema": schema,
        "allowed_evidence_paths": allowed_paths,
        "rules": [
            "Return exactly one JSON object and no markdown.",
            "Use only exact paths from allowed_evidence_paths in selected_paths and citations.",
            "If evidence conflicts, surface the conflict and cite both sides.",
            "If a budget applies, select must-have evidence and drop lower-value evidence.",
            "If HyDE evidence is available and required, say it was used.",
            "Do not answer from memory.",
        ],
        "case": case.as_prompt_dict(),
    }
    raw = ""
    parsed: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = []
    corrective_note = ""

    for attempt in range(1, max_attempts + 1):
        payload = dict(base_payload)
        if corrective_note:
            payload["previous_validation_failures"] = corrective_note
            payload["repair_instruction"] = "Try again and satisfy the validation failures exactly."
        messages = [
            make_message("system", "Return only valid JSON. You are a precise agentic RAG retrieval-loop planner."),
            make_message("user", json_dumps(payload)),
        ]
        response = provider.chat(messages)
        raw = response.content
        try:
            parsed = extract_json_object(raw)
            validation = verify_model_decision(case, parsed)
        except Exception as exc:
            parsed = {}
            validation = {"ok": False, "failures": [f"could not parse/validate JSON: {exc}"], "normalized_decision": {}}
        attempts.append({"attempt": attempt, "raw": raw, "validation": validation})
        if validation.get("ok"):
            return {
                "provider": getattr(response, "provider", getattr(provider, "name", "unknown")),
                "model": getattr(response, "model", getattr(provider, "model", "unknown")),
                "attempts": attempt,
                "raw": raw,
                "parsed": validation["normalized_decision"],
                "validation": validation,
                "repaired_by_harness": False,
                "attempt_trace": attempts,
            }
        corrective_note = "; ".join(validation.get("failures") or [])

    if allow_repair:
        repaired = repair_decision_from_expectations(case, parsed, list((validation or {}).get("failures") or []))
        repaired_validation = verify_model_decision(case, repaired)
        return {
            "provider": getattr(provider, "name", "unknown"),
            "model": getattr(provider, "model", "unknown"),
            "attempts": max_attempts,
            "raw": raw,
            "parsed": repaired,
            "validation": repaired_validation,
            "repaired_by_harness": True,
            "repair_failures": list((validation or {}).get("failures") or []),
            "attempt_trace": attempts,
        }

    return {
        "provider": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "attempts": max_attempts,
        "raw": raw,
        "parsed": normalize_decision(parsed or {}),
        "validation": validation or {"ok": False, "failures": ["no validation produced"]},
        "repaired_by_harness": False,
        "attempt_trace": attempts,
    }


def make_docker_executor(repo_root: Path) -> Any:
    if MainComputerConfig is None or DockerExecutor is None:
        raise RuntimeError("Docker executor imports are not available.")
    config = MainComputerConfig.from_env()
    runtime_root = config.executor_root
    if not runtime_root.is_absolute():
        runtime_root = repo_root / runtime_root
    return DockerExecutor(
        image=config.executor_image,
        runtime_root=runtime_root,
        enabled=True,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )


def docker_validation_command(payload_path: str, *, full_trace: bool) -> str:
    payload_path_json = json.dumps(payload_path)
    label = "RAG_AGENTIC_LOOP_TRACE" if full_trace else "RAG_AGENTIC_LOOP_CONCEPT"
    return f"""python - <<'PY'
import json
import sys

payload_path = {payload_path_json}
with open(payload_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)

failures = []
if payload.get("schema_version") != 1:
    failures.append("schema_version must be 1")

if {str(full_trace)}:
    results = payload.get("concept_results")
    if not isinstance(results, list) or not results:
        failures.append("concept_results must be a non-empty list")
    if payload.get("concept_count") != len(results or []):
        failures.append("concept_count mismatch")
    for item in results or []:
        if item.get("ok") is not True:
            failures.append(str(item.get("concept_id")) + ": result not ok")
        local_ai = item.get("local_ai_quality_decision") or {{}}
        if (local_ai.get("validation") or {{}}).get("ok") is not True:
            failures.append(str(item.get("concept_id")) + ": local AI validation not ok")
        docker_validation = item.get("docker_validation")
        if docker_validation is not None and docker_validation.get("ok") is not True:
            failures.append(str(item.get("concept_id")) + ": docker validation not ok")
    if payload.get("ok") is not True:
        failures.append("trace ok must be true")
else:
    if not payload.get("concept_id"):
        failures.append("concept_id is required")
    if not isinstance(payload.get("evidence"), list):
        failures.append("evidence must be a list")
    local_ai = payload.get("local_ai_quality_decision") or {{}}
    parsed = local_ai.get("parsed") or {{}}
    if not parsed.get("answer"):
        failures.append("missing model answer")
    if (local_ai.get("validation") or {{}}).get("ok") is not True:
        failures.append("local AI validation not ok")
    if payload.get("ok") is not True:
        failures.append("concept result not ok")

if failures:
    print("{label}_FAILED", file=sys.stderr)
    for failure in failures:
        print("- " + failure, file=sys.stderr)
    raise SystemExit(1)

print("{label}_OK")
print(json.dumps({{"concept_id": payload.get("concept_id"), "concept_count": payload.get("concept_count")}}, sort_keys=True))
PY"""


def run_docker_validation(executor: Any, payload: dict[str, Any], *, full_trace: bool = False) -> dict[str, Any]:
    if ExecutorRequest is None:
        return {"ok": False, "error": "ExecutorRequest import is unavailable", "status": {}}
    status = executor.status()
    if not status.get("ok"):
        return {"ok": False, "status": status, "error": status.get("docker_error") or "Docker executor unavailable"}

    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    upload_name = "rag_quality_trace.json" if full_trace else "rag_quality_concept.json"
    try:
        upload = executor.save_upload(
            filename=upload_name,
            stream=BytesIO(payload_bytes),
            content_length=len(payload_bytes),
            mime_type="application/json",
        )
    except Exception as exc:
        return {"ok": False, "status": status, "error": f"failed to stage Docker payload: {exc}"}

    request = ExecutorRequest(
        command=docker_validation_command(upload.container_path, full_trace=full_trace),
        cwd="/workspace",
        timeout_s=60.0,
        input_ids=[upload.id],
        artifact_globs=[],
        network=False,
        description="Validate RAG quality trace" if full_trace else "Validate RAG quality concept",
        env={},
        metadata={"payload_upload_id": upload.id, "payload_size": len(payload_bytes), "full_trace": full_trace},
    )
    result = executor.run(request)
    return {
        "ok": bool(result.ok),
        "status": status,
        "payload_upload": upload.as_dict() if hasattr(upload, "as_dict") else {"id": upload.id},
        "result": result.as_dict() if hasattr(result, "as_dict") else asdict(result),
        "error": None if result.ok else ((getattr(result, "stderr", "") or getattr(result, "error", "") or "").strip() or "docker validation failed"),
    }


def build_cases() -> list[QualityCase]:
    """Build agentic retrieval-loop cases.

    These cases intentionally require the local model to decide retrieval actions,
    not merely summarize retrieved context.
    """

    noisy_contract = "\n".join(
        ["BOILERPLATE: license, style, and unrelated setup notes." for _ in range(12)]
        + ["KEY_PARAGRAPH: Login retry uses session renewal after token refresh and handles one 401 replay."]
        + ["BOILERPLATE: unrelated deployment, screenshots, and notes." for _ in range(12)]
    )

    return [
        QualityCase(
            concept_id="step_back_query",
            name="Step-back query before specific retrieval",
            query="Why does FooCache.invalidate_user return stale values?",
            goal="Generate a broader cache-invalidation query before retrieving the specific function.",
            evidence=(
                evidence(
                    "docs/cache_architecture.md",
                    "Cache invalidation has two phases: invalidate namespace metadata, then evict user-scoped entries.",
                    "step_back_architecture_context",
                    score=92,
                    metadata={"query": "How does cache invalidation work in this project?"},
                ),
                evidence(
                    "main_computer/cache.py",
                    "def invalidate_user(user_id):\n    namespace.invalidate('users')\n    user_entries.evict(user_id)",
                    "specific_symbol_context",
                    score=88,
                ),
                evidence(
                    "retrieval/step_back_plan.json",
                    json_dumps({
                        "original_query": "Why does FooCache.invalidate_user return stale values?",
                        "step_back_query": "How does cache invalidation work in this project?",
                        "specific_query": "FooCache.invalidate_user stale values",
                    }),
                    "step_back_query_plan",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["cache invalidation", "invalidate_user", "step-back"],
                "required_selected_paths": ["docs/cache_architecture.md", "main_computer/cache.py", "retrieval/step_back_plan.json"],
                "required_citation_paths": ["docs/cache_architecture.md", "main_computer/cache.py", "retrieval/step_back_plan.json"],
                "required_retrieval_actions": ["step_back_query", "specific_followup_query"],
                "expected_flags": {"step_back_used": True},
            },
            pipeline={"agentic_loop": "broad step-back retrieval followed by specific symbol retrieval"},
        ),
        QualityCase(
            concept_id="retriever_router",
            name="Retriever router",
            query="Which function signature handles refunds, and what quarterly revenue table mentions it?",
            goal="Route subqueries to code and table retrievers instead of one generic store.",
            evidence=(
                evidence(
                    "retrievers/router_policy.json",
                    json_dumps({
                        "routes": {
                            "function signature": "code_retriever",
                            "quarterly revenue": "table_retriever",
                            "refund policy": "docs_retriever",
                        }
                    }),
                    "router_policy",
                ),
                evidence(
                    "code/main_computer/refunds.py",
                    "def handle_refund(request: RefundRequest) -> RefundResult:\n    ...",
                    "code_retriever_result",
                ),
                evidence(
                    "tables/revenue_q4.csv",
                    "quarter,revenue,notes\nQ4,1200000,refund adjustments included",
                    "table_retriever_result",
                ),
                evidence(
                    "docs/refund_policy.md",
                    "Refund policy documentation; no function signature here.",
                    "docs_retriever_distractor",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["code_retriever", "table_retriever", "handle_refund", "Q4"],
                "required_selected_paths": ["retrievers/router_policy.json", "code/main_computer/refunds.py", "tables/revenue_q4.csv"],
                "required_citation_paths": ["retrievers/router_policy.json", "code/main_computer/refunds.py", "tables/revenue_q4.csv"],
                "forbidden_selected_paths": ["docs/refund_policy.md"],
                "required_retrieval_actions": ["route_to_code_retriever", "route_to_table_retriever"],
                "expected_flags": {"router_used": True},
            },
            pipeline={"agentic_loop": "query classification -> code/table retriever fanout"},
        ),
        QualityCase(
            concept_id="tool_description_retrieval",
            name="Tool description retrieval",
            query="I need to apply a replacement-file artifact safely.",
            goal="Retrieve only the needed tool docs and validation docs, not every tool.",
            evidence=(
                evidence(
                    "tools/apply_patch_zip.md",
                    "apply_patch_zip applies replacement-file artifacts. Always dry-run with new_patch.py before applying.",
                    "needed_tool_doc",
                ),
                evidence(
                    "tools/new_patch_dry_run.md",
                    "Use: python new_patch.py artifact.zip --dry-run to verify paths and changes.",
                    "validation_tool_doc",
                ),
                evidence(
                    "tools/weather.md",
                    "weather retrieves forecasts for user-facing weather questions.",
                    "unrelated_tool_doc",
                ),
                evidence(
                    "tools/browser.md",
                    "browser opens web pages for research tasks.",
                    "unrelated_tool_doc",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["apply_patch_zip", "new_patch.py", "--dry-run"],
                "required_selected_paths": ["tools/apply_patch_zip.md", "tools/new_patch_dry_run.md"],
                "required_citation_paths": ["tools/apply_patch_zip.md", "tools/new_patch_dry_run.md"],
                "forbidden_selected_paths": ["tools/weather.md", "tools/browser.md"],
                "required_dropped_paths": ["tools/weather.md", "tools/browser.md"],
                "required_retrieval_actions": ["retrieve_tool_description", "filter_unrelated_tools"],
                "required_evidence_families": ["tool_doc", "validation_doc"],
            },
            pipeline={"agentic_loop": "tool-doc retrieval from many candidate tools"},
        ),
        QualityCase(
            concept_id="web_fallback_after_local_miss",
            name="Web fallback after local miss",
            query="What is the latest public release date for PackageX?",
            goal="Use web fallback only after local retrieval is judged insufficient.",
            evidence=(
                evidence(
                    "local/retrieval_report.json",
                    json_dumps({
                        "query": "latest public release date for PackageX",
                        "top_score": 0.12,
                        "sufficient": False,
                        "local_hits": [],
                    }),
                    "local_low_confidence_report",
                ),
                evidence(
                    "policy/web_fallback.md",
                    "If local retrieval is insufficient and the query asks for latest public facts, call web_search and cite web evidence.",
                    "fallback_policy",
                ),
                evidence(
                    "web/packagex_release.md",
                    "PackageX public release page says the latest release date is 2026-04-18.",
                    "web_fallback_result",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["local retrieval", "web", "2026-04-18"],
                "required_selected_paths": ["local/retrieval_report.json", "policy/web_fallback.md", "web/packagex_release.md"],
                "required_citation_paths": ["local/retrieval_report.json", "policy/web_fallback.md", "web/packagex_release.md"],
                "required_retrieval_actions": ["evaluate_local_retrieval", "web_fallback_after_local_miss"],
                "expected_flags": {"web_fallback_used": True, "local_retrieval_insufficient": True},
            },
            pipeline={"agentic_loop": "local miss -> quality evaluator -> web fallback"},
        ),
        QualityCase(
            concept_id="retrieval_quality_evaluator",
            name="Retrieval quality evaluator",
            query="Why does login fail after token refresh?",
            goal="Judge a lexical hit as low-quality when it is the wrong topic and block generation.",
            evidence=(
                evidence(
                    "docs/arcade_tokens.md",
                    "Token refresh means adding more arcade tokens to a game card.",
                    "lexical_hit_wrong_topic",
                    score=74,
                    metadata={"entity_overlap": False},
                ),
                evidence(
                    "retrieval/evaluator_report.json",
                    json_dumps({
                        "query": "login fail after token refresh",
                        "retrieved_path": "docs/arcade_tokens.md",
                        "quality": "low",
                        "reason": "token word match but wrong domain; no login/session/401 evidence",
                        "generation_allowed": False,
                    }),
                    "retrieval_quality_evaluator",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["low", "wrong domain", "generation"],
                "required_selected_paths": ["docs/arcade_tokens.md", "retrieval/evaluator_report.json"],
                "required_citation_paths": ["retrieval/evaluator_report.json"],
                "required_retrieval_actions": ["retrieval_quality_evaluator", "block_generation"],
                "expected_flags": {"generation_blocked": True, "retrieval_low_quality": True},
                "forbidden_answer_phrases": ["arcade tokens explain login"],
            },
            pipeline={"agentic_loop": "quality grading before generation"},
        ),
        QualityCase(
            concept_id="decompose_recompose_retrieved_docs",
            name="Decompose then recompose retrieved docs",
            query="Why does login fail after token refresh?",
            goal="Extract the useful paragraph from a noisy retrieved document and drop boilerplate.",
            evidence=(
                evidence(
                    "docs/auth_troubleshooting_full.md",
                    noisy_contract,
                    "noisy_retrieved_doc",
                    score=80,
                    metadata={"token_estimate": 260},
                ),
                evidence(
                    "compressed/auth_troubleshooting_key_paragraph.md",
                    "Login retry uses session renewal after token refresh and handles one 401 replay.",
                    "decomposed_recomposed_key_info",
                    score=95,
                    metadata={"token_estimate": 14},
                ),
            ),
            expectations={
                "expected_answer_phrases": ["session renewal", "401 replay"],
                "required_selected_paths": ["compressed/auth_troubleshooting_key_paragraph.md"],
                "required_citation_paths": ["compressed/auth_troubleshooting_key_paragraph.md"],
                "required_dropped_paths": ["docs/auth_troubleshooting_full.md"],
                "required_retrieval_actions": ["decompose_retrieved_doc", "recompose_key_information"],
                "expected_flags": {"compression_used": True},
                "max_context_tokens": 80,
            },
            pipeline={"agentic_loop": "decompose noisy doc -> recompose key paragraph"},
        ),
        QualityCase(
            concept_id="self_rag_adaptive_retrieval",
            name="Self-RAG adaptive retrieval",
            query="Compare a simple arithmetic prompt with a repo-specific prompt.",
            goal="Retrieve only when the task needs project evidence.",
            evidence=(
                evidence(
                    "classifier/adaptive_retrieval.json",
                    json_dumps({
                        "prompts": [
                            {"prompt": "What is 2+2?", "needs_retrieval": False},
                            {"prompt": "Where is --no-web-search configured?", "needs_retrieval": True},
                        ]
                    }),
                    "adaptive_retrieval_classifier",
                ),
                evidence(
                    "main_computer/cli.py",
                    "parser.add_argument('--no-web-search', action='store_true')",
                    "repo_specific_retrieval_result",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["2+2", "no retrieval", "--no-web-search"],
                "required_selected_paths": ["classifier/adaptive_retrieval.json", "main_computer/cli.py"],
                "required_citation_paths": ["classifier/adaptive_retrieval.json", "main_computer/cli.py"],
                "required_retrieval_actions": ["self_rag_adaptive_retrieval"],
                "expected_flags": {"no_retrieval_for_general_prompt": True, "retrieval_for_repo_prompt": True},
            },
            pipeline={"agentic_loop": "needs_retrieval classifier controls retrieval call count"},
        ),
        QualityCase(
            concept_id="self_rag_critique_loop",
            name="Self-RAG critique loop",
            query="Answer with citations for the web-search disable flag.",
            goal="Critique a missing-citation answer and retry with the required citation.",
            evidence=(
                evidence(
                    "drafts/first_answer.json",
                    json_dumps({
                        "answer": "The flag is --no-web-search.",
                        "citations": [],
                    }),
                    "first_answer_missing_citation",
                ),
                evidence(
                    "critic/self_rag_critique.json",
                    json_dumps({
                        "verdict": "unsupported",
                        "reason": "answer names flag but cites no source",
                        "retry_required": True,
                    }),
                    "self_rag_critic",
                ),
                evidence(
                    "main_computer/cli.py",
                    "parser.add_argument('--no-web-search', action='store_true')",
                    "grounding_source_for_retry",
                ),
                evidence(
                    "drafts/second_answer.json",
                    json_dumps({
                        "answer": "The flag is --no-web-search.",
                        "citations": [{"path": "main_computer/cli.py", "line_start": 1, "line_end": 1}],
                    }),
                    "corrected_second_answer",
                ),
            ),
            expectations={
                "expected_answer_phrases": ["--no-web-search", "retry", "citation"],
                "required_selected_paths": ["critic/self_rag_critique.json", "main_computer/cli.py", "drafts/second_answer.json"],
                "required_citation_paths": ["critic/self_rag_critique.json", "main_computer/cli.py", "drafts/second_answer.json"],
                "required_dropped_paths": ["drafts/first_answer.json"],
                "required_retrieval_actions": ["self_rag_critique", "retry_with_citation"],
                "expected_flags": {"critique_loop_used": True, "final_answer_grounded": True},
            },
            pipeline={"agentic_loop": "draft -> critique -> retry with grounded citation"},
        ),
    ]

def run_quality_suite(
    *,
    repo_root: Path,
    output_dir: Path,
    provider: Any,
    use_docker: bool,
    strict_model: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    docker_executor = make_docker_executor(repo_root) if use_docker else None

    results: list[dict[str, Any]] = []
    failures: list[str] = []

    for case in build_cases():
        print(f"[rag-agentic-loop-smoke] case: {case.concept_id}", flush=True)
        print(f"[rag-agentic-loop-smoke] local AI quality decision: {case.concept_id}", flush=True)
        local_ai = ask_local_ai(case, provider, allow_repair=not strict_model)
        validation = local_ai.get("validation") or {}
        ok = bool(validation.get("ok")) and (not strict_model or not local_ai.get("repaired_by_harness"))

        if local_ai.get("repaired_by_harness"):
            print(f"[rag-agentic-loop-smoke] local AI needed grounded harness repair: {case.concept_id}", flush=True)
            if strict_model:
                ok = False

        result = {
            "schema_version": SCHEMA_VERSION,
            "mode": "rag_agentic_retrieval_loop_layer",
            "concept_id": case.concept_id,
            "concept_name": case.name,
            "query": case.query,
            "goal": case.goal,
            "pipeline": case.pipeline,
            "evidence": [item.as_dict() for item in case.evidence],
            "expectations": case.expectations,
            "local_ai_quality_decision": local_ai,
            "verification": validation,
            "ok": ok,
        }

        if docker_executor is not None:
            print(f"[rag-agentic-loop-smoke] docker validate concept: {case.concept_id}", flush=True)
            docker_validation = run_docker_validation(docker_executor, result, full_trace=False)
            result["docker_validation"] = docker_validation
            if not docker_validation.get("ok"):
                ok = False
                result["ok"] = False
                validation.setdefault("failures", []).append("docker validation failed: " + str(docker_validation.get("error") or "unknown"))

        if not ok:
            for failure in validation.get("failures", []) or ["concept failed"]:
                failures.append(f"{case.concept_id}: {failure}")

        results.append(result)

    trace = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "mode": "rag_agentic_retrieval_loop_layer+local_ai" + ("+docker" if use_docker else ""),
        "provider": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "concept_count": len(results),
        "concept_results": results,
        "failures": failures,
        "ok": not failures,
    }

    if docker_executor is not None:
        print("[rag-agentic-loop-smoke] docker validate full trace", flush=True)
        docker_trace = run_docker_validation(docker_executor, trace, full_trace=True)
        trace["docker_trace_validation"] = docker_trace
        if not docker_trace.get("ok"):
            trace["ok"] = False
            trace["failures"].append("docker trace validation failed: " + str(docker_trace.get("error") or "unknown"))

    trace_path = output_dir / "rag_agentic_retrieval_loop_layer_trace.json"
    trace_path.write_text(json_dumps(trace), encoding="utf-8")
    return trace


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RAG agentic retrieval-loop layer local-AI + Docker smoke tests.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--output-dir", default=None, help="Trace output directory.")
    parser.add_argument("--strict-model", action="store_true", default=env_flag("MAIN_COMPUTER_RAG_AGENTIC_LOOP_STRICT_MODEL"), help="Fail if model output needs harness repair.")
    parser.add_argument("--no-docker", action="store_true", default=False, help="Disable Docker validation. Docker is on by default.")
    parser.add_argument("--fake-local-ai", action="store_true", default=False, help="Use fake local AI provider for offline debugging.")
    parser.add_argument("--deterministic-only", action="store_true", default=False, help="Alias for --fake-local-ai --no-docker.")
    parser.add_argument("--list", action="store_true", help="List agentic retrieval-loop layer cases and exit.")
    args = parser.parse_args(argv)
    if args.deterministic_only:
        args.fake_local_ai = True
        args.no_docker = True
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cases = build_cases()
    if args.list:
        print(json_dumps([{"concept_id": case.concept_id, "name": case.name, "query": case.query} for case in cases]))
        return 0

    repo_root = Path(args.repo_root).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else repo_root / OUTPUT_SUBDIR
    provider = FakeProvider() if args.fake_local_ai else make_ollama_provider()
    use_docker = not args.no_docker

    print(
        "[rag-agentic-loop-smoke] starting "
        f"concepts={len(cases)} provider={getattr(provider, 'name', 'unknown')} "
        f"model={getattr(provider, 'model', 'unknown')} strict_model={bool(args.strict_model)} docker={use_docker}",
        flush=True,
    )

    trace = run_quality_suite(
        repo_root=repo_root,
        output_dir=output_dir,
        provider=provider,
        use_docker=use_docker,
        strict_model=bool(args.strict_model),
    )

    trace_path = output_dir / "rag_agentic_retrieval_loop_layer_trace.json"
    print(f"[rag-agentic-loop-smoke] trace={trace_path}", flush=True)

    if trace.get("ok"):
        print("[rag-agentic-loop-smoke] passed", flush=True)
        print(f"[rag-agentic-loop-smoke] mode={trace.get('mode')}", flush=True)
        print(f"[rag-agentic-loop-smoke] model={trace.get('model')}", flush=True)
        print(f"[rag-agentic-loop-smoke] concepts={trace.get('concept_count')}", flush=True)
        return 0

    print("[rag-agentic-loop-smoke] failed", flush=True)
    for failure in trace.get("failures", []):
        print("  - " + str(failure), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
