(function (global) {
  "use strict";

  const SCHEMA = "game.spaceNavigation.v1";
  const DEFINITION_VERSION = "game.spaceNavigation.definition.v2";
  const STATE_VERSION = "game.spaceNavigation.state.v2";
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

  function systemPlanets(system) {
    const raw = objectValue(system);
    const planets = [];
    const primary = objectValue(raw.primaryPlanet);
    if (Object.keys(primary).length) planets.push(primary);
    const additional = Array.isArray(raw.additionalPlanets) ? raw.additionalPlanets : [];
    additional.forEach((planet) => {
      const normalized = objectValue(planet);
      if (Object.keys(normalized).length) planets.push(normalized);
    });
    return planets;
  }

  function systemStars(system) {
    const raw = objectValue(system);
    return Array.isArray(raw.stars)
      ? raw.stars.map(objectValue).filter((star) => Object.keys(star).length)
      : [];
  }

  function authoredLocalDestinations(system) {
    const raw = objectValue(system);
    return Array.isArray(raw.localDestinations)
      ? raw.localDestinations.map(objectValue).filter((destination) => Object.keys(destination).length)
      : [];
  }

  function systemLocalDestinations(system) {
    const raw = objectValue(system);
    const authored = authoredLocalDestinations(raw);
    if (authored.length) return authored;
    const primaryPlanet = systemPlanets(raw)[0] || {};
    const systemId = stringValue(raw.id).replace(/^system\./, "") || "unknown";
    const planetId = stringValue(primaryPlanet.id);
    if (!planetId) return [];
    return [{
      id: `destination.${systemId}.primary-orbit`,
      label: `${stringValue(primaryPlanet.label || raw.label || systemId)} Orbit`,
      kind: "planet-orbit",
      parentBodyId: planetId,
      position: [0, 0],
      discoveredByDefault: true,
      availableByDefault: true,
      visualProgram: "systemPlanet",
      description: `Primary arrival orbit for ${stringValue(primaryPlanet.label || raw.label || systemId)}.`
    }];
  }

  function systemLocalRoutes(system) {
    const raw = objectValue(system);
    return Array.isArray(raw.localRoutes)
      ? raw.localRoutes.map(objectValue).filter((route) => Object.keys(route).length)
      : [];
  }

  function arrivalLocalDestinationId(system) {
    const raw = objectValue(system);
    const explicit = stringValue(raw.arrivalDestinationId);
    if (explicit) return explicit;
    return stringValue(systemLocalDestinations(raw)[0]?.id);
  }

  function localDestinationById(system, destinationId) {
    const wanted = stringValue(destinationId);
    if (!wanted) return null;
    return systemLocalDestinations(system).find((destination) => stringValue(destination.id) === wanted) || null;
  }

  function systemBodyById(system, bodyId) {
    const wanted = stringValue(bodyId);
    if (!wanted) return null;
    return [...systemPlanets(system), ...systemStars(system)].find((body) => stringValue(body.id) === wanted) || null;
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
    if (!stringValue(definition.captainScrawl)) errors.push("captainScrawl must be a non-empty string");
    if (!systems.length) errors.push("systems must be a non-empty list");
    if (!routes.length) errors.push("routes must be a non-empty list");

    const systemIds = new Set();
    const localSpaceIds = new Set();
    const planetIds = new Set();
    const starIds = new Set();
    const localDestinationIds = new Set();
    const localRouteIds = new Set();
    const localDestinationsBySystem = new Map();
    let habitablePlanetCount = 0;
    let inhabitedPlanetCount = 0;
    let multiPlanetSystemCount = 0;
    let multiStarSystemCount = 0;
    let localNavigationSystemCount = 0;
    let localDestinationCount = 0;
    let localRouteCount = 0;
    const colorPattern = /^#[0-9a-f]{6}$/i;
    const localKinds = new Set([
      "planet-orbit",
      "moon-orbit",
      "station",
      "fleet",
      "wreck",
      "beacon",
      "settlement",
      "hazard",
      "deep-space"
    ]);

    systems.forEach((system, index) => {
      const raw = objectValue(system);
      const id = stringValue(raw.id);
      const path = id || `systems[${index}]`;
      const localSpaceId = stringValue(raw.localSpaceId);
      if (!id) errors.push(`systems[${index}] is missing id`);
      else if (systemIds.has(id)) errors.push(`duplicate system id ${id}`);
      else systemIds.add(id);
      if (!stringValue(raw.label)) errors.push(`${path} is missing label`);
      if (!localSpaceId) errors.push(`${path} is missing localSpaceId`);
      else if (localSpaceIds.has(localSpaceId)) errors.push(`duplicate localSpaceId ${localSpaceId}`);
      else localSpaceIds.add(localSpaceId);
      if (!Array.isArray(raw.mapPosition) || raw.mapPosition.length !== 2 || raw.mapPosition.some((entry) => !Number.isFinite(Number(entry)))) {
        errors.push(`${path} has invalid mapPosition`);
      }
      const arrivalProfile = stringValue(raw.arrivalProfile);
      if (!arrivalProfile || !arrivalProfiles[arrivalProfile]) errors.push(`${path} references missing arrival profile ${arrivalProfile || "<empty>"}`);

      const bodyIds = new Set();
      const planets = systemPlanets(raw);
      if (!planets.length) errors.push(`${path} is missing primaryPlanet`);
      if (planets.length > 1) multiPlanetSystemCount += 1;
      planets.forEach((planet, planetIndex) => {
        const planetPath = planetIndex === 0 ? `${path}.primaryPlanet` : `${path}.additionalPlanets[${planetIndex - 1}]`;
        const planetId = stringValue(planet.id);
        if (!planetId) errors.push(`${planetPath} is missing id`);
        else if (planetIds.has(planetId)) errors.push(`duplicate planet id ${planetId}`);
        else {
          planetIds.add(planetId);
          bodyIds.add(planetId);
        }
        if (!stringValue(planet.label)) errors.push(`${planetPath} is missing label`);
        if (!stringValue(planet.classification)) errors.push(`${planetPath} is missing classification`);
        if (finiteNumber(planet.radiusScale, 0) < 0.5) errors.push(`${planetId || planetPath} has invalid radiusScale`);
        ["surfaceColor", "secondaryColor", "atmosphereColor", "cloudColor"].forEach((field) => {
          if (!colorPattern.test(stringValue(planet[field]))) errors.push(`${planetId || planetPath} has invalid ${field}`);
        });
        if (!Number.isInteger(Number(planet.moonCount)) || Number(planet.moonCount) < 0) errors.push(`${planetId || planetPath} has invalid moonCount`);
        const rings = objectValue(planet.rings);
        if (typeof rings.enabled !== "boolean") errors.push(`${planetId || planetPath} has invalid rings.enabled`);
        if (!colorPattern.test(stringValue(rings.color))) errors.push(`${planetId || planetPath} has invalid rings.color`);
        if (finiteNumber(rings.outerRadius, 0) <= finiteNumber(rings.innerRadius, 0)) errors.push(`${planetId || planetPath} has invalid ring radii`);
        if (!stringValue(planet.description)) errors.push(`${planetId || planetPath} is missing description`);
        if (planet.habitable !== undefined && typeof planet.habitable !== "boolean") errors.push(`${planetId || planetPath} has invalid habitable flag`);
        if (planet.inhabited !== undefined && typeof planet.inhabited !== "boolean") errors.push(`${planetId || planetPath} has invalid inhabited flag`);
        if (planet.habitable === true) habitablePlanetCount += 1;
        if (planet.inhabited === true) inhabitedPlanetCount += 1;
      });

      const stars = systemStars(raw);
      if (stars.length > 1) multiStarSystemCount += 1;
      stars.forEach((star, starIndex) => {
        const starPath = `${path}.stars[${starIndex}]`;
        const starId = stringValue(star.id);
        if (!starId) errors.push(`${starPath} is missing id`);
        else if (starIds.has(starId)) errors.push(`duplicate star id ${starId}`);
        else {
          starIds.add(starId);
          bodyIds.add(starId);
        }
        if (!stringValue(star.label)) errors.push(`${starPath} is missing label`);
        if (!stringValue(star.spectralClass)) errors.push(`${starPath} is missing spectralClass`);
        if (!colorPattern.test(stringValue(star.color))) errors.push(`${starPath} has invalid color`);
        if (finiteNumber(star.radiusScale, 0) <= 0) errors.push(`${starPath} has invalid radiusScale`);
        if (!["primary", "companion"].includes(stringValue(star.role))) errors.push(`${starPath} has invalid role`);
        if (!stringValue(star.description)) errors.push(`${starPath} is missing description`);
      });

      const hasArrivalDestination = Object.prototype.hasOwnProperty.call(raw, "arrivalDestinationId");
      const hasLocalDestinations = Object.prototype.hasOwnProperty.call(raw, "localDestinations");
      const hasLocalRoutes = Object.prototype.hasOwnProperty.call(raw, "localRoutes");
      const hasAnyLocalContract = hasArrivalDestination || hasLocalDestinations || hasLocalRoutes;
      const hasCompleteLocalContract = hasArrivalDestination && hasLocalDestinations && hasLocalRoutes;
      if (hasAnyLocalContract && !hasCompleteLocalContract) {
        errors.push(`${path} must declare arrivalDestinationId, localDestinations, and localRoutes together`);
      }

      const authoredDestinations = authoredLocalDestinations(raw);
      const resolvedDestinations = systemLocalDestinations(raw);
      const resolvedDestinationIds = new Set();
      resolvedDestinations.forEach((destination, destinationIndex) => {
        const destinationId = stringValue(destination.id);
        if (!destinationId) {
          errors.push(`${path}.localDestinations[${destinationIndex}] is missing id`);
          return;
        }
        if (localDestinationIds.has(destinationId)) errors.push(`duplicate local destination id ${destinationId}`);
        else localDestinationIds.add(destinationId);
        resolvedDestinationIds.add(destinationId);
      });
      localDestinationsBySystem.set(id, resolvedDestinationIds);

      if (hasCompleteLocalContract) {
        localNavigationSystemCount += 1;
        if (!authoredDestinations.length) errors.push(`${path}.localDestinations must be a non-empty list`);
        localDestinationCount += authoredDestinations.length;

        authoredDestinations.forEach((destination, destinationIndex) => {
          const destinationPath = `${path}.localDestinations[${destinationIndex}]`;
          const destinationId = stringValue(destination.id);
          if (!destinationId) errors.push(`${destinationPath} is missing id`);
          if (!stringValue(destination.label)) errors.push(`${destinationPath} is missing label`);
          if (!localKinds.has(stringValue(destination.kind))) errors.push(`${destinationPath} has invalid kind`);
          const parentBodyId = destination.parentBodyId === null ? "" : stringValue(destination.parentBodyId);
          if (parentBodyId && !bodyIds.has(parentBodyId)) {
            errors.push(`${destinationId || destinationPath} references missing parent body ${parentBodyId}`);
          }
          if (!Array.isArray(destination.position) || destination.position.length !== 2 || destination.position.some((entry) => !Number.isFinite(Number(entry)))) {
            errors.push(`${destinationId || destinationPath} has invalid position`);
          }
          if (typeof destination.discoveredByDefault !== "boolean") errors.push(`${destinationId || destinationPath} has invalid discoveredByDefault`);
          if (typeof destination.availableByDefault !== "boolean") errors.push(`${destinationId || destinationPath} has invalid availableByDefault`);
          if (!stringValue(destination.visualProgram)) errors.push(`${destinationId || destinationPath} is missing visualProgram`);
          if (!stringValue(destination.description)) errors.push(`${destinationId || destinationPath} is missing description`);
        });

        const arrivalDestinationId = stringValue(raw.arrivalDestinationId);
        if (!resolvedDestinationIds.has(arrivalDestinationId)) {
          errors.push(`${path}.arrivalDestinationId references missing local destination ${arrivalDestinationId || "<empty>"}`);
        }

        const authoredRoutes = systemLocalRoutes(raw);
        localRouteCount += authoredRoutes.length;
        const localGraph = new Map([...resolvedDestinationIds].map((destinationId) => [destinationId, new Set()]));
        authoredRoutes.forEach((route, routeIndex) => {
          const routePath = `${path}.localRoutes[${routeIndex}]`;
          const routeId = stringValue(route.id);
          const from = stringValue(route.from);
          const to = stringValue(route.to);
          if (!routeId) errors.push(`${routePath} is missing id`);
          else if (localRouteIds.has(routeId)) errors.push(`duplicate local route id ${routeId}`);
          else localRouteIds.add(routeId);
          if (!resolvedDestinationIds.has(from)) errors.push(`${routeId || routePath} references missing from destination ${from || "<empty>"}`);
          if (!resolvedDestinationIds.has(to)) errors.push(`${routeId || routePath} references missing to destination ${to || "<empty>"}`);
          if (from && from === to) errors.push(`${routeId || routePath} cannot connect a destination to itself`);
          if (localGraph.has(from) && localGraph.has(to)) {
            localGraph.get(from).add(to);
            if (route.bidirectional !== false) localGraph.get(to).add(from);
          }
          if (finiteNumber(route.presentationDurationMs, 0) < 250) errors.push(`${routeId || routePath} has invalid presentationDurationMs`);
          if (finiteNumber(route.worldTimeCost, 0) < 1) errors.push(`${routeId || routePath} has invalid worldTimeCost`);
        });

        if (arrivalDestinationId && localGraph.has(arrivalDestinationId)) {
          const reached = new Set([arrivalDestinationId]);
          const queue = [arrivalDestinationId];
          while (queue.length) {
            const current = queue.shift();
            for (const destination of localGraph.get(current) || []) {
              if (reached.has(destination)) continue;
              reached.add(destination);
              queue.push(destination);
            }
          }
          if (reached.size !== resolvedDestinationIds.size) {
            const missing = [...resolvedDestinationIds].filter((destinationId) => !reached.has(destinationId));
            errors.push(`${path} local destinations are unreachable from ${arrivalDestinationId}: ${missing.join(", ")}`);
          }
        }
      }
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

    const currentLocalDestinationId = stringValue(stateDefaults.currentLocalDestinationId);
    if (!localDestinationsBySystem.get(currentSystemId)?.has(currentLocalDestinationId)) {
      errors.push(`stateDefaults.currentLocalDestinationId references missing destination ${currentLocalDestinationId || "<empty>"} in ${currentSystemId || "<empty>"}`);
    }

    const discoveredLocalDestinations = objectValue(stateDefaults.discoveredLocalDestinations);
    Object.entries(discoveredLocalDestinations).forEach(([systemId, destinationIds]) => {
      if (!systemIds.has(systemId)) {
        errors.push(`stateDefaults.discoveredLocalDestinations references missing system ${systemId}`);
        return;
      }
      if (!Array.isArray(destinationIds)) {
        errors.push(`stateDefaults.discoveredLocalDestinations.${systemId} must be a list`);
        return;
      }
      const seen = new Set();
      destinationIds.forEach((destinationIdValue) => {
        const destinationId = stringValue(destinationIdValue);
        if (seen.has(destinationId)) errors.push(`stateDefaults.discoveredLocalDestinations.${systemId} contains duplicate ${destinationId}`);
        seen.add(destinationId);
        if (!localDestinationsBySystem.get(systemId)?.has(destinationId)) {
          errors.push(`stateDefaults.discoveredLocalDestinations.${systemId} references missing destination ${destinationId || "<empty>"}`);
        }
      });
    });

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
      starCount: starIds.size,
      habitablePlanetCount,
      inhabitedPlanetCount,
      multiPlanetSystemCount,
      multiStarSystemCount,
      localNavigationSystemCount,
      localDestinationCount,
      localRouteCount,
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
      const requestedSystemId = stringValue(defaults.currentSystemId || this.definition.startSystem);
      const currentSystemId = this.systems.has(requestedSystemId)
        ? requestedSystemId
        : stringValue(this.definition.startSystem);
      const configuredDiscovery = objectValue(defaults.discoveredLocalDestinations);
      const discoveredLocalDestinations = {};
      this.systems.forEach((system, systemId) => {
        const validIds = new Set(systemLocalDestinations(system).map((destination) => stringValue(destination.id)).filter(Boolean));
        const configured = Array.isArray(configuredDiscovery[systemId]) ? configuredDiscovery[systemId] : [];
        const defaultDiscovered = systemLocalDestinations(system)
          .filter((destination) => destination.discoveredByDefault === true)
          .map((destination) => stringValue(destination.id));
        discoveredLocalDestinations[systemId] = [...new Set([...configured, ...defaultDiscovered].map(stringValue).filter((id) => validIds.has(id)))];
      });
      const currentSystem = this.systems.get(currentSystemId);
      const requestedLocalDestinationId = stringValue(defaults.currentLocalDestinationId);
      const currentLocalDestinationId = localDestinationById(currentSystem, requestedLocalDestinationId)
        ? requestedLocalDestinationId
        : arrivalLocalDestinationId(currentSystem);
      if (currentLocalDestinationId && !discoveredLocalDestinations[currentSystemId].includes(currentLocalDestinationId)) {
        discoveredLocalDestinations[currentSystemId].push(currentLocalDestinationId);
      }
      return {
        schema: STATE_VERSION,
        currentSystemId,
        currentLocalDestinationId,
        plottedRouteId: defaults.plottedRouteId === null ? null : stringValue(defaults.plottedRouteId) || null,
        travelPhase: TRAVEL_PHASES.has(stringValue(defaults.travelPhase)) ? stringValue(defaults.travelPhase) : "in-system",
        elapsedWorldTime: finiteNumber(defaults.elapsedWorldTime, 0, 0),
        discoveredSystems: Array.isArray(defaults.discoveredSystems)
          ? [...new Set(defaults.discoveredSystems.map(stringValue).filter((id) => this.systems.has(id)))]
          : [...this.systems.keys()],
        discoveredLocalDestinations,
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

    localDestinations(systemId = this.state.currentSystemId) {
      const resolvedSystemId = stringValue(systemId);
      const system = this.systems.get(resolvedSystemId);
      if (!system) return [];
      const discovered = new Set(this.state.discoveredLocalDestinations[resolvedSystemId] || []);
      return systemLocalDestinations(system).map((destination) => ({
        ...clone(destination),
        discovered: discovered.has(stringValue(destination.id)),
        available: destination.availableByDefault !== false
      }));
    }

    localRoutes(systemId = this.state.currentSystemId) {
      const system = this.systems.get(stringValue(systemId));
      return system ? clone(systemLocalRoutes(system)) : [];
    }

    localDestination(destinationId, systemId = this.state.currentSystemId) {
      const system = this.systems.get(stringValue(systemId));
      const destination = localDestinationById(system, destinationId);
      return destination ? clone(destination) : null;
    }

    arrivalLocalDestination(systemId = this.state.currentSystemId) {
      const system = this.systems.get(stringValue(systemId));
      const destination = localDestinationById(system, arrivalLocalDestinationId(system));
      return destination ? clone(destination) : null;
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
          const planets = systemPlanets(system);
          const stars = systemStars(system);
          const primaryPlanet = planets[0] || {};
          const arrivalDestination = localDestinationById(system, arrivalLocalDestinationId(system)) || {};
          return {
            routeId: stringValue(route.id),
            systemId: destinationSystemId,
            label: stringValue(system.label || destinationSystemId),
            region: stringValue(system.region),
            localSpaceId: stringValue(system.localSpaceId),
            planetId: stringValue(primaryPlanet.id),
            planetLabel: stringValue(primaryPlanet.label),
            planetClassification: stringValue(primaryPlanet.classification),
            planetCount: planets.length,
            starCount: Math.max(1, stars.length),
            habitablePlanetCount: planets.filter((planet) => planet.habitable === true).length,
            arrivalDestinationId: stringValue(arrivalDestination.id),
            arrivalDestinationLabel: stringValue(arrivalDestination.label),
            localDestinationCount: systemLocalDestinations(system).length,
            localRouteCount: systemLocalRoutes(system).length,
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
      const arrivedSystem = this.systems.get(this.state.currentSystemId);
      this.state.currentLocalDestinationId = arrivalLocalDestinationId(arrivedSystem);
      const discoveredAtArrival = this.state.discoveredLocalDestinations[this.state.currentSystemId] || [];
      if (this.state.currentLocalDestinationId && !discoveredAtArrival.includes(this.state.currentLocalDestinationId)) {
        discoveredAtArrival.push(this.state.currentLocalDestinationId);
      }
      this.state.discoveredLocalDestinations[this.state.currentSystemId] = discoveredAtArrival;
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
      const currentPlanets = systemPlanets(currentSystem);
      const currentStars = systemStars(currentSystem);
      const currentLocalDestinations = this.localDestinations(this.state.currentSystemId);
      const currentLocalRoutes = this.localRoutes(this.state.currentSystemId);
      const currentLocalDestination = localDestinationById(currentSystem, this.state.currentLocalDestinationId) || {};
      const currentLocalPlanet = currentPlanets.find(
        (planet) => stringValue(planet.id) === stringValue(currentLocalDestination.parentBodyId)
      );
      const currentPlanet = objectValue(currentLocalPlanet || currentPlanets[0]);

      const plottedRoute = this.state.plottedRouteId ? this.routes.get(this.state.plottedRouteId) || null : null;
      const destinationSystem = this.state.destinationSystemId ? this.systems.get(this.state.destinationSystemId) || null : null;
      const destinationPlanets = systemPlanets(destinationSystem);
      const destinationStars = systemStars(destinationSystem);
      const destinationLocalDestinations = destinationSystem ? systemLocalDestinations(destinationSystem) : [];
      const destinationLocalRoutes = destinationSystem ? systemLocalRoutes(destinationSystem) : [];
      const destinationLocalDestination = destinationSystem
        ? localDestinationById(destinationSystem, arrivalLocalDestinationId(destinationSystem)) || {}
        : {};
      const destinationLocalPlanet = destinationPlanets.find(
        (planet) => stringValue(planet.id) === stringValue(destinationLocalDestination.parentBodyId)
      );
      const destinationPlanet = objectValue(destinationLocalPlanet || destinationPlanets[0]);

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
        captainScrawl: stringValue(this.definition.captainScrawl),
        currentSystemId: this.state.currentSystemId,
        currentSystemLabel: stringValue(currentSystem?.label || this.state.currentSystemId),
        currentSystem: currentSystem ? clone(currentSystem) : null,
        currentPlanets: clone(currentPlanets),
        currentStars: clone(currentStars),
        currentPlanetCount: currentPlanets.length,
        currentStarCount: Math.max(1, currentStars.length),
        currentHabitablePlanetCount: currentPlanets.filter((planet) => planet.habitable === true).length,
        currentPlanet: Object.keys(currentPlanet).length ? clone(currentPlanet) : null,
        currentPlanetId: stringValue(currentPlanet.id),
        currentPlanetLabel: stringValue(currentPlanet.label),
        currentPlanetClassification: stringValue(currentPlanet.classification),
        currentRegion: stringValue(currentSystem?.region),
        currentLocalSpaceId: stringValue(currentSystem?.localSpaceId),
        currentLocalDestinationId: stringValue(currentLocalDestination.id),
        currentLocalDestinationLabel: stringValue(currentLocalDestination.label),
        currentLocalDestinationKind: stringValue(currentLocalDestination.kind),
        currentLocalDestination: Object.keys(currentLocalDestination).length ? clone(currentLocalDestination) : null,
        currentLocalDestinations: clone(currentLocalDestinations),
        currentLocalRoutes: clone(currentLocalRoutes),
        currentLocalDestinationCount: currentLocalDestinations.length,
        currentLocalRouteCount: currentLocalRoutes.length,
        plottedRouteId: this.state.plottedRouteId,
        plottedRoute: plottedRoute ? clone(plottedRoute) : null,
        destinationSystemId: this.state.destinationSystemId,
        destinationSystemLabel: stringValue(destinationSystem?.label || this.state.destinationSystemId),
        destinationPlanets: clone(destinationPlanets),
        destinationStars: clone(destinationStars),
        destinationPlanetCount: destinationPlanets.length,
        destinationStarCount: Math.max(1, destinationStars.length),
        destinationPlanet: Object.keys(destinationPlanet).length ? clone(destinationPlanet) : null,
        destinationPlanetId: stringValue(destinationPlanet.id),
        destinationPlanetLabel: stringValue(destinationPlanet.label),
        destinationLocalDestinationId: stringValue(destinationLocalDestination.id),
        destinationLocalDestinationLabel: stringValue(destinationLocalDestination.label),
        destinationLocalDestinationKind: stringValue(destinationLocalDestination.kind),
        destinationLocalDestination: Object.keys(destinationLocalDestination).length ? clone(destinationLocalDestination) : null,
        destinationLocalDestinations: clone(destinationLocalDestinations),
        destinationLocalRoutes: clone(destinationLocalRoutes),
        destinationLocalDestinationCount: destinationLocalDestinations.length,
        destinationLocalRouteCount: destinationLocalRoutes.length,
        travelPhase: this.state.travelPhase,
        travelling: ACTIVE_TRAVEL_PHASES.has(this.state.travelPhase),
        travelProgress: Number(progress.toFixed(4)),
        elapsedWorldTime: this.state.elapsedWorldTime,
        pendingWorldTimeCost: this.state.pendingWorldTimeCost,
        lastCompletedRouteId: this.state.lastCompletedRouteId,
        lastArrivalAtMs: this.state.lastArrivalAtMs,
        destinations: this.destinations(),
        discoveredSystems: this.state.discoveredSystems.slice(),
        discoveredLocalDestinations: clone(this.state.discoveredLocalDestinations),
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
