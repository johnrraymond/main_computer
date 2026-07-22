var McelAuthoredSurfaceDocument = (() => {
  "use strict";

  const contractVersion = "mcel.authored-surface-document.v1";

  const extractorApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

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

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value);
  }

  function trimmed(value) {
    return safeString(value).trim();
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
    });
    return Object.freeze(counts);
  }

  function hasErrors(diagnostics) {
    return (diagnostics || []).some((item) => item && item.severity === "error");
  }

  function containsSurfaceRidges(sourceText) {
    const text = safeString(sourceText);
    return /\bdata-mcel-[A-Za-z0-9_-]+\s*=/.test(text)
      || /\bdata-layout-[A-Za-z0-9_-]+\s*=/.test(text)
      || /"data-mcel-[A-Za-z0-9_-]+"\s*:/.test(text)
      || /"data-layout-[A-Za-z0-9_-]+"\s*:/.test(text)
      || /"ridgeRecords"\s*:/.test(text)
      || /"surfaceIR"\s*:/.test(text)
      || /"layoutGrammar"\s*:/.test(text);
  }

  function detectDocumentKind(sourceText) {
    const text = trimmed(sourceText);
    if (!text) return "empty";
    if (text.startsWith("<svg") || text.startsWith("<?xml") && text.includes("<svg")) return "svg";
    if (text.startsWith("<!doctype") || text.startsWith("<!DOCTYPE") || text.startsWith("<html") || text.startsWith("<section") || text.startsWith("<div") || text.startsWith("<main")) return "html";
    if (text.startsWith("{") || text.startsWith("[")) return "json";
    if (containsSurfaceRidges(text) && text.includes("<")) return "markup";
    if (containsSurfaceRidges(text)) return "ridge-text";
    return "unknown";
  }

  function dependencyDiagnostics(kind) {
    const diagnostics = [];
    if ((kind === "html" || kind === "svg" || kind === "markup") && (!extractorApi || typeof extractorApi.buildSurfaceBundleFromMarkup !== "function")) {
      diagnostics.push(diagnostic("authored-surface-document-missing-extractor-api", "error", "McelSurfaceExtractors is required to analyze authored markup.", {}));
    }
    if (!irApi || typeof irApi.buildSurfaceIRFromRidges !== "function") {
      diagnostics.push(diagnostic("authored-surface-document-missing-surface-ir-api", "error", "McelSemanticSurfaceIR is required to analyze authored ridge records.", {}));
    }
    if (!layoutApi || typeof layoutApi.buildSharedLayoutGrammar !== "function") {
      diagnostics.push(diagnostic("authored-surface-document-missing-layout-api", "error", "McelSharedLayoutGrammar is required to analyze authored layout ridges.", {}));
    }
    return diagnostics;
  }

  function summarizeBundle(bundle, diagnostics, documentKind, options) {
    const allDiagnostics = Object.freeze([...(diagnostics || []), ...((bundle && bundle.diagnostics) || [])]);
    const surfaceValidation = bundle?.validation?.surface || null;
    const layoutValidation = bundle?.validation?.layout || null;
    const surfaceIrBuildable = !!bundle?.surfaceIR;
    const surfaceIrValid = !!surfaceValidation?.valid || (surfaceIrBuildable && !hasErrors(allDiagnostics));
    const layoutGrammarBuildable = !!bundle?.layoutGrammar;
    const layoutGrammarValid = !!layoutValidation?.valid || (layoutGrammarBuildable && !hasErrors(allDiagnostics));
    const valid = surfaceIrBuildable && surfaceIrValid && layoutGrammarBuildable && layoutGrammarValid && !hasErrors(allDiagnostics);
    const surfaceCount = bundle?.extraction?.surfaces?.length || (bundle?.surfaceIR?.surface?.id ? 1 : 0);
    return Object.freeze({
      contractVersion,
      documentKind,
      applicable: true,
      containsSurfaceRidges: true,
      status: valid ? "pass" : "fail",
      valid,
      surfaceCount,
      surfaceIrBuildable,
      surfaceIrValid,
      layoutGrammarBuildable,
      layoutGrammarValid,
      diagnostics: allDiagnostics,
      counts: countBySeverity(allDiagnostics),
      surfaceIR: bundle?.surfaceIR || null,
      layoutGrammar: bundle?.layoutGrammar || null,
      summary: summarizeFlags({
        applicable: true,
        status: valid ? "pass" : "fail",
        surfaceIrBuildable,
        surfaceIrValid,
        layoutGrammarBuildable,
        layoutGrammarValid,
        diagnostics: allDiagnostics
      })
    });
  }

  function analyzeRenderedMarkup(markupText, options) {
    const opts = options || {};
    const text = safeString(markupText);
    const documentKind = opts.documentKind || detectDocumentKind(text);
    const hasRidges = containsSurfaceRidges(text);
    if (!hasRidges) return notApplicable(documentKind, "No MCEL semantic surface ridges were found in the authored document.");

    const deps = dependencyDiagnostics(documentKind === "svg" ? "svg" : "html");
    if (deps.length) return failure(documentKind, deps);

    try {
      const surfaceKind = documentKind === "svg" ? "svg" : "html";
      const bundle = surfaceKind === "svg"
        ? extractorApi.extractSurfaceBundleFromSvg(text, opts.extractorOptions || {})
        : extractorApi.extractSurfaceBundleFromHtml(text, opts.extractorOptions || {});
      return summarizeBundle(bundle, [], surfaceKind, opts);
    } catch (error) {
      return failure(documentKind, [
        diagnostic("authored-surface-markup-analysis-exception", "error", "Authored MCEL markup analysis raised an exception.", {message: error?.message || String(error)})
      ]);
    }
  }

  function analyzeJsonValue(value, options) {
    const opts = options || {};
    const diagnostics = dependencyDiagnostics("json");
    if (diagnostics.length) return failure("json", diagnostics);

    try {
      if (Array.isArray(value)) {
        const irResult = irApi.buildSurfaceIRFromRidges(value, Object.assign({requireSurface: true, defaultSurfaceKind: "semantic-surface"}, opts.irOptions || {}));
        const layoutResult = layoutApi.buildSharedLayoutGrammar(irResult.ir, opts.layoutOptions || {});
        const validation = {
          surface: irApi.validateSurfaceIR(irResult.ir),
          layout: layoutApi.validateSharedLayoutGrammar(irResult.ir, layoutResult.grammar)
        };
        return summarizeBundle({
          surfaceIR: irResult.ir,
          layoutGrammar: layoutResult.grammar,
          validation,
          diagnostics: [...(irResult.diagnostics || []), ...(layoutResult.diagnostics || [])],
          extraction: {surfaces: irResult.ir?.surface?.id ? [{id: irResult.ir.surface.id}] : []}
        }, [], "json", opts);
      }

      if (value && typeof value === "object") {
        if (Array.isArray(value.ridgeRecords)) {
          return analyzeJsonValue(value.ridgeRecords, opts);
        }
        if (value.surfaceIR || value.surfaceIr || value.layoutGrammar) {
          const surfaceIR = value.surfaceIR || value.surfaceIr || null;
          const layoutGrammar = value.layoutGrammar || (surfaceIR ? layoutApi.buildSharedLayoutGrammar(surfaceIR, opts.layoutOptions || {}).grammar : null);
          const validation = {
            surface: surfaceIR ? irApi.validateSurfaceIR(surfaceIR) : {valid: false, diagnostics: [diagnostic("authored-json-surface-ir-missing", "error", "JSON surface bundle does not contain a surfaceIR object.", {})]},
            layout: surfaceIR && layoutGrammar ? layoutApi.validateSharedLayoutGrammar(surfaceIR, layoutGrammar) : {valid: false, diagnostics: [diagnostic("authored-json-layout-grammar-missing", "error", "JSON surface bundle does not contain or build a layoutGrammar object.", {})]}
          };
          return summarizeBundle({
            surfaceIR,
            layoutGrammar,
            validation,
            diagnostics: [...(validation.surface.diagnostics || []), ...(validation.layout.diagnostics || [])],
            extraction: {surfaces: surfaceIR?.surface?.id ? [{id: surfaceIR.surface.id}] : []}
          }, [], "json", opts);
        }
      }

      return notApplicable("json", "JSON document does not contain MCEL ridge records or a surface bundle.");
    } catch (error) {
      return failure("json", [
        diagnostic("authored-surface-json-analysis-exception", "error", "Authored MCEL JSON analysis raised an exception.", {message: error?.message || String(error)})
      ]);
    }
  }

  function analyzeJsonText(sourceText, options) {
    try {
      return analyzeJsonValue(JSON.parse(sourceText), options || {});
    } catch (error) {
      return failure("json", [
        diagnostic("authored-surface-json-parse-error", "error", "Authored JSON could not be parsed.", {message: error?.message || String(error)})
      ]);
    }
  }

  function notApplicable(documentKind, reason) {
    const diagnostics = Object.freeze([
      diagnostic("authored-surface-not-applicable", "ok", reason || "Document does not contain an MCEL authored surface.", {documentKind})
    ]);
    return Object.freeze({
      contractVersion,
      documentKind,
      applicable: false,
      containsSurfaceRidges: false,
      status: "not-applicable",
      valid: true,
      surfaceCount: 0,
      surfaceIrBuildable: false,
      surfaceIrValid: false,
      layoutGrammarBuildable: false,
      layoutGrammarValid: false,
      diagnostics,
      counts: countBySeverity(diagnostics),
      surfaceIR: null,
      layoutGrammar: null,
      summary: "No MCEL authored surface detected."
    });
  }

  function failure(documentKind, diagnostics) {
    const frozen = Object.freeze(diagnostics || []);
    return Object.freeze({
      contractVersion,
      documentKind,
      applicable: true,
      containsSurfaceRidges: true,
      status: "fail",
      valid: false,
      surfaceCount: 0,
      surfaceIrBuildable: false,
      surfaceIrValid: false,
      layoutGrammarBuildable: false,
      layoutGrammarValid: false,
      diagnostics: frozen,
      counts: countBySeverity(frozen),
      surfaceIR: null,
      layoutGrammar: null,
      summary: summarizeFlags({applicable: true, status: "fail", diagnostics: frozen})
    });
  }

  function summarizeFlags(report) {
    const subject = report || {};
    if (subject.applicable === false || subject.status === "not-applicable") return "No MCEL authored surface detected.";
    const status = subject.valid || subject.status === "pass" ? "PASS" : "FAIL";
    const parts = [
      `Authored MCEL: ${status}`,
      `IR ${subject.surfaceIrBuildable && subject.surfaceIrValid ? "PASS" : "FAIL"}`,
      `Layout ${subject.layoutGrammarBuildable && subject.layoutGrammarValid ? "PASS" : "FAIL"}`
    ];
    const counts = countBySeverity(subject.diagnostics || []);
    if (counts.errors) parts.push(`${counts.errors} error${counts.errors === 1 ? "" : "s"}`);
    if (counts.warnings) parts.push(`${counts.warnings} warning${counts.warnings === 1 ? "" : "s"}`);
    return parts.join(" · ");
  }

  function summarizeAnalysis(report) {
    return summarizeFlags(report);
  }

  function analyzeText(sourceText, options) {
    const text = safeString(sourceText);
    const documentKind = (options && options.documentKind) || detectDocumentKind(text);
    if (!containsSurfaceRidges(text)) return notApplicable(documentKind, "No MCEL semantic surface ridges were found in the authored document.");
    if (documentKind === "json") return analyzeJsonText(text, options || {});
    return analyzeRenderedMarkup(text, Object.assign({documentKind}, options || {}));
  }

  function isSurfaceAuthoredDocument(sourceText) {
    const report = analyzeText(sourceText, {quiet: true});
    return !!report.containsSurfaceRidges && report.applicable !== false;
  }

  return Object.freeze({
    contractVersion,
    detectDocumentKind,
    containsSurfaceRidges,
    analyzeText,
    analyzeRenderedMarkup,
    summarizeAnalysis,
    isSurfaceAuthoredDocument
  });
})();

if (typeof window !== "undefined") {
  window.McelAuthoredSurfaceDocument = McelAuthoredSurfaceDocument;
}
