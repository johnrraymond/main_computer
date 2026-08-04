from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


STRATEGIC_AI_SCHEMA = "game.strategicAI.v1"
STRATEGIC_AI_DEFINITION_VERSION = "game.strategicAI.definition.v8"
STRATEGIC_AI_STATE_VERSION = "game.strategicAI.state.v8"
STRATEGIC_AI_DIRECTOR_PREDICATE = "predicate.campaign.opportunity-window-active"
STRATEGIC_AI_SCORE_METRICS = {
    "goalPriority",
    "evidenceSupport",
    "uncertainty",
    "memoryRelevance",
    "observationReliability",
    "captainCooperation",
    "captainEvidenceDiscipline",
    "captainAuthorityResistance",
    "commitmentTrust",
}


def _records(definition: Mapping[str, Any], key: str, problems: list[str]) -> list[Mapping[str, Any]]:
    value = definition.get(key)
    if not isinstance(value, list):
        problems.append(f"strategicAI.{key} must be a list")
        return []
    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            problems.append(f"strategicAI.{key}[{index}] must be an object")
            continue
        records.append(item)
    return records


def _state_records(
    state: Mapping[str, Any],
    key: str,
    problems: list[str],
) -> list[Mapping[str, Any]]:
    value = state.get(key)
    if not isinstance(value, list):
        problems.append(f"strategicAI.stateDefaults.{key} must be a list")
        return []
    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            problems.append(f"strategicAI.stateDefaults.{key}[{index}] must be an object")
            continue
        records.append(item)
    return records


