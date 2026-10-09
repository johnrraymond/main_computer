;(function () {
  const PROJECTION = globalThis.MainComputerBridgeViewscreenProjection;
  if (!PROJECTION) throw new Error("BRIDGE_VIEWSCREEN_PROJECTION_MISSING");

  const SCHEMA = "game.bridgeViewscreenPresentation.v1";
  const SYSTEM_STATE_SCHEMA = "game.bridgeViewscreenSystemState.v1";
  const ENCOUNTER_MODE = "encounter";
  const PLANET_MODE = "planet";
  const WARP_MODE = "warp-transit";
  const ASTROMETRIC_MODE = "astrometric";
  const IDLE_MODE = "idle";
  const MODES = Object.freeze([ENCOUNTER_MODE, PLANET_MODE, WARP_MODE, ASTROMETRIC_MODE, IDLE_MODE]);
  const IMPACT_EFFECT_SECONDS = 0.5;
  const DESTRUCTION_EFFECT_SECONDS = 1.9;
  const LOGICAL_VIEWSCREEN_ASPECT = 6.9 / 2.1;

  const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : Number(fallback);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, finite(value)));
  const clamp01 = (value) => clamp(value, 0, 1);
  const clone = (value) => value && typeof value === "object" ? JSON.parse(JSON.stringify(value)) : value;

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
    return value;
  }

  function normalizedPoint(value) {
    const point = Array.isArray(value) ? value : [0, 0];
    return deepFreeze({
      xNormalized: finite(point[0]),
      yNormalized: finite(point[1]),
    });
  }

  function validHex(value, fallback) {
    const text = String(value || "");
    return /^#[0-9a-f]{6}$/i.test(text) ? text : fallback;
  }

  function requireMode(mode) {
    const selected = String(mode || "").trim();
    if (!MODES.includes(selected)) throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_UNSUPPORTED: ${selected || "<empty>"}`);
    return selected;
  }

  function normalizeSystemState(state) {
    if (!state || state.schema !== SYSTEM_STATE_SCHEMA) {
      throw new Error(`BRIDGE_VIEWSCREEN_SYSTEM_STATE_SCHEMA: expected ${SYSTEM_STATE_SCHEMA}`);
    }
    requireMode(state.selectedMode);
    return state;
  }

  function createSystemState(options = {}) {
    return deepFreeze({
      schema: SYSTEM_STATE_SCHEMA,
      selectedMode: requireMode(options.initialMode || ENCOUNTER_MODE),
      selectedAtSimulationSeconds: finite(options.selectedAtSimulationSeconds, 0),
      displayPowered: options.displayPowered !== false,
      selectionRevision: 1,
      displayRevision: 1,
    });
  }

  function selectMode(state, mode, simulationSeconds = null) {
    const current = normalizeSystemState(state);
    const selectedMode = requireMode(mode);
    if (current.selectedMode === selectedMode) return current;
    return deepFreeze({
      ...current,
      selectedMode,
      selectedAtSimulationSeconds: simulationSeconds === null
        ? current.selectedAtSimulationSeconds
        : finite(simulationSeconds),
      selectionRevision: Number(current.selectionRevision || 0) + 1,
    });
  }

  function setDisplayPowered(state, powered) {
    const current = normalizeSystemState(state);
    const nextPowered = Boolean(powered);
    if (Boolean(current.displayPowered) === nextPowered) return current;
    return deepFreeze({
      ...current,
      displayPowered: nextPowered,
      displayRevision: Number(current.displayRevision || 0) + 1,
    });
  }

  function basePresentation(system, mode, simulationSeconds) {
    return {
      schema: SCHEMA,
      mode,
      display: {powered: Boolean(system.displayPowered)},
      selection: {
        mode: system.selectedMode,
        selectedAtSimulationSeconds: finite(system.selectedAtSimulationSeconds),
        selectionRevision: Number(system.selectionRevision || 0),
      },
      time: {simulationSeconds: finite(simulationSeconds)},
    };
  }

  function projectileEffects(snapshot, simulationSeconds, fromScreen, toScreen) {
    return (snapshot.weapons?.projectiles || [])
      .filter((shot) => {
        const fired = Number(shot.firedAtSeconds);
        const impact = Number(shot.impactAtSeconds);
        return Number.isFinite(fired) && Number.isFinite(impact)
          && simulationSeconds + 1e-9 >= fired
          && simulationSeconds < impact - 1e-9;
      })
      .map((shot) => {
        const fired = finite(shot.firedAtSeconds);
        const impact = finite(shot.impactAtSeconds, fired);
        const duration = Math.max(1e-9, impact - fired);
        const progress = clamp01((simulationSeconds - fired) / duration);
        return {
          id: String(shot.id || "projectile"),
          kind: "primary-weapon",
          from: clone(fromScreen),
          to: clone(toScreen),
          screen: {
            xNormalized: finite(fromScreen.xNormalized) + (finite(toScreen.xNormalized) - finite(fromScreen.xNormalized)) * progress,
            yNormalized: finite(fromScreen.yNormalized) + (finite(toScreen.yNormalized) - finite(fromScreen.yNormalized)) * progress,
          },
          progress,
        };
      });
  }

  function impactEffects(snapshot, simulationSeconds, targetScreen) {
    return (snapshot.recentEvents || [])
      .filter((event) => String(event.kind || "") === "Impact")
      .map((event) => ({event, ageSeconds: simulationSeconds - finite(event.atSeconds)}))
      .filter(({ageSeconds}) => ageSeconds >= -1e-9 && ageSeconds <= IMPACT_EFFECT_SECONDS + 1e-9)
      .map(({event, ageSeconds}) => ({
        id: `impact-${String(event.shotId || event.sequence || "target")}`,
        targetId: String(snapshot.target?.id || "ship.beta"),
        screen: clone(targetScreen),
        progress: clamp01(ageSeconds / IMPACT_EFFECT_SECONDS),
      }));
  }

  function destructionEffects(snapshot, simulationSeconds, targetScreen) {
    const target = snapshot.target;
    const destroyedAtValue = target?.destroyedAtSeconds;
    // null is the authority's explicit "not destroyed" value. Number(null) == 0
    // must never turn a live ship into an explosion at simulation time zero.
    if (!target || !(target.destroyed === true || target.disabled === true || target.status === "destroyed")
        || destroyedAtValue === null || destroyedAtValue === undefined || destroyedAtValue === "") {
      return {explosions: [], debris: []};
    }
    const destroyedAt = Number(destroyedAtValue);
    if (!Number.isFinite(destroyedAt) || destroyedAt < 0 || simulationSeconds + 1e-9 < destroyedAt) {
      return {explosions: [], debris: []};
    }
    const age = Math.max(0, simulationSeconds - destroyedAt);
    const progress = clamp01(age / DESTRUCTION_EFFECT_SECONDS);
    const explosions = age <= DESTRUCTION_EFFECT_SECONDS + 1e-9
      ? [{
          id: `explosion-${String(snapshot.target?.id || "ship.beta")}`,
          screen: clone(targetScreen),
          progress,
          scaleClass: "ship-destruction",
        }]
      : [];
    const debris = [{
      id: `debris-${String(snapshot.target?.id || "ship.beta")}`,
      screen: clone(targetScreen),
      progress,
      visualState: age <= DESTRUCTION_EFFECT_SECONDS ? "expanding" : "settled",
    }];
    return {explosions, debris};
  }

  function buildEncounter({systemState, projectionSnapshot} = {}) {
    const system = normalizeSystemState(systemState);
    if (system.selectedMode !== ENCOUNTER_MODE) {
      throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_MISMATCH: selected ${system.selectedMode}, builder ${ENCOUNTER_MODE}`);
    }
    const snapshot = projectionSnapshot;
    if (!snapshot || snapshot.schema !== PROJECTION.SCHEMA) {
      throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_PROJECTION_SCHEMA: expected ${PROJECTION.SCHEMA}`);
    }

    const simulationSeconds = finite(snapshot.simulationSeconds);
    const ownScreen = normalizedPoint(snapshot.viewScreen?.playerOffsetNormalized);
    const targetScreen = normalizedPoint(snapshot.viewScreen?.targetOffsetNormalized);
    const targetHullPercent = clamp(snapshot.target?.hullPercent, 0, 100);
    const targetDestroyed = Boolean(snapshot.target?.destroyed || snapshot.target?.disabled || String(snapshot.target?.status || "") === "destroyed");
    const targetVisualState = targetDestroyed ? "destroyed" : targetHullPercent < 100 ? "damaged" : "intact";
    const relationship = String(snapshot.hostileAwareness || "") === "active-hostile-player" ? "hostile" : "unidentified";
    const phase = String(snapshot.phase || "boarding-approach");
    const destruction = destructionEffects(snapshot, simulationSeconds, targetScreen);
    const effects = {
      projectiles: projectileEffects(snapshot, simulationSeconds, ownScreen, targetScreen),
      impacts: impactEffects(snapshot, simulationSeconds, targetScreen),
      explosions: destruction.explosions,
      debris: destruction.debris,
    };

    return deepFreeze({
      ...basePresentation(system, ENCOUNTER_MODE, simulationSeconds),
      environment: {
        alertState: relationship === "hostile" ? "hostile" : "contact",
        trackingMode: "soft-target-lock",
      },
      ownShip: {
        id: "ship.alpha",
        visible: Boolean(snapshot.viewScreen?.bothShipsVisible),
        screen: ownScreen,
        visualState: "nominal",
      },
      target: {
        id: String(snapshot.target?.id || "ship.beta"),
        visible: Boolean(snapshot.viewScreen?.hardLockRetained),
        screen: targetScreen,
        rangeM: finite(snapshot.rangeM),
        lock: {
          acquired: true,
          hardEnvelopeRetained: Boolean(snapshot.viewScreen?.hardLockRetained),
        },
        hullFraction: targetHullPercent / 100,
        visualState: targetVisualState,
        relationship,
        status: String(snapshot.target?.status || "operational"),
      },
      encounter: {
        phase,
        combatBoundarySeconds: snapshot.combatBoundarySeconds ?? null,
      },
      telemetry: {
        rangeM: finite(snapshot.rangeM),
        phase,
      },
      effects,
    });
  }

  function seededStars(seedText, count = 18) {
    let seed = 2166136261;
    String(seedText || "planet").split("").forEach((character) => {
      seed ^= character.charCodeAt(0);
      seed = Math.imul(seed, 16777619) >>> 0;
    });
    const random = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    return Array.from({length: count}, (_, index) => ({
      id: `field-star-${index}`,
      screen: {
        xNormalized: (0.08 + random() * 0.84 - 0.5) * 2,
        yNormalized: (0.14 + random() * 0.72 - 0.5) * 2,
      },
      sizeNormalized: 0.004 + random() * 0.006,
      visualState: index % 3 === 0 ? "blue-white" : "white",
    }));
  }

  function planetVisual(planet = {}) {
    const rings = planet.rings || {};
    return {
      atmosphereColor: validHex(planet.atmosphereColor, "#67e8f9"),
      surfaceColor: validHex(planet.surfaceColor, "#2563eb"),
      secondaryColor: validHex(planet.secondaryColor, "#16a34a"),
      cloudColor: validHex(planet.cloudColor, "#f8fafc"),
      radiusScale: clamp(planet.radiusScale || 1, 0.68, 1.38),
      rings: {
        enabled: Boolean(rings.enabled),
        color: validHex(rings.color, "#94a3b8"),
        outerRadius: clamp(rings.outerRadius || 1.75, 1.18, 2.2),
        innerRadius: clamp(rings.innerRadius || 1.35, 1.05, Math.max(1.1, finite(rings.outerRadius, 1.75) - 0.08)),
        tiltDegrees: clamp(rings.tiltDegrees || 0, -58.5, 58.5),
      },
    };
  }

  function buildPlanet({systemState, navigationSnapshot, simulationSeconds = 0, tracked = false} = {}) {
    const system = normalizeSystemState(systemState);
    if (system.selectedMode !== PLANET_MODE) {
      throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_MISMATCH: selected ${system.selectedMode}, builder ${PLANET_MODE}`);
    }
    const navigation = navigationSnapshot || {};
    const planet = navigation.currentPlanet;
    if (!planet) throw new Error("BRIDGE_VIEWSCREEN_PLANET_PRESENTATION_MISSING_PLANET");
    const radiusScale = clamp(planet.radiusScale || 1, 0.68, 1.38);
    const moonCount = Math.max(0, Math.min(6, Math.floor(finite(planet.moonCount, 0))));
    const moons = Array.from({length: moonCount}, (_, index) => ({
      id: `moon-${index}`,
      angleRadians: (index / Math.max(1, moonCount)) * Math.PI * 1.65 + 0.35,
      orbitScale: 1.45 + index * 0.12,
      radiusScale: 0.075 + (index % 2) * 0.018,
      visualState: index % 2 ? "slate" : "light",
    }));
    return deepFreeze({
      ...basePresentation(system, PLANET_MODE, simulationSeconds),
      environment: {alertState: "nominal", trackingMode: tracked ? "tracked" : "sensor-scan"},
      planet: {
        id: String(planet.id || navigation.currentPlanetId || "current-planet"),
        label: String(planet.label || navigation.currentPlanetLabel || "Current planet"),
        classification: String(planet.classification || navigation.currentPlanetClassification || ""),
        screen: {xNormalized: 0.08, yNormalized: 0.06},
        radiusHeightFraction: 0.39 * radiusScale,
        visual: planetVisual(planet),
        moons,
      },
      stars: seededStars(planet.id || navigation.currentSystemId || "planet"),
      tracking: {active: Boolean(tracked)},
      telemetry: {
        systemId: String(navigation.currentSystemId || ""),
        systemLabel: String(navigation.currentSystemLabel || ""),
      },
    });
  }

  function buildWarpTransit({systemState, navigationSnapshot, simulationSeconds = 0} = {}) {
    const system = normalizeSystemState(systemState);
    if (system.selectedMode !== WARP_MODE) {
      throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_MISMATCH: selected ${system.selectedMode}, builder ${WARP_MODE}`);
    }
    const navigation = navigationSnapshot || {};
    if (!navigation.travelling) throw new Error("BRIDGE_VIEWSCREEN_WARP_PRESENTATION_NOT_TRAVELLING");
    return deepFreeze({
      ...basePresentation(system, WARP_MODE, simulationSeconds),
      environment: {alertState: "transit", trackingMode: "warp-corridor"},
      warp: {
        phase: String(navigation.travelPhase || "in-warp"),
        progress: clamp01(navigation.travelProgress),
        originSystemId: String(navigation.currentSystemId || ""),
        destinationSystemId: String(navigation.destinationSystemId || ""),
        originPlanet: navigation.currentPlanet ? {
          id: String(navigation.currentPlanet.id || navigation.currentPlanetId || "origin"),
          visual: planetVisual(navigation.currentPlanet),
        } : null,
        destinationPlanet: navigation.destinationPlanet ? {
          id: String(navigation.destinationPlanet.id || "destination"),
          visual: planetVisual(navigation.destinationPlanet),
        } : null,
      },
      telemetry: {
        phase: String(navigation.travelPhase || "in-warp"),
        progress: clamp01(navigation.travelProgress),
        destinationSystemLabel: String(navigation.destinationSystemLabel || ""),
      },
    });
  }

  const vector = (value, fallback = [0, 0, -1]) => {
    const result = Array.isArray(value) && value.length === 3 ? value.map(Number) : fallback.slice();
    return result.every(Number.isFinite) ? result : fallback.slice();
  };
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
  function normalized(value, fallback = [0, 0, -1]) {
    const item = vector(value, fallback);
    const length = Math.hypot(item[0], item[1], item[2]);
    return length > 1e-12 ? [item[0] / length, item[1] / length, item[2] / length] : fallback.slice();
  }

  function buildAstrometric({systemState, navigationSnapshot, astrometricSnapshot, simulationSeconds = 0, tracked = false} = {}) {
    const system = normalizeSystemState(systemState);
    if (system.selectedMode !== ASTROMETRIC_MODE) {
      throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_MISMATCH: selected ${system.selectedMode}, builder ${ASTROMETRIC_MODE}`);
    }
    const navigation = navigationSnapshot || {};
    const astrometrics = astrometricSnapshot || {};
    const target = astrometrics.targetObject;
    if (!target?.direction) throw new Error("BRIDGE_VIEWSCREEN_ASTROMETRIC_PRESENTATION_MISSING_TARGET");

    const forward = normalized(target.direction);
    const referenceUp = Math.abs(dot(forward, [0, 0, 1])) > 0.93 ? [0, 1, 0] : [0, 0, 1];
    const right = normalized(cross(forward, referenceUp), [1, 0, 0]);
    const up = normalized(cross(right, forward), [0, 1, 0]);
    const targetAngularRadius = Math.max(1e-8, finite(target.angularRadiusRad, 1e-8));
    const targetTangent = Math.max(1e-8, Math.tan(targetAngularRadius));
    const targetScreen = {xNormalized: 0.08, yNormalized: 0.06};
    const targetRadiusHeightFraction = 0.39;

    const projectEntry = (entry) => {
      const direction = normalized(entry?.direction);
      const forwardComponent = dot(direction, forward);
      if (!(forwardComponent > 1e-6)) return null;
      const tangentX = dot(direction, right) / forwardComponent;
      const tangentY = dot(direction, up) / forwardComponent;
      const angularRadius = Math.max(0, finite(entry?.angularRadiusRad));
      return {
        xNormalized: targetScreen.xNormalized + tangentX * (0.78 / LOGICAL_VIEWSCREEN_ASPECT) / targetTangent,
        yNormalized: targetScreen.yNormalized + tangentY * 0.78 / targetTangent,
        radiusHeightFraction: Math.abs(Math.tan(angularRadius) * targetRadiusHeightFraction / targetTangent),
        forwardComponent,
      };
    };

    const projectedObjects = (Array.isArray(astrometrics.visibleObjects) ? astrometrics.visibleObjects : [])
      .map((entry) => ({entry, projection: entry.id === target.id ? {...targetScreen, radiusHeightFraction: targetRadiusHeightFraction, forwardComponent: 1} : projectEntry(entry)}))
      .filter(({projection}) => projection && Math.abs(projection.xNormalized) <= 1.12 && Math.abs(projection.yNormalized) <= 1.12);

    const stars = projectedObjects
      .filter(({entry}) => entry.kind === "star" && entry.id !== target.id)
      .map(({entry, projection}) => ({
        id: String(entry.id || "star"),
        screen: {xNormalized: projection.xNormalized, yNormalized: projection.yNormalized},
        radiusHeightFraction: clamp(projection.radiusHeightFraction || 0.006, 0.006, 0.13),
        local: Boolean(entry.local),
        color: validHex(entry.visual?.color, entry.local ? "#fff4d6" : "#f8fafc"),
      }));

    const localBodies = projectedObjects
      .filter(({entry}) => Boolean(entry.local) && entry.id !== target.id && entry.kind !== "star")
      .map(({entry, projection}) => ({
        id: String(entry.id || "body"),
        kind: String(entry.kind || "body"),
        screen: {xNormalized: projection.xNormalized, yNormalized: projection.yNormalized},
        radiusHeightFraction: clamp(projection.radiusHeightFraction || 0.006, 0.006, 0.12),
        color: entry.kind === "moon" ? "#cbd5e1" : validHex(entry.visual?.surfaceColor, "#94a3b8"),
      }));

    return deepFreeze({
      ...basePresentation(system, ASTROMETRIC_MODE, simulationSeconds),
      environment: {alertState: "nominal", trackingMode: tracked ? "tracked-astrometric" : "astrometric-scan"},
      targetObject: {
        id: String(target.id || navigation.currentPlanetId || "astrometric-target"),
        kind: String(target.kind || "planet"),
        screen: targetScreen,
        radiusHeightFraction: targetRadiusHeightFraction,
        visual: planetVisual(target.visual || navigation.currentPlanet || {}),
      },
      catalog: {stars, localBodies},
      tracking: {active: Boolean(tracked)},
      telemetry: {
        observerBodyId: String(astrometrics.observer?.bodyId || ""),
        targetId: String(target.id || ""),
      },
    });
  }

  function buildIdle({systemState, simulationSeconds = 0, reason = "standby"} = {}) {
    const system = normalizeSystemState(systemState);
    if (system.selectedMode !== IDLE_MODE) {
      throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_MISMATCH: selected ${system.selectedMode}, builder ${IDLE_MODE}`);
    }
    return deepFreeze({
      ...basePresentation(system, IDLE_MODE, simulationSeconds),
      environment: {alertState: "standby", trackingMode: "none"},
      idle: {reason: String(reason || "standby")},
    });
  }

  function selectModeForSources({encounterActive = false, navigationSnapshot = null, astrometricSnapshot = null} = {}) {
    if (encounterActive) return ENCOUNTER_MODE;
    const navigation = navigationSnapshot || {};
    if (navigation.travelling) return WARP_MODE;
    const astrometrics = astrometricSnapshot || {};
    if (astrometrics?.observer && astrometrics?.targetObject?.direction) return ASTROMETRIC_MODE;
    if (navigation.currentPlanet) return PLANET_MODE;
    return IDLE_MODE;
  }

  function build({
    systemState,
    encounterProjection = null,
    navigationSnapshot = null,
    astrometricSnapshot = null,
    simulationSeconds = 0,
    tracked = false,
    idleReason = "standby",
  } = {}) {
    const system = normalizeSystemState(systemState);
    switch (system.selectedMode) {
      case ENCOUNTER_MODE:
        return buildEncounter({systemState: system, projectionSnapshot: encounterProjection});
      case PLANET_MODE:
        return buildPlanet({systemState: system, navigationSnapshot, simulationSeconds, tracked});
      case WARP_MODE:
        return buildWarpTransit({systemState: system, navigationSnapshot, simulationSeconds});
      case ASTROMETRIC_MODE:
        return buildAstrometric({systemState: system, navigationSnapshot, astrometricSnapshot, simulationSeconds, tracked});
      case IDLE_MODE:
        return buildIdle({systemState: system, simulationSeconds, reason: idleReason});
      default:
        throw new Error(`BRIDGE_VIEWSCREEN_PRESENTATION_MODE_NOT_IMPLEMENTED: ${system.selectedMode}`);
    }
  }

  class BridgeViewscreenSystem {
    constructor(options = {}) {
      this.systemState = createSystemState(options);
      this.lastPresentation = null;
      this.lastInputs = null;
    }

    reset(options = {}) {
      this.systemState = createSystemState({
        initialMode: options.initialMode || ENCOUNTER_MODE,
        selectedAtSimulationSeconds: options.selectedAtSimulationSeconds ?? 0,
        displayPowered: options.displayPowered !== false,
      });
      this.lastPresentation = null;
      this.lastInputs = null;
      return this;
    }

    state() { return this.systemState; }
    selectedMode() { return this.systemState.selectedMode; }
    displayPowered() { return Boolean(this.systemState.displayPowered); }

    select(mode, simulationSeconds = null) {
      this.systemState = selectMode(this.systemState, mode, simulationSeconds);
      return this.systemState;
    }

    setDisplayPowered(powered) {
      this.systemState = setDisplayPowered(this.systemState, powered);
      if (this.lastInputs) this.lastPresentation = build({systemState: this.systemState, ...this.lastInputs});
      return this.systemState;
    }

    present(inputs = {}) {
      this.lastInputs = clone(inputs);
      this.lastPresentation = build({systemState: this.systemState, ...inputs});
      return this.lastPresentation;
    }
  }

  globalThis.MainComputerBridgeViewscreenPresentation = Object.freeze({
    SCHEMA,
    SYSTEM_STATE_SCHEMA,
    PROJECTION_SCHEMA: PROJECTION.SCHEMA,
    MODES,
    ENCOUNTER_MODE,
    PLANET_MODE,
    WARP_MODE,
    ASTROMETRIC_MODE,
    IDLE_MODE,
    EFFECT_TIMING: Object.freeze({impactSeconds: IMPACT_EFFECT_SECONDS, destructionSeconds: DESTRUCTION_EFFECT_SECONDS}),
    createSystemState,
    selectMode,
    setDisplayPowered,
    selectModeForSources,
    buildEncounter,
    buildPlanet,
    buildWarpTransit,
    buildAstrometric,
    buildIdle,
    build,
    createSystem(options = {}) { return new BridgeViewscreenSystem(options); },
  });
})();
