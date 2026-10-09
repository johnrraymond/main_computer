const contract = globalThis.MainComputerSpaceCaptainMultirateContract;
const authorityFactory = globalThis.MainComputerBridgeEncounterRuntime;
const projection = globalThis.MainComputerBridgeViewscreenProjection;
const wrapperFactory = globalThis.MainComputerBridgeViewscreenEncounterRuntime;
const presentation = globalThis.MainComputerBridgeViewscreenPresentation;
const renderer = globalThis.MainComputerBridgeViewscreenRenderer;
if (!contract) return {ok:false,error:'multirate-contract-missing'};
if (!authorityFactory) return {ok:false,error:'bridge-encounter-runtime-missing'};
if (!projection) return {ok:false,error:'bridge-viewscreen-projection-missing'};
if (!wrapperFactory) return {ok:false,error:'bridge-viewscreen-wrapper-missing'};
if (!presentation) return {ok:false,error:'bridge-viewscreen-presentation-missing'};
if (!renderer) return {ok:false,error:'bridge-viewscreen-renderer-missing'};

const EPS = 1e-8;
const START_MS = 1000;
const CONFIG = {tacticalSliceSeconds:3, physicsStepSeconds:0.2};
const FIRE_SECONDS = 7.02;
const SURFACE = {position:[0,-39.12], size:[6.9,2.1,0.08], color:'#38bdf8'};
const stable = (value) => JSON.stringify(value);
const OBSERVER={bodyId:'ship.mother',positionM:[1000,2000,3000],velocityMps:[0,0,0],validThroughSeconds:40,forwardWorld:[1,0,0],upWorld:[0,0,1]};
const TARGET_WORLD=[1400,2000,3000];
const distance2 = (a,b) => Math.hypot(Number(a?.[0]||0)-Number(b?.[0]||0), Number(a?.[1]||0)-Number(b?.[1]||0));

function createBuilder() {
  const calls = [];
  const color = (hex, emissive=false) => `${String(hex)}${emissive ? ':e' : ''}`;
  return {
    calls,
    color,
    box(min,max,material) { calls.push({kind:'box',min:min.slice(),max:max.slice(),material}); },
    beam(from,to,width,material) { calls.push({kind:'beam',from:from.slice(),to:to.slice(),width:Number(width),material}); },
    ellipsoid(center,radii,segments,rings,material) { calls.push({kind:'ellipsoid',center:center.slice(),radii:radii.slice(),segments:Number(segments),rings:Number(rings),material}); },
  };
}

function renderFrame(frame) {
  const builder = createBuilder();
  const before = stable(frame);
  const result = renderer.render({builder,surface:SURFACE,presentation:frame});
  const after = stable(frame);
  return {builder,result,before,after,fingerprint:stable(builder.calls)};
}

function expectedScreenPoint(screen) {
  const centerX = Number(SURFACE.position[0]);
  const width = Number(SURFACE.size[0]);
  const y0 = 0.18;
  const height = Number(SURFACE.size[1]);
  return [
    centerX + Number(screen.xNormalized) * width * 0.5,
    y0 + height * 0.5 + Number(screen.yNormalized) * height * 0.5,
  ];
}

function getFrame(authority, wrapper, system, seconds) {
  authority.advance(START_MS + seconds * 1000,{active:true});
  const target = authority.readAuthorityState().ships['ship.beta'];
  const projected = wrapper.snapshot(START_MS + seconds * 1000,{active:true,observerPose:OBSERVER,
    targetWorldPositionM:[TARGET_WORLD[0]+target.xM-initialTarget.xM,
      TARGET_WORLD[1]+target.yM-initialTarget.yM,TARGET_WORLD[2]]});
  return {projected, frame:system.present({encounterProjection:projected})};
}

const authority = authorityFactory.create(CONFIG);
const wrapper = wrapperFactory.create({authority});
const system = presentation.createSystem({initialMode:'encounter',selectedAtSimulationSeconds:0,displayPowered:true});
authority.advance(START_MS,{active:true});
const initialTarget = authority.readAuthorityState().ships['ship.beta'];

const early = getFrame(authority,wrapper,system,2.0);
const earlyRender = renderFrame(early.frame);
const earlyRepeat = renderFrame(early.frame);
const mid = getFrame(authority,wrapper,system,6.0);
const midRender = renderFrame(mid.frame);

