#!/usr/bin/env python3
"""Compare local Ollama models using the repo's known-good RAG generate stream path.

This is intentionally not a generic Ollama client and not the main
OllamaProvider wrapper. It uses the same raw streaming helper used by the RAG
gremlin smoke path:

    main_computer.rag_gremlin_pyramid_atom_smoke.call_ollama_generate_streaming

That helper posts to /api/generate, forces stream=true, applies the repo's
generate think policy through prepare_ollama_generate_payload(), and reads the
Ollama JSONL stream byte-by-byte. The byte-by-byte read is important on local
Windows/Ollama setups where HTTPResponse iteration can miss intermediate
stream chunks.

Default contest:

    qwen3.8:27b vs gemma4:26b

By default, this script now benchmarks in grouped model order with a warmup
request and keep_alive set. The summary reports both end-to-end timing and
loaded-model timing, where loaded_total_s = total_duration - load_duration.

Run from repo root:

    python scripts/smoke_ollama_models.py
    python scripts/smoke_ollama_models.py --twiddle-only
    python scripts/smoke_ollama_models.py --models qwen3.8:27b gemma4:26b --runs 2
    python scripts/smoke_ollama_models.py --hardness 4:11
    python scripts/smoke_ollama_models.py --hardness 7:12 --runs 1

Outputs are written under diagnostics_output/ollama_model_contest/.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
import traceback
from typing import Any, Callable
import urllib.error
import urllib.request


SCRIPT_VERSION = "6.0.1"
DEFAULT_MODELS = ["qwen3.8:27b", "gemma4:26b"]
DEFAULT_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_OUT_ROOT = Path("diagnostics_output") / "ollama_model_contest"


_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass(frozen=True)
class SmokeTask:
    stage: int
    name: str
    prompt: str
    num_predict: int
    scorer: Callable[[str], tuple[int, int, str]]


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_name(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    return "_".join(parts).lower() or "item"


def ns_to_s(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 1_000_000_000.0
    except Exception:
        return None


def tps(eval_count: Any, eval_duration_ns: Any) -> float | None:
    try:
        count = float(eval_count)
        duration = float(eval_duration_ns)
    except Exception:
        return None
    if count <= 0 or duration <= 0:
        return None
    return count / duration * 1_000_000_000.0


def ms_per_token(eval_count: Any, eval_duration_ns: Any) -> float | None:
    try:
        count = float(eval_count)
        duration = float(eval_duration_ns)
    except Exception:
        return None
    if count <= 0 or duration <= 0:
        return None
    return duration / count / 1_000_000.0


def subtract_nonnegative(total_s: Any, load_s: Any) -> float | None:
    if not isinstance(total_s, (int, float)):
        return None
    if not isinstance(load_s, (int, float)):
        return float(total_s)
    return max(0.0, float(total_s) - float(load_s))


def pct(part: Any, whole: Any) -> float | None:
    if not isinstance(part, (int, float)) or not isinstance(whole, (int, float)):
        return None
    if float(whole) <= 0:
        return None
    return float(part) / float(whole) * 100.0


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_generate_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        text = DEFAULT_GENERATE_URL
    if text.endswith("/api/generate"):
        return text
    if text.endswith("/api/chat"):
        return text[: -len("/api/chat")] + "/api/generate"
    if text.endswith("/api/tags") or text.endswith("/api/show") or text.endswith("/api/version"):
        return text.rsplit("/api/", 1)[0] + "/api/generate"
    return text + "/api/generate"


def base_url_from_generate_url(generate_url: str) -> str:
    text = str(generate_url or "").strip().rstrip("/")
    if text.endswith("/api/generate"):
        return text[: -len("/api/generate")]
    return text


def request_json(
    *,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(format_http_error(exc)) from exc
    parsed = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{url} returned {type(parsed).__name__}, not JSON object")
    return parsed


def read_http_error_body(exc: urllib.error.HTTPError, limit: int = 64 * 1024) -> str:
    try:
        body = exc.read(limit + 1)
    except Exception as body_exc:
        return f"<could not read HTTP error body: {type(body_exc).__name__}: {body_exc}>"
    text = body.decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def format_http_error(exc: urllib.error.HTTPError) -> str:
    body = read_http_error_body(exc)
    body_part = f" body={body!r}" if body else ""
    return f"HTTP {exc.code} {exc.reason}.{body_part}"


def strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", str(text), flags=re.I | re.S).strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = strip_think_blocks(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def score_exact_ready(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text)
    first = cleaned.splitlines()[0].strip() if cleaned else ""
    if first == "READY":
        return 2, 2, "exact READY"
    if "READY" in cleaned:
        return 1, 2, "contains READY but not exact"
    return 0, 2, "missing READY"


def score_json_config(text: str) -> tuple[int, int, str]:
    obj = extract_json_object(text)
    if obj is None:
        return 0, 5, "invalid JSON object"

    checks = [
        (obj.get("env_var") == "MAIN_COMPUTER_MODEL", "env_var"),
        (obj.get("python_default") == "gemma4:26b", "python_default"),
        (obj.get("compose_default") == "qwen2.5:1.5b", "compose_default"),
        ("mismatch" in str(obj.get("problem", "")).lower(), "problem mentions mismatch"),
        (
            "gemma4:26b" in str(obj.get("recommendation", "")).lower()
            and "qwen2.5" in str(obj.get("recommendation", "")).lower(),
            "recommendation names both defaults",
        ),
    ]
    score = sum(1 for ok, _ in checks if ok)
    missing = [label for ok, label in checks if not ok]
    return score, len(checks), "missing: " + ", ".join(missing) if missing else "good config extraction"


def score_choose_faster_model_code(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text)
    checks = [
        ("def choose_faster_model" in cleaned, "defines function"),
        ("quality_pct" in cleaned and "80" in cleaned, "quality threshold"),
        ("median_wall_s" in cleaned, "uses wall time"),
        ("None" in cleaned or "is not None" in cleaned, "handles None"),
        ("ValueError" in cleaned, "raises ValueError"),
        ("min(" in cleaned or "sorted(" in cleaned or "<" in cleaned, "selects lowest wall time"),
    ]
    score = sum(1 for ok, _ in checks if ok)
    missing = [label for ok, label in checks if not ok]
    return score, len(checks), "missing: " + ", ".join(missing) if missing else "good static code coverage"


def score_repo_design_plan(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        (any(s in cleaned for s in ["repo-local", "local config", ".main-computer", "local.toml"]), "repo-local config"),
        ("main_computer_model" in cleaned or "environment variable" in cleaned or "env var" in cleaned, "env precedence"),
        ("cli" in cleaned or "--model" in cleaned, "CLI override"),
        ("fallback" in cleaned or "default" in cleaned, "tracked fallback"),
        ("gitignore" in cleaned or "gitignored" in cleaned or "not edit tracked" in cleaned, "does not mutate tracked default"),
        (any(s in cleaned for s in ["/api/tags", "installed", "ollama list", "doctor"]), "validates installed models"),
        ("resolver" in cleaned or "resolve" in cleaned, "single resolver"),
        ("test" in cleaned and "precedence" in cleaned, "precedence tests"),
    ]
    score = sum(1 for ok, _ in checks if ok)
    missing = [label for ok, label in checks if not ok]
    return score, len(checks), "missing: " + ", ".join(missing) if missing else "good design coverage"


def score_patch_risk_review(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("mismatch" in cleaned and ("source" in cleaned or "truth" in cleaned), "source-of-truth mismatch"),
        ("docker" in cleaned and "compose" in cleaned, "docker compose"),
        (any(s in cleaned for s in ["readme", "environment", "docs"]), "docs/readme/environment"),
        ("baseline" in cleaned and ("do not" in cleaned or "not change" in cleaned or "leave" in cleaned), "do not touch eval baseline"),
        ("grep" in cleaned or "search" in cleaned or "verify" in cleaned, "verification search"),
        ("narrow" in cleaned or "only" in cleaned or "changed files" in cleaned, "narrow patch"),
    ]
    score = sum(1 for ok, _ in checks if ok)
    missing = [label for ok, label in checks if not ok]
    return score, len(checks), "missing: " + ", ".join(missing) if missing else "good risk review"



def score_keyword_rubric(text: str, checks: list[tuple[bool, str]], good_note: str) -> tuple[int, int, str]:
    score = sum(1 for ok, _ in checks if ok)
    missing = [label for ok, label in checks if not ok]
    return score, len(checks), "missing: " + ", ".join(missing) if missing else good_note


def score_provider_http_error_diagnosis(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("500" in cleaned or "internal server" in cleaned, "identifies HTTP 500"),
        ("body" in cleaned or "response" in cleaned, "preserves HTTP response body"),
        ("not" in cleaned and ("unreachable" in cleaned or "could not reach" in cleaned), "does not call it unreachable"),
        ("pull" in cleaned and ("not" in cleaned or "misleading" in cleaned), "does not blame missing pull"),
        ("--load-mode" in cleaned or "load-mode" in cleaned, "mentions load-mode runner failure"),
        ("llama-server" in cleaned or "runner" in cleaned, "mentions llama-server/runner"),
        ("mask" in cleaned or "wrap" in cleaned or "misleading" in cleaned, "provider masks root cause"),
        ("httperror" in cleaned or "http error" in cleaned, "handles HTTPError separately"),
        ("connection" in cleaned and "server" in cleaned, "separates connection from server errors"),
        ("test" in cleaned or "fixture" in cleaned or "fake" in cleaned, "asks for regression test"),
    ]
    return score_keyword_rubric(text, checks, "good provider error diagnosis")


def score_rag_route_decision(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("/api/generate" in cleaned, "uses /api/generate"),
        ("stream" in cleaned and "true" in cleaned, "stream=true"),
        ("byte" in cleaned or "jsonl" in cleaned, "byte/jsonl streaming"),
        ("rag_gremlin_pyramid_atom_smoke" in cleaned or "call_ollama_generate_streaming" in cleaned, "names existing RAG helper"),
        ("/api/chat" in cleaned and ("not" in cleaned or "avoid" in cleaned or "provider" in cleaned), "distinguishes provider chat route"),
        ("tags" in cleaned or "/api/tags" in cleaned, "preflights installed models"),
        ("show" in cleaned or "/api/show" in cleaned, "uses show metadata"),
        ("error body" in cleaned or "body" in cleaned, "keeps useful error body"),
        ("twiddle" in cleaned or "minimal" in cleaned or "probe" in cleaned, "has narrow twiddle/probe"),
        ("benchmark" in cleaned and ("after" in cleaned or "only" in cleaned or "preflight" in cleaned), "benchmarks only after route works"),
    ]
    return score_keyword_rubric(text, checks, "good route decision")


def score_local_model_resolver(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("--model" in cleaned or "cli" in cleaned, "CLI override"),
        ("main_computer_model" in cleaned, "MAIN_COMPUTER_MODEL env"),
        (".main-computer" in cleaned or "local.toml" in cleaned or "repo-local" in cleaned, "repo-local config file"),
        ("gitignore" in cleaned or "gitignored" in cleaned or "untracked" in cleaned, "untracked local state"),
        ("default_ollama_model" in cleaned or "gemma4:26b" in cleaned or "tracked default" in cleaned, "tracked fallback"),
        ("precedence" in cleaned and ("test" in cleaned or "tests" in cleaned), "precedence tests"),
        ("resolver" in cleaned or "resolve_model" in cleaned or "single" in cleaned, "single resolver"),
        ("ollama" in cleaned and ("tags" in cleaned or "list" in cleaned or "installed" in cleaned), "installed-model validation"),
        ("doctor" in cleaned or "current" in cleaned or "set" in cleaned or "reset" in cleaned, "user-facing commands"),
        ("no" in cleaned and ("tracked source" in cleaned or "edit tracked" in cleaned or "commit local" in cleaned), "does not edit tracked source for local preference"),
    ]
    return score_keyword_rubric(text, checks, "good local resolver plan")


def score_scope_gate_response(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("overloaded" in cleaned, "calls overloaded"),
        ("short" in cleaned and ("circuit" in cleaned or "stop" in cleaned), "short-circuits implementation"),
        ("scope" in cleaned and ("gate" in cleaned or "verdict" in cleaned), "uses scope verdict"),
        ("slice" in cleaned or "slices" in cleaned, "decomposes into slices"),
        ("acceptance" in cleaned or "proof" in cleaned or "verification" in cleaned, "acceptance proof"),
        ("artifact" in cleaned and ("boundary" in cleaned or "changed files" in cleaned or "overlay" in cleaned), "artifact boundary"),
        ("dependencies" in cleaned or "dependency" in cleaned or "order" in cleaned, "dependency ordered"),
        ("smallest" in cleaned or "first" in cleaned, "identifies first slice"),
        ("do not" in cleaned and ("patch" in cleaned or "artifact" in cleaned), "does not create broad patch"),
        ("work" in cleaned and ("chat" in cleaned or "handoff" in cleaned), "gives Chat/Work routes"),
    ]
    return score_keyword_rubric(text, checks, "good scope-gate response")


def score_inventory_json_review(text: str) -> tuple[int, int, str]:
    obj = extract_json_object(text)
    if obj is None:
        return 0, 10, "invalid JSON object"

    serialized = json.dumps(obj, sort_keys=True).lower()
    checks = [
        ("docker-compose.dev.yml" in serialized and "change" in serialized, "changes compose fallback"),
        ("readme.md" in serialized and "change" in serialized, "changes README default doc"),
        ("environment.md" in serialized and "change" in serialized, "changes ENVIRONMENT doc"),
        ("docker/dev/readme.md" in serialized and "change" in serialized, "changes docker dev README"),
        ("rag_advanced_eval_layer_smoke.py" in serialized and ("leave" in serialized or "do_not_change" in serialized or "do not" in serialized), "leaves synthetic eval baseline"),
        ("pretty_docs" in serialized and ("leave" in serialized or "defer" in serialized or "out_of_scope" in serialized), "does not blindly rewrite archived docs"),
        ("grep" in serialized or "search" in serialized, "verification search"),
        ("new_patch.py" in serialized or "dry-run" in serialized, "new_patch dry run"),
        ("source" in serialized and "truth" in serialized, "source-of-truth rationale"),
        ("narrow" in serialized or "changed_files" in serialized or "only" in serialized, "narrow artifact"),
    ]
    return score_keyword_rubric(text, checks, "good JSON inventory review")


def score_agentic_patch_plan(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("config" in cleaned and ("resolver" in cleaned or "resolve" in cleaned), "config resolver"),
        ("cli" in cleaned or "--model" in cleaned, "CLI integration"),
        ("tests" in cleaned or "pytest" in cleaned, "tests"),
        ("precedence" in cleaned, "precedence logic"),
        ("installed" in cleaned and "model" in cleaned, "installed model validation"),
        ("gitignore" in cleaned or "untracked" in cleaned or "local" in cleaned, "local preference not committed"),
        ("migration" in cleaned or "backward" in cleaned or "compatible" in cleaned, "migration/backward compatibility"),
        ("docs" in cleaned or "readme" in cleaned, "docs update"),
        ("new_patch.py" in cleaned or "dry-run" in cleaned or "overlay" in cleaned, "patch artifact verification"),
        ("narrow" in cleaned or "minimal" in cleaned or "do not refactor" in cleaned, "bounded implementation"),
        ("smoke" in cleaned or "doctor" in cleaned, "smoke/doctor check"),
    ]
    return score_keyword_rubric(text, checks, "good agentic patch plan")


def score_benchmark_decision(text: str) -> tuple[int, int, str]:
    cleaned = strip_think_blocks(text).lower()
    checks = [
        ("gemma4:26b" in cleaned and ("default" in cleaned or "winner" in cleaned), "keeps gemma default"),
        ("qwen3.8:27b" in cleaned and ("optional" in cleaned or "experiment" in cleaned or "deep" in cleaned), "keeps qwen as experiment"),
        ("loaded" in cleaned and ("speed" in cleaned or "total" in cleaned), "uses loaded-speed metric"),
        ("eval_tps" in cleaned or "tokens/sec" in cleaned or "tok/s" in cleaned, "uses token throughput"),
        ("quality" in cleaned and ("45/54" in cleaned or "42/54" in cleaned or "rubric" in cleaned), "uses quality rubric"),
        ("thinking" in cleaned or "think" in cleaned, "calls for thinking-enabled eval"),
        ("fixed" in cleaned and ("token" in cleaned or "continuation" in cleaned), "asks for fixed-token continuation"),
        ("karol" in cleaned or "hype" in cleaned or "model class" in cleaned, "separates hype/model class from local default"),
        ("cold" in cleaned or "load" in cleaned, "separates cold load"),
        ("repo" in cleaned and ("default" in cleaned or "workload" in cleaned), "repo-specific conclusion"),
    ]
    return score_keyword_rubric(text, checks, "good benchmark decision")


def build_tasks() -> list[SmokeTask]:
    return [
        SmokeTask(
            stage=1,
            name="tiny_instruction_following",
            num_predict=16,
            prompt="Reply with exactly one word: READY",
            scorer=score_exact_ready,
        ),
        SmokeTask(
            stage=2,
            name="config_mismatch_json",
            num_predict=220,
            prompt=(
                "You are checking a repo's Ollama configuration.\n\n"
                "Facts:\n"
                '- main_computer/config.py has DEFAULT_OLLAMA_MODEL = "gemma4:26b"\n'
                "- environment variable override is MAIN_COMPUTER_MODEL\n"
                '- docker-compose.dev.yml incorrectly falls back to "qwen2.5:1.5b"\n\n'
                "Return only valid JSON with exactly these keys:\n"
                '{"env_var":"","python_default":"","compose_default":"","problem":"","recommendation":""}'
            ),
            scorer=score_json_config,
        ),
        SmokeTask(
            stage=3,
            name="small_coding_task",
            num_predict=420,
            prompt=(
                "Write only Python code. No markdown.\n\n"
                "Implement:\n\n"
                "def choose_faster_model(results: list[dict]) -> str:\n"
                "    ...\n\n"
                "Requirements:\n"
                '- Each result has "model", "median_wall_s", and "quality_pct".\n'
                "- Ignore rows where median_wall_s is None.\n"
                "- Only consider models with quality_pct >= 80.\n"
                "- Return the model with the lowest median_wall_s.\n"
                "- Raise ValueError if no model qualifies.\n"
            ),
            scorer=score_choose_faster_model_code,
        ),
        SmokeTask(
            stage=4,
            name="repo_model_selection_design",
            num_predict=700,
            prompt=(
                "Hardness 4. Use deliberate reasoning if thinking is enabled, then give the final answer.\n\n"
                "We want a better way to select the default Ollama model in a checked-out repo.\n\n"
                "Current behavior:\n"
                '- Python code has DEFAULT_OLLAMA_MODEL = "gemma4:26b".\n'
                "- MAIN_COMPUTER_MODEL can override it.\n"
                "- Some dev/docs defaults accidentally used qwen2.5:1.5b.\n"
                "- We want each checkout to remember its own preferred model without editing tracked source files.\n\n"
                "Give a concise implementation plan with precedence order, where the repo-local setting should live, "
                "CLI commands, validation against installed Ollama models, and tests."
            ),
            scorer=score_repo_design_plan,
        ),
        SmokeTask(
            stage=5,
            name="patch_risk_review",
            num_predict=650,
            prompt=(
                "Hardness 5. Use deliberate reasoning if thinking is enabled, then give the final answer.\n\n"
                "Review this proposed cleanup before a patch:\n\n"
                "Goal:\n"
                "Align incorrect qwen2.5:1.5b references to gemma4:26b, because config.py says gemma4:26b is the default.\n\n"
                "Observed files:\n"
                "- docker-compose.dev.yml contains qwen2.5:1.5b as a fallback.\n"
                "- README.md contains qwen2.5:1.5b as an example default.\n"
                "- ENVIRONMENT.md contains qwen2.5:1.5b as an example default.\n"
                "- docker/dev/README.md tells users to pull qwen2.5:1.5b.\n"
                "- main_computer/rag_advanced_eval_layer_smoke.py contains qwen2.5 as a synthetic baseline_model.\n\n"
                "Say what should change, what should not change, and how to verify the patch."
            ),
            scorer=score_patch_risk_review,
        ),
        SmokeTask(
            stage=6,
            name="provider_http_error_diagnosis",
            num_predict=900,
            prompt=(
                "Hardness 6. Use thinking if enabled. Final answer should be concise but complete.\n\n"
                "You are debugging a local Ollama failure. Evidence:\n"
                "- The Ollama desktop UI returns: 500 Internal Server Error: llama-server process has terminated: "
                "exit status 1: error: invalid argument: --load-mode.\n"
                "- The repo's OllamaProvider catches urllib.error.HTTPError 500 inside _chat_streaming.\n"
                "- The provider then raises RuntimeError('Could not reach Ollama at http://localhost:11434. "
                "Start Ollama and pull qwen3.8:27b.').\n"
                "- /api/tags and /api/show can see the models.\n\n"
                "Diagnose the real failure, explain why the provider message is misleading, and propose the minimal "
                "code/test fix to preserve the useful HTTP error body while still handling true connection failures."
            ),
            scorer=score_provider_http_error_diagnosis,
        ),
        SmokeTask(
            stage=7,
            name="rag_route_vs_provider_route_decision",
            num_predict=950,
            prompt=(
                "Hardness 7. Use thinking if enabled. Final answer should make a route decision.\n\n"
                "The repo has two possible ways to probe Ollama:\n"
                "A. main_computer.providers.ollama.OllamaProvider.chat(), which uses /api/chat and currently masks "
                "HTTP 500 bodies behind a generic could-not-reach message.\n"
                "B. main_computer.rag_gremlin_pyramid_atom_smoke.call_ollama_generate_streaming(), which uses "
                "/api/generate, stream=true, the repo prepare_ollama_generate_payload policy, and a byte-by-byte "
                "JSONL streaming read. Existing RAG smoke scripts use this route successfully.\n\n"
                "Design the smallest trustworthy smoke/twiddle path for comparing local Ollama models. Include "
                "preflight checks, route choice, what to log, and when to stop before benchmarking."
            ),
            scorer=score_rag_route_decision,
        ),
        SmokeTask(
            stage=8,
            name="local_model_resolver_design",
            num_predict=1050,
            prompt=(
                "Hardness 8. Use thinking if enabled. Final answer should be implementation-oriented.\n\n"
                "Design a repo-local Ollama model resolver so every checkout can remember its preferred default "
                "without editing tracked source. Requirements:\n"
                "- one-off CLI override must win\n"
                "- MAIN_COMPUTER_MODEL must still work\n"
                "- repo-local config should be gitignored\n"
                "- tracked source default remains a safe fallback\n"
                "- users need commands to inspect/set/reset/doctor the current model\n"
                "- invalid or missing local models should be detected against Ollama's installed model list\n"
                "- tests must prove precedence and no accidental tracked-source mutation\n\n"
                "Give file-level implementation steps and tests."
            ),
            scorer=score_local_model_resolver,
        ),
        SmokeTask(
            stage=9,
            name="scope_gate_overload_decomposition",
            num_predict=1050,
            prompt=(
                "Hardness 9. Use thinking if enabled. Final answer should apply a patch-operator scope gate.\n\n"
                "A user uploads a zip snapshot and asks: 'Finish everything remaining: audit the entire repo, redesign "
                "model defaults, fix all docs, migrate old configs, update Docker, implement CLI, add tests, prove the "
                "release, and package it.'\n\n"
                "Respond as a patch operator. Decide whether this is one bounded patch or overloaded. If overloaded, "
                "short-circuit implementation, explain why, decompose into dependency-ordered slices with objectives, "
                "likely files, acceptance proof, and artifact boundaries, identify the smallest useful first slice, "
                "and provide Chat vs Work routes. Do not create a patch."
            ),
            scorer=score_scope_gate_response,
        ),
        SmokeTask(
            stage=10,
            name="multi_file_inventory_json_review",
            num_predict=1200,
            prompt=(
                "Hardness 10. Use thinking if enabled, but the final answer must be only a JSON object.\n\n"
                "Review this inventory for a narrow replacement-file patch. config.py says gemma4:26b is the true "
                "runtime default. Inventory:\n"
                "- docker-compose.dev.yml: MAIN_COMPUTER_MODEL fallback is qwen2.5:1.5b\n"
                "- README.md: setup example says export MAIN_COMPUTER_MODEL=qwen2.5:1.5b\n"
                "- ENVIRONMENT.md: environment table lists qwen2.5:1.5b as default\n"
                "- docker/dev/README.md: tells users to pull qwen2.5:1.5b for dev\n"
                "- main_computer/rag_advanced_eval_layer_smoke.py: qwen2.5 appears as a synthetic baseline_model\n"
                "- pretty_docs/model-boundary.md: old planning note mentions qwen2.5 historically\n"
                "- diagnostics_output/: generated files may contain qwen2.5\n\n"
                "Return JSON with keys change, leave, verify, risks. Put file paths in change/leave and concise "
                "reasons. The answer should avoid broad historical rewrites and explain how to verify with search "
                "and new_patch.py --dry-run."
            ),
            scorer=score_inventory_json_review,
        ),
        SmokeTask(
            stage=11,
            name="agentic_patch_plan_with_tests",
            num_predict=1300,
            prompt=(
                "Hardness 11. Use thinking if enabled. Final answer should be a concrete patch plan, not code.\n\n"
                "Plan a bounded implementation for repo-local model selection. The repo already has Ollama provider "
                "code, RAG Ollama smoke scripts, docker-compose.dev.yml, README.md, ENVIRONMENT.md, and tests. "
                "The default must remain gemma4:26b unless overridden. qwen3.8:27b should be available as an "
                "optional comparison/deep model.\n\n"
                "Produce a dependency-ordered patch plan with likely files, exact behavior, CLI surface, config "
                "format, validation behavior, test cases, docs updates, migration/backward compatibility, and "
                "new_patch.py artifact verification. Keep it narrow and avoid unrelated refactors."
            ),
            scorer=score_agentic_patch_plan,
        ),
        SmokeTask(
            stage=12,
            name="benchmark_decision_and_next_eval",
            num_predict=1200,
            prompt=(
                "Hardness 12. Use thinking if enabled. Final answer should make a recommendation and design the next eval.\n\n"
                "Local loaded-speed contest results:\n"
                "- qwen3.8:27b scored 42/54 quality, median_loaded_total_s 26.93s, median_eval_tps 3.32, "
                "median_eval_ms_per_token 301.54.\n"
                "- gemma4:26b scored 45/54 quality, median_loaded_total_s 6.14s, median_eval_tps 26.23, "
                "median_eval_ms_per_token 38.12.\n"
                "- Both models had near-zero measured load_s after warmup.\n"
                "- A reviewer/YouTuber hypes Qwen3.8-27B as an important local open-weight model because of its "
                "27B size and progressive training on hard agentic tasks.\n\n"
                "Decide the repo default and the role of qwen3.8:27b. Explain why the hype and local result can both "
                "be true. Propose the next thinking-enabled hard eval, including fixed-token continuation, long-horizon "
                "repo tasks, and how to separate cold-load, loaded speed, quality, and thinking cost."
            ),
            scorer=score_benchmark_decision,
        ),
    ]

def get_rag_generate_helper() -> tuple[Any, Any]:
    from main_computer.rag_gremlin_pyramid_atom_smoke import (  # type: ignore
        Logger,
        call_ollama_generate_streaming,
    )

    return Logger, call_ollama_generate_streaming


def call_model_via_rag_generate(
    *,
    model: str,
    prompt: str,
    num_predict: int,
    generate_url: str,
    out_dir: Path,
    label: str,
    temperature: float,
    quiet_helper_log: bool,
    keep_alive: str,
    think_value: bool | None,
) -> tuple[str, dict[str, Any], str | None]:
    """Call Ollama through the existing RAG generate-stream helper."""

    Logger, call_ollama_generate_streaming = get_rag_generate_helper()

    case_dir = out_dir / safe_name(model) / safe_name(label)
    case_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": temperature,
            "num_predict": int(num_predict),
        },
    }
    if keep_alive:
        payload["keep_alive"] = keep_alive
    if think_value is not None:
        payload["think"] = think_value
    write_json(case_dir / "payload.json", payload)

    log = Logger(case_dir / "rag_generate_stream.log", quiet=quiet_helper_log)
    raw_path = case_dir / "raw_response.jsonl"

    try:
        text, summary = call_ollama_generate_streaming(
            payload=payload,
            url=generate_url,
            timeout_s=0,
            log=log,
            raw_path=raw_path,
            stream_label=label,
        )
        write_json(case_dir / "summary.json", summary)
        return text, summary, None
    except urllib.error.HTTPError as exc:
        error = format_http_error(exc)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    fail_summary = {
        "ok": False,
        "error": error,
        "traceback": traceback.format_exc(limit=6),
    }
    write_json(case_dir / "summary.json", fail_summary)
    return "", fail_summary, error


def installed_models(base_url: str, timeout: float) -> list[str]:
    data = request_json(method="GET", url=base_url.rstrip("/") + "/api/tags", payload=None, timeout=timeout)
    records = data.get("models", [])
    if not isinstance(records, list):
        return []
    names: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get("name") or record.get("model")
        if name:
            names.append(str(name))
    return names


def model_aliases(model: str) -> set[str]:
    lowered = model.strip().lower()
    aliases = {lowered}
    if ":" not in lowered:
        aliases.add(lowered + ":latest")
    if lowered.endswith(":latest"):
        aliases.add(lowered[:-7])
    return aliases


def model_is_installed(model: str, names: list[str]) -> bool:
    aliases = model_aliases(model)
    return any(name.lower() in aliases for name in names)


def show_model(base_url: str, model: str, timeout: float) -> dict[str, Any]:
    return request_json(
        method="POST",
        url=base_url.rstrip("/") + "/api/show",
        payload={"model": model, "verbose": False},
        timeout=timeout,
    )


def preflight(args: argparse.Namespace, out_dir: Path) -> tuple[dict[str, Any], set[str]]:
    base_url = base_url_from_generate_url(args.generate_url)
    print("Preflight")
    print(f"  generate_url: {args.generate_url}")
    print("  call path: imported main_computer.rag_gremlin_pyramid_atom_smoke.call_ollama_generate_streaming")
    print("  transport: /api/generate stream=true, byte-by-byte JSONL read, repo prepare_ollama_generate_payload")
    print(f"  benchmark think: {'on' if args.think_value is True else 'off' if args.think_value is False else 'default/off'}")
    print("  preflight think: off (transport probe)")
    print()

    diagnostics: dict[str, Any] = {
        "base_url": base_url,
        "generate_url": args.generate_url,
        "call_path": "main_computer.rag_gremlin_pyramid_atom_smoke.call_ollama_generate_streaming",
        "models": {},
    }

    try:
        version = request_json(method="GET", url=base_url.rstrip("/") + "/api/version", payload=None, timeout=args.preflight_timeout)
        diagnostics["version"] = version
        print(f"  Ollama version: {version.get('version', '<unknown>')}")
    except Exception as exc:
        diagnostics["version_error"] = f"{type(exc).__name__}: {exc}"
        print(f"  Ollama version: failed: {diagnostics['version_error']}")

    try:
        names = installed_models(base_url, args.preflight_timeout)
        diagnostics["installed_models"] = names
        print(f"  Installed models visible to /api/tags: {len(names)}")
    except Exception as exc:
        names = []
        diagnostics["tags_error"] = f"{type(exc).__name__}: {exc}"
        print(f"  /api/tags failed: {diagnostics['tags_error']}")

    ok_models: set[str] = set()
    for model in args.models:
        print(f"  {model}:")
        model_diag: dict[str, Any] = {"installed": model_is_installed(model, names)}
        diagnostics["models"][model] = model_diag
        print(f"    tags: {'installed' if model_diag['installed'] else 'not listed'}")

        try:
            shown = show_model(base_url, model, args.preflight_timeout)
            details = shown.get("details") if isinstance(shown.get("details"), dict) else {}
            model_diag["show"] = {
                "ok": True,
                "family": details.get("family"),
                "parameter_size": details.get("parameter_size"),
                "quantization_level": details.get("quantization_level"),
            }
            print(
                "    show: ok"
                f", family={details.get('family')}"
                f", params={details.get('parameter_size')}"
                f", quant={details.get('quantization_level')}"
            )
        except Exception as exc:
            model_diag["show"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"    show: failed: {model_diag['show']['error']}")

        # Preflight validates the transport/model route, not reasoning quality.
        # A 16-token READY probe cannot safely run with thinking enabled because
        # thinking-capable models may spend the entire budget in the separate
        # `thinking` stream and reach done_reason=length before emitting a final
        # `response`. Keep the probe non-thinking; measured hardness tasks still
        # use args.think_value.
        text, summary, error = call_model_via_rag_generate(
            model=model,
            prompt="Reply with exactly one word: READY",
            num_predict=16,
            generate_url=args.generate_url,
            out_dir=out_dir / "preflight",
            label=f"preflight_{model}",
            temperature=0.0,
            quiet_helper_log=True,
            keep_alive=args.keep_alive,
            think_value=False,
        )
        model_diag["rag_generate_probe"] = {
            "ok": error is None and bool(text.strip()),
            "error": error,
            "response_preview": text[:200],
            "summary": summary,
        }

        if error:
            print(f"    rag-generate-stream probe: failed: {error}")
        elif text.strip():
            score, max_score, note = score_exact_ready(text)
            print(
                "    rag-generate-stream probe: ok"
                f", score={score}/{max_score}"
                f", elapsed={fmt(summary.get('elapsed_s'))}s"
                f", preview={text[:80]!r}"
            )
            if score == max_score:
                ok_models.add(model)
            else:
                print(f"    probe quality note: {note}")
        else:
            print("    rag-generate-stream probe: failed: empty response")

    write_json(out_dir / "preflight.json", diagnostics)
    return diagnostics, ok_models


def build_timing_fields(summary: dict[str, Any], wall_s: float) -> dict[str, Any]:
    total_s = ns_to_s(summary.get("total_duration"))
    load_s = ns_to_s(summary.get("load_duration"))
    prompt_eval_s = ns_to_s(summary.get("prompt_eval_duration"))
    eval_s = ns_to_s(summary.get("eval_duration"))
    loaded_total_s = subtract_nonnegative(total_s, load_s)
    loaded_wall_s = subtract_nonnegative(wall_s, load_s)

    return {
        "wall_s": wall_s,
        "helper_elapsed_s": summary.get("elapsed_s"),
        "total_s": total_s,
        "load_s": load_s,
        "load_pct": pct(load_s, total_s),
        "loaded_total_s": loaded_total_s,
        "loaded_wall_s": loaded_wall_s,
        "prompt_eval_s": prompt_eval_s,
        "eval_s": eval_s,
        "prompt_eval_count": summary.get("prompt_eval_count"),
        "eval_count": summary.get("eval_count"),
        "prompt_eval_tps": tps(summary.get("prompt_eval_count"), summary.get("prompt_eval_duration")),
        "eval_tps": tps(summary.get("eval_count"), summary.get("eval_duration")),
        "eval_ms_per_token": ms_per_token(summary.get("eval_count"), summary.get("eval_duration")),
        "stream_event_count": summary.get("stream_event_count"),
        "think": summary.get("think"),
        "think_source": summary.get("think_source"),
    }


def run_task(args: argparse.Namespace, out_dir: Path, task: SmokeTask, model: str, run_idx: int) -> dict[str, Any]:
    label = f"stage{task.stage}_{task.name}_run{run_idx}"
    started = time.perf_counter()
    text, summary, error = call_model_via_rag_generate(
        model=model,
        prompt=task.prompt,
        num_predict=task.num_predict,
        generate_url=args.generate_url,
        out_dir=out_dir / "runs",
        label=label,
        temperature=args.temperature,
        quiet_helper_log=args.quiet_helper_log,
        keep_alive=args.keep_alive,
        think_value=args.think_value,
    )
    wall_s = time.perf_counter() - started

    if error:
        score = 0
        max_score = task.scorer("")[1]
        note = error
    else:
        score, max_score, note = task.scorer(text)

    row = {
        "kind": "task",
        "stage": task.stage,
        "task": task.name,
        "model": model,
        "run": run_idx,
        "ok": error is None,
        "error": error,
        "score": score,
        "max_score": max_score,
        "quality_pct": (score / max_score * 100.0) if max_score else None,
        **build_timing_fields(summary, wall_s),
        "note": note,
        "response_preview": strip_think_blocks(text)[:500].replace("\n", "\\n"),
    }
    return row


def run_warmup(args: argparse.Namespace, out_dir: Path, model: str, warmup_idx: int) -> dict[str, Any]:
    label = f"warmup_run{warmup_idx}"
    started = time.perf_counter()
    text, summary, error = call_model_via_rag_generate(
        model=model,
        prompt=args.warmup_prompt,
        num_predict=args.warmup_num_predict,
        generate_url=args.generate_url,
        out_dir=out_dir / "warmups",
        label=label,
        temperature=0.0,
        quiet_helper_log=args.quiet_helper_log,
        keep_alive=args.keep_alive,
        # Warmup exists only to load the model before measured runs. Keep it
        # non-thinking so the tiny warmup budget cannot be consumed by hidden
        # reasoning. Measured tasks retain the requested think policy.
        think_value=False,
    )
    wall_s = time.perf_counter() - started
    score, max_score, note = score_exact_ready(text) if not error else (0, 2, error)

    return {
        "kind": "warmup",
        "stage": 0,
        "task": "warmup",
        "model": model,
        "run": warmup_idx,
        "ok": error is None,
        "error": error,
        "score": score,
        "max_score": max_score,
        "quality_pct": (score / max_score * 100.0) if max_score else None,
        **build_timing_fields(summary, wall_s),
        "note": note,
        "response_preview": strip_think_blocks(text)[:500].replace("\n", "\\n"),
    }


def print_run_result(row: dict[str, Any]) -> None:
    if row["ok"]:
        print(
            f"{row['score']}/{row['max_score']}, "
            f"wall={fmt(row['wall_s'])}s, "
            f"load={fmt(row['load_s'])}s, "
            f"loaded={fmt(row['loaded_total_s'])}s, "
            f"eval_tps={fmt(row['eval_tps'])}"
        )
    else:
        print(f"ERROR, wall={fmt(row['wall_s'])}s")
        print(f"    {row['error']}")

def summarize_model(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    model_rows = [row for row in rows if row["model"] == model and row.get("ok")]
    score = sum(int(row.get("score") or 0) for row in model_rows)
    max_score = sum(int(row.get("max_score") or 0) for row in model_rows)
    quality_pct = score / max_score * 100.0 if max_score else 0.0

    def median(key: str) -> float | None:
        values = [row.get(key) for row in model_rows if isinstance(row.get(key), (int, float))]
        return statistics.median(values) if values else None

    load_events = sum(1 for row in model_rows if isinstance(row.get("load_s"), (int, float)) and float(row["load_s"]) > 1.0)
    loaded_rows = sum(1 for row in model_rows if isinstance(row.get("loaded_total_s"), (int, float)))

    return {
        "model": model,
        "runs": len(model_rows),
        "score": score,
        "max_score": max_score,
        "quality_pct": quality_pct,
        "load_events_gt_1s": load_events,
        "loaded_rows": loaded_rows,
        "median_wall_s": median("wall_s"),
        "median_helper_elapsed_s": median("helper_elapsed_s"),
        "median_total_s": median("total_s"),
        "median_load_s": median("load_s"),
        "median_load_pct": median("load_pct"),
        "median_loaded_total_s": median("loaded_total_s"),
        "median_loaded_wall_s": median("loaded_wall_s"),
        "median_prompt_eval_s": median("prompt_eval_s"),
        "median_eval_s": median("eval_s"),
        "median_prompt_eval_tps": median("prompt_eval_tps"),
        "median_eval_tps": median("eval_tps"),
        "median_eval_ms_per_token": median("eval_ms_per_token"),
    }


def summarize_warmups(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    warm_rows = [row for row in rows if row["model"] == model and row.get("ok")]

    def median(key: str) -> float | None:
        values = [row.get(key) for row in warm_rows if isinstance(row.get(key), (int, float))]
        return statistics.median(values) if values else None

    return {
        "model": model,
        "runs": len(warm_rows),
        "median_wall_s": median("wall_s"),
        "median_total_s": median("total_s"),
        "median_load_s": median("load_s"),
        "median_loaded_total_s": median("loaded_total_s"),
        "median_eval_tps": median("eval_tps"),
    }

def print_table(headers: list[str], rows: list[list[Any]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(str(value)))
    print("  ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers))))
    print("  ".join("-" * widths[idx] for idx in range(len(headers))))
    for row in rows:
        print("  ".join(str(row[idx]).ljust(widths[idx]) for idx in range(len(headers))))



def parse_hardness_range(value: str) -> tuple[int, int]:
    text = str(value or "").strip()
    if not text:
        raise argparse.ArgumentTypeError("--hardness requires START:END, for example 4:11")
    if ":" in text:
        left, right = text.split(":", 1)
    else:
        left, right = text, text
    try:
        start = int(left)
        end = int(right)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--hardness requires integer START:END, for example 7:12") from exc
    if start < 1 or end < 1:
        raise argparse.ArgumentTypeError("--hardness stages must be positive")
    if start > end:
        raise argparse.ArgumentTypeError("--hardness START must be <= END")
    return start, end


def resolve_think_value(think: str, hardness: str | None) -> bool | None:
    value = str(think or "auto").strip().lower()
    if value in {"on", "true", "1", "yes"}:
        return True
    if value in {"off", "false", "0", "no"}:
        return False
    if value != "auto":
        raise argparse.ArgumentTypeError("--think must be auto, on, or off")
    # Preserve existing default smoke behavior unless the user asks for hardness mode.
    # In hardness mode, turn thinking on for every requested model.
    return True if hardness else None


def select_tasks(all_tasks: list[SmokeTask], hardness: str | None, max_stage: int) -> tuple[list[SmokeTask], str]:
    if hardness:
        start, end = parse_hardness_range(hardness)
        selected = [task for task in all_tasks if start <= task.stage <= end]
        selector = f"hardness {start}:{end}"
    else:
        selected = [task for task in all_tasks if task.stage <= max_stage]
        selector = f"max-stage <= {max_stage}"
    if not selected:
        available = ", ".join(str(task.stage) for task in all_tasks)
        raise SystemExit(f"No tasks selected. Available stages: {available}")
    return selected, selector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--generate-url", default=os.environ.get("OLLAMA_GENERATE_URL") or os.environ.get("OLLAMA_URL") or DEFAULT_GENERATE_URL)
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Measured runs per task. Defaults to 2 for normal smoke and 1 for --hardness ranges.",
    )
    parser.add_argument("--max-stage", type=int, default=5)
    parser.add_argument(
        "--hardness",
        default=None,
        help="Run an inclusive hardness/task range such as 4:11 or 7:12. Enables thinking by default.",
    )
    parser.add_argument(
        "--think",
        choices=("auto", "on", "off"),
        default="auto",
        help="Top-level Ollama think policy. auto preserves normal smoke defaults, but turns thinking on with --hardness.",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--preflight-timeout", type=float, default=10.0)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument(
        "--schedule",
        choices=("grouped", "interleaved"),
        default="grouped",
        help=(
            "grouped keeps one model active through all measured tasks after warmup; "
            "interleaved alternates models like the original contest"
        ),
    )
    parser.add_argument("--warmup-runs", type=int, default=1, help="Unscored warmup calls per model before measured tasks.")
    parser.add_argument("--warmup-num-predict", type=int, default=16)
    parser.add_argument("--warmup-prompt", default="Reply with exactly one word: READY")
    parser.add_argument(
        "--keep-alive",
        default="30m",
        help="Top-level Ollama keep_alive value sent with each /api/generate request. Use '' to omit.",
    )
    parser.add_argument("--twiddle-only", action="store_true", help="Only run preflight/probe through the RAG generate stream helper.")
    parser.add_argument("--quiet-helper-log", action="store_true", help="Do not echo each streamed chunk from the imported RAG helper.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args(argv)

    if args.runs is None:
        args.runs = 1 if args.hardness else 2
    args.think_value = resolve_think_value(args.think, args.hardness)
    args.generate_url = normalize_generate_url(args.generate_url)
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / f"omos_{utc_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Ollama model contest script: {SCRIPT_VERSION}")
    print(f"Repo root: {_REPO_ROOT}")
    print(f"Models: {', '.join(args.models)}")
    all_tasks = build_tasks()
    tasks, task_selector = select_tasks(all_tasks, args.hardness, args.max_stage)

    print(f"Runs per task: {args.runs}")
    print(f"Task selector: {task_selector}")
    print(f"Thinking: {'on' if args.think_value is True else 'off' if args.think_value is False else 'default/off'}")
    print(f"Schedule: {args.schedule}")
    print(f"Warmup runs per model: {args.warmup_runs}")
    print("Warmup thinking: off (load-only warmup)")
    print(f"keep_alive: {args.keep_alive or '<omitted>'}")
    print(f"Output: {out_dir}")
    print()

    diagnostics, ok_models = preflight(args, out_dir)
    if args.twiddle_only:
        print()
        print(f"Wrote preflight diagnostics: {out_dir / 'preflight.json'}")
        return 0 if len(ok_models) == len(args.models) else 3

    failed = [model for model in args.models if model not in ok_models]
    if failed and not args.allow_partial:
        print()
        print("Stopping before timed benchmark because the imported RAG generate-stream helper failed preflight.")
        print("Failed models: " + ", ".join(failed))
        print()
        print("To isolate routes using the existing repo diagnostic, run:")
        for model in failed:
            print(
                "  python -u main_computer/rag_ollama_stream_route_matrix_smoke.py "
                f"--repo . --model {model} --live"
            )
        print()
        print(f"Wrote preflight diagnostics: {out_dir / 'preflight.json'}")
        return 3

    benchmark_models = [model for model in args.models if model in ok_models or args.allow_partial]
    rows: list[dict[str, Any]] = []

    print()
    print("Progressive tasks:")
    for task in tasks:
        print(f"  stage {task.stage}: {task.name}")
    print()

    warmup_rows: list[dict[str, Any]] = []

    def warm_model(model: str) -> None:
        if args.warmup_runs <= 0:
            return
        print(f"== Warmup: {model} ==")
        for warmup_idx in range(1, args.warmup_runs + 1):
            print(f"  {model} warmup {warmup_idx}/{args.warmup_runs} ... ", end="", flush=True)
            row = run_warmup(args, out_dir, model, warmup_idx)
            warmup_rows.append(row)
            print_run_result(row)
        print()

    if args.schedule == "grouped":
        for model in benchmark_models:
            warm_model(model)
            print(f"== Loaded-model measured block: {model} ==")
            for task in tasks:
                print(f"  Stage {task.stage}: {task.name}")
                for run_idx in range(1, args.runs + 1):
                    print(f"    run {run_idx}/{args.runs} ... ", end="", flush=True)
                    row = run_task(args, out_dir, task, model, run_idx)
                    rows.append(row)
                    print_run_result(row)
            print()
    else:
        for model in benchmark_models:
            warm_model(model)
        for task in tasks:
            print(f"== Stage {task.stage}: {task.name} ==")
            for run_idx in range(1, args.runs + 1):
                for model in benchmark_models:
                    print(f"  {model} run {run_idx}/{args.runs} ... ", end="", flush=True)
                    row = run_task(args, out_dir, task, model, run_idx)
                    rows.append(row)
                    print_run_result(row)
            print()

    summaries = [summarize_model(rows, model) for model in args.models]

    print("Summary")
    warmup_summaries = [summarize_warmups(warmup_rows, model) for model in args.models]
    if warmup_rows:
        print()
        print("Warmup/load summary")
        warmup_table_rows = [
            [
                item["model"],
                item["runs"],
                fmt(item["median_wall_s"]),
                fmt(item["median_load_s"]),
                fmt(item["median_loaded_total_s"]),
                fmt(item["median_eval_tps"]),
            ]
            for item in warmup_summaries
        ]
        print_table(
            ["model", "runs", "warmup_wall_s", "warmup_load_s", "warmup_loaded_s", "warmup_eval_tps"],
            warmup_table_rows,
        )

    print()
    print("Measured task summary")
    table_rows = [
        [
            item["model"],
            item["runs"],
            f"{item['score']}/{item['max_score']}",
            fmt(item["quality_pct"], 1) + "%",
            fmt(item["median_total_s"]),
            fmt(item["median_load_s"]),
            fmt(item["median_loaded_total_s"]),
            fmt(item["median_eval_tps"]),
            fmt(item["median_eval_ms_per_token"]),
            item["load_events_gt_1s"],
        ]
        for item in summaries
    ]
    print_table(
        [
            "model",
            "runs",
            "quality",
            "quality_pct",
            "median_total_s",
            "median_load_s",
            "median_loaded_s",
            "median_eval_tps",
            "median_eval_ms_tok",
            "load_events",
        ],
        table_rows,
    )
    print()
    print("Loaded speed uses loaded_total_s = total_duration - load_duration.")

    valid = [item for item in summaries if item["runs"] > 0]
    speed_metric = "median_loaded_total_s" if all(item.get("median_loaded_total_s") is not None for item in valid) else "median_total_s"
    if len(valid) >= 2:
        faster = min(valid, key=lambda item: float("inf") if item.get(speed_metric) is None else item[speed_metric])
        better = max(valid, key=lambda item: (item["quality_pct"], item["score"]))
        print()
        print(f"Faster by {speed_metric}: {faster['model']}")
        print(f"Better by smoke rubric: {better['model']}")
        if faster["model"] == better["model"]:
            print(f"Smoke-test winner: {faster['model']}")
        else:
            print("Tradeoff detected:")
            print(f"  speed winner:   {faster['model']}")
            print(f"  quality winner: {better['model']}")

    result = {
        "script_version": SCRIPT_VERSION,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "repo_root": str(_REPO_ROOT),
        "models": args.models,
        "generate_url": args.generate_url,
        "runs": args.runs,
        "max_stage": args.max_stage,
        "hardness": args.hardness,
        "task_selector": task_selector,
        "think": args.think,
        "think_value": args.think_value,
        "temperature": args.temperature,
        "schedule": args.schedule,
        "warmup_runs": args.warmup_runs,
        "keep_alive": args.keep_alive,
        "preflight": diagnostics,
        "warmup_rows": warmup_rows,
        "warmup_summary": warmup_summaries if 'warmup_summaries' in locals() else [],
        "rows": rows,
        "summary": summaries,
        "speed_metric": speed_metric if 'speed_metric' in locals() else None,
    }
    write_json(out_dir / "contest_results.json", result)
    print()
    print(f"Wrote JSON: {out_dir / 'contest_results.json'}")

    if warmup_rows:
        warmup_csv_path = out_dir / "warmup_results.csv"
        write_csv(warmup_csv_path, warmup_rows)
        print(f"Wrote warmup CSV: {warmup_csv_path}")

    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = out_dir / "contest_results.csv"
    write_csv(csv_path, rows)
    print(f"Wrote CSV: {csv_path}")

    return 0 if all(model in ok_models for model in args.models) else 3


if __name__ == "__main__":
    raise SystemExit(main())
