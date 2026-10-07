(function (global) {
  "use strict";

  const SCHEMA = "game.strategicAI.v1";
  const DEFINITION_VERSION = "game.strategicAI.definition.v8";
  const STATE_VERSION = "game.strategicAI.state.v8";

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function integerValue(value, fallback = 0, minimum = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.trunc(parsed));
  }

  function idSlug(value) {
    return stringValue(value)
      .replace(/[^a-zA-Z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "record";
  }

  function resolveCognitionApi() {
    if (global.MainComputerStrategicAIRuntime) {
      return global.MainComputerStrategicAIRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-runtime.js");
    }
    return null;
  }

  function resolveActionApi() {
    if (global.MainComputerStrategicAIActionRuntime) {
      return global.MainComputerStrategicAIActionRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-action-runtime.js");
    }
    return null;
  }

  function resolveSocialApi() {
    if (global.MainComputerStrategicAISocialRuntime) {
      return global.MainComputerStrategicAISocialRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-social-runtime.js");
    }
    return null;
  }

  function resolveCommitmentApi() {
    if (global.MainComputerStrategicAICommitmentRuntime) {
      return global.MainComputerStrategicAICommitmentRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-commitment-runtime.js");
    }
    return null;
  }

  function resolveDirectorApi() {
    if (global.MainComputerStrategicAIDirectorRuntime) {
      return global.MainComputerStrategicAIDirectorRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-director-runtime.js");
    }
    return null;
  }

  function resolveCommunicationApi() {
    if (global.MainComputerStrategicAICommunicationRuntime) {
      return global.MainComputerStrategicAICommunicationRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-communication-runtime.js");
    }
    return null;
  }

  class StrategicAICoordinatorError extends Error {
    constructor(message, details = []) {
      super(message);
      this.name = "StrategicAICoordinatorError";
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAITurnCoordinator {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.cognitionApi = objectValue(options.cognitionApi);
      this.actionApi = objectValue(options.actionApi);
      this.socialApi = objectValue(options.socialApi);
      this.commitmentApi = objectValue(options.commitmentApi);
      this.directorApi = objectValue(options.directorApi);
      this.communicationApi = objectValue(options.communicationApi);
      if (!Object.keys(this.cognitionApi).length) this.cognitionApi = resolveCognitionApi();
      if (!Object.keys(this.actionApi).length) this.actionApi = resolveActionApi();
      if (!Object.keys(this.socialApi).length) this.socialApi = resolveSocialApi();
      if (!Object.keys(this.commitmentApi).length) this.commitmentApi = resolveCommitmentApi();
      if (!Object.keys(this.directorApi).length) this.directorApi = resolveDirectorApi();
      if (!Object.keys(this.communicationApi).length) this.communicationApi = resolveCommunicationApi();
      if (!this.cognitionApi || !this.actionApi || !this.socialApi || !this.commitmentApi || !this.directorApi || !this.communicationApi) {
        throw new StrategicAICoordinatorError(
          "Strategic AI cognition, action, social, commitment, director, and communication runtimes must be loaded before the coordinator"
        );
      }
      [this.cognitionApi, this.actionApi, this.socialApi, this.commitmentApi, this.directorApi, this.communicationApi].forEach((api) => {
        if (api.DEFINITION_VERSION !== DEFINITION_VERSION) {
          throw new StrategicAICoordinatorError(
            `Strategic AI runtimes must use ${DEFINITION_VERSION}`
          );
        }
        if (api.STATE_VERSION !== STATE_VERSION) {
          throw new StrategicAICoordinatorError(
            `Strategic AI runtimes must use ${STATE_VERSION}`
          );
        }
      });
      if (this.definition.schema !== SCHEMA
          || this.definition.definitionVersion !== DEFINITION_VERSION
          || this.definition.stateVersion !== STATE_VERSION) {
        throw new StrategicAICoordinatorError("Invalid strategic AI coordinator definition");
      }

      this.seed = integerValue(options.seed, 1, 0);
      const initialState = options.state === undefined
        ? objectValue(this.definition.stateDefaults)
        : objectValue(options.state);
      const cognition = this.cognitionApi.create(this.definition, {
        state: initialState,
        seed: this.seed
      });
      this.state = cognition.snapshot();
    }

    updateBeliefsForObservations(state, observationIds) {
      const ids = new Set(arrayValue(observationIds).map(stringValue));
      if (!ids.size) return {state: clone(state), updatesByActor: {}};
      const cognition = this.cognitionApi.create(this.definition, {
        state,
        seed: this.seed
      });
      const idsByActor = {};
      cognition.snapshot().observations.forEach((observation) => {
        const observationId = stringValue(observation.id);
        if (!ids.has(observationId)) return;
        const actorId = stringValue(observation.observerId);
        if (!idsByActor[actorId]) idsByActor[actorId] = [];
        idsByActor[actorId].push(observationId);
      });
      const updatesByActor = {};
      Object.keys(idsByActor).sort().forEach((actorId) => {
        updatesByActor[actorId] = cognition.updateBeliefs(
          actorId,
          idsByActor[actorId]
        );
      });
      return {state: cognition.snapshot(), updatesByActor};
    }

    updateCaptainModels(state, observationIds = null) {
      const social = this.socialApi.create(this.definition, {state});
      const updates = {};
      arrayValue(this.definition.captainModelProfiles)
        .map((profile) => stringValue(objectValue(profile).actorId))
        .sort()
        .forEach((actorId) => {
          const model = social.updateCaptainModel(actorId, observationIds);
          if (model) updates[actorId] = model;
        });
      return {state: social.snapshot(), updates};
    }

    createCommitment(commitmentTypeId, promisorActorId, promiseeActorId, options = {}) {
      const commitments = this.commitmentApi.create(this.definition, {
        state: this.state
      });
      const commitment = commitments.createCommitment(
        commitmentTypeId,
        promisorActorId,
        promiseeActorId,
        objectValue(options)
      );
      this.state = commitments.snapshot();
      return clone(commitment);
    }

    cooperationMetrics(state, actorId) {
      const commitments = this.commitmentApi.create(this.definition, {state});
      return commitments.cooperationMetrics(actorId);
    }

    applyDirectorTransition(transition) {
      const observationIds = arrayValue(objectValue(transition).observations)
        .map((observation) => stringValue(objectValue(observation).id));
      const beliefs = this.updateBeliefsForObservations(this.state, observationIds);
      this.state = beliefs.state;
      return {
        opportunity: clone(objectValue(transition).opportunity),
        opportunityState: clone(objectValue(transition).opportunityState),
        receipt: clone(objectValue(transition).receipt),
        observationIds,
        beliefUpdatesByActor: clone(beliefs.updatesByActor),
        state: clone(this.state)
      };
    }

    activateCampaignRoute(routeSystemId, options = {}) {
      const director = this.directorApi.create(this.definition, {
        state: this.state
      });
      const transition = director.activateRoute(
        routeSystemId,
        objectValue(options)
      );
      this.state = director.snapshot();
      return this.applyDirectorTransition(transition);
    }

    deactivateCampaignOpportunity(opportunityId, options = {}) {
      const director = this.directorApi.create(this.definition, {
        state: this.state
      });
      const transition = director.deactivateOpportunity(
        opportunityId,
        objectValue(options)
      );
      this.state = director.snapshot();
      return this.applyDirectorTransition(transition);
    }

    expireCampaignOpportunities(worldTime) {
      const director = this.directorApi.create(this.definition, {
        state: this.state
      });
      const transitions = director.expireOpportunities(worldTime);
      this.state = director.snapshot();
      return transitions.map((transition) => this.applyDirectorTransition(transition));
    }

    deliverReport(routeId, senderActorId, recipientActorId, sourceObservationId, options = {}) {
      const social = this.socialApi.create(this.definition, {state: this.state});
      const delivery = social.createReport(
        routeId,
        senderActorId,
        recipientActorId,
        sourceObservationId,
        objectValue(options)
      );
      this.state = social.snapshot();
      const observationId = stringValue(objectValue(delivery).observation.id);
      const captain = this.updateCaptainModels(this.state, [observationId]);
      this.state = captain.state;
      const beliefs = this.updateBeliefsForObservations(this.state, [observationId]);
      this.state = beliefs.state;
      return {
        report: clone(objectValue(delivery).report),
        observation: clone(objectValue(delivery).observation),
        captainModelUpdates: clone(captain.updates),
        beliefUpdatesByActor: clone(beliefs.updatesByActor),
        state: clone(this.state)
      };
    }

    performCommunication(intentId, speakerActorId, audienceActorIds, options = {}) {
      const before = JSON.stringify(this.state);
      const runtime = this.communicationApi.create(this.definition, {
        state: this.state,
        modelAdapter: objectValue(options).modelAdapter || null
      });
      const result = runtime.perform(
        intentId,
        speakerActorId,
        audienceActorIds,
        objectValue(options)
      );
      if (JSON.stringify(runtime.snapshot()) !== before) {
        throw new StrategicAICoordinatorError(
          "Communication runtime attempted to mutate strategic state"
        );
      }
      return clone(result);
    }

    runTurn(actorId, options = {}) {
      const id = stringValue(actorId);
      if (!id) throw new StrategicAICoordinatorError("Turn actor id is required");
      const beforeObservationIds = new Set(
        arrayValue(objectValue(this.state).observations)
          .map((observation) => stringValue(objectValue(observation).id))
      );

      let cognition = this.cognitionApi.create(this.definition, {
        state: this.state,
        seed: this.seed
      });
      arrayValue(objectValue(options).observations).forEach((observation) => {
        cognition.ingestObservation(observation);
      });
      let workingState = cognition.snapshot();

      const social = this.socialApi.create(this.definition, {state: workingState});
      const explicitReports = [];
      arrayValue(objectValue(options).reports).forEach((request) => {
        const raw = objectValue(request);
        explicitReports.push(
          social.createReport(
            raw.routeId,
            raw.senderActorId,
            raw.recipientActorId,
            raw.sourceObservationId,
            objectValue(raw.options)
          )
        );
      });
      const publicDeliveries = [];
      arrayValue(objectValue(options).publicObservations).forEach((request) => {
        const raw = typeof request === "string"
          ? {senderActorId: id, observationId: request}
          : objectValue(request);
        publicDeliveries.push(...social.propagatePublicObservation(
          raw.senderActorId,
          raw.observationId
        ));
      });
      workingState = social.snapshot();

      const incomingObservationIds = arrayValue(workingState.observations)
        .map((observation) => stringValue(objectValue(observation).id))
        .filter((observationId) => !beforeObservationIds.has(observationId));

      const captainBefore = this.updateCaptainModels(
        workingState,
        incomingObservationIds
      );
      workingState = captainBefore.state;
      const incomingBeliefs = this.updateBeliefsForObservations(
        workingState,
        incomingObservationIds
      );
      workingState = incomingBeliefs.state;

      const socialForMetrics = this.socialApi.create(this.definition, {
        state: workingState
      });
      const captainMetrics = socialForMetrics.captainMetrics(id);
      const commitmentMetrics = this.cooperationMetrics(workingState, id);
      const decisionContext = clone(objectValue(objectValue(options).decisionContext));
      decisionContext.metricOverrides = {
        ...captainMetrics,
        ...commitmentMetrics,
        ...objectValue(decisionContext.metricOverrides)
      };

      cognition = this.cognitionApi.create(this.definition, {
        state: workingState,
        seed: this.seed
      });
      const decision = cognition.decide(
        id,
        objectValue(options).checkpointId || null,
        decisionContext
      );

      const actionRuntime = this.actionApi.create(this.definition, {
        state: cognition.snapshot()
      });
      const proposal = actionRuntime.createProposal(
        decision,
        objectValue(objectValue(options).parameters),
        objectValue(objectValue(options).proposalOptions)
      );
      const outcome = actionRuntime.commitProposal(proposal);
      workingState = actionRuntime.snapshot();

      const commitmentRuntime = this.commitmentApi.create(this.definition, {
        state: workingState
      });
      const commitmentResolutions = commitmentRuntime.evaluateOutcome(
        outcome,
        {resolvedAt: outcome.canonicalRevisionAfter}
      );
      workingState = commitmentRuntime.snapshot();
      const commitmentObservationIds = commitmentResolutions.flatMap(
        (resolution) => arrayValue(objectValue(resolution).commitment.observationIds).map(stringValue)
      );

      const propagatedResultDeliveries = [];
      let resultObservationIds = [
        ...arrayValue(outcome.resultingObservationIds).map(stringValue),
        ...commitmentObservationIds
      ];
      if (outcome.status === "accepted" && resultObservationIds.length) {
        const resultSocial = this.socialApi.create(this.definition, {
          state: workingState
        });
        const resultById = new Map(
          arrayValue(workingState.observations).map(
            (observation) => [stringValue(objectValue(observation).id), observation]
          )
        );
        resultObservationIds.slice().sort().forEach((observationId) => {
          const observation = resultById.get(observationId);
          if (!observation || stringValue(observation.visibility) !== "public") return;
          propagatedResultDeliveries.push(
            ...resultSocial.propagatePublicObservation(
              observation.observerId,
              observationId
            )
          );
        });
        workingState = resultSocial.snapshot();
        resultObservationIds = [
          ...resultObservationIds,
          ...propagatedResultDeliveries.map(
            (delivery) => stringValue(objectValue(delivery).observation.id)
          )
        ];
      }

      const captainAfter = this.updateCaptainModels(
        workingState,
        resultObservationIds
      );
      workingState = captainAfter.state;
      const resultingBeliefs = outcome.status === "accepted"
        ? this.updateBeliefsForObservations(workingState, resultObservationIds)
        : {state: workingState, updatesByActor: {}};
      workingState = resultingBeliefs.state;

      this.state = workingState;
      const explicitReportIds = explicitReports.map(
        (delivery) => stringValue(objectValue(delivery).report.reportId)
      );
      const propagatedReportIds = [
        ...publicDeliveries,
        ...propagatedResultDeliveries
      ].map((delivery) => stringValue(objectValue(delivery).report.reportId));

      return {
        turnId: `turn.runtime.${idSlug(proposal.proposalId)}`,
        actorId: id,
        policyProfileId: decision.policyProfileId,
        checkpointId: decision.checkpointId,
        canonicalRevisionBefore: decision.canonicalRevision,
        canonicalRevisionAfter: outcome.canonicalRevisionAfter,
        incomingObservationIds,
        explicitReportIds,
        propagatedReportIds,
        captainMetrics: clone(captainMetrics),
        commitmentMetrics: clone(commitmentMetrics),
        commitmentResolutions: clone(commitmentResolutions),
        captainModelUpdatesBeforeDecision: clone(captainBefore.updates),
        incomingBeliefUpdates: clone(incomingBeliefs.updatesByActor[id] || []),
        incomingBeliefUpdatesByActor: clone(incomingBeliefs.updatesByActor),
        decision: clone(decision),
        proposal: clone(proposal),
        outcome: clone(outcome),
        resultingObservationIds: clone(resultObservationIds),
        captainModelUpdatesAfterAction: clone(captainAfter.updates),
        resultingBeliefUpdates: clone(resultingBeliefs.updatesByActor[id] || []),
        resultingBeliefUpdatesByActor: clone(resultingBeliefs.updatesByActor),
        state: clone(this.state)
      };
    }

    snapshot() {
      return clone(this.state);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAITurnCoordinator(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    StrategicAICoordinatorError,
    StrategicAITurnCoordinator,
    create
  };

  global.MainComputerStrategicAICoordinator = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
