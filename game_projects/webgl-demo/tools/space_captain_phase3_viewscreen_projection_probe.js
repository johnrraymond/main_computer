const contract = globalThis.MainComputerSpaceCaptainMultirateContract;
const authorityFactory = globalThis.MainComputerBridgeEncounterRuntime;
const projection = globalThis.MainComputerBridgeViewscreenProjection;
const wrapperFactory = globalThis.MainComputerBridgeViewscreenEncounterRuntime;
if (!contract) return {ok:false, error:'multirate-contract-missing'};
if (!authorityFactory) return {ok:false, error:'bridge-encounter-runtime-missing'};
if (!projection) return {ok:false, error:'bridge-viewscreen-projection-missing'};
if (!wrapperFactory) return {ok:false, error:'bridge-viewscreen-wrapper-missing'};

const EPS = 1e-8;
const START_MS = 1000;
const CONFIG = {tacticalSliceSeconds:3, physicsStepSeconds:0.2};
const FIRE_SECONDS = 7.02;
const END_SECONDS = 12.0;
const PLAYER = 'ship.alpha';
const TARGET = 'ship.beta';
const stable = (value) => JSON.stringify(value);
const finiteVec3 = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
const separation = (a,b) => finiteVec3(a) && finiteVec3(b) ? Math.hypot(...a.map((value,index) => value-b[index])) : null;
const close = (value,expected,tol=1e-6) => Number.isFinite(value) && Math.abs(value-expected) <= tol;
const point = (value) => Array.isArray(value) && value.length === 2 && value.every(Number.isFinite);
const OBSERVER = Object.freeze({bodyId:'ship.mother',positionM:[1000,2000,3000],velocityMps:[0,0,0],validThroughSeconds:12,forwardWorld:[1,0,0],upWorld:[0,0,1]});
const ENEMY_WORLD = [1400,2000,3000];
const OFFSET = [11000,-7000,5000];
const translated = values => values.map((value,index)=>value+OFFSET[index]);
const withPose = (pose, targetWorldPositionM=ENEMY_WORLD,viewMode="fixed") => projection.project({
  authorityState,simulationSeconds:0.1,observerPose:pose,targetWorldPositionM,viewMode
}).snapshot;



const authority = authorityFactory.create(CONFIG);
authority.advance(START_MS,{active:true});
const authorityState = authority.readAuthorityState();
const authorityBefore = stable(authorityState);
const projectedA = projection.project({authorityState, simulationSeconds:0.1, observerPose:OBSERVER, targetWorldPositionM:ENEMY_WORLD});
const authorityAfter = stable(authorityState);
const projectedARepeat = projection.project({authorityState, simulationSeconds:0.1, observerPose:OBSERVER, targetWorldPositionM:ENEMY_WORLD});

const spatial = {};
for (const [name,observerPose,targetWorldPositionM] of [
  ['base',OBSERVER,ENEMY_WORLD],
  ['shipMoved',{...OBSERVER,positionM:[1100,2000,3000]},ENEMY_WORLD],
  ['translated',{...OBSERVER,positionM:translated(OBSERVER.positionM)},translated(ENEMY_WORLD)],
  ['rotated',{...OBSERVER,forwardWorld:[0,1,0]},ENEMY_WORLD],
  ['offAxis',OBSERVER,[1400,2100,3000]],
  ['behind',OBSERVER,[600,2000,3000]],
]) {
  try {spatial[name]={frame:withPose(observerPose,targetWorldPositionM)};}
  catch (error) {spatial[name]={error:String(error?.message||error)};}
}
let missingObserverError='';
try {projection.project({authorityState,simulationSeconds:0.1,targetWorldPositionM:ENEMY_WORLD});}
catch (error) {missingObserverError=String(error?.message||error);}
let staleObserverError='';
try {projection.project({authorityState,simulationSeconds:0.1,observerPose:{...OBSERVER,positionM:[NaN,0,0]},targetWorldPositionM:ENEMY_WORLD});}
catch (error) {staleObserverError=String(error?.message||error);}
let expiredObserverError='';
try {projection.project({authorityState,simulationSeconds:0.1,observerPose:{...OBSERVER,validThroughSeconds:0.05},targetWorldPositionM:ENEMY_WORLD});}
catch (error) {expiredObserverError=String(error?.message||error);}
const screen = key => spatial[key]?.frame?.viewScreen || {};
const tracking = {};
for (const [name,pose,target] of [
  ['front',OBSERVER,ENEMY_WORLD],
  ['rear',OBSERVER,[600,2000,3000]],
  ['left',OBSERVER,[1000,1600,3000]],
  ['right',OBSERVER,[1000,2400,3000]],
  ['above',OBSERVER,[1000,2000,3400]],
  ['below',OBSERVER,[1000,2000,2600]],
  ['moved',{...OBSERVER,positionM:[1100,2000,3000]},ENEMY_WORLD],
]) tracking[name] = withPose(pose,target,"track").viewScreen;
let coincidentError = "";
try {withPose(OBSERVER,OBSERVER.positionM,"track");}
catch(error) {coincidentError=String(error?.message||error);}
const cameraError = key => separation(screen(key).cameraWorldPositionM,key==='translated'?translated(OBSERVER.positionM):key==='shipMoved'?[1100,2000,3000]:OBSERVER.positionM);
const relativeError = (key,expected) => separation(screen(key).targetRelativeWorldM,expected);
const baseTargetScreen=screen('base').targetOffsetNormalized;

