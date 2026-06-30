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
      const scm = typeof McelLabScm !== "undefined" ? McelLabScm : window.McelLabScm;

      const debugTimeline = [];

      function debugNow() {
        try {
          return new Date().toISOString();
        } catch (_error) {
          return "unknown-time";
        }
      }

      function debugSummary(value) {
        if (!value || typeof value !== "object") return {};
        return {
          sourceCount: Number(value.sourceCount || 0),
          eventCount: Array.isArray(value.events) ? value.events.length : 0,
          runtimeHtmlLength: typeof value.runtimeHtml === "string" ? value.runtimeHtml.length : 0,
          serializedLength: typeof value.serialized === "string" ? value.serialized.length : 0,
          failed: Boolean(value.failed || value.report?.failed)
        };
      }

      function trimDebugTimeline() {
        const max = 250;
        while (debugTimeline.length > max) debugTimeline.shift();
      }

      function recordDebug(operation, subject = null, options = {}, result = null, error = null) {
        const reason = options.reason || `mcel-core:${operation}`;
        const envelope = engine?.captureDebugEnvelope
          ? engine.captureDebugEnvelope(subject || options.root || null, {
              name: options.debugName || `mcel-core:${operation}`,
              reason,
              rootSelector: options.rootSelector || "",
              expected: options.expectedDebug || {},
              selectors: options.debugSelectors || {},
              largestLimit: options.largestLimit || 12
            })
          : {
              kind: "mcel-debug-envelope",
              contractVersion: contract.contractVersion,
              generatedAt: debugNow(),
              name: `mcel-core:${operation}`,
              reason,
              ok: !error,
              issues: []
            };

        const entry = {
          ...envelope,
          operation,
          phase: error ? "error" : "after",
          reason,
          ok: Boolean(envelope.ok) && !error,
          resultSummary: debugSummary(result),
          error: error ? {
            name: error.name || "Error",
            message: error.message || String(error)
          } : null
        };
        debugTimeline.push(entry);
        trimDebugTimeline();
        if (typeof window !== "undefined") {
          window.__MCEL_DEBUG_TIMELINE__ = debugTimeline;
          window.__MCEL_LAST_DEBUG_ENVELOPE__ = entry;
        }
        return entry;
      }

      function getDebugTimeline() {
        return debugTimeline.map((entry) => ({...entry}));
      }

      function clearDebugTimeline() {
        debugTimeline.length = 0;
        if (typeof window !== "undefined") {
          window.__MCEL_DEBUG_TIMELINE__ = debugTimeline;
          window.__MCEL_LAST_DEBUG_ENVELOPE__ = null;
        }
        return {kind: "mcel-debug-clear", cleared: true, generatedAt: debugNow()};
      }

      function captureDebug(targetOrOptions = null, options = {}) {
        const envelope = engine?.captureDebugEnvelope
          ? engine.captureDebugEnvelope(targetOrOptions, options)
          : {
              kind: "mcel-debug-envelope",
              contractVersion: contract.contractVersion,
              generatedAt: debugNow(),
              name: options.name || "mcel-debug-capture",
              reason: options.reason || "manual-debug-capture",
              ok: true,
              issues: []
            };
        debugTimeline.push({...envelope, operation: "captureDebug", phase: "manual"});
        trimDebugTimeline();
        if (typeof window !== "undefined") {
          window.__MCEL_DEBUG_TIMELINE__ = debugTimeline;
          window.__MCEL_LAST_DEBUG_ENVELOPE__ = envelope;
        }
        return envelope;
      }

      function exportDebugPacket(options = {}) {
        return {
          kind: "mcel-debug-packet",
          contractVersion: contract.contractVersion,
          generatedAt: debugNow(),
          reason: options.reason || "mcel-core:export-debug-packet",
          timeline: getDebugTimeline(),
          mechanisms: engine?.listDebugMechanisms ? engine.listDebugMechanisms() : [],
          last: typeof window !== "undefined" ? window.__MCEL_LAST_DEBUG_ENVELOPE__ || null : debugTimeline[debugTimeline.length - 1] || null
        };
      }

      function listDebugMechanisms() {
        return engine?.listDebugMechanisms ? engine.listDebugMechanisms() : [];
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
        const result = {
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
        result.debug = recordDebug("compile", root, options, result);
        return result;
      }

      function serialize(runtimeRootOrHtml, options = {}) {
        const root = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        const result = engine.serializeRuntimeRoot(root, {reason: options.reason || "mcel-core:serialize"});
        result.debug = recordDebug("serialize", root, options, result);
        return result;
      }

      function repair(runtimeRootOrHtml, options = {}) {
        const root = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        const generatedRepair = engine.repairRuntimeRoot(root, {reason: options.reason || "mcel-core:repair"});
        const layoutRepair = layoutLaw?.repairRuntimeLaw?.(root, {reason: options.reason || "mcel-core:repair"}) || null;
        const result = {
          generatedRepair,
          layoutRepair,
          runtimeHtml: root?.innerHTML || ""
        };
        result.debug = recordDebug("repair", root, options, result);
        return result;
      }

      function audit(sourceHtml, runtimeRootOrHtml = null, options = {}) {
        const runtime = typeof runtimeRootOrHtml === "string" ? runtimeRoot(runtimeRootOrHtml) : runtimeRootOrHtml;
        const graphAudit = graph?.audit ? graph.audit(sourceHtml, runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const layoutProof = runtime && layoutLaw?.proveRuntime ? layoutLaw.proveRuntime(runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const platformProof = runtime && platformSpine?.provePlatform ? platformSpine.provePlatform(runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const lawProof = runtime && registry?.prove ? registry.prove(runtime, {reason: options.reason || "mcel-core:audit"}) : null;
        const result = {
          kind: "mcel-core-audit",
          contractVersion: contract.contractVersion,
          graphAudit,
          layoutProof,
          platformProof,
          lawProof,
          failed: Boolean(graphAudit?.failed || layoutProof?.failed || platformProof?.failed || lawProof?.failed)
        };
        result.debug = recordDebug("audit", runtime, options, result);
        return result;
      }

      function inspect(element, options = {}) {
        const debuggerState = engine.debuggerStateFor(element, options.root || element?.parentElement || element);
        const layout = layoutLaw?.computeElementLaw?.(element, Number(element?.getAttribute?.(contract.attributes.sourceIndex) || "0"), 1) || null;
        const result = {
          kind: "mcel-core-inspection",
          contractVersion: contract.contractVersion,
          geometryProof: debuggerState.geometryProof || null,
          scrollOwner: debuggerState.scrollOwner || null,
          overflowComputed: debuggerState.overflowComputed || null,
          debugger: debuggerState,
          browser: browserObserver?.observeElement?.(element, options) || null,
          layout
        };
        result.debug = recordDebug("inspect", element, options, result);
        return result;
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

      function buildAdoptionCase(options = {}) {
        return platformSpine?.buildAdoptionCase ? platformSpine.buildAdoptionCase(options) : null;
      }

      function buildUserSpaceContract() {
        return contract?.buildUserSpaceContract ? contract.buildUserSpaceContract() : null;
      }

      function listUserContractClauses() {
        return contract?.listUserContractClauses ? contract.listUserContractClauses() : [];
      }

      function buildWorkbenchPlan() {
        return workbench?.buildWorkbenchPlan ? workbench.buildWorkbenchPlan() : null;
      }

      function requireScm() {
        if (!scm) throw new Error("MCEL SCM kernel is not loaded.");
        return scm;
      }

      function defineComponent(name, manifest, options = {}) {
        return requireScm().defineComponent(name, manifest, options);
      }

      function validateComponentManifest(name, manifest) {
        return requireScm().validateComponentManifest(name, manifest);
      }

      function listComponentDefinitions() {
        return requireScm().listComponentDefinitions();
      }

      function componentDefinition(name) {
        return requireScm().componentDefinition(name);
      }

      function createComponentInstance(name, options = {}) {
        return requireScm().createComponentInstance(name, options);
      }

      function createChildContext(instance, childName) {
        return requireScm().createChildContext(instance, childName);
      }

      function createEffectContext(instance, effectName) {
        return requireScm().createEffectContext(instance, effectName);
      }

      function runEffect(instance, effectName, payload = {}) {
        return requireScm().runEffect(instance, effectName, payload);
      }

      function cancelEffect(instance, effectName, reason = "manual") {
        return requireScm().cancelEffect(instance, effectName, reason);
      }

      function checkLayoutContract(instance, observation = {}) {
        return requireScm().checkLayoutContract(instance, observation);
      }

      function checkStyleContract(instance, observation = {}) {
        return requireScm().checkStyleContract(instance, observation);
      }

      function transition(instance, transitionName, payload = {}) {
        return requireScm().transition(instance, transitionName, payload);
      }

      function exportScmEvidence(instance) {
        return requireScm().exportEvidence(instance);
      }

      function defineRoute(name, manifest, options = {}) {
        return requireScm().defineRoute(name, manifest, options);
      }

      function validateRouteManifest(name, manifest) {
        return requireScm().validateRouteManifest(name, manifest);
      }

      function listRouteDefinitions() {
        return requireScm().listRouteDefinitions();
      }

      function routeDefinition(name) {
        return requireScm().routeDefinition(name);
      }

      function createRouteInstance(name, options = {}) {
        return requireScm().createRouteInstance(name, options);
      }

      function enterRoute(instance, paramsOrOptions = {}, query = {}) {
        return requireScm().enterRoute(instance, paramsOrOptions, query);
      }

      function leaveRoute(instance, options = {}) {
        return requireScm().leaveRoute(instance, options);
      }

      function createRouteLoaderContext(instance, loaderName) {
        return requireScm().createRouteLoaderContext(instance, loaderName);
      }

      function runRouteLoader(instance, loaderName, payload = {}) {
        return requireScm().runRouteLoader(instance, loaderName, payload);
      }

      function cancelRouteLoader(instance, loaderName, reason = "manual") {
        return requireScm().cancelRouteLoader(instance, loaderName, reason);
      }

      function exportRouteEvidence(instance) {
        return requireScm().exportRouteEvidence(instance);
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
        buildAdoptionCase,
        buildUserSpaceContract,
        listUserContractClauses,
        buildWorkbenchPlan,
        defineComponent,
        validateComponentManifest,
        listComponentDefinitions,
        componentDefinition,
        createComponentInstance,
        createChildContext,
        createEffectContext,
        runEffect,
        cancelEffect,
        checkLayoutContract,
        checkStyleContract,
        transition,
        exportScmEvidence,
        defineRoute,
        validateRouteManifest,
        listRouteDefinitions,
        routeDefinition,
        createRouteInstance,
        enterRoute,
        leaveRoute,
        createRouteLoaderContext,
        runRouteLoader,
        cancelRouteLoader,
        exportRouteEvidence,
        listChromes,
        normalizeChrome,
        describeChrome,
        applyChrome,
        runBrowserProof,
        captureDebug,
        getDebugTimeline,
        exportDebugPacket,
        clearDebugTimeline,
        listDebugMechanisms,
        platform: platformSpine,
        workbench,
        browserRunner,
        scm,
        laws: registry
      });
    })();

    if (typeof window !== "undefined") {
      window.MCEL = MCEL;
      window.McelLabCore = MCEL;
    }
