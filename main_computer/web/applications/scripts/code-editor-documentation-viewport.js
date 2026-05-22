    const codeEditorDocViewportRoot = document.querySelector("#code-editor-doc-viewport");
    const codeEditorDocViewportMode = document.querySelector("#code-editor-doc-viewport-mode");
    const codeEditorDocTarget = document.querySelector("#code-editor-doc-target");
    const codeEditorDocLoad = document.querySelector("#code-editor-doc-load");
    const codeEditorDocResolution = document.querySelector("#code-editor-doc-resolution");
    const codeEditorDocWidth = document.querySelector("#code-editor-doc-width");
    const codeEditorDocHeight = document.querySelector("#code-editor-doc-height");
    const codeEditorDocApplyResolution = document.querySelector("#code-editor-doc-apply-resolution");
    const codeEditorDocSnap = document.querySelector("#code-editor-doc-snap");
    const codeEditorDocCollapse = document.querySelector("#code-editor-doc-collapse");
    const codeEditorDocStatus = document.querySelector("#code-editor-doc-status");
    const codeEditorDocFrame = document.querySelector("#code-editor-doc-frame");
    const codeEditorDocVramCanvas = document.querySelector("#code-editor-doc-vram");
    const codeEditorDocInspect = document.querySelector("#code-editor-doc-inspect");
    const codeEditorDocTargets = document.querySelector("#code-editor-doc-targets");
    const codeEditorDocScriptWidget = document.querySelector("#code-editor-doc-script-widget");
    const codeEditorDocScriptSource = document.querySelector("#code-editor-doc-script-source");
    const codeEditorDocScriptRun = document.querySelector("#code-editor-doc-script-run");
    const codeEditorDocVramReset = document.querySelector("#code-editor-doc-vram-reset");
    const codeEditorDocScriptLog = document.querySelector("#code-editor-doc-script-log");

    const codeEditorDocDefaultScript = `const { vram } = viewport;

vram.reset({ width: 320, height: 200, fill: [8, 10, 12, 255] });
vram.fillRect(18, 18, 96, 52, [64, 128, 255, 255]);
vram.fillRect(38, 38, 136, 76, [255, 210, 80, 220]);

const wave = [];
for (let x = 0; x < 320; x += 1) {
  const y = 100 + Math.round(Math.sin(x / 12) * 22);
  wave.push([x, y, [80, 255, 180, 255]]);
}
vram.setPixels(wave);

vram.setPixel(160, 100, [255, 80, 120, 255]);
console.log("VRAM size", vram.getSize());
console.log("Center pixel", vram.getPixel(160, 100));`;

    const codeEditorDocViewportPresets = {
      "docs-compact": {label: "Docs Compact 720x480", width: 720, height: 480},
      "docs-wide": {label: "Docs Wide 1024x640", width: 1024, height: 640},
      desktop: {label: "Desktop 1440x900", width: 1440, height: 900},
      mobile: {label: "Mobile 390x844", width: 390, height: 844},
      custom: {label: "Custom", width: null, height: null},
    };
    const codeEditorDocDefaultSnapConfig = {
      default_snap_mode: "docs-wide",
      grow_step_px: 80,
      allow_manual_override: true,
      presets: {
        "docs-compact": {max_width: 720, max_height: 480},
        "docs-wide": {max_width: 1024, max_height: 640},
        workspace: {max_width: 1280, max_height: 800},
        full: {max_width: 1920, max_height: 1080},
      },
    };
    const codeEditorDocViewportState = {
      mode: "collapsed",
      targetId: "",
      width: 1024,
      height: 640,
      snapMode: "docs-wide",
      docStatus: "idle",
      docPath: "",
      contentType: "text/html",
      scriptStatus: "idle",
      scriptLog: [],
      vram: {
        width: 320,
        height: 200,
        format: "rgba8888",
        frameId: 0,
        dirty: false,
        lastUpdate: "",
      },
      snapConfig: codeEditorDocDefaultSnapConfig,
      metadata: {},
    };
    const codeEditorDocViewportSubscribers = new Set();
    const codeEditorDocManifestLookup = new Map();

    function notifyCodeEditorDocViewport() {
      const snapshot = window.MainComputerCodeEditorViewport.getState();
      codeEditorDocViewportSubscribers.forEach((callback) => {
        try { callback(snapshot); } catch {}
      });
    }

    function safeCodeEditorDocHtml(content, title = "Generated documentation") {
      return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)}</title><style>body{margin:0;padding:18px;background:#f8f7f2;color:#171717;font:15px/1.5 Arial,sans-serif}code,pre{font-family:Consolas,monospace}article{max-width:980px;margin:auto}section{margin:0 0 22px}pre{overflow:auto;background:#111;color:#f4f4f4;padding:12px;border-radius:6px}</style></head><body>${content}</body></html>`;
    }

    function codeEditorDocTimestamp() {
      try { return new Date().toISOString(); } catch { return ""; }
    }

    function clampCodeEditorDocNumber(value, min, max, fallback) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(min, Math.min(max, Math.round(number)));
    }

    function clampCodeEditorDocByte(value, fallback = 0) {
      return clampCodeEditorDocNumber(value, 0, 255, fallback);
    }

    function normalizeCodeEditorDocRgba(value, fallback = [0, 0, 0, 255]) {
      const source = Array.isArray(value) ? value : fallback;
      return [
        clampCodeEditorDocByte(source[0], fallback[0] ?? 0),
        clampCodeEditorDocByte(source[1], fallback[1] ?? 0),
        clampCodeEditorDocByte(source[2], fallback[2] ?? 0),
        clampCodeEditorDocByte(source.length > 3 ? source[3] : fallback[3], fallback[3] ?? 255),
      ];
    }

    function codeEditorDocRgbaCss(rgba) {
      const color = normalizeCodeEditorDocRgba(rgba);
      return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${color[3] / 255})`;
    }

    function codeEditorDocIsInstance(value, constructorName) {
      const constructor = window[constructorName];
      return typeof constructor === "function" && value instanceof constructor;
    }

    function codeEditorDocVramContext() {
      if (!codeEditorDocVramCanvas) return null;
      return codeEditorDocVramCanvas.getContext("2d", {willReadFrequently: true});
    }

    function setCodeEditorDocVramDimensions(width, height, source = "vram") {
      const nextWidth = clampCodeEditorDocNumber(width, 1, 4096, codeEditorDocViewportState.vram.width);
      const nextHeight = clampCodeEditorDocNumber(height, 1, 4096, codeEditorDocViewportState.vram.height);
      codeEditorDocViewportState.vram.width = nextWidth;
      codeEditorDocViewportState.vram.height = nextHeight;
      codeEditorDocViewportState.width = nextWidth;
      codeEditorDocViewportState.height = nextHeight;
      codeEditorDocViewportState.metadata.resolution_source = source;
      if (codeEditorDocWidth) codeEditorDocWidth.value = String(nextWidth);
      if (codeEditorDocHeight) codeEditorDocHeight.value = String(nextHeight);
      if (codeEditorDocVramCanvas) {
        if (codeEditorDocVramCanvas.width !== nextWidth) codeEditorDocVramCanvas.width = nextWidth;
        if (codeEditorDocVramCanvas.height !== nextHeight) codeEditorDocVramCanvas.height = nextHeight;
      }
    }

    function bumpCodeEditorDocVramFrame() {
      codeEditorDocViewportState.vram.frameId += 1;
      codeEditorDocViewportState.vram.dirty = true;
      codeEditorDocViewportState.vram.lastUpdate = codeEditorDocTimestamp();
      updateCodeEditorDocViewportStatus();
      return codeEditorDocViewportState.vram.frameId;
    }

    function ensureCodeEditorDocVramBuffer() {
      const context = codeEditorDocVramContext();
      if (!context || !codeEditorDocVramCanvas) return null;
      if (!codeEditorDocVramCanvas.width || !codeEditorDocVramCanvas.height) {
        codeEditorDocVramCanvas.width = codeEditorDocViewportState.vram.width;
        codeEditorDocVramCanvas.height = codeEditorDocViewportState.vram.height;
        context.fillStyle = codeEditorDocRgbaCss([0, 0, 0, 255]);
        context.fillRect(0, 0, codeEditorDocVramCanvas.width, codeEditorDocVramCanvas.height);
      }
      return context;
    }

    function resetCodeEditorDocVram(options = {}) {
      const nextWidth = options.width ?? codeEditorDocViewportState.vram.width;
      const nextHeight = options.height ?? codeEditorDocViewportState.vram.height;
      const fill = normalizeCodeEditorDocRgba(options.fill ?? [0, 0, 0, 255], [0, 0, 0, 255]);
      setCodeEditorDocVramDimensions(nextWidth, nextHeight, "vram");
      const context = ensureCodeEditorDocVramBuffer();
      if (context && codeEditorDocVramCanvas) {
        context.clearRect(0, 0, codeEditorDocVramCanvas.width, codeEditorDocVramCanvas.height);
        context.fillStyle = codeEditorDocRgbaCss(fill);
        context.fillRect(0, 0, codeEditorDocVramCanvas.width, codeEditorDocVramCanvas.height);
      }
      return {
        width: codeEditorDocViewportState.vram.width,
        height: codeEditorDocViewportState.vram.height,
        frameId: bumpCodeEditorDocVramFrame(),
      };
    }

    function setCodeEditorDocVramPixel(x, y, rgba) {
      const context = ensureCodeEditorDocVramBuffer();
      if (!context || !codeEditorDocVramCanvas) return false;
      const pixelX = Math.trunc(Number(x));
      const pixelY = Math.trunc(Number(y));
      if (!Number.isFinite(pixelX) || !Number.isFinite(pixelY)) return false;
      if (pixelX < 0 || pixelY < 0 || pixelX >= codeEditorDocVramCanvas.width || pixelY >= codeEditorDocVramCanvas.height) return false;
      context.fillStyle = codeEditorDocRgbaCss(rgba);
      context.fillRect(pixelX, pixelY, 1, 1);
      bumpCodeEditorDocVramFrame();
      return true;
    }

    function normalizeCodeEditorDocPixelUpdate(update) {
      if (Array.isArray(update)) {
        return {x: update[0], y: update[1], rgba: update[2]};
      }
      if (update && typeof update === "object") {
        return {x: update.x, y: update.y, rgba: update.rgba ?? update.color};
      }
      return null;
    }

    function setCodeEditorDocVramPixels(updates = []) {
      const context = ensureCodeEditorDocVramBuffer();
      if (!context || !codeEditorDocVramCanvas || !Array.isArray(updates)) return {written: 0, frameId: codeEditorDocViewportState.vram.frameId};
      const imageData = context.getImageData(0, 0, codeEditorDocVramCanvas.width, codeEditorDocVramCanvas.height);
      let written = 0;
      updates.forEach((update) => {
        const normalized = normalizeCodeEditorDocPixelUpdate(update);
        if (!normalized) return;
        const pixelX = Math.trunc(Number(normalized.x));
        const pixelY = Math.trunc(Number(normalized.y));
        if (!Number.isFinite(pixelX) || !Number.isFinite(pixelY)) return;
        if (pixelX < 0 || pixelY < 0 || pixelX >= codeEditorDocVramCanvas.width || pixelY >= codeEditorDocVramCanvas.height) return;
        const rgba = normalizeCodeEditorDocRgba(normalized.rgba);
        const offset = (pixelY * codeEditorDocVramCanvas.width + pixelX) * 4;
        imageData.data[offset] = rgba[0];
        imageData.data[offset + 1] = rgba[1];
        imageData.data[offset + 2] = rgba[2];
        imageData.data[offset + 3] = rgba[3];
        written += 1;
      });
      if (written) {
        context.putImageData(imageData, 0, 0);
        return {written, frameId: bumpCodeEditorDocVramFrame()};
      }
      return {written, frameId: codeEditorDocViewportState.vram.frameId};
    }

    function fillCodeEditorDocVramRect(x, y, width, height, rgba) {
      const context = ensureCodeEditorDocVramBuffer();
      if (!context || !codeEditorDocVramCanvas) return false;
      const rectX = Math.trunc(Number(x));
      const rectY = Math.trunc(Number(y));
      const rectWidth = Math.max(0, Math.trunc(Number(width)));
      const rectHeight = Math.max(0, Math.trunc(Number(height)));
      if (!Number.isFinite(rectX) || !Number.isFinite(rectY) || !rectWidth || !rectHeight) return false;
      context.fillStyle = codeEditorDocRgbaCss(rgba);
      context.fillRect(rectX, rectY, rectWidth, rectHeight);
      bumpCodeEditorDocVramFrame();
      return true;
    }

    function getCodeEditorDocVramPixel(x, y) {
      const context = ensureCodeEditorDocVramBuffer();
      if (!context || !codeEditorDocVramCanvas) return [0, 0, 0, 0];
      const pixelX = Math.trunc(Number(x));
      const pixelY = Math.trunc(Number(y));
      if (!Number.isFinite(pixelX) || !Number.isFinite(pixelY)) return [0, 0, 0, 0];
      if (pixelX < 0 || pixelY < 0 || pixelX >= codeEditorDocVramCanvas.width || pixelY >= codeEditorDocVramCanvas.height) return [0, 0, 0, 0];
      return Array.from(context.getImageData(pixelX, pixelY, 1, 1).data);
    }

    function getCodeEditorDocVramImageData() {
      const context = ensureCodeEditorDocVramBuffer();
      if (!context || !codeEditorDocVramCanvas) return null;
      return context.getImageData(0, 0, codeEditorDocVramCanvas.width, codeEditorDocVramCanvas.height);
    }

    function drawCodeEditorDocVramSource(source, options = {}) {
      const context = ensureCodeEditorDocVramBuffer();
      if (!context || !codeEditorDocVramCanvas) return {drawn: false, frameId: codeEditorDocViewportState.vram.frameId};
      const x = Math.trunc(Number(options.x) || 0);
      const y = Math.trunc(Number(options.y) || 0);
      if (codeEditorDocIsInstance(source, "ImageData")) {
        if (options.width || options.height) {
          const buffer = document.createElement("canvas");
          buffer.width = source.width;
          buffer.height = source.height;
          const bufferContext = buffer.getContext("2d");
          bufferContext.putImageData(source, 0, 0);
          context.drawImage(buffer, x, y, Math.trunc(Number(options.width) || source.width), Math.trunc(Number(options.height) || source.height));
        } else {
          context.putImageData(source, x, y);
        }
      } else {
        const width = Math.trunc(Number(options.width) || source.width || source.videoWidth || codeEditorDocVramCanvas.width);
        const height = Math.trunc(Number(options.height) || source.height || source.videoHeight || codeEditorDocVramCanvas.height);
        context.drawImage(source, x, y, width, height);
      }
      return {drawn: true, frameId: bumpCodeEditorDocVramFrame()};
    }

    function blitCodeEditorDocVramImage(source, options = {}) {
      if (typeof source === "string") {
        return new Promise((resolve, reject) => {
          const image = new Image();
          image.crossOrigin = "anonymous";
          image.onload = () => {
            try {
              resolve(drawCodeEditorDocVramSource(image, options));
            } catch (error) {
              reject(error);
            }
          };
          image.onerror = () => reject(new Error("Unable to load image source for VRAM blit."));
          image.src = source;
        });
      }
      if (
        codeEditorDocIsInstance(source, "ImageData") ||
        codeEditorDocIsInstance(source, "HTMLImageElement") ||
        codeEditorDocIsInstance(source, "HTMLCanvasElement") ||
        codeEditorDocIsInstance(source, "ImageBitmap") ||
        codeEditorDocIsInstance(source, "HTMLVideoElement")
      ) {
        return drawCodeEditorDocVramSource(source, options);
      }
      throw new Error("Unsupported VRAM blit source. Use ImageData, image/canvas/video/ImageBitmap, or an image URL/data URL.");
    }

    function getCodeEditorDocVramSize() {
      return {
        width: codeEditorDocViewportState.vram.width,
        height: codeEditorDocViewportState.vram.height,
        format: codeEditorDocViewportState.vram.format,
        frameId: codeEditorDocViewportState.vram.frameId,
      };
    }

    const codeEditorDocVramApi = {
      reset: resetCodeEditorDocVram,
      blitImage: blitCodeEditorDocVramImage,
      setPixel: setCodeEditorDocVramPixel,
      setPixels: setCodeEditorDocVramPixels,
      fillRect: fillCodeEditorDocVramRect,
      getPixel: getCodeEditorDocVramPixel,
      getImageData: getCodeEditorDocVramImageData,
      getSize: getCodeEditorDocVramSize,
    };

    function codeEditorDocScriptLogPart(value) {
      if (typeof value === "string") return value;
      try { return JSON.stringify(value); } catch { return String(value); }
    }

    function appendCodeEditorDocScriptLog(level, args) {
      const line = `[${level}] ${Array.from(args).map(codeEditorDocScriptLogPart).join(" ")}`;
      codeEditorDocViewportState.scriptLog.push(line);
      codeEditorDocViewportState.scriptLog = codeEditorDocViewportState.scriptLog.slice(-80);
      if (codeEditorDocScriptLog) codeEditorDocScriptLog.textContent = codeEditorDocViewportState.scriptLog.join("\n");
    }

    function clearCodeEditorDocScriptLog() {
      codeEditorDocViewportState.scriptLog = [];
      if (codeEditorDocScriptLog) codeEditorDocScriptLog.textContent = "";
    }

    function codeEditorDocScriptConsole() {
      return {
        log(...args) { appendCodeEditorDocScriptLog("log", args); },
        warn(...args) { appendCodeEditorDocScriptLog("warn", args); },
        error(...args) { appendCodeEditorDocScriptLog("error", args); },
      };
    }

    async function runCodeEditorDocScript() {
      if (!codeEditorDocScriptSource) return;
      setCodeEditorDocMode("script");
      clearCodeEditorDocScriptLog();
      codeEditorDocViewportState.scriptStatus = "running";
      updateCodeEditorDocViewportStatus();
      try {
        const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
        const scriptSource = codeEditorDocScriptSource.value;
        const runner = new AsyncFunction(
          "viewport",
          "console",
          `"use strict";\nconst vram = viewport.vram;\n{\n${scriptSource}\n}`
        );
        await runner(window.MainComputerCodeEditorViewport, codeEditorDocScriptConsole());
        codeEditorDocViewportState.scriptStatus = "done";
        appendCodeEditorDocScriptLog("log", ["script complete"]);
      } catch (error) {
        codeEditorDocViewportState.scriptStatus = "error";
        codeEditorDocViewportState.metadata = {...codeEditorDocViewportState.metadata, script_error: error.message || String(error)};
        appendCodeEditorDocScriptLog("error", [error.message || String(error)]);
      }
      updateCodeEditorDocViewportStatus();
    }

    function updateCodeEditorDocViewportStatus() {
      if (!codeEditorDocStatus || !codeEditorDocViewportRoot) return;
      codeEditorDocViewportRoot.dataset.mode = codeEditorDocViewportState.mode;
      codeEditorDocViewportRoot.style.setProperty("--code-editor-doc-width", `${codeEditorDocViewportState.width}px`);
      codeEditorDocViewportRoot.style.setProperty("--code-editor-doc-height", `${codeEditorDocViewportState.height}px`);
      const scriptSuffix = codeEditorDocViewportState.mode === "script"
        ? ` | ${codeEditorDocViewportState.scriptStatus} | vram ${codeEditorDocViewportState.vram.width}x${codeEditorDocViewportState.vram.height} | frame ${codeEditorDocViewportState.vram.frameId}`
        : "";
      codeEditorDocStatus.textContent = `${codeEditorDocViewportState.targetId} | ${codeEditorDocViewportState.docStatus} | ${codeEditorDocViewportState.mode} | ${codeEditorDocViewportState.width}x${codeEditorDocViewportState.height}${scriptSuffix}`;
      if (codeEditorDocInspect) {
        codeEditorDocInspect.textContent = JSON.stringify(codeEditorDocViewportState, null, 2);
        codeEditorDocInspect.hidden = codeEditorDocViewportState.mode !== "inspect";
      }
      if (codeEditorDocFrame) codeEditorDocFrame.hidden = ["inspect", "script", "collapsed"].includes(codeEditorDocViewportState.mode);
      if (codeEditorDocVramCanvas) codeEditorDocVramCanvas.hidden = codeEditorDocViewportState.mode !== "script";
      if (codeEditorDocScriptWidget) codeEditorDocScriptWidget.hidden = codeEditorDocViewportState.mode !== "script";
      if (codeEditorDocCollapse) codeEditorDocCollapse.textContent = codeEditorDocViewportState.mode === "collapsed" ? "Expand Viewport" : "Collapse Viewport";
      notifyCodeEditorDocViewport();
    }

    function setCodeEditorDocMode(mode) {
      const next = ["docs", "inspect", "script", "backend", "collapsed"].includes(mode) ? mode : "docs";
      codeEditorDocViewportState.mode = next;
      if (codeEditorDocViewportMode) codeEditorDocViewportMode.value = next;
      if (next === "script") ensureCodeEditorDocVramBuffer();
      updateCodeEditorDocViewportStatus();
    }

    function setCodeEditorDocResolution(width, height, source = "manual") {
      const nextWidth = Math.max(240, Math.min(1920, Number(width) || codeEditorDocViewportState.width));
      const nextHeight = Math.max(180, Math.min(1080, Number(height) || codeEditorDocViewportState.height));
      codeEditorDocViewportState.width = Math.round(nextWidth);
      codeEditorDocViewportState.height = Math.round(nextHeight);
      codeEditorDocViewportState.metadata.resolution_source = source;
      if (codeEditorDocWidth) codeEditorDocWidth.value = String(codeEditorDocViewportState.width);
      if (codeEditorDocHeight) codeEditorDocHeight.value = String(codeEditorDocViewportState.height);
      updateCodeEditorDocViewportStatus();
    }

    function applyCodeEditorDocPreset(presetName) {
      const preset = codeEditorDocViewportPresets[presetName] || codeEditorDocViewportPresets["docs-wide"];
      if (codeEditorDocResolution) codeEditorDocResolution.value = presetName;
      if (preset.width && preset.height) setCodeEditorDocResolution(preset.width, preset.height, `preset:${presetName}`);
    }

    function setCodeEditorDocSnapMode(snapMode) {
      const config = codeEditorDocViewportState.snapConfig?.presets?.[snapMode];
      codeEditorDocViewportState.snapMode = config ? snapMode : codeEditorDocViewportState.snapMode;
      if (codeEditorDocSnap) codeEditorDocSnap.value = codeEditorDocViewportState.snapMode;
      if (config) setCodeEditorDocResolution(config.max_width, config.max_height, `snap:${snapMode}`);
      updateCodeEditorDocViewportStatus();
    }

    async function loadCodeEditorDocManifest() {
      const response = await fetch("/api/applications/component-docs/manifest", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
      codeEditorDocManifestLookup.clear();
      if (Array.isArray(data.entries)) {
        data.entries.forEach((entry) => {
          if (!entry?.id) return;
          codeEditorDocManifestLookup.set(String(entry.id), String(entry.id));
          (Array.isArray(entry.aliases) ? entry.aliases : []).forEach((alias) => {
            if (alias) codeEditorDocManifestLookup.set(String(alias), String(entry.id));
          });
        });
      }
      if (codeEditorDocTargets && Array.isArray(data.entries)) {
        const known = new Set([...codeEditorDocTargets.querySelectorAll("option")].map((option) => option.value));
        data.entries.forEach((entry) => {
          if (!entry.id || known.has(entry.id)) return;
          const option = document.createElement("option");
          option.value = entry.id;
          codeEditorDocTargets.append(option);
          known.add(entry.id);
        });
      }
      return data;
    }

    async function loadCodeEditorDoc(targetId = codeEditorDocViewportState.targetId) {
      codeEditorDocViewportState.targetId = String(targetId || "").trim();
      if (!codeEditorDocViewportState.targetId) return;
      if (codeEditorDocTarget) codeEditorDocTarget.value = codeEditorDocViewportState.targetId;
      codeEditorDocViewportState.docStatus = "loading";
      updateCodeEditorDocViewportStatus();
      try {
        const response = await fetch("/api/applications/component-docs/read", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({id: codeEditorDocViewportState.targetId}),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
        codeEditorDocViewportState.docStatus = data.exists ? "loaded" : "missing";
        codeEditorDocViewportState.docPath = data.path || "";
        codeEditorDocViewportState.contentType = data.content_type || "text/html";
        codeEditorDocViewportState.metadata = data.metadata || {};
        if (codeEditorDocFrame) codeEditorDocFrame.srcdoc = safeCodeEditorDocHtml(data.content || "<article><h1>No documentation found</h1><p>No generated documentation exists for this target yet.</p></article>", data.title || codeEditorDocViewportState.targetId);
        setCodeEditorDocMode(codeEditorDocViewportState.mode === "collapsed" ? "docs" : codeEditorDocViewportState.mode);
      } catch (error) {
        codeEditorDocViewportState.docStatus = "error";
        codeEditorDocViewportState.metadata = {error: error.message || String(error)};
        if (codeEditorDocFrame) codeEditorDocFrame.srcdoc = safeCodeEditorDocHtml(`<article><h1>Documentation unavailable</h1><p>${escapeHtml(error.message || error)}</p></article>`, "Documentation unavailable");
        updateCodeEditorDocViewportStatus();
      }
    }

    async function reloadCodeEditorDocConfig() {
      try {
        const response = await fetch("/api/applications/component-docs/viewport-config", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
        const data = await response.json();
        if (response.ok && data.ok && data.config) {
          codeEditorDocViewportState.snapConfig = data.config;
        } else {
          codeEditorDocViewportState.snapConfig = codeEditorDocDefaultSnapConfig;
        }
      } catch {
        codeEditorDocViewportState.snapConfig = codeEditorDocDefaultSnapConfig;
      }
      setCodeEditorDocSnapMode(codeEditorDocViewportState.snapConfig.default_snap_mode || "docs-wide");
      return codeEditorDocViewportState.snapConfig;
    }

    function pushCodeEditorDocFrame(payload = {}) {
      if (payload.mode) codeEditorDocViewportState.mode = payload.mode;
      if (payload.targetId || payload.id) codeEditorDocViewportState.targetId = payload.targetId || payload.id;
      if (payload.width || payload.height) setCodeEditorDocResolution(payload.width, payload.height, "script");
      if (payload.status) codeEditorDocViewportState.docStatus = String(payload.status);
      if (payload.html && codeEditorDocFrame) codeEditorDocFrame.srcdoc = safeCodeEditorDocHtml(String(payload.html), payload.title || "Script controlled documentation");
      if (payload.metadata && typeof payload.metadata === "object") codeEditorDocViewportState.metadata = payload.metadata;
      if (payload.vram && typeof payload.vram === "object") {
        setCodeEditorDocMode("script");
        if (payload.vram.reset) resetCodeEditorDocVram(payload.vram.reset === true ? {} : payload.vram.reset);
        if (payload.vram.image) blitCodeEditorDocVramImage(payload.vram.image, payload.vram.options || {});
        if (payload.vram.pixel) {
          const pixel = normalizeCodeEditorDocPixelUpdate(payload.vram.pixel);
          if (pixel) setCodeEditorDocVramPixel(pixel.x, pixel.y, pixel.rgba);
        }
        if (Array.isArray(payload.vram.pixels)) setCodeEditorDocVramPixels(payload.vram.pixels);
      }
      updateCodeEditorDocViewportStatus();
    }

    window.MainComputerCodeEditorViewport = {
      getState() { return JSON.parse(JSON.stringify(codeEditorDocViewportState)); },
      loadDoc: loadCodeEditorDoc,
      setMode: setCodeEditorDocMode,
      setResolution: setCodeEditorDocResolution,
      applyPreset: applyCodeEditorDocPreset,
      setSnapMode: setCodeEditorDocSnapMode,
      reloadConfig: reloadCodeEditorDocConfig,
      pushFrame: pushCodeEditorDocFrame,
      runScript: runCodeEditorDocScript,
      vram: codeEditorDocVramApi,
      subscribe(callback) {
        if (typeof callback !== "function") return () => {};
        codeEditorDocViewportSubscribers.add(callback);
        return () => codeEditorDocViewportSubscribers.delete(callback);
      },
      documentTarget() {
        return {
          id: "code-editor.viewport.root",
          kind: "panel",
          label: "Documentation Viewport",
          owner: "code-editor.root",
          feature: "code-editor.feature.documentation-viewport",
        };
      },
    };

    if (codeEditorDocViewportRoot) {
      if (codeEditorDocScriptSource && !codeEditorDocScriptSource.value.trim()) codeEditorDocScriptSource.value = codeEditorDocDefaultScript;
      codeEditorDocViewportMode.addEventListener("change", () => setCodeEditorDocMode(codeEditorDocViewportMode.value));
      codeEditorDocLoad.addEventListener("click", () => loadCodeEditorDoc(codeEditorDocTarget.value));
      codeEditorDocResolution.addEventListener("change", () => applyCodeEditorDocPreset(codeEditorDocResolution.value));
      codeEditorDocApplyResolution.addEventListener("click", () => setCodeEditorDocResolution(codeEditorDocWidth.value, codeEditorDocHeight.value, "manual"));
      codeEditorDocSnap.addEventListener("change", () => setCodeEditorDocSnapMode(codeEditorDocSnap.value));
      codeEditorDocCollapse.addEventListener("click", () => setCodeEditorDocMode(codeEditorDocViewportState.mode === "collapsed" ? "docs" : "collapsed"));
      if (codeEditorDocScriptRun) codeEditorDocScriptRun.addEventListener("click", () => runCodeEditorDocScript());
      if (codeEditorDocVramReset) {
        codeEditorDocVramReset.addEventListener("click", () => {
          setCodeEditorDocMode("script");
          resetCodeEditorDocVram({width: 320, height: 200, fill: [0, 0, 0, 255]});
          clearCodeEditorDocScriptLog();
          appendCodeEditorDocScriptLog("log", ["VRAM reset", codeEditorDocVramApi.getSize()]);
        });
      }
      loadCodeEditorDocManifest().catch(() => {});
      reloadCodeEditorDocConfig().catch(() => {});
      updateCodeEditorDocViewportStatus();
    }
