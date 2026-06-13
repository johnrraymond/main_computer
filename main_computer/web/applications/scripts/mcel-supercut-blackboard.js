    (function (global) {
      "use strict";

      const BLACKBOARD_VERSION = "0.2.0";
      const COMPONENT_SELECTOR = [
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "form",
        "details",
        "summary",
        "label",
        "button",
        "a[href]",
        "input",
        "select",
        "textarea",
        "output",
        "pre",
        "code",
        "ul",
        "ol",
        "li",
        "[role]",
        "[id]",
        "[class]",
        "[data-mc-component-id]",
        "[data-mc-widget-id]"
      ].join(",");

      function nowIso() {
        try {
          return new Date().toISOString();
        } catch (_error) {
          return "";
        }
      }

      function normalizeText(value, limit = 160) {
        return String(value || "")
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, limit);
      }

      function slugify(value) {
        const slug = normalizeText(value, 80)
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "");
        return slug || "node";
      }

      function escapeSelectorToken(value) {
        if (global.CSS?.escape) return global.CSS.escape(String(value));
        return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "\\$&");
      }

      function stableHash(value) {
        let hash = 0;
        const text = String(value || "");
        for (let index = 0; index < text.length; index += 1) {
          hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
        }
        return Math.abs(hash).toString(36);
      }

      function elementText(element, limit = 180) {
        return normalizeText(element?.textContent || element?.getAttribute?.("aria-label") || element?.getAttribute?.("title") || "", limit);
      }

      function elementSignature(element) {
        if (!element) return "";
        const id = element.id ? `#${element.id}` : "";
        const classes = Array.from(element.classList || []).slice(0, 5).map((item) => `.${item}`).join("");
        const component = element.getAttribute?.("data-mc-component-id") || element.getAttribute?.("data-mc-widget-id") || "";
        const role = element.getAttribute?.("role") || "";
        const label = element.getAttribute?.("aria-label") || "";
        return normalizeText([
          element.tagName?.toLowerCase?.() || "",
          id,
          classes,
          component,
          role,
          label,
          elementText(element, 80)
        ].join(" "), 240);
      }

      function selectorFor(element, root) {
        if (!element) return "";
        if (element.id) return `#${escapeSelectorToken(element.id)}`;
        const componentId = element.getAttribute?.("data-mc-component-id") || "";
        if (componentId) return `[data-mc-component-id="${String(componentId).replace(/"/g, "\\\"")}"]`;
        const parts = [];
        let current = element;
        while (current && current !== root && current.nodeType === 1 && parts.length < 7) {
          const tag = current.tagName.toLowerCase();
          const parent = current.parentElement;
          if (!parent) {
            parts.unshift(tag);
            break;
          }
          const siblings = Array.from(parent.children || []).filter((candidate) => candidate.tagName === current.tagName);
          const index = siblings.indexOf(current) + 1;
          parts.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${index})` : tag);
          current = parent;
        }
        const rootSelector = root?.id ? `#${escapeSelectorToken(root.id)}` : (root?.tagName?.toLowerCase?.() || "body");
        return [rootSelector, ...parts].filter(Boolean).join(" > ");
      }

      function parentRecordId(element, recordsByElement) {
        let parent = element?.parentElement || null;
        while (parent) {
          const record = recordsByElement.get(parent);
          if (record) return record.id;
          parent = parent.parentElement;
        }
        return "";
      }

      function depthFromRoot(element, root) {
        let depth = 0;
        let current = element;
        while (current && current !== root) {
          depth += 1;
          current = current.parentElement;
        }
        return depth;
      }

      function controlCount(element) {
        return Array.from(element?.querySelectorAll?.("button, a[href], input, select, textarea, summary, [role=\"button\"]") || []).length;
      }

      function childRecordIds(record, records) {
        return records.filter((candidate) => candidate.parentRecordId === record.id).map((candidate) => candidate.id);
      }

      function makeRecord(element, root, index, recordsByElement, sessionId) {
        const tag = element?.tagName?.toLowerCase?.() || "";
        const signature = elementSignature(element);
        const idSource = [
          sessionId,
          index,
          element?.id || "",
          element?.getAttribute?.("data-mc-component-id") || "",
          selectorFor(element, root),
          signature
        ].join("|");
        return {
          id: `record-${index + 1}-${stableHash(idSource)}`,
          index,
          element,
          tag,
          selector: selectorFor(element, root),
          sourceSelector: selectorFor(element, root),
          depth: depthFromRoot(element, root),
          parentRecordId: parentRecordId(element, recordsByElement),
          role: element?.getAttribute?.("role") || "",
          domId: element?.id || "",
          classes: Array.from(element?.classList || []),
          componentId: element?.getAttribute?.("data-mc-component-id") || element?.getAttribute?.("data-mc-widget-id") || "",
          text: elementText(element, 180),
          signature,
          controlCount: controlCount(element),
          childElementCount: Array.from(element?.children || []).length,
          facts: [],
          tags: [],
          kind: "",
          purpose: "",
          contract: "",
          risk: "",
          proofPolicy: "",
          rewriteTag: "",
          evidenceIds: [],
          createdAt: nowIso()
        };
      }

      function createBlackboard(options = {}) {
        const rootDocument = options.rootDocument || options.document || global.document;
        const rootNode = options.rootElement || options.root || rootDocument?.querySelector?.(options.rootSelector || "body") || null;
        const sessionId = options.sessionId || `sc-${Date.now().toString(36)}`;
        const recordsByElement = new Map();
        const candidates = rootNode
          ? [rootNode, ...Array.from(rootNode.querySelectorAll?.(options.selector || COMPONENT_SELECTOR) || [])]
          : [];
        const unique = candidates.filter((element, index, all) => element && all.indexOf(element) === index);
        const records = unique.slice(0, Math.max(1, Number(options.maxRecords || options.maxComponents || 260))).map((element, index) => {
          const record = makeRecord(element, rootNode, index, recordsByElement, sessionId);
          recordsByElement.set(element, record);
          return record;
        });
        const byId = new Map(records.map((record) => [record.id, record]));
        records.forEach((record) => {
          record.children = childRecordIds(record, records);
        });

        const blackboard = {
          version: BLACKBOARD_VERSION,
          sessionId,
          specimenId: options.specimenId || options.app || rootNode?.id || "legacy-html",
          rootNode,
          rootSelector: options.rootSelector || (rootNode?.id ? `#${escapeSelectorToken(rootNode.id)}` : "body"),
          records,
          recordsByElement,
          recordsById: byId,
          evidence: [],
          hypotheses: [],
          components: [],
          regions: [],
          actions: [],
          risks: [],
          layoutLaws: [],
          violations: [],
          explanations: [],
          rewritePreview: [],
          metrics: {
            nodesScanned: records.length,
            components: 0,
            regions: 0,
            actionsClassified: 0,
            unsafeActionsBlocked: 0,
            rulesFired: 0,
            packsLoaded: 0,
            rewritePreviewNodes: 0,
            violations: 0,
            explanationsReady: 0,
            sourceMutations: 0,
            runtimeSourceMutations: 0,
            unknownPurpose: 0
          }
        };

        function addEvidence(record, type, value, ruleId) {
          const evidence = {
            id: `evidence-${blackboard.evidence.length + 1}`,
            recordId: record?.id || "",
            type,
            value: normalizeText(value, 260),
            ruleId: ruleId || "",
            createdAt: nowIso()
          };
          blackboard.evidence.push(evidence);
          if (record && !record.evidenceIds.includes(evidence.id)) record.evidenceIds.push(evidence.id);
          return evidence;
        }

        function explain(record, summary, details, ruleId) {
          const explanation = {
            id: `explanation-${blackboard.explanations.length + 1}`,
            recordId: record?.id || "",
            summary: normalizeText(summary, 220),
            details: Array.isArray(details) ? details.slice(0, 8).map((item) => normalizeText(item, 180)) : [normalizeText(details, 180)].filter(Boolean),
            ruleId: ruleId || "",
            createdAt: nowIso()
          };
          blackboard.explanations.push(explanation);
          blackboard.metrics.explanationsReady = blackboard.explanations.length;
          return explanation;
        }

        function ensureComponent(record, patch = {}, ruleId = "") {
          if (!record) return null;
          const contracts = global.McelSupercutContracts;
          const nextRisk = patch.risk || record.risk || "";
          const contract = patch.contract
            ? contracts?.getContract?.(patch.contract)
            : contracts?.contractForKind?.(patch.kind || record.kind || "unknown", nextRisk);
          const existing = blackboard.components.find((component) => component.recordId === record.id);
          const component = existing || {
            id: `sc-node-${blackboard.components.length + 1}`,
            recordId: record.id,
            sourceSelector: record.sourceSelector,
            originalTag: record.tag,
            role: "",
            kind: "",
            originalPurpose: "",
            purpose: "",
            contract: "",
            risk: "",
            proofPolicy: "",
            rewriteTag: "",
            evidence: [],
            children: record.children || []
          };
          Object.assign(component, {
            role: patch.role || record.role || component.role || "",
            kind: patch.kind || record.kind || component.kind || "unknown",
            originalPurpose: patch.purpose || patch.originalPurpose || record.purpose || component.originalPurpose || "legacy-html.unknown-purpose",
            purpose: patch.purpose || patch.originalPurpose || record.purpose || component.purpose || "legacy-html.unknown-purpose",
            contract: contract?.id || patch.contract || record.contract || component.contract || "component.unknown",
            risk: patch.risk || record.risk || contract?.riskDefault || component.risk || "unknown",
            proofPolicy: patch.proofPolicy || record.proofPolicy || contracts?.proofPolicyForRisk?.(patch.risk || record.risk || contract?.riskDefault) || contract?.proofPolicy || component.proofPolicy || "inspect-only",
            rewriteTag: patch.rewriteTag || record.rewriteTag || contract?.rewriteTag || component.rewriteTag || "mcel-unknown",
            evidence: Array.from(new Set([...(component.evidence || []), ...(patch.evidence || record.evidenceIds || [])]))
          });
          record.kind = component.kind;
          record.purpose = component.purpose;
          record.contract = component.contract;
          record.risk = component.risk;
          record.proofPolicy = component.proofPolicy;
          record.rewriteTag = component.rewriteTag;
          if (ruleId) addEvidence(record, "component-contract", `${component.contract} via ${ruleId}`, ruleId);
          if (!existing) blackboard.components.push(component);
          blackboard.metrics.components = blackboard.components.length;
          if (component.contract === "component.unknown" || component.purpose === "legacy-html.unknown-purpose") {
            blackboard.metrics.unknownPurpose = blackboard.components.filter((item) => item.contract === "component.unknown" || item.purpose === "legacy-html.unknown-purpose").length;
          }
          return component;
        }

        function addRegion(record, patch = {}, ruleId = "") {
          const component = ensureComponent(record, {...patch, kind: "region", contract: patch.contract || "component.region"}, ruleId);
          if (!component) return null;
          const existing = blackboard.regions.find((region) => region.recordId === record.id);
          const region = existing || {
            id: `region-${blackboard.regions.length + 1}`,
            recordId: record.id,
            selector: record.sourceSelector
          };
          Object.assign(region, {
            role: patch.role || component.role || "semantic-region",
            purpose: patch.purpose || component.purpose || "legacy-html.region",
            evidence: patch.evidence || component.evidence || []
          });
          if (!existing) blackboard.regions.push(region);
          blackboard.metrics.regions = blackboard.regions.length;
          return region;
        }

        function addAction(record, patch = {}, ruleId = "") {
          const risk = patch.risk || record.risk || "safe";
          const contracts = global.McelSupercutContracts;
          const policy = patch.proofPolicy || contracts?.proofPolicyForRisk?.(risk) || "inspect-only";
          const contract = patch.contract || contracts?.contractForKind?.("action", risk)?.id || "component.action";
          const component = ensureComponent(record, {
            ...patch,
            kind: "action",
            contract,
            risk,
            proofPolicy: policy,
            role: patch.role || "action-surface"
          }, ruleId);
          const existing = blackboard.actions.find((action) => action.recordId === record.id);
          const action = existing || {
            id: `action-${blackboard.actions.length + 1}`,
            recordId: record.id,
            selector: record.sourceSelector
          };
          Object.assign(action, {
            role: patch.role || component.role || "action-surface",
            label: patch.label || record.text || record.domId || record.sourceSelector,
            risk,
            proofPolicy: policy,
            contract,
            blocked: Boolean(contracts?.riskPolicy?.(risk)?.blocked)
          });
          if (!existing) blackboard.actions.push(action);
          blackboard.metrics.actionsClassified = blackboard.actions.length;
          blackboard.metrics.unsafeActionsBlocked = blackboard.actions.filter((item) => item.blocked).length;
          return action;
        }

        function addRisk(record, risk, proofPolicy, reason, ruleId) {
          if (!record) return null;
          const contracts = global.McelSupercutContracts;
          const policy = proofPolicy || contracts?.proofPolicyForRisk?.(risk) || "inspect-only";
          const item = {
            id: `risk-${blackboard.risks.length + 1}`,
            recordId: record.id,
            selector: record.sourceSelector,
            risk,
            proofPolicy: policy,
            blocked: Boolean(contracts?.riskPolicy?.(risk)?.blocked),
            reason: normalizeText(reason, 220),
            ruleId: ruleId || ""
          };
          blackboard.risks.push(item);
          record.risk = risk;
          record.proofPolicy = policy;
          if (item.blocked) blackboard.metrics.unsafeActionsBlocked = blackboard.risks.filter((candidate) => candidate.blocked).length;
          return item;
        }

        function addViolation(law, record, message, severity = "warning") {
          const violation = {
            id: `violation-${blackboard.violations.length + 1}`,
            law,
            recordId: record?.id || "",
            selector: record?.sourceSelector || "",
            message: normalizeText(message, 260),
            severity
          };
          blackboard.violations.push(violation);
          blackboard.metrics.violations = blackboard.violations.length;
          return violation;
        }

        function addRewriteNode(component) {
          if (!component) return null;
          const existing = blackboard.rewritePreview.find((node) => node.id === component.id);
          const node = existing || {
            id: component.id,
            sourceSelector: component.sourceSelector,
            originalTag: component.originalTag,
            proposedTag: component.rewriteTag,
            originalPurpose: component.purpose || component.originalPurpose,
            contract: component.contract,
            risk: component.risk,
            proofPolicy: component.proofPolicy,
            evidence: component.evidence || [],
            children: component.children || []
          };
          Object.assign(node, {
            proposedTag: component.rewriteTag,
            originalPurpose: component.purpose || component.originalPurpose,
            contract: component.contract,
            risk: component.risk,
            proofPolicy: component.proofPolicy,
            evidence: component.evidence || [],
            children: component.children || []
          });
          if (!existing) blackboard.rewritePreview.push(node);
          blackboard.metrics.rewritePreviewNodes = blackboard.rewritePreview.length;
          return node;
        }

        Object.assign(blackboard, {
          normalizeText,
          slugify,
          selectorFor,
          elementText,
          elementSignature,
          addEvidence,
          explain,
          ensureComponent,
          addRegion,
          addAction,
          addRisk,
          addViolation,
          addRewriteNode,
          recordForElement: (element) => recordsByElement.get(element) || null,
          recordById: (id) => byId.get(id) || null
        });

        return blackboard;
      }

      function serializableBlackboard(blackboard) {
        if (!blackboard) return null;
        return {
          version: blackboard.version,
          sessionId: blackboard.sessionId,
          specimenId: blackboard.specimenId,
          rootSelector: blackboard.rootSelector,
          records: blackboard.records.map((record) => ({
            id: record.id,
            index: record.index,
            tag: record.tag,
            selector: record.selector,
            sourceSelector: record.sourceSelector,
            depth: record.depth,
            parentRecordId: record.parentRecordId,
            children: record.children || [],
            role: record.role,
            domId: record.domId,
            classes: record.classes,
            componentId: record.componentId,
            text: record.text,
            controlCount: record.controlCount,
            childElementCount: record.childElementCount,
            kind: record.kind,
            purpose: record.purpose,
            contract: record.contract,
            risk: record.risk,
            proofPolicy: record.proofPolicy,
            rewriteTag: record.rewriteTag,
            evidenceIds: record.evidenceIds
          })),
          evidence: blackboard.evidence,
          hypotheses: blackboard.hypotheses,
          components: blackboard.components,
          regions: blackboard.regions,
          actions: blackboard.actions,
          risks: blackboard.risks,
          layoutLaws: blackboard.layoutLaws,
          violations: blackboard.violations,
          explanations: blackboard.explanations,
          rewritePreview: blackboard.rewritePreview,
          metrics: blackboard.metrics
        };
      }

      global.McelSupercutBlackboard = {
        BLACKBOARD_VERSION,
        COMPONENT_SELECTOR,
        createBlackboard,
        serializableBlackboard,
        normalizeText,
        slugify,
        stableHash
      };
    })(window);
