from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import traceback
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider, OllamaProvider, OpenAIProvider
from main_computer.rag_activity import RagActivityEmitter
from main_computer.rag_retriever import DeterministicRagRetriever, RagRetrieverConfig, normalize_queries, tokenize
from main_computer.thinking_models import RagHarnessResult, RagRetrievalResult, ThinkingStepRecord, utc_now_iso


DECOMPOSITION_PROMPT = """You are the RAG bootstrap planner for Main Computer.

Do not solve the user's task yet. Classify the request and decide what context should be retrieved.
Return JSON only with these keys:
{
  "task_type": "short snake_case type",
  "goal": "one sentence",
  "needs": ["context needed"],
  "retrieval_queries": ["literal search query"],
  "candidate_paths": ["repo-relative path when obvious"],
  "executor_likely_needed": false,
  "risk": "read_only_analysis | may_need_execution | may_need_writes | unknown"
}
"""

GROUNDED_PLAN_PROMPT = """You are creating a grounded plan from retrieved repository context.

Use only the supplied context. Do not claim that commands were run.
Return JSON only with these keys:
{
  "type": "plan | answer",
  "summary": "brief grounded summary",
  "evidence": [{"path": "repo-relative path", "reason": "why this source supports the plan"}],
  "next_step": {
    "kind": "proposal | none",
    "description": "safe next backend/front-end step",
    "requires_executor": false,
    "requires_approval": true
  },
  "open_questions": []
}
"""


