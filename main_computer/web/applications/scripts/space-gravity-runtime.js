(function (global) {
  "use strict";

  const SCHEMA = "game.spacePhysics.v1";
  const DEFINITION_VERSION = "game.spacePhysics.definition.v1";
  const STATE_VERSION = "game.spacePhysics.state.v1";
  const DEFAULT_G = 6.67430e-11;
  const TWO_PI = Math.PI * 2;

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function finiteNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function positiveNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function vector3(value, fallback = [0, 0, 0]) {
    if (!Array.isArray(value) || value.length !== 3) return fallback.slice();
    const result = value.map((entry) => Number(entry));
    return result.every(Number.isFinite) ? result : fallback.slice();
  }

  function add(a, b) {
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  }

  function scale(a, scalar) {
    return [a[0] * scalar, a[1] * scalar, a[2] * scalar];
  }

  function magnitude(a) {
    return Math.hypot(a[0], a[1], a[2]);
  }

  function degreesToRadians(value) {
    return finiteNumber(value, 0) * Math.PI / 180;
  }

  function normalizeAngle(value) {
    let angle = finiteNumber(value, 0) % TWO_PI;
    if (angle < 0) angle += TWO_PI;
    return angle;
  }

  function solveEccentricAnomaly(meanAnomaly, eccentricity) {
    const e = eccentricity;
    const M = normalizeAngle(meanAnomaly);
    let E = e < 0.8 ? M : Math.PI;
    for (let iteration = 0; iteration < 24; iteration += 1) {
      const f = E - e * Math.sin(E) - M;
      const derivative = 1 - e * Math.cos(E);
      const delta = derivative ? f / derivative : 0;
      E -= delta;
      if (Math.abs(delta) < 1e-13) break;
    }
    return E;
  }

  function rotateOrbitalVector(vector, longitudeAscendingNode, inclination, argumentPeriapsis) {
    const [x, y, z] = vector;
    const cO = Math.cos(longitudeAscendingNode);
    const sO = Math.sin(longitudeAscendingNode);
    const ci = Math.cos(inclination);
    const si = Math.sin(inclination);
    const cw = Math.cos(argumentPeriapsis);
    const sw = Math.sin(argumentPeriapsis);

    return [
      (cO * cw - sO * sw * ci) * x + (-cO * sw - sO * cw * ci) * y + (sO * si) * z,
      (sO * cw + cO * sw * ci) * x + (-sO * sw + cO * cw * ci) * y + (-cO * si) * z,
      (sw * si) * x + (cw * si) * y + ci * z
    ];
  }

  function keplerianRelativeState(orbit, gravitationalConstant, centralMassKg, bodyMassKg = 0) {
    const raw = objectValue(orbit);
    const semiMajorAxisM = positiveNumber(raw.semiMajorAxisM, 0);
    const eccentricity = finiteNumber(raw.eccentricity, 0);
    if (!semiMajorAxisM) throw new Error("orbit.semiMajorAxisM must be positive");
    if (!(eccentricity >= 0 && eccentricity < 1)) throw new Error("orbit.eccentricity must be >= 0 and < 1");
    if (!(centralMassKg > 0)) throw new Error("orbit primary must provide positive mass");

    const meanAnomaly = degreesToRadians(raw.meanAnomalyDeg);
    const eccentricAnomaly = solveEccentricAnomaly(meanAnomaly, eccentricity);
    const cosE = Math.cos(eccentricAnomaly);
    const sinE = Math.sin(eccentricAnomaly);
    const beta = Math.sqrt(1 - eccentricity * eccentricity);
    const denominator = 1 - eccentricity * cosE;
    const mu = gravitationalConstant * (centralMassKg + Math.max(0, finiteNumber(bodyMassKg, 0)));
    const meanMotion = Math.sqrt(mu / Math.pow(semiMajorAxisM, 3));

    const planarPosition = [
      semiMajorAxisM * (cosE - eccentricity),
      semiMajorAxisM * beta * sinE,
      0
    ];
    const planarVelocity = [
      -semiMajorAxisM * meanMotion * sinE / denominator,
      semiMajorAxisM * meanMotion * beta * cosE / denominator,
      0
    ];

    const longitudeAscendingNode = degreesToRadians(raw.longitudeAscendingNodeDeg);
    const inclination = degreesToRadians(raw.inclinationDeg);
    const argumentPeriapsis = degreesToRadians(raw.argumentPeriapsisDeg);
    return {
      positionM: rotateOrbitalVector(planarPosition, longitudeAscendingNode, inclination, argumentPeriapsis),
      velocityMps: rotateOrbitalVector(planarVelocity, longitudeAscendingNode, inclination, argumentPeriapsis)
    };
  }

  function validateDefinition(value) {
    const definition = objectValue(value);
    const errors = [];
    const warnings = [];
    if (definition.schema !== SCHEMA) errors.push(`schema must be ${SCHEMA}`);
    if (definition.definitionVersion !== DEFINITION_VERSION) errors.push(`definitionVersion must be ${DEFINITION_VERSION}`);
    if (definition.stateVersion !== STATE_VERSION) errors.push(`stateVersion must be ${STATE_VERSION}`);
    if (!(positiveNumber(definition.gravitationalConstant, 0) > 0)) errors.push("gravitationalConstant must be positive");
    if (!(positiveNumber(definition.fixedStepSeconds, 0) > 0)) errors.push("fixedStepSeconds must be positive");
    if (!(positiveNumber(definition.timeScale, 0) > 0)) errors.push("timeScale must be positive");

    const systems = Array.isArray(definition.systems) ? definition.systems : [];
    if (!systems.length) errors.push("systems must be a non-empty list");
    const systemIds = new Set();
    systems.forEach((system, systemIndex) => {
      const rawSystem = objectValue(system);
      const systemId = stringValue(rawSystem.id);
      const path = systemId || `systems[${systemIndex}]`;
      if (!systemId) errors.push(`systems[${systemIndex}] is missing id`);
      else if (systemIds.has(systemId)) errors.push(`duplicate system id ${systemId}`);
      else systemIds.add(systemId);

      const anchors = Array.isArray(rawSystem.anchors) ? rawSystem.anchors : [];
      const bodies = Array.isArray(rawSystem.bodies) ? rawSystem.bodies : [];
      if (!bodies.length) errors.push(`${path}.bodies must be non-empty`);
      const referenceIds = new Set();
      anchors.forEach((anchor, anchorIndex) => {
        const rawAnchor = objectValue(anchor);
        const id = stringValue(rawAnchor.id);
        if (!id) errors.push(`${path}.anchors[${anchorIndex}] is missing id`);
        else if (referenceIds.has(id)) errors.push(`${path} has duplicate reference id ${id}`);
        else referenceIds.add(id);
        if (!(positiveNumber(rawAnchor.massKg, 0) > 0)) errors.push(`${id || path}.massKg must be positive`);
        if (!Array.isArray(rawAnchor.positionM) || rawAnchor.positionM.length !== 3) errors.push(`${id || path}.positionM must be a 3-vector`);
        if (!Array.isArray(rawAnchor.velocityMps) || rawAnchor.velocityMps.length !== 3) errors.push(`${id || path}.velocityMps must be a 3-vector`);
      });

      bodies.forEach((body, bodyIndex) => {
        const rawBody = objectValue(body);
        const id = stringValue(rawBody.id);
        const bodyPath = id || `${path}.bodies[${bodyIndex}]`;
        if (!id) errors.push(`${path}.bodies[${bodyIndex}] is missing id`);
        else if (referenceIds.has(id)) errors.push(`${path} has duplicate reference id ${id}`);
        else referenceIds.add(id);
        if (finiteNumber(rawBody.massKg, -1) < 0) errors.push(`${bodyPath}.massKg must be >= 0`);
        if (!(positiveNumber(rawBody.radiusM, 0) > 0)) errors.push(`${bodyPath}.radiusM must be positive`);
        const hasState = Boolean(rawBody.initialState);
        const hasOrbit = Boolean(rawBody.orbit);
        if (hasState === hasOrbit) errors.push(`${bodyPath} must declare exactly one of initialState or orbit`);
        if (hasState) {
          const state = objectValue(rawBody.initialState);
          if (!Array.isArray(state.positionM) || state.positionM.length !== 3) errors.push(`${bodyPath}.initialState.positionM must be a 3-vector`);
          if (!Array.isArray(state.velocityMps) || state.velocityMps.length !== 3) errors.push(`${bodyPath}.initialState.velocityMps must be a 3-vector`);
        }
        if (hasOrbit) {
          const orbit = objectValue(rawBody.orbit);
          if (!stringValue(orbit.primaryId)) errors.push(`${bodyPath}.orbit.primaryId is required`);
          if (!(positiveNumber(orbit.semiMajorAxisM, 0) > 0)) errors.push(`${bodyPath}.orbit.semiMajorAxisM must be positive`);
          const e = finiteNumber(orbit.eccentricity, -1);
          if (!(e >= 0 && e < 1)) errors.push(`${bodyPath}.orbit.eccentricity must be >= 0 and < 1`);
        }
      });

      bodies.forEach((body) => {
        const rawBody = objectValue(body);
        if (!rawBody.orbit) return;
        const primaryId = stringValue(objectValue(rawBody.orbit).primaryId);
        if (primaryId && !referenceIds.has(primaryId)) errors.push(`${stringValue(rawBody.id)} references missing orbit primary ${primaryId}`);
      });
    });

    return {ok: errors.length === 0, errors, warnings};
  }

  class SpacePhysicsDefinitionError extends Error {
    constructor(report) {
      super(`Invalid space-physics definition: ${(report?.errors || []).join("; ")}`);
      this.name = "SpacePhysicsDefinitionError";
      this.report = clone(report);
    }
  }

  function instantiateSystem(systemDefinition, gravitationalConstant) {
    const system = objectValue(systemDefinition);
    const anchors = new Map();
    (Array.isArray(system.anchors) ? system.anchors : []).forEach((anchor) => {
      const raw = objectValue(anchor);
      anchors.set(stringValue(raw.id), {
        id: stringValue(raw.id),
        massKg: positiveNumber(raw.massKg, 0),
        positionM: vector3(raw.positionM),
        velocityMps: vector3(raw.velocityMps)
      });
    });

    const definitions = new Map();
    (Array.isArray(system.bodies) ? system.bodies : []).forEach((body) => definitions.set(stringValue(body.id), objectValue(body)));
    const resolved = new Map();

    function resolveBody(bodyId, stack = []) {
      if (resolved.has(bodyId)) return resolved.get(bodyId);
      const raw = definitions.get(bodyId);
      if (!raw) throw new Error(`missing body definition ${bodyId}`);
      if (stack.includes(bodyId)) throw new Error(`orbital dependency cycle: ${[...stack, bodyId].join(" -> ")}`);

      let positionM;
      let velocityMps;
      if (raw.initialState) {
        const initialState = objectValue(raw.initialState);
        positionM = vector3(initialState.positionM);
        velocityMps = vector3(initialState.velocityMps);
      } else {
        const orbit = objectValue(raw.orbit);
        const primaryId = stringValue(orbit.primaryId);
        const primary = anchors.get(primaryId) || resolveBody(primaryId, [...stack, bodyId]);
        const relative = keplerianRelativeState(
          orbit,
          gravitationalConstant,
          positiveNumber(primary.massKg, 0),
          Math.max(0, finiteNumber(raw.massKg, 0))
        );
        positionM = add(primary.positionM, relative.positionM);
        velocityMps = add(primary.velocityMps, relative.velocityMps);
      }

      const body = {
        id: bodyId,
        label: stringValue(raw.label || bodyId),
        kind: stringValue(raw.kind || "body"),
        massKg: Math.max(0, finiteNumber(raw.massKg, 0)),
        radiusM: positiveNumber(raw.radiusM, 1),
        positionM,
        velocityMps
      };
      resolved.set(bodyId, body);
      return body;
    }

    definitions.forEach((_body, bodyId) => resolveBody(bodyId));
    return {
      id: stringValue(system.id),
      epochSeconds: finiteNumber(system.epochSeconds, 0),
      simulationSeconds: 0,
      bodies: [...resolved.values()].map(clone)
    };
  }

  function accelerationsForBodies(bodies, gravitationalConstant) {
    const accelerations = bodies.map(() => [0, 0, 0]);
    for (let i = 0; i < bodies.length; i += 1) {
      const target = bodies[i];
      for (let j = 0; j < bodies.length; j += 1) {
        if (i === j) continue;
        const source = bodies[j];
        if (!(source.massKg > 0)) continue;
        const dx = source.positionM[0] - target.positionM[0];
        const dy = source.positionM[1] - target.positionM[1];
        const dz = source.positionM[2] - target.positionM[2];
        const distanceSquared = dx * dx + dy * dy + dz * dz;
        if (!(distanceSquared > 0)) throw new Error(`coincident gravity bodies ${target.id} and ${source.id}`);
        const inverseDistance = 1 / Math.sqrt(distanceSquared);
        const factor = gravitationalConstant * source.massKg * inverseDistance * inverseDistance * inverseDistance;
        accelerations[i][0] += dx * factor;
        accelerations[i][1] += dy * factor;
        accelerations[i][2] += dz * factor;
      }
    }
    return accelerations;
  }

  function velocityVerletStep(state, dtSeconds, gravitationalConstant) {
    const dt = positiveNumber(dtSeconds, 0);
    if (!dt) return state;
    const bodies = state.bodies;
    const before = accelerationsForBodies(bodies, gravitationalConstant);
    const halfDtSquared = 0.5 * dt * dt;

    bodies.forEach((body, index) => {
      body.positionM[0] += body.velocityMps[0] * dt + before[index][0] * halfDtSquared;
      body.positionM[1] += body.velocityMps[1] * dt + before[index][1] * halfDtSquared;
      body.positionM[2] += body.velocityMps[2] * dt + before[index][2] * halfDtSquared;
    });

    const after = accelerationsForBodies(bodies, gravitationalConstant);
    const halfDt = 0.5 * dt;
    bodies.forEach((body, index) => {
      body.velocityMps[0] += (before[index][0] + after[index][0]) * halfDt;
      body.velocityMps[1] += (before[index][1] + after[index][1]) * halfDt;
      body.velocityMps[2] += (before[index][2] + after[index][2]) * halfDt;
    });
    state.simulationSeconds += dt;
    return state;
  }

  function diagnostics(state, gravitationalConstant) {
    const bodies = state?.bodies || [];
    let totalMassKg = 0;
    const centerNumerator = [0, 0, 0];
    const momentum = [0, 0, 0];
    let kineticEnergyJ = 0;
    let potentialEnergyJ = 0;

    bodies.forEach((body) => {
      if (!(body.massKg > 0)) return;
      totalMassKg += body.massKg;
      centerNumerator[0] += body.positionM[0] * body.massKg;
      centerNumerator[1] += body.positionM[1] * body.massKg;
      centerNumerator[2] += body.positionM[2] * body.massKg;
      momentum[0] += body.velocityMps[0] * body.massKg;
      momentum[1] += body.velocityMps[1] * body.massKg;
      momentum[2] += body.velocityMps[2] * body.massKg;
      kineticEnergyJ += 0.5 * body.massKg * (
        body.velocityMps[0] * body.velocityMps[0]
        + body.velocityMps[1] * body.velocityMps[1]
        + body.velocityMps[2] * body.velocityMps[2]
      );
    });

    for (let i = 0; i < bodies.length; i += 1) {
      if (!(bodies[i].massKg > 0)) continue;
      for (let j = i + 1; j < bodies.length; j += 1) {
        if (!(bodies[j].massKg > 0)) continue;
        const distance = magnitude([
          bodies[j].positionM[0] - bodies[i].positionM[0],
          bodies[j].positionM[1] - bodies[i].positionM[1],
          bodies[j].positionM[2] - bodies[i].positionM[2]
        ]);
        if (distance > 0) potentialEnergyJ -= gravitationalConstant * bodies[i].massKg * bodies[j].massKg / distance;
      }
    }

    return {
      totalMassKg,
      centerOfMassM: totalMassKg > 0 ? scale(centerNumerator, 1 / totalMassKg) : [0, 0, 0],
      momentumKgMps: momentum,
      kineticEnergyJ,
      potentialEnergyJ,
      totalEnergyJ: kineticEnergyJ + potentialEnergyJ
    };
  }

  class SpaceGravityRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.ok) throw new SpacePhysicsDefinitionError(this.report);
      this.projectId = stringValue(options.projectId || "game-project");
      this.gravitationalConstant = positiveNumber(this.definition.gravitationalConstant, DEFAULT_G);
      this.fixedStepSeconds = positiveNumber(this.definition.fixedStepSeconds, 30);
      this.timeScale = positiveNumber(this.definition.timeScale, 1);
      this.maxSubstepsPerUpdate = Math.max(1, Math.floor(positiveNumber(this.definition.maxSubstepsPerUpdate, 120)));
      this.maxRealDeltaSeconds = positiveNumber(this.definition.maxRealDeltaSeconds, 0.25);
      this.systemDefinitions = new Map();
      this.systemStates = new Map();
      (this.definition.systems || []).forEach((system) => this.systemDefinitions.set(stringValue(system.id), clone(system)));
      this.activeSystemId = "";
      this.accumulatorSeconds = 0;
    }

    hasSystem(systemId) {
      return this.systemDefinitions.has(stringValue(systemId));
    }

    ensureSystem(systemId) {
      const id = stringValue(systemId);
      if (!this.systemDefinitions.has(id)) return null;
      if (!this.systemStates.has(id)) {
        this.systemStates.set(id, instantiateSystem(this.systemDefinitions.get(id), this.gravitationalConstant));
      }
      return this.systemStates.get(id);
    }

    setActiveSystem(systemId) {
      const id = stringValue(systemId);
      if (id === this.activeSystemId) return this.ensureSystem(id);
      this.activeSystemId = this.hasSystem(id) ? id : "";
      this.accumulatorSeconds = 0;
      return this.activeSystemId ? this.ensureSystem(this.activeSystemId) : null;
    }

    body(bodyId, systemId = this.activeSystemId) {
      const state = this.ensureSystem(systemId);
      if (!state) return null;
      const body = state.bodies.find((candidate) => candidate.id === stringValue(bodyId));
      return body ? clone(body) : null;
    }

    advancePhysicsSeconds(seconds, systemId = this.activeSystemId) {
      const state = this.ensureSystem(systemId);
      let remaining = Math.max(0, finiteNumber(seconds, 0));
      if (!state || !remaining) return this.snapshot(systemId);
      const step = this.fixedStepSeconds;
      while (remaining >= step) {
        velocityVerletStep(state, step, this.gravitationalConstant);
        remaining -= step;
      }
      if (remaining > 1e-9) velocityVerletStep(state, remaining, this.gravitationalConstant);
      return this.snapshot(systemId);
    }

    advanceRealSeconds(realSeconds) {
      if (!this.activeSystemId) return null;
      const boundedRealSeconds = Math.min(this.maxRealDeltaSeconds, Math.max(0, finiteNumber(realSeconds, 0)));
      this.accumulatorSeconds += boundedRealSeconds * this.timeScale;
      const state = this.ensureSystem(this.activeSystemId);
      let steps = 0;
      while (this.accumulatorSeconds >= this.fixedStepSeconds && steps < this.maxSubstepsPerUpdate) {
        velocityVerletStep(state, this.fixedStepSeconds, this.gravitationalConstant);
        this.accumulatorSeconds -= this.fixedStepSeconds;
        steps += 1;
      }
      if (steps >= this.maxSubstepsPerUpdate && this.accumulatorSeconds >= this.fixedStepSeconds) {
        this.accumulatorSeconds %= this.fixedStepSeconds;
      }
      return this.snapshot(this.activeSystemId);
    }

    snapshot(systemId = this.activeSystemId) {
      const id = stringValue(systemId);
      const state = id ? this.ensureSystem(id) : null;
      if (!state) {
        return {
          enabled: this.definition.enabled !== false,
          schema: STATE_VERSION,
          projectId: this.projectId,
          activeSystemId: "",
          simulated: false,
          bodyCount: 0,
          simulationSeconds: 0,
          bodies: [],
          diagnostics: null,
          validation: clone(this.report)
        };
      }
      return {
        enabled: this.definition.enabled !== false,
        schema: STATE_VERSION,
        projectId: this.projectId,
        activeSystemId: id,
        simulated: true,
        bodyCount: state.bodies.length,
        epochSeconds: state.epochSeconds,
        simulationSeconds: state.simulationSeconds,
        bodies: clone(state.bodies),
        diagnostics: diagnostics(state, this.gravitationalConstant),
        validation: clone(this.report)
      };
    }
  }

  function definitionFromProject(project) {
    return objectValue(objectValue(project).metadata).spacePhysics || null;
  }

  function create(definition, options = {}) {
    return new SpaceGravityRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    DEFAULT_G,
    SpacePhysicsDefinitionError,
    SpaceGravityRuntime,
    definitionFromProject,
    validateDefinition,
    keplerianRelativeState,
    accelerationsForBodies,
    velocityVerletStep,
    diagnostics,
    create
  };

  global.MainComputerSpaceGravityRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
