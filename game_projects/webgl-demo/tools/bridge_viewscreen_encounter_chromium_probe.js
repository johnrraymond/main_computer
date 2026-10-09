const runtime = globalThis.MainComputerBridgeViewscreenEncounterRuntime?.create?.();
if (!runtime) return {ok:false, error:'bridge-runtime-missing'};

const canvas = document.createElement('canvas');
canvas.width = 960;
canvas.height = 540;
canvas.style.background = '#020617';
document.body.appendChild(canvas);
const ctx = canvas.getContext('2d');

const START_MS = 1000;
const FIRE_AT_SECONDS = 11.7;
const END_SECONDS = 20.2;
const SIMULATION_SCALE = 4;
let fired = false;
let frames = 0;
let priorWall = null;
let frameIntervals = [];
let hardLockFrames = 0;
let bothVisibleFrames = 0;
let maxTargetOffset = 0;
let minTargetOffset = Infinity;
let combatObserved = false;
let impactObserved = false;
let lastSnapshot = null;
const phases = new Set();

function draw(snapshot) {
  const view = snapshot.viewScreen;
  ctx.fillStyle = '#020617';
  ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.strokeStyle = '#155e75';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(canvas.width/2,0); ctx.lineTo(canvas.width/2,canvas.height); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0,canvas.height/2); ctx.lineTo(canvas.width,canvas.height/2); ctx.stroke();
  const drawShip = (offset, radius, color) => {
    const x = canvas.width/2 + Number(offset[0]) * canvas.width/2;
    const y = canvas.height/2 - Number(offset[1]) * canvas.height/2;
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(x,y,radius,0,Math.PI*2); ctx.fill();
  };
  drawShip(view.playerOffsetNormalized,5,'#bae6fd');
  drawShip(view.targetOffsetNormalized,8,snapshot.phase === 'combat' ? '#ef4444' : '#f59e0b');
}

let simulationStartWallMs = null;
let firstRuntimeSimulationSeconds = null;
await new Promise((resolve) => {
  const tick = (wallNow) => {
    if (simulationStartWallMs === null) simulationStartWallMs = wallNow;
    if (priorWall !== null) frameIntervals.push(wallNow-priorWall);
    priorWall = wallNow;
    const simMs = START_MS + (wallNow-simulationStartWallMs) * SIMULATION_SCALE;
    const simSeconds = (simMs-START_MS)/1000;
    if (!fired && simSeconds >= FIRE_AT_SECONDS) {
      runtime.playerFire(simMs);
      fired = true;
    }
    runtime.advance(simMs,{active:true});
    const snapshot = runtime.snapshot(simMs,{active:true});
    if (firstRuntimeSimulationSeconds === null) firstRuntimeSimulationSeconds = Number(snapshot?.simulationSeconds || 0);
    lastSnapshot = snapshot;
    phases.add(snapshot.phase);
    const offset = Math.hypot(...snapshot.viewScreen.targetOffsetNormalized.map(Number));
    maxTargetOffset = Math.max(maxTargetOffset,offset);
    minTargetOffset = Math.min(minTargetOffset,offset);
    if (snapshot.viewScreen.hardLockRetained) hardLockFrames++;
    if (snapshot.viewScreen.bothShipsVisible) bothVisibleFrames++;
    if (snapshot.phase === 'combat') combatObserved = true;
    if (snapshot.authority?.eventAnchorCount >= 1 && snapshot.impactAtSeconds !== null && snapshot.simulationSeconds >= snapshot.impactAtSeconds) impactObserved = true;
    draw(snapshot);
    frames++;
    if (simSeconds >= END_SECONDS) resolve();
    else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const meanFrameMs = frameIntervals.reduce((a,b)=>a+b,0)/Math.max(1,frameIntervals.length);
const measuredRafHz = meanFrameMs > 0 ? 1000/meanFrameMs : 0;
const runtimeSimulationSeconds = Number(lastSnapshot?.simulationSeconds || 0);
const authorityUpdateCount = Number(lastSnapshot?.authority?.updateCount || 0);
const authorityUpdatesPerRuntimeSecond = runtimeSimulationSeconds > 0 ? authorityUpdateCount/runtimeSimulationSeconds : 0;
const checks = {
  browserUsesRequestAnimationFrame: frames > 20 && measuredRafHz > 20,
  runtimeStartsOnFirstAnimationFrame: Math.abs(Number(firstRuntimeSimulationSeconds || 0)) <= 0.001,
  runtimeCoversFullScriptedEncounter: runtimeSimulationSeconds >= END_SECONDS - 0.15,
  runtimeUsesTenHzAuthority: Number(lastSnapshot?.physicsStepSeconds) === 0.1 && authorityUpdatesPerRuntimeSecond >= 9.0 && authorityUpdatesPerRuntimeSecond <= 10.5,
  tacticalSliceIsFiveSeconds: Number(lastSnapshot?.tacticalSliceSeconds) === 5,
  playerFireCreatesHostility: fired && lastSnapshot?.playerFireAtSeconds !== null && lastSnapshot?.hostileAwareness === 'active-hostile-player',
  playerFireOccursAtScriptedEncounterTime: Math.abs(Number(lastSnapshot?.playerFireAtSeconds) - FIRE_AT_SECONDS) <= 0.15,
  boardingPhasesRunBeforeHostility: phases.has('boarding-approach') && phases.has('boarding-velocity-match') && phases.has('boarding-prep'),
  impactBecomesImmediateAuthorityEvent: impactObserved && Number(lastSnapshot?.impactAtSeconds) > Number(lastSnapshot?.playerFireAtSeconds),
  combatBeginsOnFixedBoundary: combatObserved && Number(lastSnapshot?.combatBoundarySeconds) % 5 === 0,
  softLockIsNotHardCentered: maxTargetOffset > 0.03 && minTargetOffset > 0.001,
  softLockRetainsEnemy: hardLockFrames / Math.max(1,frames) >= 0.99,
  zoomedOutViewKeepsBothShips: bothVisibleFrames / Math.max(1,frames) >= 0.99,
};
const failedChecks = Object.entries(checks).filter(([,v])=>!v).map(([k])=>k);
return {
  ok: failedChecks.length === 0,
  schema:'game.bridgeViewscreenEncounterChromiumProbe.v1',
  checks,
  failedChecks,
  metrics:{
    frames,
    measuredRafHz,
    runtimeSimulationSeconds,
    firstRuntimeSimulationSeconds,
    authorityUpdateCount,
    authorityUpdatesPerRuntimeSecond,
    eventAnchorCount:lastSnapshot?.authority?.eventAnchorCount || 0,
    maxTargetOffsetNormalized:maxTargetOffset,
    minTargetOffsetNormalized:minTargetOffset,
    hardLockFraction:hardLockFrames/Math.max(1,frames),
    bothVisibleFraction:bothVisibleFrames/Math.max(1,frames),
    playerFireAtSeconds:lastSnapshot?.playerFireAtSeconds,
    impactAtSeconds:lastSnapshot?.impactAtSeconds,
    combatBoundarySeconds:lastSnapshot?.combatBoundarySeconds,
    phases:[...phases],
  },
};
