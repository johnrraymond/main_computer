    var McelLabGraph = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const engine = typeof McelLabEngine !== "undefined" ? McelLabEngine : window.McelLabEngine;
      const editor = typeof McelLabEditor !== "undefined" ? McelLabEditor : window.McelLabEditor;
      const {attributes, runtimeOwnedAttributes, contractVersion} = contract;

      function parseSource(source) {
        return engine.parseSource(String(source || ""));
      }

      function rootFromRuntime(runtime) {
        if (typeof runtime === "string") {
          const root = document.createElement("div");
          root.innerHTML = runtime;
          return root;
        }
        return runtime;
      }

      function smartElements(root) {
        return [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function generatedParts(element) {
        return [...(element?.querySelectorAll?.(`:scope > [${attributes.generated}="true"]`) || [])]
          .map((part) => ({
            part: part.getAttribute(attributes.part) || "unknown",
            owner: part.getAttribute(attributes.artifactOwner) || "",
            origin: part.getAttribute(attributes.artifactOrigin) || "",
            reason: part.getAttribute(attributes.artifactReason) || "",
            contractVersion: part.getAttribute(attributes.contractVersion) || "",
            ariaHidden: part.getAttribute("aria-hidden") === "true"
          }));
      }

      function labelFor(element, index) {
        const heading = element.querySelector?.("h1,h2,h3,h4,h5,h6");
        return heading?.textContent?.trim() || element.id || `source-${index + 1}`;
      }

      function tokenList(value) {
        return String(value || "")
          .split(/[,\s]+/)
          .map((item) => item.trim().replace(/^#/, ""))
          .filter(Boolean);
      }

      function readNode(element, index, mode = "source") {
        return {
          id: element.id || element.getAttribute("data-mc-id") || `mcel-${index + 1}`,
          index,
          label: labelFor(element, index),
          type: element.getAttribute(attributes.type) || "panel",
          kind: element.getAttribute(attributes.kind) || "",
          flow: element.getAttribute(attributes.flow) || "",
          rank: element.getAttribute(attributes.rank) || "",
          state: element.getAttribute(attributes.state) || "",
          density: element.getAttribute(attributes.density) || "",
          sizePolicy: element.getAttribute(attributes.sizePolicy) || "",
          overflowPolicy: element.getAttribute(attributes.overflowPolicy) || "",
          scrollPolicy: element.getAttribute(attributes.scrollPolicy) || "",
          computedDensity: element.getAttribute(attributes.computedDensity) || "",
          layoutPressure: element.getAttribute(attributes.layoutPressure) || "",
          scrollOwner: element.getAttribute(attributes.scrollOwner) || "",
          overflowComputed: element.getAttribute(attributes.overflowComputed) || "",
          geometryProof: element.getAttribute(attributes.geometryProof) || "",
          componentName: element.getAttribute(attributes.componentName) || "",
          componentKind: element.getAttribute(attributes.componentKind) || "",
          stateOwner: element.getAttribute(attributes.stateOwner) || "",
          stateScope: element.getAttribute(attributes.stateScope) || "",
          statePolicy: element.getAttribute(attributes.statePolicy) || "",
          query: element.getAttribute(attributes.query) || "",
          cachePolicy: element.getAttribute(attributes.cachePolicy) || "",
          mutation: element.getAttribute(attributes.mutation) || "",
          syncPolicy: element.getAttribute(attributes.syncPolicy) || "",
          submit: element.getAttribute(attributes.submit) || "",
          validation: element.getAttribute(attributes.validation) || "",
          action: element.getAttribute(attributes.action) || "",
          target: element.getAttribute(attributes.target) || "",
          swapPolicy: element.getAttribute(attributes.swapPolicy) || "",
          eventPolicy: element.getAttribute(attributes.eventPolicy) || "",
          route: element.getAttribute(attributes.route) || "",
          renderMode: element.getAttribute(attributes.renderMode) || "",
          hydration: element.getAttribute(attributes.hydration) || "",
          islandPolicy: element.getAttribute(attributes.islandPolicy) || "",
          focusPolicy: element.getAttribute(attributes.focusPolicy) || "",
          a11yPolicy: element.getAttribute(attributes.a11yPolicy) || "",
          performanceBudget: element.getAttribute(attributes.performanceBudget) || "",
          securityPolicy: element.getAttribute(attributes.securityPolicy) || "",
          platformProofed: element.getAttribute(attributes.proofTier) === "platform-spine",
          semanticRisk: element.getAttribute(attributes.semanticRisk) || "",
          neighborhood: element.getAttribute(attributes.neighborhood) || "",
          clusterSize: Number(element.getAttribute(attributes.clusterSize) || "1"),
          relation: element.getAttribute(attributes.relation) || "",
          relationCount: Number(element.getAttribute(attributes.relationCount) || "0"),
          connects: tokenList(element.getAttribute(attributes.connects)),
          words: tokenList(element.getAttribute(attributes.words)),
          owner: mode === "runtime" ? element.getAttribute(attributes.artifactOwner) || "" : "author",
          origin: mode === "runtime" ? element.getAttribute(attributes.artifactOrigin) || "" : "source",
          reason: mode === "runtime" ? element.getAttribute(attributes.artifactReason) || "semantic source" : "authored source",
          contractVersion: element.getAttribute(attributes.contractVersion) || (mode === "runtime" ? contractVersion : ""),
          generatedParts: mode === "runtime" ? generatedParts(element) : []
        };
      }

      function relationEdges(nodes) {
        const byId = new Map();
        nodes.forEach((node) => {
          byId.set(node.id, node);
          byId.set(String(node.index), node);
          byId.set(`source-${node.index}`, node);
        });
        return nodes.flatMap((node) => node.connects.map((target) => {
          const resolved = byId.get(target) || null;
          return {
            from: node.id,
            to: target,
            resolved: Boolean(resolved),
            targetIndex: resolved ? resolved.index : -1,
            status: resolved ? "resolved" : "pending"
          };
        }));
      }

      function clusterSummary(nodes) {
        return nodes.reduce((summary, node) => {
          const key = node.neighborhood || "source";
          summary[key] = (summary[key] || 0) + 1;
          return summary;
        }, {});
      }

      function graphFromSource(source) {
        const doc = parseSource(editor?.canonicalSource ? editor.canonicalSource(source) : source);
        const nodes = smartElements(doc.body).map((element, index) => readNode(element, index, "source"));
        const edges = relationEdges(nodes);
        return {
          mode: "source",
          contractVersion,
          nodeCount: nodes.length,
          edgeCount: edges.length,
          unresolvedEdgeCount: edges.filter((edge) => !edge.resolved).length,
          nodes,
          edges,
          clusters: clusterSummary(nodes)
        };
      }

      function graphFromRuntime(runtime) {
        const root = rootFromRuntime(runtime);
        const nodes = smartElements(root).map((element, index) => readNode(element, index, "runtime"));
        const edges = relationEdges(nodes);
        const generated = nodes.reduce((count, node) => count + node.generatedParts.length, 0);
        const orphanGeneratedParts = [...(root?.querySelectorAll?.(`[${attributes.generated}="true"]`) || [])]
          .filter((part) => !part.closest(`[${attributes.type}]`)).length;
        return {
          mode: "runtime",
          contractVersion,
          nodeCount: nodes.length,
          edgeCount: edges.length,
          unresolvedEdgeCount: edges.filter((edge) => !edge.resolved).length,
          generatedPartCount: generated,
          orphanGeneratedParts,
          nodes,
          edges,
          clusters: clusterSummary(nodes)
        };
      }

      function hasRuntimeAttributeLeakage(source) {
        const doc = parseSource(source);
        const leakage = [];
        smartElements(doc.body).forEach((element, index) => {
          runtimeOwnedAttributes.forEach((attribute) => {
            if (element.hasAttribute(attribute)) {
              leakage.push({index, attribute});
            }
          });
        });
        if (doc.body.querySelector(`[${attributes.generated}="true"]`)) {
          leakage.push({index: -1, attribute: attributes.generated});
        }
        return leakage;
      }

      function audit(source, runtimeRoot = null, options = {}) {
        const cleanSource = editor?.canonicalSource ? editor.canonicalSource(source) : String(source || "");
        const compiled = engine.compileSource(cleanSource, {reason: options.reason || "operational-audit"});
        const root = runtimeRoot || rootFromRuntime(compiled.runtimeHtml);
        if (!runtimeRoot) root.innerHTML = compiled.runtimeHtml;
        const sourceGraph = graphFromSource(cleanSource);
        const runtimeGraph = graphFromRuntime(root);
        const serialized = engine.serializeRuntimeRoot(root, {reason: "operational-audit"});
        const recompiled = engine.compileSource(serialized.serialized, {reason: "operational-audit-recompile"});
        const sourceLeaks = hasRuntimeAttributeLeakage(cleanSource);
        const generatedWithMissingProvenance = runtimeGraph.nodes.flatMap((node) => (
          node.generatedParts
            .filter((part) => !part.owner || !part.origin || !part.reason || !part.contractVersion)
            .map((part) => ({node: node.id, part: part.part}))
        ));
        const issues = [];
        const warnings = [];

        if (sourceLeaks.length) issues.push(`${sourceLeaks.length} runtime-owned attribute(s) leaked into source.`);
        if (!serialized.report.serializerClean) issues.push("Serializer did not return a clean source report.");
        if (serialized.serialized.includes(attributes.generated)) issues.push("Serialized source contains generated marker literal.");
        if (recompiled.sourceCount !== sourceGraph.nodeCount) issues.push("Recompiled source count changed after serialization.");
        if (runtimeGraph.orphanGeneratedParts) issues.push(`${runtimeGraph.orphanGeneratedParts} generated part(s) are outside a smart element.`);
        if (generatedWithMissingProvenance.length) issues.push(`${generatedWithMissingProvenance.length} generated part(s) lack provenance.`);
        if (runtimeGraph.unresolvedEdgeCount) warnings.push(`${runtimeGraph.unresolvedEdgeCount} semantic relation edge(s) are pending.`);

        const checks = [
          {name: "source has no runtime leakage", passed: !sourceLeaks.length},
          {name: "runtime has smart nodes", passed: runtimeGraph.nodeCount > 0},
          {name: "generated parts carry provenance", passed: generatedWithMissingProvenance.length === 0},
          {name: "serializer is clean", passed: serialized.report.serializerClean && !serialized.serialized.includes(attributes.generated)},
          {name: "round trip preserves node count", passed: recompiled.sourceCount === sourceGraph.nodeCount},
          {name: "generated parts stay attached to smart nodes", passed: runtimeGraph.orphanGeneratedParts === 0}
        ];

        const passed = checks.filter((check) => check.passed).length;
        const failed = checks.length - passed;

        return {
          status: failed ? "blocked" : warnings.length ? "clean-with-warnings" : "clean",
          contractVersion,
          passed,
          failed,
          checks,
          issues,
          warnings,
          sourceGraph: {
            nodeCount: sourceGraph.nodeCount,
            edgeCount: sourceGraph.edgeCount,
            unresolvedEdgeCount: sourceGraph.unresolvedEdgeCount,
            clusters: sourceGraph.clusters
          },
          runtimeGraph: {
            nodeCount: runtimeGraph.nodeCount,
            edgeCount: runtimeGraph.edgeCount,
            unresolvedEdgeCount: runtimeGraph.unresolvedEdgeCount,
            generatedPartCount: runtimeGraph.generatedPartCount,
            orphanGeneratedParts: runtimeGraph.orphanGeneratedParts,
            clusters: runtimeGraph.clusters
          },
          serializer: serialized.report,
          recompile: {
            sourceCount: recompiled.sourceCount
          }
        };
      }

      function compactReport(source, runtimeRoot) {
        const sourceGraph = graphFromSource(source);
        const runtimeGraph = runtimeRoot ? graphFromRuntime(runtimeRoot) : null;
        return {
          contractVersion,
          source: {
            nodes: sourceGraph.nodeCount,
            edges: sourceGraph.edgeCount,
            unresolvedEdges: sourceGraph.unresolvedEdgeCount
          },
          runtime: runtimeGraph ? {
            nodes: runtimeGraph.nodeCount,
            edges: runtimeGraph.edgeCount,
            generatedParts: runtimeGraph.generatedPartCount,
            unresolvedEdges: runtimeGraph.unresolvedEdgeCount,
            orphanGeneratedParts: runtimeGraph.orphanGeneratedParts,
            layoutProofed: runtimeGraph.nodes.filter((node) => node.geometryProof).length
          } : null,
          nodes: runtimeGraph?.nodes || sourceGraph.nodes,
          edges: runtimeGraph?.edges || sourceGraph.edges
        };
      }

      return Object.freeze({
        graphFromSource,
        graphFromRuntime,
        audit,
        compactReport
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabGraph = McelLabGraph;
    }
