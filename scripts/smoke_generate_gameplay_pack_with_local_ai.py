#!/usr/bin/env python3
"""Smoke-generate an experimental JS gameplay pack with the local AI.

This is intentionally a narrow smoke tool. By default it asks the configured
local model to produce a small generation plan for one existing scenario/section
and stops. After reviewing/editing plan.json, run the script again with
--from-plan to generate one candidate pack. The candidate is written under the
project's experimental gameplay pack area and validated only for pack shape,
scenario targeting, and plan structure.

Examples:

    python scripts/smoke_generate_gameplay_pack_with_local_ai.py \
      --scenario opening-shuttle-ambush \
      --slug large-random-shuttle-boarder \
      --prompt "At the start, spawn one large hostile boarder in a random valid shuttle location."

    python scripts/smoke_generate_gameplay_pack_with_local_ai.py \
      --scenario opening-shuttle-ambush \
      --slug large-random-shuttle-boarder \
      --from-plan game_projects/webgl-demo/gameplay_packs/experimental/large-random-shuttle-boarder/plan.json \
      --overwrite

    python scripts/smoke_generate_gameplay_pack_with_local_ai.py \
      --scenario opening-shuttle-ambush \
      --slug large-random-shuttle-boarder \
      --prompt "At the start, spawn one large hostile boarder." \
      --auto-approve-plan \
      --overwrite
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


FORBIDDEN_GLOBALS = (
    "window",
    "document",
    "fetch",
    "XMLHttpRequest",
    "localStorage",
    "sessionStorage",
    "eval",
    "Function",
)


@dataclass(frozen=True)
class ScenarioSurface:
    """One currently packable game surface."""

    scenario_id: str
    kind: str
    api_call: str
    description: str
    examples: tuple[str, ...]
    known_events: tuple[str, ...]
    known_commands: tuple[str, ...]
    cutscene_id: str | None = None

    def manifest_target_key(self) -> str:
        return "encounter" if self.kind == "encounter" else "section"


SCENARIO_SURFACES: dict[str, ScenarioSurface] = {
    "opening-shuttle-ambush": ScenarioSurface(
        scenario_id="opening-shuttle-ambush",
        kind="encounter",
        api_call='pack.encounter("opening-shuttle-ambush", ...)',
        description="Opening shuttle combat encounter.",
        examples=(
            "game_projects/webgl-demo/gameplay_packs/opening_shuttle_elite_boarders/manifest.json",
            "game_projects/webgl-demo/gameplay_packs/opening_shuttle_elite_boarders/pack.js",
        ),
        known_events=(
            "onStart",
            "onHostilesDefeated",
            "onAllHostilesDefeated",
            "onDestinationReached",
            "onPlayerDefeated",
        ),
        known_commands=(
            "setHostileHealthMultiplier",
            "showHudMessage",
            "spawnWave",
            "setObjective",
            "complete",
            "fail",
        ),
    ),
    "main-ship-bay": ScenarioSurface(
        scenario_id="main-ship-bay",
        kind="section",
        api_call='pack.section("main-ship-bay", ...)',
        description="Main-ship bay entry section.",
        examples=(
            "game_projects/webgl-demo/gameplay_packs/main_ship_bay_boarders/manifest.json",
            "game_projects/webgl-demo/gameplay_packs/main_ship_bay_boarders/pack.js",
        ),
        known_events=(
            "onCutsceneResolved",
            "onAllHostilesDefeated",
            "onPlayerDefeated",
        ),
        known_commands=(
            "showHudMessage",
            "spawnWave",
            "setObjective",
            "complete",
            "fail",
        ),
        cutscene_id="main-ship-bay-entry",
    ),
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def slugify(value: str, *, fallback: str = "generated-gameplay-pack") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80].strip("-") or fallback


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def json_stdout(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"```(?P<info>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)
    return [(match.group("info").strip(), match.group("body").strip()) for match in pattern.finditer(text or "")]


def marked_file_block(text: str, filename: str) -> str | None:
    """Extract a file body from BEGIN/END markers.

    The local model is asked to return boring file markers instead of prose so
    extraction does not depend on markdown formatting choices.
    """

    escaped = re.escape(filename)
    pattern = re.compile(
        rf"^[ \t]*BEGIN[ \t]+{escaped}[ \t]*\r?\n(?P<body>.*?)^[ \t]*END[ \t]+{escaped}[ \t]*$",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text or "")
    if not match:
        return None
    body = match.group("body").strip()
    nested_blocks = fenced_blocks(body)
    if len(nested_blocks) == 1:
        return nested_blocks[0][1].strip()
    return body


def looks_like_model_summary(response_text: str) -> bool:
    lowered = str(response_text or "").lower()
    summary_markers = (
        "it looks like you are providing",
        "it looks like you provided",
        "the code cuts off",
        "are you looking to debug",
        "here is the completed version",
        "summary of what this code does",
    )
    return any(marker in lowered for marker in summary_markers)



def extract_marked_json(response_text: str, filename: str) -> dict[str, Any] | None:
    marked = marked_file_block(response_text, filename)
    if not marked:
        return None
    try:
        parsed = json.loads(marked)
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse marked {filename}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"marked {filename} was not a JSON object")
    return dict(parsed)


def extract_pack_plan(response_text: str) -> dict[str, Any]:
    """Extract the small generation plan produced before pack.js generation."""

    for filename in ("pack_plan.json", "plan.json"):
        marked = extract_marked_json(response_text, filename)
        if marked is not None:
            return marked

    candidates: list[dict[str, Any]] = []
    for info, body in fenced_blocks(response_text):
        lowered = info.lower()
        if "json" not in lowered and "plan" not in lowered:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)

    for candidate in candidates:
        if (
            candidate.get("kind") == "gameplay-pack-generation-plan"
            or candidate.get("schema") == "game.gameplayPackGenerationPlan.v1"
            or "steps" in candidate
        ):
            return dict(candidate)
    if candidates:
        return dict(candidates[0])

    if looks_like_model_summary(response_text):
        raise ValueError(
            "local AI summarized the runtime/examples instead of returning BEGIN pack_plan.json / END pack_plan.json"
        )
    raise ValueError("could not extract pack_plan.json from local AI response")


def synthesize_pack_plan(
    *,
    project: str,
    scenario: ScenarioSurface,
    slug: str,
    prompt: str,
) -> dict[str, Any]:
    """Create a deterministic fallback plan for tests and fake pack responses.

    Real local-AI runs use a model-generated plan. Existing fake-response tests
    can still exercise the pack generation/extraction path without needing a
    second fake file.
    """

    event = "onStart" if scenario.kind == "encounter" else "onCutsceneResolved"
    commands = ["showHudMessage", "spawnWave"]
    if "objective" in prompt.lower():
        commands.append("setObjective")
    return {
        "schema": "game.gameplayPackGenerationPlan.v1",
        "kind": "gameplay-pack-generation-plan",
        "project": project,
        "scenario": {
            "id": scenario.scenario_id,
            "kind": scenario.kind,
            "apiCall": scenario.api_call,
            **({"cutsceneId": scenario.cutscene_id} if scenario.cutscene_id else {}),
        },
        "packId": f"pack.experimental.{slug}",
        "title": slug.replace("-", " ").title(),
        "summary": prompt.strip(),
        "steps": [
            {
                "id": "step-1",
                "event": event,
                **({"cutsceneId": scenario.cutscene_id} if scenario.cutscene_id and event == "onCutsceneResolved" else {}),
                "summary": prompt.strip(),
                "commands": commands,
            }
        ],
    }


def normalize_pack_plan(
    plan: dict[str, Any],
    *,
    project: str,
    scenario: ScenarioSurface,
    slug: str,
    prompt: str,
) -> dict[str, Any]:
    normalized = dict(plan)
    normalized.setdefault("schema", "game.gameplayPackGenerationPlan.v1")
    normalized.setdefault("kind", "gameplay-pack-generation-plan")
    normalized["project"] = project
    normalized.setdefault("packId", f"pack.experimental.{slug}")
    normalized.setdefault("title", slug.replace("-", " ").title())
    normalized.setdefault("summary", prompt.strip())

    scenario_payload = normalized.get("scenario")
    if not isinstance(scenario_payload, dict):
        scenario_payload = {}
    scenario_payload = dict(scenario_payload)
    scenario_payload["id"] = scenario.scenario_id
    scenario_payload["kind"] = scenario.kind
    scenario_payload["apiCall"] = scenario.api_call
    if scenario.cutscene_id:
        scenario_payload.setdefault("cutsceneId", scenario.cutscene_id)
    normalized["scenario"] = scenario_payload

    steps = normalized.get("steps")
    if not isinstance(steps, list) or not steps:
        normalized["steps"] = synthesize_pack_plan(
            project=project,
            scenario=scenario,
            slug=slug,
            prompt=prompt,
        )["steps"]
    return normalized


def validate_pack_plan(
    plan: dict[str, Any],
    *,
    scenario: ScenarioSurface,
    prompt: str = "",
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    prompt_lower = str(prompt or "").lower()

    if plan.get("kind") != "gameplay-pack-generation-plan":
        errors.append("pack plan kind must be gameplay-pack-generation-plan")
    scenario_payload = plan.get("scenario") if isinstance(plan.get("scenario"), dict) else {}
    if scenario_payload.get("id") != scenario.scenario_id:
        errors.append(f"pack plan scenario.id must be {scenario.scenario_id!r}")
    if scenario_payload.get("kind") != scenario.kind:
        errors.append(f"pack plan scenario.kind must be {scenario.kind!r}")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("pack plan must include at least one step")
        return errors, warnings

    allowed_events = set(scenario.known_events)
    allowed_commands = set(scenario.known_commands)
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            errors.append(f"pack plan step {index} must be an object")
            continue
        event = step.get("event")
        if event not in allowed_events:
            errors.append(
                f"pack plan step {index} event {event!r} must be one of {sorted(allowed_events)}"
            )
        commands = step.get("commands")
        if not isinstance(commands, list) or not commands:
            warnings.append(f"pack plan step {index} has no command list")
            continue
        for command in commands:
            if command not in allowed_commands:
                errors.append(
                    f"pack plan step {index} command {command!r} must be one of {sorted(allowed_commands)}"
                )
            if (
                command == "setHostileHealthMultiplier"
                and "additive only" in prompt_lower
                and not re.search(r"\b(all|every|existing|global|globally)\b.*\b(hostile|hostiles|boarder|boarders|enemy|enemies)\b", prompt_lower)
            ):
                errors.append(
                    "pack plan includes setHostileHealthMultiplier even though the prompt says additive only; "
                    "use actor-local healthMultiplier inside spawnWave instead"
                )
    return errors, warnings


def extract_pack_js(response_text: str) -> str:
    marked = marked_file_block(response_text, "pack.js")
    if marked and "defineGameplayPack" in marked:
        return marked.strip() + "\n"

    blocks = fenced_blocks(response_text)
    for info, body in blocks:
        lowered = info.lower()
        if "pack.js" in lowered and "defineGameplayPack" in body:
            return body.strip() + "\n"
    for info, body in blocks:
        lowered = info.lower()
        if ("javascript" in lowered or "js" in lowered) and "defineGameplayPack" in body:
            return body.strip() + "\n"
    for _, body in blocks:
        if "defineGameplayPack" in body and "export default" in body:
            return body.strip() + "\n"
    if looks_like_model_summary(response_text):
        raise ValueError(
            "local AI summarized the runtime/examples instead of returning BEGIN pack.js / END pack.js"
        )
    raise ValueError("could not extract pack.js from local AI response")


def extract_manifest(response_text: str) -> dict[str, Any]:
    marked = marked_file_block(response_text, "manifest.json")
    if marked:
        try:
            parsed = json.loads(marked)
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse marked manifest.json: {exc}") from exc
        if isinstance(parsed, dict):
            return dict(parsed)
        raise ValueError("marked manifest.json was not a JSON object")

    candidates: list[dict[str, Any]] = []
    for info, body in fenced_blocks(response_text):
        lowered = info.lower()
        if "json" not in lowered and "manifest" not in lowered:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    for candidate in candidates:
        if (
            "manifestVersion" in candidate
            or candidate.get("kind") == "gameplay-pack-js"
            or "targets" in candidate
        ):
            return dict(candidate)
    if candidates:
        return dict(candidates[0])
    if looks_like_model_summary(response_text):
        raise ValueError(
            "local AI summarized the runtime/examples instead of returning BEGIN manifest.json / END manifest.json"
        )
    raise ValueError("could not extract manifest.json from local AI response")


def normalize_manifest(
    manifest: dict[str, Any],
    *,
    project: str,
    scenario: ScenarioSurface,
    slug: str,
    prompt: str,
) -> dict[str, Any]:
    normalized = dict(manifest)
    normalized.setdefault("schema", "game.gameplayPackJsManifest.v1")
    normalized.setdefault("kind", "gameplay-pack-js")
    normalized.setdefault("manifestVersion", "gameplay-pack-js.manifest.v1")
    normalized.setdefault("id", f"pack.experimental.{slug}")
    normalized.setdefault("title", slug.replace("-", " ").title())
    normalized.setdefault("version", "0.1.0")
    normalized.setdefault(
        "description",
        f"Experimental locally generated gameplay pack for {scenario.scenario_id}: {prompt.strip()[:160]}",
    )
    normalized["entry"] = "pack.js"
    normalized["experimental"] = True
    normalized["generated"] = True
    normalized["defaultEnabled"] = False
    normalized["hiddenFromLobby"] = True

    targets = normalized.get("targets")
    if not isinstance(targets, dict):
        targets = {}
    targets = dict(targets)
    targets["projectId"] = project
    targets[scenario.manifest_target_key()] = scenario.scenario_id
    if scenario.cutscene_id:
        targets.setdefault("cutsceneId", scenario.cutscene_id)
    normalized["targets"] = targets

    safety = normalized.get("safety")
    if not isinstance(safety, dict):
        safety = {}
    safety = dict(safety)
    safety["experimentalGeneratedPack"] = True
    safety["doNotAutoEnable"] = True
    safety["doNotModifyBaseScenarioFiles"] = True
    safety["noArbitraryEngineMutation"] = True
    normalized["safety"] = safety
    return normalized


def build_plan_prompt(
    *,
    project: str,
    scenario: ScenarioSurface,
    user_prompt: str,
    slug: str,
) -> str:
    plan_contract = f"""BEGIN pack_plan.json
{{
  "schema": "game.gameplayPackGenerationPlan.v1",
  "kind": "gameplay-pack-generation-plan",
  "project": "{project}",
  "scenario": {{
    "id": "{scenario.scenario_id}",
    "kind": "{scenario.kind}",
    "apiCall": "{scenario.api_call}"
  }},
  "packId": "pack.experimental.{slug}",
  "title": "Short Descriptive Title",
  "summary": "One sentence summary of the generated pack.",
  "steps": [
    {{
      "id": "step-1",
      "event": "{'onStart' if scenario.kind == 'encounter' else 'onCutsceneResolved'}",
      "summary": "One small behavior step.",
      "commands": ["showHudMessage"]
    }}
  ]
}}
END pack_plan.json"""

    cutscene_rule = (
        f'\n- For bay-entry timing, use event "onCutsceneResolved" with cutsceneId "{scenario.cutscene_id}".'
        if scenario.cutscene_id
        else ""
    )
    return f"""TASK: Break a requested gameplay pack into a small implementation plan.

