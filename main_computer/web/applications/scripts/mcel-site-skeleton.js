    var McelLabSiteSkeleton = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const observer = typeof McelLabBrowserObserver !== "undefined" ? McelLabBrowserObserver : window.McelLabBrowserObserver;
      const {attributes, defaults} = contract;

      function parseSource(source) {
        return new DOMParser().parseFromString(String(source || ""), "text/html");
      }

      function sourceElements(root) {
        return [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
      }

      function labelFor(element, index = 0) {
        const heading = element.querySelector?.("h1,h2,h3,h4,h5,h6");
        return String(
          heading?.textContent ||
          element.getAttribute?.(attributes.componentName) ||
          element.getAttribute?.(attributes.type) ||
          `section ${index + 1}`
        ).trim();
      }

      function roleFor(element) {
        const type = element.getAttribute(attributes.type) || defaults.type;
        const kind = element.getAttribute(attributes.kind) || defaults.kind;
        const componentKind = element.getAttribute(attributes.componentKind) || "";
        if (componentKind === "page") return "page shell";
        if (kind === "hero") return "hero";
        if (type === "feed") return "trust cluster";
        if (element.tagName === "FORM" || element.getAttribute(attributes.submit)) return "conversion form";
        if (type === "command-row") return "command row";
        if (componentKind === "island") return "interactive island";
        return `${kind} ${type}`;
      }

      function policyFor(element) {
        return {
          size: element.getAttribute(attributes.sizePolicy) || defaults.sizePolicy,
          overflow: element.getAttribute(attributes.overflowPolicy) || defaults.overflowPolicy,
          scroll: element.getAttribute(attributes.scrollPolicy) || defaults.scrollPolicy,
          render: element.getAttribute(attributes.renderMode) || "unset",
          component: element.getAttribute(attributes.componentKind) || "component"
        };
      }

      function extractSourceSections(source) {
        const doc = parseSource(source);
        return sourceElements(doc.body).map((element, index) => ({
          index,
          tag: element.tagName.toLowerCase(),
          type: element.getAttribute(attributes.type) || defaults.type,
          kind: element.getAttribute(attributes.kind) || defaults.kind,
          label: labelFor(element, index),
          role: roleFor(element),
          policy: policyFor(element),
          action: element.getAttribute(attributes.action) || element.getAttribute(attributes.submit) || "",
          route: element.getAttribute(attributes.route) || "",
          sourceSimple: element.attributes.length <= 18
        }));
      }

      function observeRuntime(runtimeRoot) {
        return sourceElements(runtimeRoot).map((element, index) => {
          const observed = observer?.observeElement?.(element) || {};
          const scrollOwner = element.getAttribute(attributes.scrollOwner) || "unknown";
          const label = labelFor(element, index);
          const hasTrap = Boolean(observed.hasInternalScrollbar && scrollOwner !== "self");
          return {
            index,
            label,
            scrollOwner,
            geometryProof: element.getAttribute(attributes.geometryProof) || "unknown",
            overflowComputed: element.getAttribute(attributes.overflowComputed) || "",
            hasInternalScrollbar: Boolean(observed.hasInternalScrollbar),
            verticalOverflowPossible: Boolean(observed.verticalOverflowPossible),
            horizontalOverflowPossible: Boolean(observed.horizontalOverflowPossible),
            trap: hasTrap
          };
        });
      }

      function buildSkeleton(source, runtimeRoot = null) {
        const sections = extractSourceSections(source);
        const runtime = runtimeRoot ? observeRuntime(runtimeRoot) : [];
        const roles = sections.reduce((counts, section) => {
          counts[section.role] = (counts[section.role] || 0) + 1;
          return counts;
        }, {});
        const traps = runtime.filter((item) => item.trap);
        const nestedScrollbarCount = runtime.filter((item) => item.hasInternalScrollbar && item.scrollOwner !== "self").length;
        const selfScrollCount = runtime.filter((item) => item.scrollOwner === "self").length;
        const realSiteScore = [
          roles["page shell"],
          roles.hero,
          roles["trust cluster"],
          roles["conversion form"],
          roles["command row"]
        ].filter(Boolean).length;
        return {
          kind: "mcel-site-skeleton",
          version: "site-skeleton.v1",
          sectionCount: sections.length,
          realSiteScore,
          roles,
          sections,
          runtime,
          layoutHealth: {
            status: traps.length ? "fail" : "pass",
            nestedScrollbarCount,
            selfScrollCount,
            traps: traps.map((item) => `${item.index}: ${item.label}`),
            claim: traps.length
              ? "Runtime produced nested scrollbar traps that MCEL should repair."
              : "No illegal nested scrollbar traps detected by the current runtime observer."
          },
          teachingClaim: "Simple semantic HTML is allowed to be sparse; MCEL supplies the product layout, proof, runtime facts, and clean serializer."
        };
      }

      function compactText(report) {
        if (!report) return "Site skeleton has not been analyzed yet.";
        const lines = [
          `MCEL site skeleton: ${report.sectionCount} semantic region(s), ${report.realSiteScore}/5 product roles present`,
          `layout health: ${report.layoutHealth.status} · illegal nested scrollbars: ${report.layoutHealth.nestedScrollbarCount}`,
          report.teachingClaim
        ];
        report.sections.slice(0, 8).forEach((section) => {
          lines.push(`- ${section.label}: ${section.role} · ${section.policy.component}/${section.policy.render} · scroll=${section.policy.scroll}`);
        });
        if (report.layoutHealth.traps.length) {
          lines.push(`traps: ${report.layoutHealth.traps.join("; ")}`);
        }
        return lines.join("\n");
      }

      return Object.freeze({
        buildSkeleton,
        compactText,
        extractSourceSections,
        observeRuntime
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabSiteSkeleton = McelLabSiteSkeleton;
    }
