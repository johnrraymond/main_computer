    (function (global) {
      "use strict";

      const BLUEPRINTS_CORE_VERSION = "0.3.0";
      const GENERIC_ASPECT_IDS = Object.freeze([
        "overview",
        "form",
        "objects",
        "workflows",
        "layout",
        "actions",
        "capabilities",
        "evidence",
        "source",
        "tests",
        "annotations",
        "findings",
        "repair"
      ]);

      const GENERIC_LAYOUT_ZONES = Object.freeze([
        "identity",
        "navigation",
        "primary",
        "inspector",
        "evidence",
        "actions",
        "status",
        "advanced"
      ]);

      const GENERIC_INSPECTION_ELEMENTS = Object.freeze([
        "element.inspection.aspect-map",
        "element.inspection.aspect-panel",
        "element.inspection.blueprint-editor",
        "element.inspection.source-binding",
        "element.inspection.implementation-delta",
        "element.inspection.acid-test-result",
        "element.inspection.repair-finding",
        "element.inspection.repair-plan"
      ]);

      const GENERIC_REFACTOR_ELEMENTS = Object.freeze([
        "element.refactor.annotation-map",
        "element.refactor.element-annotation",
        "element.refactor.removal-candidate",
        "element.refactor.rework-candidate",
        "element.refactor.refactor-export-packet"
      ]);

      const REQUIRED_DEPENDENCY_CHECKS = Object.freeze([
        "handlers",
        "tests",
        "docs",
        "sourceOwners",
        "replacementPath"
      ]);

      const REQUIRED_EXPORT_PACKET_FILES = Object.freeze([
        "manifest.json",
        "app-blueprint.json",
        "annotations.json",
        "dom-snapshot.html",
        "layout-report.json",
        "source-map.json",
        "acid-test-report.json",
        "refactor-brief.md",
        "tests-to-update.json"
      ]);

      const REQUIRED_MOUNT_CAPTURE_FIELDS = Object.freeze([
        "appId",
        "route",
        "domSnapshot",
        "dataMcelAttributes",
        "layoutZones",
        "visibleText",
        "boundingBoxes",
        "sourceFileHints",
        "plannerMetadata",
        "knownTests",
        "knownDocs",
        "cssOwners",
        "jsOwners"
      ]);

      const REQUIRED_INSPECTION_FIELDS = Object.freeze([
        "recordId",
        "appId",
        "owningAppId",
        "selectedAppId",
        "inspectionRootId",
        "inspectionContext",
        "selector",
        "previewPath",
        "visibleText",
        "tagName",
        "role",
        "mcelElementGuess",
        "layoutZone",
        "parentRegion",
        "boundingBox",
        "dataMcelAttributes",
        "nearbyElements",
        "sourceHints",
        "cssOwners",
        "jsOwners",
        "testHints"
      ]);

      const BLUEPRINT_DETAIL_GROUPS = Object.freeze([
        Object.freeze({
          id: "aspect-contract",
          label: "Aspect contract",
          renderer: "aspect-contract",
          source: "selectedAspect"
        }),
        Object.freeze({
          id: "semantic-form-primitives",
          label: "Semantic form primitives",
          renderer: "semantic-form-primitives",
          source: "requirementsContract.form_primitives"
        }),
        Object.freeze({
          id: "layout-zone-contract",
          label: "Layout zone contract",
          renderer: "layout-zones",
          source: "layoutBinding.zones"
        }),
        Object.freeze({
          id: "support-hints",
          label: "Source, test, and documentation hints",
          renderer: "support-hints",
          source: "blueprintHints"
        }),
        Object.freeze({
          id: "export-contract",
          label: "Export contract",
          renderer: "export-contract",
          source: "annotationPolicy,exportPolicy"
        }),
        Object.freeze({
          id: "raw-blueprint-json",
          label: "Raw blueprint JSON",
          renderer: "raw-json",
          source: "trimmedBlueprint",
          advanced: true
        })
      ]);

      function freezeDeep(value) {
        if (!value || typeof value !== "object") return value;
        Object.freeze(value);
        Object.getOwnPropertyNames(value).forEach((name) => freezeDeep(value[name]));
        return value;
      }

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function aspect(id, label, elementId, requiredEvidence = []) {
        return Object.freeze({
          id,
          label,
          elementId,
          requiredEvidence: Object.freeze(requiredEvidence.slice()),
          findingPolicy: "missing evidence produces findings, not silent blanks"
        });
      }

      function buildGenericAspects() {
        return Object.freeze([
          aspect("overview", "Overview", "element.inspection.aspect-panel", ["app identity", "dominant object", "route", "purpose"]),
          aspect("form", "Form", "element.inspection.aspect-panel", ["subjects", "actions", "work surfaces", "context", "feedback", "constraints", "transients", "interruptions"]),
          aspect("objects", "Objects", "element.inspection.aspect-panel", ["dominant object", "object model", "relationships"]),
          aspect("workflows", "Workflows", "element.inspection.aspect-panel", ["workflow map", "primary workflow", "secondary workflows"]),
          aspect("layout", "Layout", "element.inspection.aspect-panel", ["layout zones", "zone bindings", "primary surface policy"]),
          aspect("actions", "Actions", "element.inspection.aspect-panel", ["action hierarchy", "risk policy", "blocked actions"]),
          aspect("capabilities", "Capabilities", "element.inspection.aspect-panel", ["provider projections", "consumer-native placement"]),
          aspect("evidence", "Evidence", "element.inspection.aspect-panel", ["DOM snapshot", "implementation evidence", "gaps"]),
          aspect("source", "Source", "element.inspection.source-binding", ["source files", "CSS owners", "JS owners", "route"]),
          aspect("tests", "Tests", "element.inspection.acid-test-result", ["known tests", "acid checks", "test expectations"]),
          aspect("annotations", "Annotations", "element.refactor.annotation-map", ["saved annotations", "remove/rework dependency checks"]),
          aspect("findings", "Findings", "element.inspection.repair-finding", ["repair findings", "severity", "required checks"]),
          aspect("repair", "Repair", "element.inspection.repair-plan", ["repair plan", "patch mode", "tests to update"])
        ]);
      }

      function annotationPolicy(appId) {
        return Object.freeze({
          appId,
          annotationElementId: "element.refactor.element-annotation",
          candidateElementIds: Object.freeze([
            "element.refactor.removal-candidate",
            "element.refactor.rework-candidate"
          ]),
          allowedKinds: Object.freeze(["keep", "remove", "rework", "move", "hide", "merge", "investigate"]),
          requiredFields: Object.freeze([
            "targetSelector",
            "mcelRole",
            "layoutZone",
            "userReasoning",
            "allowedOutcomes",
            "forbiddenOutcomes",
            "dependencyChecks",
            "sourceHints",
            "testExpectations"
          ]),
          requiredDependencyChecks: REQUIRED_DEPENDENCY_CHECKS,
          removalOrReworkRequiresDependencyChecks: true,
          dependencyChecksAreRequiredBeforeDeletion: true,
          userIntentIsNotVerifiedFact: true
        });
      }

      function exportPolicy(appId) {
        return Object.freeze({
          appId,
          packetElementId: "element.refactor.refactor-export-packet",
          requiredFiles: REQUIRED_EXPORT_PACKET_FILES,
          mustIncludeAnnotations: true,
          mustIncludeSourceHints: true,
          mustIncludeAllowedAndForbiddenOutcomes: true,
          neverClaimUncheckedDependencies: true,
          patchMode: "replacement-file-guidance"
        });
      }

      function mountPolicy(appId, rootSelector, route, options = {}) {
        return Object.freeze({
          appId,
          mode: "same-page-contained-clone",
          route,
          rootSelector,
          previewSurfaceSelector: "#mcel-blueprint-work-surface",
          preserveDataMcelAttributes: true,
          stripDuplicateIds: true,
          inertPreview: true,
          sourceMutationAllowed: false,
          autoMountOnActivate: options.autoMountOnActivate !== false,
          autoMountOnSelect: options.autoMountOnSelect !== false,
          selfMountRecursionGuard: Boolean(options.selfMountRecursionGuard),
          requiredCaptureFields: REQUIRED_MOUNT_CAPTURE_FIELDS,
          detailSource: "blueprint.mountPolicy"
        });
      }

      function inspectionPolicy(appId, options = {}) {
        const inspectionRoots = [
          {
            id: "mounted-app",
            context: "mounted-app",
            selector: "[data-mcel-preview-clone]",
            eventRootSelector: ".mcel-lab-mounted-preview-frame",
            owningAppId: appId,
            excludeSelectors: []
          }
        ];
        (options.hostSurfaceSelectors || []).forEach((selector, index) => {
          inspectionRoots.push({
            id: `host-surface-${index + 1}`,
            context: "host-app",
            selector,
            eventRootSelector: selector,
            owningAppId: "mcel-lab",
            excludeSelectors: [".mcel-lab-mounted-preview-frame"]
          });
        });
        return Object.freeze({
          appId,
          mode: "multi-surface-point-inspection",
          enabled: true,
          patternId: "pattern.point-and-annotate",
          selectedElementId: "element.refactor.element-annotation",
          preventDefaultActions: true,
          sourceMutationAllowed: false,
          hoverHighlight: true,
          selectionHighlight: true,
          coordinateHitTesting: true,
          proximityRadius: 18,
          cycleModifier: "Alt",
          revealSelectedElement: true,
          inspectionRoots: Object.freeze(
            inspectionRoots.map((root) => Object.freeze({
              ...root,
              excludeSelectors: Object.freeze(root.excludeSelectors.slice())
            }))
          ),
          selectorAttributes: Object.freeze([
            "data-mc-component-id",
            "data-mc-widget-id",
            "data-mcel-element",
            "aria-label"
          ]),
          layoutZoneAttributes: Object.freeze([
            "data-mcel-layout-zone",
            "data-mcel-zone"
          ]),
          roleAttributes: Object.freeze([
            "role",
            "data-mc-component-kind",
            "data-mc-widget-kind"
          ]),
          requiredSelectedElementFields: REQUIRED_INSPECTION_FIELDS,
          detailSource: "blueprint.inspectionPolicy"
        });
      }


      const REQUIREMENTS_APP_RUNTIME_HINTS = Object.freeze({
        "calculator": Object.freeze({
          label: "Calculator",
          route: "/applications/calculator",
          rootSelector: "#calculator-app",
          sourceHints: Object.freeze([
            "main_computer/web/applications/apps/calculator.html",
            "main_computer/web/applications/scripts/calculator.js",
            "main_computer/web/applications/styles/calculator.css"
          ]),
          testHints: Object.freeze(["tests/test_mcel_calculator_requirements.py"]),
          docHints: Object.freeze(["pretty_docs/mcel-calculator-requirements.md"]),
          riskFamilies: Object.freeze(["expression-evaluation", "graph-domain", "local-state"])
        }),
        "code-editor": Object.freeze({
          label: "Code Editor",
          route: "/applications/code-editor",
          rootSelector: "#code-editor-app",
          sourceHints: Object.freeze([
            "main_computer/web/applications/apps/code-editor.html",
            "main_computer/web/applications/scripts/code-editor-mcel-studio.js",
            "main_computer/web/applications/scripts/mcel-self-diagnosis.js",
            "main_computer/web/applications/styles/code-editor.css"
          ]),
          testHints: Object.freeze([
            "tests/test_mcel_code_studio_app.py",
            "tests/test_flog_code_editor_live_smoke.py"
          ]),
          docHints: Object.freeze(["pretty_docs/mcel-code-editor-requirements.md"]),
          riskFamilies: Object.freeze(["source-mutation", "ai-assisted-edit", "runtime-preview-leakage"])
        }),
        "file-explorer": Object.freeze({
          label: "File Explorer",
          route: "/applications/file-explorer",
          rootSelector: "#file-explorer-app",
          sourceHints: Object.freeze([
            "main_computer/web/applications/apps/file-explorer.html",
            "main_computer/web/applications/scripts/file-explorer.js",
            "main_computer/web/applications/styles/file-explorer.css"
          ]),
          testHints: Object.freeze(["tests/test_mcel_requirements_registry.py"]),
          docHints: Object.freeze(["pretty_docs/mcel-file-explorer-requirements.md"]),
          riskFamilies: Object.freeze(["filesystem-read", "path-selection", "mutation-prohibited"])
        }),
        "git-tools": Object.freeze({
          label: "Git Tools",
          route: "/applications/git-tools",
          rootSelector: "#git-tools-app",
          sourceHints: Object.freeze([
            "main_computer/web/applications/apps/git-tools.html",
            "main_computer/web/applications/scripts/git-tools-mcel.js",
            "main_computer/web/applications/scripts/git-tools-semantic-adapter.js",
            "main_computer/web/applications/styles/git-tools.css"
          ]),
          testHints: Object.freeze([
            "tests/test_git_tools_semantic_runtime.py",
            "tests/test_mcel_requirements_registry.py"
          ]),
          docHints: Object.freeze(["pretty_docs/mcel-git-tools-requirements.md"]),
          riskFamilies: Object.freeze(["repository-mutation", "remote-publish", "manual-command"])
        }),
        "mcel-lab": Object.freeze({
          label: "MCEL Lab",
          route: "/applications/mcel-lab",
          rootSelector: "#mcel-lab-app",
          sourceHints: Object.freeze([
            "main_computer/web/applications/apps/mcel-lab.html",
            "main_computer/web/applications/scripts/mcel-lab.js",
            "main_computer/web/applications/scripts/mcel-app-blueprints-core.js",
            "main_computer/web/applications/styles/mcel-lab.css"
          ]),
          testHints: Object.freeze([
            "tests/test_mcel_lab_app.py",
            "tests/test_mcel_lab_blueprint_studio_documentation.py",
            "tests/test_mcel_app_blueprint_contracts.py"
          ]),
          docHints: Object.freeze(["pretty_docs/mcel-lab-blueprint-studio.md"]),
          riskFamilies: Object.freeze(["self-recursion", "specimen-boundary", "live-self-overwrite"])
        }),
        "website-builder": Object.freeze({
          label: "Website Builder",
          route: "/applications/website-builder",
          rootSelector: "#website-builder-app",
          sourceHints: Object.freeze([
            "main_computer/web/applications/apps/website-builder.html",
            "main_computer/web/applications/scripts/website-builder.js",
            "main_computer/web/applications/styles/website-builder.css"
          ]),
          testHints: Object.freeze([
            "tests/test_website_builder_application.py",
            "tests/test_mcel_requirements_registry.py"
          ]),
          docHints: Object.freeze(["pretty_docs/mcel-website-builder-requirements.md"]),
          riskFamilies: Object.freeze(["site-save", "site-publish", "runtime-page-generation"])
        })
      });

      const FORM_PRIMITIVE_KIND_ORDER = Object.freeze([
        "subject",
        "action",
        "work-surface",
        "context",
        "feedback",
        "constraint",
        "transient",
        "interruption"
      ]);

      function uniqueStrings(values) {
        const seen = new Set();
        const result = [];
        (Array.isArray(values) ? values : []).forEach((value) => {
          const text = String(value || "").trim();
          if (!text || seen.has(text)) return;
          seen.add(text);
          result.push(text);
        });
        return result;
      }

      function requirementsRegistryContracts() {
        const registry = global.McelRequirementsRegistry;
        if (!registry || typeof registry.listAppContracts !== "function") return [];
        try {
          return registry.listAppContracts() || [];
        } catch (_error) {
          return [];
        }
      }

      function normalizeFormPrimitiveKind(value) {
        return String(value || "unknown").trim().toLowerCase() || "unknown";
      }

      function formPrimitiveKindCounts(primitives) {
        const counts = {};
        (Array.isArray(primitives) ? primitives : []).forEach((primitive) => {
          const kind = normalizeFormPrimitiveKind(primitive?.primitive);
          counts[kind] = (counts[kind] || 0) + 1;
        });
        return Object.fromEntries(Object.entries(counts).sort((left, right) => {
          const leftIndex = FORM_PRIMITIVE_KIND_ORDER.indexOf(left[0]);
          const rightIndex = FORM_PRIMITIVE_KIND_ORDER.indexOf(right[0]);
          if (leftIndex !== -1 || rightIndex !== -1) {
            return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex);
          }
          return left[0].localeCompare(right[0]);
        }));
      }

      function formPrimitiveGroups(primitives) {
        const groups = {};
        (Array.isArray(primitives) ? primitives : []).forEach((primitive) => {
          const kind = normalizeFormPrimitiveKind(primitive?.primitive);
          if (!groups[kind]) groups[kind] = [];
          groups[kind].push(clone(primitive));
        });
        return Object.fromEntries(Object.entries(groups).sort((left, right) => {
          const leftIndex = FORM_PRIMITIVE_KIND_ORDER.indexOf(left[0]);
          const rightIndex = FORM_PRIMITIVE_KIND_ORDER.indexOf(right[0]);
          if (leftIndex !== -1 || rightIndex !== -1) {
            return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex);
          }
          return left[0].localeCompare(right[0]);
        }));
      }

      function buildRequirementsBackedBlueprint(contract, baseBlueprint = null) {
        if (!contract || !contract.app) return baseBlueprint ? clone(baseBlueprint) : null;
        const appId = String(contract.app);
        const hint = REQUIREMENTS_APP_RUNTIME_HINTS[appId] || {};
        const label = contract.title || baseBlueprint?.label || hint.label || appId;
        const route = baseBlueprint?.route || hint.route || `/applications/${appId}`;
        const rootSelector = baseBlueprint?.rootSelector || hint.rootSelector || `#${appId}-app`;
        const formPrimitives = Array.isArray(contract.form_primitives) ? clone(contract.form_primitives) : [];
        const firstRegions = Array.isArray(contract.first_regions) ? clone(contract.first_regions) : [];
        const runtimeChecks = Array.isArray(contract.runtime_checks) ? clone(contract.runtime_checks) : [];
        const useCases = Array.isArray(contract.use_cases) ? clone(contract.use_cases) : [];
        const sourceDoc = contract.source?.file ? [contract.source.file] : [];
        const sourceHints = uniqueStrings([
          ...(baseBlueprint?.sourceHints || []),
          ...(hint.sourceHints || []),
          ...sourceDoc
        ]);
        const testHints = uniqueStrings([
          ...(baseBlueprint?.testHints || []),
          ...(hint.testHints || [])
        ]);
        const docHints = uniqueStrings([
          ...(baseBlueprint?.docHints || []),
          ...(hint.docHints || []),
          ...sourceDoc
        ]);
        const riskFamilies = uniqueStrings([
          ...(baseBlueprint?.riskFamilies || []),
          ...(hint.riskFamilies || [])
        ]);
        return {
          ...(baseBlueprint || {}),
          appId,
          aliases: Array.isArray(baseBlueprint?.aliases) ? baseBlueprint.aliases.slice() : [],
          label,
          route,
          rootSelector,
          mountPolicy: baseBlueprint?.mountPolicy || mountPolicy(appId, rootSelector, route, {selfMountRecursionGuard: appId === "mcel-lab"}),
          inspectionPolicy: baseBlueprint?.inspectionPolicy || inspectionPolicy(appId, {
            hostSurfaceSelectors: ["#mcel-lab-app [data-mcel-layout-zone='primary']"]
          }),
          blueprintElementId: baseBlueprint?.blueprintElementId || "element.workbench.specification",
          dominantObject: contract.dominant_object || baseBlueprint?.dominantObject || "App",
          purpose: contract.primary_user_goal || baseBlueprint?.purpose || `Inspect the ${label} requirements contract, semantic form, runtime checks, and implementation evidence.`,
          aspects: buildGenericAspects(),
          aspectIds: GENERIC_ASPECT_IDS,
          genericInspectionElements: GENERIC_INSPECTION_ELEMENTS,
          genericRefactorElements: GENERIC_REFACTOR_ELEMENTS,
          layoutZones: baseBlueprint?.layoutZones || GENERIC_LAYOUT_ZONES,
          detailGroups: BLUEPRINT_DETAIL_GROUPS,
          layoutBinding: baseBlueprint?.layoutBinding || {
            root: rootSelector,
            zones: Object.fromEntries(GENERIC_LAYOUT_ZONES.map((zone) => [zone, `[data-mcel-layout-zone='${zone}'], [data-mcel-zone='${zone}']`]))
          },
          sourceHints,
          testHints,
          docHints,
          riskFamilies,
          annotationPolicy: baseBlueprint?.annotationPolicy || annotationPolicy(appId),
          exportPolicy: baseBlueprint?.exportPolicy || exportPolicy(appId),
          requirementsContract: {
            app: appId,
            title: contract.title || label,
            status: contract.status || "",
            contractComplete: contract.contract_complete === true,
            currentRuntimeStatus: contract.current_runtime_status || "",
            targetRuntimeStatus: contract.target_runtime_status || "",
            totalBlocks: Object.values(contract.block_type_counts || {}).reduce((total, value) => total + Number(value || 0), 0),
            blockTypeCounts: clone(contract.block_type_counts || {}),
            formPrimitiveCount: formPrimitives.length,
            formPrimitiveKinds: formPrimitiveKindCounts(formPrimitives),
            runtimeCheckCount: runtimeChecks.length,
            regionCount: Number(contract.region_count || firstRegions.length || 0),
            intentCount: Number(contract.intent_count || 0),
            source: clone(contract.source || null)
          },
          formPrimitives,
          formPrimitiveGroups: formPrimitiveGroups(formPrimitives),
          formPrimitiveKinds: formPrimitiveKindCounts(formPrimitives),
          formPrimitiveCount: formPrimitives.length,
          regionContracts: firstRegions,
          runtimeChecks,
          useCases
        };
      }

      function buildInspectableBlueprints() {
        const byId = new Map(Object.values(BLUEPRINTS).map((blueprint) => [blueprint.appId, blueprint]));
        requirementsRegistryContracts().forEach((contract) => {
          const base = byId.get(contract.app) || null;
          const blueprint = buildRequirementsBackedBlueprint(contract, base);
          if (blueprint) byId.set(blueprint.appId, blueprint);
        });
        return Array.from(byId.values()).sort((left, right) => {
          if (left.appId === "document-editor") return -1;
          if (right.appId === "document-editor") return 1;
          return String(left.label || left.appId).localeCompare(String(right.label || right.appId));
        });
      }

      const BLUEPRINTS = freezeDeep({
        "document-editor": {
          appId: "document-editor",
          aliases: ["document"],
          label: "Document Editor",
          route: "/applications/document",
          rootSelector: "#document-app",
          mountPolicy: mountPolicy("document-editor", "#document-app", "/applications/document"),
          inspectionPolicy: inspectionPolicy("document-editor", {
            hostSurfaceSelectors: ["#mcel-lab-app [data-mcel-layout-zone='primary']"]
          }),
          blueprintElementId: "element.workbench.specification",
          dominantObject: "Document",
          purpose: "Inspectable writing workbench with document page primary, navigation left, companion/history/AI context right, and visible save/status evidence.",
          aspects: buildGenericAspects(),
          aspectIds: GENERIC_ASPECT_IDS,
          genericInspectionElements: GENERIC_INSPECTION_ELEMENTS,
          genericRefactorElements: GENERIC_REFACTOR_ELEMENTS,
          layoutZones: GENERIC_LAYOUT_ZONES,
          detailGroups: BLUEPRINT_DETAIL_GROUPS,
          layoutBinding: {
            root: "[data-mcel-workbench='document-editor']",
            shell: "[data-mcel-layout='page-centered-writing-workbench']",
            zones: {
              identity: "[data-mcel-workbench='document-editor']",
              navigation: "[data-mcel-layout-zone='navigation']",
              primary: "[data-mcel-layout-zone='primary']",
              inspector: "[data-mcel-layout-zone='companion']",
              evidence: "[data-mcel-layout-zone='companion'], [data-mcel-layout-zone='advanced']",
              actions: "[data-mcel-layout-zone='toolbar']",
              status: "[data-mcel-layout-zone='status']",
              advanced: "[data-mcel-layout-zone='advanced']"
            }
          },
          sourceHints: [
            "main_computer/web/applications/apps/document.html",
            "main_computer/web/applications/scripts/document-editor.js",
            "main_computer/web/applications/styles/document.css",
            "main_computer/web/applications/scripts/mcel-specimen-planner.js",
            "main_computer/web/applications/scripts/mcel-elements-core.js",
            "main_computer/web/applications/scripts/mcel-toolkit-core.js"
          ],
          testHints: [
            "tests/test_document_editor_mcel_layout_binding.py",
            "tests/test_mcel_workbench_spec_language.py"
          ],
          docHints: [
            "pretty_docs/mcel-lab-blueprint-studio.md"
          ],
          riskFamilies: [
            "save-overwrite",
            "export-file",
            "ai-network-assist",
            "history-restore",
            "raw-git-provider-action"
          ],
          annotationPolicy: annotationPolicy("document-editor"),
          exportPolicy: exportPolicy("document-editor")
        },
        "mcel-lab": {
          appId: "mcel-lab",
          aliases: [],
          label: "MCEL Lab",
          route: "/applications/mcel-lab",
          rootSelector: "#mcel-lab-app",
          mountPolicy: mountPolicy("mcel-lab", "#mcel-lab-app", "/applications/mcel-lab", {selfMountRecursionGuard: true}),
          inspectionPolicy: inspectionPolicy("mcel-lab", {
            hostSurfaceSelectors: ["#mcel-lab-app [data-mcel-layout-zone='primary']"]
          }),
          blueprintElementId: "element.workbench.specification",
          dominantObject: "AppBlueprint",
          purpose: "Self-hosting app blueprint inspector and repair planner that can inspect itself through the same generic aspects used for product apps.",
          aspects: buildGenericAspects(),
          aspectIds: GENERIC_ASPECT_IDS,
          genericInspectionElements: GENERIC_INSPECTION_ELEMENTS,
          genericRefactorElements: GENERIC_REFACTOR_ELEMENTS,
          layoutZones: GENERIC_LAYOUT_ZONES,
          detailGroups: BLUEPRINT_DETAIL_GROUPS,
          layoutBinding: {
            root: "#mcel-lab-app",
            zones: {
              identity: "[data-mcel-zone='identity'], [data-mcel-layout-zone='identity']",
              navigation: "[data-mcel-zone='navigation'], [data-mcel-layout-zone='navigation']",
              primary: "[data-mcel-zone='primary'], [data-mcel-layout-zone='primary']",
              inspector: "[data-mcel-zone='inspector'], [data-mcel-layout-zone='inspector']",
              evidence: "[data-mcel-zone='evidence'], [data-mcel-layout-zone='evidence']",
              actions: "[data-mcel-zone='actions'], [data-mcel-layout-zone='actions']",
              status: "[data-mcel-zone='status'], [data-mcel-layout-zone='status']",
              advanced: "[data-mcel-zone='advanced'], [data-mcel-layout-zone='advanced']"
            }
          },
          sourceHints: [
            "main_computer/web/applications/apps/mcel-lab.html",
            "main_computer/web/applications/scripts/mcel-lab.js",
            "main_computer/web/applications/styles/mcel-lab.css",
            "main_computer/web/applications/scripts/mcel-app-blueprints-core.js",
            "main_computer/web/applications/scripts/mcel-specimen-planner.js",
            "main_computer/web/applications/scripts/mcel-elements-core.js",
            "main_computer/web/applications/scripts/mcel-toolkit-core.js",
            "pretty_docs/mcel-lab-blueprint-studio.md"
          ],
          testHints: [
            "tests/test_mcel_lab_app.py",
            "tests/test_mcel_lab_blueprint_studio_documentation.py",
            "tests/test_mcel_workbench_spec_language.py",
            "tests/test_mcel_app_blueprint_contracts.py"
          ],
          docHints: [
            "pretty_docs/mcel-lab-blueprint-studio.md",
            "README.md"
          ],
          riskFamilies: [
            "self-recursion",
            "specimen-boundary",
            "live-self-overwrite"
          ],
          annotationPolicy: annotationPolicy("mcel-lab"),
          exportPolicy: exportPolicy("mcel-lab"),
          selfHostingPolicy: {
            mayEditBlueprintDraft: true,
            mayPreviewRedesign: true,
            mayGenerateReplacementFilePatch: true,
            mustNotRewriteLiveImplementation: true,
            mustAlsoInspectNonLabTargets: true
          }
        }
      });

      function normalizeAppId(appId) {
        const normalized = String(appId || "").trim().toLowerCase();
        if (normalized === "document") return "document-editor";
        return normalized;
      }

      function listInspectableAppBlueprints() {
        return buildInspectableBlueprints().map(clone);
      }

      function inspectableBlueprintFor(appId) {
        const normalized = normalizeAppId(appId);
        const direct = buildInspectableBlueprints().find((blueprint) => blueprint.appId === normalized);
        if (direct) return clone(direct);
        const aliasMatch = buildInspectableBlueprints().find((blueprint) => (blueprint.aliases || []).includes(normalized));
        return aliasMatch ? clone(aliasMatch) : null;
      }

      function genericAspectIds() {
        return Array.from(GENERIC_ASPECT_IDS);
      }

      function genericLayoutZones() {
        return Array.from(GENERIC_LAYOUT_ZONES);
      }

      function genericDetailGroups() {
        return clone(BLUEPRINT_DETAIL_GROUPS);
      }

      function requiredDependencyChecks() {
        return Array.from(REQUIRED_DEPENDENCY_CHECKS);
      }

      function requiredExportPacketFiles() {
        return Array.from(REQUIRED_EXPORT_PACKET_FILES);
      }

      function requiredMountCaptureFields() {
        return Array.from(REQUIRED_MOUNT_CAPTURE_FIELDS);
      }

      function requiredInspectionFields() {
        return Array.from(REQUIRED_INSPECTION_FIELDS);
      }

      global.McelAppBlueprintsCore = {
        BLUEPRINTS_CORE_VERSION,
        GENERIC_ASPECT_IDS: Array.from(GENERIC_ASPECT_IDS),
        GENERIC_LAYOUT_ZONES: Array.from(GENERIC_LAYOUT_ZONES),
        GENERIC_INSPECTION_ELEMENTS: Array.from(GENERIC_INSPECTION_ELEMENTS),
        GENERIC_REFACTOR_ELEMENTS: Array.from(GENERIC_REFACTOR_ELEMENTS),
        BLUEPRINT_DETAIL_GROUPS: clone(BLUEPRINT_DETAIL_GROUPS),
        REQUIRED_DEPENDENCY_CHECKS: Array.from(REQUIRED_DEPENDENCY_CHECKS),
        REQUIRED_EXPORT_PACKET_FILES: Array.from(REQUIRED_EXPORT_PACKET_FILES),
        REQUIRED_MOUNT_CAPTURE_FIELDS: Array.from(REQUIRED_MOUNT_CAPTURE_FIELDS),
        REQUIRED_INSPECTION_FIELDS: Array.from(REQUIRED_INSPECTION_FIELDS),
        BLUEPRINTS: clone(BLUEPRINTS),
        listInspectableAppBlueprints,
        inspectableBlueprintFor,
        formPrimitiveGroups,
        formPrimitiveKindCounts,
        genericAspectIds,
        genericLayoutZones,
        genericDetailGroups,
        requiredDependencyChecks,
        requiredExportPacketFiles,
        requiredMountCaptureFields,
        requiredInspectionFields
      };
    })(window);
