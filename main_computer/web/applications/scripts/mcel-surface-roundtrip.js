var McelSurfaceRoundTrip = (() => {
  "use strict";

  const contractVersion = "mcel.surface-roundtrip.v1";
  const surfaceExtractorContractVersion = "mcel.surface-extractors.v1";
  const surfaceIrContractVersion = "mcel.semantic-surface-ir.v1";
  const layoutContractVersion = "mcel.shared-layout-grammar.v1";

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

  const extractorApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

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

  function dependencyDiagnostics() {
    const diagnostics = [];
    if (!irApi) {
      diagnostics.push(diagnostic(
        "roundtrip-missing-semantic-surface-ir-api",
        "error",
        "MCEL round-trip verification requires McelSemanticSurfaceIR."
      ));
    }
    if (!layoutApi) {
      diagnostics.push(diagnostic(
        "roundtrip-missing-shared-layout-grammar-api",
        "error",
        "MCEL round-trip verification requires McelSharedLayoutGrammar."
      ));
    }
    if (!extractorApi) {
      diagnostics.push(diagnostic(
        "roundtrip-missing-surface-extractor-api",
        "error",
        "MCEL round-trip verification requires McelSurfaceExtractors."
      ));
    }
    return diagnostics;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value === undefined ? null : value));
  }

  function sortById(items) {
    return Object.freeze([...(items || [])].map((item) => Object.freeze(clone(item))).sort((a, b) => {
      return safeString(a.id).localeCompare(safeString(b.id));
    }));
  }

  function sortedStrings(items) {
    return Object.freeze([...(items || [])].map(safeString).filter(Boolean).sort());
  }

  function normalizeEdge(edge) {
    const copy = clone(edge || {});
    if (Array.isArray(copy.allowedInferences)) copy.allowedInferences = sortedStrings(copy.allowedInferences);
    if (Array.isArray(copy.forbiddenInferences)) copy.forbiddenInferences = sortedStrings(copy.forbiddenInferences);
    return Object.freeze(copy);
  }

  function unwrapSurfaceIR(subject) {
    if (!subject) return null;
    if (subject.surfaceIR) return subject.surfaceIR;
    if (subject.ir) return subject.ir;
    return subject;
  }

  function unwrapLayoutGrammar(subject) {
    if (!subject) return null;
    if (subject.layoutGrammar) return subject.layoutGrammar;
    if (subject.grammar) return subject.grammar;
    return subject;
  }

  function canonicalSurfaceGraph(subject) {
    const source = unwrapSurfaceIR(subject);
    const canonical = irApi && typeof irApi.canonicalizeSurfaceIR === "function"
      ? irApi.canonicalizeSurfaceIR(source)
      : source;

    const surface = canonical && canonical.surface ? canonical.surface : {};
    const graph = canonical && canonical.graph ? canonical.graph : {};
    return Object.freeze({
      surface: Object.freeze({
        id: safeString(surface.id),
        kind: safeString(surface.kind),
        role: safeString(surface.role),
        contract: safeString(surface.contract)
      }),
      graph: Object.freeze({
        nodes: sortById(graph.nodes || []),
        edges: Object.freeze([...(graph.edges || [])].map(normalizeEdge).sort((a, b) => safeString(a.id).localeCompare(safeString(b.id)))),
        regions: sortById(graph.regions || []),
        controls: sortById(graph.controls || [])
      })
    });
  }

  function canonicalLayout(subject) {
    const source = unwrapLayoutGrammar(subject);
    if (layoutApi && typeof layoutApi.canonicalizeLayoutGrammar === "function") {
      return layoutApi.canonicalizeLayoutGrammar(source);
    }
    return source || {};
  }

  function stableFingerprint(value) {
    return JSON.stringify(value);
  }

  function diffObjects(expected, actual, path, limit, results) {
    if (results.length >= limit) return;
    if (Object.is(expected, actual)) return;

    const expectedIsArray = Array.isArray(expected);
    const actualIsArray = Array.isArray(actual);
    if (expectedIsArray || actualIsArray) {
      if (!expectedIsArray || !actualIsArray) {
        results.push({path, expected, actual});
        return;
      }
      if (expected.length !== actual.length) {
        results.push({path: `${path}.length`, expected: expected.length, actual: actual.length});
        if (results.length >= limit) return;
      }
      const max = Math.max(expected.length, actual.length);
      for (let index = 0; index < max && results.length < limit; index += 1) {
        diffObjects(expected[index], actual[index], `${path}[${index}]`, limit, results);
      }
      return;
    }

    const expectedIsObject = expected && typeof expected === "object";
    const actualIsObject = actual && typeof actual === "object";
    if (expectedIsObject || actualIsObject) {
      if (!expectedIsObject || !actualIsObject) {
        results.push({path, expected, actual});
        return;
      }
      const keys = sortedStrings([...Object.keys(expected), ...Object.keys(actual)]);
      const seen = new Set();
      for (const key of keys) {
        if (seen.has(key)) continue;
        seen.add(key);
        diffObjects(expected[key], actual[key], path ? `${path}.${key}` : key, limit, results);
        if (results.length >= limit) return;
      }
      return;
    }

    results.push({path, expected, actual});
  }

  function firstDiffs(expected, actual, limit) {
    const results = [];
    diffObjects(expected, actual, "", limit || 8, results);
    return Object.freeze(results.map((item) => Object.freeze(item)));
  }

  function compareSemanticGraphs(expected, actual, options) {
    const settings = Object.assign({diffLimit: 8}, options || {});
    const expectedCanonical = canonicalSurfaceGraph(expected);
    const actualCanonical = canonicalSurfaceGraph(actual);
    const expectedFingerprint = stableFingerprint(expectedCanonical);
    const actualFingerprint = stableFingerprint(actualCanonical);
    const valid = expectedFingerprint === actualFingerprint;
    const diagnostics = valid ? [] : [
      diagnostic(
        "semantic-surface-roundtrip-mismatch",
        "error",
        "Extracted semantic surface graph does not match the expected canonical graph.",
        {
          diffs: firstDiffs(expectedCanonical, actualCanonical, settings.diffLimit),
          expectedFingerprint,
          actualFingerprint
        }
      )
    ];

    return Object.freeze({
      kind: "semantic",
      valid,
      expectedFingerprint,
      actualFingerprint,
      expected: expectedCanonical,
      actual: actualCanonical,
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function compareLayoutGrammars(expected, actual, options) {
    const settings = Object.assign({diffLimit: 8}, options || {});
    const expectedCanonical = canonicalLayout(expected);
    const actualCanonical = canonicalLayout(actual);
    const expectedFingerprint = stableFingerprint(expectedCanonical);
    const actualFingerprint = stableFingerprint(actualCanonical);
    const valid = expectedFingerprint === actualFingerprint;
    const diagnostics = valid ? [] : [
      diagnostic(
        "layout-grammar-roundtrip-mismatch",
        "error",
        "Extracted layout grammar does not match the expected canonical layout grammar.",
        {
          diffs: firstDiffs(expectedCanonical, actualCanonical, settings.diffLimit),
          expectedFingerprint,
          actualFingerprint
        }
      )
    ];

    return Object.freeze({
      kind: "layout",
      valid,
      expectedFingerprint,
      actualFingerprint,
      expected: expectedCanonical,
      actual: actualCanonical,
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function extractBundle(renderedText, surfaceKind, options) {
    if (!extractorApi) {
      return {
        bundle: null,
        diagnostics: dependencyDiagnostics()
      };
    }
    const settings = options || {};
    if (surfaceKind === "html") {
      return {
        bundle: extractorApi.extractSurfaceBundleFromHtml(renderedText, settings),
        diagnostics: []
      };
    }
    if (surfaceKind === "svg") {
      return {
        bundle: extractorApi.extractSurfaceBundleFromSvg(renderedText, settings),
        diagnostics: []
      };
    }
    return {
      bundle: null,
      diagnostics: [
        diagnostic(
          "roundtrip-unsupported-surface-kind",
          "error",
          "Round-trip verification only supports html and svg rendered surfaces in this contract.",
          {surfaceKind: safeString(surfaceKind)}
        )
      ]
    };
  }

  function combineDiagnostics(parts) {
    return Object.freeze(parts.flatMap((part) => {
      if (!part) return [];
      if (Array.isArray(part)) return part;
      if (part.diagnostics) return [...part.diagnostics];
      return [];
    }));
  }

  function verifyRenderedSurfaceRoundTrip(input) {
    const settings = Object.assign({surfaceKind: "html"}, input || {});
    const dependencyIssues = dependencyDiagnostics();
    if (dependencyIssues.length) {
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        surfaceKind: safeString(settings.surfaceKind),
        diagnostics: Object.freeze(dependencyIssues)
      });
    }

    const extraction = extractBundle(settings.renderedText || "", settings.surfaceKind, settings.extractorOptions || {});
    if (extraction.diagnostics.length || !extraction.bundle) {
      const diagnostics = Object.freeze(extraction.diagnostics);
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        surfaceKind: safeString(settings.surfaceKind),
        diagnostics
      });
    }

    const expectedSurface = settings.expectedSurfaceIr || settings.expectedSurfaceIR || settings.expectedSurface || extraction.bundle.surfaceIR;
    const expectedLayout = settings.expectedLayoutGrammar || settings.expectedLayout || extraction.bundle.layoutGrammar;

    const semantic = compareSemanticGraphs(expectedSurface, extraction.bundle.surfaceIR, settings.compareOptions || {});
    const layout = compareLayoutGrammars(expectedLayout, extraction.bundle.layoutGrammar, settings.compareOptions || {});
    const diagnostics = combineDiagnostics([
      extraction.bundle,
      extraction.bundle.validation && extraction.bundle.validation.surface,
      extraction.bundle.validation && extraction.bundle.validation.layout,
      semantic,
      layout
    ]);
    const valid = extraction.bundle.valid && semantic.valid && layout.valid && diagnostics.filter((item) => item.severity === "error").length === 0;

    return Object.freeze({
      contractVersion,
      surfaceExtractorContractVersion,
      surfaceIrContractVersion,
      layoutContractVersion,
      valid,
      status: valid ? "pass" : "fail",
      surfaceKind: safeString(settings.surfaceKind),
      extractedBundle: extraction.bundle,
      semantic,
      layout,
      diagnostics
    });
  }

  function verifyHtmlAndSvgAgree(htmlText, svgText, options) {
    const settings = options || {};
    const dependencyIssues = dependencyDiagnostics();
    if (dependencyIssues.length) {
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        diagnostics: Object.freeze(dependencyIssues)
      });
    }

    const htmlExtraction = extractBundle(htmlText || "", "html", settings.htmlOptions || settings.extractorOptions || {});
    const svgExtraction = extractBundle(svgText || "", "svg", settings.svgOptions || settings.extractorOptions || {});
    const initialDiagnostics = combineDiagnostics([htmlExtraction, svgExtraction]);

    if (initialDiagnostics.filter((item) => item.severity === "error").length || !htmlExtraction.bundle || !svgExtraction.bundle) {
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        htmlBundle: htmlExtraction.bundle,
        svgBundle: svgExtraction.bundle,
        diagnostics: initialDiagnostics
      });
    }

    const semantic = compareSemanticGraphs(htmlExtraction.bundle.surfaceIR, svgExtraction.bundle.surfaceIR, settings.compareOptions || {});
    const layout = compareLayoutGrammars(htmlExtraction.bundle.layoutGrammar, svgExtraction.bundle.layoutGrammar, settings.compareOptions || {});
    const diagnostics = combineDiagnostics([
      htmlExtraction.bundle,
      svgExtraction.bundle,
      htmlExtraction.bundle.validation && htmlExtraction.bundle.validation.surface,
      htmlExtraction.bundle.validation && htmlExtraction.bundle.validation.layout,
      svgExtraction.bundle.validation && svgExtraction.bundle.validation.surface,
      svgExtraction.bundle.validation && svgExtraction.bundle.validation.layout,
      semantic,
      layout
    ]);
    const valid = htmlExtraction.bundle.valid && svgExtraction.bundle.valid && semantic.valid && layout.valid && diagnostics.filter((item) => item.severity === "error").length === 0;

    return Object.freeze({
      contractVersion,
      surfaceExtractorContractVersion,
      surfaceIrContractVersion,
      layoutContractVersion,
      valid,
      status: valid ? "pass" : "fail",
      htmlBundle: htmlExtraction.bundle,
      svgBundle: svgExtraction.bundle,
      semantic,
      layout,
      diagnostics
    });
  }

  function summarizeRoundTrip(report) {
    const source = report || {};
    const diagnostics = [...(source.diagnostics || [])];
    return Object.freeze({
      contractVersion,
      status: source.status || (source.valid ? "pass" : "fail"),
      valid: !!source.valid,
      errorCount: diagnostics.filter((item) => item.severity === "error").length,
      warningCount: diagnostics.filter((item) => item.severity === "warning").length,
      diagnosticCodes: Object.freeze(diagnostics.map((item) => item.code).sort()),
      semanticFingerprint: source.semantic && source.semantic.actualFingerprint || "",
      layoutFingerprint: source.layout && source.layout.actualFingerprint || ""
    });
  }

  return Object.freeze({
    contractVersion,
    surfaceExtractorContractVersion,
    surfaceIrContractVersion,
    layoutContractVersion,
    canonicalSurfaceGraph,
    canonicalLayout,
    compareSemanticGraphs,
    compareLayoutGrammars,
    verifyRenderedSurfaceRoundTrip,
    verifyHtmlAndSvgAgree,
    summarizeRoundTrip
  });
})();

if (typeof window !== "undefined") {
  window.McelSurfaceRoundTrip = McelSurfaceRoundTrip;
}
