const contract = globalThis.MainComputerSpaceCaptainMultirateContract;
const authorityFactory = globalThis.MainComputerBridgeEncounterRuntime;
const projection = globalThis.MainComputerBridgeViewscreenProjection;
const wrapperFactory = globalThis.MainComputerBridgeViewscreenEncounterRuntime;
const presentation = globalThis.MainComputerBridgeViewscreenPresentation;
if (!contract) return {ok:false, error:'multirate-contract-missing'};
if (!authorityFactory) return {ok:false, error:'bridge-encounter-runtime-missing'};
if (!projection) return {ok:false, error:'bridge-viewscreen-projection-missing'};
if (!wrapperFactory) return {ok:false, error:'bridge-viewscreen-wrapper-missing'};
if (!presentation) return {ok:false, error:'bridge-viewscreen-presentation-missing'};

const EPS = 1e-8;
const START_MS = 1000;
const CONFIG = {tacticalSliceSeconds:3, physicsStepSeconds:0.2};
const FIRE_SECONDS = 7.02;
const BRIDGE_ENTRY_SECONDS = 8.0;
const POWER_OFF_SECONDS = 6.0;
const POWER_ON_SECONDS = 10.5;
const END_SECONDS = 12.0;
const RENDER_HZ = 60;
const stable = (value) => JSON.stringify(value);
const OBSERVER={bodyId:'ship.mother',positionM:[1000,2000,3000],velocityMps:[0,0,0],validThroughSeconds:40,forwardWorld:[1,0,0],upWorld:[0,0,1]};
const TARGET_WORLD=[1400,2000,3000];

// A world contact moves according to tactical authority, but its initial world
// location does not follow the observer. The old static-target test fixture
// could not verify continuous projection of moving physical contacts.
function worldTarget(state, initialTarget) {
  const ship = state.ships['ship.beta'];
  return [TARGET_WORLD[0] + ship.xM - initialTarget.xM,
          TARGET_WORLD[1] + ship.yM - initialTarget.yM, TARGET_WORLD[2]];
}

function authorityFingerprint(runtime) {
  const state = runtime.readAuthorityState();
  return stable({
    authority:state.authority,
    encounter:state.encounter,
    target:state.target,
    weapons:state.weapons,
    combatBoundarySeconds:state.combatBoundarySeconds,
    ships:state.ships,
  });
}

