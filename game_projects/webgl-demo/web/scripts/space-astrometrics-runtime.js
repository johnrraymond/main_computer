(function (global) {
  "use strict";

  const STATE_VERSION = "game.spaceAstrometrics.state.v1";
  const LIGHT_YEAR_M = 9.4607304725808e15;
  const SOLAR_RADIUS_M = 6.957e8;
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

  function vector3(value, fallback = [0, 0, 0]) {
    if (!Array.isArray(value) || value.length !== 3) return fallback.slice();
    const parsed = value.map(Number);
    return parsed.every(Number.isFinite) ? parsed : fallback.slice();
  }

  function subtract(a, b) {
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  }

  function magnitude(a) {
    return Math.hypot(a[0], a[1], a[2]);
  }

  function normalize(a, fallback = [0, 0, -1]) {
    const length = magnitude(a);
    if (length < EPSILON) return fallback.slice();
    return [a[0] / length, a[1] / length, a[2] / length];
  }

  function angularRadius(radiusM, distanceM) {
    const radius = Math.max(0, finiteNumber(radiusM, 0));
    const distance = Math.max(EPSILON, finiteNumber(distanceM, 0));
    return Math.asin(Math.max(0, Math.min(1, radius / distance)));
  }

  function bodyMetadataMap(navigationSnapshot) {
    const navigation = objectValue(navigationSnapshot);
    const result = new Map();
    (Array.isArray(navigation.currentStars) ? navigation.currentStars : []).forEach((star) => {
      result.set(stringValue(star?.id), clone(star));
    });
    (Array.isArray(navigation.currentPlanets) ? navigation.currentPlanets : []).forEach((planet) => {
      result.set(stringValue(planet?.id), clone(planet));
    });
    return result;
  }

  class SpaceAstrometricsRuntime {
    constructor(options = {}) {
      this.projectId = stringValue(options.projectId || "game-project");
      this.universeRuntime = options.universeRuntime || null;
      if (!this.universeRuntime) throw new Error("space astrometrics requires a universe runtime");
      this.lastSnapshot = null;
    }

    observe({navigationSnapshot = null, physicsSnapshot = null} = {}) {
      const navigation = objectValue(navigationSnapshot);
      const physics = objectValue(physicsSnapshot);
      const currentSystemId = stringValue(navigation.currentSystemId);
      const observerBodyId = stringValue(this.universeRuntime.observerBodyId || "ship.mother");
      if (currentSystemId) this.universeRuntime.observeCatalogFromSystem(currentSystemId);

      const bodies = Array.isArray(physics.bodies) ? physics.bodies : [];
      const observer = bodies.find((body) => stringValue(body?.id) === observerBodyId) || null;
      const observerPositionM = vector3(observer?.positionM);
      const observerVelocityMps = vector3(observer?.velocityMps);
      const metadata = bodyMetadataMap(navigation);
      const visibleObjects = [];

      if (observer) {
        bodies.forEach((body) => {
          const id = stringValue(body?.id);
          if (!id || id === observerBodyId) return;
          const relativePositionM = subtract(vector3(body.positionM), observerPositionM);
          const distanceM = magnitude(relativePositionM);
          if (!(distanceM > EPSILON)) return;
          const visual = objectValue(metadata.get(id));
          visibleObjects.push({
            id,
            label: stringValue(body.label || visual.label || id),
            kind: stringValue(body.kind || "body"),
            source: "local-gravity",
            systemId: currentSystemId,
            local: true,
            positionM: vector3(body.positionM),
            velocityMps: vector3(body.velocityMps),
            relativePositionM,
            direction: normalize(relativePositionM),
            distanceM,
            distanceLightYears: distanceM / LIGHT_YEAR_M,
            radiusM: Math.max(0, finiteNumber(body.radiusM, 0)),
            angularRadiusRad: angularRadius(body.radiusM, distanceM),
            visual: clone(visual)
          });
        });
      }

      const universeSnapshot = this.universeRuntime.snapshot(currentSystemId);
      (universeSnapshot.catalog || []).forEach((system) => {
        if (system.id === currentSystemId) return;
        const relative = this.universeRuntime.relativeSystemObservation(currentSystemId, system.id, true);
        if (!relative || !(relative.distanceLightYears > 0)) return;
        const stars = Array.isArray(system.stars) ? system.stars : [];
        stars.forEach((star) => {
          const radiusM = SOLAR_RADIUS_M * Math.max(0.05, finiteNumber(star?.radiusScale, 1));
          const distanceM = relative.distanceLightYears * LIGHT_YEAR_M;
          visibleObjects.push({
            id: stringValue(star?.id || `${system.id}.catalog-star`),
            label: stringValue(star?.label || system.label || system.id),
            kind: "star",
            source: "hyperbolic-catalog",
            systemId: system.id,
            local: false,
            direction: vector3(relative.direction, [0, 0, -1]),
            centeredHyperbolicPosition: vector3(relative.centeredPosition),
            hyperbolicDistance: relative.hyperbolicDistance,
            distanceLightYears: relative.distanceLightYears,
            distanceM,
            radiusM,
            angularRadiusRad: angularRadius(radiusM, distanceM),
            estimatorCount: relative.baselineCount,
            visual: clone(star)
          });
        });
      });

      const targetId = stringValue(
        objectValue(navigation.currentLocalDestination).parentBodyId
        || navigation.currentPlanetId
      );
      const target = visibleObjects.find((entry) => entry.id === targetId) || null;
      this.lastSnapshot = {
        enabled: true,
        schema: STATE_VERSION,
        projectId: this.projectId,
        universeSeconds: finiteNumber(universeSnapshot.universeSeconds, 0),
        currentSystemId,
        observer: observer ? {
          bodyId: observerBodyId,
          positionM: observerPositionM,
          velocityMps: observerVelocityMps
        } : null,
        targetObjectId: targetId,
        targetObject: target ? clone(target) : null,
        visibleObjectCount: visibleObjects.length,
        localObjectCount: visibleObjects.filter((entry) => entry.local).length,
        catalogObjectCount: visibleObjects.filter((entry) => !entry.local).length,
        baselineCount: finiteNumber(universeSnapshot.baselineCount, 0),
        visibleObjects
      };
      return clone(this.lastSnapshot);
    }

    snapshot() {
      return clone(this.lastSnapshot || {
        enabled: true,
        schema: STATE_VERSION,
        projectId: this.projectId,
        universeSeconds: 0,
        currentSystemId: "",
        observer: null,
        targetObjectId: "",
        targetObject: null,
        visibleObjectCount: 0,
        localObjectCount: 0,
        catalogObjectCount: 0,
        baselineCount: 0,
        visibleObjects: []
      });
    }
  }

  function create(options = {}) {
    return new SpaceAstrometricsRuntime(options);
  }

  const api = {
    STATE_VERSION,
    LIGHT_YEAR_M,
    SOLAR_RADIUS_M,
    SpaceAstrometricsRuntime,
    angularRadius,
    create
  };

  global.MainComputerSpaceAstrometricsRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
