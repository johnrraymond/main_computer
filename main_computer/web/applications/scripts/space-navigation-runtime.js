(function (global) {
  "use strict";

  const SCHEMA = "game.spaceNavigation.v1";
  const DEFINITION_VERSION = "game.spaceNavigation.definition.v1";
  const STATE_VERSION = "game.spaceNavigation.state.v1";
  const TRAVEL_PHASES = new Set(["in-system", "course-plotted", "warp-charging", "in-warp", "arriving"]);
  const ACTIVE_TRAVEL_PHASES = new Set(["warp-charging", "in-warp", "arriving"]);

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function finiteNumber(value, fallback = 0, minimum = -Infinity, maximum = Infinity) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function definitionFromProject(project) {
    return objectValue(objectValue(project).metadata).spaceNavigation || null;
  }

  function validateDefinition(value) {
    const definition = objectValue(value);
    const errors = [];
    const warnings = [];
    const systems = Array.isArray(definition.systems) ? definition.systems : [];
    const routes = Array.isArray(definition.routes) ? definition.routes : [];
    const arrivalProfiles = objectValue(definition.arrivalProfiles);
    const stateDefaults = objectValue(definition.stateDefaults);

    if (definition.enabled === false) warnings.push("space navigation is disabled");
    if (definition.schema !== SCHEMA) errors.push(`schema must be ${SCHEMA}`);
    if (definition.definitionVersion !== DEFINITION_VERSION) errors.push(`definitionVersion must be ${DEFINITION_VERSION}`);
    if (definition.stateVersion !== STATE_VERSION) errors.push(`stateVersion must be ${STATE_VERSION}`);
    if (!systems.length) errors.push("systems must be a non-empty list");
    if (!routes.length) errors.push("routes must be a non-empty list");

    const systemIds = new Set();
    const localSpaceIds = new Set();
    const planetIds = new Set();
    const colorPattern = /^#[0-9a-f]{6}$/i;
    systems.forEach((system, index) => {
      const raw = objectValue(system);
      const id = stringValue(raw.id);
      const localSpaceId = stringValue(raw.localSpaceId);
      if (!id) errors.push(`systems[${index}] is missing id`);
      else if (systemIds.has(id)) errors.push(`duplicate system id ${id}`);
      else systemIds.add(id);
      if (!stringValue(raw.label)) errors.push(`${id || `systems[${index}]`} is missing label`);
      if (!localSpaceId) errors.push(`${id || `systems[${index}]`} is missing localSpaceId`);
      else if (localSpaceIds.has(localSpaceId)) errors.push(`duplicate localSpaceId ${localSpaceId}`);
      else localSpaceIds.add(localSpaceId);
      if (!Array.isArray(raw.mapPosition) || raw.mapPosition.length !== 2 || raw.mapPosition.some((entry) => !Number.isFinite(Number(entry)))) {
        errors.push(`${id || `systems[${index}]`} has invalid mapPosition`);
      }
      const arrivalProfile = stringValue(raw.arrivalProfile);
      if (!arrivalProfile || !arrivalProfiles[arrivalProfile]) errors.push(`${id || `systems[${index}]`} references missing arrival profile ${arrivalProfile || "<empty>"}`);

      const planet = objectValue(raw.primaryPlanet);
      const planetId = stringValue(planet.id);
      if (!planetId) errors.push(`${id || `systems[${index}]`} is missing primaryPlanet.id`);
      else if (planetIds.has(planetId)) errors.push(`duplicate planet id ${planetId}`);
      else planetIds.add(planetId);
      if (!stringValue(planet.label)) errors.push(`${id || `systems[${index}]`} is missing primaryPlanet.label`);
      if (!stringValue(planet.classification)) errors.push(`${id || `systems[${index}]`} is missing primaryPlanet.classification`);
      if (finiteNumber(planet.radiusScale, 0) < 0.5) errors.push(`${planetId || id || `systems[${index}]`} has invalid primaryPlanet.radiusScale`);
      ["surfaceColor", "secondaryColor", "atmosphereColor", "cloudColor"].forEach((field) => {
        if (!colorPattern.test(stringValue(planet[field]))) errors.push(`${planetId || id || `systems[${index}]`} has invalid primaryPlanet.${field}`);
      });
      if (!Number.isInteger(Number(planet.moonCount)) || Number(planet.moonCount) < 0) errors.push(`${planetId || id || `systems[${index}]`} has invalid primaryPlanet.moonCount`);
      const rings = objectValue(planet.rings);
      if (typeof rings.enabled !== "boolean") errors.push(`${planetId || id || `systems[${index}]`} has invalid primaryPlanet.rings.enabled`);
      if (!colorPattern.test(stringValue(rings.color))) errors.push(`${planetId || id || `systems[${index}]`} has invalid primaryPlanet.rings.color`);
      if (finiteNumber(rings.outerRadius, 0) <= finiteNumber(rings.innerRadius, 0)) errors.push(`${planetId || id || `systems[${index}]`} has invalid primaryPlanet ring radii`);
      if (!stringValue(planet.description)) errors.push(`${planetId || id || `systems[${index}]`} is missing primaryPlanet.description`);
    });

    const routeIds = new Set();
    const graph = new Map([...systemIds].map((id) => [id, new Set()]));
    routes.forEach((route, index) => {
      const raw = objectValue(route);
      const id = stringValue(raw.id);
      const from = stringValue(raw.from);
      const to = stringValue(raw.to);
      if (!id) errors.push(`routes[${index}] is missing id`);
      else if (routeIds.has(id)) errors.push(`duplicate route id ${id}`);
      else routeIds.add(id);
      if (!systemIds.has(from)) errors.push(`${id || `routes[${index}]`} references missing from system ${from || "<empty>"}`);
      if (!systemIds.has(to)) errors.push(`${id || `routes[${index}]`} references missing to system ${to || "<empty>"}`);
      if (from && from === to) errors.push(`${id || `routes[${index}]`} cannot connect a system to itself`);
      if (graph.has(from) && graph.has(to)) {
        graph.get(from).add(to);
        if (raw.bidirectional !== false) graph.get(to).add(from);
      }
      if (finiteNumber(raw.presentationDurationMs, 0) <= 0) errors.push(`${id || `routes[${index}]`} has invalid presentationDurationMs`);
      if (finiteNumber(raw.worldTimeCost, -1) < 0) errors.push(`${id || `routes[${index}]`} has invalid worldTimeCost`);
    });

    const startSystem = stringValue(definition.startSystem);
    if (!systemIds.has(startSystem)) errors.push(`startSystem references missing system ${startSystem || "<empty>"}`);
    const currentSystemId = stringValue(stateDefaults.currentSystemId || startSystem);
    if (!systemIds.has(currentSystemId)) errors.push(`stateDefaults.currentSystemId references missing system ${currentSystemId || "<empty>"}`);
    const plottedRouteId = stateDefaults.plottedRouteId;
    if (plottedRouteId !== null && plottedRouteId !== undefined && !routeIds.has(stringValue(plottedRouteId))) {
      errors.push(`stateDefaults.plottedRouteId references missing route ${stringValue(plottedRouteId)}`);
    }
    const phase = stringValue(stateDefaults.travelPhase || "in-system");
    if (!TRAVEL_PHASES.has(phase)) errors.push(`stateDefaults.travelPhase is invalid: ${phase || "<empty>"}`);

    if (startSystem && graph.has(startSystem)) {
      const reached = new Set([startSystem]);
      const queue = [startSystem];
      while (queue.length) {
        const current = queue.shift();
        for (const destination of graph.get(current) || []) {
          if (reached.has(destination)) continue;
          reached.add(destination);
          queue.push(destination);
        }
      }
      if (reached.size !== systemIds.size) {
        const missing = [...systemIds].filter((id) => !reached.has(id));
        errors.push(`systems are unreachable from ${startSystem}: ${missing.join(", ")}`);
      }
    }

    return {
      ok: errors.length === 0,
      schema: SCHEMA,
      systemCount: systems.length,
      planetCount: planetIds.size,
      routeCount: routes.length,
      errors,
      warnings
    };
  }

  class SpaceNavigationDefinitionError extends Error {
    constructor(report) {
      super(`Invalid space-navigation definition: ${report.errors.join("; ")}`);
      this.name = "SpaceNavigationDefinitionError";
      this.report = report;
    }
  }

  class SpaceNavigationRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(definition);
      this.report = validateDefinition(this.definition);
      if (!this.report.ok) throw new SpaceNavigationDefinitionError(this.report);
      this.projectId = stringValue(options.projectId || "game-project");
      this.systems = new Map(this.definition.systems.map((system) => [stringValue(system.id), clone(system)]));
      this.routes = new Map(this.definition.routes.map((route) => [stringValue(route.id), clone(route)]));
      this.listeners = new Set();
      this.sequence = 0;
      this.state = this.createInitialState(options.state);
    }

    createInitialState(overrideState) {
      const defaults = {...objectValue(this.definition.stateDefaults), ...objectValue(overrideState)};
      const startSystem = stringValue(defaults.currentSystemId || this.definition.startSystem);
      return {
        schema: STATE_VERSION,
        currentSystemId: this.systems.has(startSystem) ? startSystem : stringValue(this.definition.startSystem),
        plottedRouteId: defaults.plottedRouteId === null ? null : stringValue(defaults.plottedRouteId) || null,
        travelPhase: TRAVEL_PHASES.has(stringValue(defaults.travelPhase)) ? stringValue(defaults.travelPhase) : "in-system",
        elapsedWorldTime: finiteNumber(defaults.elapsedWorldTime, 0, 0),
        discoveredSystems: Array.isArray(defaults.discoveredSystems)
          ? [...new Set(defaults.discoveredSystems.map(stringValue).filter((id) => this.systems.has(id)))]
          : [...this.systems.keys()],
        originSystemId: null,
        destinationSystemId: null,
        activeRouteId: null,
        travelStartedAtMs: null,
        travelEndsAtMs: null,
        phaseStartedAtMs: null,
        pendingWorldTimeCost: 0,
        lastCompletedRouteId: null,
        lastArrivalAtMs: null,
        sequence: 0
      };
    }

    subscribe(listener) {
      if (typeof listener !== "function") return function () {};
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    notify(reason) {
      const snapshot = this.snapshot();
      this.listeners.forEach((listener) => {
        try { listener(snapshot, reason); } catch (error) { console.error("Space navigation listener failed", error); }
      });
      return snapshot;
    }

    system(systemId) {
      return clone(this.systems.get(stringValue(systemId)) || null);
    }

    route(routeId) {
      return clone(this.routes.get(stringValue(routeId)) || null);
    }

    routeDestination(route, originSystemId = this.state.currentSystemId) {
      const raw = objectValue(route);
      const origin = stringValue(originSystemId);
      if (stringValue(raw.from) === origin) return stringValue(raw.to);
      if (raw.bidirectional !== false && stringValue(raw.to) === origin) return stringValue(raw.from);
      return "";
    }

    destinations(originSystemId = this.state.currentSystemId) {
      const origin = stringValue(originSystemId);
      return [...this.routes.values()]
        .map((route) => {
          const destinationSystemId = this.routeDestination(route, origin);
          if (!destinationSystemId) return null;
          const system = this.systems.get(destinationSystemId);
          if (!system) return null;
          return {
            routeId: stringValue(route.id),
            systemId: destinationSystemId,
            label: stringValue(system.label || destinationSystemId),
            region: stringValue(system.region),
            localSpaceId: stringValue(system.localSpaceId),
            planetId: stringValue(system.primaryPlanet?.id),
            planetLabel: stringValue(system.primaryPlanet?.label),
            planetClassification: stringValue(system.primaryPlanet?.classification),
            presentationDurationMs: finiteNumber(route.presentationDurationMs, 4500, 250),
            worldTimeCost: finiteNumber(route.worldTimeCost, 0, 0),
            bidirectional: route.bidirectional !== false
          };
        })
        .filter(Boolean)
        .sort((left, right) => left.label.localeCompare(right.label));
    }

    resolveCourse(routeOrDestinationId) {
      const requested = stringValue(routeOrDestinationId);
      if (!requested) return null;
      const direct = this.routes.get(requested);
      if (direct) {
        const destinationSystemId = this.routeDestination(direct);
        return destinationSystemId ? {route: direct, destinationSystemId} : null;
      }
      for (const route of this.routes.values()) {
        const destinationSystemId = this.routeDestination(route);
        if (destinationSystemId === requested) return {route, destinationSystemId};
      }
      return null;
    }

    plotCourse(routeOrDestinationId) {
      if (ACTIVE_TRAVEL_PHASES.has(this.state.travelPhase)) throw new Error("Cannot plot a new course while warp travel is active.");
      const course = this.resolveCourse(routeOrDestinationId);
      if (!course) throw new Error(`No direct route from ${this.state.currentSystemId} to ${stringValue(routeOrDestinationId) || "the requested destination"}.`);
      this.state.plottedRouteId = stringValue(course.route.id);
      this.state.travelPhase = "course-plotted";
      this.state.destinationSystemId = course.destinationSystemId;
      this.state.sequence = ++this.sequence;
      return this.notify("course-plotted");
    }

    clearCourse() {
      if (ACTIVE_TRAVEL_PHASES.has(this.state.travelPhase)) throw new Error("Cannot clear the course while warp travel is active.");
      this.state.plottedRouteId = null;
      this.state.destinationSystemId = null;
      this.state.travelPhase = "in-system";
      this.state.sequence = ++this.sequence;
      return this.notify("course-cleared");
    }

    engage(nowMs = 0) {
      if (this.state.travelPhase !== "course-plotted" || !this.state.plottedRouteId) {
        throw new Error("Plot a direct course before engaging warp.");
      }
      const course = this.resolveCourse(this.state.plottedRouteId);
      if (!course) throw new Error("The plotted route is no longer valid from the current system.");
      const route = course.route;
      const startedAt = finiteNumber(nowMs, 0, 0);
      const totalDurationMs = finiteNumber(route.presentationDurationMs, 4500, 250, 120000);
      this.state.originSystemId = this.state.currentSystemId;
      this.state.destinationSystemId = course.destinationSystemId;
      this.state.activeRouteId = stringValue(route.id);
      this.state.travelStartedAtMs = startedAt;
      this.state.phaseStartedAtMs = startedAt;
      this.state.travelEndsAtMs = startedAt + totalDurationMs;
      this.state.pendingWorldTimeCost = finiteNumber(route.worldTimeCost, 0, 0);
      this.state.travelPhase = "warp-charging";
      this.state.sequence = ++this.sequence;
      return this.notify("warp-engaged");
    }

    update(nowMs = 0) {
      if (!ACTIVE_TRAVEL_PHASES.has(this.state.travelPhase)) return {changed: false, arrived: false, snapshot: this.snapshot()};
      const now = finiteNumber(nowMs, 0, 0);
      const start = finiteNumber(this.state.travelStartedAtMs, now, 0);
      const end = Math.max(start + 1, finiteNumber(this.state.travelEndsAtMs, start + 1, start + 1));
      const total = end - start;
      const elapsed = Math.max(0, now - start);
      const progress = Math.max(0, Math.min(1, elapsed / total));
      let nextPhase = "warp-charging";
      if (progress >= 0.82) nextPhase = "arriving";
      else if (progress >= 0.18) nextPhase = "in-warp";

      let changed = false;
      if (nextPhase !== this.state.travelPhase) {
        this.state.travelPhase = nextPhase;
        this.state.phaseStartedAtMs = now;
        this.state.sequence = ++this.sequence;
        changed = true;
      }
      if (now < end) {
        const snapshot = this.snapshot(now);
        if (changed) this.notify(`phase-${nextPhase}`);
        return {changed, arrived: false, snapshot};
      }

      const completedRouteId = this.state.activeRouteId;
      this.state.currentSystemId = this.state.destinationSystemId;
      this.state.elapsedWorldTime += this.state.pendingWorldTimeCost;
      this.state.lastCompletedRouteId = completedRouteId;
      this.state.lastArrivalAtMs = now;
      this.state.plottedRouteId = null;
      this.state.travelPhase = "in-system";
      this.state.originSystemId = null;
      this.state.destinationSystemId = null;
      this.state.activeRouteId = null;
      this.state.travelStartedAtMs = null;
      this.state.travelEndsAtMs = null;
      this.state.phaseStartedAtMs = now;
      this.state.pendingWorldTimeCost = 0;
      this.state.sequence = ++this.sequence;
      return {changed: true, arrived: true, snapshot: this.notify("arrival-committed")};
    }

    snapshot(nowMs = null) {
      const currentSystem = this.systems.get(this.state.currentSystemId) || null;
      const currentPlanet = objectValue(currentSystem?.primaryPlanet);
      const plottedRoute = this.state.plottedRouteId ? this.routes.get(this.state.plottedRouteId) || null : null;
      const destinationSystem = this.state.destinationSystemId ? this.systems.get(this.state.destinationSystemId) || null : null;
      const destinationPlanet = objectValue(destinationSystem?.primaryPlanet);
      const start = finiteNumber(this.state.travelStartedAtMs, 0, 0);
      const end = finiteNumber(this.state.travelEndsAtMs, start, start);
      const now = nowMs === null ? start : finiteNumber(nowMs, start, 0);
      const progress = ACTIVE_TRAVEL_PHASES.has(this.state.travelPhase) && end > start
        ? Math.max(0, Math.min(1, (now - start) / (end - start)))
        : 0;
      return {
        enabled: this.definition.enabled !== false,
        schema: STATE_VERSION,
        definitionSchema: this.definition.schema,
        projectId: this.projectId,
        startSystemId: stringValue(this.definition.startSystem),
        currentSystemId: this.state.currentSystemId,
        currentSystemLabel: stringValue(currentSystem?.label || this.state.currentSystemId),
        currentSystem: currentSystem ? clone(currentSystem) : null,
        currentPlanet: Object.keys(currentPlanet).length ? clone(currentPlanet) : null,
        currentPlanetId: stringValue(currentPlanet.id),
        currentPlanetLabel: stringValue(currentPlanet.label),
        currentPlanetClassification: stringValue(currentPlanet.classification),
        currentRegion: stringValue(currentSystem?.region),
        currentLocalSpaceId: stringValue(currentSystem?.localSpaceId),
        plottedRouteId: this.state.plottedRouteId,
        plottedRoute: plottedRoute ? clone(plottedRoute) : null,
        destinationSystemId: this.state.destinationSystemId,
        destinationSystemLabel: stringValue(destinationSystem?.label || this.state.destinationSystemId),
        destinationPlanet: Object.keys(destinationPlanet).length ? clone(destinationPlanet) : null,
        destinationPlanetId: stringValue(destinationPlanet.id),
        destinationPlanetLabel: stringValue(destinationPlanet.label),
        travelPhase: this.state.travelPhase,
        travelling: ACTIVE_TRAVEL_PHASES.has(this.state.travelPhase),
        travelProgress: Number(progress.toFixed(4)),
        elapsedWorldTime: this.state.elapsedWorldTime,
        pendingWorldTimeCost: this.state.pendingWorldTimeCost,
        lastCompletedRouteId: this.state.lastCompletedRouteId,
        lastArrivalAtMs: this.state.lastArrivalAtMs,
        destinations: this.destinations(),
        discoveredSystems: this.state.discoveredSystems.slice(),
        sequence: this.state.sequence,
        validation: clone(this.report)
      };
    }
  }

  function create(definition, options = {}) {
    return new SpaceNavigationRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    TRAVEL_PHASES: [...TRAVEL_PHASES],
    ACTIVE_TRAVEL_PHASES: [...ACTIVE_TRAVEL_PHASES],
    SpaceNavigationDefinitionError,
    SpaceNavigationRuntime,
    definitionFromProject,
    validateDefinition,
    create
  };

  global.MainComputerSpaceNavigationRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
