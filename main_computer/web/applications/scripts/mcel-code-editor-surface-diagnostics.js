var McelCodeEditorSurfaceDiagnostics = (() => {
  "use strict";

  const contractVersion = "mcel.code-editor-surface-diagnostics.v1";
  const surfaceIrContractVersion = "mcel.semantic-surface-ir.v1";
  const layoutContractVersion = "mcel.shared-layout-grammar.v1";
  const roundTripContractVersion = "mcel.surface-roundtrip.v1";

  const irApi = (() => {
    if (typeof McelSemanticSurfaceIR !== "undefined") return McelSemanticSurfaceIR;
    if (typeof window !== "undefined" && window.McelSemanticSurfaceIR) return window.McelSemanticSurfaceIR;
    return null;
  })();

  const layoutApi = (() => {
    if (typeof McelSharedLayoutGrammar !== "undefined") return McelSharedLayoutGrammar;
    if (typeof window !== "undefined" && window.McelSharedLayoutGrammar) return window.McelSharedLayoutGrammar;
    return null;
  })();

  const roundTripApi = (() => {
    if (typeof McelSurfaceRoundTrip !== "undefined") return McelSurfaceRoundTrip;
    if (typeof window !== "undefined" && window.McelSurfaceRoundTrip) return window.McelSurfaceRoundTrip;
    return null;
  })();

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function hasErrors(diagnostics) {
    return (diagnostics || []).some((item) => item && item.severity === "error");
  }

  function countBySeverity(diagnostics) {
    const counts = {errors: 0, warnings: 0, ok: 0};
    (diagnostics || []).forEach((item) => {
      if (item.severity === "error") counts.errors += 1;
      else if (item.severity === "warning") counts.warnings += 1;
    });
    return counts;
  }

  function dependencyDiagnostics() {
    const diagnostics = [];
    if (!irApi || typeof irApi.buildSurfaceIRFromRidges !== "function") {
      diagnostics.push(diagnostic(
        "code-editor-surface-diagnostics-missing-surface-ir-api",
        "error",
        "Code editor surface diagnostics require McelSemanticSurfaceIR."
      ));
    }
    if (!layoutApi || typeof layoutApi.buildSharedLayoutGrammar !== "function") {
      diagnostics.push(diagnostic(
        "code-editor-surface-diagnostics-missing-layout-api",
        "error",
        "Code editor surface diagnostics require McelSharedLayoutGrammar."
      ));
    }
    return diagnostics;
  }

  function regionIdFromKey(key, fallback) {
    const text = safeString(key);
    if (text.startsWith("code-editor.region.")) return text;
    return fallback;
  }

  function compactBox(box) {
    const width = finiteNumber(box && box.width, 0);
    const height = finiteNumber(box && box.height, 0);
    const x = finiteNumber(box && box.x, 0);
    const y = finiteNumber(box && box.y, 0);
    return Object.freeze({
      exists: !!(box && box.exists),
      visible: !!(box && box.visible),
      selector: safeString(box && box.selector),
      x,
      y,
      width,
      height,
      right: finiteNumber(box && box.right, x + width),
      bottom: finiteNumber(box && box.bottom, y + height),
      display: safeString(box && box.display),
      gridRow: safeString(box && box.gridRow),
      gridColumn: safeString(box && box.gridColumn)
    });
  }

  function boxCenter(box) {
    const b = compactBox(box);
    return Object.freeze({
      anchorX: b.x + b.width / 2,
      anchorY: b.y + b.height / 2,
      width: b.width,
      height: b.height
    });
  }

  function surfaceReportInput(reportOrSnapshot) {
    const subject = reportOrSnapshot || {};
    const summaryPrimary = subject.summary && subject.summary.primarySurface || subject.primarySurface || {};
    const measurements = subject.measurements || {};
    const surfaces = measurements.surfaces || subject.surfaces || {};
    const requiredRegions = measurements.requiredRegions || subject.requiredRegions || {};
    const optionalRegions = measurements.optionalRegions || subject.optionalRegions || {};
    const viewport = measurements.viewport || subject.viewport || {};
    return Object.freeze({
      appId: safeString(subject.appId || "code-editor"),
      contractId: safeString(subject.contractId || subject.contract && subject.contract.id || "code-editor.contract.authoring.monaco-golden-path"),
      primarySurface: summaryPrimary,
      measurements,
      surfaces,
      requiredRegions,
      optionalRegions,
      viewport
    });
  }

  function primaryBox(input) {
    const summary = input.primarySurface || {};
    const summaryEditor = summary.editor || {};
    const summaryHost = summary.host || {};
    const surfaces = input.surfaces || {};
    return compactBox(
      surfaces.primaryEditor ||
      surfaces.monacoEditor ||
      summaryEditor ||
      surfaces.primaryHost ||
      surfaces.monacoHost ||
      summaryHost ||
      {}
    );
  }

  function hostBox(input) {
    const summary = input.primarySurface || {};
    const surfaces = input.surfaces || {};
    return compactBox(
      surfaces.primaryHost ||
      surfaces.monacoHost ||
      summary.host ||
      primaryBox(input)
    );
  }

  function rootRegionBox(input) {
    const regions = input.requiredRegions || {};
    return compactBox(
      regions["code-editor.region.root"] ||
      regions["#code-editor-app"] ||
      input.measurements && input.measurements.root ||
      {exists: true, visible: true, selector: "#code-editor-app", x: 0, y: 0, width: finiteNumber(input.viewport.width, 1), height: finiteNumber(input.viewport.height, 1)}
    );
  }

  function editorRegionBox(input) {
    const regions = input.requiredRegions || {};
    return compactBox(
      regions["code-editor.region.editor-group"] ||
      regions[".code-studio-editor-group"] ||
      hostBox(input)
    );
  }

  function visibleOptionalContextBox(input) {
    const regions = input.optionalRegions || {};
    return compactBox(
      regions["code-editor.region.inspector"] ||
      regions["code-editor.region.right-pane"] ||
      regions[".code-studio-inspector"] ||
      {}
    );
  }

  function viewportFor(input) {
    const root = rootRegionBox(input);
    const width = finiteNumber(input.viewport.width, 0) || root.width || 1;
    const height = finiteNumber(input.viewport.height, 0) || root.height || 1;
    return Object.freeze({width, height, safeMargin: 0});
  }

  function regionBoundsFor(input) {
    const root = rootRegionBox(input);
    const editor = editorRegionBox(input);
    const context = visibleOptionalContextBox(input);
    const regions = [
      {id: "code-editor.region.root", role: "application-root", x: root.x, y: root.y, width: root.width || viewportFor(input).width, height: root.height || viewportFor(input).height},
      {id: "code-editor.region.editor-group", role: "primary-editor-group", x: editor.x, y: editor.y, width: editor.width, height: editor.height}
    ];
    if (context.exists) {
      regions.push({
        id: "code-editor.region.inspector",
        role: "supporting-context-feedback",
        x: context.x,
        y: context.y,
        width: context.width,
        height: context.height
      });
    }
    return Object.freeze(regions);
  }

  function buildCodeEditorSurfaceRidgeRecords(reportOrSnapshot) {
    const input = surfaceReportInput(reportOrSnapshot);
    const editor = primaryBox(input);
    const host = hostBox(input);
    const context = visibleOptionalContextBox(input);
    const surfaceId = safeString(input.primarySurface && input.primarySurface.expected) || "code-editor.surface.monaco-selected-file-editor";
    const records = [
      Object.freeze({
        "data-mcel-surface-id": surfaceId,
        "data-mcel-surface-kind": "code-editor-authoring-surface",
        "data-mcel-surface-role": "primary-authoring-diagnostic",
        "data-mcel-surface-contract": input.contractId,
        "data-mcel-authoritative": "true",
        "data-mcel-renderer": "mcel-self-diagnosis",
        "data-mcel-projection": "diagnostic"
      }),
      Object.freeze({
        "data-mcel-region": "code-editor.region.root",
        "data-mcel-region-role": "application-root"
      }),
      Object.freeze({
        "data-mcel-region": "code-editor.region.editor-group",
        "data-mcel-region-role": "primary-editor-group"
      }),
      Object.freeze({
        "data-mcel-node-id": "code-editor.node.monaco-selected-file-editor",
        "data-mcel-node-type": "primary_authoring_editor",
        "data-mcel-node-label": "Monaco selected-file editor",
        "data-mcel-source": "mcel-self-diagnosis.primarySurface",
        "data-mcel-provenance": "runtime.primarySurface.editor",
        "data-mcel-home-region": "code-editor.region.editor-group",
        "data-mcel-actual-region": "code-editor.region.editor-group",
        "data-mcel-teleported": "false",
        "data-layout-anchor-x": editor.x + editor.width / 2,
        "data-layout-anchor-y": editor.y + editor.height / 2,
        "data-layout-width": editor.width,
        "data-layout-height": editor.height,
        "data-layout-z": "10",
        "data-layout-region": "code-editor.region.editor-group",
        "data-layout-port-in": "west",
        "data-layout-port-out": "east"
      })
    ];

    if (host.exists && (host.selector || host.width || host.height)) {
      records.push(Object.freeze({
        "data-mcel-edge-id": "EDGE.code-editor.primary-surface-owns-editor",
        "data-mcel-edge-kind": "OWNS",
        "data-mcel-from": "code-editor.node.monaco-selected-file-editor",
        "data-mcel-to": "code-editor.node.monaco-selected-file-editor",
        "data-mcel-relation": "authoritative_surface",
        "data-mcel-causal-link": "false",
        "data-layout-route-kind": "cubic",
        "data-layout-from-port": "east",
        "data-layout-to-port": "west",
        "data-layout-z": "1"
      }));
    }

    if (context.exists && context.visible && context.width > 0 && context.height > 0) {
      records.push(Object.freeze({
        "data-mcel-region": "code-editor.region.inspector",
        "data-mcel-region-role": "supporting-context-feedback"
      }));
      records.push(Object.freeze({
        "data-mcel-node-id": "code-editor.node.supporting-context-feedback",
        "data-mcel-node-type": "supporting_context_surface",
        "data-mcel-node-label": "Supporting context feedback",
        "data-mcel-source": "mcel-self-diagnosis.optionalRegions",
        "data-mcel-provenance": "runtime.optionalRegions.inspector",
        "data-mcel-home-region": "code-editor.region.inspector",
        "data-mcel-actual-region": "code-editor.region.inspector",
        "data-mcel-teleported": "false",
        "data-layout-anchor-x": context.x + context.width / 2,
        "data-layout-anchor-y": context.y + context.height / 2,
        "data-layout-width": context.width,
        "data-layout-height": context.height,
        "data-layout-z": "4",
        "data-layout-region": "code-editor.region.inspector",
        "data-layout-port-in": "west",
        "data-layout-port-out": "east"
      }));
    }

    return Object.freeze(records);
  }

  function buildCodeEditorSurfaceModel(reportOrSnapshot, options) {
    const diagnostics = dependencyDiagnostics();
    const input = surfaceReportInput(reportOrSnapshot);
    if (hasErrors(diagnostics)) {
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        diagnostics: Object.freeze(diagnostics),
        semanticRidgesPresent: false,
        surfaceIrBuildable: false,
        layoutGrammarPresent: false,
        layoutGrammarValid: false
      });
    }

    const records = buildCodeEditorSurfaceRidgeRecords(reportOrSnapshot);
    const primary = primaryBox(input);
    const surfaceRidge = records.find((record) => record["data-mcel-surface-id"]);
    const nodeRidges = records.filter((record) => record["data-mcel-node-id"]);
    if (!surfaceRidge) {
      diagnostics.push(diagnostic("code-editor-surface-ridge-missing", "error", "Code editor diagnostics must identify the primary semantic surface."));
    }
    if (!nodeRidges.length) {
      diagnostics.push(diagnostic("code-editor-surface-node-ridge-missing", "error", "Code editor diagnostics must identify at least one surface node."));
    }
    if (!primary.exists || !primary.visible || primary.width <= 0 || primary.height <= 0) {
      diagnostics.push(diagnostic(
        "code-editor-primary-editor-not-layout-usable",
        "error",
        "Code editor MCEL surface pathway requires the primary editor to be visible with a positive layout box.",
        {primary}
      ));
    }

    let irResult = null;
    let surfaceIR = null;
    let layoutResult = null;
    let layoutGrammar = null;
    try {
      irResult = irApi.buildSurfaceIRFromRidges(records, {
        requireSurface: true,
        defaultSurfaceKind: "code-editor-authoring-surface",
        defaultSurfaceRole: "primary-authoring-diagnostic"
      });
      surfaceIR = irResult.ir || irResult;
      diagnostics.push(...(irResult.diagnostics || []));
    } catch (error) {
      diagnostics.push(diagnostic(
        "code-editor-surface-ir-build-threw",
        "error",
        "Code editor diagnostic ridges could not build a SemanticSurfaceIR.",
        {message: safeString(error && error.message || error)}
      ));
    }

    if (surfaceIR) {
      try {
        const nodePorts = {"code-editor.node.monaco-selected-file-editor": ["west", "east"]};
        if ((surfaceIR.graph && surfaceIR.graph.nodes || []).some((node) => node.id === "code-editor.node.supporting-context-feedback")) {
          nodePorts["code-editor.node.supporting-context-feedback"] = ["west", "east"];
        }
        const layoutOptions = Object.assign({
          viewport: viewportFor(input),
          regions: regionBoundsFor(input),
          nodePorts
        }, options && options.layoutOptions || {});
        layoutResult = layoutApi.buildSharedLayoutGrammar(surfaceIR, layoutOptions);
        layoutGrammar = layoutResult.grammar || layoutResult;
        diagnostics.push(...(layoutResult.diagnostics || []));
      } catch (error) {
        diagnostics.push(diagnostic(
          "code-editor-layout-grammar-build-threw",
          "error",
          "Code editor surface layout could not build a shared layout grammar.",
          {message: safeString(error && error.message || error)}
        ));
      }
    }

    const semanticRidgesPresent = Boolean(surfaceRidge && nodeRidges.length);
    const surfaceValidation = surfaceIR && irApi.validateSurfaceIR ? irApi.validateSurfaceIR(surfaceIR) : null;
    const layoutValidation = surfaceIR && layoutGrammar && layoutApi.validateSharedLayoutGrammar
      ? layoutApi.validateSharedLayoutGrammar(surfaceIR, layoutGrammar)
      : null;

    if (surfaceValidation) diagnostics.push(...(surfaceValidation.diagnostics || []));
    if (layoutValidation) diagnostics.push(...(layoutValidation.diagnostics || []));

    const counts = countBySeverity(diagnostics);
    counts.ok = [
      semanticRidgesPresent,
      !!surfaceIR && surfaceValidation && surfaceValidation.valid,
      !!layoutGrammar && layoutValidation && layoutValidation.valid
    ].filter(Boolean).length;

    return Object.freeze({
      contractVersion,
      surfaceIrContractVersion,
      layoutContractVersion,
      appId: input.appId,
      status: hasErrors(diagnostics) ? "fail" : "pass",
      valid: !hasErrors(diagnostics),
      semanticRidgesPresent,
      surfaceIrBuildable: !!surfaceIR,
      surfaceIrValid: !!(surfaceValidation && surfaceValidation.valid),
      layoutGrammarPresent: !!layoutGrammar,
      layoutGrammarValid: !!(layoutValidation && layoutValidation.valid),
      roundTripStatus: "not-run",
      counts: Object.freeze(counts),
      diagnostics: Object.freeze(diagnostics),
      records,
      surfaceIR,
      layoutGrammar,
      fingerprints: Object.freeze({
        semantic: surfaceIR && irApi.semanticFingerprint ? irApi.semanticFingerprint(surfaceIR) : "",
        layout: layoutGrammar && layoutApi.layoutFingerprint ? layoutApi.layoutFingerprint(layoutGrammar) : ""
      })
    });
  }

  function attrs(values) {
    return Object.entries(values || {})
      .filter(([, value]) => value !== undefined && value !== null && safeString(value) !== "")
      .map(([key, value]) => `${key}="${safeString(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;"
      })[char])}"`)
      .join(" ");
  }

  function renderDiagnosticSurfaceHtml(surfaceIR, layoutGrammar) {
    const ir = surfaceIR || {};
    const layout = layoutGrammar || {};
    const surface = ir.surface || {};
    const regions = (ir.graph && ir.graph.regions || []).map((region) => {
      const bounds = (layout.regions || []).find((item) => item.id === region.id) || {};
      return `<section ${attrs({
        "data-mcel-region": region.id,
        "data-mcel-region-role": region.role,
        "data-layout-x": bounds.x,
        "data-layout-y": bounds.y,
        "data-layout-width": bounds.width,
        "data-layout-height": bounds.height
      })}></section>`;
    }).join("");
    const layoutByNode = new Map((layout.nodes || []).map((item) => [item.id, item]));
    const nodes = (ir.graph && ir.graph.nodes || []).map((node) => {
      const box = layoutByNode.get(node.id) || {};
      return `<article ${attrs({
        "data-mcel-node-id": node.id,
        "data-mcel-node-type": node.type,
        "data-mcel-node-label": node.label,
        "data-mcel-source": node.source,
        "data-mcel-provenance": node.provenance,
        "data-mcel-home-region": node.homeRegion,
        "data-mcel-actual-region": node.actualRegion,
        "data-mcel-teleported": node.teleported ? "true" : "false",
        "data-layout-anchor-x": box.anchorX,
        "data-layout-anchor-y": box.anchorY,
        "data-layout-width": box.width,
        "data-layout-height": box.height,
        "data-layout-z": box.z,
        "data-layout-region": box.region,
        "data-layout-port-in": "west",
        "data-layout-port-out": "east"
      })}></article>`;
    }).join("");
    const layoutByEdge = new Map((layout.edges || []).map((item) => [item.id, item]));
    const edges = (ir.graph && ir.graph.edges || []).map((edge) => {
      const route = layoutByEdge.get(edge.id) || {};
      return `<i ${attrs({
        "data-mcel-edge-id": edge.id,
        "data-mcel-edge-kind": edge.kind,
        "data-mcel-from": edge.from,
        "data-mcel-to": edge.to,
        "data-mcel-relation": edge.relation,
        "data-mcel-causal-link": edge.causalLink ? "true" : "false",
        "data-layout-route-kind": route.routeKind,
        "data-layout-from-port": route.fromPort,
        "data-layout-to-port": route.toPort,
        "data-layout-z": route.z
      })}></i>`;
    }).join("");
    const viewport = layout.viewport || {};
    return `<section ${attrs({
      "data-mcel-surface-id": surface.id,
      "data-mcel-surface-kind": surface.kind,
      "data-mcel-surface-role": surface.role,
      "data-mcel-surface-contract": surface.contract,
      "data-mcel-authoritative": "true",
      "data-mcel-renderer": "mcel-code-editor-surface-diagnostics",
      "data-mcel-projection": "html",
      "data-layout-viewport-width": viewport.width,
      "data-layout-viewport-height": viewport.height,
      "data-layout-safe-margin": viewport.safeMargin
    })}>${regions}${nodes}${edges}</section>`;
  }

  function evaluateCodeEditorSurfacePathway(reportOrSnapshot, options) {
    const model = buildCodeEditorSurfaceModel(reportOrSnapshot, options);
    const diagnostics = [...(model.diagnostics || [])];
    let roundTrip = null;
    let roundTripStatus = "not-run";

    if (model.valid && roundTripApi && typeof roundTripApi.verifyRenderedSurfaceRoundTrip === "function") {
      const renderedText = renderDiagnosticSurfaceHtml(model.surfaceIR, model.layoutGrammar);
      roundTrip = roundTripApi.verifyRenderedSurfaceRoundTrip({
        expectedSurfaceIr: model.surfaceIR,
        expectedLayoutGrammar: model.layoutGrammar,
        renderedText,
        surfaceKind: "html"
      });
      diagnostics.push(...(roundTrip.diagnostics || []));
      roundTripStatus = roundTrip.valid ? "pass" : "fail";
    } else if (!roundTripApi) {
      roundTripStatus = "unavailable";
    }

    const counts = countBySeverity(diagnostics);
    counts.ok = [
      model.semanticRidgesPresent,
      model.surfaceIrValid,
      model.layoutGrammarValid,
      roundTripStatus === "pass"
    ].filter(Boolean).length;

    return Object.freeze({
      contractVersion,
      roundTripContractVersion,
      appId: model.appId || "code-editor",
      status: hasErrors(diagnostics) ? "fail" : "pass",
      valid: !hasErrors(diagnostics),
      semanticRidgesPresent: !!model.semanticRidgesPresent,
      surfaceIrBuildable: !!model.surfaceIrBuildable,
      surfaceIrValid: !!model.surfaceIrValid,
      layoutGrammarPresent: !!model.layoutGrammarPresent,
      layoutGrammarValid: !!model.layoutGrammarValid,
      extractable: roundTripStatus === "pass",
      roundTripStatus,
      counts: Object.freeze(counts),
      diagnostics: Object.freeze(diagnostics),
      fingerprints: model.fingerprints || {},
      surfaceIR: model.surfaceIR,
      layoutGrammar: model.layoutGrammar
    });
  }

  function summarizeForDiagnosis(reportOrSnapshot, options) {
    const pathway = evaluateCodeEditorSurfacePathway(reportOrSnapshot, options || {});
    return Object.freeze({
      contractVersion,
      status: pathway.status,
      valid: pathway.valid,
      semanticRidgesPresent: pathway.semanticRidgesPresent,
      surfaceIrBuildable: pathway.surfaceIrBuildable,
      surfaceIrValid: pathway.surfaceIrValid,
      layoutGrammarPresent: pathway.layoutGrammarPresent,
      layoutGrammarValid: pathway.layoutGrammarValid,
      extractable: pathway.extractable,
      roundTripStatus: pathway.roundTripStatus,
      counts: pathway.counts,
      diagnosticCodes: Object.freeze((pathway.diagnostics || []).map((item) => item.code))
    });
  }

  return Object.freeze({
    contractVersion,
    surfaceIrContractVersion,
    layoutContractVersion,
    roundTripContractVersion,
    dependencyDiagnostics,
    buildCodeEditorSurfaceRidgeRecords,
    buildCodeEditorSurfaceModel,
    renderDiagnosticSurfaceHtml,
    evaluateCodeEditorSurfacePathway,
    summarizeForDiagnosis
  });
})();

if (typeof window !== "undefined") {
  window.McelCodeEditorSurfaceDiagnostics = McelCodeEditorSurfaceDiagnostics;
}