def default_run_id() -> str:
    return "rag_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def run_rag_harness(
    *,
    prompt: str,
    repo_dir: Path | str = ".",
    queries: list[str] | str | None = None,
    upload_ids: list[str] | None = None,
    output_root: Path | str | None = None,
    max_context_chars: int = 30_000,
    max_candidates: int = 24,
    max_chunks: int = 12,
    use_model: bool = False,
    provider: LLMProvider | None = None,
    config: MainComputerConfig | None = None,
    run_id: str | None = None,
    activity: RagActivityEmitter | None = None,
    activity_bus: Any | None = None,
) -> RagHarnessResult:
    repo_path = Path(repo_dir).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"repo_dir does not exist or is not a directory: {repo_path}")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    run_id = run_id or default_run_id()
    output_dir = Path(output_root or (repo_path / "diagnostics_output" / "rag_runs")) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    steps: list[ThinkingStepRecord] = []
    model_provider: LLMProvider | None = provider
    if activity is None and activity_bus is not None:
        activity = RagActivityEmitter(activity_bus, run_id=run_id, prompt=prompt, repo_dir=str(repo_path))
    if activity is not None:
        activity.run_started(mode="model" if use_model else "no_model", use_model=use_model, output_dir=str(output_dir))

    def step(kind: str, input_data: dict[str, Any], func: Any) -> Any:
        index = len(steps) + 1
        started = utc_now_iso()
        if activity is not None:
            activity.step_started(index=index, kind=kind, input_data=input_data)

        emits_model_call = kind in {"task_decomposition", "grounded_plan"} and bool(use_model) and model_provider is not None
        if emits_model_call and activity is not None:
            activity.model_call_started(
                stage=kind,
                provider=getattr(model_provider, "name", "provider"),
                model=getattr(model_provider, "model", "model"),
                input_chars=int(input_data.get("grounded_prompt_chars") or len(str(input_data.get("prompt") or ""))),
            )
        try:
            output = func()
            output_payload = output if isinstance(output, dict) else {"value": output}
            steps.append(
                ThinkingStepRecord(
                    index=index,
                    kind=kind,
                    status="ok",
                    started_at=started,
                    completed_at=utc_now_iso(),
                    input=input_data,
                    output=output_payload,
                )
            )
            _write_json(output_dir / f"{index:02d}_{kind}.json", output_payload)
            if emits_model_call and activity is not None:
                activity.model_call_completed(
                    stage=kind,
                    provider=getattr(model_provider, "name", "provider"),
                    model=getattr(model_provider, "model", "model"),
                    output=output_payload,
                )
            if activity is not None:
                activity.step_completed(index=index, kind=kind, output=output_payload)
            return output
        except Exception as exc:
            error_payload = {"error": str(exc), "traceback": traceback.format_exc()}
            steps.append(
                ThinkingStepRecord(
                    index=index,
                    kind=kind,
                    status="error",
                    started_at=started,
                    completed_at=utc_now_iso(),
                    input=input_data,
                    output={},
                    error=str(exc),
                )
            )
            _write_json(output_dir / f"{index:02d}_{kind}.json", error_payload)
            if emits_model_call and activity is not None:
                activity.model_call_failed(
                    stage=kind,
                    provider=getattr(model_provider, "name", "provider"),
                    model=getattr(model_provider, "model", "model"),
                    error=str(exc),
                )
            if activity is not None:
                activity.step_failed(index=index, kind=kind, error=str(exc))
                activity.run_failed(error=str(exc), step=kind)
            raise

    intake = step(
        "intake",
        {"prompt": prompt, "repo_dir": str(repo_path), "upload_ids": upload_ids or []},
        lambda: {
            "prompt": prompt,
            "repo_dir": str(repo_path),
            "upload_ids": list(upload_ids or []),
            "mode": "model" if use_model else "no_model",
            "created_at": utc_now_iso(),
        },
    )

    if use_model and model_provider is None:
        config = config or MainComputerConfig.from_env()
        model_provider = _provider_from_config(config)

    task_decomposition = step(
        "task_decomposition",
        {"prompt": prompt, "use_model": bool(use_model)},
        lambda: _model_decomposition(model_provider, prompt) if use_model and model_provider is not None else deterministic_decomposition(prompt, queries),
    )

    retrieval_queries = normalize_queries(queries or task_decomposition.get("retrieval_queries") or prompt)
    if not retrieval_queries:
        retrieval_queries = normalize_queries(prompt)
    candidate_paths = [
        str(item)
        for item in task_decomposition.get("candidate_paths", [])
        if isinstance(item, str) and item.strip()
    ]

    config_for_roots = config or MainComputerConfig.from_env()
    executor_root = config_for_roots.executor_root
    if not executor_root.is_absolute():
        executor_root = repo_path / executor_root

    retriever = DeterministicRagRetriever(
        RagRetrieverConfig(
            repo_dir=repo_path,
            max_context_chars=max_context_chars,
            max_candidates=max_candidates,
            max_chunks=max_chunks,
        )
    )

    inventory = step(
        "context_inventory",
        {"upload_ids": upload_ids or [], "executor_root": str(executor_root)},
        lambda: retriever.inventory(upload_ids=upload_ids or [], executor_root=executor_root),
    )

    retrieval = step(
        "retrieval",
        {"queries": retrieval_queries, "candidate_paths": candidate_paths},
        lambda: retriever.retrieve(retrieval_queries, extra_paths=candidate_paths).as_dict(),
    )
    retrieval_result = _retrieval_from_dict(retrieval)

    context_chunks_payload = {
        "context_budget_chars": retrieval_result.context_budget_chars,
        "used_chars": retrieval_result.used_chars,
        "truncated_files": retrieval_result.truncated_files,
        "chunks": [chunk.as_dict() for chunk in retrieval_result.chunks],
    }
    _write_json(output_dir / "context_chunks.json", context_chunks_payload)

    context_brief = step(
        "context_brief",
        {"chunks": len(retrieval_result.chunks), "used_chars": retrieval_result.used_chars},
        lambda: build_context_brief(prompt=prompt, task_decomposition=task_decomposition, inventory=inventory, retrieval=retrieval_result),
    )

    grounded_prompt = build_grounded_prompt(
        prompt=prompt,
        task_decomposition=task_decomposition,
        inventory=inventory,
        retrieval=retrieval_result,
        context_brief=context_brief,
    )
    (output_dir / "grounded_prompt.txt").write_text(grounded_prompt, encoding="utf-8")

    final_plan = step(
        "grounded_plan",
        {"use_model": bool(use_model), "grounded_prompt_chars": len(grounded_prompt)},
        lambda: _model_grounded_plan(model_provider, grounded_prompt) if use_model and model_provider is not None else deterministic_final_plan(prompt, task_decomposition, retrieval_result, context_brief),
    )

    result = RagHarnessResult(
        ok=True,
        run_id=run_id,
        prompt=prompt,
        repo_dir=str(repo_path),
        output_dir=str(output_dir),
        no_model=not use_model,
        status="complete",
        task_decomposition=task_decomposition,
        inventory=inventory,
        retrieval=retrieval_result,
        context_brief=context_brief,
        final_plan=final_plan,
        steps=steps,
        error=None,
    )
    _write_json(output_dir / "run.json", result.as_dict())
    _write_json(output_dir / "final_plan.json", final_plan)
    if activity is not None:
        activity.run_completed(status=result.status, result=result.as_dict())
    return result


