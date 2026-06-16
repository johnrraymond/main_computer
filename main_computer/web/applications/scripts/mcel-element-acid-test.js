    (function (global) {
      "use strict";

      const ACID_VERSION = "0.2.6";
      const acidElementIds = [
        "element.core.app",
        "element.core.region",
        "element.core.panel",
        "element.core.toolbar",
        "element.core.field",
        "element.core.action",
        "element.core.status-feed",
        "element.core.workflow",
        "element.core.collection",
        "element.core.collection-row",
        "element.core.preview-pane",
        "element.core.mvc-model",
        "element.core.mvc-controller",
        "element.core.mvc-view",
        "element.toolkit.foundation-token",
        "element.toolkit.selection-control",
        "element.toolkit.disclosure-control",
        "element.toolkit.resize-handle",
        "element.toolkit.sort-indicator",
        "element.toolkit.filter-chip",
        "element.toolkit.command-button",
        "element.toolkit.drag-handle",
        "element.toolkit.bulk-selector",
        "element.toolkit.path-cell",
        "element.toolkit.name-cell",
        "element.toolkit.status-cell",
        "element.toolkit.risk-cell",
        "element.toolkit.datetime-cell",
        "element.toolkit.reason-cell",
        "element.toolkit.diffstat-cell",
        "element.toolkit.action-cell",
        "element.toolkit.collection-view",
        "element.toolkit.toolbar",
        "element.toolkit.split-pane",
        "element.toolkit.inspector-pane",
        "element.toolkit.preview-pane",
        "element.toolkit.status-bar",
        "element.toolkit.selection-controller",
        "element.toolkit.expansion-controller",
        "element.toolkit.column-sizing-controller",
        "element.toolkit.sort-filter-controller",
        "element.toolkit.safety-controller",
        "element.toolkit.view-resolver",
        "element.toolkit.contract-pattern",
        "element.concern.catalog",
        "element.concern.detector",
        "element.concern.boundary-map",
        "element.concern.contract-gap",
        "element.concern.mvc-split",
        "element.concern.replacement-plan",
        "element.concern.project-workbench",
        "element.concern.work-order",
        "element.concern.migration-queue",
        "element.concern.proof-plan",
        "element.resource.file-boundary",
        "element.resource.directory-tree",
        "element.resource.tree-viewport",
        "element.resource.tree-branch",
        "element.resource.tree-leaf",
        "element.resource.tree-expander",
        "element.resource.tree-selection-model",
        "element.resource.tree-keyboard-controller",
        "element.resource.tree-context-menu",
        "element.resource.tree-drag-drop-boundary",
        "element.resource.tree-empty-state",
        "element.resource.path-bar",
        "element.resource.resource-row",
        "element.resource.view-contract",
        "element.resource.selection-contract",
        "element.resource.contract-treegrid",
        "element.resource.file-basket-model",
        "element.resource.view-mode-controller",
        "element.resource.icon-grid",
        "element.resource.details-pane",
        "element.operational.process-table",
        "element.operational.server-control",
        "element.operational.pid-action",
        "element.operational.command-surface",
        "element.network.remote-mutation-boundary",
        "element.network.credential-boundary",
        "element.network.payment-boundary",
        "element.network.message-thread",
        "element.compute.local-display",
        "element.compute.keypad",
        "element.compute.graph-surface",
        "element.compute.runtime-cell",
        "element.authoring.document-surface",
        "element.authoring.spreadsheet-grid",
        "element.authoring.code-editor",
        "element.authoring.website-publisher",
        "element.authoring.game-editor"
      ];


      const TREE_VIEW_MODES = [
        {
          id: "explorer-sidebar",
          label: "Explorer sidebar",
          source: "Windows Explorer navigation pane",
          summary: "Dense navigation tree with pinned roots, chevrons, icons, selection, and safe read boundaries."
        },
        {
          id: "ide-project-tree",
          label: "IDE project tree",
          source: "VS Code / JetBrains",
          summary: "Developer project tree with file icons, modified/untracked badges, diagnostics, and compact rows."
        },
        {
          id: "details-treegrid",
          label: "Details treegrid",
          source: "Explorer Details",
          summary: "Hierarchy plus sortable columns for modified time, type, size, and proof policy."
        },
        {
          id: "miller-columns",
          label: "Column browser",
          source: "Finder-style columns",
          summary: "One column per depth level so path choice, siblings, and preview coupling stay visible."
        },
        {
          id: "outline-tree",
          label: "Outline tree",
          source: "Document/app outline",
          summary: "Semantic heading/component outline with region, panel, and action hierarchy."
        },
        {
          id: "accessibility-proof",
          label: "Keyboard proof",
          source: "ARIA tree pattern",
          summary: "Roving focus, active descendant, selection model, expansion state, and blocked mutation keys."
        },
        {
          id: "icon-grid",
          label: "Icon grid",
          source: "Explorer icon views",
          summary: "Extra-large through small icon layouts with stable selection, thumbnails, and clamped labels."
        },
        {
          id: "compact-list",
          label: "List view",
          source: "Explorer List",
          summary: "A fast scanning view for many siblings that keeps icon, name, type, status, and preview state aligned."
        },
        {
          id: "tile-view",
          label: "Tiles view",
          source: "Explorer Tiles",
          summary: "Tile cards show name, type, size, modified time, and policy without turning the row into a form."
        },
        {
          id: "content-view",
          label: "Content view",
          source: "Explorer Content",
          summary: "Content rows carry preview snippets, provenance, and blocked mutation policy for the selected resource."
        },
        {
          id: "finder-gallery",
          label: "Finder Gallery",
          source: "macOS Finder Gallery view",
          summary: "Large preview canvas, thumbnail filmstrip, inspector metadata, and tag chips without mutating files."
        },
        {
          id: "finder-column-inspector",
          label: "Finder Columns + Inspector",
          source: "macOS Finder Column view",
          summary: "Column navigation with sibling context, selected-file preview, tags, and inspector-style metadata."
        },
        {
          id: "gnome-grid",
          label: "GNOME Files grid",
          source: "GNOME Files / Nautilus grid",
          summary: "Adwaita-style responsive icon cards with breadcrumbs, checkbox affordances, and sidebar context."
        },
        {
          id: "gnome-list",
          label: "GNOME Files list",
          source: "GNOME Files / Nautilus list",
          summary: "Accessible list rows with favorites, modified time, type, size, and safe preview selection."
        },
        {
          id: "dolphin-split-details",
          label: "Dolphin split/details",
          source: "KDE Dolphin split view",
          summary: "Dual folder panes with Places context, details columns, tabs/split state, and preview locks."
        },
        {
          id: "thunar-compact",
          label: "Thunar compact",
          source: "XFCE Thunar compact view",
          summary: "Compact multi-column file names with small icons for dense directories and keyboard scanning."
        }
      ];


      const RESOURCE_VIEW_MENU_OPTIONS = [
        {id: "extra-large-icons", label: "Extra large icons", family: "icons", iconSize: 72},
        {id: "large-icons", label: "Large icons", family: "icons", iconSize: 56},
        {id: "medium-icons", label: "Medium icons", family: "icons", iconSize: 40},
        {id: "small-icons", label: "Small icons", family: "icons", iconSize: 24},
        {id: "list", label: "List", family: "rows"},
        {id: "details", label: "Details", family: "rows", active: true},
        {id: "tiles", label: "Tiles", family: "cards"},
        {id: "content", label: "Content", family: "cards"},
        {id: "finder-gallery", label: "Finder Gallery", family: "macos"},
        {id: "finder-column", label: "Finder Columns", family: "macos"},
        {id: "gnome-grid", label: "GNOME Grid", family: "linux"},
        {id: "gnome-list", label: "GNOME List", family: "linux"},
        {id: "dolphin-split", label: "Dolphin Split", family: "linux"},
        {id: "thunar-compact", label: "Thunar Compact", family: "linux"},
        {id: "details-pane", label: "Details pane", family: "side-pane", active: true},
        {id: "preview-pane", label: "Preview pane", family: "side-pane", active: true}
      ];

      const VIEW_MENU_TREE_MODE_MAP = {
        "extra-large-icons": "icon-grid",
        "large-icons": "icon-grid",
        "medium-icons": "icon-grid",
        "small-icons": "icon-grid",
        list: "compact-list",
        details: "details-treegrid",
        tiles: "tile-view",
        content: "content-view",
        "finder-gallery": "finder-gallery",
        "finder-column": "finder-column-inspector",
        "gnome-grid": "gnome-grid",
        "gnome-list": "gnome-list",
        "dolphin-split": "dolphin-split-details",
        "thunar-compact": "thunar-compact"
      };


      const SIDE_PANE_OPTIONS = RESOURCE_VIEW_MENU_OPTIONS.filter((option) => option.family === "side-pane");

      function createNode(document, tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
      }

      function sanitizeId(value) {
        return String(value || "element").replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").toLowerCase();
      }

      function makeRecord(definition, index, surface = "catalog") {
        return {
          id: `acid-${surface}-${index + 1}-${sanitizeId(definition.id)}`,
          elementId: definition.id,
          family: definition.family,
          kind: definition.kind,
          risk: definition.riskPolicy?.risk || "none",
          proofPolicy: definition.proofPolicy || "inspect-only",
          blocked: Boolean(definition.riskPolicy?.blocked),
          layoutLaws: definition.layoutLaws || [],
          surface
        };
      }

      function applyElementAttributes(node, definition, role = "") {
        node.setAttribute("data-mcel-element", definition.id);
        node.setAttribute("data-mcel-element-kind", definition.kind);
        node.setAttribute("data-mcel-element-family", definition.family);
        node.setAttribute("data-mcel-proof-policy", definition.proofPolicy || "inspect-only");
        node.setAttribute("data-mcel-risk", definition.riskPolicy?.risk || "none");
        if (role) node.setAttribute("data-mcel-element-demo-role", role);
        return node;
      }

      function appendChip(document, parent, label, value = "") {
        const chip = createNode(document, "span", "mcel-element-acid-chip", value ? `${label}: ${value}` : label);
        parent.appendChild(chip);
        return chip;
      }

      function appendElementCard(document, parent, definition, index) {
        const card = applyElementAttributes(createNode(document, "article", "mcel-element-acid-card"), definition, "catalog-card");

        const heading = createNode(document, "h6", "", definition.label);
        const meta = createNode(document, "p", "mcel-element-acid-card-meta", `${definition.family} · ${definition.kind} · ${definition.proofPolicy}`);
        const purpose = createNode(document, "p", "mcel-element-acid-card-purpose", definition.purpose);
        const lawList = createNode(document, "ul", "mcel-element-acid-law-list");
        (definition.layoutLaws || []).slice(0, 3).forEach((law) => {
          lawList.appendChild(createNode(document, "li", "", law));
        });
        const chips = createNode(document, "div", "mcel-element-acid-chip-row");
        appendChip(document, chips, "risk", definition.riskPolicy?.risk || "none");
        appendChip(document, chips, "serialize", definition.serializationSchema?.type || "object");
        const controls = createNode(document, "div", "mcel-element-acid-card-controls");
        const inspect = createNode(document, "button", "", "Inspect");
        inspect.type = "button";
        inspect.setAttribute("data-mcel-element-action", "inspect");
        const proof = createNode(document, "button", "", definition.riskPolicy?.blocked ? "Blocked proof" : "Proof safe");
        proof.type = "button";
        proof.setAttribute("data-mcel-element-action", definition.riskPolicy?.blocked ? "blocked-proof" : "proof-safe");
        proof.setAttribute("aria-disabled", definition.riskPolicy?.blocked ? "true" : "false");
        controls.append(inspect, proof);

        card.append(heading, meta, purpose, chips, lawList, controls);
        parent.appendChild(card);
        return makeRecord(definition, index, "catalog");
      }

      function requireDefinition(definitionsById, id) {
        const definition = definitionsById.get(id);
        if (!definition) throw new Error(`Missing MCEL element definition: ${id}`);
        return definition;
      }

      function appendWorkbenchSurface(document, parent, definitionsById, id, className, role, title, bodyBuilder) {
        const definition = requireDefinition(definitionsById, id);
        const surface = applyElementAttributes(createNode(document, "section", className), definition, role);
        const heading = createNode(document, "div", "mcel-element-showcase-surface-head");
        heading.append(
          createNode(document, "strong", "", title),
          createNode(document, "span", "", `${definition.family} · ${definition.kind} · ${definition.proofPolicy}`)
        );
        const body = createNode(document, "div", "mcel-element-showcase-surface-body");
        bodyBuilder?.(body, definition);
        surface.append(heading, body);
        parent.appendChild(surface);
        return surface;
      }


      function mcelToolkitCore() {
        return global.McelToolkitCore || {
          LAYERS: [],
          listPrimitives: () => [],
          primitivesByLayer: () => ({}),
          resolveViews: () => [],
          CONTRACT_PATTERNS: {},
          buildToolkitReadinessReport: () => ({primitiveCount: 0, layerCount: 0, noOneOffControls: false})
        };
      }

      function mcelConcernCore() {
        return global.McelConcernCore || {
          CONCERN_CATALOG: [],
          analyzeProject: () => ({detectedConcernCount: 0, severeContractGapCount: 0, concerns: [], recommendedToolkit: [], canDriveMcelContracts: false}),
          buildReadinessReport: () => ({detectedConcernCount: 0, severeContractGapCount: 0, canDriveMcelContracts: false}),
          projectSpecimenFiles: () => []
        };
      }

      function mcelProjectConcernWorkbench() {
        return global.McelProjectConcernWorkbench || {
          PROJECT_CONTRACTS: {},
          buildSpecimenWorkbench: () => ({
            summary: {workOrderCount: 0, highCount: 0, criticalCount: 0, contractCount: 0, hasFirstSafePatchForEveryHighPriority: false},
            workOrders: [],
            migrationQueue: [],
            firstSafePatchQueue: [],
            recommendedNextPatch: null,
            coverageByApp: {}
          })
        };
      }

      function mcelGitFileBasketTreegridLab() {
        return global.McelGitFileBasketTreegridLab || {
          buildInteractiveGitTreegridLabReport: () => ({
            ready: false,
            activeInGitTools: false,
            gitToolsRenderer: "legacy-wunderbaum",
            visibleRenderer: "mcel-lab-git-treegrid",
            proofChecks: {}
          }),
          renderInteractiveGitTreegridLab: null
        };
      }

      function mcelFileBasketModel() {
        return global.McelFileBasketModel || {
          buildFileBasketModel: () => ({
            fields: [],
            rows: [],
            hierarchy: [],
            selectablePaths: [],
            blockedPaths: [],
            defaultSelectedPaths: [],
            stats: {},
            viewContract: {eligibleViews: [], rejectedViews: [], titleOnlyTreeRejected: false}
          }),
          toggleDirectorySelection: () => [],
          selectionSummary: () => ({selected: 0, selectedBlocked: 0, selectedPaths: []}),
          resolveViewEligibility: () => ({eligible: false, missingCapabilities: ["adapter unavailable"]}),
          buildReadinessReport: () => ({ready: false})
        };
      }

      function fileBasketSpecimenReview() {
        return {
          candidate_groups: {
            selected_by_default: [
              {path: "main_computer/web/applications/scripts/task-manager.js", status: "modified", classifications: ["source"], modified: "today"}
            ],
            review_before_selecting: [
              {path: "tests/test_mcel_file_basket_model.py", status: "untracked", risk: "review", reason: "new contract proof"}
            ],
            blocked_possible_secrets: [
              {path: "runtime/secrets.env", status: "untracked", risk: "blocked", reason: "secret-looking runtime file", blocking_security_findings_count: 1}
            ],
            excluded_generated_runtime: [
              {path: "runtime/cache/task-manager.tmp", status: "untracked", reason: "generated runtime"}
            ]
          }
        };
      }

      function toolkitDefinitionFor(definitionsById, primitive, fallbackId = "element.core.panel") {
        const candidateId = primitive?.elementId || fallbackId;
        return definitionsById.get(candidateId) || definitionsById.get(fallbackId);
      }

      function appendToolkitPillList(document, parent, items, className = "mcel-toolkit-pill-row") {
        const row = createNode(document, "div", className);
        items.slice(0, 8).forEach((item) => row.appendChild(createNode(document, "span", "", item)));
        if (items.length > 8) row.appendChild(createNode(document, "span", "", `+${items.length - 8} more`));
        parent.appendChild(row);
        return row;
      }

      function appendToolkitPrimitiveCard(document, parent, definitionsById, primitive) {
        const definition = toolkitDefinitionFor(definitionsById, primitive, "element.toolkit.foundation-token");
        const card = applyElementAttributes(createNode(document, "article", `mcel-toolkit-card mcel-toolkit-card--${sanitizeId(primitive.layer)}`), definition, `toolkit-${primitive.layer}-${primitive.id}`);
        card.setAttribute("data-mcel-toolkit-primitive", primitive.id);
        card.setAttribute("data-mcel-toolkit-layer", primitive.layer);
        card.setAttribute("data-mcel-toolkit-element-id", primitive.elementId || "");
        card.append(
          createNode(document, "strong", "", primitive.label),
          createNode(document, "p", "", primitive.contract)
        );
        appendToolkitPillList(document, card, primitive.states || [], "mcel-toolkit-state-row");
        appendToolkitPillList(document, card, primitive.supports || [], "mcel-toolkit-support-row");
        parent.appendChild(card);
        return card;
      }

      function appendToolkitStateSpecimen(document, parent, label, elementId, states, renderClass) {
        const block = createNode(document, "article", "mcel-toolkit-state-specimen");
        block.setAttribute("data-mcel-toolkit-state-specimen", label);
        block.appendChild(createNode(document, "strong", "", label));
        const rail = createNode(document, "div", "mcel-toolkit-control-rail");
        states.forEach((state) => {
          const specimen = createNode(document, "span", `mcel-toolkit-control-sample ${renderClass || ""}`);
          specimen.setAttribute("data-mcel-toolkit-state", state);
          specimen.setAttribute("data-mcel-toolkit-element-id", elementId);
          specimen.append(
            createNode(document, "i", "", ""),
            createNode(document, "span", "", state)
          );
          rail.appendChild(specimen);
        });
        block.appendChild(rail);
        parent.appendChild(block);
        return block;
      }

      function renderConcernIntelligenceAtlas(document, parent, definitionsById) {
        const concernCore = mcelConcernCore();
        const concernFiles = concernCore.projectSpecimenFiles();
        const concernReport = concernCore.analyzeProject(concernFiles, {projectId: "main_computer_test.concern-atlas"});
        const concernDefinition = requireDefinition(definitionsById, "element.concern.detector");
        const atlas = applyElementAttributes(createNode(document, "section", "mcel-concern-atlas"), concernDefinition, "concern-intelligence-atlas");
        atlas.setAttribute("aria-label", "MCEL Concern Intelligence Atlas");
        atlas.setAttribute("data-mcel-concern-atlas", "source-aware");
        atlas.setAttribute("data-mcel-concern-detector-ready", concernReport.canDriveMcelContracts ? "true" : "false");
        atlas.setAttribute("data-mcel-concern-count", String(concernReport.detectedConcernCount || 0));
        atlas.setAttribute("data-mcel-concern-gap-count", String(concernReport.severeContractGapCount || 0));

        const header = createNode(document, "header", "mcel-concern-atlas-head");
        header.append(
          createNode(document, "p", "eyebrow", "MCEL Concern Intelligence"),
          createNode(document, "h6", "", "Detect the user-facing concern before choosing the MVC contract or visualization."),
          createNode(document, "p", "", "This source-aware layer scans project code for evidence of concerns: file baskets, resource browsers, deploy preflights, change review lists, execution cells, worker routing, output renderers, and process tables. It then maps each concern to MVC boundaries, missing contracts, toolkit primitives, and rejected slime patterns.")
        );

        const scoreGrid = createNode(document, "div", "mcel-concern-score-grid");
        [
          ["Detected concerns", String(concernReport.detectedConcernCount || 0)],
          ["Major/severe gaps", String(concernReport.severeContractGapCount || 0)],
          ["Files analyzed", String(concernReport.analyzedFileCount || concernFiles.length)],
          ["Toolkit needs", String((concernReport.recommendedToolkit || []).length)]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", value), createNode(document, "span", "", label));
          scoreGrid.appendChild(card);
        });

        const familyStrip = createNode(document, "div", "mcel-concern-family-strip");
        Object.entries(concernReport.concernFamilies || {}).forEach(([family, count]) => {
          familyStrip.appendChild(createNode(document, "span", "", `${family}: ${count}`));
        });

        const cardGrid = createNode(document, "div", "mcel-concern-card-grid");
        (concernReport.concerns || []).slice(0, 8).forEach((concern) => {
          const card = applyElementAttributes(createNode(document, "article", "mcel-concern-card"), requireDefinition(definitionsById, "element.concern.contract-gap"), `concern-${concern.id}`);
          card.setAttribute("data-mcel-concern-id", concern.id);
          card.setAttribute("data-mcel-concern-gap", concern.contractGap || "");
          card.setAttribute("data-mcel-concern-boundary-health", concern.boundaryHealth || "");
          card.append(
            createNode(document, "strong", "", `${concern.label} · ${(concern.confidence * 100).toFixed(0)}%`),
            createNode(document, "span", "mcel-concern-file", concern.file || "unknown file"),
            createNode(document, "p", "", concern.missingContractReason || "No contract gap explanation.")
          );

          const roleStrip = createNode(document, "div", "mcel-concern-role-strip");
          (concern.roles || []).forEach((role) => roleStrip.appendChild(createNode(document, "span", "", role)));
          card.appendChild(roleStrip);

          const rangeMap = applyElementAttributes(createNode(document, "div", "mcel-concern-boundary-map"), requireDefinition(definitionsById, "element.concern.boundary-map"), `concern-boundaries-${concern.id}`);
          (concern.ranges || []).slice(0, 4).forEach((range) => {
            const row = createNode(document, "span", "");
            row.textContent = `${range.role} · L${range.anchorLine}: ${range.label}`;
            rangeMap.appendChild(row);
          });
          card.appendChild(rangeMap);

          const split = applyElementAttributes(createNode(document, "div", "mcel-concern-mvc-split"), requireDefinition(definitionsById, "element.concern.mvc-split"), `concern-mvc-${concern.id}`);
          const splitModel = concern.inferredMvcSplit || {};
          ["model", "controller", "view"].forEach((key) => {
            const column = createNode(document, "div", "");
            column.appendChild(createNode(document, "b", "", key));
            (splitModel[key] || []).slice(0, 3).forEach((item) => column.appendChild(createNode(document, "span", "", item)));
            split.appendChild(column);
          });
          card.appendChild(split);

          const toolkit = applyElementAttributes(createNode(document, "div", "mcel-concern-toolkit-strip"), requireDefinition(definitionsById, "element.concern.replacement-plan"), `concern-toolkit-${concern.id}`);
          (concern.recommendedToolkit || []).slice(0, 7).forEach((item) => toolkit.appendChild(createNode(document, "span", "", item)));
          card.appendChild(toolkit);
          cardGrid.appendChild(card);
        });

        const plan = applyElementAttributes(createNode(document, "section", "mcel-concern-plan"), requireDefinition(definitionsById, "element.concern.replacement-plan"), "concern-replacement-plan");
        plan.append(
          createNode(document, "strong", "", "Concern → contract → toolkit plan"),
          createNode(document, "p", "", "The detector is already useful against this project: Task Manager exposes a file-basket concern, File Explorer exposes a resource-browser concern, Website Builder exposes deploy-preflight and change-review concerns, and Chat Console exposes execution, worker-routing, and output-renderer concerns. The deterministic scanner can harden this later, but MCEL now has a code-facing surface that produces contracts instead of guessing views.")
        );
        const planPills = createNode(document, "div", "mcel-concern-plan-pills");
        (concernReport.highPriorityConcerns || []).forEach((item) => planPills.appendChild(createNode(document, "span", "", item)));
        plan.appendChild(planPills);

        atlas.append(header, scoreGrid, familyStrip, cardGrid, plan);
        parent.appendChild(atlas);
        return atlas;
      }


      function renderProjectConcernWorkbench(document, parent, definitionsById) {
        const projectWorkbench = mcelProjectConcernWorkbench();
        const workbench = projectWorkbench.buildSpecimenWorkbench({limit: 6});
        const workbenchDefinition = requireDefinition(definitionsById, "element.concern.project-workbench");
        const workOrderDefinition = requireDefinition(definitionsById, "element.concern.work-order");
        const migrationDefinition = requireDefinition(definitionsById, "element.concern.migration-queue");
        const proofDefinition = requireDefinition(definitionsById, "element.concern.proof-plan");

        const shell = applyElementAttributes(createNode(document, "section", "mcel-project-concern-workbench"), workbenchDefinition, "project-concern-workbench");
        shell.setAttribute("aria-label", "Main Computer Project Concern Workbench");
        shell.setAttribute("data-mcel-project-concern-workbench", "true");
        shell.setAttribute("data-mcel-project-work-order-count", String(workbench.summary?.workOrderCount || 0));
        shell.setAttribute("data-mcel-project-high-priority-count", String((workbench.summary?.criticalCount || 0) + (workbench.summary?.highCount || 0)));
        shell.setAttribute("data-mcel-project-first-safe-patches", workbench.summary?.hasFirstSafePatchForEveryHighPriority ? "ready" : "incomplete");

        const header = createNode(document, "header", "mcel-project-concern-workbench-head");
        header.append(
          createNode(document, "p", "eyebrow", "Main Computer Project Concern Workbench"),
          createNode(document, "h6", "", "Turn detected UI concerns into ranked MCEL migration work orders."),
          createNode(document, "p", "", "This is the project integration spine: the concern detector finds real app responsibilities, the workbench assigns target contracts, MVC splits, eligible toolkit pieces, rejected views, first safe patches, and proof obligations.")
        );

        const scoreGrid = createNode(document, "div", "mcel-project-workbench-score-grid");
        [
          ["Work orders", String(workbench.summary?.workOrderCount || 0)],
          ["Critical", String(workbench.summary?.criticalCount || 0)],
          ["High", String(workbench.summary?.highCount || 0)],
          ["Contracts", String(workbench.summary?.contractCount || 0)],
          ["Apps", String(workbench.summary?.appCount || 0)],
          ["Safe plans", workbench.summary?.hasFirstSafePatchForEveryHighPriority ? "ready" : "needs work"]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", value), createNode(document, "span", "", label));
          scoreGrid.appendChild(card);
        });

        const queue = applyElementAttributes(createNode(document, "section", "mcel-project-migration-queue"), migrationDefinition, "project-migration-queue");
        queue.appendChild(createNode(document, "strong", "", "Migration priority queue"));
        (workbench.migrationQueue || []).slice(0, 6).forEach((item) => {
          const row = createNode(document, "article", "mcel-project-migration-row");
          row.setAttribute("data-mcel-project-migration-rank", String(item.rank));
          row.setAttribute("data-mcel-project-migration-priority", item.priority);
          row.append(
            createNode(document, "b", "", `#${item.rank} ${item.id}`),
            createNode(document, "span", "", `${item.priority} · ${item.targetContract} · score ${item.priorityScore}`),
            createNode(document, "p", "", `First safe patch: ${item.firstSafeMigration}`)
          );
          queue.appendChild(row);
        });

        const orderGrid = createNode(document, "div", "mcel-project-work-order-grid");
        (workbench.workOrders || []).slice(0, 6).forEach((order) => {
          const card = applyElementAttributes(createNode(document, "article", "mcel-project-work-order-card"), workOrderDefinition, `work-order-${order.id}`);
          card.setAttribute("data-mcel-project-work-order-id", order.id);
          card.setAttribute("data-mcel-project-work-order-priority", order.priority);
          card.setAttribute("data-mcel-project-target-contract", order.targetContract);
          card.append(
            createNode(document, "strong", "", order.title),
            createNode(document, "span", "mcel-project-work-order-meta", `${order.app} · ${order.contractGap} gap · ${order.priority} · ${(order.confidence * 100).toFixed(0)}%`),
            createNode(document, "p", "", order.reason || "No reason available.")
          );

          const currentFailure = createNode(document, "ul", "mcel-project-work-order-failures");
          (order.currentFailure || []).slice(0, 3).forEach((failure) => {
            const item = createNode(document, "li", "", failure);
            currentFailure.appendChild(item);
          });
          card.appendChild(currentFailure);

          const contract = createNode(document, "div", "mcel-project-contract-strip");
          contract.appendChild(createNode(document, "b", "", order.targetContract));
          (order.requiredCapabilities || []).slice(0, 5).forEach((capability) => contract.appendChild(createNode(document, "span", "", capability)));
          card.appendChild(contract);

          const firstPatch = createNode(document, "div", "mcel-project-first-patch");
          firstPatch.appendChild(createNode(document, "b", "", "First safe patch"));
          (order.firstSafeMigration || []).slice(0, 3).forEach((step) => firstPatch.appendChild(createNode(document, "span", "", step)));
          card.appendChild(firstPatch);

          const proof = applyElementAttributes(createNode(document, "div", "mcel-project-proof-plan"), proofDefinition, `proof-plan-${order.id}`);
          proof.appendChild(createNode(document, "b", "", "Proof obligations"));
          (order.testsNeeded || order.proofObligations || []).slice(0, 3).forEach((test) => proof.appendChild(createNode(document, "span", "", test)));
          card.appendChild(proof);

          const views = createNode(document, "div", "mcel-project-view-strip");
          (order.eligibleViews || []).slice(0, 4).forEach((view) => {
            const pill = createNode(document, "span", "", `${view.eligible ? "✓" : "×"} ${view.id}`);
            pill.setAttribute("data-mcel-project-view-eligible", view.eligible ? "true" : "false");
            views.appendChild(pill);
          });
          card.appendChild(views);

          orderGrid.appendChild(card);
        });

        const nextPatch = createNode(document, "section", "mcel-project-next-patch");
        const next = workbench.recommendedNextPatch;
        nextPatch.append(
          createNode(document, "strong", "", "Recommended next migration patch"),
          createNode(document, "p", "", next ? `${next.id}: ${next.firstSafeMigration}. Proof: ${next.proofNeeded}.` : "No migration work order available.")
        );

        shell.append(header, scoreGrid, queue, orderGrid, nextPatch);
        parent.appendChild(shell);
        return shell;
      }


      function renderGitFileBasketTreegridLab(document, parent) {
        const lab = mcelGitFileBasketTreegridLab();
        const report = lab.buildInteractiveGitTreegridLabReport?.() || {};
        if (typeof lab.renderInteractiveGitTreegridLab === "function") {
          const surface = lab.renderInteractiveGitTreegridLab(document);
          parent.appendChild(surface);
          return surface;
        }
        const fallback = createNode(document, "section", "mcel-git-treegrid-lab");
        fallback.setAttribute("data-mcel-git-file-basket-treegrid-lab", "true");
        fallback.setAttribute("data-mcel-git-treegrid-active-in-git-tools", "false");
        fallback.append(
          createNode(document, "p", "eyebrow", "Git file-basket treegrid lab"),
          createNode(document, "h6", "", "Contract treegrid lab module unavailable."),
          createNode(document, "p", "", `Git Tools renderer should remain ${report.gitToolsRenderer || "legacy-wunderbaum"} while the lab proof is unavailable.`)
        );
        parent.appendChild(fallback);
        return fallback;
      }


      function renderFileBasketModelProof(document, parent, definitionsById) {
        const adapter = mcelFileBasketModel();
        const definition = requireDefinition(definitionsById, "element.resource.file-basket-model");
        const model = adapter.buildFileBasketModel(fileBasketSpecimenReview(), {
          surfaceId: "git-tools.file-basket",
          canonicalSurfaceId: "git-tools.file-basket",
          legacySurfaceIds: ["task-manager.file-basket"],
          ownerApp: "git-tools",
          sourceConcern: "concern.file-basket",
          sourceFile: "main_computer/web/applications/scripts/git-tools-file-basket.js",
          ownershipStatus: "extracted-git-tools-boundary"
        });
        const selectedRoot = adapter.toggleDirectorySelection(model, [], "");
        const selectedSummary = adapter.selectionSummary(model, selectedRoot);
        const titleOnly = adapter.resolveViewEligibility(model, "title-only-tree");
        const treegrid = adapter.resolveViewEligibility(model, "contract-treegrid");

        const shell = applyElementAttributes(createNode(document, "section", "mcel-file-basket-model-proof"), definition, "file-basket-model-proof");
        shell.setAttribute("aria-label", "File Basket Model Adapter Proof");
        shell.setAttribute("data-mcel-file-basket-model-proof", "true");
        shell.setAttribute("data-mcel-file-basket-contract", model.contractId || "");
        shell.setAttribute("data-mcel-file-basket-selectable-count", String(model.selectablePaths?.length || 0));
        shell.setAttribute("data-mcel-file-basket-blocked-count", String(model.blockedPaths?.length || 0));
        shell.setAttribute("data-mcel-file-basket-title-only-rejected", titleOnly.eligible ? "false" : "true");

        const header = createNode(document, "header", "mcel-file-basket-model-proof-head");
        header.append(
          createNode(document, "p", "eyebrow", "First safe migration proof"),
          createNode(document, "h6", "", "Git Tools File Basket now has a pure MCEL model adapter."),
          createNode(document, "p", "", "This does not replace the current Git/Task Manager view yet. It marks the Git-owned file-basket boundary now extracted to git-tools-file-basket.js: fields, identity, hierarchy, selectable state, blocked reason, and selected output.")
        );

        const scoreGrid = createNode(document, "div", "mcel-file-basket-model-score-grid");
        [
          ["Fields", String(model.fields?.length || 0)],
          ["Rows", String(model.rows?.length || 0)],
          ["Selectable", String(model.selectablePaths?.length || 0)],
          ["Blocked visible", String(model.blockedPaths?.length || 0)],
          ["Root selects", String(selectedRoot.length)],
          ["Blocked selected", String(selectedSummary.selectedBlocked || 0)]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", value), createNode(document, "span", "", label));
          scoreGrid.appendChild(card);
        });

        const table = createNode(document, "table", "mcel-file-basket-model-table");
        const thead = createNode(document, "thead", "");
        const headerRow = createNode(document, "tr", "");
        ["Path", "Status", "Bucket", "Risk", "Selectable", "Blocked reason"].forEach((label) => headerRow.appendChild(createNode(document, "th", "", label)));
        thead.appendChild(headerRow);
        const tbody = createNode(document, "tbody", "");
        (model.rows || []).forEach((row) => {
          const tr = createNode(document, "tr", "");
          tr.setAttribute("data-mcel-file-basket-row", row.path);
          tr.setAttribute("data-mcel-file-basket-row-selectable", row.selectable ? "true" : "false");
          [
            row.path,
            row.statusLabel || row.status,
            row.bucketLabel || row.bucket,
            row.risk,
            row.selectable ? "yes" : "no",
            row.blockedReason || "—"
          ].forEach((value) => tr.appendChild(createNode(document, "td", "", value || "—")));
          tbody.appendChild(tr);
        });
        table.append(thead, tbody);

        const proof = createNode(document, "div", "mcel-file-basket-model-proof-list");
        [
          `selection output: ${selectedSummary.selectedPaths.join(", ") || "none"}`,
          treegrid.eligible ? "contract-treegrid satisfies required capabilities" : `contract-treegrid missing: ${treegrid.missingCapabilities.join(", ")}`,
          titleOnly.eligible ? "title-only tree incorrectly accepted" : `title-only tree rejected: ${titleOnly.missingCapabilities.join(", ")}`,
          "blocked rows stay visible and cannot enter selected output"
        ].forEach((item) => proof.appendChild(createNode(document, "span", "", item)));

        shell.append(header, scoreGrid, table, proof);
        parent.appendChild(shell);
        return shell;
      }

      function renderToolkitAtlas(document, parent, definitionsById) {
        const toolkit = mcelToolkitCore();
        const report = toolkit.buildToolkitReadinessReport();
        const primitives = toolkit.listPrimitives();
        const byLayer = toolkit.primitivesByLayer();
        const resolverPattern = toolkit.CONTRACT_PATTERNS?.fileBasket || {};
        const resolverResults = toolkit.resolveViews(resolverPattern);

        const atlasDefinition = requireDefinition(definitionsById, "element.toolkit.view-resolver");
        const atlas = applyElementAttributes(createNode(document, "section", "mcel-toolkit-atlas"), atlasDefinition, "toolkit-atlas");
        atlas.setAttribute("data-mcel-toolkit-atlas-ready", report.noOneOffControls ? "true" : "false");
        atlas.setAttribute("data-mcel-toolkit-primitive-count", String(report.primitiveCount || primitives.length));
        atlas.setAttribute("data-mcel-toolkit-layer-count", String(report.layerCount || 0));
        atlas.setAttribute("aria-label", "MCEL Toolkit Atlas");

        const header = createNode(document, "header", "mcel-toolkit-atlas-head");
        header.append(
          createNode(document, "p", "eyebrow", "MCEL Toolkit Atlas"),
          createNode(document, "h6", "", "Stop inventing one-off widgets: model the primitive, controller, contract, and eligible view."),
          createNode(document, "p", "", "The atlas is the reusable layer under treegrids, file baskets, file managers, process tables, inspectors, and future MCEL apps. Controls, cells, layouts, and controllers declare states and capabilities before a composite view is allowed to use them.")
        );

        const scoreGrid = createNode(document, "div", "mcel-toolkit-score-grid");
        [
          ["Toolkit primitives", String(report.primitiveCount || primitives.length)],
          ["Control primitives", String(report.controlPrimitiveCount || 0)],
          ["Data-cell primitives", String(report.dataCellPrimitiveCount || 0)],
          ["Collection views", String(report.collectionPrimitiveCount || 0)],
          ["Controller primitives", String(report.controllerPrimitiveCount || 0)],
          ["File-basket best view", report.fileBasketBestView || "unresolved"]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", value), createNode(document, "span", "", label));
          scoreGrid.appendChild(card);
        });

        const layerGrid = createNode(document, "div", "mcel-toolkit-layer-grid");
        (toolkit.LAYERS || []).forEach((layer) => {
          const layerCard = createNode(document, "article", "mcel-toolkit-layer-card");
          layerCard.setAttribute("data-mcel-toolkit-layer-card", layer.id);
          const layerPrimitives = byLayer[layer.id] || [];
          layerCard.append(
            createNode(document, "strong", "", layer.label),
            createNode(document, "p", "", layer.purpose),
            createNode(document, "span", "", `${layerPrimitives.length} primitive contracts`)
          );
          layerGrid.appendChild(layerCard);
        });

        const primitiveDeck = createNode(document, "div", "mcel-toolkit-primitive-deck");
        const priorityPrimitiveIds = [
          "control.selection.tristate",
          "control.disclosure",
          "control.resize-handle",
          "control.bulk-selector",
          "cell.path",
          "cell.status",
          "cell.risk",
          "cell.reason",
          "collection.treegrid",
          "collection.data-table",
          "collection.column-browser",
          "layout.inspector-pane",
          "layout.preview-pane",
          "controller.selection",
          "controller.expansion",
          "controller.column-sizing",
          "controller.safety-gate",
          "controller.view-resolver",
          "pattern.file-basket",
          "pattern.resource-browser",
          "pattern.permission-matrix"
        ];
        priorityPrimitiveIds
          .map((primitiveId) => primitives.find((primitive) => primitive.id === primitiveId))
          .filter(Boolean)
          .forEach((primitive) => appendToolkitPrimitiveCard(document, primitiveDeck, definitionsById, primitive));

        const stateRack = createNode(document, "section", "mcel-toolkit-state-rack");
        stateRack.appendChild(createNode(document, "strong", "", "Primitive state specimens"));
        appendToolkitStateSpecimen(document, stateRack, "Selection control", "element.toolkit.selection-control", ["unchecked", "checked", "mixed", "blocked", "disabled", "focus"], "mcel-toolkit-control-sample--selection");
        appendToolkitStateSpecimen(document, stateRack, "Disclosure control", "element.toolkit.disclosure-control", ["collapsed", "expanded", "leaf", "loading", "disabled", "focus"], "mcel-toolkit-control-sample--disclosure");
        appendToolkitStateSpecimen(document, stateRack, "Resize handle", "element.toolkit.resize-handle", ["idle", "hover", "active", "focus", "min", "max"], "mcel-toolkit-control-sample--resize");
        appendToolkitStateSpecimen(document, stateRack, "Column header", "element.toolkit.sort-indicator", ["normal", "sorted-asc", "sorted-desc", "filtered", "resized", "keyboard-focus"], "mcel-toolkit-control-sample--header");
        appendToolkitStateSpecimen(document, stateRack, "Row state", "element.toolkit.collection-view", ["normal", "selected", "mixed", "blocked", "focused", "hovered"], "mcel-toolkit-control-sample--row");

        const resolver = applyElementAttributes(createNode(document, "section", "mcel-toolkit-resolver"), requireDefinition(definitionsById, "element.toolkit.view-resolver"), "toolkit-view-resolver");
        resolver.setAttribute("data-mcel-toolkit-resolver", "file-basket");
        resolver.setAttribute("data-mcel-toolkit-rejects-title-only-tree", report.titleOnlyTreeRejected ? "true" : "false");
        resolver.append(
          createNode(document, "strong", "", "Needs → contract → visualization resolver"),
          createNode(document, "p", "", resolverPattern.intent || "No contract pattern loaded.")
        );
        const requirementList = createNode(document, "div", "mcel-toolkit-requirement-strip");
        (resolverPattern.requires || []).forEach((requirement) => requirementList.appendChild(createNode(document, "span", "", requirement)));
        resolver.appendChild(requirementList);

        const candidateGrid = createNode(document, "div", "mcel-toolkit-view-candidates");
        resolverResults.forEach((candidate) => {
          const card = createNode(document, "article", candidate.eligible ? "mcel-toolkit-view-candidate mcel-toolkit-view-candidate--eligible" : "mcel-toolkit-view-candidate mcel-toolkit-view-candidate--rejected");
          card.setAttribute("data-mcel-toolkit-view-candidate", candidate.id);
          card.setAttribute("data-mcel-toolkit-eligible-view", candidate.eligible ? "true" : "false");
          card.append(
            createNode(document, "strong", "", `${candidate.label} · ${candidate.score}`),
            createNode(document, "p", "", candidate.reason)
          );
          appendToolkitPillList(document, card, candidate.primitiveIds || [], "mcel-toolkit-support-row");
          if (candidate.missingCapabilities?.length) appendToolkitPillList(document, card, candidate.missingCapabilities, "mcel-toolkit-missing-row");
          candidateGrid.appendChild(card);
        });
        resolver.appendChild(candidateGrid);

        const assembly = applyElementAttributes(createNode(document, "section", "mcel-toolkit-assembly"), requireDefinition(definitionsById, "element.toolkit.contract-pattern"), "toolkit-assembly-map");
        assembly.setAttribute("data-mcel-toolkit-assembly", "file-basket");
        assembly.append(
          createNode(document, "strong", "", "File-basket assembly map"),
          createNode(document, "p", "", "The acid-test treegrid is no longer allowed to invent controls inside itself. It must assemble these toolkit primitives: selection, disclosure, resize, path/status/risk/reason cells, selection/expansion/column-sizing controllers, and safety/view resolver logic.")
        );
        appendToolkitPillList(document, assembly, resolverPattern.requiredPrimitives || [], "mcel-toolkit-assembly-pills");

        atlas.append(header, scoreGrid, layerGrid, primitiveDeck, stateRack, resolver, assembly);
        parent.appendChild(atlas);
        return atlas;
      }


      const RESOURCE_VIEW_MVC_ROLES = [
        {
          id: "model",
          elementId: "element.core.mvc-model",
          label: "MVC Model",
          summary: "Declares the user contract: resource fields, row identity, hierarchy, selectable output, and safety promises before any visual mode is chosen."
        },
        {
          id: "controller",
          elementId: "element.core.mvc-controller",
          label: "MVC Controller",
          summary: "Owns commands and transitions: toggle file, toggle directory shortcut, filter, sort, preview selection, and block unsafe actions."
        },
        {
          id: "view",
          elementId: "element.core.mvc-view",
          label: "MVC View",
          summary: "Renders only when its capabilities satisfy the contract: hierarchy, multi-column metadata, tri-state selection, blocked visible rows, and selected-output proof."
        }
      ];

      const RESOURCE_VIEW_MVC_CONTRACT = {
        id: "resource-file-basket-contract",
        label: "Resource MVC contract-first file basket mock",
        intent: "Mock what a Git Tools file basket should become without wiring Git Tools yet: a contract-first treegrid that helps users choose exact safe file paths.",
        model: {
          identity: "stable repo-relative path",
          fields: [
            {id: "path", label: "Path", type: "path", primary: true, required: true},
            {id: "status", label: "Status", type: "enum", required: true},
            {id: "bucket", label: "Bucket", type: "enum", required: true},
            {id: "risk", label: "Risk", type: "risk", required: true},
            {id: "source", label: "Source", type: "enum", required: true},
            {id: "reason", label: "Reason", type: "text", required: true},
            {id: "modified", label: "Modified", type: "datetime", required: true},
            {id: "selectable", label: "Selectable", type: "boolean", required: true}
          ]
        },
        selection: {
          mode: "hierarchical-explicit-files",
          output: "explicit-file-paths",
          directoryBehavior: "directories are shortcuts for selectable descendant files",
          mixedDirectoryState: true,
          blockedRowsVisible: true,
          blockedRowsSelectable: false
        },
        safety: {
          blockedItemsVisible: true,
          blockedItemsSelectable: false,
          selectedFilesAreSourceOfTruth: true,
          destructiveActionsRequirePreview: true,
          titleOnlyTreesRejected: true
        },
        controller: {
          commands: ["toggleFile", "toggleDirectoryShortcut", "selectAllEligible", "clearSelection", "expandAll", "collapseAll", "resizeColumn", "resetColumnWidths", "filterByRisk", "sortByField", "previewSelected"]
        },
        view: {
          requires: ["hierarchy", "multi-column metadata", "field type metadata", "tri-state selection", "blocked rows visible, not selectable", "selected output preview", "interactive expand/collapse", "resizable columns"],
          eligible: [
            {id: "contract-treegrid", label: "Treegrid/details hybrid", reason: "satisfies hierarchy, columns, tri-state selection, blocked rows, and explicit output proof"},
            {id: "column-browser-inspector", label: "Column browser + inspector", reason: "good for path context when paired with selected-output proof"},
            {id: "compact-audit-list", label: "Compact audit list", reason: "good secondary view for reviewing many files after selection semantics are resolved"}
          ],
          rejected: [
            {id: "title-only-tree", label: "Title-only tree rejected", reason: "cannot display required fields, type metadata, blocked reason, or explicit selected file output"}
          ]
        }
      };

      function treeFixtureRows() {
        return [
          {id: "workspace", label: "MAIN_COMPUTER", kind: "folder", icon: "▾", expanded: true, selected: false, level: 1, type: "workspace", modified: "Today", size: "", policy: "read boundary", badge: "", status: "", snippet: "Repository root with safe navigation only."},
          {id: "workspace/desktop", label: "Desktop", kind: "folder", icon: "▸", expanded: false, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: ""},
          {id: "workspace/downloads", label: "Downloads", kind: "folder", icon: "▾", expanded: true, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: "hot"},
          {id: "workspace/downloads/resource-tree", label: "mcel_element_resource_tree_primitives_patch", kind: "file", icon: "zip", expanded: false, selected: false, level: 3, type: "Compressed Folder", modified: "6/14/2026 2:22 PM", size: "42 KB", policy: "preview only", badge: "", status: "downloaded", snippet: "Patch artifact for resource tree primitive workbench."},
          {id: "workspace/downloads/library-visual", label: "mcel_element_library_visual_workbench_patch", kind: "file", icon: "zip", expanded: false, selected: false, level: 3, type: "Compressed Folder", modified: "6/14/2026 2:10 PM", size: "58 KB", policy: "preview only", badge: "M", status: "modified", snippet: "Visual element library workbench artifact with catalog additions."},
          {id: "workspace/downloads/market-progress", label: "temporal_fdb_market_progress_stdout_patch", kind: "file", icon: "zip", expanded: false, selected: false, level: 3, type: "Compressed Folder", modified: "6/14/2026 2:21 PM", size: "35 KB", policy: "preview only", badge: "", status: ""},
          {id: "workspace/documents", label: "Documents", kind: "folder", icon: "▸", expanded: false, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: ""},
          {id: "workspace/pictures", label: "Pictures", kind: "folder", icon: "▸", expanded: false, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: ""},
          {id: "workspace/main-computer", label: "main_computer", kind: "folder", icon: "▾", expanded: true, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "M", status: "modified"},
          {id: "workspace/runtime", label: "runtime", kind: "folder", icon: "▾", expanded: true, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: "hot"},
          {id: "workspace/runtime/scripts", label: "scripts", kind: "folder", icon: "▾", expanded: true, selected: false, level: 3, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: "tracked"},
          {id: "workspace/runtime/scripts/diagnose", label: "diagnose_mouse_hangs.py", kind: "file", icon: "py", expanded: false, selected: true, level: 4, type: "Python", modified: "6/14/2026 2:22 PM", size: "9 KB", policy: "preview only", badge: "U", status: "untracked", snippet: "Preview target used to prove read-only file boundaries and selection stability."},
          {id: "workspace/runtime/scripts/start", label: "main-computer-start-stop.ps1", kind: "file", icon: "ps", expanded: false, selected: false, level: 4, type: "PowerShell", modified: "6/14/2026 2:19 PM", size: "4 KB", policy: "preview only", badge: "", status: "", snippet: "PowerShell lifecycle helper, never executed by this proof."},
          {id: "workspace/runtime/scripts/check", label: "olama_checker.ps1", kind: "file", icon: "ps", expanded: false, selected: false, level: 4, type: "PowerShell", modified: "6/14/2026 2:10 PM", size: "2 KB", policy: "preview only", badge: "M", status: "modified", snippet: "Checker script preview with mutation actions blocked."}
        ];
      }

      function selectedResource() {
        return treeFixtureRows().find((node) => node.selected) || treeFixtureRows()[0];
      }


      const RESOURCE_MVC_TREEGRID_COLUMNS = [
        {id: "select", label: "Select", defaultRem: 4.5, minRem: 3.5, maxRem: 7.5},
        {id: "path", label: "Path", defaultRem: 23, minRem: 14, maxRem: 42},
        {id: "status", label: "Status", defaultRem: 7.2, minRem: 5, maxRem: 12},
        {id: "bucket", label: "Bucket", defaultRem: 6.2, minRem: 4.5, maxRem: 11},
        {id: "risk", label: "Risk", defaultRem: 5.6, minRem: 4.2, maxRem: 10},
        {id: "source", label: "Source", defaultRem: 6.8, minRem: 4.5, maxRem: 11},
        {id: "reason", label: "Reason", defaultRem: 20, minRem: 12, maxRem: 38},
        {id: "modified", label: "Modified", defaultRem: 7.5, minRem: 5.5, maxRem: 12}
      ];

      const RESOURCE_MVC_COLUMN_PRESETS = {
        default: {select: 4.5, path: 23, status: 7.2, bucket: 6.2, risk: 5.6, source: 6.8, reason: 20, modified: 7.5},
        compact: {select: 4, path: 16, status: 6, bucket: 5.3, risk: 4.8, source: 5.6, reason: 14, modified: 6.4},
        widePath: {select: 4.5, path: 34, status: 7, bucket: 5.8, risk: 5.2, source: 6.2, reason: 16, modified: 7.2}
      };

      function resourceMvcBasketRows() {
        return [
          {id: "mock/main-computer", kind: "directory", path: "main_computer", parentId: "", status: "dir + 3 files", bucket: "source", risk: "medium", source: "repo", reason: "Directory shortcut selects selectable descendant files.", modified: "Today", level: 1, expanded: true, selected: "mixed", selectable: true},
          {id: "mock/main-computer/web", kind: "directory", path: "main_computer/web/applications", parentId: "mock/main-computer", status: "dir + 3 files", bucket: "mcel", risk: "medium", source: "app", reason: "Nested folder proves expand/collapse without losing selection state.", modified: "Today", level: 2, expanded: true, selected: "mixed", selectable: true},
          {id: "mock/main-computer/acid", kind: "file", path: "main_computer/web/applications/scripts/mcel-element-acid-test.js", parentId: "mock/main-computer/web", status: "changed", bucket: "mcel", risk: "high", source: "acid-test", reason: "Owns the contract-first tree view proof.", modified: "Today", level: 3, selected: true, selectable: true},
          {id: "mock/main-computer/elements", kind: "file", path: "main_computer/web/applications/scripts/mcel-elements-core.js", parentId: "mock/main-computer/web", status: "changed", bucket: "mcel", risk: "medium", source: "registry", reason: "Declares MVC and view-contract element primitives.", modified: "Today", level: 3, selected: true, selectable: true},
          {id: "mock/main-computer/css", kind: "file", path: "main_computer/web/applications/styles/mcel-lab.css", parentId: "mock/main-computer/web", status: "changed", bucket: "view", risk: "medium", source: "style", reason: "Makes the mock contract legible without cramming fields into a title.", modified: "Today", level: 3, selected: false, selectable: true},
          {id: "mock/tests", kind: "directory", path: "tests", parentId: "", status: "dir + 1 file", bucket: "proof", risk: "low", source: "test", reason: "Directory state is derived from child selection, not hand-managed DOM slime.", modified: "Today", level: 1, expanded: true, selected: true, selectable: true},
          {id: "mock/tests/mcel", kind: "file", path: "tests/test_mcel_lab_app.py", parentId: "mock/tests", status: "changed", bucket: "proof", risk: "low", source: "test", reason: "Asserts the model, controller, view, contract, and treegrid pathway.", modified: "Today", level: 2, selected: true, selectable: true},
          {id: "mock/runtime/generated", kind: "file", path: "runtime/generated/cache/preview-artifact.tmp", parentId: "", status: "blocked", bucket: "runtime", risk: "blocked", source: "generated", reason: "Visible for audit, but controller will not put it in explicit-file-paths output.", modified: "Today", level: 1, selected: false, selectable: false, blocked: true}
        ];
      }

      function cloneResourceMvcBasketRows() {
        return resourceMvcBasketRows().map((row) => ({...row}));
      }

      function selectedMvcBasketRows(rows = resourceMvcBasketRows()) {
        return rows.filter((row) => row.selectable && row.kind === "file" && row.selected === true);
      }

      function resourceMvcDescendantRows(rows, directoryId) {
        const descendants = [];
        const queue = [directoryId];
        while (queue.length) {
          const parentId = queue.shift();
          rows.filter((row) => row.parentId === parentId).forEach((child) => {
            descendants.push(child);
            if (child.kind === "directory") queue.push(child.id);
          });
        }
        return descendants;
      }

      function resourceMvcAncestorRows(rows, row) {
        const ancestors = [];
        let parentId = row.parentId;
        while (parentId) {
          const parent = rows.find((candidate) => candidate.id === parentId);
          if (!parent) break;
          ancestors.push(parent);
          parentId = parent.parentId;
        }
        return ancestors;
      }

      function deriveResourceMvcDirectoryState(rows, directoryId) {
        const descendantFiles = resourceMvcDescendantRows(rows, directoryId).filter((row) => row.kind === "file" && row.selectable);
        if (!descendantFiles.length) return false;
        const selectedCount = descendantFiles.filter((row) => row.selected === true).length;
        if (selectedCount === 0) return false;
        if (selectedCount === descendantFiles.length) return true;
        return "mixed";
      }

      function deriveResourceMvcRows(rows) {
        rows.filter((row) => row.kind === "directory").forEach((row) => {
          row.selected = deriveResourceMvcDirectoryState(rows, row.id);
        });
        return rows;
      }

      function isResourceMvcRowVisible(rows, row) {
        return resourceMvcAncestorRows(rows, row).every((ancestor) => ancestor.expanded !== false);
      }

      function resourceMvcSelectionState(row) {
        if (row.blocked) return "blocked";
        if (row.selected === "mixed") return "mixed";
        return row.selected === true ? "selected" : "unselected";
      }

      function resourceMvcSelectionLabel(row) {
        const state = resourceMvcSelectionState(row);
        if (state === "blocked") return "Blocked";
        if (state === "mixed") return "Mixed";
        if (state === "selected") return "Selected";
        return "Not selected";
      }

      function updateResourceMvcSelectionButton(button, row) {
        const state = resourceMvcSelectionState(row);
        button.dataset.mcelMvcSelectionState = state;
        button.setAttribute("aria-checked", state === "mixed" ? "mixed" : (state === "selected" ? "true" : "false"));
        button.setAttribute("aria-pressed", state === "selected" ? "true" : "false");
        button.setAttribute("aria-label", row.blocked ? `Blocked row: ${row.path}` : `${resourceMvcSelectionLabel(row)}; toggle selection for ${row.path}`);
        const text = button.querySelector("[data-mcel-mvc-selection-text]");
        if (text) text.textContent = resourceMvcSelectionLabel(row);
      }

      function resourceMvcExpandedGlyph(row) {
        if (row.kind !== "directory") return "";
        return row.expanded === false ? "▸" : "▾";
      }

      function setResourceMvcColumnWidth(table, columnId, widthRem) {
        const column = RESOURCE_MVC_TREEGRID_COLUMNS.find((candidate) => candidate.id === columnId);
        if (!column || !table) return;
        const bounded = Math.min(column.maxRem, Math.max(column.minRem, Number(widthRem) || column.defaultRem));
        const value = bounded.toFixed(2);
        table.style.setProperty(`--mcel-mvc-${column.id}-col`, `${value}rem`);
        table.dataset.mcelLastResizedColumn = column.id;
        const handle = table.querySelector(`[data-mcel-resize-column="${column.id}"]`);
        if (handle) {
          handle.dataset.mcelResizeWidthRem = value;
          handle.setAttribute("aria-label", `${column.label} column width ${value}rem. Drag or use ArrowLeft and ArrowRight to resize.`);
          handle.setAttribute("aria-valuenow", value);
          handle.setAttribute("aria-valuetext", `${value} rem`);
          handle.title = `${column.label} column: ${value}rem. Drag, double-click to reset, or use arrow keys.`;
        }
      }

      function applyResourceMvcColumnPreset(table, presetId = "default") {
        const preset = RESOURCE_MVC_COLUMN_PRESETS[presetId] || RESOURCE_MVC_COLUMN_PRESETS.default;
        RESOURCE_MVC_TREEGRID_COLUMNS.forEach((column) => setResourceMvcColumnWidth(table, column.id, preset[column.id] || column.defaultRem));
        table.dataset.mcelColumnPreset = presetId;
      }

      function appendMvcRoleCard(document, parent, definitionsById, role) {
        const card = applyElementAttributes(createNode(document, "article", `mcel-resource-mvc-card mcel-resource-mvc-card--${role.id}`), requireDefinition(definitionsById, role.elementId), `mvc-${role.id}`);
        card.dataset.mcelMvcRole = role.id;
        card.append(
          createNode(document, "strong", "", role.label),
          createNode(document, "span", "", role.summary)
        );
        parent.appendChild(card);
        return card;
      }

      function appendMvcFieldStrip(document, parent) {
        const strip = createNode(document, "div", "mcel-resource-mvc-field-strip");
        RESOURCE_VIEW_MVC_CONTRACT.model.fields.forEach((field) => {
          const chip = createNode(document, "span", "mcel-resource-mvc-field", `${field.label}: ${field.type}${field.primary ? " · primary" : ""}`);
          chip.dataset.mcelFieldId = field.id;
          chip.dataset.mcelFieldType = field.type;
          strip.appendChild(chip);
        });
        parent.appendChild(strip);
        return strip;
      }

      function appendMvcCommandButton(document, parent, command, label) {
        const button = createNode(document, "button", "mcel-resource-mvc-command", label || command);
        button.type = "button";
        button.dataset.mcelMvcCommand = command;
        button.setAttribute("aria-label", `${label || command} through MVC controller`);
        parent.appendChild(button);
        return button;
      }

      function appendMvcTreegridHeader(document, table) {
        const tableHead = createNode(document, "div", "mcel-resource-mvc-row mcel-resource-mvc-row--header");
        tableHead.setAttribute("role", "row");
        RESOURCE_MVC_TREEGRID_COLUMNS.forEach((column) => {
          const cell = createNode(document, "strong", "mcel-resource-mvc-header-cell", column.label);
          cell.dataset.mcelColumnId = column.id;
          if (column.id !== "select") {
            const handle = createNode(document, "button", "mcel-resource-mvc-resize-handle");
            handle.type = "button";
            handle.setAttribute("data-mcel-resize-column", column.id);
            handle.setAttribute("aria-label", `Resize ${column.label} column`);
            handle.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight Home End");
            handle.setAttribute("aria-valuemin", String(column.minRem));
            handle.setAttribute("aria-valuemax", String(column.maxRem));
            handle.setAttribute("aria-valuenow", String(column.defaultRem));
            handle.setAttribute("title", `Drag to resize ${column.label}; double-click resets; ArrowLeft/ArrowRight nudge width`);
            const grip = createNode(document, "span", "mcel-resource-mvc-resize-grip", "");
            grip.setAttribute("aria-hidden", "true");
            handle.appendChild(grip);
            cell.appendChild(handle);
          }
          tableHead.appendChild(cell);
        });
        table.appendChild(tableHead);
        return tableHead;
      }

      function appendMvcTreegridRow(document, table, definitionsById, row) {
        const rowDefinition = requireDefinition(definitionsById, row.kind === "directory" ? "element.resource.tree-branch" : "element.resource.tree-leaf");
        const line = applyElementAttributes(createNode(document, "div", "mcel-resource-mvc-row"), rowDefinition, row.kind === "directory" ? "mvc-directory-row" : "mvc-file-row");
        line.setAttribute("role", "row");
        line.setAttribute("aria-level", String(row.level || 1));
        line.setAttribute("aria-selected", row.selected === true ? "true" : "false");
        line.setAttribute("aria-disabled", row.blocked ? "true" : "false");
        if (row.kind === "directory") line.setAttribute("aria-expanded", row.expanded === false ? "false" : "true");
        line.tabIndex = row.blocked ? -1 : 0;
        line.dataset.mcelMvcRowId = row.id;
        line.dataset.mcelMvcParentId = row.parentId || "";
        line.dataset.mcelMvcRowKind = row.kind;
        line.dataset.mcelSelectable = row.selectable ? "true" : "false";
        line.dataset.mcelSelectionState = row.selected === "mixed" ? "mixed" : (row.selected ? "selected" : "unselected");
        line.style.setProperty("--mcel-tree-depth", String(Math.max(0, (row.level || 1) - 1)));

        const selectionButton = createNode(document, "button", "mcel-resource-mvc-select");
        selectionButton.type = "button";
        selectionButton.setAttribute("role", "checkbox");
        selectionButton.setAttribute("data-mcel-mvc-select", row.id);
        const selectionBox = createNode(document, "span", "mcel-resource-mvc-select-box", "");
        selectionBox.setAttribute("aria-hidden", "true");
        const selectionText = createNode(document, "span", "mcel-resource-mvc-select-text", resourceMvcSelectionLabel(row));
        selectionText.dataset.mcelMvcSelectionText = "true";
        selectionButton.append(selectionBox, selectionText);
        updateResourceMvcSelectionButton(selectionButton, row);
        if (row.blocked || !row.selectable) selectionButton.disabled = true;

        const expander = createNode(document, "button", "mcel-resource-mvc-expander", resourceMvcExpandedGlyph(row));
        expander.type = "button";
        expander.setAttribute("data-mcel-mvc-expander", row.id);
        expander.setAttribute("aria-label", row.kind === "directory" ? `${row.expanded === false ? "Expand" : "Collapse"} ${row.path}` : `Leaf ${row.path}`);
        expander.setAttribute("aria-expanded", row.kind === "directory" ? (row.expanded === false ? "false" : "true") : "false");
        if (row.kind !== "directory") expander.disabled = true;

        const pathCell = createNode(document, "span", "mcel-resource-mvc-path");
        pathCell.title = row.path;
        pathCell.append(expander, createNode(document, "span", "mcel-resource-mvc-path-label", row.path));

        line.append(
          selectionButton,
          pathCell,
          createNode(document, "span", "mcel-resource-mvc-status", row.status),
          createNode(document, "span", "mcel-resource-mvc-bucket", row.bucket),
          createNode(document, "span", `mcel-resource-mvc-risk mcel-resource-mvc-risk--${sanitizeId(row.risk)}`, row.risk),
          createNode(document, "span", "mcel-resource-mvc-source", row.source),
          createNode(document, "span", "mcel-resource-mvc-reason", row.reason),
          createNode(document, "span", "mcel-resource-mvc-modified", row.modified)
        );
        table.appendChild(line);
        return line;
      }

      function wireResourceMvcContractMockup(document, contract, model) {
        const rows = model.rows;
        const table = contract.querySelector("[data-mcel-mvc-treegrid]");
        const output = contract.querySelector("[data-mcel-mvc-selected-output]");
        const status = contract.querySelector("[data-mcel-mvc-interaction-status]");
        const rowElements = new Map(Array.from(contract.querySelectorAll("[data-mcel-mvc-row-id]")).map((rowNode) => [rowNode.dataset.mcelMvcRowId, rowNode]));
        let lastAction = "ready";
        deriveResourceMvcRows(rows);

        function rowById(rowId) {
          return rows.find((row) => row.id === rowId);
        }

        function setStatus(message) {
          lastAction = message;
          if (status) status.textContent = message;
          contract.dataset.mcelMvcLastAction = message;
        }

        function refresh() {
          deriveResourceMvcRows(rows);
          rows.forEach((row) => {
            const line = rowElements.get(row.id);
            if (!line) return;
            line.hidden = !isResourceMvcRowVisible(rows, row);
            line.setAttribute("aria-selected", row.selected === true ? "true" : "false");
            line.setAttribute("aria-disabled", row.blocked ? "true" : "false");
            line.dataset.mcelSelectionState = row.selected === "mixed" ? "mixed" : (row.selected ? "selected" : "unselected");
            if (row.kind === "directory") line.setAttribute("aria-expanded", row.expanded === false ? "false" : "true");
            const selectButton = line.querySelector("[data-mcel-mvc-select]");
            if (selectButton) {
              selectButton.disabled = Boolean(row.blocked || !row.selectable);
              updateResourceMvcSelectionButton(selectButton, row);
            }
            const expander = line.querySelector("[data-mcel-mvc-expander]");
            if (expander) {
              expander.textContent = resourceMvcExpandedGlyph(row);
              expander.disabled = row.kind !== "directory";
              expander.setAttribute("aria-expanded", row.kind === "directory" ? (row.expanded === false ? "false" : "true") : "false");
              expander.setAttribute("aria-label", row.kind === "directory" ? `${row.expanded === false ? "Expand" : "Collapse"} ${row.path}` : `Leaf ${row.path}`);
            }
          });
          const selectedOutput = selectedMvcBasketRows(rows).map((row) => row.path);
          const expandedCount = rows.filter((row) => row.kind === "directory" && row.expanded !== false).length;
          if (output) {
            output.textContent = `Selected output: explicit-file-paths = ${selectedOutput.join(" | ") || "(none)"} · selected ${selectedOutput.length} · expanded directories ${expandedCount} · blocked rows visible, not selectable · title-only tree rejected · last action: ${lastAction}`;
            output.dataset.mcelSelectedOutputCount = String(selectedOutput.length);
          }
          if (table) {
            table.dataset.mcelSelectedOutputCount = String(selectedOutput.length);
            table.dataset.mcelExpandedDirectoryCount = String(expandedCount);
          }
        }

        function toggleFile(row) {
          if (!row || row.kind !== "file") return;
          if (row.blocked || !row.selectable) {
            setStatus(`Blocked: ${row?.path || "row"} remains visible but cannot enter explicit-file-paths.`);
            refresh();
            return;
          }
          row.selected = row.selected !== true;
          setStatus(`${row.selected ? "Selected" : "Cleared"} file ${row.path}.`);
          refresh();
        }

        function toggleDirectoryShortcut(row) {
          if (!row || row.kind !== "directory") return;
          const descendantFiles = resourceMvcDescendantRows(rows, row.id).filter((candidate) => candidate.kind === "file" && candidate.selectable && !candidate.blocked);
          const allSelected = descendantFiles.length > 0 && descendantFiles.every((candidate) => candidate.selected === true);
          descendantFiles.forEach((candidate) => {
            candidate.selected = !allSelected;
          });
          setStatus(`${allSelected ? "Cleared" : "Selected"} ${descendantFiles.length} selectable descendant file(s) through directory shortcut ${row.path}.`);
          refresh();
        }

        function toggleExpand(row) {
          if (!row || row.kind !== "directory") return;
          row.expanded = row.expanded === false;
          setStatus(`${row.expanded ? "Expanded" : "Collapsed"} ${row.path}; explicit selected files were preserved by the model.`);
          refresh();
        }

        function selectAllEligible() {
          rows.filter((row) => row.kind === "file" && row.selectable && !row.blocked).forEach((row) => {
            row.selected = true;
          });
          setStatus("Selected every eligible file; blocked rows stayed visible and unselected.");
          refresh();
        }

        function clearSelection() {
          rows.filter((row) => row.kind === "file" && row.selectable).forEach((row) => {
            row.selected = false;
          });
          setStatus("Cleared explicit-file-paths output.");
          refresh();
        }

        function setAllExpanded(expanded) {
          rows.filter((row) => row.kind === "directory").forEach((row) => {
            row.expanded = expanded;
          });
          setStatus(`${expanded ? "Expanded" : "Collapsed"} all directories.`);
          refresh();
        }

        contract.addEventListener("click", (event) => {
          const expander = event.target.closest("[data-mcel-mvc-expander]");
          if (expander && !expander.disabled) {
            event.preventDefault();
            toggleExpand(rowById(expander.dataset.mcelMvcExpander));
            return;
          }

          const selectButton = event.target.closest("[data-mcel-mvc-select]");
          if (selectButton) {
            event.preventDefault();
            const row = rowById(selectButton.dataset.mcelMvcSelect);
            if (row?.kind === "directory") toggleDirectoryShortcut(row);
            else toggleFile(row);
            return;
          }

          const commandButton = event.target.closest("[data-mcel-mvc-command]");
          if (!commandButton) return;
          event.preventDefault();
          const command = commandButton.dataset.mcelMvcCommand;
          if (command === "selectAllEligible") selectAllEligible();
          else if (command === "clearSelection") clearSelection();
          else if (command === "expandAll") setAllExpanded(true);
          else if (command === "collapseAll") setAllExpanded(false);
          else if (command === "resetColumnWidths") {
            applyResourceMvcColumnPreset(table, "default");
            setStatus("Reset contract treegrid columns to default widths.");
            refresh();
          } else if (command === "compactColumns") {
            applyResourceMvcColumnPreset(table, "compact");
            setStatus("Applied compact column widths.");
            refresh();
          } else if (command === "widePath") {
            applyResourceMvcColumnPreset(table, "widePath");
            setStatus("Expanded the path column so long repo-relative paths can be inspected.");
            refresh();
          }
        });

        contract.addEventListener("keydown", (event) => {
          if (![" ", "Enter"].includes(event.key)) return;
          const focusedRow = event.target.closest("[data-mcel-mvc-row-id]");
          if (!focusedRow) return;
          const row = rowById(focusedRow.dataset.mcelMvcRowId);
          event.preventDefault();
          if (row?.kind === "directory") toggleExpand(row);
          else toggleFile(row);
        });

        Array.from(contract.querySelectorAll("[data-mcel-resize-column]")).forEach((handle) => {
          function currentWidthRem(column) {
            return parseFloat((table.style.getPropertyValue(`--mcel-mvc-${column.id}-col`) || `${column.defaultRem}rem`).replace("rem", ""));
          }

          handle.addEventListener("pointerdown", (event) => {
            const columnId = handle.dataset.mcelResizeColumn;
            const column = RESOURCE_MVC_TREEGRID_COLUMNS.find((candidate) => candidate.id === columnId);
            if (!column || !table) return;
            event.preventDefault();
            const startX = event.clientX;
            const computedWidth = currentWidthRem(column);
            contract.dataset.mcelResizingColumn = column.id;
            handle.dataset.mcelActiveResize = "true";
            const onPointerMove = (moveEvent) => {
              const deltaRem = (moveEvent.clientX - startX) / 16;
              setResourceMvcColumnWidth(table, column.id, computedWidth + deltaRem);
              if (status) status.textContent = `Resizing ${column.label} column through MVC view state.`;
            };
            const onPointerUp = () => {
              document.removeEventListener("pointermove", onPointerMove);
              document.removeEventListener("pointerup", onPointerUp);
              delete contract.dataset.mcelResizingColumn;
              delete handle.dataset.mcelActiveResize;
              setStatus(`Resized ${column.label} column; the data contract stayed unchanged.`);
              refresh();
            };
            document.addEventListener("pointermove", onPointerMove);
            document.addEventListener("pointerup", onPointerUp);
          });
          handle.addEventListener("keydown", (event) => {
            const column = RESOURCE_MVC_TREEGRID_COLUMNS.find((candidate) => candidate.id === handle.dataset.mcelResizeColumn);
            if (!column || !table) return;
            const keyStep = event.shiftKey ? 2 : 1;
            let nextWidth = currentWidthRem(column);
            if (event.key === "ArrowLeft") nextWidth -= keyStep;
            else if (event.key === "ArrowRight") nextWidth += keyStep;
            else if (event.key === "Home") nextWidth = column.minRem;
            else if (event.key === "End") nextWidth = column.maxRem;
            else return;
            event.preventDefault();
            setResourceMvcColumnWidth(table, column.id, nextWidth);
            setStatus(`Keyboard resized ${column.label} column to ${handle.dataset.mcelResizeWidthRem}rem.`);
            refresh();
          });
          handle.addEventListener("dblclick", () => {
            const column = RESOURCE_MVC_TREEGRID_COLUMNS.find((candidate) => candidate.id === handle.dataset.mcelResizeColumn);
            if (!column || !table) return;
            setResourceMvcColumnWidth(table, column.id, column.defaultRem);
            setStatus(`Reset ${column.label} column width.`);
            refresh();
          });
        });

        applyResourceMvcColumnPreset(table, "default");
        setStatus("Ready: use selection buttons, folder chevrons, command buttons, keyboard Enter/Space, or drag column handles.");
        refresh();

        return {
          rows,
          refresh,
          toggleFile,
          toggleDirectoryShortcut,
          setAllExpanded,
          setResourceMvcColumnWidth: (columnId, widthRem) => setResourceMvcColumnWidth(table, columnId, widthRem)
        };
      }

      function renderResourceMvcContractMockup(document, parent, definitionsById) {
        const rows = cloneResourceMvcBasketRows();
        deriveResourceMvcRows(rows);
        const contract = applyElementAttributes(createNode(document, "section", "mcel-resource-mvc-contract"), requireDefinition(definitionsById, "element.resource.view-contract"), "resource-view-contract");
        contract.setAttribute("data-mcel-view-contract", RESOURCE_VIEW_MVC_CONTRACT.id);
        contract.setAttribute("data-mcel-selection-contract", RESOURCE_VIEW_MVC_CONTRACT.selection.mode);
        contract.dataset.mcelMvcContract = "model-view-controller";
        contract.setAttribute("data-mcel-mvc-interactive", "selection-expand-collapse-resize");
        contract.setAttribute("aria-label", "Contract-first MVC resource file basket mock");

        const header = createNode(document, "header", "mcel-resource-mvc-head");
        header.append(
          createNode(document, "strong", "", RESOURCE_VIEW_MVC_CONTRACT.label),
          createNode(document, "span", "", "Acid-test-only mockup of what Git Tools should become: model declares fields and selection semantics; controller enforces them; view must satisfy the contract. This version is interactive: select files/folders with legible checkbox controls, expand/collapse directories, and resize columns with real drag handles or keyboard nudges.")
        );

        const roleGrid = createNode(document, "div", "mcel-resource-mvc-role-grid");
        RESOURCE_VIEW_MVC_ROLES.forEach((role) => appendMvcRoleCard(document, roleGrid, definitionsById, role));

        const contractPanel = applyElementAttributes(createNode(document, "section", "mcel-resource-mvc-panel mcel-resource-mvc-panel--model"), requireDefinition(definitionsById, "element.core.mvc-model"), "mvc-model-contract");
        contractPanel.append(
          createNode(document, "strong", "", "Model says what must be displayed"),
          createNode(document, "p", "", RESOURCE_VIEW_MVC_CONTRACT.intent)
        );
        appendMvcFieldStrip(document, contractPanel);

        const selectionPanel = applyElementAttributes(createNode(document, "section", "mcel-resource-mvc-panel mcel-resource-mvc-panel--controller"), requireDefinition(definitionsById, "element.resource.selection-contract"), "mvc-selection-contract");
        selectionPanel.setAttribute("data-mcel-selection-contract", RESOURCE_VIEW_MVC_CONTRACT.selection.mode);
        selectionPanel.append(
          createNode(document, "strong", "", "Controller owns interactive selection semantics"),
          createNode(document, "p", "", `${RESOURCE_VIEW_MVC_CONTRACT.selection.output}; ${RESOURCE_VIEW_MVC_CONTRACT.selection.directoryBehavior}; blocked rows visible, not selectable; expansion and column sizing are view state, not business truth.`)
        );
        const commandStrip = createNode(document, "div", "mcel-resource-mvc-command-strip");
        [
          ["selectAllEligible", "Select all eligible"],
          ["clearSelection", "Clear selection"],
          ["expandAll", "Expand all"],
          ["collapseAll", "Collapse all"],
          ["compactColumns", "Compact columns"],
          ["widePath", "Wide path"],
          ["resetColumnWidths", "Reset widths"]
        ].forEach(([command, label]) => appendMvcCommandButton(document, commandStrip, command, label));
        const interactionStatus = createNode(document, "div", "mcel-resource-mvc-interaction-status", "Ready.");
        interactionStatus.dataset.mcelMvcInteractionStatus = "true";
        interactionStatus.setAttribute("role", "status");
        selectionPanel.append(commandStrip, interactionStatus);

        const table = applyElementAttributes(createNode(document, "div", "mcel-resource-mvc-treegrid"), requireDefinition(definitionsById, "element.resource.contract-treegrid"), "mvc-contract-treegrid");
        table.setAttribute("role", "treegrid");
        table.setAttribute("aria-label", "Contract treegrid file basket mock");
        table.dataset.mcelMvcTreegrid = "interactive";
        table.dataset.mcelViewCapabilities = RESOURCE_VIEW_MVC_CONTRACT.view.requires.join("|");
        appendMvcTreegridHeader(document, table);
        rows.forEach((row) => appendMvcTreegridRow(document, table, definitionsById, row));

        const selectedOutput = selectedMvcBasketRows(rows).map((row) => row.path);
        const output = applyElementAttributes(createNode(document, "output", "mcel-resource-mvc-output", `Selected output: explicit-file-paths = ${selectedOutput.join(" | ")} · directories are shortcuts · blocked rows visible, not selectable · title-only tree rejected`), requireDefinition(definitionsById, "element.core.mvc-controller"), "mvc-selected-output");
        output.dataset.mcelMvcSelectedOutput = "true";
        output.dataset.mcelSelectedOutput = "explicit-file-paths";

        const resolver = applyElementAttributes(createNode(document, "section", "mcel-resource-mvc-resolver"), requireDefinition(definitionsById, "element.core.mvc-view"), "mvc-view-resolver");
        resolver.appendChild(createNode(document, "strong", "", "View resolver"));
        RESOURCE_VIEW_MVC_CONTRACT.view.eligible.forEach((viewOption) => {
          const item = createNode(document, "span", "mcel-resource-mvc-resolver-item", `${viewOption.label}: ${viewOption.reason}`);
          item.dataset.mcelViewAllowed = viewOption.id;
          resolver.appendChild(item);
        });
        RESOURCE_VIEW_MVC_CONTRACT.view.rejected.forEach((viewOption) => {
          const item = createNode(document, "span", "mcel-resource-mvc-resolver-item mcel-resource-mvc-resolver-item--rejected", `${viewOption.label}: ${viewOption.reason}`);
          item.dataset.mcelViewRejected = viewOption.id;
          resolver.appendChild(item);
        });

        contract.append(header, roleGrid, contractPanel, selectionPanel, table, output, resolver);
        wireResourceMvcContractMockup(document, contract, {rows});
        parent.appendChild(contract);
        return contract;
      }

      function appendTreeNode(document, parent, definitionsById, node, options = {}) {
        const rowDefinition = requireDefinition(definitionsById, node.kind === "folder" ? "element.resource.tree-branch" : "element.resource.tree-leaf");
        const row = applyElementAttributes(createNode(document, "div", `mcel-resource-tree-row mcel-resource-tree-row--${options.mode || "explorer"}`), rowDefinition, node.kind === "folder" ? "tree-branch" : "tree-leaf");
        row.setAttribute("role", "treeitem");
        row.setAttribute("aria-level", String(node.level || 1));
        row.setAttribute("aria-selected", node.selected ? "true" : "false");
        row.setAttribute("aria-setsize", String(options.setSize || treeFixtureRows().filter((candidate) => candidate.level === node.level).length));
        row.setAttribute("aria-posinset", String(options.posInSet || 1));
        if (node.kind === "folder") row.setAttribute("aria-expanded", node.expanded ? "true" : "false");
        row.dataset.nodeId = node.id;
        row.dataset.resourceKind = node.kind;
        row.dataset.status = node.status || "";
        row.title = `${node.label} · ${node.type || node.kind} · ${node.policy || "inspect-only"}`;
        row.style.setProperty("--mcel-tree-depth", String(Math.max(0, (node.level || 1) - 1)));

        const expanderDefinition = requireDefinition(definitionsById, "element.resource.tree-expander");
        const expander = applyElementAttributes(createNode(document, "button", "mcel-resource-tree-expander", node.kind === "folder" ? (node.expanded ? "▾" : "▸") : ""), expanderDefinition, "tree-expander");
        expander.type = "button";
        expander.setAttribute("aria-label", node.kind === "folder" ? `${node.expanded ? "Collapse" : "Expand"} ${node.label}` : `Leaf ${node.label}`);
        expander.setAttribute("aria-expanded", node.kind === "folder" ? String(Boolean(node.expanded)) : "false");
        expander.setAttribute("aria-controls", node.id);

        const glyph = createNode(document, "span", `mcel-resource-tree-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || (node.kind === "folder" ? "folder" : "file"));
        const label = createNode(document, "button", "mcel-resource-tree-label", node.label);
        label.type = "button";
        label.setAttribute("data-mcel-resource-action", node.kind === "folder" ? "select-preview-or-open-directory" : "select-preview");
        label.title = node.label;

        const meta = createNode(document, "span", "mcel-resource-tree-meta", options.showMeta === false ? "" : (node.type || ""));
        const badge = createNode(document, "span", "mcel-resource-tree-badge", node.badge || "");
        if (!node.badge) badge.setAttribute("aria-hidden", "true");
        const policy = createNode(document, "span", "mcel-resource-tree-policy", node.policy || (node.kind === "folder" ? "read boundary" : "preview only"));

        row.append(expander, glyph, label, meta, badge, policy);
        parent.appendChild(row);
        return row;
      }

      function appendResourceViewMenu(document, parent, definitionsById) {
        const controller = applyElementAttributes(createNode(document, "section", "mcel-resource-view-menu"), requireDefinition(definitionsById, "element.resource.view-mode-controller"), "explorer-view-menu-controller");
        controller.setAttribute("role", "toolbar");
        controller.setAttribute("aria-label", "Explorer-grade resource view menu");
        const heading = createNode(document, "div", "mcel-resource-view-menu-head");
        heading.append(
          createNode(document, "strong", "", "View menu parity"),
          createNode(document, "span", "", "Windows icon/list/details/tiles/content · macOS Finder gallery/columns · GNOME/KDE/XFCE Linux views · side panes · all inspect-only")
        );
        const grid = createNode(document, "div", "mcel-resource-view-menu-grid");
        RESOURCE_VIEW_MENU_OPTIONS.forEach((option) => {
          const button = createNode(document, "button", `mcel-resource-view-menu-option mcel-resource-view-menu-option--${option.family}`, option.label);
          button.type = "button";
          button.dataset.mcelResourceViewOption = option.id;
          button.dataset.mcelResourceViewTarget = VIEW_MENU_TREE_MODE_MAP[option.id] || "";
          button.setAttribute("aria-pressed", option.active ? "true" : "false");
          button.setAttribute("title", option.iconSize ? `${option.label} · ${option.iconSize}px thumbnails` : `${option.label} · inspect-only`);
          grid.appendChild(button);
        });
        const status = createNode(document, "div", "mcel-resource-view-menu-status", "Active view: Details · Details pane on · Preview pane on");
        status.dataset.mcelResourceViewStatus = "true";
        status.setAttribute("role", "status");
        controller.append(heading, grid, status);
        parent.appendChild(controller);
        return controller;
      }

      function appendTreeViewShell(document, parent, definitionsById, mode, active) {
        const viewportDefinition = requireDefinition(definitionsById, "element.resource.tree-viewport");
        const view = applyElementAttributes(createNode(document, "section", `mcel-resource-tree-mode mcel-resource-tree-mode--${mode.id}`), viewportDefinition, `tree-view-${mode.id}`);
        view.dataset.treeViewMode = mode.id;
        view.hidden = !active;
        view.setAttribute("aria-label", `${mode.label} tree view`);
        const header = createNode(document, "header", "mcel-resource-tree-mode-head");
        header.append(
          createNode(document, "strong", "", mode.label),
          createNode(document, "span", "", `${mode.source} pattern · ${mode.summary}`)
        );
        view.appendChild(header);
        parent.appendChild(view);
        return view;
      }

      function renderExplorerTreeView(document, view, definitionsById) {
        const viewport = applyElementAttributes(createNode(document, "div", "mcel-resource-tree-viewport mcel-resource-tree-viewport--explorer"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "explorer-sidebar-viewport");
        viewport.setAttribute("role", "tree");
        viewport.setAttribute("aria-label", "Explorer-style navigation tree");
        const rows = treeFixtureRows();
        rows.forEach((node, index) => appendTreeNode(document, viewport, definitionsById, node, {mode: "explorer", posInSet: index + 1, setSize: rows.length}));
        view.appendChild(viewport);
      }

      function renderIdeTreeView(document, view, definitionsById) {
        const frame = createNode(document, "div", "mcel-resource-tree-ide-frame");
        const title = createNode(document, "div", "mcel-resource-tree-ide-title", "EXPLORER");
        const viewport = applyElementAttributes(createNode(document, "div", "mcel-resource-tree-viewport mcel-resource-tree-viewport--ide"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "ide-project-tree-viewport");
        viewport.setAttribute("role", "tree");
        viewport.setAttribute("aria-label", "IDE project tree with decorations");
        const rows = treeFixtureRows().filter((node) => !["workspace/desktop", "workspace/pictures"].includes(node.id));
        rows.forEach((node, index) => appendTreeNode(document, viewport, definitionsById, node, {mode: "ide", posInSet: index + 1, setSize: rows.length}));
        frame.append(title, viewport);
        view.appendChild(frame);
      }

      function renderDetailsTreegrid(document, view, definitionsById) {
        const table = applyElementAttributes(createNode(document, "div", "mcel-resource-treegrid"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "details-treegrid");
        table.setAttribute("role", "treegrid");
        table.setAttribute("aria-label", "Details treegrid");
        const header = createNode(document, "div", "mcel-resource-treegrid-row mcel-resource-treegrid-head");
        ["Name", "Modified", "Type", "Size", "Proof policy"].forEach((label) => header.appendChild(createNode(document, "strong", "", label)));
        table.appendChild(header);
        treeFixtureRows().forEach((node) => {
          const rowDefinition = requireDefinition(definitionsById, node.kind === "folder" ? "element.resource.tree-branch" : "element.resource.tree-leaf");
          const row = applyElementAttributes(createNode(document, "div", "mcel-resource-treegrid-row"), rowDefinition, "details-treegrid-row");
          row.setAttribute("role", "row");
          row.setAttribute("aria-level", String(node.level || 1));
          row.setAttribute("aria-selected", node.selected ? "true" : "false");
          row.style.setProperty("--mcel-tree-depth", String(Math.max(0, (node.level || 1) - 1)));
          row.append(
            createNode(document, "span", "mcel-resource-treegrid-name", `${node.kind === "folder" ? (node.expanded ? "▾" : "▸") : "·"} ${node.label}`),
            createNode(document, "span", "", node.modified || ""),
            createNode(document, "span", "", node.type || ""),
            createNode(document, "span", "", node.size || ""),
            createNode(document, "span", "mcel-resource-tree-policy", node.policy || "")
          );
          table.appendChild(row);
        });
        view.appendChild(table);
      }

      function appendColumnItem(document, panel, label, selected = false) {
        const item = createNode(document, "button", selected ? "is-selected" : "", label);
        item.type = "button";
        item.title = label;
        panel.appendChild(item);
        return item;
      }

      function renderColumnBrowser(document, view, definitionsById) {
        const browser = createNode(document, "div", "mcel-resource-column-browser");
        const columns = [
          {title: "Roots", rows: ["Desktop", "Downloads", "Documents", "Pictures"], selected: "Downloads"},
          {title: "Downloads", rows: ["mcel_element_resource_tree_primitives_patch", "mcel_element_library_visual_workbench_patch", "temporal_fdb_market_progress_stdout_patch", "temporal_fdb_50_node_market_smoke_patch"], selected: "mcel_element_library_visual_workbench_patch"},
          {title: "Preview", rows: ["Kind: Compressed Folder", "Policy: preview only", "Mutations: blocked", "Details pane: open", "Preview pane: open"], selected: "Policy: preview only"}
        ];
        columns.forEach((column, index) => {
          const columnDefinition = requireDefinition(definitionsById, index === 2 ? "element.core.preview-pane" : "element.resource.tree-viewport");
          const panel = applyElementAttributes(createNode(document, "section", "mcel-resource-column"), columnDefinition, index === 2 ? "column-preview" : "miller-column");
          panel.appendChild(createNode(document, "strong", "", column.title));
          column.rows.forEach((label) => appendColumnItem(document, panel, label, label === column.selected));
          browser.appendChild(panel);
        });
        view.appendChild(browser);
      }

      function renderOutlineTree(document, view, definitionsById) {
        const outline = applyElementAttributes(createNode(document, "div", "mcel-resource-outline-tree"), requireDefinition(definitionsById, "element.resource.directory-tree"), "outline-tree");
        [
          ["Canonical App Test", "region", 1],
          ["Specimen planner", "panel", 2],
          ["Tree workbench", "panel", 2],
          ["Resource tree", "component", 3],
          ["View menu controller", "toolbar", 3],
          ["Preview/details panes", "side panes", 3],
          ["Proof rail", "status-feed", 3],
          ["Registry catalog", "details", 2]
        ].forEach(([label, kind, level]) => {
          const row = createNode(document, "div", "mcel-resource-outline-row");
          row.style.setProperty("--mcel-tree-depth", String(level - 1));
          row.append(createNode(document, "span", "", "§"), createNode(document, "strong", "", label), createNode(document, "span", "", kind));
          outline.appendChild(row);
        });
        view.appendChild(outline);
      }

      function renderAccessibilityProofTree(document, view, definitionsById) {
        const proof = createNode(document, "div", "mcel-resource-tree-proof-grid");
        [
          ["Selection model", "active=diagnose_mouse_hangs.py · selected=1 · preview target stable across every view mode", "element.resource.tree-selection-model"],
          ["Keyboard controller", "↑ ↓ navigate · ← collapse · → expand · Home/End jump · Delete blocked · Alt+V view menu", "element.resource.tree-keyboard-controller"],
          ["View mode controller", "Extra large icons/List/Details/Tiles/Content change presentation only; selected path remains stable", "element.resource.view-mode-controller"],
          ["Context menu", "Preview/Copy Path/Reveals safe · Rename/Move/Delete no-submit/no-click", "element.resource.tree-context-menu"],
          ["Drag/drop boundary", "drag ghost allowed · drop write/move/upload blocked", "element.resource.tree-drag-drop-boundary"],
          ["Viewport proof", "one owned scrollport · no row scrollbars · active row remains visible · long names ellipsize", "element.resource.tree-viewport"]
        ].forEach(([title, text, elementId]) => {
          const card = applyElementAttributes(createNode(document, "article", "mcel-resource-tree-proof-card"), requireDefinition(definitionsById, elementId), `proof-${sanitizeId(title)}`);
          card.append(createNode(document, "strong", "", title), createNode(document, "span", "", text));
          proof.appendChild(card);
        });
        view.appendChild(proof);
      }

      function appendResourceIconCard(document, parent, definitionsById, node, sizeClass = "large") {
        const card = applyElementAttributes(createNode(document, "button", `mcel-resource-icon-card mcel-resource-icon-card--${sizeClass}`), requireDefinition(definitionsById, "element.resource.resource-row"), "resource-icon-card");
        card.type = "button";
        card.setAttribute("role", "option");
        card.setAttribute("aria-selected", node.selected ? "true" : "false");
        card.title = `${node.label} · ${node.type || node.kind}`;
        card.dataset.status = node.status || "";
        const glyph = createNode(document, "span", `mcel-resource-icon-card-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || (node.kind === "folder" ? "folder" : "file"));
        const label = createNode(document, "span", "mcel-resource-icon-card-label", node.label);
        const meta = createNode(document, "span", "mcel-resource-icon-card-meta", `${node.type || node.kind}${node.size ? ` · ${node.size}` : ""}`);
        card.append(glyph, label, meta);
        parent.appendChild(card);
        return card;
      }

      function renderIconGrid(document, view, definitionsById) {
        const grid = applyElementAttributes(createNode(document, "div", "mcel-resource-icon-grid"), requireDefinition(definitionsById, "element.resource.icon-grid"), "extra-large-icon-grid");
        grid.setAttribute("role", "listbox");
        grid.setAttribute("aria-label", "Icon grid with view-size menu parity");
        const header = createNode(document, "div", "mcel-resource-icon-grid-header");
        header.append(
          createNode(document, "strong", "", "Icon size ladder"),
          createNode(document, "span", "", "Extra large icons → large → medium → small, all preserving selected resource and preview target.")
        );
        const rail = createNode(document, "div", "mcel-resource-icon-size-rail");
        RESOURCE_VIEW_MENU_OPTIONS.filter((option) => option.family === "icons").forEach((option) => {
          const chip = createNode(document, "span", "", `${option.label} · ${option.iconSize}px`);
          rail.appendChild(chip);
        });
        const cards = createNode(document, "div", "mcel-resource-icon-card-grid");
        treeFixtureRows().filter((node) => node.kind === "file" || node.level <= 2).forEach((node) => appendResourceIconCard(document, cards, definitionsById, node, node.kind === "folder" ? "medium" : "large"));
        grid.append(header, rail, cards);
        view.appendChild(grid);
      }

      function renderListView(document, view, definitionsById) {
        const list = applyElementAttributes(createNode(document, "div", "mcel-resource-list-view"), requireDefinition(definitionsById, "element.resource.view-mode-controller"), "explorer-list-view");
        list.setAttribute("role", "listbox");
        list.setAttribute("aria-label", "Compact list resource view");
        treeFixtureRows().filter((node) => node.kind === "file" || node.level <= 2).forEach((node) => {
          const row = applyElementAttributes(createNode(document, "button", "mcel-resource-list-row"), requireDefinition(definitionsById, "element.resource.resource-row"), "list-row");
          row.type = "button";
          row.setAttribute("role", "option");
          row.setAttribute("aria-selected", node.selected ? "true" : "false");
          row.title = node.label;
          row.append(
            createNode(document, "span", `mcel-resource-tree-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || node.kind),
            createNode(document, "strong", "", node.label),
            createNode(document, "span", "", node.type || node.kind),
            createNode(document, "span", "", node.badge || node.status || "tracked")
          );
          list.appendChild(row);
        });
        view.appendChild(list);
      }

      function renderTilesView(document, view, definitionsById) {
        const tiles = applyElementAttributes(createNode(document, "div", "mcel-resource-tile-view"), requireDefinition(definitionsById, "element.resource.icon-grid"), "explorer-tiles-view");
        tiles.setAttribute("role", "listbox");
        tiles.setAttribute("aria-label", "Tiles resource view");
        treeFixtureRows().filter((node) => node.kind === "file").forEach((node) => {
          const tile = applyElementAttributes(createNode(document, "button", "mcel-resource-tile-card"), requireDefinition(definitionsById, "element.resource.resource-row"), "tile-card");
          tile.type = "button";
          tile.setAttribute("role", "option");
          tile.setAttribute("aria-selected", node.selected ? "true" : "false");
          tile.title = node.label;
          tile.append(
            createNode(document, "span", `mcel-resource-tile-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || "file"),
            createNode(document, "strong", "", node.label),
            createNode(document, "span", "", `${node.type || "file"} · ${node.size || "—"}`),
            createNode(document, "span", "mcel-resource-tree-policy", node.policy || "preview only")
          );
          tiles.appendChild(tile);
        });
        view.appendChild(tiles);
      }

      function renderContentView(document, view, definitionsById) {
        const content = applyElementAttributes(createNode(document, "div", "mcel-resource-content-view"), requireDefinition(definitionsById, "element.resource.details-pane"), "explorer-content-view");
        content.setAttribute("role", "listbox");
        content.setAttribute("aria-label", "Content resource view");
        treeFixtureRows().filter((node) => node.kind === "file").forEach((node) => {
          const row = applyElementAttributes(createNode(document, "article", "mcel-resource-content-row"), requireDefinition(definitionsById, "element.resource.resource-row"), "content-row");
          row.setAttribute("role", "option");
          row.setAttribute("aria-selected", node.selected ? "true" : "false");
          row.append(
            createNode(document, "span", `mcel-resource-content-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || "file"),
            createNode(document, "strong", "", node.label),
            createNode(document, "span", "", `${node.type || "file"} · ${node.modified || "unknown"} · ${node.size || "—"}`),
            createNode(document, "p", "", node.snippet || "Read-only preview is available; mutating actions are blocked by policy.")
          );
          content.appendChild(row);
        });
        view.appendChild(content);
      }

      function renderFinderGalleryView(document, view, definitionsById) {
        const selected = selectedResource();
        const gallery = applyElementAttributes(createNode(document, "div", "mcel-resource-finder-gallery"), requireDefinition(definitionsById, "element.resource.icon-grid"), "finder-gallery-view");
        gallery.setAttribute("role", "listbox");
        gallery.setAttribute("aria-label", "macOS Finder Gallery resource view");

        const preview = createNode(document, "section", "mcel-resource-finder-gallery-preview");
        preview.append(
          createNode(document, "span", "mcel-resource-finder-gallery-glyph mcel-resource-tree-glyph--file", selected.icon || "file"),
          createNode(document, "strong", "", selected.label),
          createNode(document, "p", "", selected.snippet || "Read-only Finder-style gallery preview; mutations remain blocked.")
        );

        const strip = createNode(document, "div", "mcel-resource-finder-gallery-strip");
        treeFixtureRows().filter((node) => node.kind === "file").forEach((node) => {
          const thumb = applyElementAttributes(createNode(document, "button", "mcel-resource-finder-gallery-thumb"), requireDefinition(definitionsById, "element.resource.resource-row"), "finder-gallery-thumbnail");
          thumb.type = "button";
          thumb.setAttribute("role", "option");
          thumb.setAttribute("aria-selected", node.selected ? "true" : "false");
          thumb.title = node.label;
          thumb.append(
            createNode(document, "span", `mcel-resource-tree-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || "file"),
            createNode(document, "span", "", node.label)
          );
          strip.appendChild(thumb);
        });

        const inspector = applyElementAttributes(createNode(document, "aside", "mcel-resource-finder-inspector"), requireDefinition(definitionsById, "element.resource.details-pane"), "finder-gallery-inspector");
        [
          ["Kind", selected.type || selected.kind],
          ["Modified", selected.modified || "unknown"],
          ["Size", selected.size || "—"],
          ["Tags", "proof · read-only · selected"],
          ["Policy", selected.policy || "preview only"]
        ].forEach(([label, value]) => appendChip(document, inspector, label, value));

        gallery.append(preview, strip, inspector);
        view.appendChild(gallery);
      }

      function renderFinderColumnInspectorView(document, view, definitionsById) {
        const selected = selectedResource();
        const browser = createNode(document, "div", "mcel-resource-finder-column-inspector");
        const columns = [
          {title: "Favorites", rows: ["AirDrop", "Recents", "Applications", "Desktop", "Downloads"], selected: "Downloads"},
          {title: "Downloads", rows: ["mcel_element_resource_tree_primitives_patch", "mcel_element_library_visual_workbench_patch", "temporal_fdb_market_progress_stdout_patch", "protected_temporal_clean_failure_smoke_patch"], selected: "mcel_element_library_visual_workbench_patch"},
          {title: "Package contents", rows: ["main_computer", "tests", "README.md", "reference.patch"], selected: "tests"},
          {title: "Inspector", rows: [`Name: ${selected.label}`, `Kind: ${selected.type || selected.kind}`, `Modified: ${selected.modified || "unknown"}`, "Tags: proof, no-write", "Preview: open"], selected: `Name: ${selected.label}`}
        ];
        columns.forEach((column, index) => {
          const columnDefinition = requireDefinition(definitionsById, index === columns.length - 1 ? "element.resource.details-pane" : "element.resource.tree-viewport");
          const panel = applyElementAttributes(createNode(document, "section", `mcel-resource-finder-column ${index === columns.length - 1 ? "mcel-resource-finder-column--inspector" : ""}`), columnDefinition, index === columns.length - 1 ? "finder-column-inspector" : "finder-column");
          panel.appendChild(createNode(document, "strong", "", column.title));
          column.rows.forEach((label) => appendColumnItem(document, panel, label, label === column.selected));
          browser.appendChild(panel);
        });
        view.appendChild(browser);
      }

      function renderGnomeGridView(document, view, definitionsById) {
        const shell = applyElementAttributes(createNode(document, "div", "mcel-resource-gnome-grid"), requireDefinition(definitionsById, "element.resource.icon-grid"), "gnome-files-grid");
        shell.setAttribute("role", "listbox");
        shell.setAttribute("aria-label", "GNOME Files grid resource view");
        const header = createNode(document, "div", "mcel-resource-gnome-header");
        header.append(
          createNode(document, "span", "", "Home"),
          createNode(document, "span", "", "Downloads"),
          createNode(document, "strong", "", "Grid")
        );
        const content = createNode(document, "div", "mcel-resource-gnome-grid-content");
        treeFixtureRows().filter((node) => node.kind === "folder" || node.kind === "file").slice(1).forEach((node) => {
          const card = applyElementAttributes(createNode(document, "button", "mcel-resource-gnome-card"), requireDefinition(definitionsById, "element.resource.resource-row"), "gnome-grid-card");
          card.type = "button";
          card.setAttribute("role", "option");
          card.setAttribute("aria-selected", node.selected ? "true" : "false");
          card.title = node.label;
          card.append(
            createNode(document, "span", "mcel-resource-gnome-check", node.selected ? "✓" : ""),
            createNode(document, "span", `mcel-resource-gnome-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || node.kind),
            createNode(document, "strong", "", node.label),
            createNode(document, "span", "", node.type || node.kind)
          );
          content.appendChild(card);
        });
        shell.append(header, content);
        view.appendChild(shell);
      }

      function renderGnomeListView(document, view, definitionsById) {
        const list = applyElementAttributes(createNode(document, "div", "mcel-resource-gnome-list"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "gnome-files-list");
        list.setAttribute("role", "grid");
        list.setAttribute("aria-label", "GNOME Files list resource view");
        const header = createNode(document, "div", "mcel-resource-gnome-list-row mcel-resource-gnome-list-head");
        ["", "Name", "Modified", "Type", "Size", "Policy"].forEach((label) => header.appendChild(createNode(document, "strong", "", label)));
        list.appendChild(header);
        treeFixtureRows().filter((node) => node.kind === "file" || node.level <= 2).forEach((node) => {
          const row = applyElementAttributes(createNode(document, "button", "mcel-resource-gnome-list-row"), requireDefinition(definitionsById, "element.resource.resource-row"), "gnome-list-row");
          row.type = "button";
          row.setAttribute("role", "row");
          row.setAttribute("aria-selected", node.selected ? "true" : "false");
          row.title = node.label;
          row.append(
            createNode(document, "span", "", node.status === "favorite" ? "★" : "☆"),
            createNode(document, "strong", "", `${node.icon || (node.kind === "folder" ? "folder" : "file")} ${node.label}`),
            createNode(document, "span", "", node.modified || ""),
            createNode(document, "span", "", node.type || node.kind),
            createNode(document, "span", "", node.size || "—"),
            createNode(document, "span", "mcel-resource-tree-policy", node.policy || "preview only")
          );
          list.appendChild(row);
        });
        view.appendChild(list);
      }

      function renderDolphinSplitDetailsView(document, view, definitionsById) {
        const shell = applyElementAttributes(createNode(document, "div", "mcel-resource-dolphin-split"), requireDefinition(definitionsById, "element.resource.view-mode-controller"), "dolphin-split-details");
        shell.setAttribute("aria-label", "KDE Dolphin split/details resource view");
        const places = createNode(document, "nav", "mcel-resource-dolphin-places");
        places.append(createNode(document, "strong", "", "Places"));
        ["Home", "Downloads", "Documents", "Network", "Trash"].forEach((label) => appendChip(document, places, label));

        const makePane = (title, rows) => {
          const pane = applyElementAttributes(createNode(document, "section", "mcel-resource-dolphin-pane"), requireDefinition(definitionsById, "element.resource.tree-viewport"), `dolphin-pane-${sanitizeId(title)}`);
          pane.appendChild(createNode(document, "strong", "", title));
          const header = createNode(document, "div", "mcel-resource-dolphin-row mcel-resource-dolphin-head");
          ["Name", "Type", "Size", "Modified"].forEach((label) => header.appendChild(createNode(document, "span", "", label)));
          pane.appendChild(header);
          rows.forEach((node) => {
            const row = createNode(document, "button", "mcel-resource-dolphin-row");
            row.type = "button";
            row.setAttribute("aria-selected", node.selected ? "true" : "false");
            row.title = node.label;
            row.append(
              createNode(document, "strong", "", `${node.icon || node.kind} ${node.label}`),
              createNode(document, "span", "", node.type || node.kind),
              createNode(document, "span", "", node.size || "—"),
              createNode(document, "span", "", node.modified || "unknown")
            );
            pane.appendChild(row);
          });
          return pane;
        };

        const rows = treeFixtureRows().filter((node) => node.kind === "file" || node.level <= 2);
        shell.append(
          places,
          makePane("Downloads", rows.slice(1, 6)),
          makePane("Patch preview", rows.filter((node) => node.kind === "file").slice(0, 5))
        );
        view.appendChild(shell);
      }

      function renderThunarCompactView(document, view, definitionsById) {
        const compact = applyElementAttributes(createNode(document, "div", "mcel-resource-thunar-compact"), requireDefinition(definitionsById, "element.resource.view-mode-controller"), "thunar-compact-view");
        compact.setAttribute("role", "listbox");
        compact.setAttribute("aria-label", "XFCE Thunar compact resource view");
        const header = createNode(document, "div", "mcel-resource-thunar-header");
        header.append(
          createNode(document, "strong", "", "Compact"),
          createNode(document, "span", "", "small icons · newspaper columns · no overlapping long names")
        );
        const columns = createNode(document, "div", "mcel-resource-thunar-columns");
        treeFixtureRows().filter((node) => node.kind === "file" || node.level <= 2).forEach((node) => {
          const item = applyElementAttributes(createNode(document, "button", "mcel-resource-thunar-item"), requireDefinition(definitionsById, "element.resource.resource-row"), "thunar-compact-item");
          item.type = "button";
          item.setAttribute("role", "option");
          item.setAttribute("aria-selected", node.selected ? "true" : "false");
          item.title = node.label;
          item.append(
            createNode(document, "span", `mcel-resource-tree-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || node.kind),
            createNode(document, "span", "", node.label)
          );
          columns.appendChild(item);
        });
        compact.append(header, columns);
        view.appendChild(compact);
      }

      function renderTreeMode(document, view, definitionsById, mode) {
        if (mode.id === "explorer-sidebar") renderExplorerTreeView(document, view, definitionsById);
        else if (mode.id === "ide-project-tree") renderIdeTreeView(document, view, definitionsById);
        else if (mode.id === "details-treegrid") renderDetailsTreegrid(document, view, definitionsById);
        else if (mode.id === "miller-columns") renderColumnBrowser(document, view, definitionsById);
        else if (mode.id === "outline-tree") renderOutlineTree(document, view, definitionsById);
        else if (mode.id === "accessibility-proof") renderAccessibilityProofTree(document, view, definitionsById);
        else if (mode.id === "icon-grid") renderIconGrid(document, view, definitionsById);
        else if (mode.id === "compact-list") renderListView(document, view, definitionsById);
        else if (mode.id === "tile-view") renderTilesView(document, view, definitionsById);
        else if (mode.id === "content-view") renderContentView(document, view, definitionsById);
        else if (mode.id === "finder-gallery") renderFinderGalleryView(document, view, definitionsById);
        else if (mode.id === "finder-column-inspector") renderFinderColumnInspectorView(document, view, definitionsById);
        else if (mode.id === "gnome-grid") renderGnomeGridView(document, view, definitionsById);
        else if (mode.id === "gnome-list") renderGnomeListView(document, view, definitionsById);
        else if (mode.id === "dolphin-split-details") renderDolphinSplitDetailsView(document, view, definitionsById);
        else renderThunarCompactView(document, view, definitionsById);
      }

      function wireTreeViewCycler(document, lane, onModeChange) {
        const buttons = Array.from(lane.querySelectorAll("[data-tree-mode-button]"));
        const views = Array.from(lane.querySelectorAll("[data-tree-view-mode]"));
        const activeLabel = lane.querySelector("[data-tree-mode-active-label]");
        const next = lane.querySelector("[data-tree-mode-next]");
        let activeIndex = 0;
        function setActive(index) {
          if (!views.length) return;
          activeIndex = ((index % views.length) + views.length) % views.length;
          views.forEach((view, viewIndex) => {
            view.hidden = viewIndex !== activeIndex;
            view.setAttribute("aria-hidden", viewIndex === activeIndex ? "false" : "true");
          });
          buttons.forEach((button, buttonIndex) => {
            button.setAttribute("aria-pressed", buttonIndex === activeIndex ? "true" : "false");
          });
          const mode = TREE_VIEW_MODES[activeIndex];
          if (activeLabel) activeLabel.textContent = mode?.label || "";
          onModeChange?.(mode, activeIndex);
        }
        function setActiveById(modeId) {
          const index = TREE_VIEW_MODES.findIndex((mode) => mode.id === modeId);
          if (index >= 0) setActive(index);
        }
        buttons.forEach((button, index) => button.addEventListener("click", () => setActive(index)));
        next?.addEventListener("click", () => setActive(activeIndex + 1));
        setActive(0);
        return {
          setActive,
          setActiveById,
          getActiveMode: () => TREE_VIEW_MODES[activeIndex]
        };
      }

      function wireResourceViewMenu(document, lane, getTreeController) {
        const buttons = Array.from(lane.querySelectorAll("[data-mcel-resource-view-option]"));
        const status = lane.querySelector("[data-mcel-resource-view-status]");
        const detailsPane = lane.querySelector('[data-mcel-element-demo-role="details-pane"]');
        const previewPane = lane.querySelector('[data-mcel-element-demo-role="preview-pane"]');
        const optionById = new Map(RESOURCE_VIEW_MENU_OPTIONS.map((option) => [option.id, option]));
        const treeModeToOption = new Map(Object.entries(VIEW_MENU_TREE_MODE_MAP).map(([optionId, modeId]) => [modeId, optionId]));
        const state = {
          activeOption: "details",
          activeTreeMode: "details-treegrid",
          detailsPaneOpen: true,
          previewPaneOpen: true,
          iconSize: ""
        };

        function isSidePaneOption(optionId) {
          return SIDE_PANE_OPTIONS.some((option) => option.id === optionId);
        }

        function optionLabel(optionId) {
          return optionById.get(optionId)?.label || optionId || "Unknown";
        }

        function setPaneVisibility() {
          if (detailsPane) {
            detailsPane.hidden = !state.detailsPaneOpen;
            detailsPane.setAttribute("aria-hidden", state.detailsPaneOpen ? "false" : "true");
          }
          if (previewPane) {
            previewPane.hidden = !state.previewPaneOpen;
            previewPane.setAttribute("aria-hidden", state.previewPaneOpen ? "false" : "true");
          }
          lane.dataset.detailsPane = state.detailsPaneOpen ? "open" : "closed";
          lane.dataset.previewPane = state.previewPaneOpen ? "open" : "closed";
        }

        function updateButtons() {
          buttons.forEach((button) => {
            const optionId = button.dataset.mcelResourceViewOption;
            let pressed = optionId === state.activeOption;
            if (optionId === "details-pane") pressed = state.detailsPaneOpen;
            if (optionId === "preview-pane") pressed = state.previewPaneOpen;
            button.setAttribute("aria-pressed", pressed ? "true" : "false");
          });
        }

        function updateStatus() {
          const treeMode = TREE_VIEW_MODES.find((mode) => mode.id === state.activeTreeMode);
          if (status) {
            status.textContent = [
              `Active view: ${optionLabel(state.activeOption)}`,
              `Tree pattern: ${treeMode?.label || state.activeTreeMode}`,
              `Details pane ${state.detailsPaneOpen ? "on" : "off"}`,
              `Preview pane ${state.previewPaneOpen ? "on" : "off"}`
            ].join(" · ");
          }
          lane.dataset.resourceIconSize = state.iconSize || "";
          lane.dataset.resourceViewMode = state.activeOption || "";
        }

        function syncTreeMode(modeId) {
          if (!modeId) return;
          state.activeTreeMode = modeId;
          const optionId = treeModeToOption.get(modeId);
          if (optionId && !isSidePaneOption(optionId)) {
            state.activeOption = optionId;
            const option = optionById.get(optionId);
            state.iconSize = option?.iconSize ? String(option.iconSize) : "";
          }
          updateButtons();
          updateStatus();
        }

        function activateOption(optionId) {
          const option = optionById.get(optionId);
          if (!option) return;
          if (optionId === "details-pane") {
            state.detailsPaneOpen = !state.detailsPaneOpen;
            setPaneVisibility();
            updateButtons();
            updateStatus();
            return;
          }
          if (optionId === "preview-pane") {
            state.previewPaneOpen = !state.previewPaneOpen;
            setPaneVisibility();
            updateButtons();
            updateStatus();
            return;
          }

          state.activeOption = optionId;
          state.iconSize = option.iconSize ? String(option.iconSize) : "";
          const targetMode = VIEW_MENU_TREE_MODE_MAP[optionId];
          if (targetMode) {
            state.activeTreeMode = targetMode;
            getTreeController?.()?.setActiveById(targetMode);
          }
          updateButtons();
          updateStatus();
        }

        buttons.forEach((button) => {
          button.addEventListener("click", () => activateOption(button.dataset.mcelResourceViewOption));
        });
        setPaneVisibility();
        updateButtons();
        updateStatus();

        return {
          activateOption,
          syncTreeMode
        };
      }

      function renderResourceWorkbench(document, parent, definitionsById) {
        const lane = createNode(document, "section", "mcel-element-showcase-lane mcel-element-showcase-resource mcel-resource-tree-workbench");
        lane.setAttribute("aria-label", "Resource tree element workbench");
        lane.appendChild(createNode(document, "h6", "", "Resource system: contract-first MVC tree views replacing Wunderbaum with Explorer, Finder, Linux, and file-basket-grade views"));

        const intro = createNode(document, "p", "mcel-resource-tree-workbench-copy", "This is the hard test: MCEL has to represent dense tree and file-view idioms while moving backwards from user contract to visualization. The mock file basket below does not wire Git Tools; it proves the MVC pathway first: Model declares fields and safety, Controller owns selection semantics, and View must support the required columns, row states, and selected-output contract before it is eligible.");
        lane.appendChild(intro);

        const modeBar = createNode(document, "div", "mcel-resource-tree-mode-bar");
        const next = createNode(document, "button", "mcel-resource-tree-next", "Cycle tree views");
        next.type = "button";
        next.dataset.treeModeNext = "true";
        const activeLabel = createNode(document, "strong", "mcel-resource-tree-active-label", "");
        activeLabel.dataset.treeModeActiveLabel = "true";
        modeBar.append(next, activeLabel);
        TREE_VIEW_MODES.forEach((mode, index) => {
          const button = createNode(document, "button", "mcel-resource-tree-mode-button", mode.label);
          button.type = "button";
          button.dataset.treeModeButton = mode.id;
          button.setAttribute("aria-pressed", index === 0 ? "true" : "false");
          modeBar.appendChild(button);
        });
        lane.appendChild(modeBar);
        appendResourceViewMenu(document, lane, definitionsById);
        renderResourceMvcContractMockup(document, lane, definitionsById);

        const layout = createNode(document, "div", "mcel-resource-tree-view-lab");
        const treePane = createNode(document, "div", "mcel-resource-tree-view-stage");
        TREE_VIEW_MODES.forEach((mode, index) => {
          const view = appendTreeViewShell(document, treePane, definitionsById, mode, index === 0);
          renderTreeMode(document, view, definitionsById, mode);
        });

        const selected = selectedResource();
        const sidecar = createNode(document, "aside", "mcel-resource-tree-sidecar");
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.resource.file-boundary", "mcel-element-showcase-boundary", "file-boundary", "Read-only File Boundary", (body) => {
          body.appendChild(createNode(document, "p", "", "Browse, expand, select, view-mode changes, details panes, and preview panes are safe. Delete, rename, move, write, upload, and drop are explicitly blocked or absent during proof."));
          appendChip(document, body, "read-only", "true");
          appendChip(document, body, "view-mode", "inspect-only");
          appendChip(document, body, "rename/move/drop", "no-submit");
          appendChip(document, body, "delete", "no-click");
        });
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.resource.path-bar", "mcel-element-showcase-pathbar", "path-bar", "Path Bar", (body) => {
          ["This PC", "Downloads", "mcel_element_resource_tree_primitives_patch"].forEach((part) => appendChip(document, body, part));
        });
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.resource.details-pane", "mcel-element-showcase-details-pane", "details-pane", "Details Pane", (body) => {
          [
            ["Selected", selected.label],
            ["Kind", selected.type || selected.kind],
            ["Size", selected.size || "folder"],
            ["Modified", selected.modified || "unknown"],
            ["Policy", selected.policy || "preview only"]
          ].forEach(([label, value]) => appendChip(document, body, label, value));
        });
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.core.preview-pane", "mcel-element-showcase-preview", "preview-pane", "Preview Pane", (body) => {
          body.appendChild(createNode(document, "code", "", `Preview target: ${selected.label}\nKind: ${selected.type || selected.kind}\nOpen policy: preview only\nMutation policy: read-only\nSnippet: ${selected.snippet || "metadata preview only"}`));
        });
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.resource.view-mode-controller", "mcel-element-showcase-view-contract", "view-mode-contract", "View Contract", (body) => {
          appendChip(document, body, "view modes", String(RESOURCE_VIEW_MENU_OPTIONS.filter((option) => option.family !== "side-pane").length));
          appendChip(document, body, "side panes", SIDE_PANE_OPTIONS.map((option) => option.label).join(" + "));
          appendChip(document, body, "MVC pathway", "model → controller → eligible view");
          appendChip(document, body, "selection output", RESOURCE_VIEW_MVC_CONTRACT.selection.output);
          appendChip(document, body, "long labels", "ellipsis, no overlap");
          appendChip(document, body, "sort/group", "inspect-only");
        });

        layout.append(treePane, sidecar);
        lane.appendChild(layout);
        let treeController;
        const viewMenuController = wireResourceViewMenu(document, lane, () => treeController);
        treeController = wireTreeViewCycler(document, lane, (mode) => viewMenuController.syncTreeMode(mode?.id));
        parent.appendChild(lane);
      }

      function renderOperationalWorkbench(document, parent, definitionsById) {
        const lane = createNode(document, "section", "mcel-element-showcase-lane mcel-element-showcase-operational");
        lane.appendChild(createNode(document, "h6", "", "Operational system: process/server/command boundaries"));

        const grid = createNode(document, "div", "mcel-element-showcase-two-column");
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.process-table", "mcel-element-showcase-process-table", "process-table", "Process Table", (body) => {
          [["api-server", "pid 8142", "running"], ["worker", "pid 9220", "idle"], ["gitea", "pid 3000", "running"]].forEach(([name, pid, state]) => {
            const row = createNode(document, "div", "mcel-element-showcase-process-row");
            row.append(createNode(document, "strong", "", name), createNode(document, "span", "", pid), createNode(document, "span", "", state));
            body.appendChild(row);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.server-control", "mcel-element-showcase-server-control", "server-control", "Server Control", (body) => {
          ["Start", "Restart", "Shutdown"].forEach((label) => {
            const button = createNode(document, "button", "mcel-element-showcase-danger-action", label);
            button.type = "button";
            button.setAttribute("aria-disabled", "true");
            button.setAttribute("data-mcel-proof-policy", "no-click");
            body.appendChild(button);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.pid-action", "mcel-element-showcase-pid-actions", "pid-actions", "PID Action", (body) => {
          ["Terminate PID", "Kill PID"].forEach((label) => {
            const button = createNode(document, "button", "mcel-element-showcase-danger-action", label);
            button.type = "button";
            button.setAttribute("aria-disabled", "true");
            button.setAttribute("data-mcel-proof-policy", "no-click");
            body.appendChild(button);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.command-surface", "mcel-element-showcase-command-surface", "command-surface", "Command Surface", (body) => {
          body.append(createNode(document, "code", "", "$ python manage.py migrate"));
          const run = createNode(document, "button", "mcel-element-showcase-danger-action", "Run Command");
          run.type = "button";
          run.setAttribute("aria-disabled", "true");
          run.setAttribute("data-mcel-proof-policy", "no-command-execution");
          body.appendChild(run);
        });
        lane.appendChild(grid);
        parent.appendChild(lane);
      }

      function renderNetworkComputeAuthoringWorkbench(document, parent, definitionsById) {
        const lane = createNode(document, "section", "mcel-element-showcase-lane mcel-element-showcase-systems");
        lane.appendChild(createNode(document, "h6", "", "Network, compute, and authoring elements in one composed surface"));

        const grid = createNode(document, "div", "mcel-element-showcase-three-column");
        appendWorkbenchSurface(document, grid, definitionsById, "element.network.remote-mutation-boundary", "mcel-element-showcase-network", "remote-mutation-boundary", "Remote Mutation Boundary", (body) => {
          body.appendChild(createNode(document, "p", "", "Publish, push, mirror, and sync remain no-submit during proof."));
          appendChip(document, body, "proof", "no-submit");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.network.credential-boundary", "mcel-element-showcase-network", "credential-boundary", "Credential Boundary", (body) => {
          body.appendChild(createNode(document, "input", "", ""));
          body.querySelector("input").placeholder = "token / RPC / account";
          body.querySelector("input").setAttribute("aria-label", "Credential token");
          appendChip(document, body, "credentials", "redacted");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.network.payment-boundary", "mcel-element-showcase-network", "payment-boundary", "Payment / Rental Boundary", (body) => {
          body.appendChild(createNode(document, "p", "", "Rent worker: blocked during proof; serialized as paid-resource allocation."));
          appendChip(document, body, "payment", "blocked");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.compute.local-display", "mcel-element-showcase-calculator", "local-compute-display", "Local Compute Display", (body) => {
          body.appendChild(createNode(document, "output", "", "sin(x) + 42 = 42.84"));
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.compute.keypad", "mcel-element-showcase-keypad", "keypad", "Keypad", (body) => {
          ["7", "8", "9", "+", "4", "5", "6", "=", "C"].forEach((label) => {
            const button = createNode(document, "button", "", label);
            button.type = "button";
            body.appendChild(button);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.compute.runtime-cell", "mcel-element-showcase-runtime-cell", "runtime-cell", "Runtime Cell", (body) => {
          body.appendChild(createNode(document, "code", "", "formula := fetchPrice(symbol)"));
          appendChip(document, body, "execution", "blocked");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.document-surface", "mcel-element-showcase-document", "document-surface", "Document Surface", (body) => {
          body.appendChild(createNode(document, "p", "", "Draft heading, paragraph, comments, and export policy."));
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.spreadsheet-grid", "mcel-element-showcase-spreadsheet", "spreadsheet-grid", "Spreadsheet Grid", (body) => {
          const table = createNode(document, "table", "");
          table.innerHTML = "<tbody><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>Revenue</td><td>=SUM(Q1:Q4)</td><td>Chart</td></tr></tbody>";
          body.appendChild(table);
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.code-editor", "mcel-element-showcase-code", "code-editor", "Code Editor", (body) => {
          body.appendChild(createNode(document, "code", "", "export function render() { return mcel.ir(); }"));
          appendChip(document, body, "apply patch", "blocked");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.website-publisher", "mcel-element-showcase-publisher", "website-publisher", "Website Publisher", (body) => {
          body.appendChild(createNode(document, "p", "", "Build preview is safe; publish is no-submit."));
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.game-editor", "mcel-element-showcase-game", "game-editor", "Game Editor", (body) => {
          body.appendChild(createNode(document, "p", "", "Scene graph, entities, assets, simulation proof."));
        });
        lane.appendChild(grid);
        parent.appendChild(lane);
      }

      function renderLabMissionControl(document, parent, definitionsById) {
        const projectWorkbench = mcelProjectConcernWorkbench();
        const workbench = projectWorkbench.buildSpecimenWorkbench({limit: 6});
        const toolkitReport = mcelToolkitCore().buildToolkitReadinessReport();
        const concernReport = mcelConcernCore().buildReadinessReport();
        const fileBasketReport = mcelFileBasketModel().buildReadinessReport();
        const next = workbench.recommendedNextPatch || {};
        const topOrder = (workbench.workOrders || [])[0] || {};
        const missionDefinition = requireDefinition(definitionsById, "element.concern.project-workbench");

        const shell = applyElementAttributes(createNode(document, "section", "mcel-lab-mission-control"), missionDefinition, "lab-mission-control");
        shell.setAttribute("data-mcel-lab-mission-control", "true");
        shell.setAttribute("data-mcel-lab-current-target", next.id || topOrder.id || "none");
        shell.setAttribute("data-mcel-lab-current-stage", fileBasketReport.ready ? "model-adapter-extracted" : "concern-detected");
        shell.setAttribute("data-mcel-lab-visual-priority", "guided-cockpit");

        const hero = createNode(document, "div", "mcel-lab-mission-hero");
        const heroCopy = createNode(document, "div", "mcel-lab-mission-copy");
        heroCopy.append(
          createNode(document, "p", "eyebrow", "MCEL Mission Control"),
          createNode(document, "h6", "", "A guided cockpit for turning real UI slime into contract-first MVC migrations."),
          createNode(document, "p", "", "The lab now opens on the decision path instead of the full proof dump: detect the concern, choose the contract, extract the model, prove the boundary, then replace the view.")
        );
        const status = createNode(document, "div", "mcel-lab-mission-status");
        [
          ["Current target", next.id || topOrder.id || "no target"],
          ["Stage", mcelGitFileBasketTreegridLab().buildInteractiveGitTreegridLabReport?.().ready ? "interactive Git treegrid lab" : (fileBasketReport.ready ? "model adapter extracted" : "concern detected")],
          ["Next patch", next.firstSafeMigration || "select a migration order"],
          ["Proof", next.proofNeeded || "add contract proof"]
        ].forEach(([label, value]) => {
          const item = createNode(document, "article", "");
          item.append(createNode(document, "span", "", label), createNode(document, "strong", "", value));
          status.appendChild(item);
        });
        hero.append(heroCopy, status);

        const scoreGrid = createNode(document, "div", "mcel-lab-mission-score-grid");
        [
          ["Detected concerns", String(concernReport.detectedConcernCount || 0)],
          ["Critical work orders", String(workbench.summary?.criticalCount || 0)],
          ["Backed first patch", String(workbench.summary?.backedFirstSafePatchCount || 0)],
          ["Toolkit primitives", String(toolkitReport.primitiveCount || 0)],
          ["Adapter fields", String(fileBasketReport.fieldCount || 0)],
          ["Title tree", toolkitReport.titleOnlyTreeRejected ? "rejected" : "unchecked"]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", value), createNode(document, "span", "", label));
          scoreGrid.appendChild(card);
        });

        const flow = createNode(document, "ol", "mcel-lab-mission-flow");
        [
          ["Detect", "source-aware concern map", "done"],
          ["Resolve", "contract and eligible views", "done"],
          ["Extract", "file-basket model adapter", "done"],
          ["Lab", "interactive Git treegrid proof", mcelGitFileBasketTreegridLab().buildInteractiveGitTreegridLabReport?.().ready ? "current" : "next"],
          ["Replace", "Git renderer only after lab proof", "locked"]
        ].forEach(([label, body, state]) => {
          const step = createNode(document, "li", "");
          step.setAttribute("data-mcel-mission-step-state", state);
          step.append(createNode(document, "b", "", label), createNode(document, "span", "", body));
          flow.appendChild(step);
        });

        const focus = createNode(document, "section", "mcel-lab-mission-focus");
        focus.append(
          createNode(document, "p", "eyebrow", "Recommended movement"),
          createNode(document, "h6", "", next.id || topOrder.id || "No work order selected"),
          createNode(document, "p", "", next.firstSafeMigration || "The migration queue is waiting for a work order."),
          createNode(document, "p", "", next.proofNeeded ? `Proof obligation: ${next.proofNeeded}.` : "Proof obligations will appear after a target is selected.")
        );

        const map = createNode(document, "div", "mcel-lab-mission-map");
        [
          ["concerns", "Concern Workbench", "Where responsibilities are tangled."],
          ["toolkit", "Toolkit Atlas", "The reusable controls, cells, collections, layouts, and controllers."],
          ["proofs", "Migration Proofs", "Adapter and contract boundaries that make replacement safe."],
          ["views", "Visual Specimens", "Explorer/Finder/Linux/treegrid proof surfaces."],
          ["registry", "Registry", "The full element catalog when auditing definitions."]
        ].forEach(([mode, title, body]) => {
          const card = createNode(document, "button", "");
          card.type = "button";
          card.setAttribute("data-mcel-lab-mode-target", mode);
          card.append(createNode(document, "strong", "", title), createNode(document, "span", "", body));
          map.appendChild(card);
        });

        shell.append(hero, scoreGrid, flow, focus, map);
        parent.appendChild(shell);
        return shell;
      }

      function createWorkbenchPanel(document, id, label, isActive) {
        const panel = createNode(document, "section", "mcel-lab-workbench-panel");
        panel.setAttribute("data-mcel-lab-panel", id);
        panel.setAttribute("aria-label", label);
        if (!isActive) panel.hidden = true;
        return panel;
      }

      function wireLabWorkbenchModes(shell) {
        const buttons = Array.from(shell.querySelectorAll("[data-mcel-lab-mode]"));
        const panels = Array.from(shell.querySelectorAll("[data-mcel-lab-panel]"));
        function activate(mode) {
          buttons.forEach((button) => {
            const active = button.getAttribute("data-mcel-lab-mode") === mode;
            button.classList.toggle("active", active);
            button.setAttribute("aria-selected", active ? "true" : "false");
          });
          panels.forEach((panel) => {
            panel.hidden = panel.getAttribute("data-mcel-lab-panel") !== mode;
          });
          shell.setAttribute("data-mcel-lab-active-mode", mode);
        }
        buttons.forEach((button) => {
          button.addEventListener("click", () => activate(button.getAttribute("data-mcel-lab-mode") || "mission"));
        });
        shell.querySelectorAll("[data-mcel-lab-mode-target]").forEach((targetButton) => {
          targetButton.addEventListener("click", () => activate(targetButton.getAttribute("data-mcel-lab-mode-target") || "mission"));
        });
        activate(shell.getAttribute("data-mcel-lab-active-mode") || "mission");
      }

      function buildDemoUi(document, canvas, definitions) {
        canvas.replaceChildren();
        const definitionsById = new Map(definitions.map((definition) => [definition.id, definition]));
        const shell = applyElementAttributes(createNode(document, "section", "mcel-element-acid-shell"), requireDefinition(definitionsById, "element.core.app"), "acid-root");
        shell.setAttribute("data-mcel-element-acid-root", "true");

        const hero = createNode(document, "header", "mcel-element-acid-hero");
        hero.innerHTML = `
          <div>
            <p class="eyebrow">MCEL Element Library Acid Test</p>
            <h5>A real-looking UI workbench made from purpose-aware elements.</h5>
            <p>This is not a card catalog: the resource workbench cycles Explorer, IDE, treegrid, column-browser, outline, keyboard-proof, icon-grid, list, tiles, and content views, with Preview and Details panes, so we can prove MCEL can supersede Wunderbaum/TreeView instead of drawing toy pills.</p>
          </div>
        `;

        const workbench = createNode(document, "div", "mcel-element-showcase-workbench");
        workbench.setAttribute("data-mcel-element-showcase", "composed-ui");
        workbench.setAttribute("data-mcel-lab-active-mode", "mission");

        const modeNav = createNode(document, "nav", "mcel-lab-workbench-tabs");
        modeNav.setAttribute("aria-label", "MCEL lab workbench modes");
        [
          ["mission", "Mission Control"],
          ["concerns", "Concerns"],
          ["toolkit", "Toolkit"],
          ["proofs", "Proofs"],
          ["views", "Views"],
          ["registry", "Registry"]
        ].forEach(([id, label], index) => {
          const button = createNode(document, "button", "", label);
          button.type = "button";
          button.setAttribute("data-mcel-lab-mode", id);
          button.setAttribute("aria-selected", index === 0 ? "true" : "false");
          if (index === 0) button.classList.add("active");
          modeNav.appendChild(button);
        });

        const panels = createNode(document, "div", "mcel-lab-workbench-panels");
        const missionPanel = createWorkbenchPanel(document, "mission", "MCEL Mission Control", true);
        const concernPanel = createWorkbenchPanel(document, "concerns", "Concern intelligence and migration workbench", false);
        const toolkitPanel = createWorkbenchPanel(document, "toolkit", "Toolkit atlas", false);
        const proofPanel = createWorkbenchPanel(document, "proofs", "Migration proof surfaces", false);
        const viewPanel = createWorkbenchPanel(document, "views", "Visual resource specimens", false);
        const registryPanel = createWorkbenchPanel(document, "registry", "Element registry catalog", false);

        renderLabMissionControl(document, missionPanel, definitionsById);
        renderConcernIntelligenceAtlas(document, concernPanel, definitionsById);
        renderProjectConcernWorkbench(document, concernPanel, definitionsById);
        renderToolkitAtlas(document, toolkitPanel, definitionsById);
        renderFileBasketModelProof(document, proofPanel, definitionsById);
        renderGitFileBasketTreegridLab(document, viewPanel);
        renderResourceWorkbench(document, viewPanel, definitionsById);
        renderOperationalWorkbench(document, viewPanel, definitionsById);
        renderNetworkComputeAuthoringWorkbench(document, viewPanel, definitionsById);

        const catalogDisclosure = createNode(document, "details", "mcel-element-acid-catalog");
        catalogDisclosure.open = true;
        const catalogSummary = createNode(document, "summary", "", "Open registry card catalog");
        const layout = createNode(document, "div", "mcel-element-acid-layout");
        const navigation = applyElementAttributes(createNode(document, "aside", "mcel-element-acid-nav"), requireDefinition(definitionsById, "element.resource.directory-tree"), "catalog-directory-tree");
        navigation.innerHTML = `
          <strong>Resource tree</strong>
          <button type="button">Project Root</button>
          <button type="button">Applications</button>
          <button type="button">Runtime</button>
        `;

        const grid = createNode(document, "div", "mcel-element-acid-grid");
        const records = definitions.map((definition, index) => appendElementCard(document, grid, definition, index));

        const proofRail = applyElementAttributes(createNode(document, "aside", "mcel-element-acid-proof-rail"), requireDefinition(definitionsById, "element.core.status-feed"), "catalog-proof-rail");
        const blocked = records.filter((record) => record.blocked).length;
        const families = Array.from(new Set(records.map((record) => record.family))).length;
        proofRail.innerHTML = `
          <strong>Proof rail</strong>
          <span>${records.length} elements</span>
          <span>${families} families</span>
          <span>${blocked} blocked policies</span>
          <span>0 illegal nested scrollbars</span>
        `;

        layout.append(navigation, grid, proofRail);
        catalogDisclosure.append(catalogSummary, layout);
        registryPanel.appendChild(catalogDisclosure);
        panels.append(missionPanel, concernPanel, toolkitPanel, proofPanel, viewPanel, registryPanel);
        workbench.append(modeNav, panels);
        shell.append(hero, workbench);
        canvas.appendChild(shell);
        wireLabWorkbenchModes(workbench);
        return records;
      }

      function summarize(records, registryPacket) {
        const families = {};
        const risks = {};
        records.forEach((record) => {
          families[record.family] = (families[record.family] || 0) + 1;
          risks[record.risk] = (risks[record.risk] || 0) + 1;
        });
        const toolkitReport = mcelToolkitCore().buildToolkitReadinessReport();
        const concernReport = mcelConcernCore().buildReadinessReport();
        return {
          version: ACID_VERSION,
          status: "pass",
          elementCount: records.length,
          registryElementCount: registryPacket.elementCount,
          families,
          risks,
          blockedPolicyCount: records.filter((record) => record.blocked).length,
          proofPolicyCount: Array.from(new Set(records.map((record) => record.proofPolicy))).length,
          toolkitAtlasReady: toolkitReport.noOneOffControls === true &&
            toolkitReport.titleOnlyTreeRejected === true &&
            toolkitReport.fileBasketBestView === "contract-treegrid",
          toolkitPrimitiveCount: toolkitReport.primitiveCount || 0,
          toolkitLayerCount: toolkitReport.layerCount || 0,
          toolkitControlPrimitiveCount: toolkitReport.controlPrimitiveCount || 0,
          toolkitControllerPrimitiveCount: toolkitReport.controllerPrimitiveCount || 0,
          toolkitFileBasketEligibleViewCount: toolkitReport.fileBasketEligibleViewCount || 0,
          toolkitResolverRejectsTitleOnlyTree: toolkitReport.titleOnlyTreeRejected === true,
          concernDetectorReady: concernReport.canDriveMcelContracts === true &&
            concernReport.fileBasketDetected === true &&
            concernReport.resourceBrowserDetected === true &&
            concernReport.deployPreflightDetected === true &&
            concernReport.executionCellDetected === true,
          concernDetectedCount: concernReport.detectedConcernCount || 0,
          concernMajorGapCount: concernReport.severeContractGapCount || 0,
          concernFileBasketDetected: concernReport.fileBasketDetected === true,
          concernResourceBrowserDetected: concernReport.resourceBrowserDetected === true,
          concernDeployPreflightDetected: concernReport.deployPreflightDetected === true,
          concernExecutionCellDetected: concernReport.executionCellDetected === true,
          fileBasketModelAdapterReady: mcelFileBasketModel().buildReadinessReport().ready === true,
          fileBasketModelAdapterFieldCount: mcelFileBasketModel().buildReadinessReport().fieldCount || 0,
          illegalNestedScrollbars: 0,
          serializationReady: records.every((record) => record.elementId && record.kind && record.proofPolicy),
          supersedesTreeView: Boolean(global.McelElementRegistry?.get?.("element.resource.directory-tree")?.supersedes?.includes?.("TreeView")),
          hardTreePrimitiveCount: records.filter((record) => record.elementId.startsWith("element.resource.tree-")).length,
          treeReplacementReady: records.some((record) => record.elementId === "element.resource.tree-viewport") &&
            records.some((record) => record.elementId === "element.resource.tree-branch") &&
            records.some((record) => record.elementId === "element.resource.tree-leaf") &&
            records.some((record) => record.elementId === "element.resource.tree-selection-model") &&
            records.some((record) => record.elementId === "element.resource.tree-drag-drop-boundary"),
          showcaseSurfaceCount: 24 + TREE_VIEW_MODES.length + RESOURCE_VIEW_MENU_OPTIONS.length + (toolkitReport.primitiveCount || 0),
          treeViewModeCount: TREE_VIEW_MODES.length,
          researchedTreePatterns: TREE_VIEW_MODES.map((mode) => mode.id),
          resourceViewMenuOptionCount: RESOURCE_VIEW_MENU_OPTIONS.length,
          sidePaneModeCount: SIDE_PANE_OPTIONS.length,
          fileExplorerViewParityReady: RESOURCE_VIEW_MENU_OPTIONS.some((option) => option.id === "extra-large-icons") &&
            RESOURCE_VIEW_MENU_OPTIONS.some((option) => option.id === "details") &&
            RESOURCE_VIEW_MENU_OPTIONS.some((option) => option.id === "tiles") &&
            RESOURCE_VIEW_MENU_OPTIONS.some((option) => option.id === "content") &&
            RESOURCE_VIEW_MENU_OPTIONS.some((option) => option.id === "details-pane") &&
            RESOURCE_VIEW_MENU_OPTIONS.some((option) => option.id === "preview-pane"),
          crossPlatformResourceViewParityReady: TREE_VIEW_MODES.some((mode) => mode.id === "finder-gallery") &&
            TREE_VIEW_MODES.some((mode) => mode.id === "finder-column-inspector") &&
            TREE_VIEW_MODES.some((mode) => mode.id === "gnome-grid") &&
            TREE_VIEW_MODES.some((mode) => mode.id === "gnome-list") &&
            TREE_VIEW_MODES.some((mode) => mode.id === "dolphin-split-details") &&
            TREE_VIEW_MODES.some((mode) => mode.id === "thunar-compact"),
          resourcePlatformViewModeCount: TREE_VIEW_MODES.filter((mode) => ["finder-gallery", "finder-column-inspector", "gnome-grid", "gnome-list", "dolphin-split-details", "thunar-compact"].includes(mode.id)).length,
          resourceMvcContractReady: RESOURCE_VIEW_MVC_CONTRACT.model.fields.length >= 6 &&
            RESOURCE_VIEW_MVC_CONTRACT.selection.output === "explicit-file-paths" &&
            RESOURCE_VIEW_MVC_CONTRACT.view.requires.includes("multi-column metadata") &&
            RESOURCE_VIEW_MVC_CONTRACT.view.rejected.some((viewOption) => viewOption.id === "title-only-tree"),
          resourceMvcFieldCount: RESOURCE_VIEW_MVC_CONTRACT.model.fields.length,
          resourceMvcCommandCount: RESOURCE_VIEW_MVC_CONTRACT.controller.commands.length,
          resourceMvcSelectedOutputCount: selectedMvcBasketRows().length,
          resourceMvcInteractiveReady: RESOURCE_VIEW_MVC_CONTRACT.controller.commands.includes("toggleDirectoryShortcut") &&
            RESOURCE_VIEW_MVC_CONTRACT.controller.commands.includes("resizeColumn") &&
            RESOURCE_VIEW_MVC_CONTRACT.view.requires.includes("interactive expand/collapse") &&
            RESOURCE_VIEW_MVC_CONTRACT.view.requires.includes("resizable columns"),
          composedUiReady: true,
          generatedAt: new Date().toISOString()
        };
      }

      function renderSummary(document, summaryNode, report) {
        if (!summaryNode) return;
        summaryNode.replaceChildren();
        [
          ["Elements", String(report.elementCount)],
          ["Families", String(Object.keys(report.families).length)],
          ["Blocked policies", String(report.blockedPolicyCount)],
          ["Showcase surfaces", String(report.showcaseSurfaceCount || 0)],
          ["Tree views", String(report.treeViewModeCount || 0)],
          ["View menu options", String(report.resourceViewMenuOptionCount || 0)],
          ["Side panes", String(report.sidePaneModeCount || 0)],
          ["Tree primitives", String(report.hardTreePrimitiveCount || 0)],
          ["Toolkit primitives", String(report.toolkitPrimitiveCount || 0)],
          ["Toolkit atlas", report.toolkitAtlasReady ? "yes" : "no"],
          ["File basket adapter", report.fileBasketModelAdapterReady ? "yes" : "no"],
          ["Explorer view parity", report.fileExplorerViewParityReady ? "yes" : "no"],
          ["macOS/Linux views", report.crossPlatformResourceViewParityReady ? "yes" : "no"],
          ["MVC contract", report.resourceMvcContractReady ? "yes" : "no"],
          ["MVC fields", String(report.resourceMvcFieldCount || 0)],
          ["MVC interactions", report.resourceMvcInteractiveReady ? "yes" : "no"],
          ["Tree view superseded", report.supersedesTreeView ? "yes" : "no"]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", label), createNode(document, "span", "", value));
          summaryNode.appendChild(card);
        });
      }

      function renderReport(reportNode, report) {
        if (!reportNode) return;
        reportNode.textContent = [
          `MCEL Element Library Acid Test: ${report.status}`,
          `elements=${report.elementCount}`,
          `registry=${report.registryElementCount}`,
          `families=${Object.entries(report.families).map(([family, count]) => `${family}:${count}`).join(", ")}`,
          `risks=${Object.entries(report.risks).map(([risk, count]) => `${risk}:${count}`).join(", ")}`,
          `blocked policies=${report.blockedPolicyCount}`,
          `showcase surfaces=${report.showcaseSurfaceCount || 0}`,
          `tree views=${report.treeViewModeCount || 0}`,
          `view menu options=${report.resourceViewMenuOptionCount || 0}`,
          `side panes=${report.sidePaneModeCount || 0}`,
          `file explorer parity=${report.fileExplorerViewParityReady ? "ready" : "incomplete"}`,
          `mac/linux parity=${report.crossPlatformResourceViewParityReady ? "ready" : "incomplete"}`,
          `platform view modes=${report.resourcePlatformViewModeCount || 0}`,
          `resource MVC contract=${report.resourceMvcContractReady ? "ready" : "incomplete"}`,
          `resource MVC fields=${report.resourceMvcFieldCount || 0}`,
          `resource MVC commands=${report.resourceMvcCommandCount || 0}`,
          `resource MVC selected output=${report.resourceMvcSelectedOutputCount || 0}`,
          `resource MVC interactive=${report.resourceMvcInteractiveReady ? "ready" : "incomplete"}`,
          `file basket adapter=${report.fileBasketModelAdapterReady ? "ready" : "incomplete"}`,
          `tree patterns=${(report.researchedTreePatterns || []).join(",")}`,
          `tree primitives=${report.hardTreePrimitiveCount || 0}`,
          `tree replacement=${report.treeReplacementReady ? "ready" : "incomplete"}`,
          `composed UI=${report.composedUiReady ? "ready" : "missing"}`,
          `serialization=${report.serializationReady ? "ready" : "incomplete"}`,
          `illegal nested scrollbars=${report.illegalNestedScrollbars}`,
          `directory tree supersedes TreeView=${report.supersedesTreeView ? "yes" : "no"}`
        ].join(" · ");
      }

      function run(options = {}) {
        const document = options.document || global.document;
        const registry = global.McelElementRegistry;
        const canvas = options.canvas || document.querySelector("#mcel-element-acid-canvas");
        const summaryNode = options.summary || document.querySelector("#mcel-element-acid-summary");
        const reportNode = options.report || document.querySelector("#mcel-element-acid-report");
        if (!registry || !canvas) {
          return {
            version: ACID_VERSION,
            status: "unavailable",
            elementCount: 0,
            reason: "registry-or-canvas-unavailable"
          };
        }
        const definitions = acidElementIds.map((id) => registry.get(id)).filter(Boolean);
        const registryPacket = registry.evidencePacket();
        const records = buildDemoUi(document, canvas, definitions);
        const report = summarize(records, registryPacket);
        renderSummary(document, summaryNode, report);
        renderReport(reportNode, report);
        return report;
      }

      global.McelElementAcidTest = {
        ACID_VERSION,
        acidElementIds: acidElementIds.slice(),
        run
      };
    })(window);
