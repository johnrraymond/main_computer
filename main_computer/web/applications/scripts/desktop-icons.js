    function ensureDesktopIcons() {
      if (!desktopOverlay || desktopOverlay.querySelector(".desktop-icon")) {
        return;
      }
      desktopApps.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "desktop-icon";
        button.dataset.app = item.app;
        button.innerHTML = `
          <span class="desktop-glyph">${item.glyph}</span>
          <strong>${item.title}</strong>
          <span>${item.summary}</span>
        `;
        button.addEventListener("click", () => setActiveApp(item.app));
        desktopOverlay.append(button);
      });
    }

    function layoutDesktopIcons(activeApp) {
      if (!desktopOverlay) return;
      ensureDesktopIcons();
      const icons = [...desktopOverlay.querySelectorAll(".desktop-icon")];
      const rect = desktopOverlay.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      const orbitX = Math.min(width * 0.34, 280);
      const orbitY = Math.min(height * 0.24, 190);
      const remaining = desktopApps.filter((item) => item.app !== activeApp);
      const slotCount = Math.max(1, remaining.length);
      let slotIndex = 0;
      icons.forEach((icon) => {
        const app = icon.dataset.app;
        const isActive = app === activeApp;
        let x = 0;
        let y = 0;
        let z = isActive ? 180 : -90;
        let scale = isActive ? 1.16 : 0.9;
        let rotX = isActive ? 0 : 8;
        let rotY = isActive ? 0 : -14;
        if (!isActive) {
          const progress = slotCount === 1 ? 0.5 : slotIndex / (slotCount - 1);
          const angle = (-0.85 + progress * 1.7) * Math.PI;
          const sweep = Math.sin(angle);
          x = Math.cos(angle) * orbitX;
          y = Math.sin(angle) * orbitY * 0.72;
          z = -160 + Math.round((1 - Math.abs(sweep)) * 70);
          scale = 0.86 + Math.max(0, sweep + 0.6) * 0.12;
          rotX = Math.round(-sweep * 12);
          rotY = Math.round(Math.cos(angle) * 20);
          slotIndex += 1;
        }
        icon.classList.toggle("active", isActive);
        icon.style.zIndex = String(isActive ? 40 : 10 + slotIndex);
        icon.style.transform = `translate3d(calc(-50% + ${x}px), calc(-50% + ${y}px), ${z}px) scale(${scale}) rotateX(${rotX}deg) rotateY(${rotY}deg)`;
      });
    }
