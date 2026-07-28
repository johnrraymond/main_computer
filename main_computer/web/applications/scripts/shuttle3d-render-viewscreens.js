;(function () {
  // Patch S extracts content-defined viewscreen render passes from scene-viewer.js.
  const modules = globalThis.MainComputerShuttle3DRendererModules;
  if (!modules || typeof modules.register !== "function") return;

  modules.register("viewscreens", {
        appendMotherShipViewscreenDisplay(builder, prop, nowMs = 0) {
          // Patch P renders content-defined viewscreens/displays from prop.display metadata.
          // The bridge viewscreen is now a normal motherShipInterior prop with display: "enemyShipTactical".
          const display = String(prop?.display || "").toLowerCase();
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
          const hostileHull = builder.color(disabled ? "#334155" : hullPercent < 45 ? "#854d0e" : "#365314");
          const hostileDark = builder.color(disabled ? "#0f172a" : "#111827");
          const hostileAlert = builder.color(disabled ? "#64748b" : "#ef4444", true);
          const signal = builder.color(disabled ? "#86efac" : "#fbbf24", true);
          const weaponGlow = builder.color("#f97316", true);
          const hitGlow = builder.color("#fef3c7", true);
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

          // Enemy raider tactical image on the content-defined display.
          builder.ellipsoid([centerX, py(0.524), tacticalZ], [width * 0.029, height * 0.2, depth * 0.62], 14, 6, hostileDark);
          builder.ellipsoid([centerX - width * 0.07, py(0.524), tacticalZ + depth * 0.12], [width * 0.072, height * 0.076, depth * 0.62], 14, 5, hostileHull);
          builder.ellipsoid([centerX + width * 0.07, py(0.524), tacticalZ + depth * 0.12], [width * 0.072, height * 0.076, depth * 0.62], 14, 5, hostileHull);
          builder.box([centerX - width * 0.017, py(0.448), tacticalZ + depth * 0.88], [centerX + width * 0.017, py(0.6), tacticalZ + depth * 1.38], hostileAlert);
          builder.box([centerX - width * 0.128, py(0.486), tacticalZ + depth * 0.62], [centerX - width * 0.084, py(0.562), tacticalZ + depth * 1.25], hostileAlert);
          builder.box([centerX + width * 0.084, py(0.486), tacticalZ + depth * 0.62], [centerX + width * 0.128, py(0.562), tacticalZ + depth * 1.25], hostileAlert);
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
            builder.box([centerX - width * 0.038, py(0.438), tacticalZ + depth * 2.12], [centerX + width * 0.038, py(0.619), tacticalZ + depth * 3.25], hitGlow);
          }
          const hullBarWidth = width * 0.348 * Math.max(0, Math.min(1, hullPercent / 100));
          builder.box([centerX - width * 0.174, y0, tacticalZ + depth * 0.25], [centerX - width * 0.174 + hullBarWidth, y0 + height * 0.033, tacticalZ + depth * 1.12], disabled ? lockGlow : hostileAlert);
          if (disabled) {
            builder.beam([centerX - width * 0.232, py(0.524), tacticalZ + depth * 2.25], [centerX + width * 0.232, py(0.524), tacticalZ + depth * 2.25], 0.028, lockGlow);
            builder.beam([centerX, py(0.248), tacticalZ + depth * 2.25], [centerX, py(0.8), tacticalZ + depth * 2.25], 0.028, lockGlow);
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