// First shot: verify projectile and impact rendering come only from the presentation frame.
authority.advance(START_MS + FIRE_SECONDS*1000,{active:true});
const firstFire = authority.command({type:'fire-primary-weapon'}, START_MS + FIRE_SECONDS*1000);
if (!firstFire.accepted) return {ok:false,error:'first-fire-rejected'};
const projectileTime = FIRE_SECONDS + Math.min(0.12, (Number(firstFire.shot.impactAtSeconds)-FIRE_SECONDS)*0.45);
const projectileFrame = getFrame(authority,wrapper,system,projectileTime);
const projectileRender = renderFrame(projectileFrame.frame);
const firstImpact = Number(firstFire.shot.impactAtSeconds);
const impactFrame = getFrame(authority,wrapper,system,firstImpact + 0.1);
const impactRender = renderFrame(impactFrame.frame);

// Second shot: produce the authoritative destruction presentation.
const secondFireAt = firstImpact + 0.41;
authority.advance(START_MS + secondFireAt*1000,{active:true});
const secondFire = authority.command({type:'fire-primary-weapon'}, START_MS + secondFireAt*1000);
if (!secondFire.accepted) return {ok:false,error:'second-fire-rejected'};
const secondImpact = Number(secondFire.shot.impactAtSeconds);
const destructionFrame = getFrame(authority,wrapper,system,secondImpact + 0.1);
const destructionRender = renderFrame(destructionFrame.frame);

// Display power is output-only. Re-present the same projection under a powered-off system state.
const poweredPresentationBefore = stable(destructionFrame.frame);
system.setDisplayPowered(false);
const poweredOffFrame = system.present({encounterProjection:destructionFrame.projected});
const poweredOffRender = renderFrame(poweredOffFrame);
const poweredPresentationAfter = stable(destructionFrame.frame);

// Rendering the same immutable frame repeatedly must not mutate it or accumulate hidden renderer state.
const immutableFrame = impactFrame.frame;
const immutableBefore = stable(immutableFrame);
for (let index=0; index<200; index+=1) renderFrame(immutableFrame);
const immutableAfter = stable(immutableFrame);

let unsupportedModeError = '';
try {
  const unsupported = JSON.parse(stable(early.frame));
  unsupported.mode = 'legacy-fallback';
  renderer.render({builder:createBuilder(),surface:SURFACE,presentation:unsupported});
} catch (error) {
  unsupportedModeError = String(error && error.message || error);
}

// Adversarial stale frame: forcing old 'ownShip.visible' must not render the observer vessel.
const staleSelfFrame=JSON.parse(stable(early.frame));
staleSelfFrame.ownShip={id:'ship.mother',visible:true,screen:{xNormalized:0,yNormalized:0},visualState:'nominal'};
const staleSelfRender=renderFrame(staleSelfFrame);
const expectedTargetEarly = expectedScreenPoint(early.frame.target.screen);
const expectedTargetMid = expectedScreenPoint(mid.frame.target.screen);
const expectedProjectile = expectedScreenPoint(projectileFrame.frame.effects.projectiles[0]?.screen || {xNormalized:0,yNormalized:0});
const projectileBeam = projectileRender.builder.calls.find(call => call.kind === 'beam' && call.material === '#f97316:e');
const expectedImpact = expectedScreenPoint(impactFrame.frame.effects.impacts[0]?.screen || impactFrame.frame.target.screen);
const impactEllipsoid = impactRender.builder.calls.find(call => call.kind === 'ellipsoid' && call.material === '#fef3c7:e');
const expectedExplosion = expectedScreenPoint(destructionFrame.frame.effects.explosions[0]?.screen || destructionFrame.frame.target.screen);
const explosionEllipsoid = destructionRender.builder.calls.find(call => call.kind === 'ellipsoid' && call.material === '#ef4444:e');

