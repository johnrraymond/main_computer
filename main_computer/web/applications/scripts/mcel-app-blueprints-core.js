    (function (global) {
      "use strict";

      const BLUEPRINTS_CORE_VERSION = "0.2.0";
      const GENERIC_ASPECT_IDS = Object.freeze([
        "overview",
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

      function inspectionPolicy(appId) {
        return Object.freeze({
          appId,
          mode: "contained-clone-point-inspection",
          enabled: true,
          patternId: "pattern.point-and-annotate",
          selectedElementId: "element.refactor.element-annotation",
          preventDefaultActions: true,
          sourceMutationAllowed: false,
          hoverHighlight: true,
          selectionHighlight: true,
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

      const BLUEPRINTS = freezeDeep({
        "document-editor": {
          appId: "document-editor",
          aliases: ["document"],
          label: "Document Editor",
          route: "/applications/document",
          rootSelector: "#document-app",
          mountPolicy: mountPolicy("document-editor", "#document-app", "/applications/document"),
          inspectionPolicy: inspectionPolicy("document-editor"),
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
          inspectionPolicy: inspectionPolicy("mcel-lab"),
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
        return Object.values(BLUEPRINTS).map(clone);
      }

      function inspectableBlueprintFor(appId) {
        const normalized = normalizeAppId(appId);
        const direct = BLUEPRINTS[normalized];
        if (direct) return clone(direct);
        const aliasMatch = Object.values(BLUEPRINTS).find((blueprint) => (blueprint.aliases || []).includes(normalized));
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
        genericAspectIds,
        genericLayoutZones,
        genericDetailGroups,
        requiredDependencyChecks,
        requiredExportPacketFiles,
        requiredMountCaptureFields,
        requiredInspectionFields
      };
    })(window);
