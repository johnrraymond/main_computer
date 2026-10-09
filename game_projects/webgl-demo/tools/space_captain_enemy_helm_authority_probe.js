// Captain/helm authority contract smoke. Test-only; no production patches or mocks.
// Executed as an isolated Node Function after loading the real multirate contract
// and bridge encounter runtime. Results remain meaningful when the command API is absent.
const F = globalThis.MainComputerBridgeEncounterRuntime;
const contract = globalThis.MainComputerSpaceCaptainMultirateContract;
if (!F || !contract) return {ok:false,error:'PRODUCTION_RUNTIME_OR_CONTRACT_NOT_LOADED'};
const START = 1000;
const OPTIONS = {physicalPlayerAuthority:true,requirePhysicalWeaponRange:true,
  tacticalSliceSeconds:3,physicsStepSeconds:0.2};
const newRuntime = () => F.create(OPTIONS);
const now = sec => START+sec*1000;
const round = n => Number.isFinite(n) ? Math.round(n*1000000)/1000000 : null;
const clone = v => JSON.parse(JSON.stringify(v));
const has = (o,k) => Object.prototype.hasOwnProperty.call(o || {},k);
const norm = s => {
  const own=s?.ships?.['ship.alpha'], enemy=s?.ships?.['ship.beta'];
  return own&&enemy ? {
    rangeM:Math.hypot(enemy.xM-own.xM,enemy.yM-own.yM),
    relativePositionM:[enemy.xM-own.xM,enemy.yM-own.yM],
    relativeVelocityMps:[enemy.vxMps-own.vxMps,enemy.vyMps-own.vyMps],
    enemyAccelerationMps2:[enemy.axMps2,enemy.ayMps2],
    enemyPositionM:[enemy.xM,enemy.yM],
  } : null;
};
const sample = (r,t) => {
  r.advance(now(t),{active:true});
  return norm(r.snapshot(now(t)));
};
const errText = e => String(e?.stack||e);
const invoke = (r,order,t) => {
  try { return {return:r.command({type:'captain-helm-order',order:clone(order)},now(t)),error:null}; }
  catch(e) {return {return:null,error:errText(e)};}
};
const order = (decisionId,revision,maneuver,issuedAtSeconds,extras={}) => ({
  captainId:'captain.beta',shipId:'ship.beta',decisionId,revision,
  issuedAtSeconds,validThroughSeconds:issuedAtSeconds+240,
  maneuver,...extras
});
const close = (a,b,eps=1e-6) => Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=eps;
const vecNorm = v => Array.isArray(v) ? Math.hypot(...v) : Infinity;
const diff = (a,b) => a&&b ? Math.hypot(...a.map((v,i)=>v-b[i])) : Infinity;

// 1. With no captain decision, a mission label is not an order to accelerate.
const passive=newRuntime();
const passive0=sample(passive,0);
const passive9=sample(passive,9);
const passive90=sample(passive,90);
const passiveAuthority=passive.readAuthorityState();

// 2. Same initial truth, divergent captain choices (not hard-coded phase decisions).
const baseline=newRuntime();
const baseline0=sample(baseline,0);
const approach=newRuntime(); sample(approach,0);
const hold=newRuntime(); sample(hold,0);
const withdraw=newRuntime(); sample(withdraw,0);
const coast=newRuntime(); sample(coast,0);
const acceptApproach=invoke(approach,order('beta-approach-001',1,'approach',0),0);
const acceptHold=invoke(hold,order('beta-hold-001',1,'hold',0,{rangeM:2050}),0);
const acceptWithdraw=invoke(withdraw,order('beta-withdraw-001',1,'withdraw',0),0);
const acceptCoast=invoke(coast,order('beta-coast-001',1,'coast',0),0);
const a30=sample(approach,30), h30=sample(hold,30), w30=sample(withdraw,30), c30=sample(coast,30);
const h180=sample(hold,180);
const ah=hold.readAuthorityState();

// 3. Captain can replace the mission while it is underway. The helm cannot
// retain the previous mission after a valid change of orders.
const switched=newRuntime(); sample(switched,0);
const beforeOrder=invoke(switched,order('beta-approach-002',1,'approach',0),0);
const switch30=sample(switched,30);
const changeOrder=invoke(switched,order('beta-withdraw-002',2,'withdraw',30),30);
const switch60=sample(switched,60);
const cancelOrder=invoke(switched,order('beta-coast-002',3,'coast',60),60);
const switch90=sample(switched,90);