Return EXACTLY one JSON file using the markers below.
Do not write pack.js.
Do not write manifest.json.
Do not explain anything.
Do not ask questions.

REQUIRED OUTPUT CONTRACT:
{plan_contract}

USER INTENT:
{user_prompt.strip()}

HARD TARGET:
- Project: {project}
- Existing scenario id: {scenario.scenario_id}
- Existing scenario kind: {scenario.kind}
- Required API shape: {scenario.api_call}
- Use exactly the scenario id "{scenario.scenario_id}".
- Do not create a new scenario id.
- The generated behavior must be additive to the existing scenario.
- If the user intent says "additive only", do not include setHostileHealthMultiplier unless the user explicitly asks to change all existing hostiles.{cutscene_rule}

CURRENTLY ALLOWED EVENTS:
{json.dumps(list(scenario.known_events), indent=2)}

CURRENTLY ALLOWED COMMANDS:
{json.dumps(list(scenario.known_commands), indent=2)}

PLAN RULES:
- Use one to three steps.
- Each step must name one allowed event.
- Each step must list only allowed commands.
- Keep each step small enough to implement directly in one pack.js handler.
- The plan should capture the user's requested behavior, but do not invent engine APIs.
- If an idea cannot be expressed with allowed events/commands, omit it from the plan summary rather than inventing new APIs.

