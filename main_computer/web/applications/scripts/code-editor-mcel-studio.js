    (() => {
      const root = document.querySelector("#code-editor-app");
      if (!root) return;

      const sourceEditor = root.querySelector("#code-studio-source-editor");
      const gutter = root.querySelector("#code-studio-line-gutter");
      const runtimePreview = root.querySelector("#code-studio-runtime-preview");
      const serializedOutput = root.querySelector("#code-studio-serialized-output");
      const contractReport = root.querySelector("#code-studio-contract-report");
      const contractEnvelope = root.querySelector("#code-studio-contract-envelope");
      const status = root.querySelector("#code-studio-status");
      const runtimeState = root.querySelector("#code-studio-runtime-state");
      const validateButton = root.querySelector("#code-studio-validate");
      const serializeButton = root.querySelector("#code-studio-serialize");
      const mountButton = root.querySelector("#code-studio-mount-runtime");
      const damageButton = root.querySelector("#code-studio-damage-runtime");
      const repairButton = root.querySelector("#code-studio-repair-runtime");
      const commitButton = root.querySelector("#code-studio-commit-runtime");
      const panes = [...root.querySelectorAll("[data-code-studio-pane]")];
      const tabButtons = [...root.querySelectorAll("[data-code-studio-tab]")];

      if (!sourceEditor || !runtimePreview) return;

      const studioState = {
        mounted: false,
        dirty: false,
        damaged: false,
        selectedPath: "src/app.js",
        lastReport: null,
      };

      function escapeHtml(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function parseSource() {
        const parser = new DOMParser();
        const doc = parser.parseFromString(sourceEditor.value || "", "text/html");
        const parseError = doc.querySelector("parsererror");
        const workspace = doc.querySelector('[data-mc-component="code-workspace"]');
        return {doc, parseError, workspace};
      }

      function workspaceFields() {
        const {workspace} = parseSource();
        if (!workspace) return {files: [], title: "", summary: ""};
        const title = workspace.querySelector('[data-mc-field="workspace-title"]')?.textContent?.trim() || "";
        const summary = workspace.querySelector('[data-mc-field="workspace-summary"]')?.textContent?.trim() || "";
        const files = [...workspace.querySelectorAll('[data-mc-component="code-file"]')].map((node, index) => ({
          index,
          path: node.getAttribute("data-mc-file-path") || `untitled-${index + 1}.txt`,
          language: node.getAttribute("data-mc-language") || "plaintext",
          field: node.getAttribute("data-mc-field") || `file-${index + 1}`,
          required: node.hasAttribute("data-mc-required"),
          value: node.textContent.replace(/^\n+|\s+$/g, ""),
        }));
        return {files, title, summary};
      }

      function selectedFile(fields = workspaceFields()) {
        return fields.files.find((file) => file.path === studioState.selectedPath) || fields.files[0] || null;
      }

      function setStatus(message) {
        if (status) status.textContent = message;
      }

      function setRuntimeLabel() {
        if (!runtimeState) return;
        const bits = [
          studioState.mounted ? "mounted" : "not mounted",
          studioState.dirty ? "dirty" : "clean",
          studioState.damaged ? "damaged" : "healthy",
        ];
        runtimeState.textContent = `runtime: ${bits.join(" / ")}`;
      }

      function syncLineGutter() {
        if (!gutter) return;
        const lineCount = Math.max(1, String(sourceEditor.value || "").split(/\r\n|\r|\n/).length);
        gutter.textContent = Array.from({length: lineCount}, (_, index) => index + 1).join("\n");
      }

      function showPane(name) {
        panes.forEach((pane) => pane.classList.toggle("active", pane.dataset.codeStudioPane === name));
        tabButtons.forEach((button) => button.classList.toggle("active", button.dataset.codeStudioTab === name));
      }

      function generatedAttrs(kind, key) {
        return `data-mc-generated="runtime" data-mc-serialize="omit" data-mc-runtime-kind="${escapeHtml(kind)}" data-mc-runtime-key="${escapeHtml(key)}"`;
      }

      function renderRuntime() {
        const fields = workspaceFields();
        const file = selectedFile(fields);
        const fileButtons = fields.files.map((entry) => `
          <button type="button" data-code-studio-runtime-file="${escapeHtml(entry.path)}" ${entry.path === (file?.path || "") ? 'aria-current="true"' : ""}>
            ${escapeHtml(entry.path)}
          </button>
        `).join("");

        runtimePreview.innerHTML = `
          <section class="code-studio-runtime-window" ${generatedAttrs("runtime-envelope", "code-studio")}>
            <header class="code-studio-runtime-header" ${generatedAttrs("runtime-header", "workbench-header")}>
              <strong>${escapeHtml(fields.title || "Untitled MCEL workspace")}</strong>
              <span>${escapeHtml(fields.summary || "Runtime generated from author source.")}</span>
            </header>
            <div class="code-studio-runtime-layout" ${generatedAttrs("runtime-layout", "workbench-layout")}>
              <aside class="code-studio-runtime-files" ${generatedAttrs("runtime-file-list", "open-files")}>
                <strong>Generated file explorer</strong>
                ${fileButtons || "<p>No files found in source.</p>"}
              </aside>
              <article class="code-studio-runtime-editor" ${generatedAttrs("runtime-editor", file?.path || "empty")}>
                <label>
                  <span>${escapeHtml(file?.path || "No source file")}</span>
                  <textarea id="code-studio-runtime-draft" spellcheck="false">${escapeHtml(file?.value || "")}</textarea>
                </label>
                <div class="code-studio-runtime-badges" ${generatedAttrs("runtime-badges", "proof-badges")}>
                  <span>generated editor chrome</span>
                  <span>runtime-only dirty state</span>
                  <span>serialize=omit</span>
                  <span>repairable from source</span>
                </div>
              </article>
            </div>
          </section>
        `;
        runtimePreview.querySelectorAll("[data-code-studio-runtime-file]").forEach((button) => {
          button.addEventListener("click", () => {
            studioState.selectedPath = button.dataset.codeStudioRuntimeFile || "";
            renderRuntime();
          });
        });
        const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
        if (draft) {
          draft.addEventListener("input", () => {
            studioState.dirty = true;
            studioState.damaged = false;
            setRuntimeLabel();
            setStatus("Runtime draft changed. Source is still unchanged until Commit editor draft.");
          });
        }
        studioState.mounted = true;
        studioState.damaged = false;
        setRuntimeLabel();
      }

      function validateSource() {
        const {parseError, workspace} = parseSource();
        const fields = workspaceFields();
        const file = selectedFile(fields);
        const checks = [
          {
            id: "mcel-code-editor-source-root",
            ok: Boolean(workspace) && !parseError,
            text: "Source has a code-workspace root and parses as HTML.",
          },
          {
            id: "mcel-code-editor-use-case",
            ok: workspace?.getAttribute("data-mc-use-case") === "source-safe-code-editor",
            text: "Source declares the source-safe-code-editor use case.",
          },
          {
            id: "mcel-code-editor-required-title",
            ok: Boolean(fields.title),
            text: "Workspace title is author-owned and required.",
          },
          {
            id: "mcel-code-editor-file-paths",
            ok: fields.files.length > 0 && fields.files.every((entry) => Boolean(entry.path)),
            text: "Each code file has an author-owned file path.",
          },
          {
            id: "mcel-code-editor-runtime-firewall",
            ok: !sourceEditor.value.includes('data-mc-generated="runtime"'),
            text: "Author source does not contain generated runtime chrome.",
          },
          {
            id: "mcel-code-editor-repair-base",
            ok: Boolean(file && file.value.trim()),
            text: "Selected file has enough source content to regenerate the runtime editor.",
          },
        ];
        const failed = checks.filter((check) => !check.ok);
        studioState.lastReport = {
          ok: failed.length === 0,
          useCase: "source-safe-code-editor",
          selectedPath: file?.path || "",
          checks,
          failed: failed.map((check) => check.id),
        };
        renderContractReport(studioState.lastReport);
        setStatus(studioState.lastReport.ok ? "Validation passed: source can mount, repair, and serialize." : `Validation blocked: ${failed.length} contract check(s) failed.`);
        return studioState.lastReport;
      }

      function renderContractReport(report = validateSource()) {
        const rows = report.checks.map((check) => `
          <div class="code-studio-contract-row ${check.ok ? "pass" : "fail"}">
            <strong>${check.ok ? "PASS" : "FAIL"} ${escapeHtml(check.id)}</strong>
            <span>${escapeHtml(check.text)}</span>
          </div>
        `).join("");
        contractReport.innerHTML = rows;
        const mcelEnvelope = typeof window.MCEL?.buildUserSpaceContract === "function"
          ? window.MCEL.buildUserSpaceContract({useCase: "source-safe-code-editor", surface: "code-editor"})
          : null;
        if (contractEnvelope) {
          contractEnvelope.textContent = JSON.stringify({
            useCase: "source-safe-code-editor",
            selectedPath: report.selectedPath,
            ok: report.ok,
            failed: report.failed,
            mcelClauses: mcelEnvelope?.clauses?.length || 0,
            userPlanningModel: [
              "author source is canonical",
              "runtime editor chrome is generated",
              "dirty state is runtime-only until commit",
              "serialization strips generated nodes",
              "repair regenerates from source"
            ],
          }, null, 2);
        }
        showPane("contract");
      }

      function serializeCleanSource() {
        const {doc, workspace} = parseSource();
        if (!workspace) {
          serializedOutput.textContent = "Cannot serialize: source-safe-code-editor root is missing.";
          showPane("serialized");
          return "";
        }
        doc.querySelectorAll('[data-mc-generated="runtime"], [data-mc-serialize="omit"]').forEach((node) => node.remove());
        const clean = workspace.outerHTML.trim();
        serializedOutput.textContent = clean;
        showPane("serialized");
        setStatus("Serialized clean source. Runtime chrome was excluded.");
        return clean;
      }

      function damageRuntime() {
        if (!studioState.mounted) renderRuntime();
        const generated = [...runtimePreview.querySelectorAll('[data-mc-generated="runtime"]')];
        generated.slice(0, Math.max(1, Math.ceil(generated.length / 2))).forEach((node) => node.remove());
        studioState.damaged = true;
        setRuntimeLabel();
        setStatus("Runtime chrome was intentionally damaged. Author source was not changed.");
        showPane("runtime");
      }

      function repairRuntime() {
        const report = validateSource();
        if (!report.ok) {
          showPane("contract");
          return;
        }
        renderRuntime();
        showPane("runtime");
        setStatus("Runtime repaired from author-owned source intent.");
      }

      function commitRuntimeDraft() {
        const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
        if (!draft) {
          setStatus("No runtime draft is mounted.");
          return;
        }
        const {doc, workspace} = parseSource();
        const file = selectedFile();
        if (!workspace || !file) {
          setStatus("Cannot commit: source workspace or selected file is missing.");
          return;
        }
        const target = [...workspace.querySelectorAll('[data-mc-component="code-file"]')]
          .find((node) => node.getAttribute("data-mc-file-path") === file.path);
        if (!target) {
          setStatus("Cannot commit: selected file path is no longer in source.");
          return;
        }
        target.textContent = draft.value;
        sourceEditor.value = workspace.outerHTML.trim();
        studioState.dirty = false;
        syncLineGutter();
        renderRuntime();
        setStatus("Runtime draft committed into author-owned source.");
      }

      tabButtons.forEach((button) => {
        button.addEventListener("click", () => showPane(button.dataset.codeStudioTab || "source"));
      });
      root.querySelectorAll("[data-code-studio-panel]").forEach((button) => {
        button.addEventListener("click", () => {
          root.querySelectorAll("[data-code-studio-panel]").forEach((entry) => entry.classList.remove("active"));
          button.classList.add("active");
          const panel = button.dataset.codeStudioPanel;
          if (panel === "runtime") renderRuntime();
          if (panel === "contract") validateSource();
          if (panel === "source" || panel === "explorer") showPane("source");
          if (panel === "assistant") {
            const dock = root.querySelector("#code-studio-bottom-panel");
            const dockToggle = root.querySelector("#code-studio-toggle-assistant");
            if (dock) dock.dataset.expanded = "true";
            if (dockToggle) {
              dockToggle.setAttribute("aria-expanded", "true");
              dockToggle.textContent = "Close assistant dock";
            }
          }
        });
      });
      root.querySelectorAll("[data-code-studio-file]").forEach((button) => {
        button.addEventListener("click", () => {
          studioState.selectedPath = button.dataset.codeStudioFile || studioState.selectedPath;
          root.querySelectorAll("[data-code-studio-file]").forEach((entry) => entry.classList.toggle("active", entry === button));
          renderRuntime();
          showPane("runtime");
        });
      });


      const assistantDock = root.querySelector("#code-studio-bottom-panel");
      const assistantToggle = root.querySelector("#code-studio-toggle-assistant");
      assistantToggle?.addEventListener("click", () => {
        const expanded = assistantDock?.dataset.expanded === "true";
        if (assistantDock) assistantDock.dataset.expanded = expanded ? "false" : "true";
        assistantToggle.setAttribute("aria-expanded", expanded ? "false" : "true");
        assistantToggle.textContent = expanded ? "Open assistant dock" : "Close assistant dock";
      });

      sourceEditor.addEventListener("input", () => {
        syncLineGutter();
        studioState.mounted = false;
        studioState.damaged = false;
        setRuntimeLabel();
        setStatus("Source changed. Remount or validate to refresh the MCEL runtime.");
      });
      sourceEditor.addEventListener("scroll", () => {
        if (gutter) gutter.scrollTop = sourceEditor.scrollTop;
      });

      validateButton?.addEventListener("click", validateSource);
      serializeButton?.addEventListener("click", serializeCleanSource);
      mountButton?.addEventListener("click", () => { renderRuntime(); showPane("runtime"); setStatus("Runtime mounted from author source."); });
      damageButton?.addEventListener("click", damageRuntime);
      repairButton?.addEventListener("click", repairRuntime);
      commitButton?.addEventListener("click", commitRuntimeDraft);

      window.MainComputerCodeStudio = {
        getState() {
          return {...studioState, sourceLength: sourceEditor.value.length};
        },
        validate: validateSource,
        mountRuntime: renderRuntime,
        damageRuntime,
        repairRuntime,
        serialize: serializeCleanSource,
        commitRuntimeDraft,
      };

      syncLineGutter();
      validateSource();
      renderRuntime();
      serializeCleanSource();
      showPane("source");
      setRuntimeLabel();
    })();

    (() => {
      const root = document.querySelector("#code-editor-app");
      if (!root) return;
      const toggle = root.querySelector("#code-editor-gridstack-toggle");
      const reset = root.querySelector("#code-editor-gridstack-reset");
      const status = root.querySelector("#code-editor-gridstack-status");
      const layoutKey = "main-computer-code-editor-gridstack-layout-v1";
      const enabledKey = "main-computer-code-editor-gridstack-enabled-v1";
      let grid = null;

      function setGridStatus(message) {
        if (status) status.textContent = message;
      }

      function saveCodeEditorGridStackLayout() {
        if (!grid) return;
        try {
          localStorage.setItem(layoutKey, JSON.stringify(grid.save(false)));
        } catch {
          setGridStatus("GridStack layout could not be saved.");
        }
      }

      function disableCodeEditorGridStackTest() {
        try {
          if (grid) grid.destroy(false);
        } catch {}
        grid = null;
        root.dataset.gridstackEnabled = "false";
        try { localStorage.setItem(enabledKey, "false"); } catch {}
        setGridStatus("Layout locked");
      }

      function enableCodeEditorGridStackTest() {
        if (!window.GridStack) {
          setGridStatus("GridStack library unavailable");
          return null;
        }
        const container = root.querySelector(".code-studio-body");
        if (!container) {
          setGridStatus("GridStack container unavailable");
          return null;
        }
        try {
          grid = GridStack.init({
            cellHeight: 80,
            float: true,
            margin: 4,
            resizable: {handles: "e, se, s, sw, w"},
          }, container);
          root.dataset.gridstackEnabled = "true";
          localStorage.setItem(enabledKey, "true");
          setGridStatus("Layout unlocked");
          grid.on("change", saveCodeEditorGridStackLayout);
          return grid;
        } catch {
          setGridStatus("GridStack could not attach to this shell.");
          return null;
        }
      }

      toggle?.addEventListener("click", () => {
        if (grid) {
          disableCodeEditorGridStackTest();
        } else {
          enableCodeEditorGridStackTest();
        }
      });
      reset?.addEventListener("click", () => {
        try { localStorage.removeItem(layoutKey); } catch {}
        if (grid) {
          disableCodeEditorGridStackTest();
          enableCodeEditorGridStackTest();
        }
        setGridStatus("Layout reset");
      });

      window.enableCodeEditorGridStackTest = enableCodeEditorGridStackTest;
      window.disableCodeEditorGridStackTest = disableCodeEditorGridStackTest;
      window.saveCodeEditorGridStackLayout = saveCodeEditorGridStackLayout;
    })();