// 4. Only the target vessel's captain has authority; stale, duplicate, invalid
// and expired decisions cannot reprogram the helm.
const guarded=newRuntime(); sample(guarded,0);
const good=invoke(guarded,order('beta-valid-001',4,'hold',0,{rangeM:2050}),0);
const guardBefore=clone(guarded.readAuthorityState());
const foreign=invoke(guarded,{...order('alpha-spoof-001',5,'withdraw',0),captainId:'captain.alpha'},0);
const stale=invoke(guarded,order('beta-stale-001',3,'withdraw',0),0);
const duplicate=invoke(guarded,order('beta-valid-001',4,'withdraw',0),0);
const invalid=invoke(guarded,order('beta-bad-range-001',5,'hold',0,{rangeM:-50}),0);
const guardAfter=clone(guarded.readAuthorityState());
const expiring=newRuntime(); sample(expiring,0);
const expOrder=order('beta-expires-001',1,'hold',0,{rangeM:2050});
expOrder.validThroughSeconds=6;
const expiryAccepted=invoke(expiring,expOrder,0);
const exp9=sample(expiring,9);

// 5. Retained captain control during a combat phase change. Player weapon fire
// must not secretly replace enemy captain's helm order with a phase script.
const combat=newRuntime(); sample(combat,0);
const combatOrder=invoke(combat,order('beta-combat-001',1,'coast',0),0);
const combatFire=combat.command({type:'fire-primary-weapon',physicalShot:{
  originWorldPositionM:[1000,2000,3000],targetWorldPositionM:[3600,2598,3000]}},now(7));
const combat12=sample(combat,12);

// 6. The authored game must pass captain decisions into the live bridge.
// A static presence check is diagnostic only: production acceptance must pair
// it with the executable command-causality checks above and a browser smoke.
const fs=require('fs'),path=require('path');
const gameRoot=path.resolve(process.argv[2]||process.cwd());
const scene=fs.readFileSync(path.join(gameRoot,'web','scripts','scene-viewer.js'),'utf8');
const sceneBridgeDeclared=scene.includes('submitEnemyCaptainOrder(');
// Exercise the real scene adapter method (if implemented) on a minimal context,
// rather than trusting its name or a source regex as proof of integration.
let sceneOrderForwarded=false;
let sceneInvocationError=null;
if(sceneBridgeDeclared) {
  try {
    const pattern=/\bsubmitEnemyCaptainOrder\s*\([^)]*\)\s*\{/g;
    const match=pattern.exec(scene);
    if(!match) throw new Error('scene command method body missing');
    const open=scene.indexOf('{',match.index);
    let depth=0,finish=-1;
    for(let i=open;i<scene.length;i++) {
      if(scene[i]==='{') depth++;
      if(scene[i]==='}' && --depth===0) {finish=i+1; break;}
    }
    if(finish<0) throw new Error('scene command method is malformed');
    const method=scene.slice(match.index,finish);
    const adapter=new Function('return ({'+method+'})')();
    const sceneRuntime=newRuntime();
    sample(sceneRuntime,0);
    const candidate=order('beta-from-scene-001',1,'withdraw',0);
    const result=adapter.submitEnemyCaptainOrder.call({bridgeEncounterRuntime:sceneRuntime},candidate,now(0));
    sceneOrderForwarded=result?.accepted===true &&
      sceneRuntime.readAuthorityState()?.helm?.['ship.beta']?.activeOrder?.decisionId==='beta-from-scene-001';
  } catch(e) {sceneInvocationError=errText(e);}
}

