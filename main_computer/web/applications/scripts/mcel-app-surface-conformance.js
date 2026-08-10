var McelAppSurfaceConformance = (() => {
  "use strict";

  const contractVersion = "mcel.app-surface-conformance.v1";

  const BASELINE_LAYERS = Object.freeze([
    "semantic-surface",
    "layout-grammar",
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw"
  ]);

  const extractorApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

  const registryApi = (() => {
    if (typeof McelAppSurfaceRegistry !== "undefined") return McelAppSurfaceRegistry;
    if (typeof window !== "undefined" && window.McelAppSurfaceRegistry) return window.McelAppSurfaceRegistry;
    if (typeof window !== "undefined" && window.MCEL?.appSurfaceRegistry) return window.MCEL.appSurfaceRegistry;
    return null;
  })();

  function applicationPackageCatalogApi() {
    if (typeof McelApplicationPackages !== "undefined") return McelApplicationPackages;
    if (typeof window !== "undefined" && window.McelApplicationPackages) return window.McelApplicationPackages;
    if (typeof window !== "undefined" && window.MCEL?.applicationPackages) return window.MCEL.applicationPackages;
    return null;
  }

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value);
  }

  function freezeArray(items) {
    return Object.freeze([...(items || [])]);
  }

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function compactDiagnostic(item) {
    return diagnostic(
      safeString(item?.code || "app-surface-conformance-diagnostic"),
      safeString(item?.severity || "warning"),
      safeString(item?.finding || "MCEL app surface conformance diagnostic."),
      item?.detail || {}
    );
  }

  function layer(id, status, finding, detail) {
    return Object.freeze({
      id,
      status,
      valid: status === "pass",
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function statusFromValidation(valid, unavailable) {
    if (unavailable) return "unavailable";
    return valid ? "pass" : "fail";
  }

  function hasCriticalFailure(layers) {
    return layers.some((item) => item.status === "fail");
  }

  function severityCounts(findings) {
    return freezeArray(findings).reduce(
      (counts, finding) => {
        if (finding?.severity === "critical" || finding?.severity === "error") counts.errors += 1;
        else if (finding?.severity === "warning") counts.warnings += 1;
        else counts.info += 1;
        return counts;
      },
      {errors: 0, warnings: 0, info: 0}
    );
  }

  function uniqueStrings(values) {
    return freezeArray([...(new Set((values || []).map((value) => safeString(value).trim()).filter(Boolean)))]);
  }

  const DECLARED_SURFACE_BUNDLE_SCHEMA = "mcel.application-surface-bundle.v1";
  const CODE_EDITOR_INTENTS = Object.freeze([
    "applyReviewedPatch",
    "closeFile",
    "discardDraft",
    "editDraft",
    "inspectWorkspace",
    "openFile",
    "previewAiderPlan",
    "saveFile"
  ]);

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function isPlainObject(value) {
    return Boolean(value && typeof value === "object" && !Array.isArray(value));
  }

  function selectorList(selector) {
    return safeString(selector)
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  }

  function escapeRegExp(value) {
    return safeString(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function getDocument() {
    try {
      if (typeof document !== "undefined" && document?.querySelector) return document;
    } catch {}
    try {
      if (typeof window !== "undefined" && window.document?.querySelector) return window.document;
    } catch {}
    return null;
  }

  function elementMatches(el, selector) {
    if (!el || typeof el.matches !== "function" || !selector) return false;
    try {
      return el.matches(selector);
    } catch {
      return false;
    }
  }

  function queryAll(selector, root = null) {
    const base = root?.querySelectorAll ? root : getDocument();
    if (!base || !selector) return null;
    const matches = [];
    for (const part of selectorList(selector)) {
      try {
        if (elementMatches(base, part)) matches.push(base);
        matches.push(...Array.from(base.querySelectorAll(part)));
      } catch {}
    }
    return matches;
  }

  function selectorExistsInDom(selector, root = null) {
    const matches = queryAll(selector, root);
    return matches ? matches.length > 0 : null;
  }

  function htmlContainsId(html, id) {
    return new RegExp(`\\bid\\s*=\\s*["']${escapeRegExp(id)}["']`, "i").test(html);
  }

  function htmlContainsClass(html, className) {
    return new RegExp(`\\bclass\\s*=\\s*["'][^"']*\\b${escapeRegExp(className)}\\b[^"']*["']`, "i").test(html);
  }

  function htmlContainsAttribute(html, name, value = null) {
    const attr = escapeRegExp(name);
    if (value === null || value === undefined) {
      return new RegExp(`\\b${attr}(\\s*=|\\s|>|/)`, "i").test(html);
    }
    const escaped = escapeRegExp(value);
    return new RegExp(`\\b${attr}\\s*=\\s*["']${escaped}["']`, "i").test(html) ||
      new RegExp(`\\b${attr}\\s*=\\s*${escaped}(\\s|>|/)`, "i").test(html);
  }

  function simpleSelectorExistsInHtml(part, html) {
    if (!part || !html) return null;
    const idMatch = /^#([A-Za-z0-9_-]+)$/.exec(part);
    if (idMatch) return htmlContainsId(html, idMatch[1]);

    const classMatch = /^\.([A-Za-z0-9_-]+)$/.exec(part);
    if (classMatch) return htmlContainsClass(html, classMatch[1]);

    const attrMatch = /^\[\s*([A-Za-z0-9:_-]+)(?:\s*=\s*["']?([^"'\]]+)["']?)?\s*\]$/.exec(part);
    if (attrMatch) return htmlContainsAttribute(html, attrMatch[1], attrMatch[2] ?? null);

    const tagClassMatch = /^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$/.exec(part);
    if (tagClassMatch) {
      return part
        .split(".")
        .slice(1)
        .every((className) => htmlContainsClass(html, className));
    }

    return null;
  }

  function selectorExistsInHtml(selector, html) {
    const source = safeString(html);
    if (!source.trim()) return null;
    let sawUnknown = false;
    for (const part of selectorList(selector)) {
      const exists = simpleSelectorExistsInHtml(part, source);
      if (exists === true) return true;
      if (exists === null) sawUnknown = true;
    }
    return sawUnknown ? null : false;
  }

  function observedBoxes(report) {
    const measurements = report?.measurements || {};
    const boxes = [];
    for (const collection of [measurements.requiredRegions, measurements.optionalRegions, measurements.surfaces]) {
      if (!collection || typeof collection !== "object") continue;
      for (const value of Object.values(collection)) {
        if (value && typeof value === "object") boxes.push(value);
      }
    }
    for (const entry of safeArray(measurements.forbiddenRegions)) {
      if (entry?.box) boxes.push(entry.box);
    }
    for (const entry of safeArray(measurements.ownerChain)) {
      if (entry && typeof entry === "object") boxes.push(entry);
    }
    const primary = primarySurface(report);
    if (primary.host) boxes.push(primary.host);
    if (primary.editor) boxes.push(primary.editor);
    return boxes;
  }

  function selectorPartMatchesObserved(part, observedSelector) {
    const actual = safeString(observedSelector);
    if (!part || !actual) return false;
    if (actual === part) return true;
    if (part.startsWith("#")) return actual.includes(part);
    if (part.startsWith(".")) {
      const className = part.slice(1);
      return actual === part || actual.split(/\s+/).some((token) => token === part) || actual.includes(`.${className}`);
    }
    return actual.includes(part);
  }

  function selectorExistsInReport(selector, report) {
    if (!report || typeof report !== "object") return null;
    const parts = selectorList(selector);
    const matches = observedBoxes(report).filter((box) =>
      parts.some((part) => selectorPartMatchesObserved(part, box?.selector))
    );
    if (!matches.length) return null;
    return matches.some((box) => box?.exists !== false);
  }

  function selectorExists(selector, options = {}) {
    const domExists = selectorExistsInDom(selector, options.root);
    if (domExists !== null) return domExists;
    const htmlExists = selectorExistsInHtml(selector, options.surfaceHtml || options.html || "");
    if (htmlExists !== null) return htmlExists;
    return selectorExistsInReport(selector, options.report);
  }

  function visibleElement(el) {
    if (!el) return false;
    try {
      if (el.hidden || el.getAttribute?.("aria-hidden") === "true") return false;
      const style = typeof getComputedStyle === "function" ? getComputedStyle(el) : {};
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
      const rect = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : {};
      return Number(rect.width || el.offsetWidth || 0) > 0 && Number(rect.height || el.offsetHeight || 0) > 0;
    } catch {
      return false;
    }
  }

  function selectorHasVisibleMatchesInDom(selector, root = null) {
    const matches = queryAll(selector, root);
    return matches ? matches.some(visibleElement) : null;
  }

  function selectorHasVisibleMatchesInReport(selector, report) {
    if (!report || typeof report !== "object") return null;
    const parts = selectorList(selector);
    const matches = observedBoxes(report).filter((box) =>
      parts.some((part) => selectorPartMatchesObserved(part, box?.selector))
    );
    if (!matches.length) return null;
    return matches.some((box) => box?.exists && box.visible);
  }

  function selectorHasVisibleMatches(selector, options = {}) {
    const domVisible = selectorHasVisibleMatchesInDom(selector, options.root);
    if (domVisible !== null) return domVisible;
    return selectorHasVisibleMatchesInReport(selector, options.report);
  }

  function trackListHasZeroWidth(trackList) {
    const value = safeString(trackList).trim();
    if (!value) return false;
    return /(^|\s)0(?:px|fr|rem|em|%)?(\s|$)/.test(value);
  }

  function selectorTrackList(selector, options = {}) {
    const matches = queryAll(selector, options.root);
    if (matches && matches.length) {
      try {
        const style = typeof getComputedStyle === "function" ? getComputedStyle(matches[0]) : {};
        return safeString(style.gridTemplateColumns || "");
      } catch {}
    }
    for (const box of observedBoxes(options.report)) {
      if (selectorList(selector).some((part) => selectorPartMatchesObserved(part, box?.selector))) {
        return safeString(box.gridTemplateColumns || "");
      }
    }
    return "";
  }

  function knownIntentIds(bundle, options = {}) {
    if (Array.isArray(options.knownIntentIds)) return new Set(uniqueStrings(options.knownIntentIds));
    if (Array.isArray(bundle?.intentIds)) return new Set(uniqueStrings(bundle.intentIds));
    if (Array.isArray(bundle?.intents)) {
      return new Set(uniqueStrings(bundle.intents.map((item) => isPlainObject(item) ? (item.sourceName || item.id) : item)));
    }
    if (isPlainObject(bundle?.intents)) return new Set(Object.keys(bundle.intents));
    if (safeString(bundle?.appId) === "code-editor") return new Set(CODE_EDITOR_INTENTS);
    return null;
  }

  function duplicateIds(items) {
    const seen = new Set();
    const duplicates = new Set();
    for (const item of safeArray(items)) {
      const id = safeString(item?.id);
      if (!id) continue;
      if (seen.has(id)) duplicates.add(id);
      seen.add(id);
    }
    return [...duplicates].sort();
  }

  function fallbackPolicy(appId) {
    const safeAppId = safeString(appId);
    return Object.freeze({
      appId: safeAppId,
      label: safeAppId,
      state: "unregistered",
      conformanceRequired: false,
      maturity: "unregistered",
      surfaceId: "",
      contractId: "",
      requiredLayerIds: freezeArray([]),
      notes: "No MCEL app-surface registry policy was available for this app."
    });
  }

  function normalizePolicy(policy, appId = "") {
    const input = policy && typeof policy === "object" ? policy : fallbackPolicy(appId);
    const required = !!input.conformanceRequired;
    return Object.freeze({
      appId: safeString(input.appId || appId),
      label: safeString(input.label || input.appId || appId),
      state: safeString(input.state || (required ? "surface-aware" : "legacy")),
      conformanceRequired: required,
      maturity: safeString(input.maturity || (required ? "runtime-baseline" : input.state || "legacy")),
      surfaceId: safeString(input.surfaceId || ""),
      contractId: safeString(input.contractId || ""),
      requiredLayerIds: uniqueStrings(input.requiredLayerIds || (required ? BASELINE_LAYERS : [])),
      notes: safeString(input.notes || "")
    });
  }

  function registryPolicyFor(appId, options = {}) {
    if (options.registryPolicy) return normalizePolicy(options.registryPolicy, appId);
    if (registryApi && typeof registryApi.getAppPolicy === "function") {
      try {
        return normalizePolicy(registryApi.getAppPolicy(appId), appId);
      } catch {}
    }
    return fallbackPolicy(appId);
  }

  function surfaceBundleDiagnostics(bundle) {
    const diagnostics = [];
    if (Array.isArray(bundle?.diagnostics)) diagnostics.push(...bundle.diagnostics);
    if (Array.isArray(bundle?.validation?.surface?.diagnostics)) diagnostics.push(...bundle.validation.surface.diagnostics);
    if (Array.isArray(bundle?.validation?.layout?.diagnostics)) diagnostics.push(...bundle.validation.layout.diagnostics);
    return freezeArray(diagnostics.map(compactDiagnostic));
  }

  function resolveCatalogSurfaceBundle(appId, surfaceId = "") {
    const catalog = applicationPackageCatalogApi();
    if (!catalog || typeof catalog.getSurfaceBundle !== "function") return null;
    try {
      const bundle = catalog.getSurfaceBundle(appId);
      if (!bundle || typeof bundle !== "object") return null;
      const expectedSurfaceId = safeString(surfaceId);
      const actualSurfaceId = safeString(bundle.surfaceId || bundle.semanticSurface?.surfaceId || "");
      if (expectedSurfaceId && actualSurfaceId && expectedSurfaceId !== actualSurfaceId) {
        return {
          ...bundle,
          __surfaceBundleMismatch: {
            expectedSurfaceId,
            actualSurfaceId
          }
        };
      }
      return bundle;
    } catch {
      return null;
    }
  }

  function resolveSurfaceBundle(input = {}, options = {}) {
    if (input.surfaceBundle) return input.surfaceBundle;
    if (options.surfaceBundle) return options.surfaceBundle;
    const appId = input.appId || input.report?.appId || options.appId || "";
    const surfaceId = input.surfaceId || input.expectedSurfaceId || options.surfaceId || options.expectedSurfaceId || "";
    const catalogBundle = resolveCatalogSurfaceBundle(appId, surfaceId);
    if (catalogBundle) return catalogBundle;
    return extractSurfaceBundleFromHtml(input.surfaceHtml || input.html || "", {appId, surfaceId});
  }

  function declarationValidationOptions(options = {}) {
    return {
      report: options.report || null,
      surfaceHtml: options.surfaceHtml || options.html || "",
      root: options.root || null,
      knownIntentIds: options.knownIntentIds || null
    };
  }

  function pushSelectorDiagnostic(diagnostics, code, bundle, item, kind, selector) {
    diagnostics.push(diagnostic(
      code,
      "error",
      `Declared ${kind} selector does not match the live surface: ${selector}`,
      {
        appId: bundle?.appId || "",
        id: item?.id || "",
        selector: selector || "",
        kind
      }
    ));
  }

  function validateDeclaredSemanticSurface(bundle, options = {}) {
    const semanticSurface = bundle?.semanticSurface;
    const diagnostics = [];
    const detail = {
      surfaceId: safeString(bundle?.surfaceId || semanticSurface?.surfaceId || options.surfaceId || ""),
      regionCount: 0,
      controlCount: 0,
      forbiddenDefaultRegionCount: 0,
      primaryRegionId: ""
    };
    let valid = true;

    if (!isPlainObject(semanticSurface)) {
      return {
        valid: false,
        detail,
        diagnostics: [
          diagnostic(
            "app-surface-conformance-semantic-surface-missing",
            "error",
            "A declared MCEL surface bundle must include semanticSurface.",
            {appId: bundle?.appId || options.appId || ""}
          )
        ]
      };
    }

    const expectedSurfaceId = safeString(options.surfaceId || "");
    const actualSurfaceId = safeString(bundle?.surfaceId || semanticSurface.surfaceId || "");
    if (expectedSurfaceId && actualSurfaceId && expectedSurfaceId !== actualSurfaceId) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-surface-id-mismatch",
        "error",
        "The static surface bundle targets a different surface than the conformance policy.",
        {
          appId: bundle?.appId || options.appId || "",
          expectedSurfaceId,
          actualSurfaceId
        }
      ));
    }

    if (bundle?.__surfaceBundleMismatch) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-catalog-surface-id-mismatch",
        "error",
        "The browser package catalog returned a surface bundle for a different surface.",
        {
          appId: bundle?.appId || options.appId || "",
          ...bundle.__surfaceBundleMismatch
        }
      ));
    }

    const regions = safeArray(semanticSurface.regions);
    const controls = safeArray(semanticSurface.controls);
    const forbidden = safeArray(semanticSurface.forbiddenDefaultRegions);
    detail.regionCount = regions.length;
    detail.controlCount = controls.length;
    detail.forbiddenDefaultRegionCount = forbidden.length;

    const duplicateRegionIds = duplicateIds(regions);
    if (duplicateRegionIds.length) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-semantic-region-id-duplicate",
        "error",
        "Declared semantic surface region IDs must be unique.",
        {appId: bundle?.appId || "", duplicateRegionIds}
      ));
    }

    const primaryRegions = regions.filter((region) => region?.primary === true || safeString(region?.role) === "primary-authoring-surface");
    if (primaryRegions.length !== 1) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-primary-region-invalid",
        "error",
        "Declared semantic surface must identify exactly one primary authoring region.",
        {appId: bundle?.appId || "", primaryRegionCount: primaryRegions.length}
      ));
    } else {
      detail.primaryRegionId = safeString(primaryRegions[0].id || "");
    }

    const evidenceOptions = declarationValidationOptions(options);
    for (const region of regions) {
      const selector = safeString(region?.selector || region?.runtimeHostSelector || "");
      if (!region?.id || !selector) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-semantic-region-incomplete",
          "error",
          "Declared semantic surface regions must include id and selector.",
          {appId: bundle?.appId || "", id: region?.id || "", selector}
        ));
        continue;
      }
      const exists = selectorExists(selector, evidenceOptions);
      const runtimeHostExists = region?.runtimeHostSelector
        ? selectorExists(region.runtimeHostSelector, evidenceOptions)
        : null;
      if (exists === false && runtimeHostExists !== true) {
        valid = false;
        pushSelectorDiagnostic(
          diagnostics,
          "app-surface-conformance-semantic-region-selector-missing",
          bundle,
          region,
          "semantic region",
          selector
        );
      }
    }

    const duplicateControlIds = duplicateIds(controls);
    if (duplicateControlIds.length) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-semantic-control-id-duplicate",
        "error",
        "Declared semantic surface control IDs must be unique.",
        {appId: bundle?.appId || "", duplicateControlIds}
      ));
    }

    const knownIntents = knownIntentIds(bundle, options);
    for (const control of controls) {
      const selector = safeString(control?.selector || "");
      const intent = safeString(control?.intent || "");
      if (!control?.id || !selector || !intent) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-semantic-control-incomplete",
          "error",
          "Declared semantic surface controls must include id, selector, and intent.",
          {appId: bundle?.appId || "", id: control?.id || "", selector, intent}
        ));
        continue;
      }
      const exists = selectorExists(selector, evidenceOptions);
      if (exists === false) {
        valid = false;
        pushSelectorDiagnostic(
          diagnostics,
          "app-surface-conformance-semantic-control-selector-missing",
          bundle,
          control,
          "semantic control",
          selector
        );
      }
      if (knownIntents && !knownIntents.has(intent)) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-semantic-control-intent-unknown",
          "error",
          "Declared semantic surface control targets an unknown intent.",
          {appId: bundle?.appId || "", id: control.id || "", intent}
        ));
      }
    }

    for (const region of forbidden) {
      if (region?.defaultVisible !== false) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-forbidden-default-region-not-hidden",
          "error",
          "Forbidden/default-hidden regions must declare defaultVisible: false.",
          {appId: bundle?.appId || "", id: region?.id || "", selector: region?.selector || ""}
        ));
      }
      const selector = safeString(region?.selector || "");
      if (!selector) continue;
      const visible = selectorHasVisibleMatches(selector, evidenceOptions);
      if (visible === true) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-forbidden-default-region-visible",
          "error",
          "A forbidden/default-hidden region is visible in the current runtime surface.",
          {appId: bundle?.appId || "", id: region?.id || "", selector}
        ));
      }
    }

    return {valid, detail, diagnostics};
  }

  function validateDeclaredLayoutGrammar(bundle, options = {}) {
    const layoutGrammar = bundle?.layoutGrammar;
    const diagnostics = [];
    const detail = {
      surfaceId: safeString(bundle?.surfaceId || layoutGrammar?.surfaceId || options.surfaceId || ""),
      regionCount: 0,
      constraintCount: 0,
      primaryUsable: false,
      zeroWidthTrackDetected: false
    };
    let valid = true;

    if (!isPlainObject(layoutGrammar)) {
      return {
        valid: false,
        detail,
        diagnostics: [
          diagnostic(
            "app-surface-conformance-layout-grammar-missing",
            "error",
            "A declared MCEL surface bundle must include layoutGrammar.",
            {appId: bundle?.appId || options.appId || ""}
          )
        ]
      };
    }

    const regions = safeArray(layoutGrammar.regions);
    const constraints = safeArray(layoutGrammar.constraints);
    detail.regionCount = regions.length;
    detail.constraintCount = constraints.length;

    if (!regions.length || !constraints.length) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-layout-grammar-incomplete",
        "error",
        "Declared layout grammar must include regions and constraints.",
        {appId: bundle?.appId || "", regionCount: regions.length, constraintCount: constraints.length}
      ));
    }

    const duplicateRegionIds = duplicateIds(regions);
    if (duplicateRegionIds.length) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-layout-region-id-duplicate",
        "error",
        "Declared layout grammar region IDs must be unique.",
        {appId: bundle?.appId || "", duplicateRegionIds}
      ));
    }

    const evidenceOptions = declarationValidationOptions(options);
    for (const region of regions) {
      const selector = safeString(region?.selector || region?.runtimeHostSelector || "");
      if (!region?.id || !selector) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-layout-region-incomplete",
          "error",
          "Declared layout grammar regions must include id and selector.",
          {appId: bundle?.appId || "", id: region?.id || "", selector}
        ));
        continue;
      }
      const exists = selectorExists(selector, evidenceOptions);
      const runtimeHostExists = region?.runtimeHostSelector
        ? selectorExists(region.runtimeHostSelector, evidenceOptions)
        : null;
      if (exists === false && runtimeHostExists !== true) {
        valid = false;
        pushSelectorDiagnostic(
          diagnostics,
          "app-surface-conformance-layout-region-selector-missing",
          bundle,
          region,
          "layout region",
          selector
        );
      }
    }

    const report = options.report || {};
    const primary = primarySurface(report);
    const host = primaryHostBox(report);
    const primaryConstraint = constraints.find((constraint) =>
      safeString(constraint?.id).includes("primary-editor") ||
      safeString(constraint?.selector).includes("code-studio-runtime")
    );
    const minWidth = Number(primaryConstraint?.minWidth || contractMin(report, "minWidth") || 1);
    const minHeightValue = isPlainObject(primaryConstraint?.minHeight)
      ? (primaryConstraint.minHeight.compactViewport || primaryConstraint.minHeight.default)
      : primaryConstraint?.minHeight;
    const minHeight = Number(minHeightValue || contractMin(report, "minHeight") || 1);
    detail.primaryUsable = !!primary.usable || isVisibleUsefulBox(host, minWidth, minHeight);
    if ((report && Object.keys(report).length) && !detail.primaryUsable) {
      valid = false;
      diagnostics.push(diagnostic(
        "app-surface-conformance-layout-primary-editor-unusable",
        "error",
        "Declared layout grammar primary editor is not non-zero and owned in the runtime report.",
        {
          appId: bundle?.appId || "",
          minWidth,
          minHeight,
          hostSelector: host.selector || primary.host?.selector || "",
          width: Number(host.width || 0),
          height: Number(host.height || 0)
        }
      ));
    }

    for (const constraint of constraints) {
      const rule = safeString(constraint?.rule || "");
      if (!/zero-width|single-nonzero/.test(rule)) continue;
      const selector = safeString(constraint?.selector || "");
      const trackList = selector ? selectorTrackList(selector, evidenceOptions) : "";
      if (trackListHasZeroWidth(trackList)) {
        valid = false;
        detail.zeroWidthTrackDetected = true;
        diagnostics.push(diagnostic(
          "app-surface-conformance-layout-zero-width-track",
          "error",
          "Declared layout grammar detected a zero-width shell/workbench grid track.",
          {appId: bundle?.appId || "", id: constraint?.id || "", selector, gridTemplateColumns: trackList}
        ));
      }
    }

    const forbiddenConstraints = constraints.filter((constraint) => constraint?.defaultVisible === false);
    for (const constraint of forbiddenConstraints) {
      const selector = safeString(constraint?.selector || "");
      if (!selector) continue;
      const visible = selectorHasVisibleMatches(selector, evidenceOptions);
      if (visible === true) {
        valid = false;
        diagnostics.push(diagnostic(
          "app-surface-conformance-layout-default-hidden-region-visible",
          "error",
          "Declared layout grammar default-hidden constraint is visible in the current runtime surface.",
          {appId: bundle?.appId || "", id: constraint?.id || "", selector}
        ));
      }
    }

    return {valid, detail, diagnostics};
  }

  function isDeclaredSurfaceBundle(bundle) {
    return Boolean(
      bundle &&
      typeof bundle === "object" &&
      (
        bundle.schema === DECLARED_SURFACE_BUNDLE_SCHEMA ||
        bundle.semanticSurface ||
        (bundle.layoutGrammar && !bundle.surfaceIR)
      )
    );
  }

  function evaluateDeclaredSurfaceBundle(bundle, options = {}) {
    const semantic = validateDeclaredSemanticSurface(bundle, options);
    const layoutResult = validateDeclaredLayoutGrammar(bundle, options);
    const diagnostics = [
      ...surfaceBundleDiagnostics(bundle),
      ...semantic.diagnostics,
      ...layoutResult.diagnostics
    ];
    const layers = [
      layer(
        "semantic-surface",
        semantic.valid ? "pass" : "fail",
        semantic.valid
          ? "Declared MCEL semantic surface matches the current runtime surface."
          : "Declared MCEL semantic surface does not match the current runtime surface.",
        semantic.detail
      ),
      layer(
        "layout-grammar",
        layoutResult.valid ? "pass" : "fail",
        layoutResult.valid
          ? "Declared MCEL layout grammar matches the current runtime surface."
          : "Declared MCEL layout grammar does not match the current runtime surface.",
        layoutResult.detail
      )
    ];

    return Object.freeze({
      contractVersion,
      status: hasCriticalFailure(layers) ? "fail" : "pass",
      valid: !hasCriticalFailure(layers),
      surfaceId: bundle?.surfaceId || bundle?.semanticSurface?.surfaceId || options.surfaceId || "",
      layers: freezeArray(layers),
      diagnostics: freezeArray(diagnostics)
    });
  }

  function evaluateExtractedSurfaceBundle(bundle, options = {}) {
    const missing = !bundle;
    const diagnostics = [];
    const surfaceValid = !missing && !!(bundle.valid && bundle.surfaceIR && bundle.validation?.surface?.valid);
    const layoutValid = !missing && !!(bundle.valid && bundle.layoutGrammar && bundle.validation?.layout?.valid);

    if (missing) {
      diagnostics.push(diagnostic(
        "app-surface-conformance-surface-bundle-unavailable",
        "info",
        "No extracted or declared MCEL surface bundle was supplied for static semantic/layout conformance.",
        {appId: options.appId || ""}
      ));
    } else {
      diagnostics.push(...surfaceBundleDiagnostics(bundle));
    }

    const layers = [
      layer(
        "semantic-surface",
        statusFromValidation(surfaceValid, missing),
        missing
          ? "Static MCEL semantic extraction was not available for this report."
          : surfaceValid
            ? "MCEL semantic surface extracted and validated."
            : "MCEL semantic surface extraction failed.",
        {
          surfaceId: bundle?.surfaceIR?.surface?.id || options.surfaceId || "",
          nodeCount: bundle?.surfaceIR?.graph?.nodes?.length || 0,
          edgeCount: bundle?.surfaceIR?.graph?.edges?.length || 0,
          regionCount: bundle?.surfaceIR?.graph?.regions?.length || 0,
          controlCount: bundle?.surfaceIR?.graph?.controls?.length || 0
        }
      ),
      layer(
        "layout-grammar",
        statusFromValidation(layoutValid, missing),
        missing
          ? "Static MCEL layout grammar extraction was not available for this report."
          : layoutValid
            ? "MCEL shared layout grammar extracted and validated."
            : "MCEL shared layout grammar extraction failed.",
        {
          surfaceId: bundle?.layoutGrammar?.surfaceId || bundle?.surfaceIR?.surface?.id || options.surfaceId || "",
          regionCount: bundle?.layoutGrammar?.regions?.length || 0,
          nodeCount: bundle?.layoutGrammar?.nodes?.length || 0,
          routeCount: bundle?.layoutGrammar?.routes?.length || 0,
          controlCount: bundle?.layoutGrammar?.controls?.length || 0
        }
      )
    ];

    return Object.freeze({
      contractVersion,
      status: hasCriticalFailure(layers) ? "fail" : missing ? "unavailable" : "pass",
      valid: !hasCriticalFailure(layers) && !missing,
      surfaceId: bundle?.surfaceIR?.surface?.id || options.surfaceId || "",
      layers: freezeArray(layers),
      diagnostics: freezeArray(diagnostics)
    });
  }

  function evaluateSurfaceBundle(surfaceBundle, options = {}) {
    const bundle = surfaceBundle || null;
    if (isDeclaredSurfaceBundle(bundle)) return evaluateDeclaredSurfaceBundle(bundle, options);
    return evaluateExtractedSurfaceBundle(bundle, options);
  }

  function extractSurfaceBundleFromHtml(surfaceHtml, options = {}) {
    const html = safeString(surfaceHtml);
    if (!html.trim()) return null;
    if (!extractorApi || typeof extractorApi.extractSurfaceBundleFromHtml !== "function") {
      return {
        valid: false,
        diagnostics: [
          diagnostic(
            "app-surface-conformance-extractor-api-missing",
            "error",
            "McelSurfaceExtractors is required to extract a static app surface bundle.",
            {appId: options.appId || ""}
          )
        ],
        validation: {
          surface: {valid: false, diagnostics: []},
          layout: {valid: false, diagnostics: []}
        }
      };
    }
    try {
      return extractorApi.extractSurfaceBundleFromHtml(html, {
        surfaceId: options.surfaceId || options.expectedSurfaceId || ""
      });
    } catch (error) {
      return {
        valid: false,
        diagnostics: [
          diagnostic(
            "app-surface-conformance-extraction-threw",
            "error",
            safeString(error?.message || error || "Surface extraction failed."),
            {appId: options.appId || ""}
          )
        ],
        validation: {
          surface: {valid: false, diagnostics: []},
          layout: {valid: false, diagnostics: []}
        }
      };
    }
  }

  function findings(report) {
    return Array.isArray(report?.findings) ? report.findings : [];
  }

  function hasFinding(report, code) {
    return findings(report).some((finding) => safeString(finding?.code) === code);
  }

  function hasFindingContaining(report, token) {
    const needle = safeString(token);
    return findings(report).some((finding) => safeString(finding?.code).includes(needle));
  }

  function nonEmptyObject(value) {
    return Boolean(value && typeof value === "object" && Object.keys(value).length > 0);
  }

  function isVisibleUsefulBox(box, minWidth = 1, minHeight = 1) {
    return Boolean(
      box &&
      box.exists &&
      box.visible &&
      Number(box.width || 0) >= minWidth &&
      Number(box.height || 0) >= minHeight
    );
  }

  function primarySurface(report) {
    return report?.summary?.primarySurface || report?.primarySurface || {};
  }

  function primaryHostBox(report) {
    const summary = primarySurface(report);
    return report?.measurements?.surfaces?.primaryHost ||
      report?.measurements?.surfaces?.monacoHost ||
      summary.host ||
      {};
  }

  function contractMin(report, key) {
    const value = Number(report?.contract?.primarySurface?.[key] || 1);
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  function visualViolationCount(report) {
    const measurements = report?.measurements || {};
    return [
      measurements.visualIntegrityViolations,
      measurements.contentFitViolations,
      measurements.layoutCollisions
    ].reduce((count, entries) => count + (Array.isArray(entries) ? entries.length : 0), 0);
  }

  function evaluateRuntimeReport(report, options = {}) {
    const runtimeReport = report || {};
    const measurementObject = runtimeReport.measurements || {};
    const primary = primarySurface(runtimeReport);
    const host = primaryHostBox(runtimeReport);
    const noDiagnosisThrow = !hasFinding(runtimeReport, "diagnosis-threw") &&
      safeString(runtimeReport?.verdict).toLowerCase() !== "unsupported";
    const hasMeasurements = nonEmptyObject(measurementObject);
    const minWidth = contractMin(runtimeReport, "minWidth");
    const minHeight = contractMin(runtimeReport, "minHeight");
    const hostUseful = isVisibleUsefulBox(host, minWidth, minHeight);
    const primaryUsable = !!primary.usable || hostUseful;
    const uniqueAuthoritative = primary.exactlyOneAuthoritativeSurface !== false;
    const visualClean = visualViolationCount(runtimeReport) === 0 &&
      !hasFinding(runtimeReport, "visual-integrity-violation") &&
      !hasFinding(runtimeReport, "semantic-content-fit-violation") &&
      !hasFindingContaining(runtimeReport, "layout");

    const layers = [
      layer(
        "runtime-ownership",
        primaryUsable && uniqueAuthoritative ? "pass" : "fail",
        primaryUsable && uniqueAuthoritative
          ? "Runtime primary surface ownership is usable."
          : "Runtime primary surface ownership is missing, unusable, or ambiguous.",
        {
          primaryUsable,
          exactlyOneAuthoritativeSurface: uniqueAuthoritative,
          hostSelector: host.selector || primary.host?.selector || "",
          width: Number(host.width || 0),
          height: Number(host.height || 0)
        }
      ),
      layer(
        "runtime-visual-fit",
        visualClean ? "pass" : "fail",
        visualClean
          ? "Runtime layout/readability probes found no visual-fit violations."
          : "Runtime layout/readability probes found visual-fit or layout violations.",
        {
          visualViolationCount: visualViolationCount(runtimeReport),
          fitContractVersion: safeString(runtimeReport?.measurements?.fitContract?.contractVersion || "")
        }
      ),
      layer(
        "diagnostic-no-throw",
        noDiagnosisThrow && hasMeasurements ? "pass" : "fail",
        noDiagnosisThrow && hasMeasurements
          ? "Runtime diagnostics completed and retained measurements."
          : "Runtime diagnostics threw or returned without useful measurements.",
        {
          diagnosisThrew: hasFinding(runtimeReport, "diagnosis-threw"),
          hasMeasurements
        }
      )
    ];

    const diagnostics = [];
    if (!noDiagnosisThrow) {
      diagnostics.push(diagnostic(
        "app-surface-conformance-diagnosis-threw",
        "error",
        "The app surface conformance baseline cannot trust a diagnostic report that threw.",
        {appId: runtimeReport.appId || options.appId || ""}
      ));
    }
    if (!hasMeasurements) {
      diagnostics.push(diagnostic(
        "app-surface-conformance-measurements-missing",
        "error",
        "The app surface conformance baseline requires populated runtime measurements.",
        {appId: runtimeReport.appId || options.appId || ""}
      ));
    }

    return Object.freeze({
      contractVersion,
      status: hasCriticalFailure(layers) ? "fail" : "pass",
      valid: !hasCriticalFailure(layers),
      appId: runtimeReport.appId || options.appId || "",
      layers: freezeArray(layers),
      diagnostics: freezeArray(diagnostics)
    });
  }

  function mergeLayerSets(staticResult, runtimeResult) {
    const layers = [];
    const byId = new Map();
    [...(staticResult?.layers || []), ...(runtimeResult?.layers || [])].forEach((item) => {
      if (!item || byId.has(item.id)) return;
      byId.set(item.id, item);
      layers.push(item);
    });
    for (const id of BASELINE_LAYERS) {
      if (byId.has(id)) continue;
      layers.push(layer(id, "unavailable", "This conformance layer was not exercised by the supplied inputs.", {}));
    }
    return freezeArray(layers);
  }

  function evaluateAppSurfaceConformance(input = {}, options = {}) {
    const appId = input.appId || input.report?.appId || options.appId || "";
    const policy = registryPolicyFor(appId, {
      ...options,
      registryPolicy: input.registryPolicy || options.registryPolicy
    });
    const surfaceId = input.surfaceId ||
      input.expectedSurfaceId ||
      options.surfaceId ||
      options.expectedSurfaceId ||
      policy.surfaceId ||
      "";
    const surfaceHtml = input.surfaceHtml || input.html || options.surfaceHtml || options.html || "";
    const surfaceBundle = resolveSurfaceBundle({...input, surfaceHtml}, {...options, appId, surfaceId});
    const staticResult = evaluateSurfaceBundle(surfaceBundle, {
      ...options,
      appId,
      surfaceId,
      report: input.report,
      surfaceHtml
    });
    const runtimeResult = input.report ? evaluateRuntimeReport(input.report, {appId}) : null;
    const layers = mergeLayerSets(staticResult, runtimeResult);
    const failed = layers.filter((item) => item.status === "fail");
    const unavailable = layers.filter((item) => item.status === "unavailable");
    const requiredLayerIds = uniqueStrings(input.requiredLayerIds || options.requiredLayerIds || policy.requiredLayerIds || []);
    const requiredLayerSet = new Set(requiredLayerIds);
    const policyFailed = layers.filter((item) => requiredLayerSet.has(item.id) && item.status === "fail");
    const policyUnavailable = layers.filter((item) => requiredLayerSet.has(item.id) && item.status === "unavailable");
    const diagnostics = [
      ...(staticResult?.diagnostics || []),
      ...(runtimeResult?.diagnostics || [])
    ];
    const counts = severityCounts(diagnostics);
    const policyRequiresConformance = !!policy.conformanceRequired;
    const policyFailure = policyRequiresConformance && (policyFailed.length > 0 || policyUnavailable.length > 0);
    const status = policyRequiresConformance
      ? (policyFailure ? "fail" : "pass")
      : failed.length
        ? "fail"
        : policy.state === "legacy"
          ? "not-required"
          : unavailable.length
            ? "partial"
            : "pass";

    return Object.freeze({
      contractVersion,
      appId,
      surfaceId: surfaceId || staticResult.surfaceId,
      status,
      valid: status === "pass" || status === "not-required",
      conformanceRequired: policyRequiresConformance,
      registryState: policy.state || "unregistered",
      registryPolicy: policy,
      requiredLayerIds,
      layers,
      failedLayerIds: freezeArray(failed.map((item) => item.id)),
      unavailableLayerIds: freezeArray(unavailable.map((item) => item.id)),
      policyFailedLayerIds: freezeArray(policyFailed.map((item) => item.id)),
      policyUnavailableLayerIds: freezeArray(policyUnavailable.map((item) => item.id)),
      diagnosticCodes: freezeArray(diagnostics.map((item) => item.code).sort()),
      counts,
      diagnostics: freezeArray(diagnostics)
    });
  }


  return Object.freeze({
    contractVersion,
    BASELINE_LAYERS,
    registryPolicyFor,
    extractSurfaceBundleFromHtml,
    evaluateSurfaceBundle,
    evaluateRuntimeReport,
    evaluateAppSurfaceConformance
  });
})();

if (typeof window !== "undefined") {
  window.McelAppSurfaceConformance = McelAppSurfaceConformance;
}
