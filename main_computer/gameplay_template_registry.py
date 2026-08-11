from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


GAMEPLAY_TEMPLATE_REGISTRY_SCHEMA = "game.gameplayTemplateRegistry.v1"
GAMEPLAY_TEMPLATE_REGISTRY_VERSION = "gameplay-template-registry.v1"


@dataclass(frozen=True)
class GameplayEncounterTemplate:
    """One engine-owned gameplay template that plugins may instantiate.

    Templates define supported composition primitives. They do not activate
    gameplay by themselves; they describe the legal vocabulary that a hand-
    authored or AI-authored plugin can request.
    """

    id: str
    title: str
    gameplay_kind: str
    objective_types: tuple[str, ...]
    actor_archetypes: tuple[str, ...]
    consequence_types: tuple[str, ...]
    systems: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuiltInGameplayTemplateConsumer:
    """One built-in hand-authored gameplay implementation that consumes a template.

    This lets the validator vocabulary point back at real, already-playable game
    content instead of becoming an abstract plugin-only list. It is diagnostic
    metadata only; it does not activate or mutate runtime gameplay.
    """

    id: str
    title: str
    source: str
    template_id: str
    scenario_id: str
    encounter_id: str
    system_id: str
    destination_id: str
    objective_types: tuple[str, ...]
    actor_archetypes: tuple[str, ...]
    consequence_types: tuple[str, ...]


@dataclass(frozen=True)
class GameplayTemplateRegistry:
    """Canonical game-owned vocabulary for declarative gameplay plugins."""

    schema: str
    registry_version: str
    encounter_templates: tuple[GameplayEncounterTemplate, ...]
    built_in_consumers: tuple[BuiltInGameplayTemplateConsumer, ...]
    objective_types: tuple[str, ...]
    actor_archetypes: tuple[str, ...]
    consequence_types: tuple[str, ...]
    systems: tuple[str, ...]
    destinations: tuple[str, ...]

    @property
    def template_ids(self) -> tuple[str, ...]:
        return tuple(template.id for template in self.encounter_templates)

    def template(self, template_id: str) -> GameplayEncounterTemplate | None:
        for template in self.encounter_templates:
            if template.id == template_id:
                return template
        return None

    def built_in_consumer(self, consumer_id: str) -> BuiltInGameplayTemplateConsumer | None:
        for consumer in self.built_in_consumers:
            if consumer.id == consumer_id:
                return consumer
        return None