def _index(
    records: Sequence[Mapping[str, Any]],
    *,
    collection: str,
    id_key: str,
    problems: list[str],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        raw = record.get(id_key)
        record_id = str(raw or "").strip()
        if not record_id:
            problems.append(f"{collection}[{index}] is missing {id_key}")
            continue
        if record_id in result:
            problems.append(f"duplicate id {record_id} in {collection}")
            continue
        result[record_id] = record
    return result


def _string_ids(value: Any, label: str, problems: list[str]) -> list[str]:
    if not isinstance(value, list):
        problems.append(f"{label} must be a list")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        item_id = str(item or "").strip()
        if not item_id:
            problems.append(f"{label}[{index}] must be a non-empty id")
            continue
        result.append(item_id)
    if len(result) != len(set(result)):
        problems.append(f"{label} contains duplicate ids")
    return result


def _require_refs(
    refs: Sequence[str],
    known: set[str],
    *,
    label: str,
    problems: list[str],
) -> None:
    for ref in refs:
        if ref not in known:
            problems.append(f"{label} references missing {ref}")


def _probability(value: Any, label: str, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{label} must be a number between 0 and 1")
        return
    if not 0 <= float(value) <= 1:
        problems.append(f"{label} must be between 0 and 1")


def _navigation_locations(space_navigation: Mapping[str, Any] | None) -> tuple[set[str], dict[str, set[str]]]:
    if not isinstance(space_navigation, Mapping):
        return set(), {}
    systems = space_navigation.get("systems")
    if not isinstance(systems, list):
        return set(), {}
    system_ids: set[str] = set()
    local_by_system: dict[str, set[str]] = {}
    for system in systems:
        if not isinstance(system, Mapping):
            continue
        system_id = str(system.get("id") or "").strip()
        if not system_id:
            continue
        system_ids.add(system_id)
        destinations = system.get("localDestinations")
        if not isinstance(destinations, list):
            destinations = []
        local_by_system[system_id] = {
            str(destination.get("id") or "").strip()
            for destination in destinations
            if isinstance(destination, Mapping)
            and str(destination.get("id") or "").strip()
        }
    return system_ids, local_by_system


def validate_strategic_ai_definition(
    definition: Mapping[str, Any] | None,
    *,
    space_navigation: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return strategic-AI cross-reference and state-invariant problems.

    JSON Schema owns closed record shapes and primitive constraints. This
    validator owns identity uniqueness, cross-record references, navigation
    placement, action authority, canonical-state coherence, proposal lineage,
    and the separation between world truth and private cognition.
    """

    if not isinstance(definition, Mapping):
        return ["project.metadata.strategicAI must be an object"]

    problems: list[str] = []
    if definition.get("schema") != STRATEGIC_AI_SCHEMA:
        problems.append(f"strategicAI.schema must be {STRATEGIC_AI_SCHEMA}")
    if definition.get("definitionVersion") != STRATEGIC_AI_DEFINITION_VERSION:
        problems.append(
            f"strategicAI.definitionVersion must be {STRATEGIC_AI_DEFINITION_VERSION}"
        )
    if definition.get("stateVersion") != STRATEGIC_AI_STATE_VERSION:
        problems.append(f"strategicAI.stateVersion must be {STRATEGIC_AI_STATE_VERSION}")

    sources = _records(definition, "sources", problems)
    channels = _records(definition, "observationChannels", problems)
    resources = _records(definition, "resources", problems)
    effects = _records(definition, "effectTypes", problems)
    actions = _records(definition, "actionTypes", problems)
    policy_profiles = _records(definition, "policyProfiles", problems)
    report_routes = _records(definition, "reportRoutes", problems)
    captain_model_profiles = _records(definition, "captainModelProfiles", problems)
    commitment_types = _records(definition, "commitmentTypes", problems)
    cooperation_profiles = _records(definition, "cooperationProfiles", problems)
    campaign_opportunities = _records(definition, "campaignOpportunities", problems)
    communication_claims = _records(definition, "communicationClaims", problems)
    communicative_intents = _records(definition, "communicativeIntents", problems)
    speech_templates = _records(definition, "speechTemplates", problems)
    offscreen_schedules = _records(definition, "offscreenSchedules", problems)
    actors = _records(definition, "actors", problems)
    facts = _records(definition, "facts", problems)
    evidence = _records(definition, "evidence", problems)
    goals = _records(definition, "goals", problems)
    checkpoints = _records(definition, "checkpoints", problems)

    state_value = definition.get("stateDefaults")
    if not isinstance(state_value, Mapping):
        problems.append("strategicAI.stateDefaults must be an object")
        state: Mapping[str, Any] = {}
    else:
        state = state_value

    observations = _state_records(state, "observations", problems)
    beliefs = _state_records(state, "beliefs", problems)
    memories = _state_records(state, "memories", problems)
    receipts = _state_records(state, "receipts", problems)
    actor_states = _state_records(state, "actorStates", problems)
    proposals = _state_records(state, "proposals", problems)
    outcomes = _state_records(state, "outcomes", problems)
    reports = _state_records(state, "reports", problems)
    captain_models = _state_records(state, "captainModels", problems)
    commitments = _state_records(state, "commitments", problems)
    cooperation_models = _state_records(state, "cooperationModels", problems)
    campaign_opportunity_states = _state_records(
        state, "campaignOpportunityStates", problems
    )
    director_receipts = _state_records(state, "directorReceipts", problems)
    offscreen_step_states = _state_records(state, "offscreenStepStates", problems)
    offscreen_simulation_receipts = _state_records(
        state, "offscreenSimulationReceipts", problems
    )

    canonical_value = state.get("canonicalState")
    if not isinstance(canonical_value, Mapping):
        problems.append("strategicAI.stateDefaults.canonicalState must be an object")
        canonical: Mapping[str, Any] = {}
    else:
        canonical = canonical_value
    fact_states = _state_records(canonical, "factStates", problems)
    resource_balances = _state_records(canonical, "resourceBalances", problems)
    events = _state_records(canonical, "events", problems)

    source_by_id = _index(sources, collection="sources", id_key="id", problems=problems)
    channel_by_id = _index(
        channels, collection="observationChannels", id_key="id", problems=problems
    )
    resource_by_id = _index(
        resources, collection="resources", id_key="id", problems=problems
    )
    effect_by_id = _index(
        effects, collection="effectTypes", id_key="id", problems=problems
    )
    action_by_id = _index(
        actions, collection="actionTypes", id_key="id", problems=problems
    )
    policy_profile_by_id = _index(
        policy_profiles, collection="policyProfiles", id_key="id", problems=problems
    )
    report_route_by_id = _index(
        report_routes, collection="reportRoutes", id_key="id", problems=problems
    )
    captain_model_profile_by_id = _index(
        captain_model_profiles,
        collection="captainModelProfiles",
        id_key="id",
        problems=problems,
    )
    commitment_type_by_id = _index(
        commitment_types,
        collection="commitmentTypes",
        id_key="id",
        problems=problems,
    )
    cooperation_profile_by_id = _index(
        cooperation_profiles,
        collection="cooperationProfiles",
        id_key="id",
        problems=problems,
    )
    campaign_opportunity_by_id = _index(
        campaign_opportunities,
        collection="campaignOpportunities",
        id_key="id",
        problems=problems,
    )
    communication_claim_by_id = _index(
        communication_claims,
        collection="communicationClaims",
        id_key="id",
        problems=problems,
    )
    communicative_intent_by_id = _index(
        communicative_intents,
        collection="communicativeIntents",
        id_key="id",
        problems=problems,
    )
    speech_template_by_id = _index(
        speech_templates,
        collection="speechTemplates",
        id_key="id",
        problems=problems,
    )
    offscreen_schedule_by_id = _index(
        offscreen_schedules,
        collection="offscreenSchedules",
        id_key="id",
        problems=problems,
    )
    actor_by_id = _index(actors, collection="actors", id_key="id", problems=problems)
    fact_by_id = _index(facts, collection="facts", id_key="id", problems=problems)
    evidence_by_id = _index(
        evidence, collection="evidence", id_key="id", problems=problems
    )
    goal_by_id = _index(goals, collection="goals", id_key="id", problems=problems)
    checkpoint_by_id = _index(
        checkpoints, collection="checkpoints", id_key="id", problems=problems
    )
    observation_by_id = _index(
        observations,
        collection="stateDefaults.observations",
        id_key="id",
        problems=problems,
    )
    belief_by_id = _index(
        beliefs,
        collection="stateDefaults.beliefs",
        id_key="id",
        problems=problems,
    )
    memory_by_id = _index(
        memories,
        collection="stateDefaults.memories",
        id_key="id",
        problems=problems,
    )
    receipt_by_id = _index(
        receipts,
        collection="stateDefaults.receipts",
        id_key="decisionId",
        problems=problems,
    )
    actor_state_by_id = _index(
        actor_states,
        collection="stateDefaults.actorStates",
        id_key="actorId",
        problems=problems,
    )
    proposal_by_id = _index(
        proposals,
        collection="stateDefaults.proposals",
        id_key="proposalId",
        problems=problems,
    )
    outcome_by_id = _index(
        outcomes,
        collection="stateDefaults.outcomes",
        id_key="outcomeId",
        problems=problems,
    )
    report_by_id = _index(
        reports,
        collection="stateDefaults.reports",
        id_key="reportId",
        problems=problems,
    )
    captain_model_by_id = _index(
        captain_models,
        collection="stateDefaults.captainModels",
        id_key="modelId",
        problems=problems,
    )
    commitment_by_id = _index(
        commitments,
        collection="stateDefaults.commitments",
        id_key="commitmentId",
        problems=problems,
    )
    cooperation_model_by_id = _index(
        cooperation_models,
        collection="stateDefaults.cooperationModels",
        id_key="modelId",
        problems=problems,
    )
    campaign_opportunity_state_by_id = _index(
        campaign_opportunity_states,
        collection="stateDefaults.campaignOpportunityStates",
        id_key="opportunityId",
        problems=problems,
    )
    director_receipt_by_id = _index(
        director_receipts,
        collection="stateDefaults.directorReceipts",
        id_key="directorReceiptId",
        problems=problems,
    )
    offscreen_step_state_by_id = _index(
        offscreen_step_states,
        collection="stateDefaults.offscreenStepStates",
        id_key="stepId",
        problems=problems,
    )
    offscreen_simulation_receipt_by_id = _index(
        offscreen_simulation_receipts,
        collection="stateDefaults.offscreenSimulationReceipts",
        id_key="simulationReceiptId",
        problems=problems,
    )
    event_by_id = _index(
        events,
        collection="stateDefaults.canonicalState.events",
        id_key="eventId",
        problems=problems,
    )
    fact_state_by_id = _index(
        fact_states,
        collection="stateDefaults.canonicalState.factStates",
        id_key="factId",
        problems=problems,
    )
    resource_balance_by_id = _index(
        resource_balances,
        collection="stateDefaults.canonicalState.resourceBalances",
        id_key="resourceId",
        problems=problems,
    )

    all_indexes = {
        "sources": source_by_id,
        "observationChannels": channel_by_id,
        "resources": resource_by_id,
        "effectTypes": effect_by_id,
        "actionTypes": action_by_id,
        "policyProfiles": policy_profile_by_id,
        "reportRoutes": report_route_by_id,
        "captainModelProfiles": captain_model_profile_by_id,
        "commitmentTypes": commitment_type_by_id,
        "cooperationProfiles": cooperation_profile_by_id,
        "campaignOpportunities": campaign_opportunity_by_id,
        "communicationClaims": communication_claim_by_id,
        "communicativeIntents": communicative_intent_by_id,
        "speechTemplates": speech_template_by_id,
        "offscreenSchedules": offscreen_schedule_by_id,
        "actors": actor_by_id,
        "facts": fact_by_id,
        "evidence": evidence_by_id,
        "goals": goal_by_id,
        "checkpoints": checkpoint_by_id,
        "observations": observation_by_id,
        "beliefs": belief_by_id,
        "memories": memory_by_id,
        "receipts": receipt_by_id,
        "proposals": proposal_by_id,
        "outcomes": outcome_by_id,
        "reports": report_by_id,
        "captainModels": captain_model_by_id,
        "commitments": commitment_by_id,
        "cooperationModels": cooperation_model_by_id,
        "directorReceipts": director_receipt_by_id,
        "offscreenSimulationReceipts": offscreen_simulation_receipt_by_id,
        "events": event_by_id,
    }
    global_ids: dict[str, str] = {}
    for collection, index in all_indexes.items():
        for record_id in index:
            previous = global_ids.get(record_id)
            if previous is not None:
                problems.append(
                    f"id {record_id} is reused across {previous} and {collection}"
                )
            else:
                global_ids[record_id] = collection

    source_ids = set(source_by_id)
    channel_ids = set(channel_by_id)
    resource_ids = set(resource_by_id)
    effect_ids = set(effect_by_id)
    action_ids = set(action_by_id)
    policy_profile_ids = set(policy_profile_by_id)
    report_route_ids = set(report_route_by_id)
    captain_model_profile_ids = set(captain_model_profile_by_id)
    commitment_type_ids = set(commitment_type_by_id)
    cooperation_profile_ids = set(cooperation_profile_by_id)
    campaign_opportunity_ids = set(campaign_opportunity_by_id)
    communication_claim_ids = set(communication_claim_by_id)
    communicative_intent_ids = set(communicative_intent_by_id)
    speech_template_ids = set(speech_template_by_id)
    offscreen_schedule_ids = set(offscreen_schedule_by_id)
    offscreen_step_state_ids = set(offscreen_step_state_by_id)
    offscreen_simulation_receipt_ids = set(offscreen_simulation_receipt_by_id)
    actor_ids = set(actor_by_id)
    fact_ids = set(fact_by_id)
    evidence_ids = set(evidence_by_id)
    goal_ids = set(goal_by_id)
    checkpoint_ids = set(checkpoint_by_id)
    observation_ids = set(observation_by_id)
    belief_ids = set(belief_by_id)
    memory_ids = set(memory_by_id)
    receipt_ids = set(receipt_by_id)
    proposal_ids = set(proposal_by_id)
    outcome_ids = set(outcome_by_id)
    report_ids = set(report_by_id)
    captain_model_ids = set(captain_model_by_id)
    commitment_ids = set(commitment_by_id)
    cooperation_model_ids = set(cooperation_model_by_id)
    director_receipt_ids = set(director_receipt_by_id)
    event_ids = set(event_by_id)

    system_ids, local_by_system = _navigation_locations(space_navigation)
    all_local_destinations = set().union(*local_by_system.values()) if local_by_system else set()

    simulation_budget = definition.get("offscreenSimulationBudget")
    if (
        isinstance(simulation_budget, bool)
        or not isinstance(simulation_budget, int)
        or simulation_budget < 1
    ):
        problems.append(
            "strategicAI.offscreenSimulationBudget must be a positive integer"
        )

    authored_step_ids: set[str] = set()
    authored_step_by_id: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for schedule_id, schedule in offscreen_schedule_by_id.items():
        schedule_system_id = str(schedule.get("systemId") or "").strip()
        if space_navigation is not None and schedule_system_id not in system_ids:
            problems.append(
                f"offscreenSchedule {schedule_id} references missing system "
                f"{schedule_system_id}"
            )
        steps = schedule.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(
                f"offscreenSchedule {schedule_id}.steps must be a non-empty list"
            )
            continue
        local_step_ids: set[str] = set()
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                problems.append(
                    f"offscreenSchedule {schedule_id}.steps[{index}] must be an object"
                )
                continue
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                problems.append(
                    f"offscreenSchedule {schedule_id}.steps[{index}] has no id"
                )
                continue
            if step_id in local_step_ids or step_id in authored_step_ids:
                problems.append(f"duplicate off-screen step id {step_id}")
            local_step_ids.add(step_id)
            authored_step_ids.add(step_id)
            authored_step_by_id[step_id] = (schedule_id, step)
            kind = str(step.get("kind") or "").strip()
            cost = step.get("cost")
            if isinstance(cost, bool) or not isinstance(cost, int) or cost < 1:
                problems.append(
                    f"offscreen step {step_id}.cost must be a positive integer"
                )
            due_at = step.get("dueAt")
            if isinstance(due_at, bool) or not isinstance(due_at, int) or due_at < 0:
                problems.append(
                    f"offscreen step {step_id}.dueAt must be a non-negative integer"
                )

            if kind == "actor-turn":
                actor_id = str(step.get("actorId") or "").strip()
                actor = actor_by_id.get(actor_id)
                if actor is None:
                    problems.append(
                        f"offscreen step {step_id} references missing actor {actor_id}"
                    )
                    continue
                if str(actor.get("systemId") or "").strip() != schedule_system_id:
                    problems.append(
                        f"offscreen step {step_id} actor is outside schedule system"
                    )
                allowed_actions = _string_ids(
                    step.get("allowedActionTypeIds"),
                    f"offscreen step {step_id}.allowedActionTypeIds",
                    problems,
                )
                _require_refs(
                    allowed_actions,
                    action_ids,
                    label=f"offscreen step {step_id}.allowedActionTypeIds",
                    problems=problems,
                )
                actor_actions = set(
                    _string_ids(
                        actor.get("candidateActionTypeIds"),
                        f"actor {actor_id}.candidateActionTypeIds",
                        problems,
                    )
                )
                actor_authorities = set(
                    _string_ids(
                        actor.get("authorityIds"),
                        f"actor {actor_id}.authorityIds",
                        problems,
                    )
                )
                for action_id in allowed_actions:
                    if action_id not in actor_actions:
                        problems.append(
                            f"offscreen step {step_id} does not authorize actor "
                            f"candidate action {action_id}"
                        )
                    action = action_by_id.get(action_id)
                    if action is None:
                        continue
                    protected_effects = [
                        effect_by_id.get(str(effect_id or "").strip())
                        for effect_id in (
                            action.get("effectTypeIds")
                            if isinstance(action.get("effectTypeIds"), list)
                            else []
                        )
                        if effect_by_id.get(
                            str(effect_id or "").strip(), {}
                        ).get("protected") is True
                    ]
                    if protected_effects:
                        deadline_at = step.get("deadlineAt")
                        if (
                            isinstance(deadline_at, bool)
                            or not isinstance(deadline_at, int)
                            or deadline_at < 0
                        ):
                            problems.append(
                                f"protected offscreen step {step_id} requires "
                                "an explicit deadline"
                            )
                        required_authorities = set(
                            _string_ids(
                                action.get("requiredAuthorityIds"),
                                f"actionType {action_id}.requiredAuthorityIds",
                                problems,
                            )
                        )
                        for effect in protected_effects:
                            if effect is None:
                                continue
                            required_authorities.update(
                                _string_ids(
                                    effect.get("requiredAuthorityIds"),
                                    f"effectType {effect.get('id')}.requiredAuthorityIds",
                                    problems,
                                )
                            )
                        missing = sorted(required_authorities - actor_authorities)
                        if missing:
                            problems.append(
                                f"protected offscreen step {step_id} lacks "
                                f"authorities {missing}"
                            )

            elif kind == "report":
                route_id = str(step.get("routeId") or "").strip()
                sender_id = str(step.get("senderActorId") or "").strip()
                recipient_id = str(step.get("recipientActorId") or "").strip()
                source_observation_id = str(
                    step.get("sourceObservationId") or ""
                ).strip()
                route = report_route_by_id.get(route_id)
                if route is None:
                    problems.append(
                        f"offscreen step {step_id} references missing report route "
                        f"{route_id}"
                    )
                sender = actor_by_id.get(sender_id)
                if sender is None:
                    problems.append(
                        f"offscreen step {step_id} references missing sender {sender_id}"
                    )
                elif str(sender.get("systemId") or "").strip() != schedule_system_id:
                    problems.append(
                        f"offscreen step {step_id} report sender is outside "
                        "schedule system"
                    )
                if recipient_id not in actor_ids:
                    problems.append(
                        f"offscreen step {step_id} references missing recipient "
                        f"{recipient_id}"
                    )
                if source_observation_id not in observation_ids:
                    problems.append(
                        f"offscreen step {step_id} references missing source "
                        f"observation {source_observation_id}"
                    )
                if route is not None:
                    senders = set(
                        _string_ids(
                            route.get("senderActorIds"),
                            f"reportRoute {route_id}.senderActorIds",
                            problems,
                        )
                    )
                    recipients = set(
                        _string_ids(
                            route.get("recipientActorIds"),
                            f"reportRoute {route_id}.recipientActorIds",
                            problems,
                        )
                    )
                    if sender_id not in senders:
                        problems.append(
                            f"offscreen step {step_id} sender is not authorized "
                            f"by route {route_id}"
                        )
                    if recipient_id not in recipients:
                        problems.append(
                            f"offscreen step {step_id} recipient is not authorized "
                            f"by route {route_id}"
                        )

            elif kind == "commitment":
                type_id = str(step.get("commitmentTypeId") or "").strip()
                promisor_id = str(step.get("promisorActorId") or "").strip()
                promisee_id = str(step.get("promiseeActorId") or "").strip()
                if type_id not in commitment_type_ids:
                    problems.append(
                        f"offscreen step {step_id} references missing commitment "
                        f"type {type_id}"
                    )
                promisor = actor_by_id.get(promisor_id)
                if promisor is None:
                    problems.append(
                        f"offscreen step {step_id} references missing promisor "
                        f"{promisor_id}"
                    )
                elif str(promisor.get("systemId") or "").strip() != schedule_system_id:
                    problems.append(
                        f"offscreen step {step_id} promisor is outside schedule system"
                    )
                if promisee_id not in actor_ids:
                    problems.append(
                        f"offscreen step {step_id} references missing promisee "
                        f"{promisee_id}"
                    )

            elif kind == "director":
                operation = str(step.get("operation") or "").strip()
                if operation == "activate-route":
                    route_system_id = str(step.get("routeSystemId") or "").strip()
                    if route_system_id != schedule_system_id:
                        problems.append(
                            f"offscreen step {step_id} activates a different system"
                        )
                elif operation == "deactivate-opportunity":
                    opportunity_id = str(step.get("opportunityId") or "").strip()
                    if opportunity_id not in campaign_opportunity_ids:
                        problems.append(
                            f"offscreen step {step_id} references missing "
                            f"opportunity {opportunity_id}"
                        )
                elif operation != "expire-opportunities":
                    problems.append(
                        f"offscreen step {step_id} has unsupported director "
                        f"operation {operation}"
                    )

            elif kind == "communication":
                intent_id = str(step.get("intentId") or "").strip()
                speaker_id = str(step.get("speakerActorId") or "").strip()
                audiences = _string_ids(
                    step.get("audienceActorIds"),
                    f"offscreen step {step_id}.audienceActorIds",
                    problems,
                )
                if intent_id not in communicative_intent_ids:
                    problems.append(
                        f"offscreen step {step_id} references missing intent "
                        f"{intent_id}"
                    )
                speaker = actor_by_id.get(speaker_id)
                if speaker is None:
                    problems.append(
                        f"offscreen step {step_id} references missing speaker "
                        f"{speaker_id}"
                    )
                elif str(speaker.get("systemId") or "").strip() != schedule_system_id:
                    problems.append(
                        f"offscreen step {step_id} speaker is outside schedule system"
                    )
                _require_refs(
                    audiences,
                    actor_ids,
                    label=f"offscreen step {step_id}.audienceActorIds",
                    problems=problems,
                )
            else:
                problems.append(
                    f"offscreen step {step_id} has unsupported kind {kind}"
                )

    for step_id, (schedule_id, step) in authored_step_by_id.items():
        if str(step.get("kind") or "").strip() != "communication":
            continue
        commitment_step_value = step.get("commitmentStepId")
        if commitment_step_value is None:
            continue
        commitment_step_id = str(commitment_step_value or "").strip()
        linked = authored_step_by_id.get(commitment_step_id)
        if linked is None:
            problems.append(
                f"offscreen step {step_id} references missing commitment step "
                f"{commitment_step_id}"
            )
        elif linked[0] != schedule_id:
            problems.append(
                f"offscreen step {step_id} references commitment step in "
                "another schedule"
            )
        elif str(linked[1].get("kind") or "").strip() != "commitment":
            problems.append(
                f"offscreen step {step_id} commitmentStepId is not a "
                "commitment step"
            )

    if set(offscreen_step_state_by_id) != authored_step_ids:
        missing = sorted(authored_step_ids - set(offscreen_step_state_by_id))
        extra = sorted(set(offscreen_step_state_by_id) - authored_step_ids)
        if missing:
            problems.append(
                f"stateDefaults.offscreenStepStates missing steps {missing}"
            )
        if extra:
            problems.append(
                f"stateDefaults.offscreenStepStates references unknown steps {extra}"
            )

    for step_id, step_state in offscreen_step_state_by_id.items():
        schedule_id = str(step_state.get("scheduleId") or "").strip()
        authored = authored_step_by_id.get(step_id)
        if schedule_id not in offscreen_schedule_ids:
            problems.append(
                f"offscreenStepState {step_id} references missing schedule "
                f"{schedule_id}"
            )
        if authored is not None and authored[0] != schedule_id:
            problems.append(
                f"offscreenStepState {step_id}.scheduleId does not match "
                "authored schedule"
            )
        expected_ready = None
        if authored is not None:
            authored_step = authored[1]
            expected_ready = int(authored_step.get("dueAt", 0))
            if str(authored_step.get("kind") or "").strip() == "report":
                route = report_route_by_id.get(
                    str(authored_step.get("routeId") or "").strip()
                )
                if route is not None:
                    expected_ready += int(route.get("latency", 0))
            deadline = authored_step.get("deadlineAt")
            if (
                str(authored_step.get("kind") or "").strip() == "actor-turn"
                and isinstance(deadline, int)
                and not isinstance(deadline, bool)
            ):
                expected_ready = max(expected_ready, deadline)
        if expected_ready is not None and step_state.get("readyAt") != expected_ready:
            problems.append(
                f"offscreenStepState {step_id}.readyAt does not match authored timing"
            )

    for receipt_id, receipt in offscreen_simulation_receipt_by_id.items():
        processed = _string_ids(
            receipt.get("processedStepIds"),
            f"offscreenSimulationReceipt {receipt_id}.processedStepIds",
            problems,
        )
        deferred = _string_ids(
            receipt.get("deferredStepIds"),
            f"offscreenSimulationReceipt {receipt_id}.deferredStepIds",
            problems,
        )
        skipped = _string_ids(
            receipt.get("skippedScheduleIds"),
            f"offscreenSimulationReceipt {receipt_id}.skippedScheduleIds",
            problems,
        )
        _require_refs(
            processed,
            authored_step_ids,
            label=f"offscreenSimulationReceipt {receipt_id}.processedStepIds",
            problems=problems,
        )
        _require_refs(
            deferred,
            authored_step_ids,
            label=f"offscreenSimulationReceipt {receipt_id}.deferredStepIds",
            problems=problems,
        )
        _require_refs(
            skipped,
            offscreen_schedule_ids,
            label=f"offscreenSimulationReceipt {receipt_id}.skippedScheduleIds",
            problems=problems,
        )
        budget = receipt.get("budget")
        consumed = receipt.get("consumedBudget")
        if (
            isinstance(budget, bool)
            or not isinstance(budget, int)
            or budget < 0
            or (
                isinstance(simulation_budget, int)
                and not isinstance(simulation_budget, bool)
                and budget > simulation_budget
            )
        ):
            problems.append(
                f"offscreenSimulationReceipt {receipt_id}.budget exceeds "
                "the declared budget"
            )
        if (
            isinstance(consumed, bool)
            or not isinstance(consumed, int)
            or consumed < 0
            or (isinstance(budget, int) and consumed > budget)
        ):
            problems.append(
                f"offscreenSimulationReceipt {receipt_id}.consumedBudget is invalid"
            )
        from_time = receipt.get("fromTime")
        to_time = receipt.get("toTime")
        if (
            not isinstance(from_time, int)
            or isinstance(from_time, bool)
            or not isinstance(to_time, int)
            or isinstance(to_time, bool)
            or to_time < from_time
        ):
            problems.append(
                f"offscreenSimulationReceipt {receipt_id} has invalid time range"
            )
        active_system_id = str(receipt.get("activeSystemId") or "").strip()
        if space_navigation is not None and active_system_id not in system_ids:
            problems.append(
                f"offscreenSimulationReceipt {receipt_id} references missing "
                f"active system {active_system_id}"
            )

    simulation_time = state.get("offscreenSimulationTime")
    if (
        isinstance(simulation_time, bool)
        or not isinstance(simulation_time, int)
        or simulation_time < 0
    ):
        problems.append(
            "strategicAI.stateDefaults.offscreenSimulationTime must be "
            "a non-negative integer"
        )

    for source_id, source in source_by_id.items():
        if not str(source.get("label") or "").strip():
            problems.append(f"source {source_id} has no label")

    for channel_id, channel in channel_by_id.items():
        _probability(
            channel.get("defaultReliability"),
            f"observationChannel {channel_id}.defaultReliability",
            problems,
        )

    for resource_id, resource in resource_by_id.items():
        capacity = resource.get("capacity")
        if isinstance(capacity, bool) or not isinstance(capacity, (int, float)):
            problems.append(f"resource {resource_id}.capacity must be a non-negative number")
        elif float(capacity) < 0:
            problems.append(f"resource {resource_id}.capacity must be non-negative")

    for effect_id, effect in effect_by_id.items():
        required = _string_ids(
            effect.get("requiredAuthorityIds"),
            f"effectType {effect_id}.requiredAuthorityIds",
            problems,
        )
        if effect.get("protected") is True and not required:
            problems.append(f"protected effectType {effect_id} has no authority gate")
        operation = str(effect.get("operation") or "").strip()
        if operation == "set-fact":
            target_fact_id = str(effect.get("targetFactId") or "").strip()
            if target_fact_id not in fact_ids:
                problems.append(
                    f"effectType {effect_id} references missing target fact {target_fact_id}"
                )
        elif operation != "append-event":
            problems.append(f"effectType {effect_id} has unsupported operation {operation}")

    action_authorities: dict[str, set[str]] = {}
    action_effects_by_id: dict[str, list[str]] = {}
    for action_id, action in action_by_id.items():
        required_authorities = set(
            _string_ids(
                action.get("requiredAuthorityIds"),
                f"actionType {action_id}.requiredAuthorityIds",
                problems,
            )
        )
        action_authorities[action_id] = required_authorities
        action_effects = _string_ids(
            action.get("effectTypeIds"),
            f"actionType {action_id}.effectTypeIds",
            problems,
        )
        action_effects_by_id[action_id] = action_effects
        if not action_effects:
            problems.append(f"actionType {action_id} declares no effects")
        _require_refs(
            action_effects,
            effect_ids,
            label=f"actionType {action_id}.effectTypeIds",
            problems=problems,
        )

        locations = _string_ids(
            action.get("allowedLocationIds"),
            f"actionType {action_id}.allowedLocationIds",
            problems,
        )
        if space_navigation is not None:
            _require_refs(
                locations,
                all_local_destinations,
                label=f"actionType {action_id}.allowedLocationIds",
                problems=problems,
            )

        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            problems.append(f"actionType {action_id}.preconditions must be a list")
        else:
            for index, precondition in enumerate(preconditions):
                if not isinstance(precondition, Mapping):
                    problems.append(
                        f"actionType {action_id}.preconditions[{index}] must be an object"
                    )
                    continue
                fact_id = str(precondition.get("factId") or "").strip()
                if fact_id not in fact_ids:
                    problems.append(
                        f"actionType {action_id}.preconditions[{index}] "
                        f"references missing fact {fact_id}"
                    )

        costs = action.get("resourceCosts")
        seen_costs: set[str] = set()
        if not isinstance(costs, list):
            problems.append(f"actionType {action_id}.resourceCosts must be a list")
        else:
            for index, cost in enumerate(costs):
                if not isinstance(cost, Mapping):
                    problems.append(
                        f"actionType {action_id}.resourceCosts[{index}] must be an object"
                    )
                    continue
                resource_id = str(cost.get("resourceId") or "").strip()
                if resource_id in seen_costs:
                    problems.append(
                        f"actionType {action_id}.resourceCosts contains duplicate {resource_id}"
                    )
                seen_costs.add(resource_id)
                if resource_id not in resource_ids:
                    problems.append(
                        f"actionType {action_id}.resourceCosts[{index}] "
                        f"references missing resource {resource_id}"
                    )
                amount = cost.get("amount")
                if isinstance(amount, bool) or not isinstance(amount, (int, float)) or float(amount) <= 0:
                    problems.append(
                        f"actionType {action_id}.resourceCosts[{index}].amount "
                        "must be a positive number"
                    )

        templates = action.get("resultObservationTemplates")
        if not isinstance(templates, list):
            problems.append(
                f"actionType {action_id}.resultObservationTemplates must be a list"
            )
        else:
            for index, template in enumerate(templates):
                if not isinstance(template, Mapping):
                    problems.append(
                        f"actionType {action_id}.resultObservationTemplates[{index}] "
                        "must be an object"
                    )
                    continue
                observer_id = str(template.get("observerId") or "").strip()
                channel_id = str(template.get("channelId") or "").strip()
                source_id = str(template.get("sourceId") or "").strip()
                if observer_id not in actor_ids:
                    problems.append(
                        f"actionType {action_id}.resultObservationTemplates[{index}] "
                        f"references missing observer {observer_id}"
                    )
                if channel_id not in channel_ids:
                    problems.append(
                        f"actionType {action_id}.resultObservationTemplates[{index}] "
                        f"references missing channel {channel_id}"
                    )
                if source_id not in source_ids:
                    problems.append(
                        f"actionType {action_id}.resultObservationTemplates[{index}] "
                        f"references missing source {source_id}"
                    )
                _probability(
                    template.get("reliability"),
                    f"actionType {action_id}.resultObservationTemplates[{index}].reliability",
                    problems,
                )

    policy_actions_by_id: dict[str, set[str]] = {}
    for profile_id, profile in policy_profile_by_id.items():
        action_policies = profile.get("actionPolicies")
        if not isinstance(action_policies, Mapping) or not action_policies:
            problems.append(
                f"policyProfile {profile_id}.actionPolicies must be a non-empty object"
            )
            policy_actions_by_id[profile_id] = set()
            continue
        profile_actions: set[str] = set()
        for action_id, policy in action_policies.items():
            action_type_id = str(action_id or "").strip()
            profile_actions.add(action_type_id)
            if action_type_id not in action_ids:
                problems.append(
                    f"policyProfile {profile_id}.actionPolicies references missing "
                    f"{action_type_id}"
                )
            if not isinstance(policy, Mapping):
                problems.append(
                    f"policyProfile {profile_id}.actionPolicies.{action_type_id} "
                    "must be an object"
                )
                continue
            base_score = policy.get("baseScore")
            if (
                isinstance(base_score, bool)
                or not isinstance(base_score, (int, float))
                or not -10 <= float(base_score) <= 10
            ):
                problems.append(
                    f"policyProfile {profile_id}.actionPolicies.{action_type_id}."
                    "baseScore must be between -10 and 10"
                )
            weights = policy.get("weights")
            if not isinstance(weights, Mapping):
                problems.append(
                    f"policyProfile {profile_id}.actionPolicies.{action_type_id}."
                    "weights must be an object"
                )
                continue
            unknown_metrics = sorted(set(weights) - STRATEGIC_AI_SCORE_METRICS)
            if unknown_metrics:
                problems.append(
                    f"policyProfile {profile_id}.actionPolicies.{action_type_id} "
                    f"uses unknown metrics {unknown_metrics}"
                )
            for metric, weight in weights.items():
                if (
                    isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not -10 <= float(weight) <= 10
                ):
                    problems.append(
                        f"policyProfile {profile_id}.actionPolicies.{action_type_id}."
                        f"weights.{metric} must be between -10 and 10"
                    )
        policy_actions_by_id[profile_id] = profile_actions

    captain_profile_by_actor: dict[str, str] = {}
    for route_id, route in report_route_by_id.items():
        sender_ids = _string_ids(
            route.get("senderActorIds"),
            f"reportRoute {route_id}.senderActorIds",
            problems,
        )
        recipient_ids = _string_ids(
            route.get("recipientActorIds"),
            f"reportRoute {route_id}.recipientActorIds",
            problems,
        )
        _require_refs(
            sender_ids,
            actor_ids,
            label=f"reportRoute {route_id}.senderActorIds",
            problems=problems,
        )
        _require_refs(
            recipient_ids,
            actor_ids,
            label=f"reportRoute {route_id}.recipientActorIds",
            problems=problems,
        )
        channel_id = str(route.get("channelId") or "").strip()
        if channel_id not in channel_ids:
            problems.append(
                f"reportRoute {route_id} references missing channel {channel_id}"
            )
        for recipient_id in recipient_ids:
            actor = actor_by_id.get(recipient_id)
            if actor is None:
                continue
            authorized_channels = set(
                _string_ids(
                    actor.get("observationChannelIds"),
                    f"actor {recipient_id}.observationChannelIds",
                    problems,
                )
            )
            if channel_id and channel_id not in authorized_channels:
                problems.append(
                    f"reportRoute {route_id} delivers unauthorized channel "
                    f"{channel_id} to {recipient_id}"
                )
        _probability(
            route.get("baseReliability"),
            f"reportRoute {route_id}.baseReliability",
            problems,
        )
        _probability(
            route.get("maxDistortion"),
            f"reportRoute {route_id}.maxDistortion",
            problems,
        )
        mode = str(route.get("mode") or "").strip()
        visibilities = _string_ids(
            route.get("allowedVisibilities"),
            f"reportRoute {route_id}.allowedVisibilities",
            problems,
        )
        if mode == "public" and set(visibilities) != {"public"}:
            problems.append(
                f"public reportRoute {route_id} must carry only public observations"
            )
        if mode == "public" and route.get("maxDistortion") != 0:
            problems.append(
                f"public reportRoute {route_id} cannot permit distortion"
            )
        if mode == "rumor" and route.get("maxDistortion") == 0:
            problems.append(
                f"rumor reportRoute {route_id} must permit bounded distortion"
            )

    for profile_id, profile in captain_model_profile_by_id.items():
        actor_id = str(profile.get("actorId") or "").strip()
        subject_actor_id = str(profile.get("subjectActorId") or "").strip()
        if actor_id not in actor_ids:
            problems.append(
                f"captainModelProfile {profile_id} references missing actor {actor_id}"
            )
        if not subject_actor_id:
            problems.append(
                f"captainModelProfile {profile_id} has no subject actor"
            )
        previous_profile = captain_profile_by_actor.get(actor_id)
        if previous_profile is not None:
            problems.append(
                f"actor {actor_id} has multiple captain model profiles: "
                f"{previous_profile} and {profile_id}"
            )
        elif actor_id:
            captain_profile_by_actor[actor_id] = profile_id
        tendencies = profile.get("initialTendencies")
        if not isinstance(tendencies, Mapping) or not tendencies:
            problems.append(
                f"captainModelProfile {profile_id}.initialTendencies "
                "must be a non-empty object"
            )
            tendency_ids: set[str] = set()
        else:
            tendency_ids = {str(key or "").strip() for key in tendencies}
            for tendency_id, value in tendencies.items():
                _probability(
                    value,
                    f"captainModelProfile {profile_id}.initialTendencies.{tendency_id}",
                    problems,
                )
        signals = profile.get("signals")
        if not isinstance(signals, list) or not signals:
            problems.append(
                f"captainModelProfile {profile_id}.signals must be a non-empty list"
            )
        else:
            for index, signal in enumerate(signals):
                if not isinstance(signal, Mapping):
                    problems.append(
                        f"captainModelProfile {profile_id}.signals[{index}] "
                        "must be an object"
                    )
                    continue
                predicate = str(signal.get("predicate") or "").strip()
                if not predicate:
                    problems.append(
                        f"captainModelProfile {profile_id}.signals[{index}] "
                        "has no predicate"
                    )
                deltas = signal.get("tendencyDeltas")
                if not isinstance(deltas, Mapping) or not deltas:
                    problems.append(
                        f"captainModelProfile {profile_id}.signals[{index}]."
                        "tendencyDeltas must be a non-empty object"
                    )
                    continue
                unknown_tendencies = sorted(set(deltas) - tendency_ids)
                if unknown_tendencies:
                    problems.append(
                        f"captainModelProfile {profile_id}.signals[{index}] "
                        f"references unknown tendencies {unknown_tendencies}"
                    )
                for tendency_id, delta in deltas.items():
                    if (
                        isinstance(delta, bool)
                        or not isinstance(delta, (int, float))
                        or not -1 <= float(delta) <= 1
                    ):
                        problems.append(
                            f"captainModelProfile {profile_id}.signals[{index}]."
                            f"tendencyDeltas.{tendency_id} must be between -1 and 1"
                        )

    for commitment_type_id, commitment_type in commitment_type_by_id.items():
        promisor_ids = _string_ids(
            commitment_type.get("promisorActorIds"),
            f"commitmentType {commitment_type_id}.promisorActorIds",
            problems,
        )
        promisee_ids = _string_ids(
            commitment_type.get("promiseeActorIds"),
            f"commitmentType {commitment_type_id}.promiseeActorIds",
            problems,
        )
        required_authority_ids = _string_ids(
            commitment_type.get("requiredAuthorityIds"),
            f"commitmentType {commitment_type_id}.requiredAuthorityIds",
            problems,
        )
        _require_refs(
            promisor_ids,
            actor_ids,
            label=f"commitmentType {commitment_type_id}.promisorActorIds",
            problems=problems,
        )
        _require_refs(
            promisee_ids,
            actor_ids,
            label=f"commitmentType {commitment_type_id}.promiseeActorIds",
            problems=problems,
        )
        if set(promisor_ids) & set(promisee_ids):
            problems.append(
                f"commitmentType {commitment_type_id} cannot use the same actor "
                "as promisor and promisee"
            )
        resource_id = str(commitment_type.get("resourceId") or "").strip()
        if resource_id not in resource_ids:
            problems.append(
                f"commitmentType {commitment_type_id} references missing resource "
                f"{resource_id}"
            )
        resource_amount = commitment_type.get("resourceAmount")
        if (
            isinstance(resource_amount, bool)
            or not isinstance(resource_amount, (int, float))
            or float(resource_amount) <= 0
        ):
            problems.append(
                f"commitmentType {commitment_type_id}.resourceAmount must be positive"
            )
        promised_action_id = str(
            commitment_type.get("promisedActionTypeId") or ""
        ).strip()
        if promised_action_id not in action_ids:
            problems.append(
                f"commitmentType {commitment_type_id} references missing action "
                f"{promised_action_id}"
            )
        else:
            promised_action = action_by_id[promised_action_id]
            promised_costs = promised_action.get("resourceCosts")
            matching_cost = None
            if isinstance(promised_costs, list):
                matching_cost = next(
                    (
                        cost
                        for cost in promised_costs
                        if isinstance(cost, Mapping)
                        and str(cost.get("resourceId") or "").strip() == resource_id
                    ),
                    None,
                )
            if matching_cost is None:
                problems.append(
                    f"commitmentType {commitment_type_id} promised action "
                    f"{promised_action_id} does not consume {resource_id}"
                )
            elif (
                isinstance(resource_amount, (int, float))
                and not isinstance(resource_amount, bool)
                and isinstance(matching_cost.get("amount"), (int, float))
                and not isinstance(matching_cost.get("amount"), bool)
                and float(matching_cost["amount"]) < float(resource_amount)
            ):
                problems.append(
                    f"commitmentType {commitment_type_id} resource amount exceeds "
                    "the promised action cost"
                )

        for promisor_id in promisor_ids:
            promisor = actor_by_id.get(promisor_id)
            if promisor is None:
                continue
            authorities = set(
                _string_ids(
                    promisor.get("authorityIds"),
                    f"actor {promisor_id}.authorityIds",
                    problems,
                )
            )
            missing = sorted(set(required_authority_ids) - authorities)
            if missing:
                problems.append(
                    f"actor {promisor_id} lacks authorities required by "
                    f"{commitment_type_id}: {missing}"
                )

        for template_key in (
            "keptObservationTemplates",
            "brokenObservationTemplates",
        ):
            templates = commitment_type.get(template_key)
            if not isinstance(templates, list) or not templates:
                problems.append(
                    f"commitmentType {commitment_type_id}.{template_key} "
                    "must be a non-empty list"
                )
                continue
            for index, template in enumerate(templates):
                if not isinstance(template, Mapping):
                    problems.append(
                        f"commitmentType {commitment_type_id}.{template_key}"
                        f"[{index}] must be an object"
                    )
                    continue
                observer_id = str(template.get("observerId") or "").strip()
                channel_id = str(template.get("channelId") or "").strip()
                source_id = str(template.get("sourceId") or "").strip()
                if observer_id not in actor_ids:
                    problems.append(
                        f"commitmentType {commitment_type_id}.{template_key}"
                        f"[{index}] references missing observer {observer_id}"
                    )
                if channel_id not in channel_ids:
                    problems.append(
                        f"commitmentType {commitment_type_id}.{template_key}"
                        f"[{index}] references missing channel {channel_id}"
                    )
                if source_id not in source_ids:
                    problems.append(
                        f"commitmentType {commitment_type_id}.{template_key}"
                        f"[{index}] references missing source {source_id}"
                    )
                observer = actor_by_id.get(observer_id)
                if observer is not None:
                    channels_for_actor = set(
                        _string_ids(
                            observer.get("observationChannelIds"),
                            f"actor {observer_id}.observationChannelIds",
                            problems,
                        )
                    )
                    if channel_id and channel_id not in channels_for_actor:
                        problems.append(
                            f"commitmentType {commitment_type_id}.{template_key}"
                            f"[{index}] uses unauthorized channel {channel_id}"
                        )
                _probability(
                    template.get("reliability"),
                    f"commitmentType {commitment_type_id}.{template_key}"
                    f"[{index}].reliability",
                    problems,
                )

    cooperation_profiles_by_parties: dict[tuple[str, str], str] = {}
    for profile_id, profile in cooperation_profile_by_id.items():
        holder_id = str(profile.get("holderActorId") or "").strip()
        subject_id = str(profile.get("subjectActorId") or "").strip()
        if holder_id not in actor_ids:
            problems.append(
                f"cooperationProfile {profile_id} references missing holder "
                f"{holder_id}"
            )
        if subject_id not in actor_ids:
            problems.append(
                f"cooperationProfile {profile_id} references missing subject "
                f"{subject_id}"
            )
        if holder_id and holder_id == subject_id:
            problems.append(
                f"cooperationProfile {profile_id} cannot model self-trust"
            )
        parties = (holder_id, subject_id)
        previous = cooperation_profiles_by_parties.get(parties)
        if previous is not None:
            problems.append(
                f"cooperation profiles {previous} and {profile_id} duplicate "
                f"{holder_id} -> {subject_id}"
            )
        elif holder_id and subject_id:
            cooperation_profiles_by_parties[parties] = profile_id
        for field in ("initialTrust", "keptDelta", "brokenDelta"):
            _probability(
                profile.get(field),
                f"cooperationProfile {profile_id}.{field}",
                problems,
            )

    campaign_route_checkpoints: set[tuple[str, str]] = set()
    for opportunity_id, opportunity in campaign_opportunity_by_id.items():
        route_system_id = str(opportunity.get("routeSystemId") or "").strip()
        if not route_system_id:
            problems.append(
                f"campaignOpportunity {opportunity_id} has no routeSystemId"
            )
        elif space_navigation is not None and route_system_id not in system_ids:
            problems.append(
                f"campaignOpportunity {opportunity_id} references missing system "
                f"{route_system_id}"
            )

        opportunity_checkpoint_ids = _string_ids(
            opportunity.get("checkpointIds"),
            f"campaignOpportunity {opportunity_id}.checkpointIds",
            problems,
        )
        _require_refs(
            opportunity_checkpoint_ids,
            checkpoint_ids,
            label=f"campaignOpportunity {opportunity_id}.checkpointIds",
            problems=problems,
        )
        for checkpoint_id in opportunity_checkpoint_ids:
            route_checkpoint = (route_system_id, checkpoint_id)
            if route_checkpoint in campaign_route_checkpoints:
                problems.append(
                    f"campaign route {route_system_id} is ambiguous at "
                    f"{checkpoint_id}"
                )
            campaign_route_checkpoints.add(route_checkpoint)

        observer_ids = _string_ids(
            opportunity.get("observerIds"),
            f"campaignOpportunity {opportunity_id}.observerIds",
            problems,
        )
        _require_refs(
            observer_ids,
            actor_ids,
            label=f"campaignOpportunity {opportunity_id}.observerIds",
            problems=problems,
        )
        channel_id = str(opportunity.get("channelId") or "").strip()
        source_id = str(opportunity.get("sourceId") or "").strip()
        if channel_id not in channel_ids:
            problems.append(
                f"campaignOpportunity {opportunity_id} references missing channel "
                f"{channel_id}"
            )
        if source_id not in source_ids:
            problems.append(
                f"campaignOpportunity {opportunity_id} references missing source "
                f"{source_id}"
            )
        elif str(source_by_id[source_id].get("kind") or "").strip() != "system":
            problems.append(
                f"campaignOpportunity {opportunity_id} must use a system source"
            )
        for observer_id in observer_ids:
            actor = actor_by_id.get(observer_id)
            if actor is None:
                continue
            actor_channels = set(
                _string_ids(
                    actor.get("observationChannelIds"),
                    f"actor {observer_id}.observationChannelIds",
                    problems,
                )
            )
            if channel_id and channel_id not in actor_channels:
                problems.append(
                    f"campaignOpportunity {opportunity_id} delivers unauthorized "
                    f"channel {channel_id} to {observer_id}"
                )
        duration = opportunity.get("windowDuration")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or duration < 1
        ):
            problems.append(
                f"campaignOpportunity {opportunity_id}.windowDuration must be "
                "a positive integer"
            )
        _probability(
            opportunity.get("reliability"),
            f"campaignOpportunity {opportunity_id}.reliability",
            problems,
        )
        forbidden_director_fields = {
            "actionTypeId",
            "effectTypeId",
            "evidenceId",
            "factId",
            "forcedActorId",
        }
        present_forbidden = sorted(
            forbidden_director_fields & set(opportunity)
        )
        if present_forbidden:
            problems.append(
                f"campaignOpportunity {opportunity_id} contains forbidden actor "
                f"or evidence controls {present_forbidden}"
            )

    for claim_id, claim in communication_claim_by_id.items():
        speaker_ids = _string_ids(
            claim.get("speakerActorIds"),
            f"communicationClaim {claim_id}.speakerActorIds",
            problems,
        )
        audience_ids = _string_ids(
            claim.get("authorizedAudienceActorIds"),
            f"communicationClaim {claim_id}.authorizedAudienceActorIds",
            problems,
        )
        _require_refs(
            speaker_ids,
            actor_ids,
            label=f"communicationClaim {claim_id}.speakerActorIds",
            problems=problems,
        )
        _require_refs(
            audience_ids,
            actor_ids,
            label=f"communicationClaim {claim_id}.authorizedAudienceActorIds",
            problems=problems,
        )
        _probability(
            claim.get("minimumConfidence"),
            f"communicationClaim {claim_id}.minimumConfidence",
            problems,
        )
        proposition = claim.get("proposition")
        minimum = claim.get("minimumConfidence")
        if isinstance(proposition, Mapping) and isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            for speaker_id in speaker_ids:
                has_matching_knowledge = any(
                    belief.get("proposition") == proposition
                    and str(belief.get("holderId") or "").strip() == speaker_id
                    and isinstance(belief.get("confidence"), (int, float))
                    and not isinstance(belief.get("confidence"), bool)
                    and float(belief["confidence"]) >= float(minimum)
                    for belief in beliefs
                ) or any(
                    observation.get("proposition") == proposition
                    and str(observation.get("observerId") or "").strip() == speaker_id
                    and isinstance(observation.get("reliability"), (int, float))
                    and not isinstance(observation.get("reliability"), bool)
                    and float(observation["reliability"]) >= float(minimum)
                    for observation in observations
                )
                if not has_matching_knowledge:
                    problems.append(
                        f"communicationClaim {claim_id} has no sufficient authored "
                        f"knowledge for speaker {speaker_id}"
                    )

    templates_by_intent: dict[str, set[str]] = {}
    for template_id, template in speech_template_by_id.items():
        intent_id = str(template.get("intentId") or "").strip()
        if intent_id not in communicative_intent_ids:
            problems.append(
                f"speechTemplate {template_id} references missing intent {intent_id}"
            )
        templates_by_intent.setdefault(intent_id, set()).add(template_id)
        template_claim_ids = _string_ids(
            template.get("claimIds"),
            f"speechTemplate {template_id}.claimIds",
            problems,
        )
        _require_refs(
            template_claim_ids,
            communication_claim_ids,
            label=f"speechTemplate {template_id}.claimIds",
            problems=problems,
        )
        segment_claim_ids: list[str] = []
        has_commitment_segment = False
        segments = template.get("segments")
        if not isinstance(segments, list) or not segments:
            problems.append(
                f"speechTemplate {template_id}.segments must be a non-empty list"
            )
        else:
            for index, segment in enumerate(segments):
                if not isinstance(segment, Mapping):
                    problems.append(
                        f"speechTemplate {template_id}.segments[{index}] "
                        "must be an object"
                    )
                    continue
                kind = str(segment.get("kind") or "").strip()
                if kind == "claim":
                    segment_claim_ids.append(
                        str(segment.get("claimId") or "").strip()
                    )
                elif kind in {"commitment-label", "commitment-status"}:
                    has_commitment_segment = True
        if set(segment_claim_ids) != set(template_claim_ids):
            problems.append(
                f"speechTemplate {template_id}.claimIds do not match claim segments"
            )
        requires_commitment = template.get("requiresCommitment") is True
        if requires_commitment != has_commitment_segment:
            problems.append(
                f"speechTemplate {template_id}.requiresCommitment does not match "
                "its commitment segments"
            )

    for intent_id, intent in communicative_intent_by_id.items():
        speaker_ids = _string_ids(
            intent.get("speakerActorIds"),
            f"communicativeIntent {intent_id}.speakerActorIds",
            problems,
        )
        audience_ids = _string_ids(
            intent.get("audienceActorIds"),
            f"communicativeIntent {intent_id}.audienceActorIds",
            problems,
        )
        claim_ids = _string_ids(
            intent.get("claimIds"),
            f"communicativeIntent {intent_id}.claimIds",
            problems,
        )
        commitment_type_refs = _string_ids(
            intent.get("commitmentTypeIds"),
            f"communicativeIntent {intent_id}.commitmentTypeIds",
            problems,
        )
        template_ids = _string_ids(
            intent.get("templateIds"),
            f"communicativeIntent {intent_id}.templateIds",
            problems,
        )
        _require_refs(
            speaker_ids,
            actor_ids,
            label=f"communicativeIntent {intent_id}.speakerActorIds",
            problems=problems,
        )
        _require_refs(
            audience_ids,
            actor_ids,
            label=f"communicativeIntent {intent_id}.audienceActorIds",
            problems=problems,
        )
        _require_refs(
            claim_ids,
            communication_claim_ids,
            label=f"communicativeIntent {intent_id}.claimIds",
            problems=problems,
        )
        _require_refs(
            commitment_type_refs,
            commitment_type_ids,
            label=f"communicativeIntent {intent_id}.commitmentTypeIds",
            problems=problems,
        )
        _require_refs(
            template_ids,
            speech_template_ids,
            label=f"communicativeIntent {intent_id}.templateIds",
            problems=problems,
        )
        fallback_template_id = str(
            intent.get("fallbackTemplateId") or ""
        ).strip()
        if fallback_template_id not in template_ids:
            problems.append(
                f"communicativeIntent {intent_id}.fallbackTemplateId must be "
                "one of its templateIds"
            )
        authored_for_intent = templates_by_intent.get(intent_id, set())
        if set(template_ids) != authored_for_intent:
            problems.append(
                f"communicativeIntent {intent_id}.templateIds do not match "
                "the templates authored for that intent"
            )
        for template_id in template_ids:
            template = speech_template_by_id.get(template_id)
            if template is None:
                continue
            template_claim_ids = set(
                _string_ids(
                    template.get("claimIds"),
                    f"speechTemplate {template_id}.claimIds",
                    problems,
                )
            )
            if not template_claim_ids.issubset(set(claim_ids)):
                problems.append(
                    f"speechTemplate {template_id} uses claims outside "
                    f"communicativeIntent {intent_id}"
                )
            if template.get("requiresCommitment") is True and not commitment_type_refs:
                problems.append(
                    f"speechTemplate {template_id} requires a commitment but "
                    f"communicativeIntent {intent_id} allows none"
                )
        if str(intent.get("speechAct") or "").strip() == "promise":
            if not commitment_type_refs:
                problems.append(
                    f"promise intent {intent_id} must reference a commitment type"
                )
            if not any(
                speech_template_by_id.get(template_id, {}).get(
                    "requiresCommitment"
                ) is True
                for template_id in template_ids
            ):
                problems.append(
                    f"promise intent {intent_id} has no commitment-bound template"
                )

    for actor_id, actor in actor_by_id.items():
        actor_goals = _string_ids(
            actor.get("goalIds"), f"actor {actor_id}.goalIds", problems
        )
        actor_channels = _string_ids(
            actor.get("observationChannelIds"),
            f"actor {actor_id}.observationChannelIds",
            problems,
        )
        actor_actions = _string_ids(
            actor.get("candidateActionTypeIds"),
            f"actor {actor_id}.candidateActionTypeIds",
            problems,
        )
        initial_beliefs = _string_ids(
            actor.get("initialBeliefs"), f"actor {actor_id}.initialBeliefs", problems
        )
        initial_memories = _string_ids(
            actor.get("initialMemories"), f"actor {actor_id}.initialMemories", problems
        )
        actor_authorities = set(
            _string_ids(
                actor.get("authorityIds"), f"actor {actor_id}.authorityIds", problems
            )
        )
        policy_profile_id = str(actor.get("policyProfileId") or "").strip()
        if policy_profile_id not in policy_profile_ids:
            problems.append(
                f"actor {actor_id} references missing policy profile {policy_profile_id}"
            )
        else:
            missing_policies = sorted(
                set(actor_actions) - policy_actions_by_id.get(policy_profile_id, set())
            )
            if missing_policies:
                problems.append(
                    f"actor {actor_id} policy profile {policy_profile_id} lacks "
                    f"candidate action policies {missing_policies}"
                )
        _require_refs(
            actor_goals, goal_ids, label=f"actor {actor_id}.goalIds", problems=problems
        )
        _require_refs(
            actor_channels,
            channel_ids,
            label=f"actor {actor_id}.observationChannelIds",
            problems=problems,
        )
        _require_refs(
            actor_actions,
            action_ids,
            label=f"actor {actor_id}.candidateActionTypeIds",
            problems=problems,
        )
        _require_refs(
            initial_beliefs,
            belief_ids,
            label=f"actor {actor_id}.initialBeliefs",
            problems=problems,
        )
        _require_refs(
            initial_memories,
            memory_ids,
            label=f"actor {actor_id}.initialMemories",
            problems=problems,
        )

        for action_type_id in actor_actions:
            missing = sorted(action_authorities.get(action_type_id, set()) - actor_authorities)
            if missing:
                problems.append(
                    f"actor {actor_id} lacks authorities required by {action_type_id}: {missing}"
                )
            for effect_id in action_effects_by_id.get(action_type_id, []):
                effect = effect_by_id.get(effect_id)
                if effect is None:
                    continue
                required_effect_authorities = set(
                    _string_ids(
                        effect.get("requiredAuthorityIds"),
                        f"effectType {effect_id}.requiredAuthorityIds",
                        problems,
                    )
                )
                missing_effect = sorted(required_effect_authorities - actor_authorities)
                if effect.get("protected") is True and missing_effect:
                    problems.append(
                        f"actor {actor_id} lacks protected authorities required by "
                        f"{action_type_id}: {missing_effect}"
                    )

        values = actor.get("valueWeights")
        if not isinstance(values, Mapping) or not values:
            problems.append(f"actor {actor_id}.valueWeights must be a non-empty object")
        else:
            for value_id, weight in values.items():
                _probability(weight, f"actor {actor_id}.valueWeights.{value_id}", problems)

        system_id = str(actor.get("systemId") or "").strip()
        destination_id = str(actor.get("localDestinationId") or "").strip()
        if space_navigation is not None:
            if system_id not in system_ids:
                problems.append(f"actor {actor_id} references missing system {system_id}")
            elif destination_id not in local_by_system.get(system_id, set()):
                problems.append(
                    f"actor {actor_id} references local destination {destination_id} "
                    f"outside system {system_id}"
                )

        actor_state = actor_state_by_id.get(actor_id)
        if actor_state is None:
            problems.append(f"actor {actor_id} has no stateDefaults.actorStates entry")
        else:
            state_goals = set(
                _string_ids(
                    actor_state.get("activeGoalIds"),
                    f"actorState {actor_id}.activeGoalIds",
                    problems,
                )
            )
            state_beliefs = set(
                _string_ids(
                    actor_state.get("beliefIds"),
                    f"actorState {actor_id}.beliefIds",
                    problems,
                )
            )
            state_memories = set(
                _string_ids(
                    actor_state.get("memoryIds"),
                    f"actorState {actor_id}.memoryIds",
                    problems,
                )
            )
            pending = _string_ids(
                actor_state.get("pendingProposalIds"),
                f"actorState {actor_id}.pendingProposalIds",
                problems,
            )
            _require_refs(
                sorted(state_goals),
                goal_ids,
                label=f"actorState {actor_id}.activeGoalIds",
                problems=problems,
            )
            _require_refs(
                sorted(state_beliefs),
                belief_ids,
                label=f"actorState {actor_id}.beliefIds",
                problems=problems,
            )
            _require_refs(
                sorted(state_memories),
                memory_ids,
                label=f"actorState {actor_id}.memoryIds",
                problems=problems,
            )
            _require_refs(
                pending,
                proposal_ids,
                label=f"actorState {actor_id}.pendingProposalIds",
                problems=problems,
            )
            for proposal_id in pending:
                outcome_exists = any(
                    str(outcome.get("proposalId") or "").strip() == proposal_id
                    for outcome in outcomes
                )
                if outcome_exists:
                    problems.append(
                        f"actorState {actor_id}.pendingProposalIds contains completed {proposal_id}"
                    )
            if not set(initial_beliefs).issubset(state_beliefs):
                problems.append(
                    f"actorState {actor_id}.beliefIds does not contain all initialBeliefs"
                )
            if not set(initial_memories).issubset(state_memories):
                problems.append(
                    f"actorState {actor_id}.memoryIds does not contain all initialMemories"
                )

    for actor_state_id in actor_state_by_id:
        if actor_state_id not in actor_ids:
            problems.append(f"actorState references missing actor {actor_state_id}")

    for fact_id, fact in fact_by_id.items():
        refs = _string_ids(fact.get("sourceIds"), f"fact {fact_id}.sourceIds", problems)
        _require_refs(
            refs, source_ids, label=f"fact {fact_id}.sourceIds", problems=problems
        )

    for evidence_id, item in evidence_by_id.items():
        source_id = str(item.get("sourceId") or "").strip()
        if source_id not in source_ids:
            problems.append(f"evidence {evidence_id} references missing source {source_id}")
        supported = _string_ids(
            item.get("supportsFactIds"),
            f"evidence {evidence_id}.supportsFactIds",
            problems,
        )
        _require_refs(
            supported,
            fact_ids,
            label=f"evidence {evidence_id}.supportsFactIds",
            problems=problems,
        )
        _probability(item.get("reliability"), f"evidence {evidence_id}.reliability", problems)

    for goal_id, goal in goal_by_id.items():
        owner_id = str(goal.get("ownerActorId") or "").strip()
        if owner_id not in actor_ids:
            problems.append(f"goal {goal_id} references missing owner actor {owner_id}")
        _probability(goal.get("priority"), f"goal {goal_id}.priority", problems)

    for checkpoint_id, checkpoint in checkpoint_by_id.items():
        refs = _string_ids(
            checkpoint.get("factIds"), f"checkpoint {checkpoint_id}.factIds", problems
        )
        _require_refs(
            refs,
            fact_ids,
            label=f"checkpoint {checkpoint_id}.factIds",
            problems=problems,
        )

    for observation_id, observation in observation_by_id.items():
        observer_id = str(observation.get("observerId") or "").strip()
        channel_id = str(observation.get("channelId") or "").strip()
        source_id = str(observation.get("sourceId") or "").strip()
        if observer_id not in actor_ids:
            problems.append(
                f"observation {observation_id} references missing observer {observer_id}"
            )
        if channel_id not in channel_ids:
            problems.append(
                f"observation {observation_id} references missing channel {channel_id}"
            )
        if source_id not in source_ids:
            problems.append(
                f"observation {observation_id} references missing source {source_id}"
            )
        actor = actor_by_id.get(observer_id)
        if actor is not None:
            actor_channels = set(
                _string_ids(
                    actor.get("observationChannelIds"),
                    f"actor {observer_id}.observationChannelIds",
                    problems,
                )
            )
            if channel_id and channel_id not in actor_channels:
                problems.append(
                    f"observation {observation_id} uses unauthorized channel {channel_id}"
                )
        _probability(
            observation.get("reliability"),
            f"observation {observation_id}.reliability",
            problems,
        )
        report_value = observation.get("reportId")
        report_id = (
            str(report_value or "").strip()
            if report_value is not None
            else ""
        )
        if report_value is not None and report_id not in report_ids:
            problems.append(
                f"observation {observation_id} references missing report {report_id}"
            )
        origin_value = observation.get("originObservationId")
        origin_id = (
            str(origin_value or "").strip()
            if origin_value is not None
            else ""
        )
        if origin_value is not None and origin_id not in observation_ids:
            problems.append(
                f"observation {observation_id} references missing origin observation "
                f"{origin_id}"
            )

    for report_id, report in report_by_id.items():
        route_id = str(report.get("routeId") or "").strip()
        sender_id = str(report.get("senderActorId") or "").strip()
        recipient_id = str(report.get("recipientActorId") or "").strip()
        source_observation_id = str(report.get("sourceObservationId") or "").strip()
        origin_observation_id = str(report.get("originObservationId") or "").strip()
        recipient_observation_id = str(
            report.get("recipientObservationId") or ""
        ).strip()
        if route_id not in report_route_ids:
            problems.append(
                f"report {report_id} references missing route {route_id}"
            )
        if sender_id not in actor_ids:
            problems.append(
                f"report {report_id} references missing sender {sender_id}"
            )
        if recipient_id not in actor_ids:
            problems.append(
                f"report {report_id} references missing recipient {recipient_id}"
            )
        if source_observation_id not in observation_ids:
            problems.append(
                f"report {report_id} references missing source observation "
                f"{source_observation_id}"
            )
        if origin_observation_id not in observation_ids:
            problems.append(
                f"report {report_id} references missing origin observation "
                f"{origin_observation_id}"
            )
        if recipient_observation_id not in observation_ids:
            problems.append(
                f"report {report_id} references missing recipient observation "
                f"{recipient_observation_id}"
            )
        parent_ids = _string_ids(
            report.get("parentReportIds"),
            f"report {report_id}.parentReportIds",
            problems,
        )
        _require_refs(
            parent_ids,
            report_ids,
            label=f"report {report_id}.parentReportIds",
            problems=problems,
        )
        if report_id in parent_ids:
            problems.append(f"report {report_id} cannot be its own parent")
        source_observation = observation_by_id.get(source_observation_id)
        if source_observation is not None:
            if str(source_observation.get("observerId") or "").strip() != sender_id:
                problems.append(
                    f"report {report_id} sender did not observe its source"
                )
            expected_origin = str(
                source_observation.get("originObservationId")
                or source_observation_id
            ).strip()
            if origin_observation_id != expected_origin:
                problems.append(
                    f"report {report_id}.originObservationId does not preserve ancestry"
                )
            source_report_id = str(source_observation.get("reportId") or "").strip()
            if source_report_id and source_report_id not in parent_ids:
                problems.append(
                    f"report {report_id}.parentReportIds omits source report "
                    f"{source_report_id}"
                )
        recipient_observation = observation_by_id.get(recipient_observation_id)
        if recipient_observation is not None:
            if str(recipient_observation.get("observerId") or "").strip() != recipient_id:
                problems.append(
                    f"report {report_id} recipient observation has wrong observer"
                )
            if str(recipient_observation.get("reportId") or "").strip() != report_id:
                problems.append(
                    f"report {report_id} recipient observation does not link back"
                )
            if str(recipient_observation.get("originObservationId") or "").strip() != origin_observation_id:
                problems.append(
                    f"report {report_id} recipient observation loses origin ancestry"
                )
            if recipient_observation.get("proposition") != report.get("proposition"):
                problems.append(
                    f"report {report_id} recipient proposition does not match report"
                )
        route = report_route_by_id.get(route_id)
        if route is not None:
            senders = set(
                _string_ids(
                    route.get("senderActorIds"),
                    f"reportRoute {route_id}.senderActorIds",
                    problems,
                )
            )
            recipients = set(
                _string_ids(
                    route.get("recipientActorIds"),
                    f"reportRoute {route_id}.recipientActorIds",
                    problems,
                )
            )
            if sender_id not in senders:
                problems.append(
                    f"report {report_id} sender is not authorized by route {route_id}"
                )
            if recipient_id not in recipients:
                problems.append(
                    f"report {report_id} recipient is not authorized by route {route_id}"
                )
            distortion = report.get("distortion")
            _probability(
                distortion,
                f"report {report_id}.distortion",
                problems,
            )
            maximum = route.get("maxDistortion")
            if (
                isinstance(distortion, (int, float))
                and not isinstance(distortion, bool)
                and isinstance(maximum, (int, float))
                and not isinstance(maximum, bool)
                and float(distortion) > float(maximum)
            ):
                problems.append(
                    f"report {report_id} exceeds route distortion limit"
                )
            sent_at = report.get("sentAt")
            received_at = report.get("receivedAt")
            latency = route.get("latency")
            if (
                isinstance(sent_at, int)
                and not isinstance(sent_at, bool)
                and isinstance(received_at, int)
                and not isinstance(received_at, bool)
                and isinstance(latency, int)
                and not isinstance(latency, bool)
                and received_at != sent_at + latency
            ):
                problems.append(
                    f"report {report_id}.receivedAt does not match route latency"
                )
            if (
                str(route.get("mode") or "").strip() == "public"
                and source_observation is not None
                and str(source_observation.get("visibility") or "").strip() != "public"
            ):
                problems.append(
                    f"public report {report_id} exposes a non-public source"
                )
        _probability(
            report.get("reliability"),
            f"report {report_id}.reliability",
            problems,
        )

    captain_models_by_profile: dict[str, str] = {}
    for model_id, model in captain_model_by_id.items():
        profile_id = str(model.get("profileId") or "").strip()
        holder_id = str(model.get("holderActorId") or "").strip()
        subject_id = str(model.get("subjectActorId") or "").strip()
        if profile_id not in captain_model_profile_ids:
            problems.append(
                f"captainModel {model_id} references missing profile {profile_id}"
            )
        if holder_id not in actor_ids:
            problems.append(
                f"captainModel {model_id} references missing holder {holder_id}"
            )
        previous_model = captain_models_by_profile.get(profile_id)
        if previous_model is not None:
            problems.append(
                f"captain model profile {profile_id} has multiple states: "
                f"{previous_model} and {model_id}"
            )
        elif profile_id:
            captain_models_by_profile[profile_id] = model_id
        profile = captain_model_profile_by_id.get(profile_id)
        if profile is not None:
            if str(profile.get("actorId") or "").strip() != holder_id:
                problems.append(
                    f"captainModel {model_id}.holderActorId does not match profile"
                )
            if str(profile.get("subjectActorId") or "").strip() != subject_id:
                problems.append(
                    f"captainModel {model_id}.subjectActorId does not match profile"
                )
            expected_tendencies = set(
                profile.get("initialTendencies", {})
                if isinstance(profile.get("initialTendencies"), Mapping)
                else {}
            )
            tendencies = model.get("tendencies")
            if not isinstance(tendencies, Mapping):
                problems.append(
                    f"captainModel {model_id}.tendencies must be an object"
                )
            else:
                if set(tendencies) != expected_tendencies:
                    problems.append(
                        f"captainModel {model_id}.tendencies do not match profile"
                    )
                for tendency_id, value in tendencies.items():
                    _probability(
                        value,
                        f"captainModel {model_id}.tendencies.{tendency_id}",
                        problems,
                    )
        model_observation_ids = _string_ids(
            model.get("observationIds"),
            f"captainModel {model_id}.observationIds",
            problems,
        )
        _require_refs(
            model_observation_ids,
            observation_ids,
            label=f"captainModel {model_id}.observationIds",
            problems=problems,
        )
        model_report_ids = _string_ids(
            model.get("reportIds"),
            f"captainModel {model_id}.reportIds",
            problems,
        )
        _require_refs(
            model_report_ids,
            report_ids,
            label=f"captainModel {model_id}.reportIds",
            problems=problems,
        )
    missing_captain_models = sorted(
        captain_model_profile_ids - set(captain_models_by_profile)
    )
    if missing_captain_models:
        problems.append(
            f"stateDefaults.captainModels missing profiles {missing_captain_models}"
        )

    commitments_by_type: dict[str, list[str]] = {}
    for commitment_id, commitment in commitment_by_id.items():
        type_id = str(commitment.get("commitmentTypeId") or "").strip()
        promisor_id = str(commitment.get("promisorActorId") or "").strip()
        promisee_id = str(commitment.get("promiseeActorId") or "").strip()
        status = str(commitment.get("status") or "").strip()
        resolution_outcome_value = commitment.get("resolutionOutcomeId")
        resolution_outcome_id = (
            str(resolution_outcome_value or "").strip()
            if resolution_outcome_value is not None
            else ""
        )
        if type_id not in commitment_type_ids:
            problems.append(
                f"commitment {commitment_id} references missing type {type_id}"
            )
        if promisor_id not in actor_ids:
            problems.append(
                f"commitment {commitment_id} references missing promisor {promisor_id}"
            )
        if promisee_id not in actor_ids:
            problems.append(
                f"commitment {commitment_id} references missing promisee {promisee_id}"
            )
        type_record = commitment_type_by_id.get(type_id)
        if type_record is not None:
            allowed_promisors = set(
                _string_ids(
                    type_record.get("promisorActorIds"),
                    f"commitmentType {type_id}.promisorActorIds",
                    problems,
                )
            )
            allowed_promisees = set(
                _string_ids(
                    type_record.get("promiseeActorIds"),
                    f"commitmentType {type_id}.promiseeActorIds",
                    problems,
                )
            )
            if promisor_id not in allowed_promisors:
                problems.append(
                    f"commitment {commitment_id} uses unauthorized promisor "
                    f"{promisor_id}"
                )
            if promisee_id not in allowed_promisees:
                problems.append(
                    f"commitment {commitment_id} uses unauthorized promisee "
                    f"{promisee_id}"
                )
        observation_refs = _string_ids(
            commitment.get("observationIds"),
            f"commitment {commitment_id}.observationIds",
            problems,
        )
        _require_refs(
            observation_refs,
            observation_ids,
            label=f"commitment {commitment_id}.observationIds",
            problems=problems,
        )
        if status == "pending":
            if resolution_outcome_value is not None:
                problems.append(
                    f"pending commitment {commitment_id} cannot reference an outcome"
                )
            if observation_refs:
                problems.append(
                    f"pending commitment {commitment_id} cannot have observations"
                )
        elif status in {"kept", "broken"}:
            if resolution_outcome_id not in outcome_ids:
                problems.append(
                    f"commitment {commitment_id} references missing outcome "
                    f"{resolution_outcome_id}"
                )
            if not observation_refs:
                problems.append(
                    f"resolved commitment {commitment_id} must have observations"
                )
            outcome = outcome_by_id.get(resolution_outcome_id)
            if outcome is not None:
                if str(outcome.get("status") or "").strip() != "accepted":
                    problems.append(
                        f"commitment {commitment_id} resolves from a rejected outcome"
                    )
                if type_record is not None:
                    promised_action_id = str(
                        type_record.get("promisedActionTypeId") or ""
                    ).strip()
                    if status == "kept":
                        if (
                            str(outcome.get("actorId") or "").strip() != promisor_id
                            or str(outcome.get("actionTypeId") or "").strip()
                            != promised_action_id
                        ):
                            problems.append(
                                f"kept commitment {commitment_id} does not match "
                                "the promised actor action"
                            )
                    if status == "broken":
                        resource_id = str(
                            type_record.get("resourceId") or ""
                        ).strip()
                        consumed = outcome.get("consumedResources")
                        consumed_ids = {
                            str(item.get("resourceId") or "").strip()
                            for item in consumed
                            if isinstance(item, Mapping)
                        } if isinstance(consumed, list) else set()
                        if resource_id not in consumed_ids:
                            problems.append(
                                f"broken commitment {commitment_id} outcome does not "
                                f"consume pledged resource {resource_id}"
                            )
        else:
            problems.append(
                f"commitment {commitment_id} has unsupported status {status}"
            )
        commitments_by_type.setdefault(type_id, []).append(commitment_id)

    cooperation_models_by_profile: dict[str, str] = {}
    for model_id, model in cooperation_model_by_id.items():
        profile_id = str(model.get("profileId") or "").strip()
        holder_id = str(model.get("holderActorId") or "").strip()
        subject_id = str(model.get("subjectActorId") or "").strip()
        if profile_id not in cooperation_profile_ids:
            problems.append(
                f"cooperationModel {model_id} references missing profile {profile_id}"
            )
        if holder_id not in actor_ids:
            problems.append(
                f"cooperationModel {model_id} references missing holder {holder_id}"
            )
        if subject_id not in actor_ids:
            problems.append(
                f"cooperationModel {model_id} references missing subject {subject_id}"
            )
        previous = cooperation_models_by_profile.get(profile_id)
        if previous is not None:
            problems.append(
                f"cooperation profile {profile_id} has multiple states: "
                f"{previous} and {model_id}"
            )
        elif profile_id:
            cooperation_models_by_profile[profile_id] = model_id
        profile = cooperation_profile_by_id.get(profile_id)
        if profile is not None:
            if str(profile.get("holderActorId") or "").strip() != holder_id:
                problems.append(
                    f"cooperationModel {model_id}.holderActorId does not match profile"
                )
            if str(profile.get("subjectActorId") or "").strip() != subject_id:
                problems.append(
                    f"cooperationModel {model_id}.subjectActorId does not match profile"
                )
        _probability(
            model.get("trust"),
            f"cooperationModel {model_id}.trust",
            problems,
        )
        model_commitment_ids = _string_ids(
            model.get("commitmentIds"),
            f"cooperationModel {model_id}.commitmentIds",
            problems,
        )
        _require_refs(
            model_commitment_ids,
            commitment_ids,
            label=f"cooperationModel {model_id}.commitmentIds",
            problems=problems,
        )
        for commitment_id in model_commitment_ids:
            commitment = commitment_by_id.get(commitment_id)
            if commitment is None:
                continue
            if (
                str(commitment.get("promiseeActorId") or "").strip() != holder_id
                or str(commitment.get("promisorActorId") or "").strip() != subject_id
            ):
                problems.append(
                    f"cooperationModel {model_id} references unrelated commitment "
                    f"{commitment_id}"
                )

    missing_cooperation_models = sorted(
        cooperation_profile_ids - set(cooperation_models_by_profile)
    )
    if missing_cooperation_models:
        problems.append(
            f"stateDefaults.cooperationModels missing profiles "
            f"{missing_cooperation_models}"
        )

    opportunity_state_ids = set(campaign_opportunity_state_by_id)
    if opportunity_state_ids != campaign_opportunity_ids:
        missing = sorted(campaign_opportunity_ids - opportunity_state_ids)
        extra = sorted(opportunity_state_ids - campaign_opportunity_ids)
        if missing:
            problems.append(
                f"stateDefaults.campaignOpportunityStates missing opportunities "
                f"{missing}"
            )
        if extra:
            problems.append(
                f"stateDefaults.campaignOpportunityStates references unknown "
                f"opportunities {extra}"
            )

    receipts_by_opportunity: dict[str, list[Mapping[str, Any]]] = {}
    for director_receipt_id, director_receipt in director_receipt_by_id.items():
        opportunity_id = str(
            director_receipt.get("opportunityId") or ""
        ).strip()
        route_system_id = str(
            director_receipt.get("routeSystemId") or ""
        ).strip()
        checkpoint_id = str(director_receipt.get("checkpointId") or "").strip()
        operation = str(director_receipt.get("operation") or "").strip()
        previous_status = str(
            director_receipt.get("previousStatus") or ""
        ).strip()
        next_status = str(director_receipt.get("nextStatus") or "").strip()
        if opportunity_id not in campaign_opportunity_ids:
            problems.append(
                f"directorReceipt {director_receipt_id} references missing "
                f"opportunity {opportunity_id}"
            )
        opportunity = campaign_opportunity_by_id.get(opportunity_id)
        if opportunity is not None:
            expected_route = str(
                opportunity.get("routeSystemId") or ""
            ).strip()
            if route_system_id != expected_route:
                problems.append(
                    f"directorReceipt {director_receipt_id}.routeSystemId does "
                    "not match its opportunity"
                )
            allowed_checkpoints = set(
                _string_ids(
                    opportunity.get("checkpointIds"),
                    f"campaignOpportunity {opportunity_id}.checkpointIds",
                    problems,
                )
            )
            if checkpoint_id not in allowed_checkpoints:
                problems.append(
                    f"directorReceipt {director_receipt_id} uses unauthorized "
                    f"checkpoint {checkpoint_id}"
                )
        if checkpoint_id not in checkpoint_ids:
            problems.append(
                f"directorReceipt {director_receipt_id} references missing "
                f"checkpoint {checkpoint_id}"
            )

        transition = (operation, previous_status, next_status)
        allowed_transitions = {
            ("activate", "available", "active"),
            ("deactivate", "active", "available"),
            ("expire", "active", "closed"),
        }
        if transition not in allowed_transitions:
            problems.append(
                f"directorReceipt {director_receipt_id} has invalid transition "
                f"{transition}"
            )

        selected_at = director_receipt.get("selectedAt")
        if (
            isinstance(selected_at, bool)
            or not isinstance(selected_at, int)
            or selected_at < 0
        ):
            problems.append(
                f"directorReceipt {director_receipt_id}.selectedAt must be a "
                "non-negative integer"
            )
        canonical_revision = director_receipt.get("canonicalRevision")
        if (
            isinstance(canonical_revision, bool)
            or not isinstance(canonical_revision, int)
            or canonical_revision < 0
        ):
            problems.append(
                f"directorReceipt {director_receipt_id}.canonicalRevision must "
                "be a non-negative integer"
            )
        current_revision = canonical.get("revision")
        if (
            isinstance(canonical_revision, int)
            and not isinstance(canonical_revision, bool)
            and isinstance(current_revision, int)
            and not isinstance(current_revision, bool)
            and canonical_revision > current_revision
        ):
            problems.append(
                f"directorReceipt {director_receipt_id} is bound to a future "
                "canonical revision"
            )

        expires_at = director_receipt.get("expiresAt")
        if operation == "activate":
            duration = (
                opportunity.get("windowDuration")
                if opportunity is not None
                else None
            )
            if (
                isinstance(selected_at, int)
                and not isinstance(selected_at, bool)
                and isinstance(duration, int)
                and not isinstance(duration, bool)
                and expires_at != selected_at + duration
            ):
                problems.append(
                    f"directorReceipt {director_receipt_id}.expiresAt does not "
                    "match the authored duration"
                )
        elif expires_at is not None:
            problems.append(
                f"directorReceipt {director_receipt_id}.expiresAt must be null "
                f"for {operation}"
            )

        director_observation_ids = _string_ids(
            director_receipt.get("observationIds"),
            f"directorReceipt {director_receipt_id}.observationIds",
            problems,
        )
        _require_refs(
            director_observation_ids,
            observation_ids,
            label=f"directorReceipt {director_receipt_id}.observationIds",
            problems=problems,
        )
        expected_observers = set(
            _string_ids(
                opportunity.get("observerIds"),
                f"campaignOpportunity {opportunity_id}.observerIds",
                problems,
            )
        ) if opportunity is not None else set()
        actual_observers: set[str] = set()
        for observation_id in director_observation_ids:
            observation = observation_by_id.get(observation_id)
            if observation is None:
                continue
            observer_id = str(observation.get("observerId") or "").strip()
            actual_observers.add(observer_id)
            proposition = observation.get("proposition")
            expected_value = operation == "activate"
            expected_proposition = {
                "predicate": STRATEGIC_AI_DIRECTOR_PREDICATE,
                "arguments": [opportunity_id, route_system_id],
                "value": expected_value,
            }
            if proposition != expected_proposition:
                problems.append(
                    f"directorReceipt {director_receipt_id} observation "
                    f"{observation_id} is not a bounded opportunity proposition"
                )
            if opportunity is not None:
                if str(observation.get("channelId") or "").strip() != str(
                    opportunity.get("channelId") or ""
                ).strip():
                    problems.append(
                        f"directorReceipt {director_receipt_id} observation "
                        f"{observation_id} uses the wrong channel"
                    )
                if str(observation.get("sourceId") or "").strip() != str(
                    opportunity.get("sourceId") or ""
                ).strip():
                    problems.append(
                        f"directorReceipt {director_receipt_id} observation "
                        f"{observation_id} uses the wrong source"
                    )
                if observation.get("visibility") != opportunity.get("visibility"):
                    problems.append(
                        f"directorReceipt {director_receipt_id} observation "
                        f"{observation_id} uses the wrong visibility"
                    )
            if (
                isinstance(selected_at, int)
                and not isinstance(selected_at, bool)
                and observation.get("observedAt") != selected_at
            ):
                problems.append(
                    f"directorReceipt {director_receipt_id} observation "
                    f"{observation_id} has the wrong time"
                )
        if expected_observers and actual_observers != expected_observers:
            problems.append(
                f"directorReceipt {director_receipt_id} observations do not "
                "cover the authored observer set"
            )
        receipts_by_opportunity.setdefault(opportunity_id, []).append(
            director_receipt
        )

    for opportunity_id, opportunity_state in campaign_opportunity_state_by_id.items():
        status = str(opportunity_state.get("status") or "").strip()
        activated_at = opportunity_state.get("activatedAt")
        expires_at = opportunity_state.get("expiresAt")
        activation_receipt_value = opportunity_state.get("activationReceiptId")
        activation_receipt_id = (
            str(activation_receipt_value or "").strip()
            if activation_receipt_value is not None
            else ""
        )
        activation_count = opportunity_state.get("activationCount")
        if (
            isinstance(activation_count, bool)
            or not isinstance(activation_count, int)
            or activation_count < 0
        ):
            problems.append(
                f"campaignOpportunityState {opportunity_id}.activationCount "
                "must be a non-negative integer"
            )
        opportunity_receipts = receipts_by_opportunity.get(opportunity_id, [])
        actual_activation_count = sum(
            1
            for receipt in opportunity_receipts
            if str(receipt.get("operation") or "").strip() == "activate"
        )
        if (
            isinstance(activation_count, int)
            and not isinstance(activation_count, bool)
            and activation_count != actual_activation_count
        ):
            problems.append(
                f"campaignOpportunityState {opportunity_id}.activationCount "
                "does not match director receipts"
            )

        if opportunity_receipts:
            last_receipt = opportunity_receipts[-1]
            expected_status = str(last_receipt.get("nextStatus") or "").strip()
            if status != expected_status:
                problems.append(
                    f"campaignOpportunityState {opportunity_id}.status does not "
                    "match its latest receipt"
                )
        elif status != "available":
            problems.append(
                f"campaignOpportunityState {opportunity_id} must begin available"
            )

        if status == "active":
            if activation_receipt_id not in director_receipt_ids:
                problems.append(
                    f"campaignOpportunityState {opportunity_id} references "
                    f"missing activation receipt {activation_receipt_id}"
                )
            activation_receipt = director_receipt_by_id.get(
                activation_receipt_id
            )
            if activation_receipt is not None:
                if (
                    str(activation_receipt.get("operation") or "").strip()
                    != "activate"
                    or str(
                        activation_receipt.get("opportunityId") or ""
                    ).strip()
                    != opportunity_id
                ):
                    problems.append(
                        f"campaignOpportunityState {opportunity_id} references "
                        "an unrelated activation receipt"
                    )
                if activated_at != activation_receipt.get("selectedAt"):
                    problems.append(
                        f"campaignOpportunityState {opportunity_id}.activatedAt "
                        "does not match its receipt"
                    )
                if expires_at != activation_receipt.get("expiresAt"):
                    problems.append(
                        f"campaignOpportunityState {opportunity_id}.expiresAt "
                        "does not match its receipt"
                    )
        else:
            if (
                activated_at is not None
                or expires_at is not None
                or activation_receipt_value is not None
            ):
                problems.append(
                    f"inactive campaignOpportunityState {opportunity_id} must "
                    "clear activation bindings"
                )

    belief_basis_ids = (
        fact_ids | evidence_ids | observation_ids | memory_ids | proposal_ids
        | outcome_ids | report_ids | captain_model_ids | commitment_ids
        | cooperation_model_ids | event_ids
    )
    for belief_id, belief in belief_by_id.items():
        holder_id = str(belief.get("holderId") or "").strip()
        if holder_id not in actor_ids:
            problems.append(f"belief {belief_id} references missing holder {holder_id}")
        basis = _string_ids(
            belief.get("basisIds"), f"belief {belief_id}.basisIds", problems
        )
        if not basis:
            problems.append(f"belief {belief_id} has no provenance basis")
        _require_refs(
            basis,
            belief_basis_ids,
            label=f"belief {belief_id}.basisIds",
            problems=problems,
        )
        _probability(belief.get("confidence"), f"belief {belief_id}.confidence", problems)

    memory_source_ids = (
        fact_ids | evidence_ids | observation_ids | belief_ids | memory_ids
        | proposal_ids | outcome_ids | report_ids | captain_model_ids
        | commitment_ids | cooperation_model_ids | event_ids
    )
    for memory_id, memory in memory_by_id.items():
        owner_id = str(memory.get("ownerActorId") or "").strip()
        if owner_id not in actor_ids:
            problems.append(f"memory {memory_id} references missing owner actor {owner_id}")
        refs = _string_ids(
            memory.get("sourceIds"), f"memory {memory_id}.sourceIds", problems
        )
        _require_refs(
            refs,
            memory_source_ids,
            label=f"memory {memory_id}.sourceIds",
            problems=problems,
        )
        _probability(memory.get("salience"), f"memory {memory_id}.salience", problems)

    for receipt_id, receipt in receipt_by_id.items():
        actor_id = str(receipt.get("actorId") or "").strip()
        checkpoint_id = str(receipt.get("checkpointId") or "").strip()
        selected_action = str(receipt.get("selectedActionTypeId") or "").strip()
        policy_profile_value = receipt.get("policyProfileId")
        policy_profile_id = (
            str(policy_profile_value or "").strip()
            if policy_profile_value is not None
            else ""
        )
        canonical_revision = receipt.get("canonicalRevision")
        if policy_profile_value is not None and policy_profile_id not in policy_profile_ids:
            problems.append(
                f"receipt {receipt_id} references missing policy profile {policy_profile_id}"
            )
        if canonical_revision is not None and (
            isinstance(canonical_revision, bool)
            or not isinstance(canonical_revision, int)
            or canonical_revision < 0
        ):
            problems.append(
                f"receipt {receipt_id}.canonicalRevision must be null or a non-negative integer"
            )
        if actor_id not in actor_ids:
            problems.append(f"receipt {receipt_id} references missing actor {actor_id}")
        if checkpoint_id not in checkpoint_ids:
            problems.append(
                f"receipt {receipt_id} references missing checkpoint {checkpoint_id}"
            )
        if selected_action not in action_ids:
            problems.append(
                f"receipt {receipt_id} references missing selected action {selected_action}"
            )
        actor = actor_by_id.get(actor_id)
        if actor is not None and policy_profile_value is not None:
            actor_profile_id = str(actor.get("policyProfileId") or "").strip()
            if policy_profile_id != actor_profile_id:
                problems.append(
                    f"receipt {receipt_id}.policyProfileId does not match actor"
                )
        receipt_goals = _string_ids(
            receipt.get("activeGoalIds"),
            f"receipt {receipt_id}.activeGoalIds",
            problems,
        )
        receipt_beliefs = _string_ids(
            receipt.get("beliefIds"), f"receipt {receipt_id}.beliefIds", problems
        )
        expected_effects = _string_ids(
            receipt.get("expectedEffectTypeIds"),
            f"receipt {receipt_id}.expectedEffectTypeIds",
            problems,
        )
        _require_refs(
            receipt_goals,
            goal_ids,
            label=f"receipt {receipt_id}.activeGoalIds",
            problems=problems,
        )
        _require_refs(
            receipt_beliefs,
            belief_ids,
            label=f"receipt {receipt_id}.beliefIds",
            problems=problems,
        )
        _require_refs(
            expected_effects,
            effect_ids,
            label=f"receipt {receipt_id}.expectedEffectTypeIds",
            problems=problems,
        )
        if selected_action in action_effects_by_id:
            if sorted(expected_effects) != sorted(action_effects_by_id[selected_action]):
                problems.append(
                    f"receipt {receipt_id}.expectedEffectTypeIds does not match selected action"
                )
        candidate_actions = receipt.get("candidateActions")
        if not isinstance(candidate_actions, list):
            problems.append(f"receipt {receipt_id}.candidateActions must be a list")
        else:
            for index, candidate in enumerate(candidate_actions):
                if not isinstance(candidate, Mapping):
                    problems.append(
                        f"receipt {receipt_id}.candidateActions[{index}] must be an object"
                    )
                    continue
                action_type_id = str(candidate.get("actionTypeId") or "").strip()
                if action_type_id not in action_ids:
                    problems.append(
                        f"receipt {receipt_id}.candidateActions[{index}] "
                        f"references missing action {action_type_id}"
                    )
        rejections = receipt.get("rejections")
        if not isinstance(rejections, list):
            problems.append(f"receipt {receipt_id}.rejections must be a list")
        else:
            for index, rejection in enumerate(rejections):
                if not isinstance(rejection, Mapping):
                    problems.append(
                        f"receipt {receipt_id}.rejections[{index}] must be an object"
                    )
                    continue
                action_type_id = str(rejection.get("actionTypeId") or "").strip()
                if action_type_id not in action_ids:
                    problems.append(
                        f"receipt {receipt_id}.rejections[{index}] "
                        f"references missing action {action_type_id}"
                    )
        _probability(receipt.get("confidence"), f"receipt {receipt_id}.confidence", problems)

    rejected_proposal_ids = {
        str(outcome.get("proposalId") or "").strip()
        for outcome in outcomes
        if str(outcome.get("status") or "").strip() == "rejected"
    }
    proposal_decisions: set[str] = set()
    for proposal_id, proposal in proposal_by_id.items():
        decision_id = str(proposal.get("decisionId") or "").strip()
        if decision_id in proposal_decisions:
            problems.append(f"multiple proposals reference decision {decision_id}")
        proposal_decisions.add(decision_id)
        actor_id = str(proposal.get("actorId") or "").strip()
        checkpoint_id = str(proposal.get("checkpointId") or "").strip()
        action_id = str(proposal.get("actionTypeId") or "").strip()
        location_id = str(proposal.get("locationId") or "").strip()
        proposal_revision = proposal.get("canonicalRevision")
        if proposal_revision is not None and (
            isinstance(proposal_revision, bool)
            or not isinstance(proposal_revision, int)
            or proposal_revision < 0
        ):
            problems.append(
                f"proposal {proposal_id}.canonicalRevision must be null or a non-negative integer"
            )
        if decision_id not in receipt_ids:
            problems.append(f"proposal {proposal_id} references missing decision {decision_id}")
        if actor_id not in actor_ids:
            problems.append(f"proposal {proposal_id} references missing actor {actor_id}")
        if checkpoint_id not in checkpoint_ids:
            problems.append(
                f"proposal {proposal_id} references missing checkpoint {checkpoint_id}"
            )
        if action_id not in action_ids:
            problems.append(f"proposal {proposal_id} references missing action {action_id}")
        receipt = receipt_by_id.get(decision_id)
        enforce_semantics = proposal_id not in rejected_proposal_ids
        if receipt is not None and enforce_semantics:
            if str(receipt.get("actorId") or "").strip() != actor_id:
                problems.append(f"proposal {proposal_id} actor does not match decision")
            if str(receipt.get("checkpointId") or "").strip() != checkpoint_id:
                problems.append(f"proposal {proposal_id} checkpoint does not match decision")
            if str(receipt.get("selectedActionTypeId") or "").strip() != action_id:
                problems.append(f"proposal {proposal_id} action does not match decision")
            if receipt.get("canonicalRevision") != proposal_revision:
                problems.append(
                    f"proposal {proposal_id} canonical revision does not match decision"
                )
        action = action_by_id.get(action_id)
        if action is not None and enforce_semantics:
            locations = set(
                _string_ids(
                    action.get("allowedLocationIds"),
                    f"actionType {action_id}.allowedLocationIds",
                    problems,
                )
            )
            if location_id not in locations:
                problems.append(
                    f"proposal {proposal_id} uses disallowed location {location_id}"
                )
        requested = proposal.get("requestedEffects")
        requested_ids: list[str] = []
        if not isinstance(requested, list):
            problems.append(f"proposal {proposal_id}.requestedEffects must be a list")
        else:
            for index, request in enumerate(requested):
                if not isinstance(request, Mapping):
                    problems.append(
                        f"proposal {proposal_id}.requestedEffects[{index}] must be an object"
                    )
                    continue
                effect_id = str(request.get("effectTypeId") or "").strip()
                requested_ids.append(effect_id)
                if effect_id not in effect_ids:
                    problems.append(
                        f"proposal {proposal_id}.requestedEffects[{index}] "
                        f"references missing effect {effect_id}"
                    )
        if enforce_semantics and action_id in action_effects_by_id:
            if sorted(requested_ids) != sorted(action_effects_by_id[action_id]):
                problems.append(
                    f"proposal {proposal_id}.requestedEffects does not match action allowlist"
                )

    outcome_proposals: set[str] = set()
    for outcome_id, outcome in outcome_by_id.items():
        proposal_id = str(outcome.get("proposalId") or "").strip()
        decision_id = str(outcome.get("decisionId") or "").strip()
        actor_id = str(outcome.get("actorId") or "").strip()
        action_id = str(outcome.get("actionTypeId") or "").strip()
        if proposal_id in outcome_proposals:
            problems.append(f"multiple outcomes reference proposal {proposal_id}")
        outcome_proposals.add(proposal_id)
        if proposal_id not in proposal_ids:
            problems.append(f"outcome {outcome_id} references missing proposal {proposal_id}")
        if decision_id not in receipt_ids:
            problems.append(f"outcome {outcome_id} references missing decision {decision_id}")
        if actor_id not in actor_ids:
            problems.append(f"outcome {outcome_id} references missing actor {actor_id}")
        if action_id not in action_ids:
            problems.append(f"outcome {outcome_id} references missing action {action_id}")
        proposal = proposal_by_id.get(proposal_id)
        requested_effects_for_outcome: list[str] = []
        if proposal is not None:
            for field, expected in (
                ("decisionId", decision_id),
                ("actorId", actor_id),
                ("actionTypeId", action_id),
            ):
                if str(proposal.get(field) or "").strip() != expected:
                    problems.append(
                        f"outcome {outcome_id}.{field} does not match proposal"
                    )
            raw_requested = proposal.get("requestedEffects")
            if isinstance(raw_requested, list):
                requested_effects_for_outcome = [
                    str(item.get("effectTypeId") or "").strip()
                    for item in raw_requested
                    if isinstance(item, Mapping)
                ]
        committed = _string_ids(
            outcome.get("committedEffectTypeIds"),
            f"outcome {outcome_id}.committedEffectTypeIds",
            problems,
        )
        _require_refs(
            committed,
            effect_ids,
            label=f"outcome {outcome_id}.committedEffectTypeIds",
            problems=problems,
        )
        consumed = outcome.get("consumedResources")
        if not isinstance(consumed, list):
            problems.append(f"outcome {outcome_id}.consumedResources must be a list")
        else:
            for index, item in enumerate(consumed):
                if not isinstance(item, Mapping):
                    problems.append(
                        f"outcome {outcome_id}.consumedResources[{index}] must be an object"
                    )
                    continue
                resource_id = str(item.get("resourceId") or "").strip()
                if resource_id not in resource_ids:
                    problems.append(
                        f"outcome {outcome_id}.consumedResources[{index}] "
                        f"references missing resource {resource_id}"
                    )
        resulting = _string_ids(
            outcome.get("resultingObservationIds"),
            f"outcome {outcome_id}.resultingObservationIds",
            problems,
        )
        _require_refs(
            resulting,
            observation_ids,
            label=f"outcome {outcome_id}.resultingObservationIds",
            problems=problems,
        )
        before = outcome.get("canonicalRevisionBefore")
        after = outcome.get("canonicalRevisionAfter")
        status = str(outcome.get("status") or "").strip()
        if not isinstance(before, int) or isinstance(before, bool):
            problems.append(f"outcome {outcome_id}.canonicalRevisionBefore must be an integer")
        if not isinstance(after, int) or isinstance(after, bool):
            problems.append(f"outcome {outcome_id}.canonicalRevisionAfter must be an integer")
        if isinstance(before, int) and isinstance(after, int):
            if status == "accepted" and after != before + 1:
                problems.append(f"accepted outcome {outcome_id} must advance revision by one")
            if status == "rejected" and after != before:
                problems.append(f"rejected outcome {outcome_id} must preserve revision")
        if (
            status == "accepted"
            and proposal is not None
            and proposal.get("canonicalRevision") != before
        ):
            problems.append(
                f"accepted outcome {outcome_id} revision does not match proposal"
            )
        if status == "accepted" and sorted(committed) != sorted(requested_effects_for_outcome):
            problems.append(
                f"accepted outcome {outcome_id} effects do not match proposal"
            )
        if status == "rejected" and (
            committed
            or resulting
            or (isinstance(consumed, list) and bool(consumed))
        ):
            problems.append(
                f"rejected outcome {outcome_id} cannot commit effects, resources, or observations"
            )

    for event_id, event in event_by_id.items():
        effect_id = str(event.get("effectTypeId") or "").strip()
        actor_id = str(event.get("actorId") or "").strip()
        proposal_id = str(event.get("proposalId") or "").strip()
        checkpoint_id = str(event.get("checkpointId") or "").strip()
        if effect_id not in effect_ids:
            problems.append(f"event {event_id} references missing effect {effect_id}")
        if actor_id not in actor_ids:
            problems.append(f"event {event_id} references missing actor {actor_id}")
        if proposal_id not in proposal_ids:
            problems.append(f"event {event_id} references missing proposal {proposal_id}")
        if checkpoint_id not in checkpoint_ids:
            problems.append(
                f"event {event_id} references missing checkpoint {checkpoint_id}"
            )

    if set(fact_state_by_id) != fact_ids:
        missing = sorted(fact_ids - set(fact_state_by_id))
        extra = sorted(set(fact_state_by_id) - fact_ids)
        if missing:
            problems.append(f"canonicalState.factStates missing facts {missing}")
        if extra:
            problems.append(f"canonicalState.factStates references unknown facts {extra}")

    for resource_id, balance in resource_balance_by_id.items():
        if resource_id not in resource_ids:
            problems.append(
                f"canonicalState.resourceBalances references missing resource {resource_id}"
            )
            continue
        quantity = balance.get("quantity")
        capacity = resource_by_id[resource_id].get("capacity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            problems.append(
                f"canonicalState resource {resource_id}.quantity must be a number"
            )
        elif float(quantity) < 0:
            problems.append(
                f"canonicalState resource {resource_id}.quantity cannot be negative"
            )
        elif isinstance(capacity, (int, float)) and not isinstance(capacity, bool):
            if float(quantity) > float(capacity):
                problems.append(
                    f"canonicalState resource {resource_id}.quantity exceeds capacity"
                )
    if set(resource_balance_by_id) != resource_ids:
        missing = sorted(resource_ids - set(resource_balance_by_id))
        extra = sorted(set(resource_balance_by_id) - resource_ids)
        if missing:
            problems.append(f"canonicalState.resourceBalances missing resources {missing}")
        if extra:
            problems.append(
                f"canonicalState.resourceBalances references unknown resources {extra}"
            )

    revision = canonical.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        problems.append("strategicAI.stateDefaults.canonicalState.revision must be a non-negative integer")

    if state.get("stateVersion") != STRATEGIC_AI_STATE_VERSION:
        problems.append(
            f"strategicAI.stateDefaults.stateVersion must be {STRATEGIC_AI_STATE_VERSION}"
        )
    current_checkpoint_id = str(state.get("currentCheckpointId") or "").strip()
    if current_checkpoint_id not in checkpoint_ids:
        problems.append(
            "strategicAI.stateDefaults.currentCheckpointId references missing "
            f"{current_checkpoint_id}"
        )

    return problems


def assert_valid_strategic_ai_definition(
    definition: Mapping[str, Any] | None,
    *,
    space_navigation: Mapping[str, Any] | None = None,
) -> None:
    """Raise ValueError when the strategic-AI definition is incoherent."""

    problems = validate_strategic_ai_definition(
        definition,
        space_navigation=space_navigation,
    )
    if problems:
        raise ValueError("Invalid strategic-AI definition:\n- " + "\n- ".join(problems))
