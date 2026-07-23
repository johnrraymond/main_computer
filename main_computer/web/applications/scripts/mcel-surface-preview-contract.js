var McelSurfacePreviewContract = (() => {
  "use strict";

  const contractVersion = "mcel.surface-preview-contract.v1";
  const rendererInterfaceContractVersion = "mcel.surface-renderer-interface.v1";
  const authoredDocumentContractVersion = "mcel.authored-surface-document.v1";
  const surfaceRoundTripContractVersion = "mcel.surface-roundtrip.v1";

  const rendererApi = (() => {
    if (typeof McelSurfaceRendererInterface !== "undefined") return McelSurfaceRendererInterface;
    if (typeof window !== "undefined" && window.McelSurfaceRendererInterface) return window.McelSurfaceRendererInterface;
    return null;
  })();

  const authoredDocumentApi = (() => {
    if (typeof McelAuthoredSurfaceDocument !== "undefined") return McelAuthoredSurfaceDocument;
    if (typeof window !== "undefined" && window.McelAuthoredSurfaceDocument) return window.McelAuthoredSurfaceDocument;
    return null;
  })();

  const supportedSurfaceKinds = Object.freeze(["html", "svg"]);

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function countBySeverity(diagnostics) {
    const counts = {errors: 0, warnings: 0, ok: 0};
    (diagnostics || []).forEach((item) => {
      if (!item) return;
      if (item.severity === "error") counts.errors += 1;
      else if (item.severity === "warning") counts.warnings += 1;
      else counts.ok += 1;
    });
    return Object.freeze(counts);
  }

  function hasErrors(diagnostics) {
    return (diagnostics || []).some((item) => item && item.severity === "error");
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value === undefined ? null : value));
  }

  function normalizeSurfaceKind(value, fallback) {
    const text = safeString(value || fallback || "html").toLowerCase();
    return supportedSurfaceKinds.includes(text) ? text : text;
  }

  function summarizePreviewReport(report) {
    const subject = report || {};
    if (subject.status === "not-applicable" || subject.previewable === false) {
      return "MCEL Preview: not applicable";
    }
    const counts = countBySeverity(subject.diagnostics || []);
    const parts = [
      `MCEL Preview: ${subject.valid ? "PASS" : "FAIL"}`,
      safeString(subject.surfaceKind || subject.projection || "surface")
    ];
    if (subject.roundTripStatus) parts.push(`round-trip ${safeString(subject.roundTripStatus).toUpperCase()}`);
    if (counts.errors) parts.push(`${counts.errors} error${counts.errors === 1 ? "" : "s"}`);
    if (counts.warnings) parts.push(`${counts.warnings} warning${counts.warnings === 1 ? "" : "s"}`);
    return parts.join(" · ");
  }

  function dependencyDiagnostics(options) {
    const opts = options || {};
    const diagnostics = [];
    if (opts.requireAuthoredDocument !== false && !authoredDocumentApi) {
      diagnostics.push(diagnostic(
        "surface-preview-missing-authored-document-api",
        "error",
        "MCEL preview analysis requires McelAuthoredSurfaceDocument when source text is supplied."
      ));
    }
    if (opts.requireRenderer !== false && (!rendererApi || typeof rendererApi.renderWithRenderer !== "function")) {
      diagnostics.push(diagnostic(
        "surface-preview-missing-renderer-interface-api",
        "error",
        "MCEL preview rendering requires McelSurfaceRendererInterface."
      ));
    }
    return diagnostics;
  }

  function createPreviewRequest(input) {
    const source = input || {};
    const surfaceKind = normalizeSurfaceKind(source.surfaceKind || source.projection || source.kind, "html");
    return Object.freeze({
      contractVersion,
      surfaceKind,
      projection: surfaceKind,
      verifyOutput: source.verifyOutput !== false,
      hasSourceText: safeString(source.sourceText || source.text || "").length > 0,
      hasSurfaceIR: !!(source.surfaceIR || source.surfaceIr || source.expectedSurfaceIr || source.expectedSurfaceIR),
      hasLayoutGrammar: !!(source.layoutGrammar || source.expectedLayoutGrammar),
      hasRenderer: !!source.renderer,
      sourceText: source.sourceText || source.text || "",
      analysis: source.analysis || null,
      surfaceIR: source.surfaceIR || source.surfaceIr || source.expectedSurfaceIr || source.expectedSurfaceIR || null,
      layoutGrammar: source.layoutGrammar || source.expectedLayoutGrammar || null,
      renderer: source.renderer || null,
      options: Object.freeze(Object.assign({}, source.options || {})),
      extractorOptions: Object.freeze(Object.assign({}, source.extractorOptions || {})),
      authoredDocumentOptions: Object.freeze(Object.assign({}, source.authoredDocumentOptions || {}))
    });
  }

  function isPreviewableAnalysis(analysis) {
    const subject = analysis || {};
    return subject.applicable !== false
      && subject.status !== "not-applicable"
      && !!subject.surfaceIR
      && !!subject.layoutGrammar
      && subject.valid !== false;
  }

  function resolvePreviewInput(input) {
    const request = createPreviewRequest(input);
    const diagnostics = [];
    let analysis = request.analysis;
    let surfaceIR = request.surfaceIR;
    let layoutGrammar = request.layoutGrammar;

    if ((!surfaceIR || !layoutGrammar) && request.hasSourceText) {
      if (!authoredDocumentApi || typeof authoredDocumentApi.analyzeText !== "function") {
        diagnostics.push(...dependencyDiagnostics({requireRenderer: false, requireAuthoredDocument: true}));
      } else {
        analysis = authoredDocumentApi.analyzeText(request.sourceText, request.authoredDocumentOptions || {});
        diagnostics.push(...(analysis.diagnostics || []));
        if (analysis.applicable === false || analysis.status === "not-applicable") {
          return Object.freeze({
            contractVersion,
            request,
            analysis,
            surfaceIR: null,
            layoutGrammar: null,
            previewable: false,
            valid: true,
            status: "not-applicable",
            diagnostics: Object.freeze(diagnostics),
            counts: countBySeverity(diagnostics),
            summary: "MCEL Preview: not applicable"
          });
        }
        surfaceIR = surfaceIR || analysis.surfaceIR || null;
        layoutGrammar = layoutGrammar || analysis.layoutGrammar || null;
      }
    }

    if (analysis && analysis.status === "fail") {
      diagnostics.push(diagnostic(
        "surface-preview-authored-analysis-failed",
        "error",
        "Authored MCEL surface analysis failed; preview rendering is unsafe.",
        {documentKind: analysis.documentKind || ""}
      ));
    }
    if (!surfaceIR) {
      diagnostics.push(diagnostic(
        "surface-preview-missing-surface-ir",
        "error",
        "MCEL preview requires a SemanticSurfaceIR or authored source that can build one."
      ));
    }
    if (!layoutGrammar) {
      diagnostics.push(diagnostic(
        "surface-preview-missing-layout-grammar",
        "error",
        "MCEL preview requires a SharedLayoutGrammar or authored source that can build one."
      ));
    }
    if (!supportedSurfaceKinds.includes(request.surfaceKind)) {
      diagnostics.push(diagnostic(
        "surface-preview-unsupported-surface-kind",
        "error",
        "MCEL preview supports only html and svg projections.",
        {surfaceKind: request.surfaceKind}
      ));
    }

    const valid = !hasErrors(diagnostics);
    return Object.freeze({
      contractVersion,
      request,
      analysis: analysis || null,
      surfaceIR,
      layoutGrammar,
      previewable: !!(surfaceIR && layoutGrammar && valid),
      valid,
      status: valid ? "pass" : "fail",
      diagnostics: Object.freeze(diagnostics),
      counts: countBySeverity(diagnostics),
      summary: valid ? "MCEL Preview: ready" : summarizePreviewReport({valid: false, diagnostics})
    });
  }

  function validatePreviewRequest(input) {
    return resolvePreviewInput(input);
  }

  function renderPreview(input) {
    const request = createPreviewRequest(input);
    const resolved = resolvePreviewInput(request);
    const diagnostics = [...(resolved.diagnostics || [])];

    if (resolved.status === "not-applicable" || resolved.previewable === false && resolved.valid) {
      return Object.freeze({
        contractVersion,
        request,
        analysis: resolved.analysis,
        surfaceIR: null,
        layoutGrammar: null,
        previewable: false,
        renderedText: "",
        renderResult: null,
        surfaceKind: request.surfaceKind,
        valid: true,
        status: "not-applicable",
        diagnostics: Object.freeze(diagnostics),
        counts: countBySeverity(diagnostics),
        summary: "MCEL Preview: not applicable"
      });
    }

    if (!request.renderer) {
      diagnostics.push(diagnostic(
        "surface-preview-missing-renderer",
        "error",
        "MCEL preview rendering requires an explicit renderer implementation.",
        {surfaceKind: request.surfaceKind}
      ));
    }
    if (!rendererApi || typeof rendererApi.renderWithRenderer !== "function") {
      diagnostics.push(...dependencyDiagnostics({requireRenderer: true, requireAuthoredDocument: false}));
    }

    let renderResult = null;
    if (!hasErrors(diagnostics)) {
      renderResult = rendererApi.renderWithRenderer(request.renderer, {
        surfaceIR: resolved.surfaceIR,
        layoutGrammar: resolved.layoutGrammar,
        surfaceKind: request.surfaceKind,
        verifyOutput: request.verifyOutput,
        options: request.options,
        extractorOptions: request.extractorOptions
      });
      diagnostics.push(...(renderResult.diagnostics || []));
      if (!renderResult.valid) {
        diagnostics.push(diagnostic(
          "surface-preview-renderer-output-invalid",
          "error",
          "MCEL preview renderer output did not satisfy the renderer interface and round-trip contract.",
          {surfaceKind: request.surfaceKind}
        ));
      }
    }

    const valid = !hasErrors(diagnostics) && !!renderResult && renderResult.valid;
    const report = Object.freeze({
      contractVersion,
      request,
      analysis: resolved.analysis,
      surfaceIR: resolved.surfaceIR,
      layoutGrammar: resolved.layoutGrammar,
      previewable: true,
      renderedText: renderResult?.renderedText || "",
      renderResult,
      surfaceKind: renderResult?.surfaceKind || request.surfaceKind,
      roundTripStatus: renderResult?.outputVerification?.status || renderResult?.status || "",
      valid,
      status: valid ? "pass" : "fail",
      diagnostics: Object.freeze(diagnostics),
      counts: countBySeverity(diagnostics)
    });
    return Object.freeze(Object.assign({}, report, {summary: summarizePreviewReport(report)}));
  }

  function renderPreviewPair(input) {
    const source = input || {};
    const resolved = resolvePreviewInput(source);
    const diagnostics = [...(resolved.diagnostics || [])];

    if (resolved.status === "not-applicable" || resolved.previewable === false && resolved.valid) {
      return Object.freeze({
        contractVersion,
        previewable: false,
        valid: true,
        status: "not-applicable",
        htmlResult: null,
        svgResult: null,
        agreement: null,
        diagnostics: Object.freeze(diagnostics),
        counts: countBySeverity(diagnostics),
        summary: "MCEL Preview: not applicable"
      });
    }

    if (!source.htmlRenderer) {
      diagnostics.push(diagnostic(
        "surface-preview-pair-missing-html-renderer",
        "error",
        "MCEL preview pair rendering requires an HTML renderer."
      ));
    }
    if (!source.svgRenderer) {
      diagnostics.push(diagnostic(
        "surface-preview-pair-missing-svg-renderer",
        "error",
        "MCEL preview pair rendering requires an SVG renderer."
      ));
    }
    if (!rendererApi || typeof rendererApi.verifyRendererPairAgreement !== "function") {
      diagnostics.push(...dependencyDiagnostics({requireRenderer: true, requireAuthoredDocument: false}));
    }

    let htmlResult = null;
    let svgResult = null;
    let agreement = null;
    if (!hasErrors(diagnostics)) {
      htmlResult = renderPreview(Object.assign({}, source, {
        surfaceIR: resolved.surfaceIR,
        layoutGrammar: resolved.layoutGrammar,
        renderer: source.htmlRenderer,
        surfaceKind: "html"
      }));
      svgResult = renderPreview(Object.assign({}, source, {
        surfaceIR: resolved.surfaceIR,
        layoutGrammar: resolved.layoutGrammar,
        renderer: source.svgRenderer,
        surfaceKind: "svg"
      }));
      diagnostics.push(...(htmlResult.diagnostics || []), ...(svgResult.diagnostics || []));
      if (htmlResult.valid && svgResult.valid) {
        agreement = rendererApi.verifyRendererPairAgreement(htmlResult.renderResult, svgResult.renderResult, source.agreementOptions || {});
        diagnostics.push(...(agreement.diagnostics || []));
        if (!agreement.valid) {
          diagnostics.push(diagnostic(
            "surface-preview-pair-agreement-failed",
            "error",
            "HTML and SVG preview projections did not extract to the same MCEL surface."
          ));
        }
      }
    }

    const valid = !hasErrors(diagnostics) && !!htmlResult && !!svgResult && htmlResult.valid && svgResult.valid && (!agreement || agreement.valid);
    const report = Object.freeze({
      contractVersion,
      previewable: true,
      surfaceIR: resolved.surfaceIR,
      layoutGrammar: resolved.layoutGrammar,
      htmlResult,
      svgResult,
      agreement,
      valid,
      status: valid ? "pass" : "fail",
      diagnostics: Object.freeze(diagnostics),
      counts: countBySeverity(diagnostics)
    });
    return Object.freeze(Object.assign({}, report, {summary: summarizePreviewReport(Object.assign({}, report, {surfaceKind: "html+svg"}))}));
  }

  function previewReportFingerprint(report) {
    const subject = report || {};
    return JSON.stringify({
      contractVersion,
      status: subject.status || "",
      valid: !!subject.valid,
      previewable: !!subject.previewable,
      surfaceKind: subject.surfaceKind || "",
      surfaceId: subject.surfaceIR?.surface?.id || "",
      rendererId: subject.renderResult?.profile?.id || "",
      diagnosticCodes: (subject.diagnostics || []).map((item) => item.code || "").sort()
    });
  }

  return Object.freeze({
    contractVersion,
    rendererInterfaceContractVersion,
    authoredDocumentContractVersion,
    surfaceRoundTripContractVersion,
    supportedSurfaceKinds,
    createPreviewRequest,
    validatePreviewRequest,
    resolvePreviewInput,
    isPreviewableAnalysis,
    renderPreview,
    renderPreviewPair,
    summarizePreviewReport,
    previewReportFingerprint
  });
})();

if (typeof window !== "undefined") {
  window.McelSurfacePreviewContract = McelSurfacePreviewContract;
}
