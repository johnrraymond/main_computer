from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from main_computer.gameplay_plugin_manifest import validate_gameplay_plugin_manifest
from main_computer.gameplay_template_registry import validate_gameplay_plugin_content_against_template_registry


GAMEPLAY_PLUGIN_CONTENT_SCHEMA = "game.gameplayPluginContent.v1"

_FORBIDDEN_EXECUTABLE_KEYS = {
    "code",
    "eval",
    "function",
    "handler",
    "javascript",
    "js",
    "localstorage",
    "mutation",
    "onentercode",
    "onexitcode",
    "runtimecode",
    "runtimeMutation".lower(),
    "script",
    "scripturl",
}


def _mapping(value: Any, label: str, problems: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        problems.append(f"{label} must be an object")
        return {}
    return value


def _records(value: Any, label: str, problems: list[str]) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{label} must be a list")
        return []
    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            problems.append(f"{label}[{index}] must be an object")
            continue
        records.append(item)
    return records


def _strings(value: Any, label: str, problems: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{label} must be a list")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        string_value = str(item or "").strip()
        if not string_value:
            problems.append(f"{label}[{index}] must be a non-empty string")
            continue
        result.append(string_value)
    if len(result) != len(set(result)):
        problems.append(f"{label} contains duplicate values")
    return result


def _string(value: Any) -> str:
    return str(value or "").strip()


def _content_records(manifest: Mapping[str, Any], collection_name: str, problems: list[str]) -> list[Mapping[str, Any]]:
    content = _mapping(manifest.get("content"), "manifest.content", problems)
    return _records(content.get(collection_name), f"manifest.content.{collection_name}", problems)


def _record_index_by_path(
    records: Sequence[Mapping[str, Any]],
    *,
    label: str,
    problems: list[str],
) -> dict[str, Mapping[str, Any]]:
    by_path: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        path = _string(record.get("path"))
        if not path:
            problems.append(f"{label}[{index}] is missing path")
            continue
        if path in by_path:
            problems.append(f"{label}[{index}].path duplicates {path}")
            continue
        by_path[path] = record
    return by_path


def _ids_from_manifest_records(
    records: Sequence[Mapping[str, Any]],
    *,
    label: str,
    problems: list[str],
) -> set[str]:
    ids: set[str] = set()
    for index, record in enumerate(records):
        record_id = _string(record.get("id"))
        if not record_id:
            problems.append(f"{label}[{index}] is missing id")
            continue
        if record_id in ids:
            problems.append(f"{label} contains duplicate id {record_id}")
        ids.add(record_id)
    return ids


def _forbidden_field_paths(value: Any, *, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key or "")
            normal_key = key_text.replace("-", "").replace("_", "").lower()
            child_path = f"{prefix}.{key_text}"
            if normal_key in _FORBIDDEN_EXECUTABLE_KEYS:
                paths.append(child_path)
            paths.extend(_forbidden_field_paths(item, prefix=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_forbidden_field_paths(item, prefix=f"{prefix}[{index}]"))
    return paths


def _manifest_id_sets(manifest: Mapping[str, Any], problems: list[str]) -> dict[str, set[str]]:
    content = _mapping(manifest.get("content"), "manifest.content", problems)
    requires = _mapping(manifest.get("requires"), "manifest.requires", problems)
    scope = _mapping(manifest.get("scope"), "manifest.scope", problems)

    return {
        "scenarios": _ids_from_manifest_records(
            _records(content.get("scenarios"), "manifest.content.scenarios", problems),
            label="manifest.content.scenarios",
            problems=problems,
        ),
        "encounters": _ids_from_manifest_records(
            _records(content.get("encounters"), "manifest.content.encounters", problems),
            label="manifest.content.encounters",
            problems=problems,
        ),
        "receipts": _ids_from_manifest_records(
            _records(content.get("receipts"), "manifest.content.receipts", problems),
            label="manifest.content.receipts",
            problems=problems,
        ),
        "consequences": _ids_from_manifest_records(
            _records(content.get("consequences"), "manifest.content.consequences", problems),
            label="manifest.content.consequences",
            problems=problems,
        ),
        "encounterTemplates": set(
            _strings(requires.get("encounterTemplates"), "manifest.requires.encounterTemplates", problems)
        ),
        "actorArchetypes": set(
            _strings(requires.get("actorArchetypes"), "manifest.requires.actorArchetypes", problems)
        ),
        "objectiveTypes": set(
            _strings(requires.get("objectiveTypes"), "manifest.requires.objectiveTypes", problems)
        ),
        "consequenceTypes": set(
            _strings(requires.get("consequenceTypes"), "manifest.requires.consequenceTypes", problems)
        ),
        "systems": set(
            _strings(requires.get("systems"), "manifest.requires.systems", problems)
        )
        | set(_strings(scope.get("allowedSystems"), "manifest.scope.allowedSystems", problems)),
        "destinations": set(
            _strings(requires.get("destinations"), "manifest.requires.destinations", problems)
        )
        | set(_strings(scope.get("allowedDestinations"), "manifest.scope.allowedDestinations", problems)),
    }


def _stage_refs(stage: Mapping[str, Any], label: str, problems: list[str]) -> list[str]:
    refs: list[str] = []
    for key in ("nextStage", "onComplete", "onFail"):
        ref = _string(stage.get(key))
        if ref:
            refs.append(ref)
    for choice_index, choice in enumerate(_records(stage.get("choices"), f"{label}.choices", problems)):
        next_stage = _string(choice.get("nextStage"))
        outcome_id = _string(choice.get("outcomeId"))
        if next_stage:
            refs.append(next_stage)
        if next_stage and outcome_id:
            problems.append(f"{label}.choices[{choice_index}] must not set both nextStage and outcomeId")
        if not next_stage and not outcome_id:
            problems.append(f"{label}.choices[{choice_index}] must set nextStage or outcomeId")
    return refs


def _validate_stage_graph(scenario: Mapping[str, Any], label: str, problems: list[str]) -> None:
    stages = _records(scenario.get("stages"), f"{label}.stages", problems)
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, stage in enumerate(stages):
        stage_id = _string(stage.get("id"))
        if not stage_id:
            problems.append(f"{label}.stages[{index}] is missing id")
            continue
        if stage_id in by_id:
            problems.append(f"{label}.stages contains duplicate stage id {stage_id}")
        by_id[stage_id] = stage

    entry_stage = _string(scenario.get("entryStage"))
    if not entry_stage:
        problems.append(f"{label}.entryStage is required")
        return
    if entry_stage not in by_id:
        problems.append(f"{label}.entryStage references missing stage {entry_stage}")
        return

    edges: dict[str, list[str]] = {}
    for stage_id, stage in by_id.items():
        refs = _stage_refs(stage, f"{label}.stages[{stage_id}]", problems)
        edges[stage_id] = refs
        for ref in refs:
            if ref not in by_id:
                problems.append(f"{label}.stages[{stage_id}] references missing stage {ref}")

    reachable: set[str] = set()
    pending = [entry_stage]
    while pending:
        stage_id = pending.pop()
        if stage_id in reachable:
            continue
        reachable.add(stage_id)
        pending.extend(ref for ref in edges.get(stage_id, []) if ref in by_id)

    unreachable = sorted(set(by_id) - reachable)
    if unreachable:
        problems.append(f"{label}.stages has unreachable stages {unreachable}")

    completion_stages = set(_strings(scenario.get("completionStages"), f"{label}.completionStages", problems))
    terminal_stage_ids = {
        stage_id
        for stage_id, stage in by_id.items()
        if not edges.get(stage_id)
        or _string(stage.get("kind")) in {"resolution", "failure"}
        or stage_id in completion_stages
    }
    reachable_terminal_stages = sorted(terminal_stage_ids & reachable)
    if not reachable_terminal_stages:
        problems.append(f"{label}.stages must have at least one reachable terminal/completion stage")

    for completion_stage in completion_stages:
        if completion_stage not in by_id:
            problems.append(f"{label}.completionStages references missing stage {completion_stage}")


def _validate_scenario_content(
    scenario: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    index: int,
    all_ids: dict[str, set[str]],
    problems: list[str],
) -> None:
    label = f"content.scenarios[{index}]"
    if scenario.get("schema") != GAMEPLAY_PLUGIN_CONTENT_SCHEMA:
        problems.append(f"{label}.schema must be {GAMEPLAY_PLUGIN_CONTENT_SCHEMA}")
    if scenario.get("kind") != "scenario":
        problems.append(f"{label}.kind must be scenario")

    scenario_id = _string(scenario.get("id"))
    manifest_id = _string(record.get("id"))
    if scenario_id != manifest_id:
        problems.append(f"{label}.id must match manifest id {manifest_id}")

    declared_encounter_ids = set(_strings(record.get("encounterIds"), f"manifest.content.scenarios[{index}].encounterIds", problems))
    content_encounter_ids = set(_strings(scenario.get("encounterIds"), f"{label}.encounterIds", problems))
    if content_encounter_ids != declared_encounter_ids:
        problems.append(
            f"{label}.encounterIds must match manifest declaration {sorted(declared_encounter_ids)}"
        )
    for encounter_id in content_encounter_ids:
        if encounter_id not in all_ids["encounters"]:
            problems.append(f"{label}.encounterIds references missing encounter {encounter_id}")

    for receipt_id in _strings(scenario.get("receiptIds"), f"{label}.receiptIds", problems):
        if receipt_id not in all_ids["receipts"]:
            problems.append(f"{label}.receiptIds references undeclared receipt {receipt_id}")

    entry = scenario.get("entry")
    if isinstance(entry, Mapping):
        system_id = _string(entry.get("systemId"))
        destination_id = _string(entry.get("destinationId"))
        if system_id and all_ids["systems"] and system_id not in all_ids["systems"]:
            problems.append(f"{label}.entry.systemId is not allowed by manifest requirements/scope: {system_id}")
        if destination_id and all_ids["destinations"] and destination_id not in all_ids["destinations"]:
            problems.append(
                f"{label}.entry.destinationId is not allowed by manifest requirements/scope: {destination_id}"
            )

    _validate_stage_graph(scenario, label, problems)

    for stage_index, stage in enumerate(_records(scenario.get("stages"), f"{label}.stages", problems)):
        stage_encounter_id = _string(stage.get("encounterId"))
        if stage_encounter_id and stage_encounter_id not in content_encounter_ids:
            problems.append(
                f"{label}.stages[{stage_index}].encounterId references encounter not declared by scenario "
                f"{stage_encounter_id}"
            )
        for receipt_id in _strings(stage.get("receiptIds"), f"{label}.stages[{stage_index}].receiptIds", problems):
            if receipt_id not in all_ids["receipts"]:
                problems.append(f"{label}.stages[{stage_index}].receiptIds references undeclared receipt {receipt_id}")


def _validate_conditions(
    conditions: Sequence[Mapping[str, Any]],
    *,
    objective_ids: set[str],
    label: str,
    problems: list[str],
) -> None:
    for index, condition in enumerate(conditions):
        referenced = _strings(condition.get("objectiveIds"), f"{label}[{index}].objectiveIds", problems)
        for objective_id in referenced:
            if objective_id not in objective_ids:
                problems.append(f"{label}[{index}].objectiveIds references missing objective {objective_id}")


def _validate_encounter_content(
    encounter: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    index: int,
    all_ids: dict[str, set[str]],
    problems: list[str],
) -> None:
    label = f"content.encounters[{index}]"
    if encounter.get("schema") != GAMEPLAY_PLUGIN_CONTENT_SCHEMA:
        problems.append(f"{label}.schema must be {GAMEPLAY_PLUGIN_CONTENT_SCHEMA}")
    if encounter.get("kind") != "encounter":
        problems.append(f"{label}.kind must be encounter")

    encounter_id = _string(encounter.get("id"))
    manifest_id = _string(record.get("id"))
    if encounter_id != manifest_id:
        problems.append(f"{label}.id must match manifest id {manifest_id}")

    template = _string(encounter.get("template"))
    if template and template not in all_ids["encounterTemplates"]:
        problems.append(f"{label}.template is not declared in manifest.requires.encounterTemplates: {template}")

    location = encounter.get("location")
    if isinstance(location, Mapping):
        system_id = _string(location.get("systemId"))
        destination_id = _string(location.get("destinationId"))
        if system_id and all_ids["systems"] and system_id not in all_ids["systems"]:
            problems.append(f"{label}.location.systemId is not allowed by manifest requirements/scope: {system_id}")
        if destination_id and all_ids["destinations"] and destination_id not in all_ids["destinations"]:
            problems.append(
                f"{label}.location.destinationId is not allowed by manifest requirements/scope: {destination_id}"
            )

    for participant_index, participant in enumerate(
        _records(encounter.get("participants"), f"{label}.participants", problems)
    ):
        archetype = _string(participant.get("actorArchetypeId"))
        if archetype and archetype not in all_ids["actorArchetypes"]:
            problems.append(
                f"{label}.participants[{participant_index}].actorArchetypeId is not declared in "
                f"manifest.requires.actorArchetypes: {archetype}"
            )
        count = participant.get("count")
        if not isinstance(count, int) or count < 1:
            problems.append(f"{label}.participants[{participant_index}].count must be a positive integer")

    objective_ids: set[str] = set()
    for objective_index, objective in enumerate(_records(encounter.get("objectives"), f"{label}.objectives", problems)):
        objective_id = _string(objective.get("id"))
        if not objective_id:
            problems.append(f"{label}.objectives[{objective_index}] is missing id")
        elif objective_id in objective_ids:
            problems.append(f"{label}.objectives contains duplicate objective id {objective_id}")
        objective_ids.add(objective_id)
        objective_type = _string(objective.get("type"))
        if objective_type and objective_type not in all_ids["objectiveTypes"]:
            problems.append(
                f"{label}.objectives[{objective_index}].type is not declared in manifest.requires.objectiveTypes: "
                f"{objective_type}"
            )

    _validate_conditions(
        _records(encounter.get("winConditions"), f"{label}.winConditions", problems),
        objective_ids=objective_ids,
        label=f"{label}.winConditions",
        problems=problems,
    )
    _validate_conditions(
        _records(encounter.get("lossConditions"), f"{label}.lossConditions", problems),
        objective_ids=objective_ids,
        label=f"{label}.lossConditions",
        problems=problems,
    )

    for outcome_index, outcome in enumerate(_records(encounter.get("outcomes"), f"{label}.outcomes", problems)):
        for receipt_id in _strings(outcome.get("receiptIds"), f"{label}.outcomes[{outcome_index}].receiptIds", problems):
            if receipt_id not in all_ids["receipts"]:
                problems.append(f"{label}.outcomes[{outcome_index}].receiptIds references undeclared receipt {receipt_id}")
        for consequence_type in _strings(
            outcome.get("consequenceTypeIds"),
            f"{label}.outcomes[{outcome_index}].consequenceTypeIds",
            problems,
        ):
            if consequence_type not in all_ids["consequenceTypes"]:
                problems.append(
                    f"{label}.outcomes[{outcome_index}].consequenceTypeIds references undeclared consequence type "
                    f"{consequence_type}"
                )


def validate_gameplay_plugin_content_package(
    manifest: Mapping[str, Any] | None,
    content_by_path: Mapping[str, Any] | None,
) -> list[str]:
    """Return validation problems for a gameplay plugin manifest plus content.

    The manifest declares what a plugin intends to add. This validator checks
    that the referenced scenario and encounter JSON files exist, match their
    manifest references, avoid executable fields, and form a coherent stage and
    encounter graph before any runtime importer is allowed to stage them.
    """

    problems: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["gameplay plugin manifest must be an object"]
    if not isinstance(content_by_path, Mapping):
        return ["gameplay plugin content package must be a path-to-object mapping"]

    problems.extend(validate_gameplay_plugin_manifest(manifest))

    scenario_records = _content_records(manifest, "scenarios", problems)
    encounter_records = _content_records(manifest, "encounters", problems)
    scenario_records_by_path = _record_index_by_path(
        scenario_records,
        label="manifest.content.scenarios",
        problems=problems,
    )
    encounter_records_by_path = _record_index_by_path(
        encounter_records,
        label="manifest.content.encounters",
        problems=problems,
    )
    declared_paths = set(scenario_records_by_path) | set(encounter_records_by_path)

    for path in sorted(declared_paths):
        if path not in content_by_path:
            problems.append(f"content package is missing declared file {path}")

    for path in sorted(content_by_path):
        if not str(path).startswith("content/"):
            problems.append(f"content package contains non-content path {path}")
        if path not in declared_paths and (
            str(path).startswith("content/scenarios/") or str(path).startswith("content/encounters/")
        ):
            problems.append(f"content package contains undeclared scenario/encounter file {path}")

    all_ids = _manifest_id_sets(manifest, problems)

    for index, record in enumerate(scenario_records):
        path = _string(record.get("path"))
        scenario = content_by_path.get(path)
        if scenario is None:
            continue
        if not isinstance(scenario, Mapping):
            problems.append(f"content.scenarios[{index}] file {path} must contain an object")
            continue
        for field_path in _forbidden_field_paths(scenario):
            problems.append(f"content.scenarios[{index}] contains forbidden executable/runtime field {field_path}")
        _validate_scenario_content(
            scenario,
            record=record,
            index=index,
            all_ids=all_ids,
            problems=problems,
        )

    for index, record in enumerate(encounter_records):
        path = _string(record.get("path"))
        encounter = content_by_path.get(path)
        if encounter is None:
            continue
        if not isinstance(encounter, Mapping):
            problems.append(f"content.encounters[{index}] file {path} must contain an object")
            continue
        for field_path in _forbidden_field_paths(encounter):
            problems.append(f"content.encounters[{index}] contains forbidden executable/runtime field {field_path}")
        _validate_encounter_content(
            encounter,
            record=record,
            index=index,
            all_ids=all_ids,
            problems=problems,
        )

    problems.extend(validate_gameplay_plugin_content_against_template_registry(manifest, content_by_path))

    return problems


def assert_valid_gameplay_plugin_content_package(
    manifest: Mapping[str, Any] | None,
    content_by_path: Mapping[str, Any] | None,
) -> None:
    """Raise ValueError when a plugin manifest/content package is unsafe or incoherent."""

    problems = validate_gameplay_plugin_content_package(manifest, content_by_path)
    if problems:
        raise ValueError("Invalid gameplay plugin content package:\n- " + "\n- ".join(problems))
