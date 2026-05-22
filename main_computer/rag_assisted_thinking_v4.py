from __future__ import annotations

"""RAG assisted thinking backend v4.

v4 keeps the v2/v3 safety contract but changes the slow defaults:
read-only RAG skips Docker verification, retrieval is one smaller pass by default,
model context is assembled from retrieved chunks instead of whole files, broad RAG
smoke-test query expansion is removed, and inner diagnostics are scrubbed/bounded.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any, Mapping, Sequence

from main_computer.models import ChatMessage
from main_computer.providers import LLMProvider
import main_computer.rag_assisted_thinking_v2 as v2
from main_computer.rag_assisted_thinking_v2 import (
    RagAssistedThinkingV2Result,
    RetrievalQualityReport,
    WebSearchFn,
)
from main_computer.rag_assisted_thinking_v3 import (
    ActivityAwareProvider,
    RagAssistedThinkingV3Policy,
    UnifiedRagActivityBus,
    _write_session_log,
)


RAG_ASSISTED_THINKING_V4_VERSION = "4.0"
_PATCH_LOCK = threading.RLock()
_LAST_RAG_RESULT: ContextVar[Any | None] = ContextVar("v4_last_rag_result", default=None)
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+")
_PATH_RE = re.compile(r"\b[\w./-]+\.(?:py|js|ts|tsx|json|md|toml|yaml|yml|html|css|txt|ps1|sh)\b", re.I)
_SYMBOL_RE = re.compile(r"\b(?:class|def|function)\s+([A-Za-z_][A-Za-z0-9_]*)|\b([A-Za-z_][A-Za-z0-9_]{3,})\(")


def utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


def default_run_id() -> str:
    return f"rag_assisted_thinking_v4_{utc_stamp()}"


@dataclass(frozen=True)
class RagAssistedThinkingV4Policy(RagAssistedThinkingV3Policy):
    max_retrieval_rounds: int = 1
    max_context_chars: int = 18_000
    max_candidates: int = 12
    max_chunks: int = 8
    max_context_files: int = 0
    max_file_chars: int = 0
    max_repair_prompt_chars: int = 45_000
    min_quality_score: float = 0.35
    skip_docker_for_read_only: bool = True
    use_retrieved_chunks_only: bool = True
    exact_evidence_required: bool = True
    scrub_harness_diagnostics: bool = True


class RagAssistedThinkingV4Result:
    def __init__(self, delegate: RagAssistedThinkingV2Result, *, optimizations: Mapping[str, Any] | None = None) -> None:
        self._delegate = delegate
        self.version = RAG_ASSISTED_THINKING_V4_VERSION
        self.previous_version = v2.RAG_ASSISTED_THINKING_V2_VERSION
        self.mode = "rag_assisted_thinking_v4"
        self.optimizations = dict(optimizations or {})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def as_dict(self) -> dict[str, Any]:
        data = self._delegate.as_dict()
        data["version"] = self.version
        data["previous_version"] = self.previous_version
        data["mode"] = self.mode
        data["activity_filter"] = "ai"
        data["optimizations"] = dict(self.optimizations)
        return data


def _safe_rel(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    parts = [part for part in raw.split("/") if part]
    if not parts or any(part == ".." or ":" in part for part in parts):
        return ""
    return "/".join(parts)


def _path_hints(prompt: str, queries: Sequence[str] | str | None = None) -> list[str]:
    text = " ".join([str(prompt or ""), " ".join(queries) if isinstance(queries, list) else str(queries or "")])
    return v2._dedupe(_safe_rel(match) for match in _PATH_RE.findall(text) if _safe_rel(match))[:12]


def _symbol_hints(prompt: str) -> list[str]:
    found: list[str] = []
    for match in _SYMBOL_RE.finditer(str(prompt or "")):
        value = (match.group(1) or match.group(2) or "").strip()
        if value and value.lower() not in {"print", "return"}:
            found.append(value)
    return v2._dedupe(found)[:8]


def build_v4_retrieval_queries(prompt: str, queries: Sequence[str] | str | None, intent: Any) -> list[str]:
    result: list[str] = []
    if isinstance(queries, str):
        raw_queries = [part.strip() for part in re.split(r"[\n;]+", queries) if part.strip()]
    else:
        raw_queries = [str(item).strip() for item in (queries or []) if str(item).strip()]
    for item in raw_queries:
        if len(item) <= 180:
            result.append(item)
    result.extend(_path_hints(prompt, queries))
    result.extend(_symbol_hints(prompt))

    lowered = str(prompt or "").lower()
    if "rag" in lowered:
        result.extend(
            [
                "run_rag_assisted_thinking_v4_request run_rag_assisted_thinking_v3_request",
                "run_rag_assisted_thinking_v2_request evaluate_retrieval_quality build_v2_messages",
                "run_rag_harness DeterministicRagRetriever retrieve chunks",
                "viewport_routes_rag_assisted_thinking policy",
                "chat_ai_subprocess rag_assisted_thinking_v4",
            ]
        )
    if getattr(intent, "requires_file_change", False):
        result.append("complete replacement files allowed_write_paths auto_apply")
    if getattr(intent, "requires_execution_or_tests", False):
        result.append("docker verification executor tests")
    if not result and str(prompt or "").strip():
        result.append(str(prompt).strip()[:180])
    return v2._dedupe(result)[:8]


def _chunk_context_from_last_result(*, max_total_chars: int) -> list[dict[str, Any]]:
    rag_result = _LAST_RAG_RESULT.get()
    if rag_result is None:
        return []
    context: list[dict[str, Any]] = []
    used = 0
    seen: set[tuple[str, int, int]] = set()
    try:
        chunks = list(rag_result.retrieval.chunks)
    except Exception:
        chunks = []
    for chunk in chunks:
        key = (str(chunk.path), int(chunk.start_line), int(chunk.end_line))
        if key in seen:
            continue
        seen.add(key)
        remaining = max(0, max_total_chars - used)
        if remaining <= 0:
            break
        content = str(chunk.content or "")
        truncated = bool(chunk.truncated)
        if len(content) > remaining:
            content = content[:remaining]
            truncated = True
        content = _SECRET_RE.sub(r"\1=<redacted>", content)
        if not content.strip():
            continue
        context.append(
            {
                "path": str(chunk.path),
                "content": content,
                "chars": len(content),
                "truncated": truncated,
                "start_line": int(chunk.start_line),
                "end_line": int(chunk.end_line),
                "score": float(chunk.score),
                "reason": str(chunk.reason),
                "context_kind": "retrieved_chunk",
                "trusted_as_tool_instructions": False,
                "source_role": "untrusted_retrieved_evidence",
            }
        )
        used += len(content)
    return context


def _scrub_text(text: str, *, limit: int = 1200) -> str:
    text = _SECRET_RE.sub(r"\1=<redacted>", str(text or ""))
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _scrub_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"content", "grounded_prompt"} and isinstance(item, str):
                result[key] = _scrub_text(item)
                if len(item) > 1200:
                    result[f"{key}_scrubbed_by_v4"] = True
            else:
                result[key] = _scrub_json(item)
        return result
    if isinstance(value, list):
        return [_scrub_json(item) for item in value]
    if isinstance(value, str):
        return _SECRET_RE.sub(r"\1=<redacted>", value)
    return value


def _scrub_harness_diagnostics(rag_result: Any | None) -> None:
    if rag_result is None:
        return
    root = Path(str(getattr(rag_result, "output_dir", "") or ""))
    if not root.exists():
        return
    for name in ("context_chunks.json", "run.json", "final_plan.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload = _scrub_json(payload)
            if isinstance(payload, dict):
                payload["diagnostics_scrubbed_by_v4"] = True
            path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        except Exception:
            pass
    grounded = root / "grounded_prompt.txt"
    if grounded.is_file():
        try:
            grounded.write_text(
                "RAG assisted thinking v4 scrubbed the full grounded prompt. "
                "Use context_chunks.json for bounded redacted evidence excerpts.\n",
                encoding="utf-8",
            )
        except Exception:
            pass


def _is_read_only(intent: Any) -> bool:
    return not bool(getattr(intent, "requires_file_change", False) or getattr(intent, "requires_execution_or_tests", False))


def _quality_with_v4_gates(base: RetrievalQualityReport, *, prompt: str, queries: Sequence[str] | str | None, intent: Any, context: Sequence[Mapping[str, Any]]) -> RetrievalQualityReport:
    evidence_paths = v2._context_paths(context)
    missing = list(base.missing)
    warnings = list(base.warnings)
    flags = dict(base.flags)
    actions = v2._dedupe([*base.retrieval_actions, "v4_exact_chunk_quality"])

    missing_hints = [path for path in _path_hints(prompt, queries) if path not in evidence_paths]
    if missing_hints:
        missing.append("explicit path evidence: " + ", ".join(missing_hints[:6]))
        flags["missing_explicit_path_evidence"] = True

    docs_only = bool(evidence_paths) and all(path.endswith(".md") or path in {"README.md", "ENVIRONMENT.md", "TODO.md"} for path in evidence_paths)
    if docs_only and getattr(intent, "needs_local_repo_context", False) and "rag" in str(prompt or "").lower():
        warnings.append("v4 retrieval evidence is documentation-heavy; source chunks may be needed for code-level claims")
        flags["documentation_heavy_evidence"] = True

    if getattr(intent, "rag_required", False) and not evidence_paths:
        missing.append("local retrieval chunk evidence")
        flags["missing_chunk_evidence"] = True

    score = min(float(base.score), 0.85)
    if evidence_paths:
        score = max(score, min(0.55, 0.25 + len(evidence_paths) / 20.0))
    if docs_only:
        score = min(score, 0.55)

    blocked = bool(base.generation_blocked or missing or score < 0.35)
    if getattr(base, "needs_clarification", False):
        status = "clarify"
    elif blocked:
        status = "retry_or_abstain"
    else:
        status = "sufficient"
    if blocked:
        flags["generation_blocked"] = True
        actions.append("block_generation")
    else:
        flags.pop("generation_blocked", None)
        actions.append("allow_grounded_generation")

    return RetrievalQualityReport(
        status=status,
        score=round(score, 3),
        sufficient=not blocked,
        needs_clarification=base.needs_clarification,
        generation_blocked=blocked,
        missing=v2._dedupe(missing),
        warnings=v2._dedupe(warnings),
        retrieval_actions=actions,
        searched_scopes=list(base.searched_scopes),
        evidence_paths=evidence_paths,
        dropped_paths=list(base.dropped_paths),
        flags=flags,
    )


@contextmanager
def _v4_v2_patches(policy: RagAssistedThinkingV4Policy, prompt: str, queries: Sequence[str] | str | None):
    original_run_harness = v2.run_rag_harness
    original_read_context = v2.read_retrieved_context
    original_build_queries = v2.build_retrieval_queries
    original_quality = v2.evaluate_retrieval_quality

    def run_harness_wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original_run_harness(*args, **kwargs)
        _LAST_RAG_RESULT.set(result)
        if policy.scrub_harness_diagnostics:
            _scrub_harness_diagnostics(result)
        return result

    def read_context_wrapper(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if policy.use_retrieved_chunks_only and _LAST_RAG_RESULT.get() is not None:
            max_total = int(kwargs.get("max_total_chars") or policy.max_context_chars)
            return _chunk_context_from_last_result(max_total_chars=max_total)
        return original_read_context(*args, **kwargs)

    def build_queries_wrapper(inner_prompt: str, inner_queries: Sequence[str] | str | None, intent: Any) -> list[str]:
        return build_v4_retrieval_queries(inner_prompt, inner_queries, intent)

    def quality_wrapper(*args: Any, **kwargs: Any) -> RetrievalQualityReport:
        base = original_quality(*args, **kwargs)
        if not policy.exact_evidence_required:
            return base
        if policy.self_contained_benchmark_mode or v2.is_self_contained_recreation_benchmark(str(kwargs.get("prompt") or prompt)):
            return base
        return _quality_with_v4_gates(
            base,
            prompt=str(kwargs.get("prompt") or prompt),
            queries=queries,
            intent=kwargs.get("intent"),
            context=kwargs.get("retrieved_context") or [],
        )

    with _PATCH_LOCK:
        token = _LAST_RAG_RESULT.set(None)
        v2.run_rag_harness = run_harness_wrapper
        v2.read_retrieved_context = read_context_wrapper
        v2.build_retrieval_queries = build_queries_wrapper
        v2.evaluate_retrieval_quality = quality_wrapper
        try:
            yield
        finally:
            v2.run_rag_harness = original_run_harness
            v2.read_retrieved_context = original_read_context
            v2.build_retrieval_queries = original_build_queries
            v2.evaluate_retrieval_quality = original_quality
            _LAST_RAG_RESULT.reset(token)


def _effective_policy(prompt: str, queries: Sequence[str] | str | None, upload_ids: Sequence[str] | None, policy: RagAssistedThinkingV4Policy) -> RagAssistedThinkingV4Policy:
    intent = v2.classify_request_intent(prompt, queries=queries, upload_ids=upload_ids)
    if policy.skip_docker_for_read_only and _is_read_only(intent) and policy.verify_before:
        return replace(policy, verify_before=False)
    return policy


def _optimizations(policy: RagAssistedThinkingV4Policy) -> dict[str, Any]:
    return {
        "chunks_only_context": bool(policy.use_retrieved_chunks_only),
        "skip_docker_for_read_only": bool(policy.skip_docker_for_read_only),
        "exact_evidence_required": bool(policy.exact_evidence_required),
        "scrub_harness_diagnostics": bool(policy.scrub_harness_diagnostics),
        "max_retrieval_rounds": int(policy.max_retrieval_rounds),
    }


def run_rag_assisted_thinking_v4_request(
    *,
    prompt: str,
    repo_dir: Path | str = ".",
    provider: LLMProvider | None = None,
    rag_provider: LLMProvider | None = None,
    activity_bus: Any | None = None,
    queries: list[str] | str | None = None,
    upload_ids: list[str] | None = None,
    run_id: str | None = None,
    output_root: Path | str | None = None,
    policy: RagAssistedThinkingV4Policy | None = None,
    web_search_fn: WebSearchFn | None = None,
) -> RagAssistedThinkingV4Result:
    if provider is None:
        raise ValueError("provider is required for RAG-assisted thinking v4")

    run_id = run_id or default_run_id()
    requested_policy = policy or RagAssistedThinkingV4Policy()
    policy = _effective_policy(prompt, queries, upload_ids, requested_policy)
    docker_before_skipped_for_read_only = bool(
        requested_policy.docker_enabled
        and requested_policy.verify_before
        and not policy.verify_before
        and requested_policy.skip_docker_for_read_only
    )
    repo_path = Path(repo_dir).resolve()
    base_output = Path(output_root).resolve() if output_root else repo_path / "diagnostics_output" / "rag_assisted_thinking_v4_runs"
    log_file = str(base_output / f"{run_id}.session.jsonl")
    opts = _optimizations(policy)
    _write_session_log(
        log_file,
        {
            "event": "prompt",
            "run_id": run_id,
            "mode": "rag_assisted_thinking_v4",
            "prompt": prompt,
            "queries": queries,
            "upload_ids": upload_ids,
            "repo_dir": str(repo_path),
            "provider": getattr(provider, "name", ""),
            "model": getattr(provider, "model", ""),
            "policy": policy.as_dict(),
            "optimizations": opts,
        },
    )

    unified_activity = UnifiedRagActivityBus(
        activity_bus,
        run_id=run_id,
        log_file=log_file,
        activity_tag="rag-assisted-thinking-v4",
    )
    wrapped_provider = ActivityAwareProvider(provider, unified_activity, run_id=run_id)
    wrapped_rag_provider = ActivityAwareProvider(rag_provider, unified_activity, run_id=run_id) if rag_provider is not None else None

    unified_activity.record(
        source="rag-assisted-thinking-v4",
        kind="ai",
        time_model="parallel",
        severity="info",
        title="AI RAG backend v4 started",
        message=v2._preview(prompt),
        status="running",
        tags=["ai", "rag", "thinking", "local-ai", "run", "v4"],
        data={
            "run_id": run_id,
            "mode": "rag_assisted_thinking_v4",
            "activity_filter": "ai",
            "log_file": log_file,
            "output_dir": str(base_output / run_id),
            "docker_enabled": bool(policy.docker_enabled),
            "raw_thinking_exposed": False,
            "running_text": "RAG-assisted thinking v4 backend running",
            "rag_type": "run",
            "optimizations": opts,
        },
    )

    with _v4_v2_patches(policy, prompt, queries):
        result = v2.run_rag_assisted_thinking_v2_request(
            prompt=prompt,
            repo_dir=repo_dir,
            provider=wrapped_provider,
            rag_provider=wrapped_rag_provider,
            activity_bus=unified_activity,
            queries=queries,
            upload_ids=upload_ids,
            run_id=run_id,
            output_root=output_root,
            policy=policy,
            web_search_fn=web_search_fn,
        )

    if docker_before_skipped_for_read_only and not any("skipped Docker before verification" in str(item) for item in result.warnings):
        result.warnings.append("v4 skipped Docker before verification for read-only RAG")
    wrapped = RagAssistedThinkingV4Result(result, optimizations=opts)
    unified_activity.record(
        source="rag-assisted-thinking-v4",
        kind="ai",
        time_model="parallel",
        severity="info" if result.ok else "error",
        title="AI RAG backend v4 finished",
        message=f"status={result.status}; proposed={len(result.proposed_paths)}; written={len(result.written_paths)}",
        status=result.status,
        tags=["ai", "rag", "thinking", "local-ai", "run", "v4", "completed" if result.ok else "failed"],
        data={
            "run_id": run_id,
            "mode": "rag_assisted_thinking_v4",
            "activity_filter": "ai",
            "ok": result.ok,
            "status": result.status,
            "output_dir": result.output_dir,
            "log_file": log_file,
            "proposed_paths": result.proposed_paths,
            "written_paths": result.written_paths,
            "docker_before_ok": result.docker_before.ok if result.docker_before else None,
            "docker_after_ok": result.docker_after.ok if result.docker_after else None,
            "raw_thinking_exposed": False,
            "ran_text": f"RAG-assisted thinking v4 finished with status={result.status}",
            "rag_type": "run",
            "optimizations": opts,
        },
    )
    return wrapped


run_rag_assisted_thinking_request_v4 = run_rag_assisted_thinking_v4_request


__all__ = [
    "RAG_ASSISTED_THINKING_V4_VERSION",
    "RagAssistedThinkingV4Policy",
    "RagAssistedThinkingV4Result",
    "build_v4_retrieval_queries",
    "default_run_id",
    "run_rag_assisted_thinking_request_v4",
    "run_rag_assisted_thinking_v4_request",
]
