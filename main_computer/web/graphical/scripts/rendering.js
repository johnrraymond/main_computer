          const failed = data.checks.filter((check) => !check.ok).length;
          addEntry("diagnostics", formatDiagnosticReport(data), failed || !response.ok ? "error" : "assistant", {renderMode: "diagnostic"});
          statusLine.textContent = failed ? "diagnostics failed" : "diagnostics passed";
          providerState.textContent = failed ? "diagnostic fault" : "diagnostic complete";
          return;
        }
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        const failed = data.checks.filter((check) => !check.ok).length;
        addEntry("diagnostics", formatDiagnosticReport(data), failed ? "error" : "assistant", {renderMode: "diagnostic"});
        statusLine.textContent = failed ? "diagnostics failed" : "diagnostics passed";
        providerState.textContent = failed ? "diagnostic fault" : "diagnostic complete";
      } catch (error) {
        addEntry("error", String(error.message || error), "error", {renderMode: "plain"});
        statusLine.textContent = "diagnostics error";
        providerState.textContent = "diagnostic fault";
      } finally {
        button.disabled = false;
      }
    }

    diagnosticButtons.forEach((button) => {
      button.addEventListener("click", () => runDiagnostic(button.dataset.diagnosticLevel, button));
    });

    ensureWidgetTickers();

    document.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-fullscreen-target]");
      if (!button) return;
      const widget = button.closest(".fullscreen-widget");
      if (!widget) return;
      try {
        if (document.fullscreenElement === widget) {
          await document.exitFullscreen();
        } else {
          await widget.requestFullscreen();
        }
      } catch (error) {
        addEntry("error", `fullscreen failed: ${String(error.message || error)}`, "error", {renderMode: "plain"});
      }
    });

    document.addEventListener("fullscreenchange", () => {
      document.querySelectorAll("[data-fullscreen-target]").forEach((button) => {
        button.textContent = document.fullscreenElement === button.closest(".fullscreen-widget") ? "Exit Full Screen" : "Full Screen";
      });
      if (document.fullscreenElement?.classList.contains("buddhabrot-panel")) {
        renderBuddhabrot();
      }
    });

    promptBox.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    promptBox.addEventListener("input", () => {
      session.draft = promptBox.value;
      saveSession();
    });

    fractalSelector?.addEventListener("change", () => {
      renderFractalViewport();
    });

    document.querySelectorAll(".control[data-prompt]").forEach((button) => {
      button.addEventListener("click", () => {
        promptBox.value = button.dataset.prompt;
        session.draft = promptBox.value;
        saveSession();
        promptBox.focus();
      });
    });

    function tickClock() {
      clock.textContent = new Date().toLocaleTimeString();
    }

    const fractalPlugins = {
      "standard-buddhabrot": {
        label: "Standard Buddhabrot",
        bounds: {realMin: -2.0, realMax: 1.2, imagMin: -1.8, imagMax: 1.8},
        stochastic: true,
        render: renderStandardBuddhabrot,
      },
      "mandelbrot": {
        label: "Mandelbrot parameter plane",
        bounds: {realMin: -2.2, realMax: 1.1, imagMin: -1.35, imagMax: 1.35},
        formula: (zReal, zImag, cReal, cImag) => [zReal * zReal - zImag * zImag + cReal, 2 * zReal * zImag + cImag],
      },
      "mandelbrot-distance-field": {
        label: "Distance-estimated Mandelbrot field",
        bounds: {realMin: -2.2, realMax: 1.1, imagMin: -1.35, imagMax: 1.35},
        render: renderMandelbrotDistanceField,
      },
      "cycle-harm": {
        label: "Cycle-harm parameter plane",
        bounds: {realMin: -2.2, realMax: 1.1, imagMin: -1.35, imagMax: 1.35},
        formula: (zReal, zImag, cReal, cImag, iter, state) => {
          const rho = 0.92;
          const alpha = 0.62;
          const beta = 0.09;
          const quota = 4.0;
          const h = rho * (state.h || 0) + Math.log1p(zReal * zReal + zImag * zImag);
          state.h = h;
          const d = h / (quota + h);
          const qReal = zReal * zReal - zImag * zImag + cReal;
          const qImag = 2 * zReal * zImag + cImag;
          return [qReal - alpha * d * zReal - beta * d * zImag, qImag - alpha * d * zImag + beta * d * zReal];
        },
      },
      "fractal-derivative": {
        label: "Fractal derivative periods",
        bounds: {realMin: -2.2, realMax: 1.0, imagMin: -1.2, imagMax: 1.2},
        mode: "period",
      },
      "curved-multibrot": {
        label: "Curved Multibrot",
        bounds: {realMin: -2.25, realMax: 1.25, imagMin: -1.55, imagMax: 1.55},
        warp: "egg",
        power: 3,
      },
      "mobius-julia": {
        label: "Mobius conjugated Julia",
        bounds: {realMin: -2.2, realMax: 2.2, imagMin: -2.2, imagMax: 2.2},
        juliaC: [-0.8, 0.156],
        mobius: true,
      },
      "su2-warped-julia": {
        label: "SU(2) warped Julia",
        bounds: {realMin: -1.6, realMax: 1.6, imagMin: -1.0, imagMax: 1.0},
        juliaC: [-0.8, 0.156],
        su2: true,
      },
      "wavelet-fractal": {
        label: "Covenant wavelet fractal",
        bounds: {realMin: -2.2, realMax: 1.4, imagMin: -1.4, imagMax: 1.4},
        wavelet: true,
      },
      "complex-alpha": {
        label: "Complex-alpha parameter set",
        bounds: {realMin: -8.0, realMax: 8.0, imagMin: -8.0, imagMax: 8.0},
        alpha: [1.18, 0.62],
      },
      "godel-mother": {
        label: "Godel mother fractal",
        bounds: {realMin: -2.2, realMax: 1.2, imagMin: -1.4, imagMax: 1.4},
        godel: true,
      },
      "holographic-plate-bundle": {
        label: "Holographic plate bundle",
        bounds: {realMin: -1.8, realMax: 1.8, imagMin: -1.8, imagMax: 1.8},
        render: renderHolographicPlateBundle,
      },
      "mother-period": {
        label: "Mother period map",
        bounds: {realMin: -2.2, realMax: 1.0, imagMin: -1.2, imagMax: 1.2},
        mode: "period",
      },
      "mcmc-zoom": {
        label: "MCMC Mandelbrot zoom",
        bounds: {realMin: -2.1, realMax: 1.1, imagMin: -0.9, imagMax: 0.9},
        formula: (zReal, zImag, cReal, cImag) => [zReal * zReal - zImag * zImag + cReal, 2 * zReal * zImag + cImag],
      },
      "text-orbit": {
        label: "Text orbit Mandelbrot grid",
        bounds: {realMin: -2.0, realMax: 2.0, imagMin: -2.0, imagMax: 2.0},
        formula: (zReal, zImag, cReal, cImag) => [zReal * zReal - zImag * zImag + cReal, 2 * zReal * zImag + cImag],
      },
    };

    function renderBuddhabrot() {
      renderFractalViewport();
    }

    function currentOrbitsPerSlice() {
      const raw = Number(buddhabrotOrbits?.value || 450);
      return Math.max(25, Math.min(5000, Math.floor(raw)));
    }

    function currentDelayMs() {
      const raw = Number(buddhabrotDelay?.value || 40);
      return Math.max(0, Math.min(2000, Math.floor(raw)));
    }

    function renderFractalViewport() {
      if (!buddhabrotCanvas) return;
      fractalRenderRun += 1;
      const runId = fractalRenderRun;
      const pluginKey = fractalSelector?.value || "standard-buddhabrot";
      const plugin = fractalPlugins[pluginKey] || fractalPlugins["standard-buddhabrot"];
      buddhabrotCanvas.dataset.plugin = pluginKey;
      buddhabrotCanvas.dataset.rendered = "false";
      buddhabrotCanvas.dataset.samples = "0";
      buddhabrotCanvas.dataset.slices = "0";
      buddhabrotStatus.textContent = `${plugin.label} loading`;
      if (plugin.render) {
        plugin.render(plugin, runId);
      } else {
        renderEscapePlugin(plugin, runId);
      }
    }

    function renderStandardBuddhabrot(plugin, runId) {
      const panel = buddhabrotCanvas.parentElement;
      const size = Math.max(180, Math.min(panel.clientWidth, panel.clientHeight || panel.clientWidth));
      const width = Math.floor(size);
      const height = Math.floor(size);
      buddhabrotCanvas.width = width;
      buddhabrotCanvas.height = height;
      const ctx = buddhabrotCanvas.getContext("2d", {willReadFrequently: false});
      const hits = new Uint16Array(width * height);
      const {realMin, realMax, imagMin, imagMax} = plugin.bounds;
      const maxIter = 160;
      let samples = 0;
      let maxHit = 1;
      let sliceNumber = 0;

      function plotOrbit(cReal, cImag) {
        let zReal = 0;
        let zImag = 0;
        const orbit = [];
        for (let iter = 0; iter < maxIter; iter += 1) {
          const nextReal = zReal * zReal - zImag * zImag + cReal;
          const nextImag = 2 * zReal * zImag + cImag;
          zReal = nextReal;
          zImag = nextImag;
          orbit.push([zReal, zImag]);
          if (zReal * zReal + zImag * zImag > 4) {
            for (const point of orbit) {
              const real = point[0];
              const imag = point[1];
              if (real < realMin || real > realMax || imag < imagMin || imag > imagMax) continue;
              // Axis convention for this viewport:
              // x-axis is imaginary, y-axis is real.
              const x = Math.floor(((imag - imagMin) / (imagMax - imagMin)) * (width - 1));
              const y = Math.floor(((real - realMin) / (realMax - realMin)) * (height - 1));
              const index = y * width + x;
              const value = hits[index] + 1;
              hits[index] = value;
              if (value > maxHit) maxHit = value;
            }
            return;
          }
        }
      }

      function draw() {
        const image = ctx.createImageData(width, height);
        const logMax = Math.log(maxHit + 1);
        for (let i = 0; i < hits.length; i += 1) {
          const value = Math.log(hits[i] + 1) / logMax;
          const offset = i * 4;
          image.data[offset] = Math.floor(255 * Math.min(1, value * 1.4));
          image.data[offset + 1] = Math.floor(210 * Math.pow(value, 0.75));
          image.data[offset + 2] = Math.floor(120 + 135 * value);
          image.data[offset + 3] = value > 0 ? 255 : 255;
        }
        ctx.putImageData(image, 0, 0);
        ctx.strokeStyle = "rgba(115, 214, 255, 0.36)";
        ctx.lineWidth = 1;
        const zeroImagX = ((0 - imagMin) / (imagMax - imagMin)) * width;
        const zeroRealY = ((0 - realMin) / (realMax - realMin)) * height;
        ctx.beginPath();
        ctx.moveTo(zeroImagX, 0);
        ctx.lineTo(zeroImagX, height);
        ctx.moveTo(0, zeroRealY);
        ctx.lineTo(width, zeroRealY);
        ctx.stroke();
      }

      function step() {
        if (runId !== fractalRenderRun) return;
        const orbitsThisSlice = currentOrbitsPerSlice();
        const end = samples + orbitsThisSlice;
        for (; samples < end; samples += 1) {
          const cReal = realMin + Math.random() * (realMax - realMin);
          const cImag = imagMin + Math.random() * (imagMax - imagMin);
          plotOrbit(cReal, cImag);
        }
        sliceNumber += 1;
        draw();
        buddhabrotCanvas.dataset.rendered = "true";
        buddhabrotCanvas.dataset.samples = String(samples);
        buddhabrotCanvas.dataset.slices = String(sliceNumber);
        const delay = currentDelayMs();
        buddhabrotStatus.textContent = `buddhabrot live ${samples} orbits | Standard Buddhabrot | slice ${orbitsThisSlice} | delay ${delay}ms | x imaginary, y real`;
        setTimeout(() => requestAnimationFrame(step), delay);
      }

      ctx.fillStyle = "#030204";
      ctx.fillRect(0, 0, width, height);
      requestAnimationFrame(step);
    }

    function pluginPoint(plugin, real, imag) {
      if (plugin.warp === "egg") {
        const r = Math.hypot(real, imag);
        const theta = Math.atan2(imag, real);
        const thetaP = theta + 0.08 * r;
        const rp = r * (1.0 + 0.22 * Math.cos(thetaP));
        return [rp * Math.cos(thetaP), rp * Math.sin(thetaP) * (1.0 + 0.14 * rp * rp)];
      }
      if (plugin.mobius) {
        const denomReal = real + 1.0;
        const denomImag = imag;
        const denom = denomReal * denomReal + denomImag * denomImag || 1e-9;
        return [(2 * real * denomReal + 2 * imag * denomImag) / denom, (2 * imag * denomReal - 2 * real * denomImag) / denom];
      }
      if (plugin.su2) {
        const aReal = Math.cos(0.35 / 2);
        const bReal = 0.3 * Math.sin(0.35 / 2);
        const bImag = 0.7 * Math.sin(0.35 / 2);
        const nr = aReal * real + bReal;
        const ni = aReal * imag + bImag;
        const dr = -bReal * real + bImag * imag + aReal;
        const di = -bReal * imag - bImag * real;
        const den = dr * dr + di * di || 1e-9;
        return [(nr * dr + ni * di) / den, (ni * dr - nr * di) / den];
      }
      return [real, imag];
    }

    function renderMandelbrotDistanceField(plugin, runId) {
      const panel = buddhabrotCanvas.parentElement;
      const size = Math.max(180, Math.min(panel.clientWidth, panel.clientHeight || panel.clientWidth));
      const width = Math.floor(size);
      const height = Math.floor(size);
      buddhabrotCanvas.width = width;
      buddhabrotCanvas.height = height;
      const ctx = buddhabrotCanvas.getContext("2d", {willReadFrequently: true});
      const image = ctx.createImageData(width, height);
      const {realMin, realMax, imagMin, imagMax} = plugin.bounds;
      const maxIter = 220;
      const bailout = 16.0;
      let row = 0;
      let pixels = 0;
      let sliceNumber = 0;

      function estimate(cReal, cImag) {
        let zReal = 0;
        let zImag = 0;
        let dzReal = 0;
        let dzImag = 0;
        for (let iter = 0; iter < maxIter; iter += 1) {
          const nextDzReal = 2 * (zReal * dzReal - zImag * dzImag) + 1;
          const nextDzImag = 2 * (zReal * dzImag + zImag * dzReal);
          const nextReal = zReal * zReal - zImag * zImag + cReal;
          const nextImag = 2 * zReal * zImag + cImag;
          dzReal = nextDzReal;
          dzImag = nextDzImag;
          zReal = nextReal;
          zImag = nextImag;
          const radius2 = zReal * zReal + zImag * zImag;
          if (radius2 > bailout * bailout) {
            const radius = Math.sqrt(radius2);
            const derivative = Math.max(1e-9, Math.hypot(dzReal, dzImag));
            const distance = Math.max(0, 0.5 * radius * Math.log(radius) / derivative);
            const smooth = iter + 1 - Math.log2(Math.max(1e-9, Math.log2(radius)));
            return {bounded: false, iter, distance, smooth};
          }
        }
        return {bounded: true, iter: maxIter, distance: 0, smooth: maxIter};
      }

      function colorPixel(offset, result) {
        if (result.bounded) {
          image.data[offset] = 4;
          image.data[offset + 1] = 8;
          image.data[offset + 2] = 12;
          image.data[offset + 3] = 255;
          return;
        }
        const scale = Math.min(1, Math.max(0, -Math.log10(result.distance + 1e-10) / 8));
        const band = 0.5 + 0.5 * Math.cos(34 * Math.sqrt(result.distance + 1e-9));
        const ridge = Math.pow(scale, 2.4);
        const orbitTone = result.smooth / maxIter;
        image.data[offset] = Math.floor(22 + 220 * ridge);
        image.data[offset + 1] = Math.floor(34 + 170 * Math.max(ridge, band * 0.45));
        image.data[offset + 2] = Math.floor(58 + 190 * Math.max(orbitTone, ridge * 0.8));
        image.data[offset + 3] = 255;
      }

      function drawAxes() {
        ctx.putImageData(image, 0, 0);
        ctx.strokeStyle = "rgba(115, 214, 255, 0.32)";
        ctx.lineWidth = 1;
        const zeroImagX = ((0 - imagMin) / (imagMax - imagMin)) * width;
        const zeroRealY = ((0 - realMin) / (realMax - realMin)) * height;
        ctx.beginPath();
        ctx.moveTo(zeroImagX, 0);
        ctx.lineTo(zeroImagX, height);
        ctx.moveTo(0, zeroRealY);
        ctx.lineTo(width, zeroRealY);
        ctx.stroke();
      }

      function step() {
        if (runId !== fractalRenderRun) return;
        const rowsPerSlice = Math.max(1, Math.min(48, Math.floor(currentOrbitsPerSlice() / 50)));
        const end = Math.min(height, row + rowsPerSlice);
        for (; row < end; row += 1) {
          for (let col = 0; col < width; col += 1) {
            // Axis convention for this viewport:
            // x-axis is imaginary, y-axis is real.
            const imag = imagMin + (col / Math.max(1, width - 1)) * (imagMax - imagMin);
            const real = realMin + (row / Math.max(1, height - 1)) * (realMax - realMin);
            colorPixel((row * width + col) * 4, estimate(real, imag));
            pixels += 1;
          }
        }
        sliceNumber += 1;
        drawAxes();
        buddhabrotCanvas.dataset.rendered = "true";
        buddhabrotCanvas.dataset.samples = String(pixels);
        buddhabrotCanvas.dataset.slices = String(sliceNumber);
        const complete = row >= height;
        const delay = currentDelayMs();