const checks = {
  phase1ContractStillLoaded: contract.SCHEMA === 'game.spaceCaptainMultirateContract.v1',
  phase2AuthorityStillLoaded: authorityFactory.SCHEMA === 'game.bridgeEncounterRuntime.v1',
  phase3ProjectionStillLoaded: projection.SCHEMA === 'game.bridgeViewscreenProjection.v1',
  phase4Part1PresentationStillLoaded: presentation.SCHEMA === 'game.bridgeViewscreenPresentation.v1',
  rendererHasDedicatedSchema: renderer.SCHEMA === 'game.bridgeViewscreenRenderer.v1',
  rendererConsumesPresentationSchema: renderer.PRESENTATION_SCHEMA === presentation.SCHEMA,
  identicalPresentationProducesIdenticalGeometry: earlyRender.fingerprint === earlyRepeat.fingerprint,
  rendererLeavesPresentationUnchanged: earlyRender.before === earlyRender.after && immutableBefore === immutableAfter,
  rendererUsesNormalizedTargetPosition: distance2(earlyRender.result.targetCenter,expectedTargetEarly) <= EPS,
  normalRendererDoesNotDrawOwnShip: earlyRender.result.ownShipCenter === null && midRender.result.ownShipCenter === null,
  staleSelfVisibilityFlagCannotDrawObserver: staleSelfRender.result.ownShipCenter === null &&
    staleSelfRender.builder.calls.filter(call=>call.material==='#bae6fd:e').length === 0,
  rendererKeepsEnemyGeometryWhenSelfIsSuppressed: distance2(staleSelfRender.result.targetCenter,expectedTargetEarly) <= EPS,
  trackingCentersMovingPhysicalTarget: distance2(earlyRender.result.targetCenter,expectedTargetEarly) <= EPS &&
    distance2(midRender.result.targetCenter,expectedTargetMid) <= EPS &&
    distance2(earlyRender.result.targetCenter,midRender.result.targetCenter) <= EPS &&
    Math.hypot(...early.projected.viewScreen.targetWorldPositionM.map((value,i)=>
      value-mid.projected.viewScreen.targetWorldPositionM[i])) > 1 &&
    Math.hypot(...early.projected.viewScreen.cameraForwardWorld.map((value,i)=>
      value-mid.projected.viewScreen.cameraForwardWorld[i])) > 1e-5,
  projectileGeometryFollowsPresentation: Boolean(projectileBeam) && distance2(projectileBeam.to,expectedProjectile) <= EPS,
  impactGeometryFollowsPresentation: Boolean(impactEllipsoid) && distance2(impactEllipsoid.center,expectedImpact) <= EPS,
  destructionGeometryFollowsPresentation: Boolean(explosionEllipsoid) && distance2(explosionEllipsoid.center,expectedExplosion) <= EPS,
  hullDamageComesFromPresentation: Math.abs(Number(impactFrame.frame.target.hullFraction)-0.5) <= EPS,
  destructionComesFromPresentation: destructionFrame.frame.target.visualState === 'destroyed' && destructionFrame.frame.effects.explosions.length === 1 && destructionFrame.frame.effects.debris.length === 1,
  poweredOffRendererEmitsDarkSurfaceOnly: poweredOffRender.result.powered === false && poweredOffRender.builder.calls.length === 1 && poweredOffRender.builder.calls[0].kind === 'box',
  displayPowerDoesNotMutatePriorPresentation: poweredPresentationBefore === poweredPresentationAfter,
  unsupportedModeFailsInsteadOfFallingBack: unsupportedModeError.includes('BRIDGE_VIEWSCREEN_RENDERER_MODE_NOT_IMPLEMENTED'),
  rendererCarriesNoMutationApi: typeof renderer.advance === 'undefined' && typeof renderer.command === 'undefined' && typeof renderer.playerFire === 'undefined',
};
const failedChecks = Object.entries(checks).filter(([,passed])=>!passed).map(([name])=>name);

return {
  ok:failedChecks.length===0,
  schema:'game.spaceCaptainPhase4Part2ViewscreenRendererProbe.v1',
  checks,
  failedChecks,
  metrics:{
    configuredTacticalSliceSeconds:CONFIG.tacticalSliceSeconds,
    configuredPhysicsStepSeconds:CONFIG.physicsStepSeconds,
    earlySimulationSeconds:early.frame.time.simulationSeconds,
    midSimulationSeconds:mid.frame.time.simulationSeconds,
    targetTravelOnPhysicalScreen:distance2(earlyRender.result.targetCenter,midRender.result.targetCenter),
    projectilePresentationSeconds:projectileFrame.frame.time.simulationSeconds,
    firstImpactSeconds:firstImpact,
    secondImpactSeconds:secondImpact,
    impactHullFraction:impactFrame.frame.target.hullFraction,
    destructionVisualState:destructionFrame.frame.target.visualState,
    poweredOffPrimitiveCount:poweredOffRender.builder.calls.length,
    repeatedRenderCount:200,
    staleSelfGeometryCount:staleSelfRender.builder.calls.filter(call=>call.material==='#bae6fd:e').length,
    unsupportedModeError,
  }
};
