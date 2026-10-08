#!/usr/bin/env python3
"""Run the meaningful-game growth workflow end-to-end through local Ollama.

This is the boring integration smoke for the state-to-module compiler.  It owns
all transport between the growth workflow and the repo's existing local-model
boundary:

    growth prompt
      -> local_model_prompt_component_v1 / OllamaProvider
      -> canonical response_NN.json
      -> schema/workflow validation
      -> advance
      -> repeat until the code prompt
      -> local Ollama code call
      -> extract three generated module files
      -> validate artifact shape / JS syntax
      -> smoke_report.json

No model response is copied from the prompt and no manual response file is
required.  Raw model text and provider traces are retained beside the workflow.

Typical live run from the repository root:

    python game_projects/webgl-demo/tools/smoke_meaningful_game_growth_with_local_ai.py \\
      --project webgl-demo \\
      --workflow-dir diagnostics_output/spy_hunt_growth_smoke \\
      --model qwen3.8:27b \\
      --reset

The Ollama endpoint, timeout, think flag, and fallback policy come from the same
MainComputerConfig used by ``local_model_prompt_component_v1``.  ``--model`` is
only an optional model-name override.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from main_computer.local_model_prompt_component_v1 import run_local_model_prompt_call
from main_computer.meaningful_game_module_prompt_v1 import DEFAULT_SPY_HUNT_BRIEF
from main_computer.meaningful_game_growth_prompt_v2 import (
    advance_workflow,
    current_prompt_text,
    init_workflow,
    status_workflow,
    validate_response_contract,
)


SMOKE_VERSION = "smoke_meaningful_game_growth_with_local_ai_v1.1"
DEFAULT_WORKFLOW_DIR = Path("diagnostics_output") / "spy_hunt_growth_smoke"
DEFAULT_MAX_GROWTH_ROUNDS = 10
DEFAULT_HEARTBEAT_SECONDS = 10.0
FINAL_FILENAMES = (
    "game_module_manifest.json",
    "game_module.js",
    "integration_contract.json",
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return dict(value)


def _json_stdout(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _log(event: str, message: str = "", **fields: Any) -> None:
    """Emit one flushed human-readable progress line to stderr.

    Keeping progress on stderr leaves stdout available for the final JSON smoke
    report and makes the script safe to tee/redirect in automation.
    """

    timestamp = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    parts = [f"[{timestamp}]", "[game-growth-smoke]", str(event)]
    if message:
        parts.append(str(message))
    for key, value in fields.items():
        if value is None:
            continue
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple, bool)) else str(value)
        parts.append(f"{key}={rendered}")
    print(" ".join(parts), file=sys.stderr, flush=True)


class _CallHeartbeat:
    """Periodic visibility while the synchronous local-model call is running."""

    def __init__(self, *, round_number: int, prompt_kind: str, interval_seconds: float) -> None:
        self.round_number = round_number
        self.prompt_kind = prompt_kind
        self.interval_seconds = max(0.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def __enter__(self) -> "_CallHeartbeat":
        self._started = time.monotonic()
        if self.interval_seconds <= 0:
            return self
        self._thread = threading.Thread(target=self._run, name="game-growth-ollama-heartbeat", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            elapsed = int(time.monotonic() - self._started)
            _log(
                "OLLAMA_WAIT",
                "local model call still running",
                round=self.round_number,
                kind=self.prompt_kind,
                elapsed_s=elapsed,
            )

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def _single_json_fence(text: str) -> str | None:
    """Return one whole-response JSON fence body, otherwise None.

    The growth contract asks the model for raw JSON.  Some local models still
    wrap an otherwise exact answer in one markdown fence.  This is transport
    cleanup only: prose before/after the fence remains a hard failure.
    """

    match = re.fullmatch(
        r"\s*```(?:json)?\s*\r?\n(?P<body>.*?)\r?\n```\s*",
        text or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return match.group("body").strip()


def parse_decision_response(response_text: str) -> tuple[dict[str, Any], str]:
    """Parse a model decision without fishing JSON out of arbitrary prose."""

    raw = str(response_text or "").strip()
    if not raw:
        raise ValueError("local model returned an empty decision response")

    transport = "raw-json"
    candidate = raw
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as first_error:
        fenced = _single_json_fence(raw)
        if fenced is None:
            raise ValueError(
                "growth response was not exact JSON or one whole JSON fence: "
                f"{first_error}"
            ) from first_error
        candidate = fenced
        transport = "single-json-fence"
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as second_error:
            raise ValueError(f"JSON fence body was invalid JSON: {second_error}") from second_error

    if not isinstance(value, dict):
        raise ValueError("growth response JSON root must be an object")
    return dict(value), transport


def _marked_file_block(text: str, filename: str) -> str:
    escaped = re.escape(filename)
    pattern = re.compile(
        rf"^[ \t]*BEGIN[ \t]+{escaped}[ \t]*\r?\n(?P<body>.*?)^[ \t]*END[ \t]+{escaped}[ \t]*$",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text or "")
    if not match:
        raise ValueError(f"final local-model response is missing BEGIN/END markers for {filename}")
    body = match.group("body").strip()
    fence = re.fullmatch(
        r"```[^\n`]*\r?\n(?P<body>.*?)\r?\n```",
        body,
        flags=re.DOTALL,
    )
    return fence.group("body").strip() if fence else body


def extract_generated_module(response_text: str) -> dict[str, str]:
    files = {name: _marked_file_block(response_text, name) for name in FINAL_FILENAMES}
    if not files["game_module.js"].strip():
        raise ValueError("generated game_module.js is empty")
    return files


def _validate_generated_artifacts(
    *,
    files: dict[str, str],
    expected_module_id: str,
    desired_exports: list[str],
    output_dir: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        manifest = json.loads(files["game_module_manifest.json"])
    except json.JSONDecodeError as exc:
        manifest = {}
        errors.append(f"game_module_manifest.json is invalid JSON: {exc}")
    if not isinstance(manifest, dict):
        manifest = {}
        errors.append("game_module_manifest.json root must be an object")

    try:
        integration = json.loads(files["integration_contract.json"])
    except json.JSONDecodeError as exc:
        integration = {}
        errors.append(f"integration_contract.json is invalid JSON: {exc}")
    if not isinstance(integration, dict):
        integration = {}
        errors.append("integration_contract.json root must be an object")

    if manifest.get("schema") != "game.perGameModuleManifest.v1":
        errors.append("manifest schema must be game.perGameModuleManifest.v1")
    if str(manifest.get("id") or "") != expected_module_id:
        errors.append(
            f"manifest id {manifest.get('id')!r} does not match expected module id {expected_module_id!r}"
        )
    if manifest.get("entry") != "game_module.js":
        errors.append("manifest entry must be game_module.js")

    if integration.get("schema") != "game.perGameModuleIntegrationContract.v1":
        errors.append("integration schema must be game.perGameModuleIntegrationContract.v1")
    if str(integration.get("moduleId") or "") != expected_module_id:
        errors.append(
            f"integration moduleId {integration.get('moduleId')!r} does not match expected module id {expected_module_id!r}"
        )

    js = files["game_module.js"]
    for export_name in desired_exports:
        # This is intentionally a smoke signal, not a JS parser.  node --check
        # below handles syntax; this check only catches obviously omitted API.
        if not re.search(rf"\b{re.escape(export_name)}\b", js):
            errors.append(f"game_module.js is missing requested export symbol {export_name!r}")

    for forbidden in (
        "window",
        "document",
        "fetch",
        "XMLHttpRequest",
        "localStorage",
        "sessionStorage",
        "eval",
    ):
        if re.search(rf"\b{re.escape(forbidden)}\b", js):
            warnings.append(f"game_module.js contains forbidden/global token {forbidden!r}; inspect usage")

    node_check: dict[str, Any] = {"attempted": False, "ok": None}
    node = shutil.which("node")
    js_path = output_dir / "game_module.js"
    if node:
        node_check["attempted"] = True
        completed = subprocess.run(
            [node, "--check", str(js_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        node_check.update(
            {
                "ok": completed.returncode == 0,
                "returnCode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        )
        if completed.returncode != 0:
            errors.append("node --check rejected game_module.js")
    else:
        warnings.append("node executable not found; skipped JavaScript syntax check")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "manifest": manifest,
        "integrationContract": integration,
        "nodeCheck": node_check,
    }


def _call_local_model(
    *,
    prompt_text: str,
    workflow_dir: Path,
    round_number: int,
    prompt_kind: str,
    model: str | None,
    system_prompt: str,
    heartbeat_seconds: float,
) -> tuple[str, dict[str, Any]]:
    trace_dir = workflow_dir / "ollama_calls" / f"round_{round_number:02d}_{prompt_kind}"
    _log(
        "OLLAMA_START",
        "calling local model",
        round=round_number,
        kind=prompt_kind,
        model_override=model or "<configured default>",
        prompt_chars=len(prompt_text),
        prompt_sha256=_sha256_text(prompt_text),
        trace_dir=str(trace_dir),
    )
    started = time.monotonic()
    with _CallHeartbeat(
        round_number=round_number,
        prompt_kind=prompt_kind,
        interval_seconds=heartbeat_seconds,
    ):
        result = run_local_model_prompt_call(
            prompt_text=prompt_text,
            output_dir=trace_dir,
            system_prompt=system_prompt,
            model=model,
            run_id=f"{SMOKE_VERSION}-{round_number:02d}-{prompt_kind}",
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not result.ok:
        _log(
            "OLLAMA_FAIL",
            "local model call failed",
            round=round_number,
            kind=prompt_kind,
            elapsed_ms=elapsed_ms,
            details=result.details,
        )
        raise RuntimeError(f"local Ollama call failed for {prompt_kind}: {result.details}")
    response_text = str(result.provided_state.get("local_model_response_text") or "")
    if not response_text.strip():
        _log("OLLAMA_FAIL", "local model returned empty response", round=round_number, kind=prompt_kind)
        raise RuntimeError(f"local Ollama returned an empty response for {prompt_kind}")
    _log(
        "OLLAMA_DONE",
        "local model response received",
        round=round_number,
        kind=prompt_kind,
        provider=result.provided_state.get("local_model_provider"),
        model=result.provided_state.get("local_model_model"),
        elapsed_ms=elapsed_ms,
        response_chars=len(response_text),
        response_sha256=_sha256_text(response_text),
    )
    trace = {
        "round": round_number,
        "promptKind": prompt_kind,
        "provider": result.provided_state.get("local_model_provider"),
        "model": result.provided_state.get("local_model_model"),
        "elapsedMs": elapsed_ms,
        "promptSha256": result.provided_state.get("local_model_prompt_sha256"),
        "responseChars": result.provided_state.get("local_model_response_chars"),
        "providerTrace": result.provided_state.get("local_model_trace_path"),
    }
    return response_text, trace


def _ensure_workflow(
    *,
    repo_root: Path,
    workflow_dir: Path,
    project: str,
    brief: dict[str, Any],
    reset: bool,
) -> dict[str, Any]:
    state_path = workflow_dir / "growth_state.json"
    if reset and workflow_dir.exists():
        _log("RESET", "removing existing workflow directory", workflow_dir=str(workflow_dir))
        shutil.rmtree(workflow_dir)
    if state_path.is_file():
        _log("WORKFLOW_RESUME", "using existing growth workflow", workflow_dir=str(workflow_dir))
        return status_workflow(workflow_dir=workflow_dir)
    _log("WORKFLOW_INIT", "creating growth workflow", project=project, workflow_dir=str(workflow_dir))
    result = init_workflow(
        repo_root=repo_root,
        output_dir=workflow_dir,
        project=project,
        brief=brief,
    )
    _log(
        "WORKFLOW_READY",
        "initial prompt created",
        phase=result.get("phase"),
        round=result.get("round"),
        next_kind=result.get("nextPromptKind"),
        next_prompt=result.get("nextPrompt"),
    )
    return result


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script().resolve()
    workflow_dir = Path(args.workflow_dir).resolve()
    project = str(args.project or "webgl-demo").strip() or "webgl-demo"
    if args.brief:
        brief = _read_json(Path(args.brief).resolve())
    else:
        brief = dict(DEFAULT_SPY_HUNT_BRIEF)

    _log(
        "START",
        "end-to-end meaningful game growth smoke",
        version=SMOKE_VERSION,
        project=project,
        workflow_dir=str(workflow_dir),
        model_override=args.model or "<configured default>",
        max_growth_rounds=args.max_growth_rounds,
        heartbeat_s=args.heartbeat_seconds,
        reset=bool(args.reset),
    )

    _ensure_workflow(
        repo_root=repo_root,
        workflow_dir=workflow_dir,
        project=project,
        brief=brief,
        reset=bool(args.reset),
    )

    report_path = workflow_dir / "smoke_report.json"
    calls: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    advances: list[dict[str, Any]] = []
    started = time.monotonic()

    reasoning_system_prompt = (
        "You are executing a strict state-to-module compiler stage. Follow the user prompt's output contract exactly. "
        "Return only the requested JSON object. Do not include markdown, commentary, or chain-of-thought."
    )
    code_system_prompt = (
        "You are executing the final code stage of a strict game-module compiler. Follow the file-marker contract exactly. "
        "Return only the requested BEGIN/END marked files and no commentary."
    )

    for _ in range(int(args.max_growth_rounds) + 1):
        status = status_workflow(workflow_dir=workflow_dir)
        prompt_ref = status.get("nextPrompt") or {}
        prompt_kind = str(prompt_ref.get("kind") or "")
        round_number = int(status.get("round") or 0)
        if not prompt_kind:
            raise RuntimeError("growth workflow has no next prompt kind")

        _log(
            "ROUND",
            "processing workflow prompt",
            round=round_number,
            phase=status.get("phase"),
            kind=prompt_kind,
            context_packets=status.get("contextPacketCount"),
            context_chars=status.get("contextCharsAccepted"),
            facts=status.get("factCount"),
            unknowns=status.get("unknownCount"),
        )
        prompt = current_prompt_text(workflow_dir=workflow_dir)
        prompt_capture = workflow_dir / "model_prompts" / f"round_{round_number:02d}_{prompt_kind}.txt"
        _write_text(prompt_capture, prompt)
        _log(
            "PROMPT_READY",
            "captured model prompt",
            round=round_number,
            kind=prompt_kind,
            chars=len(prompt),
            sha256=_sha256_text(prompt),
            path=str(prompt_capture),
        )

        if prompt_kind == "code":
            raw_response, call_trace = _call_local_model(
                prompt_text=prompt,
                workflow_dir=workflow_dir,
                round_number=round_number,
                prompt_kind=prompt_kind,
                model=args.model,
                system_prompt=code_system_prompt,
                heartbeat_seconds=float(args.heartbeat_seconds),
            )
            calls.append(call_trace)
            raw_path = workflow_dir / f"code_response_{round_number:02d}.raw.txt"
            _write_text(raw_path, raw_response)
            _log("CODE_RESPONSE", "saved raw code response", path=str(raw_path), chars=len(raw_response))

            _log("CODE_EXTRACT", "extracting generated module files")
            files = extract_generated_module(raw_response)
            generated_dir = workflow_dir / "generated_module"
            generated_dir.mkdir(parents=True, exist_ok=True)
            _write_text(generated_dir / "game_module.js", files["game_module.js"] + "\n")
            # Canonicalize the JSON artifacts after proving they parse.
            for filename in ("game_module_manifest.json", "integration_contract.json"):
                parsed = json.loads(files[filename])
                if not isinstance(parsed, dict):
                    raise ValueError(f"{filename} root must be an object")
                _write_json(generated_dir / filename, parsed)

            _log(
                "CODE_WRITTEN",
                "generated module files written",
                directory=str(generated_dir),
                files=list(FINAL_FILENAMES),
            )
            artifact_validation = _validate_generated_artifacts(
                files={
                    "game_module_manifest.json": (generated_dir / "game_module_manifest.json").read_text(encoding="utf-8"),
                    "game_module.js": (generated_dir / "game_module.js").read_text(encoding="utf-8"),
                    "integration_contract.json": (generated_dir / "integration_contract.json").read_text(encoding="utf-8"),
                },
                expected_module_id=str(brief.get("id") or ""),
                desired_exports=[str(item) for item in brief.get("desiredExports", [])],
                output_dir=generated_dir,
            )
            report = {
                "ok": bool(artifact_validation.get("ok")),
                "schema": "game.meaningfulGameGrowthOllamaSmoke.v1",
                "componentVersion": SMOKE_VERSION,
                "project": project,
                "workflowDir": str(workflow_dir),
                "modelOverride": args.model,
                "growthRounds": len(validations),
                "ollamaCallCount": len(calls),
                "calls": calls,
                "validations": validations,
                "advances": advances,
                "generatedModuleDir": str(generated_dir),
                "generatedFiles": [str(generated_dir / name) for name in FINAL_FILENAMES],
                "artifactValidation": artifact_validation,
                "elapsedMs": int((time.monotonic() - started) * 1000),
            }
            _write_json(report_path, report)
            _log(
                "PASS" if report.get("ok") else "FAIL",
                "generated module validation complete",
                elapsed_ms=report.get("elapsedMs"),
                report=str(report_path),
                generated_dir=str(generated_dir),
                errors=artifact_validation.get("errors"),
                warnings=artifact_validation.get("warnings"),
            )
            return report

        if len(validations) >= int(args.max_growth_rounds):
            raise RuntimeError(
                f"growth workflow exceeded --max-growth-rounds={args.max_growth_rounds} before reaching code"
            )

        raw_response, call_trace = _call_local_model(
            prompt_text=prompt,
            workflow_dir=workflow_dir,
            round_number=round_number,
            prompt_kind=prompt_kind,
            model=args.model,
            system_prompt=reasoning_system_prompt,
            heartbeat_seconds=float(args.heartbeat_seconds),
        )
        calls.append(call_trace)
        raw_path = workflow_dir / f"response_{round_number:02d}.raw.txt"
        _write_text(raw_path, raw_response)
        _log("RESPONSE_SAVED", "saved raw reasoning response", round=round_number, path=str(raw_path), chars=len(raw_response))

        decision, transport = parse_decision_response(raw_response)
        _log(
            "PARSE_OK",
            "parsed decision JSON",
            round=round_number,
            transport=transport,
            phase=decision.get("phase"),
            ready_for_design=decision.get("readyForDesign"),
            ready_for_code=decision.get("readyForCode"),
            catalog_queries=len(decision.get("catalogQueries") or []),
            context_requests=len(decision.get("contextRequests") or []),
            facts=len(decision.get("facts") or []),
            unknowns=len(decision.get("unknowns") or []),
        )
        response_path = workflow_dir / f"response_{round_number:02d}.json"
        _write_json(response_path, decision)
        _log("DECISION_SAVED", "saved canonical decision JSON", round=round_number, path=str(response_path))

        _log("VALIDATE_START", "validating decision contract", round=round_number, kind=prompt_kind)
        validation = validate_response_contract(
            workflow_dir=workflow_dir,
            response_path=response_path,
        )
        validation["round"] = round_number
        validation["promptKind"] = prompt_kind
        validation["transport"] = transport
        validation["rawResponse"] = str(raw_path)
        validations.append(validation)
        if not validation.get("ok"):
            _log(
                "VALIDATE_FAIL",
                "decision contract rejected",
                round=round_number,
                schema_errors=validation.get("schemaErrors"),
                contextual_errors=validation.get("contextualErrors"),
            )
            failure = {
                "ok": False,
                "schema": "game.meaningfulGameGrowthOllamaSmoke.v1",
                "componentVersion": SMOKE_VERSION,
                "project": project,
                "workflowDir": str(workflow_dir),
                "failedStage": "decision-validation",
                "round": round_number,
                "promptKind": prompt_kind,
                "validation": validation,
                "calls": calls,
                "validations": validations,
                "advances": advances,
                "elapsedMs": int((time.monotonic() - started) * 1000),
            }
            _write_json(report_path, failure)
            _log("FAIL", "smoke stopped at decision validation", report=str(report_path))
            return failure

        _log("VALIDATE_OK", "decision contract accepted", round=round_number, kind=prompt_kind)
        _log("ADVANCE_START", "advancing growth workflow", round=round_number)
        advance_result = advance_workflow(
            workflow_dir=workflow_dir,
            response_path=response_path,
        )
        advances.append(advance_result)
        _log(
            "ADVANCE_DONE",
            "next workflow prompt prepared",
            previous_round=round_number,
            next_round=advance_result.get("round"),
            phase=advance_result.get("phase"),
            next_kind=advance_result.get("nextPromptKind"),
            context_packets=advance_result.get("contextPacketCount"),
            context_chars=advance_result.get("contextCharsAccepted"),
            facts=advance_result.get("factCount"),
            unknowns=advance_result.get("unknownCount"),
        )

    raise AssertionError("unreachable growth smoke loop exit")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive meaningful-game growth from source context through Ollama to generated module files."
    )
    parser.add_argument("--project", default="webgl-demo")
    parser.add_argument("--workflow-dir", default=str(DEFAULT_WORKFLOW_DIR))
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--brief", default=None, help="Optional game.meaningfulGameModuleBrief.v1 JSON file.")
    parser.add_argument("--model", default=None, help="Optional local Ollama model override.")
    parser.add_argument(
        "--max-growth-rounds",
        type=int,
        default=DEFAULT_MAX_GROWTH_ROUNDS,
        help="Maximum schema-validated reasoning rounds before the final code call.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="Emit an OLLAMA_WAIT line this often while a local-model call is blocking; 0 disables the heartbeat.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate --workflow-dir before starting. Use this after changing the prompt/schema contract.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_smoke(args)
    except Exception as exc:
        _log("ERROR", "smoke raised exception", error=f"{type(exc).__name__}: {exc}")
        result = {
            "ok": False,
            "schema": "game.meaningfulGameGrowthOllamaSmoke.v1",
            "componentVersion": SMOKE_VERSION,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _json_stdout(result)
        return 2
    _json_stdout(result)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
