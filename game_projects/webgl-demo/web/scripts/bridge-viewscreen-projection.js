;(function () {
  const CONTRACT = globalThis.MainComputerSpaceCaptainMultirateContract;
  if (!CONTRACT) throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_MISSING");

  const SCHEMA = "game.bridgeViewscreenProjection.v1";
  const STATE_SCHEMA = "game.bridgeViewscreenProjectionState.v1";
  const AUTHORITY_SCHEMA = "game.bridgeEncounterAuthorityState.v1";
  const PLAYER = "ship.alpha";
  const TARGET = "ship.beta";
  const EPS = Number(CONTRACT.EPSILON_SECONDS);

  const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : Number(fallback);
  const clone = (value) => value && typeof value === "object" ? JSON.parse(JSON.stringify(value)) : value;

  function resolveAuthorityConfig(authorityState) {
    return CONTRACT.resolveConfig(authorityState?.config || {});
  }

  function assertAuthorityState(authorityState) {
    if (!authorityState || authorityState.schema !== AUTHORITY_SCHEMA) {
      throw new Error(`BRIDGE_VIEWSCREEN_PROJECTION_AUTHORITY_SCHEMA: expected ${AUTHORITY_SCHEMA}`);
    }
    if (!authorityState.authority || !authorityState.ships?.[PLAYER] || !authorityState.ships?.[TARGET]) {
      throw new Error("BRIDGE_VIEWSCREEN_PROJECTION_AUTHORITY_STATE_INCOMPLETE");
    }
    return authorityState;
  }

  function nextRequiredAuthorityAtSeconds(authorityState) {
    const values = [
      authorityState?.authority?.nextPhysicsAtSeconds,
      authorityState?.authority?.nextTacticalBoundaryAtSeconds,
      authorityState?.authority?.nextPendingImpactAtSeconds,
    ]
      .filter((value) => value !== null && value !== undefined)
      .map(Number)
      .filter(Number.isFinite);
    return values.length ? Math.min(...values) : Infinity;
  }

  function assertFresh(authorityState, simulationSeconds) {
    const state = assertAuthorityState(authorityState);
    const atSeconds = finite(state.authority.atSeconds);
    const renderSeconds = finite(simulationSeconds);
    if (renderSeconds + EPS < atSeconds) {
      throw new Error(
        `BRIDGE_VIEWSCREEN_PROJECTION_TIME_REWIND: render=${renderSeconds.toFixed(9)}s authority=${atSeconds.toFixed(9)}s`
      );
    }
    const nextRequired = nextRequiredAuthorityAtSeconds(state);
    if (nextRequired <= renderSeconds + EPS) {
      throw new Error(
        `BRIDGE_VIEWSCREEN_PROJECTION_AUTHORITY_STALE: authority required through ${renderSeconds.toFixed(9)}s before projection; next authority boundary=${nextRequired.toFixed(9)}s`
      );
    }
    return renderSeconds;
  }

  function predictShip(anchor, dtSeconds) {
    const dt = Math.max(0, finite(dtSeconds));
    const xM = finite(anchor.xM) + finite(anchor.vxMps) * dt + 0.5 * finite(anchor.axMps2) * dt * dt;
    const yM = finite(anchor.yM) + finite(anchor.vyMps) * dt + 0.5 * finite(anchor.ayMps2) * dt * dt;
    const vxMps = finite(anchor.vxMps) + finite(anchor.axMps2) * dt;
    const vyMps = finite(anchor.vyMps) + finite(anchor.ayMps2) * dt;
    return {xM, yM, vxMps, vyMps, axMps2: finite(anchor.axMps2), ayMps2: finite(anchor.ayMps2), speedMps: Math.hypot(vxMps, vyMps)};
  }

  function predictShips(authorityState, simulationSeconds) {
    const state = assertAuthorityState(authorityState);
    const renderSeconds = assertFresh(state, simulationSeconds);
    const dt = renderSeconds - finite(state.authority.atSeconds);
    return {
      [PLAYER]: predictShip(state.ships[PLAYER], dt),
      [TARGET]: predictShip(state.ships[TARGET], dt),
    };
  }

  function phaseAt(authorityState, simulationSeconds) {
    const state = assertAuthorityState(authorityState);
    const target = state.target || {};
    if (target.destroyed === true || String(target.status || "") === "destroyed") return "destroyed";
    const hostile = Boolean(state.encounter?.hostile);
    const combatBoundary = Number(state.combatBoundarySeconds);
    if (hostile) {
      if (Number.isFinite(combatBoundary) && finite(simulationSeconds) + EPS >= combatBoundary) return "combat";
      return "hostile-reaction-pending";
    }
    const step = resolveAuthorityConfig(state).tacticalSliceSeconds;
    if (finite(simulationSeconds) < step) return "boarding-approach";
    if (finite(simulationSeconds) < 2 * step) return "boarding-velocity-match";
    return "boarding-prep";
  }

  function initialPresentationState(authorityState) {
    const state = assertAuthorityState(authorityState);
    const config = resolveAuthorityConfig(state);
    const target = state.ships[TARGET];
    return Object.freeze({
      schema: STATE_SCHEMA,
      simulationSeconds: finite(state.authority.atSeconds),
      cameraCenterM: Object.freeze([
        finite(target.xM) - 0.08 * Number(config.viewHalfWidthM),
        finite(target.yM) + 0.035 * Number(config.viewHalfHeightM),
      ]),
    });
  }

  function normalizePresentationState(authorityState, presentationState) {
    if (!presentationState) return initialPresentationState(authorityState);
    if (presentationState.schema !== STATE_SCHEMA || !Array.isArray(presentationState.cameraCenterM)) {
      throw new Error(`BRIDGE_VIEWSCREEN_PROJECTION_STATE_SCHEMA: expected ${STATE_SCHEMA}`);
    }
    return {
      schema: STATE_SCHEMA,
      simulationSeconds: finite(presentationState.simulationSeconds),
      cameraCenterM: [finite(presentationState.cameraCenterM[0]), finite(presentationState.cameraCenterM[1])],
    };
  }

  function project({authorityState, simulationSeconds, presentationState = null} = {}) {
    const state = assertAuthorityState(authorityState);
    const renderSeconds = assertFresh(state, simulationSeconds);
    const config = resolveAuthorityConfig(state);
    const ships = predictShips(state, renderSeconds);
    const previous = normalizePresentationState(state, presentationState);
    if (renderSeconds + EPS < previous.simulationSeconds) {
      throw new Error(
        `BRIDGE_VIEWSCREEN_PROJECTION_STATE_REWIND: render=${renderSeconds.toFixed(9)}s presentation=${previous.simulationSeconds.toFixed(9)}s`
      );
    }

    const halfWidth = Number(config.viewHalfWidthM);
    const halfHeight = Number(config.viewHalfHeightM);
    const soft = Number(config.softZoneNormalized);
    const hard = Number(config.hardLockEnvelopeNormalized);
    const response = Math.max(0.01, Number(config.cameraResponseSeconds));
    const target = ships[TARGET];
    const player = ships[PLAYER];
    let cameraX = finite(previous.cameraCenterM[0]);
    let cameraY = finite(previous.cameraCenterM[1]);
    const rawX = (target.xM - cameraX) / halfWidth;
    const rawY = (target.yM - cameraY) / halfHeight;
    let desiredX = cameraX;
    let desiredY = cameraY;
    if (Math.abs(rawX) > soft) desiredX = target.xM - Math.sign(rawX) * soft * halfWidth;
    if (Math.abs(rawY) > soft) desiredY = target.yM - Math.sign(rawY) * soft * halfHeight;
    const renderDt = Math.max(0, renderSeconds - previous.simulationSeconds);
    if (renderDt > 0) {
      const alpha = 1 - Math.exp(-renderDt / response);
      cameraX += (desiredX - cameraX) * alpha;
      cameraY += (desiredY - cameraY) * alpha;
    }

    const targetOffset = [(target.xM - cameraX) / halfWidth, (target.yM - cameraY) / halfHeight];
    const playerOffset = [(player.xM - cameraX) / halfWidth, (player.yM - cameraY) / halfHeight];
    const rangeM = Math.hypot(target.xM - player.xM, target.yM - player.yM);
    const projectiles = (state.weapons?.projectiles || []).map((shot) => ({...shot}));
    const firstProjectile = projectiles[0] || null;
    const nextPresentationState = Object.freeze({
      schema: STATE_SCHEMA,
      simulationSeconds: renderSeconds,
      cameraCenterM: Object.freeze([cameraX, cameraY]),
    });

    const snapshot = {
      schema: SCHEMA,
      contractSchema: CONTRACT.SCHEMA,
      authoritySchema: state.schema,
      active: true,
      simulationSeconds: renderSeconds,
      tacticalSliceSeconds: Number(config.tacticalSliceSeconds),
      physicsStepSeconds: Number(config.physicsStepSeconds),
      renderCadence: CONTRACT.RULES.renderCadence,
      phase: phaseAt(state, renderSeconds),
      hostileAwareness: state.hostileAwareness,
      playerFireAtSeconds: state.weapons?.firstFireAtSeconds ?? null,
      impactAtSeconds: state.weapons?.firstImpactAtSeconds ?? firstProjectile?.impactAtSeconds ?? null,
      combatBoundarySeconds: state.combatBoundarySeconds ?? null,
      rangeM,
      target: clone(state.target),
      weapons: {
        ...clone(state.weapons || {}),
        projectiles,
      },
      authority: clone(state.authority),
      ships: {
        [PLAYER]: {...player},
        [TARGET]: {...target},
      },
      recentEvents: (state.recentEvents || []).map((event) => ({...event})),
      projection: {
        authorityAtSeconds: finite(state.authority.atSeconds),
        predictionSeconds: Math.max(0, renderSeconds - finite(state.authority.atSeconds)),
        presentationStateSchema: STATE_SCHEMA,
      },
      viewScreen: {
        mode: "enemy-soft-target-lock",
        cameraCenterM: [cameraX, cameraY],
        targetOffsetNormalized: targetOffset,
        playerOffsetNormalized: playerOffset,
        hardLockRetained: Math.abs(targetOffset[0]) <= hard && Math.abs(targetOffset[1]) <= hard,
        bothShipsVisible: Math.abs(targetOffset[0]) <= 1 && Math.abs(targetOffset[1]) <= 1 && Math.abs(playerOffset[0]) <= 1 && Math.abs(playerOffset[1]) <= 1,
      },
    };

    return {snapshot, nextPresentationState};
  }

  globalThis.MainComputerBridgeViewscreenProjection = Object.freeze({
    SCHEMA,
    STATE_SCHEMA,
    AUTHORITY_SCHEMA,
    CONTRACT_SCHEMA: CONTRACT.SCHEMA,
    initialPresentationState,
    predictShips,
    phaseAt,
    project,
  });
})();
