;(function () {
  const SCHEMA = "game.bridgeViewscreenRenderer.v1";
  const PRESENTATION_SCHEMA = "game.bridgeViewscreenPresentation.v1";
  const RESULT_SCHEMA = "game.bridgeViewscreenRenderResult.v1";

  const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : Number(fallback);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, finite(value)));
  const clamp01 = (value) => clamp(value, 0, 1);

  function requireBuilder(builder) {
    for (const method of ["color", "box", "beam", "ellipsoid"]) {
      if (!builder || typeof builder[method] !== "function") {
        throw new Error(`BRIDGE_VIEWSCREEN_RENDERER_BUILDER_METHOD_MISSING: ${method}`);
      }
    }
    return builder;
  }

  const MODES = Object.freeze(["encounter", "planet", "warp-transit", "astrometric", "idle"]);

  function requirePresentation(presentation) {
    if (!presentation || presentation.schema !== PRESENTATION_SCHEMA) {
      throw new Error(`BRIDGE_VIEWSCREEN_RENDERER_PRESENTATION_SCHEMA: expected ${PRESENTATION_SCHEMA}`);
    }
    const mode = String(presentation.mode || "");
    if (!MODES.includes(mode)) {
      throw new Error(`BRIDGE_VIEWSCREEN_RENDERER_MODE_NOT_IMPLEMENTED: ${mode || "<empty>"}`);
    }
    return presentation;
  }

  function surfaceGeometry(surface = {}) {
    const position = Array.isArray(surface.position) ? surface.position.map(Number) : [0, -39.12];
    const size = Array.isArray(surface.size) ? surface.size.map(Number) : [6.9, 2.1, 0.08];
    const centerX = Number.isFinite(position[0]) ? position[0] : 0;
    const centerZ = Number.isFinite(position[1]) ? position[1] : -39.12;
    const width = Math.max(1.0, Number.isFinite(size[0]) ? size[0] : 6.9);
    const height = Math.max(0.65, Number.isFinite(size[1]) ? size[1] : 2.1);
    const depth = Math.max(0.02, Number.isFinite(size[2]) ? size[2] : 0.08);
    const x0 = centerX - width / 2;
    const x1 = centerX + width / 2;
    const y0 = 0.18;
    const y1 = y0 + height;
    return Object.freeze({
      centerX,
      centerY: y0 + height / 2,
      centerZ,
      width,
      height,
      depth,
      x0,
      x1,
      y0,
      y1,
      frontZ: centerZ + depth,
      displayZ: centerZ + depth * 2,
    });
  }

  function screenPoint(frame, screen, zOffset = 0) {
    return [
      frame.centerX + clamp(screen?.xNormalized, -1, 1) * frame.width * 0.5,
      frame.centerY + clamp(screen?.yNormalized, -1, 1) * frame.height * 0.5,
      frame.displayZ + zOffset,
    ];
  }

  function renderPoweredOff(builder, frame) {
    const darkGlass = builder.color("#010204");
    builder.box(
      [frame.x0, frame.y0, frame.centerZ - frame.depth / 2],
      [frame.x1, frame.y1, frame.centerZ + frame.depth / 2],
      darkGlass
    );
  }

  function renderFrame(builder, frame, surface) {
    const glass = builder.color("#06111f");
    const grid = builder.color("#0e7490", true);
    const glow = builder.color(surface?.color || "#38bdf8", true);
    const px = (ratio) => frame.x0 + frame.width * ratio;
    const py = (ratio) => frame.y0 + frame.height * ratio;

    builder.box(
      [frame.x0, frame.y0, frame.centerZ - frame.depth / 2],
      [frame.x1, frame.y1, frame.centerZ + frame.depth / 2],
      glass
    );
    builder.beam([px(0.022), py(0.076), frame.frontZ], [px(0.978), py(0.076), frame.frontZ], 0.022, grid);
    builder.beam([px(0.022), py(0.914), frame.frontZ], [px(0.978), py(0.914), frame.frontZ], 0.022, grid);
    builder.beam([px(0.03), py(0.114), frame.frontZ], [px(0.03), py(0.867), frame.frontZ], 0.018, glow);
    builder.beam([px(0.97), py(0.114), frame.frontZ], [px(0.97), py(0.867), frame.frontZ], 0.018, glow);
    [0.196, 0.5, 0.804].forEach((ratio) => {
      builder.beam([px(ratio), py(0.114), frame.displayZ], [px(ratio), py(0.876), frame.displayZ], 0.005, grid);
    });
    [0.305, 0.524, 0.743].forEach((ratio) => {
      builder.beam([px(0.051), py(ratio), frame.displayZ], [px(0.949), py(ratio), frame.displayZ], 0.005, grid);
    });
  }

  function renderOwnShip(builder, frame, ownShip) {
    if (!ownShip?.visible) return null;
    const center = screenPoint(frame, ownShip.screen, frame.depth * 0.5);
    const glow = builder.color("#bae6fd", true);
    builder.ellipsoid(center, [frame.width * 0.018, frame.height * 0.045, frame.depth * 0.42], 12, 6, glow);
    builder.beam(
      [center[0] - frame.width * 0.025, center[1], frame.displayZ],
      [center[0] + frame.width * 0.025, center[1], frame.displayZ],
      0.01,
      glow
    );
    return center;
  }

  function renderTargetHull(builder, frame, target, center) {
    if (!target?.visible || target.visualState === "destroyed") return;
    const damaged = target.visualState === "damaged" || finite(target.hullFraction, 1) < 0.999;
    const hull = builder.color(damaged ? "#854d0e" : "#365314");
    const dark = builder.color("#111827");
    const alert = builder.color(target.relationship === "hostile" ? "#ef4444" : "#fbbf24", true);
    const scale = 0.72;
    const x = (ratio) => center[0] + frame.width * ratio * scale;
    const y = (ratio) => center[1] + frame.height * ratio * scale;
    builder.ellipsoid([center[0], center[1], frame.displayZ], [frame.width * 0.029 * scale, frame.height * 0.2 * scale, frame.depth * 0.62], 14, 6, dark);
    builder.ellipsoid([x(-0.07), center[1], frame.displayZ + frame.depth * 0.12], [frame.width * 0.072 * scale, frame.height * 0.076 * scale, frame.depth * 0.62], 14, 5, hull);
    builder.ellipsoid([x(0.07), center[1], frame.displayZ + frame.depth * 0.12], [frame.width * 0.072 * scale, frame.height * 0.076 * scale, frame.depth * 0.62], 14, 5, hull);
    builder.box([x(-0.017), y(-0.076), frame.displayZ + frame.depth * 0.88], [x(0.017), y(0.076), frame.displayZ + frame.depth * 1.38], alert);
  }

  function renderTargetLock(builder, frame, target, center, simulationSeconds) {
    if (!target?.visible || !target.lock?.acquired) return;
    const hostile = target.relationship === "hostile";
    const lockGlow = builder.color(hostile ? "#ef4444" : "#fbbf24", true);
    const pulse = 0.5 + 0.5 * Math.sin(finite(simulationSeconds) * Math.PI * 3.5);
    const bracketX = frame.width * 0.105;
    const bracketY = frame.height * 0.18;
    const cornerX = frame.width * 0.042;
    const cornerY = frame.height * 0.07;
    const z = frame.displayZ + frame.depth * 0.25;
    const thickness = 0.018 + pulse * 0.012;
    const x = center[0];
    const y = center[1];
    builder.beam([x-bracketX,y-bracketY,z],[x-bracketX+cornerX,y-bracketY,z],thickness,lockGlow);
    builder.beam([x+bracketX-cornerX,y-bracketY,z],[x+bracketX,y-bracketY,z],thickness,lockGlow);
    builder.beam([x-bracketX,y+bracketY,z],[x-bracketX+cornerX,y+bracketY,z],thickness,lockGlow);
    builder.beam([x+bracketX-cornerX,y+bracketY,z],[x+bracketX,y+bracketY,z],thickness,lockGlow);
    builder.beam([x-bracketX,y-bracketY,z],[x-bracketX,y-bracketY+cornerY,z],thickness,lockGlow);
    builder.beam([x+bracketX,y-bracketY,z],[x+bracketX,y-bracketY+cornerY,z],thickness,lockGlow);
    builder.beam([x-bracketX,y+bracketY-cornerY,z],[x-bracketX,y+bracketY,z],thickness,lockGlow);
    builder.beam([x+bracketX,y+bracketY-cornerY,z],[x+bracketX,y+bracketY,z],thickness,lockGlow);
  }

  function renderProjectiles(builder, frame, effects) {
    const glow = builder.color("#f97316", true);
    (effects?.projectiles || []).forEach((projectile) => {
      const from = screenPoint(frame, projectile.from, frame.depth * 0.75);
      const current = screenPoint(frame, projectile.screen, frame.depth * 2.25);
      const progress = clamp01(projectile.progress);
      builder.beam(from, current, 0.016 + (1 - progress) * 0.02, glow);
    });
  }

  function renderImpacts(builder, frame, effects) {
    const flash = builder.color("#fef3c7", true);
    (effects?.impacts || []).forEach((impact) => {
      const center = screenPoint(frame, impact.screen, frame.depth * 2.7);
      const progress = clamp01(impact.progress);
      const scale = 1 - progress * 0.55;
      builder.ellipsoid(center, [frame.width * 0.026 * scale, frame.height * 0.07 * scale, frame.depth * (2.0 + scale)], 12, 6, flash);
    });
  }

  function renderExplosions(builder, frame, effects) {
    const outer = builder.color("#ef4444", true);
    const fire = builder.color("#fb923c", true);
    const core = builder.color("#fff7ed", true);
    (effects?.explosions || []).forEach((explosion) => {
      const center = screenPoint(frame, explosion.screen, frame.depth * 2.4);
      const progress = clamp01(explosion.progress);
      const blast = 0.22 + progress * 0.78;
      builder.ellipsoid(center, [frame.width*(0.025+blast*0.045),frame.height*(0.06+blast*0.13),frame.depth*(2.1+blast*4.2)],18,9,outer);
      builder.ellipsoid(center, [frame.width*(0.018+blast*0.032),frame.height*(0.04+blast*0.09),frame.depth*(2.5+blast*3.0)],16,8,fire);
      builder.ellipsoid(center, [frame.width*(0.01+blast*0.018),frame.height*(0.025+blast*0.05),frame.depth*(2.8+blast*2.0)],14,7,core);
    });
  }

  function renderDebris(builder, frame, effects) {
    const hull = builder.color("#475569");
    const dark = builder.color("#1f2937");
    const fragments = [[-0.16,-0.17,0.028],[-0.11,0.19,0.022],[0.13,-0.14,0.026],[0.18,0.16,0.021],[-0.22,0.05,0.018],[0.23,-0.01,0.019]];
    (effects?.debris || []).forEach((debris) => {
      const center = screenPoint(frame, debris.screen, frame.depth * 1.5);
      const progress = clamp01(debris.progress);
      const spread = 0.35 + progress;
      fragments.forEach(([dx,dy,size], index) => {
        const fx = center[0] + frame.width * dx * spread;
        const fy = center[1] + frame.height * dy * spread;
        const sx = frame.width * size;
        const sy = frame.height * size * 1.7;
        builder.box(
          [fx-sx,fy-sy,frame.displayZ + frame.depth*(1.4+index*0.15)],
          [fx+sx,fy+sy,frame.displayZ + frame.depth*(2.2+index*0.2)],
          index % 2 ? hull : dark
        );
      });
    });
  }

  function renderTelemetry(builder, frame, presentation) {
    const hostile = presentation.target?.relationship === "hostile";
    const alert = builder.color(hostile ? "#ef4444" : "#fbbf24", true);
    const x0 = frame.centerX - frame.width * 0.174;
    const hullFraction = clamp01(presentation.target?.hullFraction);
    if (hullFraction > 0) {
      builder.box(
        [x0, frame.y0, frame.displayZ + frame.depth * 0.25],
        [x0 + frame.width * 0.348 * hullFraction, frame.y0 + frame.height * 0.033, frame.displayZ + frame.depth * 1.12],
        alert
      );
    }
    const rangeRatio = Math.max(0.05, Math.min(1, finite(presentation.telemetry?.rangeM) / 3500));
    const px = (ratio) => frame.x0 + frame.width * ratio;
    const py = (ratio) => frame.y0 + frame.height * ratio;
    builder.box(
      [px(0.075), py(0.86), frame.displayZ + frame.depth * 0.4],
      [px(0.075 + 0.22 * rangeRatio), py(0.885), frame.displayZ + frame.depth * 1.0],
      alert
    );
    if (String(presentation.encounter?.phase || "") === "combat") {
      builder.beam([px(0.72),py(0.16),frame.displayZ],[px(0.92),py(0.16),frame.displayZ],0.018,alert);
    }
  }

  function renderEncounter(builder, surface, presentation) {
    const frame = surfaceGeometry(surface);
    renderFrame(builder, frame, surface);
    const ownShipCenter = renderOwnShip(builder, frame, presentation.ownShip);
    const targetCenter = screenPoint(frame, presentation.target?.screen, 0);
    renderTargetHull(builder, frame, presentation.target, targetCenter);
    renderTargetLock(builder, frame, presentation.target, targetCenter, presentation.time?.simulationSeconds);
    renderProjectiles(builder, frame, presentation.effects);
    renderImpacts(builder, frame, presentation.effects);
    renderExplosions(builder, frame, presentation.effects);
    renderDebris(builder, frame, presentation.effects);
    renderTelemetry(builder, frame, presentation);

    return Object.freeze({
      schema: RESULT_SCHEMA,
      mode: presentation.mode,
      powered: true,
      simulationSeconds: finite(presentation.time?.simulationSeconds),
      ownShipCenter: ownShipCenter ? Object.freeze(ownShipCenter.slice()) : null,
      targetCenter: Object.freeze(targetCenter.slice()),
    });
  }

  function renderPlanetBody(builder, frame, planet, center, radius) {
    const visual = planet?.visual || {};
    const glass = builder.color("#030712");
    const atmosphere = builder.color(visual.atmosphereColor || "#67e8f9", true);
    const surface = builder.color(visual.surfaceColor || "#2563eb");
    const secondary = builder.color(visual.secondaryColor || "#16a34a");
    const clouds = builder.color(visual.cloudColor || "#f8fafc", true);
    const darkSide = builder.color("#0f172a");
    const rings = visual.rings || {};
    if (rings.enabled) {
      const outer = clamp(rings.outerRadius || 1.75, 1.18, 2.2);
      const inner = clamp(rings.innerRadius || 1.35, 1.05, outer - 0.08);
      const tilt = clamp(finite(rings.tiltDegrees) / 90, -0.65, 0.65);
      builder.ellipsoid(
        [center[0], center[1] + radius * tilt * 0.12, frame.displayZ - frame.depth * 0.12],
        [radius * outer, Math.max(radius * 0.055, radius * 0.12 * Math.abs(tilt)), frame.depth * 0.75],
        28, 5, builder.color(rings.color || "#94a3b8", true)
      );
      builder.ellipsoid(
        [center[0], center[1] + radius * tilt * 0.12, frame.displayZ + frame.depth * 0.02],
        [radius * inner, Math.max(radius * 0.035, radius * 0.07 * Math.abs(tilt)), frame.depth * 0.92],
        28, 5, glass
      );
    }
    builder.ellipsoid(center, [radius * 1.09, radius * 1.09, frame.depth * 0.8], 28, 14, atmosphere);
    builder.ellipsoid([center[0], center[1], frame.displayZ + frame.depth * 0.12], [radius, radius, frame.depth * 0.95], 28, 14, surface);
    builder.ellipsoid([center[0] - radius * 0.17, center[1] + radius * 0.1, frame.displayZ + frame.depth * 0.75], [radius * 0.48, radius * 0.31, frame.depth * 0.38], 16, 8, secondary);
    builder.ellipsoid([center[0] + radius * 0.26, center[1] - radius * 0.2, frame.displayZ + frame.depth * 0.78], [radius * 0.31, radius * 0.2, frame.depth * 0.35], 14, 7, secondary);
    builder.ellipsoid([center[0] + radius * 0.48, center[1], frame.displayZ + frame.depth * 0.72], [radius * 0.62, radius * 1.02, frame.depth * 0.42], 22, 12, darkSide);
    const pulse = 0.5 + 0.5 * Math.sin(finite(planet?.simulationSeconds) * 2.3);
    [-0.28, 0.08, 0.34].forEach((offset, index) => {
      builder.beam(
        [center[0] - radius * (0.78 - index * 0.08), center[1] + radius * offset, frame.displayZ + frame.depth * 1.08],
        [center[0] + radius * (0.45 + index * 0.06), center[1] + radius * (offset + 0.04), frame.displayZ + frame.depth * 1.08],
        0.01 + pulse * 0.004,
        clouds
      );
    });
  }

  function renderTracking(builder, frame, center, radius, active, simulationSeconds, colorValue) {
    const glow = builder.color(active ? "#86efac" : (colorValue || "#67e8f9"), true);
    const scanPulse = 0.5 + 0.5 * Math.sin(finite(simulationSeconds) * 5.55);
    if (active) {
      const thickness = 0.016 + scanPulse * 0.012;
      const span = radius * 1.2;
      const inner = radius * 0.72;
      builder.beam([center[0]-span,center[1]-span,frame.displayZ+frame.depth*1.2],[center[0]-inner,center[1]-span,frame.displayZ+frame.depth*1.2],thickness,glow);
      builder.beam([center[0]+inner,center[1]-span,frame.displayZ+frame.depth*1.2],[center[0]+span,center[1]-span,frame.displayZ+frame.depth*1.2],thickness,glow);
      builder.beam([center[0]-span,center[1]+span,frame.displayZ+frame.depth*1.2],[center[0]-inner,center[1]+span,frame.displayZ+frame.depth*1.2],thickness,glow);
      builder.beam([center[0]+inner,center[1]+span,frame.displayZ+frame.depth*1.2],[center[0]+span,center[1]+span,frame.displayZ+frame.depth*1.2],thickness,glow);
    } else {
      const scanX = frame.x0 + frame.width * (0.1 + scanPulse * 0.8);
      builder.beam([scanX, frame.y0 + frame.height*0.13, frame.displayZ + frame.depth], [scanX, frame.y0 + frame.height*0.91, frame.displayZ + frame.depth], 0.012, glow);
    }
  }

  function renderPlanet(builder, surface, presentation) {
    const frame = surfaceGeometry(surface);
    renderFrame(builder, frame, surface);
    (presentation.stars || []).forEach((star) => {
      const center = screenPoint(frame, star.screen, -frame.depth * 0.45);
      const size = frame.height * clamp(star.sizeNormalized, 0.002, 0.02);
      builder.box([center[0]-size,center[1]-size,center[2]],[center[0]+size,center[1]+size,center[2]+frame.depth*0.3],builder.color(star.visualState === "blue-white" ? "#bae6fd" : "#f8fafc", true));
    });
    const planet = presentation.planet || {};
    const center = screenPoint(frame, planet.screen, 0);
    const radius = frame.height * clamp(planet.radiusHeightFraction, 0.05, 0.7);
    renderPlanetBody(builder, frame, {...planet, simulationSeconds: presentation.time?.simulationSeconds}, center, radius);
    (planet.moons || []).forEach((moon) => {
      const orbit = radius * finite(moon.orbitScale, 1.45);
      const angle = finite(moon.angleRadians);
      const moonRadius = radius * finite(moon.radiusScale, 0.075);
      builder.ellipsoid(
        [center[0] + Math.cos(angle)*orbit, center[1] + Math.sin(angle)*orbit*0.42, frame.displayZ + frame.depth*0.92],
        [moonRadius, moonRadius, frame.depth*0.28], 10, 6,
        builder.color(moon.visualState === "slate" ? "#94a3b8" : "#cbd5e1")
      );
    });
    renderTracking(builder, frame, center, radius, Boolean(presentation.tracking?.active), presentation.time?.simulationSeconds, planet.visual?.atmosphereColor);
    return Object.freeze({schema: RESULT_SCHEMA, mode: presentation.mode, powered: true, simulationSeconds: finite(presentation.time?.simulationSeconds), ownShipCenter: null, targetCenter: Object.freeze(center.slice())});
  }

  function renderPlanetMarker(builder, frame, marker, x, y, radius, glowFallback) {
    if (!marker || radius <= 0.012) return;
    const visual = marker.visual || {};
    const glow = builder.color(visual.atmosphereColor || glowFallback, true);
    const surface = builder.color(visual.surfaceColor || "#2563eb");
    const secondary = builder.color(visual.secondaryColor || "#16a34a");
    builder.ellipsoid([x,y,frame.displayZ+frame.depth*1.3],[radius*1.1,radius*1.1,frame.depth*0.45],18,9,glow);
    builder.ellipsoid([x,y,frame.displayZ+frame.depth*1.55],[radius,radius,frame.depth*0.52],18,9,surface);
    builder.ellipsoid([x-radius*0.2,y+radius*0.08,frame.displayZ+frame.depth*1.9],[radius*0.44,radius*0.27,frame.depth*0.24],12,6,secondary);
  }

  function renderWarpTransit(builder, surface, presentation) {
    const frame = surfaceGeometry(surface);
    renderFrame(builder, frame, surface);
    const warp = presentation.warp || {};
    const phase = String(warp.phase || "in-warp");
    const progress = clamp01(warp.progress);
    const clock = finite(presentation.time?.simulationSeconds);
    const cyan = builder.color("#67e8f9", true);
    const white = builder.color("#f8fafc", true);
    const amber = builder.color("#fde68a", true);
    const destinationGlow = warp.destinationPlanet?.visual?.atmosphereColor || "#a5f3fc";
    const originGlow = warp.originPlanet?.visual?.atmosphereColor || "#93c5fd";
    const tunnelCenter = [frame.centerX, frame.y0 + frame.height*0.52, frame.displayZ + frame.depth*0.2];
    const phaseSpeed = phase === "warp-charging" ? 0.38 : phase === "arriving" ? 0.72 : 1.7;
    const phaseLength = phase === "warp-charging" ? 0.065 : phase === "arriving" ? 0.13 : 0.24;
    for (let index=0; index<34; index+=1) {
      const angle=index*2.399963229728653;
      const lane=(index%7)/7;
      const travel=(clock*phaseSpeed+index*0.071+lane*0.19)%1;
      const rs=frame.width*(0.018+travel*0.36);
      const re=rs+frame.width*phaseLength*(0.35+travel*0.95);
      const start=[tunnelCenter[0]+Math.cos(angle)*rs,tunnelCenter[1]+Math.sin(angle)*rs*0.31,frame.displayZ];
      const end=[tunnelCenter[0]+Math.cos(angle)*re,tunnelCenter[1]+Math.sin(angle)*re*0.31,frame.displayZ+frame.depth*(0.2+travel)];
      builder.beam(start,end,0.006+travel*0.012,index%5===0?white:cyan);
    }
    const pulse=0.5+0.5*Math.sin(clock*8.5);
    [0.11,0.19,0.28].forEach((scale,index)=>{
      builder.ellipsoid([tunnelCenter[0],tunnelCenter[1],frame.displayZ-frame.depth*(0.2+index*0.08)],[frame.width*scale,frame.height*scale*0.55,frame.depth*0.2],28,5,index===2?builder.color(destinationGlow,true):cyan);
      builder.ellipsoid([tunnelCenter[0],tunnelCenter[1],frame.displayZ+frame.depth*0.02],[frame.width*Math.max(0.01,scale-0.012-pulse*0.003),frame.height*Math.max(0.01,scale-0.012)*0.55,frame.depth*0.24],28,5,builder.color("#020617"));
    });
    const px=(ratio)=>frame.x0+frame.width*ratio;
    const py=(ratio)=>frame.y0+frame.height*ratio;
    if (phase === "warp-charging") {
      const charge=clamp01(progress/0.18);
      renderPlanetMarker(builder,frame,warp.originPlanet,px(0.5-charge*0.32),py(0.52),frame.height*(0.23-charge*0.16),originGlow);
    } else if (phase === "arriving") {
      const arrival=clamp01((progress-0.82)/0.18);
      renderPlanetMarker(builder,frame,warp.destinationPlanet,px(0.68-arrival*0.18),py(0.52),frame.height*(0.055+arrival*0.25),destinationGlow);
    } else {
      renderPlanetMarker(builder,frame,warp.originPlanet,px(0.13),py(0.79),frame.height*0.035,originGlow);
      renderPlanetMarker(builder,frame,warp.destinationPlanet,px(0.87),py(0.21),frame.height*0.048,destinationGlow);
    }
    builder.box([px(0.11),py(0.865),frame.displayZ+frame.depth],[px(0.89),py(0.89),frame.displayZ+frame.depth*1.35],builder.color("#0f172a"));
    if (progress>0.001) builder.box([px(0.11),py(0.865),frame.displayZ+frame.depth*1.4],[px(0.11)+frame.width*0.78*progress,py(0.89),frame.displayZ+frame.depth*1.8],phase==="arriving"?amber:cyan);
    const markerX=phase==="warp-charging"?px(0.18):phase==="arriving"?px(0.82):px(0.5);
    builder.beam([markerX,py(0.13),frame.displayZ+frame.depth],[markerX,py(0.2),frame.displayZ+frame.depth*1.4],0.016+pulse*0.007,phase==="arriving"?builder.color(destinationGlow,true):cyan);
    return Object.freeze({schema: RESULT_SCHEMA, mode: presentation.mode, powered: true, simulationSeconds: clock, ownShipCenter: null, targetCenter: null});
  }

  function renderAstrometric(builder, surface, presentation) {
    const frame = surfaceGeometry(surface);
    renderFrame(builder, frame, surface);
    (presentation.catalog?.stars || []).forEach((star) => {
      const center=screenPoint(frame,star.screen,-frame.depth*0.42);
      const radius=frame.height*clamp(star.radiusHeightFraction,0.003,0.13);
      builder.ellipsoid(center,[radius,radius,frame.depth*0.24],star.local?12:7,star.local?6:4,builder.color(star.color||"#f8fafc",true));
    });
    const target=presentation.targetObject||{};
    const center=screenPoint(frame,target.screen,0);
    const radius=frame.height*clamp(target.radiusHeightFraction,0.05,0.5);
    renderPlanetBody(builder,frame,{...target,simulationSeconds:presentation.time?.simulationSeconds},center,radius);
    (presentation.catalog?.localBodies || []).forEach((body)=>{
      const bodyCenter=screenPoint(frame,body.screen,frame.depth*0.9);
      const bodyRadius=frame.height*clamp(body.radiusHeightFraction,0.006,0.12);
      builder.ellipsoid(bodyCenter,[bodyRadius,bodyRadius,frame.depth*0.28],10,6,builder.color(body.color||"#94a3b8"));
    });
    renderTracking(builder,frame,center,radius,Boolean(presentation.tracking?.active),presentation.time?.simulationSeconds,target.visual?.atmosphereColor);
    return Object.freeze({schema: RESULT_SCHEMA, mode: presentation.mode, powered: true, simulationSeconds: finite(presentation.time?.simulationSeconds), ownShipCenter: null, targetCenter: Object.freeze(center.slice())});
  }

  function renderIdle(builder, surface, presentation) {
    const frame=surfaceGeometry(surface);
    renderFrame(builder,frame,surface);
    const pulse=0.5+0.5*Math.sin(finite(presentation.time?.simulationSeconds)*2.2);
    const glow=builder.color("#38bdf8",true);
    const y=frame.centerY;
    builder.beam([frame.centerX-frame.width*0.16,y,frame.displayZ],[frame.centerX+frame.width*0.16,y,frame.displayZ],0.008+pulse*0.008,glow);
    builder.box([frame.centerX-frame.width*0.018,y-frame.height*0.055,frame.displayZ],[frame.centerX+frame.width*0.018,y+frame.height*0.055,frame.displayZ+frame.depth],glow);
    return Object.freeze({schema: RESULT_SCHEMA, mode: presentation.mode, powered: true, simulationSeconds: finite(presentation.time?.simulationSeconds), ownShipCenter: null, targetCenter: null});
  }

  function render({builder, surface, presentation} = {}) {
    requireBuilder(builder);
    const framePresentation = requirePresentation(presentation);
    const surfaceSpec = surface || {};
    if (framePresentation.display?.powered === false) {
      const frame = surfaceGeometry(surfaceSpec);
      renderPoweredOff(builder, frame);
      return Object.freeze({schema: RESULT_SCHEMA, mode: framePresentation.mode, powered: false, simulationSeconds: finite(framePresentation.time?.simulationSeconds), ownShipCenter: null, targetCenter: null});
    }
    switch (framePresentation.mode) {
      case "encounter": return renderEncounter(builder, surfaceSpec, framePresentation);
      case "planet": return renderPlanet(builder, surfaceSpec, framePresentation);
      case "warp-transit": return renderWarpTransit(builder, surfaceSpec, framePresentation);
      case "astrometric": return renderAstrometric(builder, surfaceSpec, framePresentation);
      case "idle": return renderIdle(builder, surfaceSpec, framePresentation);
      default: throw new Error(`BRIDGE_VIEWSCREEN_RENDERER_MODE_NOT_IMPLEMENTED: ${String(framePresentation.mode || "<empty>")}`);
    }
  }

  globalThis.MainComputerBridgeViewscreenRenderer = Object.freeze({
    SCHEMA,
    PRESENTATION_SCHEMA,
    RESULT_SCHEMA,
    render,
  });
})();
