    function createDefaultMcelLabState() {
      return {
        initialized: false,
        compileEvents: [],
        selectedIndex: 0,
        currentMode: "source",
        grapesEditor: null,
        grapesReady: false,
        syncingGrapes: false,
        theme: "theme-machine",
        lastSerializerReport: null,
        lastTestReport: null,
        lastCssLawReport: null,
        lastGraphReport: null,
        lastAuditReport: null,
        lastMatrixReport: null,
        lastAcidReport: null,
        lastAcidCaseId: null,
        lastEvidencePacket: null,
        lastReadinessReport: null,
        lastSupervisorReport: null,
        lastKernelAudit: null,
        lastTraceabilityMap: null,
        lastCommandPlan: null,
        lastProjectSnapshot: null,
        lastSourceList: []
      };
    }

    var mcelLabState = window.mcelLabState || (window.mcelLabState = createDefaultMcelLabState());

    const mcelLabApp = document.querySelector("#mcel-lab-app");
    const mcelSourceHtml = document.querySelector("#mcel-source-html");
    const mcelRuntimePreview = document.querySelector("#mcel-runtime-preview");
    const mcelRuntimeDom = document.querySelector("#mcel-runtime-dom");
    const mcelCompilerLog = document.querySelector("#mcel-compiler-log");
    const mcelSerializerDiff = document.querySelector("#mcel-serializer-diff");
    const mcelDebuggerOutput = document.querySelector("#mcel-debugger-output");
    const mcelA11yReport = document.querySelector("#mcel-a11y-report");
    const mcelTestReport = document.querySelector("#mcel-test-report");
    const mcelEditorStatus = document.querySelector("#mcel-editor-status");
    const mcelScenarioSelect = document.querySelector("#mcel-scenario-select");
    const mcelLoadScenario = document.querySelector("#mcel-load-scenario");
    const mcelScenarioDescription = document.querySelector("#mcel-scenario-description");
    const mcelSelectionStatus = document.querySelector("#mcel-selection-status");
    const mcelGrapesHost = document.querySelector("#mcel-grapes-host");
    const mcelGrapesCanvas = document.querySelector("#mcel-grapes");
    const mcelGrapesFallback = document.querySelector("#mcel-grapes-fallback");
    const mcelCompile = document.querySelector("#mcel-lab-compile");
    const mcelSerialize = document.querySelector("#mcel-lab-serialize");
    const mcelDamage = document.querySelector("#mcel-lab-damage");
    const mcelRepair = document.querySelector("#mcel-lab-repair");
    const mcelReset = document.querySelector("#mcel-lab-reset");
    const mcelRunTests = document.querySelector("#mcel-lab-run-tests");
    const mcelRunMatrix = document.querySelector("#mcel-lab-matrix");
    const mcelRunAudit = document.querySelector("#mcel-lab-audit");
    const mcelBuildEvidence = document.querySelector("#mcel-lab-evidence");
    const mcelRunAutopilot = document.querySelector("#mcel-lab-autopilot");
    const mcelRunAcid = document.querySelector("#mcel-lab-acid");
    const mcelRunAcidSuite = document.querySelector("#mcel-lab-acid-suite");
    const mcelAcidSelect = document.querySelector("#mcel-acid-select");
    const mcelRunKernel = document.querySelector("#mcel-lab-kernel");
    const mcelBuildTraceability = document.querySelector("#mcel-lab-traceability");
    const mcelApplyTraits = document.querySelector("#mcel-apply-traits");
    const mcelTraitKind = document.querySelector("#mcel-trait-kind");
    const mcelTraitFlow = document.querySelector("#mcel-trait-flow");
    const mcelTraitRank = document.querySelector("#mcel-trait-rank");
    const mcelTraitState = document.querySelector("#mcel-trait-state");
    const mcelTraitDensity = document.querySelector("#mcel-trait-density");
    const mcelTraitWords = document.querySelector("#mcel-trait-words");
    const mcelTraitConnects = document.querySelector("#mcel-trait-connects");
    const mcelThemeSelect = document.querySelector("#mcel-theme-select");
    const mcelCssLawReport = document.querySelector("#mcel-css-law-report");
    const mcelGraphReport = document.querySelector("#mcel-graph-report");
    const mcelAuditReport = document.querySelector("#mcel-audit-report");
    const mcelMatrixReport = document.querySelector("#mcel-matrix-report");
    const mcelAcidReport = document.querySelector("#mcel-acid-report");
    const mcelEvidenceReport = document.querySelector("#mcel-evidence-report");
    const mcelSupervisorReport = document.querySelector("#mcel-supervisor-report");
    const mcelKernelReport = document.querySelector("#mcel-kernel-report");
    const mcelTraceabilityReport = document.querySelector("#mcel-traceability-report");
    const mcelPriorArtReport = document.querySelector("#mcel-prior-art-report");
    const mcelReadinessScore = document.querySelector("#mcel-readiness-score");
    const mcelReadinessCards = document.querySelector("#mcel-readiness-cards");
    const mcelCommandInput = document.querySelector("#mcel-command-input");
    const mcelCommandPlan = document.querySelector("#mcel-command-plan");
    const mcelCommandApply = document.querySelector("#mcel-command-apply");
    const mcelCommandReport = document.querySelector("#mcel-command-report");
    const mcelProjectSave = document.querySelector("#mcel-project-save");
    const mcelProjectRestore = document.querySelector("#mcel-project-restore");
    const mcelProjectExport = document.querySelector("#mcel-project-export");
    const mcelProjectStatus = document.querySelector("#mcel-project-status");
    const mcelProjectReport = document.querySelector("#mcel-project-report");
    const mcelDiagnosticsDrawer = document.querySelector("#mcel-diagnostics-drawer");
