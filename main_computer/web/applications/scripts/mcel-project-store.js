    const McelLabProjectStore = (() => {
      const storageKey = "main-computer.mcel-lab.project.v1";

      function now() {
        return new Date().toISOString();
      }

      function safeLocalStorage() {
        try {
          if (typeof window === "undefined" || !window.localStorage) return null;
          const testKey = `${storageKey}.probe`;
          window.localStorage.setItem(testKey, "1");
          window.localStorage.removeItem(testKey);
          return window.localStorage;
        } catch (error) {
          return null;
        }
      }

      function snapshot(state = {}) {
        return {
          version: 1,
          savedAt: now(),
          source: String(state.source || ""),
          selectedIndex: Number(state.selectedIndex || 0),
          theme: String(state.theme || "theme-machine"),
          mode: String(state.mode || "source"),
          scenario: String(state.scenario || "round-trip"),
          lastSerializerClean: Boolean(state.lastSerializerClean),
          note: "MCEL Lab snapshots store clean semantic source and UI state, never generated runtime DOM."
        };
      }

      function save(state = {}) {
        const store = safeLocalStorage();
        const data = snapshot(state);
        if (!store) {
          return {ok: false, snapshot: data, message: "localStorage is unavailable; project snapshot was not persisted."};
        }
        store.setItem(storageKey, JSON.stringify(data));
        return {ok: true, snapshot: data, message: `Saved MCEL Lab project snapshot at ${data.savedAt}.`};
      }

      function restore() {
        const store = safeLocalStorage();
        if (!store) return {ok: false, snapshot: null, message: "localStorage is unavailable; no project was restored."};
        const raw = store.getItem(storageKey);
        if (!raw) return {ok: false, snapshot: null, message: "No MCEL Lab project snapshot has been saved yet."};
        try {
          const data = JSON.parse(raw);
          return {ok: true, snapshot: data, message: `Restored MCEL Lab project snapshot from ${data.savedAt || "unknown time"}.`};
        } catch (error) {
          return {ok: false, snapshot: null, message: `Saved project snapshot is invalid JSON: ${error.message}`};
        }
      }

      function exportText(state = {}) {
        return JSON.stringify(snapshot(state), null, 2);
      }

      return Object.freeze({
        storageKey,
        snapshot,
        save,
        restore,
        exportText
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabProjectStore = McelLabProjectStore;
    }
