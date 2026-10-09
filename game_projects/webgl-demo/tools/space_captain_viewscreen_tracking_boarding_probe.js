// Adversarial long-duration encounter: physical observer does not steer the camera,
// target tracking does not change authority, boarding must not run away unattended.
const authorityFactory = globalThis.MainComputerBridgeEncounterRuntime;
const projection = globalThis.MainComputerBridgeViewscreenProjection;
const config = {tacticalSliceSeconds:3,physicsStepSeconds:0.2,physicalPlayerAuthority:true};
const origin = [1000,2000,3000];
const pose = {bodyId:'ship.mother',positionM:origin,velocityMps:[0,0,0],forwardWorld:[1,0,0],upWorld:[0,0,1],validThroughSeconds:901};
const times = [0,3,6,9,15,30,60,120,300,600,840,900];
const snap = [];
const a = authorityFactory.create(config);
a.advance(1000,{active:true});
const holdDecision=a.command({type:'captain-helm-order',order:{
  captainId:'captain.beta',shipId:'ship.beta',decisionId:'long-hold-001',revision:1,
  issuedAtSeconds:0,validThroughSeconds:901,maneuver:'hold',rangeM:2050,
  source:'deterministic-test-captain'
}},1000);
if(!holdDecision.accepted) throw new Error('TEST_CAPTAIN_HOLD_ORDER_REJECTED');
const initial = a.readAuthorityState().ships['ship.beta'];
for (const t of times) {
  a.advance(1000+t*1000,{active:true});
  const state=a.readAuthorityState();
  const enemy=a.predict(1000+t*1000)['ship.beta'];
  const own=a.predict(1000+t*1000)['ship.alpha'];
  const relativePositionM=[enemy.xM-own.xM,enemy.yM-own.yM];
  const relativeVelocityMps=[enemy.vxMps-own.vxMps,enemy.vyMps-own.vyMps];
  const targetWorld=origin.map((v,i)=>v+(i<2?relativePositionM[i]:0));
  const view=projection.project({authorityState:state,simulationSeconds:t,
    observerPose:pose,targetWorldPositionM:targetWorld}).snapshot.viewScreen;
  snap.push({t,rangeM:Math.hypot(...relativePositionM),
    velocityMps:Math.hypot(...relativeVelocityMps),
    accelerationMps2:Math.hypot(state.ships["ship.beta"].axMps2,state.ships["ship.beta"].ayMps2),
    viewMode:view.mode,targetVisible:view.targetVisible,
    targetOffsetNormalized:view.targetOffsetNormalized,
    cameraWorldPositionM:view.cameraWorldPositionM,
    targetWorldPositionM:view.targetWorldPositionM});
}
const at=t=>snap.find(s=>s.t===t);
const final=at(900);
const before=at(840);
const state=a.readAuthorityState();
const pass={
  unattendedBoardingRemainsInRange:snap.every(s=>s.rangeM>500&&s.rangeM<4000),
  boardingConvergesToStandoff:final.rangeM>1700&&final.rangeM<2500,
  velocityMatchingConverges:final.velocityMps<0.3&&before.velocityMps<0.3,
  accelerationAlwaysBounded:snap.filter(s=>s.t>=9).every(s=>s.accelerationMps2<=8.000001),
  noUnattendedCombatOrDestruction:!state.encounter?.hostile&&state.target?.hullPercent===100&&final.t===900,
  cameraRemainsAtMother:snap.every(s=>JSON.stringify(s.cameraWorldPositionM)===JSON.stringify(origin)),
  trackingNeverLosesEnemy:snap.every(s=>s.viewMode==='ship-mounted-track'&&s.targetVisible&&
    Math.hypot(...s.targetOffsetNormalized)<1e-6),
  enemyHasIndependentWorldPosition:snap.some(s=>s.t>0 &&
    Math.hypot(...s.targetWorldPositionM.map((v,i)=>v-(origin[i]+(i<2?initial[['xM','yM'][i]]:0))))>1),
};
const failedChecks=Object.entries(pass).filter(([,ok])=>!ok).map(([name])=>name);
return {ok:failedChecks.length===0,checks:pass,failedChecks,
  schema:'game.spaceCaptainTrackingBoardingLongSmoke.v1',metrics:{samples:snap}};
