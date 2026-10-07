(function (global) {
  "use strict";

  const PaxScenarioSession = global.MainComputerPaxScenarioSession
    || (typeof require === "function" ? require("./pax-scenario-session.js") : null);

  if (!PaxScenarioSession?.bind) {
    throw new Error("MainComputerPaxScenarioSession must load before Pax scenario interaction.");
  }

  const api = PaxScenarioSession;

  global.MainComputerPaxScenarioInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") api.bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
