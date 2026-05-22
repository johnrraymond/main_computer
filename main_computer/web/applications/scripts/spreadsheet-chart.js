      context.clearRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#010201";
      context.fillRect(0, 0, canvas.width, canvas.height);
      if (!spreadsheetSelectedRange) {
        spreadsheetPlotStatus.textContent = "select numeric cells first";
        return;
      }
      const sheet = spreadsheetActiveSheet();
      const numeric = (ref) => {
        const value = Number(sheet.cells[ref]?.value);
        return Number.isFinite(value) ? value : null;
      };
      let points = [];
      if (spreadsheetSelectedRange.colMax - spreadsheetSelectedRange.colMin === 1) {
        for (let row = spreadsheetSelectedRange.rowMin; row <= spreadsheetSelectedRange.rowMax; row += 1) {
          const x = numeric(spreadsheetCellRef(row, spreadsheetSelectedRange.colMin));
          const y = numeric(spreadsheetCellRef(row, spreadsheetSelectedRange.colMax));
          if (x != null && y != null) points.push({x, y});
        }
      } else {
        spreadsheetSelectedRange.cells.forEach((ref, index) => {
          const y = numeric(ref);
          if (y != null) points.push({x: index + 1, y});
        });
      }
      if (!points.length) {
        spreadsheetPlotStatus.textContent = "selection has no numeric values";
        return;
      }
      const xMin = Math.min(...points.map((point) => point.x));
      const xMax = Math.max(...points.map((point) => point.x));
      const yMin = Math.min(...points.map((point) => point.y));
      const yMax = Math.max(...points.map((point) => point.y));
      const pad = 28;
      const toX = (x) => pad + ((x - xMin) / (xMax - xMin || 1)) * (canvas.width - pad * 2);
      const toY = (y) => canvas.height - pad - ((y - yMin) / (yMax - yMin || 1)) * (canvas.height - pad * 2);
      context.strokeStyle = "#313828";
      context.lineWidth = 1;
      context.strokeRect(pad, pad, canvas.width - pad * 2, canvas.height - pad * 2);
      context.strokeStyle = "#a7d86d";
      context.lineWidth = 2;
      context.beginPath();
      points.forEach((point, index) => {
        const x = toX(point.x);
        const y = toY(point.y);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
      context.fillStyle = "#79d4f2";
      points.forEach((point) => {
        context.beginPath();
        context.arc(toX(point.x), toY(point.y), 3, 0, Math.PI * 2);
        context.fill();
      });
      context.fillStyle = "#f7c948";
      context.font = "700 11px Consolas, monospace";
      context.fillText(`${yMax}`, 4, pad + 4);
      context.fillText(`${yMin}`, 4, canvas.height - pad);
      spreadsheetPlotStatus.textContent = `plotted ${points.length} numeric points`;
    }
