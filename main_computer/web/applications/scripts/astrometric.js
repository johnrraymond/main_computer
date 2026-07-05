const astrometricState = window.astrometricState || (window.astrometricState = {
      initialized: false,
      streamAttached: false,
      statusTimer: null,
      pointer: null,
      lastCameraSend: 0,
      pendingCameraPayload: null,
      lastStatus: null
    });

    const ASTROMETRIC_STATUS_ENDPOINT = "/api/applications/astrometric/status";
    const ASTROMETRIC_ACTION_ENDPOINT = "/api/applications/astrometric/action";
    const ASTROMETRIC_CAMERA_ENDPOINT = "/api/applications/astrometric/camera";
    const ASTROMETRIC_STREAM_ENDPOINT = "/api/applications/astrometric/stream.mjpg";

    function astrometricSetStatus(message, mode = "idle") {
      if (astrometricStatusPill) {
        astrometricStatusPill.textContent = message;
        astrometricStatusPill.dataset.state = mode;
      }
      if (astrometricViewportMessage && message) {
        astrometricViewportMessage.textContent = message;
      }
    }

    function astrometricPretty(value) {
      try {
        return JSON.stringify(value, null, 2);
      } catch {
        return String(value || "");
      }
    }

    function astrometricRendererReachable(status) {
      return Boolean(status?.renderer?.reachable && status?.renderer?.ok !== false);
    }

    function astrometricRendererStreamReady(status) {
      const renderer = status?.renderer || {};
      return astrometricRendererReachable(status) && Boolean(renderer.stream_ready || Number(renderer.frame_seq || 0) > 0);
    }

    function astrometricFormatScientific(value) {
      const number = Number(value);
      return Number.isFinite(number) ? number.toExponential(3) : "n/a";
    }

    function astrometricRenderStatus(status) {
      astrometricState.lastStatus = status;
      if (astrometricStatusJson) {
        astrometricStatusJson.textContent = astrometricPretty(status);
      }

      const renderer = status?.renderer || {};
      const docker = status?.docker || {};
      const reachable = astrometricRendererReachable(status);
      const streamReady = astrometricRendererStreamReady(status);
      const frameSeq = Number(renderer.frame_seq || 0);
      const frameMs = Number(renderer.frame_ms || 0);
      const camera = renderer.camera || {};

      if (astrometricDockerState) {
        astrometricDockerState.textContent = docker.available
          ? `compose ${Array.isArray(docker.compose_command) ? docker.compose_command.join(" ") : "available"}`
          : `not available${docker.error ? `: ${docker.error}` : ""}`;
      }
      if (astrometricGpuState) {
        if (reachable) {
          const gpuName = `${renderer.gl_vendor || "GL"} / ${renderer.gl_renderer || "renderer unknown"}`;
          astrometricGpuState.textContent = streamReady ? gpuName : `${gpuName} · waiting for first frame`;
        } else {
          astrometricGpuState.textContent = renderer.error || "renderer not reachable";
        }
      }
      if (astrometricFrameState) {
        astrometricFrameState.textContent = reachable
          ? `#${frameSeq} · ${Number.isFinite(frameMs) ? frameMs.toFixed(1) : "?"} ms · ${renderer.width || "?"}×${renderer.height || "?"}`
          : "no stream";
      }
      if (astrometricCameraState) {
        astrometricCameraState.textContent = reachable
          ? `r ${astrometricFormatScientific(camera.radius)} · az ${Number(camera.azimuth || 0).toFixed(2)} · el ${Number(camera.elevation || 0).toFixed(2)}`
          : "not connected";
      }
      if (astrometricEndpoint) {
        astrometricEndpoint.textContent = streamReady
          ? status.stream_path || ASTROMETRIC_STREAM_ENDPOINT
          : (reachable ? "renderer starting; waiting for first frame" : "renderer offline");
      }

      if (streamReady) {
        astrometricSetStatus("GPU stream live", "live");
        astrometricAttachStream();
      } else if (reachable) {
        const lastError = renderer.last_error ? `: ${renderer.last_error}` : "";
        astrometricSetStatus(`renderer starting${lastError}`, renderer.last_error ? "error" : "working");
        astrometricDetachStream();
      } else {
        astrometricSetStatus("renderer offline", "offline");
        astrometricDetachStream();
      }
    }

    async function astrometricFetchStatus() {
      const response = await fetch(ASTROMETRIC_STATUS_ENDPOINT, {cache: "no-store"});
      const status = await response.json();
      astrometricRenderStatus(status);
      return status;
    }

    async function astrometricPost(endpoint, payload) {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload || {})
      });
      const result = await response.json().catch(() => ({ok: false, error: "Invalid JSON response"}));
      if (!response.ok && !result.error) {
        result.error = `HTTP ${response.status}`;
      }
      return result;
    }

    function astrometricSetBusy(isBusy) {
      [astrometricStartButton, astrometricRestartButton, astrometricStopButton, astrometricResetCameraButton, astrometricRefreshButton].forEach((button) => {
        if (button) button.disabled = Boolean(isBusy);
      });
    }

    async function astrometricRendererAction(action) {
      astrometricSetBusy(true);
      astrometricSetStatus(`${action} requested`, "working");
      try {
        const result = await astrometricPost(ASTROMETRIC_ACTION_ENDPOINT, {action});
        if (astrometricStatusJson) {
          astrometricStatusJson.textContent = astrometricPretty(result);
        }
        if (result.status) {
          astrometricRenderStatus(result.status);
        }
        if (!result.ok) {
          throw new Error(result.message || result.error || `${action} failed`);
        }
        astrometricRenderStatus(result.status || await astrometricFetchStatus());
      } catch (error) {
        astrometricSetStatus(error?.message || `${action} failed`, "error");
      } finally {
        astrometricSetBusy(false);
      }
    }

    function astrometricAttachStream() {
      if (!astrometricStream || astrometricState.streamAttached) return;
      astrometricStream.src = `${ASTROMETRIC_STREAM_ENDPOINT}?t=${Date.now()}`;
      astrometricState.streamAttached = true;
      astrometricViewport?.classList.add("streaming");
    }

    function astrometricDetachStream() {
      if (astrometricStream) {
        astrometricStream.removeAttribute("src");
      }
      astrometricState.streamAttached = false;
      astrometricViewport?.classList.remove("streaming");
    }

    function astrometricScheduleStatusPolling() {
      if (astrometricState.statusTimer) return;
      astrometricState.statusTimer = window.setInterval(() => {
        if (currentApp === "astrometric") {
          astrometricFetchStatus().catch(() => {});
        }
      }, 3000);
    }

    function astrometricStopStatusPolling() {
      if (astrometricState.statusTimer) {
        window.clearInterval(astrometricState.statusTimer);
        astrometricState.statusTimer = null;
      }
    }

    function astrometricSendCameraNow(payload) {
      astrometricState.lastCameraSend = performance.now();
      astrometricPost(ASTROMETRIC_CAMERA_ENDPOINT, payload).then((result) => {
        if (result?.ok !== false) {
          astrometricRenderStatus({...(astrometricState.lastStatus || {}), renderer: {...((astrometricState.lastStatus || {}).renderer || {}), ...(result || {})}});
        }
      }).catch((error) => {
        astrometricSetStatus(error?.message || "camera control failed", "error");
      });
    }

    function astrometricQueueCamera(payload) {
      astrometricState.pendingCameraPayload = payload;
      const elapsed = performance.now() - astrometricState.lastCameraSend;
      if (elapsed > 40) {
        astrometricSendCameraNow(astrometricState.pendingCameraPayload);
        astrometricState.pendingCameraPayload = null;
        return;
      }
      window.setTimeout(() => {
        if (!astrometricState.pendingCameraPayload) return;
        const next = astrometricState.pendingCameraPayload;
        astrometricState.pendingCameraPayload = null;
        astrometricSendCameraNow(next);
      }, Math.max(12, 45 - elapsed));
    }

    function astrometricBindViewportControls() {
      if (!astrometricViewport || astrometricViewport.dataset.astrometricBound === "true") return;
      astrometricViewport.dataset.astrometricBound = "true";

      astrometricViewport.addEventListener("pointerdown", (event) => {
        astrometricViewport.setPointerCapture?.(event.pointerId);
        astrometricState.pointer = {
          id: event.pointerId,
          x: event.clientX,
          y: event.clientY,
          shift: event.shiftKey || event.button === 1 || event.button === 2
        };
        astrometricViewport.focus();
        event.preventDefault();
      });

      astrometricViewport.addEventListener("pointermove", (event) => {
        const pointer = astrometricState.pointer;
        if (!pointer || pointer.id !== event.pointerId) return;
        const dx = event.clientX - pointer.x;
        const dy = event.clientY - pointer.y;
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        const shift = event.shiftKey || pointer.shift;
        astrometricQueueCamera({type: shift ? "pan" : "orbit", dx, dy, shift});
        event.preventDefault();
      });

      ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) => {
        astrometricViewport.addEventListener(name, () => {
          astrometricState.pointer = null;
        });
      });

      astrometricViewport.addEventListener("wheel", (event) => {
        astrometricQueueCamera({type: "zoom", deltaY: event.deltaY});
        event.preventDefault();
      }, {passive: false});

      astrometricViewport.addEventListener("contextmenu", (event) => event.preventDefault());
    }

    function initAstrometricApp() {
      if (!astrometricApp) return;
      astrometricBindViewportControls();
      if (!astrometricState.initialized) {
        astrometricState.initialized = true;
        astrometricStartButton?.addEventListener("click", () => astrometricRendererAction("start"));
        astrometricRestartButton?.addEventListener("click", () => {
          astrometricDetachStream();
          astrometricRendererAction("restart");
        });
        astrometricStopButton?.addEventListener("click", () => {
          astrometricDetachStream();
          astrometricRendererAction("stop");
        });
        astrometricRefreshButton?.addEventListener("click", () => astrometricFetchStatus().catch((error) => astrometricSetStatus(error?.message || "status failed", "error")));
        astrometricResetCameraButton?.addEventListener("click", () => astrometricQueueCamera({type: "reset"}));
        astrometricQuality?.addEventListener("change", () => astrometricQueueCamera({type: "quality", jpeg_quality: Number(astrometricQuality.value || 86)}));
        astrometricStream?.addEventListener("error", () => {
          astrometricState.streamAttached = false;
          astrometricViewport?.classList.remove("streaming");
        });
      }
      astrometricScheduleStatusPolling();
      astrometricFetchStatus().catch((error) => {
        astrometricSetStatus(error?.message || "renderer status unavailable", "error");
      });
    }

    function pauseAstrometricApp() {
      astrometricStopStatusPolling();
      astrometricDetachStream();
    }
