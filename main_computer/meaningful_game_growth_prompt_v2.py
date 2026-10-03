#!/usr/bin/env python3
"""State-to-module prompt workflow for growing Main Computer games incrementally.

Version 1 of ``meaningful_game_module_prompt`` deliberately produced one large
code-generation prompt.  This component turns that idea into a bounded prompt
state machine.  The model first translates current game/source state into a
module proposal, then asks for narrow source slices.  This component—not the
model—materializes those slices from the repository, hashes them, records them
in a growth ledger, and emits the next prompt.  Only after the model has enough
source-grounded context does the workflow ask for a module design and then code.

The point is context accretion rather than context dumping: every admitted
source excerpt has an explicit reason, selector, hash, and round.  The final
code prompt is therefore a translation of known current game state into a new
module contract, not a free-form request to invent an isolated mini-game.

Typical use::

    python -m main_computer.meaningful_game_growth_prompt_v2 init \
      --project webgl-demo \
      --output-dir diagnostics_output/spy_hunt_growth

    # Give 00_translate_game_state.md to the coding/reasoning model and save its
    # JSON-only response as response_00.json.

    python -m main_computer.meaningful_game_growth_prompt_v2 advance \
      --workflow-dir diagnostics_output/spy_hunt_growth \
      --response diagnostics_output/spy_hunt_growth/response_00.json

Repeat ``advance`` with each JSON response until ``nextPromptKind`` is ``code``.
Use ``prompt`` at any point to emit the current model-ready prompt text directly
to stdout.  ``init`` and ``advance`` intentionally keep returning compact JSON
receipts for orchestration; ``prompt`` is the clean text boundary for a model.
The final generated prompt asks for the module files.  No local-model call is
performed by this component; it is a deterministic prompt/context compiler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:  # schema text can still be rendered; validation fails closed when requested.
    Draft202012Validator = None  # type: ignore[assignment]
    Registry = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]

from main_computer.meaningful_game_module_prompt_v1 import (
    DEFAULT_SPY_HUNT_BRIEF,
    PromptGeneratorError,
    _project_context,
    _read_json,
    _validate_brief,
)


COMPONENT_VERSION = "meaningful_game_growth_prompt_v2.4"
STATE_SCHEMA = "game.meaningfulGameGrowthState.v1"
DECISION_SCHEMA = "game.meaningfulGameGrowthDecision.v1"
CONTEXT_PACKET_SCHEMA = "game.meaningfulGameGrowthContextPacket.v1"
DESIGN_SCHEMA = "game.meaningfulGameModuleDesign.v1"

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
DECISION_SCHEMA_PATH = SCHEMA_DIR / "game.meaningfulGameGrowthDecision.v1.schema.json"
DESIGN_SCHEMA_PATH = SCHEMA_DIR / "game.meaningfulGameModuleDesign.v1.schema.json"
DECISION_SCHEMA_ID = "https://maincomputer.local/schemas/game.meaningfulGameGrowthDecision.v1.schema.json"
DESIGN_SCHEMA_ID = "https://maincomputer.local/schemas/game.meaningfulGameModuleDesign.v1.schema.json"

DEFAULT_MAX_REQUESTS_PER_ROUND = 6
DEFAULT_MAX_CHARS_PER_PACKET = 12_000
DEFAULT_MAX_CHARS_PER_ROUND = 48_000
DEFAULT_MAX_ACCEPTED_CONTEXT_CHARS = 180_000
MAX_SEARCH_TERMS = 8
MAX_CONTEXT_LINES = 120
MAX_LINE_SPAN = 400
MAX_CATALOG_QUERIES_PER_ROUND = 4
MAX_CATALOG_RESULTS_PER_QUERY = 20

# These are discoverable source surfaces, not automatically admitted context.
# project.json is always catalogued separately under game_projects/<project>/.
SOURCE_CATALOG_ROOTS = (
    "main_computer/web/applications/scripts",
    "main_computer",
)

# The broad main_computer root is filtered so the catalog remains game-focused.
_MAIN_COMPUTER_NAME_HINTS = (
    "game",
    "gameplay",
    "strategic",
    "scenario",
    "space",
    "character",
)



_CORE_SOURCE_BASENAMES = {
    "project.json",
    "space-navigation-runtime.js",
    "system-scenario-runtime.js",
    "gameplay-pack-runtime.js",
    "strategic-ai-runtime.js",
    "strategic-ai-action-runtime.js",
    "strategic-ai-offscreen-runtime.js",
    "strategic-ai-director-runtime.js",
    "pax-scenario-session-runtime.js",
    "character-ai-runtime.js",
}

_TEXT_SUFFIXES = {
    ".js",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".yml",
    ".yaml",
}


class GrowthPromptError(ValueError):
    """Raised when the growth ledger or a model decision is invalid."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def _safe_id(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    return text or fallback


def _json_block(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=False)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    tmp.replace(path)


def _decode_json_bytes(raw: bytes, *, path: Path) -> Any:
    """Decode JSON text from common Windows/editor encodings.

    BOM detection is exact and ordered from the longest signatures to the
    shortest.  UTF-32LE starts with ``FF FE`` too, so treating every ``FF FE``
    file as UTF-16 corrupts the decoded text with NULs before JSON parsing.
    Successfully parsed responses are canonicalized to UTF-8 by the managed
    response loader.
    """

    attempts: list[str] = []

    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encodings = ["utf-32"]
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    elif raw.startswith(b"\xef\xbb\xbf"):
        encodings = ["utf-8-sig"]
    else:
        encodings = ["utf-8", "utf-8-sig"]

    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            attempts.append(f"{encoding}: {exc}")
            continue

        # Some editors can leave a decoded BOM marker after an otherwise valid
        # conversion.  It is transport metadata, not part of the JSON value.
        text = text.lstrip("\ufeff")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            attempts.append(f"{encoding}: {exc}")

    detail = "; ".join(attempts[:4])
    raise GrowthPromptError(f"could not decode/parse JSON file {path}: {detail}")


def _managed_response_json(path: Path) -> tuple[dict[str, Any], bool, bool]:
    """Ensure a response artifact exists, load it, and canonicalize UTF-8.

    Missing files are created as an empty JSON object.  This intentionally does
    not fabricate a valid model decision: schema validation remains responsible
    for explaining which decision fields are still missing.
    """

    resolved = path.resolve()
    created = False
    if not resolved.exists():
        _write_json(resolved, {})
        created = True
    if not resolved.is_file():
        raise GrowthPromptError(f"response path is not a file: {resolved}")

    raw = resolved.read_bytes()
    parsed = _decode_json_bytes(raw, path=resolved)
    if not isinstance(parsed, dict):
        raise GrowthPromptError(f"response JSON root must be an object: {resolved}")

    canonical = (json.dumps(parsed, indent=2, sort_keys=True) + "\n").encode("utf-8")
    normalized = raw != canonical
    if normalized:
        _write_json(resolved, parsed)
    return dict(parsed), created, normalized


def _repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[1]


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_game_focused_main_computer_file(relative: str) -> bool:
    name = Path(relative).name.lower()
    return any(hint in name for hint in _MAIN_COMPUTER_NAME_HINTS)


