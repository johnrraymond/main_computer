    const workerSettingsStorageKey = "main-computer-worker-settings-v1";
    const workerDefaultHubs = [
      {name: "Main Hub", url: "https://hub.example", role: "use-provide"},
      {name: "Friend Hub", url: "https://friend-hub.local", role: "use-only"}
    ];
    let workerSettingsLoaded = false;
    let workerHubs = [...workerDefaultHubs];

    function workerRoleLabel(role) {
      return role === "use-only"
        ? "Use only"
        : role === "provide-only"
          ? "Provide only"
          : role === "disabled"
            ? "Disabled"
            : "Use + provide";
    }

    function renderWorkerHubs() {
      if (!workerHubList) return;
      workerHubList.innerHTML = "";
      workerHubs.forEach((hub, index) => {
        const card = document.createElement("article");
        card.className = "worker-hub-card";
        card.innerHTML = `
          <div>
            <strong></strong>
            <span></span>
          </div>
          <div class="worker-hub-badges">
            <span>${hub.role === "disabled" ? "Disabled" : "Configured"}</span>
            <span>${workerRoleLabel(hub.role)}</span>
          </div>
          <button type="button" data-worker-remove-hub="${index}">Remove</button>
        `;
        card.querySelector("strong").textContent = hub.name || "Hub";
        card.querySelector("div > span").textContent = hub.url || "No URL set";
        workerHubList.append(card);
      });
      if (workerHubCount) {
        const count = workerHubs.length;
        workerHubCount.textContent = `${count} hub${count === 1 ? "" : "s"} configured`;
      }
    }

    function readWorkerFormSettings() {
      return {
        remoteEnabled: Boolean(workerRemoteEnabled?.checked),
        rentalEnabled: Boolean(workerRentalEnabled?.checked),
        lockAiModel: Boolean(workerLockAiModel?.checked),
        hubs: workerHubs
      };
    }

    function saveWorkerSettings() {
      const settings = readWorkerFormSettings();
      try {
        localStorage.setItem(workerSettingsStorageKey, JSON.stringify(settings));
        if (workerSaveStatus) {
          workerSaveStatus.textContent = "Worker settings saved locally. Backend sync can attach to this form later.";
        }
      } catch {
        if (workerSaveStatus) {
          workerSaveStatus.textContent = "Worker settings could not be saved in this browser.";
        }
      }
    }

    function loadWorkerSettings() {
      if (workerSettingsLoaded) return;
      workerSettingsLoaded = true;
      try {
        const raw = localStorage.getItem(workerSettingsStorageKey);
        const parsed = raw ? JSON.parse(raw) : null;
        if (parsed && Array.isArray(parsed.hubs)) {
          workerHubs = parsed.hubs
            .map((hub) => ({
              name: String(hub.name || "").trim(),
              url: String(hub.url || "").trim(),
              role: String(hub.role || "use-provide")
            }))
            .filter((hub) => hub.name || hub.url);
        }
        if (workerRemoteEnabled && parsed && typeof parsed.remoteEnabled === "boolean") {
          workerRemoteEnabled.checked = parsed.remoteEnabled;
        }
        if (workerRentalEnabled && parsed && typeof parsed.rentalEnabled === "boolean") {
          workerRentalEnabled.checked = parsed.rentalEnabled;
        }
        if (workerLockAiModel && parsed && typeof parsed.lockAiModel === "boolean") {
          workerLockAiModel.checked = parsed.lockAiModel;
        }
      } catch {
        workerHubs = [...workerDefaultHubs];
      }
      renderWorkerHubs();
    }

    function initWorkerApp() {
      loadWorkerSettings();
      renderWorkerHubs();
      if (workerAddHubForm && !workerAddHubForm.dataset.workerBound) {
        workerAddHubForm.dataset.workerBound = "true";
        workerAddHubForm.addEventListener("submit", (event) => {
          event.preventDefault();
          const name = String(workerHubName?.value || "").trim() || "Hub";
          const url = String(workerHubUrl?.value || "").trim();
          const role = String(workerHubRole?.value || "use-provide");
          if (!url) {
            if (workerSaveStatus) workerSaveStatus.textContent = "Enter a hub URL before adding it.";
            return;
          }
          workerHubs.push({name, url, role});
          renderWorkerHubs();
          if (workerSaveStatus) workerSaveStatus.textContent = `${name} added. Save settings to keep it.`;
        });
      }
      if (workerHubList && !workerHubList.dataset.workerBound) {
        workerHubList.dataset.workerBound = "true";
        workerHubList.addEventListener("click", (event) => {
          const removeButton = event.target instanceof Element
            ? event.target.closest("[data-worker-remove-hub]")
            : null;
          if (!removeButton) return;
          const index = Number(removeButton.getAttribute("data-worker-remove-hub"));
          if (!Number.isInteger(index) || index < 0 || index >= workerHubs.length) return;
          const [removed] = workerHubs.splice(index, 1);
          renderWorkerHubs();
          if (workerSaveStatus) workerSaveStatus.textContent = `${removed?.name || "Hub"} removed. Save settings to keep the change.`;
        });
      }
      if (workerSaveSettings && !workerSaveSettings.dataset.workerBound) {
        workerSaveSettings.dataset.workerBound = "true";
        workerSaveSettings.addEventListener("click", saveWorkerSettings);
      }
      if (workerPauseRentals && !workerPauseRentals.dataset.workerBound) {
        workerPauseRentals.dataset.workerBound = "true";
        workerPauseRentals.addEventListener("click", () => {
          if (workerRentalEnabled) workerRentalEnabled.checked = false;
          if (workerSaveStatus) workerSaveStatus.textContent = "Rentals paused locally. Save settings to keep rentals off.";
        });
      }
      if (workerTestHubs && !workerTestHubs.dataset.workerBound) {
        workerTestHubs.dataset.workerBound = "true";
        workerTestHubs.addEventListener("click", () => {
          if (workerSaveStatus) {
            workerSaveStatus.textContent = `Hub test queued for ${workerHubs.length} configured hub${workerHubs.length === 1 ? "" : "s"} once backend wiring is attached.`;
          }
        });
      }
    }
