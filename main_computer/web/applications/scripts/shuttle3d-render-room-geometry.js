;(function () {
  // Patch S extracts the room-geometry render pass from scene-viewer.js.
  const modules = globalThis.MainComputerShuttle3DRendererModules;
  if (!modules || typeof modules.register !== "function") return;

  modules.register("roomGeometry", {
        appendMotherShipRoomGeometry(builder, nowMs = 0) {
          // Patch O renders room shell/wall/opening geometry from rooms[].geometry.
          // Bay Ops interior transit spine now renders from rooms[].geometry boxes/beams.
          // See pretty_docs/game-runtime-patch-O-room-geometry-extraction.md for the content contract.
          // This keeps traversal metadata, room visuals, and structural affordances on the same data model.
          const rooms = Array.isArray(this.interiorConfig?.rooms) ? this.interiorConfig.rooms : [];
          if (!rooms.length) return;
          const pulse = 0.55 + 0.45 * Math.sin((nowMs || 0) / 320);
          const deck = builder.color("#1e293b");
          const deckDark = builder.color("#0f172a");
          const wallMaterial = builder.color("#334155");
          const doorMaterial = builder.color("#475569");
          const openDoor = builder.color("#22c55e", true);
          const closedDoor = builder.color("#f59e0b", true);
          const attentionDoor = builder.color("#f59e0b", true);
          const valueNumber = (value, fallback) => {
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : fallback;
          };
          const colorFor = (value, fallback = "#67e8f9", emissive = false) => builder.color(String(value || fallback), emissive);
          const boundsFor = (room, shell) => {
            const bounds = shell?.bounds || room?.bounds || {};
            return {
              minX: valueNumber(bounds.minX, valueNumber(room?.bounds?.minX, -1)),
              maxX: valueNumber(bounds.maxX, valueNumber(room?.bounds?.maxX, 1)),
              minZ: valueNumber(bounds.minZ, valueNumber(room?.bounds?.minZ, -1)),
              maxZ: valueNumber(bounds.maxZ, valueNumber(room?.bounds?.maxZ, 1))
            };
          };
          const drawShell = (room, geometry) => {
            const shell = geometry.shell && typeof geometry.shell === "object" ? geometry.shell : {};
            if (geometry.shell === false || shell.enabled === false) return;
            const bounds = boundsFor(room, shell);
            const floorEnabled = shell.floor !== false;
            const ceilingEnabled = shell.ceiling !== false;
            const floorMinY = valueNumber(shell.floorMinY, -1.2);
            const floorMaxY = valueNumber(shell.floorMaxY, -1.12);
            const ceilingMinY = valueNumber(shell.ceilingMinY, 2.72);
            const ceilingMaxY = valueNumber(shell.ceilingMaxY, 3.02);
            if (floorEnabled) builder.box([bounds.minX, floorMinY, bounds.minZ], [bounds.maxX, floorMaxY, bounds.maxZ], deck);
            if (ceilingEnabled) builder.box([bounds.minX, ceilingMinY, bounds.minZ], [bounds.maxX, ceilingMaxY, bounds.maxZ], deckDark);
            const accent = colorFor(shell.accentColor || geometry.accentColor || room?.visual?.edgeColor, "#67e8f9", true);
            if (shell.accentBeams !== false) {
              builder.beam([bounds.minX + 0.45, 2.56, bounds.minZ + 0.42], [bounds.maxX - 0.45, 2.56, bounds.minZ + 0.42], 0.018, accent);
              builder.beam([bounds.minX + 0.45, 2.56, bounds.maxZ - 0.42], [bounds.maxX - 0.45, 2.56, bounds.maxZ - 0.42], 0.018, accent);
            }
          };
          const drawWall = (wall) => {
            const axis = String(wall?.axis || "").toLowerCase();
            const material = colorFor(wall?.color, "#334155", wall?.emissive === true);
            if (axis === "x") {
              const x = valueNumber(wall.x, NaN);
              const minZ = valueNumber(wall.minZ, NaN);
              const maxZ = valueNumber(wall.maxZ, NaN);
              if ([x, minZ, maxZ].every(Number.isFinite)) {
                builder.box([x - 0.15, -1.2, minZ], [x + 0.15, 2.85, maxZ], material || wallMaterial);
              }
            } else if (axis === "z") {
              const z = valueNumber(wall.z, NaN);
              const minX = valueNumber(wall.minX, NaN);
              const maxX = valueNumber(wall.maxX, NaN);
              if ([z, minX, maxX].every(Number.isFinite)) {
                builder.box([minX, -1.2, z - 0.15], [maxX, 2.85, z + 0.15], material || wallMaterial);
              }
            }
          };
          const doorStateColor = (doorId) => {
            const state = this.shipDoorState(doorId);
            if (state === "open") return openDoor;
            if (state === "closed") return attentionDoor;
            return closedDoor;
          };
          const drawDoorPanel = (panel) => {
            const doorId = String(panel?.door || panel?.id || "");
            const center = Array.isArray(panel?.center) ? panel.center : [panel?.centerX, panel?.centerZ];
            const centerX = valueNumber(center[0], NaN);
            const centerZ = valueNumber(center[1], NaN);
            const width = Math.max(0.2, valueNumber(panel?.width, 1.6));
            if (![centerX, centerZ, width].every(Number.isFinite)) return;
            const color = doorStateColor(doorId);
            const state = this.shipDoorState(doorId);
            const vertical = panel?.vertical === true;
            if (vertical) {
              if (state !== "open") builder.box([centerX - 0.12, -1.05, centerZ - width / 2], [centerX + 0.12, 2.42, centerZ + width / 2], doorMaterial);
              builder.beam([centerX, 0.35, centerZ - width / 2], [centerX, 0.35, centerZ + width / 2], 0.03, color);
              builder.beam([centerX, 2.02, centerZ - width / 2], [centerX, 2.02, centerZ + width / 2], 0.03, color);
            } else {
              if (state !== "open") builder.box([centerX - width / 2, -1.05, centerZ - 0.12], [centerX + width / 2, 2.42, centerZ + 0.12], doorMaterial);
              builder.beam([centerX - width / 2, 0.35, centerZ], [centerX + width / 2, 0.35, centerZ], 0.03, color);
              builder.beam([centerX - width / 2, 2.02, centerZ], [centerX + width / 2, 2.02, centerZ], 0.03, color);
            }
          };
          const drawBox = (box) => {
            const min = Array.isArray(box?.min) ? box.min.map(Number) : [];
            const max = Array.isArray(box?.max) ? box.max.map(Number) : [];
            if (min.length === 3 && max.length === 3 && min.every(Number.isFinite) && max.every(Number.isFinite)) {
              builder.box(min, max, colorFor(box?.color, "#475569", box?.emissive === true));
            }
          };
          const drawBeam = (beam) => {
            const start = Array.isArray(beam?.start) ? beam.start.map(Number) : [];
            const end = Array.isArray(beam?.end) ? beam.end.map(Number) : [];
            if (start.length === 3 && end.length === 3 && start.every(Number.isFinite) && end.every(Number.isFinite)) {
              const radius = Math.max(0.004, valueNumber(beam?.radius, 0.018));
              builder.beam(start, end, radius + (beam?.pulse === true ? pulse * 0.006 : 0), colorFor(beam?.color, "#67e8f9", beam?.emissive !== false));
            }
          };

          rooms.forEach((room) => {
            const geometry = room?.geometry && typeof room.geometry === "object" ? room.geometry : {};
            if (!Object.keys(geometry).length || geometry.enabled === false) return;
            drawShell(room, geometry);
            (Array.isArray(geometry.walls) ? geometry.walls : []).forEach(drawWall);
            (Array.isArray(geometry.boxes) ? geometry.boxes : []).forEach(drawBox);
            (Array.isArray(geometry.beams) ? geometry.beams : []).forEach(drawBeam);
            (Array.isArray(geometry.lighting) ? geometry.lighting : []).forEach(drawBeam);
            (Array.isArray(geometry.doorPanels) ? geometry.doorPanels : []).forEach(drawDoorPanel);
          });
        }

  });
})();
