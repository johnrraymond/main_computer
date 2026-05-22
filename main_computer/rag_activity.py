from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ActivityRecorder(Protocol):
    def record(self, **event: Any) -> dict[str, Any]:
        ...


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _preview(value: Any, *, limit: int = 220) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part for part in text.split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _compact_mapping(data: dict[str, Any], *, max_items: int = 12) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for index, (key, value) in enumerate(data.items()):
        if index >= max_items:
            compact["truncated"] = True
            break
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[str(key)] = _preview(value) if isinstance(value, str) else value
        elif isinstance(value, list):
            compact[str(key)] = [
                _preview(item) if isinstance(item, str) else item
                for item in value[:8]
                if isinstance(item, (str, int, float, bool)) or item is None
            ]
            if len(value) > 8:
                compact[f"{key}_truncated"] = True
        elif isinstance(value, dict):
            compact[str(key)] = _compact_mapping(value, max_items=6)
        else:
            compact[str(key)] = _preview(value)
    return compact


def _evidence_paths(items: Any, *, limit: int = 8) -> list[str]:
    paths: list[str] = []
    if not isinstance(items, list):
        return paths
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def summarize_step_output(kind: str, output: Any) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {"value": _preview(output)}

    if kind == "intake":
        upload_ids = output.get("upload_ids") if isinstance(output.get("upload_ids"), list) else []
        return {
            "mode": output.get("mode"),
            "repo_dir": output.get("repo_dir"),
            "upload_count": len(upload_ids),
        }

    if kind == "task_decomposition":
        return {
            "task_type": output.get("task_type", "unknown"),
            "risk": output.get("risk", ""),
            "executor_likely_needed": bool(output.get("executor_likely_needed")),
            "retrieval_queries": [
                str(item)
                for item in (output.get("retrieval_queries") or [])
                if isinstance(item, str)
            ][:8],
            "candidate_paths": [
                str(item)
                for item in (output.get("candidate_paths") or [])
                if isinstance(item, str)
            ][:8],
            "provider": output.get("provider", ""),
            "model": output.get("model", ""),
            "mode": output.get("mode", ""),
        }

    if kind == "context_inventory":
        uploads = output.get("uploads") if isinstance(output.get("uploads"), list) else []
        return {
            "file_count": int(output.get("file_count") or 0),
            "upload_count": len(uploads),
            "executor_root": output.get("executor_root", ""),
            "top_level": output.get("top_level", [])[:8] if isinstance(output.get("top_level"), list) else [],
        }

    if kind == "retrieval":
        candidates = output.get("candidates") if isinstance(output.get("candidates"), list) else []
        chunks = output.get("chunks") if isinstance(output.get("chunks"), list) else []
        return {
            "queries": [str(item) for item in (output.get("queries") or []) if isinstance(item, str)][:8],
            "scanned_files": int(output.get("scanned_files") or 0),
            "candidate_count": len(candidates),
            "chunk_count": len(chunks),
            "used_chars": int(output.get("used_chars") or 0),
            "context_budget_chars": int(output.get("context_budget_chars") or 0),
            "top_paths": _evidence_paths(candidates),
            "chunk_paths": _evidence_paths(chunks),
            "truncated_files": [
                str(item)
                for item in (output.get("truncated_files") or [])
                if isinstance(item, str)
            ][:8],
        }

    if kind == "context_brief":
        return {
            "task_type": output.get("task_type", "unknown"),
            "file_count": int(output.get("file_count") or 0),
            "used_chars": int(output.get("used_chars") or 0),
            "context_budget_chars": int(output.get("context_budget_chars") or 0),
            "queries": [str(item) for item in (output.get("queries") or []) if isinstance(item, str)][:8],
            "evidence_paths": _evidence_paths(output.get("evidence")),
            "fact_paths": _evidence_paths(output.get("facts")),
            "open_question_count": len(output.get("open_questions") or []),
        }

    if kind == "grounded_plan":
        next_step = output.get("next_step") if isinstance(output.get("next_step"), dict) else {}
        return {
            "type": output.get("type", "plan"),
            "summary": _preview(output.get("summary", ""), limit=300),
            "evidence_paths": _evidence_paths(output.get("evidence")),
            "next_step": _compact_mapping(next_step, max_items=8),
            "open_question_count": len(output.get("open_questions") or []),
            "provider": output.get("provider", ""),
            "model": output.get("model", ""),
            "mode": output.get("mode", ""),
        }

    return _compact_mapping(output)




def _rag_type(kind: str) -> str:
    return str(kind or "rag").strip().replace("-", "_") or "rag"

def _step_message(kind: str, summary: dict[str, Any]) -> str:
    if kind == "retrieval":
        return (
            f"{summary.get('chunk_count', 0)} chunks selected from "
            f"{summary.get('candidate_count', 0)} candidate files"
        )
    if kind == "context_inventory":
        return f"{summary.get('file_count', 0)} repository files inventoried"
    if kind == "task_decomposition":
        return f"Task classified as {summary.get('task_type', 'unknown')}"
    if kind == "context_brief":
        return f"Context brief covers {summary.get('used_chars', 0)} retrieved characters"
    if kind == "grounded_plan":
        return _preview(summary.get("summary", "")) or "Grounded plan completed"
    if kind == "intake":
        return f"RAG intake accepted in {summary.get('mode', 'unknown')} mode"
    return f"{kind.replace('_', ' ').title()} completed"


