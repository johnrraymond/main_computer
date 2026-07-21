var McelSurfaceRendererInterface = (() => {
  "use strict";

  const contractVersion = "mcel.surface-renderer-interface.v1";
  const surfaceRoundTripContractVersion = "mcel.surface-roundtrip.v1";
  const surfaceIrContractVersion = "mcel.semantic-surface-ir.v1";
  const layoutContractVersion = "mcel.shared-layout-grammar.v1";

  const roundTripApi = (() => {
    if (typeof McelSurfaceRoundTrip !== "undefined") return McelSurfaceRoundTrip;
    if (typeof window !== "undefined" && window.McelSurfaceRoundTrip) return window.McelSurfaceRoundTrip;
    return null;
  })();

  const extractorApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

  const supportedSurfaceKinds = Object.freeze(["html", "svg"]);

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function safeBoolean(value, fallback) {
    if (value === undefined || value === null || value === "") return !!fallback;
    const normalized = safeString(value).toLowerCase();
    if (["true", "1", "yes", "y"].includes(normalized)) return true;
    if (["false", "0", "no", "n"].includes(normalized)) return false;
    return !!fallback;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value === undefined ? null : value));
  }

  function uniqueSortedStrings(values) {
    const seen = new Set();
    const out = [];
    (Array.isArray(values) ? values : [values]).forEach((value) => {
      const text = safeString(value).toLowerCase();
      if (!text || seen.has(text)) return;
      seen.add(text);
      out.push(text);
    });
    return Object.freeze(out.sort());
  }

  function uniqueSortedTokens(values) {
    const seen = new Set();
    const out = [];
    (Array.isArray(values) ? values : [values]).forEach((value) => {
      const text = safeString(value);
      if (!text || seen.has(text)) return;
      seen.add(text);
      out.push(text);
    });
    return Object.freeze(out.sort());
  }

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function combineDiagnostics(items) {
    const out = [];
    (items || []).forEach((item) => {
      if (!item) return;
      if (Array.isArray(item)) {
        item.forEach((nested) => out.push(nested));
        return;
      }
      if (Array.isArray(item.diagnostics)) {
        item.diagnostics.forEach((nested) => out.push(nested));
        return;
      }
      if (item.code && item.severity) out.push(item);
    });
    return Object.freeze(out);
  }

  function hasErrors(diagnostics) {
    return (diagnostics || []).some((item) => item && item.severity === "error");
  }

  function normalizeRendererProfile(profile) {
    const source = profile || {};
    const surfaceKinds = uniqueSortedStrings(source.surfaceKinds || source.supportedSurfaceKinds || source.projections || source.surfaceKind || source.projection || "html");
    const defaultSurfaceKind = safeString(source.defaultSurfaceKind || source.surfaceKind || source.projection || surfaceKinds[0] || "html").toLowerCase();
    const capabilities = uniqueSortedTokens(source.capabilities || []);
    const inputs = Object.assign({
      semanticSurfaceIR: true,
      sharedLayoutGrammar: true
    }, source.inputs || {});
    const output = Object.assign({
      emitsSemanticRidges: true,
      emitsLayoutRidges: true,
      authoritativeSurface: true,
      rendererAttribution: true,
      projectionAttribution: true
    }, source.output || {});

    return Object.freeze({
      contractVersion,
      id: safeString(source.id || source.rendererId),
      label: safeString(source.label || source.name || source.id || source.rendererId),
      version: safeString(source.version || "1"),
      defaultSurfaceKind,
      surfaceKinds,
      capabilities,
      inputs: Object.freeze({
        semanticSurfaceIR: safeBoolean(inputs.semanticSurfaceIR, true),
        sharedLayoutGrammar: safeBoolean(inputs.sharedLayoutGrammar, true)
      }),
      output: Object.freeze({
        emitsSemanticRidges: safeBoolean(output.emitsSemanticRidges, true),
        emitsLayoutRidges: safeBoolean(output.emitsLayoutRidges, true),
        authoritativeSurface: safeBoolean(output.authoritativeSurface, true),
        rendererAttribution: safeBoolean(output.rendererAttribution, true),
        projectionAttribution: safeBoolean(output.projectionAttribution, true)
      })
    });
  }

  function validateRendererProfile(profile) {
    const normalized = normalizeRendererProfile(profile);
    const diagnostics = [];

    if (!normalized.id) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-missing-id",
        "error",
        "A surface renderer profile must declare a stable id."
      ));
    }
    if (!normalized.surfaceKinds.length) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-missing-surface-kind",
        "error",
        "A surface renderer profile must declare at least one supported surface kind."
      ));
    }
    normalized.surfaceKinds.forEach((kind) => {
      if (!supportedSurfaceKinds.includes(kind)) {
        diagnostics.push(diagnostic(
          "surface-renderer-profile-unsupported-surface-kind",
          "error",
          "A surface renderer profile declared an unsupported surface kind.",
          {rendererId: normalized.id, surfaceKind: kind, supportedSurfaceKinds}
        ));
      }
    });
    if (!normalized.surfaceKinds.includes(normalized.defaultSurfaceKind)) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-default-kind-not-supported",
        "error",
        "A surface renderer default kind must be listed in supported surface kinds.",
        {rendererId: normalized.id, defaultSurfaceKind: normalized.defaultSurfaceKind, surfaceKinds: normalized.surfaceKinds}
      ));
    }
    if (!normalized.inputs.semanticSurfaceIR) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-missing-semantic-ir-input",
        "error",
        "A renderer in the MCEL surface pathway must accept SemanticSurfaceIR."
      ));
    }
    if (!normalized.inputs.sharedLayoutGrammar) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-missing-layout-input",
        "error",
        "A renderer in the MCEL surface pathway must accept SharedLayoutGrammar."
      ));
    }
    if (!normalized.output.emitsSemanticRidges) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-does-not-emit-semantic-ridges",
        "error",
        "A renderer in the MCEL surface pathway must emit semantic ridge attributes."
      ));
    }
    if (!normalized.output.emitsLayoutRidges) {
      diagnostics.push(diagnostic(
        "surface-renderer-profile-does-not-emit-layout-ridges",
        "error",
        "A renderer in the MCEL surface pathway must emit layout ridge attributes."
      ));
    }

    return Object.freeze({
      contractVersion,
      profile: normalized,
      valid: !hasErrors(diagnostics),
      status: hasErrors(diagnostics) ? "fail" : "pass",
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function validateRendererImplementation(renderer) {
    const source = renderer || {};
    const profileValidation = validateRendererProfile(source.profile || source.rendererProfile || source);
    const diagnostics = [...profileValidation.diagnostics];

    if (typeof source.render !== "function") {
      diagnostics.push(diagnostic(
        "surface-renderer-implementation-missing-render-function",
        "error",
        "A surface renderer implementation must expose render(request).",
        {rendererId: profileValidation.profile.id}
      ));
    }

    return Object.freeze({
      contractVersion,
      profile: profileValidation.profile,
      valid: !hasErrors(diagnostics),
      status: hasErrors(diagnostics) ? "fail" : "pass",
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function normalizeRenderOutput(output, requestedSurfaceKind) {
    if (typeof output === "string") {
      return Object.freeze({
        renderedText: output,
        surfaceKind: requestedSurfaceKind,
        diagnostics: Object.freeze([])
      });
    }
    const source = output || {};
    const renderedText = safeString(source.renderedText || source.text || source.html || source.svg || "");
    const surfaceKind = safeString(source.surfaceKind || source.kind || requestedSurfaceKind).toLowerCase();
    return Object.freeze({
      renderedText,
      surfaceKind,
      diagnostics: Object.freeze([...(source.diagnostics || [])])
    });
  }

  function extractRendererAttribution(roundTripReport) {
    const surface = roundTripReport
      && roundTripReport.extractedBundle
      && roundTripReport.extractedBundle.surfaceIR
      && roundTripReport.extractedBundle.surfaceIR.surface
      ? roundTripReport.extractedBundle.surfaceIR.surface
      : {};
    return Object.freeze({
      renderer: safeString(surface.renderer),
      projection: safeString(surface.projection)
    });
  }

  function verifyRendererOutput(input) {
    const settings = input || {};
    const profileValidation = validateRendererProfile(settings.profile || {});
    const profile = profileValidation.profile;
    const surfaceKind = safeString(settings.surfaceKind || profile.defaultSurfaceKind || "html").toLowerCase();
    const diagnostics = [...profileValidation.diagnostics];

    if (!profile.surfaceKinds.includes(surfaceKind)) {
      diagnostics.push(diagnostic(
        "surface-renderer-output-kind-not-supported",
        "error",
        "Rendered output used a surface kind not declared by the renderer profile.",
        {rendererId: profile.id, surfaceKind, surfaceKinds: profile.surfaceKinds}
      ));
    }
    if (!safeString(settings.renderedText)) {
      diagnostics.push(diagnostic(
        "surface-renderer-output-empty",
        "error",
        "A surface renderer returned empty output.",
        {rendererId: profile.id, surfaceKind}
      ));
    }
    if (!roundTripApi || typeof roundTripApi.verifyRenderedSurfaceRoundTrip !== "function") {
      diagnostics.push(diagnostic(
        "surface-renderer-missing-roundtrip-api",
        "error",
        "Renderer output verification requires McelSurfaceRoundTrip."
      ));
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        profile,
        surfaceKind,
        diagnostics: Object.freeze(diagnostics)
      });
    }

    let roundTrip = null;
    if (!hasErrors(diagnostics)) {
      roundTrip = roundTripApi.verifyRenderedSurfaceRoundTrip({
        expectedSurfaceIr: settings.expectedSurfaceIr || settings.expectedSurfaceIR || settings.surfaceIR,
        expectedLayoutGrammar: settings.expectedLayoutGrammar || settings.layoutGrammar,
        renderedText: settings.renderedText,
        surfaceKind,
        extractorOptions: settings.extractorOptions || {}
      });
      diagnostics.push(...(roundTrip.diagnostics || []));
      if (!roundTrip.valid) {
        diagnostics.push(diagnostic(
          "surface-renderer-output-roundtrip-failed",
          "error",
          "Rendered output did not preserve the expected MCEL semantic surface and layout grammar.",
          {rendererId: profile.id, surfaceKind}
        ));
      }

      const attribution = extractRendererAttribution(roundTrip);
      if (profile.output.rendererAttribution && attribution.renderer !== profile.id) {
        diagnostics.push(diagnostic(
          "surface-renderer-attribution-mismatch",
          "error",
          "Rendered output must identify the renderer profile that produced it.",
          {expectedRenderer: profile.id, actualRenderer: attribution.renderer}
        ));
      }
      if (profile.output.projectionAttribution && attribution.projection !== surfaceKind) {
        diagnostics.push(diagnostic(
          "surface-renderer-projection-mismatch",
          "error",
          "Rendered output must identify the projection/surface kind that produced it.",
          {expectedProjection: surfaceKind, actualProjection: attribution.projection}
        ));
      }
    }

    return Object.freeze({
      contractVersion,
      surfaceRoundTripContractVersion,
      surfaceIrContractVersion,
      layoutContractVersion,
      profile,
      surfaceKind,
      roundTrip,
      valid: !hasErrors(diagnostics),
      status: hasErrors(diagnostics) ? "fail" : "pass",
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function renderWithRenderer(renderer, request) {
    const source = renderer || {};
    const settings = request || {};
    const implementation = validateRendererImplementation(source);
    const diagnostics = [...implementation.diagnostics];
    const profile = implementation.profile;
    const surfaceKind = safeString(settings.surfaceKind || profile.defaultSurfaceKind || "html").toLowerCase();

    if (!profile.surfaceKinds.includes(surfaceKind)) {
      diagnostics.push(diagnostic(
        "surface-renderer-request-kind-not-supported",
        "error",
        "A render request selected a surface kind not supported by the renderer.",
        {rendererId: profile.id, surfaceKind, surfaceKinds: profile.surfaceKinds}
      ));
    }

    if (hasErrors(diagnostics)) {
      return Object.freeze({
        contractVersion,
        profile,
        surfaceKind,
        renderedText: "",
        valid: false,
        status: "fail",
        diagnostics: Object.freeze(diagnostics)
      });
    }

    let rawOutput;
    try {
      rawOutput = source.render(Object.freeze({
        surfaceIR: settings.surfaceIR || settings.expectedSurfaceIr || settings.expectedSurfaceIR,
        layoutGrammar: settings.layoutGrammar || settings.expectedLayoutGrammar,
        surfaceKind,
        profile,
        options: Object.freeze(Object.assign({}, settings.options || {}))
      }));
    } catch (error) {
      diagnostics.push(diagnostic(
        "surface-renderer-render-threw",
        "error",
        "A surface renderer threw while rendering.",
        {rendererId: profile.id, message: error && error.message || String(error)}
      ));
    }

    const output = normalizeRenderOutput(rawOutput, surfaceKind);
    diagnostics.push(...output.diagnostics);

    let outputVerification = null;
    if (!hasErrors(diagnostics) && settings.verifyOutput !== false) {
      outputVerification = verifyRendererOutput({
        profile,
        surfaceKind: output.surfaceKind,
        renderedText: output.renderedText,
        expectedSurfaceIr: settings.surfaceIR || settings.expectedSurfaceIr || settings.expectedSurfaceIR,
        expectedLayoutGrammar: settings.layoutGrammar || settings.expectedLayoutGrammar,
        extractorOptions: settings.extractorOptions || {}
      });
      diagnostics.push(...outputVerification.diagnostics);
    }

    return Object.freeze({
      contractVersion,
      profile,
      surfaceKind: output.surfaceKind,
      renderedText: output.renderedText,
      outputVerification,
      valid: !hasErrors(diagnostics),
      status: hasErrors(diagnostics) ? "fail" : "pass",
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function verifyRendererPairAgreement(htmlResult, svgResult, options) {
    const settings = options || {};
    const diagnostics = [];
    if (!htmlResult || !safeString(htmlResult.renderedText)) {
      diagnostics.push(diagnostic(
        "surface-renderer-pair-missing-html-output",
        "error",
        "Renderer pair agreement requires HTML renderer output."
      ));
    }
    if (!svgResult || !safeString(svgResult.renderedText)) {
      diagnostics.push(diagnostic(
        "surface-renderer-pair-missing-svg-output",
        "error",
        "Renderer pair agreement requires SVG renderer output."
      ));
    }
    if (!roundTripApi || typeof roundTripApi.verifyHtmlAndSvgAgree !== "function") {
      diagnostics.push(diagnostic(
        "surface-renderer-pair-missing-roundtrip-api",
        "error",
        "Renderer pair agreement requires McelSurfaceRoundTrip."
      ));
    }
    if (hasErrors(diagnostics)) {
      return Object.freeze({
        contractVersion,
        valid: false,
        status: "fail",
        diagnostics: Object.freeze(diagnostics)
      });
    }

    const agreement = roundTripApi.verifyHtmlAndSvgAgree(
      htmlResult.renderedText,
      svgResult.renderedText,
      settings
    );
    diagnostics.push(...(agreement.diagnostics || []));
    if (!agreement.valid) {
      diagnostics.push(diagnostic(
        "surface-renderer-pair-roundtrip-mismatch",
        "error",
        "HTML and SVG renderer outputs did not extract to the same MCEL semantic surface and layout grammar."
      ));
    }

    return Object.freeze({
      contractVersion,
      agreement,
      valid: !hasErrors(diagnostics),
      status: hasErrors(diagnostics) ? "fail" : "pass",
      diagnostics: Object.freeze(diagnostics)
    });
  }

  function rendererProfileFingerprint(profile) {
    return JSON.stringify(normalizeRendererProfile(profile));
  }

  function createRendererProfile(profile) {
    return normalizeRendererProfile(profile);
  }

  return Object.freeze({
    contractVersion,
    surfaceRoundTripContractVersion,
    surfaceIrContractVersion,
    layoutContractVersion,
    supportedSurfaceKinds,
    createRendererProfile,
    normalizeRendererProfile,
    validateRendererProfile,
    validateRendererImplementation,
    verifyRendererOutput,
    renderWithRenderer,
    verifyRendererPairAgreement,
    rendererProfileFingerprint
  });
})();

if (typeof window !== "undefined") {
  window.McelSurfaceRendererInterface = McelSurfaceRendererInterface;
}
