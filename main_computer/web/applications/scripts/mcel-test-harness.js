    const McelLabTestHarness = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const scenarios = typeof McelLabScenarios !== "undefined" ? McelLabScenarios : window.McelLabScenarios;
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
          connects: ""
        });
        const traits = editor.readTraits(traitSource.source, {index: 1});
        record(
          results,
          "selection-aware traits update selected widget",
          traits.kind === "work" && traits.state === "warning" && editor.readTraits(traitSource.source, {index: 0}).kind === "signal",
          `selected index ${traits.index + 1}`,
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
