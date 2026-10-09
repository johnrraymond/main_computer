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

  // Camera pose belongs to the physical ship, not to the 2-D tactical encounter.
  function requireVec3(value, label) {
    if (!Array.isArray(value) || value.length !== 3 || !value.every((item) => typeof item === "number" && Number.isFinite(item))) {
      throw new Error(`BRIDGE_VIEWSCREEN_${label}_INVALID`);
    }
    return value.slice();
  }
  const dot = (a,b) => a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  const cross = (a,b) => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  const difference = (a,b) => a.map((value,index) => value-b[index]);
  function unit(value,label) {
    const vector = requireVec3(value,label);
    const length = Math.hypot(...vector);
    if (!(length>1e-9)) throw new Error(`BRIDGE_VIEWSCREEN_${label}_ZERO`);
    return vector.map(value => value/length);
  }
  function requireObserver(pose, simulationSeconds) {
    if (!pose || pose.bodyId !== "ship.mother") throw new Error("BRIDGE_VIEWSCREEN_OBSERVER_REQUIRED: ship.mother");
    const positionM = requireVec3(pose.positionM,"OBSERVER_POSITION");
    const velocityMps = requireVec3(pose.velocityMps || [0,0,0],"OBSERVER_VELOCITY");
    const validThroughSeconds = Number(pose.validThroughSeconds);
    if (!Number.isFinite(validThroughSeconds) || validThroughSeconds + EPS < simulationSeconds) {
      throw new Error("BRIDGE_VIEWSCREEN_OBSERVER_EXPIRED");
    }
    const forward = unit(pose.forwardWorld,"OBSERVER_FORWARD");
    const proposedUp = unit(pose.upWorld,"OBSERVER_UP");
    const right = unit(cross(proposedUp,forward),"OBSERVER_AXES");
    const up = unit(cross(forward,right),"OBSERVER_AXES");
    return {bodyId:"ship.mother",positionM,velocityMps,forwardWorld:forward,rightWorld:right,upWorld:up,validThroughSeconds};
  }

  function initialPresentationState(authorityState) {
    assertAuthorityState(authorityState);
    return Object.freeze({schema:STATE_SCHEMA,simulationSeconds:finite(authorityState.authority.atSeconds)});
  }
  function normalizePresentationState(authorityState, presentationState) {
    if (!presentationState) return initialPresentationState(authorityState);
    if (presentationState.schema !== STATE_SCHEMA) throw new Error(`BRIDGE_VIEWSCREEN_PROJECTION_STATE_SCHEMA: expected ${STATE_SCHEMA}`);
    return {schema:STATE_SCHEMA,simulationSeconds:finite(presentationState.simulationSeconds)};
  }

  function project({authorityState,simulationSeconds,presentationState=null,observerPose,targetWorldPositionM,viewMode="track"}={}) {
    const state = assertAuthorityState(authorityState);
    const renderSeconds = assertFresh(state,simulationSeconds);
    const observer = requireObserver(observerPose,renderSeconds);
    const targetWorld = requireVec3(targetWorldPositionM,"TARGET_WORLD_POSITION");
    const config = resolveAuthorityConfig(state);
    const ships = predictShips(state,renderSeconds); // tactical predictions remain read-only
    const previous = normalizePresentationState(state,presentationState);
    if (renderSeconds + EPS < previous.simulationSeconds) {
      throw new Error(`BRIDGE_VIEWSCREEN_PROJECTION_STATE_REWIND: render=${renderSeconds.toFixed(9)}s presentation=${previous.simulationSeconds.toFixed(9)}s`);
    }
    const delta = difference(targetWorld,observer.positionM);
    const rangeM = Math.hypot(...delta);
    if (viewMode !== "track" && viewMode !== "fixed") throw new Error("BRIDGE_VIEWSCREEN_VIEW_MODE_INVALID");
    // Tracking is an optical orientation about the physical ship, not a
    // translation, a ship-attitude command, or a weapons lock.
    if (viewMode === "track" && !(rangeM > 1e-9)) throw new Error("BRIDGE_VIEWSCREEN_TRACK_TARGET_COINCIDENT");
    let cameraForward = observer.forwardWorld;
    let cameraRight = observer.rightWorld;
    let cameraUp = observer.upWorld;
    if (viewMode === "track") {
      cameraForward = delta.map(value => value / rangeM);
      // Keep the camera roll stable with the ship's declared up basis, using
      // the ship's right basis at the poles where up and forward coincide.
      const referenceUp = Math.abs(dot(observer.upWorld,cameraForward)) > 0.99
        ? observer.rightWorld : observer.upWorld;
      cameraRight = unit(cross(referenceUp,cameraForward),"TRACK_AXES");
      cameraUp = unit(cross(cameraForward,cameraRight),"TRACK_AXES");
    }
    const local = [dot(delta,cameraRight),dot(delta,cameraUp),dot(delta,cameraForward)];
    const inFront = local[2] > 1e-9;
    // Perspective uses the existing half-width/height as a fixed focal scale.
    // Only projection rotates; the camera's world origin is never smoothed or moved.
    const halfWidth = Number(config.viewHalfWidthM);
    const halfHeight = Number(config.viewHalfHeightM);
    const depthScale = Math.max(1,Math.abs(local[2]) / Math.max(1,halfWidth));
    const targetOffset = [local[0]/(halfWidth*depthScale),local[1]/(halfHeight*depthScale)];
    const targetVisible = inFront && Math.abs(targetOffset[0])<=1 && Math.abs(targetOffset[1])<=1;
    const hard = Number(config.hardLockEnvelopeNormalized);
    const hardLockRetained = targetVisible && Math.abs(targetOffset[0])<=hard && Math.abs(targetOffset[1])<=hard;
    const projectiles = (state.weapons?.projectiles || []).map(shot => ({...shot}));
    const firstProjectile = projectiles[0] || null;
    const nextPresentationState = Object.freeze({schema:STATE_SCHEMA,simulationSeconds:renderSeconds});
    const snapshot = {
      schema:SCHEMA,contractSchema:CONTRACT.SCHEMA,authoritySchema:state.schema,
      active:true,simulationSeconds:renderSeconds,
      tacticalSliceSeconds:Number(config.tacticalSliceSeconds),physicsStepSeconds:Number(config.physicsStepSeconds),
      renderCadence:CONTRACT.RULES.renderCadence,
      phase:phaseAt(state,renderSeconds),hostileAwareness:state.hostileAwareness,
      playerFireAtSeconds:state.weapons?.firstFireAtSeconds ?? null,
      impactAtSeconds:state.weapons?.firstImpactAtSeconds ?? firstProjectile?.impactAtSeconds ?? null,
      combatBoundarySeconds:state.combatBoundarySeconds ?? null,
      rangeM,target:clone(state.target),
      weapons:{...clone(state.weapons || {}),projectiles},
      authority:clone(state.authority),
      ships:{[PLAYER]:{...ships[PLAYER],worldPositionM:observer.positionM.slice()},
             [TARGET]:{...ships[TARGET],worldPositionM:targetWorld.slice()}},
      recentEvents:(state.recentEvents || []).map(event=>({...event})),
      projection:{authorityAtSeconds:finite(state.authority.atSeconds),predictionSeconds:Math.max(0,renderSeconds-finite(state.authority.atSeconds)),presentationStateSchema:STATE_SCHEMA},
      viewScreen:{
        mode:viewMode === "track" ? "ship-mounted-track" : "ship-mounted-forward",
        observerBodyId:"ship.mother",cameraWorldPositionM:observer.positionM.slice(),
        shipForwardWorld:observer.forwardWorld.slice(),
        cameraForwardWorld:cameraForward.slice(),
        cameraRightWorld:cameraRight.slice(),cameraUpWorld:cameraUp.slice(),
        targetWorldPositionM:targetWorld.slice(),targetRelativeWorldM:delta,
        targetRelativeCameraM:local,targetInFront:inFront,targetVisible,
        // Kept only for older diagnostics; there is no independent world camera center.
        cameraCenterM:observer.positionM.slice(0,2),
        targetOffsetNormalized:targetOffset,playerOffsetNormalized:[0,0],
        hardLockRetained,bothShipsVisible:false,
      },
    };
    return {snapshot,nextPresentationState};
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
