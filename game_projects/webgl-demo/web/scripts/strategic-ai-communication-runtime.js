(function (global) {
  "use strict";

  const SCHEMA = "game.strategicAI.v1";
  const DEFINITION_VERSION = "game.strategicAI.definition.v8";
  const STATE_VERSION = "game.strategicAI.state.v8";
  const LEGACY_STATE_VERSIONS = Object.freeze([
    "game.strategicAI.state.v1",
    "game.strategicAI.state.v2",
    "game.strategicAI.state.v3",
    "game.strategicAI.state.v4",
    "game.strategicAI.state.v5",
    "game.strategicAI.state.v6",
    "game.strategicAI.state.v7"
  ]);

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

  function finiteNumber(value, fallback = 0, minimum = -Infinity, maximum = Infinity) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
  }

  function probability(value, fallback = 0) {
    return finiteNumber(value, fallback, 0, 1);
  }

  function integerValue(value, fallback = 0, minimum = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.trunc(parsed));
  }

  function stableStringify(value) {
    if (Array.isArray(value)) {
      return `[${value.map((item) => stableStringify(item)).join(",")}]`;
    }
    if (value && typeof value === "object") {
      const entries = Object.keys(value)
        .sort()
        .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`);
      return `{${entries.join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function hashString(value) {
    const text = String(value);
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function idSlug(value) {
    return stringValue(value)
      .replace(/[^a-zA-Z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "record";
  }

  function indexById(records, key = "id") {
    const result = new Map();
    arrayValue(records).forEach((record) => {
      const raw = objectValue(record);
      const id = stringValue(raw[key]);
      if (id && !result.has(id)) result.set(id, raw);
    });
    return result;
  }


  function defaultOffscreenStepStates(definition) {
    const routes = indexById(objectValue(definition).reportRoutes);
    return arrayValue(objectValue(definition).offscreenSchedules).flatMap((schedule) => (
      arrayValue(objectValue(schedule).steps).map((step) => {
        const raw = objectValue(step);
        let readyAt = integerValue(raw.dueAt, 0, 0);
        if (stringValue(raw.kind) === "report") {
          const route = routes.get(stringValue(raw.routeId));
          readyAt += integerValue(objectValue(route).latency, 0, 0);
        }
        if (
          stringValue(raw.kind) === "actor-turn"
          && raw.deadlineAt !== null
          && raw.deadlineAt !== undefined
        ) {
          readyAt = Math.max(readyAt, integerValue(raw.deadlineAt, readyAt, 0));
        }
        return {
          scheduleId: stringValue(objectValue(schedule).id),
          stepId: stringValue(raw.id),
          status: "pending",
          attempts: 0,
          readyAt,
          completedAt: null,
          resultIds: [],
          reason: ""
        };
      })
    ));
  }


  function propositionsEqual(left, right) {
    return stableStringify(objectValue(left)) === stableStringify(objectValue(right));
  }

  function migrateState(value, definition) {
    const state = clone(objectValue(value));
    if (!LEGACY_STATE_VERSIONS.includes(stringValue(state.stateVersion))) return state;
    if (!Array.isArray(state.offscreenStepStates)) {
      state.offscreenStepStates = defaultOffscreenStepStates(definition);
    }
    if (!Array.isArray(state.offscreenSimulationReceipts)) {
      state.offscreenSimulationReceipts = [];
    }
    if (!Number.isInteger(state.offscreenSimulationTime) || state.offscreenSimulationTime < 0) {
      state.offscreenSimulationTime = 0;
    }
    state.stateVersion = STATE_VERSION;
    return state;
  }

  function validateDefinition(value) {
    const definition = objectValue(value);
    const errors = [];
    if (definition.schema !== SCHEMA) errors.push(`schema must be ${SCHEMA}`);
    if (definition.definitionVersion !== DEFINITION_VERSION) {
      errors.push(`definitionVersion must be ${DEFINITION_VERSION}`);
    }
    if (definition.stateVersion !== STATE_VERSION) {
      errors.push(`stateVersion must be ${STATE_VERSION}`);
    }
    if (!arrayValue(definition.communicationClaims).length) {
      errors.push("communicationClaims must be a non-empty list");
    }
    if (!arrayValue(definition.communicativeIntents).length) {
      errors.push("communicativeIntents must be a non-empty list");
    }
    if (!arrayValue(definition.speechTemplates).length) {
      errors.push("speechTemplates must be a non-empty list");
    }
    if (!Number.isInteger(definition.offscreenSimulationBudget)
        || definition.offscreenSimulationBudget < 1) {
      errors.push("offscreenSimulationBudget must be a positive integer");
    }
    if (!arrayValue(definition.offscreenSchedules).length) {
      errors.push("offscreenSchedules must be a non-empty list");
    }
    return {valid: errors.length === 0, errors};
  }

  class StrategicAICommunicationError extends Error {
    constructor(message, code = "communication-invalid", details = []) {
      super(message);
      this.name = "StrategicAICommunicationError";
      this.code = stringValue(code || "communication-invalid");
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAICommunicationRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAICommunicationError(
          "Invalid strategic AI communication definition",
          "definition-invalid",
          this.report.errors
        );
      }

      this.actors = indexById(this.definition.actors);
      this.claims = indexById(this.definition.communicationClaims);
      this.intents = indexById(this.definition.communicativeIntents);
      this.templates = indexById(this.definition.speechTemplates);
      this.commitmentTypes = indexById(this.definition.commitmentTypes);
      this.modelAdapter = options.modelAdapter || null;
      this.state = migrateState(
        options.state === undefined
          ? objectValue(this.definition.stateDefaults)
          : objectValue(options.state),
        this.definition
      );
      this.normalizeState();
    }

    normalizeState() {
      if (this.state.stateVersion !== STATE_VERSION) {
        throw new StrategicAICommunicationError(
          `Strategic AI state version must be ${STATE_VERSION}`,
          "state-version-invalid"
        );
      }
      this.state.offscreenSimulationTime = integerValue(
        this.state.offscreenSimulationTime,
        0,
        0
      );
      this.state.offscreenStepStates = arrayValue(this.state.offscreenStepStates).map(clone);
      this.state.offscreenSimulationReceipts = arrayValue(
        this.state.offscreenSimulationReceipts
      ).map(clone);
      this.state.observations = arrayValue(this.state.observations).map(clone);
      this.state.beliefs = arrayValue(this.state.beliefs).map(clone);
      this.state.commitments = arrayValue(this.state.commitments).map(clone);
      this.observations = indexById(this.state.observations);
      this.beliefs = indexById(this.state.beliefs);
      this.commitments = indexById(this.state.commitments, "commitmentId");
    }

    actor(actorId) {
      const id = stringValue(actorId);
      const actor = this.actors.get(id);
      if (!actor) {
        throw new StrategicAICommunicationError(
          `Unknown strategic actor ${id}`,
          "actor-unknown"
        );
      }
      return actor;
    }

    intent(intentId) {
      const id = stringValue(intentId);
      const intent = this.intents.get(id);
      if (!intent) {
        throw new StrategicAICommunicationError(
          `Unknown communicative intent ${id}`,
          "intent-unknown"
        );
      }
      return intent;
    }

    template(templateId) {
      const id = stringValue(templateId);
      const template = this.templates.get(id);
      if (!template) {
        throw new StrategicAICommunicationError(
          `Unknown speech template ${id}`,
          "template-unknown"
        );
      }
      return template;
    }

    audience(intent, audienceActorIds) {
      const ids = [...new Set(arrayValue(audienceActorIds).map(stringValue).filter(Boolean))].sort();
      if (!ids.length) {
        throw new StrategicAICommunicationError(
          "Communication audience is required",
          "audience-empty"
        );
      }
      const allowed = new Set(arrayValue(intent.audienceActorIds).map(stringValue));
      ids.forEach((actorId) => {
        this.actor(actorId);
        if (!allowed.has(actorId)) {
          throw new StrategicAICommunicationError(
            `Actor ${actorId} is not an authorized audience for ${intent.id}`,
            "audience-unauthorized"
          );
        }
      });
      return ids;
    }

    matchingKnowledge(actorId, claim) {
      const threshold = probability(claim.minimumConfidence, 0);
      const proposition = objectValue(claim.proposition);
      const matches = [];

      this.state.beliefs.forEach((belief) => {
        const raw = objectValue(belief);
        if (
          stringValue(raw.holderId) === actorId
          && propositionsEqual(raw.proposition, proposition)
          && probability(raw.confidence, 0) >= threshold
        ) {
          matches.push({
            recordId: stringValue(raw.id),
            kind: "belief",
            confidence: probability(raw.confidence, 0)
          });
        }
      });

      this.state.observations.forEach((observation) => {
        const raw = objectValue(observation);
        if (
          stringValue(raw.observerId) === actorId
          && propositionsEqual(raw.proposition, proposition)
          && probability(raw.reliability, 0) >= threshold
        ) {
          matches.push({
            recordId: stringValue(raw.id),
            kind: "observation",
            confidence: probability(raw.reliability, 0)
          });
        }
      });

      matches.sort((left, right) => {
        const confidenceDelta = right.confidence - left.confidence;
        if (Math.abs(confidenceDelta) > 1e-9) return confidenceDelta;
        return left.recordId.localeCompare(right.recordId);
      });
      return matches;
    }

    validateClaim(claimId, speakerActorId, audienceActorIds) {
      const id = stringValue(claimId);
      const claim = this.claims.get(id);
      if (!claim) {
        throw new StrategicAICommunicationError(
          `Unknown communication claim ${id}`,
          "claim-unknown"
        );
      }
      if (!arrayValue(claim.speakerActorIds).map(stringValue).includes(speakerActorId)) {
        throw new StrategicAICommunicationError(
          `Actor ${speakerActorId} cannot voice claim ${id}`,
          "claim-speaker-unauthorized"
        );
      }
      const authorizedAudience = new Set(
        arrayValue(claim.authorizedAudienceActorIds).map(stringValue)
      );
      const unauthorized = audienceActorIds.find((actorId) => !authorizedAudience.has(actorId));
      if (unauthorized) {
        throw new StrategicAICommunicationError(
          `Claim ${id} cannot be disclosed to ${unauthorized}`,
          "claim-audience-unauthorized"
        );
      }
      const knowledge = this.matchingKnowledge(speakerActorId, claim);
      if (!knowledge.length) {
        throw new StrategicAICommunicationError(
          `Actor ${speakerActorId} lacks sufficient knowledge for claim ${id}`,
          "claim-knowledge-insufficient"
        );
      }
      return {claim, knowledge: knowledge[0]};
    }

    resolveCommitment(intent, speakerActorId, audienceActorIds, commitmentId) {
      const id = stringValue(commitmentId);
      if (!id) {
        throw new StrategicAICommunicationError(
          `Intent ${intent.id} requires a structured commitment`,
          "commitment-required"
        );
      }
      const commitment = this.commitments.get(id);
      if (!commitment) {
        throw new StrategicAICommunicationError(
          `Unknown structured commitment ${id}`,
          "commitment-unknown"
        );
      }
      const allowedTypes = new Set(arrayValue(intent.commitmentTypeIds).map(stringValue));
      if (!allowedTypes.has(stringValue(commitment.commitmentTypeId))) {
        throw new StrategicAICommunicationError(
          `Commitment ${id} is not permitted by intent ${intent.id}`,
          "commitment-type-unauthorized"
        );
      }
      if (stringValue(commitment.promisorActorId) !== speakerActorId) {
        throw new StrategicAICommunicationError(
          `Actor ${speakerActorId} is not the promisor for ${id}`,
          "commitment-speaker-mismatch"
        );
      }
      if (!audienceActorIds.includes(stringValue(commitment.promiseeActorId))) {
        throw new StrategicAICommunicationError(
          `Commitment ${id} promisee is not in the audience`,
          "commitment-audience-mismatch"
        );
      }
      const type = this.commitmentTypes.get(stringValue(commitment.commitmentTypeId));
      if (!type) {
        throw new StrategicAICommunicationError(
          `Commitment ${id} references a missing type`,
          "commitment-type-missing"
        );
      }
      return {commitment, type};
    }

    validateTemplate(templateId, intent, speakerActorId, audienceActorIds, commitmentId) {
      const template = this.template(templateId);
      if (stringValue(template.intentId) !== stringValue(intent.id)) {
        throw new StrategicAICommunicationError(
          `Template ${template.id} does not belong to intent ${intent.id}`,
          "template-intent-mismatch"
        );
      }
      if (!arrayValue(intent.templateIds).map(stringValue).includes(stringValue(template.id))) {
        throw new StrategicAICommunicationError(
          `Template ${template.id} is not allowed by intent ${intent.id}`,
          "template-not-allowed"
        );
      }
      const allowedClaims = new Set(arrayValue(intent.claimIds).map(stringValue));
      const claimBindings = [];
      arrayValue(template.claimIds).map(stringValue).forEach((claimId) => {
        if (!allowedClaims.has(claimId)) {
          throw new StrategicAICommunicationError(
            `Template ${template.id} uses claim ${claimId} outside intent ${intent.id}`,
            "template-claim-not-allowed"
          );
        }
        claimBindings.push(
          this.validateClaim(claimId, speakerActorId, audienceActorIds)
        );
      });
      const commitmentBinding = template.requiresCommitment
        ? this.resolveCommitment(
          intent,
          speakerActorId,
          audienceActorIds,
          commitmentId
        )
        : null;
      return {template, claimBindings, commitmentBinding};
    }

    render(binding, speakerActorId, audienceActorIds) {
      const actor = this.actor(speakerActorId);
      const audienceNames = audienceActorIds.map((actorId) => this.actor(actorId).name);
      const claimById = new Map(
        binding.claimBindings.map((entry) => [stringValue(entry.claim.id), entry])
      );
      return arrayValue(binding.template.segments).map((segment) => {
        const raw = objectValue(segment);
        const kind = stringValue(raw.kind);
        if (kind === "literal") return String(raw.text || "");
        if (kind === "speaker-name") return stringValue(actor.name);
        if (kind === "audience-names") return audienceNames.join(", ");
        if (kind === "claim") {
          const entry = claimById.get(stringValue(raw.claimId));
          if (!entry) {
            throw new StrategicAICommunicationError(
              `Template ${binding.template.id} has an unbound claim segment`,
              "template-claim-unbound"
            );
          }
          return stringValue(entry.claim.spokenText);
        }
        if (kind === "commitment-label") {
          if (!binding.commitmentBinding) {
            throw new StrategicAICommunicationError(
              `Template ${binding.template.id} has no commitment binding`,
              "template-commitment-unbound"
            );
          }
          return stringValue(binding.commitmentBinding.type.label);
        }
        if (kind === "commitment-status") {
          if (!binding.commitmentBinding) {
            throw new StrategicAICommunicationError(
              `Template ${binding.template.id} has no commitment binding`,
              "template-commitment-unbound"
            );
          }
          const status = stringValue(binding.commitmentBinding.commitment.status);
          if (status === "pending") return "in force";
          return status;
        }
        throw new StrategicAICommunicationError(
          `Template ${binding.template.id} has unsupported segment ${kind}`,
          "template-segment-unsupported"
        );
      }).join("");
    }

    normalizeAdapterSelection(value) {
      if (typeof value === "string") return {templateId: stringValue(value)};
      const raw = objectValue(value);
      const keys = Object.keys(raw);
      if (keys.length !== 1 || keys[0] !== "templateId") return null;
      const templateId = stringValue(raw.templateId);
      return templateId ? {templateId} : null;
    }

    perform(intentId, speakerActorId, audienceActorIds, options = {}) {
      const intent = this.intent(intentId);
      const speakerId = stringValue(speakerActorId);
      this.actor(speakerId);
      if (!arrayValue(intent.speakerActorIds).map(stringValue).includes(speakerId)) {
        throw new StrategicAICommunicationError(
          `Actor ${speakerId} cannot perform intent ${intent.id}`,
          "intent-speaker-unauthorized"
        );
      }
      const audienceIds = this.audience(intent, audienceActorIds);
      const commitmentId = stringValue(objectValue(options).commitmentId);

      const fallbackBinding = this.validateTemplate(
        intent.fallbackTemplateId,
        intent,
        speakerId,
        audienceIds,
        commitmentId
      );
      const safeTemplateIds = [];
      arrayValue(intent.templateIds).map(stringValue).sort().forEach((templateId) => {
        try {
          this.validateTemplate(
            templateId,
            intent,
            speakerId,
            audienceIds,
            commitmentId
          );
          safeTemplateIds.push(templateId);
        } catch (_error) {
          // Unsafe authored alternatives are excluded from the adapter request.
        }
      });

      let binding = fallbackBinding;
      let mode = "fallback";
      let fallbackReason = "model-disabled";
      const disableModel = objectValue(options).disableModel === true;
      const adapter = objectValue(options).modelAdapter || this.modelAdapter;
      if (!disableModel && adapter) {
        try {
          const request = {
            intentId: stringValue(intent.id),
            speechAct: stringValue(intent.speechAct),
            speakerActorId: speakerId,
            audienceActorIds: audienceIds.slice(),
            safeTemplateIds: safeTemplateIds.slice(),
            fallbackTemplateId: stringValue(intent.fallbackTemplateId),
            commitmentId: commitmentId || null,
            canonicalRevision: integerValue(
              objectValue(objectValue(this.state).canonicalState).revision,
              0,
              0
            )
          };
          const rawSelection = typeof adapter === "function"
            ? adapter(clone(request))
            : (
              typeof adapter.selectTemplate === "function"
                ? adapter.selectTemplate(clone(request))
                : null
            );
          const selection = this.normalizeAdapterSelection(rawSelection);
          if (!selection) {
            fallbackReason = "adapter-output-invalid";
          } else if (!safeTemplateIds.includes(selection.templateId)) {
            fallbackReason = "adapter-template-unsafe";
          } else {
            binding = this.validateTemplate(
              selection.templateId,
              intent,
              speakerId,
              audienceIds,
              commitmentId
            );
            mode = "adapter";
            fallbackReason = "";
          }
        } catch (_error) {
          fallbackReason = "adapter-failed";
        }
      }

      const text = this.render(binding, speakerId, audienceIds);
      const claimIds = arrayValue(binding.template.claimIds).map(stringValue);
      const knowledgeRecordIds = binding.claimBindings.map(
        (entry) => stringValue(entry.knowledge.recordId)
      );
      const structuredCommitmentIds = binding.commitmentBinding
        ? [stringValue(binding.commitmentBinding.commitment.commitmentId)]
        : [];
      const canonicalRevision = integerValue(
        objectValue(objectValue(this.state).canonicalState).revision,
        0,
        0
      );
      const identity = {
        intentId: stringValue(intent.id),
        speakerActorId: speakerId,
        audienceActorIds: audienceIds,
        templateId: stringValue(binding.template.id),
        claimIds,
        structuredCommitmentIds,
        canonicalRevision
      };
      return {
        communicationId: (
          `communication.runtime.${idSlug(intent.id)}.`
          + hashString(stableStringify(identity)).toString(16)
        ),
        intentId: stringValue(intent.id),
        speechAct: stringValue(intent.speechAct),
        speakerActorId: speakerId,
        audienceActorIds: audienceIds,
        mode,
        fallbackReason,
        templateId: stringValue(binding.template.id),
        text,
        claimIds,
        knowledgeRecordIds,
        structuredCommitmentIds,
        canonicalRevision
      };
    }

    snapshot() {
      return clone(this.state);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAICommunicationRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    LEGACY_STATE_VERSIONS: LEGACY_STATE_VERSIONS.slice(),
    StrategicAICommunicationError,
    StrategicAICommunicationRuntime,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAICommunicationRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