function runScenario({powerCycle=false} = {}) {
  const authority = authorityFactory.create(CONFIG);
  const wrapper = wrapperFactory.create({authority});
  const system = presentation.createSystem({
    initialMode:'encounter',
    selectedAtSimulationSeconds:0,
    displayPowered:true,
  });
  authority.advance(START_MS,{active:true});
  const initialTarget = authority.readAuthorityState().ships['ship.beta'];

  const rows = [];
  let fired = false;
  let powerOffApplied = false;
  let powerOnApplied = false;
  let lastProjection = null;
  let lastPresentation = null;
  const frameStep = 1 / RENDER_HZ;

  for (let frame=0;;frame++) {
    const t = Math.min(END_SECONDS, frame * frameStep);
    if (powerCycle && !powerOffApplied && t + EPS >= POWER_OFF_SECONDS) {
      system.setDisplayPowered(false);
      powerOffApplied = true;
    }
    if (powerCycle && !powerOnApplied && t + EPS >= POWER_ON_SECONDS) {
      system.setDisplayPowered(true);
      powerOnApplied = true;
    }
    if (!fired && t + EPS >= FIRE_SECONDS) {
      authority.advance(START_MS + FIRE_SECONDS * 1000,{active:true});
      const command = authority.command({type:'fire-primary-weapon'}, START_MS + FIRE_SECONDS * 1000);
      if (!command.accepted) return {ok:false,error:'fire-command-rejected'};
      fired = true;
    }
    authority.advance(START_MS + t * 1000,{active:true});
    lastProjection = wrapper.snapshot(START_MS + t * 1000,{active:true,observerPose:OBSERVER,
      targetWorldPositionM:worldTarget(authority.readAuthorityState(),initialTarget)});
    lastPresentation = system.present({encounterProjection:lastProjection});
    rows.push({
      t,
      simulatedPlayerLocation:t < BRIDGE_ENTRY_SECONDS ? 'shuttle' : 'bridge.deck',
      selectedMode:system.selectedMode(),
      displayPowered:system.displayPowered(),
      presentationMode:lastPresentation.mode,
      presentationSeconds:lastPresentation.time.simulationSeconds,
      phase:lastPresentation.encounter.phase,
      targetX:lastPresentation.target.screen.xNormalized,
      targetY:lastPresentation.target.screen.yNormalized,
      targetWorldPositionM:lastProjection.viewScreen.targetWorldPositionM.slice(),
      trackingMode:lastPresentation.environment.trackingMode,
      cameraCenterM:lastProjection.viewScreen.cameraCenterM.slice(),
      authorityAtSeconds:lastProjection.authority.atSeconds,
      projectileCount:lastPresentation.effects.projectiles.length,
      projectileLaunchWorld:(lastPresentation.effects.projectiles||[]).map(shot=>shot.originWorldPositionM||null),
      impactCount:lastPresentation.effects.impacts.length,
      explosionCount:lastPresentation.effects.explosions.length,
      debrisCount:lastPresentation.effects.debris.length,
      targetHullFraction:lastPresentation.target.hullFraction,
    });
    if (t >= END_SECONDS - EPS) break;
  }

  return {
    ok:true,
    authorityFingerprint:authorityFingerprint(authority),
    finalAuthority:authority.readAuthorityState(),
    finalProjection:lastProjection,
    finalPresentation:lastPresentation,
    systemState:system.state(),
    rows,
  };
}


function runDestructionPresentation() {
  const authority = authorityFactory.create(CONFIG);
  const wrapper = wrapperFactory.create({authority});
  const system = presentation.createSystem({initialMode:'encounter',displayPowered:true});
  authority.advance(START_MS,{active:true});
  authority.advance(START_MS + FIRE_SECONDS * 1000,{active:true});
  const first = authority.command({type:'fire-primary-weapon'}, START_MS + FIRE_SECONDS * 1000);
  if (!first.accepted) return {ok:false,error:'destruction-first-fire-rejected'};
  const firstImpact = Number(first.shot.impactAtSeconds);
  authority.advance(START_MS + (firstImpact + 0.41) * 1000,{active:true});
  const secondFire = firstImpact + 0.41;
  const second = authority.command({type:'fire-primary-weapon'}, START_MS + secondFire * 1000);
  if (!second.accepted) return {ok:false,error:'destruction-second-fire-rejected'};
  const secondImpact = Number(second.shot.impactAtSeconds);
  const observeAt = secondImpact + 0.1;
  authority.advance(START_MS + observeAt * 1000,{active:true});
  const projected = wrapper.snapshot(START_MS + observeAt * 1000,{active:true,observerPose:OBSERVER,targetWorldPositionM:TARGET_WORLD});
  const frame = system.present({encounterProjection:projected});
  return {ok:true, firstImpact, secondImpact, observeAt, frame};
}

const initialSystem = presentation.createSystem({initialMode:'encounter', displayPowered:true});
const initialState = initialSystem.state();
const initialStateBeforePower = stable(initialState);
initialSystem.setDisplayPowered(false);
const poweredOffState = initialSystem.state();
const initialStateAfterPower = stable(initialState);

const control = runScenario({powerCycle:false});
const cycled = runScenario({powerCycle:true});
const destruction = runDestructionPresentation();
if (!control.ok || !cycled.ok || !destruction.ok) return {ok:false,error:control.error||cycled.error||destruction.error};