def default_gameplay_template_registry() -> GameplayTemplateRegistry:
    """Return the built-in plugin gameplay vocabulary.

    The initial registry intentionally mirrors already-authored gameplay verbs:
    Pax-style boarding defense, Vela-style cave escape, shuttle ambushes, and
    social investigation setup. It is a validator input, not a runtime activator.
    """

    return GameplayTemplateRegistry(
        schema=GAMEPLAY_TEMPLATE_REGISTRY_SCHEMA,
        registry_version=GAMEPLAY_TEMPLATE_REGISTRY_VERSION,
        encounter_templates=(
            GameplayEncounterTemplate(
                id="encounter-template.boarding-defense",
                title="Boarding Defense",
                gameplay_kind="shipboard-combat",
                objective_types=(
                    "objective-type.clear-hostiles",
                    "objective-type.protect-actor",
                    "objective-type.survive",
                ),
                actor_archetypes=(
                    "actor-archetype.boarder",
                    "actor-archetype.ship-security",
                    "actor-archetype.ally-witness",
                ),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.faction-standing",
                ),
                systems=("system.pax",),
            ),
            GameplayEncounterTemplate(
                id="encounter-template.cave-combat-run",
                title="Cave Combat Run",
                gameplay_kind="away-mission-combat",
                objective_types=(
                    "objective-type.escape-captivity",
                    "objective-type.recover-item",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-extraction",
                ),
                actor_archetypes=(
                    "actor-archetype.cave-guard",
                    "actor-archetype.vela-cave-guard",
                ),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.unlock-route",
                ),
                systems=("system.vela-gate",),
                destinations=("destination.vela-gate.subsurface-cavern",),
            ),
            GameplayEncounterTemplate(
                id="encounter-template.surface-transporter-extraction",
                title="Surface Transporter Extraction",
                gameplay_kind="extraction",
                objective_types=(
                    "objective-type.reach-extraction",
                    "objective-type.survive",
                ),
                actor_archetypes=(
                    "actor-archetype.cave-guard",
                    "actor-archetype.vela-cave-guard",
                ),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.unlock-route",
                ),
                systems=("system.vela-gate",),
                destinations=("destination.vela-gate.subsurface-cavern",),
            ),
            GameplayEncounterTemplate(
                id="encounter-template.shuttle-ambush",
                title="Shuttle Ambush",
                gameplay_kind="shuttle-combat",
                objective_types=(
                    "objective-type.clear-hostiles",
                    "objective-type.reach-destination",
                    "objective-type.survive",
                ),
                actor_archetypes=(
                    "actor-archetype.shuttle-raider",
                    "actor-archetype.ship-security",
                ),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.mark-system",
                ),
                systems=("system.solace-reach",),
                destinations=("destination.solace-reach.haven-orbit",),
            ),
            GameplayEncounterTemplate(
                id="encounter-template.social-investigation",
                title="Social Investigation",
                gameplay_kind="strategic-investigation",
                objective_types=(
                    "objective-type.gather-evidence",
                    "objective-type.deliver-briefing",
                    "objective-type.choose-outcome",
                ),
                actor_archetypes=(
                    "actor-archetype.strategic-official",
                    "actor-archetype.strategic-witness",
                    "actor-archetype.rescue-organizer",
                ),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.faction-standing",
                    "consequence-type.mark-system",
                ),
                systems=("system.vela-gate", "system.solace-reach"),
                destinations=(
                    "destination.vela-gate.velaris-orbit",
                    "destination.vela-gate.seraph-relay",
                    "destination.vela-gate.chiron-observatory",
                    "destination.solace-reach.haven-orbit",
                ),
            ),
        ),
        built_in_consumers=(
            BuiltInGameplayTemplateConsumer(
                id="built-in.vela-gate.subsurface-cave-escape",
                title="Vela Gate Subsurface Cave Escape",
                source="built-in",
                template_id="encounter-template.cave-combat-run",
                scenario_id="scenario.vela.underground-captivity-escape",
                encounter_id="encounter.vela.subsurface-captive-breakout",
                system_id="system.vela-gate",
                destination_id="destination.vela-gate.subsurface-cavern",
                objective_types=(
                    "objective-type.escape-captivity",
                    "objective-type.recover-item",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-extraction",
                ),
                actor_archetypes=("actor-archetype.vela-cave-guard",),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.unlock-route",
                ),
            ),
            BuiltInGameplayTemplateConsumer(
                id="built-in.solace-reach.opening-shuttle-ambush",
                title="Solace Reach Opening Shuttle Ambush",
                source="built-in",
                template_id="encounter-template.shuttle-ambush",
                scenario_id="scenario.solace-reach.opening-shuttle-ambush",
                encounter_id="encounter.solace-reach.opening-shuttle-ambush",
                system_id="system.solace-reach",
                destination_id="destination.solace-reach.haven-orbit",
                objective_types=(
                    "objective-type.survive",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-destination",
                ),
                actor_archetypes=("actor-archetype.shuttle-raider",),
                consequence_types=(
                    "consequence-type.record-receipt",
                    "consequence-type.mark-system",
                ),
            ),
        ),
        objective_types=(
            "objective-type.clear-hostiles",
            "objective-type.recover-item",
            "objective-type.reach-extraction",
            "objective-type.protect-actor",
            "objective-type.survive",
            "objective-type.gather-evidence",
            "objective-type.choose-outcome",
            "objective-type.reach-destination",
            "objective-type.deliver-briefing",
            "objective-type.escape-captivity",
        ),
        actor_archetypes=(
            "actor-archetype.boarder",
            "actor-archetype.ship-security",
            "actor-archetype.ally-witness",
            "actor-archetype.cave-guard",
            "actor-archetype.vela-cave-guard",
            "actor-archetype.shuttle-raider",
            "actor-archetype.strategic-official",
            "actor-archetype.strategic-witness",
            "actor-archetype.rescue-organizer",
        ),
        consequence_types=(
            "consequence-type.record-receipt",
            "consequence-type.faction-standing",
            "consequence-type.unlock-route",
            "consequence-type.mark-system",
        ),
        systems=(
            "system.pax",
            "system.vela-gate",
            "system.solace-reach",
            "system.solar-reach",
        ),
        destinations=(
            "destination.vela-gate.subsurface-cavern",
            "destination.vela-gate.velaris-orbit",
            "destination.vela-gate.seraph-relay",
            "destination.vela-gate.chiron-observatory",
            "destination.vela-gate.bastion-citadel",
            "destination.vela-gate.antares-corridor",
            "destination.solace-reach.haven-orbit",
            "destination.solace-reach.lyria-transfer",
            "destination.solace-reach.osprey-anchorage",
            "destination.solace-reach.talon-approach",
            "destination.solace-reach.bellara-corridor",
        ),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    return tuple(str(item or "").strip() for item in value if str(item or "").strip())


