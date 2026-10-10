;(function () {
  const CONTRACT = globalThis.MainComputerSpaceCaptainMultirateContract;
  if (!CONTRACT) throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_MISSING");

  const PLAYER = "ship.alpha";
  const TARGET = "ship.beta";
  const EPS = Number(CONTRACT.EPSILON_SECONDS);
  const DEFAULT_WEAPON_DAMAGE_HULL_PERCENT = 50;
  // Tactical coordinates are relative to ship.mother when the physical
  // player authority owns the player's motion. Captain orders set objectives;
  // the helm does not choose its own boarding distance.
  const BOARDING_KP_PER_SECOND2 = 0.014;
  const BOARDING_KD_PER_SECOND = 0.30;
  const BOARDING_MAX_ACCEL_MPS2 = 8;
  // Abstract crew commitment only; no boarding combat/teleport side effects.
  const BOARDING_RANGE_M = 2500;
  const BOARDING_RELATIVE_SPEED_MPS = 5;
  const BOARDING_DURATIONS = Object.freeze({initiate:30,recall:24,abandon:12});

  const magnitude = (x, y) => Math.hypot(Number(x) || 0, Number(y) || 0);
  const finiteNow = (value) => Number.isFinite(Number(value)) ? Number(value) : performance.now();

  const cloneShip = (ship, acceleration = [0, 0]) => ({
    xM: Number(ship.xM) || 0,
    yM: Number(ship.yM) || 0,
    vxMps: Number(ship.vxMps) || 0,
    vyMps: Number(ship.vyMps) || 0,
    axMps2: Number(acceleration[0]) || 0,
    ayMps2: Number(acceleration[1]) || 0,
    speedMps: magnitude(ship.vxMps, ship.vyMps),
  });

  class BridgeEncounterRuntime {
    constructor(options = {}) {
      this.config = CONTRACT.resolveConfig(options);
      this.weaponDamageHullPercent = Math.max(
        0,
        Math.min(100, Number(options.weaponDamageHullPercent ?? DEFAULT_WEAPON_DAMAGE_HULL_PERCENT) || 0)
      );
      // The live bridge uses ship.mother as its player-motion authority.
      // Standalone tactical probes can still model both vessels locally.
      this.physicalPlayerAuthority = options.physicalPlayerAuthority === true;
      this.requirePhysicalWeaponRange = options.requirePhysicalWeaponRange === true;
      this.reset();
    }

    reset() {
      this.startedAtMs = null;
      this.authorityAtSeconds = 0;
      this.nextPhysicsAtSeconds = CONTRACT.nextPhysicsBoundaryAfter(0, this.config);
      this.hostile = false;
      this.combatBoundarySeconds = null;
      this.authorityUpdateCount = 0;
      this.eventAnchorCount = 0;
      this.lastAnchorKind = "initial";
      this.lastExactEvent = null;
      this.eventSequence = 0;
      this.events = [];
      this.shotSequence = 0;
      this.shots = [];
      this.firstFireAtSeconds = null;
      this.lastFireAtSeconds = null;
      this.firstImpactAtSeconds = null;
      this.lastImpactAtSeconds = null;
      this.targetHullPercent = 100;
      this.targetStatus = "operational";
      this.targetDestroyedAtSeconds = null;
      this.terminalOutcome = null;
      this.ships = {
        [PLAYER]: {xM: 0, yM: 0, vxMps: 0, vyMps: 0},
        [TARGET]: {xM: 2600, yM: 598, vxMps: -85, vyMps: -12},
      };
      this.acceleration = {[PLAYER]:[0,0],[TARGET]:[0,0]};
      this.enemyCaptainOrder = null;
      this.enemyCaptainRevision = -1;
      this.enemyCaptainDecisionIds = new Set();
      this.lastCaptainDecisionEvent = null;
      this.boarding = {phase:'idle',boarders:'aboard',action:null,
        startedAtSeconds:null,completeAtSeconds:null,decisionId:null,source:null};
      return this;
    }

    _helmAcceleration(atSeconds) {
      const order = this.enemyCaptainOrder;
      if (!order || order.validThroughSeconds <= atSeconds + EPS || this.targetStatus === "destroyed") return [0,0];
      if (order.maneuver === "coast") return [0,0];
      const own=this.ships[PLAYER], ship=this.ships[TARGET];
      const dx=ship.xM-own.xM, dy=ship.yM-own.yM;
      const r=Math.max(0.000001,Math.hypot(dx,dy)), ux=dx/r, uy=dy/r;
      const vx=ship.vxMps-own.vxMps, vy=ship.vyMps-own.vyMps;
      let ax=0,ay=0;
      if (order.maneuver === 'withdraw') {
        const radialVelocity = vx*ux+vy*uy;
        const wanted = Math.max(0, 70-radialVelocity);
        ax = ux * Math.min(BOARDING_MAX_ACCEL_MPS2, wanted*0.3);
        ay = uy * Math.min(BOARDING_MAX_ACCEL_MPS2, wanted*0.3);
      } else {
        // Captain chooses range. Helm may brake, but never chooses the objective.
        const wanted=order.maneuver==='approach' ? (order.rangeM ?? 0) : order.rangeM;
        const goalX=ux*wanted,goalY=uy*wanted;
        const kp=order.maneuver==='approach'?0.022:BOARDING_KP_PER_SECOND2;
        const kd=order.maneuver==='approach'?0.27:BOARDING_KD_PER_SECOND;
        ax=kp*(goalX-dx)-kd*vx;
        ay=kp*(goalY-dy)-kd*vy;
      }
      const a=Math.hypot(ax,ay),scale=a>BOARDING_MAX_ACCEL_MPS2?BOARDING_MAX_ACCEL_MPS2/a:1;
      return [ax*scale,ay*scale];
    }

    _applyCaptainOrder(order,nowMs) {
      const fail = reason => ({accepted:false,reason,commandType:'captain-helm-order',snapshot:null});
      if (!order || typeof order!=='object') return fail('invalid-captain-order');
      if (order.captainId!=='captain.beta' || order.shipId!==TARGET) return fail('captain-not-authorized');
      if (typeof order.decisionId!=='string'||!order.decisionId.trim()) return fail('invalid-decision-id');
      if (this.enemyCaptainDecisionIds.has(order.decisionId)) return fail('duplicate-decision-id');
      if (!Number.isSafeInteger(order.revision)||order.revision<=this.enemyCaptainRevision) return fail('stale-captain-revision');
      if (!['approach','hold','withdraw','coast'].includes(order.maneuver)) return fail('invalid-maneuver');
      if (order.maneuver==='withdraw' && ['deploying','deployed','recalling','abandoning'].includes(this.boarding.phase))
        return fail('boarders-committed-choose-recall-or-abandon');
      if ((order.maneuver==='hold' || order.rangeM!==undefined) &&
           (!Number.isFinite(order.rangeM)||order.rangeM<0||order.rangeM>1e9)) return fail('invalid-range');
      if (!Number.isFinite(order.issuedAtSeconds)||!Number.isFinite(order.validThroughSeconds)||
          order.issuedAtSeconds<0 || order.validThroughSeconds<=order.issuedAtSeconds) return fail('invalid-order-time');
      const nowS=this.startedAtMs===null ? 0 : Math.max(0,(finiteNow(nowMs)-this.startedAtMs)/1000);
      if (order.issuedAtSeconds>nowS+EPS || order.validThroughSeconds<=nowS+EPS) return fail('order-not-current');
      if (this.targetStatus==='destroyed') return fail('target-destroyed');
      // A decision changes acceleration only at its real simulation timestamp.
      this.advance(nowMs,{active:true});
      if (this.authorityAtSeconds+EPS<nowS) this._integrateTo(nowS);
      this.enemyCaptainOrder=Object.freeze({
        captainId:order.captainId,shipId:TARGET,decisionId:order.decisionId,revision:order.revision,
        maneuver:order.maneuver,rangeM:order.rangeM??null,
        issuedAtSeconds:order.issuedAtSeconds,validThroughSeconds:order.validThroughSeconds,
        source:String(order.source||'captain-decision'),
        modelReceipt:order.modelReceipt ? {...order.modelReceipt} : null
      });
      this.enemyCaptainDecisionIds.add(order.decisionId);
      this.enemyCaptainRevision=order.revision;
      this.acceleration[TARGET]=this._helmAcceleration(nowS);
      this.lastAnchorKind='captain-order';
      this.lastCaptainDecisionEvent=this._recordEvent('captain-order',nowS,{captainId:order.captainId,shipId:TARGET,
        decisionId:order.decisionId,revision:order.revision,maneuver:order.maneuver,
        source:String(order.source||'captain-decision'),
        checkpointSha256:order.modelReceipt?.checkpointSha256||null});
      this.eventAnchorCount++;
      return {accepted:true,commandType:'captain-helm-order',
        decisionId:order.decisionId,snapshot:this.snapshot(nowMs)};
    }

    _boardingRangeM() {
      const a=this.ships[PLAYER],b=this.ships[TARGET];
      return Math.hypot(b.xM-a.xM,b.yM-a.yM);
    }

    _boardingRelativeSpeedMps() {
      const a=this.ships[PLAYER],b=this.ships[TARGET];
      return Math.hypot(b.vxMps-a.vxMps,b.vyMps-a.vyMps);
    }

    _applyBoardingOrder(order, nowMs) {
      const fail=reason=>({accepted:false,reason,commandType:'captain-boarding-order',snapshot:null});
      if(!order || typeof order!=='object') return fail('invalid-captain-order');
      if(order.captainId!=='captain.beta'||order.shipId!==TARGET) return fail('captain-not-authorized');
      if(typeof order.decisionId!=='string'||!order.decisionId.trim()) return fail('invalid-decision-id');
      if(this.enemyCaptainDecisionIds.has(order.decisionId)) return fail('duplicate-decision-id');
      if(!Number.isSafeInteger(order.revision)||order.revision<=this.enemyCaptainRevision) return fail('stale-captain-revision');
      if(!['initiate','recall','abandon'].includes(order.action)) return fail('invalid-boarding-action');
      if(!Number.isFinite(order.issuedAtSeconds)||!Number.isFinite(order.validThroughSeconds)||
         order.issuedAtSeconds<0||order.validThroughSeconds<=order.issuedAtSeconds) return fail('invalid-order-time');
      const nowS=this.startedAtMs===null?0:Math.max(0,(finiteNow(nowMs)-this.startedAtMs)/1000);
      if(order.issuedAtSeconds>nowS+EPS||order.validThroughSeconds<=nowS+EPS) return fail('order-not-current');
      if(this.targetStatus==='destroyed') return fail('target-destroyed');
      // Validate against authoritative state *at the order time*, never the
      // previous display/prediction frame.
      this.advance(nowMs,{active:true});
      this._integrateTo(nowS);
      const phase=this.boarding.phase;
      if(order.action==='initiate') {
        if(phase!=='idle') return fail('boarding-already-committed');
        if(this._boardingRangeM()>BOARDING_RANGE_M+EPS) return fail('transporter-out-of-range');
        if(this._boardingRelativeSpeedMps()>BOARDING_RELATIVE_SPEED_MPS+EPS) return fail('relative-speed-too-high');
      } else if(phase!=='deployed') return fail('no-deployed-boarders-to-resolve');
      const nextPhase={initiate:'deploying',recall:'recalling',abandon:'abandoning'}[order.action];
      this.boarding={phase:nextPhase,boarders:order.action==='initiate'?'aboard':'deployed',
        action:order.action,startedAtSeconds:nowS,
        completeAtSeconds:nowS+BOARDING_DURATIONS[order.action],
        decisionId:order.decisionId,source:String(order.source||'captain-decision'),
        modelReceipt:order.modelReceipt ? {...order.modelReceipt} : null};
      this.enemyCaptainRevision=order.revision;
      this.enemyCaptainDecisionIds.add(order.decisionId);
      this.lastAnchorKind='captain-boarding-order';
      this.lastCaptainDecisionEvent=this._recordEvent('captain-boarding-order',nowS,{
        decisionId:order.decisionId,revision:order.revision,action:order.action,
        completeAtSeconds:this.boarding.completeAtSeconds,source:this.boarding.source,
        checkpointSha256:this.boarding.modelReceipt?.checkpointSha256||null});
      this.eventAnchorCount++;
      return {accepted:true,commandType:'captain-boarding-order',decisionId:order.decisionId,
        snapshot:this.snapshot(nowMs)};
    }

    _nextBoardingCompletionSeconds() {
      const t=this.boarding.completeAtSeconds;
      return Number.isFinite(t) && t>this.authorityAtSeconds+EPS ? t : Infinity;
    }

    _completeBoarding(atSeconds) {
      const previous=this.boarding,action=previous.action;
      if(!action||Math.abs(previous.completeAtSeconds-atSeconds)>EPS) return;
      // Deployment and recall are distance-sensitive *at completion* too.
      // A broken contact does not magically transport anyone.
      const inRange=this._boardingRangeM()<=BOARDING_RANGE_M+EPS &&
        this._boardingRelativeSpeedMps()<=BOARDING_RELATIVE_SPEED_MPS+EPS;
      let phase,boarders;
      if(action==='initiate') {phase=inRange?'deployed':'idle';boarders=inRange?'deployed':'aboard';}
      else if(action==='recall') {phase=inRange?'recovered':'deployed';boarders=inRange?'aboard':'deployed';}
      else {phase='abandoned';boarders='abandoned';}
      this.boarding={...previous,phase,boarders,action:null,completeAtSeconds:null};
      this.lastAnchorKind='boarding-complete';
      this._recordEvent('boarding-complete',atSeconds,{
        decisionId:previous.decisionId,action,phase,boarders,success:action==='abandon'||inRange,
        source:previous.source,checkpointSha256:previous.modelReceipt?.checkpointSha256||null});
      this.eventAnchorCount++;
    }

    _nextOrderExpiryAtSeconds() {
      const order=this.enemyCaptainOrder;
      return order && order.validThroughSeconds>this.authorityAtSeconds+EPS ? order.validThroughSeconds : Infinity;
    }

    _ensureStarted(nowMs) {
      if (this.startedAtMs !== null) return;
      this.startedAtMs = finiteNow(nowMs);
    }

    simulationSeconds(nowMs) {
      this._ensureStarted(nowMs);
      return Math.max(0, (finiteNow(nowMs) - this.startedAtMs) / 1000);
    }

    _integrateTo(atSeconds) {
      const dt = Math.max(0, Number(atSeconds) - this.authorityAtSeconds);
      if (!(dt > 0)) return;
      for (const id of [PLAYER, TARGET]) {
        const ship = this.ships[id];
        const acc = this.acceleration[id] || [0, 0];
        ship.xM += ship.vxMps * dt + 0.5 * Number(acc[0]) * dt * dt;
        ship.yM += ship.vyMps * dt + 0.5 * Number(acc[1]) * dt * dt;
        ship.vxMps += Number(acc[0]) * dt;
        ship.vyMps += Number(acc[1]) * dt;
      }
      this.authorityAtSeconds = Number(atSeconds);
    }

    _recordEvent(kind, atSeconds, details = {}) {
      const event = {
        sequence: ++this.eventSequence,
        kind: String(kind),
        atSeconds: Number(atSeconds),
        ...details,
      };
      this.lastExactEvent = event;
      this.events.push(event);
      if (this.events.length > 128) this.events.splice(0, this.events.length - 128);
      return event;
    }

    _nextTacticalBoundaryAfter(atSeconds) {
      return CONTRACT.nextTacticalBoundaryAfter(atSeconds, this.config);
    }

    _nextPendingImpactAtSeconds() {
      let next = Infinity;
      for (const shot of this.shots) {
        if (shot.status !== "in-flight") continue;
        next = Math.min(next, Number(shot.impactAtSeconds));
      }
      return next;
    }

    _applyTacticalBoundary(atSeconds) {
      this.acceleration = {
        [PLAYER]:[0,0],
        [TARGET]:this._helmAcceleration(atSeconds)
      };
      this.lastAnchorKind = "tactical-boundary";
      this._recordEvent("tactical-boundary", atSeconds, {
        targetStatus: this.targetStatus,
        hostile: this.hostile,
      });
      this.eventAnchorCount += 1;
    }

    _applyImpactShot(shot, atSeconds) {
      const player = this.ships[PLAYER];
      const target = this.ships[TARGET];
      const dx = target.xM - player.xM;
      const dy = target.yM - player.yM;
      const distance = Math.max(EPS, magnitude(dx, dy));
      const impulse = Math.min(1.0, Math.max(0, Number(this.config.impactDeltaVMps)));
      target.vxMps += (dx / distance) * impulse;
      target.vyMps += (dy / distance) * impulse;

      const hullBeforePercent = this.targetHullPercent;
      const damageHullPercent = Math.min(hullBeforePercent, this.weaponDamageHullPercent);
      this.targetHullPercent = Math.max(0, hullBeforePercent - damageHullPercent);
      this.targetStatus = this.targetHullPercent <= 0 ? "destroyed" : "damaged";
      if (this.targetStatus === "destroyed") {
        if (this.targetDestroyedAtSeconds === null) this.targetDestroyedAtSeconds = Number(atSeconds);
        this.terminalOutcome = "target-destroyed";
        this.acceleration[TARGET] = [0, 0];
        this.enemyCaptainOrder=null;
      }
      shot.status = "impact";
      shot.impactedAtSeconds = Number(atSeconds);
      shot.damageHullPercent = damageHullPercent;
      shot.hullBeforePercent = hullBeforePercent;
      shot.hullAfterPercent = this.targetHullPercent;
      if (this.firstImpactAtSeconds === null) this.firstImpactAtSeconds = Number(atSeconds);
      this.lastImpactAtSeconds = Number(atSeconds);
      this.lastAnchorKind = "Impact";
      this._recordEvent("Impact", atSeconds, {
        shotId: shot.id,
        impactDeltaVMps: impulse,
        damageHullPercent,
        hullBeforePercent,
        hullAfterPercent: this.targetHullPercent,
        targetStatus: this.targetStatus,
      });
      this.eventAnchorCount += 1;
    }

    _applyImpactsAt(atSeconds) {
      const due = this.shots.filter((shot) => shot.status === "in-flight" && Math.abs(Number(shot.impactAtSeconds) - Number(atSeconds)) <= EPS);
      for (const shot of due) this._applyImpactShot(shot, atSeconds);
    }

    _advanceAuthorityTo(simulationSeconds) {
      const targetTime = Math.max(this.authorityAtSeconds, Number(simulationSeconds) || 0);
      while (true) {
        const nextBoundary = this._nextTacticalBoundaryAfter(this.authorityAtSeconds);
        const nextImpact = this._nextPendingImpactAtSeconds();
        const nextExpiry=this._nextOrderExpiryAtSeconds();
        const nextBoarding=this._nextBoardingCompletionSeconds();
        const nextTime = Math.min(this.nextPhysicsAtSeconds, nextBoundary, nextImpact, nextExpiry, nextBoarding);
        if (nextTime > targetTime + EPS || !Number.isFinite(nextTime)) break;

        this._integrateTo(nextTime);

        const isImpact = Math.abs(nextImpact - nextTime) <= EPS;
        const isTacticalBoundary = Math.abs(nextBoundary - nextTime) <= EPS;
        const isPhysicsGrid = Math.abs(this.nextPhysicsAtSeconds - nextTime) <= EPS;

        if (isImpact) this._applyImpactsAt(nextTime);
        if(Math.abs(nextBoarding-nextTime)<=EPS) this._completeBoarding(nextTime);
        if (Math.abs(nextExpiry-nextTime)<=EPS && this.enemyCaptainOrder) {
          const expired=this.enemyCaptainOrder;
          this.enemyCaptainOrder=null;
          this.acceleration[TARGET]=[0,0];
          this._recordEvent('captain-order-expired',nextTime,{decisionId:expired.decisionId});
          this.eventAnchorCount++;
        }
        if (isTacticalBoundary) this._applyTacticalBoundary(nextTime);
        if (isPhysicsGrid) {
          if (!isImpact && !isTacticalBoundary) this.lastAnchorKind = "physics-update";
          this.authorityUpdateCount += 1;
          this.nextPhysicsAtSeconds = CONTRACT.nextPhysicsBoundaryAfter(nextTime, this.config);
        }
      }
    }

    _nextRequiredAuthorityAtSeconds() {
      return Math.min(
        this.nextPhysicsAtSeconds,
        this._nextTacticalBoundaryAfter(this.authorityAtSeconds),
        this._nextPendingImpactAtSeconds(),
        this._nextOrderExpiryAtSeconds(),
        this._nextBoardingCompletionSeconds()
      );
    }

    assertFreshFor(nowMs) {
      const simulationSeconds = this.simulationSeconds(nowMs);
      const nextRequired = this._nextRequiredAuthorityAtSeconds();
      if (nextRequired <= simulationSeconds + EPS) {
        throw new Error(
          `BRIDGE_ENCOUNTER_AUTHORITY_STALE: advance() required through ${simulationSeconds.toFixed(9)}s before read; next authority boundary=${nextRequired.toFixed(9)}s`
        );
      }
      return simulationSeconds;
    }

    advance(nowMs, options = {}) {
      if (options.active === false) return null;
      const simulationSeconds = this.simulationSeconds(nowMs);
      this._advanceAuthorityTo(simulationSeconds);
      return {
        simulationSeconds,
        authorityAtSeconds: this.authorityAtSeconds,
        authorityUpdateCount: this.authorityUpdateCount,
        eventAnchorCount: this.eventAnchorCount,
      };
    }

    _predictAtSimulationSeconds(simulationSeconds) {
      const dt = Math.max(0, Number(simulationSeconds) - this.authorityAtSeconds);
      const ships = {};
      for (const id of [PLAYER, TARGET]) {
        const ship = this.ships[id];
        const acc = this.acceleration[id] || [0, 0];
        ships[id] = {
          xM: ship.xM + ship.vxMps * dt + 0.5 * Number(acc[0]) * dt * dt,
          yM: ship.yM + ship.vyMps * dt + 0.5 * Number(acc[1]) * dt * dt,
          vxMps: ship.vxMps + Number(acc[0]) * dt,
          vyMps: ship.vyMps + Number(acc[1]) * dt,
        };
        ships[id].speedMps = magnitude(ships[id].vxMps, ships[id].vyMps);
      }
      return ships;
    }

    predict(nowMs) {
      const simulationSeconds = this.assertFreshFor(nowMs);
      return this._predictAtSimulationSeconds(simulationSeconds);
    }

    phaseAt(simulationSeconds) {
      if (this.targetStatus === "destroyed") return "destroyed";
      if (this.hostile) {
        if (this.combatBoundarySeconds !== null && simulationSeconds + EPS >= this.combatBoundarySeconds) return "combat";
        return "hostile-reaction-pending";
      }
      const step = Number(this.config.tacticalSliceSeconds);
      if (simulationSeconds < step) return "boarding-approach";
      if (simulationSeconds < 2 * step) return "boarding-velocity-match";
      return "boarding-prep";
    }

    playerFire(nowMs, physicalShot = null) {
      const simulationSeconds = this.simulationSeconds(nowMs);
      this._advanceAuthorityTo(simulationSeconds);

      if (this.targetStatus === "destroyed") {
        return {
          accepted: false,
          reason: "target-destroyed",
          simulationSeconds,
          targetHullPercent: this.targetHullPercent,
          targetStatus: this.targetStatus,
        };
      }

      // An accepted player command is an exact semantic event anchor. Only accepted
      // commands may fracture the ordinary physics grid at their true timestamp.
      if (this.authorityAtSeconds + EPS < simulationSeconds) this._integrateTo(simulationSeconds);

      const ships = this._predictAtSimulationSeconds(simulationSeconds);
      const player = ships[PLAYER];
      const target = ships[TARGET];
      const tacticalRangeM = magnitude(target.xM - player.xM, target.yM - player.yM);
      const worldVector = value => Array.isArray(value) && value.length === 3
        && value.every(x => typeof x === "number" && Number.isFinite(x));
      const physicalValid = physicalShot && worldVector(physicalShot.originWorldPositionM)
        && worldVector(physicalShot.targetWorldPositionM);
      if (this.requirePhysicalWeaponRange && !physicalValid) {
        return {accepted:false,reason:"physical-shot-state-required",simulationSeconds};
      }
      const rangeM = physicalValid
        ? Math.hypot(...physicalShot.targetWorldPositionM.map((v,i) => v-physicalShot.originWorldPositionM[i]))
        : tacticalRangeM;
      const impactAtSeconds = simulationSeconds + rangeM / Number(this.config.projectileSpeedMps);
      const shot = {
        id: `player-shot-${++this.shotSequence}`,
        firedAtSeconds: simulationSeconds,
        impactAtSeconds,
        status: "in-flight",
        rangeAtFireM: rangeM,
        originWorldPositionM: physicalValid ? physicalShot.originWorldPositionM.slice() : null,
        targetWorldPositionAtFireM: physicalValid ? physicalShot.targetWorldPositionM.slice() : null,
        damageHullPercent: 0,
      };
      this.shots.push(shot);
      if (this.firstFireAtSeconds === null) this.firstFireAtSeconds = simulationSeconds;
      this.lastFireAtSeconds = simulationSeconds;
      if (!this.hostile) {
        this.hostile = true;
        this.combatBoundarySeconds = CONTRACT.nextTacticalBoundaryAfter(simulationSeconds, this.config);
      }
      this.lastAnchorKind = "player-fire";
      this._recordEvent("player-fire", simulationSeconds, {
        shotId: shot.id,
        impactAtSeconds,
        rangeM,
      });
      this.eventAnchorCount += 1;
      return {
        accepted: true,
        simulationSeconds,
        shot: {...shot},
        targetHullPercent: this.targetHullPercent,
        targetStatus: this.targetStatus,
      };
    }


    status(nowMs = null) {
      const simulationSeconds = this.startedAtMs === null
        ? 0
        : Math.max(0, ((nowMs === null ? this.startedAtMs + this.authorityAtSeconds * 1000 : finiteNow(nowMs)) - this.startedAtMs) / 1000);
      const lastFireAgeMs = this.lastFireAtSeconds === null ? Infinity : Math.max(0, (simulationSeconds - this.lastFireAtSeconds) * 1000);
      const lastImpactAgeMs = this.lastImpactAtSeconds === null ? Infinity : Math.max(0, (simulationSeconds - this.lastImpactAtSeconds) * 1000);
      return {
        schema: "game.bridgeEncounterStatus.v1",
        simulationSeconds,
        phase: this.phaseAt(simulationSeconds),
        hostileAwareness: this.hostile ? "active-hostile-player" : "player-believed-inactive",
        targetHullPercent: this.targetHullPercent,
        targetStatus: this.targetStatus,
        targetDestroyed: this.targetStatus === "destroyed",
        targetDestroyedAtSeconds: this.targetDestroyedAtSeconds,
        terminalOutcome: this.terminalOutcome,
        shotsFired: this.shots.length,
        pendingShotCount: this.shots.filter((shot) => shot.status === "in-flight").length,
        firstFireAtSeconds: this.firstFireAtSeconds,
        lastFireAtSeconds: this.lastFireAtSeconds,
        firstImpactAtSeconds: this.firstImpactAtSeconds,
        lastImpactAtSeconds: this.lastImpactAtSeconds,
        lastFireAgeMs,
        lastImpactAgeMs,
        combatBoundarySeconds: this.combatBoundarySeconds,
        lastExactEvent: this.lastExactEvent ? {...this.lastExactEvent} : null,
      };
    }

    combatState() {
      return {
        schema: "game.bridgeEncounterCombatState.v1",
        hostile: Boolean(this.hostile),
        hostileAwareness: this.hostile ? "active-hostile-player" : "player-believed-inactive",
        shotsFired: this.shots.length,
        firstFireAtSeconds: this.firstFireAtSeconds,
        lastFireAtSeconds: this.lastFireAtSeconds,
        firstImpactAtSeconds: this.firstImpactAtSeconds,
        lastImpactAtSeconds: this.lastImpactAtSeconds,
        combatBoundarySeconds: this.combatBoundarySeconds,
        projectiles: this.shots.map((shot) => ({
          ...shot,
          impactApplied: shot.status === "impact",
        })),
        target: {
          id: TARGET,
          hullPercent: Number(this.targetHullPercent),
          status: this.targetStatus,
          disabled: this.targetStatus === "destroyed",
          destroyedAtSeconds: this.targetDestroyedAtSeconds,
        },
        terminalOutcome: this.terminalOutcome,
      };
    }

    command(command, nowMs) {
      const payload = command && typeof command === "object" ? command : {type: command};
      const type = String(payload?.type || "").trim();
      if (type === "captain-helm-order") return this._applyCaptainOrder(payload.order,nowMs);
      if (type === "captain-boarding-order") return this._applyBoardingOrder(payload.order,nowMs);
      if (type !== "fire-primary-weapon") {
        return {
          accepted: false,
          reason: "unsupported-command",
          commandType: type,
          snapshot: null,
        };
      }
      const result = this.playerFire(nowMs, payload.physicalShot || null);
      return {
        ...result,
        commandType: type,
        snapshot: this.snapshot(nowMs),
      };
    }

    eventsSince(sequence = 0) {
      const after = Number(sequence) || 0;
      return this.events.filter((event) => Number(event.sequence) > after).map((event) => ({...event}));
    }

    readAuthorityState() {
      const nextImpact = this._nextPendingImpactAtSeconds();
      return {
        schema: "game.bridgeEncounterAuthorityState.v1",
        contractSchema: CONTRACT.SCHEMA,
        config: {...this.config},
        hostileAwareness: this.hostile ? "active-hostile-player" : "player-believed-inactive",
        encounter: {
          hostile: Boolean(this.hostile),
          terminalOutcome: this.terminalOutcome,
        },
        target: {
          id: TARGET,
          hullPercent: this.targetHullPercent,
          status: this.targetStatus,
          destroyed: this.targetStatus === "destroyed",
          disabled: this.targetStatus === "destroyed",
          destroyedAtSeconds: this.targetDestroyedAtSeconds,
        },
        weapons: {
          playerShotsFired: this.shots.length,
          pendingShotCount: this.shots.filter((shot) => shot.status === "in-flight").length,
          firstFireAtSeconds: this.firstFireAtSeconds,
          lastFireAtSeconds: this.lastFireAtSeconds,
          firstImpactAtSeconds: this.firstImpactAtSeconds,
          lastImpactAtSeconds: this.lastImpactAtSeconds,
          projectiles: this.shots.map((shot) => ({
            ...shot,
            impactApplied: shot.impactApplied === true || shot.status === "impact",
          })),
        },
        combatBoundarySeconds: this.combatBoundarySeconds,
        authority: {
          atSeconds: this.authorityAtSeconds,
          kind: this.lastAnchorKind,
          updateCount: this.authorityUpdateCount,
          eventAnchorCount: this.eventAnchorCount,
          nextPhysicsAtSeconds: this.nextPhysicsAtSeconds,
          nextTacticalBoundaryAtSeconds: this._nextTacticalBoundaryAfter(this.authorityAtSeconds),
          nextPendingImpactAtSeconds: Number.isFinite(nextImpact) ? nextImpact : null,
          lastExactEvent: this.lastExactEvent ? {...this.lastExactEvent} : null,
        },
        boarding:{...this.boarding},
        helm: {
          [TARGET]: {activeOrder:this.enemyCaptainOrder ? {...this.enemyCaptainOrder} : null,
                     lastRevision:this.enemyCaptainRevision}
        },
        ships: {
          [PLAYER]: cloneShip(this.ships[PLAYER], this.acceleration[PLAYER]),
          [TARGET]: cloneShip(this.ships[TARGET], this.acceleration[TARGET]),
        },
        recentEvents: [
          ...(this.lastCaptainDecisionEvent && !this.events.slice(-24).some(event=>event.sequence===this.lastCaptainDecisionEvent.sequence)
             ? [this.lastCaptainDecisionEvent] : []),
          ...this.events.slice(-24)
        ].map(event=>({...event})),
      };
    }

    snapshot(nowMs) {
      const simulationSeconds = this.assertFreshFor(nowMs);
      const ships = this._predictAtSimulationSeconds(simulationSeconds);
      return {
        schema: "game.bridgeEncounterRuntime.v1",
        contractSchema: CONTRACT.SCHEMA,
        simulationSeconds,
        tacticalSliceSeconds: Number(this.config.tacticalSliceSeconds),
        physicsStepSeconds: Number(this.config.physicsStepSeconds),
        phase: this.phaseAt(simulationSeconds),
        hostileAwareness: this.hostile ? "active-hostile-player" : "player-believed-inactive",
        target: {
          id: TARGET,
          hullPercent: this.targetHullPercent,
          status: this.targetStatus,
          destroyed: this.targetStatus === "destroyed",
          disabled: this.targetStatus === "destroyed",
          destroyedAtSeconds: this.targetDestroyedAtSeconds,
        },
        combat: this.combatState(),
        weapons: {
          playerShotsFired: this.shots.length,
          pendingShotCount: this.shots.filter((shot) => shot.status === "in-flight").length,
          firstFireAtSeconds: this.firstFireAtSeconds,
          lastFireAtSeconds: this.lastFireAtSeconds,
          firstImpactAtSeconds: this.firstImpactAtSeconds,
          lastImpactAtSeconds: this.lastImpactAtSeconds,
          projectiles: this.shots.map((shot) => ({
            ...shot,
            impactApplied: shot.impactApplied === true || shot.status === "impact",
          })),
        },
        combatBoundarySeconds: this.combatBoundarySeconds,
        authority: {
          atSeconds: this.authorityAtSeconds,
          kind: this.lastAnchorKind,
          updateCount: this.authorityUpdateCount,
          eventAnchorCount: this.eventAnchorCount,
          nextPhysicsAtSeconds: this.nextPhysicsAtSeconds,
          nextTacticalBoundaryAtSeconds: this._nextTacticalBoundaryAfter(this.authorityAtSeconds),
          nextPendingImpactAtSeconds: this._nextPendingImpactAtSeconds(),
          lastExactEvent: this.lastExactEvent ? {...this.lastExactEvent} : null,
        },
        boarding:{...this.boarding},
        helm: {
          [TARGET]: {activeOrder:this.enemyCaptainOrder ? {...this.enemyCaptainOrder} : null,
                     lastRevision:this.enemyCaptainRevision}
        },
        ships: {
          [PLAYER]: cloneShip(ships[PLAYER], this.acceleration[PLAYER]),
          [TARGET]: cloneShip(ships[TARGET], this.acceleration[TARGET]),
        },
        recentEvents: [
          ...(this.lastCaptainDecisionEvent && !this.events.slice(-24).some(event=>event.sequence===this.lastCaptainDecisionEvent.sequence)
             ? [this.lastCaptainDecisionEvent] : []),
          ...this.events.slice(-24)
        ].map(event=>({...event})),
      };
    }
  }

  globalThis.MainComputerBridgeEncounterRuntime = Object.freeze({
    SCHEMA: "game.bridgeEncounterRuntime.v1",
    BOARDING_DURATIONS,
    BOARDING_RANGE_M,
    BOARDING_RELATIVE_SPEED_MPS,
    CONTRACT_SCHEMA: CONTRACT.SCHEMA,
    DEFAULT_WEAPON_DAMAGE_HULL_PERCENT,
    COMBAT_RULES: Object.freeze({
      primaryWeaponDamageHullPercent: DEFAULT_WEAPON_DAMAGE_HULL_PERCENT,
    }),
    create(options = {}) {
      return new BridgeEncounterRuntime(options);
    },
  });
})();
