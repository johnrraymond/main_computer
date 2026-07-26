var McelSurfaceFitContract = (() => {
  "use strict";

  const contractVersion = "mcel.surface-fit-contract.v1";

  const POLICY_DEFINITIONS = Object.freeze({
    wrap: Object.freeze({
      id: "wrap",
      intent: "Readable text may wrap within its owner; clipping is not permitted."
    }),
    truncate: Object.freeze({
      id: "truncate",
      intent: "Readable text may be shortened only when CSS explicitly clips with ellipsis."
    }),
    scroll: Object.freeze({
      id: "scroll",
      intent: "Overflow is permitted only when the owner exposes scrollable overflow."
    }),
    compact: Object.freeze({
      id: "compact",
      intent: "Controls may switch to a smaller labeled representation."
    }),
    "compact-icon": Object.freeze({
      id: "compact-icon",
      intent: "Controls may hide visible text only when an accessible label remains."
    }),
    "collapse-optional": Object.freeze({
      id: "collapse-optional",
      intent: "Optional content may collapse after the required region remains usable."
    }),
    overlay: Object.freeze({
      id: "overlay",
      intent: "Floating content is measured as an overlay, not a normal layout child."
    }),
    decorative: Object.freeze({
      id: "decorative",
      intent: "Pure decoration is ignored by readability and collision probes."
    }),
    "ignore-hidden": Object.freeze({
      id: "ignore-hidden",
      intent: "Hidden/inactive content does not participate in fit probes until visible."
    })
  });

  const HARD_NO_SILENT_CLIP = Object.freeze([
    "Required readable/control content must declare a fit policy.",
    "A shrinking region must explain how its readable/control children wrap, truncate, scroll, compact, collapse, or overlay.",
    "Silent clipping is never an allowed fit policy for semantic-runtime surfaces."
  ]);

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value);
  }

  function uniqueStrings(values) {
    return Object.freeze([...(new Set((values || []).map((value) => safeString(value).trim()).filter(Boolean)))]);
  }

  function policyTokens(value) {
    return uniqueStrings(safeString(value).toLowerCase().split(/[^a-z0-9-]+/).filter(Boolean));
  }

  function normalizePolicy(value) {
    const tokens = policyTokens(value);
    const known = tokens.filter((token) => Object.prototype.hasOwnProperty.call(POLICY_DEFINITIONS, token));
    const unknown = tokens.filter((token) => !Object.prototype.hasOwnProperty.call(POLICY_DEFINITIONS, token));
    return Object.freeze({
      policy: known.join(" "),
      tokens,
      knownTokens: Object.freeze(known),
      unknownTokens: Object.freeze(unknown),
      valid: tokens.length > 0 && unknown.length === 0
    });
  }

  function isElement(value) {
    return !!value && typeof value === "object" && typeof value.getAttribute === "function";
  }

  function policyForElement(el) {
    if (!isElement(el)) {
      return Object.freeze({
        policy: "",
        tokens: Object.freeze([]),
        knownTokens: Object.freeze([]),
        unknownTokens: Object.freeze([]),
        valid: false,
        source: "none",
        sourceSelector: ""
      });
    }

    const own = safeString(el.getAttribute("data-mcel-fit-policy")).trim();
    if (own) {
      return Object.freeze({...normalizePolicy(own), source: "self", sourceSelector: selectorForElement(el)});
    }

    const declared = typeof el.closest === "function" ? el.closest("[data-mcel-fit-policy]") : null;
    if (declared && declared !== el) {
      const inherited = safeString(declared.getAttribute("data-mcel-fit-policy")).trim();
      return Object.freeze({...normalizePolicy(inherited), source: "ancestor", sourceSelector: selectorForElement(declared)});
    }

    return Object.freeze({
      policy: "",
      tokens: Object.freeze([]),
      knownTokens: Object.freeze([]),
      unknownTokens: Object.freeze([]),
      valid: false,
      source: "none",
      sourceSelector: ""
    });
  }

  function selectorForElement(el) {
    if (!isElement(el)) return "";
    const id = safeString(el.id).trim();
    if (id) return `#${id.replace(/"/g, '\\"')}`;
    const attrId = safeString(el.getAttribute("data-mcel-node-id") || el.getAttribute("data-mcel-region") || el.getAttribute("data-mcel-control")).trim();
    if (attrId) return `[data-mcel-node-id="${attrId.replace(/"/g, '\\"')}"], [data-mcel-region="${attrId.replace(/"/g, '\\"')}"], [data-mcel-control="${attrId.replace(/"/g, '\\"')}"]`;
    const cls = safeString(el.className).trim().split(/\s+/).filter(Boolean)[0];
    if (cls) return `${safeString(el.tagName || "element").toLowerCase()}.${cls.replace(/[^a-zA-Z0-9_-]/g, "")}`;
    return safeString(el.tagName || "element").toLowerCase();
  }

  function styleValue(styles, key) {
    return safeString(styles?.[key]);
  }

  function overflowAllowsScroll(styles, axis) {
    const value = axis === "y"
      ? styleValue(styles, "overflowY") || styleValue(styles, "overflow")
      : styleValue(styles, "overflowX") || styleValue(styles, "overflow");
    return /auto|scroll/.test(value);
  }

  function cssDeclaresEllipsis(styles) {
    const overflow = styleValue(styles, "overflowX") || styleValue(styles, "overflow");
    const textOverflow = styleValue(styles, "textOverflow");
    return /hidden|clip/.test(overflow) && textOverflow === "ellipsis";
  }

  function hasAccessibleLabel(el) {
    if (!isElement(el)) return false;
    return Boolean(
      safeString(el.getAttribute("aria-label")).trim() ||
      safeString(el.getAttribute("title")).trim() ||
      safeString(el.getAttribute("aria-labelledby")).trim()
    );
  }

  function allowsContentOverflow(el, options = {}) {
    const policy = options.policyInfo || policyForElement(el);
    const tokens = new Set(policy.knownTokens || policyTokens(policy.policy));
    const horizontalClipped = !!options.horizontalClipped;
    const verticalClipped = !!options.verticalClipped;
    const styles = options.styles || {};

    if (!tokens.size) return false;

    if (tokens.has("decorative") || tokens.has("overlay") || tokens.has("ignore-hidden") || tokens.has("collapse-optional")) {
      return true;
    }

    if (verticalClipped && !tokens.has("scroll")) return false;
    if (verticalClipped && tokens.has("scroll") && !overflowAllowsScroll(styles, "y")) return false;
    if (verticalClipped && !horizontalClipped && tokens.has("scroll") && overflowAllowsScroll(styles, "y")) return true;

    if (horizontalClipped && tokens.has("scroll") && overflowAllowsScroll(styles, "x")) return true;
    if (horizontalClipped && tokens.has("truncate")) return cssDeclaresEllipsis(styles);
    if (horizontalClipped && tokens.has("compact-icon")) {
      const fontSize = parseFloat(styleValue(styles, "fontSize") || "0") || 0;
      return fontSize === 0 && hasAccessibleLabel(el);
    }
    if (horizontalClipped && tokens.has("compact")) {
      return hasAccessibleLabel(el) || cssDeclaresEllipsis(styles);
    }

    return !horizontalClipped && !verticalClipped;
  }

  function applyFitPolicy(target, policy, options = {}) {
    const policyInfo = normalizePolicy(policy);
    if (!target) return target;

    if (typeof target.forEach === "function" && !isElement(target)) {
      target.forEach((item) => applyFitPolicy(item, policy, options));
      return target;
    }

    if (!isElement(target)) return target;
    if (policyInfo.policy) target.setAttribute("data-mcel-fit-policy", policyInfo.policy);
    if (options.readable) target.setAttribute("data-mcel-readable", "true");
    if (options.visualOwner) target.setAttribute("data-mcel-visual-owner", safeString(options.visualOwner));
    if (options.fitRole) target.setAttribute("data-mcel-fit-role", safeString(options.fitRole));
    if (options.required !== undefined) target.setAttribute("data-mcel-fit-required", options.required ? "true" : "false");
    return target;
  }

  function candidateRequiresDeclaredPolicy(el) {
    if (!isElement(el)) return false;
    const required = safeString(el.getAttribute("data-mcel-fit-required")).toLowerCase();
    if (required === "true") return true;
    return Boolean(
      safeString(el.getAttribute("data-mcel-readable")).toLowerCase() === "true" ||
      safeString(el.getAttribute("data-mcel-control")).trim() ||
      safeString(el.getAttribute("data-mcel-node-id")).trim()
    );
  }

  function describeElementPolicy(el) {
    const policy = policyForElement(el);
    return Object.freeze({
      ...policy,
      selector: selectorForElement(el),
      required: candidateRequiresDeclaredPolicy(el)
    });
  }

  return Object.freeze({
    contractVersion,
    POLICY_DEFINITIONS,
    HARD_NO_SILENT_CLIP,
    normalizePolicy,
    policyTokens,
    policyForElement,
    allowsContentOverflow,
    applyFitPolicy,
    candidateRequiresDeclaredPolicy,
    describeElementPolicy
  });
})();

if (typeof window !== "undefined") {
  window.McelSurfaceFitContract = McelSurfaceFitContract;
  window.MCEL = window.MCEL || {};
  window.MCEL.surfaceFitContract = McelSurfaceFitContract;
}
