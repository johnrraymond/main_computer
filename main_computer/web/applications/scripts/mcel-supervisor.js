    var McelLabSupervisor = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const styleLaw = typeof McelLabStyleLaw !== "undefined" ? McelLabStyleLaw : window.McelLabStyleLaw;
      const graph = typeof McelLabGraph !== "undefined" ? McelLabGraph : window.McelLabGraph;
      const opsRunner = typeof McelLabOpsRunner !== "undefined" ? McelLabOpsRunner : window.McelLabOpsRunner;
      const {attributes, contractVersion} = contract;

      function testHarness() {
        return window.McelLabTestHarness || null;
      }

      function acidTests() {
        return window.McelLabAcidTests || null;
      }

      function now() {
        return new Date().toISOString();
      }

      function runtimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = String(html || "");
        return root;
      }

      function passGate(key, label, passed, detail = "", extra = {}) {
        return {
          key,
          label,
          status: passed ? "pass" : "fail",
          detail,
          ...extra
        };
      }

      function smartCount(root) {
        return root?.querySelectorAll?.(`[${attributes.type}]`)?.length || 0;
      }

      function generatedCount(root) {
        return root?.querySelectorAll?.(`[${attributes.generated}="true"]`)?.length || 0;
      }

      function noGeneratedSourceLeak(serialized) {
        return !String(serialized || "").includes(attributes.generated) &&
          !String(serialized || "").includes(attributes.enhanced) &&
          !String(serialized || "").includes(attributes.artifactOwner);
      }

      function buildQualityGate(state = {}) {
        const serializedClean = Boolean(state.serializerReport?.serializerClean) && noGeneratedSourceLeak(state.serialized);
        const cssClean = Boolean(state.cssLawReport?.cssLawClean);
        const a11yClean = Boolean(state.a11yReport?.a11yValid);
        const auditClean = Boolean(state.auditReport && !state.auditReport.failed);
        const testsClean = Boolean(state.testReport && !state.testReport.failed);
        const matrixClean = Boolean(state.matrixReport && !state.matrixReport.failed);
        const acidClean = Boolean(state.acidReport && !state.acidReport.failed);
        const evidenceClean = Boolean(state.evidencePacket?.hashes?.source && ["ready", "warming"].includes(state.evidencePacket?.readiness?.status));
        const compilerClean = Boolean(state.sourceElementCount > 0 && state.generatedPartCount >= state.sourceElementCount);
        const kernelClean = Boolean(state.kernelReport && state.kernelReport.status === "ready");

        const gates = [
          passGate("compiler", "Compiler", compilerClean, compilerClean ? `${state.sourceElementCount} source element(s), ${state.generatedPartCount} generated part(s)` : "compiler did not produce the expected runtime structure"),
          passGate("serializer", "Serializer Firewall", serializedClean, serializedClean ? "serialized output contains no runtime ownership markers" : "serialized output is not clean"),
          passGate("css-law", "CSS Law", cssClean, cssClean ? `${state.cssLawReport?.elementCount || 0} element(s) received runtime tokens` : "CSS law report is not clean"),
          passGate("a11y", "A11y", a11yClean, a11yClean ? "labels, order, and hidden decoration are valid" : "a11y report is not valid"),
          passGate("operational-audit", "Operational Audit", auditClean, auditClean ? "semantic graph and provenance are clean" : "audit failed or has not run"),
          passGate("contract-suite", "Contract Suite", testsClean, testsClean ? `${state.testReport.passed} passed / ${state.testReport.failed} failed` : "contract suite failed or has not run"),
          passGate("scenario-matrix", "Scenario Matrix", matrixClean, matrixClean ? `${state.matrixReport.passed} passed / ${state.matrixReport.caseCount} cases` : "scenario matrix failed or has not run"),
          passGate("acid-tests", "Acid Tests", acidClean, acidClean ? `${state.acidReport.passed} passed / ${state.acidReport.total} acid tests` : "acid tests failed or have not run"),
          passGate("evidence-packet", "Evidence Packet", evidenceClean, evidenceClean ? "hash-stamped evidence packet is ready" : "evidence packet is incomplete"),
          passGate("kernel", "Kernel Audit", kernelClean, kernelClean ? `${state.kernelReport.passCount}/${state.kernelReport.total} debt gates passed` : "kernel audit failed or has not run")
        ];

        const passCount = gates.filter((gate) => gate.status === "pass").length;
        const failCount = gates.length - passCount;
        return {
          kind: "mcel-supervisor-quality-gate",
          contractVersion,
          generatedAt: now(),
          status: failCount ? "blocked" : "ready",
          score: Number((passCount / gates.length).toFixed(3)),
          passCount,
          failCount,
          total: gates.length,
          gates
        };
      }

      function runFullProof(options = {}) {
        const reason = options.reason || "autopilot-proof";
        const source = editor.canonicalSource(options.source || contract.defaultSource);
        const theme = styleLaw.normalizeTheme(options.theme || "theme-machine");
        const compiled = engine.compileSource(source, {reason});
        const root = runtimeRoot(compiled.runtimeHtml);
        const cssLawReport = styleLaw.applyRuntimeLaw(root, {theme, reason});
        const serializer = engine.serializeRuntimeRoot(root, {reason: `${reason}:serializer-firewall`});
        const a11yReport = engine.computeA11y(root);
        const graphReport = graph.compactReport(source, root);
        const auditReport = graph.audit(source, root, {reason: `${reason}:audit`});
        const runHeavyProofs = Boolean(options.runHeavyProofs);
        const harness = testHarness();
        const testReport = options.testReport || (runHeavyProofs && harness ? harness.runAll() : null);
        const matrixReport = options.matrixReport || (runHeavyProofs ? opsRunner.runScenarioMatrix() : null);
        const acidReport = options.acidReport || (runHeavyProofs ? acidTests()?.runAll?.({source, theme, matrixReport, reason: `${reason}:acid`}) : null) || null;
        const kernelReport = options.kernelReport || (runHeavyProofs && window.McelLabKernel
          ? window.McelLabKernel.runKernelAudit({
            source,
            runtimeRoot: root,
            theme,
            testReport,
            matrixReport,
            acidReport,
            reason: `${reason}:kernel-audit`
          })
          : null);
        const evidencePacket = opsRunner.buildEvidencePacket({
          source,
          runtimeRoot: root,
          theme,
          serializerReport: serializer.report,
          cssLawReport,
          auditReport,
          testReport,
          matrixReport,
          acidReport,
          kernelReport
        });

        const sourceElementCount = smartCount(engine.parseSource(source));
        const runtimeElementCount = smartCount(root);
        const generatedPartCount = generatedCount(root);
        const qualityGate = buildQualityGate({
          sourceElementCount,
          runtimeElementCount,
          generatedPartCount,
          serialized: serializer.serialized,
          serializerReport: serializer.report,
          cssLawReport,
          a11yReport,
          auditReport,
          testReport,
          matrixReport,
          acidReport,
          evidencePacket,
          kernelReport
        });

        return {
          kind: "mcel-supervisor-autopilot-proof",
          contractVersion,
          generatedAt: now(),
          reason,
          theme,
          source,
          runtimeHtml: root.innerHTML,
          serialized: serializer.serialized,
          compileEvents: compiled.events,
          sourceElementCount,
          runtimeElementCount,
          generatedPartCount,
          serializerReport: serializer.report,
          cssLawReport,
          a11yReport,
          graphReport,
          auditReport,
          testReport,
          matrixReport,
          acidReport,
          evidencePacket,
          kernelReport,
          readiness: evidencePacket.readiness,
          qualityGate,
          warnings: [
            ...serializer.report.warnings,
            ...(auditReport.issues || []),
            ...(matrixReport?.warnings || []),
            ...(acidReport?.tests?.filter((test) => !test.passed).map((test) => `Acid: ${test.name}`) || []),
            ...(qualityGate.gates.filter((gate) => gate.status !== "pass").map((gate) => `${gate.label}: ${gate.detail}`))
          ]
        };
      }

      function compactText(report) {
        if (!report) return "Autopilot proof has not run yet.";
        const lines = [
          "MCEL AUTOPILOT PROOF",
          `status: ${report.qualityGate.status}`,
          `quality gate: ${report.qualityGate.passCount}/${report.qualityGate.total} pass · score ${report.qualityGate.score}`,
          `readiness: ${report.readiness.status} · ${report.readiness.passCount}/${report.readiness.total}`,
          `acid tests: ${report.acidReport ? `${report.acidReport.passed}/${report.acidReport.total}` : "not attached"}`,
          `kernel: ${report.kernelReport ? `${report.kernelReport.status} · ${report.kernelReport.passCount}/${report.kernelReport.total}` : "not attached"}`,
          `contractVersion: ${report.contractVersion}`,
          `generatedAt: ${report.generatedAt}`,
          `reason: ${report.reason}`,
          `theme: ${report.theme}`,
          "",
          "COUNTS",
          `source elements: ${report.sourceElementCount}`,
          `runtime elements: ${report.runtimeElementCount}`,
          `generated parts: ${report.generatedPartCount}`,
          "",
          "GATES",
          ...report.qualityGate.gates.map((gate) => `${gate.status === "pass" ? "PASS" : "FAIL"} ${gate.label} — ${gate.detail}`),
          "",
          "HASHES",
          `source: ${report.evidencePacket.hashes.source}`,
          `runtime: ${report.evidencePacket.hashes.runtime}`,
          `serialized: ${report.evidencePacket.hashes.serialized}`
        ];
        if (report.warnings.length) {
          lines.push("", "WARNINGS", ...report.warnings.slice(0, 24).map((warning) => `- ${warning}`));
        }
        return lines.join("\n").trim();
      }

      return Object.freeze({
        runFullProof,
        buildQualityGate,
        compactText
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabSupervisor = McelLabSupervisor;
    }
