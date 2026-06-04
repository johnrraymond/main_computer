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
      mcelOpenSmartCssModal?.addEventListener("click", () => openMcelLabModal("smart-css"));
      mcelSmartCssRerun?.addEventListener("click", () => renderMcelSmartCssPrimitiveLab("manual-rerun"));
      mcelSiteFrameResync?.addEventListener("click", () => syncMcelRenderedSiteFrame("twiddle-resync"));
      mcelSiteFrameRebuild?.addEventListener("click", () => rebuildMcelSiteFrameShell("twiddle-rebuild", {syncAfter: true}));
      mcelSiteFrameClear?.addEventListener("click", () => clearMcelSiteFrameSrcdoc("twiddle-clear"));
      mcelCanonicalAppMount?.addEventListener("click", () => mountMcelCanonicalAppSpecimen("manual-mount"));
      mcelCanonicalAppRefresh?.addEventListener("click", () => refreshMcelCanonicalAppSpecimen("manual-refresh"));
      mcelCanonicalAppInspect?.addEventListener("click", () => inspectMcelCanonicalAppSpecimen("manual-inspect"));
      mcelCanonicalAppEnrich?.addEventListener("click", () => applyMcelCanonicalTaskManagerEnrichment("manual-enrich"));
      mcelCanonicalAppProof?.addEventListener("click", () => runMcelCanonicalAppSpecimenProof("manual-proof"));
      mcelCanonicalAppLens?.addEventListener("click", () => applyMcelCanonicalTaskManagerLens("manual-lens"));
      mcelCanonicalAppClean?.addEventListener("click", () => clearMcelCanonicalTaskManagerLens("manual-clean"));
      mcelCanonicalAppSelect?.addEventListener("change", () => {
        renderMcelCanonicalAppLensMap(null, "specimen-select");
        renderMcelCanonicalAppSpecimenStatus("specimen-select");
      });
      bindMcelSiteFrameLifecycle("boot");
      renderMcelSiteFrameTwiddle("boot");
      bindMcelCanonicalAppSpecimenLifecycle("boot");
      renderMcelCanonicalAppSpecimenStatus("boot");
      document.querySelectorAll("[data-mcel-close-modal]").forEach((button) => {
        button.addEventListener("click", () => closeMcelLabModal(button.dataset.mcelCloseModal || "all"));
      });
      [mcelEditorModal, mcelSiteModal, mcelSmartCssModal].filter(Boolean).forEach((modal) => {
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
        ".mcel-chrome-compact-panel",
        "[data-mcel-chrome-frame]",
        "[data-mcel-chrome-region-role]"
      ].join(", ");
    }

    function mcelSafeAttributeValue(value) {
      return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    }

    function findMcelCompositionRemedyTarget(doc, warning = {}) {
      const sourceIndex = String(warning.sourceIndex || "");
      const chromePart = String(warning.chromePart || "");
      const scope = mcelChromeCompositionScopeSelector();
      const wantsGeneratedContainer = warning?.problem === "container-distorted-by-extreme-aspect-ratio" ||
        warning?.problem === "shape-containment-failed" ||
        warning?.remedy === "dedistort-container-shape" ||
        warning?.remedy === "smart-content-envelope";

      if (wantsGeneratedContainer && chromePart) {
        const selector = `[data-mcel-chrome-part="${mcelSafeAttributeValue(chromePart)}"]`;
        const generatedTargets = [...(doc?.querySelectorAll?.(selector) || [])];
        const sourceSelector = sourceIndex
          ? `[data-mc-source-index="${mcelSafeAttributeValue(sourceIndex)}"]`
          : "";
        const generatedContainerWithSource = sourceSelector
          ? generatedTargets.find((element) => element?.querySelector?.(sourceSelector))
          : null;
        if (generatedContainerWithSource) return generatedContainerWithSource;
        const generatedContainer = generatedTargets.find((element) => element?.getAttribute?.("data-mcel-chrome-generated") === "true");
        if (generatedContainer) return generatedContainer;
        if (generatedTargets.length) return generatedTargets[0];
      }

      if (sourceIndex) {
        const selector = `${scope} [data-mc-source-index="${mcelSafeAttributeValue(sourceIndex)}"]`;
        const candidates = [...(doc?.querySelectorAll?.(selector) || [])];
        const direct = candidates.find((element) => element.matches?.(".mc, [data-mc]"));
        if (direct) return direct;
        const nested = candidates.map((element) => element.closest?.(".mc, [data-mc]")).find(Boolean);
        if (nested) return nested;
        if (candidates.length) return candidates[0];
      }

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
            : (warning?.problem === "content-fit-failed"
              ? "smart-flow-frame"
              : (warning?.problem === "shape-containment-failed"
                ? "smart-content-envelope"
                : (warning?.problem === "shape-interior-escape"
                  ? "shape-inset-content"
                  : (warning?.problem === "text-distorted-by-narrow-inline-size"
                    ? "dedistort-inline-content"
                    : (warning?.problem === "container-distorted-by-extreme-aspect-ratio" ? "dedistort-container-shape" : ""))))));
        if (!remedy) return;
        const target = findMcelCompositionRemedyTarget(doc, warning);
        if (!target) return;
        const existing = new Set(String(target.getAttribute("data-mcel-composition-remedy") || "").split(/\s+/).filter(Boolean));
        const beforeRemedyCount = existing.size;
        String(remedy).split(/\s+/).filter(Boolean).forEach((token) => existing.add(token));
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

    function getMcelSmartCssPrimitiveCases() {
      return [
        {
          id: "unbounded-pill-frame",
          title: "Unbounded CSS pill used as a content-bearing frame",
          rawPrimitive: "border-radius: 999px on a generated frame that contains a card stack",
          smartPrimitive: "big-rounded support-frame object with explicit content region and growth contract",
          proof: "shape-containment",
          rawClass: "mcel-smart-css-raw-pill",
          smartClass: "mcel-smart-css-smart-frame",
          expectedRawFailure: "shape-containment-failed"
        },
        {
          id: "fixed-clip-box",
          title: "Fixed overflow clip box pretending to be a layout primitive",
          rawPrimitive: "fixed block-size plus overflow: clip around variable children",
          smartPrimitive: "flow-frame object that derives block-size from accepted children",
          proof: "content-fit",
          rawClass: "mcel-smart-css-raw-clip",
          smartClass: "mcel-smart-css-smart-flow",
          expectedRawFailure: "content-fit-failed"
        },
        {
          id: "overlay-paint-layer",
          title: "Decorative paint layer order around semantic content",
          rawPrimitive: "same decorative paint token, but raw stacking places it above semantic content",
          smartPrimitive: "same decorative paint token, but paint envelope is behind semantic content and inert to hit testing",
          proof: "paint-layer-order",
          rawClass: "mcel-smart-css-raw-overlay",
          smartClass: "mcel-smart-css-smart-paint",
          expectedRawFailure: "paint-layer-overlay-failed"
        }
      ];
    }

    function createMcelSmartCssCard(title, copy) {
      const card = document.createElement("article");
      card.className = "mcel-smart-css-card";
      card.innerHTML = `<strong>${title}</strong><span>${copy}</span>`;
      return card;
    }

    function createMcelSmartCssPrimitiveStage(spec, side) {
      const isRaw = side === "raw";
      const stage = document.createElement("section");
      stage.className = `mcel-smart-css-stage ${isRaw ? spec.rawClass : spec.smartClass}`;
      stage.dataset.mcelSmartCssSide = side;
      stage.dataset.mcelSmartCssProof = spec.proof;

      const heading = document.createElement("header");
      heading.className = "mcel-smart-css-stage-head";
      heading.innerHTML = [
        `<span>${isRaw ? "Raw CSS backend" : "MCEL smart primitive"}</span>`,
        `<strong>${isRaw ? spec.rawPrimitive : spec.smartPrimitive}</strong>`
      ].join("");

      const object = document.createElement("div");
      object.className = "mcel-smart-css-object";
      object.dataset.mcelSmartCssObject = isRaw ? "raw" : "smart";

      const layer = document.createElement("div");
      layer.className = "mcel-smart-css-paint-layer";
      layer.setAttribute("aria-hidden", "true");
      object.appendChild(layer);

      const content = document.createElement("div");
      content.className = "mcel-smart-css-content";
      content.appendChild(createMcelSmartCssCard("Fresh daily", "Card stack child one"));
      content.appendChild(createMcelSmartCssCard("Pickup + delivery", "Card stack child two"));
      content.appendChild(createMcelSmartCssCard("Proof visible", "Card stack child three"));

      if (spec.id === "fixed-clip-box") {
        content.appendChild(createMcelSmartCssCard("Fourth child", "Variable content that raw CSS clips"));
      }

      object.appendChild(content);
      const verdict = document.createElement("output");
      verdict.className = "mcel-smart-css-verdict";
      verdict.setAttribute("aria-live", "polite");
      verdict.textContent = "not run";

      stage.append(heading, object, verdict);
      return stage;
    }

    function renderMcelSmartCssPrimitiveCase(spec) {
      const article = document.createElement("article");
      article.className = "mcel-smart-css-case";
      article.dataset.mcelSmartCssCase = spec.id;
      article.innerHTML = `
        <header class="mcel-smart-css-case-head">
          <div>
            <p class="eyebrow">Primitive replacement test</p>
            <h5>${spec.title}</h5>
          </div>
          <code>${spec.proof}</code>
        </header>
      `;

      const comparison = document.createElement("div");
      comparison.className = "mcel-smart-css-comparison";
      comparison.append(
        createMcelSmartCssPrimitiveStage(spec, "raw"),
        createMcelSmartCssPrimitiveStage(spec, "smart")
      );

      article.appendChild(comparison);
      return article;
    }

    function mcelSmartCssPx(value) {
      const parsed = Number.parseFloat(value);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function mcelSmartCssRound(value, places = 1) {
      const factor = 10 ** places;
      return Math.round(value * factor) / factor;
    }

    function getMcelSmartCssUsedRadius(element) {
      const styles = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const raw = Math.max(
        mcelSmartCssPx(styles.borderTopLeftRadius),
        mcelSmartCssPx(styles.borderTopRightRadius),
        mcelSmartCssPx(styles.borderBottomRightRadius),
        mcelSmartCssPx(styles.borderBottomLeftRadius)
      );
      return {
        raw,
        used: Math.min(raw, rect.width / 2, rect.height / 2),
        css: styles.borderRadius
      };
    }

    function getMcelSmartCssSafeInterval(parent, y) {
      const rect = parent.getBoundingClientRect();
      const radius = getMcelSmartCssUsedRadius(parent).used;
      let left = rect.left;
      let right = rect.right;

      if (radius > 0 && y < rect.top + radius) {
        const centerY = rect.top + radius;
        const dy = Math.abs(y - centerY);
        if (dy < radius) {
          const dx = Math.sqrt(Math.max(0, radius * radius - dy * dy));
          const inset = radius - dx;
          left = Math.max(left, rect.left + inset);
          right = Math.min(right, rect.right - inset);
        }
      } else if (radius > 0 && y > rect.bottom - radius) {
        const centerY = rect.bottom - radius;
        const dy = Math.abs(y - centerY);
        if (dy < radius) {
          const dx = Math.sqrt(Math.max(0, radius * radius - dy * dy));
          const inset = radius - dx;
          left = Math.max(left, rect.left + inset);
          right = Math.min(right, rect.right - inset);
        }
      }

      return {left, right, width: Math.max(0, right - left)};
    }

    function analyzeMcelSmartCssShapeContainment(stage) {
      const object = stage.querySelector(".mcel-smart-css-object");
      const cards = Array.from(stage.querySelectorAll(".mcel-smart-css-card"));
      const radius = getMcelSmartCssUsedRadius(object);
      const failures = cards.map((card, index) => {
        const rect = card.getBoundingClientRect();
        const samples = [
          rect.top + 1,
          rect.top + rect.height * 0.25,
          rect.top + rect.height * 0.5,
          rect.top + rect.height * 0.75,
          rect.bottom - 1
        ].map((y) => {
          const safe = getMcelSmartCssSafeInterval(object, y);
          const leftEscape = Math.max(0, safe.left - rect.left);
          const rightEscape = Math.max(0, rect.right - safe.right);
          return {
            y: mcelSmartCssRound(y),
            safeWidth: mcelSmartCssRound(safe.width),
            leftEscape: mcelSmartCssRound(leftEscape),
            rightEscape: mcelSmartCssRound(rightEscape),
            worstEscape: mcelSmartCssRound(Math.max(leftEscape, rightEscape))
          };
        });
        const worstEscape = Math.max(...samples.map((sample) => sample.worstEscape));
        return {
          index,
          failed: worstEscape > 2,
          worstEscapePx: mcelSmartCssRound(worstEscape),
          samples
        };
      }).filter((failure) => failure.failed);

      return {
        failed: failures.length > 0,
        failure: failures.length ? "shape-containment-failed" : null,
        detail: {
          rawRadius: mcelSmartCssRound(radius.raw),
          usedRadius: mcelSmartCssRound(radius.used),
          collisionCount: failures.length,
          failures
        }
      };
    }

    function analyzeMcelSmartCssContentFit(stage) {
      const object = stage.querySelector(".mcel-smart-css-object");
      const cards = Array.from(stage.querySelectorAll(".mcel-smart-css-card"));
      const objectRect = object.getBoundingClientRect();
      const styles = window.getComputedStyle(object);
      const clips = ["clip", "hidden", "scroll", "auto"].includes(styles.overflow) || ["clip", "hidden", "scroll", "auto"].includes(styles.overflowY);
      const union = cards.reduce((bounds, card) => {
        const rect = card.getBoundingClientRect();
        return {
          left: Math.min(bounds.left, rect.left),
          top: Math.min(bounds.top, rect.top),
          right: Math.max(bounds.right, rect.right),
          bottom: Math.max(bounds.bottom, rect.bottom)
        };
      }, {left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity});
      const bottomEscape = Math.max(0, union.bottom - objectRect.bottom);
      const rightEscape = Math.max(0, union.right - objectRect.right);
      const leftEscape = Math.max(0, objectRect.left - union.left);
      const topEscape = Math.max(0, objectRect.top - union.top);
      const worstEscape = Math.max(bottomEscape, rightEscape, leftEscape, topEscape);
      const failed = clips && worstEscape > 2;

      return {
        failed,
        failure: failed ? "content-fit-failed" : null,
        detail: {
          clips,
          objectHeight: mcelSmartCssRound(objectRect.height),
          contentHeight: mcelSmartCssRound(union.bottom - union.top),
          worstEscapePx: mcelSmartCssRound(worstEscape)
        }
      };
    }

    function mcelSmartCssStackOrder(styles) {
      const parsed = Number.parseInt(styles.zIndex, 10);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function mcelSmartCssRectsOverlap(a, b) {
      return a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom;
    }

    function analyzeMcelSmartCssPaintLayerOrder(stage) {
      const layer = stage.querySelector(".mcel-smart-css-paint-layer");
      const content = stage.querySelector(".mcel-smart-css-content");
      const cards = Array.from(stage.querySelectorAll(".mcel-smart-css-card"));
      if (!layer || !content || !cards.length) {
        return {
          failed: true,
          failure: "paint-layer-overlay-failed",
          detail: {reason: "missing paint-layer, content layer, or cards"}
        };
      }

      const layerStyles = window.getComputedStyle(layer);
      const contentStyles = window.getComputedStyle(content);
      const layerRect = layer.getBoundingClientRect();
      const layerZ = mcelSmartCssStackOrder(layerStyles);
      const contentZ = mcelSmartCssStackOrder(contentStyles);
      const pointerEvents = layerStyles.pointerEvents;
      const paintCanReceiveHits = pointerEvents !== "none";
      const paintStacksAboveContent = layerZ >= contentZ;
      const foregroundPaint = paintCanReceiveHits || paintStacksAboveContent;

      const hits = cards.map((card, index) => {
        const rect = card.getBoundingClientRect();
        const overlapsPaint = mcelSmartCssRectsOverlap(layerRect, rect);
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const inViewport = x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight;
        const stack = inViewport && document.elementsFromPoint ? document.elementsFromPoint(x, y) : [];
        const blockedByPaintLayer = stack.some((hit) => hit === layer || layer.contains(hit));
        const hitContent = stack.some((hit) => hit === card || card.contains(hit) || hit === content || content.contains(hit));
        return {
          index,
          overlapsPaint,
          blockedByPaintLayer,
          hitContent,
          hitTested: inViewport
        };
      });

      const foregroundOverlapCount = hits.filter((hit) => hit.overlapsPaint && foregroundPaint).length;
      const blockedHitCount = hits.filter((hit) => hit.blockedByPaintLayer).length;
      const failed = foregroundOverlapCount > 0 || blockedHitCount > 0;

      return {
        failed,
        failure: failed ? "paint-layer-overlay-failed" : null,
        detail: {
          paintLayerZ: layerZ,
          contentLayerZ: contentZ,
          paintLayerPointerEvents: pointerEvents,
          foregroundOverlapCount,
          blockedHitCount,
          hitTestedCount: hits.filter((hit) => hit.hitTested).length,
          hits
        }
      };
    }

    function analyzeMcelSmartCssPrimitiveStage(stage, spec) {
      if (spec.proof === "shape-containment") return analyzeMcelSmartCssShapeContainment(stage);
      if (spec.proof === "content-fit") return analyzeMcelSmartCssContentFit(stage);
      if (spec.proof === "paint-layer-order") return analyzeMcelSmartCssPaintLayerOrder(stage);
      return {failed: true, failure: "unknown-proof", detail: {proof: spec.proof}};
    }

    function summarizeMcelSmartCssVerdictDetail(analysis) {
      const detail = analysis.detail || {};
      const pieces = [];
      if (Number.isFinite(detail.collisionCount)) pieces.push(`${detail.collisionCount} child collision(s)`);
      if (Number.isFinite(detail.worstEscapePx)) pieces.push(`worst escape ${detail.worstEscapePx}px`);
      if (Number.isFinite(detail.rawRadius)) pieces.push(`raw radius ${detail.rawRadius}px`);
      if (Number.isFinite(detail.usedRadius)) pieces.push(`used radius ${detail.usedRadius}px`);
      if (Number.isFinite(detail.objectHeight)) pieces.push(`object ${detail.objectHeight}px`);
      if (Number.isFinite(detail.contentHeight)) pieces.push(`content ${detail.contentHeight}px`);
      if (Number.isFinite(detail.foregroundOverlapCount)) pieces.push(`${detail.foregroundOverlapCount} foreground overlap(s)`);
      if (Number.isFinite(detail.blockedHitCount)) pieces.push(`${detail.blockedHitCount} blocked hit(s)`);
      if (Number.isFinite(detail.paintLayerZ)) pieces.push(`paint z=${detail.paintLayerZ}`);
      if (Number.isFinite(detail.contentLayerZ)) pieces.push(`content z=${detail.contentLayerZ}`);
      if (detail.paintLayerPointerEvents) pieces.push(`pointer-events=${detail.paintLayerPointerEvents}`);
      return pieces.length ? `; ${pieces.join("; ")}` : "";
    }

    function updateMcelSmartCssVerdict(stage, analysis, expectedFailure) {
      const side = stage.dataset.mcelSmartCssSide || "raw";
      const verdict = stage.querySelector(".mcel-smart-css-verdict");
      const contractPassed = side === "raw" ? analysis.failed && analysis.failure === expectedFailure : !analysis.failed;
      const detail = summarizeMcelSmartCssVerdictDetail(analysis);
      stage.dataset.mcelSmartCssStatus = contractPassed ? "passed" : "failed";
      stage.dataset.mcelSmartCssDetectedFailure = analysis.failure || "none";
      if (verdict) {
        if (side === "raw") {
          verdict.textContent = contractPassed
            ? `expected backend hazard detected: ${analysis.failure}${detail}`
            : `unexpected raw backend result: ${analysis.failure || "no failure"}${detail}`;
        } else {
          verdict.textContent = contractPassed
            ? `golden-path smart primitive proof passed${detail}`
            : `golden-path smart primitive failed: ${analysis.failure}${detail}`;
        }
      }
      return contractPassed;
    }

    function runMcelSmartCssPrimitiveProofs() {
      const cases = getMcelSmartCssPrimitiveCases();
      const results = cases.map((spec) => {
        const caseEl = mcelSmartCssSuite?.querySelector(`[data-mcel-smart-css-case="${spec.id}"]`);
        const rawStage = caseEl?.querySelector('[data-mcel-smart-css-side="raw"]');
        const smartStage = caseEl?.querySelector('[data-mcel-smart-css-side="smart"]');
        const rawAnalysis = rawStage ? analyzeMcelSmartCssPrimitiveStage(rawStage, spec) : {failed: true, failure: "missing-raw-stage", detail: {}};
        const smartAnalysis = smartStage ? analyzeMcelSmartCssPrimitiveStage(smartStage, spec) : {failed: true, failure: "missing-smart-stage", detail: {}};
        const rawContractPassed = rawStage ? updateMcelSmartCssVerdict(rawStage, rawAnalysis, spec.expectedRawFailure) : false;
        const smartContractPassed = smartStage ? updateMcelSmartCssVerdict(smartStage, smartAnalysis, spec.expectedRawFailure) : false;
        const passed = rawContractPassed && smartContractPassed;

        if (caseEl) caseEl.dataset.mcelSmartCssStatus = passed ? "passed" : "failed";

        return {
          id: spec.id,
          title: spec.title,
          proof: spec.proof,
          expectedRawFailure: spec.expectedRawFailure,
          passed,
          raw: rawAnalysis,
          smart: smartAnalysis
        };
      });
      const report = {
        status: results.every((result) => result.passed) ? "passed" : "failed",
        premise: "CSS/HTML are treated as backend output; MCEL-generated golden-path surfaces must use smart primitives that prove object contracts before raw CSS is emitted.",
        caseCount: results.length,
        passedCount: results.filter((result) => result.passed).length,
        failedCount: results.filter((result) => !result.passed).length,
        results
      };
      mcelLabState.lastSmartCssPrimitiveReport = report;
      if (mcelSmartCssReport) mcelSmartCssReport.textContent = JSON.stringify(report, null, 2);
      recordMcelEvent(
        "smart-css",
        report.status === "passed" ? "MCEL_SMART_CSS_PRIMITIVES_PROVED" : "MCEL_SMART_CSS_PRIMITIVES_FAILED",
        `Smart CSS primitive suite ${report.status}: ${report.passedCount}/${report.caseCount} primitive replacement proofs passed.`,
        report.status === "passed" ? "info" : "warning"
      );
      return report;
    }

    function renderMcelSmartCssPrimitiveLab(reason = "open-smart-css-modal") {
      if (!mcelSmartCssSuite) return null;
      mcelSmartCssSuite.innerHTML = "";
      getMcelSmartCssPrimitiveCases().forEach((spec) => {
        mcelSmartCssSuite.appendChild(renderMcelSmartCssPrimitiveCase(spec));
      });
      window.requestAnimationFrame(() => runMcelSmartCssPrimitiveProofs());
      recordMcelEvent("smart-css", "MCEL_SMART_CSS_PRIMITIVE_LAB_RENDERED", `Smart CSS primitive lab rendered for ${reason}.`);
      return true;
    }


    function openMcelLabModal(which = "site") {
      const modals = {
        editor: mcelEditorModal,
        site: mcelSiteModal,
        "smart-css": mcelSmartCssModal
      };
      const target = modals[which] || mcelSiteModal;
      const active = modals[which] ? which : "site";
      if (!target) return;
      closeMcelLabModal("all", {silent: true});
      target.setAttribute("aria-hidden", "false");
      target.dataset.open = "true";
      mcelLabState.activeModal = active;
      document.body?.classList?.add("mcel-modal-open");
      if (active === "site") {
        const twiddle = ensureMcelSiteFrameTwiddle();
        twiddle.openCount += 1;
        bindMcelSiteFrameLifecycle("open-site-modal");
        syncMcelRenderedSiteFrame("open-site-modal");
        recordMcelSiteFrameTwiddle("modal-open", {reason: "open-site-modal"});
      } else if (active === "editor") {
        syncMcelGrapesFromSource();
      } else if (active === "smart-css") {
        renderMcelSmartCssPrimitiveLab("open-smart-css-modal");
      }
      recordMcelEvent("ui", "MCEL_MODAL_OPENED", `${mcelLabState.activeModal} modal opened as isolated product surface.`);
    }

    function closeMcelLabModal(which = "all", options = {}) {
      const wasSiteClose = which === "site" || which === "all" || mcelLabState.activeModal === "site";
      const targets = [];
      if (which === "editor" || which === "all") targets.push(mcelEditorModal);
      if (which === "site" || which === "all") targets.push(mcelSiteModal);
      if (which === "smart-css" || which === "all") targets.push(mcelSmartCssModal);
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
          display: inline-flex;
          align-items: center;
          justify-content: center;
          justify-self: start;
          max-inline-size: 100%;
          min-height: 42px;
          border: 0;
          border-radius: 999px;
          background: var(--site-action);
          color: var(--site-action-ink);
          padding: 12px 20px;
          box-sizing: border-box;
          font-weight: 950;
          line-height: 1;
          text-decoration: none;
          vertical-align: top;
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
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-item {
          min-block-size: 100%;
          align-content: start;
          --mcel-chrome-frame-gap: 0px;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-body > .mc {
          min-block-size: 100%;
          align-content: start;
        }
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-body > .mc h2,
        body[data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-body > .mc h3 {
          font-size: clamp(1.2rem, 2.4vw, 2rem);
          line-height: 1.02;
        }

        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell {
          display: grid;
          grid-template-columns: minmax(0, 1.25fr) minmax(250px, 0.75fr);
          gap: clamp(22px, 4vw, 52px);
          align-items: start;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary > .mcel-chrome-spotlight-item {
          min-block-size: clamp(360px, 52vw, 680px);
          align-content: center;
          padding: clamp(28px, 6vw, 76px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-primary .mcel-chrome-spotlight-body > .mc {
          border: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
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
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item {
          padding: clamp(18px, 2.6vw, 30px);
          border: 1px solid var(--site-line);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] {
          --mcel-smart-envelope-block-pad: clamp(72px, 11vw, 160px);
          --mcel-smart-envelope-inline-pad: clamp(32px, 6vw, 84px);
          position: relative;
          display: grid;
          align-content: center;
          min-block-size: max-content;
          padding: var(--mcel-smart-envelope-block-pad) var(--mcel-smart-envelope-inline-pad);
          border-radius: 999px;
          overflow: visible;
          isolation: isolate;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] > [data-mcel-chrome-region-role="body"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] > [data-mcel-chrome-region-role="body"] {
          position: relative;
          z-index: 1;
          display: grid;
          align-content: center;
          min-inline-size: 0;
          max-inline-size: 100%;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] [data-mc="feed"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] [data-mc="feed"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] [data-mc-component-kind="layout"],
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] [data-mc-component-kind="layout"] {
          display: grid;
          gap: clamp(16px, 2.4vw, 28px);
          min-inline-size: 0;
          max-inline-size: 100%;
          margin-inline: auto;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(.mc-panel, [data-mc="panel"]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(.mc-panel, [data-mc="panel"]) {
          max-inline-size: 100%;
          margin-inline: 0;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]) {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
          align-content: start;
          gap: clamp(12px, 2vw, 18px);
          min-block-size: max-content;
          max-block-size: none;
          overflow: visible !important;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]) :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="island"], .mc[data-mc-component-kind="primitive"]) :is(h1,h2,h3,p,label,input,textarea,select,button,a[data-mc-action]) {
          max-inline-size: 100%;
          min-inline-size: 0;
          overflow: visible;
          box-sizing: border-box;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-chrome-primitive="content-envelope"] :is(form.mc, .mc[data-mc="command-row"]) :is(input,textarea,select,button,a[data-mc-action]),
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item[data-mcel-composition-remedy~="smart-content-envelope"] :is(form.mc, .mc[data-mc="command-row"]) :is(input,textarea,select,button,a[data-mc-action]) {
          inline-size: 100%;
          width: 100%;
          justify-self: stretch;
        }
        body[data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support .mcel-chrome-spotlight-body > .mc {
          border: 0;
          background: transparent;
          box-shadow: none;
          padding: 0;
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
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step > .mcel-chrome-journey-body {
          min-width: 0;
          border-left: 3px solid var(--site-accent);
          border-radius: var(--site-radius-sm);
          background: var(--site-card-soft);
          box-shadow: var(--site-shadow);
          overflow: clip;
          padding: clamp(30px, 5vw, 72px);
        }
        body[data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-content > .mc {
          margin: 0;
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
          padding: clamp(28px, 5vw, 70px) clamp(38px, 7vw, 112px) clamp(34px, 5.5vw, 78px);
          --mcel-chrome-frame-gap: clamp(18px, 3vw, 38px);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-summary {
          cursor: pointer;
          padding: 0;
          color: var(--site-ink);
          font-weight: 900;
          list-style-position: inside;
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-body {
          border-top: 1px solid var(--site-line);
          padding-block-start: clamp(18px, 3vw, 34px);
        }
        body[data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-body > .mc {
          margin: 0;
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

        body[data-mcel-chrome] [data-mcel-chrome-frame] {
          display: grid;
          grid-template-rows: auto minmax(0, auto);
          row-gap: var(--mcel-chrome-frame-gap, clamp(14px, 2.4vw, 28px));
          align-items: start;
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
          isolation: isolate;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role] {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
          position: relative;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role="header"] {
          grid-row: 1;
          z-index: 2;
          justify-self: center;
          text-align: center;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role="body"] {
          grid-row: 2;
          z-index: 1;
          display: grid;
          min-inline-size: 0;
          align-items: stretch;
        }
        body[data-mcel-chrome] [data-mcel-chrome-frame] > [data-mcel-chrome-region-role="body"]:first-child {
          grid-row: 1 / -1;
        }
        body[data-mcel-chrome] [data-mcel-chrome-region-role="body"] > * {
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

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-container-shape"] {
          border-radius: min(var(--site-radius), 28px) !important;
          min-block-size: max-content !important;
          aspect-ratio: auto !important;
          align-content: start;
          overflow: visible;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-container-shape"] > [data-mcel-chrome-region-role="body"] {
          align-items: stretch;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-container-shape"] :is(.mc,[data-mc]) {
          border-radius: min(var(--site-radius), 22px);
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-content-envelope"] {
          --mcel-smart-envelope-block-pad: clamp(72px, 11vw, 160px);
          --mcel-smart-envelope-inline-pad: clamp(32px, 6vw, 84px);
          position: relative;
          display: grid;
          align-content: center;
          min-block-size: max-content;
          padding: var(--mcel-smart-envelope-block-pad) var(--mcel-smart-envelope-inline-pad) !important;
          border-radius: 999px;
          overflow: visible;
          isolation: isolate;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-content-envelope"] > [data-mcel-chrome-region-role="body"] {
          position: relative;
          z-index: 1;
          display: grid;
          align-content: center;
          min-inline-size: 0;
          max-inline-size: 100%;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-flow-frame"] {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
          align-content: start;
          min-inline-size: 0;
          max-inline-size: 100%;
          min-block-size: max-content !important;
          block-size: auto !important;
          max-block-size: none !important;
          overflow: visible !important;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-flow-frame"] > * {
          min-inline-size: 0;
          max-inline-size: 100%;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="smart-flow-frame"] :is(h1,h2,h3,h4,h5,h6,p,label,input,textarea,select,button,a[data-mc-action]) {
          max-inline-size: 100%;
          min-inline-size: 0;
          overflow: visible;
          box-sizing: border-box;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) :is(form[data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] form, .mc[data-mc="command-row"][data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] .mc[data-mc="command-row"]) {
          grid-template-columns: minmax(0, 1fr);
          align-items: stretch;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) :is(form[data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] form, .mc[data-mc="command-row"][data-mcel-composition-remedy~="smart-flow-frame"], [data-mcel-composition-remedy~="smart-flow-frame"] .mc[data-mc="command-row"]) :is(input,textarea,select,button,a[data-mc-action]) {
          inline-size: 100%;
          width: 100%;
          justify-self: stretch;
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
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-journey"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]),
        body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-compact-disclosure"] :is(form.mc, .mc[data-mc="command-row"], .mc[data-mc-component-kind="layout"]) {
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

        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-inline-content"] :is(p[data-mc-slot="actions"], [data-mc-slot="actions"]) {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          align-items: center;
        }
        body:not([data-mcel-chrome="chrome-strict-hierarchy"]) [data-mcel-composition-remedy~="dedistort-inline-content"] :is(button,a[data-mc-action],[role="button"]) {
          writing-mode: horizontal-tb;
          text-orientation: mixed;
          white-space: nowrap;
          word-break: normal;
          overflow-wrap: normal;
          inline-size: max-content;
          width: max-content;
          max-inline-size: none;
          min-inline-size: max-content;
          justify-self: start;
          align-self: center;
        }

        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid {
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
        }
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-cluster-grid"] .mcel-chrome-cluster-grid > .mcel-chrome-cluster-item,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-support > .mcel-chrome-spotlight-item,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-journey"] .mcel-chrome-journey-step > .mcel-chrome-journey-body,
        body[data-mcel-fit-remediation~="object-grow"][data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel {
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
          grid-template-rows: auto minmax(0, auto);
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
      const chrome = typeof MCEL !== "undefined" && MCEL.normalizeChrome
        ? MCEL.normalizeChrome(mcelLabState.chrome)
        : (mcelLabState.chrome || "chrome-strict-hierarchy");
      const chromeResult = typeof MCEL !== "undefined" && MCEL.applyChrome
        ? MCEL.applyChrome(runtimeHtml, {chrome, theme, reason})
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

    const MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS = [
      "task-manager-app",
      "task-manager-status",
      "task-manager-server",
      "task-query",
      "task-limit",
      "task-refresh",
      "task-process-table",
      "task-all-process-table",
      "task-connection-table",
      "task-hardware-table",
      "task-ai-output"
    ];

    const MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_IDS = [
      "task-server-shutdown",
      "task-server-start",
      "task-server-restart",
      "task-schedule-create"
    ];

    const MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_SELECTORS = [
      "#task-server-shutdown",
      "#task-server-start",
      "#task-server-restart",
      "#task-schedule-create",
      "[data-task-action=\"terminate-pid\"]",
      "[data-task-action=\"kill-pid\"]"
    ];

    const MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID = "mcel-lab-canonical-specimen-style";
    const MCEL_CANONICAL_SPECIMEN_RIBBON_ID = "mcel-lab-canonical-specimen-ribbon";

    const MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID = "mcel-lab-canonical-task-manager-lens-style";
    const MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID = "mcel-lab-canonical-task-manager-lens-hud";
    const MCEL_CANONICAL_TASK_MANAGER_LENS_CLASS = "mcel-canonical-task-manager-lens";
    const MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID = "mcel-lab-canonical-task-manager-enrichment-style";
    const MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_CLASS = "mcel-canonical-task-manager-enriched";

    const MCEL_CANONICAL_TASK_MANAGER_REGION_ENRICHMENT = [
      {selector: "#task-manager-app", role: "operator-console", kind: "app", layout: "sidebar-workspace", fitContext: "root"},
      {selector: ".task-manager-shell", role: "sidebar-workspace-shell", kind: "layout", layout: "sidebar-workspace", fitContext: "structural"},
      {selector: ".task-manager-sidebar", role: "command-status-rail", kind: "region", region: "sidebar", fitContext: "constrained", widthPolicy: "compact-fixed"},
      {selector: ".task-manager-detail", role: "primary-workspace", kind: "region", region: "workspace", fitContext: "expansive", widthPolicy: "fluid"}
    ];

    const MCEL_CANONICAL_TASK_MANAGER_COMPONENT_ENRICHMENT = [
      {selector: ".task-overview-card", role: "status-summary-card", kind: "card", fit: "wrap-text-no-scroll-trap"},
      {selector: "#task-manager-status", role: "status-line", kind: "text", fit: "wrap-status"},
      {selector: "#task-manager-server", role: "server-snapshot-text", kind: "text", fit: "wrap-no-scroll-trap"},
      {selector: ".task-controls-card", role: "command-control-card", kind: "card", fit: "compact-control-card"},
      {selector: ".task-controls-card .task-inline-grid", role: "control-group", kind: "form", fit: "compact-controls", layoutPolicy: "primary-full-row-secondary-checkbox-grid"},
      {selector: ".task-inline-check", role: "checkbox-control", kind: "control", fit: "fixed-input-shrink-label"},
      {selector: ".task-schedule-card", role: "deferred-command-form", kind: "form", fit: "compact-form"},
      {selector: ".task-schedule-card label", role: "field-row", kind: "form-field", fit: "compact-field-row"},
      {selector: ".task-schedule-list", role: "schedule-feed", kind: "feed", fit: "bounded-list"},
      {selector: ".task-notebook", role: "tabbed-data-feed", kind: "feed", fit: "expansive-scroll"},
      {selector: ".task-tab-button", role: "feed-tab", kind: "navigation", fit: "wrap-tabs"},
      {selector: ".task-grid-scroll", role: "data-grid-scrollport", kind: "scrollport", fit: "intentional-scroll"},
      {selector: ".task-table", role: "data-feed-table", kind: "table", fit: "expansive-table"},
      {selector: ".task-table td code", role: "command-preview", kind: "text-preview", fit: "single-line-ellipsis"},
      {selector: ".task-row-actions", role: "action-cell", kind: "actions", fit: "compact-action-cell"},
      {selector: ".task-ai-toolbar", role: "ai-prompt-toolbar", kind: "ai", fit: "prompt-action-row"},
      {selector: "#task-ai-output", role: "ai-analysis-output", kind: "ai", fit: "wrap-intentional-scroll"}
    ];

    const MCEL_CANONICAL_TASK_MANAGER_FIELD_ENRICHMENT = [
      {control: "#task-query", role: "process-filter", priority: "primary"},
      {control: "#task-limit", role: "row-limit", priority: "primary"},
      {control: "#task-include-connections", role: "include-connections", priority: "secondary"},
      {control: "#task-auto-refresh", role: "auto-refresh", priority: "secondary"},
      {control: "#task-schedule-action", role: "scheduled-action", priority: "primary"},
      {control: "#task-schedule-when", role: "scheduled-time", priority: "primary"},
      {control: "#task-schedule-note", role: "schedule-note", priority: "primary"},
      {control: "#task-ai-prompt", role: "ai-prompt", priority: "primary"}
    ];

    const MCEL_CANONICAL_TASK_MANAGER_PANEL_LENS = [
      {selector: ".task-overview-card", role: "overview", label: "MCEL: overview", kind: "state"},
      {selector: ".task-controls-card", role: "audited-command-zone", label: "MCEL: audited command zone", kind: "actions"},
      {selector: ".task-schedule-card", role: "scheduler", label: "MCEL: scheduler", kind: "mutation"},
      {selector: ".task-notebook", role: "live-data-notebook", label: "MCEL: live data notebook", kind: "feed"},
      {selector: "#task-panel-processes", role: "server-process-feed", label: "MCEL: server process feed", kind: "feed"},
      {selector: "#task-panel-all-processes", role: "all-process-feed", label: "MCEL: all process feed", kind: "feed"},
      {selector: "#task-panel-connections", role: "connection-feed", label: "MCEL: connection feed", kind: "feed"},
      {selector: "#task-panel-hardware", role: "hardware-feed", label: "MCEL: hardware feed", kind: "feed"},
      {selector: ".task-ai-toolbar", role: "ai-command-surface", label: "MCEL: AI command surface", kind: "ai"},
      {selector: "[data-widget-label=\"Task AI Brief\"]", role: "ai-brief", label: "MCEL: AI operations brief", kind: "ai"}
    ];

    const MCEL_CANONICAL_TASK_MANAGER_ACTION_LENS = [
      {selector: "#task-refresh", risk: "safe", role: "refresh-query", label: "safe refresh"},
      {selector: "#task-server-shutdown", risk: "destructive", role: "server-shutdown", label: "destructive server command"},
      {selector: "#task-server-start", risk: "operational", role: "server-start", label: "operational server command"},
      {selector: "#task-server-restart", risk: "disruptive", role: "server-restart", label: "disruptive server command"},
      {selector: "#task-schedule-create", risk: "deferred-mutation", role: "schedule-create", label: "scheduled operation"},
      {selector: "#task-schedules-refresh", risk: "safe", role: "schedule-refresh", label: "safe schedule refresh"},
      {selector: "#task-ai-analyze", risk: "analysis", role: "ai-analysis", label: "AI analysis request"},
      {selector: "[data-task-action=\"terminate-pid\"]", risk: "process-destructive", role: "terminate-pid", label: "process termination"},
      {selector: "[data-task-action=\"kill-pid\"]", risk: "process-destructive", role: "kill-pid", label: "process kill"}
    ];

    function ensureMcelCanonicalAppSpecimenState() {
      if (!mcelLabState.canonicalAppSpecimen) {
        mcelLabState.canonicalAppSpecimen = {
          mountCount: 0,
          loadCount: 0,
          errorCount: 0,
          inspectCount: 0,
          proofCount: 0,
          specimenChromeCount: 0,
          app: "task-manager",
          route: "/applications/task-manager/server-processes?mcel_lab_specimen=task-manager",
          rootSelector: "#task-manager-app",
          status: "idle",
          lastAt: null
        };
      }
      mcelLabState.canonicalAppSpecimen.lensCount = mcelLabState.canonicalAppSpecimen.lensCount || 0;
      mcelLabState.canonicalAppSpecimen.lensStatus = mcelLabState.canonicalAppSpecimen.lensStatus || "idle";
      mcelLabState.canonicalAppSpecimen.enrichmentCount = mcelLabState.canonicalAppSpecimen.enrichmentCount || 0;
      mcelLabState.canonicalAppSpecimen.enrichmentStatus = mcelLabState.canonicalAppSpecimen.enrichmentStatus || "idle";
      return mcelLabState.canonicalAppSpecimen;
    }

    function selectedMcelCanonicalAppSpecimen() {
      const option = mcelCanonicalAppSelect?.selectedOptions?.[0] || mcelCanonicalAppSelect?.querySelector?.("option");
      const frame = mcelCanonicalAppFrame;
      return {
        app: option?.value || frame?.dataset?.mcelSpecimenApp || "task-manager",
        route: option?.dataset?.route || frame?.dataset?.mcelSpecimenRoute || "/applications/task-manager/server-processes?mcel_lab_specimen=task-manager",
        rootSelector: option?.dataset?.root || frame?.dataset?.mcelSpecimenRoot || "#task-manager-app",
        label: option?.textContent?.trim() || "Task Manager"
      };
    }

    function renderMcelCanonicalAppSpecimenStatus(reason = "render") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const report = mcelLabState.lastCanonicalSpecimenReport;
      const proof = mcelLabState.lastCanonicalSpecimenProof;
      const status = [
        `specimen=${state.app || "task-manager"}`,
        `status=${state.status || "idle"}`,
        `mounts=${state.mountCount || 0}`,
        `loads=${state.loadCount || 0}`,
        `inspections=${state.inspectCount || 0}`,
        `proofs=${state.proofCount || 0}`,
        `chrome=${state.specimenChromeCount || 0}`,
        `enrichment=${state.enrichmentStatus || "idle"}`,
        `enrichmentRuns=${state.enrichmentCount || 0}`,
        `lens=${state.lensStatus || "idle"}`,
        `lensRuns=${state.lensCount || 0}`,
        report ? `root=${report.rootPresent ? "present" : "missing"}` : "root=unknown",
        proof ? `browserProof=${proof.failed ? "warning" : "ready"}` : "browserProof=not-run",
        `reason=${reason}`
      ].join(" · ");
      if (mcelCanonicalAppStatus) mcelCanonicalAppStatus.textContent = status;
      if (mcelCanonicalAppFrameShell) {
        mcelCanonicalAppFrameShell.dataset.mcelSpecimenFrameStatus = state.status || "idle";
      }
      if (mcelCanonicalAppFrameSummary) {
        const mounted = state.status && state.status !== "idle";
        const rootSummary = report ? `root ${report.rootPresent ? "present" : "missing"}` : "root not inspected";
        const proofSummary = proof ? `proof ${proof.failed ? "warning" : "ready"}` : "proof pending";
        const enrichment = mcelLabState.lastCanonicalSpecimenEnrichment;
        const enrichmentSummary = enrichment ? `enriched: ${enrichment.enrichedElementCount} element(s), ${enrichment.layoutLawStatus}` : "enrichment pending";
        const lens = mcelLabState.lastCanonicalSpecimenLens;
        const lensSummary = lens ? `lens active: ${lens.classifiedPanelCount} panel(s), ${lens.riskControlCount} risk surface(s)` : "lens pending";
        mcelCanonicalAppFrameSummary.textContent = mounted
          ? `${state.app || "task-manager"} specimen ${state.status}; ${rootSummary}; ${proofSummary}; ${enrichmentSummary}; ${lensSummary}.`
          : "Task Manager has not been mounted yet.";
      }
      if (mcelCanonicalAppReport && !mcelLabState.lastCanonicalSpecimenReport) {
        mcelCanonicalAppReport.textContent = "Mount Task Manager to enrich it as a canonical MCEL specimen.";
      }
      return status;
    }

    function injectMcelCanonicalAppSpecimenChrome(reason = "specimen-chrome") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) return false;
      const root = doc.querySelector?.(specimen.rootSelector) || null;
      doc.documentElement?.setAttribute?.("data-mcel-lab-specimen", specimen.app);
      doc.body.setAttribute("data-mcel-lab-specimen", specimen.app);
      doc.body.setAttribute("data-mcel-lab-specimen-reason", reason);
      let style = doc.getElementById(MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID);
      if (!style) {
        style = doc.createElement("style");
        style.id = MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID;
        style.textContent = `
          [data-mcel-lab-specimen-root="true"] {
            outline: 1px solid rgba(115, 214, 255, 0.42);
            outline-offset: -3px;
          }
          [data-mcel-lab-specimen-root="true"]:focus-within {
            outline-color: rgba(115, 214, 255, 0.76);
          }
        `;
        doc.head?.appendChild?.(style);
      }

      // Earlier versions inserted a fixed in-frame ribbon. That crowded legacy app chrome,
      // so this inspector twiddle deliberately removes any stale ribbon and keeps the visible
      // MCEL status in the Lab sidecar/frame bar instead.
      doc.getElementById(MCEL_CANONICAL_SPECIMEN_RIBBON_ID)?.remove?.();

      if (root) {
        root.setAttribute("data-mcel-lab-specimen-root", "true");
        root.setAttribute("data-mcel-lab-specimen-app", specimen.app);
      }
      state.specimenChromeCount = (state.specimenChromeCount || 0) + 1;
      return true;
    }

    function ensureMcelCanonicalTaskManagerEnrichmentStyle(doc) {
      if (!doc?.head) return false;
      let style = doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID);
      if (style) return true;
      style = doc.createElement("style");
      style.id = MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID;
      style.textContent = `
        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-role],
        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-region],
        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit] {
          box-sizing: border-box !important;
          min-width: 0 !important;
          max-width: 100%;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-layout-region="sidebar-workspace-shell"] {
          display: grid !important;
          grid-template-columns: var(--mcel-task-sidebar-width, 300px) minmax(0, 1fr) !important;
          gap: 8px !important;
          align-items: stretch !important;
          overflow: hidden !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-region="command-status-rail"] {
          width: var(--mcel-task-sidebar-width, 300px) !important;
          max-width: var(--mcel-task-sidebar-width, 300px) !important;
          display: flex !important;
          flex-direction: column !important;
          gap: 8px !important;
          overflow: auto !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-region="primary-workspace"] {
          min-width: 0 !important;
          display: grid !important;
          grid-template-rows: minmax(0, 1fr) auto minmax(120px, 18vh) !important;
          gap: 8px !important;
          overflow: hidden !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="wrap-no-scroll-trap"] {
          max-height: none !important;
          height: auto !important;
          overflow: visible !important;
          white-space: pre-wrap !important;
          overflow-wrap: anywhere !important;
          word-break: break-word !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-controls"] {
          display: grid !important;
          grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
          gap: 7px !important;
          align-items: stretch !important;
          width: 100% !important;
          min-width: 0 !important;
          max-width: 100% !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-control-priority="primary"] {
          grid-column: 1 / -1 !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="fixed-input-shrink-label"] {
          display: grid !important;
          grid-template-columns: 16px minmax(0, 1fr) !important;
          align-items: center !important;
          gap: 6px !important;
          width: 100% !important;
          min-width: 0 !important;
          max-width: 100% !important;
          overflow: hidden !important;
          white-space: nowrap !important;
          text-overflow: ellipsis !important;
          line-height: 1.1 !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="fixed-input-shrink-label"] input[type="checkbox"] {
          width: 16px !important;
          height: 16px !important;
          min-width: 16px !important;
          max-width: 16px !important;
          margin: 0 !important;
          justify-self: start !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] {
          display: grid !important;
          grid-template-columns: minmax(76px, 0.35fr) minmax(0, 1fr) !important;
          gap: 8px !important;
          align-items: center !important;
          width: 100% !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] input,
        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] select,
        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] textarea {
          width: 100% !important;
          min-width: 0 !important;
          max-width: 100% !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="single-line-ellipsis"] {
          display: block !important;
          width: 100% !important;
          max-width: 100% !important;
          min-width: 0 !important;
          max-height: none !important;
          overflow: hidden !important;
          text-overflow: ellipsis !important;
          white-space: nowrap !important;
          word-break: normal !important;
          overflow-wrap: normal !important;
          line-height: 1.25 !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-action-cell"] {
          min-width: 0 !important;
          max-width: 100% !important;
          width: 100% !important;
          grid-template-columns: minmax(0, 1fr) !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-action-cell"] button,
        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="compact-action-cell"] .task-pill {
          width: 100% !important;
          min-width: 0 !important;
          max-width: 100% !important;
          white-space: normal !important;
        }

        body[data-mcel-lab-specimen="task-manager"][data-mcel-task-enrichment="active"] [data-mcel-fit="prompt-action-row"] {
          min-width: 0 !important;
        }
      `;
      doc.head.appendChild(style);
      return true;
    }

    function applyMcelElementEnrichment(element, definition) {
      if (!element || !definition) return false;
      element.setAttribute("data-mcel-role", definition.role);
      element.setAttribute("data-mcel-kind", definition.kind || "surface");
      if (definition.fit) element.setAttribute("data-mcel-fit", definition.fit);
      if (definition.fitContext) element.setAttribute("data-mcel-fit-context", definition.fitContext);
      if (definition.layout) element.setAttribute("data-mcel-layout", definition.layout);
      if (definition.layoutPolicy) element.setAttribute("data-mcel-layout-policy", definition.layoutPolicy);
      if (definition.region) element.setAttribute("data-mcel-region", definition.role);
      if (definition.widthPolicy) element.setAttribute("data-mcel-width-policy", definition.widthPolicy);
      element.setAttribute("data-mcel-enriched", "task-manager-lab");
      element.setAttribute("data-mcel-enrichment-source", "legacy-dom-reader");
      return true;
    }

    function mcelNearestControlLabel(control) {
      if (!control) return null;
      return control.closest?.("label") || control.parentElement;
    }

    function buildMcelCanonicalTaskManagerEnrichmentModel(doc, root, reason = "build-enrichment") {
      const regions = MCEL_CANONICAL_TASK_MANAGER_REGION_ENRICHMENT.map((definition) => {
        const element = doc.querySelector?.(definition.selector) || null;
        return {
          selector: definition.selector,
          role: definition.role,
          kind: definition.kind,
          region: definition.region || definition.role,
          fitContext: definition.fitContext || "",
          widthPolicy: definition.widthPolicy || "",
          layout: definition.layout || "",
          present: Boolean(element),
          inferredFrom: definition.selector === ".task-manager-sidebar"
            ? ["dom-class", "first-shell-child", "constrained-width", "control/status/form-descendants"]
            : definition.selector === ".task-manager-detail"
              ? ["dom-class", "second-shell-child", "expansive-data-workspace-descendants"]
              : ["dom-contract-selector"]
        };
      });

      const components = MCEL_CANONICAL_TASK_MANAGER_COMPONENT_ENRICHMENT.map((definition) => {
        const elements = Array.from(doc.querySelectorAll?.(definition.selector) || []);
        return {
          selector: definition.selector,
          role: definition.role,
          kind: definition.kind,
          fit: definition.fit || "",
          layoutPolicy: definition.layoutPolicy || "",
          count: elements.length,
          present: elements.length > 0
        };
      });

      const fields = MCEL_CANONICAL_TASK_MANAGER_FIELD_ENRICHMENT.map((definition) => {
        const control = doc.querySelector?.(definition.control) || null;
        return {
          selector: definition.control,
          role: definition.role,
          priority: definition.priority,
          controlTag: control?.tagName?.toLowerCase?.() || "",
          present: Boolean(control)
        };
      });

      const actions = MCEL_CANONICAL_TASK_MANAGER_ACTION_LENS.map((definition) => {
        const count = Array.from(doc.querySelectorAll?.(definition.selector) || []).length;
        return {
          selector: definition.selector,
          role: definition.role,
          risk: definition.risk,
          label: definition.label,
          count,
          present: count > 0
        };
      });

      return {
        app: "task-manager",
        kind: "operator-console",
        layout: "sidebar-workspace",
        rootSelector: "#task-manager-app",
        rootPresent: Boolean(root),
        regions,
        components,
        fields,
        actions,
        generatedBy: "mcel-lab-legacy-dom-enrichment",
        reason,
        builtAt: new Date().toISOString(),
        laws: [
          "structural containers preserve app geometry",
          "constrained regions use compact leaf-control fit policies",
          "checkbox controls reserve a fixed input slot and shrinkable label slot",
          "status text avoids accidental internal scroll traps",
          "command previews clip intentionally on one line",
          "destructive action surfaces are classified but never executed"
        ]
      };
    }

    function collectMcelCanonicalTaskManagerEnrichmentViolations(doc, root) {
      if (!doc || !root) return [{law: "root-present", status: "failed", message: "Task Manager root unavailable"}];
      const violations = [];
      const constrainedRegion = root.querySelector?.("[data-mcel-region=\"command-status-rail\"]");
      const constrainedWidth = constrainedRegion?.getBoundingClientRect?.().width || 0;

      Array.from(root.querySelectorAll?.("[data-mcel-fit], [data-mcel-role]") || []).forEach((element) => {
        const rect = element.getBoundingClientRect?.();
        const parentRect = element.parentElement?.getBoundingClientRect?.();
        if (!rect || rect.width <= 0 || rect.height <= 0) return;
        const fit = element.getAttribute("data-mcel-fit") || "";
        const role = element.getAttribute("data-mcel-role") || "";
        const intentionalOverflow = ["intentional-scroll", "expansive-scroll", "expansive-table", "single-line-ellipsis", "wrap-intentional-scroll"].includes(fit);
        if (!intentionalOverflow && element.scrollWidth - element.clientWidth > 1) {
          violations.push({
            law: "horizontal-containment",
            role,
            fit,
            id: element.id || "",
            selector: element.getAttribute("data-mcel-enrichment-selector") || "",
            delta: Math.ceil(element.scrollWidth - element.clientWidth)
          });
        }
        if (parentRect && rect.right > parentRect.right + 1 && !intentionalOverflow) {
          violations.push({
            law: "parent-boundary",
            role,
            fit,
            id: element.id || "",
            selector: element.getAttribute("data-mcel-enrichment-selector") || "",
            delta: Math.ceil(rect.right - parentRect.right)
          });
        }
      });

      Array.from(root.querySelectorAll?.("[data-mcel-fit=\"fixed-input-shrink-label\"]") || []).forEach((element) => {
        const checkbox = element.querySelector?.("input[type=\"checkbox\"]");
        const rect = element.getBoundingClientRect?.();
        if (!checkbox) {
          violations.push({law: "checkbox-slot", role: "checkbox-control", status: "failed", message: "missing checkbox input"});
        }
        if (constrainedWidth && rect?.width && rect.width > constrainedWidth) {
          violations.push({law: "checkbox-containment", role: "checkbox-control", status: "failed", width: Math.ceil(rect.width), constrainedWidth: Math.ceil(constrainedWidth)});
        }
      });

      return violations.slice(0, 32);
    }

    function applyMcelCanonicalTaskManagerEnrichment(reason = "enrichment") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) {
        const unavailable = {
          app: specimen.app,
          rootSelector: specimen.rootSelector,
          enrichmentActive: false,
          rootPresent: false,
          enrichedElementCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: "iframe-document", status: "failed", message: "iframe document unavailable"}],
          destructiveActionsExecuted: false,
          safetyClaim: "enrichment reads and annotates the specimen DOM; it never clicks Task Manager controls",
          reason,
          appliedAt: new Date().toISOString()
        };
        state.enrichmentStatus = "unavailable";
        mcelLabState.lastCanonicalSpecimenEnrichment = unavailable;
        renderMcelCanonicalAppLensMap(unavailable, reason);
        renderMcelCanonicalAppSpecimenStatus(reason);
        return unavailable;
      }

      injectMcelCanonicalAppSpecimenChrome(reason);
      ensureMcelCanonicalTaskManagerEnrichmentStyle(doc);
      const root = doc.querySelector?.(specimen.rootSelector) || null;
      doc.documentElement?.setAttribute?.("data-mcel-task-enrichment", "active");
      doc.body.setAttribute("data-mcel-task-enrichment", "active");
      doc.body.classList.add(MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_CLASS);
      doc.body.style.setProperty("--mcel-task-sidebar-width", "300px");

      let enrichedElementCount = 0;
      if (root) {
        root.setAttribute("data-mcel-app", "task-manager");
        root.setAttribute("data-mcel-kind", "operator-console");
        root.setAttribute("data-mcel-layout", "sidebar-workspace");
        root.setAttribute("data-mcel-enriched", "task-manager-lab");
        root.setAttribute("data-mcel-enrichment-source", "legacy-dom-reader");
        root.setAttribute("data-mcel-enrichment-state", "active");
        root.setAttribute("data-mcel-proof-surface", "canonical-app-specimen");
        root.setAttribute("data-mcel-component-id", "canonical.task-manager.root");
        enrichedElementCount += 1;
      }

      MCEL_CANONICAL_TASK_MANAGER_REGION_ENRICHMENT.forEach((definition) => {
        Array.from(doc.querySelectorAll?.(definition.selector) || []).forEach((element) => {
          applyMcelElementEnrichment(element, definition);
          element.setAttribute("data-mcel-enrichment-selector", definition.selector);
          if (definition.kind === "layout") {
            element.setAttribute("data-mcel-layout-region", definition.role);
          }
          if (definition.region) {
            element.setAttribute("data-mcel-region-kind", definition.region);
            element.setAttribute("data-mcel-region", definition.role);
          }
          enrichedElementCount += 1;
        });
      });

      MCEL_CANONICAL_TASK_MANAGER_COMPONENT_ENRICHMENT.forEach((definition) => {
        Array.from(doc.querySelectorAll?.(definition.selector) || []).forEach((element) => {
          applyMcelElementEnrichment(element, definition);
          element.setAttribute("data-mcel-enrichment-selector", definition.selector);
          enrichedElementCount += 1;
        });
      });

      MCEL_CANONICAL_TASK_MANAGER_FIELD_ENRICHMENT.forEach((definition) => {
        const control = doc.querySelector?.(definition.control) || null;
        if (!control) return;
        control.setAttribute("data-mcel-control-role", definition.role);
        control.setAttribute("data-mcel-control-priority", definition.priority);
        control.setAttribute("data-mcel-enrichment-selector", definition.control);
        const label = mcelNearestControlLabel(control);
        if (label) {
          label.setAttribute("data-mcel-role", "field-control");
          label.setAttribute("data-mcel-kind", "control");
          label.setAttribute("data-mcel-control-role", definition.role);
          label.setAttribute("data-mcel-control-priority", definition.priority);
          label.setAttribute("data-mcel-enriched", "task-manager-lab");
        }
        enrichedElementCount += label ? 2 : 1;
      });

      MCEL_CANONICAL_TASK_MANAGER_ACTION_LENS.forEach((action) => {
        Array.from(doc.querySelectorAll?.(action.selector) || []).forEach((element) => {
          element.setAttribute("data-mcel-role", "action-surface");
          element.setAttribute("data-mcel-action-role", action.role);
          element.setAttribute("data-mcel-action-risk", action.risk);
          element.setAttribute("data-mcel-action-label", action.label);
          element.setAttribute("data-mcel-mutates", action.risk === "safe" || action.risk === "analysis" ? "false" : "potential");
          element.setAttribute("data-mcel-enriched", "task-manager-lab");
          element.setAttribute("data-mcel-enrichment-selector", action.selector);
          enrichedElementCount += 1;
        });
      });

      const model = buildMcelCanonicalTaskManagerEnrichmentModel(doc, root, reason);
      const violations = collectMcelCanonicalTaskManagerEnrichmentViolations(doc, root);
      const report = {
        ...model,
        route: mcelCanonicalAppFrame?.dataset?.mcelSpecimenRoute || specimen.route,
        enrichmentActive: Boolean(root),
        enrichedElementCount,
        regionCount: model.regions.filter((item) => item.present).length,
        componentCount: model.components.reduce((total, item) => total + item.count, 0),
        fieldCount: model.fields.filter((item) => item.present).length,
        actionControlCount: model.actions.reduce((total, item) => total + item.count, 0),
        riskControlCount: model.actions.filter((item) => item.present && !["safe", "analysis"].includes(item.risk)).reduce((total, item) => total + item.count, 0),
        fitLawCount: model.components.filter((item) => item.fit).length,
        layoutLawStatus: violations.length ? "warning" : "ready",
        violations,
        enrichmentStyleId: MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID,
        overlayMode: "semantic enrichment with role/fit attributes; layout repair comes from MCEL fit policies, not text-specific selectors",
        destructiveActionsExecuted: false,
        safetyClaim: "MCEL enrichment reads, annotates, and applies role-based fit policies; it does not click server control, PID termination, or schedule actions",
        appliedAt: new Date().toISOString()
      };

      state.enrichmentCount = (state.enrichmentCount || 0) + 1;
      state.enrichmentStatus = report.enrichmentActive ? report.layoutLawStatus : "warning";
      state.lastAt = report.appliedAt;
      mcelLabState.lastCanonicalSpecimenEnrichment = report;
      renderMcelCanonicalAppLensMap(report, reason);
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = JSON.stringify({enrichment: report}, null, 2);
      }
      recordMcelEvent(
        "canonical-app",
        report.enrichmentActive ? "MCEL_CANONICAL_TASK_MANAGER_ENRICHED" : "MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_WARNING",
        report.enrichmentActive
          ? `Task Manager enrichment mapped ${report.regionCount} region(s), ${report.componentCount} component(s), ${report.fieldCount} field(s), and ${report.riskControlCount} risk surface(s).`
          : `Task Manager enrichment could not find ${specimen.rootSelector}.`,
        report.enrichmentActive ? (violations.length ? "warning" : "success") : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return report;
    }

    function ensureMcelCanonicalTaskManagerLensStyle(doc) {
      if (!doc?.head) return false;
      let style = doc.getElementById(MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID);
      if (style) return true;
      style = doc.createElement("style");
      style.id = MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID;
      style.textContent = `
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] [data-mcel-lens-role] {
          position: relative;
        }
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] [data-mcel-lens-role]::before {
          content: "";
          position: absolute;
          inset: 4px;
          z-index: 2;
          border: 1px solid rgba(115, 214, 255, 0.0);
          border-radius: inherit;
          opacity: 0;
          pointer-events: none;
          transition: opacity 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
        }
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] [data-mcel-lens-role]:hover::before,
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] [data-mcel-lens-role]:focus-within::before {
          border-color: rgba(115, 214, 255, 0.5);
          box-shadow: inset 0 0 0 1px rgba(115, 214, 255, 0.08);
          opacity: 1;
        }
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] button[data-mcel-action-risk]:focus-visible {
          outline: 2px solid rgba(115, 214, 255, 0.74);
          outline-offset: 2px;
        }
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] .mcel-lens-label,
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] .mcel-lens-hud,
        body[data-mcel-lab-specimen="task-manager"][data-mcel-canonical-lens="active"] .mcel-lens-risk-badge {
          display: none !important;
        }
      `;
      doc.head.appendChild(style);
      return true;
    }

    function ensureMcelCanonicalTaskManagerLensLabel(doc, element, label, kind = "surface") {
      if (!doc || !element) return false;
      element.setAttribute("data-mcel-lens-label", label);
      element.setAttribute("data-mcel-lens-kind", kind);
      const staleBadge = element.querySelector?.(":scope > .mcel-lens-label");
      if (staleBadge?.dataset?.mcelLensGenerated === "true") {
        staleBadge.remove();
      }
      return true;
    }

    function renderMcelCanonicalTaskManagerLensHud(doc, root, report) {
      if (!doc?.body || !root || !report) return false;
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID)?.remove?.();
      root.setAttribute("data-mcel-lens-sidecar", "active");
      root.setAttribute(
        "aria-description",
        `MCEL Lab sidecar classified ${report.classifiedPanelCount} panels and ${report.riskControlCount} risk surfaces without modifying Task Manager controls.`
      );
      return true;
    }

    function renderMcelCanonicalAppLensMap(report, reason = "render-lens-map") {
      if (!mcelCanonicalAppLensMap) return false;
      mcelCanonicalAppLensMap.replaceChildren();
      const heading = document.createElement("div");
      heading.className = "mcel-canonical-app-lens-map-heading";
      const title = document.createElement("strong");
      title.textContent = report?.enrichmentActive ? "Task Manager enrichment map" : "Task Manager specimen map";
      const meta = document.createElement("span");
      meta.textContent = report
        ? report.enrichmentActive
          ? `enrichment ${report.layoutLawStatus || "active"} · ${report.reason || reason}`
          : `inspector ${report.lensActive ? "active" : "inactive"} · ${report.reason || reason}`
        : "enrichment not applied yet";
      heading.append(title, meta);
      mcelCanonicalAppLensMap.appendChild(heading);

      const items = report ? report.enrichmentActive ? [
        ["Root", report.rootPresent ? "present" : "missing"],
        ["Regions", `${report.regionCount || 0}/${MCEL_CANONICAL_TASK_MANAGER_REGION_ENRICHMENT.length}`],
        ["Components", `${report.componentCount || 0} enriched`],
        ["Fields", `${report.fieldCount || 0} enriched`],
        ["Actions", `${report.actionControlCount || 0} classified`],
        ["Fit laws", `${report.fitLawCount || 0} declared`],
        ["Violations", `${report.violations?.length || 0}`],
        ["Safety", report.destructiveActionsExecuted ? "mutation executed" : "no destructive clicks"]
      ] : [
        ["Root", report.rootPresent ? "present" : "missing"],
        ["Panels", `${report.classifiedPanelCount || 0}/${report.panelCount || 0}`],
        ["Feeds", String(report.feedCount || 0)],
        ["Actions", `${report.actionControlCount || 0} classified`],
        ["Risk", `${report.riskControlCount || 0} audited in sidecar`],
        ["Overlay", report.overlayMode || "subtle hover/focus only"],
        ["Safety", report.destructiveActionsExecuted ? "mutation executed" : "no destructive clicks"]
      ] : [
        ["Root", "unknown"],
        ["Regions", "not enriched"],
        ["Components", "not enriched"],
        ["Fields", "not enriched"],
        ["Actions", "not classified"],
        ["Fit laws", "not declared"],
        ["Safety", "observational"]
      ];

      const grid = document.createElement("div");
      grid.className = "mcel-canonical-app-lens-map-grid";
      items.forEach(([label, value]) => {
        const card = document.createElement("div");
        card.className = "mcel-canonical-app-lens-map-card";
        const k = document.createElement("span");
        k.textContent = label;
        const v = document.createElement("strong");
        v.textContent = value;
        card.append(k, v);
        grid.appendChild(card);
      });
      mcelCanonicalAppLensMap.appendChild(grid);

      if (report?.regions?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Enriched regions:";
        details.appendChild(label);
        report.regions.filter((region) => region.present).forEach((region) => {
          const chip = document.createElement("span");
          chip.textContent = `${region.role}: ${region.fitContext || region.layout || "semantic"}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }

      if (report?.violations?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Fit proof warnings:";
        details.appendChild(label);
        report.violations.slice(0, 8).forEach((violation) => {
          const chip = document.createElement("span");
          chip.textContent = `${violation.law}: ${violation.role || violation.id || violation.selector || "surface"}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      } else if (report?.riskControls?.length) {
        const details = document.createElement("div");
        details.className = "mcel-canonical-app-lens-sidecar-list";
        const label = document.createElement("strong");
        label.textContent = "Risk surfaces stay in sidecar:";
        details.appendChild(label);
        report.riskControls.slice(0, 8).forEach((control) => {
          const chip = document.createElement("span");
          chip.textContent = `${control.role}: ${control.risk}`;
          details.appendChild(chip);
        });
        mcelCanonicalAppLensMap.appendChild(details);
      }
      return true;
    }

    function clearMcelCanonicalTaskManagerLens(reason = "clear-lens") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) {
        state.lensStatus = "idle";
        renderMcelCanonicalAppLensMap(null, reason);
        renderMcelCanonicalAppSpecimenStatus(reason);
        return false;
      }

      doc.documentElement?.removeAttribute?.("data-mcel-canonical-lens");
      doc.documentElement?.removeAttribute?.("data-mcel-task-enrichment");
      doc.body.removeAttribute("data-mcel-canonical-lens");
      doc.body.removeAttribute("data-mcel-task-enrichment");
      doc.body.classList.remove(MCEL_CANONICAL_TASK_MANAGER_LENS_CLASS);
      doc.body.classList.remove(MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_CLASS);
      doc.body.style.removeProperty("--mcel-task-sidebar-width");
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_ENRICHMENT_STYLE_ID)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_SPECIMEN_RIBBON_ID)?.remove?.();

      const generatedSelectors = [
        ".mcel-lens-label[data-mcel-lens-generated=\"true\"]",
        ".mcel-lens-hud",
        ".mcel-lens-risk-badge"
      ];
      generatedSelectors.forEach((selector) => {
        Array.from(doc.querySelectorAll?.(selector) || []).forEach((node) => node.remove());
      });

      Array.from(doc.querySelectorAll?.("[data-mcel-lens-role], [data-mcel-action-risk], [data-mcel-risk], [data-mcel-lens-label], [data-mcel-lens-kind], [data-mcel-mutates], [data-mcel-action-label], [data-mcel-enriched], [data-mcel-enrichment-source], [data-mcel-enrichment-selector], [data-mcel-role], [data-mcel-kind], [data-mcel-fit], [data-mcel-fit-context], [data-mcel-layout], [data-mcel-layout-policy], [data-mcel-layout-region], [data-mcel-region], [data-mcel-region-kind], [data-mcel-width-policy], [data-mcel-control-role], [data-mcel-control-priority], [data-mcel-action-role]") || []).forEach((element) => {
        element.removeAttribute("data-mcel-lens-role");
        element.removeAttribute("data-mcel-action-risk");
        element.removeAttribute("data-mcel-risk");
        element.removeAttribute("data-mcel-lens-label");
        element.removeAttribute("data-mcel-lens-kind");
        element.removeAttribute("data-mcel-mutates");
        element.removeAttribute("data-mcel-action-label");
        element.removeAttribute("data-mcel-enriched");
        element.removeAttribute("data-mcel-enrichment-source");
        element.removeAttribute("data-mcel-enrichment-selector");
        element.removeAttribute("data-mcel-role");
        element.removeAttribute("data-mcel-kind");
        element.removeAttribute("data-mcel-fit");
        element.removeAttribute("data-mcel-fit-context");
        element.removeAttribute("data-mcel-layout");
        element.removeAttribute("data-mcel-layout-policy");
        element.removeAttribute("data-mcel-layout-region");
        element.removeAttribute("data-mcel-region");
        element.removeAttribute("data-mcel-region-kind");
        element.removeAttribute("data-mcel-width-policy");
        element.removeAttribute("data-mcel-control-role");
        element.removeAttribute("data-mcel-control-priority");
        element.removeAttribute("data-mcel-action-role");
      });

      const root = doc.querySelector?.(specimen.rootSelector) || null;
      if (root) {
        root.removeAttribute("data-mcel-lens");
        root.removeAttribute("data-mcel-lens-state");
        root.removeAttribute("data-mcel-component-id");
        root.removeAttribute("data-mcel-component-kind");
        root.removeAttribute("data-mcel-layout-law");
        root.removeAttribute("data-mcel-lens-sidecar");
        root.removeAttribute("data-mcel-lens-hud");
        root.removeAttribute("data-mcel-app");
        root.removeAttribute("data-mcel-enrichment-state");
        root.removeAttribute("data-mcel-proof-surface");
        root.removeAttribute("aria-description");
      }

      state.lensStatus = "clean";
      state.enrichmentStatus = "clean";
      state.lastAt = new Date().toISOString();
      mcelLabState.lastCanonicalSpecimenLens = null;
      mcelLabState.lastCanonicalSpecimenEnrichment = null;
      renderMcelCanonicalAppLensMap(null, reason);
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = `Cleaned MCEL specimen overlays and enrichment for ${specimen.label}.\nThe iframe still has specimen root markers, but Task Manager no longer carries lab-generated MCEL role/fit attributes.`;
      }
      recordMcelEvent(
        "canonical-app",
        "MCEL_CANONICAL_TASK_MANAGER_LENS_CLEANED",
        "Task Manager specimen lens overlay removed; sidecar state reset.",
        "info"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return true;
    }

    function applyMcelCanonicalTaskManagerLens(reason = "lens") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const doc = mcelCanonicalAppFrameDocument();
      if (!doc?.body) {
        const unavailable = {
          app: specimen.app,
          rootSelector: specimen.rootSelector,
          lensActive: false,
          rootPresent: false,
          panelCount: MCEL_CANONICAL_TASK_MANAGER_PANEL_LENS.length,
          classifiedPanelCount: 0,
          actionControlCount: 0,
          riskControlCount: 0,
          feedCount: 0,
          layoutLaw: "iframe document unavailable",
          overlayMode: "none",
          destructiveActionsExecuted: false,
          safetyClaim: "lens application never clicks Task Manager controls",
          reason,
          appliedAt: new Date().toISOString()
        };
        mcelLabState.lastCanonicalSpecimenLens = unavailable;
        renderMcelCanonicalAppLensMap(unavailable, reason);
        return unavailable;
      }

      const enrichmentReport = mcelLabState.lastCanonicalSpecimenEnrichment || applyMcelCanonicalTaskManagerEnrichment(reason);
      injectMcelCanonicalAppSpecimenChrome(reason);
      ensureMcelCanonicalTaskManagerLensStyle(doc);
      const root = doc.querySelector?.(specimen.rootSelector) || null;
      doc.documentElement?.setAttribute?.("data-mcel-canonical-lens", "active");
      doc.body.setAttribute("data-mcel-canonical-lens", "active");
      doc.body.classList.add(MCEL_CANONICAL_TASK_MANAGER_LENS_CLASS);
      doc.getElementById(MCEL_CANONICAL_SPECIMEN_RIBBON_ID)?.remove?.();
      doc.getElementById(MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID)?.remove?.();

      if (root) {
        root.setAttribute("data-mcel-lens", "canonical-task-manager");
        root.setAttribute("data-mcel-lens-state", "sidecar-inspector");
        root.setAttribute("data-mcel-component-id", "canonical.task-manager.root");
        root.setAttribute("data-mcel-component-kind", "canonical-app-specimen");
        root.setAttribute("data-mcel-layout-law", "sidecar-inspector");
      }

      const panels = MCEL_CANONICAL_TASK_MANAGER_PANEL_LENS.map((panel) => {
        const element = doc.querySelector?.(panel.selector) || null;
        if (element) {
          element.setAttribute("data-mcel-lens-role", panel.role);
          element.setAttribute("data-mcel-lens-kind", panel.kind);
          element.setAttribute("data-mcel-component-id", `canonical.task-manager.${panel.role}`);
          ensureMcelCanonicalTaskManagerLensLabel(doc, element, panel.label, panel.kind);
        }
        return {...panel, present: Boolean(element)};
      });

      const actionControls = [];
      MCEL_CANONICAL_TASK_MANAGER_ACTION_LENS.forEach((action) => {
        Array.from(doc.querySelectorAll?.(action.selector) || []).forEach((element) => {
          element.setAttribute("data-mcel-lens-role", action.role);
          element.setAttribute("data-mcel-action-risk", action.risk);
          element.setAttribute("data-mcel-action-label", action.label);
          element.setAttribute("data-mcel-mutates", action.risk === "safe" || action.risk === "analysis" ? "false" : "potential");
          actionControls.push({
            selector: action.selector,
            role: action.role,
            risk: action.risk,
            label: action.label,
            text: (element.textContent || element.getAttribute("aria-label") || element.id || action.selector).trim()
          });
        });
      });

      const feeds = panels.filter((panel) => panel.present && (panel.kind === "feed" || panel.role.endsWith("-feed")));
      const riskControls = actionControls.filter((item) => !["safe", "analysis"].includes(item.risk));
      const report = {
        app: specimen.app,
        route: mcelCanonicalAppFrame?.dataset?.mcelSpecimenRoute || specimen.route,
        rootSelector: specimen.rootSelector,
        lensActive: Boolean(root),
        enrichmentActive: Boolean(enrichmentReport?.enrichmentActive),
        enrichmentElementCount: enrichmentReport?.enrichedElementCount || 0,
        rootPresent: Boolean(root),
        panelCount: panels.length,
        classifiedPanelCount: panels.filter((panel) => panel.present).length,
        missingPanels: panels.filter((panel) => !panel.present).map((panel) => panel.selector),
        feedCount: feeds.length,
        feeds: feeds.map((panel) => panel.role),
        actionControlCount: actionControls.length,
        actionControls,
        riskControlCount: riskControls.length,
        riskControls,
        layoutLaw: "lab-side inspector lens active",
        overlayMode: "subtle root outline and hover/focus rings; no inline labels or risk badges",
        chromeStyleId: MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID,
        lensStyleId: MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID,
        lensHudId: MCEL_CANONICAL_TASK_MANAGER_LENS_HUD_ID,
        destructiveActionsExecuted: false,
        safetyClaim: "canonical lens annotates Task Manager and reports risk in the Lab sidecar; it does not restyle layout, inject labels into cards, or click server control, PID termination, or schedule actions",
        reason,
        appliedAt: new Date().toISOString()
      };

      renderMcelCanonicalTaskManagerLensHud(doc, root, report);
      renderMcelCanonicalAppLensMap(report, reason);
      state.lensCount = (state.lensCount || 0) + 1;
      state.lensStatus = report.lensActive ? "sidecar" : "warning";
      state.lastAt = report.appliedAt;
      mcelLabState.lastCanonicalSpecimenLens = report;
      recordMcelEvent(
        "canonical-app",
        report.lensActive ? "MCEL_CANONICAL_TASK_MANAGER_LENS_ACTIVE" : "MCEL_CANONICAL_TASK_MANAGER_LENS_WARNING",
        report.lensActive
          ? `Task Manager sidecar inspector classified ${report.classifiedPanelCount} panel(s), ${report.feedCount} feed(s), and ${report.riskControlCount} risk surface(s) without in-frame badges.`
          : `Task Manager canonical lens could not find ${specimen.rootSelector}.`,
        report.lensActive ? "success" : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return report;
    }

    function bindMcelCanonicalAppSpecimenLifecycle(reason = "bind") {
      const frame = mcelCanonicalAppFrame;
      if (!frame || frame.dataset.lifecycleBound === "true") {
        renderMcelCanonicalAppSpecimenStatus(reason);
        return frame;
      }
      frame.dataset.lifecycleBound = "true";
      frame.addEventListener("load", () => {
        const state = ensureMcelCanonicalAppSpecimenState();
        state.loadCount += 1;
        state.status = "loaded";
        state.lastAt = new Date().toISOString();
        injectMcelCanonicalAppSpecimenChrome("iframe-load");
        applyMcelCanonicalTaskManagerEnrichment("iframe-load");
        renderMcelCanonicalAppSpecimenStatus("iframe-load");
        window.setTimeout(() => inspectMcelCanonicalAppSpecimen("iframe-load"), 80);
      });
      frame.addEventListener("error", () => {
        const state = ensureMcelCanonicalAppSpecimenState();
        state.errorCount += 1;
        state.status = "error";
        state.lastAt = new Date().toISOString();
        renderMcelCanonicalAppSpecimenStatus("iframe-error");
        recordMcelEvent("canonical-app", "MCEL_CANONICAL_SPECIMEN_IFRAME_ERROR", "Canonical app specimen iframe emitted an error.", "warning");
      });
      renderMcelCanonicalAppSpecimenStatus(reason);
      return frame;
    }

    function mountMcelCanonicalAppSpecimen(reason = "mount") {
      const frame = bindMcelCanonicalAppSpecimenLifecycle(reason);
      if (!frame) return null;
      const specimen = selectedMcelCanonicalAppSpecimen();
      const state = ensureMcelCanonicalAppSpecimenState();
      state.mountCount += 1;
      state.app = specimen.app;
      state.route = specimen.route;
      state.rootSelector = specimen.rootSelector;
      state.status = "loading";
      state.lastAt = new Date().toISOString();
      mcelLabState.lastCanonicalSpecimenReport = null;
      mcelLabState.lastCanonicalSpecimenProof = null;
      mcelLabState.lastCanonicalSpecimenLens = null;
      mcelLabState.lastCanonicalSpecimenEnrichment = null;
      state.lensStatus = "pending";
      frame.dataset.mcelSpecimenApp = specimen.app;
      frame.dataset.mcelSpecimenRoot = specimen.rootSelector;
      frame.dataset.mcelSpecimenRoute = specimen.route;
      frame.src = specimen.route;
      renderMcelCanonicalAppLensMap(null, reason);
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = `Mounting ${specimen.label} from ${specimen.route}\nreason: ${reason}\nMCEL Lab will enrich the legacy DOM into regions, components, fields, actions, and fit laws after load.\nNo destructive controls are executed by this lab harness.`;
      }
      recordMcelEvent("canonical-app", "MCEL_CANONICAL_SPECIMEN_MOUNTING", `${specimen.label} specimen iframe loading ${specimen.route}.`);
      renderMcelCanonicalAppSpecimenStatus(reason);
      return specimen;
    }

    function refreshMcelCanonicalAppSpecimen(reason = "refresh") {
      const frame = bindMcelCanonicalAppSpecimenLifecycle(reason);
      if (!frame) return mountMcelCanonicalAppSpecimen(reason);
      const currentSrc = frame.getAttribute("src") || "";
      if (!currentSrc || currentSrc === "about:blank") {
        return mountMcelCanonicalAppSpecimen(reason);
      }
      const state = ensureMcelCanonicalAppSpecimenState();
      state.status = "refreshing";
      state.lastAt = new Date().toISOString();
      let refreshedMountedApp = false;
      try {
        const child = frame.contentWindow;
        const childDocument = child?.document;
        const hasTaskManagerRoot = Boolean(childDocument?.querySelector?.("#task-manager-app"));
        if (hasTaskManagerRoot && typeof child?.refreshTaskManager === "function") {
          refreshedMountedApp = true;
          Promise.resolve(child.refreshTaskManager())
            .catch(() => null)
            .finally(() => {
              window.setTimeout(() => inspectMcelCanonicalAppSpecimen(`${reason}:app-refresh-complete`), 80);
            });
        } else {
          const refreshButton = hasTaskManagerRoot ? childDocument?.querySelector?.("#task-refresh") : null;
          if (refreshButton) {
            refreshedMountedApp = true;
            refreshButton.click();
            window.setTimeout(() => inspectMcelCanonicalAppSpecimen(`${reason}:app-refresh-clicked`), 180);
          }
        }
        if (!refreshedMountedApp) {
          frame.contentWindow?.location?.reload();
        }
      } catch (error) {
        frame.src = currentSrc;
      }
      recordMcelEvent(
        "canonical-app",
        "MCEL_CANONICAL_SPECIMEN_REFRESHING",
        refreshedMountedApp
          ? "Canonical app specimen mounted-app refresh requested."
          : "Canonical app specimen iframe refresh requested."
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return selectedMcelCanonicalAppSpecimen();
    }

    function mcelCanonicalAppFrameDocument() {
      const frame = mcelCanonicalAppFrame;
      if (!frame) return null;
      try {
        return frame.contentDocument || frame.contentWindow?.document || null;
      } catch (error) {
        return null;
      }
    }

    function inspectMcelCanonicalAppSpecimen(reason = "inspect") {
      const state = ensureMcelCanonicalAppSpecimenState();
      const specimen = selectedMcelCanonicalAppSpecimen();
      const frame = bindMcelCanonicalAppSpecimenLifecycle(reason);
      injectMcelCanonicalAppSpecimenChrome(reason);
      const enrichmentReport = applyMcelCanonicalTaskManagerEnrichment(reason);
      const lensReport = applyMcelCanonicalTaskManagerLens(reason);
      const doc = mcelCanonicalAppFrameDocument();
      const root = doc?.querySelector?.(specimen.rootSelector) || null;
      const requiredIds = MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS.map((id) => ({
        id,
        present: Boolean(doc?.getElementById?.(id))
      }));
      const dangerousControls = MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_SELECTORS.map((selector) => {
        const elements = Array.from(doc?.querySelectorAll?.(selector) || []);
        return {
          selector,
          present: elements.length > 0,
          count: elements.length,
          labels: elements.slice(0, 8).map((element) => (element.textContent || element.getAttribute("aria-label") || element.id || selector).trim())
        };
      });
      const report = {
        app: specimen.app,
        route: frame?.dataset?.mcelSpecimenRoute || specimen.route,
        rootSelector: specimen.rootSelector,
        mounted: Boolean(frame && frame.getAttribute("src") && frame.getAttribute("src") !== "about:blank"),
        frameReadyState: doc?.readyState || "unavailable",
        rootPresent: Boolean(root),
        rootLabel: root?.getAttribute?.("aria-label") || root?.id || "",
        rootWidgetCount: root ? root.querySelectorAll(".app-widget, [data-widget-label], [data-mc-widget-id]").length : 0,
        tabCount: root ? root.querySelectorAll("[role=\"tab\"], [data-task-tab]").length : 0,
        requiredIds,
        missingRequiredIds: requiredIds.filter((item) => !item.present).map((item) => item.id),
        dangerousControls,
        dangerousControlCount: dangerousControls.reduce((total, item) => total + item.count, 0),
        specimenChromeApplied: Boolean(root?.getAttribute?.("data-mcel-lab-specimen-root")),
        specimenChromeStyleId: MCEL_CANONICAL_SPECIMEN_CHROME_STYLE_ID,
        specimenRibbonId: "removed-from-clean-sidecar-lens",
        enrichmentActive: Boolean(enrichmentReport?.enrichmentActive),
        enrichmentElementCount: enrichmentReport?.enrichedElementCount || 0,
        enrichmentLayoutLawStatus: enrichmentReport?.layoutLawStatus || "not-run",
        lensActive: Boolean(lensReport?.lensActive),
        lensPanelCount: lensReport?.classifiedPanelCount || 0,
        lensRiskControlCount: lensReport?.riskControlCount || 0,
        destructiveActionsExecuted: false,
        safetyClaim: "inspection only; the harness does not click server control, PID termination, or schedule creation actions",
        inspectedAt: new Date().toISOString(),
        reason
      };
      report.status = report.rootPresent && report.missingRequiredIds.length === 0 ? "passed" : "warning";
      mcelLabState.lastCanonicalSpecimenReport = report;
      state.inspectCount += 1;
      state.status = report.status === "passed" ? "inspected" : "inspection-warning";
      state.lastAt = report.inspectedAt;
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = JSON.stringify(report, null, 2);
      }
      recordMcelEvent(
        "canonical-app",
        report.status === "passed" ? "MCEL_CANONICAL_SPECIMEN_INSPECTED" : "MCEL_CANONICAL_SPECIMEN_INCOMPLETE",
        report.status === "passed"
          ? `Task Manager specimen inspected with ${report.rootWidgetCount} widget surface(s) and ${report.dangerousControlCount} audited risky control selector match(es).`
          : `Canonical specimen inspection warning: missing ${report.missingRequiredIds.join(", ") || specimen.rootSelector}.`,
        report.status === "passed" ? "success" : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return report;
    }

    function runMcelCanonicalAppSpecimenProof(reason = "specimen-proof") {
      const report = inspectMcelCanonicalAppSpecimen(reason);
      injectMcelCanonicalAppSpecimenChrome(reason);
      const doc = mcelCanonicalAppFrameDocument();
      const root = doc?.querySelector?.(report.rootSelector) || null;
      let proof = null;
      if (root && window.MCEL?.runBrowserProof) {
        try {
          proof = MCEL.runBrowserProof(root, {
            reason,
            surface: "canonical-app-specimen",
            app: report.app
          });
        } catch (error) {
          proof = {
            failed: true,
            error: error?.message || String(error),
            reason,
            surface: "canonical-app-specimen",
            app: report.app
          };
        }
      } else {
        proof = {
          failed: true,
          reason,
          surface: "canonical-app-specimen",
          app: report.app,
          error: root ? "MCEL.runBrowserProof unavailable" : `missing root ${report.rootSelector}`
        };
      }
      const state = ensureMcelCanonicalAppSpecimenState();
      state.proofCount += 1;
      state.status = proof && !proof.failed ? "proof-ready" : "proof-warning";
      state.lastAt = new Date().toISOString();
      mcelLabState.lastCanonicalSpecimenProof = proof;
      const combined = {
        inspection: report,
        enrichment: mcelLabState.lastCanonicalSpecimenEnrichment || applyMcelCanonicalTaskManagerEnrichment(reason),
        lens: mcelLabState.lastCanonicalSpecimenLens || applyMcelCanonicalTaskManagerLens(reason),
        browserProof: proof,
        destructiveActionsExecuted: false,
        safetyClaim: "browser proof observes the iframe DOM; it does not invoke Task Manager command buttons"
      };
      if (mcelCanonicalAppReport) {
        mcelCanonicalAppReport.textContent = JSON.stringify(combined, null, 2);
      }
      recordMcelEvent(
        "canonical-app",
        proof && !proof.failed ? "MCEL_CANONICAL_SPECIMEN_PROOF_READY" : "MCEL_CANONICAL_SPECIMEN_PROOF_WARNING",
        proof && !proof.failed
          ? `Task Manager specimen browser proof observed ${proof.elementCount || 0} element(s).`
          : `Task Manager specimen browser proof warning: ${proof?.error || "unavailable"}.`,
        proof && !proof.failed ? "success" : "warning"
      );
      renderMcelCanonicalAppSpecimenStatus(reason);
      return combined;
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