const preBridgeRows = cycled.rows.filter(row => row.t < BRIDGE_ENTRY_SECONDS - EPS);
const offRows = cycled.rows.filter(row => row.t >= POWER_OFF_SECONDS - EPS && row.t < POWER_ON_SECONDS - EPS);
const beforeEntry = cycled.rows.reduce((best,row)=> row.t < BRIDGE_ENTRY_SECONDS && (!best || row.t > best.t) ? row : best, null);
const afterEntry = cycled.rows.find(row => row.t + EPS >= BRIDGE_ENTRY_SECONDS);
const repowered = cycled.rows.find(row => row.t + EPS >= POWER_ON_SECONDS);
const offTargetWorldTravelM = offRows.length > 1
  ? Math.hypot(...offRows.at(-1).targetWorldPositionM.map((v,i)=>v-offRows[0].targetWorldPositionM[i]))
  : 0;
const projectileFrames = control.rows.filter(row=>row.projectileCount > 0);
const impactFrames = control.rows.filter(row=>row.impactCount > 0);

const immutableProjection = control.finalProjection;
const immutableSystem = presentation.createSystem({initialMode:'encounter',displayPowered:true});
const immutableBefore = stable(immutableProjection);
const immutablePresentation = immutableSystem.present({encounterProjection:immutableProjection});
const immutableAfter = stable(immutableProjection);

const deepFrozen = Object.isFrozen(immutablePresentation)
  && Object.isFrozen(immutablePresentation.target)
  && Object.isFrozen(immutablePresentation.target.screen)
  && Object.isFrozen(immutablePresentation.effects)
  && Object.isFrozen(immutablePresentation.effects.projectiles);

const checks = {
  phase1ContractStillLoaded: contract.SCHEMA === 'game.spaceCaptainMultirateContract.v1',
  phase2AuthorityStillLoaded: authorityFactory.SCHEMA === 'game.bridgeEncounterRuntime.v1',
  phase3ProjectionStillLoaded: projection.SCHEMA === 'game.bridgeViewscreenProjection.v1',
  presentationHasDedicatedSchema: presentation.SCHEMA === 'game.bridgeViewscreenPresentation.v1',
  initialPresentationModeIsPreselectedEncounter: initialState.selectedMode === 'encounter' && Math.abs(Number(initialState.selectedAtSimulationSeconds)) <= EPS,
  displayPowerIsSeparateFromSelectedMode: poweredOffState.displayPowered === false && poweredOffState.selectedMode === 'encounter' && poweredOffState.selectionRevision === initialState.selectionRevision,
  displayPowerChangeDoesNotMutatePriorSystemState: initialStateBeforePower === initialStateAfterPower,
  presentationRunsBeforeBridgeEntry: preBridgeRows.length > 0 && preBridgeRows.every(row => row.presentationMode === 'encounter' && row.presentationSeconds >= -EPS),
  bridgeEntryDoesNotSelectOrResetPresentation: beforeEntry && afterEntry && beforeEntry.selectedMode === afterEntry.selectedMode && afterEntry.presentationSeconds >= beforeEntry.presentationSeconds,
  displayPowerDoesNotChangeAuthority: control.authorityFingerprint === cycled.authorityFingerprint,
  displayPowerDoesNotResetProjectionCamera: stable(control.finalProjection.viewScreen.cameraCenterM) === stable(cycled.finalProjection.viewScreen.cameraCenterM),
  selectedPresentationContinuesWhileDisplayIsOff: offRows.length > 0 && offRows.every(row => row.displayPowered === false && row.presentationMode === 'encounter') && offTargetWorldTravelM > 1 &&
    offRows.every(row=>row.trackingMode==='optical-target-track' && Math.abs(row.targetX)<1e-6 && Math.abs(row.targetY)<1e-6),
  repowerShowsCurrentStateNotReplay: repowered && repowered.displayPowered === true && repowered.presentationSeconds + EPS >= POWER_ON_SECONDS && repowered.phase === 'combat',
  projectionInputRemainsUnchangedByPresentation: immutableBefore === immutableAfter,
  presentationFrameIsDeepFrozen: deepFrozen,
  presentationCarriesNoAuthorityMutationApi: typeof presentation.advance === 'undefined' && typeof presentation.command === 'undefined' && typeof presentation.playerFire === 'undefined',
  encounterPresentationUsesNormalizedScreenContract: Number.isFinite(immutablePresentation.target.screen.xNormalized) && Number.isFinite(immutablePresentation.target.screen.yNormalized),
  playerShipIsNotExternalVisibleVessel: control.finalPresentation.ownShip?.visible === false && cycled.finalPresentation.ownShip?.visible === false,
  presentationKeepsAuthoritativeObserver: control.finalPresentation.observer?.bodyId === 'ship.mother'
    && Array.isArray(control.finalPresentation.observer?.positionM)
    && control.finalPresentation.observer.positionM.length === 3
    && control.finalPresentation.observer.positionM.every(Number.isFinite),
  projectileEffectIsDerivedFromExactShotTimes: projectileFrames.length > 0 && projectileFrames.every(row => row.targetHullFraction === 1),
  projectileLaunchUsesRealMotherShipWorldPosition: projectileFrames.length > 0 && projectileFrames.every(row =>
    row.projectileLaunchWorld.length > 0 && row.projectileLaunchWorld.every(origin =>
      Array.isArray(origin) && origin.length === 3 && origin.every(Number.isFinite) &&
      Math.hypot(...origin.map((value,index)=>value-OBSERVER.positionM[index]))<=1e-6)),
  impactEffectAndDamageAppearAfterAuthorityImpact: impactFrames.length > 0 && impactFrames.some(row => Math.abs(Number(row.targetHullFraction) - 0.5) <= EPS),
  destructionPresentationComesFromAuthorityState: destruction.frame.target.visualState === 'destroyed' && destruction.frame.target.hullFraction === 0 && destruction.frame.effects.explosions.length === 1 && destruction.frame.effects.debris.length === 1,
  undestroyedEnemyNeverReceivesDestructionEffects: control.rows.filter(row=>row.targetHullFraction>0).every(row=>row.explosionCount===0 && row.debrisCount===0),
};
const failedChecks = Object.entries(checks).filter(([,passed])=>!passed).map(([name])=>name);

