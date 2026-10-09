const presentation = globalThis.MainComputerBridgeViewscreenPresentation;
const renderer = globalThis.MainComputerBridgeViewscreenRenderer;
if (!presentation) return {ok:false,error:'bridge-viewscreen-presentation-missing'};
if (!renderer) return {ok:false,error:'bridge-viewscreen-renderer-missing'};

const SURFACE={position:[0,-39.12],size:[6.9,2.1,0.08],color:'#38bdf8'};
const stable=(value)=>JSON.stringify(value);
const finitePoint=(point)=>point && Number.isFinite(Number(point.xNormalized)) && Number.isFinite(Number(point.yNormalized));

function createBuilder(){
  const calls=[];
  return {
    calls,
    color(hex,emissive=false){return `${String(hex)}${emissive?':e':''}`;},
    box(min,max,material){calls.push({kind:'box',min:min.slice(),max:max.slice(),material});},
    beam(from,to,width,material){calls.push({kind:'beam',from:from.slice(),to:to.slice(),width:Number(width),material});},
    ellipsoid(center,radii,segments,rings,material){calls.push({kind:'ellipsoid',center:center.slice(),radii:radii.slice(),segments:Number(segments),rings:Number(rings),material});},
  };
}
function render(frame){
  const builder=createBuilder();
  const before=stable(frame);
  const result=renderer.render({builder,surface:SURFACE,presentation:frame});
  return {builder,result,before,after:stable(frame),fingerprint:stable(builder.calls)};
}

const haven={
  id:'planet.haven',label:'Haven',classification:'terrestrial',
  atmosphereColor:'#67e8f9',surfaceColor:'#2563eb',secondaryColor:'#16a34a',cloudColor:'#f8fafc',
  radiusScale:1.08,moonCount:2,rings:{enabled:true,color:'#94a3b8',innerRadius:1.3,outerRadius:1.72,tiltDegrees:18}
};
const ember={
  id:'planet.ember',label:'Ember',classification:'rocky',
  atmosphereColor:'#fbbf24',surfaceColor:'#b45309',secondaryColor:'#78350f',cloudColor:'#fde68a',radiusScale:0.86,moonCount:1,rings:{enabled:false}
};
const planetNav={
  enabled:true,travelling:false,currentSystemId:'system.pax',currentSystemLabel:'Pax',
  currentPlanetId:haven.id,currentPlanetLabel:haven.label,currentPlanetClassification:haven.classification,currentPlanet:haven,
  destinationSystemId:null,destinationPlanet:null,travelPhase:'available',travelProgress:0
};
const warpNav={
  ...planetNav,travelling:true,travelPhase:'in-warp',travelProgress:0.47,
  destinationSystemId:'system.vela-gate',destinationSystemLabel:'Vela Gate',destinationPlanet:ember
};
const astro={
  observer:{bodyId:'ship.mother',positionM:[1000,2000,3000]},
  targetObject:{id:haven.id,kind:'planet',direction:[0,0,-1],angularRadiusRad:0.1,visual:haven,local:true},
  visibleObjects:[
    {id:haven.id,kind:'planet',direction:[0,0,-1],angularRadiusRad:0.1,visual:haven,local:true},
    {id:'star.pax',kind:'star',direction:[0.035,0.02,-0.9992],angularRadiusRad:0.002,visual:{color:'#fff4d6'},local:true},
    {id:'star.remote',kind:'star',direction:[-0.06,0.04,-0.9974],angularRadiusRad:0.0001,visual:{color:'#bae6fd'},local:false},
    {id:'moon.haven.1',kind:'moon',direction:[0.045,-0.025,-0.9987],angularRadiusRad:0.006,visual:{},local:true},
  ]
};
const idleNav={enabled:false,travelling:false,currentPlanet:null};

const selectionCases={
  encounter:presentation.selectModeForSources({encounterActive:true,navigationSnapshot:warpNav,astrometricSnapshot:astro}),
  warp:presentation.selectModeForSources({encounterActive:false,navigationSnapshot:warpNav,astrometricSnapshot:astro}),
  astrometric:presentation.selectModeForSources({encounterActive:false,navigationSnapshot:planetNav,astrometricSnapshot:astro}),
  planet:presentation.selectModeForSources({encounterActive:false,navigationSnapshot:planetNav,astrometricSnapshot:null}),
  idle:presentation.selectModeForSources({encounterActive:false,navigationSnapshot:idleNav,astrometricSnapshot:null}),
};

const system=presentation.createSystem({initialMode:'encounter',selectedAtSimulationSeconds:0,displayPowered:true});
function presentMode(mode, inputs, seconds){
  system.select(mode,seconds);
  return system.present({simulationSeconds:seconds,...inputs});
}

const shipObserverPose={bodyId:'ship.mother',positionM:[1000,2000,3000],forwardWorld:[1,0,0],upWorld:[0,0,1]};
const planetFrame=presentMode('planet',{navigationSnapshot:planetNav,observerPose:shipObserverPose,tracked:true},12.5);
const planetRender=render(planetFrame);
const planetRepeat=render(planetFrame);
const warpFrame=presentMode('warp-transit',{navigationSnapshot:warpNav},20.25);
const warpRender=render(warpFrame);
const astroFrame=presentMode('astrometric',{navigationSnapshot:planetNav,astrometricSnapshot:astro,observerPose:shipObserverPose,tracked:true},30.75);
const astroRender=render(astroFrame);
const idleFrame=presentMode('idle',{navigationSnapshot:idleNav,idleReason:'standby'},40.5);
const idleRender=render(idleFrame);

