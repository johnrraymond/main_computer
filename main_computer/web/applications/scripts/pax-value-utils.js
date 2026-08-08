(function (global) {
  "use strict";

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function vector3(value, fallback = [0, 0, 0]) {
    const source = Array.isArray(value) ? value : fallback;
    return [
      finiteNumber(source[0], fallback[0] || 0),
      finiteNumber(source[1], fallback[1] || 0),
      finiteNumber(source[2], fallback[2] || 0)
    ];
  }

  function freezeVector3(value, fallback = [0, 0, 0]) {
    return Object.freeze(vector3(value, fallback));
  }

  function cloneSnapshot(snapshot) {
    try {
      return JSON.parse(JSON.stringify(snapshot));
    } catch {
      return snapshot;
    }
  }

  function nowMs(options = {}) {
    if (Number.isFinite(Number(options.nowMs))) return Number(options.nowMs);
    const source = global.performance;
    if (source && typeof source.now === "function") return source.now();
    return Date.now();
  }

  const api = Object.freeze({
    objectValue,
    arrayValue,
    stringValue,
    finiteNumber,
    vector3,
    freezeVector3,
    cloneSnapshot,
    nowMs
  });

  global.MainComputerPaxValueUtils = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
