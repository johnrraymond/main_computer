;(function () {
  const CONTRACT = globalThis.MainComputerSpaceCaptainMultirateContract;
  const AUTHORITY = globalThis.MainComputerBridgeEncounterRuntime;
  const PROJECTION = globalThis.MainComputerBridgeViewscreenProjection;
  if (!CONTRACT) throw new Error("SPACE_CAPTAIN_MULTIRATE_CONTRACT_MISSING");
  if (!AUTHORITY) throw new Error("BRIDGE_ENCOUNTER_RUNTIME_MISSING");
  if (!PROJECTION) throw new Error("BRIDGE_VIEWSCREEN_PROJECTION_MISSING");

  class BridgeViewscreenEncounterRuntime {
    constructor(options = {}) {
      const suppliedAuthority = options.authority || null;
      this.authority = suppliedAuthority || AUTHORITY.create(options);
      this.ownsAuthority = !suppliedAuthority;
      this.config = this.authority.config;
      this.presentationState = null;
      this.lastSnapshotNowMs = null;
      this.lastSnapshot = null;
    }

    get authorityAtSeconds() { return this.authority.authorityAtSeconds; }
    get authorityUpdateCount() { return this.authority.authorityUpdateCount; }
    get eventAnchorCount() { return this.authority.eventAnchorCount; }
    get nextPhysicsAtSeconds() { return this.authority.nextPhysicsAtSeconds; }
    get startedAtMs() { return this.authority.startedAtMs; }

    reset() {
      if (this.ownsAuthority) this.authority.reset();
      this.presentationState = null;
      this.lastSnapshotNowMs = null;
      this.lastSnapshot = null;
      return this;
    }

    advance(nowMs, options = {}) {
      return this.authority.advance(nowMs, options);
    }

    playerFire(nowMs) {
      const result = this.authority.playerFire(nowMs);
      return {
        command: result,
        snapshot: this.snapshot(nowMs, {active: true}),
      };
    }

    snapshot(nowMs, options = {}) {
      if (options.active === false) return this.lastSnapshot;
      let simulationSeconds;
      try {
        simulationSeconds = this.authority.assertFreshFor(nowMs);
      } catch (error) {
        if (String(error?.message || error).includes("BRIDGE_ENCOUNTER_AUTHORITY_STALE")) {
          throw new Error(String(error.message).replace("BRIDGE_ENCOUNTER_AUTHORITY_STALE", "BRIDGE_VIEWSCREEN_AUTHORITY_STALE"));
        }
        throw error;
      }
      const authorityState = this.authority.readAuthorityState();
      const projected = PROJECTION.project({
        authorityState,
        simulationSeconds,
        presentationState: this.presentationState,
      });
      this.presentationState = projected.nextPresentationState;
      this.lastSnapshotNowMs = Number(nowMs);
      this.lastSnapshot = projected.snapshot;
      return this.lastSnapshot;
    }
  }

  globalThis.MainComputerBridgeViewscreenEncounterRuntime = Object.freeze({
    CONTRACT_SCHEMA: CONTRACT.SCHEMA,
    PROJECTION_SCHEMA: PROJECTION.SCHEMA,
    DEFAULTS: CONTRACT.DEFAULTS,
    create(options = {}) {
      return new BridgeViewscreenEncounterRuntime(options);
    },
  });
})();
