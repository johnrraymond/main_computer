    (function (global) {
      "use strict";

      const WORKBENCH_VERSION = "0.1.0";

      const GAP_WEIGHTS = {severe: 90, major: 72, moderate: 48, minor: 24, unknown: 12};
      const FAMILY_WEIGHTS = {safety: 12, workflow: 9, compute: 7, resource: 7, unknown: 3};

      const PROJECT_CONTRACTS = {
        "pattern.file-basket": {
          id: "pattern.file-basket",
          label: "File Basket Contract",
          purpose: "Choose exact repo-relative file paths for an operation while hierarchy, typed metadata, blocked rows, and selected output stay truthful.",
          fields: [
            {id: "path", label: "Path", type: "path", primary: true},
            {id: "status", label: "Status", type: "enum"},
            {id: "bucket", label: "Bucket", type: "enum"},
            {id: "risk", label: "Risk", type: "risk"},
            {id: "reason", label: "Reason", type: "text"},
            {id: "modified", label: "Modified", type: "datetime"}
          ],
          requiredCapabilities: ["hierarchy", "multi-column-fields", "typed-cells", "tri-state-selection", "blocked-visible-not-selectable", "selected-output-proof", "keyboard-navigation"],
          selectionContract: "hierarchical-explicit-files-with-directory-shortcuts",
          safetyContract: ["blocked rows visible but not selectable", "selected file paths are the source of truth", "destructive operation requires preview"],
          primaryViews: ["details-tree", "treegrid", "compact-audit-list"],
          rejectedViews: ["title-only-tree", "plain-tree-primary", "icon-grid-primary"],
          proofObligations: ["selected output equals explicit file paths", "blocked rows cannot enter selection", "folder selection derives mixed state"]
        },
        "pattern.resource-browser": {
          id: "pattern.resource-browser",
          label: "Resource Browser Contract",
          purpose: "Browse, find, inspect, preview, and open hierarchical resources without the rendering widget owning navigation policy.",
          fields: [
            {id: "name", label: "Name", type: "name", primary: true},
            {id: "path", label: "Path", type: "path"},
            {id: "kind", label: "Kind", type: "enum"},
            {id: "size", label: "Size", type: "size"},
            {id: "modified", label: "Modified", type: "datetime"}
          ],
          requiredCapabilities: ["hierarchy", "path-context", "preview", "keyboard-navigation", "single-selection", "typed-cells"],
          selectionContract: "single-resource-selection-with-open-preview-actions",
          safetyContract: ["mutation requires explicit command policy", "preview isolates unsupported or unsafe content"],
          primaryViews: ["column-browser", "details-list", "treegrid", "icon-grid-with-preview"],
          rejectedViews: ["title-only-tree", "widget-owned-navigation"],
          proofObligations: ["selecting an entry updates preview", "opening a directory changes navigation state", "keyboard Enter follows open policy"]
        },
        "pattern.safety-preflight": {
          id: "pattern.safety-preflight",
          label: "Safety Preflight Contract",
          purpose: "Review operation plan, warnings, command preview, and acknowledgement gates before a risky operation can run.",
          fields: [
            {id: "operation", label: "Operation", type: "text", primary: true},
            {id: "target", label: "Target", type: "resource"},
            {id: "warning", label: "Warning", type: "warning"},
            {id: "command", label: "Command", type: "command"},
            {id: "acknowledged", label: "Acknowledged", type: "boolean"}
          ],
          requiredCapabilities: ["warning-list", "command-preview", "acknowledgement-gate", "disabled-until-valid", "audit-proof"],
          selectionContract: "acknowledge-to-enable-confirm",
          safetyContract: ["dry-run before mutation", "warnings require explicit acknowledgement", "confirm button derives from controller"],
          primaryViews: ["preflight-panel", "inspector-pane", "command-bar"],
          rejectedViews: ["bare-confirm-button", "render-owned-policy"],
          proofObligations: ["confirm disabled until required acknowledgement", "warnings displayed before action", "dry-run result is visible"]
        },
        "pattern.change-review": {
          id: "pattern.change-review",
          label: "Change Review Contract",
          purpose: "Scan edits, diffs, files, and commits before choosing a change to inspect or apply.",
          fields: [
            {id: "subject", label: "Subject", type: "text", primary: true},
            {id: "files", label: "Files", type: "path-list"},
            {id: "diffstat", label: "Diff", type: "diffstat"},
            {id: "author", label: "Author", type: "text"},
            {id: "time", label: "Time", type: "datetime"}
          ],
          requiredCapabilities: ["select-change", "preview", "diff-stat", "multi-file-summary", "keyboard-navigation"],
          selectionContract: "single-change-preview-selection",
          safetyContract: ["apply action separated from preview selection"],
          primaryViews: ["review-list", "data-table-with-preview", "timeline"],
          rejectedViews: ["flat-buttons-only", "file-path-text-blob"],
          proofObligations: ["selected change drives preview", "file paths remain structured", "apply command is explicit"]
        },
        "pattern.execution-cell": {
          id: "pattern.execution-cell",
          label: "Execution Cell Contract",
          purpose: "Run typed user requests while routing, readiness, output variants, progress, and recovery stay visible.",
          fields: [
            {id: "cellId", label: "Cell", type: "identity", primary: true},
            {id: "type", label: "Type", type: "enum"},
            {id: "source", label: "Source", type: "code"},
            {id: "status", label: "Status", type: "status"},
            {id: "output", label: "Output", type: "rich-output"}
          ],
          requiredCapabilities: ["command-execution", "status", "output-rendering", "variant-selection", "recovery"],
          selectionContract: "active-cell-and-output-variant",
          safetyContract: ["execution readiness controlled by controller", "output rendering isolates content type"],
          primaryViews: ["execution-cell", "notebook-row", "output-pane"],
          rejectedViews: ["untyped-message-blob", "button-owned-execution"],
          proofObligations: ["run state transitions are explicit", "output variant selection is controller-owned", "errors are visible"]
        },
        "pattern.worker-routing": {
          id: "pattern.worker-routing",
          label: "Worker Routing Contract",
          purpose: "Choose local or remote execution from readiness, capacity, queue, and user policy instead of hiding routing in modal glue.",
          fields: [
            {id: "route", label: "Route", type: "enum", primary: true},
            {id: "capacity", label: "Capacity", type: "status"},
            {id: "readiness", label: "Readiness", type: "status"},
            {id: "pendingRequest", label: "Pending request", type: "identity"}
          ],
          requiredCapabilities: ["choice-cards", "capacity-status", "readiness-gate", "policy-explanation"],
          selectionContract: "choose-execution-route",
          safetyContract: ["remote route is explicit", "capacity status is shown before choice"],
          primaryViews: ["routing-modal", "choice-panel", "status-cards"],
          rejectedViews: ["ambiguous-spinner", "hidden-routing-policy"],
          proofObligations: ["chosen route is recorded", "readiness/capacity are visible", "cancel path is available"]
        },
        "pattern.output-renderer": {
          id: "pattern.output-renderer",
          label: "Output Renderer Contract",
          purpose: "Render heterogeneous output parts as typed cells with copy/export/preview affordances instead of text soup.",
          fields: [
            {id: "partType", label: "Part type", type: "enum", primary: true},
            {id: "content", label: "Content", type: "rich-output"},
            {id: "copyState", label: "Copy", type: "action-state"},
            {id: "error", label: "Error", type: "error"}
          ],
          requiredCapabilities: ["typed-output-parts", "copy-action", "tables", "code", "error-boundary"],
          selectionContract: "output-part-focus-selection",
          safetyContract: ["unsafe output rendered through explicit boundary"],
          primaryViews: ["output-cell", "typed-output-stack", "preview-pane"],
          rejectedViews: ["innerhtml-dump", "untyped-output-string"],
          proofObligations: ["each output part has a renderer", "copy/export targets typed content", "errors do not erase output"]
        },
        "pattern.process-table": {
          id: "pattern.process-table",
          label: "Process Table Contract",
          purpose: "Observe runtime processes/services with typed status columns and gated actions.",
          fields: [
            {id: "name", label: "Name", type: "name", primary: true},
            {id: "status", label: "Status", type: "status"},
            {id: "pid", label: "PID", type: "number"},
            {id: "cpu", label: "CPU", type: "metric"},
            {id: "memory", label: "Memory", type: "metric"},
            {id: "action", label: "Action", type: "action"}
          ],
          requiredCapabilities: ["multi-column-fields", "status-scan", "sort", "filter", "action-policy"],
          selectionContract: "row-action-policy",
          safetyContract: ["kill/stop actions require explicit controller policy"],
          primaryViews: ["data-table", "status-board"],
          rejectedViews: ["card-only-primary", "action-buttons-without-policy"],
          proofObligations: ["status remains typed", "danger actions are gated", "sort/filter preserve action policy"]
        }
      };

      const WORK_ORDER_TEMPLATES = {
        "concern.file-basket": {
          title: "Replace file basket slime with a contract treegrid workbench",
          currentFailure: [
            "Typed file fields are present but can be flattened into a single title string.",
            "Selection output is scraped from widget-specific tree state instead of derived by a controller.",
            "Blocked/selectable policy exists but is not declared as a first-class safety contract."
          ],
          migrationPhases: [
            "Extract FileBasketModel adapter from candidate groups and file metadata.",
            "Extract FileBasketSelectionController while preserving the current DOM tree.",
            "Add selected-output and blocked-row contract tests.",
            "Swap the renderer to the MCEL treegrid only after model/controller proof is stable."
          ],
          firstSafeMigration: [
            "Create a pure model adapter that returns fields, identity, hierarchy, selectable state, and blocked reason.",
            "Keep the existing view untouched.",
            "Add tests that selected output remains explicit repo-relative file paths."
          ],
          testsNeeded: [
            "selecting a directory selects only selectable descendants",
            "blocked rows remain visible and never enter selected output",
            "title-only tree is rejected by the view resolver"
          ]
        },
        "concern.resource-browser": {
          title: "Extract a resource-browser contract from File Explorer",
          currentFailure: [
            "A render function owns loading, tree widget creation, preview, open behavior, fallback rendering, and status text.",
            "Fields such as path, kind, size, and modified time are not declared as a view contract.",
            "The view is chosen before the user task and required capabilities are known."
          ],
          migrationPhases: [
            "Extract ResourceBrowserModel from filesystem entry metadata.",
            "Extract ResourceBrowserController for selection, preview, open, and keyboard Enter.",
            "Add contract tests while preserving current rendering.",
            "Offer details/list/column-browser/icon-grid through the view resolver."
          ],
          firstSafeMigration: [
            "Add ResourceBrowserModel adapter and contract assertions.",
            "Keep the Wunderbaum/fallback rendering path unchanged.",
            "Route preview/open actions through a controller facade without changing behavior."
          ],
          testsNeeded: [
            "selecting an entry updates preview",
            "opening a directory follows the navigation contract",
            "resource fields stay structured instead of title text only"
          ]
        },
        "concern.deploy-preflight": {
          title: "Turn deploy preflight into a safety contract",
          currentFailure: [
            "Warning extraction, acknowledgement policy, row rendering, and confirm enablement are coupled.",
            "A risky operation can only be reasoned about by reading DOM update code.",
            "The command preview is not backed by a reusable safety gate controller."
          ],
          migrationPhases: [
            "Extract SafetyPreflightModel from dry-run result, warnings, command, and target.",
            "Extract SafetyGateController for acknowledgement and confirm enablement.",
            "Add proof tests for warning/acknowledgement/disabled states.",
            "Render with standard preflight panel and command bar primitives."
          ],
          firstSafeMigration: [
            "Create a pure preflight model builder from existing dry-run payloads.",
            "Keep the current modal HTML.",
            "Move confirm-enabled calculation into a controller helper with tests."
          ],
          testsNeeded: [
            "warnings appear before confirmation",
            "confirm is disabled until acknowledgement when required",
            "dry-run command preview is preserved"
          ]
        },
        "concern.change-review-list": {
          title: "Make change review a structured preview list",
          currentFailure: [
            "Commit/edit metadata is rendered as ad hoc list buttons.",
            "File paths and diff summaries do not have typed cells.",
            "Preview selection and apply/open commands are not separated by contract."
          ],
          migrationPhases: [
            "Extract ChangeReviewModel from edit/commit data.",
            "Add ChangeReviewSelectionController.",
            "Add diffstat/path-cell rendering through toolkit primitives.",
            "Gate apply/open commands separately from preview selection."
          ],
          firstSafeMigration: [
            "Add a pure change-review model adapter.",
            "Preserve current buttons.",
            "Add tests that selected change drives preview and file paths remain structured."
          ],
          testsNeeded: [
            "selected change drives preview",
            "file paths stay structured",
            "apply/open commands are explicit actions"
          ]
        },
        "concern.execution-cell": {
          title: "Separate execution cell model/controller/view",
          currentFailure: [
            "Cell evaluation, routing, output application, variants, and rendering live across large mixed functions.",
            "Execution readiness and output state transitions are not visible as a contract.",
            "The renderer can become the owner of execution semantics."
          ],
          migrationPhases: [
            "Extract ExecutionCellModel and run-state transitions.",
            "Extract ExecutionController for local/remote route and evaluate commands.",
            "Add output variant controller tests.",
            "Move output display to typed output renderers."
          ],
          firstSafeMigration: [
            "Add a pure ExecutionCellModel snapshot helper.",
            "Keep chat rendering unchanged.",
            "Add tests for run state, output variants, and error visibility."
          ],
          testsNeeded: [
            "run state transitions are explicit",
            "output variants are controller-owned",
            "error output does not erase prior state"
          ]
        },
        "concern.worker-routing": {
          title: "Make worker routing policy visible",
          currentFailure: [
            "Routing choice, capacity, readiness, and modal UI are intertwined.",
            "Local versus remote execution appears as a UI modal instead of a policy contract.",
            "The selected route is not represented as a reusable controller outcome."
          ],
          migrationPhases: [
            "Extract WorkerRoutingModel from capacity and readiness snapshots.",
            "Extract route-choice controller.",
            "Add proof for route selection/cancel/readiness states.",
            "Render with standard choice cards and status cells."
          ],
          firstSafeMigration: [
            "Add a pure worker-routing model snapshot.",
            "Preserve current modal.",
            "Add tests that route choice records local/remote/cancel outcomes."
          ],
          testsNeeded: [
            "capacity and readiness are shown",
            "chosen route is recorded",
            "cancel path remains available"
          ]
        },
        "concern.output-renderer": {
          title: "Replace output soup with typed output parts",
          currentFailure: [
            "Output rendering, copy/export, rich tables, errors, and preview concerns are spread through renderer glue.",
            "Output parts lack a declared renderer contract.",
            "Unsafe or unsupported output is not consistently isolated by a reusable boundary."
          ],
          migrationPhases: [
            "Extract OutputPartModel for text/code/table/error/media parts.",
            "Add OutputRendererController for copy/export/focus.",
            "Add typed renderer registry.",
            "Render output stack with standard cells and error boundary."
          ],
          firstSafeMigration: [
            "Create a pure output-part normalization helper.",
            "Keep current output DOM.",
            "Add tests for table/code/error part classification."
          ],
          testsNeeded: [
            "each output part receives a typed renderer",
            "copy/export targets typed content",
            "renderer errors are isolated"
          ]
        },
        "concern.process-table": {
          title: "Convert process/service status to a typed process table",
          currentFailure: [
            "Runtime status, metrics, and danger actions can appear as card/list glue.",
            "Action safety policy is not declared as a controller contract.",
            "Status scanning is hard to sort, filter, or compare when not tabular."
          ],
          migrationPhases: [
            "Extract ProcessTableModel from service/process rows.",
            "Extract ProcessActionController for stop/kill/restart gates.",
            "Add typed status/metric/action cells.",
            "Render with data-table/status-board primitives."
          ],
          firstSafeMigration: [
            "Add a pure process row model helper.",
            "Preserve current UI.",
            "Add tests that danger actions are derived from policy."
          ],
          testsNeeded: [
            "status remains typed",
            "danger actions are gated",
            "sort/filter preserve row action policy"
          ]
        }
      };

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function normalizePath(path) {
        return String(path || "").replace(/\\/g, "/").replace(/^\/+/, "");
      }

      function asArray(value) {
        return Array.isArray(value) ? value.filter(Boolean) : [];
      }

      function gapWeight(gap) {
        return GAP_WEIGHTS[gap] || GAP_WEIGHTS.unknown;
      }

      function familyWeight(family) {
        return FAMILY_WEIGHTS[family] || FAMILY_WEIGHTS.unknown;
      }

      function contractForConcern(concern) {
        const explicit = concern?.recommendedContract;
        if (explicit && PROJECT_CONTRACTS[explicit]) return PROJECT_CONTRACTS[explicit];
        const contract = concern?.inferredMvcSplit?.contract;
        if (contract && PROJECT_CONTRACTS[contract]) return PROJECT_CONTRACTS[contract];
        return PROJECT_CONTRACTS["pattern." + String(concern?.id || "").replace(/^concern\./, "")] || null;
      }

      function inferAppId(path) {
        const normalized = normalizePath(path).toLowerCase();
        if (normalized.includes("file-explorer")) return "file-explorer";
        if (normalized.includes("website-builder")) return "website-builder";
        if (normalized.includes("chat-console")) return "chat-console";
        if (normalized.includes("worker")) return "worker";
        if (normalized.includes("task-manager")) return "task-manager";
        if (normalized.includes("git-tools")) return "git-tools";
        return normalized.split("/").pop()?.replace(/\.js$/, "") || "project";
      }

      function inferSurfaceId(concern) {
        const app = inferAppId(concern?.file || "");
        const concernPart = String(concern?.id || "concern.unknown").replace(/^concern\./, "");
        return `${app}.${concernPart}`;
      }

      function computePriority(concern, contract) {
        const confidence = Math.round(Number(concern?.confidence || 0) * 30);
        const slime = asArray(concern?.slimeSignals).length * 4;
        const roles = asArray(concern?.roles);
        const roleCompleteness = ["model", "controller", "view-gap"].filter((role) => roles.includes(role)).length * 4;
        const safety = roles.includes("safety") || asArray(contract?.safetyContract).length ? 8 : 0;
        const toolkit = Math.min(12, asArray(concern?.recommendedToolkit).length);
        return gapWeight(concern?.contractGap) + familyWeight(concern?.family) + confidence + slime + roleCompleteness + safety + toolkit;
      }

      function priorityBand(score) {
        if (score >= 135) return "critical";
        if (score >= 110) return "high";
        if (score >= 84) return "medium";
        return "watch";
      }

      function reasonFromConcern(concern, contract) {
        const signals = asArray(concern?.slimeSignals);
        const capabilities = asArray(contract?.requiredCapabilities);
        const bits = [];
        if (concern?.contractGap) bits.push(`${concern.contractGap} contract gap`);
        if (signals.length) bits.push(`slime signals: ${signals.join(", ")}`);
        if (capabilities.length) bits.push(`requires ${capabilities.slice(0, 4).join(", ")}`);
        return bits.join("; ");
      }

      function viewResolution(contract) {
        const toolkit = global.McelToolkitCore;
        if (!contract) return [];
        const toolkitPatterns = toolkit?.CONTRACT_PATTERNS || {};
        const lookupKey = String(contract.id || "").replace(/^pattern\./, "").replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        const toolkitContract = toolkitPatterns[lookupKey];
        if (toolkit?.resolveViews && toolkitContract) {
          return toolkit.resolveViews(toolkitContract).slice(0, 5).map((view) => ({
            id: view.id,
            label: view.label,
            eligible: !!view.eligible,
            score: view.score,
            reason: view.reason
          }));
        }
        return asArray(contract.primaryViews).map((viewId, index) => ({
          id: viewId,
          label: viewId.replace(/-/g, " "),
          eligible: true,
          score: Math.max(50, 94 - index * 8),
          reason: `Primary view for ${contract.label}; it is listed by the project contract.`
        })).concat(asArray(contract.rejectedViews).slice(0, 3).map((viewId) => ({
          id: viewId,
          label: viewId.replace(/-/g, " "),
          eligible: false,
          score: 0,
          reason: `Rejected by ${contract.label}; it cannot satisfy required user promises.`
        })));
      }


      function implementationStatusFor(contractId) {
        if (contractId === "pattern.file-basket" && global.McelFileBasketModel?.buildReadinessReport) {
          const report = global.McelFileBasketModel.buildReadinessReport();
          return {
            status: report.ready ? "adapter-ready" : "adapter-incomplete",
            label: report.ready ? "FileBasketModel adapter available" : "FileBasketModel adapter incomplete",
            firstSafePatchBacked: report.ready === true,
            module: "McelFileBasketModel",
            proof: [
              `fields=${report.fieldCount || 0}`,
              `rows=${report.rowCount || 0}`,
              `selectable=${report.selectableCount || 0}`,
              `blocked=${report.blockedCount || 0}`,
              report.titleOnlyTreeRejected ? "title-only tree rejected" : "title-only tree not rejected"
            ]
          };
        }
        return {
          status: "not-started",
          label: "No implementation adapter registered yet",
          firstSafePatchBacked: false,
          module: "",
          proof: []
        };
      }

      function buildWorkOrder(concern, options = {}) {
        const contract = contractForConcern(concern);
        const template = WORK_ORDER_TEMPLATES[concern?.id] || {};
        const priorityScore = computePriority(concern, contract);
        const surfaceId = inferSurfaceId(concern);
        const lineEvidence = asArray(concern?.ranges).map((range) => ({
          role: range.role,
          label: range.label,
          file: concern.file,
          startLine: range.startLine,
          endLine: range.endLine,
          anchorLine: range.anchorLine,
          excerpt: range.excerpt
        }));
        return {
          id: surfaceId,
          concernId: concern.id,
          label: concern.label,
          title: template.title || `Replace ${concern.label} with an MCEL contract`,
          app: inferAppId(concern.file),
          sourceFile: concern.file,
          priorityScore,
          priority: priorityBand(priorityScore),
          confidence: concern.confidence,
          contractGap: concern.contractGap,
          boundaryHealth: concern.boundaryHealth,
          currentFailure: asArray(template.currentFailure).concat(asArray(concern.slimeSignals).map((signal) => `Detected ${signal} in the current code shape.`)),
          reason: reasonFromConcern(concern, contract),
          targetContract: contract ? contract.id : concern.recommendedContract || "pattern.unresolved",
          contractLabel: contract?.label || concern.recommendedContract || "Unresolved contract",
          contractFields: clone(contract?.fields || []),
          requiredCapabilities: clone(contract?.requiredCapabilities || []),
          selectionContract: contract?.selectionContract || "",
          safetyContract: clone(contract?.safetyContract || []),
          mvcSplit: clone(concern.inferredMvcSplit || {}),
          requiredToolkit: clone(concern.recommendedToolkit || []),
          eligibleViews: viewResolution(contract),
          rejectedViews: clone(contract?.rejectedViews || []),
          proofObligations: clone(contract?.proofObligations || []),
          firstSafeMigration: clone(template.firstSafeMigration || []),
          implementationStatus: implementationStatusFor(contract?.id || concern.recommendedContract || ""),
          migrationPhases: clone(template.migrationPhases || []),
          testsNeeded: clone(template.testsNeeded || []),
          lineEvidence,
          sourceConcern: options.includeSourceConcern ? clone(concern) : undefined
        };
      }

      function groupBy(items, getter) {
        return items.reduce((accumulator, item) => {
          const key = getter(item) || "unknown";
          accumulator[key] = accumulator[key] || [];
          accumulator[key].push(item);
          return accumulator;
        }, {});
      }

      function buildProjectConcernWorkbench(files = [], options = {}) {
        const concernCore = global.McelConcernCore;
        if (!concernCore?.analyzeProject) {
          return {
            version: WORKBENCH_VERSION,
            projectId: options.projectId || "main_computer_test",
            detectorAvailable: false,
            workOrders: [],
            migrationQueue: [],
            summary: {workOrderCount: 0, criticalCount: 0, highCount: 0, contractCount: 0}
          };
        }
        const report = concernCore.analyzeProject(files, {projectId: options.projectId || "main_computer_test"});
        const workOrders = asArray(report.concerns)
          .map((concern) => buildWorkOrder(concern, options))
          .sort((left, right) => right.priorityScore - left.priorityScore || left.id.localeCompare(right.id));

        const byApp = groupBy(workOrders, (order) => order.app);
        const byContract = groupBy(workOrders, (order) => order.targetContract);
        const migrationQueue = workOrders.map((order, index) => ({
          rank: index + 1,
          id: order.id,
          priority: order.priority,
          priorityScore: order.priorityScore,
          targetContract: order.targetContract,
          firstSafeMigration: order.firstSafeMigration[0] || "extract contract adapter",
          proofNeeded: order.testsNeeded[0] || "add contract proof"
        }));
        const firstSafePatchQueue = workOrders.slice(0, Number(options.limit || 5)).map((order) => ({
          id: order.id,
          title: order.title,
          steps: order.firstSafeMigration,
          testsNeeded: order.testsNeeded
        }));

        return {
          version: WORKBENCH_VERSION,
          projectId: report.projectId,
          detectorAvailable: true,
          detectorReport: {
            analyzedFileCount: report.analyzedFileCount,
            detectedConcernCount: report.detectedConcernCount,
            severeContractGapCount: report.severeContractGapCount,
            canDriveMcelContracts: report.canDriveMcelContracts
          },
          summary: {
            workOrderCount: workOrders.length,
            criticalCount: workOrders.filter((order) => order.priority === "critical").length,
            highCount: workOrders.filter((order) => order.priority === "high").length,
            contractCount: Object.keys(byContract).length,
            appCount: Object.keys(byApp).length,
            hasFirstSafePatchForEveryHighPriority: workOrders
              .filter((order) => order.priority === "critical" || order.priority === "high")
              .every((order) => order.firstSafeMigration.length && order.testsNeeded.length),
            backedFirstSafePatchCount: workOrders.filter((order) => order.implementationStatus?.firstSafePatchBacked).length
          },
          workOrders,
          migrationQueue,
          firstSafePatchQueue,
          coverageByApp: Object.fromEntries(Object.entries(byApp).map(([app, orders]) => [app, orders.map((order) => order.id)])),
          coverageByContract: Object.fromEntries(Object.entries(byContract).map(([contract, orders]) => [contract, orders.map((order) => order.id)])),
          recommendedNextPatch: migrationQueue[0] || null,
          projectContracts: clone(PROJECT_CONTRACTS)
        };
      }

      function buildSpecimenWorkbench(options = {}) {
        const concernCore = global.McelConcernCore;
        const files = concernCore?.projectSpecimenFiles ? concernCore.projectSpecimenFiles() : [];
        return buildProjectConcernWorkbench(files, {projectId: "main_computer_test.specimen-workbench", ...options});
      }

      function getWorkOrder(workbench, id) {
        return asArray(workbench?.workOrders).find((order) => order.id === id || order.concernId === id) || null;
      }

      global.McelProjectConcernWorkbench = {
        WORKBENCH_VERSION,
        PROJECT_CONTRACTS: clone(PROJECT_CONTRACTS),
        WORK_ORDER_TEMPLATES: clone(WORK_ORDER_TEMPLATES),
        buildProjectConcernWorkbench,
        buildSpecimenWorkbench,
        buildWorkOrder,
        implementationStatusFor,
        getWorkOrder
      };
    })(typeof window !== "undefined" ? window : globalThis);