FINAL REMINDER:
Return only this exact one-file marker format, filled in with the plan:

{plan_contract}
"""


def build_pack_prompt(
    *,
    repo_root: Path,
    project: str,
    scenario: ScenarioSurface,
    user_prompt: str,
    plan: dict[str, Any],
) -> str:
    example_sections: list[str] = []
    for rel_path in scenario.examples:
        path = repo_root / rel_path
        try:
            body = read_text(path).strip()
        except OSError:
            body = ""
        if body:
            example_sections.append(f"### Reference file: {rel_path}\n\n```text\n{body}\n```")

    cutscene_guidance = (
        f'\n- If the plan uses the bay-entry trigger, use section.onCutsceneResolved("{scenario.cutscene_id}", ...).'
        if scenario.cutscene_id
        else ""
    )
    file_contract = f"""BEGIN manifest.json
{{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.experimental.short-descriptive-id",
  "title": "Short Descriptive Title",
  "version": "0.1.0",
  "entry": "pack.js",
  "targets": {{
    "{scenario.manifest_target_key()}": "{scenario.scenario_id}"
  }},
  "experimental": true,
  "generated": true,
  "defaultEnabled": false,
  "hiddenFromLobby": true
}}
END manifest.json

BEGIN pack.js
export default defineGameplayPack({{
  id: "pack.experimental.short-descriptive-id",
  title: "Short Descriptive Title",
  version: "0.1.0",

  setup(pack) {{
    // Use exactly this target shape:
    // {scenario.api_call}
  }}
}});
END pack.js"""

    return f"""TASK: Generate one experimental JavaScript gameplay pack for Main Computer from the approved plan.

