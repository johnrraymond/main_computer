const payload = args || {};
const fixture = payload.fixture || payload;
const config = payload.config || {};
if (!fixture || fixture.ok !== true) {
  return {ok:false, error:'fixture-not-ok'};
}

const PLAYER = 'ship.alpha';
const TARGET = 'ship.beta';
const TIME_STEP = Number(fixture.metrics?.timeStepSeconds || 5);
const FIRE_AT = Number(fixture.metrics?.playerFireAtSeconds ?? (2.34 * TIME_STEP));
const COMBAT_BREAK_AT = Number(fixture.metrics?.hostileCombatReactionAtSeconds ?? (Math.ceil(FIRE_AT / TIME_STEP) * TIME_STEP));
const DURATION = Number(fixture.metrics?.durationSeconds || (5 * TIME_STEP));
const START_SIM = Number(config.startSimulationSeconds ?? Math.max(0, FIRE_AT - Math.min(0.2, TIME_STEP * 0.1)));
const END_SIM = Number(config.endSimulationSeconds ?? Math.min(DURATION, COMBAT_BREAK_AT + TIME_STEP + Math.min(0.2, TIME_STEP * 0.1)));
const HALF_W = Number(fixture.viewScreen.viewHalfWidthM || 3500);
const HALF_H = Number(fixture.viewScreen.viewHalfHeightM || 2000);
const SOFT = Number(fixture.viewScreen.softZoneNormalized || 0.16);
const HARD = Number(fixture.viewScreen.hardLockEnvelopeNormalized || 0.45);
const RESPONSE = Number(fixture.viewScreen.responseSeconds || 0.35);
const EXPECTED_IMPACT_DV = Number(fixture.metrics?.openingImpactDeltaVMps ?? 0.75);

const canvas = document.createElement('canvas');
canvas.width = Number(config.canvasWidth || 960);
canvas.height = Number(config.canvasHeight || 540);
canvas.style.width = `${canvas.width}px`;
canvas.style.height = `${canvas.height}px`;
canvas.style.background = '#020617';
document.body.appendChild(canvas);
const ctx = canvas.getContext('2d');

const hypot2 = (x, y) => Math.hypot(x, y);
const cloneShip = (s) => ({xM:+s.xM, yM:+s.yM, vxMps:+s.vxMps, vyMps:+s.vyMps});
const anchorRows = fixture.trajectoryAnchors.slice().sort((a,b)=>a.atSeconds-b.atSeconds);
const physicsRows = fixture.physicsSamples.slice().sort((a,b)=>a.simulationSeconds-b.simulationSeconds);
if (!anchorRows.length || !physicsRows.length || !fixture.viewScreenSamples?.length) {
  return {ok:false, error:'fixture-samples-missing'};
}

function activeAcceleration(at) {
  let row = anchorRows[0];
  for (const candidate of anchorRows) {
    if (+candidate.atSeconds <= at + 1e-9) row = candidate;
    else break;
  }
  return row.accelerationsMps2;
}

function referenceAt(at) {
  let anchor = anchorRows[0];
  for (const candidate of anchorRows) {
    if (+candidate.atSeconds <= at + 1e-9) anchor = candidate;
    else break;
  }
  const dt = Math.max(0, at - +anchor.atSeconds);
  const result = {};
  for (const id of [PLAYER, TARGET]) {
    const s = anchor.ships[id];
    const acc = anchor.accelerationsMps2[id] || [0,0];
    result[id] = {
      xM:+s.xM + (+s.vxMps)*dt + 0.5*(+acc[0])*dt*dt,
      yM:+s.yM + (+s.vyMps)*dt + 0.5*(+acc[1])*dt*dt,
      vxMps:+s.vxMps + (+acc[0])*dt,
      vyMps:+s.vyMps + (+acc[1])*dt,
    };
  }
  return result;
}

function sampleAtOrBefore(at) {
  let row = null;
  for (const s of physicsRows) {
    if (+s.simulationSeconds <= at + 1e-9) row = s;
    else break;
  }
  if (!row) throw new Error('no initial physics sample');
  return row;
}

