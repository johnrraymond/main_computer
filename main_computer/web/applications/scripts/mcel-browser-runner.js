var McelLabBrowserRunner = (() => {
  const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
  const observer = typeof McelLabBrowserObserver !== "undefined" ? McelLabBrowserObserver : window.McelLabBrowserObserver;
  const platform = typeof McelLabPlatformSpine !== "undefined" ? McelLabPlatformSpine : window.McelLabPlatformSpine;
  const {attributes, contractVersion} = contract;

  function now() {
    return new Date().toISOString();
  }

  function browserCapabilities() {
    return {
      kind: "mcel-browser-runner-capabilities",
      contractVersion,
      generatedAt: now(),
      intendedBackends: ["Playwright Chromium", "Playwright Firefox", "Playwright WebKit", "in-browser lab observer"],
      observes: [
        "geometry",
        "scrollbar existence",
        "nearest scroll owner",
        "computed style",
        "focus reachability",
        "hydration boundary",
        "data freshness markers",
        "performance/security budget estimates"
      ],
      oracle: "MCEL laws decide meaning; browser automation only supplies machine state."
    };
  }

  function observeAndProve(root, options = {}) {
    const browserReport = observer?.observeRoot ? observer.observeRoot(root, options) : null;
    const platformProof = platform?.provePlatform ? platform.provePlatform(root, options) : null;
    return {
      kind: "mcel-browser-semantic-proof",
      contractVersion,
      generatedAt: now(),
      backend: options.backend || "in-browser-lab",
      liveGeometry: Boolean(browserReport?.observations?.some((item) => item.hasLiveGeometry)),
      elementCount: root?.querySelectorAll?.(`[${attributes.type}]`)?.length || 0,
      browserReport,
      platformProof,
      failed: Boolean(platformProof?.failed)
    };
  }

  function buildConformanceManifest() {
    return {
      kind: "mcel-browser-conformance-manifest",
      generatedAt: now(),
      fixtures: [
        "no-internal-scroll",
        "delegated-scroll-owner",
        "semantic-component-slots",
        "state-owner-replay",
        "query-cache-freshness",
        "form-validation-error-summary",
        "action-lawful-region-swap",
        "route-island-hydration",
        "strict-a11y-focus",
        "performance-security-budget"
      ],
      passRule: "A fixture passes only when source law, runtime law, browser observation, serializer firewall, and evidence packet agree."
    };
  }

  return Object.freeze({
    browserCapabilities,
    observeAndProve,
    buildConformanceManifest
  });
})();

if (typeof window !== "undefined") {
  window.McelLabBrowserRunner = McelLabBrowserRunner;
}
