/* MCEL renderer-local transition executor v1.
 * This is pure, does not touch the DOM, canonical state, capabilities or I/O.
 * Host projection/mount wiring is a separate (not yet implemented) obligation.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.McelViewTransition = api;
})(typeof globalThis !== "undefined" ? globalThis : null, function () {
  "use strict";
  function refuse(code) { return {ok: false, code, effects: []}; }
  function apply(intent, states, payload, stateDefinitions) {
    if (!intent || !states || typeof states !== "object" || Array.isArray(states) ||
        !payload || typeof payload !== "object" || Array.isArray(payload)) return refuse("invalid-context");
    const transition = intent.viewTransition;
    const input = intent.input;
    const writes = intent.viewWrites;
    if (!transition || transition.kind !== "transition.assign" || transition.invalidInput !== "refuse" ||
        !Array.isArray(input) || input.length !== 1 || !Array.isArray(writes) || writes.length !== 1 ||
        intent.operationKind !== "interaction" || intent.risk !== "local-state" ||
        intent.lane !== "local-view-state" || (intent.writes || []).length || (intent.effectRefs || []).length ||
        !transition.target || transition.target.ref !== writes[0].ref ||
        transition.value?.kind !== "input.read" || transition.value.input?.ref !== input[0].id) {
      return refuse("unproven-view-transition");
    }
    const schema = input[0].schema;
    const choices = schema?.enum;
    if (schema?.kind !== "string" || !Array.isArray(choices) || choices.length === 0 ||
        choices.some(value => typeof value !== "string" || !value) ||
        new Set(choices).size !== choices.length) return refuse("invalid-input-schema");
    const key = input[0].sourceName;
    const selected = payload[key];
    if (typeof selected !== "string" || !choices.includes(selected)) return refuse("invalid-selection");
    const stateId = transition.target.ref;
    const target = Array.isArray(stateDefinitions) ? stateDefinitions.find(item => item?.id === stateId) : null;
    if (!target || target.authority !== "renderer-local" ||
        target.schema?.kind !== "string" || JSON.stringify(target.schema.enum) !== JSON.stringify(choices)) {
      return refuse("unverified-local-state-authority");
    }
    if (!Object.prototype.hasOwnProperty.call(states, stateId)) return refuse("state-missing");
    // Do not mutate the caller's state; caller owns publication and rendering.
    return {ok: true, nextState: {...states, [stateId]: selected}, selected, effects: []};
  }
  return Object.freeze({version: "mcel-view-transition-v1", apply});
});