def _unknown_values(values: Sequence[str], allowed: Sequence[str]) -> list[str]:
    allowed_set = set(allowed)
    return sorted({value for value in values if value and value not in allowed_set})


def _template_allowed_values(template: GameplayEncounterTemplate | None, field: str) -> tuple[str, ...]:
    if template is None:
        return tuple()
    if field == "objective_types":
        return template.objective_types
    if field == "actor_archetypes":
        return template.actor_archetypes
    if field == "consequence_types":
        return template.consequence_types
    if field == "systems":
        return template.systems
    if field == "destinations":
        return template.destinations
    return tuple()


def validate_built_in_template_consumers(
    registry: GameplayTemplateRegistry | None = None,
) -> list[str]:
    """Return consistency problems in built-in template consumer metadata."""

    active_registry = registry or default_gameplay_template_registry()
    problems: list[str] = []
    consumer_ids: set[str] = set()

    for consumer in active_registry.built_in_consumers:
        if consumer.id in consumer_ids:
            problems.append(f"duplicate built-in gameplay template consumer id: {consumer.id}")
        consumer_ids.add(consumer.id)

        template = active_registry.template(consumer.template_id)
        if template is None:
            problems.append(
                f"built-in gameplay template consumer {consumer.id} references unknown template "
                f"{consumer.template_id}"
            )
            continue

        for objective_type in consumer.objective_types:
            if objective_type not in template.objective_types:
                problems.append(
                    f"built-in gameplay template consumer {consumer.id} objective {objective_type} "
                    f"is not allowed by template {consumer.template_id}"
                )
        for actor_archetype in consumer.actor_archetypes:
            if actor_archetype not in template.actor_archetypes:
                problems.append(
                    f"built-in gameplay template consumer {consumer.id} actor archetype {actor_archetype} "
                    f"is not allowed by template {consumer.template_id}"
                )
        for consequence_type in consumer.consequence_types:
            if consequence_type not in template.consequence_types:
                problems.append(
                    f"built-in gameplay template consumer {consumer.id} consequence {consequence_type} "
                    f"is not allowed by template {consumer.template_id}"
                )
        if template.systems and consumer.system_id not in template.systems:
            problems.append(
                f"built-in gameplay template consumer {consumer.id} system {consumer.system_id} "
                f"is not allowed by template {consumer.template_id}"
            )
        if template.destinations and consumer.destination_id not in template.destinations:
            problems.append(
                f"built-in gameplay template consumer {consumer.id} destination {consumer.destination_id} "
                f"is not allowed by template {consumer.template_id}"
            )

    return problems


def validate_gameplay_plugin_manifest_against_template_registry(
    manifest: Mapping[str, Any] | None,
    *,
    registry: GameplayTemplateRegistry | None = None,
) -> list[str]:
    """Return registry-reference problems in a plugin manifest.

    This is the canonical safety boundary that stops hand-authored or AI-authored
    plugins from inventing unknown gameplay templates and primitive ids.
    """

    if not isinstance(manifest, Mapping):
        return ["gameplay plugin manifest must be an object"]

    active_registry = registry or default_gameplay_template_registry()
    problems: list[str] = []

    requires = _mapping(manifest.get("requires"))
    scope = _mapping(manifest.get("scope"))

    requirement_checks = (
        (
            "manifest.requires.encounterTemplates",
            _strings(requires.get("encounterTemplates")),
            active_registry.template_ids,
        ),
        (
            "manifest.requires.actorArchetypes",
            _strings(requires.get("actorArchetypes")),
            active_registry.actor_archetypes,
        ),
        (
            "manifest.requires.objectiveTypes",
            _strings(requires.get("objectiveTypes")),
            active_registry.objective_types,
        ),
        (
            "manifest.requires.consequenceTypes",
            _strings(requires.get("consequenceTypes")),
            active_registry.consequence_types,
        ),
        (
            "manifest.requires.systems",
            _strings(requires.get("systems")),
            active_registry.systems,
        ),
        (
            "manifest.requires.destinations",
            _strings(requires.get("destinations")),
            active_registry.destinations,
        ),
        (
            "manifest.scope.allowedSystems",
            _strings(scope.get("allowedSystems")),
            active_registry.systems,
        ),
        (
            "manifest.scope.allowedDestinations",
            _strings(scope.get("allowedDestinations")),
            active_registry.destinations,
        ),
    )
    for label, values, allowed in requirement_checks:
        unknown = _unknown_values(values, allowed)
        if unknown:
            problems.append(f"{label} contains ids not in gameplay template registry {unknown}")

    content = _mapping(manifest.get("content"))
    for index, encounter in enumerate(_records(content.get("encounters"))):
        template_id = str(encounter.get("template") or "").strip()
        template = active_registry.template(template_id) if template_id else None
        if not template_id:
            continue
        if template is None:
            problems.append(
                f"manifest.content.encounters[{index}].template is not in gameplay template registry: {template_id}"
            )
            continue

        for actor_id in _strings(encounter.get("actorArchetypeIds")):
            if actor_id not in template.actor_archetypes:
                problems.append(
                    f"manifest.content.encounters[{index}].actorArchetypeIds contains {actor_id} "
                    f"which is not allowed by template {template_id}"
                )
        for objective_type in _strings(encounter.get("objectiveTypeIds")):
            if objective_type not in template.objective_types:
                problems.append(
                    f"manifest.content.encounters[{index}].objectiveTypeIds contains {objective_type} "
                    f"which is not allowed by template {template_id}"
                )

    return problems


