    var McelLabOpsRunner = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const styleLaw = typeof McelLabStyleLaw !== "undefined" ? McelLabStyleLaw : window.McelLabStyleLaw;
      const layoutLaw = typeof McelLabLayoutLaw !== "undefined" ? McelLabLayoutLaw : window.McelLabLayoutLaw;
      const platformSpine = typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine : window.McelLabPlatformSpine;
      const browserRunner = typeof McelLabBrowserRunner !== "undefined" ? McelLabBrowserRunner : window.McelLabBrowserRunner;
      const scenarios = typeof McelLabScenarios !== "undefined" ? McelLabScenarios : window.McelLabScenarios;
      const graph = typeof McelLabGraph !== "undefined" ? McelLabGraph : window.McelLabGraph;
      function testHarness() {
        return window.McelLabTestHarness || null;
      }
      const {attributes, contractVersion} = contract;

      function now() {
        return new Date().toISOString();
      }

      function hashText(input) {
        const text = String(input || "");
        let hash = 2166136261;
        for (let index = 0; index < text.length; index += 1) {
          hash ^= text.charCodeAt(index);
          hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
        }
        return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
      }

      function runtimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = String(html || "");
        return root;
      }

      function cleanScenarioSource(scenario) {
        return editor?.canonicalSource?.(scenario?.source || contract.defaultSource) || String(scenario?.source || contract.defaultSource);
      }

      function smartCount(root) {
        return root?.querySelectorAll?.(`[${attributes.type}]`).length || 0;
      }

      function serializedHasGeneratedMarkup(serialized) {
        return /\bdata-mc-generated\b|\bdata-mc-enhanced\b|\bdata-mc-source-index\b|\bdata-mc-owner\b|\bdata-mc-origin\b|\bdata-mc-reason\b|\bdata-mc-contract-version\b|\bdata-mc-layout-law\b|\bdata-mc-overflow-computed\b|\bdata-mc-scroll-needed\b|\bdata-mc-scroll-owner\b|\bdata-mc-layout-pressure\b|\bdata-mc-geometry-proof\b|\bdata-mc-keyboard-scroll\b/.test(String(serialized || ""));
      }

      function runCase(scenario, theme) {
        const source = cleanScenarioSource(scenario);
        const compiled = engine.compileSource(source, {reason: `matrix:${scenario.id}:${theme}`});
        const root = runtimeRoot(compiled.runtimeHtml);
        const cssLaw = styleLaw.applyRuntimeLaw(root, {theme, reason: "scenario-matrix"});
        const layoutReport = layoutLaw?.applyRuntimeLaw ? layoutLaw.applyRuntimeLaw(root, {reason: "scenario-matrix"}) : {layoutLawClean: true, warnings: []};
        const serialization = engine.serializeRuntimeRoot(root, {reason: "scenario-matrix"});
        const recompiled = engine.compileSource(serialization.serialized, {reason: "scenario-matrix-recompile"});
        const reRoot = runtimeRoot(recompiled.runtimeHtml);
        const reCssLaw = styleLaw.applyRuntimeLaw(reRoot, {theme, reason: "scenario-matrix-recompile"});
        const reLayoutReport = layoutLaw?.applyRuntimeLaw ? layoutLaw.applyRuntimeLaw(reRoot, {reason: "scenario-matrix-recompile"}) : {layoutLawClean: true, warnings: []};
        const audit = graph.audit(serialization.serialized, reRoot, {reason: "scenario-matrix"});
        const a11y = engine.computeA11y(reRoot);
        const leakFree = !serializedHasGeneratedMarkup(serialization.serialized);
        const sourceElements = smartCount(engine.parseSource(source));
        const serializedElements = smartCount(engine.parseSource(serialization.serialized));
        const recompiledElements = smartCount(reRoot);
        const warnings = [
          ...(serialization.report?.warnings || []),
          ...(cssLaw.warnings || []),
          ...(layoutReport.warnings || []),
          ...(reCssLaw.warnings || []),
          ...(reLayoutReport.warnings || []),
          ...(a11y.warnings || []),
          ...(audit.issues || [])
        ].filter(Boolean);
        if (sourceElements !== serializedElements) {
          warnings.push(`source/serialized element count mismatch ${sourceElements}/${serializedElements}`);
        }
        if (serializedElements !== recompiledElements) {
          warnings.push(`serialized/recompiled element count mismatch ${serializedElements}/${recompiledElements}`);
        }
        if (!leakFree) {
          warnings.push("serialized source contains runtime-owned MCEL markup");
        }

        const passed = Boolean(
          serialization.report?.serializerClean &&
          leakFree &&
          cssLaw.cssLawClean &&
          reCssLaw.cssLawClean &&
          layoutReport.layoutLawClean &&
          reLayoutReport.layoutLawClean &&
          a11y.a11yValid &&
          !audit.failed &&
          sourceElements === serializedElements &&
          serializedElements === recompiledElements
        );

        return {
          scenario: scenario.id,
          label: scenario.label,
          theme,
          passed,
          warnings,
          sourceHash: hashText(source),
          runtimeHash: hashText(root.innerHTML),
          serializedHash: hashText(serialization.serialized),
          recompiledHash: hashText(reRoot.innerHTML),
          sourceElements,
          serializedElements,
          recompiledElements,
          generatedParts: audit.runtimeGraph?.generatedPartCount || 0,
          provenanceClean: !audit.failed,
          serializerClean: Boolean(serialization.report?.serializerClean),
          cssLawClean: Boolean(cssLaw.cssLawClean && reCssLaw.cssLawClean),
          layoutLawClean: Boolean(layoutReport.layoutLawClean && reLayoutReport.layoutLawClean),
          a11yValid: Boolean(a11y.a11yValid),
          relationEdges: audit.runtimeGraph?.edgeCount || 0
        };
      }

      function runScenarioMatrix(options = {}) {
        const scenarioList = options.scenarios || scenarios.all();
        const themeList = options.themes || styleLaw.themes || contract.themes || ["theme-machine"];
        const cases = [];
        scenarioList.forEach((scenario) => {
          themeList.forEach((theme) => {
            cases.push(runCase(scenario, theme));
          });
        });
        const passed = cases.filter((item) => item.passed).length;
        const failed = cases.length - passed;
        return {
          kind: "mcel-scenario-theme-matrix",
          contractVersion,
          generatedAt: now(),
          scenarioCount: scenarioList.length,
          themeCount: themeList.length,
          caseCount: cases.length,
          passed,
          failed,
          passRate: cases.length ? Number((passed / cases.length).toFixed(4)) : 0,
          cases,
          warnings: cases.flatMap((item) => item.warnings.map((warning) => `${item.scenario}/${item.theme}: ${warning}`))
        };
      }

      function summarizeMatrix(matrix) {
        if (!matrix) return "Scenario matrix has not run yet.";
        const lines = [
          `MCEL SCENARIO × THEME MATRIX: ${matrix.passed} passed / ${matrix.failed} failed`,
          `contractVersion: ${matrix.contractVersion}`,
          `generatedAt: ${matrix.generatedAt}`,
          `coverage: ${matrix.scenarioCount} scenario(s) × ${matrix.themeCount} theme(s) = ${matrix.caseCount} case(s)`,
          ""
        ];
        matrix.cases.forEach((item) => {
          lines.push(`${item.passed ? "PASS" : "FAIL"} ${item.scenario} / ${item.theme} · source=${item.sourceElements} generated=${item.generatedParts} layout=${item.layoutLawClean ? "clean" : "blocked"} edges=${item.relationEdges}`);
          item.warnings.slice(0, 3).forEach((warning) => lines.push(`  - ${warning}`));
        });
        if (matrix.warnings.length > 0) {
          lines.push("", "WARNINGS", ...matrix.warnings.slice(0, 20));
        }
        return lines.join("\n").trim();
      }

      function buildReadiness(state = {}) {
        const serializerClean = Boolean(state.serializerReport?.serializerClean);
        const cssLawClean = Boolean(state.cssLawReport?.cssLawClean);
        const layoutLawClean = Boolean(state.layoutLawReport?.layoutLawClean);
        const a11yValid = Boolean(state.a11yReport?.a11yValid);
        const auditClean = Boolean(state.auditReport && !state.auditReport.failed);
        const testsClean = Boolean(state.testReport && !state.testReport.failed);
        const matrixClean = Boolean(state.matrixReport && !state.matrixReport.failed);
        const acidClean = Boolean(state.acidReport && !state.acidReport.failed);
        const kernelClean = Boolean(state.kernelReport && state.kernelReport.status === "ready");
        const platformClean = Boolean(state.platformReport && !state.platformReport.failed);
        const browserProofClean = Boolean(state.browserProof && !state.browserProof.failed);
        const cards = [
          {key: "serializer", label: "Serializer", status: serializerClean ? "pass" : "pending", detail: serializerClean ? "clean source output" : "needs serialization proof"},
          {key: "css-law", label: "CSS Law", status: cssLawClean ? "pass" : "pending", detail: cssLawClean ? `${state.cssLawReport?.elementCount || 0} runtime element(s)` : "runtime tokens pending"},
          {key: "layout-law", label: "Layout / Geometry Law", status: layoutLawClean ? "pass" : (state.layoutLawReport ? "fail" : "pending"), detail: state.layoutLawReport ? `${state.layoutLawReport.passed || 0}/${state.layoutLawReport.elementCount || 0} element(s)` : "overflow proof pending"},
          {key: "platform-spine", label: "Platform Spine", status: platformClean ? "pass" : (state.platformReport ? "fail" : "pending"), detail: state.platformReport ? `${state.platformReport.moduleCount || 0} subsystem law(s)` : "component/state/data/form/action/render/a11y/perf proof pending"},
          {key: "browser-proof", label: "Browser Semantic Proof", status: browserProofClean ? "pass" : (state.browserProof ? "fail" : "pending"), detail: state.browserProof ? `live geometry ${state.browserProof.liveGeometry}` : "live browser oracle pending"},
          {key: "a11y", label: "A11y", status: a11yValid ? "pass" : "pending", detail: a11yValid ? "labels and decoration valid" : "a11y report pending"},
          {key: "audit", label: "Operational Audit", status: auditClean ? "pass" : (state.auditReport ? "fail" : "pending"), detail: auditClean ? "provenance clean" : "run audit"},
          {key: "contract-suite", label: "Contract Suite", status: testsClean ? "pass" : (state.testReport ? "fail" : "pending"), detail: state.testReport ? `${state.testReport.passed}/${state.testReport.passed + state.testReport.failed} tests` : "not run"},
          {key: "matrix", label: "Scenario Matrix", status: matrixClean ? "pass" : (state.matrixReport ? "fail" : "pending"), detail: state.matrixReport ? `${state.matrixReport.passed}/${state.matrixReport.caseCount} cases` : "not run"},
          {key: "acid-tests", label: "Acid Tests", status: acidClean ? "pass" : (state.acidReport ? "fail" : "pending"), detail: state.acidReport ? `${state.acidReport.passed}/${state.acidReport.total} acid tests` : "not run"},
          {key: "kernel", label: "Kernel", status: kernelClean ? "pass" : (state.kernelReport ? "fail" : "pending"), detail: state.kernelReport ? `${state.kernelReport.passCount}/${state.kernelReport.total} debt gates` : "not run"}
        ];
        const passCount = cards.filter((card) => card.status === "pass").length;
        const failCount = cards.filter((card) => card.status === "fail").length;
        return {
          kind: "mcel-operational-readiness",
          contractVersion,
          generatedAt: now(),
          score: Number((passCount / cards.length).toFixed(3)),
          status: failCount ? "blocked" : (passCount === cards.length ? "ready" : "warming"),
          passCount,
          failCount,
          total: cards.length,
          cards
        };
      }

      function buildEvidencePacket(state = {}) {
        const source = editor?.canonicalSource?.(state.source || contract.defaultSource) || String(state.source || contract.defaultSource);
        let root = state.runtimeRoot || null;
        if (!root) {
          const compiled = engine.compileSource(source, {reason: "evidence-packet"});
          root = runtimeRoot(compiled.runtimeHtml);
          styleLaw.applyRuntimeLaw(root, {theme: state.theme || "theme-machine", reason: "evidence-packet"});
          layoutLaw?.applyRuntimeLaw?.(root, {reason: "evidence-packet"});
          platformSpine?.applyPlatformLaws?.(root, {reason: "evidence-packet"});
        }
        const layoutReport = layoutLaw?.reportFor ? layoutLaw.reportFor(root, {reason: "evidence-packet"}) : (state.layoutLawReport || {layoutLawClean: true, warnings: []});
        const platformReport = platformSpine?.provePlatform ? platformSpine.provePlatform(root, {reason: "evidence-packet"}) : (state.platformReport || null);
        const browserProof = browserRunner?.observeAndProve ? browserRunner.observeAndProve(root, {reason: "evidence-packet"}) : (state.browserProof || null);
        const serialized = engine.serializeRuntimeRoot(root, {reason: "evidence-packet"});
        const a11y = engine.computeA11y(root);
        const audit = graph.audit(source, root, {reason: "evidence-packet"});
        const compactGraph = graph.compactReport(source, root);
        const cssLaw = styleLaw.reportFor(root, {theme: state.theme || "theme-machine", reason: "evidence-packet"});
        const harness = testHarness();
        const tests = state.includeTests && harness ? harness.runAll() : state.testReport || null;
        const matrix = state.matrixReport || null;
        const readiness = buildReadiness({
          serializerReport: serialized.report,
          cssLawReport: cssLaw,
          layoutLawReport: layoutReport,
          platformReport,
          browserProof,
          a11yReport: a11y,
          auditReport: audit,
          testReport: tests,
          matrixReport: matrix,
          acidReport: state.acidReport || null,
          kernelReport: state.kernelReport || null
        });

        return {
          kind: "mcel-operational-evidence-packet",
          contractVersion,
          generatedAt: now(),
          theme: state.theme || "theme-machine",
          hashes: {
            source: hashText(source),
            runtime: hashText(root.innerHTML),
            serialized: hashText(serialized.serialized)
          },
          source: {
            length: source.length,
            elementCount: smartCount(engine.parseSource(source))
          },
          runtime: {
            length: root.innerHTML.length,
            elementCount: smartCount(root),
            generatedPartCount: compactGraph.runtime?.generatedParts || 0
          },
          serializer: serialized.report,
          a11y,
          cssLaw,
          layoutLaw: layoutReport,
          platformSpine: platformReport,
          browserProof,
          subsumptionLattice: platformSpine?.buildSubsumptionLattice ? platformSpine.buildSubsumptionLattice() : null,
          graph: compactGraph,
          audit: {
            passed: !audit.failed,
            failed: audit.failed,
            issues: audit.issues
          },
          testSuite: tests ? {
            passed: tests.passed,
            failed: tests.failed
          } : null,
          scenarioMatrix: matrix ? {
            passed: matrix.passed,
            failed: matrix.failed,
            caseCount: matrix.caseCount,
            passRate: matrix.passRate
          } : null,
          acidTests: state.acidReport ? {
            passed: state.acidReport.passed,
            failed: state.acidReport.failed,
            total: state.acidReport.total,
            status: state.acidReport.status
          } : null,
          readiness,
          claims: [
            "canonical source is clean semantic HTML",
            "runtime DOM is generated under explicit provenance",
            "serializer strips runtime-owned artifacts",
            "CSS law publishes runtime tokens without mutating source",
            "layout/overflow law proves scrollbar ownership without mutating source",
            "platform spine registers component/state/data/form/action/render/a11y/performance laws to obsolete legacy libraries",
            "browser semantic runner treats Playwright-like automation as machine-state input while MCEL laws own truth",
            "operational audit checks graph and provenance contracts",
            "scenario matrix exercises every built-in scenario across every theme",
            "acid tests inject hostile runtime/editor/command/schema pressure without source corruption"
          ],
          warnings: [
            ...(serialized.report?.warnings || []),
            ...(a11y.warnings || []),
            ...(layoutReport.warnings || []),
            ...(platformReport?.warnings || []),
            ...(browserProof?.browserReport?.warnings || []),
            ...(audit.issues || []),
            ...(!matrix ? ["scenario matrix has not been run for this evidence packet"] : []),
            ...(!state.acidReport ? ["acid tests have not been run for this evidence packet"] : [])
          ]
        };
      }

      function compactEvidenceText(packet) {
        if (!packet) return "Evidence packet has not been built yet.";
        const lines = [
          `MCEL OPERATIONAL EVIDENCE PACKET`,
          `status: ${packet.readiness.status} (${packet.readiness.passCount}/${packet.readiness.total} readiness checks)`,
          `contractVersion: ${packet.contractVersion}`,
          `generatedAt: ${packet.generatedAt}`,
          `theme: ${packet.theme}`,
          "",
          "HASHES",
          `source: ${packet.hashes.source}`,
          `runtime: ${packet.hashes.runtime}`,
          `serialized: ${packet.hashes.serialized}`,
          "",
          "COUNTS",
          `source elements: ${packet.source.elementCount}`,
          `runtime elements: ${packet.runtime.elementCount}`,
          `generated parts: ${packet.runtime.generatedPartCount}`,
          "",
          "PROOFS",
          `serializer clean: ${packet.serializer.serializerClean}`,
          `a11y valid: ${packet.a11y.a11yValid}`,
          `css law clean: ${packet.cssLaw.cssLawClean}`,
          `layout law clean: ${packet.layoutLaw?.layoutLawClean}`,
          `audit passed: ${packet.audit.passed}`,
          `contract suite: ${packet.testSuite ? `${packet.testSuite.passed} passed / ${packet.testSuite.failed} failed` : "not attached"}`,
          `scenario matrix: ${packet.scenarioMatrix ? `${packet.scenarioMatrix.passed} passed / ${packet.scenarioMatrix.failed} failed` : "not attached"}`,
          `acid tests: ${packet.acidTests ? `${packet.acidTests.passed} passed / ${packet.acidTests.failed} failed` : "not attached"}`,
          "",
          "CLAIMS",
          ...packet.claims.map((claim) => `- ${claim}`)
        ];
        if (packet.warnings.length) {
          lines.push("", "WARNINGS", ...packet.warnings.slice(0, 20).map((warning) => `- ${warning}`));
        }
        return lines.join("\n").trim();
      }

      return Object.freeze({
        hashText,
        runScenarioMatrix,
        summarizeMatrix,
        buildReadiness,
        buildEvidencePacket,
        compactEvidenceText
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabOpsRunner = McelLabOpsRunner;
    }