const ordersAccepted=[acceptApproach,acceptHold,acceptWithdraw,acceptCoast].every(x=>x.return?.accepted===true && !x.error);
const checks={
  productionRuntimeLoaded:typeof F.create==='function'&&contract.SCHEMA==='game.spaceCaptainMultirateContract.v1',
  playerPhysicalAuthorityStillEnabled:baseline.physicalPlayerAuthority===true&&baseline.requirePhysicalWeaponRange===true,
  noOrderMeansNoAutonomousThrust:vecNorm(passive0?.enemyAccelerationMps2)<1e-9 && vecNorm(passive9?.enemyAccelerationMps2)<1e-9 && vecNorm(passive90?.enemyAccelerationMps2)<1e-9,
  missionPhaseCannotInventCaptainOrder:passiveAuthority?.helm?.['ship.beta']?.activeOrder == null && vecNorm(passive9?.enemyAccelerationMps2)<1e-9,
  captainCommandsAccepted:ordersAccepted,
  approachOrderClosesRange:acceptApproach.return?.accepted===true && a30?.rangeM < c30?.rangeM-100,
  withdrawOrderOpensRange:acceptWithdraw.return?.accepted===true && w30?.rangeM > c30?.rangeM+100,
  holdRangeChosenByCaptain:acceptHold.return?.accepted===true && h180?.rangeM>1750 && h180?.rangeM<2400 && h180?.relativeVelocityMps&&vecNorm(h180.relativeVelocityMps)<1,
  helmDoesNotChooseItsOwnStandoff:ah?.helm?.['ship.beta']?.activeOrder?.rangeM===2050 && ah?.helm?.['ship.beta']?.activeOrder?.decisionId==='beta-hold-001',
  sameInitialStateDifferentOrdersDifferentOutcome:diff(a30?.enemyPositionM,w30?.enemyPositionM)>250,
  orderReversalChangesActualMotion:beforeOrder.return?.accepted===true && changeOrder.return?.accepted===true && switch60?.rangeM>switch30?.rangeM,
  cancelOrderStopsCommandedThrust:cancelOrder.return?.accepted===true && vecNorm(switch90?.enemyAccelerationMps2)<1e-9,
  captainDecisionHasAuditableProvenance:ah?.helm?.['ship.beta']?.activeOrder?.captainId==='captain.beta' && ah?.recentEvents?.some(e=>e.decisionId==='beta-hold-001'),
  rejectOtherCaptainForEnemyShip:good.return?.accepted===true&&foreign.return?.accepted===false,
  rejectStaleDecision:good.return?.accepted===true&&stale.return?.accepted===false,
  rejectDuplicateDecisionId:good.return?.accepted===true&&duplicate.return?.accepted===false,
  rejectInvalidRange:good.return?.accepted===true&&invalid.return?.accepted===false,
  rejectedOrdersCannotMutateAuthority:good.return?.accepted===true&&JSON.stringify(guardBefore)===JSON.stringify(guardAfter),
  expiredOrderCannotContinueThrust:expiryAccepted.return?.accepted===true&&vecNorm(exp9?.enemyAccelerationMps2)<1e-9,
  playerFireDoesNotAutoChooseEnemyHelm:combatOrder.return?.accepted===true && combatFire?.accepted===true && vecNorm(combat12?.enemyAccelerationMps2)<1e-9,
  shipMotherNotThrustByEnemyCaptain:close(ah?.ships?.['ship.alpha']?.axMps2,0)&&close(ah?.ships?.['ship.alpha']?.ayMps2,0),
  liveSceneHasCaptainOrderIngress:sceneBridgeDeclared,
  liveSceneActuallyForwardsCaptainOrder:sceneOrderForwarded,
};
const failedChecks=Object.entries(checks).filter(([,yes])=>yes!==true).map(([name])=>name);
return {schema:'game.spaceCaptainEnemyHelmAuthoritySmoke.v1',ok:failedChecks.length===0,
  checks,failedChecks,
  errors:[acceptApproach,acceptHold,acceptWithdraw,acceptCoast,beforeOrder,changeOrder,cancelOrder,good,foreign,stale,duplicate,invalid,expiryAccepted,combatOrder]
    .filter(x=>x.error).map(x=>x.error),
  metrics:{
    noOrder:{at0:passive0,at9:passive9,at90:passive90},
    originalRangeM:round(baseline0?.rangeM),
    at30:{approach:a30,hold:h30,withdraw:w30,coast:c30},
    holdAt180:h180,
    orderResponses:{approach:acceptApproach.return,hold:acceptHold.return,withdraw:acceptWithdraw.return,
      coast:acceptCoast.return,change:changeOrder.return,cancel:cancelOrder.return,
      unauthorized:foreign.return,stale:stale.return,duplicate:duplicate.return,
      invalid:invalid.return,expiration:expiryAccepted.return},
    switch:{t30:switch30,t60:switch60,t90:switch90},
    expiryAt9:exp9,combatAt12:combat12,
    sceneBridgeDeclared,sceneOrderForwarded,sceneInvocationError,
  }};