def validate_gameplay_plugin_content_against_template_registry(
    manifest: Mapping[str, Any] | None,
    content_by_path: Mapping[str, Any] | None,
    *,
    registry: GameplayTemplateRegistry | None = None,
) -> list[str]:
    """Return registry/template compatibility problems in plugin content files."""

    if not isinstance(manifest, Mapping):
        return ["gameplay plugin manifest must be an object"]
    if not isinstance(content_by_path, Mapping):
        return ["gameplay plugin content package must be a path-to-object mapping"]

    active_registry = registry or default_gameplay_template_registry()
    problems: list[str] = []
    content = _mapping(manifest.get("content"))
    encounter_records = _records(content.get("encounters"))

    for index, record in enumerate(encounter_records):
        path = str(record.get("path") or "").strip()
        encounter = content_by_path.get(path)
        if not isinstance(encounter, Mapping):
            continue

        label = f"content.encounters[{index}]"
        template_id = str(encounter.get("template") or record.get("template") or "").strip()
        template = active_registry.template(template_id) if template_id else None
        if not template_id:
            problems.append(f"{label}.template must identify a registered encounter template")
            continue
        if template is None:
            problems.append(f"{label}.template is not in gameplay template registry: {template_id}")
            continue

        location = _mapping(encounter.get("location"))
        system_id = str(location.get("systemId") or "").strip()
        destination_id = str(location.get("destinationId") or "").strip()
        if template.systems and system_id and system_id not in template.systems:
            problems.append(
                f"{label}.location.systemId {system_id} is not allowed by template {template_id}"
            )
        if template.destinations and destination_id and destination_id not in template.destinations:
            problems.append(
                f"{label}.location.destinationId {destination_id} is not allowed by template {template_id}"
            )

        for participant_index, participant in enumerate(_records(encounter.get("participants"))):
            archetype = str(participant.get("actorArchetypeId") or "").strip()
            if archetype and archetype not in template.actor_archetypes:
                problems.append(
                    f"{label}.participants[{participant_index}].actorArchetypeId {archetype} "
                    f"is not allowed by template {template_id}"
                )

        for objective_index, objective in enumerate(_records(encounter.get("objectives"))):
            objective_type = str(objective.get("type") or "").strip()
            if objective_type and objective_type not in template.objective_types:
                problems.append(
                    f"{label}.objectives[{objective_index}].type {objective_type} "
                    f"is not allowed by template {template_id}"
                )

        for outcome_index, outcome in enumerate(_records(encounter.get("outcomes"))):
            for consequence_type in _strings(outcome.get("consequenceTypeIds")):
                if consequence_type not in template.consequence_types:
                    problems.append(
                        f"{label}.outcomes[{outcome_index}].consequenceTypeIds contains {consequence_type} "
                        f"which is not allowed by template {template_id}"
                    )

    return problems


def assert_gameplay_plugin_matches_template_registry(
    manifest: Mapping[str, Any] | None,
    content_by_path: Mapping[str, Any] | None = None,
    *,
    registry: GameplayTemplateRegistry | None = None,
) -> None:
    """Raise when a plugin invents unsupported gameplay vocabulary."""

    problems = validate_gameplay_plugin_manifest_against_template_registry(manifest, registry=registry)
    if content_by_path is not None:
        problems.extend(
            validate_gameplay_plugin_content_against_template_registry(
                manifest,
                content_by_path,
                registry=registry,
            )
        )
    if problems:
        raise ValueError("Invalid gameplay plugin template registry references:\n- " + "\n- ".join(problems))
