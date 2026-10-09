// Production adapter + real authored gravity and combat checks, no graphics mock.
const fs=require('fs');
const path=require('path');
const files=JSON.parse(process.argv[1]);
const gameRoot=path.resolve(path.dirname(files[0]),'..','..');
const source=fs.readFileSync(path.join(gameRoot,'web/scripts/scene-viewer.js'),'utf8');
const begin=source.indexOf('        mainShipObserverPose() {');
const end=source.indexOf('        updateBridgeViewscreenEncounter(nowMs =',begin);
if(begin<0 || end<begin) return {ok:false,error:'production-adapter-missing'};
const Adapter=new Function('return class RealBridgeAdapter {\n'+source.slice(begin,end)+'\n}')();
const project=JSON.parse(fs.readFileSync(path.join(gameRoot,'project.json'),'utf8'));
const gravity=globalThis.MainComputerSpaceGravityRuntime.create(project.metadata.spacePhysics,{projectId:project.id});
const systemId=project.metadata.spacePhysics.systems[0].id;
gravity.setActiveSystem(systemId);
const authority=globalThis.MainComputerBridgeEncounterRuntime.create({physicalPlayerAuthority:true,requirePhysicalWeaponRange:true});
authority.advance(1000,{active:true});
const context={spaceGravityRuntime:gravity,spaceGravitySnapshot:()=>gravity.snapshot(),bridgeViewscreenWorldAnchor:null,bridgeEncounterRuntime:authority};
const pose0=Adapter.prototype.mainShipObserverPose.call(context);
const state0=authority.readAuthorityState();
const target0=Adapter.prototype.bridgeEncounterTargetWorldPosition.call(context,pose0,state0,1000);
const reference0=gravity.body('ship.beta.encounter-reference');
const vec=(a,b)=>a&&b?a.map((x,i)=>x-b[i]):null;
const dist=(a,b)=>a&&b&&a.length===3&&b.length===3?Math.hypot(...vec(a,b)):Infinity;
const delta=[125,-76,43];
const movedPose={...pose0,positionM:pose0.positionM.map((v,i)=>v+delta[i])};
const targetAfterObserverMove=Adapter.prototype.bridgeEncounterTargetWorldPosition.call(context,movedPose,state0,1000);
const shiftedAuthority=JSON.parse(JSON.stringify(state0));
shiftedAuthority.ships['ship.beta'].xM+=47;
shiftedAuthority.ships['ship.beta'].yM-=22;
const targetWithThrust=Adapter.prototype.bridgeEncounterTargetWorldPosition.call(context,movedPose,shiftedAuthority);
// Real orbital propagation: reference and mother have identical starting
// velocity but remain separate bodies, and advance on the same fixed grid.
gravity.advanceToSimulationSeconds(480,systemId);
const pose480=Adapter.prototype.mainShipObserverPose.call(context);
const target480=Adapter.prototype.bridgeEncounterTargetWorldPosition.call(context,pose480,state0,1000);
const reference480=gravity.body('ship.beta.encounter-reference');
const body480=gravity.body('ship.mother');
const noRange=authority.command({type:'fire-primary-weapon'},1000);
const firingPose=pose480;
const firingTarget=target480;
const fire=authority.command({type:'fire-primary-weapon',physicalShot:{originWorldPositionM:firingPose.positionM,targetWorldPositionM:firingTarget}},1000);
const expectedRange=dist(firingPose.positionM,firingTarget);
const shot=fire.shot;
// Repeat the entire unattended interval through the real gravity adapter,
// not just the isolated tactical boarding controller.
gravity.advanceToSimulationSeconds(900,systemId);
const pose900=Adapter.prototype.mainShipObserverPose.call(context);
const unattended=globalThis.MainComputerBridgeEncounterRuntime.create({physicalPlayerAuthority:true,requirePhysicalWeaponRange:true});
unattended.advance(1000,{active:true});
const holdResult=unattended.command({type:'captain-helm-order',order:{
  captainId:'captain.beta',shipId:'ship.beta',decisionId:'gravity-hold-001',revision:1,
  issuedAtSeconds:0,validThroughSeconds:901,maneuver:'hold',rangeM:2050,
  source:'deterministic-test-captain'
}},1000);
if(!holdResult.accepted) throw new Error('GRAVITY_HOLD_ORDER_REJECTED');
unattended.advance(901000,{active:true});
const boardingState900=unattended.readAuthorityState();
const boardingContext={...context,bridgeEncounterRuntime:unattended};
const target900=Adapter.prototype.bridgeEncounterTargetWorldPosition.call(boardingContext,pose900,boardingState900,901000);
const physicalBoardingRange900=dist(target900,pose900?.positionM);
const errors=[];
const checks={
  actualAuthoredGravityBodyPresent:Boolean(pose0 && gravity.snapshot().simulated),
  sceneObserverReadsPhysicsBody:pose0?.bodyId==='ship.mother',
  enemyHasIndependentGravityBody:reference0?.id==='ship.beta.encounter-reference' && reference0.massKg===0,
  enemyInheritsOrbitalVelocity:dist(reference0?.velocityMps,pose0?.velocityMps)<1e-9,
  noCameraDrivenEnemyTeleport:dist(targetAfterObserverMove,target0)<1e-6,
  enemyWorldPositionTracksTacticalAuthority:dist(vec(targetWithThrust,target0),[47,-22,0])<1e-6,
  sharedGravityAdvancesEnemyIndependently:dist(reference480?.positionM,body480?.positionM)<0.01 && dist(reference480?.positionM,reference0?.positionM)>1e6,
  targetRemainsInCombatRangeAfterOrbitalTravel:dist(target480,pose480?.positionM)<4000,
  sceneCameraReadsMovedPhysicalShip:dist(pose480?.positionM,body480?.positionM)<1e-9,
  orbitalTranslationNotFrozen:dist(vec(target480,target0),vec(pose480?.positionM,pose0?.positionM))<0.01,
  noPhysicalRangeMeansNoLiveWeapon:noRange.accepted===false && noRange.reason==='physical-shot-state-required',
  weaponUsesMeasuredWorldRange:fire.accepted===true && Math.abs(shot?.rangeAtFireM-expectedRange)<1e-6,
  weaponFlightUsesPhysicalSeparation:fire.accepted===true && Math.abs((shot.impactAtSeconds-shot.firedAtSeconds)-expectedRange/authority.config.projectileSpeedMps)<1e-9,
  projectileRemembersWorldLaunch:dist(shot?.originWorldPositionM,pose480?.positionM)<1e-9,
  playerTacticalEngineDoesNotInventPhysicalThrust:authority.physicalPlayerAuthority && authority.readAuthorityState().ships['ship.alpha'].axMps2===0,
  realGravityAndBoardingHoldFor900Seconds:physicalBoardingRange900>1700 && physicalBoardingRange900<2500,
  worldRangeMatchesUnattendedTacticalState:Math.abs(physicalBoardingRange900-
    Math.hypot(boardingState900.ships['ship.beta'].xM,boardingState900.ships['ship.beta'].yM))<0.01,
};
const failedChecks=Object.entries(checks).filter(([,pass])=>!pass).map(([name])=>name);
return {ok:!failedChecks.length, schema:'game.spaceCaptainEncounterWorldMotionProbe.v2',checks,failedChecks,
  metrics:{systemId,pose0:pose0.positionM,reference0:reference0.positionM,pose480:pose480.positionM,
    reference480:reference480.positionM, target0,target480,physicalRangeM:expectedRange,
    tacticalRangeM:Math.hypot(state0.ships['ship.beta'].xM,state0.ships['ship.beta'].yM),
    shot:shot||null,pose900:pose900?.positionM,target900,physicalBoardingRange900}};
