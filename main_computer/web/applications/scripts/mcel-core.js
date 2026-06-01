    var MCEL = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const styleLaw = typeof McelLabStyleLaw !== "undefined" ? McelLabStyleLaw : window.McelLabStyleLaw;
      const layoutLaw = typeof McelLabLayoutLaw !== "undefined" ? McelLabLayoutLaw : window.McelLabLayoutLaw;
      const chromeLaw = typeof McelLabChromeLaw !== "undefined" ? McelLabChromeLaw : window.McelLabChromeLaw;
      const browserObserver = typeof McelLabBrowserObserver !== "undefined" ? McelLabBrowserObserver : window.McelLabBrowserObserver;
      const platformSpine = typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine : window.McelLabPlatformSpine;
      const workbench = typeof McelLabWorkbench !== "undefined" ? McelLabWorkbench : window.McelLabWorkbench;
      const browserRunner = typeof McelLabBrowserRunner !== "undefined" ? McelLabBrowserRunner : window.McelLabBrowserRunner;
      const commandSurface = typeof McelLabCommandSurface !== "undefined" ? McelLabCommandSurface : window.McelLabCommandSurface;
      const graph = typeof McelLabGraph !== "undefined" ? McelLabGraph : window.McelLabGraph;
      const opsRunner = typeof McelLabOpsRunner !== "undefined" ? McelLabOpsRunner : window.McelLabOpsRunner;
      const acidTests = typeof McelLabAcidTests !== "undefined" ? McelLabAcidTests : window.McelLabAcidTests;
      const supervisor = typeof McelLabSupervisor !== "undefined" ? McelLabSupervisor : window.McelLabSupervisor;
      const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;


      function editorCatalog() {
        return contract.editorCatalog?.() || null;
      }

      function runtimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = String(html || "");
        return root;
      }

      function compile(sourceHtml, options = {}) {
        const source = editor?.canonicalSource ? editor.canonicalSource(sourceHtml || contract.defaultSource) : String(sourceHtml || contract.defaultSource);
        const compiled = engine.compileSource(source, {reason: options.reason || "mcel-core:compile"});
        const root = runtimeRoot(compiled.runtimeHtml);
        const cssLaw = options.applyLaws === false ? null : styleLaw?.applyRuntimeLaw?.(root, {theme: options.theme || "theme-machine", reason: options.reason || "mcel-core:compile"});
        const layoutReport = options.applyLaws === false ? null : layoutLaw?.applyRuntimeLaw?.(root, {reason: options.reason || "mcel-core:compile"});
        const platformReport = options.applyLaws === false ? null : platformSpine?.applyPlatformLaws?.(root, {reason: options.reason || "mcel-core:compile"});
        return {
          ...compiled,
          sourceHtml: source,
          runtimeHtml: root.innerHTML.trim(),
          runtimeRoot: root,
          laws: {
            cssLaw,
            layoutLaw: layoutReport,
            platform: platformReport
          }
        };
      }

      function serialize(runtimeRootOrHtml, options = {}) {
        const root = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        return engine.serializeRuntimeRoot(root, {reason: options.reason || "mcel-core:serialize"});
      }

      function repair(runtimeRootOrHtml, options = {}) {
        const root = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        const generatedRepair = engine.repairRuntimeRoot(root, {reason: options.reason || "mcel-core:repair"});
        const layoutRepair = layoutLaw?.repairRuntimeLaw?.(root, {reason: options.reason || "mcel-core:repair"}) || null;
        return {
          generatedRepair,
          layoutRepair,
          runtimeHtml: root?.innerHTML || ""
        };
      }

      function audit(sourceHtml, runtimeRootOrHtml = null, options = {}) {
        const runtime = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        const graphAudit = graph?.audit ? graph.audit(sourceHtml, runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const layoutProof = runtime && layoutLaw?.proveRuntime ? layoutLaw.proveRuntime(runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const platformProof = runtime && platformSpine?.provePlatform ? platformSpine.provePlatform(runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const lawProof = runtime && registry?.prove ? registry.prove(runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        return {
          kind: "mcel-core-audit",
          contractVersion: contract.contractVersion,
          graphAudit,
          layoutProof,
          platformProof,
          lawProof,
          failed: Boolean(graphAudit?.failed || layoutProof?.failed || platformProof?.failed || lawProof?.failed)
        };
      }

      function inspect(element, options = {}) {
        const debuggerState = engine.debuggerStateFor(element, options.root || element?.parentElement || element);
        const layout = layoutLaw?.computeElementLaw?.(element, Number(element?.getAttribute?.(contract.attributes.sourceIndex) || "0"), 1) || null;
        return {
          kind: "mcel-core-inspection",
          contractVersion: contract.contractVersion,
          geometryProof: debuggerState.geometryProof || null,
          scrollOwner: debuggerState.scrollOwner || null,
          overflowComputed: debuggerState.overflowComputed || null,
          debugger: debuggerState,
          browser: browserObserver?.observeElement?.(element, options) || null,
          layout
        };
      }

      function planCommand(commandText, context = {}) {
        return commandSurface.plan(commandText, context);
      }

      function applyCommand(plan, context = {}) {
        return commandSurface.apply(plan, context);
      }

      function runScenarioMatrix(options = {}) {
        return opsRunner.runScenarioMatrix(options);
      }

      function runAcidTests(options = {}) {
        return acidTests.runAll(options);
      }

      function buildEvidencePacket(options = {}) {
        return opsRunner.buildEvidencePacket(options);
      }

      function runProof(options = {}) {
        return supervisor?.runFullProof ? supervisor.runFullProof(options) : audit(options.source || contract.defaultSource, options.runtimeRoot || null, options);
      }

      function buildSubsumptionLattice() {
        return platformSpine?.buildSubsumptionLattice ? platformSpine.buildSubsumptionLattice() : null;
      }

      function buildWorkbenchPlan() {
        return workbench?.buildWorkbenchPlan ? workbench.buildWorkbenchPlan() : null;
      }

      function listChromes() {
        if (Array.isArray(chromeLaw?.chromeCatalog)) {
          return chromeLaw.chromeCatalog.map((definition) => ({...definition}));
        }
        if (Array.isArray(chromeLaw?.chromes)) {
          return chromeLaw.chromes.map((id) => chromeLaw?.chromeDefinition ? chromeLaw.chromeDefinition(id) : {id, label: id});
        }
        return [];
      }

      function normalizeChrome(chrome) {
        return chromeLaw?.normalizeChrome ? chromeLaw.normalizeChrome(chrome) : "chrome-strict-hierarchy";
      }

      function describeChrome(chrome) {
        return chromeLaw?.chromeDefinition ? chromeLaw.chromeDefinition(chrome) : null;
      }

      function applyChrome(runtimeHtml, options = {}) {
        const html = String(runtimeHtml || "");
        if (!chromeLaw?.applyChromeHtml) {
          return {
            html,
            report: {
              kind: "mcel-chrome-report",
              contractVersion: null,
              chrome: "chrome-strict-hierarchy",
              changed: false,
              generatedContainers: 0,
              movedSourceElements: 0,
              warnings: ["mcel-core:chrome-law-unavailable"]
            }
          };
        }
        return chromeLaw.applyChromeHtml(html, {...options, chrome: normalizeChrome(options.chrome)});
      }

      function runBrowserProof(runtimeRootOrHtml, options = {}) {
        const root = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        return browserRunner?.observeAndProve ? browserRunner.observeAndProve(root, options) : null;
      }

      return Object.freeze({
        version: contract.contractVersion,
        editorCatalog,
        compile,
        serialize,
        repair,
        audit,
        inspect,
        planCommand,
        applyCommand,
        runScenarioMatrix,
        runAcidTests,
        buildEvidencePacket,
        runProof,
        buildSubsumptionLattice,
        buildWorkbenchPlan,
        listChromes,
        normalizeChrome,
        describeChrome,
        applyChrome,
        runBrowserProof,
        platform: platformSpine,
        workbench,
        browserRunner,
        laws: registry
      });
    })();

    if (typeof window !== "undefined") {
      window.MCEL = MCEL;
      window.McelLabCore = MCEL;
    }
