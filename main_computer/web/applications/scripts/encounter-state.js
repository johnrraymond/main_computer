(function (global) {
  "use strict";

  const ACTOR_GROUP_STATUS = Object.freeze({
    unavailable: "unavailable",
    missing: "missing",
    active: "active",
    defeated: "defeated",
    mixed: "mixed"
  });

  const ENCOUNTER_STATE_STATUS = Object.freeze({
    unavailable: "unavailable",
    scenarioInactive: "scenario-inactive",
    actorRuntimeUnavailable: "actor-runtime-unavailable",
    consistentActive: "consistent-active",
    recoverableActiveDefeated: "recoverable-active-defeated",
    invalidActiveActors: "invalid-active-actors",
    recoverableCompletedActive: "recoverable-completed-active",
    recoverableCompletedDefeated: "recoverable-completed-defeated",
    invalidCompletedActors: "invalid-completed-actors",
    outsideEncounter: "outside-encounter"
  });

  const RECOVERY_ACTION = Object.freeze({
    none: "none",
    reviveActors: "revive-actors",
    restartEncounter: "restart-encounter"
  });

  const COMPLETION_STATUS = Object.freeze({
    notCompleted: "not-completed",
    completedTrusted: "completed-trusted",
    completedButUntrusted: "completed-but-untrusted"
  });

  const STALE_STATE_REASON = Object.freeze({
    none: "none",
    notCompleted: "not-completed",
    durableInstanceMissing: "durable-instance-missing",
    actorRuntimeUnavailable: "actor-runtime-unavailable",
    staleActiveActors: "stale-active-actors",
    staleDefeatedActors: "stale-defeated-actors",
    staleMixedActors: "stale-mixed-actors",
    staleMissingActors: "stale-missing-actors",
    restartableCorruption: "restartable-corruption"
  });

  const ENCOUNTER_INSTANCE_STATUS = Object.freeze({
    known: "known",
    placeholder: "placeholder"
  });

  function isPlainObject(value) {
    return Boolean(value && typeof value === "object" && !Array.isArray(value));
  }

  function objectValue(value) {
    return isPlainObject(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function stringValue(value) {
    return typeof value === "string" ? value : "";
  }

  function numberValue(value, fallback = 0) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
  }

  function uniqueStrings(values) {
    const seen = new Set();
    const result = [];
    arrayValue(values).forEach((value) => {
      const text = stringValue(value).trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      result.push(text);
    });
    return result;
  }

  function encounterIdentity(rawOptions = {}) {
    const options = objectValue(rawOptions);
    const view = objectValue(options.view);
    const state = objectValue(view.state);
    const definitionId = stringValue(
      options.definitionId
      || options.encounterDefinitionId
      || options.encounterId
      || options.id
    );
    const key = stringValue(options.key) || definitionId;
    const instanceId = stringValue(
      options.instanceId
      || options.encounterInstanceId
      || options.runId
    );
    return {
      key,
      definitionId,
      instanceId: instanceId || null,
      instanceKnown: Boolean(instanceId),
      scenarioId: stringValue(options.scenarioId),
      systemId: stringValue(options.systemId),
      stageId: stringValue(options.stageId || state.stageId),
      activeStageIds: uniqueStrings([
        ...stageList(options.activeStageId, options.activeStageIds)
      ]),
      completedStageIds: uniqueStrings([
        ...stageList(options.completedStageId, options.completedStageIds)
      ]),
      actorIds: uniqueStrings(options.actorIds)
    };
  }

  function stableKeyPart(value, fallback) {
    const text = stringValue(value).trim();
    if (!text) return fallback;
    const cleaned = text
      .replace(/[^A-Za-z0-9._:-]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return cleaned || fallback;
  }

  function proposedEncounterInstanceId(identity, options = {}) {
    const explicit = stringValue(
      options.proposedInstanceId
      || options.proposedInstanceKey
      || options.pendingInstanceId
      || options.pendingInstanceKey
    );
    if (explicit) return explicit;
    const encounterKey = stableKeyPart(
      identity.key || identity.definitionId,
      "encounter.unknown"
    );
    const lifecycleStage = stableKeyPart(
      identity.activeStageIds?.[0] || identity.stageId,
      "stage.unknown"
    );
    return `${encounterKey}:instance:${lifecycleStage}:pending`;
  }

  function encounterInstanceDescriptor(rawOptions = {}) {
    const options = objectValue(rawOptions);
    const identity = encounterIdentity(Object.assign(
      {},
      objectValue(options.identity),
      options
    ));
    const instanceId = stringValue(
      identity.instanceId
      || options.instanceId
      || options.encounterInstanceId
      || options.runId
    );
    const instanceKnown = Boolean(instanceId);
    const proposedInstanceId = instanceKnown
      ? instanceId
      : proposedEncounterInstanceId(identity, options);
    return {
      status: instanceKnown
        ? ENCOUNTER_INSTANCE_STATUS.known
        : ENCOUNTER_INSTANCE_STATUS.placeholder,
      key: identity.key,
      definitionId: identity.definitionId,
      instanceId: instanceId || null,
      instanceKnown,
      proposedInstanceId,
      proposedInstanceKey: proposedInstanceId,
      placeholder: !instanceKnown,
      durable: instanceKnown,
      durableCommitted: instanceKnown,
      source: instanceKnown
        ? "durable-instance"
        : stringValue(options.source) || "diagnostic-placeholder",
      scenarioId: identity.scenarioId,
      systemId: identity.systemId,
      stageId: identity.stageId,
      activeStageIds: identity.activeStageIds.slice(),
      completedStageIds: identity.completedStageIds.slice(),
      actorIds: identity.actorIds.slice()
    };
  }

  function defaultActorIsActive(actor) {
    return Boolean(actor && actor.status === "active" && Number(actor.health) > 0);
  }

  function defaultActorIsDefeated(actor) {
    return Boolean(actor && (actor.status === "down" || Number(actor.health) <= 0));
  }

  function actorSnapshot(actorRuntime, explicitSnapshot) {
    if (isPlainObject(explicitSnapshot)) return explicitSnapshot;
    if (actorRuntime && typeof actorRuntime.snapshot === "function") {
      return objectValue(actorRuntime.snapshot());
    }
    return null;
  }

  function actorCollection(snapshot, options) {
    const actorCollectionKey = stringValue(options.actorCollectionKey);
    if (isPlainObject(options.actorsById)) return options.actorsById;
    if (!isPlainObject(snapshot)) return {};
    if (actorCollectionKey) return objectValue(snapshot[actorCollectionKey]);
    return objectValue(snapshot.characters || snapshot.actors);
  }

  function classifyActorGroup(rawOptions = {}) {
    const options = objectValue(rawOptions);
    const actorIds = uniqueStrings(options.actorIds);
    const total = actorIds.length;
    const actorRuntime = options.actorRuntime || options.runtime || null;
    const snapshot = actorSnapshot(actorRuntime, options.snapshot);
    const entriesKey = stringValue(options.entriesKey) || "actors";
    const actorsById = actorCollection(snapshot, options);
    const isActiveActor = typeof options.isActiveActor === "function"
      ? options.isActiveActor
      : defaultActorIsActive;
    const isDefeatedActor = typeof options.isDefeatedActor === "function"
      ? options.isDefeatedActor
      : defaultActorIsDefeated;

    if (!snapshot || !total) {
      const empty = {
        status: ACTOR_GROUP_STATUS.unavailable,
        total,
        activeCount: 0,
        defeatedCount: 0,
        missingCount: total,
        actors: []
      };
      empty[entriesKey] = empty.actors;
      return empty;
    }

    const actors = actorIds.map((actorId) => {
      const actor = actorsById[actorId] || null;
      const active = Boolean(actor && isActiveActor(actor, actorId));
      const defeated = Boolean(actor && isDefeatedActor(actor, actorId));
      return {
        id: actorId,
        actor,
        character: actor,
        active,
        defeated,
        missing: !actor
      };
    });
    const activeCount = actors.filter((entry) => entry.active).length;
    const defeatedCount = actors.filter((entry) => entry.defeated).length;
    const missingCount = actors.filter((entry) => entry.missing).length;

    let status = ACTOR_GROUP_STATUS.mixed;
    if (missingCount === total) status = ACTOR_GROUP_STATUS.missing;
    else if (activeCount === total) status = ACTOR_GROUP_STATUS.active;
    else if (defeatedCount === total) status = ACTOR_GROUP_STATUS.defeated;

    const result = {
      status,
      total,
      activeCount,
      defeatedCount,
      missingCount,
      actors
    };
    result[entriesKey] = actors;
    return result;
  }

  function actorDiagnosticRows(rawActorGroup, rawOptions = {}) {
    const actorGroup = objectValue(rawActorGroup);
    const options = objectValue(rawOptions);
    const entriesKey = stringValue(options.entriesKey);
    const entries = entriesKey
      ? arrayValue(actorGroup[entriesKey])
      : arrayValue(actorGroup.actors);
    const actorEntries = entries.length ? entries : arrayValue(actorGroup.actors);

    return actorEntries.map((entry) => {
      const row = objectValue(entry);
      const actor = objectValue(row.actor || row.character);
      return {
        id: stringValue(row.id),
        status: stringValue(actor.status || row.status),
        health: numberValue(actor.health ?? row.health, 0),
        active: Boolean(row.active),
        defeated: Boolean(row.defeated),
        missing: Boolean(row.missing)
      };
    });
  }

  function stageList(primary, values) {
    return uniqueStrings([
      ...(primary ? [primary] : []),
      ...arrayValue(values)
    ]);
  }

  function mappedStateLabels(labels = {}) {
    return Object.assign({}, ENCOUNTER_STATE_STATUS, objectValue(labels));
  }

  function mappedRecoveryActions(actions = {}) {
    return Object.assign({}, RECOVERY_ACTION, objectValue(actions));
  }

  function scenarioView(scenarioRuntime, scenarioId, explicitView) {
    if (isPlainObject(explicitView)) return explicitView;
    if (scenarioRuntime && typeof scenarioRuntime.view === "function") {
      return scenarioRuntime.view(scenarioId) || null;
    }
    return null;
  }

  function classifyStagedEncounterState(rawOptions = {}) {
    const options = objectValue(rawOptions);
    const stateLabels = mappedStateLabels(options.stateLabels);
    const recoveryActions = mappedRecoveryActions(options.recoveryActions);
    const scenarioRuntime = options.scenarioRuntime || options.runtime || null;
    const scenarioId = stringValue(options.scenarioId);
    const view = scenarioView(scenarioRuntime, scenarioId, options.view);
    const actorRuntime = options.actorRuntime || options.characterRuntime || null;
    const actorGroup = objectValue(options.actorGroup).status
      ? options.actorGroup
      : classifyActorGroup({
        actorIds: options.actorIds,
        actorRuntime,
        snapshot: options.actorSnapshot,
        actorCollectionKey: options.actorCollectionKey,
        entriesKey: options.entriesKey,
        actorsById: options.actorsById,
        isActiveActor: options.isActiveActor,
        isDefeatedActor: options.isDefeatedActor
      });
    const activeStageIds = stageList(options.activeStageId, options.activeStageIds);
    const completedStageIds = stageList(options.completedStageId, options.completedStageIds);
    const stageId = stringValue(view?.state?.stageId);
    const actorStatus = actorGroup?.status || ACTOR_GROUP_STATUS.unavailable;
    const identityOptions = Object.assign({}, objectValue(options.identity), {
      scenarioId,
      systemId: options.systemId,
      view,
      stageId,
      activeStageIds,
      completedStageIds,
      actorIds: options.actorIds
    });
    const definitionId = stringValue(
      options.definitionId
      || options.encounterDefinitionId
      || options.encounterId
    );
    const key = stringValue(options.key);
    const instanceId = stringValue(
      options.instanceId
      || options.encounterInstanceId
      || options.runId
    );
    if (definitionId) identityOptions.definitionId = definitionId;
    if (key) identityOptions.key = key;
    if (instanceId) identityOptions.instanceId = instanceId;
    const identity = encounterIdentity(identityOptions);
    const instance = encounterInstanceDescriptor(Object.assign(
      {},
      objectValue(options.instance),
      {
        identity,
        proposedInstanceId: options.proposedInstanceId,
        proposedInstanceKey: options.proposedInstanceKey,
        source: options.instanceSource
      }
    ));

    let status = stateLabels.unavailable;
    let recovery = recoveryActions.none;
    let stageClass = "unavailable";

    if (!view?.visible || view.state?.status !== "active") {
      status = stateLabels.scenarioInactive;
      stageClass = "inactive";
    } else if (actorStatus === ACTOR_GROUP_STATUS.unavailable) {
      status = stateLabels.actorRuntimeUnavailable;
      stageClass = "actor-runtime-unavailable";
    } else if (activeStageIds.includes(stageId)) {
      stageClass = "active";
      if (actorStatus === ACTOR_GROUP_STATUS.active) {
        status = stateLabels.consistentActive;
      } else if (actorStatus === ACTOR_GROUP_STATUS.defeated) {
        status = stateLabels.recoverableActiveDefeated;
        recovery = recoveryActions.reviveActors;
      } else {
        status = stateLabels.invalidActiveActors;
      }
    } else if (completedStageIds.includes(stageId)) {
      stageClass = "completed";
      if (actorStatus === ACTOR_GROUP_STATUS.active) {
        status = stateLabels.recoverableCompletedActive;
        recovery = recoveryActions.restartEncounter;
      } else if (actorStatus === ACTOR_GROUP_STATUS.defeated) {
        status = stateLabels.recoverableCompletedDefeated;
        recovery = recoveryActions.restartEncounter;
      } else {
        status = stateLabels.invalidCompletedActors;
      }
    } else {
      status = stateLabels.outsideEncounter;
      stageClass = "outside";
    }

    return {
      status,
      recovery,
      scenarioRuntime,
      actorRuntime,
      view,
      stageId,
      stageClass,
      identity,
      instance,
      actorGroup
    };
  }

  function reconciliationPlan(rawClassification, rawOptions = {}) {
    const classification = objectValue(rawClassification);
    const options = objectValue(rawOptions);
    const recoveryActions = mappedRecoveryActions(options.recoveryActions);
    const recovery = classification.recovery || recoveryActions.none;
    const actorStatus = classification.actorGroup?.status || ACTOR_GROUP_STATUS.unavailable;
    const recoverDefeated = options.recoverDefeated === true;

    if (recovery === recoveryActions.none) {
      return {
        recover: false,
        action: recovery,
        reason: classification.status || ENCOUNTER_STATE_STATUS.unavailable
      };
    }
    if (actorStatus === ACTOR_GROUP_STATUS.active) {
      return {
        recover: true,
        action: recovery,
        reason: classification.status
      };
    }
    if (actorStatus === ACTOR_GROUP_STATUS.defeated && recoverDefeated) {
      return {
        recover: true,
        action: recovery,
        reason: classification.status
      };
    }
    return {
      recover: false,
      action: recovery,
      reason: classification.status
    };
  }

  function recoverySucceeded(rawPlan, rawResult, rawOptions = {}) {
    const plan = objectValue(rawPlan);
    const result = objectValue(rawResult);
    const options = objectValue(rawOptions);
    const successKeys = objectValue(options.successKeys);
    const key = successKeys[plan.action];
    if (key) return Boolean(result[key]);
    return Boolean(result.recovered || result.success || result.reset || result.forced);
  }

  function staleActorReason(actorStatus, instanceKnown) {
    if (instanceKnown) return STALE_STATE_REASON.none;
    if (actorStatus === ACTOR_GROUP_STATUS.active) {
      return STALE_STATE_REASON.staleActiveActors;
    }
    if (actorStatus === ACTOR_GROUP_STATUS.defeated) {
      return STALE_STATE_REASON.staleDefeatedActors;
    }
    if (actorStatus === ACTOR_GROUP_STATUS.mixed) {
      return STALE_STATE_REASON.staleMixedActors;
    }
    if (actorStatus === ACTOR_GROUP_STATUS.missing) {
      return STALE_STATE_REASON.staleMissingActors;
    }
    if (actorStatus === ACTOR_GROUP_STATUS.unavailable) {
      return STALE_STATE_REASON.actorRuntimeUnavailable;
    }
    return STALE_STATE_REASON.none;
  }

  function completionDiagnostic(rawClassification, rawPlan = null, rawOptions = {}) {
    const classification = objectValue(rawClassification);
    const plan = rawPlan ? objectValue(rawPlan) : null;
    const options = objectValue(rawOptions);
    const actorGroup = objectValue(classification.actorGroup);
    const actorStatus = actorGroup.status || ACTOR_GROUP_STATUS.unavailable;
    const stageClass = stringValue(classification.stageClass || options.stageClass);
    const identity = encounterIdentity(Object.assign(
      {},
      objectValue(classification.identity),
      objectValue(options.identity),
      {
        stageId: classification.stageId || options.stageId,
        view: classification.view || options.view
      }
    ));
    const completed = stageClass === "completed";
    const issueCodes = [];

    if (!completed) {
      return {
        status: COMPLETION_STATUS.notCompleted,
        completed: false,
        trusted: false,
        reason: STALE_STATE_REASON.notCompleted,
        staleActorState: STALE_STATE_REASON.none,
        restartable: false,
        corruption: STALE_STATE_REASON.none,
        issueCodes
      };
    }

    const trusted = Boolean(identity.instanceKnown);
    const reason = trusted
      ? STALE_STATE_REASON.none
      : STALE_STATE_REASON.durableInstanceMissing;
    const staleActorState = staleActorReason(actorStatus, trusted);
    const restartable = Boolean(
      classification.recovery
      && classification.recovery !== RECOVERY_ACTION.none
      && (
        !plan
        || plan.action === classification.recovery
        || plan.action === RECOVERY_ACTION.restartEncounter
      )
    );
    const corruption = !trusted && restartable
      ? STALE_STATE_REASON.restartableCorruption
      : STALE_STATE_REASON.none;

    if (reason !== STALE_STATE_REASON.none) issueCodes.push(reason);
    if (staleActorState !== STALE_STATE_REASON.none) issueCodes.push(staleActorState);
    if (corruption !== STALE_STATE_REASON.none) issueCodes.push(corruption);

    return {
      status: trusted
        ? COMPLETION_STATUS.completedTrusted
        : COMPLETION_STATUS.completedButUntrusted,
      completed: true,
      trusted,
      reason,
      staleActorState,
      restartable,
      corruption,
      issueCodes
    };
  }

  function diagnosticSnapshot(rawClassification, rawPlan = null, rawOptions = {}) {
    const classification = objectValue(rawClassification);
    const plan = rawPlan ? objectValue(rawPlan) : null;
    const options = objectValue(rawOptions);
    const actorGroup = objectValue(classification.actorGroup);
    const identity = encounterIdentity(Object.assign(
      {},
      objectValue(classification.identity),
      objectValue(options.identity),
      {
        stageId: classification.stageId || options.stageId,
        view: classification.view || options.view
      }
    ));
    const instance = encounterInstanceDescriptor(Object.assign(
      {},
      objectValue(classification.instance),
      objectValue(options.instance),
      {
        identity,
        proposedInstanceId: options.proposedInstanceId,
        proposedInstanceKey: options.proposedInstanceKey,
        source: options.instanceSource
      }
    ));
    const completion = completionDiagnostic(classification, plan, {identity});
    return {
      encounter: identity,
      instance,
      status: classification.status || ENCOUNTER_STATE_STATUS.unavailable,
      recovery: classification.recovery || RECOVERY_ACTION.none,
      stageId: classification.stageId || "",
      stageClass: classification.stageClass || "unavailable",
      actorStatus: actorGroup.status || ACTOR_GROUP_STATUS.unavailable,
      total: numberValue(actorGroup.total, 0),
      activeCount: numberValue(actorGroup.activeCount, 0),
      defeatedCount: numberValue(actorGroup.defeatedCount, 0),
      missingCount: numberValue(actorGroup.missingCount, 0),
      completion,
      plan: plan
        ? {
          recover: Boolean(plan.recover),
          action: plan.action || RECOVERY_ACTION.none,
          reason: plan.reason || ""
        }
        : null
    };
  }

  const api = {
    ACTOR_GROUP_STATUS,
    ENCOUNTER_STATE_STATUS,
    RECOVERY_ACTION,
    COMPLETION_STATUS,
    STALE_STATE_REASON,
    ENCOUNTER_INSTANCE_STATUS,
    encounterIdentity,
    encounterInstanceDescriptor,
    classifyActorGroup,
    actorDiagnosticRows,
    classifyStagedEncounterState,
    reconciliationPlan,
    recoverySucceeded,
    completionDiagnostic,
    diagnosticSnapshot
  };

  global.MainComputerEncounterState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
