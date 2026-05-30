    var mcelLabState = window.mcelLabState || (window.mcelLabState = createDefaultMcelLabState());

    function mcelLabDependenciesReady() {
      return Boolean(
        window.McelLabContract &&
        window.McelLabEngine &&
        window.McelLabLawRegistry &&
        window.McelLabEditor &&
        window.McelLabScenarios &&
        window.McelLabChromeLaw &&
        window.McelLabBrowserObserver &&
        window.McelLabLayoutLaw &&
        window.McelLabComponentLaw &&
        window.McelLabStateLaw &&
        window.McelLabDataLaw &&
        window.McelLabFormLaw &&
        window.McelLabActionLaw &&
        window.McelLabRenderLaw &&
        window.McelLabA11yLaw &&
        window.McelLabPerformanceLaw &&
        window.McelLabPlatformSpine &&
        window.McelLabWorkbench &&
        window.McelLabBrowserRunner &&
        window.McelLabSiteSkeleton &&
        window.McelLabAcidTests &&
        window.McelLabSupervisor &&
        window.McelLabKernel &&
        window.MCEL
      );
    }

    function initMcelLabApp() {
      if (!mcelLabApp) return;
      if (!mcelLabDependenciesReady()) {
        window.setTimeout(initMcelLabApp, 0);
        return;
      }
      mcelLabState = window.mcelLabState || (window.mcelLabState = createDefaultMcelLabState());
      if (mcelLabState.initialized) return;
      mcelLabState.initialized = true;
      if (mcelSourceHtml && !mcelSourceHtml.value.trim()) {
        mcelSourceHtml.value = McelLabContract.defaultSource;
      }
      populateMcelThemes();
      populateMcelChromes();
      populateMcelScenarios();
      populateMcelAcidCases();
      bindMcelLabControls();
      initMcelLabGrapes();
      selectMcelSourceIndex(0, "initial-selection");
      compileMcelLabSource("initial-load");
      renderMcelAutopilotDeferred("boot");
    }

    function bindMcelLabControls() {
      mcelCompile?.addEventListener("click", () => compileMcelLabSource("manual-compile"));
      mcelSerialize?.addEventListener("click", () => serializeMcelRuntime("manual-serialize"));
      mcelDamage?.addEventListener("click", damageMcelRuntime);
      mcelRepair?.addEventListener("click", () => repairMcelRuntime("manual-repair"));
      mcelReset?.addEventListener("click", resetMcelLab);
      mcelRunTests?.addEventListener("click", runMcelContractTests);
      mcelRunMatrix?.addEventListener("click", runMcelScenarioMatrix);
      mcelRunAcid?.addEventListener("click", () => runSelectedMcelAcidTest("manual-selected-acid-test"));
      mcelRunAcidSuite?.addEventListener("click", () => runMcelAcidTests("manual-acid-suite"));
      mcelRunAudit?.addEventListener("click", runMcelOperationalAudit);
      mcelBuildEvidence?.addEventListener("click", buildMcelEvidencePacket);
      mcelRunAutopilot?.addEventListener("click", () => runMcelAutopilotProof("manual-autopilot"));
      mcelRunKernel?.addEventListener("click", () => runMcelKernelAudit("manual-kernel-audit"));
      mcelBuildTraceability?.addEventListener("click", () => buildMcelTraceabilityMap("manual-traceability"));
      mcelBuildSubsumption?.addEventListener("click", () => buildMcelSubsumptionLattice("manual-subsumption"));
      mcelBuildWorkbench?.addEventListener("click", () => buildMcelWorkbenchPlan("manual-workbench"));
      mcelRunBrowserProof?.addEventListener("click", () => runMcelBrowserSemanticProof("manual-browser-proof"));
      mcelApplyTraits?.addEventListener("click", applyMcelTraitsToSelectedSourceWidget);
      mcelLoadScenario?.addEventListener("click", loadSelectedMcelScenario);
      mcelScenarioSelect?.addEventListener("change", describeSelectedMcelScenario);
      mcelThemeSelect?.addEventListener("change", () => changeMcelTheme("theme-select"));
      mcelChromeSelect?.addEventListener("change", () => changeMcelChrome("chrome-select"));
      mcelOpenEditorModal?.addEventListener("click", () => openMcelLabModal("editor"));
      mcelOpenSiteModal?.addEventListener("click", () => openMcelLabModal("site"));
      mcelSiteFrameResync?.addEventListener("click", () => syncMcelRenderedSiteFrame("twiddle-resync"));
      mcelSiteFrameRebuild?.addEventListener("click", () => rebuildMcelSiteFrameShell("twiddle-rebuild", {syncAfter: true}));
      mcelSiteFrameClear?.addEventListener("click", () => clearMcelSiteFrameSrcdoc("twiddle-clear"));
      bindMcelSiteFrameLifecycle("boot");
      renderMcelSiteFrameTwiddle("boot");
      document.querySelectorAll("[data-mcel-close-modal]").forEach((button) => {
        button.addEventListener("click", () => closeMcelLabModal(button.dataset.mcelCloseModal || "all"));
      });
      [mcelEditorModal, mcelSiteModal].filter(Boolean).forEach((modal) => {
        modal.addEventListener("click", (event) => {
          if (event.target === modal) closeMcelLabModal("all");
        });
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && mcelLabState.activeModal) closeMcelLabModal("all");
      });
      mcelCommandPlan?.addEventListener("click", planMcelSemanticCommand);
      mcelCommandApply?.addEventListener("click", applyMcelSemanticCommand);
      mcelProjectSave?.addEventListener("click", saveMcelProject);
      mcelProjectRestore?.addEventListener("click", restoreMcelProject);
      mcelProjectExport?.addEventListener("click", exportMcelProject);
      mcelCommandInput?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          applyMcelSemanticCommand();
        }
      });
      mcelSourceHtml?.addEventListener("input", debounceMcelLabCompile);
      mcelRuntimePreview?.addEventListener("click", handleMcelRuntimeClick);
      document.querySelectorAll("[data-mcel-block]").forEach((button) => {
        button.addEventListener("click", () => insertMcelLabBlock(button.dataset.mcelBlock || "panel"));
      });
      document.querySelectorAll("[data-mcel-mode]").forEach((button) => {
        button.addEventListener("click", () => setMcelLabMode(button.dataset.mcelMode || "source"));
      });
    }

    let mcelLabCompileTimer = null;
    let mcelAutopilotTimer = null;

    function recordMcelEvent(module, code, message, level = "info") {
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level, module, code, message}
      ].slice(-64);
      renderMcelCompilerLog();
    }
    function debounceMcelLabCompile() {
      clearTimeout(mcelLabCompileTimer);
      mcelLabCompileTimer = setTimeout(() => compileMcelLabSource("source-input"), 240);
    }

    function scheduleMcelAutopilotProof(reason = "scheduled-autopilot") {
      renderMcelAutopilotDeferred(reason);
      return null;
    }

    function renderMcelAutopilotDeferred(reason = "manual-only") {
      clearTimeout(mcelAutopilotTimer);
      mcelLabState.lastSupervisorReport = null;
      if (mcelSupervisorReport) {
        mcelSupervisorReport.textContent = [
          "Autopilot proof is manual-only.",
          `reason: ${reason}`,
          "Use Run Autopilot Proof from Diagnostics & Proofs when you want the supervisor report.",
          "Scenario changes and page load intentionally do not run matrix, acid, kernel, or autopilot suites."
        ].join("\n");
      }
    }

    function populateMcelThemes() {
      if (!mcelThemeSelect || typeof McelLabStyleLaw === "undefined") return;
      const catalog = McelLabStyleLaw.themeCatalog || McelLabStyleLaw.themes.map((theme) => ({id: theme, label: theme, description: ""}));
      mcelThemeSelect.innerHTML = "";
      catalog.forEach((theme) => {
        const option = document.createElement("option");
        option.value = theme.id;
        option.textContent = theme.label || theme.id;
        if (theme.description) option.title = theme.description;
        if (theme.audience) option.dataset.audience = theme.audience;
        mcelThemeSelect.appendChild(option);
      });
      mcelThemeSelect.value = McelLabStyleLaw.normalizeTheme(mcelLabState.theme);
    }

    function populateMcelChromes() {
      if (!mcelChromeSelect || typeof McelLabChromeLaw === "undefined") return;
      const catalog = McelLabChromeLaw.chromeCatalog || McelLabChromeLaw.chromes.map((chrome) => ({id: chrome, label: chrome, description: ""}));
      mcelChromeSelect.innerHTML = "";
      catalog.forEach((chrome) => {
        const option = document.createElement("option");
        option.value = chrome.id;
        option.textContent = chrome.label || chrome.id;
        if (chrome.description) option.title = chrome.description;
        if (chrome.kind) option.dataset.kind = chrome.kind;
        if (chrome.restructuresHierarchy) option.dataset.restructuresHierarchy = "true";
        mcelChromeSelect.appendChild(option);
      });
      mcelLabState.chrome = McelLabChromeLaw.normalizeChrome(mcelLabState.chrome);
      mcelChromeSelect.value = mcelLabState.chrome;
    }

    function populateMcelScenarios() {
      if (!mcelScenarioSelect || typeof McelLabScenarios === "undefined") return;
      mcelScenarioSelect.innerHTML = "";
      McelLabScenarios.all().forEach((scenario) => {
        const option = document.createElement("option");
        option.value = scenario.id;
        option.textContent = scenario.label;
        mcelScenarioSelect.appendChild(option);
      });
      describeSelectedMcelScenario();
    }

    function describeSelectedMcelScenario() {
      if (!mcelScenarioDescription || typeof McelLabScenarios === "undefined") return;
      const scenario = McelLabScenarios.byId(mcelScenarioSelect?.value || "round-trip");
      mcelScenarioDescription.textContent = scenario.description;
    }

    function populateMcelAcidCases() {
      if (!mcelAcidSelect || typeof McelLabAcidTests === "undefined") return;
      const cases = McelLabAcidTests.listCases();
      mcelAcidSelect.innerHTML = "";
      cases.forEach((testCase) => {
        const option = document.createElement("option");
        option.value = testCase.id;
        option.textContent = `${testCase.severity.toUpperCase()} · ${testCase.name}`;
        mcelAcidSelect.appendChild(option);
      });
      if (!cases.some((testCase) => testCase.id === mcelAcidSelect.value)) {
        mcelAcidSelect.value = cases[0]?.id || "";
      }
    }

    function loadSelectedMcelScenario() {
      if (!mcelSourceHtml || typeof McelLabScenarios === "undefined") return;
      const scenario = McelLabScenarios.byId(mcelScenarioSelect?.value || "round-trip");
      mcelSourceHtml.value = scenario.source;
      selectMcelSourceIndex(0, `scenario:${scenario.id}`);
      setMcelLabMode(scenario.mode || "source");
      compileMcelLabSource(`scenario:${scenario.id}`);
      syncMcelGrapesFromSource();
      renderMcelAutopilotDeferred(`scenario:${scenario.id}`);
    }

    function initMcelLabGrapes() {
      if (!mcelGrapesCanvas || !mcelGrapesHost || typeof grapesjs === "undefined") {
        if (mcelGrapesHost) mcelGrapesHost.hidden = true;
        if (mcelGrapesFallback) mcelGrapesFallback.hidden = false;
        if (mcelEditorStatus) mcelEditorStatus.textContent = "semantic fallback active";
        return;
      }
      try {
        if (mcelGrapesFallback) mcelGrapesFallback.hidden = true;
        mcelLabState.grapesEditor = grapesjs.init({
          container: "#mcel-grapes",
          height: "100%",
          storageManager: false,
          blockManager: {appendTo: null},
          traitManager: {appendTo: null},
          panels: {defaults: []},
          canvas: {styles: [], scripts: []}
        });
        mcelLabState.grapesEditor.setComponents(McelLabEditor.canonicalSource(mcelSourceHtml?.value || McelLabContract.defaultSource));
        mcelLabState.grapesEditor.on("component:selected", () => {
          const html = McelLabEditor.sanitizeEditorHtml(mcelLabState.grapesEditor.getHtml());
          const sourceList = McelLabEditor.sourceList(html);
          if (sourceList.length) {
            selectMcelSourceIndex(0, "grapes-selected");
          }
        });
        mcelLabState.grapesEditor.on("component:update component:add component:remove", () => {
          if (!mcelSourceHtml || !mcelLabState.grapesReady || mcelLabState.syncingGrapes) return;
          const html = McelLabEditor.sanitizeEditorHtml(mcelLabState.grapesEditor.getHtml());
          if (html && html.trim() && html.trim() !== mcelSourceHtml.value.trim()) {
            mcelSourceHtml.value = html.trim();
            compileMcelLabSource("grapes-update");
          }
        });
        mcelLabState.grapesReady = true;
        if (mcelEditorStatus) mcelEditorStatus.textContent = "GrapesJS editing semantic source";
      } catch (error) {
        mcelLabState.grapesEditor = null;
        mcelLabState.grapesReady = false;
        if (mcelGrapesHost) mcelGrapesHost.hidden = true;
        if (mcelGrapesFallback) mcelGrapesFallback.hidden = false;
        if (mcelEditorStatus) mcelEditorStatus.textContent = `semantic fallback active: ${error.message}`;
      }
    }

    function syncMcelGrapesFromSource() {
      if (!mcelLabState.grapesEditor || !mcelLabState.grapesReady || !mcelSourceHtml) return;
      try {
        mcelLabState.syncingGrapes = true;
        mcelLabState.grapesEditor.setComponents(McelLabEditor.canonicalSource(mcelSourceHtml.value));
      } finally {
        mcelLabState.syncingGrapes = false;
      }
    }

    function currentMcelSource() {
      return McelLabEditor.canonicalSource(mcelSourceHtml?.value || McelLabContract.defaultSource);
    }

    function compileMcelLabSource(reason = "compile") {
      if (!mcelRuntimePreview || !mcelSourceHtml) return;
      const cleanSource = currentMcelSource();
      if (cleanSource && cleanSource !== mcelSourceHtml.value.trim()) {
        mcelSourceHtml.value = cleanSource;
      }
      const compiled = window.MCEL?.compile
        ? MCEL.compile(cleanSource, {reason, theme: mcelLabState.theme})
        : McelLabEngine.compileSource(cleanSource, {reason});
      mcelRuntimePreview.innerHTML = compiled.runtimeHtml;
      applyMcelRuntimeStyleLaw(reason);
      mcelLabState.lastSourceList = McelLabEditor.sourceList(cleanSource);
      mcelLabState.selectedIndex = Math.min(
        Math.max(mcelLabState.selectedIndex, 0),
        Math.max(mcelLabState.lastSourceList.length - 1, 0)
      );
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...compiled.events].slice(-64);
      const serialization = McelLabEngine.serializeRuntimeRoot(mcelRuntimePreview, {reason: "post-compile-check"});
      mcelLabState.lastSerializerReport = serialization.report;
      renderMcelRuntimeDom();
      renderMcelSerializerDiff(serialization.serialized);
      renderMcelA11yReport();
      renderMcelDebugger();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelSiteSkeleton();
      renderMcelGraphReport();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelSubsumptionLattice();
      renderMcelWorkbenchPlan();
      renderMcelBrowserSemanticProof();
      renderMcelReadiness();
      renderMcelProjectReport();
      renderMcelCompilerLog();
      syncMcelTraitControls();
      markSelectedMcelRuntimeElement();
      renderMcelSelectionStatus();
      renderMcelSiteSkeleton();
    }

    function serializeMcelRuntime(reason = "serialize") {
      if (!mcelRuntimePreview || !mcelSourceHtml) return;
      const result = window.MCEL?.serialize
        ? MCEL.serialize(mcelRuntimePreview, {reason})
        : McelLabEngine.serializeRuntimeRoot(mcelRuntimePreview, {reason});
      mcelLabState.lastSerializerReport = result.report;
      mcelSourceHtml.value = result.serialized;
      mcelLabState.compileEvents.push({
        level: result.report.serializerClean ? "success" : "warning",
        module: "serializer",
        code: result.report.serializerClean ? "MCEL_SERIALIZER_CLEAN" : "MCEL_SERIALIZER_WARNING",
        message: result.report.serializerClean ? "Serialized clean source replaced source pane." : result.report.warnings.join(" ")
      });
      syncMcelGrapesFromSource();
      compileMcelLabSource(reason);
    }

    function repairMcelRuntime(reason = "repair") {
      if (!mcelRuntimePreview) return;
      const repair = McelLabEngine.repairRuntimeRoot(mcelRuntimePreview, {reason});
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...repair.events].slice(-64);
      applyMcelRuntimeStyleLaw(reason);
      renderMcelRuntimeDom();
      renderMcelA11yReport();
      renderMcelDebugger();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelGraphReport();
      renderMcelReadiness();
      renderMcelCompilerLog();
      markSelectedMcelRuntimeElement();
    }

    function damageMcelRuntime() {
      if (!mcelRuntimePreview) return;
      const result = McelLabEngine.damageRuntimeRoot(mcelRuntimePreview);
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...result.events].slice(-64);
      applyMcelRuntimeStyleLaw("damage");
      renderMcelRuntimeDom();
      renderMcelA11yReport();
      renderMcelDebugger();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelGraphReport();
      renderMcelReadiness();
      renderMcelCompilerLog();
      markSelectedMcelRuntimeElement();
    }

    function resetMcelLab() {
      if (!mcelSourceHtml) return;
      mcelSourceHtml.value = McelLabContract.defaultSource;
      mcelLabState.compileEvents = [];
      mcelLabState.lastSerializerReport = null;
      mcelLabState.lastTestReport = null;
      mcelLabState.lastLayoutLawReport = null;
      mcelLabState.lastGraphReport = null;
      mcelLabState.lastAuditReport = null;
      mcelLabState.lastMatrixReport = null;
      mcelLabState.lastEvidencePacket = null;
      mcelLabState.lastReadinessReport = null;
      mcelLabState.lastSupervisorReport = null;
      mcelLabState.lastCommandPlan = null;
      mcelLabState.theme = "theme-machine";
      mcelLabState.chrome = "chrome-strict-hierarchy";
      if (mcelThemeSelect) mcelThemeSelect.value = "theme-machine";
      if (mcelChromeSelect) mcelChromeSelect.value = "chrome-strict-hierarchy";
      selectMcelSourceIndex(0, "reset");
      syncMcelGrapesFromSource();
      compileMcelLabSource("reset");
      renderMcelContractTests();
      renderMcelGraphReport();
      renderMcelAuditReport();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelReadiness();
      scheduleMcelAutopilotProof("reset-autopilot");
    }

    function runMcelContractTests() {
      const report = typeof McelLabTestHarness !== "undefined"
        ? McelLabTestHarness.runAll()
        : McelLabEngine.runContractTests();
      mcelLabState.lastTestReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "tests",
          code: report.failed ? "MCEL_FULL_SUITE_FAILED" : "MCEL_FULL_SUITE_PASSED",
          message: `${report.passed} passed / ${report.failed} failed.`
        }
      ].slice(-64);
      renderMcelContractTests();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function runMcelOperationalAudit() {
      if (typeof McelLabGraph === "undefined" || !mcelSourceHtml || !mcelRuntimePreview) return;
      const report = McelLabGraph.audit(currentMcelSource(), mcelRuntimePreview, {reason: "manual-audit"});
      mcelLabState.lastAuditReport = report;
      mcelLabState.lastGraphReport = McelLabGraph.compactReport(currentMcelSource(), mcelRuntimePreview);
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "audit",
          code: report.failed ? "MCEL_OPERATIONAL_AUDIT_BLOCKED" : "MCEL_OPERATIONAL_AUDIT_CLEAN",
          message: report.failed
            ? `${report.failed} audit check(s) failed: ${report.issues.join(" ")}`
            : `Operational graph clean with ${report.runtimeGraph.generatedPartCount} generated part(s) under provenance.`
        }
      ].slice(-64);
      renderMcelGraphReport();
      renderMcelAuditReport();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function runMcelKernelAudit(reason = "manual-kernel-audit") {
      if (typeof McelLabKernel === "undefined" || !mcelSourceHtml) return null;
      const report = McelLabKernel.runKernelAudit({
        source: currentMcelSource(),
        runtimeRoot: mcelRuntimePreview,
        theme: mcelLabState.theme,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit,
        reason
      });
      mcelLabState.lastKernelAudit = report;
      mcelLabState.lastTraceabilityMap = report.traceability;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.status === "ready" ? "success" : "warning",
          module: "kernel",
          code: report.status === "ready" ? "MCEL_KERNEL_AUDIT_READY" : "MCEL_KERNEL_AUDIT_BLOCKED",
          message: `Kernel audit ${report.status}: ${report.passCount}/${report.total} debt gates at score ${report.score}.`
        }
      ].slice(-64);
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelSubsumptionLattice();
      renderMcelWorkbenchPlan();
      renderMcelBrowserSemanticProof();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function buildMcelTraceabilityMap(reason = "manual-traceability") {
      if (typeof McelLabKernel === "undefined") return null;
      const map = McelLabKernel.buildTraceabilityMap({reason});
      mcelLabState.lastTraceabilityMap = map;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: map.status === "covered" ? "success" : "warning",
          module: "kernel",
          code: map.status === "covered" ? "MCEL_TRACEABILITY_COVERED" : "MCEL_TRACEABILITY_BLOCKED",
          message: `Traceability map ${map.status}: ${map.covered}/${map.total} requirement(s) covered.`
        }
      ].slice(-64);
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelCompilerLog();
      return map;
    }

    function buildMcelSubsumptionLattice(reason = "manual-subsumption") {
      const lattice = window.MCEL?.buildSubsumptionLattice ? MCEL.buildSubsumptionLattice() : McelLabPlatformSpine?.buildSubsumptionLattice?.();
      mcelLabState.lastSubsumptionLattice = lattice;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: lattice ? "success" : "warning",
          module: "platform-spine",
          code: lattice ? "MCEL_SUBSUMPTION_LATTICE_READY" : "MCEL_SUBSUMPTION_LATTICE_UNAVAILABLE",
          message: lattice ? `Subsumption lattice maps ${lattice.obsoleteLibraryMap?.length || 0} obsolete library family claim(s).` : "Subsumption lattice is unavailable."
        }
      ].slice(-64);
      renderMcelSubsumptionLattice();
      renderMcelCompilerLog();
      return lattice;
    }

    function buildMcelWorkbenchPlan(reason = "manual-workbench") {
      const plan = window.MCEL?.buildWorkbenchPlan ? MCEL.buildWorkbenchPlan() : McelLabWorkbench?.buildWorkbenchPlan?.();
      mcelLabState.lastWorkbenchPlan = plan;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: plan ? "success" : "warning",
          module: "workbench",
          code: plan ? "MCEL_WORKBENCH_PLAN_READY" : "MCEL_WORKBENCH_PLAN_UNAVAILABLE",
          message: plan ? `Workbench plan tracks ${plan.requiredBlueprints?.length || 0} proof blueprint(s).` : "Workbench plan is unavailable."
        }
      ].slice(-64);
      renderMcelWorkbenchPlan();
      renderMcelCompilerLog();
      return plan;
    }

    function runMcelBrowserSemanticProof(reason = "manual-browser-proof") {
      const report = window.MCEL?.runBrowserProof
        ? MCEL.runBrowserProof(mcelRuntimePreview, {reason})
        : McelLabBrowserRunner?.observeAndProve?.(mcelRuntimePreview, {reason});
      mcelLabState.lastBrowserProof = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report && !report.failed ? "success" : "warning",
          module: "browser-runner",
          code: report && !report.failed ? "MCEL_BROWSER_SEMANTIC_PROOF_READY" : "MCEL_BROWSER_SEMANTIC_PROOF_BLOCKED",
          message: report ? `Browser semantic proof observed ${report.elementCount || 0} element(s); liveGeometry=${report.liveGeometry}.` : "Browser semantic proof is unavailable."
        }
      ].slice(-64);
      renderMcelBrowserSemanticProof();
      renderMcelCompilerLog();
      return report;
    }

    function runMcelScenarioMatrix() {
      if (typeof McelLabOpsRunner === "undefined") return;
      const report = McelLabOpsRunner.runScenarioMatrix();
      mcelLabState.lastMatrixReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "matrix",
          code: report.failed ? "MCEL_SCENARIO_MATRIX_FAILED" : "MCEL_SCENARIO_MATRIX_PASSED",
          message: `${report.passed} passed / ${report.failed} failed across ${report.caseCount} scenario-theme case(s).`
        }
      ].slice(-64);
      renderMcelScenarioMatrix();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function runSelectedMcelAcidTest(reason = "manual-selected-acid-test") {
      if (typeof McelLabAcidTests === "undefined") return null;
      const selectedCaseId = mcelAcidSelect?.value || McelLabAcidTests.listCases()[0]?.id;
      const report = McelLabAcidTests.runOne(selectedCaseId, {
        source: currentMcelSource(),
        theme: mcelLabState.theme,
        matrixReport: mcelLabState.lastMatrixReport,
        kernelReport: mcelLabState.lastKernelAudit,
        reason
      });
      mcelLabState.lastAcidReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "acid-tests",
          code: report.failed ? "MCEL_SELECTED_ACID_TEST_FAILED" : "MCEL_SELECTED_ACID_TEST_PASSED",
          message: `${report.passed} passed / ${report.failed} failed for selected acid test: ${report.tests[0]?.name || selectedCaseId}.`
        }
      ].slice(-64);
      renderMcelAcidTests();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function runMcelAcidTests(reason = "manual-acid-suite") {
      if (typeof McelLabAcidTests === "undefined") return null;
      const report = McelLabAcidTests.runAll({
        source: currentMcelSource(),
        theme: mcelLabState.theme,
        matrixReport: mcelLabState.lastMatrixReport,
        kernelReport: mcelLabState.lastKernelAudit,
        reason,
        explicitSuite: true
      });
      mcelLabState.lastAcidReport = report;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: report.failed ? "warning" : "success",
          module: "acid-tests",
          code: report.failed ? "MCEL_ACID_SUITE_FAILED" : "MCEL_ACID_SUITE_PASSED",
          message: `${report.passed} passed / ${report.failed} failed across ${report.total} acid test(s).`
        }
      ].slice(-64);
      renderMcelAcidTests();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function buildMcelEvidencePacket() {
      if (typeof McelLabOpsRunner === "undefined" || !mcelSourceHtml || !mcelRuntimePreview) return;
      const packet = McelLabOpsRunner.buildEvidencePacket({
        source: currentMcelSource(),
        runtimeRoot: mcelRuntimePreview,
        theme: mcelLabState.theme,
        serializerReport: mcelLabState.lastSerializerReport,
        cssLawReport: mcelLabState.lastCssLawReport,
        layoutLawReport: mcelLabState.lastLayoutLawReport,
        auditReport: mcelLabState.lastAuditReport,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit
      });
      mcelLabState.lastEvidencePacket = packet;
      mcelLabState.lastReadinessReport = packet.readiness;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {
          level: packet.readiness.status === "blocked" ? "warning" : "success",
          module: "evidence",
          code: "MCEL_EVIDENCE_PACKET_BUILT",
          message: `Evidence packet built with readiness ${packet.readiness.status} at score ${packet.readiness.score}.`
        }
      ].slice(-64);
      renderMcelEvidencePacket();
      renderMcelReadiness();
      renderMcelCompilerLog();
    }

    function applyMcelSupervisorReport(report) {
      if (!report) return;
      mcelLabState.lastSupervisorReport = report;
      mcelLabState.lastSerializerReport = report.serializerReport;
      mcelLabState.lastCssLawReport = report.cssLawReport;
      mcelLabState.lastLayoutLawReport = report.layoutLawReport || mcelLabState.lastLayoutLawReport;
      mcelLabState.lastAuditReport = report.auditReport;
      mcelLabState.lastGraphReport = report.graphReport;
      mcelLabState.lastTestReport = report.testReport;
      mcelLabState.lastMatrixReport = report.matrixReport;
      mcelLabState.lastAcidReport = report.acidReport || mcelLabState.lastAcidReport;
      mcelLabState.lastEvidencePacket = report.evidencePacket;
      mcelLabState.lastKernelAudit = report.kernelReport || mcelLabState.lastKernelAudit;
      mcelLabState.lastTraceabilityMap = report.kernelReport?.traceability || mcelLabState.lastTraceabilityMap;
      mcelLabState.lastReadinessReport = report.readiness;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        ...(report.compileEvents || []),
        {
          level: report.qualityGate.status === "ready" ? "success" : "warning",
          module: "supervisor",
          code: report.qualityGate.status === "ready" ? "MCEL_AUTOPILOT_READY" : "MCEL_AUTOPILOT_BLOCKED",
          message: `Autopilot proof ${report.qualityGate.status}: ${report.qualityGate.passCount}/${report.qualityGate.total} gates at score ${report.qualityGate.score}.`
        }
      ].slice(-64);
    }

    function runMcelAutopilotProof(reason = "manual-autopilot") {
      if (typeof McelLabSupervisor === "undefined" || !mcelSourceHtml) return null;
      const report = McelLabSupervisor.runFullProof({
        source: currentMcelSource(),
        theme: mcelLabState.theme,
        selectedIndex: mcelLabState.selectedIndex,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit,
        runHeavyProofs: false,
        reason
      });
      applyMcelSupervisorReport(report);
      renderMcelContractTests();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelGraphReport();
      renderMcelAuditReport();
      renderMcelCssLawReport();
      renderMcelLayoutLawReport();
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelSubsumptionLattice();
      renderMcelWorkbenchPlan();
      renderMcelBrowserSemanticProof();
      renderMcelReadiness();
      renderMcelCompilerLog();
      return report;
    }

    function changeMcelTheme(reason = "theme") {
      if (typeof McelLabStyleLaw !== "undefined") {
        mcelLabState.theme = McelLabStyleLaw.normalizeTheme(mcelThemeSelect?.value || mcelLabState.theme);
      } else {
        mcelLabState.theme = mcelThemeSelect?.value || mcelLabState.theme;
      }
      const label = typeof McelLabStyleLaw !== "undefined" && McelLabStyleLaw.themeLabel
        ? McelLabStyleLaw.themeLabel(mcelLabState.theme)
        : mcelLabState.theme;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "success", module: "style-law", code: "MCEL_THEME_CHANGED", message: `Theme changed to ${label} (${mcelLabState.theme}) during ${reason}.`}
      ].slice(-64);
      applyMcelRuntimeStyleLaw(reason);
      renderMcelRuntimeDom();
      renderMcelCssLawReport();
      renderMcelGraphReport();
      renderMcelCompilerLog();
      syncMcelRenderedSiteFrame("theme");
    }

    function changeMcelChrome(reason = "chrome") {
      if (typeof McelLabChromeLaw !== "undefined") {
        mcelLabState.chrome = McelLabChromeLaw.normalizeChrome(mcelChromeSelect?.value || mcelLabState.chrome);
      } else {
        mcelLabState.chrome = mcelChromeSelect?.value || mcelLabState.chrome || "chrome-strict-hierarchy";
      }
      const label = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeLabel
        ? McelLabChromeLaw.chromeLabel(mcelLabState.chrome)
        : mcelLabState.chrome;
      if (mcelChromeSelect) mcelChromeSelect.value = mcelLabState.chrome;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "success", module: "chrome-law", code: "MCEL_CHROME_CHANGED", message: `Chrome changed to ${label} (${mcelLabState.chrome}) during ${reason}.`}
      ].slice(-64);
      renderMcelCompilerLog();
      syncMcelRenderedSiteFrame("chrome");
    }

    function applyMcelRuntimeStyleLaw(reason = "style-law") {
      if (!mcelRuntimePreview || typeof McelLabStyleLaw === "undefined") return;
      mcelLabState.theme = McelLabStyleLaw.normalizeTheme(mcelThemeSelect?.value || mcelLabState.theme);
      if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
      mcelLabState.lastCssLawReport = McelLabStyleLaw.applyRuntimeLaw(mcelRuntimePreview, {
        theme: mcelLabState.theme,
        reason
      });
      if (typeof McelLabLayoutLaw !== "undefined") {
        mcelLabState.lastLayoutLawReport = McelLabLayoutLaw.applyRuntimeLaw(mcelRuntimePreview, {reason});
      }
      if (typeof McelLabPlatformSpine !== "undefined") {
        McelLabPlatformSpine.applyPlatformLaws(mcelRuntimePreview, {reason});
      }
    }

    function planMcelSemanticCommand() {
      if (typeof McelLabCommandSurface === "undefined") return null;
      const command = mcelCommandInput?.value || "";
      const plan = McelLabCommandSurface.plan(command, {
        source: mcelSourceHtml?.value || "",
        selectedIndex: mcelLabState.selectedIndex,
        theme: mcelLabState.theme
      });
      mcelLabState.lastCommandPlan = plan;
      renderMcelCommandReport();
      return plan;
    }

    function applyMcelSemanticCommand() {
      if (typeof McelLabCommandSurface === "undefined" || !mcelSourceHtml) return;
      const plan = planMcelSemanticCommand();
      if (!plan || !plan.ok) {
        mcelLabState.compileEvents = [
          ...mcelLabState.compileEvents,
          {level: "warning", module: "command", code: "MCEL_COMMAND_REJECTED", message: (plan?.warnings || ["Command could not be planned."]).join(" ")}
        ].slice(-64);
        renderMcelCompilerLog();
        return;
      }
      const applied = McelLabCommandSurface.apply(plan, {
        source: mcelSourceHtml.value,
        selectedIndex: mcelLabState.selectedIndex,
        theme: mcelLabState.theme
      });
      mcelSourceHtml.value = applied.source;
      mcelLabState.selectedIndex = applied.selectedIndex;
      mcelLabState.theme = applied.theme;
      if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        ...applied.events,
        {level: "success", module: "command", code: "MCEL_COMMAND_APPLIED", message: plan.summary.join("; ") || "Semantic command applied."}
      ].slice(-64);
      syncMcelGrapesFromSource();
      compileMcelLabSource("semantic-command");

      if (applied.actions.includes("serialize")) serializeMcelRuntime("semantic-command");
      if (applied.actions.includes("damage")) damageMcelRuntime();
      if (applied.actions.includes("repair")) repairMcelRuntime("semantic-command");
      if (applied.actions.includes("test")) runMcelContractTests();
      if (applied.actions.includes("matrix")) runMcelScenarioMatrix();
      if (applied.actions.includes("acid")) runSelectedMcelAcidTest("semantic-command-selected-acid");
      if (applied.actions.includes("graph")) renderMcelGraphReport();
      if (applied.actions.includes("layout")) {
        applyMcelRuntimeStyleLaw("semantic-command-layout");
        renderMcelLayoutLawReport();
      }
      if (applied.actions.includes("audit")) runMcelOperationalAudit();
      if (applied.actions.includes("evidence")) buildMcelEvidencePacket();
      if (applied.actions.includes("autopilot")) runMcelAutopilotProof("semantic-command");
      if (applied.actions.includes("kernel")) runMcelKernelAudit("semantic-command");
      if (applied.actions.includes("traceability")) buildMcelTraceabilityMap("semantic-command");
      if (applied.actions.includes("prior-art")) renderMcelPriorArtReport();
      if (applied.actions.includes("explain")) setMcelLabMode("runtime");

      renderMcelCommandReport();
    }

    function currentMcelProjectState() {
      return {
        source: currentMcelSource(),
        selectedIndex: mcelLabState.selectedIndex,
        theme: mcelLabState.theme,
        chrome: mcelLabState.chrome,
        mode: mcelLabState.currentMode,
        scenario: mcelScenarioSelect?.value || "round-trip",
        lastSerializerClean: Boolean(mcelLabState.lastSerializerReport?.serializerClean)
      };
    }

    function saveMcelProject() {
      if (typeof McelLabProjectStore === "undefined") return;
      const result = McelLabProjectStore.save(currentMcelProjectState());
      mcelLabState.lastProjectSnapshot = result.snapshot;
      if (mcelProjectStatus) mcelProjectStatus.textContent = result.message;
      renderMcelProjectReport(result);
    }

    function restoreMcelProject() {
      if (typeof McelLabProjectStore === "undefined" || !mcelSourceHtml) return;
      const result = McelLabProjectStore.restore();
      if (mcelProjectStatus) mcelProjectStatus.textContent = result.message;
      if (result.ok && result.snapshot) {
        mcelSourceHtml.value = result.snapshot.source || McelLabContract.defaultSource;
        mcelLabState.selectedIndex = Number(result.snapshot.selectedIndex || 0);
        mcelLabState.theme = result.snapshot.theme || "theme-machine";
        mcelLabState.chrome = typeof McelLabChromeLaw !== "undefined"
          ? McelLabChromeLaw.normalizeChrome(result.snapshot.chrome || "chrome-strict-hierarchy")
          : (result.snapshot.chrome || "chrome-strict-hierarchy");
        if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
        if (mcelChromeSelect) mcelChromeSelect.value = mcelLabState.chrome;
        setMcelLabMode(result.snapshot.mode || "source");
        syncMcelGrapesFromSource();
        compileMcelLabSource("project-restore");
      }
      mcelLabState.lastProjectSnapshot = result.snapshot;
      renderMcelProjectReport(result);
    }

    function exportMcelProject() {
      if (typeof McelLabProjectStore === "undefined") return;
      const text = McelLabProjectStore.exportText(currentMcelProjectState());
      mcelLabState.lastProjectSnapshot = JSON.parse(text);
      if (mcelProjectStatus) mcelProjectStatus.textContent = "Exported clean MCEL project snapshot into the Project State pane.";
      if (mcelProjectReport) mcelProjectReport.textContent = text;
    }

    function applyMcelTraitsToSelectedSourceWidget() {
      if (!mcelSourceHtml) return;
      const result = McelLabEditor.applyTraits(mcelSourceHtml.value, {index: mcelLabState.selectedIndex}, {
        kind: mcelTraitKind?.value,
        flow: mcelTraitFlow?.value,
        rank: mcelTraitRank?.value,
        state: mcelTraitState?.value,
        density: mcelTraitDensity?.value,
        sizePolicy: mcelTraitSizePolicy?.value,
        overflowPolicy: mcelTraitOverflowPolicy?.value,
        scrollPolicy: mcelTraitScrollPolicy?.value,
        words: mcelTraitWords?.value,
        connects: mcelTraitConnects?.value
      });
      mcelSourceHtml.value = result.source;
      mcelLabState.selectedIndex = Math.max(result.index, 0);
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...result.events].slice(-64);
      syncMcelGrapesFromSource();
      compileMcelLabSource("trait-update");
    }

    function applyMcelTraitsToFirstSourceWidget() {
      mcelLabState.selectedIndex = 0;
      applyMcelTraitsToSelectedSourceWidget();
    }

    function insertMcelLabBlock(blockKey) {
      if (!mcelSourceHtml) return;
      const result = McelLabEditor.insertBlock(mcelSourceHtml.value, blockKey, {afterIndex: mcelLabState.selectedIndex});
      mcelSourceHtml.value = result.source;
      mcelLabState.selectedIndex = result.index;
      mcelLabState.compileEvents = [...mcelLabState.compileEvents, ...result.events].slice(-64);
      syncMcelGrapesFromSource();
      compileMcelLabSource(`insert-${blockKey}`);
    }

    function handleMcelRuntimeClick(event) {
      const selected = event.target.closest?.(`[${McelLabContract.attributes.sourceIndex}]`);
      if (!selected || !mcelRuntimePreview?.contains(selected)) return;
      const index = Number(selected.getAttribute(McelLabContract.attributes.sourceIndex) || "0");
      selectMcelSourceIndex(index, "runtime-click");
    }

    function selectMcelSourceIndex(index, reason = "select") {
      const normalized = McelLabEditor.normalizeRef({index}, mcelSourceHtml?.value || McelLabContract.defaultSource);
      mcelLabState.selectedIndex = normalized.index;
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "info", module: "editor", code: "MCEL_EDITOR_SELECTED", message: `Selected source widget ${normalized.index + 1} during ${reason}.`}
      ].slice(-64);
      syncMcelTraitControls();
      markSelectedMcelRuntimeElement();
      renderMcelSelectionStatus();
      renderMcelSiteSkeleton();
      renderMcelDebugger();
      renderMcelCompilerLog();
    }

    function syncMcelTraitControls() {
      const traits = McelLabEditor.readTraits(mcelSourceHtml?.value || McelLabContract.defaultSource, {index: mcelLabState.selectedIndex});
      if (!traits.found) return;
      setSelectOptions(mcelTraitKind, traits.options.kinds, traits.kind);
      setSelectOptions(mcelTraitFlow, traits.options.flows, traits.flow);
      setSelectOptions(mcelTraitRank, traits.options.ranks, traits.rank);
      setSelectOptions(mcelTraitState, traits.options.states, traits.state);
      setSelectOptions(mcelTraitDensity, traits.options.densities, traits.density);
      setSelectOptions(mcelTraitSizePolicy, traits.options.sizePolicies, traits.sizePolicy);
      setSelectOptions(mcelTraitOverflowPolicy, traits.options.overflowPolicies, traits.overflowPolicy);
      setSelectOptions(mcelTraitScrollPolicy, traits.options.scrollPolicies, traits.scrollPolicy);
      if (mcelTraitWords) mcelTraitWords.value = traits.words;
      if (mcelTraitConnects) mcelTraitConnects.value = traits.connects;
    }

    function setSelectOptions(select, values, current) {
      if (!select) return;
      select.innerHTML = "";
      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
      select.value = values.includes(current) ? current : values[0];
    }

    function markSelectedMcelRuntimeElement() {
      if (!mcelRuntimePreview) return;
      mcelRuntimePreview.querySelectorAll(`[${McelLabContract.attributes.editorSelected}="true"]`).forEach((element) => {
        element.removeAttribute(McelLabContract.attributes.editorSelected);
        element.classList.remove("mcel-selected");
      });
      const selected = mcelRuntimePreview.querySelector(`[${McelLabContract.attributes.sourceIndex}="${mcelLabState.selectedIndex}"]`);
      if (selected) {
        selected.setAttribute(McelLabContract.attributes.editorSelected, "true");
        selected.classList.add("mcel-selected");
      }
    }

    function renderMcelSelectionStatus() {
      if (!mcelSelectionStatus) return;
      const list = mcelLabState.lastSourceList.length ? mcelLabState.lastSourceList : McelLabEditor.sourceList(mcelSourceHtml?.value || "");
      const selected = list[mcelLabState.selectedIndex];
      mcelSelectionStatus.textContent = selected
        ? `Selected source widget: ${mcelLabState.selectedIndex + 1}/${list.length} · ${selected.label} · ${selected.type}/${selected.kind}/${selected.state}`
        : "Selected source widget: none";
    }

    function setMcelLabMode(mode) {
      mcelLabState.currentMode = McelLabContract.modes.includes(mode) ? mode : "source";
      document.querySelectorAll("[data-mcel-mode]").forEach((button) => {
        button.classList.toggle("active", button.dataset.mcelMode === mcelLabState.currentMode);
      });
      if (mcelLabApp) mcelLabApp.dataset.mcelMode = mcelLabState.currentMode;
      if (["diff", "stress", "a11y"].includes(mcelLabState.currentMode)) {
        openMcelDiagnosticsDrawer(`mode:${mcelLabState.currentMode}`);
      }
    }


    function currentMcelSiteFrame() {
      if (!mcelSiteFrame || !mcelSiteFrame.isConnected) {
        mcelSiteFrame = document.querySelector("#mcel-site-frame");
      }
      return mcelSiteFrame;
    }

    function ensureMcelSiteFrameTwiddle() {
      if (!mcelLabState.siteFrameTwiddle) {
        mcelLabState.siteFrameTwiddle = {
          openCount: 0,
          closeCount: 0,
          syncCount: 0,
          rebuildCount: 0,
          clearCount: 0,
          loadCount: 0,
          errorCount: 0,
          generation: 0,
          nonce: 0,
          lastReason: "boot",
          lastHash: "none",
          lastLength: 0,
          lastAt: null,
          lastReadyState: "unknown",
          lastFitStatus: "unavailable",
          lastFitViolations: 0,
          lastFitCompositionWarnings: 0,
          lastFitRemedies: "",
          lastCompositionRemedies: "",
          lastChromeFitReport: null,
          events: []
        };
      }
      if (!Array.isArray(mcelLabState.siteFrameTwiddle.events)) {
        mcelLabState.siteFrameTwiddle.events = [];
      }
      return mcelLabState.siteFrameTwiddle;
    }

    function hashMcelSiteFrameDocument(value = "") {
      let hash = 2166136261;
      for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return (hash >>> 0).toString(16).padStart(8, "0");
    }

    function readMcelSiteFrameReadyState(frame) {
      try {
        return frame?.contentDocument?.readyState || "sandboxed-or-unavailable";
      } catch (error) {
        return `sandboxed:${error?.name || "access-denied"}`;
      }
    }

    function scheduleMcelSiteFrameWrite(callback) {
      const scheduler = typeof window.requestAnimationFrame === "function"
        ? window.requestAnimationFrame
        : (task) => window.setTimeout(task, 0);
      scheduler(callback);
    }


    function summarizeMcelChromeFitReport(report) {
      if (!report) return "fit=unavailable";
      const status = report.status || "unavailable";
      const finalViolations = Number(report.finalViolations ?? report.violationCount ?? 0);
      const finalCompositionWarnings = Number(report.finalCompositionWarnings ?? report.compositionWarningCount ?? 0);
      const composition = finalCompositionWarnings > 0
        ? ` · composition=${finalCompositionWarnings}`
        : "";
      const remedies = Array.isArray(report.appliedRemedies) && report.appliedRemedies.length
        ? ` · remedies=${report.appliedRemedies.join("+")}`
        : "";
      const compositionRemedies = Array.isArray(report.appliedCompositionRemedies) && report.appliedCompositionRemedies.length
        ? ` · compositionRemedies=${report.appliedCompositionRemedies.map((item) => item.remedy || item.problem).join("+")}`
        : "";
      return `fit=${status} · violations=${finalViolations}${composition}${remedies}${compositionRemedies}`;
    }

    function accessMcelSiteFrameDocument(frame) {
      try {
        return frame?.contentDocument || null;
      } catch (error) {
        return null;
      }
    }

    function clearMcelChromeFitRuntimeState(doc) {
      const body = doc?.body;
      const html = doc?.documentElement;
      [body, html].filter(Boolean).forEach((element) => {
        element.removeAttribute("data-mcel-fit-remediation");
        element.removeAttribute("data-mcel-fit-status");
        element.removeAttribute("data-mcel-fit-violations");
        element.removeAttribute("data-mcel-composition-status");
        element.removeAttribute("data-mcel-composition-warnings");
        element.removeAttribute("data-mcel-composition-remediation");
      });
      doc?.querySelectorAll?.("[data-mcel-composition-remedy], [data-mcel-composition-warnings]").forEach((element) => {
        element.removeAttribute("data-mcel-composition-remedy");
        element.removeAttribute("data-mcel-composition-warnings");
      });
    }

    function applyMcelChromeFitRuntimeState(doc, remedies = [], status = "probing", violationCount = 0, compositionWarningCount = 0) {
      const body = doc?.body;
      const html = doc?.documentElement;
      const value = remedies.join(" ");
      [body, html].filter(Boolean).forEach((element) => {
        if (value) {
          element.setAttribute("data-mcel-fit-remediation", value);
        } else {
          element.removeAttribute("data-mcel-fit-remediation");
        }
        element.setAttribute("data-mcel-fit-status", status);
        element.setAttribute("data-mcel-fit-violations", String(violationCount));
        element.setAttribute("data-mcel-composition-status", compositionWarningCount > 0 ? "warning" : "clean");
        element.setAttribute("data-mcel-composition-warnings", String(compositionWarningCount));
      });
      // Force a synchronous style/layout pass so the next observation measures the
      // browser's result of the chrome-approved remediation rather than queued CSS.
      void doc?.documentElement?.offsetWidth;
    }

    function observeMcelChromeFit(doc, chrome) {
      const contract = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeFitContract
        ? McelLabChromeLaw.chromeFitContract(chrome)
        : {};
      const compositionContract = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeCompositionContract
        ? McelLabChromeLaw.chromeCompositionContract(chrome)
        : {};
      return McelLabBrowserObserver.observeChromeFit(doc, {
        chrome,
        selectors: contract.observeSelectors || [],
        hardObjectSelector: contract.hardObjectSelector,
        tolerancePx: contract.tolerancePx || 2,
        compositionContract
      });
    }

    function mcelChromeGeometryFailureCount(report) {
      return Number(report?.violationCount ?? report?.finalViolations ?? 0);
    }

    function mcelChromeCompositionWarningCount(report) {
      return Number(report?.compositionWarningCount ?? report?.finalCompositionWarnings ?? 0);
    }

    function mcelChromeFitFailureCount(report) {
      return mcelChromeGeometryFailureCount(report) + mcelChromeCompositionWarningCount(report);
    }

    function mcelChromeCompositionScopeSelector() {
      return [
        ".mcel-chrome-editorial-rail",
        ".mcel-chrome-cluster-grid",
        ".mcel-chrome-spotlight-primary",
        ".mcel-chrome-spotlight-support",
        ".mcel-chrome-journey-step",
        ".mcel-chrome-compact-panel"
      ].join(", ");
    }

    function mcelSafeAttributeValue(value) {
      return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    }

    function findMcelCompositionRemedyTarget(doc, warning = {}) {
      const sourceIndex = String(warning.sourceIndex || "");
      const scope = mcelChromeCompositionScopeSelector();
      if (sourceIndex) {
        const selector = `${scope} [data-mc-source-index="${mcelSafeAttributeValue(sourceIndex)}"]`;
        const candidates = [...(doc?.querySelectorAll?.(selector) || [])];
        const direct = candidates.find((element) => element.matches?.(".mc, [data-mc]"));
        if (direct) return direct;
        const nested = candidates.map((element) => element.closest?.(".mc, [data-mc]")).find(Boolean);
        if (nested) return nested;
        if (candidates.length) return candidates[0];
      }

      const chromePart = String(warning.chromePart || "");
      if (chromePart) {
        const selector = `[data-mcel-chrome-part="${mcelSafeAttributeValue(chromePart)}"]`;
        const generatedTargets = [...(doc?.querySelectorAll?.(selector) || [])];
        const withSourceChild = generatedTargets
          .map((element) => element.querySelector?.(".mc, [data-mc]") || element)
          .find((element) => element?.matches?.(".mc, [data-mc], [data-mcel-chrome-generated=\"true\"]"));
        if (withSourceChild) return withSourceChild;
      }

      return null;
    }

    function applyMcelChromeCompositionRemedies(doc, warnings = []) {
      const applied = [];
      warnings.forEach((warning) => {
        const remedy = warning?.remedy ||
          (warning?.problem === "primary-control-width-collapsed-relative-to-input"
            ? "control-balance"
            : (warning?.problem === "shape-interior-escape" ? "shape-inset-content" : ""));
        if (!remedy) return;
        const target = findMcelCompositionRemedyTarget(doc, warning);
        if (!target) return;
        const existing = new Set(String(target.getAttribute("data-mcel-composition-remedy") || "").split(/\s+/).filter(Boolean));
        const beforeRemedyCount = existing.size;
        existing.add(remedy);
        target.setAttribute("data-mcel-composition-remedy", [...existing].join(" "));
        const existingWarnings = new Set(String(target.getAttribute("data-mcel-composition-warnings") || "").split(/\s+/).filter(Boolean));
        const beforeWarningCount = existingWarnings.size;
        if (warning.problem) existingWarnings.add(warning.problem);
        target.setAttribute("data-mcel-composition-warnings", [...existingWarnings].join(" "));
        if (existing.size === beforeRemedyCount && existingWarnings.size === beforeWarningCount) return;
        applied.push({
          problem: warning.problem || "",
          remedy,
          sourceIndex: warning.sourceIndex || "",
          chromePart: warning.chromePart || "",
          fitRegion: warning.fitRegion || "",
          childTagName: warning.childTagName || "",
          shape: warning.shape || ""
        });
      });
      if (doc?.body) {
        doc.body.setAttribute("data-mcel-composition-remediation", applied.length ? "active" : "none");
      }
      void doc?.documentElement?.offsetWidth;
      return applied;
    }

    function runMcelSiteFrameChromeFit(reason = "chrome-fit") {
      const frame = currentMcelSiteFrame();
      const twiddle = ensureMcelSiteFrameTwiddle();
      const chrome = frame?.dataset?.chrome || mcelLabState.chrome || "chrome-strict-hierarchy";
      if (!frame || typeof McelLabBrowserObserver === "undefined" || typeof McelLabBrowserObserver.observeChromeFit !== "function") {
        const unavailable = {
          kind: "mcel-chrome-fit-report",
          chrome,
          status: "unavailable",
          reason,
          firstPassViolations: 0,
          finalViolations: 0,
          firstPassCompositionWarnings: 0,
          finalCompositionWarnings: 0,
          repaired: false,
          appliedRemedies: [],
          appliedCompositionRemedies: [],
          compositionWarnings: [],
          violations: [],
          warnings: ["Chrome fit observer is unavailable."]
        };
        mcelLabState.lastChromeFitReport = unavailable;
        twiddle.lastChromeFitReport = unavailable;
        twiddle.lastFitStatus = "unavailable";
        twiddle.lastFitViolations = 0;
        twiddle.lastFitCompositionWarnings = 0;
        twiddle.lastFitRemedies = "";
        twiddle.lastCompositionRemedies = "";
        renderMcelSiteFrameTwiddle(reason);
        return unavailable;
      }

      const doc = accessMcelSiteFrameDocument(frame);
      if (!doc?.body) {
        const unavailable = {
          kind: "mcel-chrome-fit-report",
          chrome,
          status: "unavailable",
          reason,
          firstPassViolations: 0,
          finalViolations: 0,
          firstPassCompositionWarnings: 0,
          finalCompositionWarnings: 0,
          repaired: false,
          appliedRemedies: [],
          appliedCompositionRemedies: [],
          compositionWarnings: [],
          violations: [],
          warnings: ["Rendered iframe document is unavailable; check sandbox allow-same-origin."]
        };
        mcelLabState.lastChromeFitReport = unavailable;
        twiddle.lastChromeFitReport = unavailable;
        twiddle.lastFitStatus = "unavailable";
        twiddle.lastFitViolations = 0;
        twiddle.lastFitCompositionWarnings = 0;
        twiddle.lastFitRemedies = "";
        twiddle.lastCompositionRemedies = "";
        frame.dataset.fitStatus = "unavailable";
        frame.dataset.fitViolations = "0";
        frame.dataset.fitCompositionWarnings = "0";
        frame.dataset.compositionRemedies = "";
        renderMcelSiteFrameTwiddle(reason);
        return unavailable;
      }

      clearMcelChromeFitRuntimeState(doc);
      const first = observeMcelChromeFit(doc, chrome);
      const firstPassViolations = mcelChromeGeometryFailureCount(first);
      const firstPassCompositionWarnings = mcelChromeCompositionWarningCount(first);
      const firstPassFailures = mcelChromeFitFailureCount(first);
      const plan = typeof McelLabChromeLaw !== "undefined" && McelLabChromeLaw.chromeRemediationPlan
        ? McelLabChromeLaw.chromeRemediationPlan(chrome)
        : {strategies: []};
      const strategies = Array.isArray(plan.strategies) ? plan.strategies : [];
      const appliedRemedies = [];
      const appliedCompositionRemedies = [];
      const passes = [{
        stage: "prevent",
        remedies: [],
        compositionRemedies: [],
        report: first
      }];
      let current = first;

      const applyCompositionRemediesIfNeeded = (stage) => {
        if (mcelChromeCompositionWarningCount(current) === 0) return false;
        const applied = applyMcelChromeCompositionRemedies(doc, current.compositionWarnings || []);
        if (!applied.length) return false;
        applied.forEach((item) => appliedCompositionRemedies.push(item));
        current = observeMcelChromeFit(doc, chrome);
        passes.push({
          stage,
          remedies: [...appliedRemedies],
          compositionRemedies: [...appliedCompositionRemedies],
          report: current
        });
        return true;
      };

      const runCompositionRemediationPasses = (prefix) => {
        for (let index = 0; index < 4 && mcelChromeCompositionWarningCount(current) > 0; index += 1) {
          if (!applyCompositionRemediesIfNeeded(index === 0 ? prefix : `${prefix}-${index + 1}`)) break;
        }
      };

      runCompositionRemediationPasses("composition-remedy");

      if (mcelChromeFitFailureCount(current) > 0 && mcelChromeGeometryFailureCount(current) > 0 && strategies.length) {
        for (const strategy of strategies) {
          if (!strategy?.id) continue;
          appliedRemedies.push(strategy.id);
          applyMcelChromeFitRuntimeState(
            doc,
            appliedRemedies,
            "probing",
            mcelChromeGeometryFailureCount(current),
            mcelChromeCompositionWarningCount(current)
          );
          current = observeMcelChromeFit(doc, chrome);
          passes.push({
            stage: strategy.id,
            remedies: [...appliedRemedies],
            compositionRemedies: [...appliedCompositionRemedies],
            report: current
          });
          runCompositionRemediationPasses(`composition-after-${strategy.id}`);
          if (mcelChromeFitFailureCount(current) === 0) break;
        }
      }

      const finalViolations = mcelChromeGeometryFailureCount(current);
      const finalCompositionWarnings = mcelChromeCompositionWarningCount(current);
      const finalFailures = finalViolations + finalCompositionWarnings;
      const status = firstPassFailures === 0
        ? "clean"
        : (finalFailures === 0 ? "repaired" : "failed");
      applyMcelChromeFitRuntimeState(doc, appliedRemedies, status, finalViolations, finalCompositionWarnings);

      const finalReport = {
        kind: "mcel-chrome-fit-report",
        chrome,
        status,
        reason,
        firstPassViolations,
        firstPassCompositionWarnings,
        firstPassFailures,
        finalViolations,
        finalCompositionWarnings,
        finalFailures,
        repaired: status === "repaired",
        appliedRemedies,
        appliedCompositionRemedies,
        passes: passes.map((pass) => ({
          stage: pass.stage,
          remedies: pass.remedies,
          compositionRemedies: pass.compositionRemedies || [],
          violationCount: mcelChromeGeometryFailureCount(pass.report),
          compositionWarningCount: mcelChromeCompositionWarningCount(pass.report),
          failureCount: mcelChromeFitFailureCount(pass.report)
        })),
        violations: current.violations || [],
        compositionWarnings: current.compositionWarnings || [],
        compositionWarningCount: finalCompositionWarnings,
        tolerancePx: current.tolerancePx || first.tolerancePx || 2,
        warnings: current.warnings || []
      };

      mcelLabState.lastChromeFitReport = finalReport;
      twiddle.lastChromeFitReport = finalReport;
      twiddle.lastFitStatus = status;
      twiddle.lastFitViolations = finalViolations;
      twiddle.lastFitCompositionWarnings = finalCompositionWarnings;
      twiddle.lastFitRemedies = appliedRemedies.join("+");
      twiddle.lastCompositionRemedies = appliedCompositionRemedies.map((item) => item.remedy || item.problem).join("+");
      frame.dataset.fitStatus = status;
      frame.dataset.fitViolations = String(finalViolations);
      frame.dataset.fitCompositionWarnings = String(finalCompositionWarnings);
      frame.dataset.fitRemedies = appliedRemedies.join("+");
      frame.dataset.compositionRemedies = twiddle.lastCompositionRemedies;
      recordMcelSiteFrameTwiddle("chrome-fit", {
        reason,
        hash: frame.dataset.srcdocHash,
        length: Number(frame.dataset.srcdocLength || 0),
        fitStatus: status,
        fitViolations: finalViolations,
        fitCompositionWarnings: finalCompositionWarnings
      });
      return finalReport;
    }

    function recordMcelSiteFrameTwiddle(action, details = {}) {
      const twiddle = ensureMcelSiteFrameTwiddle();
      const frame = currentMcelSiteFrame();
      const event = {
        at: new Date().toISOString(),
        action,
        reason: details.reason || twiddle.lastReason || "unknown",
        hash: details.hash || frame?.dataset?.srcdocHash || twiddle.lastHash || "none",
        length: Number.isFinite(details.length) ? details.length : Number(frame?.dataset?.srcdocLength || twiddle.lastLength || 0),
        generation: Number(frame?.dataset?.generation || twiddle.generation || 0),
        connected: Boolean(frame?.isConnected),
        modalHidden: mcelSiteModal?.getAttribute("aria-hidden") || "missing",
        synced: frame?.dataset?.synced || "never",
        fitStatus: details.fitStatus || frame?.dataset?.fitStatus || twiddle.lastFitStatus || "unavailable",
        fitViolations: Number(details.fitViolations ?? frame?.dataset?.fitViolations ?? twiddle.lastFitViolations ?? 0),
        fitCompositionWarnings: Number(details.fitCompositionWarnings ?? frame?.dataset?.fitCompositionWarnings ?? twiddle.lastFitCompositionWarnings ?? 0)
      };
      twiddle.events = [...twiddle.events, event].slice(-10);
      twiddle.lastAt = event.at;
      renderMcelSiteFrameTwiddle(action);
    }

    function renderMcelSiteFrameTwiddle(reason = "render") {
      const twiddle = ensureMcelSiteFrameTwiddle();
      const frame = currentMcelSiteFrame();
      const srcdocLength = Number(frame?.dataset?.srcdocLength || frame?.srcdoc?.length || 0);
      const stateLine = [
        `state=${mcelLabState.activeModal || "closed"}`,
        `modalHidden=${mcelSiteModal?.getAttribute("aria-hidden") || "missing"}`,
        `frame=${frame?.isConnected ? "connected" : "missing"}`,
        `generation=${frame?.dataset?.generation || twiddle.generation || 0}`,
        `opens=${twiddle.openCount}`,
        `closes=${twiddle.closeCount}`,
        `syncs=${twiddle.syncCount}`,
        `loads=${twiddle.loadCount}`,
        `rebuilds=${twiddle.rebuildCount}`,
        `clears=${twiddle.clearCount}`,
        `hash=${frame?.dataset?.srcdocHash || twiddle.lastHash || "none"}`,
        `len=${srcdocLength}`,
        `theme=${mcelLabState.theme || "theme-machine"}`,
        `chrome=${mcelLabState.chrome || "chrome-strict-hierarchy"}`,
        summarizeMcelChromeFitReport(twiddle.lastChromeFitReport || mcelLabState.lastChromeFitReport),
        `reason=${reason}`
      ].join(" · ");
      if (mcelSiteFrameStatus) {
        mcelSiteFrameStatus.textContent = stateLine;
      }
      if (mcelSiteFrameMiniStatus) {
        mcelSiteFrameMiniStatus.textContent = `render iframe: ${stateLine}`;
      }
      if (mcelSiteFrameLog) {
        mcelSiteFrameLog.textContent = (twiddle.events || [])
          .slice()
          .reverse()
          .map((event) => [
            event.at,
            event.action,
            `reason=${event.reason}`,
            `hash=${event.hash}`,
            `len=${event.length}`,
            `generation=${event.generation}`,
            `connected=${event.connected}`,
            `modalHidden=${event.modalHidden}`,
            `synced=${event.synced}`,
            `fit=${event.fitStatus || "unavailable"}`,
            `fitViolations=${event.fitViolations ?? 0}`,
            `compositionWarnings=${event.fitCompositionWarnings ?? 0}`
          ].join(" | "))
          .join("\n") || "No iframe lifecycle events recorded yet.";
      }
    }

    function bindMcelSiteFrameLifecycle(reason = "bind") {
      const frame = currentMcelSiteFrame();
      if (!frame || frame.dataset.lifecycleBound === "true") {
        renderMcelSiteFrameTwiddle(reason);
        return frame;
      }
      frame.dataset.lifecycleBound = "true";
      frame.addEventListener("load", () => {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.loadCount += 1;
        twiddle.lastReadyState = readMcelSiteFrameReadyState(frame);
        recordMcelSiteFrameTwiddle("iframe-load", {reason: frame.dataset.synced || reason});
        scheduleMcelSiteFrameWrite(() => runMcelSiteFrameChromeFit(frame.dataset.synced || reason || "iframe-load"));
        recordMcelEvent("ui", "MCEL_SITE_IFRAME_LOADED", `Rendered-site iframe loaded generation ${frame.dataset.generation || 0}.`);
      });
      frame.addEventListener("error", () => {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.errorCount += 1;
        recordMcelSiteFrameTwiddle("iframe-error", {reason: frame.dataset.synced || reason});
        recordMcelEvent("ui", "MCEL_SITE_IFRAME_ERROR", "Rendered-site iframe emitted an error event.");
      });
      renderMcelSiteFrameTwiddle(reason);
      return frame;
    }

    function clearMcelSiteFrameSrcdoc(reason = "clear-srcdoc") {
      const frame = bindMcelSiteFrameLifecycle(reason);
      if (!frame) return;
      const twiddle = ensureMcelSiteFrameTwiddle();
      twiddle.clearCount += 1;
      frame.removeAttribute("srcdoc");
      frame.srcdoc = "";
      frame.dataset.synced = reason;
      frame.dataset.srcdocHash = "empty";
      frame.dataset.srcdocLength = "0";
      twiddle.lastReason = reason;
      twiddle.lastHash = "empty";
      twiddle.lastLength = 0;
      recordMcelSiteFrameTwiddle("iframe-clear", {reason, hash: "empty", length: 0});
      recordMcelEvent("ui", "MCEL_SITE_IFRAME_CLEARED", "Rendered-site iframe srcdoc was cleared from the lifecycle twiddle.");
    }

    function rebuildMcelSiteFrameShell(reason = "rebuild-frame", options = {}) {
      const frame = currentMcelSiteFrame();
      if (!frame || !frame.parentElement) return;
      const twiddle = ensureMcelSiteFrameTwiddle();
      const replacement = document.createElement("iframe");
      replacement.id = "mcel-site-frame";
      replacement.className = "mcel-site-frame";
      replacement.title = "Isolated MCEL rendered site";
      replacement.setAttribute("sandbox", "allow-same-origin");
      replacement.dataset.generation = String((Number(frame.dataset.generation || twiddle.generation || 0) || 0) + 1);
      replacement.dataset.synced = "fresh-shell";
      frame.replaceWith(replacement);
      mcelSiteFrame = replacement;
      twiddle.rebuildCount += 1;
      twiddle.generation = Number(replacement.dataset.generation || twiddle.generation || 0);
      bindMcelSiteFrameLifecycle(reason);
      recordMcelSiteFrameTwiddle("iframe-rebuild", {reason, hash: "fresh-shell", length: 0});
      recordMcelEvent("ui", "MCEL_SITE_IFRAME_REBUILT", `Rendered-site iframe shell rebuilt for ${reason}.`);
      if (options.syncAfter) {
        syncMcelRenderedSiteFrame(`${reason}:sync-after-rebuild`);
      }
    }

    function openMcelLabModal(which = "site") {
      const target = which === "editor" ? mcelEditorModal : mcelSiteModal;
      if (!target) return;
      closeMcelLabModal("all", {silent: true});
      target.setAttribute("aria-hidden", "false");
      target.dataset.open = "true";
      mcelLabState.activeModal = which === "editor" ? "editor" : "site";
      document.body?.classList?.add("mcel-modal-open");
      if (which === "site") {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.openCount += 1;
        bindMcelSiteFrameLifecycle("open-site-modal");
        syncMcelRenderedSiteFrame("open-site-modal");
        recordMcelSiteFrameTwiddle("modal-open", {reason: "open-site-modal"});
      } else {
        syncMcelGrapesFromSource();
      }
      recordMcelEvent("ui", "MCEL_MODAL_OPENED", `${mcelLabState.activeModal} modal opened as isolated product surface.`);
    }

    function closeMcelLabModal(which = "all", options = {}) {
      const wasSiteClose = which === "site" || which === "all" || mcelLabState.activeModal === "site";
      const targets = [];
      if (which === "editor" || which === "all") targets.push(mcelEditorModal);
      if (which === "site" || which === "all") targets.push(mcelSiteModal);
      targets.filter(Boolean).forEach((modal) => {
        modal.setAttribute("aria-hidden", "true");
        delete modal.dataset.open;
      });
      if (which === "all" || mcelLabState.activeModal === which) {
        mcelLabState.activeModal = null;
        document.body?.classList?.remove("mcel-modal-open");
      }
      if (wasSiteClose) {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.closeCount += 1;
        recordMcelSiteFrameTwiddle("modal-close", {reason: options.silent ? "silent-close" : "close-modal"});
      }
      if (!options.silent) recordMcelEvent("ui", "MCEL_MODAL_CLOSED", `${which} modal closed by outside click, Escape, or Close button.`);
    }

    function isolatedSiteCss() {
      return `
        :root {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 90% 0%, rgba(246, 199, 91, 0.18), transparent 34rem),
            radial-gradient(circle at 0% 20%, rgba(115, 214, 255, 0.08), transparent 28rem),
            #050605;
          --site-page: #080907;
          --site-card: #090b08;
          --site-card-soft: rgba(255,255,255,0.035);
          --site-ink: #fff8df;
          --site-muted: #b9b28d;
          --site-heading: #fff8df;
          --site-accent: #f6c75b;
          --site-accent-2: #aee06f;
          --site-action: #f6c75b;
          --site-action-ink: #151205;
          --site-line: rgba(246, 199, 91, 0.22);
          --site-shadow: 0 24px 80px rgba(0,0,0,0.26);
          --site-radius: 22px;
          --site-radius-sm: 999px;
          --site-max: 1180px;
          --site-font-body: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          --site-font-display: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          --site-heading-track: -0.075em;
          --site-hero-columns: minmax(0, 1.12fr) minmax(240px, 0.88fr);
          --site-hero-min: clamp(320px, 52vh, 620px);
          --site-hero-ornament-display: block;
          --site-hero-ornament-radius: 999px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(174,224,111,0.94), rgba(174,224,111,0.72)),
            radial-gradient(circle at 50% 26%, rgba(255,255,255,0.2), transparent 30%);
          --site-hero-ornament-shadow: inset 0 0 0 1px rgba(255,255,255,0.24), 0 24px 80px rgba(174,224,111,0.18);
          --site-hero-bg:
            radial-gradient(circle at 88% 18%, rgba(115, 214, 255, 0.22), transparent 30%),
            linear-gradient(135deg, rgba(246, 199, 91, 0.12), rgba(174, 224, 111, 0.06)),
            #0b0d09;
          --site-grid-overlay: none;
        }

        body.theme-machine,
        .mcel-runtime-preview.theme-machine {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 90% 0%, rgba(246, 199, 91, 0.18), transparent 34rem),
            radial-gradient(circle at 0% 20%, rgba(115, 214, 255, 0.08), transparent 28rem),
            #050605;
          --site-page: #080907;
          --site-card: #090b08;
          --site-card-soft: rgba(255,255,255,0.035);
          --site-ink: #fff8df;
          --site-muted: #b9b28d;
          --site-heading: #fff8df;
          --site-accent: #f6c75b;
          --site-accent-2: #aee06f;
          --site-action: #f6c75b;
          --site-action-ink: #151205;
          --site-line: rgba(246, 199, 91, 0.22);
          --site-shadow: 0 24px 80px rgba(0,0,0,0.26);
          --site-radius: 22px;
          --site-radius-sm: 999px;
          --site-max: 1180px;
          --site-heading-track: -0.075em;
          --site-hero-columns: minmax(0, 1.12fr) minmax(240px, 0.88fr);
          --site-hero-min: clamp(320px, 52vh, 620px);
          --site-hero-ornament-display: block;
          --site-hero-ornament-radius: 999px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(174,224,111,0.94), rgba(174,224,111,0.72)),
            radial-gradient(circle at 50% 26%, rgba(255,255,255,0.2), transparent 30%);
          --site-hero-ornament-shadow: inset 0 0 0 1px rgba(255,255,255,0.24), 0 24px 80px rgba(174,224,111,0.18);
          --site-hero-bg:
            radial-gradient(circle at 88% 18%, rgba(115, 214, 255, 0.22), transparent 30%),
            linear-gradient(135deg, rgba(246, 199, 91, 0.12), rgba(174, 224, 111, 0.06)),
            #0b0d09;
          --site-grid-overlay: none;
        }

        body.theme-local,
        .mcel-runtime-preview.theme-local {
          color-scheme: light;
          --site-bg:
            radial-gradient(circle at 90% 0%, rgba(247, 201, 72, 0.24), transparent 28rem),
            linear-gradient(180deg, #f6eddd, #eee4d1);
          --site-page: rgba(255, 252, 244, 0.94);
          --site-card: #fffaf0;
          --site-card-soft: rgba(255,255,255,0.72);
          --site-ink: #1b2118;
          --site-muted: #657058;
          --site-heading: #141a11;
          --site-accent: #2d7a4f;
          --site-accent-2: #d77a2d;
          --site-action: #f5c84c;
          --site-action-ink: #1f1700;
          --site-line: rgba(47, 80, 48, 0.2);
          --site-shadow: 0 18px 55px rgba(54, 69, 44, 0.16);
          --site-radius: 22px;
          --site-radius-sm: 14px;
          --site-hero-ornament-radius: 30px;
          --site-hero-ornament-bg:
            linear-gradient(135deg, rgba(45,122,79,0.86), rgba(120,160,78,0.82)),
            radial-gradient(circle at 30% 22%, rgba(255,255,255,0.52), transparent 36%);
          --site-hero-ornament-shadow: 0 28px 80px rgba(45, 122, 79, 0.24);
          --site-hero-bg:
            radial-gradient(circle at 95% 12%, rgba(45,122,79,0.13), transparent 32%),
            linear-gradient(135deg, rgba(255,255,255,0.82), rgba(255,248,230,0.7)),
            #fffaf0;
        }

        body.theme-saas,
        .mcel-runtime-preview.theme-saas {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 88% 6%, rgba(84, 116, 255, 0.28), transparent 31rem),
            radial-gradient(circle at 8% 22%, rgba(0, 214, 201, 0.18), transparent 28rem),
            #070916;
          --site-page: rgba(12, 16, 34, 0.92);
          --site-card: rgba(18, 24, 48, 0.9);
          --site-card-soft: rgba(255,255,255,0.06);
          --site-ink: #f6f8ff;
          --site-muted: #aab5d6;
          --site-heading: #ffffff;
          --site-accent: #64e4ff;
          --site-accent-2: #9d7cff;
          --site-action: #8dffcb;
          --site-action-ink: #00150f;
          --site-line: rgba(127, 157, 255, 0.28);
          --site-shadow: 0 28px 90px rgba(0, 0, 0, 0.42);
          --site-radius: 28px;
          --site-radius-sm: 16px;
          --site-heading-track: -0.075em;
          --site-hero-ornament-radius: 40% 60% 48% 52%;
          --site-hero-ornament-bg:
            linear-gradient(135deg, rgba(100,228,255,0.96), rgba(157,124,255,0.9)),
            radial-gradient(circle at 35% 24%, rgba(255,255,255,0.48), transparent 31%);
          --site-hero-ornament-shadow: 0 28px 110px rgba(100, 228, 255, 0.22);
          --site-hero-bg:
            linear-gradient(135deg, rgba(255,255,255,0.08), rgba(255,255,255,0.025)),
            rgba(12, 16, 34, 0.9);
        }

        body.theme-editorial,
        .mcel-runtime-preview.theme-editorial {
          color-scheme: light;
          --site-bg:
            linear-gradient(90deg, rgba(56, 44, 28, 0.05) 1px, transparent 1px),
            #f7f0e2;
          --site-page: #fffaf0;
          --site-card: #fffdf7;
          --site-card-soft: rgba(244,232,210,0.7);
          --site-ink: #251f17;
          --site-muted: #736450;
          --site-heading: #17120d;
          --site-accent: #9b3f2c;
          --site-accent-2: #1f5a68;
          --site-action: #17120d;
          --site-action-ink: #fff6e5;
          --site-line: rgba(45, 35, 22, 0.2);
          --site-shadow: none;
          --site-radius: 10px;
          --site-radius-sm: 6px;
          --site-max: 980px;
          --site-font-display: Georgia, "Times New Roman", serif;
          --site-font-body: Georgia, "Times New Roman", serif;
          --site-heading-track: -0.045em;
          --site-hero-columns: minmax(0, 0.95fr) minmax(220px, 0.7fr);
          --site-hero-ornament-radius: 6px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(155,63,44,0.94), rgba(31,90,104,0.88)),
            repeating-linear-gradient(45deg, rgba(255,255,255,0.18) 0 8px, transparent 8px 18px);
          --site-hero-ornament-shadow: none;
          --site-hero-bg: #fffdf7;
        }

        body.theme-luxury,
        .mcel-runtime-preview.theme-luxury {
          color-scheme: dark;
          --site-bg:
            radial-gradient(circle at 78% 8%, rgba(210, 172, 91, 0.18), transparent 30rem),
            #070605;
          --site-page: #0d0b09;
          --site-card: #15110d;
          --site-card-soft: rgba(210, 172, 91, 0.08);
          --site-ink: #fbf3df;
          --site-muted: #b9aa88;
          --site-heading: #fff7df;
          --site-accent: #d6b56d;
          --site-accent-2: #8e6f41;
          --site-action: #d6b56d;
          --site-action-ink: #120d04;
          --site-line: rgba(214, 181, 109, 0.32);
          --site-shadow: 0 26px 90px rgba(0,0,0,0.52);
          --site-radius: 4px;
          --site-radius-sm: 2px;
          --site-font-display: "Didot", "Bodoni 72", Georgia, serif;
          --site-heading-track: -0.035em;
          --site-hero-ornament-radius: 2px;
          --site-hero-ornament-bg:
            linear-gradient(145deg, rgba(214,181,109,0.88), rgba(75,55,30,0.9)),
            radial-gradient(circle at 40% 20%, rgba(255,255,255,0.32), transparent 28%);
          --site-hero-ornament-shadow: 0 26px 90px rgba(214, 181, 109, 0.18);
          --site-hero-bg:
            linear-gradient(135deg, rgba(214,181,109,0.11), rgba(255,255,255,0.02)),
            #15110d;
        }

        body.theme-civic,
        .mcel-runtime-preview.theme-civic {
          color-scheme: light;
          --site-bg: linear-gradient(180deg, #e8f1fb, #f7fbff 38%, #ffffff);
          --site-page: #ffffff;
          --site-card: #ffffff;
          --site-card-soft: #eef6ff;
          --site-ink: #132538;
          --site-muted: #51677d;
          --site-heading: #07192c;
          --site-accent: #075da8;
          --site-accent-2: #1b7b6d;
          --site-action: #075da8;
          --site-action-ink: #ffffff;
          --site-line: rgba(7, 93, 168, 0.22);
          --site-shadow: 0 16px 40px rgba(11, 57, 94, 0.12);
          --site-radius: 14px;
          --site-radius-sm: 8px;
          --site-heading-track: -0.045em;
          --site-hero-ornament-radius: 999px 999px 20px 20px;
          --site-hero-ornament-bg:
            linear-gradient(180deg, rgba(7,93,168,0.9), rgba(27,123,109,0.86)),
            radial-gradient(circle at 50% 20%, rgba(255,255,255,0.42), transparent 30%);
          --site-hero-ornament-shadow: 0 20px 70px rgba(7, 93, 168, 0.2);
          --site-hero-bg:
            linear-gradient(135deg, rgba(7,93,168,0.08), rgba(27,123,109,0.04)),
            #ffffff;
        }

        body.theme-accessible,
        .mcel-runtime-preview.theme-accessible {
          color-scheme: dark;
          --site-bg: #000000;
          --site-page: #000000;
          --site-card: #000000;
          --site-card-soft: #101010;
          --site-ink: #ffffff;
          --site-muted: #ffffff;
          --site-heading: #ffffff;
          --site-accent: #00e5ff;
          --site-accent-2: #ff7a00;
          --site-action: #ffff00;
          --site-action-ink: #000000;
          --site-line: #ffffff;
          --site-shadow: none;
          --site-radius: 0;
          --site-radius-sm: 0;
          --site-font-body: Arial, Helvetica, sans-serif;
          --site-font-display: Arial, Helvetica, sans-serif;
          --site-heading-track: -0.02em;
          --site-hero-ornament-display: none;
          --site-hero-bg: #000000;
        }

        body.theme-debug,
        .mcel-runtime-preview.theme-debug {
          color-scheme: light;
          --site-bg:
            linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px),
            linear-gradient(0deg, rgba(0,0,0,0.05) 1px, transparent 1px),
            #fafafa;
          --site-page: transparent;
          --site-card: rgba(255,255,255,0.84);
          --site-card-soft: rgba(0, 118, 255, 0.06);
          --site-ink: #101010;
          --site-muted: #313131;
          --site-heading: #000000;
          --site-accent: #005cff;
          --site-accent-2: #ff3b30;
          --site-action: #000000;
          --site-action-ink: #ffffff;
          --site-line: #005cff;
          --site-shadow: none;
          --site-radius: 0;
          --site-radius-sm: 0;
          --site-font-body: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          --site-font-display: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          --site-heading-track: -0.02em;
          --site-hero-ornament-display: none;
          --site-hero-bg: rgba(0, 92, 255, 0.04);
          --site-grid-overlay:
            linear-gradient(90deg, rgba(0,92,255,0.1) 1px, transparent 1px),
            linear-gradient(0deg, rgba(255,59,48,0.08) 1px, transparent 1px);
          background-size: 24px 24px;
        }

        * { box-sizing: border-box; }
        html { min-height: 100%; background: var(--site-bg); }
        body {
          margin: 0;
          min-height: 100%;
          font-family: var(--site-font-body);
          color: var(--site-ink);
          background: var(--site-bg);
        }
        .mcel-runtime-preview {
          width: min(var(--site-max), calc(100% - 32px));
          margin: 0 auto;
          padding: clamp(18px, 3vw, 44px) 0;
          display: grid;
          gap: 18px;
          overflow: visible;
          color: var(--site-ink);
        }
        .mcel-runtime-preview .mc {
          min-width: 0;
          position: relative;
          display: grid;
          gap: 14px;
          padding: clamp(18px, 3vw, 34px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius);
          background:
            var(--site-grid-overlay),
            linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015)),
            var(--site-card);
          box-shadow: var(--site-shadow);
          overflow: visible;
        }
        .mcel-runtime-preview [data-mc-generated="true"] {
          display: none !important;
        }
        body.theme-debug .mcel-runtime-preview [data-mc-generated="true"] {
          display: grid !important;
          min-height: 12px;
          color: var(--site-accent-2);
          font-size: 10px;
          text-transform: uppercase;
        }
        .mcel-runtime-preview .mc[data-mc-scroll-owner="self"] {
          max-block-size: min(62vh, 560px);
          overflow: auto;
        }
        .mcel-runtime-preview .mc[data-mc-scroll-owner="content"],
        .mcel-runtime-preview .mc[data-mc-scroll-owner="parent"],
        .mcel-runtime-preview .mc[data-mc-scroll-owner="viewport"] {
          overflow: visible !important;
        }
        .mcel-runtime-preview .mc[data-mc-scroll-owner="none"] {
          overflow: clip;
        }
        .mcel-runtime-preview > .mc[data-mc-component-kind="page"] {
          gap: clamp(16px, 2.4vw, 28px);
          padding: clamp(18px, 3vw, 40px);
          border-radius: calc(var(--site-radius) + 8px);
          background:
            var(--site-grid-overlay),
            radial-gradient(circle at 86% 4%, color-mix(in srgb, var(--site-accent) 14%, transparent), transparent 30%),
            var(--site-page);
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"] {
          grid-template-columns: var(--site-hero-columns);
          align-items: center;
          min-block-size: var(--site-hero-min);
          background:
            var(--site-grid-overlay),
            var(--site-hero-bg);
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
          content: "";
          display: var(--site-hero-ornament-display);
          inline-size: min(100%, 370px);
          aspect-ratio: 0.72;
          justify-self: end;
          grid-row: 1 / span 4;
          grid-column: 2;
          border: 1px solid var(--site-line);
          border-radius: var(--site-hero-ornament-radius);
          background: var(--site-hero-ornament-bg);
          box-shadow: var(--site-hero-ornament-shadow);
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"] > *:not([data-mc-generated="true"]) {
          grid-column: 1;
          z-index: 1;
        }
        .mcel-runtime-preview h1,
        .mcel-runtime-preview h2,
        .mcel-runtime-preview h3,
        .mcel-runtime-preview p {
          margin-block: 0;
        }
        .mcel-runtime-preview h1,
        .mcel-runtime-preview h2 {
          font-family: var(--site-font-display);
          color: var(--site-heading);
        }
        .mcel-runtime-preview h1 {
          max-width: 12ch;
          font-size: clamp(40px, 7vw, 92px);
          line-height: 0.92;
          letter-spacing: var(--site-heading-track);
        }
        .mcel-runtime-preview h2 {
          font-size: clamp(24px, 3vw, 42px);
          line-height: 1.02;
          letter-spacing: calc(var(--site-heading-track) * 0.45);
        }
        .mcel-runtime-preview h3 {
          width: fit-content;
          color: var(--site-accent);
          font-size: 13px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }

        body.theme-machine .mcel-runtime-preview h3 {
          color: var(--site-accent-2);
          font-size: 15px;
        }
        .mcel-runtime-preview p {
          max-width: 68ch;
          color: var(--site-muted);
          font-weight: 700;
          line-height: 1.58;
        }
        body.theme-editorial .mcel-runtime-preview p {
          font-size: 18px;
          font-weight: 500;
          line-height: 1.72;
        }
        body.theme-accessible .mcel-runtime-preview p,
        body.theme-accessible .mcel-runtime-preview label,
        body.theme-accessible .mcel-runtime-preview input,
        body.theme-accessible .mcel-runtime-preview button {
          font-size: 18px;
          line-height: 1.6;
        }
        .mcel-runtime-preview [data-mc-slot="meta"] {
          width: fit-content;
          border: 1px solid var(--site-line);
          border-radius: 999px;
          padding: 6px 10px;
          color: var(--site-accent);
          background: var(--site-card-soft);
          font-size: 12px;
          font-weight: 900;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }

        body.theme-machine .mcel-runtime-preview [data-mc-slot="meta"] {
          border-color: rgba(115, 214, 255, 0.26);
          color: #73d6ff;
          background: transparent;
          font-weight: 950;
        }
        .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
          grid-template-columns: repeat(3, minmax(0, 1fr));
          align-items: stretch;
        }
        .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > h2 {
          grid-column: 1 / -1;
        }
        .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > .mc {
          min-block-size: 100%;
          align-content: start;
          background:
            var(--site-grid-overlay),
            var(--site-card-soft);
        }
        .mcel-runtime-preview form.mc {
          grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto;
          align-items: end;
          gap: 14px;
        }
        .mcel-runtime-preview form.mc h2 {
          grid-column: 1 / -1;
        }
        .mcel-runtime-preview form.mc label {
          display: grid;
          gap: 8px;
          color: var(--site-muted);
          font-size: 12px;
          font-weight: 900;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .mcel-runtime-preview input {
          min-width: 0;
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card);
          color: var(--site-ink);
          padding: 13px 15px;
          font: inherit;
        }

        body.theme-machine .mcel-runtime-preview input {
          border-color: rgba(246, 199, 91, 0.32);
          background: #030403;
        }
        .mcel-runtime-preview button,
        .mcel-runtime-preview a[data-mc-action] {
          justify-self: start;
          min-height: 42px;
          border: 0;
          border-radius: 999px;
          background: var(--site-action);
          color: var(--site-action-ink);
          padding: 12px 20px;
          font-weight: 950;
          text-decoration: none;
          cursor: pointer;
          box-shadow: none;
        }
        body.theme-accessible .mcel-runtime-preview button,
        body.theme-accessible .mcel-runtime-preview a[data-mc-action] {
          min-height: 52px;
          border: 3px solid #ffffff;
        }
        body.theme-luxury .mcel-runtime-preview button,
        body.theme-luxury .mcel-runtime-preview a[data-mc-action] {
          border-radius: 2px;
          text-transform: uppercase;
          letter-spacing: 0.1em;
        }
        body.theme-editorial .mcel-runtime-preview button,
        body.theme-editorial .mcel-runtime-preview a[data-mc-action] {
          border-radius: 2px;
        }
        .mcel-runtime-preview .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
        }
        body.theme-debug .mcel-runtime-preview .mc::before {
          content: attr(data-mc) " / " attr(data-mc-kind);
          justify-self: start;
          padding: 3px 6px;
          border: 1px solid var(--site-accent-2);
          color: var(--site-accent-2);
          background: #fff;
          font-size: 10px;
          font-weight: 900;
          text-transform: uppercase;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview {
          max-inline-size: min(1120px, calc(100% - 48px));
          padding-block: clamp(22px, 4vw, 54px);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview > .mc {
          display: block;
          min-block-size: auto;
          border: 0;
          border-radius: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
          display: grid;
          grid-template-columns: minmax(0, 1.35fr) minmax(260px, 0.65fr);
          gap: clamp(24px, 4vw, 56px);
          align-items: start;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body {
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede {
          grid-column: 1 / -1;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body {
          display: grid;
          gap: clamp(18px, 3vw, 32px);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail {
          position: sticky;
          top: 24px;
          display: grid;
          gap: 16px;
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede > .mc,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body > .mc,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail > .mc {
          border-color: var(--site-line);
          background: transparent;
          box-shadow: none;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede > .mc[data-mc-kind="hero"] {
          grid-template-columns: minmax(0, 1fr);
          min-block-size: auto;
          padding: clamp(28px, 6vw, 76px) 0 clamp(22px, 4vw, 44px);
          border: 0;
          border-bottom: 1px solid var(--site-line);
          border-radius: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
          display: none;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede h1,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede h2 {
          max-inline-size: 14ch;
          font-size: clamp(3.4rem, 12vw, 8.8rem);
          line-height: 0.88;
          letter-spacing: -0.075em;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-lede p:not([data-mc-slot="meta"]):not([data-mc-slot="actions"]) {
          max-inline-size: 62ch;
          font-size: clamp(1.08rem, 2vw, 1.55rem);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-body > .mc {
          padding: clamp(20px, 3vw, 36px) 0;
          border: 0;
          border-top: 1px solid var(--site-line);
          border-radius: 0;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
          grid-template-columns: minmax(0, 1fr);
          gap: 14px;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > h2 {
          max-inline-size: 16ch;
          font-size: clamp(2rem, 5vw, 4.2rem);
          line-height: 0.95;
          letter-spacing: -0.045em;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > .mc {
          min-block-size: auto;
          padding: clamp(16px, 2vw, 24px) 0 clamp(16px, 2vw, 24px) clamp(18px, 3vw, 32px);
          border: 0;
          border-left: 3px solid var(--site-accent);
          border-radius: 0;
          background: transparent;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] > .mc h3 {
          font-size: clamp(1.1rem, 2vw, 1.6rem);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail > .mc {
          padding: clamp(18px, 3vw, 28px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail form.mc {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr);
        }


        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-runtime-preview,
        body[data-mcel-chrome="chrome-spotlight"] .mcel-runtime-preview,
        body[data-mcel-chrome="chrome-journey"] .mcel-runtime-preview,
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-runtime-preview {
          max-inline-size: min(1180px, calc(100% - 48px));
          padding-block: clamp(22px, 4vw, 54px);
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-runtime-preview > .mc,
        body[data-mcel-chrome="chrome-spotlight"] .mcel-runtime-preview > .mc,
        body[data-mcel-chrome="chrome-journey"] .mcel-runtime-preview > .mc,
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-runtime-preview > .mc {
          display: block;
          min-block-size: auto;
          border: 0;
          border-radius: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
        }

        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-shell {
          display: grid;
          gap: clamp(22px, 4vw, 42px);
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-intro {
          display: grid;
          gap: 12px;
          max-inline-size: 760px;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
          gap: clamp(16px, 2.4vw, 28px);
          align-items: stretch;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid > .mc {
          min-block-size: 100%;
          align-content: start;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid > .mc h2,
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid > .mc h3 {
          font-size: clamp(1.2rem, 2.4vw, 2rem);
          line-height: 1.02;
        }

        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
          display: grid;
          grid-template-columns: minmax(0, 1.25fr) minmax(250px, 0.75fr);
          gap: clamp(22px, 4vw, 52px);
          align-items: start;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary > .mc {
          min-block-size: clamp(360px, 52vw, 680px);
          align-content: center;
          padding: clamp(28px, 6vw, 76px);
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary h1,
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary h2 {
          max-inline-size: 13ch;
          font-size: clamp(3rem, 9vw, 7.2rem);
          line-height: 0.9;
          letter-spacing: -0.065em;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support {
          position: sticky;
          top: 24px;
          display: grid;
          gap: 16px;
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mc {
          padding: clamp(18px, 2.6vw, 30px);
          border-color: var(--site-line);
          background: var(--site-card-soft);
        }

        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-shell {
          display: grid;
          gap: clamp(22px, 4vw, 46px);
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-intro {
          max-inline-size: 760px;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-sequence {
          display: grid;
          gap: clamp(16px, 2.4vw, 28px);
          counter-reset: mcel-journey-step;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step {
          counter-increment: mcel-journey-step;
          display: grid;
          grid-template-columns: auto minmax(0, 1fr);
          gap: clamp(14px, 2vw, 24px);
          align-items: start;
          min-width: 0;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step::before {
          content: attr(data-mcel-step);
          display: grid;
          place-items: center;
          inline-size: clamp(38px, 6vw, 58px);
          block-size: clamp(38px, 6vw, 58px);
          border: 1px solid var(--site-line);
          border-radius: 999px;
          background: var(--site-card-soft);
          color: var(--site-accent);
          font-weight: 900;
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step > .mc {
          min-width: 0;
          border-left: 3px solid var(--site-accent);
          border-radius: var(--site-radius-sm);
        }

        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-shell {
          display: grid;
          gap: clamp(18px, 3vw, 34px);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-intro {
          max-inline-size: 760px;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panels {
          display: grid;
          gap: 12px;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel {
          overflow: clip;
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-summary {
          cursor: pointer;
          padding: clamp(16px, 2.4vw, 24px);
          color: var(--site-ink);
          font-weight: 900;
          list-style-position: inside;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel > .mc {
          margin: 0;
          border: 0;
          border-top: 1px solid var(--site-line);
          border-radius: 0;
          background: transparent;
          box-shadow: none;
        }

        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy],
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy],
        body[data-mcel-chrome="chrome-journey"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy],
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy] {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }

        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy="contain"] > *,
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy="contain"] > *,
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy="contain"] > *,
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy="contain"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button),
        body[data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button),
        body[data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button),
        body[data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy="contain"] :is(img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button) {
          max-inline-size: 100%;
        }

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="control-balance"] :is(input,textarea,select,button) {
          inline-size: 100%;
          max-inline-size: 100%;
          min-inline-size: 0;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="control-balance"] button {
          justify-self: stretch;
          width: 100%;
          white-space: normal;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) :is(form[data-mcel-composition-remedy~="control-balance"], [data-mcel-composition-remedy~="control-balance"] form) {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] {
          container-type: inline-size;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]) {
          max-inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          justify-self: center;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(input,textarea,select,button,a[data-mc-action]) {
          inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          width: calc(100% - clamp(32px, 18cqi, 96px));
          min-inline-size: 0;
        }

        @supports not (width: 1cqi) {
          body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]) {
            max-inline-size: calc(100% - clamp(32px, 12vw, 96px));
          }
          body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="shape-inset-content"] :is(input,textarea,select,button,a[data-mc-action]) {
            inline-size: calc(100% - clamp(32px, 12vw, 96px));
            width: calc(100% - clamp(32px, 12vw, 96px));
          }
        }

        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] [data-mcel-fit-policy="contain"],
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] [data-mcel-fit-policy="contain"],
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] [data-mcel-fit-policy="contain"],
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] [data-mcel-fit-policy="contain"] {
          container-type: inline-size;
          overflow-wrap: anywhere;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(h1,h2),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(h1,h2),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(h1,h2),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(h1,h2) {
          max-inline-size: 100%;
          font-size: clamp(1.35rem, 8cqi, 3rem);
          line-height: 1;
          letter-spacing: -0.045em;
          text-wrap: balance;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component="TrustCluster"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component="TrustCluster"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component="TrustCluster"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component="TrustCluster"]) {
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(label,input,button,a[data-mc-action]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(label,input,button,a[data-mc-action]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(label,input,button,a[data-mc-action]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(label,input,button,a[data-mc-action]) {
          inline-size: 100%;
          justify-self: stretch;
          white-space: normal;
        }

        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid {
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
        }
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid > .mc,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mc,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step > .mc,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel > .mc {
          min-block-size: max-content;
          align-content: start;
        }

        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid,
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support {
          position: static;
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step::before {
          justify-self: start;
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel {
          overflow: visible;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-chrome-generated="true"],
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region],
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy] {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
        }
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] img,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] svg,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] canvas,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] video,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] iframe,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] table,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] pre,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] code,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-policy="contain"] button {
          max-inline-size: 100%;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] button {
          inline-size: 100%;
          max-inline-size: 100%;
          min-inline-size: 0;
          box-sizing: border-box;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] button {
          justify-self: stretch;
          width: 100%;
          white-space: normal;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail form[data-mcel-composition-remedy~="control-balance"],
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="control-balance"] form {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] {
          container-type: inline-size;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h1,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h2,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h3,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] p,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] label,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
          max-inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          justify-self: center;
          box-sizing: border-box;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
          inline-size: calc(100% - clamp(32px, 18cqi, 96px));
          width: calc(100% - clamp(32px, 18cqi, 96px));
          min-inline-size: 0;
        }

        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail form[data-mcel-composition-remedy~="shape-inset-content"],
        body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] form {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          justify-items: center;
        }

        @supports not (width: 1cqi) {
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h1,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h2,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] h3,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] p,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] label,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
            max-inline-size: calc(100% - clamp(32px, 12vw, 96px));
          }

          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] input,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] textarea,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] select,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] button,
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail [data-mcel-composition-remedy~="shape-inset-content"] a[data-mc-action] {
            inline-size: calc(100% - clamp(32px, 12vw, 96px));
            width: calc(100% - clamp(32px, 12vw, 96px));
          }
        }

        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] {
          container-type: inline-size;
          overflow-wrap: anywhere;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] h1,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] h2 {
          max-inline-size: 100%;
          font-size: clamp(1.55rem, 8cqi, 3rem);
          line-height: 0.98;
          letter-spacing: -0.052em;
          text-wrap: balance;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] p {
          max-inline-size: 100%;
          line-height: 1.34;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] form.mc,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] label,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] input,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] button,
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] a[data-mc-action] {
          inline-size: 100%;
          justify-self: stretch;
          white-space: normal;
        }

        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
          grid-template-columns: minmax(0, 1.08fr) minmax(min(360px, 100%), 0.92fr);
        }
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] > .mc {
          min-block-size: max-content;
          align-content: center;
        }

        body[data-mcel-fit-remediation~="object-reshape"][data-mcel-chrome="chrome-editorial-flow"] [data-mcel-fit-region="narrow"] > .mc {
          border-radius: min(var(--site-radius-sm), 18cqi);
          padding: clamp(18px, 5cqi, 32px);
        }

        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
          grid-template-columns: minmax(0, 1fr);
        }
        body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail {
          position: static;
        }

        @media (max-width: 860px) {
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-shell {
            grid-template-columns: 1fr;
          }
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-chrome-editorial-rail {
            position: static;
          }
          body[data-mcel-chrome="chrome-editorial-flow"] .mcel-runtime-preview {
            max-inline-size: min(100% - 28px, 760px);
          }
          body[data-mcel-chrome="chrome-cluster-grid"] .mcel-runtime-preview,
          body[data-mcel-chrome="chrome-spotlight"] .mcel-runtime-preview,
          body[data-mcel-chrome="chrome-journey"] .mcel-runtime-preview,
          body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-runtime-preview {
            max-inline-size: min(100% - 28px, 760px);
          }
          body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
            grid-template-columns: 1fr;
          }
          body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support {
            position: static;
          }
          body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step {
            grid-template-columns: 1fr;
          }
          body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step::before {
            justify-self: start;
          }
          .mcel-runtime-preview .mc[data-mc-kind="hero"],
          .mcel-runtime-preview form.mc,
          .mcel-runtime-preview .mc[data-mc="command-row"],
          .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
            grid-template-columns: 1fr;
          }
          .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
            grid-column: 1;
            grid-row: auto;
            justify-self: stretch;
            max-block-size: 320px;
          }
        }
      `;
    }

    function isolatedSiteDocument(runtimeHtml, meta = {}) {
      const reason = String(meta.reason || "sync").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const nonce = String(meta.nonce || "0").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const hash = String(meta.hash || "none").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const theme = typeof McelLabStyleLaw !== "undefined"
        ? McelLabStyleLaw.normalizeTheme(mcelLabState.theme)
        : (mcelLabState.theme || "theme-machine");
      const chrome = typeof McelLabChromeLaw !== "undefined"
        ? McelLabChromeLaw.normalizeChrome(mcelLabState.chrome)
        : (mcelLabState.chrome || "chrome-strict-hierarchy");
      const chromeResult = typeof McelLabChromeLaw !== "undefined"
        ? McelLabChromeLaw.applyChromeHtml(runtimeHtml, {chrome, theme, reason})
        : {html: runtimeHtml || "", report: {chrome, changed: false, visibleResponse: Boolean(runtimeHtml)}};
      mcelLabState.chrome = chrome;
      mcelLabState.lastChromeReport = chromeResult.report;
      const renderedRuntimeHtml = chromeResult.html || runtimeHtml || "";
      return `<!doctype html>
<html data-mcel-frame-generation="${nonce}" data-mcel-frame-reason="${reason}" data-mcel-frame-hash="${hash}" data-mcel-theme="${theme}" data-mcel-chrome="${chrome}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCEL rendered site</title>
<style>${isolatedSiteCss()}</style>
</head>
<body class="mcel-site-theme ${theme}" data-mcel-chrome="${chrome}">
  <!-- MCEL iframe twiddle: reason=${reason}; nonce=${nonce}; hash=${hash}; theme=${theme}; chrome=${chrome} -->
  <div class="mcel-runtime-preview ${theme}" data-mcel-theme="${theme}" data-mcel-chrome="${chrome}">
    ${renderedRuntimeHtml}
  </div>
</body>
</html>`;
    }

    function syncMcelRenderedSiteFrame(reason = "sync") {
      const frame = bindMcelSiteFrameLifecycle(reason);
      if (!frame || !mcelRuntimePreview) {
        recordMcelSiteFrameTwiddle("iframe-sync-skipped", {reason, hash: "missing", length: 0});
        return;
      }
      const twiddle = ensureMcelSiteFrameTwiddle();
      twiddle.syncCount += 1;
      twiddle.nonce += 1;
      const runtimeHtml = mcelRuntimePreview.innerHTML || "";
      const runtimeHash = hashMcelSiteFrameDocument(runtimeHtml);
      const nonce = `${Date.now()}-${twiddle.nonce}`;
      const documentHtml = isolatedSiteDocument(runtimeHtml, {reason, nonce, hash: runtimeHash});
      const documentHash = hashMcelSiteFrameDocument(documentHtml);
      frame.dataset.synced = reason;
      frame.dataset.srcdocHash = documentHash;
      frame.dataset.runtimeHash = runtimeHash;
      frame.dataset.srcdocLength = String(documentHtml.length);
      frame.dataset.chrome = mcelLabState.chrome || "chrome-strict-hierarchy";
      frame.dataset.lastNonce = nonce;
      twiddle.lastReason = reason;
      twiddle.lastHash = documentHash;
      twiddle.lastLength = documentHtml.length;
      twiddle.lastAt = new Date().toISOString();

      // Twiddle/fix: clear first, then write a nonce-bearing srcdoc. This makes repeated
      // opens observable and prevents browser no-op behavior when the same srcdoc is reused.
      frame.removeAttribute("srcdoc");
      frame.srcdoc = "";
      scheduleMcelSiteFrameWrite(() => {
        const liveFrame = currentMcelSiteFrame();
        if (!liveFrame || liveFrame !== frame || !liveFrame.isConnected) {
          recordMcelSiteFrameTwiddle("iframe-sync-abandoned", {reason, hash: documentHash, length: documentHtml.length});
          return;
        }
        liveFrame.srcdoc = documentHtml;
        liveFrame.dataset.synced = reason;
        liveFrame.dataset.srcdocHash = documentHash;
        liveFrame.dataset.runtimeHash = runtimeHash;
        liveFrame.dataset.srcdocLength = String(documentHtml.length);
        liveFrame.dataset.chrome = mcelLabState.chrome || "chrome-strict-hierarchy";
        liveFrame.dataset.lastNonce = nonce;
        liveFrame.dataset.fitStatus = "pending";
        liveFrame.dataset.fitViolations = "0";
        liveFrame.dataset.fitRemedies = "";
        recordMcelSiteFrameTwiddle("iframe-sync", {reason, hash: documentHash, length: documentHtml.length, fitStatus: "pending", fitViolations: 0});
      });
    }

    function openMcelDiagnosticsDrawer(reason = "diagnostic-request") {
      if (!mcelDiagnosticsDrawer || mcelDiagnosticsDrawer.open) return;
      mcelDiagnosticsDrawer.open = true;
      recordMcelEvent("editor", "MCEL_DIAGNOSTICS_OPENED", `Diagnostics drawer opened by ${reason}.`);
    }

    function renderMcelRuntimeDom() {
      if (!mcelRuntimeDom || !mcelRuntimePreview) return;
      mcelRuntimeDom.textContent = McelLabEngine.formatHtml(mcelRuntimePreview.innerHTML);
    }

    function renderMcelSerializerDiff(serialized = "") {
      if (!mcelSerializerDiff) return;
      const report = mcelLabState.lastSerializerReport || {};
      mcelSerializerDiff.textContent = [
        "SERIALIZER REPORT",
        JSON.stringify(report, null, 2),
        "",
        "SERIALIZED SOURCE",
        serialized || "(not serialized yet)",
        "",
        "ROUND-TRIP STATUS",
        report.serializerClean ? "clean" : "warning"
      ].join("\n");
    }

    function renderMcelCssLawReport() {
      if (!mcelCssLawReport) return;
      mcelCssLawReport.textContent = mcelLabState.lastCssLawReport
        ? JSON.stringify(mcelLabState.lastCssLawReport, null, 2)
        : "CSS law has not been applied yet.";
    }

    function renderMcelLayoutLawReport() {
      if (!mcelLayoutLawReport) return;
      mcelLayoutLawReport.textContent = mcelLabState.lastLayoutLawReport
        ? JSON.stringify(mcelLabState.lastLayoutLawReport, null, 2)
        : "Layout law has not been applied yet.";
    }

    function renderMcelGraphReport() {
      if (!mcelGraphReport || typeof McelLabGraph === "undefined") return;
      mcelLabState.lastGraphReport = McelLabGraph.compactReport(currentMcelSource(), mcelRuntimePreview);
      mcelGraphReport.textContent = JSON.stringify(mcelLabState.lastGraphReport, null, 2);
    }

    function renderMcelAuditReport() {
      if (!mcelAuditReport) return;
      mcelAuditReport.textContent = mcelLabState.lastAuditReport
        ? JSON.stringify(mcelLabState.lastAuditReport, null, 2)
        : "Operational audit has not run yet.";
    }

    function currentMcelReadinessInputs() {
      return {
        serializerReport: mcelLabState.lastSerializerReport,
        cssLawReport: mcelLabState.lastCssLawReport,
        layoutLawReport: mcelLabState.lastLayoutLawReport,
        platformReport: mcelRuntimePreview && typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine.provePlatform(mcelRuntimePreview, {reason: "readiness"}) : null,
        browserProof: mcelLabState.lastBrowserProof,
        a11yReport: mcelRuntimePreview ? McelLabEngine.computeA11y(mcelRuntimePreview) : null,
        auditReport: mcelLabState.lastAuditReport,
        testReport: mcelLabState.lastTestReport,
        matrixReport: mcelLabState.lastMatrixReport,
        acidReport: mcelLabState.lastAcidReport,
        kernelReport: mcelLabState.lastKernelAudit
      };
    }

    function renderMcelScenarioMatrix() {
      if (!mcelMatrixReport) return;
      mcelMatrixReport.textContent = typeof McelLabOpsRunner !== "undefined"
        ? McelLabOpsRunner.summarizeMatrix(mcelLabState.lastMatrixReport)
        : "Scenario matrix runner is unavailable.";
    }

    function renderMcelAcidTests() {
      if (!mcelAcidReport) return;
      if (!mcelLabState.lastAcidReport) {
        mcelAcidReport.textContent = "Acid tests have not run yet.";
        return;
      }
      mcelAcidReport.textContent = McelLabAcidTests.compactText(mcelLabState.lastAcidReport);
    }

    function renderMcelEvidencePacket() {
      if (!mcelEvidenceReport) return;
      mcelEvidenceReport.textContent = typeof McelLabOpsRunner !== "undefined"
        ? McelLabOpsRunner.compactEvidenceText(mcelLabState.lastEvidencePacket)
        : "Evidence packet builder is unavailable.";
    }

    function renderMcelSupervisorReport() {
      if (!mcelSupervisorReport) return;
      mcelSupervisorReport.textContent = typeof McelLabSupervisor !== "undefined"
        ? McelLabSupervisor.compactText(mcelLabState.lastSupervisorReport)
        : "Autopilot supervisor is unavailable.";
    }

    function renderMcelKernelAudit() {
      if (!mcelKernelReport) return;
      mcelKernelReport.textContent = typeof McelLabKernel !== "undefined"
        ? McelLabKernel.compactAuditText(mcelLabState.lastKernelAudit)
        : "Kernel audit is unavailable.";
    }

    function renderMcelTraceabilityMap() {
      if (!mcelTraceabilityReport) return;
      mcelTraceabilityReport.textContent = typeof McelLabKernel !== "undefined"
        ? McelLabKernel.compactTraceabilityText(mcelLabState.lastTraceabilityMap)
        : "Traceability map is unavailable.";
    }

    function renderMcelPriorArtReport() {
      if (!mcelPriorArtReport) return;
      mcelPriorArtReport.textContent = typeof McelLabKernel !== "undefined"
        ? McelLabKernel.priorArtText()
        : "Prior art map is unavailable.";
    }

    function renderMcelSubsumptionLattice() {
      if (!mcelSubsumptionReport) return;
      const lattice = mcelLabState.lastSubsumptionLattice || (typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine.buildSubsumptionLattice() : null);
      mcelSubsumptionReport.textContent = lattice ? JSON.stringify(lattice, null, 2) : "Subsumption lattice is unavailable.";
    }

    function renderMcelWorkbenchPlan() {
      if (!mcelWorkbenchReport) return;
      const plan = mcelLabState.lastWorkbenchPlan || (typeof McelLabWorkbench !== "undefined" ? McelLabWorkbench.buildWorkbenchPlan() : null);
      mcelWorkbenchReport.textContent = plan ? JSON.stringify(plan, null, 2) : "Workbench plan is unavailable.";
    }

    function renderMcelBrowserSemanticProof() {
      if (!mcelBrowserProofReport) return;
      if (!mcelLabState.lastBrowserProof) {
        mcelBrowserProofReport.textContent = "Browser semantic proof has not run yet.";
        return;
      }
      mcelBrowserProofReport.textContent = JSON.stringify(mcelLabState.lastBrowserProof, null, 2);
    }

    function renderMcelReadiness() {
      if (typeof McelLabOpsRunner === "undefined") return;
      const readiness = McelLabOpsRunner.buildReadiness(currentMcelReadinessInputs());
      mcelLabState.lastReadinessReport = readiness;
      if (mcelReadinessScore) {
        mcelReadinessScore.textContent = `Operational readiness: ${readiness.status} · ${readiness.passCount}/${readiness.total} checks · score ${readiness.score}`;
      }
      if (!mcelReadinessCards) return;
      mcelReadinessCards.innerHTML = "";
      readiness.cards.forEach((card) => {
        const item = document.createElement("article");
        item.dataset.status = card.status;
        const title = document.createElement("strong");
        title.textContent = card.label;
        const detail = document.createElement("span");
        detail.textContent = card.detail;
        item.append(title, detail);
        mcelReadinessCards.appendChild(item);
      });
    }

    function renderMcelCommandReport() {
      if (!mcelCommandReport) return;
      if (!mcelLabState.lastCommandPlan) {
        mcelCommandReport.textContent = "No semantic command has been planned yet.";
        return;
      }
      mcelCommandReport.textContent = JSON.stringify(mcelLabState.lastCommandPlan, null, 2);
    }

    function renderMcelProjectReport(result = null) {
      if (!mcelProjectReport || typeof McelLabProjectStore === "undefined") return;
      const payload = result?.snapshot || mcelLabState.lastProjectSnapshot || McelLabProjectStore.snapshot(currentMcelProjectState());
      mcelProjectReport.textContent = JSON.stringify({
        storageKey: McelLabProjectStore.storageKey,
        persisted: Boolean(result?.ok),
        snapshot: payload
      }, null, 2);
    }

    function renderMcelSiteSkeleton() {
      if (!mcelUiSkeletonSummary || !mcelRuntimePreview || typeof McelLabSiteSkeleton === "undefined") return;
      const report = McelLabSiteSkeleton.buildSkeleton(currentMcelSource(), mcelRuntimePreview);
      mcelLabState.lastSiteSkeleton = report;
      const roleOrder = ["hero", "trust cluster", "conversion form", "command row"];
      mcelUiSkeletonSummary.innerHTML = "";
      roleOrder.forEach((role) => {
        const matching = report.sections.find((section) => section.role === role);
        const item = document.createElement("article");
        item.dataset.status = matching ? "pass" : "pending";
        const title = document.createElement("strong");
        title.textContent = role;
        const detail = document.createElement("span");
        detail.textContent = matching
          ? `${matching.label} · ${matching.policy.scroll} scroll`
          : "not present in current source";
        item.append(title, detail);
        mcelUiSkeletonSummary.appendChild(item);
      });
      if (mcelUiSkeletonHealth) {
        mcelUiSkeletonHealth.dataset.status = report.layoutHealth.status;
        mcelUiSkeletonHealth.textContent = [
          `Layout health: ${report.layoutHealth.status}`,
          `illegal nested scrollbars: ${report.layoutHealth.nestedScrollbarCount}`,
          `self-owned scroll regions: ${report.layoutHealth.selfScrollCount}`,
          report.layoutHealth.claim
        ].join(" · ");
      }
    }

    function renderMcelA11yReport() {
      if (!mcelA11yReport || !mcelRuntimePreview) return;
      mcelA11yReport.textContent = JSON.stringify(McelLabEngine.computeA11y(mcelRuntimePreview), null, 2);
    }

    function selectedRuntimeElement() {
      return mcelRuntimePreview?.querySelector?.(`[${McelLabContract.attributes.sourceIndex}="${mcelLabState.selectedIndex}"]`) ||
        mcelRuntimePreview?.querySelector?.(`[${McelLabContract.attributes.type}]`);
    }

    function renderMcelDebugger() {
      if (!mcelDebuggerOutput || !mcelRuntimePreview) return;
      mcelDebuggerOutput.textContent = JSON.stringify(McelLabEngine.debuggerStateFor(selectedRuntimeElement(), mcelRuntimePreview), null, 2);
    }

    function renderMcelContractTests() {
      if (!mcelTestReport) return;
      if (!mcelLabState.lastTestReport) {
        mcelTestReport.textContent = "Contract tests have not run yet.";
        return;
      }
      const report = mcelLabState.lastTestReport;
      mcelTestReport.textContent = [
        `MCEL FULL CONTRACT SUITE: ${report.passed} passed / ${report.failed} failed`,
        report.generatedAt ? `generatedAt: ${report.generatedAt}` : "",
        "",
        ...report.tests.map((test) => `${test.passed ? "PASS" : "FAIL"} [${test.group || "contract"}] ${test.name}${test.details ? ` — ${test.details}` : ""}`)
      ].join("\n").trim();
    }

    function renderMcelCompilerLog() {
      if (!mcelCompilerLog) return;
      mcelCompilerLog.innerHTML = "";
      mcelLabState.compileEvents.slice(-32).forEach((event) => {
        const item = document.createElement("li");
        item.dataset.level = event.level;
        item.textContent = `[${event.module}] ${event.code}: ${event.message}`;
        mcelCompilerLog.appendChild(item);
      });
    }
