(function (global) {
  "use strict";

  const VELA_SYSTEM_ID = "system.vela-gate";
  const VELA_OPPORTUNITY_ID = "opportunity.campaign.vela-gate-intervention";
  const OFFICIAL_ACTOR_ID = "actor.vela.gate-official";
  const ORGANIZER_ACTOR_ID = "actor.vela.rescue-organizer";
  const SURVIVOR_ACTOR_ID = "actor.vela.survivor";
  const BRIEFING_INTENT_ID = "communicative-intent.vela.official-customs-briefing";

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

  function finiteNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function titleWords(value) {
    return stringValue(value)
      .replace(/^[^.]+\.[^.]+\./, "")
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/[._-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  const SIGNAL_LABELS = Object.freeze({
    goalPriority: "Mission priorities",
    evidenceSupport: "Available evidence",
    uncertainty: "Remaining uncertainty",
    memoryRelevance: "Relevant history",
    observationReliability: "Source reliability",
    captainCooperation: "Expected cooperation",
    captainEvidenceDiscipline: "Evidence discipline",
    captainAuthorityResistance: "Authority resistance",
    commitmentTrust: "Commitment trust"
  });

  function signalLabel(id) {
    return SIGNAL_LABELS[stringValue(id)] || titleWords(id);
  }

  function signalAssessment(value) {
    const amount = finiteNumber(value);
    if (amount < 0) return "Reduced confidence";
    if (Math.abs(amount) >= 0.15) return "Major influence";
    if (Math.abs(amount) >= 0.08) return "Meaningful influence";
    return "Supporting influence";
  }

  function alternativeAssessment(score, selectedScore) {
    const selected = Math.max(0.000001, finiteNumber(selectedScore));
    const ratio = finiteNumber(score) / selected;
    if (ratio >= 0.7) return "Strong alternative";
    if (ratio >= 0.5) return "Viable alternative";
    return "Lower-confidence option";
  }

  function resourceLabel(resourceId, amount = 1) {
    const base = titleWords(resourceId).toLowerCase();
    return `${finiteNumber(amount)} ${base}${finiteNumber(amount) === 1 ? "" : "s"}`;
  }

  function indexById(records, key = "id") {
    return new Map(
      arrayValue(records)
        .filter((record) => stringValue(objectValue(record)[key]))
        .map((record) => [stringValue(objectValue(record)[key]), record])
    );
  }

  function latestOfficialTurn(session) {
    const state = objectValue(session?.strategicSnapshot?.());
    const receipts = arrayValue(state.receipts)
      .filter((receipt) => stringValue(receipt.actorId) === OFFICIAL_ACTOR_ID)
      .slice()
      .sort((left, right) => (
        stringValue(left.decisionId).localeCompare(stringValue(right.decisionId))
      ));
    const outcomes = indexById(state.outcomes, "decisionId");
    for (let index = receipts.length - 1; index >= 0; index -= 1) {
      const decision = receipts[index];
      const outcome = outcomes.get(stringValue(decision.decisionId));
      if (outcome) return {decision: clone(decision), outcome: clone(outcome)};
    }
    return null;
  }

  function opportunityState(session) {
    return arrayValue(objectValue(session?.strategicSnapshot?.()).campaignOpportunityStates)
      .find((record) => stringValue(record.opportunityId) === VELA_OPPORTUNITY_ID)
      || null;
  }

  function previewBriefing(session) {
    const coordinator = session?.coordinator?.();
    if (!coordinator?.performCommunication) return null;
    return coordinator.performCommunication(
      BRIEFING_INTENT_ID,
      OFFICIAL_ACTOR_ID,
      [ORGANIZER_ACTOR_ID, SURVIVOR_ACTOR_ID]
    );
  }

  function actionLabel(session, actionTypeId) {
    const action = arrayValue(objectValue(session?.definition).actionTypes)
      .find((record) => stringValue(record.id) === stringValue(actionTypeId));
    return stringValue(objectValue(action).label || titleWords(actionTypeId));
  }

  function goalLabels(session, goalIds) {
    const goals = indexById(objectValue(session?.definition).goals);
    return arrayValue(goalIds).map((goalId) => (
      stringValue(objectValue(goals.get(stringValue(goalId))).label || titleWords(goalId))
    ));
  }

  function scoreSignals(candidate) {
    return Object.entries(objectValue(objectValue(candidate).scoreComponents))
      .filter(([key, value]) => key !== "baseScore" && Math.abs(finiteNumber(value)) > 0)
      .sort((left, right) => Math.abs(finiteNumber(right[1])) - Math.abs(finiteNumber(left[1])))
      .slice(0, 3)
      .map(([key, value]) => ({
        id: key,
        label: signalLabel(key),
        value: finiteNumber(value),
        assessment: signalAssessment(value)
      }));
  }

  function buildViewModel(session) {
    if (!session) {
      return {
        visible: false,
        phase: "unavailable",
        title: "Vela Gate Authority Channel",
        status: "Strategic session unavailable.",
        canRun: false
      };
    }
    const summary = session.summary();
    const visible = stringValue(summary.activeSystemId) === VELA_SYSTEM_ID;
    const opportunity = opportunityState(session);
    const opportunityStatus = stringValue(objectValue(opportunity).status || "unavailable");
    const turn = latestOfficialTurn(session);
    let briefing = null;
    let briefingError = "";
    if (turn?.outcome?.status === "accepted") {
      try {
        briefing = previewBriefing(session);
      } catch (error) {
        briefingError = error instanceof Error ? error.message : String(error || "Briefing unavailable");
      }
    }

    const selectedCandidate = arrayValue(objectValue(turn?.decision).candidateActions)
      .find((candidate) => (
        stringValue(candidate.actionTypeId)
        === stringValue(objectValue(turn?.decision).selectedActionTypeId)
      ));
    const selectedScore = finiteNumber(objectValue(selectedCandidate).score);
    const alternatives = arrayValue(objectValue(turn?.decision).candidateActions)
      .filter((candidate) => (
        stringValue(candidate.actionTypeId)
        !== stringValue(objectValue(turn?.decision).selectedActionTypeId)
      ))
      .map((candidate) => ({
        actionTypeId: stringValue(candidate.actionTypeId),
        label: actionLabel(session, candidate.actionTypeId),
        score: finiteNumber(candidate.score),
        assessment: alternativeAssessment(candidate.score, selectedScore)
      }));

    let phase = "ready";
    let status = "A Gate Authority channel is available.";
    if (!visible) {
      phase = "away";
      status = "The Vela Gate channel is available only while the ship is in Vela Gate.";
    } else if (turn?.outcome?.status === "accepted") {
      phase = "complete";
      status = "The Gate Authority has issued a verified response.";
    } else if (turn?.outcome?.status === "rejected") {
      phase = "rejected";
      status = stringValue(turn.outcome.rejectionReason || "The verified response was rejected.");
    } else if (opportunityStatus === "closed") {
      phase = "closed";
      status = "The Vela Gate intervention window has closed.";
    } else if (opportunityStatus === "active") {
      phase = "active";
      status = "The intervention window is active. Request the official response.";
    }

    return {
      visible,
      phase,
      title: "Vela Gate Authority Channel",
      status,
      canRun: visible && !turn && ["available", "active"].includes(opportunityStatus),
      opportunityStatus,
      briefingText: stringValue(objectValue(briefing).text),
      briefingError,
      communicationId: stringValue(objectValue(briefing).communicationId),
      actionTypeId: stringValue(objectValue(turn?.decision).selectedActionTypeId),
      actionLabel: actionLabel(session, objectValue(turn?.decision).selectedActionTypeId),
      outcomeStatus: stringValue(objectValue(turn?.outcome).status),
      confidence: finiteNumber(objectValue(turn?.decision).confidence),
      canonicalRevisionAfter: finiteNumber(objectValue(turn?.outcome).canonicalRevisionAfter),
      score: finiteNumber(objectValue(selectedCandidate).score),
      scoreSignals: scoreSignals(selectedCandidate),
      goals: goalLabels(session, objectValue(turn?.decision).activeGoalIds),
      alternatives,
      resultingObservationCount: arrayValue(objectValue(turn?.outcome).resultingObservationIds).length,
      consumedResources: clone(arrayValue(objectValue(turn?.outcome).consumedResources)),
      consequenceRows: turn?.outcome ? [
        {
          label: "Verification",
          value: stringValue(turn.outcome.status) === "accepted"
            ? "Accepted by the action verifier"
            : stringValue(turn.outcome.rejectionReason || "Rejected by the action verifier")
        },
        {
          label: "World state",
          value: `Advanced to revision ${finiteNumber(turn.outcome.canonicalRevisionAfter)}`
        },
        {
          label: "Shared knowledge",
          value: `${arrayValue(turn.outcome.resultingObservationIds).length} Vela actors received updates`
        },
        ...arrayValue(turn.outcome.consumedResources).map((resource) => ({
          label: "Resource used",
          value: resourceLabel(resource.resourceId, resource.amount)
        }))
      ] : [],
      decisionId: stringValue(objectValue(turn?.decision).decisionId),
      outcomeId: stringValue(objectValue(turn?.outcome).outcomeId)
    };
  }

  function runInteraction(session) {
    if (!session) throw new Error("Strategic session unavailable.");
    if (stringValue(session.summary().activeSystemId) !== VELA_SYSTEM_ID) {
      throw new Error("The Vela Gate channel can be used only in Vela Gate.");
    }

    const existing = latestOfficialTurn(session);
    if (existing?.outcome?.status === "accepted") {
      return {
        reused: true,
        activation: null,
        turn: clone(existing),
        briefing: previewBriefing(session),
        view: buildViewModel(session)
      };
    }

    const state = opportunityState(session);
    const status = stringValue(objectValue(state).status || "unavailable");
    if (status === "closed") {
      throw new Error("The Vela Gate intervention window has closed.");
    }

    let activation = null;
    if (status === "available") {
      activation = session.activateCampaignRoute(VELA_SYSTEM_ID, {
        selectedAt: finiteNumber(session.summary().offscreenSimulationTime, 0)
      });
    } else if (status !== "active") {
      throw new Error(`Vela Gate opportunity is ${status || "unavailable"}.`);
    }

    const turn = session.runActorTurn(OFFICIAL_ACTOR_ID);
    if (stringValue(objectValue(turn).outcome?.status) !== "accepted") {
      return {
        reused: false,
        activation,
        turn,
        briefing: null,
        view: buildViewModel(session)
      };
    }
    const briefing = session.performCommunication(
      BRIEFING_INTENT_ID,
      OFFICIAL_ACTOR_ID,
      [ORGANIZER_ACTOR_ID, SURVIVOR_ACTOR_ID]
    );
    return {
      reused: false,
      activation,
      turn,
      briefing,
      view: buildViewModel(session)
    };
  }

  const state = {
    bound: false,
    session: null,
    unsubscribe: null,
    running: false,
    lastError: ""
  };

  function nodes() {
    if (typeof document === "undefined") return {};
    return {
      panel: document.querySelector("#vela-gate-strategic-contact"),
      status: document.querySelector("#vela-gate-strategic-status"),
      request: document.querySelector("#vela-gate-strategic-request"),
      briefing: document.querySelector("#vela-gate-strategic-briefing"),
      action: document.querySelector("#vela-gate-strategic-action"),
      confidence: document.querySelector("#vela-gate-strategic-confidence"),
      reasons: document.querySelector("#vela-gate-strategic-reasons"),
      alternatives: document.querySelector("#vela-gate-strategic-alternatives"),
      consequences: document.querySelector("#vela-gate-strategic-consequences"),
      explanation: document.querySelector("#vela-gate-strategic-explanation")
    };
  }

  function replaceRows(node, entries, rowClass = "") {
    if (!node) return;
    node.replaceChildren();
    arrayValue(entries).forEach((entry) => {
      const record = typeof entry === "string"
        ? {label: stringValue(entry), value: ""}
        : objectValue(entry);
      const item = document.createElement("li");
      item.className = `vela-gate-strategic-list-row ${stringValue(rowClass)}`.trim();

      const label = document.createElement("span");
      label.className = "vela-gate-strategic-list-label";
      label.textContent = stringValue(record.label);

      const value = document.createElement("span");
      value.className = "vela-gate-strategic-list-value";
      value.textContent = stringValue(record.value);

      item.append(label);
      if (value.textContent) item.append(value);
      node.append(item);
    });
  }

  function render() {
    const ui = nodes();
    if (!ui.panel) return;
    const session = state.session || global.MainComputerStrategicAISession?.current?.();
    if (session && session !== state.session) setSession(session);
    const view = buildViewModel(state.session);
    ui.panel.hidden = !view.visible;
    ui.panel.dataset.phase = stringValue(view.phase);
    if (!view.visible) return;

    if (ui.status) {
      ui.status.dataset.state = state.lastError ? "error" : view.phase;
      ui.status.textContent = state.lastError || view.status;
    }
    if (ui.request) {
      ui.request.disabled = state.running || !view.canRun;
      ui.request.dataset.state = stringValue(view.phase);
      ui.request.textContent = state.running
        ? "Contacting Gate Authority…"
        : view.phase === "complete"
          ? "✓ Briefing received"
          : "Request official briefing";
    }
    if (ui.briefing) {
      ui.briefing.textContent = view.briefingText
        || view.briefingError
        || "No official briefing has been received.";
    }
    if (ui.action) ui.action.textContent = view.actionLabel || "Awaiting verified decision";
    if (ui.confidence) {
      ui.confidence.textContent = view.actionTypeId
        ? `${Math.round(view.confidence * 100)}% decision confidence`
        : "";
    }
    replaceRows(
      ui.reasons,
      view.scoreSignals.map((signal) => ({
        label: signal.label,
        value: signal.assessment
      })),
      "vela-gate-strategic-factor-meter"
    );
    replaceRows(
      ui.alternatives,
      view.alternatives.map((alternative) => ({
        label: alternative.label,
        value: alternative.assessment
      }))
    );
    replaceRows(ui.consequences, view.consequenceRows);
    if (ui.explanation) ui.explanation.hidden = !view.actionTypeId;
  }

  function setSession(session) {
    if (state.session === session) {
      render();
      return session;
    }
    state.unsubscribe?.();
    state.unsubscribe = null;
    state.session = session || null;
    state.lastError = "";
    if (state.session?.subscribe) {
      state.unsubscribe = state.session.subscribe(() => render());
    }
    render();
    return state.session;
  }

  function handleRequest() {
    if (state.running) return;
    state.running = true;
    state.lastError = "";
    render();
    try {
      runInteraction(state.session);
    } catch (error) {
      state.lastError = error instanceof Error ? error.message : String(error || "Interaction failed.");
    } finally {
      state.running = false;
      render();
    }
  }

  function bind() {
    if (state.bound || typeof document === "undefined") return;
    state.bound = true;
    nodes().request?.addEventListener("click", handleRequest);
    global.addEventListener?.("main-computer-strategic-ai-session-change", () => {
      const current = global.MainComputerStrategicAISession?.current?.();
      if (current) setSession(current);
      else render();
    });
    const current = global.MainComputerStrategicAISession?.current?.();
    if (current) setSession(current);
    render();
  }

  const api = {
    VELA_SYSTEM_ID,
    VELA_OPPORTUNITY_ID,
    OFFICIAL_ACTOR_ID,
    BRIEFING_INTENT_ID,
    latestOfficialTurn,
    opportunityState,
    previewBriefing,
    signalLabel,
    signalAssessment,
    alternativeAssessment,
    resourceLabel,
    buildViewModel,
    runInteraction,
    setSession,
    render,
    bind,
    state
  };

  global.MainComputerStrategicAIVelaInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