return {
  ok:failedChecks.length===0,
  schema:'game.spaceCaptainPhase4Part1ViewscreenPresentationProbe.v1',
  checks,
  failedChecks,
  metrics:{
    configuredTacticalSliceSeconds:CONFIG.tacticalSliceSeconds,
    configuredPhysicsStepSeconds:CONFIG.physicsStepSeconds,
    renderHz:RENDER_HZ,
    bridgeEntryAtSeconds:BRIDGE_ENTRY_SECONDS,
    displayPowerOffAtSeconds:POWER_OFF_SECONDS,
    displayPowerOnAtSeconds:POWER_ON_SECONDS,
    preBridgePresentationFrameCount:preBridgeRows.length,
    poweredOffPresentationFrameCount:offRows.length,
    poweredOffTargetWorldTravelM:offTargetWorldTravelM,
    projectilePresentationFrameCount:projectileFrames.length,
    projectileLaunchExample:projectileFrames[0]?.projectileLaunchWorld??null,
    impactPresentationFrameCount:impactFrames.length,
    destructionPresentationAtSeconds:destruction.observeAt,
    finalControlPhase:control.finalPresentation.encounter.phase,
    finalCycledPhase:cycled.finalPresentation.encounter.phase,
    finalAuthorityIdentical:control.authorityFingerprint === cycled.authorityFingerprint,
    observerInPresentation:control.finalPresentation.observer||null,
    playerShipExternalVisible:control.finalPresentation.ownShip?.visible??null,
    finalCameraIdentical:stable(control.finalProjection.viewScreen.cameraCenterM) === stable(cycled.finalProjection.viewScreen.cameraCenterM),
    repoweredPresentationSeconds:repowered?.presentationSeconds ?? null,
    repoweredTargetOffsetNormalized:repowered ? [repowered.targetX, repowered.targetY] : null,
    initialSystemState:initialState,
    poweredOffSystemState:poweredOffState,
  }
};