def deterministic_decomposition(prompt: str, queries: list[str] | str | None = None) -> dict[str, Any]:
    tokens = set(tokenize(prompt))
    task_type = "general_repository_question"
    if {"rag", "retrieval"}.intersection(tokens):
        task_type = "rag_bootstrap_review"
    elif {"docker", "executor", "container"}.intersection(tokens):
        task_type = "executor_backend_review"
    elif {"test", "tests", "harness"}.intersection(tokens):
        task_type = "test_harness_review"
    elif {"frontend", "ui"}.intersection(tokens):
        task_type = "frontend_integration_review"

    retrieval_queries = normalize_queries(queries or [])
    if not retrieval_queries:
        retrieval_queries = _queries_from_prompt(prompt)

    candidate_paths = []
    for token in tokens:
        if token in {"rag", "retriever", "retrieval"}:
            candidate_paths.extend(["main_computer/rag_retriever.py", "main_computer/rag_harness.py"])
        if token in {"executor", "docker", "container"}:
            candidate_paths.extend([
                "main_computer/docker_executor.py",
                "main_computer/executor_tool_loop.py",
                "main_computer/viewport_routes_executor.py",
                "tests/test_executor_tool_loop.py",
            ])
        if token in {"server", "route", "routes"}:
            candidate_paths.extend(["main_computer/viewport_server.py"])
        if token in {"config", "environment"}:
            candidate_paths.extend(["main_computer/config.py", "README.md", "ENVIRONMENT.md"])
        if token in {"test", "tests", "harness"}:
            candidate_paths.extend(["tests/test_rag_retriever.py", "tests/test_rag_harness.py"])

    candidate_paths = _dedupe(candidate_paths)

    return {
        "task_type": task_type,
        "goal": f"Retrieve grounded repository context for: {prompt}",
        "needs": [
            "repo file inventory",
            "candidate source files",
            "related tests",
            "README/TODO/ENVIRONMENT context",
            "upload metadata when provided",
        ],
        "retrieval_queries": retrieval_queries,
        "candidate_paths": candidate_paths,
        "executor_likely_needed": any(token in tokens for token in {"docker", "execute", "executor", "container", "upload", "file"}),
        "risk": "read_only_analysis",
        "mode": "deterministic",
    }


def build_context_brief(
    *,
    prompt: str,
    task_decomposition: dict[str, Any],
    inventory: dict[str, Any],
    retrieval: RagRetrievalResult,
) -> dict[str, Any]:
    evidence = []
    for candidate in retrieval.candidates[:8]:
        evidence.append(
            {
                "path": candidate.path,
                "score": candidate.score,
                "reason": candidate.reason,
            }
        )
    facts = []
    for chunk in retrieval.chunks[:8]:
        facts.append(
            {
                "path": chunk.path,
                "lines": f"{chunk.start_line}-{chunk.end_line}",
                "reason": chunk.reason,
            }
        )
    return {
        "summary": "Deterministic RAG selected repository context for a grounded planning call.",
        "task_type": task_decomposition.get("task_type", "unknown"),
        "file_count": inventory.get("file_count", 0),
        "uploads": inventory.get("uploads", []),
        "queries": retrieval.queries,
        "context_budget_chars": retrieval.context_budget_chars,
        "used_chars": retrieval.used_chars,
        "truncated_files": retrieval.truncated_files,
        "evidence": evidence,
        "facts": facts,
        "open_questions": [
            "Should the next stage answer from retrieved context or propose an executor command?",
            "Does the frontend need an approval card before any tool execution?",
        ],
    }


