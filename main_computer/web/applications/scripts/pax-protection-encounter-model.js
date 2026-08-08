(function (global) {
  "use strict";

  const EncounterState = global.MainComputerEncounterState
    || (typeof require === "function" ? require("./encounter-state.js") : null);
  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!EncounterState?.classifyActorGroup) {
    throw new Error("MainComputerEncounterState must load before Pax protection encounter model.");
  }
  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection encounter model.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax protection encounter model.");
  }

  const {
    objectValue,
    stringValue
  } = PaxValueUtils;

  function createPaxProtectionEncounterModel(options = {}) {
    const config = options.config || DEFAULT_CONFIG;
    const state = options.state || {};
    const SCENARIO_ID = config.ids.scenarioId;
    const PAX_SYSTEM_ID = config.ids.systemId;
    const PROTECTION_STAGE_ID = config.stages.protection;
    const INVESTIGATION_STAGE_ID = config.stages.investigation;
    const PAX_PROTECTION_ENCOUNTER_DEFINITION_ID = config.encounter.definitionId;
    const PAX_PROTECTION_ENCOUNTER_KEY = config.encounter.key;
    const PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION = config.encounter.snapshotVersion;
    const PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE = config.encounter.diagnosticInstanceSource;
    const PAX_PROTECTION_ACTIVE_STAGE_IDS = config.encounter.activeStageIds;
    const PAX_PROTECTION_COMPLETED_STAGE_IDS = config.encounter.completedStageIds;
    const PAX_PROTECTION_RECOVERY = config.recovery.actions;
    const PAX_PROTECTION_STATE = config.recovery.states;
    const PAX_RECONCILIATION_MODE = config.recovery.reconciliationModes;
    const PAX_RECONCILIATION_POLICY = config.recovery.reconciliationPolicy;
    const BOARDER_IDS = config.actors.boarderIds;

    function currentRuntime() {
      return options.currentRuntime?.()
        || state.runtime
        || global.MainComputerSystemScenarioRuntime?.current?.()
        || null;
    }

    function currentCharacterRuntime() {
      return options.currentCharacterRuntime?.()
        || state.characterRuntime
        || global.MainComputerCharacterAIRuntime?.current?.()
        || null;
    }

    function paxProtectionStateLabels() {
      return {
        unavailable: PAX_PROTECTION_STATE.unavailable,
        scenarioInactive: PAX_PROTECTION_STATE.scenarioInactive,
        actorRuntimeUnavailable: PAX_PROTECTION_STATE.characterRuntimeUnavailable,
        consistentActive: PAX_PROTECTION_STATE.consistentActive,
        recoverableActiveDefeated: PAX_PROTECTION_STATE.recoverableProtectionDefeated,
        invalidActiveActors: PAX_PROTECTION_STATE.invalidProtectionBoarders,
        recoverableCompletedActive: PAX_PROTECTION_STATE.recoverableInvestigationActive,
        recoverableCompletedDefeated: PAX_PROTECTION_STATE.recoverableInvestigationDefeated,
        invalidCompletedActors: PAX_PROTECTION_STATE.invalidInvestigationBoarders,
        outsideEncounter: PAX_PROTECTION_STATE.outsideProtection
      };
    }

    function paxProtectionRecoveryActions() {
      return {
        none: PAX_PROTECTION_RECOVERY.none,
        reviveActors: PAX_PROTECTION_RECOVERY.reviveBoarders,
        restartEncounter: PAX_PROTECTION_RECOVERY.restartEncounter
      };
    }

    function paxProtectionEncounterIdentity(diagnosticOptions = {}) {
      const view = diagnosticOptions.view || null;
      return EncounterState.encounterIdentity({
        key: PAX_PROTECTION_ENCOUNTER_KEY,
        definitionId: PAX_PROTECTION_ENCOUNTER_DEFINITION_ID,
        instanceId: diagnosticOptions.instanceId
          || diagnosticOptions.encounterInstanceId
          || diagnosticOptions.runId,
        scenarioId: SCENARIO_ID,
        systemId: PAX_SYSTEM_ID,
        view,
        stageId: diagnosticOptions.stageId || view?.state?.stageId,
        activeStageIds: PAX_PROTECTION_ACTIVE_STAGE_IDS,
        completedStageIds: PAX_PROTECTION_COMPLETED_STAGE_IDS,
        actorIds: BOARDER_IDS
      });
    }

    function paxProtectionEncounterInstanceDescriptor(diagnosticOptions = {}) {
      const identity = diagnosticOptions.identity || paxProtectionEncounterIdentity(diagnosticOptions);
      return EncounterState.encounterInstanceDescriptor({
        identity,
        instanceId: diagnosticOptions.instanceId
          || diagnosticOptions.encounterInstanceId
          || diagnosticOptions.runId,
        proposedInstanceId: diagnosticOptions.proposedInstanceId,
        proposedInstanceKey: diagnosticOptions.proposedInstanceKey,
        source: diagnosticOptions.instanceSource || PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE
      });
    }

    function classifyBoarderGroup(characterRuntime = currentCharacterRuntime()) {
      return EncounterState.classifyActorGroup({
        actorIds: BOARDER_IDS,
        actorRuntime: characterRuntime,
        actorCollectionKey: "characters",
        entriesKey: "boarders"
      });
    }

    function classifyPaxProtectionState(diagnosticOptions = {}) {
      const scenarioRuntime = diagnosticOptions.scenarioRuntime
        || currentRuntime();
      const characterRuntime = diagnosticOptions.characterRuntime
        || currentCharacterRuntime();
      const view = scenarioRuntime?.view?.(SCENARIO_ID) || null;
      const boarderGroup = classifyBoarderGroup(characterRuntime);
      const classification = EncounterState.classifyStagedEncounterState({
        scenarioRuntime,
        actorRuntime: characterRuntime,
        view,
        actorGroup: boarderGroup,
        key: PAX_PROTECTION_ENCOUNTER_KEY,
        definitionId: PAX_PROTECTION_ENCOUNTER_DEFINITION_ID,
        scenarioId: SCENARIO_ID,
        systemId: PAX_SYSTEM_ID,
        actorIds: BOARDER_IDS,
        instanceSource: PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE,
        activeStageIds: PAX_PROTECTION_ACTIVE_STAGE_IDS,
        completedStageIds: PAX_PROTECTION_COMPLETED_STAGE_IDS,
        stateLabels: paxProtectionStateLabels(),
        recoveryActions: paxProtectionRecoveryActions()
      });

      return Object.assign({}, classification, {
        scenarioRuntime,
        characterRuntime,
        actorRuntime: characterRuntime,
        view,
        identity: classification.identity || paxProtectionEncounterIdentity({view}),
        instance: classification.instance || paxProtectionEncounterInstanceDescriptor({
          identity: classification.identity || paxProtectionEncounterIdentity({view})
        }),
        boarderGroup,
        actorGroup: boarderGroup
      });
    }

    function paxProtectionReconciliationPlan(classification, diagnosticOptions = {}) {
      return EncounterState.reconciliationPlan(classification, Object.assign({}, diagnosticOptions, {
        recoveryActions: paxProtectionRecoveryActions()
      }));
    }

    function paxProtectionReconciliationOptions(
      mode = PAX_RECONCILIATION_MODE.passive,
      overrides = {}
    ) {
      const selectedMode = stringValue(mode) || PAX_RECONCILIATION_MODE.passive;
      const policy = PAX_RECONCILIATION_POLICY[selectedMode]
        || PAX_RECONCILIATION_POLICY[PAX_RECONCILIATION_MODE.passive];
      return Object.assign({}, policy, objectValue(overrides), {
        mode: selectedMode
      });
    }

    function paxProtectionEncounterSnapshotData(diagnosticOptions = {}) {
      const snapshotOptions = objectValue(diagnosticOptions);
      const reconciliationOptions = paxProtectionReconciliationOptions(
        snapshotOptions.mode,
        snapshotOptions
      );
      const classification = classifyPaxProtectionState(reconciliationOptions);
      const plan = paxProtectionReconciliationPlan(classification, reconciliationOptions);
      const snapshot = buildPaxProtectionEncounterSnapshot(
        classification,
        plan,
        reconciliationOptions
      );
      return {
        classification,
        plan,
        reconciliationOptions,
        snapshot
      };
    }

    function buildPaxProtectionEncounterSnapshot(
      classification,
      plan,
      reconciliationOptions = {}
    ) {
      const identity = classification.identity || paxProtectionEncounterIdentity({
        view: classification.view,
        stageId: classification.stageId
      });
      const instance = classification.instance || paxProtectionEncounterInstanceDescriptor({
        identity
      });
      const diagnostic = EncounterState.diagnosticSnapshot(classification, plan, {
        identity,
        instance,
        instanceSource: PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE
      });
      const completion = diagnostic.completion || EncounterState.completionDiagnostic(
        classification,
        plan,
        {identity}
      );
      const diagnosticInstance = diagnostic.instance || instance;
      const boarders = EncounterState.actorDiagnosticRows(classification.boarderGroup, {
        entriesKey: "boarders"
      });
      const boarderIds = BOARDER_IDS.slice();
      const lastAutomaticRecovery = state.lastAutomaticRecovery
        ? Object.assign({}, state.lastAutomaticRecovery)
        : null;
      const runtimeAttached = Boolean(classification.scenarioRuntime);
      const characterRuntimeAttached = Boolean(classification.characterRuntime);
      const recoveryInProgress = Boolean(state.recoveryInProgress);
      const mode = stringValue(reconciliationOptions.mode)
        || PAX_RECONCILIATION_MODE.passive;

      return Object.assign(
        diagnostic,
        {
          snapshotVersion: PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION,
          snapshotKind: "pax-protection-encounter",
          readOnly: true,
          source: "pax-protection-encounter-model",
          mode,
          encounter: identity,
          encounterInstance: diagnosticInstance,
          encounterKey: identity.key,
          encounterDefinitionId: identity.definitionId,
          encounterInstanceId: identity.instanceId,
          encounterInstanceKnown: identity.instanceKnown,
          encounterProposedInstanceId: diagnosticInstance.proposedInstanceId,
          encounterProposedInstanceKey: diagnosticInstance.proposedInstanceKey,
          encounterInstancePlaceholder: diagnosticInstance.placeholder,
          encounterInstanceDurableCommitted: diagnosticInstance.durableCommitted,
          completion,
          completionStatus: completion.status,
          completionTrusted: completion.trusted,
          completionReason: completion.reason,
          staleActorState: completion.staleActorState,
          completionIssueCodes: completion.issueCodes.slice(),
          restartableCompletionCorruption: completion.corruption,
          scenarioId: SCENARIO_ID,
          systemId: PAX_SYSTEM_ID,
          activeStageId: PROTECTION_STAGE_ID,
          completedStageId: INVESTIGATION_STAGE_ID,
          boarderIds,
          boarders,
          actors: {
            ids: boarderIds,
            status: diagnostic.actorStatus,
            total: diagnostic.total,
            activeCount: diagnostic.activeCount,
            defeatedCount: diagnostic.defeatedCount,
            missingCount: diagnostic.missingCount,
            rows: boarders
          },
          runtimes: {
            scenarioAttached: runtimeAttached,
            characterAttached: characterRuntimeAttached
          },
          reconciliation: {
            mode,
            reason: stringValue(reconciliationOptions.reason),
            recoverDefeated: Boolean(reconciliationOptions.recoverDefeated),
            markCharacterRuntime: reconciliationOptions.markCharacterRuntime !== false,
            inProgress: recoveryInProgress,
            lastAutomaticRecovery,
            plan
          },
          runtimeAttached,
          characterRuntimeAttached,
          recoveryInProgress,
          lastAutomaticRecovery
        }
      );
    }

    function paxProtectionEncounterSnapshot(diagnosticOptions = {}) {
      return paxProtectionEncounterSnapshotData(diagnosticOptions).snapshot;
    }

    function diagnosePaxProtectionEncounter(diagnosticOptions = {}) {
      return paxProtectionEncounterSnapshot(diagnosticOptions);
    }

    function paxProtectionRecoverySucceeded(plan, result) {
      return EncounterState.recoverySucceeded(plan, result, {
        successKeys: {
          [PAX_PROTECTION_RECOVERY.reviveBoarders]: "forced",
          [PAX_PROTECTION_RECOVERY.restartEncounter]: "reset"
        }
      });
    }

    return Object.freeze({
      paxProtectionStateLabels,
      paxProtectionRecoveryActions,
      paxProtectionEncounterIdentity,
      paxProtectionEncounterInstanceDescriptor,
      classifyBoarderGroup,
      classifyPaxProtectionState,
      paxProtectionReconciliationPlan,
      paxProtectionReconciliationOptions,
      paxProtectionEncounterSnapshotData,
      buildPaxProtectionEncounterSnapshot,
      paxProtectionEncounterSnapshot,
      diagnosePaxProtectionEncounter,
      paxProtectionRecoverySucceeded
    });
  }

  const api = Object.freeze({
    create: createPaxProtectionEncounterModel,
    createPaxProtectionEncounterModel
  });

  global.MainComputerPaxProtectionEncounterModel = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
