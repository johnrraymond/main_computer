    var McelLabLayoutLaw = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const observer = typeof McelLabBrowserObserver !== "undefined" ? McelLabBrowserObserver : window.McelLabBrowserObserver;
      const registry = typeof McelLabLawRegistry !== "undefined" ? McelLabLawRegistry : window.McelLabLawRegistry;
      const {attributes, defaults, layoutPolicies} = contract;

      function attr(element, name, fallback = "") {
        return String(element?.getAttribute?.(name) || fallback).trim() || fallback;
      }

      function sourceElements(root) {
        return [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function normalizePolicy(element) {
        const sizePolicy = attr(element, attributes.sizePolicy, defaults.sizePolicy);
        const overflowPolicy = attr(element, attributes.overflowPolicy, defaults.overflowPolicy);
        const scrollPolicy = attr(element, attributes.scrollPolicy, defaults.scrollPolicy);
        return {
          sizePolicy: layoutPolicies.size.includes(sizePolicy) ? sizePolicy : defaults.sizePolicy,
          overflowPolicy: layoutPolicies.overflow.includes(overflowPolicy) ? overflowPolicy : defaults.overflowPolicy,
          scrollPolicy: layoutPolicies.scroll.includes(scrollPolicy) ? scrollPolicy : defaults.scrollPolicy
        };
      }

      function pressureFor(element, policy) {
        const density = attr(element, attributes.computedDensity, attr(element, attributes.density, defaults.density));
        const words = String(element?.textContent || "").trim().split(/\s+/).filter(Boolean).length;
        if (policy.overflowPolicy === "virtualize" || policy.scrollPolicy === "required") return "high";
        if (policy.overflowPolicy === "paginate" || density === "compressed" || words > 60) return "medium-high";
        if (policy.scrollPolicy === "never" || policy.overflowPolicy === "clip") return "constrained";
        return words > 28 || density === "dense" ? "medium" : "low";
      }

      function expectedOwner(policy) {
        if (policy.scrollPolicy === "external" || policy.overflowPolicy === "delegate") return "parent";
        if (policy.scrollPolicy === "viewport-only") return "viewport";
        if (policy.scrollPolicy === "never") return "none";
        if (policy.scrollPolicy === "required" || policy.scrollPolicy === "child-only") return "self";
        if (policy.scrollPolicy === "auto" && (policy.sizePolicy === "fixed" || policy.overflowPolicy === "virtualize")) return "self";
        if (policy.scrollPolicy === "auto") return "content";
        return "content";
      }

      function expectedOverflow(policy) {
        if (policy.scrollPolicy === "never") return "clip";
        if (policy.overflowPolicy === "visible" || policy.overflowPolicy === "expand") return "visible";
        if (policy.overflowPolicy === "delegate" || policy.scrollPolicy === "external" || policy.scrollPolicy === "viewport-only") return "visible";
        if (policy.scrollPolicy === "required" || policy.scrollPolicy === "child-only") return "auto";
        if (policy.scrollPolicy === "auto" && (policy.sizePolicy === "fixed" || policy.overflowPolicy === "virtualize")) return "auto";
        if (policy.overflowPolicy === "clip" || policy.overflowPolicy === "collapse") return "clip";
        if (policy.overflowPolicy === "paginate") return "visible";
        return "visible";
      }

      function computeElementLaw(element, index = 0, total = 1) {
        const policy = normalizePolicy(element);
        const pressure = pressureFor(element, policy);
        const overflow = expectedOverflow(policy);
        const owner = expectedOwner(policy);
        return {
          index,
          type: attr(element, attributes.type, defaults.type),
          ...policy,
          expected: {
            overflow,
            scrollOwner: owner,
            internalScrollbarAllowed: ["required", "child-only"].includes(policy.scrollPolicy) || (policy.scrollPolicy === "auto" && owner === "self"),
            internalScrollbarForbidden: policy.scrollPolicy === "never" || owner !== "self",
            keyboardRequired: ["required", "child-only"].includes(policy.scrollPolicy),
            pressure
          },
          tokens: {
            "--mc-layout-pressure": pressure,
            "--mc-scroll-owner": owner
          }
        };
      }

      function applyElementLaw(element, law, options = {}) {
        Object.entries(law.tokens).forEach(([name, value]) => element.style.setProperty(name, value));
        element.setAttribute(attributes.layoutLaw, "true");
        element.setAttribute(attributes.layoutPressure, law.expected.pressure);
        element.setAttribute(attributes.scrollOwner, law.expected.scrollOwner);
        element.setAttribute(attributes.overflowComputed, law.expected.overflow);
        element.setAttribute(attributes.keyboardScroll, law.expected.keyboardRequired ? "required" : "not-required");
        element.style.overflow = law.expected.overflow;
        if (law.sizePolicy === "fixed") {
          element.style.maxBlockSize = "min(42vh, 420px)";
        } else if (law.sizePolicy === "intrinsic") {
          element.style.maxBlockSize = "max-content";
        } else if (law.sizePolicy === "fluid") {
          element.style.maxInlineSize = "100%";
        }
        if (law.expected.scrollOwner === "content") {
          element.style.minBlockSize = "min-content";
          element.style.overflow = "visible";
          element.style.overscrollBehavior = "auto";
        }
        if (law.overflowPolicy === "contain") {
          element.style.overscrollBehavior = law.expected.scrollOwner === "self" ? "contain" : "auto";
        }
        if (law.scrollPolicy === "never") {
          element.style.overflow = "clip";
          element.style.overscrollBehavior = "none";
        }
      }

      function proveElement(element, law, observed = null) {
        const observation = observed || observer?.observeElement?.(element) || {};
        const liveGeometry = Boolean(observation.hasLiveGeometry);
        const violations = [];
        const warnings = [];
        if (law.expected.internalScrollbarForbidden && observation.hasInternalScrollbar) {
          violations.push("internal scrollbar exists even though source policy forbids it");
        }
        if (law.expected.keyboardRequired && !attr(element, attributes.keyboardScroll, "").includes("required")) {
          violations.push("keyboard scroll obligation was not published");
        }
        if (!liveGeometry) {
          warnings.push("browser geometry was not live; proof is static/runtime-token only");
        }
        const status = violations.length ? "fail" : "pass";
        element.setAttribute(attributes.scrollNeeded, observation.verticalOverflowPossible || observation.horizontalOverflowPossible ? "true" : "false");
        element.setAttribute(attributes.geometryProof, status);
        return {
          index: law.index,
          status,
          passed: !violations.length,
          liveGeometry,
          policy: {
            sizePolicy: law.sizePolicy,
            overflowPolicy: law.overflowPolicy,
            scrollPolicy: law.scrollPolicy
          },
          expected: law.expected,
          observed: observation,
          violations,
          warnings
        };
      }

      function applyRuntimeLaw(root, options = {}) {
        const elements = sourceElements(root);
        const elementsReport = elements.map((element, index) => {
          const law = computeElementLaw(element, index, elements.length);
          applyElementLaw(element, law, options);
          return proveElement(element, law);
        });
        const failed = elementsReport.filter((item) => !item.passed).length;
        const staticOnly = elementsReport.filter((item) => !item.liveGeometry).length;
        return {
          kind: "mcel-layout-law-report",
          lawId: "layout.overflow.scroll.v2",
          elementCount: elementsReport.length,
          passed: elementsReport.length - failed,
          failed,
          staticOnly,
          layoutLawClean: failed === 0,
          elements: elementsReport,
          warnings: [
            ...(elementsReport.length ? [] : ["No MCEL runtime elements were available for layout law."]),
            ...elementsReport.flatMap((item) => item.violations.map((warning) => `element ${item.index}: ${warning}`))
          ]
        };
      }

      function proveRuntime(root, options = {}) {
        const elements = sourceElements(root);
        const elementsReport = elements.map((element, index) => {
          const law = computeElementLaw(element, index, elements.length);
          return proveElement(element, law);
        });
        const failed = elementsReport.filter((item) => !item.passed).length;
        return {
          kind: "mcel-layout-proof-report",
          elementCount: elementsReport.length,
          passed: elementsReport.length - failed,
          failed,
          layoutLawClean: failed === 0,
          elements: elementsReport
        };
      }

      function repairRuntimeLaw(root, options = {}) {
        const before = proveRuntime(root, {...options, reason: "layout-repair:before"});
        const after = applyRuntimeLaw(root, {...options, reason: "layout-repair"});
        return {
          kind: "mcel-layout-repair-report",
          before,
          after,
          repaired: before.failed || before.elements.some((item) => item.status !== "pass") ? after.elementCount : 0
        };
      }

      function reportFor(root, options = {}) {
        return applyRuntimeLaw(root, options);
      }

      const descriptor = {
        id: "layout.overflow.scroll.v2",
        label: "Layout / Overflow / Scroll Law",
        notes: "Auto scroll is content-expanding by default; self scroll is opt-in through required, child-only, fixed, or virtualized policies.",
        version: "v1",
        reads: [attributes.sizePolicy, attributes.overflowPolicy, attributes.scrollPolicy, attributes.flow, attributes.density],
        writesRuntimeOnly: [
          attributes.layoutLaw,
          attributes.overflowComputed,
          attributes.scrollNeeded,
          attributes.scrollOwner,
          attributes.layoutPressure,
          attributes.geometryProof,
          attributes.keyboardScroll
        ],
        sourcePollutionForbidden: true,
        compute: computeElementLaw,
        apply: applyRuntimeLaw,
        inspect: observer?.observeElement || null,
        prove: proveRuntime,
        reportFor
      };

      if (registry?.register) registry.register(descriptor);

      return Object.freeze({
        descriptor,
        normalizePolicy,
        computeElementLaw,
        applyElementLaw,
        applyRuntimeLaw,
        proveRuntime,
        repairRuntimeLaw,
        reportFor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabLayoutLaw = McelLabLayoutLaw;
    }
