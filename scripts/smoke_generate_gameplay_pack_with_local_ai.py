#!/usr/bin/env python3
"""Smoke-generate an experimental JS gameplay pack with the local AI.

This is intentionally a narrow smoke tool. It asks the configured local model to
produce one candidate gameplay pack for one existing scenario/section, writes the
candidate under the project's experimental gameplay pack area, and validates
that the generated pack targets only the requested existing surface.

Example:

    python scripts/smoke_generate_gameplay_pack_with_local_ai.py \
      --scenario opening-shuttle-ambush \
      --slug large-random-shuttle-boarder \
      --prompt "At the start, spawn one large hostile boarder in a random valid shuttle location."
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


def build_grounded_prompt(
    *,
    repo_root: Path,
    project: str,
    scenario: ScenarioSurface,
    user_prompt: str,
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
        f'\n- If the user asks for the bay-entry trigger, use section.onCutsceneResolved("{scenario.cutscene_id}", ...).'
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

    return f"""TASK: Generate one experimental JavaScript gameplay pack for Main Computer.

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

HARD TARGET:
- Project: {project}
- Existing scenario id: {scenario.scenario_id}
- Existing scenario kind: {scenario.kind}
- Required API shape: {scenario.api_call}
- Use exactly the scenario id "{scenario.scenario_id}".
- Do not create a new scenario id.
- Do not modify base scenario files.
- The generated pack must be additive to the existing scenario.{cutscene_guidance}

CURRENTLY ALLOWED EVENTS:
{json.dumps(list(scenario.known_events), indent=2)}

CURRENTLY ALLOWED COMMANDS:
{json.dumps(list(scenario.known_commands), indent=2)}

SAFETY RULES:
- pack.js must use export default defineGameplayPack(...).
- Do not use imports.
- Do not use these globals: {", ".join(FORBIDDEN_GLOBALS)}.
- Do not use raw renderer, DOM, network, filesystem, browser storage, or save-state access.
- Manifest must keep experimental=true, generated=true, defaultEnabled=false, hiddenFromLobby=true.
- Generated behavior must be additive only.

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
) -> str:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from main_computer.local_model_prompt_component_v1 import run_local_model_prompt_call

    result = run_local_model_prompt_call(
        prompt_text=prompt_text,
        output_dir=output_dir / "local_model_call",
        model=model,
    )
    if not result.ok:
        raise RuntimeError(f"local AI call failed: {result.details}")
    response_text = str(result.provided_state.get("local_model_response_text") or "")
    if not response_text.strip():
        raise RuntimeError("local AI returned an empty response")
    return response_text


def scenario_api_pattern(scenario: ScenarioSurface) -> re.Pattern[str]:
    if scenario.kind == "encounter":
        return re.compile(r"pack\s*\.\s*encounter\s*\(\s*(['\"`])" + re.escape(scenario.scenario_id) + r"\1")
    return re.compile(r"pack\s*\.\s*section\s*\(\s*(['\"`])" + re.escape(scenario.scenario_id) + r"\1")


def validate_candidate(
    *,
    repo_root: Path,
    output_dir: Path,
    scenario: ScenarioSurface,
    manifest: dict[str, Any],
    pack_js: str,
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
const out = {};
try {
  out.sourceValidation = runtime.validateGameplayPackJsSource(source);
  if (out.sourceValidation && out.sourceValidation.ok) {
    const harness = runtime.loadGameplayPackCommandHarnessFromSource(source);
    out.harnessSummary = runtime.gameplayPackCommandHarnessSummary(harness);
  }
} catch (err) {
  out.error = String(err && err.message || err);
}
console.log(JSON.stringify(out));
"""
        harness = subprocess.run(
            [node_path, "-e", harness_script, str(runtime_path), str(pack_path)],
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
            summary = runtime_payload.get("harnessSummary") if isinstance(runtime_payload, dict) else {}
            if summary:
                ids = summary.get("encounterIds") if scenario.kind == "encounter" else summary.get("sectionIds")
                if scenario.scenario_id not in (ids or []):
                    errors.append(f"runtime harness summary did not register {scenario.scenario_id!r}")
                if not summary.get("handlersRegistered"):
                    errors.append("runtime harness registered no handlers")
            elif source_validation and source_validation.get("ok"):
                errors.append("gameplay-pack-runtime did not return a harness summary")
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
    parser.add_argument("--prompt", required=True, help="User prompt describing additive pack behavior.")
    parser.add_argument("--slug", default=None, help="Optional generated pack folder slug.")
    parser.add_argument("--repo-root", default=None, help="Repository root. Defaults to this script's parent repo.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Output root. Defaults to game_projects/<project>/gameplay_packs/experimental.",
    )
    parser.add_argument("--fake-response", default=None, help="Read a fake local-AI markdown response from this file.")
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
    user_prompt = str(args.prompt or "").strip()
    if not user_prompt:
        return {"ok": False, "error": "--prompt must be non-empty"}

    slug_base = slugify(args.slug or user_prompt)
    folder_name = slug_base if args.slug else f"generated-{_utc_stamp()}-{slug_base}"
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else repo_root / "game_projects" / project / "gameplay_packs" / "experimental"
    )
    output_dir = output_root / folder_name
    if output_dir.exists():
        if not args.overwrite:
            return {
                "ok": False,
                "error": f"output directory already exists: {output_dir}",
                "hint": "choose a different --slug or pass --overwrite",
            }
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    grounded_prompt = build_grounded_prompt(
        repo_root=repo_root,
        project=project,
        scenario=scenario,
        user_prompt=user_prompt,
    )
    write_text(output_dir / "prompt.txt", user_prompt + "\n")
    write_text(output_dir / "generation_prompt.md", grounded_prompt)

    if args.fake_response:
        response_text = read_text(Path(args.fake_response))
        provider = "fake-response"
        model = str(Path(args.fake_response))
    else:
        response_text = call_local_ai(
            repo_root=repo_root,
            prompt_text=grounded_prompt,
            output_dir=output_dir,
            model=args.model,
        )
        provider = "local-ai"
        model = args.model or "configured-local-model"
    write_text(output_dir / "generation.md", response_text)

    extracted_error: str | None = None
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
        )
    except Exception as exc:
        extracted_error = f"{type(exc).__name__}: {exc}"
        validation = {
            "ok": False,
            "errors": [extracted_error],
            "warnings": [],
            "scenario": asdict(scenario),
            "details": {},
        }

    write_json(output_dir / "validation.json", validation)

    return {
        "ok": bool(validation.get("ok")),
        "scenario": scenario.scenario_id,
        "project": project,
        "outputDir": str(output_dir),
        "provider": provider,
        "model": model,
        "files": {
            "prompt": str(output_dir / "prompt.txt"),
            "generationPrompt": str(output_dir / "generation_prompt.md"),
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
