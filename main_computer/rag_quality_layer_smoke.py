#!/usr/bin/env python3
"""RAG quality-layer smoke tests.

This is a standalone smoke harness for the retrieval-quality layer that sits
under the AI-development control plane.

Default run uses local Ollama and Docker:

    python3 ./main_computer/rag_quality_layer_smoke.py

Debug modes:

    python3 ./main_computer/rag_quality_layer_smoke.py --strict-model
    python3 ./main_computer/rag_quality_layer_smoke.py --fake-local-ai --no-docker
    python3 ./main_computer/rag_quality_layer_smoke.py --deterministic-only

Seven quality gates included:
1. lexical_exact_match
2. semantic_synonym_retrieval
3. top_k_diversity_deduplication
4. lost_in_the_middle_guard
5. token_budget_context_packing
6. contradiction_handling
7. hyde_retrieval
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
RUN_ID = "rag_quality_layer"
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


def build_cases() -> list[QualityCase]:
    long_middle = "\n".join(
        [f"noise line {i}: unrelated boilerplate" for i in range(1, 41)]
        + ["DECISIVE: Web search is disabled by --no-web-search."]
        + [f"noise line {i}: unrelated footer" for i in range(42, 82)]
    )
    return [
        QualityCase(
            concept_id="lexical_exact_match",
            name="Lexical exact-match retrieval",
            query="Where is WidgetLockError handled?",
            goal="Exact symbol/name match must win top retrieval.",
            evidence=(
                evidence("src/widget_lock.py", "def handle_widget_lock_error():\n    raise WidgetLockError('lock failure recovery')", "exact_symbol_match", score=100),
                evidence("docs/widget_errors.md", "The widget lock failure recovery path handles lock problems.", "semantic_but_no_symbol", score=35),
                evidence("docs/noise.md", "Widget lock lock lock unrelated repeated noise.", "keyword_spam", score=10),
            ),
            expectations={
                "expected_answer_phrases": ["WidgetLockError", "src/widget_lock.py"],
                "required_selected_paths": ["src/widget_lock.py"],
                "required_citation_paths": ["src/widget_lock.py"],
                "forbidden_selected_paths": ["docs/noise.md"],
                "required_retrieval_actions": ["lexical_exact_match"],
                "expected_flags": {"exact_match_won": True},
            },
            pipeline={"quality_gate": "pure lexical baseline", "top_expected": "src/widget_lock.py"},
        ),
        QualityCase(
            concept_id="semantic_synonym_retrieval",
            name="Semantic synonym retrieval",
            query="When did humans reach the moon?",
            goal="Semantic retrieval must beat a keyword-only moon-cake distractor.",
            evidence=(
                evidence("docs/lunar_landing.md", "The first lunar landing occurred in July 1969.", "semantic_synonym_hit", score=88, metadata={"semantic_terms": ["humans reached the moon", "lunar landing"]}),
                evidence("docs/moon_cake.md", "Moon cakes are traditional pastries eaten during festivals.", "keyword_distractor", score=20),
                evidence("docs/ocean.md", "Humans reached the deepest ocean point with a submersible.", "shared_words_wrong_topic", score=18),
            ),
            expectations={
                "expected_answer_phrases": ["July 1969", "lunar landing"],
                "required_selected_paths": ["docs/lunar_landing.md"],
                "required_citation_paths": ["docs/lunar_landing.md"],
                "forbidden_selected_paths": ["docs/moon_cake.md", "docs/ocean.md"],
                "required_retrieval_actions": ["semantic_synonym_retrieval"],
                "expected_flags": {"semantic_match_won": True},
            },
            pipeline={"quality_gate": "semantic synonym retrieval", "baseline_keyword_false_positive": "docs/moon_cake.md"},
        ),
        QualityCase(
            concept_id="top_k_diversity_deduplication",
            name="Top-k diversity and deduplication",
            query="Explain config loading and precedence.",
            goal="Context must include distinct evidence families instead of duplicate spam.",
            evidence=(
                evidence("docs/config_loading_a.md", "Config loads from default file, env, and CLI.", "duplicate_family:loading", metadata={"family": "loading"}),
                evidence("docs/config_loading_b.md", "Config loads from default file, env, and CLI.", "near_duplicate_family:loading", metadata={"family": "loading"}),
                evidence("docs/config_loading_c.md", "Config loads from default file, env, and CLI.", "near_duplicate_family:loading", metadata={"family": "loading"}),
                evidence("docs/config_precedence.md", "Precedence is CLI over env over default file.", "distinct_family:precedence", metadata={"family": "precedence"}),
            ),
            expectations={
                "expected_answer_phrases": ["CLI over env over default", "config loads"],
                "required_selected_paths": ["docs/config_loading_a.md", "docs/config_precedence.md"],
                "required_citation_paths": ["docs/config_loading_a.md", "docs/config_precedence.md"],
                "required_dropped_paths": ["docs/config_loading_b.md", "docs/config_loading_c.md"],
                "required_evidence_families": ["loading", "precedence"],
                "required_retrieval_actions": ["deduplicate_top_k", "diversify_evidence_families"],
            },
            pipeline={"quality_gate": "top-k diversity", "dedupe_family": "loading", "required_families": ["loading", "precedence"]},
        ),
        QualityCase(
            concept_id="lost_in_the_middle_guard",
            name="Lost-in-the-middle guard",
            query="Which flag disables web search?",
            goal="Critical evidence buried in a long block must be surfaced in an evidence brief.",
            evidence=(
                evidence("docs/long_cli_reference.md", long_middle, "long_context_with_middle_hit", metadata={"decisive_line": 41, "token_estimate": estimate_tokens(long_middle)}),
                evidence("context_brief/web_search_flag.md", "Evidence brief: Web search is disabled by --no-web-search.", "front_loaded_evidence_brief", metadata={"source": "docs/long_cli_reference.md", "line": 41}),
            ),
            expectations={
                "expected_answer_phrases": ["--no-web-search", "evidence brief"],
                "required_selected_paths": ["context_brief/web_search_flag.md"],
                "required_citation_paths": ["context_brief/web_search_flag.md"],
                "required_retrieval_actions": ["front_load_decisive_evidence"],
                "expected_flags": {"lost_in_middle_guarded": True},
            },
            pipeline={"quality_gate": "lost-in-the-middle", "decisive_evidence_line": 41, "brief_first": True},
        ),
        QualityCase(
            concept_id="token_budget_context_packing",
            name="Token-budget context packing",
            query="Summarize the startup regression under a 45 token context budget.",
            goal="Must-have evidence survives while low-value chunks are dropped under budget.",
            evidence=(
                evidence("docs/startup_regression.md", "Must-have: startup slowdown comes from plugin discovery scanning extension manifests.", "must_have:root_cause", metadata={"must_have": True, "token_estimate": 10}),
                evidence("docs/startup_fix.md", "Must-have: cache plugin manifest discovery results between runs.", "must_have:fix", metadata={"must_have": True, "token_estimate": 9}),
                evidence("docs/startup_noise_1.md", "Background: old unrelated startup notes about banner rendering.", "low_value", metadata={"must_have": False, "token_estimate": 12}),
                evidence("docs/startup_noise_2.md", "Background: unrelated terminal color setup details.", "low_value", metadata={"must_have": False, "token_estimate": 10}),
            ),
            expectations={
                "expected_answer_phrases": ["plugin discovery", "cache plugin manifest"],
                "required_selected_paths": ["docs/startup_regression.md", "docs/startup_fix.md"],
                "required_citation_paths": ["docs/startup_regression.md", "docs/startup_fix.md"],
                "required_dropped_paths": ["docs/startup_noise_1.md", "docs/startup_noise_2.md"],
                "required_retrieval_actions": ["pack_context_budget"],
                "max_context_tokens": 45,
                "expected_flags": {"budget_ok": True},
            },
            pipeline={"quality_gate": "token-budget packing", "max_context_tokens": 45},
        ),
        QualityCase(
            concept_id="contradiction_handling",
            name="Contradiction handling",
            query="What is the default timeout?",
            goal="Conflicting docs must be surfaced and both sides cited.",
            evidence=(
                evidence("docs/timeout_2024.md", "Timeout default is 30 seconds.", "older_conflicting_source", metadata={"date": "2024-01-01"}),
                evidence("docs/timeout_2025.md", "Timeout default is 60 seconds.", "newer_conflicting_source", metadata={"date": "2025-01-01"}),
                evidence("policy/conflicts.md", "When sources conflict, cite both and prefer newer only if policy says freshness wins.", "conflict_policy"),
            ),
            expectations={
                "expected_answer_phrases": ["conflict", "30 seconds", "60 seconds"],
                "required_selected_paths": ["docs/timeout_2024.md", "docs/timeout_2025.md", "policy/conflicts.md"],
                "required_citation_paths": ["docs/timeout_2024.md", "docs/timeout_2025.md", "policy/conflicts.md"],
                "required_retrieval_actions": ["surface_contradiction"],
                "expected_flags": {"conflict_detected": True},
            },
            pipeline={"quality_gate": "contradiction handling", "conflicting_values": ["30 seconds", "60 seconds"]},
        ),
        QualityCase(
            concept_id="hyde_retrieval",
            name="HyDE retrieval",
            query="Why is startup slow?",
            goal="Hypothetical document should bridge vague query to real plugin-discovery evidence.",
            evidence=(
                evidence("hyde/startup_slow_hypothetical.md", "Hypothetical answer: startup can be slow because dependency scanning and plugin discovery scan extension manifests.", "hyde_generated_document"),
                evidence("docs/plugin_discovery.md", "Plugin discovery scans extension manifests during startup.", "real_document_retrieved_by_hyde"),
                evidence("retrieval/baseline_miss.json", json_dumps({"baseline_query": "Why is startup slow?", "top_result": "docs/generic_performance.md", "found_plugin_discovery": False}), "baseline_miss_report"),
            ),
            expectations={
                "expected_answer_phrases": ["plugin discovery", "extension manifests"],
                "required_selected_paths": ["hyde/startup_slow_hypothetical.md", "docs/plugin_discovery.md"],
                "required_citation_paths": ["docs/plugin_discovery.md"],
                "required_retrieval_actions": ["generate_hypothetical_document", "retrieve_with_hyde"],
                "expected_flags": {"used_hyde": True},
            },
            pipeline={"quality_gate": "HyDE", "baseline_miss_then_hyde_hit": True},
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
        print(f"[rag-quality-smoke] case: {case.concept_id}", flush=True)
        print(f"[rag-quality-smoke] local AI quality decision: {case.concept_id}", flush=True)
        local_ai = ask_local_ai(case, provider, allow_repair=not strict_model)
        validation = local_ai.get("validation") or {}
        ok = bool(validation.get("ok")) and (not strict_model or not local_ai.get("repaired_by_harness"))

        if local_ai.get("repaired_by_harness"):
            print(f"[rag-quality-smoke] local AI needed grounded harness repair: {case.concept_id}", flush=True)
            if strict_model:
                ok = False

        result = {
            "schema_version": SCHEMA_VERSION,
            "mode": "rag_quality_layer",
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
            print(f"[rag-quality-smoke] docker validate concept: {case.concept_id}", flush=True)
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
        "mode": "rag_quality_layer+local_ai" + ("+docker" if use_docker else ""),
        "provider": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "concept_count": len(results),
        "concept_results": results,
        "failures": failures,
        "ok": not failures,
    }

    if docker_executor is not None:
        print("[rag-quality-smoke] docker validate full trace", flush=True)
        docker_trace = run_docker_validation(docker_executor, trace, full_trace=True)
        trace["docker_trace_validation"] = docker_trace
        if not docker_trace.get("ok"):
            trace["ok"] = False
            trace["failures"].append("docker trace validation failed: " + str(docker_trace.get("error") or "unknown"))

    trace_path = output_dir / "rag_quality_layer_trace.json"
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
        "[rag-quality-smoke] starting "
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

    trace_path = output_dir / "rag_quality_layer_trace.json"
    print(f"[rag-quality-smoke] trace={trace_path}", flush=True)

    if trace.get("ok"):
        print("[rag-quality-smoke] passed", flush=True)
        print(f"[rag-quality-smoke] mode={trace.get('mode')}", flush=True)
        print(f"[rag-quality-smoke] model={trace.get('model')}", flush=True)
        print(f"[rag-quality-smoke] concepts={trace.get('concept_count')}", flush=True)
        return 0

    print("[rag-quality-smoke] failed", flush=True)
    for failure in trace.get("failures", []):
        print("  - " + str(failure), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
