#!/usr/bin/env python3
"""
Website Builder generated-editor RAG smoke.

This smoke is intentionally an adapter onto the generalized generated-editor
pathway.  It stages exactly one selected Website Builder site into an isolated
workspace, asks the generated-editor model path to decide the terminal class and
evidence from a bounded site index, and then treats --endstate only as the
postcondition oracle.

No live Website Builder source files are written by this smoke.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from website_builder_generated_editor_pipeline import (
        MODE,
        SmokeFailure,
        default_output_dir,
        detect_repo_root_arg,
        postcondition_result,
        run_generated_editor_pipeline,
        select_site,
        write_json,
    )
except ModuleNotFoundError:  # pragma: no cover - package import fallback
    from main_computer.website_builder_generated_editor_pipeline import (
        MODE,
        SmokeFailure,
        default_output_dir,
        detect_repo_root_arg,
        postcondition_result,
        run_generated_editor_pipeline,
        select_site,
        write_json,
    )


DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"
DEFAULT_AI_TIMEOUT_SECONDS = 600.0
DEFAULT_TERMINAL_NUM_PREDICT = 3000
DEFAULT_GROUNDING_NUM_PREDICT = 1600
DEFAULT_PATCH_NUM_PREDICT = 9000
DEFAULT_THINK_MODE = "false"


def normalize_ollama_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        text = DEFAULT_OLLAMA_BASE_URL
    if text.endswith("/api/generate"):
        return text
    return f"{text}/api/generate"


def read_prompt_from_args(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if args.prompt:
        return args.prompt.strip()
    if args.prompt_parts:
        return " ".join(args.prompt_parts).strip()
    return ""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Website Builder generated-editor RAG endstate smoke.")
    parser.add_argument("prompt_parts", nargs="*", help="Prompt text, if --prompt is not used.")
    parser.add_argument("--repo", help="Repository root. Defaults to walking up from cwd.")
    parser.add_argument("--site-id", help="Website Builder site id under runtime/websites/<site_id>/.")
    parser.add_argument("--prompt", help="Natural-language user prompt.")
    parser.add_argument("--prompt-file", help="Read natural-language user prompt from a file.")
    parser.add_argument(
        "--endstate",
        required=True,
        choices=["edit", "info"],
        help="Postcondition oracle only. It is not passed to the generated-editor decision stage.",
    )
    parser.add_argument("--output-dir", help="Diagnostics output directory.")
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--ollama-url", default=None, help="Full Ollama /api/generate URL. Overrides --ollama-base-url.")
    parser.add_argument("--ollama-model", "--model", dest="ollama_model", default=None)
    parser.add_argument(
        "--ai-timeout",
        "--ollama-timeout",
        dest="ai_timeout",
        type=float,
        default=DEFAULT_AI_TIMEOUT_SECONDS,
    )
    parser.add_argument("--terminal-num-predict", type=int, default=DEFAULT_TERMINAL_NUM_PREDICT)
    parser.add_argument("--grounding-num-predict", type=int, default=DEFAULT_GROUNDING_NUM_PREDICT)
    parser.add_argument("--patch-num-predict", type=int, default=DEFAULT_PATCH_NUM_PREDICT)
    parser.add_argument("--format-mode", choices=["none", "json"], default="none")
    parser.add_argument(
        "--think-mode",
        choices=["omit", "false", "true", "low", "medium", "high"],
        default=DEFAULT_THINK_MODE,
    )
    parser.add_argument("--max-index-files", type=int, default=40)
    parser.add_argument("--max-index-file-chars", type=int, default=24000)
    parser.add_argument("--excerpt-context-lines", type=int, default=24)
    parser.add_argument("--max-evidence-chars", type=int, default=18000)
    return parser


def failure_result(
    *,
    output_dir: Path,
    endstate: str | None,
    failed_stage: str,
    reason: str,
    site_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "mode": MODE,
        "endstate": endstate,
        "site_id": site_id,
        "failed_stage": failed_stage,
        "reason": reason,
        "output_dir": str(output_dir),
    }
    if details:
        result["details"] = details
    write_json(output_dir / "final_result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    output_dir: Path | None = None
    site_id: str | None = None

    try:
        repo = detect_repo_root_arg(args.repo)
        output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(repo)
        output_dir.mkdir(parents=True, exist_ok=True)

        prompt = read_prompt_from_args(args)
        request_report = {
            "mode": MODE,
            "endstate": args.endstate,
            "site_id": args.site_id,
            "prompt": prompt,
            "repo": str(repo),
            "output_dir": str(output_dir),
            "live_write": False,
            "endstate_usage": "postcondition_oracle_only",
        }
        write_json(output_dir / "request.json", request_report)

        if not prompt:
            result = failure_result(
                output_dir=output_dir,
                endstate=args.endstate,
                failed_stage="request_validation",
                reason="Provide --prompt, --prompt-file, or positional prompt text.",
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2

        selected = select_site(repo, args.site_id)
        site_id = selected["site_id"]

        model = args.ollama_model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        ollama_url = normalize_ollama_url(args.ollama_url or args.ollama_base_url)

        pipeline_report = run_generated_editor_pipeline(
            repo=repo,
            site_id=site_id,
            site_root=selected["site_root"],
            user_prompt=prompt,
            output_dir=output_dir,
            model=model,
            ollama_url=ollama_url,
            timeout_seconds=args.ai_timeout,
            terminal_num_predict=args.terminal_num_predict,
            grounding_num_predict=args.grounding_num_predict,
            patch_num_predict=args.patch_num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
            max_index_files=args.max_index_files,
            max_index_file_chars=args.max_index_file_chars,
            excerpt_context_lines=args.excerpt_context_lines,
            max_evidence_chars=args.max_evidence_chars,
        )

        final_result, exit_code = postcondition_result(
            declared_endstate=args.endstate,
            site_id=site_id,
            pipeline_report=pipeline_report,
            output_dir=output_dir,
        )
        print(json.dumps(final_result, indent=2, sort_keys=True))
        return exit_code

    except SmokeFailure as exc:
        if output_dir is None:
            try:
                repo = detect_repo_root_arg(args.repo)
                output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(repo)
            except Exception:
                output_dir = Path("diagnostics_output") / f"{MODE}-failed"
            output_dir.mkdir(parents=True, exist_ok=True)
        result = failure_result(
            output_dir=output_dir,
            endstate=getattr(args, "endstate", None),
            failed_stage=exc.failed_stage,
            reason=exc.reason,
            site_id=site_id,
            details=exc.details,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2

    except Exception as exc:
        if output_dir is None:
            try:
                repo = detect_repo_root_arg(args.repo)
                output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(repo)
            except Exception:
                output_dir = Path("diagnostics_output") / f"{MODE}-failed"
            output_dir.mkdir(parents=True, exist_ok=True)
        result = failure_result(
            output_dir=output_dir,
            endstate=getattr(args, "endstate", None),
            failed_stage="unhandled_exception",
            reason=f"{type(exc).__name__}: {exc}",
            site_id=site_id,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
