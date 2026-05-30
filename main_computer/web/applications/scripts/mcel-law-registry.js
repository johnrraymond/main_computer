    var McelLabLawRegistry = (() => {
      const registry = new Map();

      function now() {
        return new Date().toISOString();
      }

      function normalizeDescriptor(descriptor = {}) {
        if (!descriptor.id) throw new Error("MCEL law descriptor requires an id.");
        return Object.freeze({
          id: String(descriptor.id),
          label: descriptor.label || descriptor.id,
          version: descriptor.version || "v1",
          reads: Object.freeze([...(descriptor.reads || [])]),
          writesRuntimeOnly: Object.freeze([...(descriptor.writesRuntimeOnly || [])]),
          sourcePollutionForbidden: descriptor.sourcePollutionForbidden !== false,
          compute: descriptor.compute || null,
          apply: descriptor.apply || descriptor.applyRuntimeLaw || null,
          inspect: descriptor.inspect || null,
          prove: descriptor.prove || null,
          reportFor: descriptor.reportFor || null,
          registeredAt: now()
        });
      }

      function register(descriptor) {
        const normalized = normalizeDescriptor(descriptor);
        registry.set(normalized.id, normalized);
        return normalized;
      }

      function get(id) {
        return registry.get(String(id)) || null;
      }

      function list() {
        return [...registry.values()].map((law) => ({
          id: law.id,
          label: law.label,
          version: law.version,
          reads: [...law.reads],
          writesRuntimeOnly: [...law.writesRuntimeOnly],
          sourcePollutionForbidden: law.sourcePollutionForbidden,
          registeredAt: law.registeredAt
        }));
      }

      function apply(root, options = {}) {
        const reports = [];
        registry.forEach((law) => {
          if (typeof law.apply === "function") {
            reports.push({
              id: law.id,
              label: law.label,
              report: law.apply(root, options)
            });
          }
        });
        return {
          kind: "mcel-law-registry-apply-report",
          generatedAt: now(),
          lawCount: registry.size,
          applied: reports
        };
      }

      function prove(root, options = {}) {
        const reports = [];
        registry.forEach((law) => {
          if (typeof law.prove === "function") {
            reports.push({
              id: law.id,
              label: law.label,
              report: law.prove(root, options)
            });
          } else if (typeof law.reportFor === "function") {
            reports.push({
              id: law.id,
              label: law.label,
              report: law.reportFor(root, options)
            });
          }
        });
        const failed = reports.filter((item) => item.report?.failed || item.report?.layoutLawClean === false || item.report?.cssLawClean === false).length;
        return {
          kind: "mcel-law-registry-proof-report",
          generatedAt: now(),
          lawCount: registry.size,
          failed,
          passed: reports.length - failed,
          reports
        };
      }

      function buildAxisMatrix(feature = "unknown") {
        return {
          kind: "mcel-axis-matrix",
          feature,
          axes: [
            "source schema",
            "compiler interpretation",
            "runtime generated behavior",
            "serializer cleanup",
            "editor trait control",
            "graph/provenance ownership",
            "css/layout/a11y law",
            "component/state/data/form/action/render law",
            "browser semantic proof",
            "performance/security budget",
            "acid/proof tests",
            "evidence packet / supervisor gate"
          ],
          laws: list()
        };
      }

      return Object.freeze({
        register,
        get,
        list,
        apply,
        prove,
        buildAxisMatrix
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabLawRegistry = McelLabLawRegistry;
    }
