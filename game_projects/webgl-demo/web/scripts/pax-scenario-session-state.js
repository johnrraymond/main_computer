(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.storage?.briefingAckPrefix) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax scenario session state.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax scenario session state.");
  }

  const {
    objectValue,
    stringValue,
    nowMs: valueNowMs
  } = PaxValueUtils;

  function createPaxScenarioSessionState(options = {}) {
    const config = options.config && typeof options.config === "object"
      ? options.config
      : DEFAULT_CONFIG;
    const globalRef = options.globalRef || global;
    const briefingCueKey = typeof options.briefingCueKey === "function"
      ? options.briefingCueKey
      : () => "";
    const initialState = objectValue(options.initialState);
    const state = Object.assign({
      bound: false,
      runtime: null,
      characterRuntime: null,
      unsubscribe: null,
      characterUnsubscribe: null,
      running: false,
      recoveryInProgress: false,
      lastAutomaticRecovery: null,
      lastError: "",
      lastHardKickoff: null,
      recoveredCharacterRuntime: null,
      worldSnapshot: null,
      acknowledgedBriefings: new Set()
    }, initialState);

    if (!(state.acknowledgedBriefings instanceof Set)) {
      state.acknowledgedBriefings = new Set(Array.isArray(state.acknowledgedBriefings)
        ? state.acknowledgedBriefings
        : []);
    }

    function nowMs(options = {}) {
      return valueNowMs(options);
    }

    function currentRuntime() {
      return state.runtime
        || globalRef.MainComputerSystemScenarioRuntime?.current?.()
        || null;
    }

    function currentCharacterRuntime() {
      return state.characterRuntime
        || globalRef.MainComputerCharacterAIRuntime?.current?.()
        || null;
    }

    function activeShuttleRenderer() {
      return globalRef.document
        ?.querySelector?.("#webgl-demo")
        ?.__mainComputerShuttle3dRenderer
        || null;
    }

    function storage() {
      try {
        return globalRef.localStorage || null;
      } catch {
        return null;
      }
    }

    function briefingStorageKey(cueKey) {
      return `${config.storage.briefingAckPrefix}:${stringValue(cueKey) || "active"}`;
    }

    function briefingAcknowledged(cueKey) {
      const key = stringValue(cueKey);
      if (!key) return false;
      if (state.acknowledgedBriefings.has(key)) return true;
      const store = storage();
      if (!store?.getItem) return false;
      try {
        return store.getItem(briefingStorageKey(key)) === "acknowledged";
      } catch {
        return false;
      }
    }

    function acknowledgeBriefing(view) {
      const cueKey = stringValue(briefingCueKey(view));
      if (!cueKey) return {acknowledged: false, cueKey: ""};
      state.acknowledgedBriefings.add(cueKey);
      const store = storage();
      if (store?.setItem) {
        try {
          store.setItem(briefingStorageKey(cueKey), "acknowledged");
        } catch {
          // Non-persistent storage is acceptable; in-memory acknowledgement still works.
        }
      }
      return {acknowledged: true, cueKey};
    }

    function debugState() {
      return {
        bound: Boolean(state.bound),
        runtimeAttached: Boolean(state.runtime),
        characterRuntimeAttached: Boolean(state.characterRuntime),
        recoveredCharacterRuntimeAttached: Boolean(state.recoveredCharacterRuntime),
        recoveredCharacterRuntimeMatchesCurrent:
          Boolean(state.recoveredCharacterRuntime)
          && state.recoveredCharacterRuntime === state.characterRuntime,
        recoveryInProgress: Boolean(state.recoveryInProgress),
        lastHardKickoff: state.lastHardKickoff
          ? Object.assign({}, state.lastHardKickoff)
          : null,
        lastAutomaticRecovery: state.lastAutomaticRecovery
          ? Object.assign({}, state.lastAutomaticRecovery)
          : null,
        hasWorldSnapshot: Boolean(state.worldSnapshot)
      };
    }

    return Object.freeze({
      state,
      nowMs,
      currentRuntime,
      currentCharacterRuntime,
      activeShuttleRenderer,
      storage,
      briefingStorageKey,
      briefingAcknowledged,
      acknowledgeBriefing,
      debugState
    });
  }

  const api = Object.freeze({
    create: createPaxScenarioSessionState
  });

  global.MainComputerPaxScenarioSessionState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