const targetAnchor = authorityState.ships[TARGET];
const dt = 0.1 - Number(authorityState.authority.atSeconds);
const expectedTargetX = Number(targetAnchor.xM) + Number(targetAnchor.vxMps) * dt + 0.5 * Number(targetAnchor.axMps2) * dt * dt;
const expectedTargetY = Number(targetAnchor.yM) + Number(targetAnchor.vyMps) * dt + 0.5 * Number(targetAnchor.ayMps2) * dt * dt;
const predictedTarget = projectedA.snapshot.ships[TARGET];

const presentationInput = projectedA.nextPresentationState;
const presentationBefore = stable(presentationInput);
const projectedB = projection.project({authorityState, simulationSeconds:0.15, presentationState:presentationInput, observerPose:OBSERVER, targetWorldPositionM:ENEMY_WORLD});
const presentationAfter = stable(presentationInput);

let staleProjectionError = null;
try {
  projection.project({authorityState, simulationSeconds:0.2, presentationState:presentationInput, observerPose:OBSERVER, targetWorldPositionM:ENEMY_WORLD});
} catch (error) {
  staleProjectionError = String(error?.message || error);
}

function runCadence(renderHz) {
  const runtime = authorityFactory.create(CONFIG);
  runtime.advance(START_MS,{active:true});
  let presentationState = null;
  let fired = false;
  let lastSnapshot = null;
  let hardLockFrames = 0;
  let maxCameraOriginErrorM = 0;
  let observerReportedFrames = 0;
  let frameCount = 0;
  let maxOffset = 0;
  const frameStep = 1 / Number(renderHz);
  for (let frame = 0; ; frame++) {
    const t = Math.min(END_SECONDS, frame * frameStep);
    if (!fired && t + EPS >= FIRE_SECONDS) {
      runtime.advance(START_MS + FIRE_SECONDS * 1000,{active:true});
      const command = runtime.command({type:'fire-primary-weapon'}, START_MS + FIRE_SECONDS * 1000);
      if (!command.accepted) return {ok:false, renderHz, error:'fire-command-rejected'};
      fired = true;
    }
    runtime.advance(START_MS + t * 1000,{active:true});
    const state = runtime.readAuthorityState();
    const result = projection.project({authorityState:state, simulationSeconds:t, presentationState, observerPose:OBSERVER, targetWorldPositionM:ENEMY_WORLD});
    presentationState = result.nextPresentationState;
    lastSnapshot = result.snapshot;
    frameCount += 1;
    if (lastSnapshot.viewScreen.hardLockRetained) hardLockFrames += 1;
    const originError=separation(lastSnapshot.viewScreen.cameraWorldPositionM,OBSERVER.positionM);
    if (originError !== null) {maxCameraOriginErrorM=Math.max(maxCameraOriginErrorM,originError);observerReportedFrames+=1;}
    maxOffset = Math.max(maxOffset, Math.hypot(...lastSnapshot.viewScreen.targetOffsetNormalized.map(Number)));
    if (t >= END_SECONDS - EPS) break;
  }
  const finalAuthority = runtime.readAuthorityState();
  return {
    ok:true,
    renderHz:Number(renderHz),
    frames:frameCount,
    authorityFingerprint:stable({
      authority:finalAuthority.authority,
      encounter:finalAuthority.encounter,
      target:finalAuthority.target,
      weapons:finalAuthority.weapons,
      combatBoundarySeconds:finalAuthority.combatBoundarySeconds,
      ships:finalAuthority.ships,
    }),
    hardLockFraction:hardLockFrames / Math.max(1,frameCount),
    observerReportedFrames,
    maxCameraOriginErrorM:observerReportedFrames?maxCameraOriginErrorM:null,
    maxTargetOffsetNormalized:maxOffset,
    phase:lastSnapshot?.phase,
    targetHullPercent:lastSnapshot?.target?.hullPercent,
    combatBoundarySeconds:lastSnapshot?.combatBoundarySeconds,
  };
}