def _source_catalog(repo_root: Path, project: str) -> list[dict[str, Any]]:
    """Return hashes/metadata only.  File contents are admitted later by request."""

    root = repo_root.resolve()
    candidates: dict[str, Path] = {}

    project_root = root / "game_projects" / project
    if project_root.is_dir():
        for path in project_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES:
                relative = _relative_posix(path, root)
                candidates[relative] = path

    scripts_root = root / "main_computer" / "web" / "applications" / "scripts"
    if scripts_root.is_dir():
        for path in scripts_root.glob("*"):
            if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES:
                relative = _relative_posix(path, root)
                candidates[relative] = path

    main_root = root / "main_computer"
    if main_root.is_dir():
        for path in main_root.glob("*"):
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            relative = _relative_posix(path, root)
            if _is_game_focused_main_computer_file(relative):
                candidates[relative] = path

    records: list[dict[str, Any]] = []
    for relative, path in sorted(candidates.items()):
        raw = path.read_bytes()
        records.append(
            {
                "path": relative,
                "bytes": len(raw),
                "sha256": _sha256_bytes(raw),
            }
        )
    return records


def _catalog_index(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("path")): dict(item)
        for item in _list(state.get("sourceCatalog"))
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }


def _compact_catalog(catalog: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(item.get("path") or ""),
            "bytes": int(item.get("bytes") or 0),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in catalog
    ]


def _compact_project_context(context: dict[str, Any]) -> dict[str, Any]:
    navigation = _object(context.get("spaceNavigation"))
    systems = [
        {
            "id": str(_object(item).get("id") or ""),
            "label": str(_object(item).get("label") or ""),
        }
        for item in _list(navigation.get("systems"))
        if str(_object(item).get("id") or "").strip()
    ]
    return {
        "project": context.get("project"),
        "spaceNavigation": {
            "schema": navigation.get("schema"),
            "definitionVersion": navigation.get("definitionVersion"),
            "stateVersion": navigation.get("stateVersion"),
            "startSystem": navigation.get("startSystem"),
            "systemCount": navigation.get("systemCount"),
            "routeCount": navigation.get("routeCount"),
            "systems": systems,
        },
        "strategicAI": context.get("strategicAI"),
        "systemScenarios": context.get("systemScenarios"),
    }


def _initial_discovered_paths(catalog: list[dict[str, Any]], project: str) -> list[str]:
    result: list[str] = []
    project_json = f"game_projects/{project}/project.json"
    by_path = {str(item.get("path") or ""): item for item in catalog}
    if project_json in by_path:
        result.append(project_json)
    for item in catalog:
        path = str(item.get("path") or "")
        if Path(path).name in _CORE_SOURCE_BASENAMES and path not in result:
            result.append(path)
    return sorted(result)