system.setDisplayPowered(false);
const poweredOffFrame=system.lastPresentation;
const poweredOffRender=render(poweredOffFrame);

let unsupportedError='';
try {
  const bad=JSON.parse(stable(idleFrame));
  bad.mode='legacy-fallback';
  renderer.render({builder:createBuilder(),surface:SURFACE,presentation:bad});
} catch(error) { unsupportedError=String(error && error.message || error); }

const planetEllipsoids=planetRender.builder.calls.filter(call=>call.kind==='ellipsoid').length;
const warpBeams=warpRender.builder.calls.filter(call=>call.kind==='beam').length;
const astroEllipsoids=astroRender.builder.calls.filter(call=>call.kind==='ellipsoid').length;
const idleCalls=idleRender.builder.calls.length;
const astroObjects=[...(astroFrame.catalog?.stars||[]),...(astroFrame.catalog?.localBodies||[])];

const checks={
  selectorPrioritizesEncounter:selectionCases.encounter==='encounter',
  selectorPrioritizesWarpAfterEncounter:selectionCases.warp==='warp-transit',
  selectorUsesAstrometricWhenObserved:selectionCases.astrometric==='astrometric',
  selectorUsesPlanetWithoutAstrometricTarget:selectionCases.planet==='planet',
  selectorUsesIdleWhenNoFeedExists:selectionCases.idle==='idle',
  planetPresentationIsFrozen:Object.isFrozen(planetFrame)&&Object.isFrozen(planetFrame.planet)&&Object.isFrozen(planetFrame.stars),
  planetPresentationCarriesVisualContract:planetFrame.mode==='planet'&&planetFrame.planet.id===haven.id&&planetFrame.stars.length===18&&planetFrame.tracking.active===true,
  planetPresentationUsesPhysicalObserver:planetFrame.observer?.bodyId==='ship.mother' &&
    Array.isArray(planetFrame.observer?.positionM) && planetFrame.observer.positionM.length===3 &&
    Math.hypot(...planetFrame.observer.positionM.map((x,i)=>x-shipObserverPose.positionM[i]))<=1e-6,
  planetRendererDrawsPortedSemantics:planetRender.result.mode==='planet'&&planetEllipsoids>=7&&planetRender.builder.calls.length>20,
  planetRenderingIsDeterministic:planetRender.fingerprint===planetRepeat.fingerprint,
  warpPresentationCarriesTransitContract:warpFrame.mode==='warp-transit'&&Math.abs(warpFrame.warp.progress-0.47)<1e-12&&warpFrame.warp.destinationPlanet.id===ember.id,
  warpRendererDrawsTransitSemantics:warpRender.result.mode==='warp-transit'&&warpBeams>=35&&warpRender.builder.calls.length>40,
  astrometricPresentationProjectsObservedCatalog:astroFrame.mode==='astrometric'&&astroFrame.catalog.stars.length===2&&astroFrame.catalog.localBodies.length===1&&astroObjects.every(entry=>finitePoint(entry.screen)),
  astrometricObserverIsPhysicalMotherShip:astroFrame.telemetry?.observerBodyId==='ship.mother'
    && Array.isArray(astroFrame.observer?.positionM)
    && astroFrame.observer.positionM.length===3
    && astroFrame.observer.positionM.every(Number.isFinite)
    && Math.hypot(...astroFrame.observer.positionM.map((x,i)=>x-[1000,2000,3000][i]))<=1e-6,
  astrometricRendererDrawsObservedObjects:astroRender.result.mode==='astrometric'&&astroEllipsoids>=7,
  idlePresentationIsFirstClassMode:idleFrame.mode==='idle'&&idleFrame.idle.reason==='standby',
  idleRendererDrawsStandbyGeometry:idleRender.result.mode==='idle'&&idleCalls>=8,
  rendererLeavesEveryPresentationUnchanged:planetRender.before===planetRender.after&&warpRender.before===warpRender.after&&astroRender.before===astroRender.after&&idleRender.before===idleRender.after,
  displayPowerRemainsOutputOnly:poweredOffFrame.mode==='idle'&&poweredOffFrame.display.powered===false&&poweredOffRender.result.powered===false&&poweredOffRender.builder.calls.length===1,
  unknownModeFailsWithoutFallback:unsupportedError.includes('BRIDGE_VIEWSCREEN_RENDERER_MODE_NOT_IMPLEMENTED'),
  rendererCarriesNoMutationApi:typeof renderer.advance==='undefined'&&typeof renderer.command==='undefined'&&typeof renderer.playerFire==='undefined',
};
const failedChecks=Object.entries(checks).filter(([,passed])=>!passed).map(([name])=>name);
return {
  ok:failedChecks.length===0,
  schema:'game.spaceCaptainPhase4Part3NonEncounterModesProbe.v1',
  checks,failedChecks,
  metrics:{
    selectionCases,
    planetPrimitiveCount:planetRender.builder.calls.length,
    planetObserver:planetFrame.observer||null,
    planetEllipsoidCount:planetEllipsoids,
    warpPrimitiveCount:warpRender.builder.calls.length,
    warpBeamCount:warpBeams,
    astrometricPrimitiveCount:astroRender.builder.calls.length,
    astrometricEllipsoidCount:astroEllipsoids,
    astrometricCatalogObjectCount:astroObjects.length,
    astrometricObserver:astroFrame.observer||null,
    idlePrimitiveCount:idleCalls,
    poweredOffPrimitiveCount:poweredOffRender.builder.calls.length,
    unsupportedModeError:unsupportedError,
  }
};