Return EXACTLY two files using the file markers below.
Do not explain anything.
Do not summarize the examples.
Do not ask questions.
Do not complete or rewrite the runtime.
Do not include markdown fences unless they are inside the markers.

REQUIRED OUTPUT CONTRACT:
{file_contract}

USER INTENT:
{user_prompt.strip()}

APPROVED SMALL PLAN:
```json
{json.dumps(plan, indent=2, sort_keys=True)}
```

HARD TARGET:
- Project: {project}
- Existing scenario id: {scenario.scenario_id}
- Existing scenario kind: {scenario.kind}
- Required API shape: {scenario.api_call}
- Use exactly the scenario id "{scenario.scenario_id}".
- Do not create a new scenario id.
- Do not modify base scenario files.
- The generated pack must be additive to the existing scenario.
- If the approved plan does not list setHostileHealthMultiplier, do not call setHostileHealthMultiplier.
- Use actor-local fields inside spawnWave actors, such as healthMultiplier and scale, for a single tougher/larger spawned actor.{cutscene_guidance}

CURRENTLY ALLOWED EVENTS:
{json.dumps(list(scenario.known_events), indent=2)}

CURRENTLY ALLOWED COMMANDS:
{json.dumps(list(scenario.known_commands), indent=2)}

ONLY AVAILABLE {gameplay_context_name(scenario).upper()} METHODS:
{json.dumps(list(allowed_context_methods(scenario)), indent=2)}

API RULES:
- Use only the methods listed in ONLY AVAILABLE {gameplay_context_name(scenario).upper()} METHODS on the {gameplay_context_name(scenario)} object.
- Do not call {gameplay_context_name(scenario)}.getMetadata(...), {gameplay_context_name(scenario)}.getState(...), {gameplay_context_name(scenario)}.query(...), or any engine-looking method not listed above.
- If the plan needs a random choice, define a local constant array in pack.js and choose from it with Math.random(); do not ask the {gameplay_context_name(scenario)} object for metadata.

SAFETY RULES:
- pack.js must use export default defineGameplayPack(...).
- Do not use imports.
- Do not use these globals: {", ".join(FORBIDDEN_GLOBALS)}.
- Do not use raw renderer, DOM, network, filesystem, browser storage, or save-state access.
- Manifest must keep experimental=true, generated=true, defaultEnabled=false, hiddenFromLobby=true.
- Generated behavior must be additive only.
- Implement only behavior described by the approved small plan.

REFERENCE LOCAL GAME PACKS FOR STYLE ONLY:
Do not summarize these files. Do not complete them. Use them only as examples of the authoring style.

{chr(10).join(example_sections)}

FINAL REMINDER:
Return only this exact two-file marker format, filled in with the generated pack:

