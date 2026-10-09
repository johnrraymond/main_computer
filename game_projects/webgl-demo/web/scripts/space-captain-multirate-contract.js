;(function () {
  const SCHEMA = "game.spaceCaptainMultirateContract.v1";
  const EPSILON_SECONDS = 1e-9;

  const DEFAULTS = Object.freeze({
    tacticalSliceSeconds: 5,
    physicsStepSeconds: 0.1,
    viewHalfWidthM: 3500,
    viewHalfHeightM: 2000,
    softZoneNormalized: 0.16,
    hardLockEnvelopeNormalized: 0.45,
    cameraResponseSeconds: 0.35,
    projectileSpeedMps: 6000,
    impactDeltaVMps: 0.75,
  });

  const TESTED_ENVELOPE = Object.freeze({
    tacticalSliceSeconds: Object.freeze([1, 2, 3, 4, 5]),
    physicsStepSeconds: Object.freeze([0.05, 0.1, 0.2]),
    renderHz: Object.freeze([30, 60, 120, 165]),
  });

  const RULES = Object.freeze({
    tacticalGrid: "immutable-configured-grid",
    physicsGrid: "immutable-configured-grid",
    exactEventAnchors: true,
    renderCadence: "requestAnimationFrame",
    renderMayMutateAuthority: false,
    predictionMayMutateAuthority: false,
    ordinaryTacticalPositionContinuity: true,
    ordinaryTacticalVelocityContinuity: true,
    impactPositionContinuity: true,
    impactVelocityDiscontinuityAllowed: true,
    cameraMode: "soft-target-lock",
    cameraMayMutateAuthority: false,
  });

  const POSITIVE_FIELDS = Object.freeze([
    "tacticalSliceSeconds",
    "physicsStepSeconds",
    "viewHalfWidthM",
    "viewHalfHeightM",
    "cameraResponseSeconds",
    "projectileSpeedMps",
  ]);

  const NUMERIC_FIELDS = Object.freeze(Object.keys(DEFAULTS));

  function finiteNumber(name, value) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`SPACE_CAPTAIN_MULTIRATE_CONTRACT_INVALID_${name}: expected a finite number`);
    return number;
  }

  function resolveConfig(options = {}) {
    const resolved = {};
    for (const key of NUMERIC_FIELDS) {
      const raw = Object.prototype.hasOwnProperty.call(options || {}, key) ? options[key] : DEFAULTS[key];
      resolved[key] = finiteNumber(key, raw);
    }
    for (const key of POSITIVE_FIELDS) {
      if (!(resolved[key] > 0)) throw new Error(`SPACE_CAPTAIN_MULTIRATE_CONTRACT_INVALID_${key}: expected > 0`);
    }
    if (!(resolved.physicsStepSeconds < resolved.tacticalSliceSeconds)) {
      throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_INVALID_TIMING: physicsStepSeconds must be smaller than tacticalSliceSeconds");
    }
    if (resolved.softZoneNormalized < 0 || resolved.softZoneNormalized >= 1) {
      throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_INVALID_softZoneNormalized: expected 0 <= value < 1");
    }
    if (resolved.hardLockEnvelopeNormalized <= resolved.softZoneNormalized || resolved.hardLockEnvelopeNormalized > 1) {
      throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_INVALID_hardLockEnvelopeNormalized: expected soft zone < value <= 1");
    }
    if (resolved.impactDeltaVMps < 0) {
      throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_INVALID_impactDeltaVMps: expected >= 0");
    }
    return Object.freeze(resolved);
  }

  function tacticalBoundaryIndex(atSeconds, config) {
    const step = resolveConfig(config).tacticalSliceSeconds;
    return Math.round(Number(atSeconds) / step);
  }

  function isTacticalBoundary(atSeconds, config, epsilon = EPSILON_SECONDS) {
    const step = resolveConfig(config).tacticalSliceSeconds;
    const index = Math.round(Number(atSeconds) / step);
    return Math.abs(Number(atSeconds) - index * step) <= Number(epsilon);
  }

  function nextTacticalBoundaryAfter(atSeconds, config) {
    const step = resolveConfig(config).tacticalSliceSeconds;
    const index = Math.floor((Number(atSeconds) + EPSILON_SECONDS) / step) + 1;
    return index * step;
  }

  function isPhysicsBoundary(atSeconds, config, epsilon = EPSILON_SECONDS) {
    const step = resolveConfig(config).physicsStepSeconds;
    const index = Math.round(Number(atSeconds) / step);
    return Math.abs(Number(atSeconds) - index * step) <= Number(epsilon);
  }

  function nextPhysicsBoundaryAfter(atSeconds, config) {
    const step = resolveConfig(config).physicsStepSeconds;
    const index = Math.floor((Number(atSeconds) + EPSILON_SECONDS) / step) + 1;
    return index * step;
  }

  function insideTestedEnvelope(config) {
    const resolved = resolveConfig(config);
    return TESTED_ENVELOPE.tacticalSliceSeconds.includes(resolved.tacticalSliceSeconds)
      && TESTED_ENVELOPE.physicsStepSeconds.includes(resolved.physicsStepSeconds);
  }

  globalThis.MainComputerSpaceCaptainMultirateContract = Object.freeze({
    SCHEMA,
    EPSILON_SECONDS,
    DEFAULTS,
    TESTED_ENVELOPE,
    RULES,
    resolveConfig,
    tacticalBoundaryIndex,
    isTacticalBoundary,
    nextTacticalBoundaryAfter,
    isPhysicsBoundary,
    nextPhysicsBoundaryAfter,
    insideTestedEnvelope,
  });
})();
