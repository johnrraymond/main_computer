#!/usr/bin/env python3
"""Build a deterministic code-generation prompt for meaningful per-game modules.

The prompt produced here is intentionally stricter than the existing gameplay-pack
smoke generator.  A per-game module is allowed to model a complete small game arc,
but its terminal outcome must be derived from authoritative play history and must
produce persistent gameplay consequences rather than an end-screen label only.

The default brief is the network spy-hunt game discussed for ``webgl-demo``.  A
caller may supply a replacement JSON brief with ``--brief`` while keeping the same
meaningfulness and source-truth contracts.

Examples:

    python -m main_computer.meaningful_game_module_prompt_v1

    python -m main_computer.meaningful_game_module_prompt_v1 \
      --project webgl-demo \
      --output-dir diagnostics_output/spy_hunt_module_prompt

    python -m main_computer.meaningful_game_module_prompt_v1 \
      --brief path/to/game_brief.json \
      --output-dir diagnostics_output/custom_game_module_prompt
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROMPT_GENERATOR_VERSION = "meaningful_game_module_prompt_v1"
PROMPT_SCHEMA = "game.meaningfulGameModulePrompt.v1"
BRIEF_SCHEMA = "game.meaningfulGameModuleBrief.v1"

SOURCE_REFERENCE_PATHS = (
    "game_projects/{project}/project.json",
    "main_computer/web/applications/scripts/space-navigation-runtime.js",
    "main_computer/web/applications/scripts/system-scenario-runtime.js",
    "main_computer/web/applications/scripts/gameplay-pack-runtime.js",
    "main_computer/web/applications/scripts/strategic-ai-runtime.js",
    "main_computer/web/applications/scripts/strategic-ai-action-runtime.js",
    "main_computer/web/applications/scripts/strategic-ai-offscreen-runtime.js",
)

DEFAULT_SPY_HUNT_BRIEF: dict[str, Any] = {
    "schema": BRIEF_SCHEMA,
    "id": "game.spy-hunt.network-pursuit",
    "title": "Network Spy Hunt",
    "premise": (
        "A hidden spy and the player move through the same system network. The player "
        "tries to find the spy without receiving exact hidden-state knowledge, while both "
        "sides decide whether to spend time farming power or hopping through the network."
    ),
    "playerObjective": (
        "Find and resolve the spy encounter while deciding how much search speed, power, "
        "and information quality to trade against one another."
    ),
    "opponentObjective": (
        "Advance the spy's own mission, survive, gain useful upgrades, and use movement, "
        "farming, stealth, and information to avoid or exploit contact with the player."
    ),
    "primitiveActions": ["FARM", "HOP"],
    "hardRules": [
        "The spy may start in any valid system in the current network.",
        "The player and spy use the same primitive action economy: each chooses FARM or HOP.",
        "Hold is represented by FARM; pursuit, movement, attack positioning, and retreat are policies over HOP/FARM, not privileged spy-only movement verbs.",
        "The player encounters the spy when, and only when, authoritative player and spy system occupancy is equal at an encounter check boundary.",
        "The player cannot know before entering a system whether the spy currently occupies that system.",
        "The spy's exact system is authoritative hidden state and must never be reconstructed from dialogue, UI memory, or model recollection.",
        "Save/restore must preserve the exact hidden state; normalization must not silently discard spy state.",
        "The spy tracker exposes only a raw signal choosing between two candidate search tracks. It must never expose exact spy location or guarantee current occupancy.",
        "Tracker evidence may become stale because the spy can act after a reading.",
        "The physical network is not assumed to be binary; the tracker selects two meaningful candidate tracks from the actual graph.",
        "A player may deliberately farm a harder path instead of following the strongest spy signal in order to become better armed for a later encounter.",
        "The spy may make the same trade: farm for power/stealth/sensors or hop for position/mission progress.",
        "Corridors retain bounded authoritative transit history so sensors can derive observations without revealing raw hidden events.",
        "If player and spy use the same corridor at different times, sensor and stealth upgrades may create asymmetric observations.",
        "Using opposite directions on the same corridor does not itself count as an encounter unless an explicit future mechanic changes that rule; same-system occupancy remains the encounter rule.",
        "Detection capability and signature suppression are opposed capabilities; the player and spy may have different levels and therefore receive different observations from the same physical history.",
        "Sensor observations must state only what the observer's equipment can justify: for example transit detected, age band, direction, or signature class. They must not silently become an omniscient spy locator.",
        "Every generated random choice must be seedable/replayable or otherwise recorded in authoritative state so the same saved state cannot later imply a different past.",
    ],
    "meaningfulOutcomeRequirements": [
        "The terminal result must be computed from accumulated authoritative state and event history, not selected as flavor text after the fact.",
        "At least one consequence must persist beyond the module's terminal screen and change later gameplay state, available capability, resources, relationships, intelligence, access, or threat state.",
        "How the result was achieved must matter. A fast interception, an over-prepared interception, a costly win, a failed interception, and a spy escape/mission success must be distinguishable when their causal histories differ.",
        "The outcome receipt must name the state facts/events that caused the terminal result and the persistent deltas it commits.",
        "Power gained by farming, time spent, sensor/stealth investment, spy mission progress, encounter condition, and relevant costs/damage must be eligible causal inputs to the outcome when they actually occurred.",
        "Do not reward a cosmetic branch. If two terminal labels commit identical future state, they are not meaningfully different outcomes unless the module records a concrete future-facing reason for the distinction.",
        "Do not force a single authored moral or optimal route. The code should make the tradeoffs legible and let the simulated history determine the consequence.",
    ],
    "desiredExports": [
        "createInitialState",
        "restoreState",
        "serializeState",
        "legalActions",
        "deriveTrackerObservation",
        "deriveCorridorObservation",
        "chooseSpyAction",
        "resolveTurn",
        "checkEncounter",
        "deriveOutcome",
        "outcomeReceipt",
    ],
}


class PromptGeneratorError(ValueError):
    """Raised when the project or brief cannot support a truthful prompt."""


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def _repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PromptGeneratorError(f"could not read JSON file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PromptGeneratorError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PromptGeneratorError(f"JSON root must be an object: {path}")
    return dict(payload)


def _validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(brief)
    normalized.setdefault("schema", BRIEF_SCHEMA)
    if normalized.get("schema") != BRIEF_SCHEMA:
        raise PromptGeneratorError(f"brief.schema must be {BRIEF_SCHEMA!r}")

    for field in ("id", "title", "premise", "playerObjective", "opponentObjective"):
        value = str(normalized.get(field) or "").strip()
        if not value:
            raise PromptGeneratorError(f"brief.{field} must be non-empty")
        normalized[field] = value

    for field in (
        "primitiveActions",
        "hardRules",
        "meaningfulOutcomeRequirements",
        "desiredExports",
    ):
        values = _strings(normalized.get(field))
        if not values:
            raise PromptGeneratorError(f"brief.{field} must contain at least one item")
        normalized[field] = values

    return normalized


def _project_context(repo_root: Path, project: str) -> dict[str, Any]:
    project_id = str(project or "").strip()
    if not project_id:
        raise PromptGeneratorError("project id must be non-empty")

    project_path = repo_root / "game_projects" / project_id / "project.json"
    project_json = _read_json(project_path)
    if str(project_json.get("id") or "").strip() != project_id:
        raise PromptGeneratorError(
            f"project.json id {project_json.get('id')!r} does not match requested project {project_id!r}"
        )

    metadata = _object(project_json.get("metadata"))
    navigation = _object(metadata.get("spaceNavigation"))
    strategic = _object(metadata.get("strategicAI"))
    scenarios = _object(metadata.get("systemScenarios"))

    systems: list[dict[str, Any]] = []
    for raw in _list(navigation.get("systems")):
        item = _object(raw)
        system_id = str(item.get("id") or "").strip()
        if not system_id:
            continue
        systems.append({
            "id": system_id,
            "label": str(item.get("label") or item.get("name") or system_id).strip(),
        })

    routes: list[dict[str, str]] = []
    system_ids = {item["id"] for item in systems}
    for raw in _list(navigation.get("routes")):
        item = _object(raw)
        route_id = str(item.get("id") or "").strip()
        from_id = str(item.get("from") or "").strip()
        to_id = str(item.get("to") or "").strip()
        if not route_id or not from_id or not to_id:
            continue
        if from_id not in system_ids or to_id not in system_ids:
            raise PromptGeneratorError(
                f"route {route_id!r} references a system absent from spaceNavigation.systems"
            )
        routes.append({"id": route_id, "from": from_id, "to": to_id})

    if not systems or not routes:
        raise PromptGeneratorError(
            f"project {project_id!r} needs non-empty metadata.spaceNavigation.systems/routes"
        )

    strategic_summary = {
        "schema": str(strategic.get("schema") or ""),
        "definitionVersion": str(strategic.get("definitionVersion") or ""),
        "actionTypeIds": [str(_object(item).get("id") or "") for item in _list(strategic.get("actionTypes")) if str(_object(item).get("id") or "")],
        "resourceIds": [str(_object(item).get("id") or "") for item in _list(strategic.get("resources")) if str(_object(item).get("id") or "")],
        "actorIds": [str(_object(item).get("id") or "") for item in _list(strategic.get("actors")) if str(_object(item).get("id") or "")],
        "observationChannelIds": [str(_object(item).get("id") or "") for item in _list(strategic.get("observationChannels")) if str(_object(item).get("id") or "")],
        "effectTypeIds": [str(_object(item).get("id") or "") for item in _list(strategic.get("effectTypes")) if str(_object(item).get("id") or "")],
        "policyProfileIds": [str(_object(item).get("id") or "") for item in _list(strategic.get("policyProfiles")) if str(_object(item).get("id") or "")],
    }

    scenario_summary = {
        "schema": str(scenarios.get("schema") or ""),
        "definitionVersion": str(scenarios.get("definitionVersion") or ""),
        "scenarioIds": [str(_object(item).get("id") or "") for item in _list(scenarios.get("scenarios")) if str(_object(item).get("id") or "")],
    }

    return {
        "project": {
            "id": project_id,
            "name": str(project_json.get("name") or project_id),
            "revision": metadata.get("revision"),
        },
        "spaceNavigation": {
            "schema": str(navigation.get("schema") or ""),
            "definitionVersion": str(navigation.get("definitionVersion") or ""),
            "stateVersion": str(navigation.get("stateVersion") or ""),
            "startSystem": str(navigation.get("startSystem") or ""),
            "systemCount": len(systems),
            "routeCount": len(routes),
            "systems": systems,
            "routes": routes,
        },
        "strategicAI": strategic_summary,
        "systemScenarios": scenario_summary,
    }


def _source_truth(repo_root: Path, project: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for template in SOURCE_REFERENCE_PATHS:
        relative = template.format(project=project)
        path = repo_root / relative
        records.append({
            "path": relative,
            "exists": path.is_file(),
        })
    return records


def _json_block(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=False)


def build_prompt(*, repo_root: Path, project: str, brief: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return prompt text plus deterministic metadata used to audit the prompt."""

    root = Path(repo_root).resolve()
    normalized_brief = _validate_brief(brief)
    context = _project_context(root, project)
    sources = _source_truth(root, project)
    missing_sources = [item["path"] for item in sources if not item["exists"]]
    if missing_sources:
        raise PromptGeneratorError(
            "required source references are missing: " + ", ".join(missing_sources)
        )

    prompt = f"""TASK: Generate the idealized JavaScript per-game module for Main Computer from the supplied game brief and current project topology.

This is NOT the existing narrow gameplay-pack smoke task. Build the smallest coherent pure game module that can own a complete run-specific causal arc. Do not pretend nonexistent engine APIs already exist. Where integration is not currently supported, expose a narrow explicit adapter contract in integration_contract.json instead of inventing calls into the runtime.

Return EXACTLY three files using the markers below. Do not add prose outside the markers. Do not ask questions.

BEGIN game_module_manifest.json
{{
  "schema": "game.perGameModuleManifest.v1",
  "kind": "per-game-module",
  "id": "{normalized_brief['id']}",
  "title": "{normalized_brief['title']}",
  "version": "0.1.0",
  "entry": "game_module.js",
  "stateVersion": "game.perGameModuleState.v1",
  "outcomeReceiptVersion": "game.perGameModuleOutcomeReceipt.v1"
}}
END game_module_manifest.json

BEGIN game_module.js
// Complete self-contained ES module. No imports. No DOM/network/storage access.
// Export the required pure functions named in REQUIRED EXPORTS.
END game_module.js

BEGIN integration_contract.json
{{
  "schema": "game.perGameModuleIntegrationContract.v1",
  "kind": "per-game-module-integration-contract",
  "moduleId": "{normalized_brief['id']}",
  "reads": [],
  "writes": [],
  "eventsIn": [],
  "eventsOut": [],
  "persistence": {{}},
  "runtimeGaps": []
}}
END integration_contract.json

GOVERNING INVARIANT:
A generated game module may terminate only in an outcome causally derived from authoritative play history, and that terminal result must commit at least one persistent gameplay consequence beyond end-screen text.

GAME BRIEF:
{_json_block(normalized_brief)}

CURRENT PROJECT SOURCE TRUTH:
{_json_block(context)}

SOURCE FILES THAT DEFINE THE EXISTING BOUNDARIES:
{_json_block(sources)}

ARCHITECTURAL RULES:
- Treat project.json topology above as source truth. Do not invent systems or routes.
- Use one authoritative serializable module state. Derived UI/observations must never become a second truth store.
- Keep hidden world state separate from each actor's observations. The player-facing observation object must not contain hidden spy fields.
- The code must be replayable from seed + authoritative state/action history, or persist every nondeterministic result that affects later play.
- A save/restore round trip must preserve all state required to make future decisions and the eventual outcome identical in meaning.
- Use bounded transit/event history; do not grow unbounded logs merely to make the outcome explainable.
- Both player and spy actions must resolve through the same FARM/HOP primitive action validation. Different strategy is allowed; privileged movement rules are not.
- Check same-system encounter at explicit documented boundaries. Do not accidentally create corridor collision as an encounter.
- The tracker is a derived two-track raw signal. It is evidence, not position truth, and it may be stale after later movement.
- Corridor sensing is also derived evidence. Resolve it from authoritative transit history plus observer detection and target signature/stealth capability.
- Sensor asymmetry must be real: the same transit history can yield different observations to player and spy because their equipment differs.
- FARM must have an opportunity cost in time/information freshness and a potential benefit in power, sensor, stealth, resource, or mission capability.
- HOP must have a positional/mission consequence and advance the world enough that indefinite chasing is not free.
- The spy must have a concrete objective function encoded in code. chooseSpyAction must choose among legal FARM/HOP actions from state; it must not receive player-only observations as secret extra input.
- Do not let an LLM choose or remember hidden state at runtime. The generated module is ordinary deterministic/seeded code once generated.
- Do not encode a fake choice where branches immediately reconverge to identical authoritative state.

MEANINGFUL-OUTCOME GATE:
The module is unacceptable unless all of these are true:
1. Terminal classification comes from state predicates that can be pointed to in code.
2. deriveOutcome returns materially different outcome data when materially different causal histories occurred.
3. outcomeReceipt identifies terminal reason, decisive events/state facts, costs, and the exact persistent consequences to commit.
4. At least one persistent consequence changes a future-facing quantity or capability; narrative text alone does not satisfy this.
5. A nominal player success can still preserve meaningful costs if the player won slowly, expensively, destructively, or after allowing spy progress.
6. A nominal failure can preserve partial accomplishments actually earned during play, if those accomplishments have a legitimate persistent effect.
7. The code never fabricates causal explanations after the result. Receipt claims must be reconstructable from retained authoritative facts/events.
8. The spy's own farming, upgrades, movement, mission progress, and information can affect the result when they actually altered play.

REQUIRED EXPORTS:
{_json_block(normalized_brief['desiredExports'])}

MINIMUM STATE CONTENT:
- schema/stateVersion/moduleId/seed/turn/status
- player: currentSystemId, resources/power/upgrades, observation-relevant capability
- spy: hidden currentSystemId, resources/power/upgrades, mission progress, policy-relevant state
- bounded corridor transit history sufficient for sensor observations
- tracker state sufficient to explain the current raw signal without storing a fake exact-location hint in player knowledge
- bounded causal ledger of only outcome-relevant facts/events
- terminal outcome state and committed/not-committed consequence receipt state

ACTION/RESOLUTION CONTRACT:
- legalActions(state, actorId) returns only FARM or valid adjacent-system HOP actions.
- resolveTurn validates both chosen actions against the same primitive rules, records/persists any randomness, resolves actions, records corridor transits, advances resource/mission effects, and checks encounter boundaries.
- chooseSpyAction is a policy over legal actions. Strategy names such as hunt/retreat/hold/power-up may exist only as policy intent explaining FARM/HOP selection.
- The player action is input from the human-facing layer; do not auto-play the player.
- If player and spy are already in the same system at a start-of-turn encounter boundary, encounter resolution preempts another free FARM payout.
- Opposite-direction use of the same route may produce sensor evidence but is not itself an encounter.

OBSERVATION CONTRACT:
- deriveTrackerObservation(state, candidateTrackA, candidateTrackB, observer='player') returns only a raw comparison/signal and metadata the player's tracker capability justifies. It must not return spySystemId.
- deriveCorridorObservation(state, observerId, routeId) returns only capability-justified derived evidence such as detected/not-detected, bounded age band, direction if unlocked, and signature class if unlocked.
- Detection and stealth/signature suppression must interact explicitly; do not hard-code observations solely by actor identity.
- Observations must be recomputable from authoritative state and persisted randomness; they are not authoritative world state themselves.

OUTCOME CONTRACT:
- deriveOutcome must separate terminal classification from presentation text.
- outcomeReceipt must include a compact causal chain and a list of persistentConsequences with stable ids/types/targets/deltas or explicit set-values.
- Persistent consequences must be suitable for a caller to validate and commit exactly once.
- Include an idempotency key or equivalent receipt identity so reload/retry cannot double-apply consequences.
- Do not write browser storage from the module; integration code owns persistence/commit.

IDEALIZED-CODE RULE:
If the current Main Computer runtime lacks a hook needed by this clean module, keep the module clean and list the missing hook in integration_contract.json.runtimeGaps. Do not contort the game logic into the current opening-shuttle gameplay-pack API and do not fabricate an engine method.

CODE QUALITY:
- Plain JavaScript ES module; no imports.
- No window, document, fetch, XMLHttpRequest, localStorage, sessionStorage, eval, Function, filesystem, or network access.
- Prefer small pure helpers and explicit validation.
- Avoid hidden mutable module globals.
- Clone/return data so callers cannot mutate authoritative state accidentally.
- Bound history arrays explicitly.
- Use stable ids for events, receipts, and consequences.
- Comments should explain invariants or non-obvious causal rules, not narrate obvious syntax.

FINAL CHECK BEFORE RETURNING FILES:
- Can the spy start at every valid system?
- Can both sides farm or hop under the same primitive legality rules?
- Can either side become stronger while the other's information ages?
- Can sensor equipment create asymmetric knowledge from the same corridor history?
- Can the tracker help without proving occupancy?
- Can save/restore reproduce the hidden state and causal history without model memory?
- Can two meaningfully different play histories produce meaningfully different persistent outcomes?
- Can every claimed outcome consequence be traced to authoritative state/events?
- Did you avoid inventing current runtime APIs?

Return only the three marked files.
"""

    encoded = prompt.encode("utf-8")
    metadata = {
        "schema": PROMPT_SCHEMA,
        "componentVersion": PROMPT_GENERATOR_VERSION,
        "project": context["project"]["id"],
        "briefId": normalized_brief["id"],
        "promptSha256": hashlib.sha256(encoded).hexdigest(),
        "promptBytes": len(encoded),
        "systemCount": context["spaceNavigation"]["systemCount"],
        "routeCount": context["spaceNavigation"]["routeCount"],
        "sourceReferences": sources,
    }
    return prompt, metadata


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an idealized meaningful per-game-module code prompt from current project source truth."
    )
    parser.add_argument("--project", default="webgl-demo", help="Game project id. Defaults to webgl-demo.")
    parser.add_argument("--repo-root", default=None, help="Repository root. Defaults to this module's repository.")
    parser.add_argument("--brief", default=None, help="Optional JSON game brief. Defaults to the network spy-hunt brief.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory to write prompt.md and prompt_metadata.json. Otherwise print prompt to stdout.",
    )
    parser.add_argument("--metadata-only", action="store_true", help="Print/write metadata without printing prompt text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_module().resolve()
    brief = _read_json(Path(args.brief).resolve()) if args.brief else dict(DEFAULT_SPY_HUNT_BRIEF)

    try:
        prompt, metadata = build_prompt(
            repo_root=repo_root,
            project=str(args.project or "webgl-demo"),
            brief=brief,
        )
    except PromptGeneratorError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=__import__("sys").stderr)
        return 2

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        if not args.metadata_only:
            (output_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        (output_dir / "prompt_metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "ok": True,
            "prompt": None if args.metadata_only else str(output_dir / "prompt.md"),
            "metadata": str(output_dir / "prompt_metadata.json"),
            **metadata,
        }, indent=2, sort_keys=True))
        return 0

    if args.metadata_only:
        print(json.dumps(metadata, indent=2, sort_keys=True))
    else:
        print(prompt, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
