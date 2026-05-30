    var McelLabTestHarness = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const scenarios = typeof McelLabScenarios !== "undefined" ? McelLabScenarios : window.McelLabScenarios;
      const styleLaw = typeof McelLabStyleLaw !== "undefined" ? McelLabStyleLaw : window.McelLabStyleLaw;
      const layoutLaw = typeof McelLabLayoutLaw !== "undefined" ? McelLabLayoutLaw : window.McelLabLayoutLaw;
      const commandSurface = typeof McelLabCommandSurface !== "undefined" ? McelLabCommandSurface : window.McelLabCommandSurface;
      const projectStore = typeof McelLabProjectStore !== "undefined" ? McelLabProjectStore : window.McelLabProjectStore;
      const graph = typeof McelLabGraph !== "undefined" ? McelLabGraph : window.McelLabGraph;
      const opsRunner = typeof McelLabOpsRunner !== "undefined" ? McelLabOpsRunner : window.McelLabOpsRunner;
      function kernel() {
        return window.McelLabKernel || null;
      }

      function acidTests() {
        return window.McelLabAcidTests || null;
      }
      const {attributes} = contract;

      function runtimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = html;
        return root;
      }

      function record(results, name, passed, details = "", group = "contract") {
        results.push({name, passed: Boolean(passed), details, group});
      }

      function runAll() {
        const results = [];
        const base = engine.runContractTests();
        base.tests.forEach((test) => record(results, test.name, test.passed, test.details, "engine"));

        scenarios.all().forEach((scenario) => {
          const compiled = engine.compileSource(scenario.source, {reason: `scenario:${scenario.id}`});
          const root = runtimeRoot(compiled.runtimeHtml);
          const serialized = engine.serializeRuntimeRoot(root, {reason: `scenario:${scenario.id}`});
          const recompiled = engine.compileSource(serialized.serialized, {reason: `scenario-recompile:${scenario.id}`});
          record(
            results,
            `${scenario.label}: compiles`,
            compiled.sourceCount > 0 && root.querySelectorAll(`[${attributes.generated}="true"]`).length > 0,
            `${compiled.sourceCount} source element(s), ${root.querySelectorAll(`[${attributes.generated}="true"]`).length} generated part(s)`,
            "scenario"
          );
          record(
            results,
            `${scenario.label}: serializes clean`,
            serialized.report.serializerClean && !serialized.serialized.includes(attributes.generated) && !serialized.serialized.includes(attributes.enhanced),
            `${serialized.report.removedGeneratedParts} generated part(s) stripped`,
            "scenario"
          );
          record(
            results,
            `${scenario.label}: reopens`,
            recompiled.sourceCount === compiled.sourceCount,
            `${recompiled.sourceCount} source element(s) after reopening`,
            "scenario"
          );
        });

        const compiled = engine.compileSource(contract.defaultSource, {reason: "editor-firewall"});
        const cleaned = editor.sanitizeEditorHtml(compiled.runtimeHtml);
        record(
          results,
          "editor save firewall strips generated runtime DOM",
          !cleaned.includes(attributes.generated) && !cleaned.includes(attributes.enhanced) && cleaned.includes(attributes.type),
          "runtime-owned attributes removed before source sync",
          "editor"
        );

        const traitSource = editor.applyTraits(contract.defaultSource, {index: 1}, {
          kind: "work",
          flow: "forward",
          rank: "secondary",
          state: "warning",
          density: "dense",
          words: "selected widget edited",
          connects: "",
          sizePolicy: "fixed",
          overflowPolicy: "delegate",
          scrollPolicy: "external"
        });
        const traits = editor.readTraits(traitSource.source, {index: 1});
        record(
          results,
          "selection-aware traits update selected widget",
          traits.kind === "work" &&
            traits.state === "warning" &&
            traits.sizePolicy === "fixed" &&
            traits.overflowPolicy === "delegate" &&
            traits.scrollPolicy === "external" &&
            editor.readTraits(traitSource.source, {index: 0}).kind === "signal",
          `selected index ${traits.index + 1}; overflow=${traits.overflowPolicy}/${traits.scrollPolicy}`,
          "editor"
        );

        const relationScenario = scenarios.byId("relation");
        const relationCompiled = engine.compileSource(relationScenario.source, {reason: "relation-harness"});
        const relationRoot = runtimeRoot(relationCompiled.runtimeHtml);
        const related = relationRoot.querySelector(`[${attributes.connects}]`);
        record(
          results,
          "relation hook resolves through semantic source",
          related?.getAttribute(attributes.relation) === "resolved",
          `relation=${related?.getAttribute(attributes.relation) || "missing"}`,
          "layout"
        );

        const styleRoot = runtimeRoot(compiled.runtimeHtml);
        const styleReport = styleLaw.applyRuntimeLaw(styleRoot, {theme: "theme-debug"});
        const styled = styleRoot.querySelector(`[${attributes.type}]`);
        const styleSerialized = engine.serializeRuntimeRoot(styleRoot, {reason: "style-law-harness"});
        record(
          results,
          "CSS law publishes runtime tokens without source pollution",
          styleReport.theme === "theme-debug" &&
            styled?.getAttribute(attributes.styleLaw) === "true" &&
            !styleSerialized.serialized.includes(attributes.styleLaw) &&
            !styleSerialized.serialized.includes(attributes.flowAxis),
          `theme=${styleReport.theme}, elements=${styleReport.elementCount}`,
          "style-law"
        );

        const layoutSource = `<section data-mc="panel" data-mc-kind="proof" data-mc-flow="stack" data-mc-density="dense" data-mc-size-policy="fixed" data-mc-overflow-policy="clip" data-mc-scroll-policy="never"><h2>Layout Harness</h2><p>Never-scroll policy should become runtime-only geometry proof data, then vanish from serialized source.</p></section>`;
        const layoutCompiled = engine.compileSource(layoutSource, {reason: "layout-law-harness"});
        const layoutRoot = runtimeRoot(layoutCompiled.runtimeHtml);
        const layoutReport = layoutLaw?.applyRuntimeLaw
          ? layoutLaw.applyRuntimeLaw(layoutRoot, {theme: "theme-machine", reason: "layout-law-harness"})
          : {layoutLawClean: false, warnings: ["layout law unavailable"]};
        const layoutTarget = layoutRoot.querySelector(`[${attributes.type}]`);
        const layoutSerialized = engine.serializeRuntimeRoot(layoutRoot, {reason: "layout-law-harness"});
        record(
          results,
          "layout law proves overflow and scrollbar policy without source pollution",
          layoutReport.layoutLawClean &&
            layoutTarget?.getAttribute(attributes.geometryProof) === "pass" &&
            layoutTarget?.getAttribute(attributes.scrollOwner) === "none" &&
            layoutSerialized.report.serializerClean &&
            !layoutSerialized.serialized.includes(attributes.geometryProof) &&
            layoutSerialized.serialized.includes(attributes.scrollPolicy),
          `layout=${layoutReport.layoutLawClean ? "clean" : "blocked"}, scrollOwner=${layoutTarget?.getAttribute(attributes.scrollOwner) || "missing"}`,
          "layout-law"
        );

        const graphReport = graph.compactReport(contract.defaultSource, styleRoot);
        record(
          results,
          "semantic graph maps source/runtime nodes and generated parts",
          graphReport.source.nodes === graphReport.runtime.nodes &&
            graphReport.runtime.generatedParts > 0 &&
            Array.isArray(graphReport.nodes),
          `${graphReport.runtime.nodes} runtime node(s), ${graphReport.runtime.generatedParts} generated part(s)`,
          "graph"
        );

        const auditReport = graph.audit(contract.defaultSource, null, {reason: "harness-audit"});
        record(
          results,
          "operational audit blocks source/runtime/provenance regressions",
          auditReport.status !== "blocked" &&
            auditReport.failed === 0 &&
            auditReport.runtimeGraph.generatedPartCount > 0,
          `${auditReport.passed} audit check(s), status=${auditReport.status}`,
          "audit"
        );

        const commandPlan = commandSurface.plan("set flow reverse; set state warning; theme debug; serialize", {
          source: contract.defaultSource,
          selectedIndex: 0,
          theme: "theme-machine"
        });
        const commandApplied = commandSurface.apply(commandPlan, {
          source: contract.defaultSource,
          selectedIndex: 0,
          theme: "theme-machine"
        });
        const commandTraits = editor.readTraits(commandApplied.source, {index: 0});
        record(
          results,
          "semantic command surface mutates clean source contracts",
          commandPlan.ok &&
            commandTraits.flow === "reverse" &&
            commandTraits.state === "warning" &&
            commandApplied.theme === "theme-debug" &&
            commandApplied.actions.includes("serialize"),
          commandPlan.summary.join("; "),
          "command"
        );

        const snapshot = projectStore.snapshot({
          source: commandApplied.source,
          selectedIndex: commandApplied.selectedIndex,
          theme: commandApplied.theme,
          mode: "diff",
          scenario: "round-trip",
          lastSerializerClean: true
        });
        record(
          results,
          "project snapshots persist clean semantic source only",
          snapshot.source.includes(attributes.type) &&
            !snapshot.source.includes(attributes.generated) &&
            snapshot.note.includes("never generated runtime DOM"),
          `version=${snapshot.version}, theme=${snapshot.theme}`,
          "project"
        );



        const matrix = opsRunner.runScenarioMatrix({
          scenarios: [scenarios.byId("round-trip"), scenarios.byId("relation")],
          themes: ["theme-machine", "theme-debug"]
        });
        record(
          results,
          "scenario-theme matrix proves cross-mode coverage",
          matrix.failed === 0 &&
            matrix.caseCount === 4 &&
            matrix.warnings.length === 0,
          `${matrix.passed}/${matrix.caseCount} scenario-theme case(s)`,
          "ops-runner"
        );

        const acid = acidTests()?.runAll?.({
          source: contract.defaultSource,
          theme: "theme-machine",
          matrixReport: matrix,
          reason: "harness-acid-tests"
        });
        record(
          results,
          "acid tests survive hostile runtime/editor/serializer pressure",
          Boolean(acid) &&
            acid.failed === 0 &&
            acid.total >= 10,
          acid ? `${acid.passed}/${acid.total} acid test(s)` : "acid module unavailable",
          "acid"
        );

        const evidence = opsRunner.buildEvidencePacket({
          source: contract.defaultSource,
          theme: "theme-machine",
          matrixReport: matrix,
          acidReport: acid,
          testReport: {passed: results.filter((result) => result.passed).length, failed: 0}
        });
        record(
          results,
          "evidence packet summarizes operational readiness",
          evidence.kind === "mcel-operational-evidence-packet" &&
            evidence.hashes.source.startsWith("fnv1a-") &&
            evidence.scenarioMatrix.caseCount === matrix.caseCount &&
            evidence.layoutLaw?.layoutLawClean !== false &&
            evidence.readiness.cards.length >= 7,
          `status=${evidence.readiness.status}, score=${evidence.readiness.score}`,
          "ops-runner"
        );

        const publicCompiled = window.MCEL?.compile?.(contract.defaultSource, {reason: "harness-public-core", theme: "theme-machine"});
        const publicRoot = publicCompiled ? runtimeRoot(publicCompiled.runtimeHtml) : null;
        const publicSerialized = publicRoot ? window.MCEL?.serialize?.(publicRoot, {reason: "harness-public-core"}) : null;
        const publicAudit = window.MCEL?.audit?.(contract.defaultSource, publicRoot, {reason: "harness-public-core"});
        const publicInspection = publicRoot ? window.MCEL?.inspect?.(publicRoot.querySelector(`[${attributes.type}]`), {root: publicRoot}) : null;
        record(
          results,
          "public MCEL core API fronts compile/serialize/repair/audit/inspect",
          Boolean(publicCompiled) &&
            publicCompiled.sourceCount > 0 &&
            publicSerialized?.report?.serializerClean &&
            publicAudit?.status !== "blocked" &&
            publicInspection?.geometryProof === "pass",
          publicCompiled ? `source=${publicCompiled.sourceCount}, audit=${publicAudit?.status || "unknown"}` : "MCEL facade unavailable",
          "core-api"
        );

        const kernelModule = kernel();
        const kernelAudit = kernelModule?.runKernelAudit?.({
          source: contract.defaultSource,
          theme: "theme-machine",
          matrixReport: matrix,
          acidReport: acid,
          testReport: {passed: results.filter((result) => result.passed).length, failed: 0},
          reason: "harness-kernel-audit"
        });
        const traceability = kernelModule?.buildTraceabilityMap?.({reason: "harness-traceability"});
        record(
          results,
          "kernel audit maps modules, requirements, prior art, and debt gates",
          Boolean(kernelAudit) &&
            kernelAudit.status === "ready" &&
            traceability.status === "covered" &&
            traceability.priorArt.length >= 8,
          kernelAudit ? `${kernelAudit.passCount}/${kernelAudit.total} debt gate(s), traceability=${traceability.covered}/${traceability.total}` : "kernel unavailable",
          "kernel"
        );

        const passed = results.filter((result) => result.passed).length;
        const failed = results.length - passed;
        return {
          passed,
          failed,
          tests: results,
          generatedAt: new Date().toISOString()
        };
      }

      return Object.freeze({runAll});
    })();

    if (typeof window !== "undefined") {
      window.McelLabTestHarness = McelLabTestHarness;
    }