def build_grounded_prompt(
    *,
    prompt: str,
    task_decomposition: dict[str, Any],
    inventory: dict[str, Any],
    retrieval: RagRetrievalResult,
    context_brief: dict[str, Any],
) -> str:
    chunks_text = []
    for chunk in retrieval.chunks:
        chunks_text.append(
            "\n".join(
                [
                    f"--- SOURCE {chunk.path} lines {chunk.start_line}-{chunk.end_line} score={chunk.score} ---",
                    chunk.content,
                ]
            )
        )

    return "\n\n".join(
        [
            GROUNDED_PLAN_PROMPT,
            "USER PROMPT:\n" + prompt,
            "TASK DECOMPOSITION:\n" + json.dumps(task_decomposition, indent=2, ensure_ascii=False),
            "CONTEXT INVENTORY:\n" + json.dumps(inventory, indent=2, ensure_ascii=False),
            "CONTEXT BRIEF:\n" + json.dumps(context_brief, indent=2, ensure_ascii=False),
            "RETRIEVED CONTEXT:\n" + "\n\n".join(chunks_text),
        ]
    )


def deterministic_final_plan(
    prompt: str,
    task_decomposition: dict[str, Any],
    retrieval: RagRetrievalResult,
    context_brief: dict[str, Any],
) -> dict[str, Any]:
    evidence = [
        {
            "path": candidate.path,
            "reason": candidate.reason,
            "score": candidate.score,
        }
        for candidate in retrieval.candidates[:6]
    ]
    requires_executor = bool(task_decomposition.get("executor_likely_needed")) and any(
        token in set(tokenize(prompt))
        for token in {"execute", "run", "inspect", "analyze", "upload", "zip", "csv", "compute"}
    )
    return {
        "type": "plan",
        "summary": (
            "RAG bootstrap completed in deterministic mode. "
            f"Selected {len(retrieval.chunks)} context chunks from {len(retrieval.candidates)} candidate files."
        ),
        "evidence": evidence,
        "next_step": {
            "kind": "proposal",
            "description": "Review retrieved context and decide whether to request an executor tool proposal.",
            "requires_executor": requires_executor,
            "requires_approval": True,
        },
        "open_questions": context_brief.get("open_questions", []),
        "mode": "deterministic",
    }


def _provider_from_config(config: MainComputerConfig) -> LLMProvider:
    if config.provider == "ollama":
        return OllamaProvider(
            model=config.model,
            base_url=config.ollama_base_url,
            timeout_s=config.ollama_timeout_s,
            fallback=config.fallback,
        )
    if config.provider == "openai":
        return OpenAIProvider(model=config.model, base_url=config.openai_base_url, fallback=config.fallback)
    raise ValueError(f"Unknown provider: {config.provider}")


def _model_decomposition(provider: LLMProvider, prompt: str) -> dict[str, Any]:
    response = provider.chat(
        [
            ChatMessage(role="system", content=DECOMPOSITION_PROMPT),
            ChatMessage(role="user", content=prompt),
        ]
    )
    parsed = parse_json_object(response.content)
    parsed.setdefault("provider", response.provider)
    parsed.setdefault("model", response.model)
    parsed.setdefault("mode", "model")
    parsed.setdefault("retrieval_queries", _queries_from_prompt(prompt))
    parsed.setdefault("candidate_paths", [])
    return parsed


def _model_grounded_plan(provider: LLMProvider, grounded_prompt: str) -> dict[str, Any]:
    response = provider.chat(
        [
            ChatMessage(role="system", content=GROUNDED_PLAN_PROMPT),
            ChatMessage(role="user", content=grounded_prompt),
        ]
    )
    parsed = parse_json_object(response.content)
    parsed.setdefault("provider", response.provider)
    parsed.setdefault("model", response.model)
    parsed.setdefault("mode", "model")
    return parsed


def parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Model returned empty content.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Expected model to return a JSON object.")
    return data