{file_contract}
"""

def call_local_ai(
    *,
    repo_root: Path,
    prompt_text: str,
    output_dir: Path,
    model: str | None,
    stage: str,
) -> tuple[str, dict[str, Any]]:
    """Call the configured local model and return response text plus an audit trace.

    The smoke script is intentionally used as a trust/debugging tool, so callers
    should expose this trace in stdout and validation.json instead of hiding that
    an AI stage occurred behind labels like "reviewed-plan".
    """

    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from main_computer.local_model_prompt_component_v1 import run_local_model_prompt_call

    trace_dir = output_dir / f"local_model_{stage}_call"
    started = time.perf_counter()
    result = run_local_model_prompt_call(
        prompt_text=prompt_text,
        output_dir=trace_dir,
        model=model,
    )
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    call_trace = {
        "stage": stage,
        "provider": "local-ai",
        "model": model or "configured-local-model",
        "traceDir": str(trace_dir),
        "elapsedMs": elapsed_ms,
        "ok": bool(getattr(result, "ok", False)),
    }
    if not result.ok:
        call_trace["details"] = getattr(result, "details", {})
        raise RuntimeError(f"local AI call failed: {result.details}")
    response_text = str(result.provided_state.get("local_model_response_text") or "")
    if not response_text.strip():
        call_trace["ok"] = False
        raise RuntimeError("local AI returned an empty response")
    return response_text, call_trace


def scenario_api_pattern(scenario: ScenarioSurface) -> re.Pattern[str]:
    if scenario.kind == "encounter":
        return re.compile(r"pack\s*\.\s*encounter\s*\(\s*(['\"`])" + re.escape(scenario.scenario_id) + r"\1")
    return re.compile(r"pack\s*\.\s*section\s*\(\s*(['\"`])" + re.escape(scenario.scenario_id) + r"\1")


def allowed_context_methods(scenario: ScenarioSurface) -> tuple[str, ...]:
    """Methods generated packs may call on the scenario context object."""

    return tuple(dict.fromkeys((*scenario.known_events, *scenario.known_commands)))


def gameplay_context_name(scenario: ScenarioSurface) -> str:
    return "encounter" if scenario.kind == "encounter" else "section"


def find_context_variable_names(pack_js: str, scenario: ScenarioSurface) -> tuple[str, ...]:
    """Return likely parameter names for the current gameplay-pack context."""

    context_name = gameplay_context_name(scenario)
    names: list[str] = [context_name]
    pack_method = "encounter" if scenario.kind == "encounter" else "section"
    registration = re.compile(
        r"pack\s*\.\s*"
        + pack_method
        + r"\s*\(\s*(['\"`])"
        + re.escape(scenario.scenario_id)
        + r"\1\s*,\s*(?:\(\s*([A-Za-z_$][\w$]*)\s*\)|([A-Za-z_$][\w$]*))\s*=>"
    )
    for match in registration.finditer(pack_js or ""):
        name = match.group(2) or match.group(3)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def find_unsupported_context_api_calls(pack_js: str, scenario: ScenarioSurface) -> list[dict[str, str]]:
    """Find direct calls to unsupported methods on the generated pack context.

    This is intentionally narrow and conservative.  It is not a JavaScript
    parser; it catches the common local-model failure mode where the pack calls
    an engine-looking method such as encounter.getMetadata(...) that is not part
    of the YAGNI gameplay-pack API exposed by the current harness.
    """

    allowed = set(allowed_context_methods(scenario))
    unsupported: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for context_name in find_context_variable_names(pack_js, scenario):
        pattern = re.compile(r"\b" + re.escape(context_name) + r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
        for match in pattern.finditer(pack_js or ""):
            method = match.group(1)
            if method in allowed:
                continue
            line = str((pack_js or "").count("\n", 0, match.start()) + 1)
            key = (context_name, method, line)
            if key in seen:
                continue
            seen.add(key)
            unsupported.append({
                "context": context_name,
                "method": method,
                "line": line,
            })
    return unsupported



def sample_event_for_plan_method(event_name: str, scenario: ScenarioSurface) -> dict[str, Any] | None:
    """Return one representative runtime event for a reviewed plan event method.

    This smoke script cannot decide whether the generated pack fulfills an open
    ended creative prompt, but it can prove that the reviewed plan's first
    gameplay hook actually emits commands through the current runtime harness.
    """

    event = str(event_name or "").strip()
    if event == "onStart":
        return {"type": "start"}
    if event == "onHostilesDefeated":
        return {"type": "hostiles-defeated", "count": 1}
    if event == "onAllHostilesDefeated":
        return {"type": "all-hostiles-defeated"}
    if event == "onDestinationReached":
        return {"type": "destination-reached", "destination": "haven-orbit"}
    if event == "onPlayerDefeated":
        return {"type": "player-defeated"}
    if event == "onCutsceneResolved":
        if scenario.cutscene_id:
            return {"type": "cutscene-resolved", "cutsceneId": scenario.cutscene_id}
        return {"type": "cutscene-resolved"}
    return None


def derive_sample_events_from_plan(plan: dict[str, Any], scenario: ScenarioSurface) -> tuple[list[dict[str, Any]], list[str]]:
    """Derive a compact set of runtime sample events from the reviewed plan."""

    warnings: list[str] = []
    sample_events: list[dict[str, Any]] = []
    seen: set[str] = set()
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        runtime_event = sample_event_for_plan_method(str(step.get("event") or ""), scenario)
        if runtime_event is None:
            warnings.append(f"no sample runtime event for plan event {step.get('event')!r}")
            continue
        key = json.dumps(runtime_event, sort_keys=True)
        if key not in seen:
            seen.add(key)
            sample_events.append(runtime_event)
    return sample_events, warnings


def validate_candidate(
    *,
    repo_root: Path,
    output_dir: Path,
    scenario: ScenarioSurface,
    manifest: dict[str, Any],
    pack_js: str,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}

    if manifest.get("kind") != "gameplay-pack-js":
        errors.append("manifest.kind must be gameplay-pack-js")
    if manifest.get("entry") != "pack.js":
        errors.append("manifest.entry must be pack.js")
    if manifest.get("defaultEnabled") is not False:
        errors.append("experimental generated packs must set defaultEnabled false")
    if manifest.get("hiddenFromLobby") is not True:
        errors.append("experimental generated packs must set hiddenFromLobby true")
    targets = manifest.get("targets") if isinstance(manifest.get("targets"), dict) else {}
    target_key = scenario.manifest_target_key()
    if targets.get(target_key) != scenario.scenario_id:
        errors.append(f"manifest.targets.{target_key} must be {scenario.scenario_id!r}")

    if "export default defineGameplayPack" not in pack_js:
        errors.append("pack.js must contain export default defineGameplayPack(...)")
    if not scenario_api_pattern(scenario).search(pack_js):
        errors.append(f"pack.js must register the requested scenario with {scenario.api_call}")

    if scenario.kind == "encounter" and re.search(r"pack\s*\.\s*section\s*\(", pack_js):
        errors.append("encounter smoke target must not use pack.section(...)")
    if scenario.kind == "section" and re.search(r"pack\s*\.\s*encounter\s*\(", pack_js):
        errors.append("section smoke target must not use pack.encounter(...)")

    unsupported_api_calls = find_unsupported_context_api_calls(pack_js, scenario)
    details["unsupportedContextApiCalls"] = unsupported_api_calls
    for call in unsupported_api_calls:
        errors.append(
            f"unsupported {call['context']} API call {call['context']}.{call['method']}(...)"
            f" on line {call['line']}; allowed methods are: "
            f"{', '.join(allowed_context_methods(scenario))}"
        )

    sample_events, sample_event_warnings = derive_sample_events_from_plan(plan or {}, scenario)
    warnings.extend(sample_event_warnings)
    details["sampleEvents"] = sample_events

    node_path = shutil.which("node")
    runtime_path = repo_root / "main_computer/web/applications/scripts/gameplay-pack-runtime.js"
    pack_path = output_dir / "pack.js"
    if node_path and runtime_path.exists():
        syntax = subprocess.run(
            [node_path, "--check", str(pack_path)],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        details["node_check"] = {
            "returncode": syntax.returncode,
            "stdout": syntax.stdout,
            "stderr": syntax.stderr,
        }
        if syntax.returncode != 0:
            errors.append("node --check failed for pack.js")

        harness_script = """
