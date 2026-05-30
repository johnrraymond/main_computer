    var mcelLabState = window.mcelLabState || (window.mcelLabState = createDefaultMcelLabState());

    function mcelLabDependenciesReady() {
      return Boolean(
        window.McelLabContract &&
        window.McelLabEngine &&
        window.McelLabLawRegistry &&
        window.McelLabEditor &&
        window.McelLabScenarios &&
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
      mcelThemeSelect.innerHTML = "";
      McelLabStyleLaw.themes.forEach((theme) => {
        const option = document.createElement("option");
        option.value = theme;
        option.textContent = theme;
        mcelThemeSelect.appendChild(option);
      });
      mcelThemeSelect.value = McelLabStyleLaw.normalizeTheme(mcelLabState.theme);
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
      if (mcelThemeSelect) mcelThemeSelect.value = "theme-machine";
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
      mcelLabState.compileEvents = [
        ...mcelLabState.compileEvents,
        {level: "success", module: "style-law", code: "MCEL_THEME_CHANGED", message: `Theme changed to ${mcelLabState.theme} during ${reason}.`}
      ].slice(-64);
      applyMcelRuntimeStyleLaw(reason);
      renderMcelRuntimeDom();
      renderMcelCssLawReport();
      renderMcelGraphReport();
      renderMcelCompilerLog();
      syncMcelRenderedSiteFrame("theme");
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
        if (mcelThemeSelect) mcelThemeSelect.value = mcelLabState.theme;
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
        synced: frame?.dataset?.synced || "never"
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
            `synced=${event.synced}`
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
      replacement.setAttribute("sandbox", "");
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
          --gold: #f6c75b;
          --ink: #fff8df;
          --muted: #b9b28d;
          --sky: #73d6ff;
          --mint: #aee06f;
          --coral: #ff8b6b;
          --panel: #090b08;
          --line: rgba(246, 199, 91, 0.22);
        }
        * { box-sizing: border-box; }
        html { min-height: 100%; background: #050605; }
        body {
          margin: 0;
          min-height: 100%;
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: var(--ink);
          background:
            radial-gradient(circle at 90% 0%, rgba(246, 199, 91, 0.18), transparent 34rem),
            radial-gradient(circle at 0% 20%, rgba(115, 214, 255, 0.08), transparent 28rem),
            #050605;
        }
        .mcel-runtime-preview {
          width: min(1180px, calc(100% - 32px));
          margin: 0 auto;
          padding: clamp(18px, 3vw, 44px) 0;
          display: grid;
          gap: 18px;
          overflow: visible;
        }
        .mcel-runtime-preview .mc {
          min-width: 0;
          position: relative;
          display: grid;
          gap: 14px;
          padding: clamp(18px, 3vw, 34px);
          border: 1px solid var(--line);
          border-radius: 22px;
          background:
            linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015)),
            var(--panel);
          box-shadow: 0 24px 80px rgba(0,0,0,0.26);
          overflow: visible;
        }
        .mcel-runtime-preview [data-mc-generated="true"] {
          display: none !important;
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
          border-radius: 30px;
          background:
            radial-gradient(circle at 86% 4%, rgba(246, 199, 91, 0.16), transparent 30%),
            linear-gradient(180deg, rgba(255,255,255,0.055), rgba(255,255,255,0.018)),
            #080907;
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"] {
          grid-template-columns: minmax(0, 1.12fr) minmax(240px, 0.88fr);
          align-items: center;
          min-block-size: clamp(320px, 52vh, 620px);
          background:
            radial-gradient(circle at 88% 18%, rgba(115, 214, 255, 0.22), transparent 30%),
            linear-gradient(135deg, rgba(246, 199, 91, 0.12), rgba(174, 224, 111, 0.06)),
            #0b0d09;
        }
        .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
          content: "";
          inline-size: min(100%, 360px);
          aspect-ratio: 0.62;
          justify-self: end;
          grid-row: 1 / span 4;
          grid-column: 2;
          border-radius: 999px;
          background:
            linear-gradient(180deg, rgba(174,224,111,0.94), rgba(174,224,111,0.72)),
            radial-gradient(circle at 50% 26%, rgba(255,255,255,0.2), transparent 30%);
          box-shadow: inset 0 0 0 1px rgba(255,255,255,0.24), 0 24px 80px rgba(174,224,111,0.18);
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
        .mcel-runtime-preview h1 {
          max-width: 12ch;
          font-size: clamp(38px, 7vw, 88px);
          line-height: 0.92;
          letter-spacing: -0.075em;
        }
        .mcel-runtime-preview h2 {
          font-size: clamp(24px, 3vw, 42px);
          line-height: 1;
        }
        .mcel-runtime-preview h3 {
          color: var(--mint);
          font-size: 15px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }
        .mcel-runtime-preview p {
          max-width: 68ch;
          color: var(--muted);
          font-weight: 760;
          line-height: 1.55;
        }
        .mcel-runtime-preview [data-mc-slot="meta"] {
          width: fit-content;
          border: 1px solid rgba(115, 214, 255, 0.26);
          border-radius: 999px;
          padding: 6px 10px;
          color: var(--sky);
          font-size: 12px;
          font-weight: 950;
          text-transform: uppercase;
          letter-spacing: 0.06em;
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
          background: rgba(255,255,255,0.035);
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
          color: var(--muted);
          font-size: 12px;
          font-weight: 950;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .mcel-runtime-preview input {
          min-width: 0;
          border: 1px solid rgba(246, 199, 91, 0.32);
          border-radius: 999px;
          background: #030403;
          color: var(--ink);
          padding: 13px 15px;
          font: inherit;
        }
        .mcel-runtime-preview button,
        .mcel-runtime-preview a[data-mc-action] {
          justify-self: start;
          min-height: 42px;
          border: 0;
          border-radius: 999px;
          background: var(--gold);
          color: #151205;
          padding: 11px 18px;
          font-weight: 950;
          text-decoration: none;
          cursor: pointer;
        }
        .mcel-runtime-preview .mc[data-mc="command-row"] {
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
        }
        @media (max-width: 860px) {
          .mcel-runtime-preview .mc[data-mc-kind="hero"],
          .mcel-runtime-preview form.mc,
          .mcel-runtime-preview .mc[data-mc="command-row"],
          .mcel-runtime-preview .mc[data-mc-component="TrustCluster"] {
            grid-template-columns: 1fr;
          }
          .mcel-runtime-preview .mc[data-mc-kind="hero"]::after {
            grid-column: 1;
            grid-row: auto;
            justify-self: center;
            max-block-size: 320px;
          }
        }
      `;
    }

    function isolatedSiteDocument(runtimeHtml, meta = {}) {
      const reason = String(meta.reason || "sync").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const nonce = String(meta.nonce || "0").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const hash = String(meta.hash || "none").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      return `<!doctype html>
<html data-mcel-frame-generation="${nonce}" data-mcel-frame-reason="${reason}" data-mcel-frame-hash="${hash}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCEL rendered site</title>
<style>${isolatedSiteCss()}</style>
</head>
<body>
  <!-- MCEL iframe twiddle: reason=${reason}; nonce=${nonce}; hash=${hash} -->
  <div class="mcel-runtime-preview ${mcelLabState.theme || "theme-machine"}">
    ${runtimeHtml || ""}
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
        liveFrame.dataset.lastNonce = nonce;
        recordMcelSiteFrameTwiddle("iframe-sync", {reason, hash: documentHash, length: documentHtml.length});
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