def _visible_catalog(state: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = _catalog_index(state)
    paths = [str(item) for item in _list(state.get("discoveredSourcePaths"))]
    return _compact_catalog(catalog[path] for path in paths if path in catalog)


def _new_state(
    *,
    repo_root: Path,
    workflow_dir: Path,
    project: str,
    brief: dict[str, Any],
    max_requests_per_round: int,
    max_chars_per_packet: int,
    max_chars_per_round: int,
    max_accepted_context_chars: int,
) -> dict[str, Any]:
    normalized_brief = _validate_brief(brief)
    project_context = _project_context(repo_root, project)
    catalog = _source_catalog(repo_root, project)
    if not catalog:
        raise GrowthPromptError("source catalog is empty")

    return {
        "schema": STATE_SCHEMA,
        "componentVersion": COMPONENT_VERSION,
        "repoRoot": str(repo_root.resolve()),
        "workflowDir": str(workflow_dir.resolve()),
        "project": str(project),
        "brief": normalized_brief,
        "projectContext": project_context,
        "sourceCatalog": catalog,
        "discoveredSourcePaths": _initial_discovered_paths(catalog, project),
        "phase": "translate",
        "round": 0,
        "contextBudget": {
            "maxRequestsPerRound": int(max_requests_per_round),
            "maxCharsPerPacket": int(max_chars_per_packet),
            "maxCharsPerRound": int(max_chars_per_round),
            "maxAcceptedContextChars": int(max_accepted_context_chars),
            "maxCatalogQueriesPerRound": MAX_CATALOG_QUERIES_PER_ROUND,
            "maxCatalogResultsPerQuery": MAX_CATALOG_RESULTS_PER_QUERY,
        },
        "moduleProposal": {},
        "facts": [],
        "unknowns": [],
        "design": {},
        "contextPackets": [],
        "catalogDiscoveries": [],
        "history": [],
        "nextPrompt": None,
        "complete": False,
    }


def _load_contract_schema(path: Path, *, expected_id: str) -> dict[str, Any]:
    if not path.is_file():
        raise GrowthPromptError(f"contract schema not found: {path}")
    value = _read_json(path)
    if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise GrowthPromptError(f"contract schema is not JSON Schema draft 2020-12: {path}")
    if value.get("$id") != expected_id:
        raise GrowthPromptError(
            f"contract schema $id mismatch for {path}: expected {expected_id!r}, got {value.get('$id')!r}"
        )
    if Draft202012Validator is not None:
        Draft202012Validator.check_schema(value)
    return value


def _require_jsonschema() -> None:
    if Draft202012Validator is None or Registry is None or Resource is None:
        raise GrowthPromptError(
            'JSON Schema validation requires jsonschema>=4.26,<5; install project dependencies or run: python -m pip install "jsonschema>=4.26,<5"'
        )


def _contract_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    decision_schema = _load_contract_schema(DECISION_SCHEMA_PATH, expected_id=DECISION_SCHEMA_ID)
    design_schema = _load_contract_schema(DESIGN_SCHEMA_PATH, expected_id=DESIGN_SCHEMA_ID)
    return decision_schema, design_schema


def _decision_validator() -> Any:
    _require_jsonschema()
    decision_schema, design_schema = _contract_schemas()
    registry = Registry().with_resource(
        DESIGN_SCHEMA_ID,
        Resource.from_contents(design_schema),
    )
    return Draft202012Validator(decision_schema, registry=registry)


def _json_path(parts: Iterable[Any]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(part)):
            rendered += f".{part}"
        else:
            rendered += "[" + json.dumps(str(part)) + "]"
    return rendered


def _schema_error_records(value: Any) -> list[dict[str, Any]]:
    errors = sorted(
        _decision_validator().iter_errors(value),
        key=lambda error: (list(error.absolute_path), list(error.absolute_schema_path)),
    )
    return [
        {
            "path": _json_path(error.absolute_path),
            "schemaPath": _json_path(error.absolute_schema_path),
            "validator": str(error.validator or ""),
            "message": error.message,
        }
        for error in errors
    ]


def _phase_contract_schema_text(*, phase: str) -> str:
    decision_schema, design_schema = _contract_schemas()
    parts = [
        "DECISION SCHEMA (JSON Schema draft 2020-12):",
        _json_block(decision_schema),
    ]
    if phase == "design":
        parts.extend(
            [
                "",
                "REFERENCED MODULE DESIGN SCHEMA:",
                _json_block(design_schema),
            ]
        )
    return "\n".join(parts)


def _translation_prompt(state: dict[str, Any]) -> str:
    budget = _object(state.get("contextBudget"))
    return f"""TASK: Translate the current Main Computer game/source state into a bounded plan for a new per-game module.  DO NOT write code yet.

You are the first stage of a state-to-module compiler.  Your job is to determine what the proposed game module must own, what current game state it must translate/read, and what repository facts are still needed before a design can be trusted.

The context window must grow incrementally.  You are NOT allowed to ask for the whole repository or every relevant-looking file.  Request only the smallest source slices that resolve concrete unknowns.  A deterministic host will materialize your requested slices, hash them, and return them in the next prompt.

GAME BRIEF:
{_json_block(state['brief'])}

CURRENT PROJECT TOPOLOGY / DEFINITION SUMMARY:
{_json_block(_compact_project_context(_object(state['projectContext'])))}

VISIBLE SOURCE CATALOG (metadata only; contents are not yet admitted):
{_json_block(_visible_catalog(state))}

The host retains a larger game-focused catalog that is NOT dumped into this prompt.  If the needed file is not visible, use catalogQueries to discover candidate paths by filename/path terms before requesting file contents.

CONTEXT BUDGET:
{_json_block(budget)}

TRANSLATION RESPONSIBILITIES:
- Identify the authoritative current game-state inputs the module will need.
- Separate existing state the module reads from new state the module will own.
- Separate authoritative state from derived observations/UI.
- Identify how terminal module consequences must translate back into future game state.
- Identify persistence/save-restore boundaries that must exist for the result to remain meaningful.
- Treat every integration claim as unknown until supported by the brief, project definition summary, or a requested source slice.
- Prefer a source request that proves a specific hook/state boundary over a broad architectural guess.
- The spy-game invariants in the brief are governing mechanics; repository source tells you how to integrate them, not whether to silently replace them.

ALLOWED CONTEXT SELECTORS:
1. Search window:
   {{"kind":"search","terms":["term1","term2"],"contextLines":50}}
   Terms are literal case-insensitive source searches.  Use exact function/class/schema/property names when possible.
2. Exact line window:
   {{"kind":"lines","start":120,"end":220}}
   Use only when a prior packet gave you useful line numbers.
3. Whole small file:
   {{"kind":"whole_if_under","maxChars":10000}}
   Use only for genuinely small files.

REQUEST RULES:
- At most {budget.get('maxCatalogQueriesPerRound')} catalogQueries this round.  A catalog query searches path names and source text for literal terms but returns only path metadata/hit counts; it does not admit source contents.
- At most {budget.get('maxRequestsPerRound')} context requests this round.
- Every contextRequests.path must appear verbatim in VISIBLE SOURCE CATALOG.
- If the required source path is not visible, ask for a catalog query first rather than guessing a path.
- Every request must say which unknown it resolves.
- Do not request generated artifacts, dependencies, caches, or tests merely for reassurance.
- If current context is already sufficient for a source-truth design, set readyForDesign=true and contextRequests=[] instead of padding the context window.

OUTPUT CONTRACT:
Return EXACTLY one JSON object and no markdown/prose.  The object MUST validate against the canonical schema below and MUST use phase="translate".

{_phase_contract_schema_text(phase='translate')}

Important: facts with basis="inference" remain assumptions for design purposes until source evidence supports them.  Do not turn an inference into a source fact by repeating it.
"""


def _load_state(workflow_dir: Path) -> dict[str, Any]:
    state_path = workflow_dir / "growth_state.json"
    if not state_path.is_file():
        raise GrowthPromptError(f"growth state not found: {state_path}")
    state = _read_json(state_path)
    if state.get("schema") != STATE_SCHEMA:
        raise GrowthPromptError(f"growth state schema must be {STATE_SCHEMA!r}")
    return state


def _validate_decision(value: dict[str, Any], *, expected_phase: str | None = None) -> dict[str, Any]:
    decision = dict(value)
    errors = _schema_error_records(decision)
    if errors:
        first = errors[0]
        raise GrowthPromptError(
            f"decision does not satisfy {DECISION_SCHEMA}: {first['path']}: {first['message']}"
        )
    phase = str(decision.get("phase") or "").strip()
    if expected_phase and phase != expected_phase:
        raise GrowthPromptError(
            f"decision.phase {phase!r} does not match current workflow phase {expected_phase!r}"
        )
    return decision


def _validate_workflow_constraints(state: dict[str, Any], decision: dict[str, Any]) -> None:
    budget = _object(state.get("contextBudget"))
    catalog_queries = _list(decision.get("catalogQueries"))
    context_requests = _list(decision.get("contextRequests"))
    max_queries = int(budget.get("maxCatalogQueriesPerRound") or MAX_CATALOG_QUERIES_PER_ROUND)
    max_requests = int(budget.get("maxRequestsPerRound") or DEFAULT_MAX_REQUESTS_PER_ROUND)
    if len(catalog_queries) > max_queries:
        raise GrowthPromptError(
            f"decision.catalogQueries has {len(catalog_queries)} items; workflow limit is {max_queries}"
        )
    if len(context_requests) > max_requests:
        raise GrowthPromptError(
            f"decision.contextRequests has {len(context_requests)} items; workflow limit is {max_requests}"
        )

    catalog = _catalog_index(state)
    visible = {str(item) for item in _list(state.get("discoveredSourcePaths"))}
    max_packet_chars = int(budget.get("maxCharsPerPacket") or DEFAULT_MAX_CHARS_PER_PACKET)
    for raw in context_requests:
        request = _object(raw)
        request_id = str(request.get("id") or "")
        relative = str(request.get("path") or "").strip().replace("\\", "/")
        if relative not in catalog:
            raise GrowthPromptError(
                f"context request {request_id!r} path is not in source catalog: {relative!r}"
            )
        if relative not in visible:
            raise GrowthPromptError(
                f"context request {request_id!r} path is not yet visible/discovered: {relative!r}; use catalogQueries first"
            )
        selector = _object(request.get("selector"))
        kind = str(selector.get("kind") or "")
        if kind == "lines":
            start = int(selector.get("start") or 0)
            end = int(selector.get("end") or 0)
            if end < start:
                raise GrowthPromptError(
                    f"context request {request_id!r} line selector end must be >= start"
                )
            if end - start + 1 > MAX_LINE_SPAN:
                raise GrowthPromptError(
                    f"context request {request_id!r} line selector spans {end - start + 1} lines; limit is {MAX_LINE_SPAN}"
                )
        elif kind == "whole_if_under":
            if int(selector.get("maxChars") or 0) > max_packet_chars:
                raise GrowthPromptError(
                    f"context request {request_id!r} maxChars exceeds workflow packet limit {max_packet_chars}"
                )


def validate_response_contract(*, workflow_dir: Path, response_path: Path) -> dict[str, Any]:
    workflow_dir = workflow_dir.resolve()
    state = _load_state(workflow_dir)
    response, response_created, response_normalized = _managed_response_json(response_path)
    schema_errors = _schema_error_records(response)
    contextual_errors: list[str] = []
    if not schema_errors:
        try:
            decision = _validate_decision(response, expected_phase=str(state.get("phase") or "translate"))
            _validate_workflow_constraints(state, decision)
        except GrowthPromptError as exc:
            contextual_errors.append(str(exc))
    return {
        "ok": not schema_errors and not contextual_errors,
        "schema": DECISION_SCHEMA,
        "schemaId": DECISION_SCHEMA_ID,
        "designSchemaId": DESIGN_SCHEMA_ID,
        "componentVersion": COMPONENT_VERSION,
        "expectedPhase": state.get("phase"),
        "response": str(response_path.resolve()),
        "responseCreated": response_created,
        "responseNormalizedUtf8": response_normalized,
        "responseSha256": _sha256_bytes(response_path.resolve().read_bytes()),
        "schemaErrors": schema_errors,
        "contextualErrors": contextual_errors,
    }


def _merge_by_id(existing: list[Any], incoming: list[Any], *, prefix: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, raw in enumerate(existing):
        item = _object(raw)
        item_id = _safe_id(item.get("id"), fallback=f"{prefix}-existing-{index}")
        item["id"] = item_id
        if item_id not in merged:
            order.append(item_id)
        merged[item_id] = item
    for index, raw in enumerate(incoming):
        item = _object(raw)
        item_id = _safe_id(item.get("id"), fallback=f"{prefix}-incoming-{index}")
        item["id"] = item_id
        if item_id not in merged:
            order.append(item_id)
        merged[item_id] = item
    return [merged[item_id] for item_id in order]


def _selector_lines(text: str, *, start: int, end: int) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return "", {"kind": "lines", "start": 1, "end": 0, "lineCount": 0}
    safe_start = max(1, int(start))
    safe_end = min(len(lines), int(end))
    if safe_end < safe_start:
        raise GrowthPromptError("line selector end must be >= start")
    if safe_end - safe_start + 1 > MAX_LINE_SPAN:
        safe_end = safe_start + MAX_LINE_SPAN - 1
    excerpt = "\n".join(f"{number}: {lines[number - 1]}" for number in range(safe_start, safe_end + 1))
    return excerpt, {
        "kind": "lines",
        "start": safe_start,
        "end": safe_end,
        "lineCount": len(lines),
    }


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not windows:
        return []
    ordered = sorted(windows)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prior_start, prior_end = merged[-1]
        if start <= prior_end + 1:
            merged[-1] = (prior_start, max(prior_end, end))
        else:
            merged.append((start, end))
    return merged


def _selector_search(text: str, *, terms: list[str], context_lines: int) -> tuple[str, dict[str, Any]]:
    clean_terms = [term for term in (str(item).strip() for item in terms) if term][:MAX_SEARCH_TERMS]
    if not clean_terms:
        raise GrowthPromptError("search selector needs at least one non-empty term")
    context = max(2, min(MAX_CONTEXT_LINES, int(context_lines)))
    lines = text.splitlines()
    lowered = [line.lower() for line in lines]
    hit_lines: list[int] = []
    term_hits: dict[str, int] = {term: 0 for term in clean_terms}
    for term in clean_terms:
        needle = term.lower()
        for index, line in enumerate(lowered, start=1):
            if needle in line:
                hit_lines.append(index)
                term_hits[term] += 1

    windows: list[tuple[int, int]] = []
    half = max(1, context // 2)
    for line_number in sorted(set(hit_lines)):
        windows.append((max(1, line_number - half), min(len(lines), line_number + half)))
    windows = _merge_windows(windows)

    chunks: list[str] = []
    for start, end in windows:
        chunks.append(f"--- lines {start}-{end} ---")
        chunks.extend(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))
    excerpt = "\n".join(chunks)
    if not hit_lines:
        excerpt = "[NO MATCHES FOR REQUESTED SEARCH TERMS]"

    return excerpt, {
        "kind": "search",
        "terms": clean_terms,
        "contextLines": context,
        "termHits": term_hits,
        "windows": [{"start": start, "end": end} for start, end in windows],
        "lineCount": len(lines),
    }


def _materialize_request(
    *,
    state: dict[str, Any],
    request: dict[str, Any],
    round_number: int,
    ordinal: int,
) -> dict[str, Any]:
    catalog = _catalog_index(state)
    request_id = _safe_id(request.get("id"), fallback=f"request-{round_number}-{ordinal}")
    relative = str(request.get("path") or "").strip().replace("\\", "/")
    discovered = {str(item) for item in _list(state.get("discoveredSourcePaths"))}
    if relative not in catalog:
        raise GrowthPromptError(
            f"context request {request_id!r} path is not in source catalog: {relative!r}"
        )
    if relative not in discovered:
        raise GrowthPromptError(
            f"context request {request_id!r} path is not yet visible/discovered: {relative!r}; use catalogQueries first"
        )

    repo_root = Path(str(state.get("repoRoot") or "")).resolve()
    source_path = (repo_root / relative).resolve()
    try:
        source_path.relative_to(repo_root)
    except ValueError as exc:
        raise GrowthPromptError(f"unsafe source path: {relative!r}") from exc
    if not source_path.is_file():
        raise GrowthPromptError(f"catalogued source file no longer exists: {relative}")

    raw = source_path.read_bytes()
    current_sha = _sha256_bytes(raw)
    catalog_sha = str(catalog[relative].get("sha256") or "")
    if current_sha != catalog_sha:
        raise GrowthPromptError(
            f"source changed since workflow init: {relative}; restart workflow to avoid mixed source truth"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GrowthPromptError(f"source file is not UTF-8 text: {relative}") from exc

    selector = _object(request.get("selector"))
    kind = str(selector.get("kind") or "").strip()
    budget = _object(state.get("contextBudget"))
    max_packet_chars = int(budget.get("maxCharsPerPacket") or DEFAULT_MAX_CHARS_PER_PACKET)

    if kind == "search":
        excerpt, resolved_selector = _selector_search(
            text,
            terms=_strings(selector.get("terms")),
            context_lines=int(selector.get("contextLines") or 50),
        )
    elif kind == "lines":
        excerpt, resolved_selector = _selector_lines(
            text,
            start=int(selector.get("start") or 1),
            end=int(selector.get("end") or 1),
        )
    elif kind == "whole_if_under":
        requested_max = int(selector.get("maxChars") or max_packet_chars)
        effective_max = min(max_packet_chars, max(1, requested_max))
        if len(text) > effective_max:
            raise GrowthPromptError(
                f"whole_if_under request {request_id!r} exceeds limit: {len(text)} > {effective_max} chars"
            )
        excerpt = text
        resolved_selector = {
            "kind": "whole_if_under",
            "maxChars": effective_max,
            "lineCount": len(text.splitlines()),
        }
    else:
        raise GrowthPromptError(
            f"context request {request_id!r} selector.kind must be search, lines, or whole_if_under"
        )

    truncated = False
    original_excerpt_chars = len(excerpt)
    if len(excerpt) > max_packet_chars:
        excerpt = excerpt[:max_packet_chars]
        excerpt += "\n[TRUNCATED BY CONTEXT PACKET BUDGET]\n"
        truncated = True

    workflow_dir = Path(str(state.get("workflowDir") or "")).resolve()
    packet_rel = Path("context") / f"r{round_number:02d}_{ordinal:02d}_{request_id}.txt"
    packet_path = workflow_dir / packet_rel
    packet_header = (
        f"CONTEXT PACKET {request_id}\n"
        f"source: {relative}\n"
        f"sourceSha256: {current_sha}\n"
        f"reason: {str(request.get('reason') or '').strip()}\n"
        f"selector: {json.dumps(resolved_selector, sort_keys=True)}\n"
        f"---\n"
    )
    packet_text = packet_header + excerpt
    _write_text(packet_path, packet_text)

    return {
        "schema": CONTEXT_PACKET_SCHEMA,
        "id": request_id,
        "round": round_number,
        "path": relative,
        "reason": str(request.get("reason") or "").strip(),
        "required": bool(request.get("required", True)),
        "selector": resolved_selector,
        "sourceSha256": current_sha,
        "packetSha256": _sha256_text(packet_text),
        "packetChars": len(packet_text),
        "excerptCharsBeforeTruncation": original_excerpt_chars,
        "truncated": truncated,
        "contentFile": packet_rel.as_posix(),
    }


def _read_context_packet_text(state: dict[str, Any], packet: dict[str, Any]) -> str:
    workflow_dir = Path(str(state.get("workflowDir") or "")).resolve()
    relative = str(packet.get("contentFile") or "")
    path = (workflow_dir / relative).resolve()
    try:
        path.relative_to(workflow_dir)
    except ValueError as exc:
        raise GrowthPromptError(f"unsafe context packet path: {relative!r}") from exc
    text = path.read_text(encoding="utf-8")
    expected = str(packet.get("packetSha256") or "")
    actual = _sha256_text(text)
    if expected and expected != actual:
        raise GrowthPromptError(f"context packet hash mismatch: {relative}")
    return text


def _accepted_context_chars(state: dict[str, Any]) -> int:
    return sum(int(_object(packet).get("packetChars") or 0) for packet in _list(state.get("contextPackets")))


def _materialize_catalog_queries(state: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    queries = [_object(item) for item in _list(decision.get("catalogQueries"))]
    budget = _object(state.get("contextBudget"))
    max_queries = int(budget.get("maxCatalogQueriesPerRound") or MAX_CATALOG_QUERIES_PER_ROUND)
    max_results = int(budget.get("maxCatalogResultsPerQuery") or MAX_CATALOG_RESULTS_PER_QUERY)
    if len(queries) > max_queries:
        raise GrowthPromptError(
            f"model requested {len(queries)} catalog queries; round limit is {max_queries}"
        )

    catalog = _compact_catalog(_list(state.get("sourceCatalog")))
    discovered = {str(item) for item in _list(state.get("discoveredSourcePaths"))}
    results: list[dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        query_id = _safe_id(query.get("id"), fallback=f"catalog-query-{index}")
        terms = [term.lower() for term in _strings(query.get("terms"))][:MAX_SEARCH_TERMS]
        if not terms:
            raise GrowthPromptError(f"catalog query {query_id!r} needs at least one term")
        matches: list[dict[str, Any]] = []
        scored: list[tuple[int, dict[str, Any]]] = []
        repo_root = Path(str(state.get("repoRoot") or "")).resolve()
        for item in catalog:
            path = str(item.get("path") or "")
            lowered_path = path.lower()
            path_hits = sum(1 for term in terms if term in lowered_path)
            content_hits = 0
            source_path = (repo_root / path).resolve()
            try:
                source_path.relative_to(repo_root)
                source_text = source_path.read_text(encoding="utf-8").lower()
            except (OSError, UnicodeDecodeError, ValueError):
                source_text = ""
            for term in terms:
                content_hits += source_text.count(term)
            if path_hits or content_hits:
                enriched = dict(item)
                enriched["pathTermHits"] = path_hits
                enriched["contentTermHits"] = content_hits
                score = path_hits * 1000 + min(content_hits, 999)
                scored.append((score, enriched))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("path") or "")))
        matches = [item for _, item in scored[:max_results]]
        for match in matches:
            discovered.add(str(match.get("path") or ""))
        results.append(
            {
                "id": query_id,
                "terms": terms,
                "reason": str(query.get("reason") or "").strip(),
                "matches": matches,
                "matchCountReturned": len(matches),
            }
        )

    state["discoveredSourcePaths"] = sorted(path for path in discovered if path)
    return results


def _materialize_context_requests(state: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    requests = [_object(item) for item in _list(decision.get("contextRequests"))]
    budget = _object(state.get("contextBudget"))
    max_requests = int(budget.get("maxRequestsPerRound") or DEFAULT_MAX_REQUESTS_PER_ROUND)
    max_round_chars = int(budget.get("maxCharsPerRound") or DEFAULT_MAX_CHARS_PER_ROUND)
    max_total_chars = int(
        budget.get("maxAcceptedContextChars") or DEFAULT_MAX_ACCEPTED_CONTEXT_CHARS
    )
    if len(requests) > max_requests:
        raise GrowthPromptError(
            f"model requested {len(requests)} context slices; round limit is {max_requests}"
        )

    current_total = _accepted_context_chars(state)
    round_number = int(state.get("round") or 0) + 1
    packets: list[dict[str, Any]] = []
    round_chars = 0
    for ordinal, request in enumerate(requests, start=1):
        packet = _materialize_request(
            state=state,
            request=request,
            round_number=round_number,
            ordinal=ordinal,
        )
        packet_chars = int(packet.get("packetChars") or 0)
        if round_chars + packet_chars > max_round_chars:
            raise GrowthPromptError(
                f"requested context exceeds per-round budget after {packet['id']!r}: "
                f"{round_chars + packet_chars} > {max_round_chars} chars"
            )
        if current_total + round_chars + packet_chars > max_total_chars:
            raise GrowthPromptError(
                f"requested context exceeds workflow total budget after {packet['id']!r}: "
                f"{current_total + round_chars + packet_chars} > {max_total_chars} chars"
            )
        packets.append(packet)
        round_chars += packet_chars
    return packets


def _ledger_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": state.get("phase"),
        "round": state.get("round"),
        "moduleProposal": state.get("moduleProposal"),
        "facts": state.get("facts"),
        "unknowns": state.get("unknowns"),
        "design": state.get("design"),
        "visibleSourceCount": len(_list(state.get("discoveredSourcePaths"))),
        "catalogDiscoveries": state.get("catalogDiscoveries"),
        "acceptedContext": [
            {
                "id": packet.get("id"),
                "path": packet.get("path"),
                "reason": packet.get("reason"),
                "selector": packet.get("selector"),
                "sourceSha256": packet.get("sourceSha256"),
                "packetSha256": packet.get("packetSha256"),
                "truncated": packet.get("truncated"),
            }
            for packet in _list(state.get("contextPackets"))
            if isinstance(packet, dict)
        ],
        "contextCharsAccepted": _accepted_context_chars(state),
        "contextBudget": state.get("contextBudget"),
    }


def _context_prompt(
    state: dict[str, Any],
    *,
    new_packets: list[dict[str, Any]],
    new_catalog_results: list[dict[str, Any]],
    phase: str,
) -> str:
    rendered_packets = []
    for packet in new_packets:
        rendered_packets.append(_read_context_packet_text(state, packet))

    phase_instruction = (
        "Determine whether the source-grounded translation is now sufficient to begin module design."
        if phase == "translate"
        else "Use the new evidence to refine the module design and determine whether it is now safe to generate code."
    )
    readiness_field = "readyForDesign" if phase == "translate" else "readyForCode"

    return f"""TASK: Incorporate the newly admitted source context into the game-growth ledger.  DO NOT write module code.

This is an incremental state-to-module workflow.  The host has materialized exactly the source slices requested in the previous round.  Treat packet contents as source evidence with the recorded hashes.  Do not assume surrounding source that is not present.

CURRENT GAME BRIEF:
{_json_block(state['brief'])}

CURRENT PROJECT SUMMARY:
{_json_block(_compact_project_context(_object(state['projectContext'])))}

CURRENT GROWTH LEDGER:
{_json_block(_ledger_summary(state))}

NEW CATALOG DISCOVERY RESULTS (path metadata/hit counts only; no source excerpts):
{_json_block(new_catalog_results)}

NEW CONTEXT PACKETS:

{chr(10).join(rendered_packets) if rendered_packets else "[NO NEW CONTENT PACKETS THIS ROUND]"}

YOUR JOB:
- Update facts: retain source/brief facts that still matter; correct prior inferences when source contradicts them.
- Update unknowns: remove resolved unknowns, keep unresolved blockers concrete.
- Keep the module boundary small and explicit.
- For every existing-state read or write the design depends on, seek an exact source-grounded integration boundary or record it as a runtime gap later.
- Never turn a derived UI field into authoritative state.
- Never let a generated module silently own state already authoritatively owned elsewhere.
- {phase_instruction}
- If another source slice is necessary, request the smallest slice that resolves one named unknown.
- If the needed file is not in the visible catalog, use catalogQueries to discover candidate paths first.
- If no more source is necessary, set {readiness_field}=true, catalogQueries=[], and contextRequests=[].

VISIBLE SOURCE CATALOG (paths currently eligible for content requests):
{_json_block(_visible_catalog(state))}

OUTPUT CONTRACT:
Return EXACTLY one JSON object and no markdown/prose.  The object MUST validate against the canonical schema below.

{_phase_contract_schema_text(phase=phase)}

The response must stay in phase={phase!r}.
"""


def _design_prompt(state: dict[str, Any]) -> str:
    packet_texts = [
        _read_context_packet_text(state, _object(packet))
        for packet in _list(state.get("contextPackets"))
    ]
    return f"""TASK: Design the source-grounded state translation and module boundary for the new Main Computer per-game module.  DO NOT write JavaScript yet.

You now have a bounded, audited context ledger.  Convert it into a concrete module design.  The design is the translation layer between CURRENT GAME STATE and NEW MODULE STATE.  It must say exactly what is read from existing runtime state, what new authoritative state the module owns, what is derived only, what events/actions cross the boundary, and how meaningful terminal consequences return to future game state.

GAME BRIEF:
{_json_block(state['brief'])}

PROJECT SUMMARY:
{_json_block(_compact_project_context(_object(state['projectContext'])))}

TRANSLATION LEDGER:
{_json_block(_ledger_summary(state))}

ACCEPTED SOURCE CONTEXT:

{chr(10).join(packet_texts)}

DESIGN REQUIREMENTS:
- Preserve all brief hard rules and meaningful-outcome requirements.
- Give the module one authoritative serializable state; list every newly owned top-level field and why it belongs here.
- ``stateTranslation.readsExistingState`` must map each needed current-game value to a source-grounded path/function/event/hook when known.
- ``stateTranslation.seedsModuleState`` must say how existing values become initial module state without copying unrelated world state.
- ``stateTranslation.ownedState`` must contain only state whose authority is transferred/created for this module.
- ``stateTranslation.derivedOnlyState`` must include tracker/sensor/UI observations that must never become a second source of truth.
- ``stateTranslation.outboundConsequences`` must map each persistent consequence class to an existing write/commit boundary or an explicit runtime gap.
- ``stateTranslation.persistence`` must say how save/restore preserves hidden state and idempotent outcome commitment.
- Define the turn ordering and encounter boundaries precisely enough that FARM/HOP, tracker staleness, corridor evidence, and simultaneous movement have one deterministic meaning.
- Define the spy policy inputs.  The policy may read authoritative world state available to the spy simulation, but must not receive player-only derived observations as a hidden shortcut.
- Define bounded causal evidence sufficient to explain terminal outcome receipts without retaining unbounded logs.
- Do not invent engine APIs.  Missing hooks go into ``integration.runtimeGaps``.
- If a design-critical source fact is still missing, request context instead of guessing and set readyForCode=false.

REQUIRED DESIGN SHAPE (values are descriptive placeholders, not answers):
{{
  "schema": "{DESIGN_SCHEMA}",
  "moduleId": "stable id",
  "responsibility": {{"owns": [], "doesNotOwn": []}},
  "stateTranslation": {{
    "readsExistingState": [{{"need":"...","source":"path/symbol/hook","evidence":["packet id"]}}],
    "seedsModuleState": [{{"from":"...","to":"...","rule":"..."}}],
    "ownedState": [{{"path":"module field","authority":"why module owns it"}}],
    "derivedOnlyState": [{{"path":"derived field","derivedFrom":"authoritative inputs"}}],
    "outboundConsequences": [{{"type":"...","target":"existing boundary or runtime gap","idempotency":"..."}}],
    "persistence": {{"save":"...","restore":"...","migration":"..."}}
  }},
  "turnModel": {{"phases": [], "encounterChecks": [], "randomnessRule":"..."}},
  "informationModel": {{"tracker":"...","corridorSensors":"...","asymmetry":"..."}},
  "economy": {{"farm":"...","hop":"...","opportunityCost":"..."}},
  "spyPolicy": {{"objective":"...","inputs":[],"legalOutputs":["FARM","HOP"]}},
  "outcomes": {{"terminalPredicates":[],"receiptInputs":[],"persistentEffectClasses":[]}},
  "integration": {{"reads":[],"writes":[],"eventsIn":[],"eventsOut":[],"runtimeGaps":[]}},
  "exports": [],
  "invariants": []
}}

VISIBLE SOURCE CATALOG FOR LAST-MILE REQUESTS:
{_json_block(_visible_catalog(state))}

If the needed file is not visible, use catalogQueries in the decision response to discover paths before requesting content.

OUTPUT CONTRACT:
Return EXACTLY one JSON object and no markdown/prose.  The object MUST validate against the canonical decision schema and referenced module-design schema below, with phase="design".

{_phase_contract_schema_text(phase='design')}

If more source is needed, readyForCode=false. Use catalogQueries to discover unseen paths and contextRequests for precise blocking source slices from the visible catalog.
"""


def _code_prompt(state: dict[str, Any]) -> str:
    design = _object(state.get("design"))
    if design.get("schema") != DESIGN_SCHEMA:
        raise GrowthPromptError("cannot build code prompt without a validated module design")

    evidence_index = [
        {
            "id": packet.get("id"),
            "path": packet.get("path"),
            "reason": packet.get("reason"),
            "selector": packet.get("selector"),
            "sourceSha256": packet.get("sourceSha256"),
            "packetSha256": packet.get("packetSha256"),
        }
        for packet in _list(state.get("contextPackets"))
        if isinstance(packet, dict)
    ]

    desired_exports = _strings(_object(state.get("brief")).get("desiredExports"))
    return f"""TASK: Generate the per-game module described by the audited state-to-module design below.

This is the CODE stage of an incremental growth workflow.  Do not redesign the game from scratch.  The brief is governing mechanics; the module design is the source-grounded translation contract; the evidence index identifies the exact source slices used to reach that contract.  If the design records a runtime gap, preserve it in integration_contract.json instead of fabricating an API.

Return EXACTLY three files using the markers below and no prose outside them.

BEGIN game_module_manifest.json
{{
  "schema": "game.perGameModuleManifest.v1",
  "kind": "per-game-module",
  "id": "{str(state['brief'].get('id') or '')}",
  "title": "{str(state['brief'].get('title') or '')}",
  "version": "0.1.0",
  "entry": "game_module.js",
  "stateVersion": "game.perGameModuleState.v1",
  "outcomeReceiptVersion": "game.perGameModuleOutcomeReceipt.v1"
}}
END game_module_manifest.json

BEGIN game_module.js
// Complete self-contained ES module. No imports. No DOM/network/storage access.
END game_module.js

BEGIN integration_contract.json
{{
  "schema": "game.perGameModuleIntegrationContract.v1",
  "kind": "per-game-module-integration-contract",
  "moduleId": "{str(state['brief'].get('id') or '')}",
  "stateTranslation": {{}},
  "reads": [],
  "writes": [],
  "eventsIn": [],
  "eventsOut": [],
  "persistence": {{}},
  "runtimeGaps": [],
  "sourceEvidence": []
}}
END integration_contract.json

GOVERNING INVARIANT:
A generated game module may terminate only in an outcome causally derived from authoritative play history, and that terminal result must commit at least one persistent gameplay consequence beyond end-screen text.

GAME BRIEF:
{_json_block(state['brief'])}

SOURCE-GROUNDED MODULE DESIGN:
{_json_block(design)}

SOURCE EVIDENCE INDEX:
{_json_block(evidence_index)}

CURRENT PROJECT SUMMARY:
{_json_block(_compact_project_context(_object(state['projectContext'])))}

CODE CONTRACT:
- Implement the design, not a new architecture.
- One authoritative serializable module state.  Derived observations are never truth stores.
- The module must translate only the existing-state reads explicitly declared by the design; unsupported reads remain runtime gaps.
- New hidden spy state must survive serialize/restore exactly.
- Player and spy share FARM/HOP primitive legality.  Higher-level hunt/retreat/hold/power-up behavior is policy over those primitives.
- Same-system occupancy at documented encounter boundaries is the encounter rule.  Corridor crossing alone is not an encounter.
- Tracker and corridor sensor outputs are derived restricted observations and cannot expose hidden exact spy location.
- Detection versus stealth/signature suppression must permit asymmetric observations from the same authoritative transit history.
- Farming must trade information freshness/time against power/resources/capability for both sides.
- All result-affecting randomness must be deterministic from persisted seed/state or explicitly persisted when first sampled.
- Keep transit/causal history bounded while retaining enough evidence to reconstruct every outcome-receipt claim.
- Outcome receipt consequences are idempotent and future-facing.
- The module itself must not commit browser/storage mutations; it returns consequence receipts to the integration boundary.
- integration_contract.json must reproduce the design's stateTranslation and list the evidence ids/source paths supporting each concrete integration claim.
- Do not claim a runtimeGap has been solved in code unless the design contains a source-grounded hook for it.
- Plain JavaScript ES module; no imports, window, document, fetch, XMLHttpRequest, storage APIs, eval, Function, filesystem, or network access.
- Avoid mutable module globals.  Validate inputs and return cloned/new data.

REQUIRED/REQUESTED EXPORTS:
{_json_block(desired_exports)}

FINAL SEMANTIC CHECK:
- Does initial module state come from explicit translated game-state inputs plus seed/options, rather than fabricated global state?
- Can the spy start anywhere allowed by the real network?
- Can both actors farm/hop under the same primitive rules?
- Can information age while either actor farms or moves?
- Can equipment create information asymmetry without omniscience?
- Can save/restore reproduce hidden state and future meaning?
- Can different causal histories produce different persistent consequences?
- Can every consequence claim be traced to retained authoritative state/events?
- Does integration_contract.json clearly separate implemented translation from runtime gaps?

Return only the three marked files.
"""


def _prompt_filename(state: dict[str, Any], kind: str) -> str:
    round_number = int(state.get("round") or 0)
    if kind == "translate":
        return "00_translate_game_state.md"
    if kind == "context":
        return f"{round_number:02d}_grow_context.md"
    if kind == "design":
        return f"{round_number:02d}_design_state_translation.md"
    if kind == "code":
        return f"{round_number:02d}_generate_module_code.md"
    return f"{round_number:02d}_{_safe_id(kind, fallback='prompt')}.md"


def _record_prompt(state: dict[str, Any], *, kind: str, prompt: str) -> Path:
    workflow_dir = Path(str(state.get("workflowDir") or "")).resolve()
    filename = _prompt_filename(state, kind)
    path = workflow_dir / filename
    _write_text(path, prompt)
    state["nextPrompt"] = {
        "kind": kind,
        "path": filename,
        "sha256": _sha256_text(prompt),
        "chars": len(prompt),
    }
    return path


def init_workflow(
    *,
    repo_root: Path,
    output_dir: Path,
    project: str,
    brief: dict[str, Any],
    max_requests_per_round: int = DEFAULT_MAX_REQUESTS_PER_ROUND,
    max_chars_per_packet: int = DEFAULT_MAX_CHARS_PER_PACKET,
    max_chars_per_round: int = DEFAULT_MAX_CHARS_PER_ROUND,
    max_accepted_context_chars: int = DEFAULT_MAX_ACCEPTED_CONTEXT_CHARS,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state = _new_state(
        repo_root=repo_root.resolve(),
        workflow_dir=output_dir,
        project=project,
        brief=brief,
        max_requests_per_round=max_requests_per_round,
        max_chars_per_packet=max_chars_per_packet,
        max_chars_per_round=max_chars_per_round,
        max_accepted_context_chars=max_accepted_context_chars,
    )
    prompt = _translation_prompt(state)
    prompt_path = _record_prompt(state, kind="translate", prompt=prompt)
    _write_json(output_dir / "growth_state.json", state)
    return {
        "ok": True,
        "schema": STATE_SCHEMA,
        "componentVersion": COMPONENT_VERSION,
        "phase": state["phase"],
        "round": state["round"],
        "sourceCatalogCount": len(state["sourceCatalog"]),
        "visibleSourceCount": len(state["discoveredSourcePaths"]),
        "systemCount": state["projectContext"]["spaceNavigation"]["systemCount"],
        "routeCount": state["projectContext"]["spaceNavigation"]["routeCount"],
        "state": str(output_dir / "growth_state.json"),
        "nextPromptKind": "translate",
        "nextPrompt": str(prompt_path),
        "nextPromptSha256": state["nextPrompt"]["sha256"],
    }


def advance_workflow(*, workflow_dir: Path, response_path: Path) -> dict[str, Any]:
    workflow_dir = workflow_dir.resolve()
    state = _load_state(workflow_dir)
    if bool(state.get("complete")):
        raise GrowthPromptError("workflow is already complete")

    response, _response_created, _response_normalized = _managed_response_json(response_path)
    current_phase = str(state.get("phase") or "translate")
    decision = _validate_decision(response, expected_phase=current_phase)
    _validate_workflow_constraints(state, decision)

    state["moduleProposal"] = _object(decision.get("moduleProposal")) or _object(state.get("moduleProposal"))
    state["facts"] = _merge_by_id(
        _list(state.get("facts")), _list(decision.get("facts")), prefix="fact"
    )
    state["unknowns"] = _merge_by_id(
        [], _list(decision.get("unknowns")), prefix="unknown"
    )
    if isinstance(decision.get("design"), dict) and decision.get("design"):
        state["design"] = _object(decision.get("design"))

    catalog_queries = _list(decision.get("catalogQueries"))
    context_requests = _list(decision.get("contextRequests"))
    state["history"].append(
        {
            "round": int(state.get("round") or 0),
            "phase": current_phase,
            "responsePath": str(response_path.resolve()),
            "responseSha256": _sha256_bytes(response_path.resolve().read_bytes()),
            "summary": str(decision.get("summary") or "").strip(),
            "catalogQueryCount": len(catalog_queries),
            "contextRequestCount": len(context_requests),
            "readyForDesign": bool(decision.get("readyForDesign")),
            "readyForCode": bool(decision.get("readyForCode")),
        }
    )

    next_kind: str
    if catalog_queries or context_requests:
        catalog_results = _materialize_catalog_queries(state, decision)
        if catalog_results:
            state["catalogDiscoveries"].extend(catalog_results)
        packets = _materialize_context_requests(state, decision)
        state["contextPackets"].extend(packets)
        state["round"] = int(state.get("round") or 0) + 1
        prompt = _context_prompt(
            state,
            new_packets=packets,
            new_catalog_results=catalog_results,
            phase=current_phase,
        )
        next_kind = "context"
    elif current_phase == "translate" and bool(decision.get("readyForDesign")):
        state["phase"] = "design"
        state["round"] = int(state.get("round") or 0) + 1
        prompt = _design_prompt(state)
        next_kind = "design"
    elif current_phase == "design" and bool(decision.get("readyForCode")):
        design = _object(state.get("design"))
        if design.get("schema") != DESIGN_SCHEMA:
            raise GrowthPromptError(
                f"readyForCode requires design.schema={DESIGN_SCHEMA!r}"
            )
        state["round"] = int(state.get("round") or 0) + 1
        prompt = _code_prompt(state)
        next_kind = "code"
        state["complete"] = True
    else:
        readiness = "readyForDesign" if current_phase == "translate" else "readyForCode"
        raise GrowthPromptError(
            f"decision made no progress: no catalogQueries/contextRequests and {readiness} is not true"
        )

    prompt_path = _record_prompt(state, kind=next_kind, prompt=prompt)
    _write_json(workflow_dir / "growth_state.json", state)

    return {
        "ok": True,
        "schema": STATE_SCHEMA,
        "componentVersion": COMPONENT_VERSION,
        "phase": state["phase"],
        "round": state["round"],
        "contextPacketCount": len(state["contextPackets"]),
        "contextCharsAccepted": _accepted_context_chars(state),
        "factCount": len(state["facts"]),
        "unknownCount": len(state["unknowns"]),
        "complete": bool(state.get("complete")),
        "state": str(workflow_dir / "growth_state.json"),
        "nextPromptKind": next_kind,
        "nextPrompt": str(prompt_path),
        "nextPromptSha256": state["nextPrompt"]["sha256"],
    }



def current_prompt_text(*, workflow_dir: Path) -> str:
    """Return the current model-ready prompt, verifying the ledger reference/hash."""

    workflow_dir = workflow_dir.resolve()
    state = _load_state(workflow_dir)
    prompt_ref = _object(state.get("nextPrompt"))
    relative = str(prompt_ref.get("path") or "").strip()
    if not relative:
        raise GrowthPromptError("growth state has no current prompt")

    prompt_path = (workflow_dir / relative).resolve()
    try:
        prompt_path.relative_to(workflow_dir)
    except ValueError as exc:
        raise GrowthPromptError(f"unsafe current prompt path: {relative!r}") from exc
    if not prompt_path.is_file():
        raise GrowthPromptError(f"current prompt file not found: {prompt_path}")

    prompt = prompt_path.read_text(encoding="utf-8")
    expected_sha = str(prompt_ref.get("sha256") or "").strip()
    actual_sha = _sha256_text(prompt)
    if expected_sha and actual_sha != expected_sha:
        raise GrowthPromptError(
            f"current prompt hash mismatch: expected {expected_sha}, got {actual_sha}"
        )

    expected_chars = prompt_ref.get("chars")
    if expected_chars is not None and int(expected_chars) != len(prompt):
        raise GrowthPromptError(
            f"current prompt length mismatch: expected {int(expected_chars)}, got {len(prompt)}"
        )
    return prompt

def status_workflow(*, workflow_dir: Path) -> dict[str, Any]:
    workflow_dir = workflow_dir.resolve()
    state = _load_state(workflow_dir)
    return {
        "ok": True,
        "schema": STATE_SCHEMA,
        "componentVersion": COMPONENT_VERSION,
        "phase": state.get("phase"),
        "round": state.get("round"),
        "complete": bool(state.get("complete")),
        "project": state.get("project"),
        "moduleId": _object(state.get("brief")).get("id"),
        "sourceCatalogCount": len(_list(state.get("sourceCatalog"))),
        "visibleSourceCount": len(_list(state.get("discoveredSourcePaths"))),
        "contextPacketCount": len(_list(state.get("contextPackets"))),
        "contextCharsAccepted": _accepted_context_chars(state),
        "factCount": len(_list(state.get("facts"))),
        "unknownCount": len(_list(state.get("unknowns"))),
        "nextPrompt": state.get("nextPrompt"),
        "history": state.get("history"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incrementally translate current game/source state into a source-grounded per-game module prompt."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a growth ledger and the first translation prompt.")
    init.add_argument("--project", default="webgl-demo")
    init.add_argument("--repo-root", default=None)
    init.add_argument("--brief", default=None)
    init.add_argument("--output-dir", required=True)
    init.add_argument("--max-requests-per-round", type=int, default=DEFAULT_MAX_REQUESTS_PER_ROUND)
    init.add_argument("--max-chars-per-packet", type=int, default=DEFAULT_MAX_CHARS_PER_PACKET)
    init.add_argument("--max-chars-per-round", type=int, default=DEFAULT_MAX_CHARS_PER_ROUND)
    init.add_argument(
        "--max-accepted-context-chars",
        type=int,
        default=DEFAULT_MAX_ACCEPTED_CONTEXT_CHARS,
    )

    advance = subparsers.add_parser("advance", help="Consume one model JSON decision and emit the next prompt.")
    advance.add_argument("--workflow-dir", required=True)
    advance.add_argument("--response", required=True)

    status = subparsers.add_parser("status", help="Print the current growth-ledger status.")
    status.add_argument("--workflow-dir", required=True)

    prompt = subparsers.add_parser(
        "prompt",
        help="Print exactly the current model-ready prompt text (no JSON envelope).",
    )
    prompt.add_argument("--workflow-dir", required=True)

    schema = subparsers.add_parser(
        "schema",
        help="Print a canonical JSON Schema contract (no receipt envelope).",
    )
    schema.add_argument("--kind", choices=("decision", "design"), default="decision")

    validate = subparsers.add_parser(
        "validate",
        help="Validate one model response against the canonical schema and current workflow constraints without advancing it.",
    )
    validate.add_argument("--workflow-dir", required=True)
    validate.add_argument("--response", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_module().resolve()
            brief = _read_json(Path(args.brief).resolve()) if args.brief else dict(DEFAULT_SPY_HUNT_BRIEF)
            result = init_workflow(
                repo_root=repo_root,
                output_dir=Path(args.output_dir),
                project=str(args.project or "webgl-demo"),
                brief=brief,
                max_requests_per_round=args.max_requests_per_round,
                max_chars_per_packet=args.max_chars_per_packet,
                max_chars_per_round=args.max_chars_per_round,
                max_accepted_context_chars=args.max_accepted_context_chars,
            )
        elif args.command == "advance":
            result = advance_workflow(
                workflow_dir=Path(args.workflow_dir),
                response_path=Path(args.response),
            )
        elif args.command == "status":
            result = status_workflow(workflow_dir=Path(args.workflow_dir))
        elif args.command == "prompt":
            prompt = current_prompt_text(workflow_dir=Path(args.workflow_dir))
            sys.stdout.write(prompt)
            if prompt and not prompt.endswith("\n"):
                sys.stdout.write("\n")
            return 0
        elif args.command == "schema":
            decision_schema, design_schema = _contract_schemas()
            schema_value = decision_schema if args.kind == "decision" else design_schema
            sys.stdout.write(json.dumps(schema_value, indent=2, sort_keys=False))
            sys.stdout.write("\n")
            return 0
        elif args.command == "validate":
            result = validate_response_contract(
                workflow_dir=Path(args.workflow_dir),
                response_path=Path(args.response),
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("ok") else 2
        else:
            raise GrowthPromptError(f"unknown command: {args.command}")
    except (GrowthPromptError, PromptGeneratorError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
