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
      mcelApplyTraits?.addEventListener("click", applyMcelTraitsToSelectedSourceWidget);
      mcelLoadScenario?.addEventListener("click", loadSelectedMcelScenario);
      mcelScenarioSelect?.addEventListener("change", describeSelectedMcelScenario);
      mcelThemeSelect?.addEventListener("change", () => changeMcelTheme("theme-select"));
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
      renderMcelGraphReport();
      renderMcelScenarioMatrix();
      renderMcelEvidencePacket();
      renderMcelAcidTests();
      renderMcelSupervisorReport();
      renderMcelKernelAudit();
      renderMcelTraceabilityMap();
      renderMcelPriorArtReport();
      renderMcelReadiness();
      renderMcelProjectReport();
      renderMcelCompilerLog();
      syncMcelTraitControls();
      markSelectedMcelRuntimeElement();
      renderMcelSelectionStatus();
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
