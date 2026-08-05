(function (global) {
  "use strict";

  const SOLACE_SYSTEM_ID = "system.solace-reach";
  const COMMITMENT_TYPE_ID = "commitment.solace.shuttle-to-osprey";
  const HAVEN_ACTOR_ID = "actor.solace.haven-coordinator";
  const OSPREY_ACTOR_ID = "actor.solace.osprey-captain";
  const LYRIA_ACTOR_ID = "actor.solace.lyria-medic";
  const PROMISE_INTENT_ID = "communicative-intent.solace.haven-confirm-shuttle-promise";
  const SHUTTLE_RESOURCE_ID = "resource.solace.rescue-shuttle";
  const SHUTTLE_DESTINATION_FACT_ID = "fact.solace.shuttle-destination";
  const COOPERATION_PROFILE_ID = "cooperation.solace.osprey-trusts-haven";

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

  function integerValue(value, fallback = 0, minimum = 0) {
    return Math.max(minimum, Math.trunc(finiteNumber(value, fallback)));
  }

  function titleWords(value) {
    return stringValue(value)
      .replace(/^[^.]+\.[^.]+\./, "")
      .replace(/[._-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function stateSnapshot(session) {
    return objectValue(session?.strategicSnapshot?.());
  }

  function latestCommitment(session) {
    return arrayValue(stateSnapshot(session).commitments)
      .filter((record) => stringValue(record.commitmentTypeId) === COMMITMENT_TYPE_ID)
      .slice()
      .sort((left, right) => (
        integerValue(left.createdAt) - integerValue(right.createdAt)
        || stringValue(left.commitmentId).localeCompare(stringValue(right.commitmentId))
      ))
      .pop() || null;
  }

  function outcomeById(state, outcomeId) {
    return arrayValue(objectValue(state).outcomes)
      .find((record) => stringValue(record.outcomeId) === stringValue(outcomeId))
      || null;
  }

  function latestOspreyOutcome(session, commitment = latestCommitment(session)) {
    const state = stateSnapshot(session);
    const minimumRevision = integerValue(objectValue(commitment).canonicalRevisionResolved, 0, 0);
    return arrayValue(state.outcomes)
      .filter((record) => (
        stringValue(record.actorId) === OSPREY_ACTOR_ID
        && ["action.solace.share-osprey-manifests", "action.solace.withhold-osprey-manifests"]
          .includes(stringValue(record.actionTypeId))
        && integerValue(record.canonicalRevisionAfter, 0, 0) >= minimumRevision
      ))
      .slice()
      .sort((left, right) => (
        integerValue(left.canonicalRevisionAfter) - integerValue(right.canonicalRevisionAfter)
        || stringValue(left.outcomeId).localeCompare(stringValue(right.outcomeId))
      ))
      .pop() || null;
  }

  function cooperationModel(session) {
    return arrayValue(stateSnapshot(session).cooperationModels)
      .find((record) => stringValue(record.profileId) === COOPERATION_PROFILE_ID)
      || null;
  }

  function shuttleQuantity(session) {
    const canonical = objectValue(stateSnapshot(session).canonicalState);
    const balance = arrayValue(canonical.resourceBalances)
      .find((record) => stringValue(record.resourceId) === SHUTTLE_RESOURCE_ID);
    return finiteNumber(objectValue(balance).quantity, 0);
  }

  function shuttleDestination(session) {
    const canonical = objectValue(stateSnapshot(session).canonicalState);
    const fact = arrayValue(canonical.factStates)
      .find((record) => stringValue(record.factId) === SHUTTLE_DESTINATION_FACT_ID);
    return stringValue(objectValue(fact).value);
  }

  function actionLabel(session, actionTypeId) {
    const action = arrayValue(objectValue(session?.definition).actionTypes)
      .find((record) => stringValue(record.id) === stringValue(actionTypeId));
    return stringValue(objectValue(action).label || titleWords(actionTypeId));
  }

  function actorName(session, actorId) {
    const actor = arrayValue(objectValue(session?.definition).actors)
      .find((record) => stringValue(record.id) === stringValue(actorId));
    return stringValue(objectValue(actor).name || titleWords(actorId));
  }

  function previewPromise(session, commitment = latestCommitment(session)) {
    if (!session || !commitment?.commitmentId) return null;
    const coordinator = session.coordinator?.();
    if (!coordinator?.performCommunication) return null;
    return coordinator.performCommunication(
      PROMISE_INTENT_ID,
      HAVEN_ACTOR_ID,
      [OSPREY_ACTOR_ID],
      {commitmentId: commitment.commitmentId}
    );
  }

  function trustBand(trust) {
    const value = finiteNumber(trust);
    if (value >= 0.8) return "High trust";
    if (value >= 0.5) return "Cautious trust";
    if (value >= 0.25) return "Low trust";
    return "Trust damaged";
  }

  function destinationLabel(destinationId) {
    const id = stringValue(destinationId);
    if (id === "destination.solace-reach.osprey-anchorage") return "Osprey Anchorage";
    if (id === "destination.solace-reach.lyria-transfer") return "Lyria medical transfer";
    if (id === "destination.solace-reach.haven-orbit") return "Haven orbit";
    return titleWords(id);
  }

  function buildViewModel(session) {
    if (!session) {
      return {
        visible: false,
        phase: "unavailable",
        status: "Strategic session unavailable.",
        canBegin: false,
        canChoose: false,
        canContinue: false
      };
    }

    const summary = session.summary();
    const visible = stringValue(summary.activeSystemId) === SOLACE_SYSTEM_ID;
    const commitment = latestCommitment(session);
    const allocationOutcome = commitment?.resolutionOutcomeId
      ? outcomeById(stateSnapshot(session), commitment.resolutionOutcomeId)
      : null;
    const ospreyOutcome = latestOspreyOutcome(session, commitment);
    const cooperation = cooperationModel(session);
    const trust = finiteNumber(objectValue(cooperation).trust, 0.55);
    const quantity = shuttleQuantity(session);
    let promise = null;
    let promiseError = "";
    if (commitment) {
      try {
        promise = previewPromise(session, commitment);
      } catch (error) {
        promiseError = error instanceof Error ? error.message : String(error || "Promise wording unavailable.");
      }
    }

    let phase = "ready";
    let status = "One rescue shuttle remains. Haven can make a binding promise before allocation.";
    if (!visible) {
      phase = "away";
      status = "The Solace relief channel is available only while the ship is in Solace Reach.";
    } else if (commitment?.status === "pending") {
      phase = "choice";
      status = "Haven has promised the shuttle to Osprey. Choose how the shared resource is allocated.";
    } else if (commitment && ["kept", "broken"].includes(stringValue(commitment.status))) {
      phase = ospreyOutcome ? "complete" : "response-pending";
      status = commitment.status === "kept"
        ? "The shuttle promise was kept."
        : "The shuttle promise was broken when Lyria claimed the shared shuttle.";
    } else if (quantity < 1) {
      phase = "unavailable";
      status = "The shared rescue shuttle has already been committed elsewhere.";
    }

    const commitmentStatus = stringValue(objectValue(commitment).status);
    const allocationActorId = stringValue(objectValue(allocationOutcome).actorId);
    const kept = commitmentStatus === "kept";
    const broken = commitmentStatus === "broken";
    const outcomeRows = [];
    if (commitment) {
      outcomeRows.push({
        label: "Promise",
        value: commitmentStatus === "pending"
          ? "In force"
          : commitmentStatus === "kept"
            ? "Kept"
            : commitmentStatus === "broken"
              ? "Broken"
              : titleWords(commitmentStatus)
      });
    }
    if (allocationOutcome) {
      outcomeRows.push({
        label: "Shuttle allocation",
        value: actionLabel(session, allocationOutcome.actionTypeId)
      });
    }
    if (commitmentStatus && commitmentStatus !== "pending") {
      outcomeRows.push({
        label: "Osprey trust",
        value: `${Math.round(trust * 100)}% — ${trustBand(trust)}`
      });
    }
    if (ospreyOutcome) {
      outcomeRows.push({
        label: "Osprey response",
        value: actionLabel(session, ospreyOutcome.actionTypeId)
      });
    }
    if (allocationOutcome) {
      outcomeRows.push({
        label: "World state",
        value: `Advanced to revision ${integerValue(objectValue(ospreyOutcome).canonicalRevisionAfter, integerValue(allocationOutcome.canonicalRevisionAfter))}`
      });
    }

    return {
      visible,
      phase,
      status,
      canBegin: visible && phase === "ready",
      canChoose: visible && phase === "choice",
      canContinue: visible && phase === "response-pending",
      commitmentId: stringValue(objectValue(commitment).commitmentId),
      commitmentStatus,
      promiseText: stringValue(objectValue(promise).text),
      promiseError,
      shuttleQuantity: quantity,
      shuttleDestinationId: shuttleDestination(session),
      shuttleDestinationLabel: destinationLabel(shuttleDestination(session)),
      trust,
      trustPercent: Math.round(trust * 100),
      trustBand: trustBand(trust),
      allocationActorId,
      allocationActorName: actorName(session, allocationActorId),
      allocationActionTypeId: stringValue(objectValue(allocationOutcome).actionTypeId),
      allocationActionLabel: actionLabel(session, objectValue(allocationOutcome).actionTypeId),
      ospreyActionTypeId: stringValue(objectValue(ospreyOutcome).actionTypeId),
      ospreyActionLabel: actionLabel(session, objectValue(ospreyOutcome).actionTypeId),
      kept,
      broken,
      outcomeRows,
      canonicalRevision: integerValue(summary.canonicalRevision),
      sequence: integerValue(summary.sequence)
    };
  }

  function currentTime(session) {
    return integerValue(session?.summary?.().offscreenSimulationTime, 0, 0);
  }

  function beginEncounter(session) {
    if (!session) throw new Error("Strategic session unavailable.");
    if (stringValue(session.summary().activeSystemId) !== SOLACE_SYSTEM_ID) {
      throw new Error("The Solace relief channel can be used only in Solace Reach.");
    }
    const existing = latestCommitment(session);
    if (existing) {
      return {
        reused: true,
        commitment: clone(existing),
        communication: previewPromise(session, existing),
        view: buildViewModel(session)
      };
    }
    if (shuttleQuantity(session) < 1) {
      throw new Error("The rescue shuttle is no longer available.");
    }

    const createdAt = currentTime(session);
    const commitment = session.createCommitment(
      COMMITMENT_TYPE_ID,
      HAVEN_ACTOR_ID,
      OSPREY_ACTOR_ID,
      {createdAt}
    );
    const communication = session.performCommunication(
      PROMISE_INTENT_ID,
      HAVEN_ACTOR_ID,
      [OSPREY_ACTOR_ID],
      {commitmentId: commitment.commitmentId}
    );
    return {
      reused: false,
      commitment,
      communication,
      view: buildViewModel(session)
    };
  }

  function continueOspreyResponse(session) {
    const commitment = latestCommitment(session);
    if (!commitment || !["kept", "broken"].includes(stringValue(commitment.status))) {
      throw new Error("The shuttle promise has not been resolved.");
    }
    const existing = latestOspreyOutcome(session, commitment);
    if (existing) return {reused: true, osprey: clone(existing), view: buildViewModel(session)};
    const osprey = session.runActorTurn(OSPREY_ACTOR_ID, {
      proposalOptions: {createdAt: currentTime(session)}
    });
    return {reused: false, osprey, view: buildViewModel(session)};
  }

  function resolveChoice(session, choice) {
    if (!session) throw new Error("Strategic session unavailable.");
    if (stringValue(session.summary().activeSystemId) !== SOLACE_SYSTEM_ID) {
      throw new Error("The Solace relief channel can be used only in Solace Reach.");
    }
    const commitment = latestCommitment(session);
    if (!commitment) throw new Error("Create the shuttle promise before allocating the resource.");

    if (["kept", "broken"].includes(stringValue(commitment.status))) {
      return {
        reused: true,
        allocation: clone(outcomeById(stateSnapshot(session), commitment.resolutionOutcomeId)),
        ...continueOspreyResponse(session),
        view: buildViewModel(session)
      };
    }
    if (stringValue(commitment.status) !== "pending") {
      throw new Error(`The shuttle promise is ${stringValue(commitment.status) || "unavailable"}.`);
    }

    const cleanChoice = stringValue(choice);
    const actorId = cleanChoice === "keep"
      ? HAVEN_ACTOR_ID
      : cleanChoice === "divert"
        ? LYRIA_ACTOR_ID
        : "";
    if (!actorId) throw new Error(`Unknown Solace allocation choice ${cleanChoice || "(empty)"}.`);

    const allocation = session.runActorTurn(actorId, {
      proposalOptions: {createdAt: currentTime(session)}
    });
    if (stringValue(objectValue(allocation).outcome?.status) !== "accepted") {
      return {
        reused: false,
        allocation,
        osprey: null,
        view: buildViewModel(session)
      };
    }
    const response = continueOspreyResponse(session);
    return {
      reused: false,
      allocation,
      osprey: response.osprey,
      view: buildViewModel(session)
    };
  }

  const uiState = {
    bound: false,
    session: null,
    unsubscribe: null,
    running: false,
    lastError: ""
  };

  function nodes() {
    if (typeof document === "undefined") return {};
    return {
      panel: document.querySelector("#solace-strategic-contact"),
      status: document.querySelector("#solace-strategic-status"),
      promise: document.querySelector("#solace-strategic-promise"),
      begin: document.querySelector("#solace-strategic-begin"),
      choices: document.querySelector("#solace-strategic-choices"),
      keep: document.querySelector("#solace-strategic-keep"),
      divert: document.querySelector("#solace-strategic-divert"),
      continueResponse: document.querySelector("#solace-strategic-continue"),
      trust: document.querySelector("#solace-strategic-trust"),
      trustFill: document.querySelector("#solace-strategic-trust-fill"),
      shuttle: document.querySelector("#solace-strategic-shuttle"),
      outcome: document.querySelector("#solace-strategic-outcome")
    };
  }

  function renderRows(node, rows) {
    if (!node) return;
    node.replaceChildren();
    arrayValue(rows).forEach((row) => {
      const item = document.createElement("li");
      item.className = "solace-strategic-outcome-row";
      const label = document.createElement("span");
      label.className = "solace-strategic-outcome-label";
      label.textContent = stringValue(row.label);
      const value = document.createElement("strong");
      value.className = "solace-strategic-outcome-value";
      value.textContent = stringValue(row.value);
      item.append(label, value);
      node.append(item);
    });
  }

  function render() {
    const ui = nodes();
    if (!ui.panel) return;
    const current = uiState.session || global.MainComputerStrategicAISession?.current?.();
    if (current && current !== uiState.session) setSession(current);
    const view = buildViewModel(uiState.session);
    ui.panel.hidden = !view.visible;
    ui.panel.dataset.phase = stringValue(view.phase);
    if (!view.visible) return;

    if (ui.status) {
      ui.status.dataset.state = uiState.lastError ? "error" : view.phase;
      ui.status.textContent = uiState.lastError || view.status;
    }
    if (ui.promise) {
      ui.promise.hidden = !view.commitmentId;
      ui.promise.textContent = view.promiseText
        || view.promiseError
        || "A structured shuttle promise exists.";
    }
    if (ui.begin) {
      ui.begin.hidden = !view.canBegin;
      ui.begin.disabled = uiState.running || !view.canBegin;
    }
    if (ui.choices) ui.choices.hidden = !view.canChoose;
    if (ui.keep) ui.keep.disabled = uiState.running || !view.canChoose;
    if (ui.divert) ui.divert.disabled = uiState.running || !view.canChoose;
    if (ui.continueResponse) {
      ui.continueResponse.hidden = !view.canContinue;
      ui.continueResponse.disabled = uiState.running || !view.canContinue;
    }
    if (ui.trust) {
      ui.trust.textContent = `${view.trustPercent}% — ${view.trustBand}`;
    }
    if (ui.trustFill) {
      ui.trustFill.style.width = `${Math.max(0, Math.min(100, view.trustPercent))}%`;
      ui.trustFill.dataset.band = view.trustPercent >= 80
        ? "high"
        : view.trustPercent < 25
          ? "damaged"
          : "cautious";
    }
    if (ui.shuttle) {
      ui.shuttle.textContent = view.shuttleQuantity > 0
        ? `Available in ${view.shuttleDestinationLabel}`
        : `Allocated to ${view.shuttleDestinationLabel}`;
    }
    renderRows(ui.outcome, view.outcomeRows);
  }

  function runUi(callback) {
    if (uiState.running) return null;
    uiState.running = true;
    uiState.lastError = "";
    render();
    try {
      return callback();
    } catch (error) {
      uiState.lastError = error instanceof Error ? error.message : String(error || "Solace interaction failed.");
      return null;
    } finally {
      uiState.running = false;
      render();
    }
  }

  function setSession(session) {
    if (uiState.session === session) {
      render();
      return session;
    }
    uiState.unsubscribe?.();
    uiState.unsubscribe = null;
    uiState.session = session || null;
    uiState.lastError = "";
    if (uiState.session?.subscribe) {
      uiState.unsubscribe = uiState.session.subscribe(() => render());
    }
    render();
    return uiState.session;
  }

  function bind() {
    if (uiState.bound || typeof document === "undefined") return;
    uiState.bound = true;
    const ui = nodes();
    ui.begin?.addEventListener("click", () => runUi(() => beginEncounter(uiState.session)));
    ui.keep?.addEventListener("click", () => runUi(() => resolveChoice(uiState.session, "keep")));
    ui.divert?.addEventListener("click", () => runUi(() => resolveChoice(uiState.session, "divert")));
    ui.continueResponse?.addEventListener("click", () => runUi(() => continueOspreyResponse(uiState.session)));
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
    SOLACE_SYSTEM_ID,
    COMMITMENT_TYPE_ID,
    HAVEN_ACTOR_ID,
    OSPREY_ACTOR_ID,
    LYRIA_ACTOR_ID,
    PROMISE_INTENT_ID,
    latestCommitment,
    latestOspreyOutcome,
    cooperationModel,
    shuttleQuantity,
    shuttleDestination,
    previewPromise,
    trustBand,
    buildViewModel,
    beginEncounter,
    continueOspreyResponse,
    resolveChoice,
    setSession,
    render,
    bind,
    state: uiState
  };

  global.MainComputerStrategicAISolaceInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