@dataclass
class RagActivityEmitter:
    """Emit safe, user-visible RAG activity into the Machine Activity Monitor.

    The emitter records step state, selected context, counts, and tool/model
    boundaries. It does not expose raw model thinking.
    """

    bus: ActivityRecorder
    run_id: str
    prompt: str = ""
    repo_dir: str = ""
    base_tags: tuple[str, ...] = ("rag", "thinking", "local-ai")

    def _record(
        self,
        *,
        title: str,
        message: str = "",
        kind: str = "ai",
        status: str = "",
        severity: str = "info",
        tags: list[str] | None = None,
        data: dict[str, Any] | None = None,
        source: str = "rag",
    ) -> dict[str, Any]:
        payload = dict(data or {})
        payload.setdefault("run_id", self.run_id)
        if self.repo_dir:
            payload.setdefault("repo_dir", self.repo_dir)
        return self.bus.record(
            source=source,
            kind=kind,
            time_model="parallel",
            severity=severity,
            title=title,
            message=message,
            status=status,
            tags=_dedupe(list(self.base_tags) + list(tags or [])),
            data=payload,
            fault=severity in {"warn", "error"},
        )

    def run_started(self, *, mode: str, use_model: bool, output_dir: str) -> dict[str, Any]:
        return self._record(
            title="RAG run started",
            message=_preview(self.prompt) or "Repository context retrieval started",
            status="running",
            tags=["run", "started", "model" if use_model else "deterministic"],
            data={
                "mode": mode,
                "use_model": bool(use_model),
                "output_dir": output_dir,
                "prompt_preview": _preview(self.prompt),
                "running_text": "RAG run started",
                "rag_type": "run",
            },
        )

    def run_completed(self, *, status: str, result: dict[str, Any]) -> dict[str, Any]:
        retrieval = result.get("retrieval") if isinstance(result.get("retrieval"), dict) else {}
        final_plan = result.get("final_plan") if isinstance(result.get("final_plan"), dict) else {}
        return self._record(
            title="RAG run completed",
            message=_preview(final_plan.get("summary", "")) or status,
            status=status,
            tags=["run", "completed"],
            data={
                "status": status,
                "step_count": len(result.get("steps") or []),
                "chunk_count": len(retrieval.get("chunks") or []),
                "candidate_count": len(retrieval.get("candidates") or []),
                "used_chars": retrieval.get("used_chars", 0),
                "final_plan_type": final_plan.get("type", ""),
                "output_dir": result.get("output_dir", ""),
                "ran_text": f"RAG run completed with {len(result.get('steps') or [])} steps",
                "rag_type": "run",
            },
        )

    def run_failed(self, *, error: str, step: str | None = None) -> dict[str, Any]:
        return self._record(
            title="RAG run failed",
            message=_preview(error),
            status="error",
            severity="error",
            tags=["run", "failed", "fault"],
            data={
                "error": _preview(error, limit=500),
                "step": step or "",
                "ran_text": f"RAG run failed at {step or 'unknown step'}",
                "rag_type": "run",
            },
        )

    def step_started(self, *, index: int, kind: str, input_data: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            title=f"RAG {kind.replace('_', ' ')} started",
            message=f"Step {index}: {kind}",
            status="running",
            tags=["step", kind],
            data={
                "step": kind,
                "step_index": index,
                "input": _compact_mapping(input_data),
                "running_text": f"RAG step {index} running: {kind}",
                "rag_type": _rag_type(kind),
            },
        )

    def step_completed(self, *, index: int, kind: str, output: Any) -> dict[str, Any]:
        summary = summarize_step_output(kind, output)
        return self._record(
            title=f"RAG {kind.replace('_', ' ')} completed",
            message=_step_message(kind, summary),
            status="completed",
            tags=["step", kind, "completed"],
            data={
                "step": kind,
                "step_index": index,
                "summary": summary,
                "ran_text": f"RAG step {index} completed: {_step_message(kind, summary)}",
                "rag_type": _rag_type(kind),
            },
        )

    def step_failed(self, *, index: int, kind: str, error: str) -> dict[str, Any]:
        return self._record(
            title=f"RAG {kind.replace('_', ' ')} failed",
            message=_preview(error),
            status="error",
            severity="error",
            tags=["step", kind, "failed", "fault"],
            data={
                "step": kind,
                "step_index": index,
                "error": _preview(error, limit=500),
                "ran_text": f"RAG step {index} failed: {kind}",
                "rag_type": _rag_type(kind),
            },
        )

    def model_call_started(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        input_chars: int,
    ) -> dict[str, Any]:
        return self._record(
            title="Local AI RAG call started",
            message=f"{provider}/{model} for {stage}",
            status="running",
            tags=["model-call", "ollama", "ai", stage],
            source="local-ai",
            data={
                "stage": stage,
                "provider": provider,
                "model": model,
                "input_chars": int(input_chars),
                "raw_thinking_exposed": False,
                "running_text": f"local AI model call for {stage}",
                "rag_type": "model_call"
            },
        )

    def model_call_completed(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        return self._record(
            title="Local AI RAG call completed",
            message=f"{provider}/{model} completed {stage}",
            status="completed",
            tags=["model-call", "ollama", "ai", stage, "completed"],
            source="local-ai",
            data={
                "stage": stage,
                "provider": provider,
                "model": model,
                "output_summary": summarize_step_output(stage, output),
                "raw_thinking_exposed": False,
                "ran_text": f"local AI model call completed for {stage}",
                "rag_type": "model_call",
            },
        )

    def model_call_failed(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        error: str,
    ) -> dict[str, Any]:
        return self._record(
            title="Local AI RAG call failed",
            message=_preview(error),
            status="error",
            severity="error",
            tags=["model-call", "ollama", "ai", stage, "failed", "fault"],
            source="local-ai",
            data={
                "stage": stage,
                "provider": provider,
                "model": model,
                "error": _preview(error, limit=500),
                "raw_thinking_exposed": False,
                "ran_text": f"local AI model call failed for {stage}",
                "rag_type": "model_call",
            },
        )
