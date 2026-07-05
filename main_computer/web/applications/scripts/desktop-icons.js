    const desktopIconSvgByApp = {
      webgl: `
        <path d="M4.5 6.5h15v11h-15z" />
        <path d="M4.5 10.5h15" />
        <path d="M9.5 6.5v11" />
        <path d="M14.5 6.5v11" />
      `,
      astrometric: `
        <circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none" />
        <path d="M3.8 13.5c4.2-4.4 12.2-5.4 16.4-2" />
        <path d="M4.5 16.5c3.8 2.4 10.9 2.4 15 0" />
        <path d="M7 6.5c3.4-1.7 7.6-1.2 10 1.3" />
      `,
      calculator: `
        <rect x="5" y="3.5" width="14" height="17" rx="3" />
        <path d="M8 7.5h8" />
        <path d="M8.5 12h2" />
        <path d="M13.5 12h2" />
        <path d="M8.5 16h2" />
        <path d="M13.5 16h2" />
        <path d="M12 11v6" />
      `,
      document: `
        <path d="M7 3.5h7l4 4v13H7z" />
        <path d="M14 3.5v4h4" />
        <path d="M9.5 11h6" />
        <path d="M9.5 14h6" />
        <path d="M9.5 17h4.5" />
      `,
      spreadsheet: `
        <rect x="4.5" y="5" width="15" height="14" rx="2" />
        <path d="M4.5 9.5h15" />
        <path d="M9.5 5v14" />
        <path d="M14.5 5v14" />
        <path d="M4.5 14h15" />
      `,
      onlyoffice: `
        <path d="M7 3.5h7l4 4v13H7z" />
        <path d="M14 3.5v4h4" />
        <path d="M9.5 12h6" />
        <path d="M9.5 15h6" />
        <path d="M9.5 18h4" />
      `,
      "task-manager": `
        <path d="M5 18.5h14" />
        <path d="M7.5 16V11" />
        <path d="M12 16V7" />
        <path d="M16.5 16V9" />
        <path d="M5.5 8.5h4l2-2 2.5 5 2-3h2.5" />
      `,
      terminal: `
        <path d="M5.5 7.5 9.5 11l-4 3.5" />
        <path d="M11.5 15.5h6" />
        <path d="M4.5 4.5h15v15h-15z" />
      `,
      "chat-console": `
        <path d="M6 6.5h12a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-6l-4 3v-3H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z" />
        <path d="M8.5 10.5h2" />
        <path d="M12 10.5h2" />
        <path d="M15.5 10.5h.01" />
      `,
      "ai-control": `
        <circle cx="9" cy="9" r="2.5" />
        <circle cx="15.5" cy="8" r="1.5" />
        <circle cx="14" cy="15.5" r="2" />
        <path d="M10.8 10.2 14 14" />
        <path d="M11.2 8.3 14 8" />
        <path d="M6.5 15.5c1.2-2.2 3.1-3.5 5.5-3.5 2.2 0 4.2 1 5.5 3" />
      `,
      email: `
        <rect x="4" y="6.5" width="16" height="11" rx="2" />
        <path d="m5 8 7 5 7-5" />
      `,
      "git-tools": `
        <circle cx="7" cy="6.5" r="1.75" />
        <circle cx="17" cy="9.5" r="1.75" />
        <circle cx="7" cy="17.5" r="1.75" />
        <path d="M8.7 6.5h4a3 3 0 0 1 3 3v0" />
        <path d="M8.7 17.5h4a3 3 0 0 0 3-3v-1" />
        <path d="M7 8.5v7" />
      `,
      "code-editor": `
        <path d="M9.5 7.5 6 11l3.5 3.5" />
        <path d="M14.5 7.5 18 11l-3.5 3.5" />
        <path d="M13 6 11 16" />
      `,
      "file-explorer": `
        <path d="M4.5 8h5l1.5-2h8a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19.5 18h-15A1.5 1.5 0 0 1 3 16.5v-7A1.5 1.5 0 0 1 4.5 8z" />
        <path d="M3 10.5h18" />
      `,
      "game-editor": `
        <path d="M5 16.5 15.5 6l2.5 2.5L7.5 19H5z" />
        <path d="m14 7.5 2.5 2.5" />
        <path d="M4.5 19.5h15" />
      `,
      "website-builder": `
        <rect x="4" y="5" width="16" height="14" rx="2" />
        <path d="M4 8.5h16" />
        <path d="M8 12h3" />
        <path d="M13 12h4" />
        <path d="M8 15.5h9" />
      `,
      "mcel-lab": `
        <circle cx="8" cy="8" r="2" />
        <circle cx="16" cy="8" r="2" />
        <circle cx="12" cy="16" r="2.25" />
        <path d="M9.6 9.3 10.8 11" />
        <path d="M14.4 9.3 13.2 11" />
        <path d="M9.8 7.8h4.4" />
      `,
      worker: `
        <circle cx="12" cy="12" r="3" />
        <path d="M12 5.5v2" />
        <path d="M12 16.5v2" />
        <path d="M18.5 12h-2" />
        <path d="M7.5 12h-2" />
        <path d="m16.6 7.4-1.4 1.4" />
        <path d="m8.8 15.2-1.4 1.4" />
        <path d="m16.6 16.6-1.4-1.4" />
        <path d="M8.8 8.8 7.4 7.4" />
      `,
      wallet: `
        <path d="M5.5 8h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-6a3 3 0 0 1 3-3h10" />
        <path d="M16 12h4" />
        <circle cx="15.5" cy="12" r=".75" fill="currentColor" stroke="none" />
      `,
    };

    function desktopIconSvg(app) {
      const markup = desktopIconSvgByApp[app];
      if (!markup) {
        return "";
      }
      return `<svg class="desktop-glyph-svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${markup}</svg>`;
    }

    function desktopGlyphMarkup(item) {
      const svg = desktopIconSvg(item.app);
      if (svg) {
        return `<span class="desktop-glyph" aria-hidden="true">${svg}</span>`;
      }
      return `<span class="desktop-glyph" aria-hidden="true">${item.glyph}</span>`;
    }

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
          ${desktopGlyphMarkup(item)}
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
      const isDesktopHome = activeApp === "desktop";
      desktopOverlay.classList.toggle("desktop-home", isDesktopHome);
      if (isDesktopHome) {
        icons.forEach((icon) => {
          icon.classList.remove("active");
          icon.style.removeProperty("z-index");
          icon.style.removeProperty("transform");
        });
        return;
      }
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
