"use strict";
/* Explicit host adapter for the existing Calculator core's pure arithmetic.
 * This file is not part of MCEL's generic renderer and grants no IO authority.
 * The authoring IR cannot directly invoke a global method; the host must
 * provide this adapter when mounting an approved calculator application.
 */
(function (global) {
  function create(options={}) {
    const core=options.core || global.MainComputerCalculatorCore;
    if (!core || typeof core.evaluateCalculatorExpression !== "function") {
      throw Error("MCEL_CALCULATOR_PORT:core-unavailable");
    }
    function permitted(req) {
      return req && req.appId === "phase2-calculator" &&
        req.intentId === "intent:compute" && req.effectId === "effect:compute.request" &&
        req.capabilityId === "capability:arithmetic" &&
        req.operation === "compute" && req.runtimeMethod === "evaluateExpression" &&
        req.risk === "external-read" && req.arguments &&
        typeof req.arguments.expression === "string" && Object.keys(req.arguments).length === 1;
    }
    return Object.freeze({
      authorize: async request => permitted(request),
      invoke: async request => {
        if (!permitted(request)) throw Error("MCEL_CALCULATOR_PORT:operation-denied");
        const evaluation=core.evaluateCalculatorExpression(request.arguments.expression);
        if (!evaluation || evaluation.ok !== true) throw Error("MCEL_CALCULATOR_PORT:invalid-expression");
        if (!Number.isSafeInteger(evaluation.value)) throw Error("MCEL_CALCULATOR_PORT:not-integer");
        return evaluation.value;
      }
    });
  }
  global.McelCalculatorCoreCapabilityPort=Object.freeze({create});
})(typeof globalThis !== "undefined" ? globalThis : this);
