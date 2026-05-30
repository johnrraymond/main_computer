    var McelLabKernel = (() => {
      const moduleManifest = Object.freeze([
        {
          id: "contract",
          global: "McelLabContract",
          label: "Source Contract",
          owns: ["source attributes", "schemas", "block templates", "runtime-owned markers"],
          dependsOn: [],
          priorArt: ["Web Components", "htmx"],
          requirements: ["clean-source-contract", "element-schema"]
        },
        {
          id: "engine",
          global: "McelLabEngine",
          label: "Compiler / Serializer / Repair Engine",
          owns: ["compile", "serialize", "repair", "a11y", "debugger"],
          dependsOn: ["McelLabContract"],
          priorArt: ["Svelte", "WAI-ARIA APG", "XState"],
          requirements: ["runtime-dom", "serializer-firewall", "repair-pass", "a11y"]
        },
        {
          id: "editor",
          global: "McelLabEditor",
          label: "Semantic Editor Adapter",
          owns: ["selection", "traits", "block insertion", "save firewall"],
          dependsOn: ["McelLabContract", "McelLabEngine"],
          priorArt: ["GrapesJS", "ProseMirror", "Lexical"],
          requirements: ["grapesjs-semantic-editing", "editor-save-firewall"]
        },
        {
          id: "style-law",
          global: "McelLabStyleLaw",
          label: "CSS Law Runtime",
          owns: ["runtime tokens", "theme hooks", "visual law"],
          dependsOn: ["McelLabContract"],
          priorArt: ["Tailwind", "Design Tokens"],
          requirements: ["css-law", "theme-system"]
        },
        {
          id: "command-surface",
          global: "McelLabCommandSurface",
          label: "Semantic Command Surface",
          owns: ["deterministic command planning", "semantic operations"],
          dependsOn: ["McelLabContract", "McelLabEditor", "McelLabStyleLaw"],
          priorArt: ["Command palettes", "AI tool call planners"],
          requirements: ["ai-operability", "semantic-command-surface"]
        },
        {
          id: "project-store",
          global: "McelLabProjectStore",
          label: "Clean Project Store",
          owns: ["clean-source snapshots", "local persistence"],
          dependsOn: ["McelLabContract", "McelLabEditor"],
          priorArt: ["CMS save models", "CRDT/Yjs future track"],
          requirements: ["clean-source-persistence"]
        },
        {
          id: "scenarios",
          global: "McelLabScenarios",
          label: "Scenario Library",
          owns: ["built-in tests", "stress source fixtures"],
          dependsOn: ["McelLabContract"],
          priorArt: ["Storybook", "fixture-driven testing"],
          requirements: ["built-in-scenarios"]
        },
        {
          id: "graph",
          global: "McelLabGraph",
          label: "Semantic Graph / Provenance Audit",
          owns: ["source/runtime graph", "provenance", "operational audit"],
          dependsOn: ["McelLabContract", "McelLabEngine"],
          priorArt: ["AST graphs", "DevTools DOM inspectors"],
          requirements: ["semantic-graph", "provenance"]
        },
        {
          id: "ops-runner",
          global: "McelLabOpsRunner",
          label: "Operational Runner",
          owns: ["scenario matrix", "evidence packet", "readiness"],
          dependsOn: ["McelLabContract", "McelLabEngine", "McelLabStyleLaw", "McelLabGraph"],
          priorArt: ["CI pipelines", "golden round-trip tests"],
          requirements: ["ci-like-evidence"]
        },
        {
          id: "acid-tests",
          global: "McelLabAcidTests",
          label: "Acid Test Runner",
          owns: ["hostile runtime tests", "serializer torture", "editor save-firewall stress", "command fuzzing"],
          dependsOn: ["McelLabContract", "McelLabEngine", "McelLabEditor", "McelLabStyleLaw", "McelLabScenarios", "McelLabCommandSurface", "McelLabGraph", "McelLabOpsRunner"],
          priorArt: ["fuzz testing", "property-based testing", "chaos engineering"],
          requirements: ["acid-tests", "zero-debt-governance"]
        },
        {
          id: "test-harness",
          global: "McelLabTestHarness",
          label: "Browser Contract Harness",
          owns: ["contract test suite", "regression probes"],
          dependsOn: ["McelLabContract", "McelLabEngine", "McelLabEditor", "McelLabAcidTests"],
          priorArt: ["unit tests", "integration harnesses"],
          requirements: ["contract-tests", "acid-tests"]
        },
        {
          id: "supervisor",
          global: "McelLabSupervisor",
          label: "Autopilot Supervisor",
          owns: ["full proof", "quality gate", "supervised readiness"],
          dependsOn: ["McelLabOpsRunner", "McelLabTestHarness", "McelLabAcidTests"],
          priorArt: ["release gates", "supervisory control loops"],
          requirements: ["autopilot-proof"]
        },
        {
          id: "kernel",
          global: "McelLabKernel",
          label: "Kernel / Traceability / Zero-Debt Ledger",
          owns: ["module registry", "requirement traceability", "prior-art map", "debt gates"],
          dependsOn: ["McelLabContract"],
          priorArt: ["architecture decision records", "dependency graphs"],
          requirements: ["traceability", "zero-debt-governance"]
        }
      ]);

      const priorArtMatrix = Object.freeze([
        {
          system: "React",
          difficulty: "component composition",
          lesson: "UIs can be assembled from reusable, nestable units.",
          mcelContract: "Keep reusable component thinking, but use clean semantic HTML as canonical source."
        },
        {
          system: "Web Components",
          difficulty: "native browser components",
          lesson: "Custom elements, templates, slots, and DOM discipline are browser-native.",
          mcelContract: "Borrow DOM discipline while keeping generated parts inspectable and serializer-removable."
        },
        {
          system: "Svelte",
          difficulty: "compiler-driven UI",
          lesson: "A compiler can turn compact source into efficient runtime behavior.",
          mcelContract: "Compile semantic HTML source into deterministic runtime DOM."
        },
        {
          system: "htmx",
          difficulty: "attribute-powered behavior",
          lesson: "HTML attributes can activate substantial behavior.",
          mcelContract: "Use data-mc attributes as the semantic control surface for runtime intelligence."
        },
        {
          system: "GrapesJS",
          difficulty: "visual editing",
          lesson: "Blocks and traits can expose editable component parameters.",
          mcelContract: "Traits edit semantic source attributes; generated runtime parts never become saved source."
        },
        {
          system: "ProseMirror / Lexical",
          difficulty: "structured rich editing",
          lesson: "Schema and transactions prevent WYSIWYG source corruption.",
          mcelContract: "Source edits pass through schema-aware normalization and a save firewall."
        },
        {
          system: "Tailwind / Design Tokens",
          difficulty: "scalable styling",
          lesson: "Constrained style primitives scale across a system.",
          mcelContract: "CSS law derives runtime tokens from semantic state without utility cluttering source."
        },
        {
          system: "WAI-ARIA APG",
          difficulty: "accessible complex UI",
          lesson: "Complex widgets need explicit labels, roles, order, and hidden decoration rules.",
          mcelContract: "A11y is checked during the compile/serialize proof, not bolted on afterward."
        },
        {
          system: "XState / statecharts",
          difficulty: "explicit lifecycle correctness",
          lesson: "State machines make impossible or missing states visible.",
          mcelContract: "Compiler, repair, serializer, and supervisor stages expose explicit quality gates."
        },
        {
          system: "Yjs / CRDTs",
          difficulty: "durable collaborative source",
          lesson: "Shared state can be merged without one client corrupting another.",
          mcelContract: "Future collaboration should sync clean semantic source, never runtime-generated DOM."
        }
      ]);

      const requirementLedger = Object.freeze([
        {id: "clean-source-contract", label: "Clean semantic source remains canonical", owners: ["contract", "editor", "engine"], evidence: ["defaultSource", "runtimeOwnedAttributes", "canonicalSource"]},
        {id: "runtime-dom", label: "Compiler creates generated runtime DOM", owners: ["engine"], evidence: ["compileSource", "data-mc-generated", "data-mc-enhanced"]},
        {id: "css-law", label: "CSS law derives tokens from semantic state", owners: ["style-law"], evidence: ["applyRuntimeLaw", "data-mc-style-law"]},
        {id: "grapesjs-semantic-editing", label: "Editor manipulates semantic traits", owners: ["editor"], evidence: ["applyTraits", "insertBlock", "sanitizeEditorHtml"]},
        {id: "serializer-firewall", label: "Serializer strips generated parts", owners: ["engine", "editor"], evidence: ["serializeRuntimeRoot", "data-mc-generated"]},
        {id: "repair-pass", label: "Repair restores disposable generated parts", owners: ["engine"], evidence: ["repairRuntimeRoot", "generatedPartsCanonical"]},
        {id: "a11y", label: "Generated decoration remains accessibility-safe", owners: ["engine"], evidence: ["computeA11y", "aria-hidden"]},
        {id: "semantic-graph", label: "Source/runtime graph and provenance are inspectable", owners: ["graph"], evidence: ["graphFromRuntime", "data-mc-owner", "operational-audit"]},
        {id: "ci-like-evidence", label: "Scenario matrix and evidence packet prove readiness", owners: ["ops-runner", "test-harness"], evidence: ["runScenarioMatrix", "buildEvidencePacket"]},
        {id: "acid-tests", label: "Hostile stress tests prove serializer/editor/runtime resilience", owners: ["acid-tests", "test-harness", "supervisor"], evidence: ["runAll", "hostile runtime pollution", "command fuzzing"]},
        {id: "autopilot-proof", label: "Supervisor combines all proof surfaces", owners: ["supervisor"], evidence: ["runFullProof", "buildQualityGate"]},
        {id: "traceability", label: "Prior art and requirements map to owned modules", owners: ["kernel"], evidence: ["buildTraceabilityMap", "priorArtMatrix"]},
        {id: "zero-debt-governance", label: "Debt gates flag missing owners, modules, or unsafe source", owners: ["kernel"], evidence: ["runKernelAudit", "debtLedger"]}
      ]);

      function now() {
        return new Date().toISOString();
      }

      function getGlobal(name) {
        if (typeof window === "undefined") return null;
        return window[name] || null;
      }

      function contract() {
        return getGlobal("McelLabContract");
      }

      function engine() {
        return getGlobal("McelLabEngine");
      }

      function editor() {
        return getGlobal("McelLabEditor");
      }

      function styleLaw() {
        return getGlobal("McelLabStyleLaw");
      }

      function graph() {
        return getGlobal("McelLabGraph");
      }

      function opsRunner() {
        return getGlobal("McelLabOpsRunner");
      }

      function runtimeRoot(html) {
        const root = document.createElement("div");
        root.innerHTML = String(html || "");
        return root;
      }

      function moduleStatus() {
        return moduleManifest.map((item) => {
          const loaded = Boolean(getGlobal(item.global));
          const missingDependencies = item.dependsOn.filter((dependency) => !getGlobal(dependency));
          return {
            ...item,
            loaded,
            missingDependencies,
            status: loaded && missingDependencies.length === 0 ? "ready" : "blocked"
          };
        });
      }

      function buildTraceabilityMap(options = {}) {
        const modules = moduleStatus();
        const moduleById = Object.fromEntries(modules.map((item) => [item.id, item]));
        const requirements = requirementLedger.map((requirement) => {
          const owners = requirement.owners.map((owner) => moduleById[owner]).filter(Boolean);
          const missingOwners = requirement.owners.filter((owner) => !moduleById[owner] || !moduleById[owner].loaded);
          const priorArt = [...new Set(owners.flatMap((owner) => owner.priorArt || []))];
          return {
            ...requirement,
            status: missingOwners.length ? "blocked" : "covered",
            ownerLabels: owners.map((owner) => owner.label),
            missingOwners,
            priorArt
          };
        });
        const covered = requirements.filter((item) => item.status === "covered").length;
        const blocked = requirements.length - covered;
        return {
          kind: "mcel-kernel-traceability-map",
          contractVersion: contract()?.contractVersion || "unknown",
          generatedAt: now(),
          reason: options.reason || "traceability",
          status: blocked ? "blocked" : "covered",
          coverage: Number((covered / requirements.length).toFixed(3)),
          covered,
          blocked,
          total: requirements.length,
          modules,
          requirements,
          priorArt: priorArtMatrix
        };
      }

      function smartCount(root) {
        const attrs = contract()?.attributes;
        return attrs ? (root?.querySelectorAll?.(`[${attrs.type}]`)?.length || 0) : 0;
      }

      function generatedCount(root) {
        const attrs = contract()?.attributes;
        return attrs ? (root?.querySelectorAll?.(`[${attrs.generated}="true"]`)?.length || 0) : 0;
      }

      function hasRuntimeLeak(source) {
        const attrs = contract()?.attributes || {};
        return [
          attrs.generated,
          attrs.enhanced,
          attrs.sourceIndex,
          attrs.artifactOwner,
          attrs.artifactOrigin,
          attrs.artifactReason,
          attrs.contractVersion,
          attrs.styleLaw
        ].filter(Boolean).some((attribute) => String(source || "").includes(attribute));
      }

      function runKernelAudit(options = {}) {
        const c = contract();
        const e = engine();
        const ed = editor();
        const sl = styleLaw();
        const gr = graph();
        const ops = opsRunner();
        const source = ed?.canonicalSource?.(options.source || c?.defaultSource || "") || String(options.source || c?.defaultSource || "");
        let root = options.runtimeRoot || null;
        let compiled = null;
        if (!root && e) {
          compiled = e.compileSource(source, {reason: options.reason || "kernel-audit"});
          root = runtimeRoot(compiled.runtimeHtml);
          sl?.applyRuntimeLaw?.(root, {theme: options.theme || "theme-machine", reason: "kernel-audit"});
        }
        const serializer = e?.serializeRuntimeRoot?.(root, {reason: "kernel-audit"}) || {serialized: source, report: {serializerClean: !hasRuntimeLeak(source), warnings: []}};
        const traceability = buildTraceabilityMap({reason: options.reason || "kernel-audit"});
        const audit = gr?.audit?.(source, root, {reason: "kernel-audit"}) || null;
        const readiness = ops?.buildReadiness?.({
          serializerReport: serializer.report,
          cssLawReport: sl?.reportFor?.(root, {theme: options.theme || "theme-machine", reason: "kernel-audit"}),
          a11yReport: e?.computeA11y?.(root),
          auditReport: audit,
          testReport: options.testReport || null,
          matrixReport: options.matrixReport || null,
          acidReport: options.acidReport || null,
          kernelReport: null
        }) || null;

        const modules = moduleStatus();
        const gates = [
          {
            key: "modules-loaded",
            label: "Modules Loaded",
            status: modules.every((item) => item.loaded) ? "pass" : "fail",
            detail: `${modules.filter((item) => item.loaded).length}/${modules.length} kernel modules loaded`
          },
          {
            key: "dependencies",
            label: "Module Dependencies",
            status: modules.every((item) => item.missingDependencies.length === 0) ? "pass" : "fail",
            detail: modules.flatMap((item) => item.missingDependencies.map((dependency) => `${item.id} missing ${dependency}`)).join("; ") || "all declared dependencies are present"
          },
          {
            key: "traceability",
            label: "Requirement Traceability",
            status: traceability.status === "covered" ? "pass" : "fail",
            detail: `${traceability.covered}/${traceability.total} requirements covered`
          },
          {
            key: "prior-art",
            label: "Prior-Art Map",
            status: priorArtMatrix.length >= 8 ? "pass" : "fail",
            detail: `${priorArtMatrix.length} reference systems mapped to MCEL contracts`
          },
          {
            key: "source-clean",
            label: "Canonical Source Clean",
            status: !hasRuntimeLeak(source) ? "pass" : "fail",
            detail: !hasRuntimeLeak(source) ? "source has no runtime-owned attributes" : "source contains runtime-owned attributes"
          },
          {
            key: "serializer-clean",
            label: "Serializer Firewall",
            status: serializer.report?.serializerClean && !hasRuntimeLeak(serializer.serialized) ? "pass" : "fail",
            detail: serializer.report?.serializerClean ? "serializer output is clean" : "serializer reported warnings"
          },
          {
            key: "acid-tests",
            label: "Acid Test Surface",
            status: getGlobal("McelLabAcidTests") && (!options.acidReport || !options.acidReport.failed) ? "pass" : "fail",
            detail: options.acidReport ? `${options.acidReport.passed}/${options.acidReport.total} acid test(s) passed` : "acid runner loaded; run suite for full pressure proof"
          },
          {
            key: "runtime-provenance",
            label: "Runtime Provenance",
            status: audit && !audit.failed ? "pass" : "fail",
            detail: audit ? (audit.failed ? audit.issues.join("; ") : `${audit.runtimeGraph?.generatedPartCount || generatedCount(root)} generated part(s) under provenance`) : "graph audit unavailable"
          },
          {
            key: "schema-surface",
            label: "Schema Surface",
            status: Object.keys(c?.schema || {}).length >= 5 ? "pass" : "fail",
            detail: `${Object.keys(c?.schema || {}).length} registered element type(s)`
          }
        ];
        const passCount = gates.filter((gate) => gate.status === "pass").length;
        const failCount = gates.length - passCount;
        return {
          kind: "mcel-kernel-audit",
          contractVersion: c?.contractVersion || "unknown",
          generatedAt: now(),
          reason: options.reason || "kernel-audit",
          status: failCount ? "blocked" : "ready",
          score: Number((passCount / gates.length).toFixed(3)),
          passCount,
          failCount,
          total: gates.length,
          sourceElementCount: e ? smartCount(e.parseSource(source)) : 0,
          runtimeElementCount: smartCount(root),
          generatedPartCount: generatedCount(root),
          moduleGraph: modules,
          traceability,
          readiness,
          compiledEvents: compiled?.events || [],
          debtLedger: gates,
          warnings: gates.filter((gate) => gate.status !== "pass").map((gate) => `${gate.label}: ${gate.detail}`)
        };
      }

      function compactAuditText(report) {
        if (!report) return "Kernel audit has not run yet.";
        const lines = [
          "MCEL KERNEL AUDIT",
          `status: ${report.status}`,
          `score: ${report.score}`,
          `contractVersion: ${report.contractVersion}`,
          `generatedAt: ${report.generatedAt}`,
          `reason: ${report.reason}`,
          "",
          "COUNTS",
          `source elements: ${report.sourceElementCount}`,
          `runtime elements: ${report.runtimeElementCount}`,
          `generated parts: ${report.generatedPartCount}`,
          "",
          "DEBT GATES",
          ...report.debtLedger.map((gate) => `${gate.status === "pass" ? "PASS" : "FAIL"} ${gate.label} — ${gate.detail}`),
          "",
          "MODULES",
          ...report.moduleGraph.map((module) => `${module.status === "ready" ? "READY" : "BLOCKED"} ${module.id} -> ${module.global}`)
        ];
        if (report.warnings.length) {
          lines.push("", "WARNINGS", ...report.warnings.slice(0, 18).map((warning) => `- ${warning}`));
        }
        return lines.join("\n").trim();
      }

      function compactTraceabilityText(map) {
        if (!map) return "Traceability map has not been built yet.";
        const lines = [
          "MCEL REQUIREMENT TRACEABILITY",
          `status: ${map.status}`,
          `coverage: ${map.covered}/${map.total} · ${map.coverage}`,
          `contractVersion: ${map.contractVersion}`,
          `generatedAt: ${map.generatedAt}`,
          "",
          "REQUIREMENTS"
        ];
        map.requirements.forEach((requirement) => {
          lines.push(`${requirement.status === "covered" ? "COVERED" : "BLOCKED"} ${requirement.id}`);
          lines.push(`  ${requirement.label}`);
          lines.push(`  owners: ${requirement.ownerLabels.join(", ") || requirement.owners.join(", ")}`);
          lines.push(`  prior art: ${requirement.priorArt.join(", ") || "not mapped"}`);
        });
        return lines.join("\n").trim();
      }

      function priorArtText() {
        const lines = [
          "MCEL PRIOR ART / DIFFICULTY RESOLUTION",
          "Each reference proves one hard system problem; MCEL keeps the lesson but tightens the source/runtime/editor/serializer contract.",
          ""
        ];
        priorArtMatrix.forEach((item, index) => {
          lines.push(`${index + 1}. ${item.system}`);
          lines.push(`   difficulty: ${item.difficulty}`);
          lines.push(`   lesson: ${item.lesson}`);
          lines.push(`   MCEL contract: ${item.mcelContract}`);
        });
        return lines.join("\n").trim();
      }

      return Object.freeze({
        moduleManifest,
        priorArtMatrix,
        requirementLedger,
        moduleStatus,
        buildTraceabilityMap,
        runKernelAudit,
        compactAuditText,
        compactTraceabilityText,
        priorArtText
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabKernel = McelLabKernel;
    }