const initial = sampleAtOrBefore(START_SIM);
let authoritative = {
  at: +initial.simulationSeconds,
  kind: 'physics-update',
  ships: {[PLAYER]:cloneShip(initial.ships[PLAYER]), [TARGET]:cloneShip(initial.ships[TARGET])},
  accel: activeAcceleration(+initial.simulationSeconds),
};

const messages = [];
for (const s of physicsRows) {
  const t = +s.simulationSeconds;
  if (t > authoritative.at + 1e-9 && t <= END_SIM + 1e-9) {
    messages.push({
      at:t,
      kind:'physics-update',
      priority:0,
      ships:{[PLAYER]:cloneShip(s.ships[PLAYER]), [TARGET]:cloneShip(s.ships[TARGET])},
      accel:activeAcceleration(t),
    });
  }
}
for (const a of anchorRows) {
  const t = +a.atSeconds;
  if (t > authoritative.at + 1e-9 && t <= END_SIM + 1e-9 && (a.kind === 'Impact' || a.kind === 'tactical-boundary')) {
    messages.push({
      at:t,
      kind:a.kind,
      priority:1,
      ships:{[PLAYER]:cloneShip(a.ships[PLAYER]), [TARGET]:cloneShip(a.ships[TARGET])},
      accel:a.accelerationsMps2,
    });
  }
}
messages.sort((a,b)=>a.at-b.at || a.priority-b.priority);

function predictFrom(anchor, at) {
  const dt = Math.max(0, at - anchor.at);
  const out = {};
  for (const id of [PLAYER, TARGET]) {
    const s = anchor.ships[id];
    const acc = anchor.accel[id] || [0,0];
    out[id] = {
      xM:s.xM + s.vxMps*dt + 0.5*(+acc[0])*dt*dt,
      yM:s.yM + s.vyMps*dt + 0.5*(+acc[1])*dt*dt,
      vxMps:s.vxMps + (+acc[0])*dt,
      vyMps:s.vyMps + (+acc[1])*dt,
    };
  }
  return out;
}

const priorView = fixture.viewScreenSamples.reduce(
  (best,row) => (+row.simulationSeconds <= START_SIM + 1e-9 ? row : best),
  fixture.viewScreenSamples[0],
);
let cameraX = +priorView.cameraCenterM[0];
let cameraY = +priorView.cameraCenterM[1];
let priorCameraX = cameraX;
let priorCameraY = cameraY;
let priorFrameNow = null;
let messageIndex = 0;
let frames = 0;
let physicsUpdates = 0;
let eventAnchors = 0;
let maxPredictionErrorM = 0;
let maxPredictionErrorAt = null;
let maxPredictionErrorShip = null;
let maxNormalReanchorPositionCorrectionM = 0;
let maxTacticalVelocityCorrectionMps = 0;
let impactPositionCorrectionM = null;
let impactVelocityCorrectionMps = null;
let maxCameraStepM = 0;
let cameraSnapCount = 0;
let hardLockFrames = 0;
let bothVisibleFrames = 0;
let frameIntervalsMs = [];
let breakOffset = null;
let breakRecoveryTarget = null;
let breakRecoveredAt = null;
let lastSimNow = START_SIM;

function stateDistance(a,b) { return hypot2(a.xM-b.xM, a.yM-b.yM); }
function velocityDistance(a,b) { return hypot2(a.vxMps-b.vxMps, a.vyMps-b.vyMps); }

function ingest(message) {
  const predicted = predictFrom(authoritative, message.at);
  let maxPosCorrection = 0;
  let maxVelCorrection = 0;
  for (const id of [PLAYER, TARGET]) {
    maxPosCorrection = Math.max(maxPosCorrection, stateDistance(predicted[id], message.ships[id]));
    maxVelCorrection = Math.max(maxVelCorrection, velocityDistance(predicted[id], message.ships[id]));
  }
  if (message.kind === 'Impact') {
    impactPositionCorrectionM = maxPosCorrection;
    impactVelocityCorrectionMps = maxVelCorrection;
    eventAnchors++;
  } else if (message.kind === 'tactical-boundary') {
    maxTacticalVelocityCorrectionMps = Math.max(maxTacticalVelocityCorrectionMps, maxVelCorrection);
    eventAnchors++;
  } else {
    maxNormalReanchorPositionCorrectionM = Math.max(maxNormalReanchorPositionCorrectionM, maxPosCorrection);
    physicsUpdates++;
  }
  authoritative = message;
}

