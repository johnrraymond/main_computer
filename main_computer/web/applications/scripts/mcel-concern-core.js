    (function (global) {
      "use strict";

      const CONCERN_VERSION = "0.1.0";

      const CONCERN_CATALOG = [
        {
          id: "concern.file-basket",
          label: "File basket",
          family: "workflow",
          purpose: "User chooses exact file paths for an operation while hierarchy, metadata, blocked state, and selected output stay truthful.",
          mvcSplit: {
            model: ["candidate files", "repo-relative identity", "status/group/risk/reason metadata", "blocked/selectable state"],
            controller: ["toggle file", "toggle directory shortcut", "select all eligible", "derive mixed state", "reject blocked rows"],
            view: ["treegrid/details hybrid", "typed path/status/risk/reason cells", "selected-output proof"],
            contract: "pattern.file-basket"
          }
        },
        {
          id: "concern.resource-browser",
          label: "Resource browser",
          family: "workflow",
          purpose: "User navigates hierarchical resources, previews a selected entry, and opens directories/files without the view owning navigation policy.",
          mvcSplit: {
            model: ["entry identity", "path", "kind", "metadata", "children"],
            controller: ["select resource", "open directory", "preview entry", "keyboard enter"],
            view: ["tree/list/details", "preview pane", "path context"],
            contract: "pattern.resource-browser"
          }
        },
        {
          id: "concern.deploy-preflight",
          label: "Deploy preflight",
          family: "safety",
          purpose: "User reviews an operation plan, warning list, command preview, and acknowledgement gate before a deploy/publish action can run.",
          mvcSplit: {
            model: ["deployment plan", "lane", "service", "warnings", "command"],
            controller: ["open preflight", "acknowledge warnings", "confirm deploy", "cancel deploy"],
            view: ["preflight panel", "warning list", "command preview", "gated command button"],
            contract: "pattern.safety-preflight"
          }
        },
        {
          id: "concern.change-review-list",
          label: "Change review list",
          family: "workflow",
          purpose: "User scans and previews a list of edits/commits/diffs before choosing what to inspect or apply.",
          mvcSplit: {
            model: ["change id", "subject", "file paths", "diff summary"],
            controller: ["select change", "preview diff", "open commit action"],
            view: ["review list", "diff stat cells", "preview pane"],
            contract: "pattern.change-review"
          }
        },
        {
          id: "concern.execution-cell",
          label: "Execution cell",
          family: "compute",
          purpose: "User runs a typed cell/request and receives output while routing, readiness, recoverability, and variants remain controlled.",
          mvcSplit: {
            model: ["cell identity", "cell type", "source", "run id", "output variants"],
            controller: ["evaluate", "route local/remote", "recover output", "select output variant"],
            view: ["input cell", "thinking panel", "output cell", "variant navigation"],
            contract: "pattern.execution-cell"
          }
        },
        {
          id: "concern.worker-routing",
          label: "Worker routing",
          family: "compute",
          purpose: "User or system chooses local versus remote execution based on capacity/readiness without hiding the policy.",
          mvcSplit: {
            model: ["capacity snapshot", "pending request", "thread/run ids", "routing choice"],
            controller: ["wait local", "use remote", "record choice", "close modal"],
            view: ["routing modal", "capacity status cards", "choice cards"],
            contract: "pattern.worker-routing"
          }
        },
        {
          id: "concern.output-renderer",
          label: "Output renderer",
          family: "view",
          purpose: "User inspects generated output, tables, variants, copy actions, and continuations without the output view owning execution state.",
          mvcSplit: {
            model: ["output parts", "variant index", "source cell id", "serialization"],
            controller: ["copy output", "focus variant", "append continuation"],
            view: ["output cell", "table renderer", "copy action", "variant controls"],
            contract: "pattern.output-renderer"
          }
        },
        {
          id: "concern.process-table",
          label: "Process table",
          family: "operations",
          purpose: "User monitors process/server state with identifiers, status, actions, and safety gates presented as typed columns.",
          mvcSplit: {
            model: ["pid", "server name", "state", "port", "command"],
            controller: ["refresh", "stop/start", "inspect log", "gate dangerous action"],
            view: ["data table", "status cells", "command/action cells"],
            contract: "pattern.process-table"
          }
        }
      ];

      const TOOLKIT_BY_CONCERN = {
        "concern.file-basket": [
          "control.selection.tristate",
          "control.disclosure",
          "control.bulk-selector",
          "cell.path",
          "cell.status",
          "cell.risk",
          "cell.reason",
          "controller.selection",
          "controller.expansion",
          "controller.safety-gate",
          "collection.treegrid",
          "pattern.file-basket"
        ],
        "concern.resource-browser": [
          "control.disclosure",
          "cell.path",
          "cell.name",
          "cell.datetime",
          "layout.preview-pane",
          "controller.expansion",
          "collection.treegrid",
          "collection.column-browser",
          "pattern.resource-browser"
        ],
        "concern.deploy-preflight": [
          "control.command-button",
          "cell.reason",
          "layout.inspector-pane",
          "controller.safety-gate",
          "pattern.safety-preflight"
        ],
        "concern.change-review-list": [
          "cell.path",
          "cell.diffstat",
          "layout.preview-pane",
          "controller.selection",
          "collection.data-table",
          "pattern.change-review"
        ],
        "concern.execution-cell": [
          "control.command-button",
          "layout.status-bar",
          "layout.preview-pane",
          "controller.safety-gate",
          "pattern.execution-cell"
        ],
        "concern.worker-routing": [
          "control.command-button",
          "cell.status",
          "layout.inspector-pane",
          "controller.safety-gate",
          "pattern.worker-routing"
        ],
        "concern.output-renderer": [
          "cell.reason",
          "layout.preview-pane",
          "controller.selection",
          "collection.data-table",
          "pattern.output-renderer"
        ],
        "concern.process-table": [
          "cell.status",
          "cell.action",
          "controller.safety-gate",
          "collection.data-table",
          "pattern.process-table"
        ]
      };

      const RULES = [
        {
          concernId: "concern.file-basket",
          pathIncludes: ["task-manager.js", "git-tools"],
          requiredTokenScore: 4,
          tokens: [
            "gitProjectCommitCandidateItems",
            "gitProjectCommitTreeSource",
            "gitProjectCommitInsertTreePath",
            "gitProjectCommitSelectedFilesFromWorkbench",
            "gitProjectInitializeCommitWunderbaum",
            "titleParts",
            "selectable",
            "blocked",
            "candidate_groups"
          ],
          linePatterns: [
            {pattern: /function\s+gitProjectCommitCandidateItems\b/, role: "model", label: "candidate file model"},
            {pattern: /function\s+gitProjectCommitFileMeta\b/, role: "model", label: "typed file metadata"},
            {pattern: /function\s+gitProjectCommitInsertTreePath\b/, role: "model", label: "hierarchy construction"},
            {pattern: /\btitleParts\b/, role: "view-gap", label: "typed fields collapsed into node title"},
            {pattern: /function\s+gitProjectCommitSelectedFilesFrom(?:Workbench|Wunderbaum|Fallback|Dom)\b/, role: "controller", label: "selected file output extraction"},
            {pattern: /function\s+gitProjectCommitUpdateFallbackParents\b/, role: "controller", label: "derived mixed directory state"},
            {pattern: /function\s+gitProjectInitializeCommitWunderbaum\b/, role: "view", label: "view adapter/wunderbaum initialization"},
            {pattern: /blocked|selectable|selected_by_default/, role: "safety", label: "blocked/selectable safety language"}
          ],
          contractGap: "severe",
          missingContractReason: "File choice, hierarchy, metadata, blocked rows, and selected output exist in code, but the code can still collapse typed fields into title strings and scrape widget-specific selection."
        },
        {
          concernId: "concern.resource-browser",
          pathIncludes: ["file-explorer.js"],
          requiredTokenScore: 4,
          tokens: [
            "systemFileExplorerTreeSource",
            "renderSystemFileExplorerEntries",
            "previewSystemFileExplorerEntry",
            "systemFileExplorerOpenEntry",
            "systemFileExplorerEntryMeta",
            "Wunderbaum"
          ],
          linePatterns: [
            {pattern: /function\s+systemFileExplorerEntryMeta\b/, role: "model", label: "entry metadata"},
            {pattern: /function\s+systemFileExplorerTreeSource\b/, role: "model", label: "tree source construction"},
            {pattern: /function\s+renderSystemFileExplorerEntries\b/, role: "view-gap", label: "render function owns loading, view, preview, and fallback"},
            {pattern: /function\s+previewSystemFileExplorerEntry\b/, role: "controller", label: "preview selection"},
            {pattern: /function\s+systemFileExplorerOpenEntry\b/, role: "controller", label: "open entry navigation"},
            {pattern: /Wunderbaum/, role: "view", label: "external tree widget adapter"}
          ],
          contractGap: "major",
          missingContractReason: "The resource browser has entry metadata, tree source, preview, and open behavior, but no explicit browser contract declaring fields, navigation semantics, and eligible views."
        },
        {
          concernId: "concern.deploy-preflight",
          pathIncludes: ["website-builder.js"],
          requiredTokenScore: 4,
          tokens: [
            "websiteBuilderDeployPreflightWarnings",
            "websiteBuilderDeployPreflightRequiresAcknowledgement",
            "renderWebsiteBuilderDeployPreflight",
            "websiteBuilderDeployPreflightConfirm",
            "dryRun",
            "acknowledged",
            "warnings"
          ],
          linePatterns: [
            {pattern: /function\s+websiteBuilderDeployPreflightWarnings\b/, role: "safety", label: "warning extraction"},
            {pattern: /function\s+websiteBuilderDeployPreflightRequiresAcknowledgement\b/, role: "safety", label: "acknowledgement rule"},
            {pattern: /function\s+websiteBuilderDeployPreflightRows\b/, role: "model", label: "preflight data rows"},
            {pattern: /function\s+renderWebsiteBuilderDeployPreflight\b/, role: "view-gap", label: "render + policy coupling"},
            {pattern: /websiteBuilderDeployPreflightConfirm\.disabled/, role: "controller", label: "gated confirm enablement"},
            {pattern: /dryRun:\s*true|dry-run/i, role: "safety", label: "dry-run preflight path"}
          ],
          contractGap: "major",
          missingContractReason: "The deploy path has warnings, acknowledgement, command preview, and confirm gating, but should be declared as a safety preflight contract before rendering."
        },
        {
          concernId: "concern.change-review-list",
          pathIncludes: ["website-builder.js"],
          requiredTokenScore: 3,
          tokens: [
            "websiteBuilderGitCommitFiles",
            "websiteBuilderGitCommitLabel",
            "renderWebsiteBuilderGitEdits",
            "websiteBuilderSiteGitRequest",
            "review",
            "diff"
          ],
          linePatterns: [
            {pattern: /function\s+websiteBuilderGitCommitFiles\b/, role: "model", label: "changed files model"},
            {pattern: /function\s+websiteBuilderGitCommitLabel\b/, role: "model", label: "change label"},
            {pattern: /function\s+renderWebsiteBuilderGitEdits\b/, role: "view-gap", label: "review list rendering"},
            {pattern: /websiteBuilderSiteGitRequest\("review"/, role: "controller", label: "review request action"},
            {pattern: /diff|preview/i, role: "view", label: "diff/preview language"}
          ],
          contractGap: "moderate",
          missingContractReason: "The page renders a git review list with file details and preview actions, but it is not described as a change-review contract."
        },
        {
          concernId: "concern.execution-cell",
          pathIncludes: ["chat-console.js"],
          requiredTokenScore: 5,
          tokens: [
            "createChatConsoleCell",
            "evaluateChatConsole",
            "chatConsoleApplyEvaluationOutput",
            "renderChatConsoleCell",
            "renderChatConsoleOutputCell",
            "output_variant_ids",
            "source_cell_id"
          ],
          linePatterns: [
            {pattern: /function\s+addChatConsoleCell\b|function\s+createChatConsoleCell\b/, role: "model", label: "cell model creation"},
            {pattern: /function\s+evaluateChatConsole\w*Cell\b/, role: "controller", label: "cell evaluation command"},
            {pattern: /function\s+chatConsoleApplyEvaluationOutput\w*\b/, role: "controller", label: "output transition"},
            {pattern: /function\s+renderChatConsoleCell\b/, role: "view", label: "cell renderer"},
            {pattern: /function\s+renderChatConsoleOutputCell\b/, role: "view", label: "output cell renderer"},
            {pattern: /output_variant_ids|selected_output_variant_index/, role: "model", label: "output variant state"}
          ],
          contractGap: "major",
          missingContractReason: "Cell execution mixes source, routing, output variants, rendering, and recoverability without an execution-cell contract."
        },
        {
          concernId: "concern.worker-routing",
          pathIncludes: ["chat-console.js", "worker.js"],
          requiredTokenScore: 4,
          tokens: [
            "RemoteWorker",
            "remote worker",
            "capacity",
            "pendingRequest",
            "chatConsoleShowRemoteWorkerControlModal",
            "chatConsoleChooseRemoteWorkerControlOption",
            "readiness"
          ],
          linePatterns: [
            {pattern: /function\s+chatConsoleShowRemoteWorkerControlModal\b/, role: "view-gap", label: "remote worker choice modal"},
            {pattern: /function\s+chatConsoleChooseRemoteWorkerControlOption\b/, role: "controller", label: "routing choice command"},
            {pattern: /function\s+chatConsoleRemoteWorkerStatusCard\b/, role: "view", label: "capacity status card"},
            {pattern: /pendingRequest|capacity|readiness/i, role: "model", label: "routing state"},
            {pattern: /remote worker/i, role: "safety", label: "local/remote execution language"}
          ],
          contractGap: "moderate",
          missingContractReason: "Remote worker routing has capacity, pending request, readiness, and choices, but lacks a worker-routing contract that can explain the policy to the user."
        },
        {
          concernId: "concern.output-renderer",
          pathIncludes: ["chat-console.js"],
          requiredTokenScore: 4,
          tokens: [
            "renderChatConsoleOutputCell",
            "renderChatConsoleOutputPart",
            "renderChatConsoleOutputTable",
            "copyChatConsoleOutputCell",
            "focusChatConsoleVariant",
            "getChatConsoleOutputVariants"
          ],
          linePatterns: [
            {pattern: /function\s+getChatConsoleOutputVariants\b/, role: "model", label: "output variants model"},
            {pattern: /function\s+focusChatConsoleVariant\b/, role: "controller", label: "variant selection"},
            {pattern: /function\s+renderChatConsoleOutputCell\b/, role: "view", label: "output cell view"},
            {pattern: /function\s+renderChatConsoleOutputPart\b/, role: "view", label: "typed output part renderer"},
            {pattern: /function\s+renderChatConsoleOutputTable\b/, role: "view", label: "table output renderer"},
            {pattern: /copyChatConsoleOutputCell/, role: "controller", label: "copy output action"}
          ],
          contractGap: "moderate",
          missingContractReason: "Output rendering has variants, tables, copy actions, and continuations; it should be formalized as an output-renderer contract."
        },
        {
          concernId: "concern.process-table",
          pathIncludes: ["task-manager.js", "worker.js"],
          requiredTokenScore: 3,
          tokens: [
            "process",
            "pid",
            "server",
            "status",
            "stop",
            "refresh"
          ],
          linePatterns: [
            {pattern: /pid|process id/i, role: "model", label: "process identity"},
            {pattern: /server|service/i, role: "model", label: "service/server metadata"},
            {pattern: /stop|start|restart|refresh/i, role: "controller", label: "process command"},
            {pattern: /status|running|stopped/i, role: "view", label: "status display"}
          ],
          contractGap: "minor",
          missingContractReason: "Operational process state is table-like and command-bearing; detector should keep it out of one-off status rows."
        }
      ];

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function normalizePath(path) {
        return String(path || "").replace(/\\/g, "/").replace(/^\/+/, "");
      }

      function splitLines(text) {
        return String(text || "").split(/\r?\n/);
      }

      function countToken(text, token) {
        const haystack = String(text || "").toLowerCase();
        const needle = String(token || "").toLowerCase();
        if (!needle) return 0;
        let count = 0;
        let index = haystack.indexOf(needle);
        while (index !== -1) {
          count += 1;
          index = haystack.indexOf(needle, index + needle.length);
        }
        return count;
      }

      function matchesPath(rule, path) {
        const normalized = normalizePath(path).toLowerCase();
        return (rule.pathIncludes || []).some((fragment) => normalized.includes(String(fragment || "").toLowerCase()));
      }

      function lineMatches(pattern, line) {
        if (pattern instanceof RegExp) return pattern.test(line);
        return String(line || "").toLowerCase().includes(String(pattern || "").toLowerCase());
      }

      function findLineEvidence(lines, rule) {
        const ranges = [];
        const seen = new Set();
        (rule.linePatterns || []).forEach((entry) => {
          lines.forEach((line, index) => {
            const localPattern = entry.pattern instanceof RegExp
              ? new RegExp(entry.pattern.source, entry.pattern.flags)
              : entry.pattern;
            if (!lineMatches(localPattern, line)) return;
            const lineNumber = index + 1;
            const key = `${entry.role}:${entry.label}:${lineNumber}`;
            if (seen.has(key)) return;
            seen.add(key);
            const start = Math.max(1, lineNumber - (entry.before || 2));
            const end = Math.min(lines.length, lineNumber + (entry.after || 6));
            ranges.push({
              role: entry.role || "evidence",
              label: entry.label || "evidence",
              startLine: start,
              endLine: end,
              anchorLine: lineNumber,
              excerpt: String(line || "").trim().slice(0, 180)
            });
          });
        });
        return ranges.sort((left, right) => left.startLine - right.startLine || left.label.localeCompare(right.label));
      }

      function scoreRuleAgainstFile(rule, file) {
        const path = normalizePath(file.path);
        const text = String(file.text || "");
        const lines = splitLines(text);
        const pathMatch = matchesPath(rule, path);
        if (!pathMatch && rule.pathIncludes?.length) {
          return null;
        }
        const tokenHits = (rule.tokens || [])
          .map((token) => ({token, count: countToken(text, token)}))
          .filter((hit) => hit.count > 0);
        const lineRanges = findLineEvidence(lines, rule);
        const roleSet = new Set(lineRanges.map((range) => range.role));
        const tokenScore = tokenHits.length;
        const roleScore = roleSet.size;
        const rawScore = tokenScore + roleScore + (pathMatch ? 2 : 0);
        const threshold = Number(rule.requiredTokenScore || 3);
        if (tokenScore < threshold && rawScore < threshold + 2) return null;
        return {
          path,
          lineCount: lines.length,
          tokenHits,
          ranges: lineRanges,
          roles: Array.from(roleSet).sort(),
          score: rawScore
        };
      }

      function catalogById() {
        const map = new Map();
        CONCERN_CATALOG.forEach((concern) => map.set(concern.id, concern));
        return map;
      }

      function concernConfidence(score, rule, fileEvidence) {
        const required = Number(rule.requiredTokenScore || 3);
        const rangeBonus = Math.min(0.18, (fileEvidence.ranges || []).length * 0.02);
        return Math.max(0.5, Math.min(0.99, 0.58 + ((score - required) * 0.045) + rangeBonus));
      }

      function contractGapWeight(gap) {
        return {severe: 4, major: 3, moderate: 2, minor: 1}[gap] || 0;
      }

      function inferMvcSplit(concernId) {
        const concern = CONCERN_CATALOG.find((item) => item.id === concernId);
        return clone(concern?.mvcSplit || {});
      }

      function inferToolkit(concernId) {
        return (TOOLKIT_BY_CONCERN[concernId] || []).slice();
      }

      function buildConcern(rule, fileEvidence) {
        const catalog = catalogById().get(rule.concernId) || {id: rule.concernId, label: rule.concernId, family: "unknown"};
        const roles = new Set(fileEvidence.roles || []);
        const hasViewGap = roles.has("view-gap");
        const hasController = roles.has("controller");
        const hasModel = roles.has("model");
        const hasSafety = roles.has("safety");
        const boundaryHealth = hasModel && hasController && (roles.has("view") || hasViewGap)
          ? (hasViewGap ? "tangled-mvc-boundary" : "visible-boundary")
          : "partial-boundary";
        return {
          id: rule.concernId,
          label: catalog.label,
          family: catalog.family,
          confidence: Number(concernConfidence(fileEvidence.score, rule, fileEvidence).toFixed(2)),
          file: fileEvidence.path,
          score: fileEvidence.score,
          tokenHits: fileEvidence.tokenHits,
          ranges: fileEvidence.ranges,
          roles: Array.from(roles).sort(),
          boundaryHealth,
          contractGap: rule.contractGap || "unknown",
          missingContractReason: rule.missingContractReason || "",
          inferredMvcSplit: inferMvcSplit(rule.concernId),
          recommendedToolkit: inferToolkit(rule.concernId),
          recommendedContract: inferMvcSplit(rule.concernId).contract || "pattern.unresolved",
          slimeSignals: [
            hasViewGap ? "view-gap" : "",
            hasController && hasModel && hasViewGap ? "model-controller-view-in-one-region" : "",
            hasSafety && hasViewGap ? "safety-policy-render-coupling" : ""
          ].filter(Boolean)
        };
      }

      function analyzeProject(files = [], options = {}) {
        const normalizedFiles = (files || [])
          .filter((file) => file && typeof file === "object")
          .map((file) => ({
            path: normalizePath(file.path || file.name || ""),
            text: String(file.text || file.source || "")
          }))
          .filter((file) => file.path && file.text);
        const concerns = [];
        RULES.forEach((rule) => {
          normalizedFiles.forEach((file) => {
            const evidence = scoreRuleAgainstFile(rule, file);
            if (!evidence) return;
            concerns.push(buildConcern(rule, evidence));
          });
        });
        concerns.sort((left, right) => contractGapWeight(right.contractGap) - contractGapWeight(left.contractGap) || right.confidence - left.confidence || left.id.localeCompare(right.id));
        const severeContractGapCount = concerns.filter((concern) => concern.contractGap === "severe" || concern.contractGap === "major").length;
        const recommendedToolkit = Array.from(new Set(concerns.flatMap((concern) => concern.recommendedToolkit || []))).sort();
        const concernFamilies = {};
        concerns.forEach((concern) => {
          concernFamilies[concern.family] = (concernFamilies[concern.family] || 0) + 1;
        });
        return {
          version: CONCERN_VERSION,
          projectId: options.projectId || "main_computer_test",
          analyzedFileCount: normalizedFiles.length,
          detectedConcernCount: concerns.length,
          severeContractGapCount,
          concernFamilies,
          concerns,
          recommendedToolkit,
          highPriorityConcerns: concerns
            .filter((concern) => concern.contractGap === "severe" || concern.contractGap === "major")
            .map((concern) => concern.id),
          canDriveMcelContracts: concerns.some((concern) => concern.recommendedContract && concern.recommendedToolkit?.length)
        };
      }

      function projectSpecimenFiles() {
        return [
          {
            path: "main_computer/web/applications/scripts/task-manager.js",
            text: `
              function gitProjectCommitCandidateItems(review = {}) { const groups = review.candidate_groups || {}; }
              function gitProjectCommitFileMeta(item = {}, group = {}) { return {status: item.status, risk: item.risk, reason: item.reason}; }
              function gitProjectCommitInsertTreePath(root, item = {}, group = {}) {
                const titleParts = [item.path, item.status, group.title, item.risk, item.reason];
                const selectable = group.selectable !== false;
                const blocked = item.blocked || item.blocking_security_findings_count;
              }
              function gitProjectCommitSelectedFilesFromWorkbench(workbench) { return selectedPaths; }
              function gitProjectCommitUpdateFallbackParents(scope) { input.indeterminate = mixed; }
              function gitProjectInitializeCommitWunderbaum(workbench) { const tree = new Wunderbaum({checkbox: true}); }
            `
          },
          {
            path: "main_computer/web/applications/scripts/file-explorer.js",
            text: `
              function systemFileExplorerEntryMeta(entry = {}) { return {path: entry.path, kind: entry.kind}; }
              function systemFileExplorerTreeSource(entries = []) { return entries.map(systemFileExplorerEntryToTreeNode); }
              function renderSystemFileExplorerEntries(entries) { systemFileExplorerLoadWunderbaum(); previewSystemFileExplorerEntry(entries[0]); }
              function previewSystemFileExplorerEntry(entry) {}
              function systemFileExplorerOpenEntry(entry = {}) {}
            `
          },
          {
            path: "main_computer/web/applications/scripts/website-builder.js",
            text: `
              function websiteBuilderDeployPreflightWarnings(result) { const warnings = []; return warnings; }
              function websiteBuilderDeployPreflightRequiresAcknowledgement(result) { return websiteBuilderDeployPreflightWarnings(result).length > 0; }
              function websiteBuilderDeployPreflightRows(result, lane) { return [["Command", result.plan.command]]; }
              function renderWebsiteBuilderDeployPreflight() { websiteBuilderDeployPreflightConfirm.disabled = !acknowledged; }
              async function openWebsiteBuilderDeployPreflight(lane) { const payload = await websiteBuilderPublishApi(siteId, lane, {dryRun: true}); }
              function websiteBuilderGitCommitFiles(edit) { return edit.files || []; }
              function websiteBuilderGitCommitLabel(edit) { return edit.subject || "Git commit"; }
              function renderWebsiteBuilderGitEdits() { websiteBuilderSiteGitRequest("review", {commit}); }
            `
          },
          {
            path: "main_computer/web/applications/scripts/chat-console.js",
            text: `
              function chatConsoleShowRemoteWorkerControlModal(args) { const pendingRequest = args.pendingRequest; }
              function chatConsoleChooseRemoteWorkerControlOption(mode, context) {}
              function chatConsoleRemoteWorkerStatusCard({capacity, readiness}) {}
              function createChatConsoleCell(type, source) { return {type, source, output_variant_ids: []}; }
              function evaluateChatConsoleAiCell(cellId) { const endpoint = "/api/applications/chat-console/cell/evaluate"; }
              function chatConsoleApplyEvaluationOutputToCurrentThread(sourceCell, outputCell) {}
              function getChatConsoleOutputVariants(sourceCellId) { return []; }
              function focusChatConsoleVariant(sourceCellId, index) {}
              function renderChatConsoleCell(cell) {}
              function renderChatConsoleOutputCell(cell) { copyChatConsoleOutputCell(cell.id); }
              function renderChatConsoleOutputPart(outputCell, part) {}
              function renderChatConsoleOutputTable(rows) {}
            `
          }
        ];
      }

      function buildReadinessReport(files = projectSpecimenFiles()) {
        const report = analyzeProject(files, {projectId: "main_computer_test.specimen"});
        return {
          version: CONCERN_VERSION,
          detectedConcernCount: report.detectedConcernCount,
          severeContractGapCount: report.severeContractGapCount,
          fileBasketDetected: report.concerns.some((concern) => concern.id === "concern.file-basket"),
          resourceBrowserDetected: report.concerns.some((concern) => concern.id === "concern.resource-browser"),
          deployPreflightDetected: report.concerns.some((concern) => concern.id === "concern.deploy-preflight"),
          executionCellDetected: report.concerns.some((concern) => concern.id === "concern.execution-cell"),
          canDriveMcelContracts: report.canDriveMcelContracts,
          highPriorityConcerns: report.highPriorityConcerns
        };
      }

      global.McelConcernCore = {
        CONCERN_VERSION,
        CONCERN_CATALOG: clone(CONCERN_CATALOG),
        RULES: clone(RULES.map((rule) => ({
          ...rule,
          linePatterns: (rule.linePatterns || []).map((entry) => ({...entry, pattern: String(entry.pattern)}))
        }))),
        TOOLKIT_BY_CONCERN: clone(TOOLKIT_BY_CONCERN),
        analyzeProject,
        buildReadinessReport,
        projectSpecimenFiles
      };
    })(typeof window !== "undefined" ? window : globalThis);
