        buddhabrotStatus.textContent = `${plugin.label} ${complete ? "complete" : "rendering"} | ${pixels} pixels | orbit derivative distance estimate | x imaginary, y real`;
        if (!complete) setTimeout(() => requestAnimationFrame(step), delay);
      }

      ctx.fillStyle = "#030204";
      ctx.fillRect(0, 0, width, height);
      requestAnimationFrame(step);
    }

    function renderHolographicPlateBundle(plugin, runId) {
      const panel = buddhabrotCanvas.parentElement;
      const size = Math.max(180, Math.min(panel.clientWidth, panel.clientHeight || panel.clientWidth));
      const width = Math.floor(size);
      const height = Math.floor(size);
      buddhabrotCanvas.width = width;
      buddhabrotCanvas.height = height;
      const ctx = buddhabrotCanvas.getContext("2d", {willReadFrequently: true});
      const image = ctx.createImageData(width, height);
      const {realMin, realMax, imagMin, imagMax} = plugin.bounds;
      const params = {
        cReal: -0.7665007921679897,
        cImag: 0.18584959902110956,
        lambda: 0.1001281228777888,
        mu: 0.04261944380625325,
        nu: 2.5135431763160287,
        theta: 4.275615985442648,
        bias: 0.005922106060347232,
        referenceKx: 10.33612956151261,
        referenceKy: 8.407128368588815,
        escapeRadius: 12.0,
        maxIter: 180,
      };
      const expReal = params.lambda * Math.cos(params.theta);
      const expImag = params.lambda * Math.sin(params.theta);
      let row = 0;
      let pixels = 0;
      let sliceNumber = 0;
      let maxEnergy = 1e-9;

      function sinhClamped(value) {
        const x = Math.max(-12, Math.min(12, value));
        return (Math.exp(x) - Math.exp(-x)) / 2;
      }

      function coshClamped(value) {
        const x = Math.max(-12, Math.min(12, value));
        return (Math.exp(x) + Math.exp(-x)) / 2;
      }

      function sample(real, imag) {
        let zReal = real;
        let zImag = imag;
        let escapeIter = params.maxIter;
        let escaped = false;
        let energy = 0;
        for (let iter = 0; iter < params.maxIter; iter += 1) {
          const abs2 = zReal * zReal + zImag * zImag;
          const denom = 1 + abs2;
          const conjTermReal = (expReal * zReal + expImag * zImag) / denom;
          const conjTermImag = (expImag * zReal - expReal * zImag) / denom;
          const sinArgReal = params.nu * zReal;
          const sinArgImag = params.nu * zImag;
          const sinReal = Math.sin(sinArgReal) * coshClamped(sinArgImag);
          const sinImag = Math.cos(sinArgReal) * sinhClamped(sinArgImag);
          const nextReal = zReal * zReal - zImag * zImag + params.cReal + conjTermReal + params.mu * sinReal + params.bias;
          const nextImag = 2 * zReal * zImag + params.cImag + conjTermImag + params.mu * sinImag;
          zReal = nextReal;
          zImag = nextImag;
          const mag2 = zReal * zReal + zImag * zImag;
          energy += Math.log1p(Math.min(mag2, 1e12));
          if (!escaped && mag2 > params.escapeRadius * params.escapeRadius) {
            escapeIter = iter;
            escaped = true;
            break;
          }
        }
        const absz = Math.max(1e-12, Math.hypot(zReal, zImag));
        let smooth = escapeIter;
        if (escaped) {
          smooth = escapeIter + 1 - Math.log(Math.max(1e-12, Math.log(absz + 1e-12)) + 1e-12) / Math.log(2);
        }
        return {smooth: Math.max(0, Math.min(params.maxIter, smooth)), energy};
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

      function colorPixel(offset, real, imag, result) {
        maxEnergy = Math.max(maxEnergy, result.energy);
        const smoothNorm = Math.max(0, Math.min(1, result.smooth / params.maxIter));
        const energyNorm = Math.max(0, Math.min(1, result.energy / maxEnergy));
        const phase = 2 * Math.PI * (0.65 * smoothNorm + 0.35 * energyNorm);
        const amplitude = Math.max(0, Math.min(1, 0.35 + 0.65 * energyNorm));
        const referencePhase = params.referenceKx * real + params.referenceKy * imag;
        const hologram = Math.max(0, Math.min(1, (amplitude * amplitude + 1 + 2 * amplitude * Math.cos(phase - referencePhase)) / 4));
        const ridge = Math.pow(1 - smoothNorm, 0.62);
        image.data[offset] = Math.floor(28 + 210 * Math.max(hologram, ridge * 0.7));
        image.data[offset + 1] = Math.floor(24 + 165 * Math.max(energyNorm, hologram * 0.55));
        image.data[offset + 2] = Math.floor(42 + 190 * Math.max(1 - ridge, hologram * 0.8));
        image.data[offset + 3] = 255;
      }

      function step() {
        if (runId !== fractalRenderRun) return;
        const rowsPerSlice = Math.max(1, Math.min(32, Math.floor(currentOrbitsPerSlice() / 70)));
        const end = Math.min(height, row + rowsPerSlice);
        for (; row < end; row += 1) {
          for (let col = 0; col < width; col += 1) {
            // Axis convention for this viewport:
            // x-axis is imaginary, y-axis is real.
            const imag = imagMin + (col / Math.max(1, width - 1)) * (imagMax - imagMin);
            const real = realMin + (row / Math.max(1, height - 1)) * (realMax - realMin);
            colorPixel((row * width + col) * 4, real, imag, sample(real, imag));
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
        buddhabrotStatus.textContent = `${plugin.label} ${complete ? "complete" : "rendering"} | ${pixels} pixels | synthetic holographic interference | x imaginary, y real`;
        if (!complete) setTimeout(() => requestAnimationFrame(step), delay);
      }

      ctx.fillStyle = "#030204";
      ctx.fillRect(0, 0, width, height);
      requestAnimationFrame(step);
    }

    function pluginFormula(plugin, zReal, zImag, cReal, cImag, iter, state) {
      if (plugin.formula) return plugin.formula(zReal, zImag, cReal, cImag, iter, state);
      if (plugin.juliaC) {
        const cR = plugin.juliaC[0];
        const cI = plugin.juliaC[1];
        return [zReal * zReal - zImag * zImag + cR, 2 * zReal * zImag + cI];
      }
      if (plugin.wavelet) {
        const abs2 = zReal * zReal + zImag * zImag;
        const gauss = Math.exp(-abs2 / (2 * 0.9 * 0.9));
        const phase = 5.5 * zReal;
        return [
          zReal * zReal - zImag * zImag + cReal + 0.5 * gauss * Math.cos(phase),
          2 * zReal * zImag + cImag + 0.5 * gauss * Math.sin(phase),
        ];
      }
      if (plugin.alpha) {
        const alphaR = plugin.alpha[0];
        const alphaI = plugin.alpha[1];
        const radius = Math.max(1e-9, Math.hypot(zReal, zImag));
        const theta = Math.atan2(zImag, zReal);
        const logR = Math.log(radius);
        const mag = Math.exp(alphaR * logR - alphaI * theta);
        const angle = alphaR * theta + alphaI * logR;
        return [mag * Math.cos(angle) + cReal, mag * Math.sin(angle) + cImag];
      }
      if (plugin.godel) {
        const correction = 0.18 * Math.sin(iter * 0.23 + zReal * 3.0) * Math.exp(-Math.hypot(zReal, zImag));
        return [zReal * zReal - zImag * zImag + cReal - correction * zReal, 2 * zReal * zImag + cImag - correction * zImag];
      }
      return [zReal * zReal - zImag * zImag + cReal, 2 * zReal * zImag + cImag];
    }

    function renderEscapePlugin(plugin, runId) {
      const panel = buddhabrotCanvas.parentElement;
      const size = Math.max(180, Math.min(panel.clientWidth, panel.clientHeight || panel.clientWidth));
      const width = Math.floor(size);
      const height = Math.floor(size);
      buddhabrotCanvas.width = width;
      buddhabrotCanvas.height = height;
      const ctx = buddhabrotCanvas.getContext("2d", {willReadFrequently: true});
      const image = ctx.createImageData(width, height);
      const {realMin, realMax, imagMin, imagMax} = plugin.bounds;
      const maxIter = plugin.mode === "period" ? 120 : 180;
      const bailout = plugin.alpha ? 8.0 : 4.0;
      let row = 0;
      let pixels = 0;
      let sliceNumber = 0;

      function colorPixel(offset, iter, bounded, periodScore) {
        if (plugin.mode === "period") {
          const band = bounded ? Math.floor(32 + (periodScore % 12) * 18) : Math.floor(255 * iter / maxIter);
          image.data[offset] = bounded ? 40 : band;
          image.data[offset + 1] = bounded ? band : Math.floor(120 * iter / maxIter);
          image.data[offset + 2] = bounded ? 255 - band / 2 : 90;
        } else {
          const t = iter / maxIter;
          image.data[offset] = Math.floor(255 * Math.pow(1 - t, 0.7));
          image.data[offset + 1] = Math.floor(210 * Math.sin(t * Math.PI));
          image.data[offset + 2] = Math.floor(80 + 170 * t);
        }
        image.data[offset + 3] = 255;
      }

      function computePixel(px, py) {
        const imag = imagMin + (px / Math.max(1, width - 1)) * (imagMax - imagMin);
        const real = realMin + (py / Math.max(1, height - 1)) * (realMax - realMin);
        let cReal = real;
        let cImag = imag;
        let zReal = 0;
        let zImag = 0;
        if (plugin.juliaC || plugin.mobius || plugin.su2) {
          const p = pluginPoint(plugin, real, imag);
          zReal = p[0];
          zImag = p[1];
          cReal = plugin.juliaC?.[0] || cReal;
          cImag = plugin.juliaC?.[1] || cImag;
        } else if (plugin.warp) {
          const p = pluginPoint(plugin, real, imag);
          cReal = p[0];
          cImag = p[1];
        }
        const state = {};
        let lastReal = zReal;
        let lastImag = zImag;
        let periodScore = 0;
        for (let iter = 0; iter < maxIter; iter += 1) {
