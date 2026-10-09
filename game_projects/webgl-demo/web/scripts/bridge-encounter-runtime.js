;(function () {
  const CONTRACT = globalThis.MainComputerSpaceCaptainMultirateContract;
  if (!CONTRACT) throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_MISSING");

  const PLAYER = "ship.alpha";
  const TARGET = "ship.beta";
  const EPS = Number(CONTRACT.EPSILON_SECONDS);
  const DEFAULT_WEAPON_DAMAGE_HULL_PERCENT = 50;

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
      this.acceleration = {
        [PLAYER]: [0, 0],
        [TARGET]: this._boardingAccelerationForBoundary(0),
      };
      return this;
    }

    _boardingAccelerationForBoundary(index) {
      if (index <= 0) return [-4.872775933074498, -1.1207384646071346];
      if (index === 1) return [7.999925750083501, -0.03446727652324954];
      return [-3.864588428255515, -1.0319671894946905];
    }

    _combatAccelerationForBoundary(index) {
      if (index <= 0) {
        return {
          [PLAYER]: [23.47688181927758, -8.592788839700047],
          [TARGET]: [12.459472249196837, 21.673983281148253],
        };
      }
      return {
        [PLAYER]: [18.958959133848047, 16.29594638433374],
        [TARGET]: [24.91500131284076, -2.0597838675800864],
      };
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
      const step = Number(this.config.tacticalSliceSeconds);
      const boundaryIndex = Math.max(0, CONTRACT.tacticalBoundaryIndex(atSeconds, this.config));
      if (this.targetStatus === "destroyed") {
        this.acceleration = {[PLAYER]: [0, 0], [TARGET]: [0, 0]};
      } else if (this.hostile && this.combatBoundarySeconds !== null && atSeconds + EPS >= this.combatBoundarySeconds) {
        const combatIndex = Math.max(0, Math.round((atSeconds - this.combatBoundarySeconds) / step));
        const next = this._combatAccelerationForBoundary(combatIndex);
        this.acceleration = {
          [PLAYER]: next[PLAYER].slice(),
          [TARGET]: next[TARGET].slice(),
        };
      } else {
        this.acceleration = {
          [PLAYER]: [0, 0],
          [TARGET]: this._boardingAccelerationForBoundary(boundaryIndex),
        };
      }
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
        const nextTime = Math.min(this.nextPhysicsAtSeconds, nextBoundary, nextImpact);
        if (nextTime > targetTime + EPS || !Number.isFinite(nextTime)) break;

        this._integrateTo(nextTime);

        const isImpact = Math.abs(nextImpact - nextTime) <= EPS;
        const isTacticalBoundary = Math.abs(nextBoundary - nextTime) <= EPS;
        const isPhysicsGrid = Math.abs(this.nextPhysicsAtSeconds - nextTime) <= EPS;

        if (isImpact) this._applyImpactsAt(nextTime);
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
        this._nextPendingImpactAtSeconds()
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

    playerFire(nowMs) {
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
      const rangeM = magnitude(target.xM - player.xM, target.yM - player.yM);
      const impactAtSeconds = simulationSeconds + rangeM / Number(this.config.projectileSpeedMps);
      const shot = {
        id: `player-shot-${++this.shotSequence}`,
        firedAtSeconds: simulationSeconds,
        impactAtSeconds,
        status: "in-flight",
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
      if (type !== "fire-primary-weapon") {
        return {
          accepted: false,
          reason: "unsupported-command",
          commandType: type,
          snapshot: null,
        };
      }
      const result = this.playerFire(nowMs);
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
        ships: {
          [PLAYER]: cloneShip(this.ships[PLAYER], this.acceleration[PLAYER]),
          [TARGET]: cloneShip(this.ships[TARGET], this.acceleration[TARGET]),
        },
        recentEvents: this.events.slice(-24).map((event) => ({...event})),
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
        ships: {
          [PLAYER]: cloneShip(ships[PLAYER], this.acceleration[PLAYER]),
          [TARGET]: cloneShip(ships[TARGET], this.acceleration[TARGET]),
        },
        recentEvents: this.events.slice(-24).map((event) => ({...event})),
      };
    }
  }

  globalThis.MainComputerBridgeEncounterRuntime = Object.freeze({
    SCHEMA: "game.bridgeEncounterRuntime.v1",
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