const cadenceRows = [30,60,120,165].map(runCadence);
const cadenceFingerprints = new Set(cadenceRows.map(row => row.authorityFingerprint));

const wrapperAuthority = authorityFactory.create(CONFIG);
const wrapper = wrapperFactory.create({authority:wrapperAuthority});
wrapper.advance(START_MS,{active:true});
const wrapperSnapshot = wrapper.snapshot(START_MS,{active:true,observerPose:OBSERVER,targetWorldPositionM:ENEMY_WORLD});

const checks = {
  phase1ContractStillLoaded: contract.SCHEMA === 'game.spaceCaptainMultirateContract.v1',
  phase2AuthorityStillLoaded: authorityFactory.SCHEMA === 'game.bridgeEncounterRuntime.v1',
  projectionHasDedicatedSchema: projection.SCHEMA === 'game.bridgeViewscreenProjection.v1',
  projectionConsumesAuthorityStateSchema: projection.AUTHORITY_SCHEMA === 'game.bridgeEncounterAuthorityState.v1',
  projectionReadLeavesAuthorityObjectUnchanged: authorityBefore === authorityAfter,
  projectionIsDeterministicForSameInputs: stable(projectedA) === stable(projectedARepeat),
  projectionPredictsFromAuthorityAnchor: Math.abs(Number(predictedTarget.xM) - expectedTargetX) <= EPS && Math.abs(Number(predictedTarget.yM) - expectedTargetY) <= EPS,
  cameraStateIsExplicitOutputNotInputMutation: presentationBefore === presentationAfter && projectedB.nextPresentationState !== presentationInput,
  staleAuthorityProjectionIsRejected: String(staleProjectionError || '').includes('BRIDGE_VIEWSCREEN_PROJECTION_AUTHORITY_STALE'),
  projectionCarriesNoCombatMutationApi: typeof projection.advance === 'undefined' && typeof projection.command === 'undefined' && typeof projection.playerFire === 'undefined',
  renderCadenceDoesNotChangeAuthority: cadenceRows.every(row => row.ok) && cadenceFingerprints.size === 1,
  // A moving target may be tracked by rotating the view, never by translating the camera.
  cameraOriginIsAuthoritativeAcrossCadences: cadenceRows.every(row => row.observerReportedFrames === row.frames && close(row.maxCameraOriginErrorM,0)),
  cameraAtRealShipPosition: close(cameraError('base'),0),
  cameraTracksMovedShipExactly: close(cameraError('shipMoved'),0),
  targetRelativeWorldPositionIsCorrect: close(relativeError('base',[400,0,0]),0),
  targetCameraAxesMatchActualView: close(separation(screen('base').targetRelativeCameraM,[0,0,400]),0) &&
    close(separation(screen('offAxis').targetRelativeCameraM,[100,0,400]),0) &&
    close(separation(screen('rotated').targetRelativeCameraM,[-400,0,0]),0),
  movingShipChangesRangeNotWorldTarget: close(relativeError('shipMoved',[300,0,0]),0) && close(Number(spatial.shipMoved?.frame?.rangeM),300),
  translatingEntireWorldPreservesView: close(cameraError('translated'),0) && close(relativeError('translated',[400,0,0]),0) &&
    point(baseTargetScreen) && point(screen('translated').targetOffsetNormalized) &&
    close(Math.hypot(...baseTargetScreen.map((value,index)=>value-screen('translated').targetOffsetNormalized[index])),0),
  rotatingViewDoesNotTranslateCamera: close(cameraError('rotated'),0) &&
    close(separation(screen('rotated').cameraForwardWorld,[0,1,0]),0) &&
    (screen('rotated').targetInFront === false ||
      (point(screen('rotated').targetOffsetNormalized) && point(baseTargetScreen) &&
      Math.hypot(...baseTargetScreen.map((v,i)=>v-screen('rotated').targetOffsetNormalized[i]))>1e-6)),
  objectBehindCameraIsExcluded: screen('behind').targetInFront === false && screen('behind').targetVisible === false,
  trackingDefaultIsTargetCentered: projectedA.snapshot.viewScreen.mode === 'ship-mounted-track' &&
    close(Math.hypot(...projectedA.snapshot.viewScreen.targetOffsetNormalized),0,1e-8),
  trackingCoversFullSphere: Object.values(tracking).every(view=>view.targetVisible && view.targetInFront &&
    close(Math.hypot(...view.targetOffsetNormalized),0,1e-8)),
  trackingRotatesWithoutMovingCamera: Object.entries(tracking).every(([name,view])=>
    close(separation(view.cameraWorldPositionM,name==='moved'?[1100,2000,3000]:OBSERVER.positionM),0) &&
    close(Math.hypot(...view.cameraForwardWorld),1,1e-8)),
  trackingDoesNotAlterShipAttitude: Object.values(tracking).every(view=>
    close(separation(view.shipForwardWorld,OBSERVER.forwardWorld),0)),
  trackingRejectsCoincidentTarget: coincidentError.includes('BRIDGE_VIEWSCREEN_TRACK_TARGET_COINCIDENT'),
  missingAuthoritativeObserverIsRejected: Boolean(missingObserverError),
  invalidAuthoritativeObserverIsRejected: Boolean(staleObserverError),
  expiredAuthoritativeObserverIsRejected: Boolean(expiredObserverError),
  encounterReachesSameCombatPhaseAcrossCadences: cadenceRows.every(row => row.phase === 'combat' && Math.abs(Number(row.combatBoundarySeconds) - 9.0) <= EPS),
  transitionalWrapperDelegatesToProjection: wrapperFactory.PROJECTION_SCHEMA === projection.SCHEMA && wrapperSnapshot.schema === projection.SCHEMA,
};
const failedChecks = Object.entries(checks).filter(([,passed])=>!passed).map(([name])=>name);
return {
  ok:failedChecks.length===0,
  schema:'game.spaceCaptainPhase3ViewscreenProjectionProbe.v1',
  checks,
  failedChecks,
  metrics:{
    configuredTacticalSliceSeconds:CONFIG.tacticalSliceSeconds,
    configuredPhysicsStepSeconds:CONFIG.physicsStepSeconds,
    directPredictionErrorM:Math.hypot(Number(predictedTarget.xM)-expectedTargetX, Number(predictedTarget.yM)-expectedTargetY),
    staleProjectionError,
    missingObserverError,
    staleObserverError,
    expiredObserverError,
    cameraOriginErrorM:cameraError('base'),
    movedCameraOriginErrorM:cameraError('shipMoved'),
    targetRelativePositionErrorM:relativeError('base',[400,0,0]),
    targetRangeErrorM:Number.isFinite(Number(spatial.base?.frame?.rangeM)) ? Math.abs(Number(spatial.base.frame.rangeM)-400) : null,
    viewDirectionErrorDeg:finiteVec3(screen('rotated').cameraForwardWorld) ? Math.acos(Math.max(-1,Math.min(1,screen('rotated').cameraForwardWorld[1]))) * 180/Math.PI : null,
    spatialCases:Object.fromEntries(Object.entries(spatial).map(([name,row])=>[name,row.error ? {error:row.error} : {
      cameraWorldPositionM:row.frame?.viewScreen?.cameraWorldPositionM ?? null,
      cameraForwardWorld:row.frame?.viewScreen?.cameraForwardWorld ?? null,
      targetWorldPositionM:row.frame?.viewScreen?.targetWorldPositionM ?? null,
      targetRelativeWorldM:row.frame?.viewScreen?.targetRelativeWorldM ?? null,
      targetRelativeCameraM:row.frame?.viewScreen?.targetRelativeCameraM ?? null,
      targetOffsetNormalized:row.frame?.viewScreen?.targetOffsetNormalized ?? null,
      targetInFront:row.frame?.viewScreen?.targetInFront ?? null,
      rangeM:row.frame?.rangeM ?? null,
    }])),
    trackingModes:Object.fromEntries(Object.entries(tracking).map(([name,view])=>[name,{
      mode:view.mode,visible:view.targetVisible,forward:view.cameraForwardWorld,rangeM:Math.hypot(...view.targetRelativeWorldM)
    }])),
    renderCadencesHz:cadenceRows.map(row=>row.renderHz),
    cadenceRows:cadenceRows.map(({authorityFingerprint,...row})=>row),
    authoritativeSolutionCountAcrossCadences:cadenceFingerprints.size,
    wrapperSnapshotSchema:wrapperSnapshot.schema,
  }
};
