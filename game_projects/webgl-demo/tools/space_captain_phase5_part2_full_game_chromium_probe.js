/* Phase 5 Part 2. Browser driver for the actual authored project and production scene.
   This test helper does not replace or reimplement any game class or combat system. */
;(function () {
  "use strict";
  const snapshot = () => {
    const r = globalThis.__phase5Part2Renderer;
    if (!r) throw new Error("PHASE5_PART2_REAL_RENDERER_MISSING");
    const presentation = r.bridgeViewscreenPresentationSnapshot?.() || null;
    const projected = r.bridgeViewscreenProjectionFrame || null;
    const authority = r.bridgeEncounterRuntime?.readAuthorityState?.() || null;
    const physics = r.spaceGravitySnapshot?.() || null;
    const physicalMother = (physics?.bodies || []).find(body => body.id === 'ship.mother') || null;
    // The attitude must come from a production source, not from an invented HUD heading.
    const observerPose = r.mainShipObserverPose?.() || null;
    const gl = r.gl;
    const geometry = r.dynamicGeometry || new Float32Array(0);
    let hash = 2166136261;
    const view = new DataView(geometry.buffer, geometry.byteOffset, geometry.byteLength);
    for (let offset = 0; offset < view.byteLength; offset += 4) {
      const value = view.getUint32(offset, true);
      hash = Math.imul(hash ^ value, 16777619) >>> 0;
    }
    return {
      sceneId: r.scene?.id || null,
      sceneProjection: r.scene?.metadata?.projection || null,
      realWebGLContext: Boolean(gl && typeof gl.drawArrays === "function"),
      webglVersion: gl?.getParameter?.(gl.VERSION) || null,
      canvasWidth: r.canvas?.width || 0,
      canvasHeight: r.canvas?.height || 0,
      drawCalls: Number(globalThis.__phase5Part2DrawCalls || 0),
      dynamicVertexCount: Number(r.dynamicVertexCount || 0),
      dynamicGeometryHash: hash.toString(16).padStart(8, "0"),
      location: String(r.shipState?.location || ""),
      objective: String(r.shipState?.objectiveId || ""),
      playerBayControl: Boolean(r.isShuttleBayPlayerControlActive?.()),
      selectedMode: r.bridgeViewscreenSelectedMode?.() || "",
      displayPowered: r.bridgeViewscreenDisplayPowered?.() !== false,
      presentationSchema: presentation?.schema || null,
      presentationSeconds: Number(presentation?.time?.simulationSeconds ?? -1),
      targetScreen: presentation?.target?.screen || null,
      targetVisible: Boolean(presentation?.target?.visible),
      targetHullFraction: Number(presentation?.target?.hullFraction ?? -1),
      targetVisualState: String(presentation?.target?.visualState || ""),
      projectileCount: presentation?.effects?.projectiles?.length || 0,
      impactCount: presentation?.effects?.impacts?.length || 0,
      explosionCount: presentation?.effects?.explosions?.length || 0,
      debrisCount: presentation?.effects?.debris?.length || 0,
      projectionCamera: projected?.viewScreen?.cameraCenterM?.slice?.() || null,
      physicsSystemId: physics?.activeSystemId || null,
      physicsSimulationSeconds: physics?.simulationSeconds ?? null,
      physicalMotherPositionM: physicalMother?.positionM?.slice?.() || null,
      physicalMotherVelocityMps: physicalMother?.velocityMps?.slice?.() || null,
      authoritativeObserver: observerPose ? JSON.parse(JSON.stringify(observerPose)) : null,
      cameraObserverBodyId: projected?.viewScreen?.observerBodyId || null,
      cameraWorldPositionM: projected?.viewScreen?.cameraWorldPositionM?.slice?.() || null,
      cameraForwardWorld: projected?.viewScreen?.cameraForwardWorld?.slice?.() || null,
      targetWorldPositionM: projected?.viewScreen?.targetWorldPositionM?.slice?.() || null,
      targetAuthorityWorldPositionM: authority?.ships?.['ship.beta']?.worldPositionM?.slice?.() || null,
      targetRelativeWorldM: projected?.viewScreen?.targetRelativeWorldM?.slice?.() || null,
      targetInFront: projected?.viewScreen?.targetInFront ?? null,
      playerShipExternalVisible: presentation?.ownShip?.visible ?? null,
      projectedRangeM: projected?.rangeM ?? null,
      authorityHullPercent: Number(authority?.target?.hullPercent ?? -1),
      authorityTargetDestroyed: Boolean(authority?.target?.destroyed),
      authorityPhase: String(authority?.encounter?.phase || ""),
      shots: (authority?.weapons?.projectiles || []).map((shot) => ({
        id: String(shot.id || ""),
        firedAtSeconds: Number(shot.firedAtSeconds),
        impactAtSeconds: Number(shot.impactAtSeconds),
        resolved: Boolean(shot.impactApplied === true || shot.status === "impact"),
      })),
      interactionTargetId: String(r.shipInteractionTarget?.()?.id || ""),
      interactionHint: String(r.shipInteractionHint?.() || ""),
      hudObjective: String(document.querySelector(".scene-shuttle3d")?.dataset?.shipObjective || ""),
      shipHudVisible: document.querySelector(".scene-shuttle3d-ship-line")?.hidden === false,
      shuttleEncounterHudHidden: document.querySelector(".scene-shuttle3d-encounter-line")?.hidden === true,
      movementLocationText: String(document.querySelector(".scene-shuttle3d")?.querySelector(".scene-shuttle3d-mesh-status")?.textContent || ""),
      lastInteractionStatus: String(r.shipState?.lastInteractionStatus || ""),
    };
  };

  globalThis.__phase5Part2 = Object.freeze({
    boot(project) {
      if (!project || !Array.isArray(project.scenes) || !project.scenes[0]) {
        throw new Error("PHASE5_PART2_AUTHORED_PROJECT_MISSING");
      }
      if (globalThis.__phase5Part2Renderer) throw new Error("PHASE5_PART2_DOUBLE_BOOT");
      if (!globalThis.MainComputerSceneStore?.saveScene || !globalThis.MainComputerSceneViewer?.renderSceneSurface) {
        throw new Error("PHASE5_PART2_PRODUCTION_SCENE_MODULE_MISSING");
      }
      const authored = globalThis.MainComputerSceneStore.saveScene(project.scenes[0], {notify:false});
      const element = document.getElementById("webgl-demo");
      if (!element) throw new Error("PHASE5_PART2_GAME_SURFACE_MISSING");
      globalThis.__phase5Part2Surface = globalThis.MainComputerSceneViewer.renderSceneSurface(element, authored, {
        mode: "surface", projectId: project.id, project,
        // This smoke exercises the actual gate with a ready provider. It does not
        // launch a local NanoJev server; that service has its own integration gate.
        tacticalAIReadiness: () => ({ready:true, consecutivePasses:3, timeStepSeconds:3}),
      });
      const renderer = element.__mainComputerShuttle3dRenderer;
      if (!renderer?.gl || !(renderer.gl instanceof WebGLRenderingContext)) {
        throw new Error("PHASE5_PART2_REAL_WEBGL_CONTEXT_REQUIRED");
      }
      const originalDrawArrays = renderer.gl.drawArrays.bind(renderer.gl);
      globalThis.__phase5Part2DrawCalls = 0;
      renderer.gl.drawArrays = function (...args) {
        globalThis.__phase5Part2DrawCalls += 1;
        return originalDrawArrays(...args);
      };
      globalThis.__phase5Part2Renderer = renderer;
      return snapshot();
    },
    step(nowMs) {
      const renderer = globalThis.__phase5Part2Renderer;
      if (!renderer || !Number.isFinite(Number(nowMs))) throw new Error("PHASE5_PART2_STEP_INPUT_INVALID");
      renderer.draw(Number(nowMs));
      return snapshot();
    },
    dock() {
      const renderer = globalThis.__phase5Part2Renderer;
      if (!renderer) throw new Error("PHASE5_PART2_DOCK_BEFORE_BOOT");
      return {entered: Boolean(renderer.enterShuttleBayPlayerControl(true)), ...snapshot()};
    },
    approachTerminal(id) {
      const renderer = globalThis.__phase5Part2Renderer;
      if (!renderer?.isShuttleBayPlayerControlActive?.()) throw new Error("PHASE5_PART2_NOT_ON_FOOT");
      const terminal = renderer.shipInteractionZones().find((zone) => String(zone.id) === String(id));
      if (!terminal) throw new Error(`PHASE5_PART2_AUTHORED_TERMINAL_MISSING: ${String(id)}`);
      // Fast-forward walking only: use real authored terminal coordinates, real
      // room selection, and the actual keyboard interaction handler afterward.
      renderer.camera = [Number(terminal.position[0]), Number(renderer.movement.eyeHeight), Number(terminal.position[1])];
      renderer.setLook(0, -2);
      renderer.syncShipLocationFromCamera(true);
      return snapshot();
    },
    snapshot,
  });
})();
