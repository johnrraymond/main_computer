const authorityFactory = globalThis.MainComputerBridgeEncounterRuntime;
const projectionFactory = globalThis.MainComputerBridgeViewscreenEncounterRuntime;
const presentationApi = globalThis.MainComputerBridgeViewscreenPresentation;
const prototype = globalThis.__phase5BridgeMethods;
if (!authorityFactory || !projectionFactory || !presentationApi || !prototype) {
  return {ok:false,error:'required-authority-projection-presentation-or-scene-method-missing'};
}
const START_MS = 1000;
const NAVIGATION = {
  enabled:true,
  currentSystemId:'start',
  startSystemId:'start',
  lastCompletedRouteId:'',
  lastArrivalAtMs:0,
  travelling:false,
  elapsedWorldTime:0,
  currentPlanetId:'planet.start',
  currentPlanetLabel:'Opening World',
  currentPlanet:{id:'planet.start',label:'Opening World',radiusScale:1},
};
function createScene() {
  const context = Object.assign({},prototype);
  context.navigation = {...NAVIGATION};
  context.playerLocation = 'bay.shuttle';
  context.shipState = {
    location: 'bay.shuttle',
    objectiveId: 'objective.bridge-access',
    flags:{currentSystemPlanetSurveyed:false,planetScansCompleted:0},
    terminals: {
      'terminal.bridge-viewscreen':{state:'online'},
      'terminal.bridge-tactical':{state:'ready'},
    },
  };
  context.lastFrameTime=START_MS;
  context.bridgeEncounterRuntime=authorityFactory.create({tacticalSliceSeconds:3,physicsStepSeconds:0.2});
  context.bridgeViewscreenEncounterRuntime=projectionFactory.create({authority:context.bridgeEncounterRuntime});
  context.bridgeViewscreenSystem=presentationApi.createSystem({initialMode:'encounter',selectedAtSimulationSeconds:0,displayPowered:true});
  context.bridgeViewscreenProjectionFrame=null;
  context.bridgeViewscreenPresentationFrame=null;
  context.bridgeViewscreenPresentationInputsFrame=null;
  context.bridgeEncounterLastUiEventSequence=0;
  context.events=[];
  context.navigationSnapshot=function(){return this.navigation;};
  context.astrometricSnapshot=function(){return null;};
  context.emitShipState=function(){};
  context.setShipTerminalState=function(id,value){
    if (!this.shipState.terminals[id]) return false;
    this.shipState.terminals[id].state=value;
    return true;
  };
  context.setShipObjective=function(id){ this.shipState.objectiveId=id;this.events.push({kind:'objective',id});return true;};
  context.setShipInteractionStatus=function(value){this.shipState.lastInteractionStatus=value;};
  context.isShuttleBaySceneActive=function(){return true;};
  context.isShuttleBayPlayerControlActive=function(){return true;};
  context.shipLocationForPosition=function(){return this.playerLocation;};
  context.setShipLocation=function(location){this.shipState.location=location;return true;};
  context.camera=[0,1,-20];
  return context;
}
const checks={};
const one=createScene();
const control=createScene();
const methods=one.createShipInteractionHandlerMap();
checks.legacyTargetAcquisitionHandlerAbsent=!Object.prototype.hasOwnProperty.call(methods,'trackEnemyShipOnViewscreen');
checks.viewscreenConsoleIsPowerToggle=typeof methods.toggleBridgeViewscreenDisplayPower==='function';
checks.tacticalConsoleStillUsesAuthority=typeof methods.fireBridgeTacticalConsole==='function';
function advance(scene,t){scene.lastFrameTime=START_MS+t*1000;scene.updateBridgeViewscreenEncounter(scene.lastFrameTime);}
advance(one,0);
advance(control,0);
const initialFrame=one.bridgeViewscreenPresentationSnapshot();
checks.encounterAlreadySelectedOutsideBridge=one.shipState.location==='bay.shuttle' && initialFrame?.mode==='encounter' && initialFrame.target.lock.acquired;
const frameTimeBeforePower=initialFrame.time.simulationSeconds;
const powerActionOff=methods.toggleBridgeViewscreenDisplayPower(null,{});
const frameAfterOff=one.bridgeViewscreenPresentationSnapshot();
checks.powerToggleSucceedsEvenWhenSwitchingOff=powerActionOff===true && !one.bridgeViewscreenDisplayPowered() && one.shipState.terminals['terminal.bridge-viewscreen'].state==='off';
checks.offRebuildsSameFrameWithoutAdvancingTime=frameAfterOff?.display.powered===false && frameAfterOff.time.simulationSeconds===frameTimeBeforePower;
checks.displayOffDoesNotChangeSelectedMode=one.bridgeViewscreenSelectedMode()==='encounter';
checks.powerOffHintReflectsPhysicalControl=one.shipInteractionHint({id:'terminal.bridge-viewscreen'}).includes('turn on');
for (const t of [1,2,3,4,5.5]) {advance(one,t);advance(control,t);}
checks.offscreenEncounterAndCameraContinue=one.bridgeViewscreenPresentationSnapshot().mode==='encounter' && one.bridgeViewscreenPresentationSnapshot().display.powered===false && JSON.stringify(one.bridgeEncounterRuntime.readAuthorityState())===JSON.stringify(control.bridgeEncounterRuntime.readAuthorityState()) && JSON.stringify(one.bridgeViewscreenProjectionFrame.viewScreen.cameraCenterM)===JSON.stringify(control.bridgeViewscreenProjectionFrame.viewScreen.cameraCenterM);
const beforeEntry=JSON.stringify(one.bridgeViewscreenProjectionFrame.viewScreen.cameraCenterM);
one.playerLocation='bridge.deck';
one.syncShipLocationFromCamera();
checks.bridgeEntryImmediatelyShowsCombatObjective=one.shipState.objectiveId==='objective.enemy-attack';
checks.bridgeEntryDoesNotResetProjection=JSON.stringify(one.bridgeViewscreenProjectionFrame.viewScreen.cameraCenterM)===beforeEntry;
const powerActionOn=methods.toggleBridgeViewscreenDisplayPower(null,{});
checks.repowerRestoresCurrentPresentation=powerActionOn===true && one.bridgeViewscreenPresentationSnapshot().display.powered===true && one.bridgeViewscreenPresentationSnapshot().time.simulationSeconds>=5.5;
checks.powerOnHintReflectsPhysicalControl=one.shipInteractionHint({id:'terminal.bridge-viewscreen'}).includes('turn off');
advance(one,7.02);
advance(control,7.02);
const hullBefore=one.bridgeEncounterStatus(one.lastFrameTime).targetHullPercent;
const firstShot=methods.fireBridgeTacticalConsole(null,{});
const statusAfterFire=one.bridgeEncounterStatus(one.lastFrameTime);
checks.fireCommandAcceptedByRealAuthority=firstShot===true && statusAfterFire.shotsFired===1;
checks.fireDoesNotDirectlyDamageTarget=hullBefore===statusAfterFire.targetHullPercent && hullBefore===100;
checks.fireDoesNotChangeScreenPower=one.bridgeViewscreenDisplayPowered()===true;
const shot=one.bridgeEncounterRuntime.readAuthorityState().weapons.projectiles[0];
const impactSeconds=Number(shot.impactAtSeconds);
advance(one,impactSeconds+0.12);
const statusAfterImpact=one.bridgeEncounterStatus(one.lastFrameTime);
checks.firstDamageComesFromAuthorityImpact=statusAfterImpact.targetHullPercent===50 && one.shipState.objectiveId==='objective.enemy-attack';
const secondFireSeconds=impactSeconds+0.41;
advance(one,secondFireSeconds);
checks.secondAuthorityFireAccepted=methods.fireBridgeTacticalConsole(null,{})===true;
const secondShot=one.bridgeEncounterRuntime.readAuthorityState().weapons.projectiles.at(-1);
const secondImpactSeconds=Number(secondShot.impactAtSeconds);
advance(one,secondImpactSeconds+0.12);
checks.destructionUpdatesObjectiveFromImpact=one.shipState.objectiveId==='objective.enemy-disabled' && one.bridgeEncounterStatus(one.lastFrameTime).targetDestroyed===true;
advance(one,secondImpactSeconds+2.4);
one.syncShipLocationFromCamera();
checks.destroyedEnemyObjectivePersistsAfterExplosion=one.shipState.objectiveId==='objective.enemy-disabled';
checks.destroyedEnemyDoesNotTurnTacticalConsoleIntoPlanetScan=one.shipInteractionHint({id:'terminal.bridge-tactical'}).includes('destroyed') && methods.fireBridgeTacticalConsole(null,{})===true && !one.shipState.flags.currentSystemPlanetSurveyed;
const destination=createScene();
destination.navigation={...NAVIGATION,currentSystemId:'destination',lastCompletedRouteId:'route.1',lastArrivalAtMs:1,currentPlanetId:'planet.next',currentPlanetLabel:'Destination Planet',currentPlanet:{id:'planet.next',label:'Destination Planet',radiusScale:1}};
advance(destination,20);
destination.playerLocation='bridge.deck';
destination.syncShipLocationFromCamera();
checks.planetModeSkipsManualCentering=destination.bridgeViewscreenSelectedMode()==='planet' && destination.shipState.objectiveId==='objective.planet-scan';
// Dispatch is bound to the original scene through the handler map; call the actual scene method for the destination scenario.
const didScan=destination.fireBridgeTacticalConsole();
checks.planetScanWorksWithoutAcquisition=didScan===true && destination.shipState.flags.currentSystemPlanetSurveyed && destination.shipState.objectiveId==='objective.planet-surveyed';
checks.tacticalScanDoesNotToggleDisplay=destination.bridgeViewscreenDisplayPowered()===true;
checks.noGameplayObjectiveRequiresTargetCentering=one.events.every(event => !['objective.bridge-screen','objective.enemy-track','objective.planet-view'].includes(event.id)) && destination.events.every(event => !['objective.bridge-screen','objective.enemy-track','objective.planet-view'].includes(event.id));
const failedChecks=Object.entries(checks).filter(([,passed])=>!passed).map(([name])=>name);
return {
  ok:failedChecks.length===0,
  schema:'game.spaceCaptainPhase5Part1BridgeGameplayProbe.v1',
  checks,
  failedChecks,
  metrics:{
    displayOffSimulationSeconds:frameTimeBeforePower,
    returnToBridgeSeconds:5.5,
    firstImpactSeconds:impactSeconds,
    secondImpactSeconds,
    finalHullPercent:one.bridgeEncounterStatus(one.lastFrameTime).targetHullPercent,
    playerBridgeObjective:one.shipState.objectiveId,
    destinationMode:destination.bridgeViewscreenSelectedMode(),
    destinationObjective:destination.shipState.objectiveId,
  }
};
