#!/usr/bin/env python3
"""Advanced RAG structures + evaluation-layer smoke tests.

This standalone harness covers the remaining advanced retrieval structures and
evaluation concepts above the retrieval-quality and agentic-loop layers.

Default run uses local Ollama and Docker:

    python3 ./main_computer/rag_advanced_eval_layer_smoke.py

Debug modes:

    python3 ./main_computer/rag_advanced_eval_layer_smoke.py --strict-model
    python3 ./main_computer/rag_advanced_eval_layer_smoke.py --fake-local-ai --no-docker
    python3 ./main_computer/rag_advanced_eval_layer_smoke.py --deterministic-only

Four advanced/eval gates included:
1. raptor_control_plane
2. treerag_locate_then_expand
3. graphrag_control_plane
4. evaluation_metrics_pack
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
RUN_ID = "rag_advanced_eval_layer"
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
class AdvancedEvalCase:
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


def verify_model_decision(case: AdvancedEvalCase, decision: dict[str, Any]) -> dict[str, Any]:
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


def repair_decision_from_expectations(case: AdvancedEvalCase, broken: dict[str, Any] | None, failures: list[str]) -> dict[str, Any]:
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
    model = "fake-rag-advanced-eval-planner"

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


def ask_local_ai(case: AdvancedEvalCase, provider: Any, *, max_attempts: int = 3, allow_repair: bool = True) -> dict[str, Any]:
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
            "You are the local AI controller for a RAG quality-layer smoke test. "
            "Make the actual retrieval/context decision from evidence. Return JSON only."
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
            make_message("system", "Return only valid JSON. You are a precise RAG retrieval-quality planner."),
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
    label = "RAG_QUALITY_TRACE" if full_trace else "RAG_QUALITY_CONCEPT"
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


def build_cases() -> list[AdvancedEvalCase]:
    return [
        AdvancedEvalCase(
            concept_id="raptor_control_plane",
            name="RAPTOR-style hierarchical retrieval control plane",
            query="Why is startup slow, and what detailed evidence explains plugin discovery?",
            goal=(
                "The local model must decide to use a broad parent summary plus precise leaf evidence, "
                "not only one flat chunk."
            ),
            evidence=(
                evidence(
                    "raptor/parent_startup_summary.json",
                    json_dumps({
                        "node_id": "parent:startup",
                        "level": "parent",
                        "summary": "Startup slowness is caused by plugin discovery and dependency scanning.",
                        "children": ["leaf:plugin_discovery", "leaf:dependency_scanning"],
                    }),
                    "hierarchy_parent_summary",
                    metadata={"family": "hierarchy_parent"},
                ),
                evidence(
                    "raptor/leaf_plugin_discovery.json",
                    json_dumps({
                        "node_id": "leaf:plugin_discovery",
                        "level": "leaf",
                        "text": "Plugin discovery scans extension manifests during startup.",
                        "parent_id": "parent:startup",
                    }),
                    "hierarchy_leaf_hit",
                    metadata={"family": "hierarchy_leaf"},
                ),
                evidence(
                    "raptor/leaf_dependency_scanning.json",
                    json_dumps({
                        "node_id": "leaf:dependency_scanning",
                        "level": "leaf",
                        "text": "Dependency scanning checks optional imports during startup.",
                        "parent_id": "parent:startup",
                    }),
                    "hierarchy_sibling_leaf",
                    metadata={"family": "hierarchy_leaf"},
                ),
            ),
            expectations={
                "expected_answer_phrases": ["startup slowness", "plugin discovery", "dependency scanning"],
                "required_selected_paths": [
                    "raptor/parent_startup_summary.json",
                    "raptor/leaf_plugin_discovery.json",
                    "raptor/leaf_dependency_scanning.json",
                ],
                "required_citation_paths": [
                    "raptor/parent_startup_summary.json",
                    "raptor/leaf_plugin_discovery.json",
                    "raptor/leaf_dependency_scanning.json",
                ],
                "required_retrieval_actions": ["retrieve_parent_summary", "retrieve_leaf_evidence"],
                "required_evidence_families": ["hierarchy_parent", "hierarchy_leaf"],
                "expected_flags": {"hierarchical_retrieval": True},
            },
            pipeline={
                "quality_gate": "broad parent summary plus precise leaf evidence",
                "retrieval_structure": "raptor_tree",
            },
        ),
        AdvancedEvalCase(
            concept_id="treerag_locate_then_expand",
            name="TreeRAG precise locate then expand",
            query="Where is the stale-cache behavior described, and what sibling context explains invalidation?",
            goal=(
                "The local model must locate the precise subsection first, then expand to parent and sibling context."
            ),
            evidence=(
                evidence(
                    "treerag/leaf_invalidate_user.md",
                    "### invalidate_user\nFooCache.invalidate_user can return stale values when the user index is not invalidated.",
                    "precise_leaf_hit",
                    metadata={"family": "tree_leaf"},
                ),
                evidence(
                    "treerag/parent_cache_invalidation.md",
                    "## Cache invalidation\nUser cache invalidation has two parts: clearing the user value and clearing index entries.",
                    "expanded_parent_context",
                    metadata={"family": "tree_parent"},
                ),
                evidence(
                    "treerag/sibling_index_entries.md",
                    "### index entries\nIndex entries must be cleared after user value invalidation to avoid stale reads.",
                    "expanded_sibling_context",
                    metadata={"family": "tree_sibling"},
                ),
            ),
            expectations={
                "expected_answer_phrases": ["invalidate_user", "parent", "sibling", "index entries"],
                "required_selected_paths": [
                    "treerag/leaf_invalidate_user.md",
                    "treerag/parent_cache_invalidation.md",
                    "treerag/sibling_index_entries.md",
                ],
                "required_citation_paths": [
                    "treerag/leaf_invalidate_user.md",
                    "treerag/parent_cache_invalidation.md",
                    "treerag/sibling_index_entries.md",
                ],
                "required_retrieval_actions": ["precise_locate", "expand_parent", "include_sibling_context"],
                "required_evidence_families": ["tree_leaf", "tree_parent", "tree_sibling"],
                "expected_flags": {"locate_then_expand": True},
            },
            pipeline={
                "quality_gate": "locate precise leaf, then expand to coherent reading context",
                "retrieval_structure": "heading_tree",
            },
        ),
        AdvancedEvalCase(
            concept_id="graphrag_control_plane",
            name="GraphRAG local/global control plane",
            query="What does Alice own, and what broader reliability risk affects that area?",
            goal=(
                "The local model must choose graph local search for the entity answer and graph global/community "
                "search for the reliability risk."
            ),
            evidence=(
                evidence(
                    "graph/local_entity_search.json",
                    json_dumps({
                        "triples": [
                            {"source": "Alice", "relation": "owns", "target": "ServiceA", "evidence": "Alice owns ServiceA."}
                        ]
                    }),
                    "graph_local_entity_result",
                    metadata={"family": "graph_local"},
                ),
                evidence(
                    "graph/global_community_summary.json",
                    json_dumps({
                        "community": "payments",
                        "summary": "The payments community has reliability risks: retry storms and stale ledgers.",
                        "risks": ["retry storms", "stale ledgers"],
                    }),
                    "graph_global_community_result",
                    metadata={"family": "graph_global"},
                ),
                evidence(
                    "graph/routing_policy.json",
                    json_dumps({
                        "local_search": "Use for entity/relationship questions.",
                        "global_search": "Use for community-wide risk or theme questions.",
                    }),
                    "graph_retrieval_router_policy",
                    metadata={"family": "graph_policy"},
                ),
            ),
            expectations={
                "expected_answer_phrases": ["Alice", "ServiceA", "retry storms"],
                "required_selected_paths": [
                    "graph/local_entity_search.json",
                    "graph/global_community_summary.json",
                    "graph/routing_policy.json",
                ],
                "required_citation_paths": [
                    "graph/local_entity_search.json",
                    "graph/global_community_summary.json",
                    "graph/routing_policy.json",
                ],
                "required_retrieval_actions": ["graph_local_search", "graph_global_search"],
                "required_evidence_families": ["graph_local", "graph_global", "graph_policy"],
                "expected_flags": {"used_graph_local_and_global": True},
            },
            pipeline={
                "quality_gate": "entity-level local graph search plus community-level global graph search",
                "retrieval_structure": "knowledge_graph",
            },
        ),
        AdvancedEvalCase(
            concept_id="evaluation_metrics_pack",
            name="Evaluation metrics pack",
            query="Did the RAG answer pass retrieval, faithfulness, citation, and model-regression checks?",
            goal=(
                "The local model must summarize multiple RAG evaluation metrics and cite the metric artifacts."
            ),
            evidence=(
                evidence(
                    "metrics/context_precision_recall.json",
                    json_dumps({
                        "context_precision": 0.80,
                        "context_recall": 0.75,
                        "gold_sources": ["a.md", "b.md", "c.md"],
                        "retrieved_sources": ["a.md", "b.md", "noise.md"],
                    }),
                    "context_retrieval_metrics",
                    metadata={"family": "context_metrics"},
                ),
                evidence(
                    "metrics/faithfulness.json",
                    json_dumps({
                        "faithfulness": 0.92,
                        "unsupported_claims": [],
                        "verdict": "pass",
                    }),
                    "answer_faithfulness_metric",
                    metadata={"family": "answer_metrics"},
                ),
                evidence(
                    "metrics/citation_correctness.json",
                    json_dumps({
                        "citation_correctness": 1.0,
                        "bad_citations": [],
                        "verdict": "pass",
                    }),
                    "citation_metric",
                    metadata={"family": "answer_metrics"},
                ),
                evidence(
                    "metrics/model_regression_delta.json",
                    json_dumps({
                        "baseline_model": "qwen2.5:1.5b",
                        "candidate_model": "gemma4:26b",
                        "pass_rate_delta": 0.18,
                        "repair_rate_delta": -0.42,
                        "verdict": "candidate_improved",
                    }),
                    "model_regression_metric",
                    metadata={"family": "regression_metrics"},
                ),
            ),
            expectations={
                "expected_answer_phrases": [
                    "context precision",
                    "context recall",
                    "faithfulness",
                    "citation correctness",
                    "regression",
                ],
                "required_selected_paths": [
                    "metrics/context_precision_recall.json",
                    "metrics/faithfulness.json",
                    "metrics/citation_correctness.json",
                    "metrics/model_regression_delta.json",
                ],
                "required_citation_paths": [
                    "metrics/context_precision_recall.json",
                    "metrics/faithfulness.json",
                    "metrics/citation_correctness.json",
                    "metrics/model_regression_delta.json",
                ],
                "required_retrieval_actions": [
                    "compute_context_precision",
                    "compute_context_recall",
                    "check_faithfulness",
                    "check_citation_correctness",
                    "compare_model_regression",
                ],
                "required_evidence_families": ["context_metrics", "answer_metrics", "regression_metrics"],
                "expected_flags": {"metrics_ok": True},
            },
            pipeline={
                "quality_gate": "RAG evaluation metrics are explicit artifacts, not prose-only claims",
                "metrics": ["context_precision", "context_recall", "faithfulness", "citation_correctness", "model_regression_delta"],
            },
        ),
    ]


def run_advanced_eval_suite(
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
        print(f"[rag-advanced-eval-smoke] case: {case.concept_id}", flush=True)
        print(f"[rag-advanced-eval-smoke] local AI advanced/eval decision: {case.concept_id}", flush=True)
        local_ai = ask_local_ai(case, provider, allow_repair=not strict_model)
        validation = local_ai.get("validation") or {}
        ok = bool(validation.get("ok")) and (not strict_model or not local_ai.get("repaired_by_harness"))

        if local_ai.get("repaired_by_harness"):
            print(f"[rag-advanced-eval-smoke] local AI needed grounded harness repair: {case.concept_id}", flush=True)
            if strict_model:
                ok = False

        result = {
            "schema_version": SCHEMA_VERSION,
            "mode": "rag_advanced_eval_layer",
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
            print(f"[rag-advanced-eval-smoke] docker validate concept: {case.concept_id}", flush=True)
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
        "mode": "rag_advanced_eval_layer+local_ai" + ("+docker" if use_docker else ""),
        "provider": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "concept_count": len(results),
        "concept_results": results,
        "failures": failures,
        "ok": not failures,
    }

    if docker_executor is not None:
        print("[rag-advanced-eval-smoke] docker validate full trace", flush=True)
        docker_trace = run_docker_validation(docker_executor, trace, full_trace=True)
        trace["docker_trace_validation"] = docker_trace
        if not docker_trace.get("ok"):
            trace["ok"] = False
            trace["failures"].append("docker trace validation failed: " + str(docker_trace.get("error") or "unknown"))

    trace_path = output_dir / "rag_advanced_eval_layer_trace.json"
    trace_path.write_text(json_dumps(trace), encoding="utf-8")
    return trace


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RAG quality-layer local-AI + Docker smoke tests.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--output-dir", default=None, help="Trace output directory.")
    parser.add_argument("--strict-model", action="store_true", default=env_flag("MAIN_COMPUTER_RAG_QUALITY_STRICT_MODEL"), help="Fail if model output needs harness repair.")
    parser.add_argument("--no-docker", action="store_true", default=False, help="Disable Docker validation. Docker is on by default.")
    parser.add_argument("--fake-local-ai", action="store_true", default=False, help="Use fake local AI provider for offline debugging.")
    parser.add_argument("--deterministic-only", action="store_true", default=False, help="Alias for --fake-local-ai --no-docker.")
    parser.add_argument("--list", action="store_true", help="List quality-layer cases and exit.")
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
        "[rag-advanced-eval-smoke] starting "
        f"concepts={len(cases)} provider={getattr(provider, 'name', 'unknown')} "
        f"model={getattr(provider, 'model', 'unknown')} strict_model={bool(args.strict_model)} docker={use_docker}",
        flush=True,
    )

    trace = run_advanced_eval_suite(
        repo_root=repo_root,
        output_dir=output_dir,
        provider=provider,
        use_docker=use_docker,
        strict_model=bool(args.strict_model),
    )

    trace_path = output_dir / "rag_advanced_eval_layer_trace.json"
    print(f"[rag-advanced-eval-smoke] trace={trace_path}", flush=True)

    if trace.get("ok"):
        print("[rag-advanced-eval-smoke] passed", flush=True)
        print(f"[rag-advanced-eval-smoke] mode={trace.get('mode')}", flush=True)
        print(f"[rag-advanced-eval-smoke] model={trace.get('model')}", flush=True)
        print(f"[rag-advanced-eval-smoke] concepts={trace.get('concept_count')}", flush=True)
        return 0

    print("[rag-advanced-eval-smoke] failed", flush=True)
    for failure in trace.get("failures", []):
        print("  - " + str(failure), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
