(function (global) {
  "use strict";

  const ACTOR_GROUP_STATUS = Object.freeze({
    unavailable: "unavailable",
    missing: "missing",
    active: "active",
    defeated: "defeated",
    mixed: "mixed"
  });

  const ENCOUNTER_STATE = Object.freeze({
    unavailable: "unavailable",
    scenarioInactive: "scenario-inactive",
    actorRuntimeUnavailable: "actor-runtime-unavailable",
    consistentActive: "consistent-active",
    recoverableActiveDefeated: "recoverable-active-defeated",
    invalidActiveActors: "invalid-active-actors",
    recoverableCompletedActive: "recoverable-completed-active",
    recoverableCompletedDefeated: "recoverable-completed-defeated",
    invalidCompletedActors: "invalid-completed-actors",
    outsideActiveStage: "outside-active-stage"
  });

  const RECOVERY_ACTION = Object.freeze({
    none: "none",
    reviveActors: "revive-actors",
    restartEncounter: "restart-encounter"
  });

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function normalizeIds(ids) {
    return arrayValue(ids)
      .map((id) => String(id || "").trim())
      .filter(Boolean);
  }

  function statusLabels(labels = {}) {
    return {
      unavailable: labels.unavailable || ACTOR_GROUP_STATUS.unavailable,
      missing: labels.missing || ACTOR_GROUP_STATUS.missing,
      active: labels.active || ACTOR_GROUP_STATUS.active,
      defeated: labels.defeated || ACTOR_GROUP_STATUS.defeated,
      mixed: labels.mixed || ACTOR_GROUP_STATUS.mixed
    };
  }

  function stateLabels(labels = {}) {
    return {
      unavailable: labels.unavailable || ENCOUNTER_STATE.unavailable,
      scenarioInactive: labels.scenarioInactive || ENCOUNTER_STATE.scenarioInactive,
      actorRuntimeUnavailable: labels.actorRuntimeUnavailable || ENCOUNTER_STATE.actorRuntimeUnavailable,
      consistentActive: labels.consistentActive || ENCOUNTER_STATE.consistentActive,
      recoverableActiveDefeated: labels.recoverableActiveDefeated || ENCOUNTER_STATE.recoverableActiveDefeated,
      invalidActiveActors: labels.invalidActiveActors || ENCOUNTER_STATE.invalidActiveActors,
      recoverableCompletedActive: labels.recoverableCompletedActive || ENCOUNTER_STATE.recoverableCompletedActive,
      recoverableCompletedDefeated: labels.recoverableCompletedDefeated || ENCOUNTER_STATE.recoverableCompletedDefeated,
      invalidCompletedActors: labels.invalidCompletedActors || ENCOUNTER_STATE.invalidCompletedActors,
      outsideActiveStage: labels.outsideActiveStage || ENCOUNTER_STATE.outsideActiveStage
    };
  }

  function recoveryLabels(labels = {}) {
    return {
      none: labels.none || RECOVERY_ACTION.none,
      reviveActors: labels.reviveActors || RECOVERY_ACTION.reviveActors,
      restartEncounter: labels.restartEncounter || RECOVERY_ACTION.restartEncounter
    };
  }

  function defaultActiveActor(character) {
    return Boolean(character
      && character.status === "active"
      && Number(character.health) > 0);
  }

  function defaultDefeatedActor(character) {
    return Boolean(character
      && (character.status === "down" || Number(character.health) <= 0));
  }

  function charactersFromRuntime(characterRuntime) {
    if (!characterRuntime?.snapshot) return null;
    const snapshot = characterRuntime.snapshot() || {};
    return objectValue(snapshot.characters);
  }

  function classifyActorGroup(actorIds, characterRuntime, options = {}) {
    const ids = normalizeIds(actorIds);
    const labels = statusLabels(options.statusLabels);
    const characters = options.characters
      ? objectValue(options.characters)
      : charactersFromRuntime(characterRuntime);

    if (!characters) {
      return {
        status: labels.unavailable,
        total: ids.length,
        activeCount: 0,
        defeatedCount: 0,
        missingCount: ids.length,
        actors: []
      };
    }

    const isActiveActor = typeof options.isActiveActor === "function"
      ? options.isActiveActor
      : defaultActiveActor;
    const isDefeatedActor = typeof options.isDefeatedActor === "function"
      ? options.isDefeatedActor
      : defaultDefeatedActor;

    const actors = ids.map((id) => {
      const character = characters[id] || null;
      const active = Boolean(character && isActiveActor(character, id));
      const defeated = Boolean(character && isDefeatedActor(character, id));
      return {
        id,
        character,
        active,
        defeated,
        missing: !character
      };
    });
    const activeCount = actors.filter((entry) => entry.active).length;
    const defeatedCount = actors.filter((entry) => entry.defeated).length;
    const missingCount = actors.filter((entry) => entry.missing).length;

    let status = labels.mixed;
    if (missingCount === ids.length) status = labels.missing;
    else if (activeCount === ids.length) status = labels.active;
    else if (defeatedCount === ids.length) status = labels.defeated;

    return {
      status,
      total: ids.length,
      activeCount,
      defeatedCount,
      missingCount,
      actors
    };
  }

  function classifyStageActorEncounter(options = {}) {
    const labels = stateLabels(options.stateLabels);
    const groupLabels = statusLabels(options.actorGroupStatusLabels);
    const recoveries = recoveryLabels(options.recoveryLabels);
    const view = options.view || null;
    const actorGroup = options.actorGroup || null;
    const stageId = String(view?.state?.stageId || "");
    const activeStageId = String(options.activeStageId || "");
    const completedStageIds = new Set(normalizeIds(options.completedStageIds));

    let status = labels.unavailable;
    let recovery = recoveries.none;

    if (!view?.visible || view.state?.status !== "active") {
      status = labels.scenarioInactive;
    } else if (actorGroup?.status === groupLabels.unavailable || !actorGroup) {
      status = labels.actorRuntimeUnavailable;
    } else if (stageId === activeStageId) {
      if (actorGroup.status === groupLabels.active) {
        status = labels.consistentActive;
      } else if (actorGroup.status === groupLabels.defeated) {
        status = labels.recoverableActiveDefeated;
        recovery = recoveries.reviveActors;
      } else {
        status = labels.invalidActiveActors;
      }
    } else if (completedStageIds.has(stageId)) {
      if (actorGroup.status === groupLabels.active) {
        status = labels.recoverableCompletedActive;
        recovery = recoveries.restartEncounter;
      } else if (actorGroup.status === groupLabels.defeated) {
        status = labels.recoverableCompletedDefeated;
        recovery = recoveries.restartEncounter;
      } else {
        status = labels.invalidCompletedActors;
      }
    } else {
      status = labels.outsideActiveStage;
    }

    return {
      status,
      recovery,
      view,
      stageId,
      actorGroup
    };
  }

  function recoveryPlan(classification, options = {}) {
    const recoveries = recoveryLabels(options.recoveryLabels);
    const groupLabels = statusLabels(options.actorGroupStatusLabels);
    const recovery = classification?.recovery || recoveries.none;
    const actorGroup = classification?.actorGroup || classification?.boarderGroup || null;
    const actorStatus = actorGroup?.status || groupLabels.unavailable;
    const recoverDefeated = options.recoverDefeated === true;

    if (recovery === recoveries.none) {
      return {
        recover: false,
        action: recovery,
        reason: classification?.status || ENCOUNTER_STATE.unavailable
      };
    }
    if (actorStatus === groupLabels.active) {
      return {
        recover: true,
        action: recovery,
        reason: classification.status
      };
    }
    if (actorStatus === groupLabels.defeated && recoverDefeated) {
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

  function reconcileStageActorEncounter(options = {}) {
    const reason = String(options.reason || "encounter-reconciliation");
    const classification = typeof options.classify === "function"
      ? options.classify(options)
      : classifyStageActorEncounter(options);
    const plan = typeof options.plan === "function"
      ? options.plan(classification, options)
      : recoveryPlan(classification, options);

    if (!plan.recover) {
      const diagnostic = typeof options.diagnostic === "function"
        ? options.diagnostic(classification, plan, options)
        : diagnosticSnapshot(classification, plan);
      return {
        recovered: false,
        reason: plan.reason,
        classification,
        plan,
        diagnostic
      };
    }

    let result = null;
    let recovered = false;
    try {
      if (typeof options.beforeRecovery === "function") {
        const before = options.beforeRecovery(classification, plan, options);
        if (before?.abort) {
          const diagnostic = typeof options.diagnostic === "function"
            ? options.diagnostic(classification, plan, options)
            : diagnosticSnapshot(classification, plan);
          return {
            recovered: false,
            reason: before.reason || plan.reason,
            classification,
            plan,
            diagnostic,
            result: before
          };
        }
      }

      result = typeof options.performRecovery === "function"
        ? options.performRecovery(plan, reason, options)
        : {forced: false, reset: false, reason: "recovery-performer-unavailable"};
      recovered = typeof options.recoverySucceeded === "function"
        ? Boolean(options.recoverySucceeded(plan, result, options))
        : Boolean(result?.recovered);
      const diagnostic = typeof options.diagnostic === "function"
        ? options.diagnostic(classification, plan, options)
        : diagnosticSnapshot(classification, plan);
      return {
        recovered,
        reason: recovered
          ? classification.status
          : result?.reason || "automatic-recovery-failed",
        classification,
        plan,
        diagnostic,
        result
      };
    } finally {
      if (typeof options.afterRecovery === "function") {
        options.afterRecovery(classification, plan, result, recovered, options);
      }
    }
  }

  function diagnosticSnapshot(classification, plan = null) {
    const actorGroup = classification?.actorGroup || classification?.boarderGroup || {};
    return {
      status: classification?.status || ENCOUNTER_STATE.unavailable,
      recovery: classification?.recovery || RECOVERY_ACTION.none,
      stageId: classification?.stageId || "",
      plan: plan ? {
        recover: Boolean(plan.recover),
        action: plan.action || RECOVERY_ACTION.none,
        reason: plan.reason || ""
      } : null,
      actors: {
        status: actorGroup.status || ACTOR_GROUP_STATUS.unavailable,
        total: Number(actorGroup.total || 0),
        active: Number(actorGroup.activeCount || 0),
        defeated: Number(actorGroup.defeatedCount || 0),
        missing: Number(actorGroup.missingCount || 0)
      }
    };
  }


  function stageActorEncounterDescriptor(descriptor = {}) {
    const actorGroupStatusLabels = statusLabels(descriptor.actorGroupStatusLabels);
    const encounterStateLabels = stateLabels(descriptor.stateLabels);
    const encounterRecoveryLabels = recoveryLabels(descriptor.recoveryLabels);
    return Object.freeze({
      scenarioId: String(descriptor.scenarioId || ""),
      systemId: String(descriptor.systemId || ""),
      activeStageId: String(descriptor.activeStageId || ""),
      completedStageIds: Object.freeze(normalizeIds(descriptor.completedStageIds)),
      actorIds: Object.freeze(normalizeIds(descriptor.actorIds)),
      actorGroupStatusLabels: Object.freeze(actorGroupStatusLabels),
      stateLabels: Object.freeze(encounterStateLabels),
      recoveryLabels: Object.freeze(encounterRecoveryLabels)
    });
  }

  function createStageActorEncounterAdapter(descriptor = {}) {
    const resolved = stageActorEncounterDescriptor(descriptor);

    function actorGroup(characterRuntime, options = {}) {
      return classifyActorGroup(
        resolved.actorIds,
        characterRuntime,
        {
          ...objectValue(options),
          statusLabels: resolved.actorGroupStatusLabels
        }
      );
    }

    function classify(options = {}) {
      const scenarioRuntime = options.scenarioRuntime || null;
      const characterRuntime = options.characterRuntime || null;
      const view = options.view || scenarioRuntime?.view?.(resolved.scenarioId) || null;
      const group = options.actorGroup || actorGroup(characterRuntime, options);
      return classifyStageActorEncounter({
        ...objectValue(options),
        view,
        actorGroup: group,
        activeStageId: resolved.activeStageId,
        completedStageIds: resolved.completedStageIds,
        actorGroupStatusLabels: resolved.actorGroupStatusLabels,
        stateLabels: resolved.stateLabels,
        recoveryLabels: resolved.recoveryLabels
      });
    }

    function plan(classification, options = {}) {
      return recoveryPlan(
        classification,
        {
          ...objectValue(options),
          actorGroupStatusLabels: resolved.actorGroupStatusLabels,
          recoveryLabels: resolved.recoveryLabels
        }
      );
    }

    function diagnostic(classification, planResult = null, extra = {}) {
      return {
        scenarioId: resolved.scenarioId,
        systemId: resolved.systemId,
        activeStageId: resolved.activeStageId,
        completedStageIds: [...resolved.completedStageIds],
        actorIds: [...resolved.actorIds],
        ...objectValue(extra),
        ...diagnosticSnapshot(classification, planResult)
      };
    }

    function reconcile(options = {}) {
      return reconcileStageActorEncounter({
        ...objectValue(options),
        classify: typeof options.classify === "function"
          ? options.classify
          : () => classify(options),
        plan: typeof options.plan === "function"
          ? options.plan
          : (classification) => plan(classification, options),
        diagnostic: typeof options.diagnostic === "function"
          ? options.diagnostic
          : (classification, planResult) => diagnostic(classification, planResult)
      });
    }

    return Object.freeze({
      ...resolved,
      descriptor: resolved,
      classifyActorGroup: actorGroup,
      classify,
      recoveryPlan: plan,
      diagnostic,
      reconcile
    });
  }


  function createStageActorEncounterReconciler(adapterOrDescriptor = {}, hooks = {}) {
    const adapter = adapterOrDescriptor?.reconcile
      ? adapterOrDescriptor
      : createStageActorEncounterAdapter(adapterOrDescriptor);
    const strategy = objectValue(hooks);

    function classify(options = {}) {
      return typeof strategy.classify === "function"
        ? strategy.classify(adapter, objectValue(options))
        : adapter.classify(options);
    }

    function plan(classification, options = {}) {
      return typeof strategy.plan === "function"
        ? strategy.plan(classification, adapter, objectValue(options))
        : adapter.recoveryPlan(classification, options);
    }

    function diagnostic(classification, planResult = null, options = {}) {
      return typeof strategy.diagnostic === "function"
        ? strategy.diagnostic(classification, planResult, adapter, objectValue(options))
        : adapter.diagnostic(classification, planResult);
    }

    function reconcile(reason = "encounter-reconciliation", options = {}) {
      const normalizedReason = String(reason || "encounter-reconciliation");
      const normalizedOptions = objectValue(options);

      return adapter.reconcile({
        ...normalizedOptions,
        reason: normalizedReason,
        classify: () => classify(normalizedOptions),
        plan: (classification) => plan(classification, normalizedOptions),
        diagnostic: (classification, planResult) => (
          diagnostic(classification, planResult, normalizedOptions)
        ),
        beforeRecovery: typeof strategy.beforeRecovery === "function"
          ? (classification, planResult) => (
            strategy.beforeRecovery(classification, planResult, normalizedOptions, adapter)
          )
          : undefined,
        afterRecovery: typeof strategy.afterRecovery === "function"
          ? (classification, planResult, result, recovered) => (
            strategy.afterRecovery(
              classification,
              planResult,
              result,
              recovered,
              normalizedOptions,
              adapter
            )
          )
          : undefined,
        performRecovery: typeof strategy.performRecovery === "function"
          ? (planResult) => strategy.performRecovery(
            planResult,
            normalizedReason,
            normalizedOptions,
            adapter
          )
          : undefined,
        recoverySucceeded: typeof strategy.recoverySucceeded === "function"
          ? (planResult, result) => strategy.recoverySucceeded(
            planResult,
            result,
            normalizedOptions,
            adapter
          )
          : undefined
      });
    }

    return Object.freeze({
      adapter,
      descriptor: adapter.descriptor || adapter,
      classify,
      recoveryPlan: plan,
      diagnostic,
      reconcile
    });
  }


  const api = {
    ACTOR_GROUP_STATUS,
    ENCOUNTER_STATE,
    RECOVERY_ACTION,
    classifyActorGroup,
    classifyStageActorEncounter,
    recoveryPlan,
    stageActorEncounterDescriptor,
    createStageActorEncounterAdapter,
    createStageActorEncounterReconciler,
    reconcileStageActorEncounter,
    diagnosticSnapshot
  };

  global.MainComputerEncounterState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