function draw(rendered) {
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle = '#020617';
  ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.strokeStyle = '#164e63';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(canvas.width/2,0); ctx.lineTo(canvas.width/2,canvas.height); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0,canvas.height/2); ctx.lineTo(canvas.width,canvas.height/2); ctx.stroke();
  for (const [id, fill] of [[PLAYER,'#cbd5e1'],[TARGET,'#f59e0b']]) {
    const s = rendered[id];
    const nx = (s.xM-cameraX)/HALF_W;
    const ny = (s.yM-cameraY)/HALF_H;
    const px = canvas.width/2 + nx*(canvas.width/2);
    const py = canvas.height/2 - ny*(canvas.height/2);
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.arc(px,py,id===TARGET?7:5,0,Math.PI*2);
    ctx.fill();
  }
}

const realStarted = performance.now();
await new Promise((resolve) => {
  const tick = (now) => {
    if (priorFrameNow !== null) frameIntervalsMs.push(now-priorFrameNow);
    priorFrameNow = now;
    let simNow = START_SIM + (now-realStarted)/1000;
    if (simNow < START_SIM) simNow = START_SIM;
    if (simNow > END_SIM) simNow = END_SIM;

    while (messageIndex < messages.length && messages[messageIndex].at <= simNow + 1e-9) {
      ingest(messages[messageIndex++]);
    }

    const rendered = predictFrom(authoritative, simNow);
    const reference = referenceAt(simNow);
    for (const id of [PLAYER, TARGET]) {
      const err = stateDistance(rendered[id], reference[id]);
      if (err > maxPredictionErrorM) {
        maxPredictionErrorM = err;
        maxPredictionErrorAt = simNow;
        maxPredictionErrorShip = id;
      }
    }

    const dt = Math.max(0, simNow-lastSimNow);
    const target = rendered[TARGET];
    const player = rendered[PLAYER];
    const rawX = (target.xM-cameraX)/HALF_W;
    const rawY = (target.yM-cameraY)/HALF_H;
    let desiredX = cameraX;
    let desiredY = cameraY;
    if (Math.abs(rawX) > SOFT) desiredX = target.xM - Math.sign(rawX)*SOFT*HALF_W;
    if (Math.abs(rawY) > SOFT) desiredY = target.yM - Math.sign(rawY)*SOFT*HALF_H;
    if (dt > 0) {
      const alpha = 1-Math.exp(-dt/RESPONSE);
      cameraX += (desiredX-cameraX)*alpha;
      cameraY += (desiredY-cameraY)*alpha;
    }
    const cameraStep = hypot2(cameraX-priorCameraX,cameraY-priorCameraY);
    maxCameraStepM = Math.max(maxCameraStepM,cameraStep);
    if (frames && cameraStep > Math.max(25,0.025*HALF_W)) cameraSnapCount++;
    priorCameraX=cameraX;
    priorCameraY=cameraY;

    const tx=(target.xM-cameraX)/HALF_W;
    const ty=(target.yM-cameraY)/HALF_H;
    const px=(player.xM-cameraX)/HALF_W;
    const py=(player.yM-cameraY)/HALF_H;
    const offset=hypot2(tx,ty);
    if (Math.abs(tx)<=HARD && Math.abs(ty)<=HARD) hardLockFrames++;
    if (Math.abs(tx)<=1 && Math.abs(ty)<=1 && Math.abs(px)<=1 && Math.abs(py)<=1) bothVisibleFrames++;
    if (simNow >= COMBAT_BREAK_AT-1e-6) {
      if (breakOffset === null) {
        breakOffset=offset;
        breakRecoveryTarget=0.8*offset;
      } else if (breakRecoveredAt === null && offset <= breakRecoveryTarget) {
        breakRecoveredAt=simNow;
      }
    }

    draw(rendered);
    frames++;
    lastSimNow=simNow;
    if (simNow >= END_SIM-1e-9) resolve();
    else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

frameIntervalsMs.sort((a,b)=>a-b);
const meanFrameMs = frameIntervalsMs.reduce((a,b)=>a+b,0)/Math.max(1,frameIntervalsMs.length);
const p95FrameMs = frameIntervalsMs[Math.min(frameIntervalsMs.length-1,Math.floor(frameIntervalsMs.length*0.95))] || 0;
const measuredRafHz = meanFrameMs>0 ? 1000/meanFrameMs : 0;
const updates = physicsUpdates + eventAnchors;
const framesPerAuthorityUpdate = frames/Math.max(1,updates);
const hardLockFraction = hardLockFrames/Math.max(1,frames);
const bothVisibleFraction = bothVisibleFrames/Math.max(1,frames);
const recoverySeconds = breakRecoveredAt === null ? null : breakRecoveredAt-COMBAT_BREAK_AT;
const impactDvTolerance = Math.max(0.05, EXPECTED_IMPACT_DV * 0.1);

const checks = {
  chromiumRafRunsFasterThanPhysics: measuredRafHz >= 45 && framesPerAuthorityUpdate >= 4,
  browserPredictionTracksAuthoritativeTrajectory: maxPredictionErrorM <= 0.05,
  ordinaryPhysicsReanchorsDoNotSnapPosition: maxNormalReanchorPositionCorrectionM <= 0.05,
  impactReanchorDoesNotTeleportPosition: impactPositionCorrectionM !== null && impactPositionCorrectionM <= 0.05,
  impactCarriesExpectedSmallVelocityDiscontinuity:
    impactVelocityCorrectionMps !== null && Math.abs(impactVelocityCorrectionMps-EXPECTED_IMPACT_DV) <= impactDvTolerance,
  tacticalBoundaryDoesNotSnapVelocity: maxTacticalVelocityCorrectionMps <= 1e-6,
  softLockRetainsEnemy: hardLockFraction >= 0.99,
  zoomedOutViewRetainsBothShips: bothVisibleFraction >= 0.99,
  cameraHasNoFrameSnaps: cameraSnapCount === 0,
  poweredBreakawaySettlesWithinOneSlice: recoverySeconds !== null && recoverySeconds <= TIME_STEP,
};
const failedChecks = Object.entries(checks).filter(([,v])=>!v).map(([k])=>k);
console.log(`physics updates=${physicsUpdates} event anchors=${eventAnchors} render frames=${frames} rafHz=${measuredRafHz.toFixed(1)}`);
console.log(`max prediction error=${maxPredictionErrorM.toFixed(6)}m impact dv=${impactVelocityCorrectionMps?.toFixed(3)}m/s camera recovery=${recoverySeconds?.toFixed(3)}s`);
return {
  ok: failedChecks.length===0,
  schema:'game.physicsToViewportChromiumProbe.v1',
  cycle:{
    tacticalSliceSeconds:TIME_STEP,
    physicsStepSeconds:Number(fixture.metrics?.physicsStepSeconds || 0.1),
    browserLoop:'requestAnimationFrame',
    browserMotion:'predict from latest authoritative position/velocity/acceleration anchor',
    immediateEventAnchors:['Impact','tactical-boundary'],
    startSimulationSeconds:START_SIM,
    endSimulationSeconds:END_SIM,
  },
  checks,
  failedChecks,
  metrics:{
    frames,
    physicsUpdates,
    eventAnchors,
    framesPerAuthorityUpdate,
    measuredRafHz,
    meanFrameIntervalMs:meanFrameMs,
    p95FrameIntervalMs:p95FrameMs,
    maxPredictionErrorM,
    maxPredictionErrorAt,
    maxPredictionErrorShip,
    maxNormalReanchorPositionCorrectionM,
    impactPositionCorrectionM,
    impactVelocityCorrectionMps,
    expectedImpactVelocityDiscontinuityMps:EXPECTED_IMPACT_DV,
    maxTacticalVelocityCorrectionMps,
    hardLockFraction,
    bothVisibleFraction,
    cameraSnapCount,
    maxCameraStepM,
    breakOffset,
    breakRecoveryTarget,
    breakRecoveredAtSeconds:breakRecoveredAt,
    breakRecoverySeconds:recoverySeconds,
  }
};
