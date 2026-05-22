          const next = pluginFormula(plugin, zReal, zImag, cReal, cImag, iter, state);
          zReal = next[0];
          zImag = next[1];
          if (iter > maxIter - 24) {
            periodScore += Math.hypot(zReal - lastReal, zImag - lastImag) < 0.02 ? 1 : 0;
            lastReal = zReal;
            lastImag = zImag;
          }
          if (!Number.isFinite(zReal) || !Number.isFinite(zImag) || zReal * zReal + zImag * zImag > bailout * bailout) {
            return {iter, bounded: false, periodScore};
          }
        }
        return {iter: maxIter, bounded: true, periodScore};
      }

      function step() {
        if (runId !== fractalRenderRun) return;
        const rowsPerSlice = Math.max(1, Math.min(48, Math.floor(currentOrbitsPerSlice() / 50)));
        const end = Math.min(height, row + rowsPerSlice);
        for (; row < end; row += 1) {
          for (let col = 0; col < width; col += 1) {
            const res = computePixel(col, row);
            colorPixel((row * width + col) * 4, res.iter, res.bounded, res.periodScore);
            pixels += 1;
          }
        }
        sliceNumber += 1;
        ctx.putImageData(image, 0, 0);
        buddhabrotCanvas.dataset.rendered = "true";
        buddhabrotCanvas.dataset.samples = String(pixels);
        buddhabrotCanvas.dataset.slices = String(sliceNumber);
        const complete = row >= height;
        const delay = currentDelayMs();
        buddhabrotStatus.textContent = `${plugin.label} ${complete ? "complete" : "rendering"} | ${pixels} pixels | ${realMin}..${realMax} real, ${imagMin}..${imagMax} imaginary`;
        if (!complete) setTimeout(() => requestAnimationFrame(step), delay);
      }

      ctx.fillStyle = "#030204";
      ctx.fillRect(0, 0, width, height);
      requestAnimationFrame(step);
    }

    tickClock();
    setInterval(tickClock, 1000);
    renderBuddhabrot();
    loadReadySystems();

    restoreSession();
    if (session.entries.length === 0) {
      addEntry("main computer", "Bridge viewport online. Select a control or enter a command-shaped prompt.", "assistant", {renderMode: "plain"});
    }
    loadProjects().catch((error) => {
      statusLine.textContent = "project load failed";
      addEntry("error", String(error.message || error), "error", {renderMode: "plain"});
    });
    pollWorkspaceTimestamp();
    setInterval(pollWorkspaceTimestamp, 4000);