def _queries_from_prompt(prompt: str) -> list[str]:
    tokens = tokenize(prompt)
    weighted = []
    for token in tokens:
        if token in {"the", "and", "that", "with", "for", "into", "from", "this", "what", "need"}:
            continue
        weighted.append(token)
    queries = []
    joined = " ".join(weighted[:5]).strip()
    if joined:
        queries.append(joined)
    for token in weighted:
        if token not in queries:
            queries.append(token)
        if len(queries) >= 8:
            break
    return queries or [prompt[:120]]


def _retrieval_from_dict(data: dict[str, Any]) -> RagRetrievalResult:
    from main_computer.thinking_models import RagCandidate, RagChunk

    return RagRetrievalResult(
        queries=list(data.get("queries") or []),
        scanned_files=int(data.get("scanned_files") or 0),
        candidates=[
            RagCandidate(
                path=str(item.get("path") or ""),
                score=float(item.get("score") or 0),
                reason=str(item.get("reason") or ""),
                matches=[int(value) for value in item.get("matches", [])],
                size=int(item.get("size") or 0),
            )
            for item in data.get("candidates", [])
            if isinstance(item, dict)
        ],
        chunks=[
            RagChunk(
                path=str(item.get("path") or ""),
                start_line=int(item.get("start_line") or 0),
                end_line=int(item.get("end_line") or 0),
                chars=int(item.get("chars") or 0),
                score=float(item.get("score") or 0),
                reason=str(item.get("reason") or ""),
                content=str(item.get("content") or ""),
                truncated=bool(item.get("truncated")),
            )
            for item in data.get("chunks", [])
            if isinstance(item, dict)
        ],
        context_budget_chars=int(data.get("context_budget_chars") or 0),
        used_chars=int(data.get("used_chars") or 0),
        truncated_files=[str(item) for item in data.get("truncated_files", [])],
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Main Computer deterministic/model-backed RAG bootstrap harness.")
    parser.add_argument("--prompt", required=True, help="User request to bootstrap through RAG.")
    parser.add_argument("--repo-dir", default=".", help="Repository root to scan.")
    parser.add_argument("--queries", default="", help="Comma/newline separated retrieval queries. Defaults to prompt-derived queries.")
    parser.add_argument("--upload-id", action="append", default=[], help="Executor upload id to include in context inventory. May be repeated.")
    parser.add_argument("--output-root", default="", help="Directory for rag_runs. Defaults to <repo>/diagnostics_output/rag_runs.")
    parser.add_argument("--max-context-chars", type=int, default=30_000)
    parser.add_argument("--max-candidates", type=int, default=24)
    parser.add_argument("--max-chunks", type=int, default=12)
    parser.add_argument("--use-model", action="store_true", help="Use the configured provider for decomposition and grounded planning.")
    parser.add_argument("--no-model", action="store_true", help="Force deterministic no-model mode. This is the default.")
    parser.add_argument("--run-id", default="", help="Optional explicit run id for repeatable tests.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    try:
        result = run_rag_harness(
            prompt=args.prompt,
            repo_dir=args.repo_dir,
            queries=normalize_queries(args.queries) if args.queries else None,
            upload_ids=args.upload_id,
            output_root=Path(args.output_root) if args.output_root else None,
            max_context_chars=args.max_context_chars,
            max_candidates=args.max_candidates,
            max_chunks=args.max_chunks,
            use_model=bool(args.use_model and not args.no_model),
            run_id=args.run_id or None,
        )
    except Exception as exc:
        print(f"rag harness failed: {exc}", file=sys.stderr)
        return 1

    print(f"RAG bootstrap run: {result.run_id}")
    print(f"Output: {result.output_dir}")
    print(f"Mode: {'no-model' if result.no_model else 'model'}")
    print(f"Task type: {result.task_decomposition.get('task_type', 'unknown')}")
    print(
        "Retrieval: "
        f"{result.retrieval.scanned_files} files scanned, "
        f"{len(result.retrieval.candidates)} candidates, "
        f"{len(result.retrieval.chunks)} chunks, "
        f"{result.retrieval.used_chars}/{result.retrieval.context_budget_chars} chars"
    )
    print(f"Final plan: {result.final_plan.get('type', 'plan')} - {result.final_plan.get('summary', '')}")
    print(f"Artifacts: {Path(result.output_dir) / 'final_plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
