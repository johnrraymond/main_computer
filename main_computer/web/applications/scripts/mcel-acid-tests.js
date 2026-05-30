    var McelLabAcidTests = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const styleLaw = typeof McelLabStyleLaw !== "undefined" ? McelLabStyleLaw : window.McelLabStyleLaw;
      const scenarios = typeof McelLabScenarios !== "undefined" ? McelLabScenarios : window.McelLabScenarios;
      const commandSurface = typeof McelLabCommandSurface !== "undefined" ? McelLabCommandSurface : window.McelLabCommandSurface;
      const graph = typeof McelLabGraph !== "undefined" ? McelLabGraph : window.McelLabGraph;
      const opsRunner = typeof McelLabOpsRunner !== "undefined" ? McelLabOpsRunner : window.McelLabOpsRunner;
      const {attributes, runtimeOwnedAttributes, contractVersion} = contract;

      const runtimeLeakPatterns = [
        attributes.generated,
        attributes.enhanced,
        attributes.sourceIndex,
        attributes.artifactOwner,
        attributes.artifactOrigin,
        attributes.artifactReason,
        attributes.contractVersion,
        attributes.computedDensity,
        attributes.neighborhood,
        attributes.clusterSize,
        attributes.relation,
        attributes.relationCount
      ].filter(Boolean);

      function now() {
        return new Date().toISOString();
      }

      function runtimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = String(html || "");
        return root;
      }

      function canonical(source) {
        return editor?.canonicalSource?.(source || contract.defaultSource) || String(source || contract.defaultSource);
      }

      function smartCount(rootOrSource) {
        if (typeof rootOrSource === "string") return engine.parseSource(rootOrSource).querySelectorAll(`[${attributes.type}]`).length;
        return rootOrSource?.querySelectorAll?.(`[${attributes.type}]`)?.length || 0;
      }

      function generatedCount(root) {
        return root?.querySelectorAll?.(`[${attributes.generated}="true"]`)?.length || 0;
      }

      function hasRuntimeLeak(source) {
        const text = String(source || "");
        return runtimeLeakPatterns.some((pattern) => text.includes(pattern)) ||
          /\bstyle=|\bclass="[^"]*(?:\bmc\b|mc-)/.test(text);
      }

      function hashText(input) {
        if (opsRunner?.hashText) return opsRunner.hashText(input);
        const text = String(input || "");
        let hash = 2166136261;
        for (let index = 0; index < text.length; index += 1) {
          hash ^= text.charCodeAt(index);
          hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
        }
        return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
      }

      function pass(name, details = "", metrics = {}) {
        return {name, passed: true, details, metrics};
      }

      function fail(name, details = "", metrics = {}) {
        return {name, passed: false, details, metrics};
      }

      function runCase(name, callback) {
        try {
          const result = callback();
          return result?.passed === false ? result : pass(name, result?.details || "", result?.metrics || {});
        } catch (error) {
          return fail(name, error?.message || String(error));
        }
      }

      function compileClean(source, reason) {
        const clean = canonical(source);
        const compiled = engine.compileSource(clean, {reason});
        const root = runtimeRoot(compiled.runtimeHtml);
        return {clean, compiled, root};
      }

      function serializeClean(root, reason) {
        const serialized = engine.serializeRuntimeRoot(root, {reason});
        return {
          ...serialized,
          leakFree: serialized.report.serializerClean && !hasRuntimeLeak(serialized.serialized)
        };
      }

      function acidRuntimePollutionFirewall(source) {
        const {root} = compileClean(source, "acid-runtime-pollution");
        root.querySelectorAll(`[${attributes.type}]`).forEach((element, index) => {
          element.setAttribute(attributes.enhanced, "corrupt");
          element.setAttribute(attributes.artifactOwner, "hostile-runtime");
          element.setAttribute(attributes.artifactOrigin, "tampered");
          element.setAttribute(attributes.artifactReason, "acid:pollution");
          element.setAttribute(attributes.contractVersion, "hostile-contract");
          element.setAttribute(attributes.sourceIndex, String(999 + index));
          element.classList.add("mc", "mc-hostile", "mc-panel");
          element.style.setProperty("--mc-corrupt", "true");
          const forged = element.ownerDocument.createElement("div");
          forged.setAttribute(attributes.generated, "true");
          forged.setAttribute(attributes.part, "forged");
          forged.setAttribute(attributes.artifactOwner, "hostile");
          forged.textContent = "FORGED GENERATED PAYLOAD SHOULD VANISH";
          element.insertBefore(forged, element.firstChild);
        });
        const serialized = serializeClean(root, "acid-runtime-pollution");
        const preserved = smartCount(serialized.serialized) === smartCount(source);
        const forgedGone = !serialized.serialized.includes("FORGED GENERATED PAYLOAD");
        return {
          passed: serialized.leakFree && preserved && forgedGone,
          details: `${serialized.report.removedGeneratedParts} generated part(s) removed; preserved=${preserved}; leakFree=${serialized.leakFree}`,
          metrics: {removedGeneratedParts: serialized.report.removedGeneratedParts, preservedSourceElements: serialized.report.preservedSourceElements}
        };
      }

      function acidCatastrophicRepair(source) {
        const {root} = compileClean(source, "acid-catastrophic-repair");
        const beforeGenerated = generatedCount(root);
        root.querySelectorAll(`[${attributes.generated}="true"]`).forEach((node) => node.remove());
        root.querySelectorAll(`[${attributes.type}]`).forEach((element) => {
          const nested = element.ownerDocument.createElement("div");
          nested.setAttribute(attributes.generated, "true");
          nested.setAttribute(attributes.part, "stale-nested");
          nested.textContent = "STALE NESTED RUNTIME PART";
          element.appendChild(nested);
        });
        const repair = engine.repairRuntimeRoot(root, {reason: "acid-catastrophic-repair"});
        const states = [...root.querySelectorAll(`[${attributes.type}]`)].map((element) => engine.debuggerStateFor(element, root));
        const canonicalParts = states.every((state) => state.generatedPartsCanonical && !state.missingParts.length);
        const serialized = serializeClean(root, "acid-catastrophic-repair");
        return {
          passed: repair.repaired === states.length && canonicalParts && serialized.leakFree && !serialized.serialized.includes("STALE NESTED"),
          details: `removed ${beforeGenerated} generated part(s), repaired ${repair.repaired}/${states.length} source element(s)`,
          metrics: {beforeGenerated, repaired: repair.repaired, sourceElements: states.length}
        };
      }

      function acidSerializerIdempotence(source) {
        const first = compileClean(source, "acid-idempotence:first");
        const firstSerialized = serializeClean(first.root, "acid-idempotence:first");
        const second = compileClean(firstSerialized.serialized, "acid-idempotence:second");
        const secondSerialized = serializeClean(second.root, "acid-idempotence:second");
        const firstCanonical = canonical(firstSerialized.serialized);
        const secondCanonical = canonical(secondSerialized.serialized);
        return {
          passed: firstSerialized.leakFree && secondSerialized.leakFree && firstCanonical === secondCanonical,
          details: `first=${hashText(firstCanonical)} second=${hashText(secondCanonical)}`,
          metrics: {firstHash: hashText(firstCanonical), secondHash: hashText(secondCanonical)}
        };
      }

      function acidEditorSaveFirewall(source) {
        const {root} = compileClean(source, "acid-editor-firewall");
        const hostileEditorHtml = `${root.innerHTML}<div data-mc-generated="true" data-mc-part="outside">BAD OUTSIDE GENERATED NODE</div>`;
        const sanitized = editor.sanitizeEditorHtml(hostileEditorHtml);
        const preserved = smartCount(sanitized) === smartCount(source);
        const generatedGone = !sanitized.includes("BAD OUTSIDE GENERATED NODE");
        const leakFree = !hasRuntimeLeak(sanitized);
        return {
          passed: preserved && generatedGone && leakFree,
          details: `sanitized ${hostileEditorHtml.length} chars to ${sanitized.length}; preserved=${preserved}; leakFree=${leakFree}`,
          metrics: {inputLength: hostileEditorHtml.length, outputLength: sanitized.length}
        };
      }

      function acidCommandFuzz(source) {
        const commands = [
          "set flow reverse; set state live; serialize",
          "theme debug; set density dense; repair",
          "insert proof",
          "set kind proof; graph audit",
          "matrix evidence serialize",
          "kernel traceability prior-art"
        ];
        let currentSource = canonical(source);
        let selectedIndex = 0;
        let theme = "theme-machine";
        const summaries = [];
        commands.forEach((text) => {
          const plan = commandSurface.plan(text, {source: currentSource, selectedIndex, theme});
          if (!plan.ok) throw new Error(`Command did not plan: ${text}`);
          const applied = commandSurface.apply(plan, {source: currentSource, selectedIndex, theme});
          currentSource = applied.source;
          selectedIndex = applied.selectedIndex;
          theme = applied.theme;
          summaries.push(plan.summary.join("; "));
        });
        const {root} = compileClean(currentSource, "acid-command-fuzz");
        const serialized = serializeClean(root, "acid-command-fuzz");
        return {
          passed: serialized.leakFree && smartCount(serialized.serialized) >= smartCount(source),
          details: `${commands.length} command(s), ${smartCount(serialized.serialized)} clean source widget(s), theme=${theme}`,
          metrics: {commands: commands.length, widgets: smartCount(serialized.serialized), summaries}
        };
      }

      function acidNestedDumbDom(source) {
        const nested = `<article class="ordinary-shell">
  <header><h1>Ordinary Host Document</h1></header>
  <div class="cms-body">
    ${source}
  </div>
  <footer><p>Ordinary footer must survive.</p></footer>
</article>`;
        const {root} = compileClean(nested, "acid-nested-dumb-dom");
        const serialized = serializeClean(root, "acid-nested-dumb-dom");
        const ordinarySurvived = serialized.serialized.includes("Ordinary footer must survive.") &&
          serialized.serialized.includes("ordinary-shell");
        return {
          passed: serialized.leakFree && ordinarySurvived && smartCount(serialized.serialized) === smartCount(nested),
          details: `ordinary DOM survived=${ordinarySurvived}; source widgets=${smartCount(serialized.serialized)}`,
          metrics: {sourceElements: smartCount(serialized.serialized)}
        };
      }

      function acidRelationMutation(source) {
        const relationSource = `<section id="alpha" data-mc="panel" data-mc-kind="signal" data-mc-connects="beta missing-target"><h2>Alpha</h2><p>Partial relation should be tracked.</p></section>
<section id="beta" data-mc="panel" data-mc-kind="work"><h2>Beta</h2><p>Resolved target.</p></section>
<section id="gamma" data-mc="panel" data-mc-kind="proof" data-mc-connects="alpha beta"><h2>Gamma</h2><p>Fully resolved relation should be tracked.</p></section>`;
        const {root} = compileClean(relationSource, "acid-relation-mutation");
        const alpha = root.querySelector("#alpha");
        const gamma = root.querySelector("#gamma");
        const before = [alpha?.getAttribute(attributes.relation), gamma?.getAttribute(attributes.relation)].join("/");
        alpha?.setAttribute(attributes.connects, "beta gamma");
        const repairedSource = serializeClean(root, "acid-relation-mutation:serialize");
        const recompiled = compileClean(repairedSource.serialized, "acid-relation-mutation:recompile");
        const nextAlpha = recompiled.root.querySelector("#alpha");
        const after = nextAlpha?.getAttribute(attributes.relation);
        return {
          passed: repairedSource.leakFree && before === "partial/resolved" && after === "resolved",
          details: `before=${before}; after=${after}`,
          metrics: {before, after}
        };
      }

      function acidSchemaFuzz() {
        const fuzzSource = `<section data-mc="nonsense" data-mc-kind="volatile" data-mc-flow="sideways" data-mc-rank="screaming" data-mc-state="exploding" data-mc-density="impossible"><h2>Malformed One</h2><p>Should normalize.</p></section>
<section data-mc="panel" data-mc-kind="" data-mc-flow="reverse" data-mc-words="zero debt &amp; stress &lt;safe&gt;"><h2>Malformed Two</h2><p>Special characters must survive safely.</p></section>`;
        const {root, compiled} = compileClean(fuzzSource, "acid-schema-fuzz");
        const serialized = serializeClean(root, "acid-schema-fuzz");
        const warnings = compiled.events.filter((event) => event.level === "warning").length;
        const first = root.querySelector(`[${attributes.type}]`);
        return {
          passed: serialized.leakFree &&
            warnings >= 1 &&
            first?.getAttribute(attributes.type) === contract.defaults.type &&
            serialized.serialized.includes("zero debt"),
          details: `${warnings} schema warning(s); first type=${first?.getAttribute(attributes.type)}`,
          metrics: {warnings}
        };
      }

      function acidScenarioThemeSoak(source) {
        const themeList = contract.themes || ["theme-machine"];
        const scenarioList = scenarios.all();
        let passed = 0;
        let failed = 0;
        const failures = [];
        scenarioList.forEach((scenario) => {
          themeList.forEach((theme) => {
            try {
              const {root} = compileClean(scenario.source, `acid-soak:${scenario.id}:${theme}`);
              const css = styleLaw.reportFor(root, {theme, reason: `acid-soak:${scenario.id}`});
              styleLaw.applyRuntimeLaw(root, {theme, reason: `acid-soak:${scenario.id}`});
              const serialized = serializeClean(root, `acid-soak:${scenario.id}:${theme}`);
              const a11y = engine.computeA11y(root);
              const ok = serialized.leakFree && css.cssLawClean && a11y.a11yValid;
              if (ok) passed += 1;
              else {
                failed += 1;
                failures.push(`${scenario.id}/${theme}`);
              }
            } catch (error) {
              failed += 1;
              failures.push(`${scenario.id}/${theme}: ${error.message}`);
            }
          });
        });
        return {
          passed: failed === 0 && passed > 0,
          details: `${passed} passed / ${failed} failed across ${scenarioList.length} scenario(s) × ${themeList.length} theme(s)`,
          metrics: {passed, failed, caseCount: passed + failed, failures: failures.slice(0, 12)}
        };
      }

      function acidOperationalEvidence(source, state = {}) {
        const {root} = compileClean(source, "acid-evidence");
        styleLaw.applyRuntimeLaw(root, {theme: state.theme || "theme-machine", reason: "acid-evidence"});
        const audit = graph.audit(source, root, {reason: "acid-evidence"});
        const evidence = opsRunner.buildEvidencePacket({
          source,
          runtimeRoot: root,
          theme: state.theme || "theme-machine",
          includeTests: true,
          matrixReport: state.matrixReport || null,
          acidReport: state.acidReport || null,
          kernelReport: state.kernelReport || null
        });
        return {
          passed: !audit.failed &&
            evidence.serializer?.serializerClean &&
            evidence.audit?.passed &&
            evidence.testSuite?.failed === 0 &&
            evidence.hashes?.source &&
            evidence.hashes?.serialized,
          details: `auditFailed=${audit.failed}; tests=${evidence.testSuite?.passed || 0}/${(evidence.testSuite?.passed || 0) + (evidence.testSuite?.failed || 0)}; serialized=${evidence.hashes?.serialized}`,
          metrics: {auditFailed: audit.failed, sourceHash: evidence.hashes?.source, serializedHash: evidence.hashes?.serialized}
        };
      }

      const acidCaseDescriptors = Object.freeze([
        Object.freeze({id: "runtime-pollution-firewall", name: "runtime pollution firewall strips hostile generated/source-owned junk", severity: "critical", run: (source, options) => acidRuntimePollutionFirewall(source)}),
        Object.freeze({id: "catastrophic-generated-repair", name: "catastrophic repair restores canonical generated parts", severity: "critical", run: (source, options) => acidCatastrophicRepair(source)}),
        Object.freeze({id: "serializer-idempotence", name: "serializer is idempotent across compile/serialize cycles", severity: "critical", run: (source, options) => acidSerializerIdempotence(source)}),
        Object.freeze({id: "editor-save-firewall", name: "editor save firewall rejects runtime/generated DOM", severity: "critical", run: (source, options) => acidEditorSaveFirewall(source)}),
        Object.freeze({id: "semantic-command-fuzz", name: "semantic command fuzz keeps clean source contract", severity: "high", run: (source, options) => acidCommandFuzz(source)}),
        Object.freeze({id: "dumb-dom-survival", name: "smart widgets survive inside ordinary dumb DOM", severity: "high", run: (source, options) => acidNestedDumbDom(source)}),
        Object.freeze({id: "relation-mutation", name: "relation mutation recomputes without source corruption", severity: "high", run: (source, options) => acidRelationMutation(source)}),
        Object.freeze({id: "schema-fuzz-normalization", name: "schema fuzz normalizes malformed traits safely", severity: "high", run: (source, options) => acidSchemaFuzz()}),
        Object.freeze({id: "scenario-theme-soak", name: "scenario × theme soak preserves serializer/a11y/CSS law", severity: "system", run: (source, options) => acidScenarioThemeSoak(source)}),
        Object.freeze({id: "operational-evidence-integrity", name: "operational evidence packet remains machine-checkable", severity: "system", run: (source, options) => acidOperationalEvidence(source, options)})
      ]);

      function listCases() {
        return acidCaseDescriptors.map(({id, name, severity}) => ({id, name, severity}));
      }

      function caseById(id) {
        return acidCaseDescriptors.find((testCase) => testCase.id === id) || acidCaseDescriptors[0];
      }

      function normalizeCaseResult(testCase, source, options = {}) {
        return runCase(testCase.name, () => {
          const result = testCase.run(source, options);
          return result?.passed === false ? result : {
            passed: Boolean(result?.passed ?? true),
            details: result?.details || "",
            metrics: result?.metrics || {}
          };
        });
      }

      function buildReport(source, tests, options = {}) {
        const passed = tests.filter((test) => test.passed).length;
        const failed = tests.length - passed;
        return {
          kind: "mcel-acid-test-report",
          contractVersion,
          generatedAt: now(),
          reason: options.reason || "manual-acid-test",
          executionMode: options.executionMode || "selected",
          sourceHash: hashText(source),
          passed,
          failed,
          total: tests.length,
          status: failed ? "failed" : "passed",
          tests,
          summary: failed
            ? `${failed} acid test(s) failed; source/runtime/serializer contract is not safe enough.`
            : `${passed} acid test(s) passed; selected hostile path survived.`
        };
      }

      function runOne(caseId, options = {}) {
        const source = canonical(options.source || contract.defaultSource);
        const testCase = caseById(caseId);
        const result = {
          group: "acid",
          severity: testCase.severity,
          id: testCase.id,
          ...normalizeCaseResult(testCase, source, options)
        };
        return buildReport(source, [result], {...options, executionMode: "selected"});
      }

      function runAll(options = {}) {
        const source = canonical(options.source || contract.defaultSource);
        const tests = acidCaseDescriptors.map((testCase) => ({
          group: "acid",
          severity: testCase.severity,
          id: testCase.id,
          ...normalizeCaseResult(testCase, source, options)
        }));
        return buildReport(source, tests, {...options, executionMode: "suite"});
      }

      function compactText(report) {
        if (!report) return "Acid tests are manual-only. Select one test and run it from Diagnostics & Proofs.";
        const lines = [
          `MCEL ACID TESTS: ${report.status.toUpperCase()} — ${report.passed}/${report.total} passed`,
          `executionMode: ${report.executionMode || "selected"}`,
          `reason: ${report.reason || "manual"}`,
          `generatedAt: ${report.generatedAt}`,
          `sourceHash: ${report.sourceHash}`,
          "",
          ...report.tests.map((test) => `${test.passed ? "PASS" : "FAIL"} [${test.severity}] ${test.name}${test.details ? ` — ${test.details}` : ""}`)
        ];
        return lines.join("\n").trim();
      }

      return Object.freeze({
        listCases,
        runOne,
        runAll,
        compactText,
        hasRuntimeLeak
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabAcidTests = McelLabAcidTests;
    }