const fs = require("fs");
const runtime = require(process.argv[1]);
const source = fs.readFileSync(process.argv[2], "utf8");
const scenarioKind = process.argv[3];
const scenarioId = process.argv[4];
let sampleEvents = [];
try {
  sampleEvents = JSON.parse(process.argv[5] || "[]");
} catch (err) {
  sampleEvents = [];
}
const out = { sampleEvents };
try {
  out.sourceValidation = runtime.validateGameplayPackJsSource(source);
  if (out.sourceValidation && out.sourceValidation.ok) {
    const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
    out.harnessSummary = runtime.gameplayPackCommandHarnessSummary(harness);
    const dispatched = [];
    for (const event of sampleEvents) {
      const commands = scenarioKind === "section"
        ? runtime.dispatchGameplayPackSectionEvent(harness, scenarioId, event)
        : runtime.dispatchGameplayPackEncounterEvent(harness, scenarioId, event);
      dispatched.push({ event, commandCount: commands.length, commandTypes: commands.map((command) => command.type), commands });
    }
    out.sampleDispatch = {
      scenarioKind,
      scenarioId,
      eventCount: sampleEvents.length,
      commandCount: dispatched.reduce((total, item) => total + item.commandCount, 0),
      commandTypes: Array.from(new Set(dispatched.flatMap((item) => item.commandTypes))),
      dispatched,
      harnessSummary: runtime.gameplayPackCommandHarnessSummary(harness)
    };
  }
} catch (err) {
  out.error = String(err && err.message || err);
}
console.log(JSON.stringify(out));
"""
        harness = subprocess.run(
            [
                node_path,
                "-e",
                harness_script,
                str(runtime_path),
                str(pack_path),
                scenario.kind,
                scenario.scenario_id,
                json.dumps(sample_events),
            ],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        details["runtime_validation_raw"] = {
            "returncode": harness.returncode,
            "stdout": harness.stdout,
            "stderr": harness.stderr,
        }
        if harness.returncode != 0:
            errors.append("gameplay-pack-runtime validation command failed")
        else:
            try:
                runtime_payload = json.loads(harness.stdout)
            except json.JSONDecodeError:
                errors.append("gameplay-pack-runtime validation returned non-JSON output")
                runtime_payload = {}
            details["runtime_validation"] = runtime_payload
            source_validation = runtime_payload.get("sourceValidation") if isinstance(runtime_payload, dict) else {}
            if not source_validation or not source_validation.get("ok"):
                errors.append("gameplay-pack-runtime rejected pack.js source")
            if isinstance(runtime_payload, dict) and runtime_payload.get("error"):
                errors.append(f"gameplay-pack-runtime sample dispatch failed: {runtime_payload.get('error')}")
            summary = runtime_payload.get("harnessSummary") if isinstance(runtime_payload, dict) else {}
            if summary:
                ids = summary.get("encounterIds") if scenario.kind == "encounter" else summary.get("sectionIds")
                if scenario.scenario_id not in (ids or []):
                    errors.append(f"runtime harness summary did not register {scenario.scenario_id!r}")
                if not summary.get("handlersRegistered"):
                    errors.append("runtime harness registered no handlers")
            elif source_validation and source_validation.get("ok"):
                errors.append("gameplay-pack-runtime did not return a harness summary")

            sample_dispatch = runtime_payload.get("sampleDispatch") if isinstance(runtime_payload, dict) else {}
            if sample_events:
                if not sample_dispatch:
                    errors.append("gameplay-pack-runtime did not return sample dispatch results")
                elif int(sample_dispatch.get("commandCount") or 0) < 1:
                    errors.append("sample scenario event emitted no gameplay pack commands")
    else:
        warnings.append("node or gameplay-pack-runtime.js unavailable; skipped JS runtime validation")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "scenario": asdict(scenario),
        "details": details,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke-generate one experimental JS gameplay pack with the local AI."
    )
    parser.add_argument("--project", default="webgl-demo", help="Project id. Defaults to webgl-demo.")
    parser.add_argument("--scenario", required=True, help="Existing scenario/section id to extend.")
    parser.add_argument("--prompt", default=None, help="User prompt describing additive pack behavior. Required for plan generation; optional with --from-plan if prompt.txt exists next to the plan.")
    parser.add_argument("--slug", default=None, help="Optional generated pack folder slug.")
    parser.add_argument("--repo-root", default=None, help="Repository root. Defaults to this script's parent repo.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Output root. Defaults to game_projects/<project>/gameplay_packs/experimental.",
    )
    parser.add_argument("--fake-response", default=None, help="Read a fake local-AI pack response from this file.")
    parser.add_argument("--fake-plan-response", default=None, help="Read a fake local-AI plan response from this file.")
    parser.add_argument(
        "--from-plan",
        default=None,
        help="Generate manifest.json and pack.js from a reviewed plan.json instead of asking the model for a new plan.",
    )
    parser.add_argument(
        "--auto-approve-plan",
        action="store_true",
        help="Generate a plan and immediately generate pack files from that unreviewed plan in one run.",
    )
    parser.add_argument("--plan-only", action="store_true", help="Stop after writing and validating the small generation plan.")
    parser.add_argument("--model", default=None, help="Optional local Ollama model override.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing generated pack folder.")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else repo_root_from_script().resolve()
    project = str(args.project or "webgl-demo").strip() or "webgl-demo"
    scenario_id = str(args.scenario or "").strip()
    if scenario_id not in SCENARIO_SURFACES:
        return {
            "ok": False,
            "error": f"unknown scenario {scenario_id!r}",
            "knownScenarios": sorted(SCENARIO_SURFACES),
        }
    scenario = SCENARIO_SURFACES[scenario_id]

    from_plan_path = Path(args.from_plan).resolve() if args.from_plan else None
    if args.plan_only and from_plan_path:
        return {
            "ok": False,
            "error": "--plan-only and --from-plan cannot be combined",
        }
    if args.plan_only and args.auto_approve_plan:
        return {
            "ok": False,
            "error": "--plan-only and --auto-approve-plan cannot be combined",
        }
    if from_plan_path and args.auto_approve_plan:
        return {
            "ok": False,
            "error": "--from-plan and --auto-approve-plan cannot be combined",
        }

    reviewed_plan: dict[str, Any] | None = None
    reviewed_plan_text = ""
    prompt_from_plan_dir = ""
    if from_plan_path:
        try:
            reviewed_plan_text = read_text(from_plan_path)
            parsed_reviewed_plan = json.loads(reviewed_plan_text)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"could not read --from-plan {from_plan_path}: {type(exc).__name__}: {exc}",
            }
        if not isinstance(parsed_reviewed_plan, dict):
            return {
                "ok": False,
                "error": f"--from-plan must point to a JSON object: {from_plan_path}",
            }
        reviewed_plan = dict(parsed_reviewed_plan)
        prompt_path = from_plan_path.parent / "prompt.txt"
        if prompt_path.exists():
            try:
                prompt_from_plan_dir = read_text(prompt_path).strip()
            except OSError:
                prompt_from_plan_dir = ""

    user_prompt = str(args.prompt or prompt_from_plan_dir or "").strip()
    if not user_prompt:
        if reviewed_plan:
            user_prompt = str(reviewed_plan.get("summary") or reviewed_plan.get("title") or "").strip()
        if not user_prompt:
            return {
                "ok": False,
                "error": "--prompt must be non-empty unless --from-plan has a sibling prompt.txt or plan summary",
            }

    slug_base = slugify(
        args.slug
        or (from_plan_path.parent.name if from_plan_path else "")
        or user_prompt
    )
    folder_name = slug_base if args.slug or from_plan_path else f"generated-{_utc_stamp()}-{slug_base}"
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else repo_root / "game_projects" / project / "gameplay_packs" / "experimental"
    )
    output_dir = output_root / folder_name

    # Important for the reviewed-plan workflow: users commonly pass
    # --from-plan game_projects/.../<slug>/plan.json --overwrite.  Read the
    # plan before clearing the output directory so the reviewed plan is not
    # deleted before it can be used.
    if output_dir.exists():
        if not args.overwrite:
            return {
                "ok": False,
                "error": f"output directory already exists: {output_dir}",
                "hint": "choose a different --slug or pass --overwrite",
            }
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_text(output_dir / "prompt.txt", user_prompt + "\n")

    ai_calls: list[dict[str, Any]] = []
    plan_provider: str
    pack_provider: str | None = None
    plan_model: str | None
    pack_model: str | None = None
    if reviewed_plan is not None:
        plan_provider = "reviewed-plan"
        plan_model = str(from_plan_path)
        plan_response_text = (
            f"Loaded reviewed plan from {from_plan_path}\n\n"
            "BEGIN pack_plan.json\n"
            + json.dumps(reviewed_plan, indent=2, sort_keys=True)
            + "\nEND pack_plan.json\n"
        )
        write_text(output_dir / "generation_plan_prompt.md", "Skipped: --from-plan supplied a reviewed plan.\n")
    else:
        plan_prompt = build_plan_prompt(
            project=project,
            scenario=scenario,
            user_prompt=user_prompt,
            slug=slug_base,
        )
        write_text(output_dir / "generation_plan_prompt.md", plan_prompt)

        if args.fake_plan_response:
            plan_response_text = read_text(Path(args.fake_plan_response))
            plan_provider = "fake-response"
            plan_model = str(Path(args.fake_plan_response))
        elif args.fake_response:
            plan = synthesize_pack_plan(
                project=project,
                scenario=scenario,
                slug=slug_base,
                prompt=user_prompt,
            )
            plan_response_text = (
                "BEGIN pack_plan.json\n"
                + json.dumps(plan, indent=2, sort_keys=True)
                + "\nEND pack_plan.json\n"
            )
            plan_provider = "synthetic-plan-from-fake-response"
            plan_model = str(Path(args.fake_response))
        else:
            plan_response_text, plan_call = call_local_ai(
                repo_root=repo_root,
                prompt_text=plan_prompt,
                output_dir=output_dir,
                model=args.model,
                stage="plan",
            )
            ai_calls.append(plan_call)
            plan_provider = "local-ai"
            plan_model = args.model or "configured-local-model"

    write_text(output_dir / "generation_plan.md", plan_response_text)

    plan_errors: list[str] = []
    plan_warnings: list[str] = []
    try:
        raw_plan = reviewed_plan if reviewed_plan is not None else extract_pack_plan(plan_response_text)
        plan = normalize_pack_plan(
            raw_plan,
            project=project,
            scenario=scenario,
            slug=slug_base,
            prompt=user_prompt,
        )
        plan_errors, plan_warnings = validate_pack_plan(plan, scenario=scenario, prompt=user_prompt)
        write_json(output_dir / "plan.json", plan)
    except Exception as exc:
        plan = {}
        plan_errors = [f"{type(exc).__name__}: {exc}"]

    should_generate_pack = reviewed_plan is not None or bool(args.auto_approve_plan)
    if plan_errors or args.plan_only or not should_generate_pack:
        validation = {
            "ok": not plan_errors,
            "errors": plan_errors,
            "warnings": plan_warnings,
            "scenario": asdict(scenario),
            "plan": plan,
            "details": {
                "stage": "plan",
                "planOnly": bool(args.plan_only or not should_generate_pack),
                "reviewRequired": not bool(plan_errors) and not should_generate_pack,
                "autoApprovedPlan": bool(args.auto_approve_plan),
                "fromPlan": str(from_plan_path) if from_plan_path else None,
                "planProvider": plan_provider,
                "planModel": plan_model,
                "packProvider": pack_provider,
                "packModel": pack_model,
                "aiCalls": ai_calls,
                "aiCallCount": len(ai_calls),
                "nextCommand": (
                    "python scripts/smoke_generate_gameplay_pack_with_local_ai.py "
                    f"--scenario {scenario.scenario_id} --slug {folder_name} "
                    f"--from-plan {output_dir / 'plan.json'} --overwrite"
                    if not bool(plan_errors) and not should_generate_pack
                    else None
                ),
            },
        }
        write_json(output_dir / "validation.json", validation)
    else:
        pack_prompt = build_pack_prompt(
            repo_root=repo_root,
            project=project,
            scenario=scenario,
            user_prompt=user_prompt,
            plan=plan,
        )
        write_text(output_dir / "generation_prompt.md", pack_prompt)
        write_text(output_dir / "pack_generation_prompt.md", pack_prompt)

        if args.fake_response:
            response_text = read_text(Path(args.fake_response))
            pack_provider = "fake-response"
            pack_model = str(Path(args.fake_response))
        else:
            response_text, pack_call = call_local_ai(
                repo_root=repo_root,
                prompt_text=pack_prompt,
                output_dir=output_dir,
                model=args.model,
                stage="pack",
            )
            ai_calls.append(pack_call)
            pack_provider = "local-ai"
            pack_model = args.model or "configured-local-model"
        write_text(output_dir / "generation.md", response_text)

        try:
            pack_js = extract_pack_js(response_text)
            manifest = normalize_manifest(
                extract_manifest(response_text),
                project=project,
                scenario=scenario,
                slug=slug_base,
                prompt=user_prompt,
            )
            write_text(output_dir / "pack.js", pack_js)
            write_json(output_dir / "manifest.json", manifest)
            validation = validate_candidate(
                repo_root=repo_root,
                output_dir=output_dir,
                scenario=scenario,
                manifest=manifest,
                pack_js=pack_js,
                plan=plan,
            )
            validation["plan"] = plan
            validation.setdefault("warnings", []).extend(plan_warnings)
            validation.setdefault("details", {})["stage"] = "pack"
            validation.setdefault("details", {})["fromPlan"] = str(from_plan_path) if from_plan_path else None
            validation.setdefault("details", {})["autoApprovedPlan"] = bool(args.auto_approve_plan)
            validation.setdefault("details", {})["planProvider"] = plan_provider
            validation.setdefault("details", {})["planModel"] = plan_model
            validation.setdefault("details", {})["packProvider"] = pack_provider
            validation.setdefault("details", {})["packModel"] = pack_model
            validation.setdefault("details", {})["aiCalls"] = ai_calls
            validation.setdefault("details", {})["aiCallCount"] = len(ai_calls)
        except Exception as exc:
            validation = {
                "ok": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "warnings": plan_warnings,
                "scenario": asdict(scenario),
                "plan": plan,
                "details": {
                    "stage": "pack",
                    "fromPlan": str(from_plan_path) if from_plan_path else None,
                    "autoApprovedPlan": bool(args.auto_approve_plan),
                    "planProvider": plan_provider,
                    "planModel": plan_model,
                    "packProvider": pack_provider,
                    "packModel": pack_model,
                    "aiCalls": ai_calls,
                    "aiCallCount": len(ai_calls),
                },
            }

        write_json(output_dir / "validation.json", validation)

    return {
        "ok": bool(validation.get("ok")),
        "scenario": scenario.scenario_id,
        "project": project,
        "outputDir": str(output_dir),
        "stage": validation.get("details", {}).get("stage"),
        "autoApprovedPlan": bool(args.auto_approve_plan),
        "planProvider": plan_provider,
        "planModel": plan_model,
        "packProvider": pack_provider,
        "packModel": pack_model,
        "aiCalls": ai_calls,
        "aiCallCount": len(ai_calls),
        "provider": pack_provider or plan_provider,
        "model": pack_model or plan_model,
        "files": {
            "prompt": str(output_dir / "prompt.txt"),
            "generationPlanPrompt": str(output_dir / "generation_plan_prompt.md"),
            "generationPlan": str(output_dir / "generation_plan.md"),
            "plan": str(output_dir / "plan.json"),
            "generationPrompt": str(output_dir / "generation_prompt.md"),
            "packGenerationPrompt": str(output_dir / "pack_generation_prompt.md"),
            "generation": str(output_dir / "generation.md"),
            "manifest": str(output_dir / "manifest.json"),
            "pack": str(output_dir / "pack.js"),
            "validation": str(output_dir / "validation.json"),
        },
        "validation": validation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = run(args)
    json_stdout(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
