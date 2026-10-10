;(function () {
  "use strict";
  // Asynchronous *decision* adapter only. The scene publishes validated orders
  // through its existing bridge authority; this module never moves a ship.
  const ENDPOINT = "/api/applications/game/tactical-ai/captain/decide";
  function create({request = null, onDecision, onStatus = null, cooldownSeconds = 20, decisionIntervalSeconds = 15} = {}) {
    if (typeof onDecision !== "function") throw new Error("BRIDGE_LIVE_CAPTAIN_DECISION_SINK_REQUIRED");
    const post = request || (async observation => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(ENDPOINT, {
          method: "POST", cache: "no-store", headers: {"content-type":"application/json"},
          signal:controller.signal, body: JSON.stringify(observation)
        });
        const result = await response.json();
        if (!response.ok || result?.ok !== true) throw new Error(String(result?.error || `HTTP ${response.status}`));
        return result;
      } finally {
        clearTimeout(timeout);
      }
    });
    let pending = false, stopped = false, epoch = 0;
    let lastAttemptAt = -Infinity, lastError = "", lastReceipt = null, liveDecisionCount = 0;
    let lastOutcome = "not-requested", lastSource = "deterministic-fallback";
    const notify = () => onStatus?.(status());
    function status() {
      return Object.freeze({schema:"game.bridgeLiveCaptainProvider.v1", pending,
        mode:lastSource, lastOutcome, lastError, lastReceipt,
        liveDecisionCount, lastAttemptSimulationSeconds:lastAttemptAt});
    }
    function tick(observation) {
      if (stopped || pending) return false;
      if (!observation || observation.captainId !== "captain.beta" || observation.shipId !== "ship.beta") return false;
      const now = Number(observation.simulationSeconds);
      if (!Number.isFinite(now) || now < 0) return false;
      const gap = lastOutcome === "error" ? cooldownSeconds : decisionIntervalSeconds;
      if (now - lastAttemptAt < gap) return false;
      lastAttemptAt = now;
      pending = true; lastOutcome = "pending"; notify();
      const thisEpoch = epoch;
      Promise.resolve().then(() => post({...observation})).then(result => {
        if (stopped || epoch !== thisEpoch) return;
        if (!result || result.ok !== true || result.schema !== "game.bridgeCaptainNanoJevDecision.v1" ||
            result.captainId !== "captain.beta" || result.shipId !== "ship.beta" ||
            result.source !== "nanojev-captain-v6" ||
            !(result.actionType==='boarding'
              ? ['initiate','recall','abandon'].includes(result.boardingAction)
              : ['approach','hold','withdraw','coast'].includes(result.maneuver)) ||
            !Number.isFinite(result.observationSeconds) || Math.abs(result.observationSeconds-now)>1e-6 ||
            (result.actionType!=='boarding' && result.maneuver === "hold" &&
             (!Number.isFinite(result.rangeM) || result.rangeM < 0)) ||
            !result.modelReceipt?.checkpointSha256 || !result.modelReceipt?.clientRequestId) {
          throw new Error("BRIDGE_LIVE_CAPTAIN_RESULT_INVALID");
        }
        // The live scene rejects outdated physics observations before it calls
        // the authority command. A model may choose, but cannot back-date an order.
        if (onDecision(result) === false) throw new Error("BRIDGE_LIVE_CAPTAIN_ORDER_REJECTED");
        lastReceipt = {...result.modelReceipt};
        lastSource = "nanojev"; lastOutcome = "accepted"; lastError = "";
        liveDecisionCount += 1;
      }).catch(error => {
        if (stopped || epoch !== thisEpoch) return;
        lastOutcome = "error";
        lastError = String(error?.message || error);
        lastSource = "deterministic-fallback";
      }).finally(() => {
        if (stopped || epoch !== thisEpoch) return;
        pending = false; notify();
      });
      return true;
    }
    function reset() {
      epoch += 1; stopped = false; pending = false;
      lastAttemptAt = -Infinity; lastError = ""; lastReceipt = null;
      liveDecisionCount = 0; lastOutcome = "not-requested";
      lastSource = "deterministic-fallback"; notify();
    }
    function stop() { epoch += 1; stopped = true; pending = false; lastOutcome="stopped"; notify(); }
    return Object.freeze({tick, status, reset, stop});
  }
  globalThis.MainComputerBridgeLiveCaptainProvider=Object.freeze({SCHEMA:"game.bridgeLiveCaptainProvider.v1",create});
})();
