;(function () {
  // Patch S extracts content-defined viewscreen render passes from scene-viewer.js.
  // Patch P renders content-defined viewscreens/displays from prop.display metadata.
  const modules = globalThis.MainComputerShuttle3DRendererModules;
  if (!modules || typeof modules.register !== "function") return;

  modules.register("viewscreens", {
        appendMotherShipViewscreenDisplay(builder, prop, nowMs = 0) {
          // Content-defined viewscreens resolve their render program from prop.display.
          // systemPlanet is stateful: the opening encounter shows the raider, warp shows transit, and post-warp systems show planets.
          const display = String(prop?.display || "").toLowerCase();
          if (display === "systemplanet") {
            this.appendSystemPlanetDisplay(builder, prop, nowMs);
            return;
          }
          if (display === "enemyshiptactical") {
            this.appendEnemyShipTacticalDisplay(builder, prop, nowMs);
            return;
          }
          const position = Array.isArray(prop?.position) ? prop.position.map(Number) : [0, -39.12];
          const size = Array.isArray(prop?.size) ? prop.size.map(Number) : [6.9, 2.1, 0.08];
          const centerX = Number.isFinite(position[0]) ? position[0] : 0;
          const centerZ = Number.isFinite(position[1]) ? position[1] : -39.12;
          const width = Math.max(1.0, Number.isFinite(size[0]) ? size[0] : 6.9);
          const height = Math.max(0.65, Number.isFinite(size[1]) ? size[1] : 2.1);
          const depth = Math.max(0.02, Number.isFinite(size[2]) ? size[2] : 0.08);
          const glass = builder.color("#06111f");
          const glow = builder.color(prop?.color || "#38bdf8", true);
          const y0 = 0.18;
          const y1 = y0 + height;
          const x0 = centerX - width / 2;
          const x1 = centerX + width / 2;
          builder.box([x0, y0, centerZ - depth / 2], [x1, y1, centerZ + depth / 2], glass);
          builder.beam([x0 + width * 0.04, y0 + height * 0.08, centerZ + depth], [x1 - width * 0.04, y0 + height * 0.08, centerZ + depth], 0.018, glow);
          builder.beam([x0 + width * 0.04, y1 - height * 0.08, centerZ + depth], [x1 - width * 0.04, y1 - height * 0.08, centerZ + depth], 0.018, glow);
          builder.beam([x0 + width * 0.04, y0 + height * 0.1, centerZ + depth], [x0 + width * 0.04, y1 - height * 0.1, centerZ + depth], 0.014, glow);
          builder.beam([x1 - width * 0.04, y0 + height * 0.1, centerZ + depth], [x1 - width * 0.04, y1 - height * 0.1, centerZ + depth], 0.014, glow);
        },

        appendSystemPlanetDisplay(builder, prop, nowMs = 0) {
          const navigation = this.navigationSnapshot?.(nowMs) || {};
          if (navigation.travelling) {
            this.appendWarpTransitDisplay(builder, prop, nowMs, navigation);
            return;
          }
          const openingEncounterActive = Boolean(
            navigation.currentSystemId
            && navigation.currentSystemId === navigation.startSystemId
            && !navigation.lastCompletedRouteId
            && !navigation.lastArrivalAtMs
            && Number(navigation.elapsedWorldTime || 0) === 0
          );
          if (openingEncounterActive) {
            this.appendEnemyShipTacticalDisplay(builder, prop, nowMs);
            return;
          }
          const planet = navigation.currentPlanet || {};
          const position = Array.isArray(prop?.position) ? prop.position.map(Number) : [0, -39.12];
          const size = Array.isArray(prop?.size) ? prop.size.map(Number) : [6.9, 2.1, 0.08];
          const centerX = Number.isFinite(position[0]) ? position[0] : 0;
          const centerZ = Number.isFinite(position[1]) ? position[1] : -39.12;
          const width = Math.max(1.0, Number.isFinite(size[0]) ? size[0] : 6.9);
          const height = Math.max(0.65, Number.isFinite(size[1]) ? size[1] : 2.1);
          const depth = Math.max(0.02, Number.isFinite(size[2]) ? size[2] : 0.08);
          const x0 = centerX - width / 2;
          const x1 = centerX + width / 2;
          const y0 = 0.18;
          const y1 = y0 + height;
          const px = (ratio) => x0 + width * ratio;
          const py = (ratio) => y0 + height * ratio;
          const frontZ = centerZ + depth;
          const displayZ = centerZ + depth * 2.1;
          const color = (value, fallback, emissive = false) => builder.color(
            /^#[0-9a-f]{6}$/i.test(String(value || "")) ? String(value) : fallback,
            emissive
          );

          const glass = builder.color("#030712");
          const grid = builder.color("#0e7490", true);
          const frame = color(prop?.color, "#38bdf8", true);
          const atmosphere = color(planet.atmosphereColor, "#67e8f9", true);
          const surface = color(planet.surfaceColor, "#2563eb");
          const secondary = color(planet.secondaryColor, "#16a34a");
          const clouds = color(planet.cloudColor, "#f8fafc", true);
          const darkSide = builder.color("#0f172a");
          const ringColor = color(planet.rings?.color, "#94a3b8", true);
          const tracked = this.bridgeViewscreenTrackingActive?.() || Boolean(this.shipState?.flags?.currentSystemPlanetSurveyed);
          const pulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 430);
          const scanPulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 180);
          const radiusScale = Math.max(0.68, Math.min(1.38, Number(planet.radiusScale) || 1));
          const radius = Math.min(width * 0.145, height * 0.39) * radiusScale;
          const planetCenter = [centerX + width * 0.04, py(0.53), displayZ];

          builder.box([x0, y0, centerZ - depth / 2], [x1, y1, centerZ + depth / 2], glass);
          builder.beam([px(0.025), py(0.075), frontZ], [px(0.975), py(0.075), frontZ], 0.02, grid);
          builder.beam([px(0.025), py(0.925), frontZ], [px(0.975), py(0.925), frontZ], 0.02, grid);
          builder.beam([px(0.03), py(0.11), frontZ], [px(0.03), py(0.89), frontZ], 0.016, frame);
          builder.beam([px(0.97), py(0.11), frontZ], [px(0.97), py(0.89), frontZ], 0.016, frame);

          let seed = 2166136261;
          String(planet.id || navigation.currentSystemId || "planet").split("").forEach((character) => {
            seed ^= character.charCodeAt(0);
            seed = Math.imul(seed, 16777619) >>> 0;
          });
          const random = () => {
            seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
            return seed / 4294967296;
          };
          for (let index = 0; index < 18; index += 1) {
            const sx = px(0.08 + random() * 0.84);
            const sy = py(0.14 + random() * 0.72);
            const starSize = 0.006 + random() * 0.009;
            builder.box(
              [sx - starSize, sy - starSize, displayZ - depth * 0.65],
              [sx + starSize, sy + starSize, displayZ - depth * 0.35],
              builder.color(index % 3 === 0 ? "#bae6fd" : "#f8fafc", true)
            );
          }

          const rings = planet.rings || {};
          if (rings.enabled) {
            const outer = Math.max(1.18, Math.min(2.2, Number(rings.outerRadius) || 1.75));
            const inner = Math.max(1.05, Math.min(outer - 0.08, Number(rings.innerRadius) || 1.35));
            const tilt = Math.max(-0.65, Math.min(0.65, Number(rings.tiltDegrees || 0) / 90));
            builder.ellipsoid(
              [planetCenter[0], planetCenter[1] + radius * tilt * 0.12, displayZ - depth * 0.12],
              [radius * outer, Math.max(radius * 0.055, radius * 0.12 * Math.abs(tilt)), depth * 0.75],
              28,
              5,
              ringColor
            );
            builder.ellipsoid(
              [planetCenter[0], planetCenter[1] + radius * tilt * 0.12, displayZ + depth * 0.02],
              [radius * inner, Math.max(radius * 0.035, radius * 0.07 * Math.abs(tilt)), depth * 0.92],
              28,
              5,
              glass
            );
          }

          builder.ellipsoid(planetCenter, [radius * 1.09, radius * 1.09, depth * 0.8], 28, 14, atmosphere);
          builder.ellipsoid(
            [planetCenter[0], planetCenter[1], displayZ + depth * 0.12],
            [radius, radius, depth * 0.95],
            28,
            14,
            surface
          );
          builder.ellipsoid(
            [planetCenter[0] - radius * 0.17, planetCenter[1] + radius * 0.1, displayZ + depth * 0.75],
            [radius * 0.48, radius * 0.31, depth * 0.38],
            16,
            8,
            secondary
          );
          builder.ellipsoid(
            [planetCenter[0] + radius * 0.26, planetCenter[1] - radius * 0.2, displayZ + depth * 0.78],
            [radius * 0.31, radius * 0.2, depth * 0.35],
            14,
            7,
            secondary
          );
          builder.ellipsoid(
            [planetCenter[0] + radius * 0.48, planetCenter[1], displayZ + depth * 0.72],
            [radius * 0.62, radius * 1.02, depth * 0.42],
            22,
            12,
            darkSide
          );
          [-0.28, 0.08, 0.34].forEach((offset, index) => {
            builder.beam(
              [planetCenter[0] - radius * (0.78 - index * 0.08), planetCenter[1] + radius * offset, displayZ + depth * 1.08],
              [planetCenter[0] + radius * (0.45 + index * 0.06), planetCenter[1] + radius * (offset + 0.04), displayZ + depth * 1.08],
              0.01 + pulse * 0.004,
              clouds
            );
          });

          const moonCount = Math.max(0, Math.min(6, Number(planet.moonCount) || 0));
          for (let index = 0; index < moonCount; index += 1) {
            const angle = (index / Math.max(1, moonCount)) * Math.PI * 1.65 + 0.35;
            const orbit = radius * (1.45 + index * 0.12);
            const moonRadius = radius * (0.075 + (index % 2) * 0.018);
            builder.ellipsoid(
              [
                planetCenter[0] + Math.cos(angle) * orbit,
                planetCenter[1] + Math.sin(angle) * orbit * 0.42,
                displayZ + depth * 0.92
              ],
              [moonRadius, moonRadius, depth * 0.28],
              10,
              6,
              builder.color(index % 2 ? "#94a3b8" : "#cbd5e1")
            );
          }

          const statusColor = tracked ? builder.color("#86efac", true) : atmosphere;
          builder.box([px(0.075), py(0.145), displayZ + depth * 0.55], [px(0.27), py(0.18), displayZ + depth * 1.15], surface);
          builder.box([px(0.075), py(0.195), displayZ + depth * 0.55], [px(0.225), py(0.225), displayZ + depth * 1.15], secondary);
          builder.box([px(0.73), py(0.82), displayZ + depth * 0.55], [px(0.925), py(0.855), displayZ + depth * 1.15], atmosphere);
          builder.box([px(0.775), py(0.87), displayZ + depth * 0.55], [px(0.925), py(0.9), displayZ + depth * 1.15], clouds);

          if (tracked) {
            const thickness = 0.016 + scanPulse * 0.012;
            builder.beam([planetCenter[0] - radius * 1.2, planetCenter[1] - radius * 1.2, displayZ + depth * 1.2], [planetCenter[0] - radius * 0.72, planetCenter[1] - radius * 1.2, displayZ + depth * 1.2], thickness, statusColor);
            builder.beam([planetCenter[0] + radius * 0.72, planetCenter[1] - radius * 1.2, displayZ + depth * 1.2], [planetCenter[0] + radius * 1.2, planetCenter[1] - radius * 1.2, displayZ + depth * 1.2], thickness, statusColor);
            builder.beam([planetCenter[0] - radius * 1.2, planetCenter[1] + radius * 1.2, displayZ + depth * 1.2], [planetCenter[0] - radius * 0.72, planetCenter[1] + radius * 1.2, displayZ + depth * 1.2], thickness, statusColor);
            builder.beam([planetCenter[0] + radius * 0.72, planetCenter[1] + radius * 1.2, displayZ + depth * 1.2], [planetCenter[0] + radius * 1.2, planetCenter[1] + radius * 1.2, displayZ + depth * 1.2], thickness, statusColor);
          } else {
            const scanX = px(0.1 + scanPulse * 0.8);
            builder.beam([scanX, py(0.13), displayZ + depth], [scanX, py(0.91), displayZ + depth], 0.012, atmosphere);
          }
        },

        appendWarpTransitDisplay(builder, prop, nowMs = 0, navigationState = null) {
          const navigation = navigationState || this.navigationSnapshot?.(nowMs) || {};
          const position = Array.isArray(prop?.position) ? prop.position.map(Number) : [0, -39.12];
          const size = Array.isArray(prop?.size) ? prop.size.map(Number) : [6.9, 2.1, 0.08];
          const centerX = Number.isFinite(position[0]) ? position[0] : 0;
          const centerZ = Number.isFinite(position[1]) ? position[1] : -39.12;
          const width = Math.max(1.0, Number.isFinite(size[0]) ? size[0] : 6.9);
          const height = Math.max(0.65, Number.isFinite(size[1]) ? size[1] : 2.1);
          const depth = Math.max(0.02, Number.isFinite(size[2]) ? size[2] : 0.08);
          const x0 = centerX - width / 2;
          const x1 = centerX + width / 2;
          const y0 = 0.18;
          const y1 = y0 + height;
          const px = (ratio) => x0 + width * ratio;
          const py = (ratio) => y0 + height * ratio;
          const frontZ = centerZ + depth;
          const displayZ = centerZ + depth * 2.2;
          const clamp = (value, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, Number(value) || 0));
          const color = (value, fallback, emissive = false) => builder.color(
            /^#[0-9a-f]{6}$/i.test(String(value || "")) ? String(value) : fallback,
            emissive
          );

          const phase = String(navigation.travelPhase || "in-warp");
          const progress = clamp(navigation.travelProgress);
          const originPlanet = navigation.currentPlanet || {};
          const destinationPlanet = navigation.destinationPlanet || {};
          const glass = builder.color("#020617");
          const frame = color(prop?.color, "#38bdf8", true);
          const cyan = builder.color("#67e8f9", true);
          const white = builder.color("#f8fafc", true);
          const amber = builder.color("#fde68a", true);
          const destinationGlow = color(destinationPlanet.atmosphereColor, "#a5f3fc", true);
          const originGlow = color(originPlanet.atmosphereColor, "#93c5fd", true);
          const tunnelCenter = [centerX, py(0.52), displayZ];

          builder.box([x0, y0, centerZ - depth / 2], [x1, y1, centerZ + depth / 2], glass);
          builder.beam([px(0.025), py(0.075), frontZ], [px(0.975), py(0.075), frontZ], 0.02, frame);
          builder.beam([px(0.025), py(0.925), frontZ], [px(0.975), py(0.925), frontZ], 0.02, frame);
          builder.beam([px(0.03), py(0.11), frontZ], [px(0.03), py(0.89), frontZ], 0.016, frame);
          builder.beam([px(0.97), py(0.11), frontZ], [px(0.97), py(0.89), frontZ], 0.016, frame);

          const phaseSpeed = phase === "warp-charging" ? 0.38 : phase === "arriving" ? 0.72 : 1.7;
          const phaseLength = phase === "warp-charging" ? 0.065 : phase === "arriving" ? 0.13 : 0.24;
          const clock = Math.max(0, Number(nowMs) || 0) / 1000;
          for (let index = 0; index < 34; index += 1) {
            const angle = index * 2.399963229728653;
            const lane = (index % 7) / 7;
            const travel = (clock * phaseSpeed + index * 0.071 + lane * 0.19) % 1;
            const radiusStart = width * (0.018 + travel * 0.36);
            const radiusEnd = radiusStart + width * phaseLength * (0.35 + travel * 0.95);
            const verticalScale = 0.31;
            const start = [
              tunnelCenter[0] + Math.cos(angle) * radiusStart,
              tunnelCenter[1] + Math.sin(angle) * radiusStart * verticalScale,
              displayZ
            ];
            const end = [
              tunnelCenter[0] + Math.cos(angle) * radiusEnd,
              tunnelCenter[1] + Math.sin(angle) * radiusEnd * verticalScale,
              displayZ + depth * (0.2 + travel)
            ];
            builder.beam(start, end, 0.006 + travel * 0.012, index % 5 === 0 ? white : cyan);
          }

          const tunnelPulse = 0.5 + 0.5 * Math.sin(clock * 8.5);
          [0.11, 0.19, 0.28].forEach((scale, index) => {
            builder.ellipsoid(
              [tunnelCenter[0], tunnelCenter[1], displayZ - depth * (0.2 + index * 0.08)],
              [width * scale, height * scale * 0.55, depth * 0.2],
              28,
              5,
              index === 2 ? destinationGlow : cyan
            );
            builder.ellipsoid(
              [tunnelCenter[0], tunnelCenter[1], displayZ + depth * 0.02],
              [width * Math.max(0.01, scale - 0.012 - tunnelPulse * 0.003), height * Math.max(0.01, scale - 0.012) * 0.55, depth * 0.24],
              28,
              5,
              glass
            );
          });

          const drawPlanetMarker = (planet, x, y, radius, glow) => {
            if (!planet || radius <= 0.012) return;
            const surface = color(planet.surfaceColor, "#2563eb");
            const secondary = color(planet.secondaryColor, "#16a34a");
            builder.ellipsoid([x, y, displayZ + depth * 1.3], [radius * 1.1, radius * 1.1, depth * 0.45], 18, 9, glow);
            builder.ellipsoid([x, y, displayZ + depth * 1.55], [radius, radius, depth * 0.52], 18, 9, surface);
            builder.ellipsoid([x - radius * 0.2, y + radius * 0.08, displayZ + depth * 1.9], [radius * 0.44, radius * 0.27, depth * 0.24], 12, 6, secondary);
          };

          if (phase === "warp-charging") {
            const charge = clamp(progress / 0.18);
            drawPlanetMarker(originPlanet, px(0.5 - charge * 0.32), py(0.52), height * (0.23 - charge * 0.16), originGlow);
          } else if (phase === "arriving") {
            const arrival = clamp((progress - 0.82) / 0.18);
            drawPlanetMarker(destinationPlanet, px(0.68 - arrival * 0.18), py(0.52), height * (0.055 + arrival * 0.25), destinationGlow);
          } else {
            drawPlanetMarker(originPlanet, px(0.13), py(0.79), height * 0.035, originGlow);
            drawPlanetMarker(destinationPlanet, px(0.87), py(0.21), height * 0.048, destinationGlow);
          }

          const progressWidth = width * 0.78 * progress;
          builder.box([px(0.11), py(0.865), displayZ + depth * 1.0], [px(0.89), py(0.89), displayZ + depth * 1.35], builder.color("#0f172a"));
          if (progressWidth > 0.001) {
            builder.box([px(0.11), py(0.865), displayZ + depth * 1.4], [px(0.11) + progressWidth, py(0.89), displayZ + depth * 1.8], phase === "arriving" ? amber : cyan);
          }
          const phaseMarkerX = phase === "warp-charging" ? px(0.18) : phase === "arriving" ? px(0.82) : px(0.5);
          builder.beam([phaseMarkerX, py(0.13), displayZ + depth], [phaseMarkerX, py(0.2), displayZ + depth * 1.4], 0.016 + tunnelPulse * 0.007, phase === "arriving" ? destinationGlow : cyan);
        },

        appendEnemyShipTacticalDisplay(builder, prop, nowMs = 0) {
          const position = Array.isArray(prop?.position) ? prop.position.map(Number) : [0, -39.12];
          const size = Array.isArray(prop?.size) ? prop.size.map(Number) : [6.9, 2.1, 0.08];
          const centerX = Number.isFinite(position[0]) ? position[0] : 0;
          const centerZ = Number.isFinite(position[1]) ? position[1] : -39.12;
          const width = Math.max(1.0, Number.isFinite(size[0]) ? size[0] : 6.9);
          const height = Math.max(0.65, Number.isFinite(size[1]) ? size[1] : 2.1);
          const depth = Math.max(0.02, Number.isFinite(size[2]) ? size[2] : 0.08);
          const x0 = centerX - width / 2;
          const x1 = centerX + width / 2;
          const y0 = 0.18;
          const y1 = y0 + height;
          const py = (ratio) => y0 + height * ratio;
          const px = (ratio) => x0 + width * ratio;
          const z = centerZ;
          const frontZ = z + depth;
          const tacticalZ = z + depth * 2;

          const screenGlass = builder.color("#06111f");
          const screenGlow = builder.color(prop?.color || "#38bdf8", true);
          const tacticalGrid = builder.color("#0ea5e9", true);
          const hullPercent = this.enemyShipHullPercent();
          const disabled = this.enemyShipDisabled();
          const shotAgeMs = this.bridgeTacticalShotAgeMs(nowMs);
          const tacticalFiring = shotAgeMs < 850;
          const explosionDurationMs = 1900;
          const exploding = disabled && shotAgeMs < explosionDurationMs;
          const explosionProgress = exploding ? Math.max(0, Math.min(1, shotAgeMs / explosionDurationMs)) : 1;
          const hostileHull = builder.color(disabled ? "#334155" : hullPercent <= 50 ? "#854d0e" : "#365314");
          const hostileDark = builder.color(disabled ? "#0f172a" : "#111827");
          const hostileAlert = builder.color(disabled ? "#64748b" : "#ef4444", true);
          const signal = builder.color(disabled ? "#86efac" : "#fbbf24", true);
          const weaponGlow = builder.color("#f97316", true);
          const hitGlow = builder.color("#fef3c7", true);
          const explosionCore = builder.color("#fff7ed", true);
          const explosionFire = builder.color("#fb923c", true);
          const explosionOuter = builder.color("#ef4444", true);
          const debrisDark = builder.color("#1f2937");
          const tracking = this.bridgeViewscreenTrackingActive();
          const lockGlow = builder.color(disabled ? "#86efac" : tracking ? "#86efac" : "#fbbf24", true);
          const scanPulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 460);
          const lockPulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 170);

          // Data-defined viewscreen surface mounted on the forward bulkhead.
          builder.box([x0, y0, z - depth / 2], [x1, y1, z + depth / 2], screenGlass);
          builder.beam([px(0.022), py(0.076), frontZ], [px(0.978), py(0.076), frontZ], 0.022, tacticalGrid);
          builder.beam([px(0.022), py(0.914), frontZ], [px(0.978), py(0.914), frontZ], 0.022, tacticalGrid);
          builder.beam([px(0.03), py(0.114), frontZ], [px(0.03), py(0.867), frontZ], 0.018, screenGlow);
          builder.beam([px(0.97), py(0.114), frontZ], [px(0.97), py(0.867), frontZ], 0.018, screenGlow);
          [0.196, 0.5, 0.804].forEach((ratio) => {
            builder.beam([px(ratio), py(0.114), frontZ * 0.999 + tacticalZ * 0.001], [px(ratio), py(0.876), frontZ * 0.999 + tacticalZ * 0.001], 0.006, tacticalGrid);
          });
          [0.305, 0.524, 0.743].forEach((ratio) => {
            builder.beam([px(0.051), py(ratio), frontZ * 0.999 + tacticalZ * 0.001], [px(0.949), py(ratio), frontZ * 0.999 + tacticalZ * 0.001], 0.006, tacticalGrid);
          });

          // Enemy raider tactical image. Two bridge volleys reduce the hull from 100% to 0%;
          // the final hit replaces the intact silhouette with an expanding explosion and debris.
          if (!disabled) {
            builder.ellipsoid([centerX, py(0.524), tacticalZ], [width * 0.029, height * 0.2, depth * 0.62], 14, 6, hostileDark);
            builder.ellipsoid([centerX - width * 0.07, py(0.524), tacticalZ + depth * 0.12], [width * 0.072, height * 0.076, depth * 0.62], 14, 5, hostileHull);
            builder.ellipsoid([centerX + width * 0.07, py(0.524), tacticalZ + depth * 0.12], [width * 0.072, height * 0.076, depth * 0.62], 14, 5, hostileHull);
            builder.box([centerX - width * 0.017, py(0.448), tacticalZ + depth * 0.88], [centerX + width * 0.017, py(0.6), tacticalZ + depth * 1.38], hostileAlert);
            builder.box([centerX - width * 0.128, py(0.486), tacticalZ + depth * 0.62], [centerX - width * 0.084, py(0.562), tacticalZ + depth * 1.25], hostileAlert);
            builder.box([centerX + width * 0.084, py(0.486), tacticalZ + depth * 0.62], [centerX + width * 0.128, py(0.562), tacticalZ + depth * 1.25], hostileAlert);
          } else if (exploding) {
            const blast = 0.22 + explosionProgress * 0.78;
            const center = [centerX, py(0.524), tacticalZ + depth * 2.4];
            builder.ellipsoid(center, [width * (0.035 + blast * 0.065), height * (0.09 + blast * 0.18), depth * (2.1 + blast * 4.8)], 18, 9, explosionOuter);
            builder.ellipsoid(center, [width * (0.026 + blast * 0.045), height * (0.07 + blast * 0.12), depth * (2.6 + blast * 3.4)], 18, 9, explosionFire);
            builder.ellipsoid(center, [width * (0.014 + blast * 0.022), height * (0.04 + blast * 0.075), depth * (3.0 + blast * 2.2)], 16, 8, explosionCore);
            for (let index = 0; index < 12; index += 1) {
              const angle = index * Math.PI * 2 / 12 + explosionProgress * 0.65;
              const radialX = Math.cos(angle) * width * (0.045 + explosionProgress * 0.19);
              const radialY = Math.sin(angle) * height * (0.06 + explosionProgress * 0.28);
              builder.beam(
                [centerX + radialX * 0.18, py(0.524) + radialY * 0.18, tacticalZ + depth * 1.8],
                [centerX + radialX, py(0.524) + radialY, tacticalZ + depth * (2.4 + index * 0.08)],
                0.012 + (1 - explosionProgress) * 0.025,
                index % 3 === 0 ? explosionCore : index % 2 === 0 ? explosionFire : explosionOuter
              );
            }
            [
              [-0.16, -0.17, 0.028],
              [-0.11, 0.19, 0.022],
              [0.13, -0.14, 0.026],
              [0.18, 0.16, 0.021],
              [-0.22, 0.05, 0.018],
              [0.23, -0.01, 0.019]
            ].forEach(([dx, dy, fragmentSize], index) => {
              const spread = 0.35 + explosionProgress;
              const fx = centerX + width * dx * spread;
              const fy = py(0.524) + height * dy * spread;
              const sizeX = width * fragmentSize;
              const sizeY = height * fragmentSize * 1.7;
              builder.box(
                [fx - sizeX, fy - sizeY, tacticalZ + depth * (1.4 + index * 0.15)],
                [fx + sizeX, fy + sizeY, tacticalZ + depth * (2.2 + index * 0.2)],
                index % 2 ? hostileHull : debrisDark
              );
            });
          } else {
            [
              [-0.19, -0.15, 0.03],
              [-0.12, 0.2, 0.024],
              [-0.02, -0.04, 0.035],
              [0.11, -0.17, 0.027],
              [0.18, 0.14, 0.031],
              [0.24, -0.01, 0.021]
            ].forEach(([dx, dy, fragmentSize], index) => {
              const fx = centerX + width * dx;
              const fy = py(0.524) + height * dy;
              builder.box(
                [fx - width * fragmentSize, fy - height * fragmentSize, tacticalZ + depth * (0.8 + index * 0.12)],
                [fx + width * fragmentSize, fy + height * fragmentSize, tacticalZ + depth * (1.7 + index * 0.18)],
                index % 2 ? hostileHull : debrisDark
              );
            });
            builder.ellipsoid([centerX, py(0.524), tacticalZ + depth * 1.1], [width * 0.035, height * 0.07, depth * 0.9], 12, 5, builder.color("#7f1d1d", true));
          }

          builder.beam([px(0.288), py(0.238), tacticalZ - depth * 0.5], [px(0.712), py(0.81), tacticalZ - depth * 0.5], 0.014 + scanPulse * 0.008, signal);
          builder.beam([px(0.288), py(0.81), tacticalZ - depth * 0.5], [px(0.712), py(0.238), tacticalZ - depth * 0.5], 0.014 + scanPulse * 0.008, signal);
          builder.beam([px(0.101), py(0.2), tacticalZ - depth * 0.38], [px(0.3), py(0.2), tacticalZ - depth * 0.38], 0.016, hostileAlert);
          builder.beam([px(0.7), py(0.2), tacticalZ - depth * 0.38], [px(0.899), py(0.2), tacticalZ - depth * 0.38], 0.016, hostileAlert);
          builder.beam([px(0.101), py(0.848), tacticalZ - depth * 0.38], [px(0.3), py(0.848), tacticalZ - depth * 0.38], 0.016, hostileAlert);
          builder.beam([px(0.7), py(0.848), tacticalZ - depth * 0.38], [px(0.899), py(0.848), tacticalZ - depth * 0.38], 0.016, hostileAlert);
          if (tacticalFiring) {
            const impact = 1 - Math.min(1, shotAgeMs / 850);
            builder.beam([px(0.914), py(0.19), tacticalZ + depth * 0.75], [px(0.561), py(0.486), tacticalZ + depth * 2.25], 0.018 + impact * 0.028, weaponGlow);
            builder.beam([px(0.886), py(0.257), tacticalZ + depth * 0.75], [px(0.451), py(0.562), tacticalZ + depth * 2.25], 0.014 + impact * 0.022, weaponGlow);
            if (!disabled) {
              builder.box([centerX - width * 0.038, py(0.438), tacticalZ + depth * 2.12], [centerX + width * 0.038, py(0.619), tacticalZ + depth * 3.25], hitGlow);
            }
          }
          const hullBarWidth = width * 0.348 * Math.max(0, Math.min(1, hullPercent / 100));
          builder.box([centerX - width * 0.174, y0, tacticalZ + depth * 0.25], [centerX - width * 0.174 + hullBarWidth, y0 + height * 0.033, tacticalZ + depth * 1.12], disabled ? lockGlow : hostileAlert);
          if (disabled && !exploding) {
            builder.beam([px(0.38), py(0.86), tacticalZ + depth * 2.25], [px(0.62), py(0.86), tacticalZ + depth * 2.25], 0.028, lockGlow);
          }

          if (tracking) {
            const thickness = 0.018 + lockPulse * 0.014;
            builder.beam([px(0.338), py(0.324), tacticalZ + depth * 0.25], [px(0.422), py(0.324), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.578), py(0.324), tacticalZ + depth * 0.25], [px(0.662), py(0.324), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.338), py(0.724), tacticalZ + depth * 0.25], [px(0.422), py(0.724), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.578), py(0.724), tacticalZ + depth * 0.25], [px(0.662), py(0.724), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.338), py(0.324), tacticalZ + depth * 0.25], [px(0.338), py(0.505), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.662), py(0.324), tacticalZ + depth * 0.25], [px(0.662), py(0.505), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.338), py(0.552), tacticalZ + depth * 0.25], [px(0.338), py(0.724), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.662), py(0.552), tacticalZ + depth * 0.25], [px(0.662), py(0.724), tacticalZ + depth * 0.25], thickness, lockGlow);
            builder.beam([px(0.072), py(0.143), tacticalZ], [px(0.928), py(0.143), tacticalZ], 0.014, lockGlow);
            builder.beam([px(0.072), py(0.905), tacticalZ], [px(0.928), py(0.905), tacticalZ], 0.014, lockGlow);
          } else {
            const scanX = px(0.087 + scanPulse * 0.826);
            builder.beam([scanX, py(0.143), tacticalZ], [scanX, py(0.905), tacticalZ], 0.012, signal);
          }
        }

  });
})();
