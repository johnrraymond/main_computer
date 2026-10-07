(function (global) {
  "use strict";

  const SCHEMA = "game.spaceUniverse.v1";
  const DEFINITION_VERSION = "game.spaceUniverse.definition.v1";
  const STATE_VERSION = "game.spaceUniverse.state.v1";
  const MODEL = "poincare-ball";
  const EPSILON = 1e-12;

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
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

  function positiveNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function vector3(value, fallback = [0, 0, 0]) {
    if (!Array.isArray(value) || value.length !== 3) return fallback.slice();
    const parsed = value.map(Number);
    return parsed.every(Number.isFinite) ? parsed : fallback.slice();
  }

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function magnitudeSquared(a) {
    return dot(a, a);
  }

  function magnitude(a) {
    return Math.sqrt(magnitudeSquared(a));
  }

  function scale(a, scalar) {
    return [a[0] * scalar, a[1] * scalar, a[2] * scalar];
  }

  function negate(a) {
    return [-a[0], -a[1], -a[2]];
  }

  function normalize(a, fallback = [1, 0, 0]) {
    const length = magnitude(a);
    return length > EPSILON ? scale(a, 1 / length) : fallback.slice();
  }

  function clampBall(point) {
    const result = vector3(point);
    const norm = magnitude(result);
    if (norm < 1 - 1e-12) return result;
    return scale(result, (1 - 1e-12) / Math.max(norm, EPSILON));
  }

  // Möbius addition for the unit Poincaré ball (curvature -1).
  // Applying (-observer) ⊕ target recenters the manifold on the observer system.
  function mobiusAdd(xRaw, yRaw) {
    const x = clampBall(xRaw);
    const y = clampBall(yRaw);
    const x2 = magnitudeSquared(x);
    const y2 = magnitudeSquared(y);
    const xy = dot(x, y);
    const denominator = 1 + 2 * xy + x2 * y2;
    if (Math.abs(denominator) < EPSILON) throw new Error("degenerate Poincare-ball Mobius addition");
    const first = 1 + 2 * xy + y2;
    const second = 1 - x2;
    return clampBall([
      (first * x[0] + second * y[0]) / denominator,
      (first * x[1] + second * y[1]) / denominator,
      (first * x[2] + second * y[2]) / denominator
    ]);
  }

  function mobiusScalarMultiply(scalarRaw, pointRaw) {
    const scalar = finiteNumber(scalarRaw, 0);
    const point = clampBall(pointRaw);
    const norm = magnitude(point);
    if (norm < EPSILON || scalar === 0) return [0, 0, 0];
    const radial = Math.tanh(scalar * Math.atanh(Math.min(1 - 1e-12, norm)));
    return scale(point, radial / norm);
  }

  function poincareCenteredVector(observerPoint, targetPoint) {
    return mobiusAdd(negate(clampBall(observerPoint)), clampBall(targetPoint));
  }

  function poincareDistance(observerPoint, targetPoint) {
    const centered = poincareCenteredVector(observerPoint, targetPoint);
    const norm = Math.min(1 - 1e-12, magnitude(centered));
    return 2 * Math.atanh(norm);
  }

  // Incremental gyrovector mean. This is the estimator boundary: when later observations
  // yield slightly different candidate anchors, they can be fused without arithmetic-
  // averaging coordinates in curved space.
  function hyperbolicMean(points) {
    const usable = (Array.isArray(points) ? points : []).map(clampBall);
    if (!usable.length) return null;
    let mean = usable[0];
    for (let index = 1; index < usable.length; index += 1) {
      const centeredDelta = poincareCenteredVector(mean, usable[index]);
      mean = mobiusAdd(mean, mobiusScalarMultiply(1 / (index + 1), centeredDelta));
    }
    return clampBall(mean);
  }

  function validateDefinition(value, navigationDefinition = null) {
    const definition = objectValue(value);
    const errors = [];
    const warnings = [];
    if (definition.schema !== SCHEMA) errors.push(`schema must be ${SCHEMA}`);
    if (definition.definitionVersion !== DEFINITION_VERSION) errors.push(`definitionVersion must be ${DEFINITION_VERSION}`);
    if (definition.stateVersion !== STATE_VERSION) errors.push(`stateVersion must be ${STATE_VERSION}`);
    if (stringValue(definition.model) !== MODEL) errors.push(`model must be ${MODEL}`);
    if (!(positiveNumber(definition.lightYearsPerHyperbolicUnit, 0) > 0)) errors.push("lightYearsPerHyperbolicUnit must be positive");
    if (!(positiveNumber(definition.secondsPerWorldTimeUnit, 0) > 0)) errors.push("secondsPerWorldTimeUnit must be positive");
    if (!(positiveNumber(definition.realtimeTimeScale, 0) > 0)) errors.push("realtimeTimeScale must be positive");
    if (!stringValue(definition.observerBodyId)) errors.push("observerBodyId is required");

    const anchors = Array.isArray(definition.systemAnchors) ? definition.systemAnchors : [];
    if (!anchors.length) errors.push("systemAnchors must be a non-empty list");
    const anchorIds = new Set();
    anchors.forEach((anchor, index) => {
      const raw = objectValue(anchor);
      const id = stringValue(raw.id);
      if (!id) errors.push(`systemAnchors[${index}] is missing id`);
      else if (anchorIds.has(id)) errors.push(`duplicate system anchor ${id}`);
      else anchorIds.add(id);
      const point = vector3(raw.poincare, [NaN, NaN, NaN]);
      if (!point.every(Number.isFinite)) errors.push(`${id || `systemAnchors[${index}]`}.poincare must be a finite 3-vector`);
      else if (!(magnitude(point) < 1)) errors.push(`${id || `systemAnchors[${index}]`}.poincare must lie inside the unit ball`);
    });

    const navigationSystems = Array.isArray(objectValue(navigationDefinition).systems)
      ? navigationDefinition.systems
      : [];
    navigationSystems.forEach((system) => {
      const id = stringValue(system?.id);
      if (id && !anchorIds.has(id)) errors.push(`missing universe anchor for navigation system ${id}`);
    });
    anchors.forEach((anchor) => {
      const id = stringValue(anchor?.id);
      if (navigationSystems.length && id && !navigationSystems.some((system) => stringValue(system?.id) === id)) {
        warnings.push(`universe anchor ${id} has no navigation-system metadata`);
      }
    });
    return {ok: errors.length === 0, errors, warnings};
  }

  class SpaceUniverseDefinitionError extends Error {
    constructor(report) {
      super(`Invalid space-universe definition: ${(report?.errors || []).join("; ")}`);
      this.name = "SpaceUniverseDefinitionError";
      this.report = clone(report);
    }
  }

  class SpaceUniverseRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.navigationDefinition = clone(objectValue(options.navigationDefinition));
      this.report = validateDefinition(this.definition, this.navigationDefinition);
      if (!this.report.ok) throw new SpaceUniverseDefinitionError(this.report);
      this.projectId = stringValue(options.projectId || "game-project");
      this.lightYearsPerHyperbolicUnit = positiveNumber(this.definition.lightYearsPerHyperbolicUnit, 1);
      this.secondsPerWorldTimeUnit = positiveNumber(this.definition.secondsPerWorldTimeUnit, 3600);
      this.realtimeTimeScale = positiveNumber(this.definition.realtimeTimeScale, 1);
      this.observerBodyId = stringValue(this.definition.observerBodyId);
      this.epochSeconds = finiteNumber(this.definition.epochSeconds, 0);
      this.universeSeconds = this.epochSeconds;
      this.lastNavigationWorldTime = null;
      this.systems = new Map();
      const navigationSystems = Array.isArray(this.navigationDefinition.systems) ? this.navigationDefinition.systems : [];
      navigationSystems.forEach((system) => this.systems.set(stringValue(system.id), clone(system)));
      this.anchors = new Map();
      (this.definition.systemAnchors || []).forEach((anchor) => {
        this.anchors.set(stringValue(anchor.id), clampBall(anchor.poincare));
      });
      this.observations = new Map();
      this.baselines = new Set();
    }

    updateClock(navigationSnapshot, realDeltaSeconds = 0) {
      const navigation = objectValue(navigationSnapshot);
      const worldTime = Math.max(0, finiteNumber(navigation.elapsedWorldTime, 0));
      if (this.lastNavigationWorldTime === null) {
        this.universeSeconds = this.epochSeconds + worldTime * this.secondsPerWorldTimeUnit;
        this.lastNavigationWorldTime = worldTime;
      } else if (worldTime !== this.lastNavigationWorldTime) {
        const deltaWorld = worldTime - this.lastNavigationWorldTime;
        if (deltaWorld >= 0) {
          this.universeSeconds += deltaWorld * this.secondsPerWorldTimeUnit;
        } else {
          // A loaded/reset navigation state is authoritative over an old runtime clock.
          this.universeSeconds = this.epochSeconds + worldTime * this.secondsPerWorldTimeUnit;
        }
        this.lastNavigationWorldTime = worldTime;
      }
      if (!navigation.travelling) {
        this.universeSeconds += Math.max(0, finiteNumber(realDeltaSeconds, 0)) * this.realtimeTimeScale;
      }
      return this.universeSeconds;
    }

    hasSystem(systemId) {
      return this.anchors.has(stringValue(systemId));
    }

    system(systemId) {
      const id = stringValue(systemId);
      const metadata = this.systems.get(id) || null;
      const anchor = this.anchors.get(id) || null;
      if (!anchor) return null;
      return {
        id,
        label: stringValue(metadata?.label || id),
        region: stringValue(metadata?.region),
        poincare: anchor.slice(),
        stars: clone(Array.isArray(metadata?.stars) ? metadata.stars : []),
        planets: clone(Array.isArray(metadata?.planets) ? metadata.planets : [])
      };
    }

    candidateAnchorFromObservation(observation) {
      const raw = objectValue(observation);
      const observerAnchor = this.anchors.get(stringValue(raw.observerSystemId));
      if (!observerAnchor) return null;
      const centered = vector3(raw.centeredPosition, [NaN, NaN, NaN]);
      if (!centered.every(Number.isFinite) || magnitude(centered) >= 1) return null;
      return mobiusAdd(observerAnchor, centered);
    }

    estimateSystemAnchor(systemId) {
      const id = stringValue(systemId);
      const records = this.observations.get(id) || [];
      const candidates = records
        .map((observation) => this.candidateAnchorFromObservation(observation))
        .filter(Boolean);
      return hyperbolicMean(candidates) || (this.anchors.get(id)?.slice() ?? null);
    }

    relativeSystemObservation(observerSystemId, targetSystemId, useEstimator = true) {
      const observerId = stringValue(observerSystemId);
      const targetId = stringValue(targetSystemId);
      const observerAnchor = this.anchors.get(observerId);
      const targetAnchor = useEstimator ? this.estimateSystemAnchor(targetId) : this.anchors.get(targetId)?.slice();
      if (!observerAnchor || !targetAnchor) return null;
      if (observerId === targetId) {
        return {
          observerSystemId: observerId,
          targetSystemId: targetId,
          centeredPosition: [0, 0, 0],
          direction: [0, 0, -1],
          hyperbolicDistance: 0,
          distanceLightYears: 0,
          baselineCount: (this.observations.get(targetId) || []).length
        };
      }
      const centeredPosition = poincareCenteredVector(observerAnchor, targetAnchor);
      const hyperbolicDistance = 2 * Math.atanh(Math.min(1 - 1e-12, magnitude(centeredPosition)));
      return {
        observerSystemId: observerId,
        targetSystemId: targetId,
        centeredPosition,
        direction: normalize(centeredPosition),
        hyperbolicDistance,
        distanceLightYears: hyperbolicDistance * this.lightYearsPerHyperbolicUnit,
        baselineCount: (this.observations.get(targetId) || []).length
      };
    }

    observeCatalogFromSystem(observerSystemId) {
      const observerId = stringValue(observerSystemId);
      const observerAnchor = this.anchors.get(observerId);
      if (!observerAnchor) return {added: 0, observerSystemId: observerId};
      let added = 0;
      this.anchors.forEach((targetAnchor, targetId) => {
        if (targetId === observerId) return;
        const baselineKey = `${observerId}->${targetId}`;
        if (this.baselines.has(baselineKey)) return;
        const centeredPosition = poincareCenteredVector(observerAnchor, targetAnchor);
        const record = {
          observerSystemId: observerId,
          targetSystemId: targetId,
          universeSeconds: this.universeSeconds,
          centeredPosition,
          hyperbolicDistance: poincareDistance(observerAnchor, targetAnchor)
        };
        if (!this.observations.has(targetId)) this.observations.set(targetId, []);
        this.observations.get(targetId).push(record);
        this.baselines.add(baselineKey);
        added += 1;
      });
      return {added, observerSystemId: observerId};
    }

    snapshot(activeSystemId = "") {
      const id = stringValue(activeSystemId);
      return {
        enabled: this.definition.enabled !== false,
        schema: STATE_VERSION,
        model: MODEL,
        projectId: this.projectId,
        epochSeconds: this.epochSeconds,
        universeSeconds: this.universeSeconds,
        secondsPerWorldTimeUnit: this.secondsPerWorldTimeUnit,
        realtimeTimeScale: this.realtimeTimeScale,
        observerBodyId: this.observerBodyId,
        activeSystemId: id,
        activeSystem: id ? this.system(id) : null,
        systemCount: this.anchors.size,
        baselineCount: this.baselines.size,
        catalog: [...this.anchors.keys()].map((systemId) => {
          const system = this.system(systemId);
          const estimate = this.estimateSystemAnchor(systemId);
          return {
            ...system,
            estimatedPoincare: estimate,
            estimatorCount: (this.observations.get(systemId) || []).length
          };
        }),
        validation: clone(this.report)
      };
    }
  }

  function definitionFromProject(project) {
    return objectValue(objectValue(project).metadata).spaceUniverse || null;
  }

  function create(definition, options = {}) {
    return new SpaceUniverseRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    MODEL,
    SpaceUniverseDefinitionError,
    SpaceUniverseRuntime,
    validateDefinition,
    mobiusAdd,
    mobiusScalarMultiply,
    poincareCenteredVector,
    poincareDistance,
    hyperbolicMean,
    definitionFromProject,
    create
  };

  global.MainComputerSpaceUniverseRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
