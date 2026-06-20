from __future__ import annotations

import re
import unittest
from pathlib import Path

from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIT_TOOLS_APP_HTML = (PROJECT_ROOT / "main_computer/web/applications/apps/git-tools.html").read_text(encoding="utf-8")
GIT_TOOLS_CSS = (PROJECT_ROOT / "main_computer/web/applications/styles/git-tools.css").read_text(encoding="utf-8")
STATUS_AND_RESPONSIVE_CSS = (
    PROJECT_ROOT / "main_computer/web/applications/styles/status-and-responsive.css"
).read_text(encoding="utf-8")
VIEWPORT_ROUTE_DISPATCH = (PROJECT_ROOT / "main_computer/viewport_route_dispatch.py").read_text(encoding="utf-8")
VIEWPORT_ROUTES_GIT = (PROJECT_ROOT / "main_computer/viewport_routes_git.py").read_text(encoding="utf-8")
GIT_TOOLS_PY = (PROJECT_ROOT / "main_computer/git_tools.py").read_text(encoding="utf-8")
GIT_DIRTY_PY = (PROJECT_ROOT / "git_dirty.py").read_text(encoding="utf-8")
GIT_TOOLS_SCRIPT_DIR = PROJECT_ROOT / "main_computer/web/applications/scripts"
TASK_MANAGER_JS = (GIT_TOOLS_SCRIPT_DIR / "task-manager.js").read_text(encoding="utf-8")
GIT_TOOLS_MODULE_JS = "\n".join(
    (GIT_TOOLS_SCRIPT_DIR / name).read_text(encoding="utf-8")
    for name in (
        "git-tools-project-workflow.js",
        "git-tools-project-shared.js",
        "git-tools-commit-workbench.js",
        "git-tools-secrets-filter-workbench.js",
        "git-tools-archive-workbench.js",
        "git-tools-project-card-subscreen.js",
        "git-tools-project-wizard-rendering.js",
        "git-tools-status-refresh-bridge.js",
        "git-tools-page-wizard.js",
        "git-tools-shim-console.js",
        "git-tools-gitignore-workbench.js",
        "git-tools-status-api.js",
    )
)


class GitPageWizardWorkflowTests(unittest.TestCase):
    def test_gitignore_right_pane_distinguishes_not_loaded_from_empty_file(self) -> None:
        self.assertIn("content_read === true", GIT_TOOLS_MODULE_JS)
        self.assertIn("contents were not loaded", GIT_TOOLS_MODULE_JS)
        self.assertIn("gitProjectGitignoreFileSummary", GIT_TOOLS_MODULE_JS)
        self.assertIn("appears to be empty", GIT_TOOLS_MODULE_JS)
        self.assertNotIn(".gitignore exists but has no lines yet.", GIT_TOOLS_MODULE_JS)

    def test_gitignore_cleanup_card_has_real_save_and_dirty_model(self) -> None:
        expected_js = (
            "Save .gitignore",
            '"/api/applications/git/project/gitignore/save"',
            "function gitProjectGitignoreCheckedLines(",
            "function gitProjectUpdateGitignoreDirtyState(",
            "function gitProjectApplyIgnoreRuleToRightPane(",
            "gitProjectAppendGitignoreRightRow(workbench, rule, \"pending\")",
            "gitProjectSetGitignoreRowChecked(workbench, row, false)",
            "beforeunload",
            "Discard unsaved .gitignore changes?",
            "gitProjectConfirmDiscardGitignoreChanges(subscreen)",
        )
        for snippet in expected_js:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_MODULE_JS)
        for snippet in (
            ".git-project-gitignore-line.is-pending",
            ".git-project-gitignore-line.is-deleted",
            ".git-project-gitignore-save-panel",
            ".git-project-gitignore-workbench.is-dirty",
        ):
            with self.subTest(css_snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_CSS)

    def test_git_page_wizard_ui_is_removed_from_git_tools_page(self) -> None:
        removed_snippets = (
            'id="git-page-wizard-workflow"',
            'id="git-page-wizard-status"',
            'data-wizard-stage="answer"',
            "Page Element Wizard",
            "Send to Git Console",
            "Create or plan shim",
            "Review and dry-run",
        )
        for snippet in removed_snippets:
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, GIT_TOOLS_APP_HTML)


    def test_repo_boundary_choice_modal_is_available_for_parent_repo_detection(self) -> None:
        expected_js = (
            "function gitProjectNeedsRepoBoundaryChoice(data = null)",
            "function openGitProjectRepoBoundaryModal(data = gitProjectLastInspection)",
            "Choose Git Tracking Method",
            "How should Git track this folder?",
            "Start Git in this folder",
            "Use parent repository",
            "Choose another folder",
            "repo-boundary:initialize_repository_here",
            '"/api/applications/git/project/action/run"',
            '"/api/applications/git/project/add"',
            "choice required: repository boundary",
        )
        for snippet in expected_js:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)

        expected_css = (
            ".git-project-next-step-actions",
            ".git-project-repo-boundary-overlay",
            ".git-project-repo-boundary-dialog",
            ".git-project-repo-boundary-options",
            ".git-project-repo-boundary-warning",
        )
        for snippet in expected_css:
            with self.subTest(css_snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_CSS)


    def test_git_project_selector_exposes_vip_lock_and_dirty_plan_wizard(self) -> None:
        expected_snippets = (
            'id="git-project-selector-panel"',
            'id="git-project-current"',
            'id="git-project-next-step"',
            'class="git-project-layout"',
            'class="git-project-roster"',
            "Current Projects",
            "git_dirty.py plan",
            'class="git-project-add-section"',
            'id="git-project-path"',
            'id="git-project-list"',
            'id="git-project-archive-list"',
            'id="git-project-wizard-plan"',
            'data-widget-label="Projects"',
            'data-mc-component-label="Git Projects"',
            'data-mc-component-label="Git Projects Layout"',
            "VIP project cannot be archived",
            "function loadGitProjects()",
            "function inspectSelectedGitProject(",
            "function renderGitProjectWizard(",
            "function gitProjectCardSubscreenHtml(",
            "function gitProjectIgnoreWorkbenchHtml(",
            "function gitProjectCommitWorkbenchHtml(",
            "function gitProjectSecretsFilterWorkbenchHtml(",
            "function gitProjectSecretsFilterRuleRowsHtml(",
            "function gitProjectFirstCommitGateOrder(",
            "lane: \"satisfied\"",
            "tone: \"complete\"",
            "git-project-gitignore-success",
            "if (id === \"secrets_filter\") return 20;",
            "const gateA = gitProjectFirstCommitGateOrder(a);",
            "secrets_filter",
            "prepare_commit_snapshot",
            "function bindGitProjectCardSubscreen(",
            "Prioritized workflow queue",
            "Action queue",
            ".gitignore review",
            "function gitProjectWizardDisplayActions(actions = [])",
            "function gitProjectMergeGitignoreReviewSteps(steps = [])",
            "function gitProjectWizardStepShouldHideInActionQueue(step = {})",
            "WIZARD_HIDDEN_ACTION_IDS",
            "save_current_state",
            "push_current_branch_to_local_server",
            "inspect_configured_remotes",
            "remove_untracked_generated_files",
            "ignore_generated_files",
            "ignore_local_environment_files",
            "inspect configured remotes",
            "remove generated untracked files",
            "const visibleActions = gitProjectWizardDisplayActions(remainingActions);",
            "const displayNumber = Number.isFinite(displayIndex) ? displayIndex + 1 : Number(step.order ?? 0) + 1;",
            'renderStepGroup("Action queue", "actionable", visibleActions, "No workflow actions need review.", {key: "action-queue"})',
            "gitignore_path_summary",
            "Command details",
            "Request state",
            "# python tools/git/git_tool_fix_project_head.py <validated-payload.json>",
            "function classifyGitProjectWizardStep(step = {}, data = {}, actionKey = \"\")",
            "function gitProjectRunnableCommandInfo(step = {}, actionKey = \"\")",
            "function gitProjectCommandDetailsForStep(step = {}, actionKey = \"\")",
            "function gitProjectExecutableLinesFromCommands(commands = [])",
            "function runGitProjectAction(actionKey)",
            "function stopGitProjectAction(actionKey = \"\")",
            "function gitProjectActionStatusLabel(actionKey = \"\")",
            "data-git-project-action-status",
            "data-git-project-open-card",
            "data-git-project-card-subscreen",
            "git-project-card-open-corner",
            "has-card-open-control",
            "data-git-ignore-rule",
            "data-git-security-rule",
            "data-git-secrets-action",
            "function runGitProjectSecretsFilterAction(button)",
            "function gitProjectStartSecretsFilterEventStream(workbench, data = {})",
            "new EventSource(url)",
            "data-git-secrets-live-events",
            "data-git-secrets-live-findings",
            "evidence || finding.evidence_redacted",
            "current status: ",
            "queued",
            "running",
            "completed",
            "failed",
            "canceled",
            "rev-parse --show-toplevel --git-dir --git-common-dir --is-inside-work-tree",
            '"/api/applications/git/projects"',
            '"/api/applications/git/project/add"',
            '"/api/applications/git/project/inspect"',
            '"/api/applications/git/project/action/run"',
            '"/api/applications/git/project/gitignore/save"',
            "/api/applications/git/project/secrets-filter/stream",
            "/api/applications/git/project/commit/start",
            "/api/applications/git/project/commit/cancel",
            "/api/applications/git/project/commit/stream",
            '"/api/applications/git/server/operation/cancel"',
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)
        project_main_match = re.search(
            r'<div class="git-project-main"[^>]*>.*?<div class="git-project-wizard-plan"',
            GIT_TOOLS_APP_HTML,
            re.S,
        )
        self.assertIsNotNone(project_main_match)
        project_main_html = project_main_match.group(0) if project_main_match else ""
        self.assertNotIn('id="git-project-path"', project_main_html)
        self.assertNotIn('id="git-project-add"', project_main_html)
        self.assertNotIn('id="git-project-rescan"', GIT_TOOLS_APP_HTML)
        self.assertNotIn('id="git-project-lock"', GIT_TOOLS_APP_HTML)
        self.assertNotIn('id="git-project-unlock"', GIT_TOOLS_APP_HTML)
        self.assertIn('class="git-project-add-section"', GIT_TOOLS_APP_HTML)
        hidden_generated_groups = (
            'renderStepGroup("Immediate attention"',
            'renderStepGroup("Satisfied prerequisites"',
            'renderStepGroup("Next action"',
            'renderStepGroup("Evidence & context"',
            'renderStepGroup("Completed / history"',
        )
        for snippet in hidden_generated_groups:
            with self.subTest(hidden_generated_group=snippet):
                self.assertNotIn(snippet, APPLICATIONS_INDEX_HTML)
        self.assertIn(
            'const remainingActions = [\n    ...readyActions,\n    ...waitingActions,\n  ];',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertNotIn("const nextAction = readyActions.slice(0, 1);", APPLICATIONS_INDEX_HTML)
        self.assertIn(
            "const closedSummary = gitProjectClosedCardSummaryHtml(step, stepComponentId, stepLabel);",
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn(
            'const openCardCorner = openCardButton ? `<div class="git-project-card-open-corner" ${gitProjectMcComponentAttrs(`${stepComponentId}.open-card`, "toolbar", `${stepLabel} Open Card Control`, stepComponentId)}>${openCardButton}</div>` : "";',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertNotIn(
            'const actionRow = (actionButton || openCardButton) ? `<div class="git-project-action-row">${openCardButton}${actionButton}</div>` : "";',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn(".git-tools-project-card", GIT_TOOLS_CSS)
        self.assertIn(".git-project-layout", GIT_TOOLS_CSS)
        self.assertNotIn(".git-project-header", GIT_TOOLS_CSS)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(320px, 420px);", GIT_TOOLS_CSS)
        self.assertIn(".git-project-roster", GIT_TOOLS_CSS)
        self.assertIn(".git-project-add-section", GIT_TOOLS_CSS)
        self.assertIn(".git-project-next-step", GIT_TOOLS_CSS)
        self.assertIn(".git-project-wizard-section", GIT_TOOLS_CSS)
        self.assertIn(".git-project-wizard-empty", GIT_TOOLS_CSS)
        self.assertIn(".git-project-wizard-step", GIT_TOOLS_CSS)
        self.assertIn(".git-project-action-row", GIT_TOOLS_CSS)
        self.assertIn(".git-project-card-open-corner", GIT_TOOLS_CSS)
        self.assertIn(".git-project-command-preview", GIT_TOOLS_CSS)
        self.assertIn(".git-project-request-state", GIT_TOOLS_CSS)
        self.assertIn(".git-project-command-history", GIT_TOOLS_CSS)
        self.assertIn(".git-project-step-note", GIT_TOOLS_CSS)
        self.assertIn(".git-project-wizard-section.is-evidence", GIT_TOOLS_CSS)
        self.assertIn(".git-project-card-subscreen", GIT_TOOLS_CSS)
        self.assertIn(".git-project-gitignore-workbench", GIT_TOOLS_CSS)
        self.assertIn(".git-project-commit-workbench", GIT_TOOLS_CSS)
        self.assertIn(".git-project-secrets-filter-workbench", GIT_TOOLS_CSS)
        self.assertIn(".git-project-card-subscreen-body.is-secrets-filter", GIT_TOOLS_CSS)
        self.assertIn(".git-project-secrets-rule", GIT_TOOLS_CSS)
        self.assertIn(".git-project-secrets-detect-status", GIT_TOOLS_CSS)
        self.assertIn(".git-project-secrets-results-panel", GIT_TOOLS_CSS)
        self.assertIn(".git-project-secrets-live-events", GIT_TOOLS_CSS)
        self.assertIn(".git-project-gitignore-success", GIT_TOOLS_CSS)
        self.assertIn(".tone-complete", GIT_TOOLS_CSS)
        self.assertIn("grid-template-areas:", GIT_TOOLS_CSS)
        self.assertIn(".git-project-ignore-rule.questionable", GIT_TOOLS_CSS)
        self.assertIn("tone-blocking", GIT_TOOLS_CSS)
        self.assertIn("tone-actionable", GIT_TOOLS_CSS)
        self.assertIn("tone-informative", GIT_TOOLS_CSS)

        forbidden_snippets = (
            "MC_GIT_PANEL_RUNNER_V1",
            "PANEL_STATE =",
            "COMMANDS = [",
            "Python runner script",
            "Panel UI state JSON",
            "Python super-version",
            "data-git-project-action-command",
            "data-git-project-action-state",
            "data-git-project-run-action",
            "data-git-project-stop-action",
            "git-project-action-button",
            "git-project-stop-button",
            "git-project-priority-board",
            "Informative context",
            "function renderGitProjectPrioritySection(",
            "function buildGitProjectPriorityBuckets(data)",
            "function gitProjectScriptForStep(",
            "function gitProjectPythonRunnerScriptForStep(",
        )
        for snippet in forbidden_snippets:
            with self.subTest(forbidden_snippet=snippet):
                self.assertNotIn(snippet, APPLICATIONS_INDEX_HTML)
        self.assertTrue((PROJECT_ROOT / "tools/git/git_tool_fix_project_head.py").exists())

    def test_git_project_wizard_markup_keeps_mc_metadata_without_report_pane(self) -> None:
        expected_snippets = (
            "const GIT_PROJECT_MC_FEATURE_ID = \"git-tools.feature.projects\";",
            "function gitProjectMcComponentAttrs(",
            "function gitProjectWizardStepComponentId(step = {}, actionKey = \"\")",
            'gitProjectMcComponentAttrs(`${stepComponentId}.command-runner`, "panel", `${stepLabel} Command Details`, stepComponentId)',
            'data-mc-component-id="${escapeHtml(stepComponentId)}.command-runner.preview"',
            'gitProjectMcComponentAttrs("git-tools.projects.wizard.summary", "status", "Prioritized Workflow Queue Summary", "git-tools.projects.wizard-plan")',
            "const groupComponentId = `git-tools.projects.wizard.section.${groupSlug}`;",
            'gitProjectMcComponentAttrs(groupComponentId, "panel", title, "git-tools.projects.wizard.queue")',
            'gitProjectMcComponentAttrs(stepComponentId, "panel", stepLabel, "git-tools.projects.wizard.queue")',
            'gitProjectMcComponentAttrs(`${stepComponentId}.mini-summary`, "status", `${stepLabel} Summary`, stepComponentId)',
            "const listComponentId = `git-tools.projects.${listScope}.list`;",
            "const projectActionAttrs = (action, label) => gitProjectMcComponentAttrs(",
            'data-git-project-action="select" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("select", selected ? "Selected Button" : "Select Button")}',
            'data-git-project-action="inspect" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("inspect", "Inspect Button")}',
            'gitProjectMcComponentAttrs(`${projectComponentId}.actions`, "toolbar", `${projectLabel} Actions`, projectComponentId)',
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)

        removed_report_snippets = (
            'id="git-project-dashboard"',
            "Prioritized project report",
            "git-tools.projects.dashboard.report",
            "git-tools.projects.report.raw-details",
            "git-tools.projects.report.plan-metadata",
            "git-project-report",
        )
        for snippet in removed_report_snippets:
            with self.subTest(removed_report_snippet=snippet):
                self.assertNotIn(snippet, APPLICATIONS_INDEX_HTML)


    def test_secrets_filter_card_routes_separately_from_commit_card(self) -> None:
        expected_snippets = (
            'if (id === "secrets_filter") return true;',
            'const isSecretsFilter = stepId === "secrets_filter";',
            '? gitProjectSecretsFilterWorkbenchHtml(step)',
            'is-secrets-filter',
            'SECRETS / FILTER',
            'detect-secrets',
            'data-git-security-rule',
            'Merge rule choices',
            'Run selected rules only',
            'Run full saved filter check',
            'No saved policy yet. Merge rule choices',
            'const savedPolicyExists = Boolean(model.saved_policy_exists || model.policy?.exists);',
            'const savedRules = savedPolicyExists && Array.isArray(model.saved_rules) ? model.saved_rules : [];',
            'Editing a saved switch writes .git_dirty_rules.json immediately',
            'function gitProjectBindSecretsSavedPolicyCheckboxes(workbench)',
            'gitProjectBindSecretsSavedPolicyCheckboxes(workbench);',
            'Scan results',
            'Live scan events',
            'data-git-secrets-action',
            'data-git-secrets-action="merge_rule_choices"',
            'update_saved_rule_choices',
            'gitProjectStartSecretsFilterEventStream',
            "function gitProjectWizardStepIsSecretsFilterCandidate(step = {})",
            "function gitProjectNormalizeSecretsFilterStep(step = {})",
            'label: "Review Security / Secrets"',
            'if (gitProjectStepId(step) === "secrets_filter") return "Open Security Review";',
            "Check selected files for API keys, usernames, credentials, tokens, private keys, generated artifacts, and risky content before committing.",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)
        route_match = re.search(
            r"const body = isSecretsFilter\s*\?\s*gitProjectSecretsFilterWorkbenchHtml\(step\)\s*:\s*isGitignore",
            APPLICATIONS_INDEX_HTML,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(route_match)
        self.assertIn(".git-project-card-subscreen-body:not(.is-gitignore):not(.is-commit):not(.is-secrets-filter)", GIT_TOOLS_CSS)
        self.assertNotIn("const savedRules = Array.isArray(model.saved_rules) ? model.saved_rules : rules;", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("const savedSummary = model.saved_summary || summary;", APPLICATIONS_INDEX_HTML)
        self.assertNotIn('gitProjectSecretsFilterRuleRowsHtml(savedRules, {interactive: false', APPLICATIONS_INDEX_HTML)

    def test_secrets_filter_card_is_preserved_as_standalone_action_queue_card(self) -> None:
        expected_js = (
            "function gitProjectWizardStepIsSecretsFilterCandidate(step = {})",
            "function gitProjectNormalizeSecretsFilterStep(step = {})",
            "const secretsFilterStep = actions.find((step) => wizardStepIsSecretsFilterCandidate(step, hooks)) || null;",
            "displayActions.push(normalizeSecretsFilterStep(step));",
            "displayActions.splice(insertAt, 0, normalized);",
            'label: "Review Security / Secrets"',
            '"Open Security Review"',
            "safety gate",
            "secrets scan",
        )
        for snippet in expected_js:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_MODULE_JS)

    def test_dirty_planner_emits_standalone_secrets_filter_before_commit_cards(self) -> None:
        expected_snippets = (
            "security_candidate_paths: list[str] = []",
            "for candidate_path in [*source_untracked, *staged]:",
            'filter_step = step(\n                order,\n                "secrets_filter"',
            'filter_step["secrets_filter"] = secrets_filter_payload(',
            'track_step["commit_review"] = commit_review_payload(',
            'commit_step["commit_review"] = commit_review_payload(',
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_DIRTY_PY)



    def test_commit_card_is_final_workbench_with_wunderbaum_file_tree(self) -> None:
        expected_snippets = (
            "function gitProjectCommitWorkbenchHtml(step = {})",
            "function gitProjectCommitHeaderHtml(review = {})",
            "function gitProjectCommitConfigStripHtml(review = {})",
            "function gitProjectCommitCenterHtml(step = {}, selectedPanel = \"gate_summary\")",
            "function gitProjectCommitComposeHtml(review = {})",
            "function gitProjectCommitGateSummaryHtml(review = {})",
            "function gitProjectCommitSecuritySecretsPaneHtml(review = {})",
            "function gitProjectCommitStagePreviewHtml(step = {})",
            "function gitProjectCommitStageReviewStatusHtml()",
            "function gitProjectCommitStageReviewFlowHtml()",
            "function gitProjectCommitUpdateReviewStatus(workbench, paths = [])",
            "function gitProjectCommitUpdateFinalReadiness(workbench, paths = [])",
            "function gitProjectCommitCreateHtml(step = {})",
            "function gitProjectCommitExecutionPaneHtml(message = \"\")",
            "function gitProjectWireCommitExecution(workbench)",
            "function gitProjectCommitRunExecution(workbench)",
            "function gitProjectCommitStartEventStream(workbench, data = {})",
            "function gitProjectCommitRefreshAfterCompletion(workbench, event = {})",
            "function gitProjectCommitRefreshWorkbenchFromReview(workbench, step = {})",
            "function gitProjectCommitRefreshExecutionRemainingFiles(workbench, review = {})",
            "function gitProjectCommitReviewCandidatePaths(review = {})",
            "function gitProjectCommitEventCreatedRealCommit(event = {})",
            "function gitProjectCommitExecutionPayload(workbench, paths = [], state = {})",
            "data-git-commit-execution-pane",
            "role=\"dialog\"",
            "data-git-commit-summary-value=\"branch\"",
            "data-git-commit-execution-option=\"dry_run\"",
            "data-git-commit-execution-option=\"one_at_a_time\"",
            "remaining uncommitted",
            "Commit finished. Refreshing uncommitted file list",
            "gitProjectCommitRefreshAfterCompletion(workbench, payload)",
            "Stop commit in progress",
            "Do Git Commit",
            "Dry Run",
            "One at a time",
            "Create one commit per selected file, one Git call at a time.",
            "backend commit action",
            "function gitProjectCommitBasketHtml(review = {})",
            "GIT_PROJECT_WUNDERBAUM_VERSION",
            "cdn.jsdelivr.net/gh/mar10/wunderbaum",
            "bootstrap-icons",
            "function gitProjectWunderbaumConstructor()",
            "function gitProjectCommitCandidateItems(review = {})",
            "function gitProjectCommitNormalizeStatus(item = {})",
            "function gitProjectCommitStatusDisplay(status = \"\")",
            "function gitProjectCommitTreeStats(nodes = [])",
            "function gitProjectCommitAnnotateDirectoryStats(node)",
            "function gitProjectCommitFinalizeDirectorySelection(node)",
            "function gitProjectCommitSelectedFilesFromWunderbaum(tree)",
            "function gitProjectCommitSizeWunderbaum(element)",
            "function gitProjectCommitNotifyWunderbaumViewport(tree, change = \"resize\")",
            "function gitProjectCommitScrollWunderbaumTop(element)",
            "gitProjectCommitNotifyWunderbaumViewport(event.tree, \"resize\")",
            "gitProjectCommitNotifyWunderbaumViewport(tree, \"scroll\")",
            "element.scrollTop = 0",
            "expanded: false,",
            "selectMode: \"hier\"",
            "types: {",
            "type: \"dir\"",
            "type: \"file\"",
            "data-git-commit-workbench",
            "data-git-commit-panel",
            "data-git-commit-field",
            "data-git-commit-basket",
            "data-git-commit-tree-source",
            "data-git-commit-tree-fallback",
            "Repo file tree",
            "Total candidates",
            "+ Untracked",
            "✓ Changed",
            "Security / Secrets review",
            "data-git-commit-panel=\"security_secrets\"",
            "This is a commit readiness summary. Open the Security / Secrets card to run or review the full scan before committing.",
            "Blocked files stay out of the commit basket",
            "git add -- <selected files>",
            "git diff --cached --stat",
            "git diff --cached --check",
            "Review selected files",
            "Selected Files Preview",
            "Developer diagnostics",
            "data-git-commit-final-readiness",
            "data-git-commit-review-count",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)

        for warning_scope_snippet in (
            "Commit blocking is scoped to the selected files",
            "Selected hard blockers:",
            "WARNINGS ACCEPTED",
            "Unselected repo warnings remain visible as context only",
        ):
            with self.subTest(warning_scope_snippet=warning_scope_snippet):
                self.assertIn(warning_scope_snippet, APPLICATIONS_INDEX_HTML)

        commit_region = APPLICATIONS_INDEX_HTML[
            APPLICATIONS_INDEX_HTML.index("const GIT_PROJECT_WUNDERBAUM_VERSION"):
            APPLICATIONS_INDEX_HTML.index("function gitProjectSecretsFilterRuleRowsHtml(")
        ]
        forbidden_snippets = (
            "Commit steps",
            "function gitProjectCommitStepsHtml",
            "data-git-commit-step",
            "git-project-commit-left",
            "Upstream gates",
            "Commit workflow",
            "Privacy scan",
            "data-git-security-rule",
            "detect-secrets",
            "Run selected rules only",
            "Save rule choices",
            "data-git-open-related-card",
            "Open .gitignore card",
            "Open Secrets / Filter card",
            "Re-run planner",
        )
        for snippet in forbidden_snippets:
            with self.subTest(forbidden_snippet=snippet):
                self.assertNotIn(snippet, commit_region)

        for css_snippet in (
            ".git-project-commit-header",
            ".git-project-commit-status-strip",
            ".git-project-commit-config-strip",
            ".git-project-commit-body",
            ".git-project-commit-center",
            ".git-project-commit-right",
            ".git-project-commit-compose",
            ".git-project-commit-gate-summary",
            ".git-project-commit-security-secrets",
            ".git-project-commit-security-grid",
            ".git-project-commit-security-blocked",
            ".git-project-commit-stage-preview",
            ".git-project-commit-review-status",
            ".git-project-commit-review-flow",
            ".git-project-commit-review-stats",
            ".git-project-commit-dev-diagnostics",
            ".git-project-commit-execution-pane",
            ".git-project-commit-execution-overlay",
            ".git-project-commit-execution-dialog",
            ".git-project-commit-execution-options",
            ".git-project-commit-execution-results",
            ".git-project-commit-wunderbaum",
            ".git-project-commit-tree-dir",
            ".git-project-commit-tree-file",
            ".git-project-commit-tree-file-untracked",
            ".git-project-commit-tree-file-tracked",
            ".git-project-commit-tree-fallback",
            ".wb-list-container",
            ".wb-node-list",
            ".wb-title",
            "overflow-y: auto !important;",
            "overflow: visible !important;",
            "--wb-row-outer-height: 22px !important;",
            "overflow-wrap: normal !important;",
        ):
            with self.subTest(css_snippet=css_snippet):
                self.assertIn(css_snippet, GIT_TOOLS_CSS)


    def test_archive_files_card_uses_commit_style_status_group_workbench(self) -> None:
        expected_js = (
            "function gitProjectStepIsArchiveCard(step = {})",
            "function gitProjectArchiveWorkbenchHtml(step = {})",
            "data-git-archive-workbench",
            "Archive Files...",
            "Changes to be committed",
            "Changes not staged for commit",
            "Untracked files",
            '"/api/applications/git/project/archive-files/status"',
            '"/api/applications/git/project/archive-files"',
            "gitProjectInitializeArchiveWorkbenches(container)",
        )
        for snippet in expected_js:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_MODULE_JS)

        expected_backend = (
            "def git_project_archive_files_status",
            "def archive_git_project_files",
            '"id": "archive_files"',
            '"label": "Archive Files..."',
            '"/api/applications/git/project/archive-files/status"',
            '"/api/applications/git/project/archive-files"',
        )
        combined_backend = GIT_TOOLS_PY + VIEWPORT_ROUTES_GIT + VIEWPORT_ROUTE_DISPATCH
        for snippet in expected_backend:
            with self.subTest(backend_snippet=snippet):
                self.assertIn(snippet, combined_backend)

        for snippet in (
            ".git-project-archive-workbench",
            ".git-project-archive-groups",
            ".git-project-archive-file-row",
        ):
            with self.subTest(css_snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_CSS)


    def test_action_queue_closed_cards_are_simple_launch_cards(self) -> None:
        expected_snippets = (
            "function gitProjectClosedCardPurpose(step = {})",
            "function gitProjectClosedCardChips(step = {})",
            "function gitProjectClosedCardSummaryHtml(step = {}, stepComponentId = \"\", stepLabel = \"\")",
            "git-project-mini-action-card",
            "git-project-mini-card-summary",
            "git-project-mini-card-chips",
            "Move selected work out of this branch without losing it.",
            "Capture intentional work in a local commit.",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_MODULE_JS + GIT_TOOLS_CSS)

        render_start = GIT_TOOLS_MODULE_JS.index("const renderStepCard = (step, displayIndex) => {")
        render_end = GIT_TOOLS_MODULE_JS.index("  const renderStepGroup =", render_start)
        render_step_card = GIT_TOOLS_MODULE_JS[render_start:render_end]
        forbidden_closed_card_snippets = (
            "Command preview",
            "pathSummary",
            "renderGitProjectCommandBox",
            "gitProjectCommitCardAttachmentHtml",
            "${paths}",
            "${commandPreview}",
            "${commandBox}",
            "${commitCardNote}",
        )
        for snippet in forbidden_closed_card_snippets:
            with self.subTest(forbidden_closed_card_snippet=snippet):
                self.assertNotIn(snippet, render_step_card)

        self.assertIn("gitProjectClosedCardSummaryHtml(step, stepComponentId, stepLabel)", render_step_card)
        self.assertIn("${cardSubscreen}", render_step_card)

    def test_git_project_selector_api_routes_are_registered(self) -> None:
        expected_routes = (
            '"/api/applications/git/projects"',
            '"/api/applications/git/project/add"',
            '"/api/applications/git/project/select"',
            '"/api/applications/git/project/archive"',
            '"/api/applications/git/project/restore"',
            '"/api/applications/git/project/lock"',
            '"/api/applications/git/project/unlock"',
            '"/api/applications/git/project/inspect"',
            '"/api/applications/git/project/action/run"',
            '"/api/applications/git/project/gitignore/save"',
            "/api/applications/git/project/secrets-filter/stream",
            '"/api/applications/git/server/operation/status"',
            '"/api/applications/git/server/operation/cancel"',
        )
        expected_handlers = (
            "self._handle_git_projects()",
            "self._handle_git_project_add()",
            "self._handle_git_project_select()",
            "self._handle_git_project_archive()",
            "self._handle_git_project_restore()",
            "self._handle_git_project_lock()",
            "self._handle_git_project_unlock()",
            "self._handle_git_project_inspect()",
            "self._handle_git_project_action_run()",
            "self._handle_git_project_gitignore_save()",
            "self._handle_git_project_secrets_filter_stream()",
        )
        for snippet in expected_routes + expected_handlers:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, VIEWPORT_ROUTE_DISPATCH)

    def test_gitignore_save_route_uses_safe_service_path(self) -> None:
        expected_route_snippets = (
            "def _handle_git_project_gitignore_save(self) -> None:",
            "save_project_gitignore(",
            "gitignore_path=gitignore_path",
            "lines=lines",
            "newline=newline",
        )
        for snippet in expected_route_snippets:
            with self.subTest(route_snippet=snippet):
                self.assertIn(snippet, VIEWPORT_ROUTES_GIT)
        for snippet in (
            "def save_project_gitignore(",
            "dirty.write_gitignore_file(",
            "Unlock the selected project before saving .gitignore.",
            "Refusing to save .gitignore for a folder inside a different parent Git repository.",
        ):
            with self.subTest(service_snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_PY)
        self.assertNotIn("git ls-files --others --exclude-standard", VIEWPORT_ROUTES_GIT)

    def test_git_page_wizard_send_to_console_is_guarded_until_complete(self) -> None:
        expected_snippets = (
            "const GIT_PAGE_WIZARD_REQUIRED_KEYS = GIT_PAGE_WIZARD_STEPS.map((step) => step.key);",
            "function gitPageWizardIsComplete()",
            "gitPageWizardSendConsole.disabled = !complete;",
            "if (!gitPageWizardIsComplete())",
            "Page Element Wizard is incomplete. Finish these fields first:",
            "Complete required fields before sending to Git Console:",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)

    def test_git_page_wizard_prompt_documents_safe_git_workflow(self) -> None:
        expected_snippets = (
            "Workflow expectations:",
            "target, purpose, kind, label, owner, and behavior",
            "Ask AI / Generate Shim or Plan Shim before any shim is reviewed or run",
            "Review the generated shim and run a dry-run before applying repository changes.",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)

    def test_git_page_wizard_console_sent_state_resets_when_draft_changes(self) -> None:
        expected_snippets = (
            "let gitPageWizardConsoleSent = false;",
            "gitPageWizardConsoleSent = false;",
            "gitPageWizardConsoleSent = true;",
            'return gitPageWizardConsoleSent ? "console" : "draft";',
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)

    def test_git_tools_progressive_shell_starts_with_project_selector_then_server(self) -> None:
        project_index = GIT_TOOLS_APP_HTML.index('id="git-project-selector-panel"')
        accordion_index = GIT_TOOLS_APP_HTML.index('id="git-workflow-accordion"')
        server_index = GIT_TOOLS_APP_HTML.index('id="git-server-pane"')
        self.assertLess(project_index, accordion_index)
        self.assertLess(accordion_index, server_index)
        expected_snippets = (
            'data-git-progressive-ui="true"',
            'data-widget-label="Projects"',
            "Current Projects",
            "Git / Gitea workflow",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_APP_HTML)
        removed_snippets = (
            'id="git-repo-status-panel"',
            'id="git-ai-request-panel"',
            'data-git-workflow-primary="ai-request"',
            "Current Git Repository",
            "Ask AI to do Git work",
            "technical patch, shim, console, and dry-run panels",
            "Project Selector",
            "Pick a project on the right.",
            "prioritized workflow queue here",
            "The Main Computer project is VIP",
            "add a project path below",
            'class="git-project-header"',
        )
        for snippet in removed_snippets:
            with self.subTest(removed_snippet=snippet):
                self.assertNotIn(snippet, GIT_TOOLS_APP_HTML)


    def test_git_tools_workflow_sections_only_expose_git_server(self) -> None:
        sections = re.findall(r'<details class="[^"]*git-workflow-section[^"]*"[^>]*data-git-workflow-section="([^"]+)"', GIT_TOOLS_APP_HTML)
        self.assertEqual(sections, ["git-server"])
        match = re.search(
            r'<details class="[^"]*git-workflow-section[^"]*"[^>]*id="git-server-pane"[^>]*>',
            GIT_TOOLS_APP_HTML,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn(" open", match.group(0))
        self.assertNotIn(" hidden", match.group(0))
        removed_sections = (
            "ai-interpretation",
            "proposed-plan",
            "patch-inventory",
            "patch-actions",
            "shim-builder",
            "dry-run",
            "advanced-diagnostics",
        )
        for section in removed_sections:
            with self.subTest(section=section):
                self.assertNotIn(f'data-git-workflow-section="{section}"', GIT_TOOLS_APP_HTML)


    def test_git_tools_javascript_keeps_server_visible_without_legacy_middle_sections(self) -> None:
        expected_snippets = (
            "function initializeGitWorkflowDisclosure()",
            "function expandGitWorkflowSection(sectionName",
            "function gitToolsRepoDirValue(fallback = \".\")",
            "function initializeGitServerHiddenPane()",
            "setGitServerPaneVisible(true, {persist: false});",
            "gitServerPane.hidden = false;",
            "gitServerPane.open = true;",
            "async function refreshGitServerStatus()",
            "if (!gitServerPane) return;",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)


    def test_git_tools_git_server_pane_is_visible_and_uses_selected_project_path(self) -> None:
        ui_snippets = (
            'id="git-server-pane"',
            'class="git-workflow-section git-tools-card git-server-pane app-widget mc-app-pane"',
            'data-git-workflow-section="git-server"',
            'data-git-server-pane open',
            "Git / Gitea workflow",
            "Publish this repo to local Gitea",
            'id="git-server-use-local"',
            "Reset suggested target",
            'id="git-server-remote-apply-local"',
            "Create / verify repo + configure remote",
            'id="git-server-push-local"',
            "Push to Local Gitea",
            "Keep GitHub/GitLab as origin and add Local Gitea as a second remote",
            "Local Gitea Git remote",
            "local-gitea",
            "Checking selected project before showing a target.",
            "Review Local Gitea target",
            "This does <strong>not</strong> create multiple origins.",
            "Replace origin with Local Gitea",
            "Show raw server output / command log",
            'id="git-server-operation-cancel"',
            "Cancel Running Command",
            'id="git-server-operation-refresh"',
            "Refresh Operation Log",
            'id="git-server-use-external"',
            "Use External Direct",
            'id="git-server-mirror-plan"',
            "Plan Server → External Mirror",
            'id="git-server-mirror-setup"',
            "Set Up Server → External Mirror",
            'id="git-server-remote-command"',
            'data-git-server-remote-preset="set-url"',
            "Copy to Git Console",
        )
        for snippet in ui_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, GIT_TOOLS_APP_HTML)
        js_snippets = (
            "function initializeGitServerHiddenPane()",
            "function runGitServerAction(action)",
            "function fillGitServerRemoteCommand(preset)",
            "function useLocalGitServerRemote()",
            "function applyLocalGitServerRemote()",
            "function pushLocalGitServerRemote()",
            "function useExternalGitRemoteDirect()",
            "function planGiteaPushMirror()",
            "function setupGiteaPushMirror()",
            "function runGitServerRemoteCommand()",
            "function startGitServerProgress(",
            "function gitToolsOperationErrorText(",
            "function refreshGitOperationStatus(",
            "function cancelGitServerOperation(",
            "function runGitServerOperationRequest(",
            "function gitServerDockerUnavailableText(",
            "function ensureGitServerDockerAvailable(",
            "Opening http://localhost:3000 in your browser, or starting Gitea somewhere else, does not give this backend process Docker access.",
            "function applyGitServerDockerAvailability(",
            "if (!(await ensureGitServerDockerAvailable",
            "function gitServerIsLocalServerUrl(",
            "function refreshGitServerTargetPrefunk(",
            "function gitServerApplyTargetPrefunk(",
            "function gitServerEnsureConfigurable(",
            "function updateGitServerRemoteChoicePreview()",
            'const LOCAL_GITEA_REMOTE_NAME = "local-gitea";',
            "function setGitServerRemoteMode(mode)",
            '"/api/applications/git/server/status"',
            '"/api/applications/git/server/target-prefunk"',
            '"/api/applications/git/server/operation/status"',
            '"/api/applications/git/server/operation/cancel"',
            '"/api/applications/git/server/action"',
            '"/api/applications/git/server/setup-local"',
            '"/api/applications/git/server/push-local"',
            '"/api/applications/git/server/mirror/plan"',
            '"/api/applications/git/server/mirror/setup"',
            '"/api/applications/git/console/run"',
            "git remote set-url",
            'repo_dir: gitToolsRepoDirValue(".")',
            'bindGitToolsControl(gitServerStart, "click", () => runGitServerAction("start"));',
            'bindGitToolsControl(gitServerUseLocal, "click", useLocalGitServerRemote);',
            'bindGitToolsControl(gitServerRemoteApplyLocal, "click", applyLocalGitServerRemote);',
            'bindGitToolsControl(gitServerPushLocal, "click", pushLocalGitServerRemote);',
            'bindGitToolsControl(gitServerOperationCancel, "click", cancelGitServerOperation);',
            'bindGitToolsControl(gitServerOperationRefresh, "click", () => refreshGitOperationStatus({renderOutput: true}));',
            'bindGitToolsControl(gitServerUseExternal, "click", useExternalGitRemoteDirect);',
            'bindGitToolsControl(gitServerMirrorPlan, "click", planGiteaPushMirror);',
            'bindGitToolsControl(gitServerMirrorSetup, "click", setupGiteaPushMirror);',
            'bindGitToolsControl(gitServerRemoteRun, "click", runGitServerRemoteCommand);',
        )
        for snippet in js_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)
        removed_snippets = (
            'id="git-server-hidden-pane"',
            'data-git-server-hidden-pane hidden',
            "Ctrl+Shift+G",
            "?gitServerPane=1",
        )
        for snippet in removed_snippets:
            with self.subTest(removed_snippet=snippet):
                self.assertNotIn(snippet, GIT_TOOLS_APP_HTML)
                self.assertNotIn(snippet, TASK_MANAGER_JS)


    def test_git_operation_progress_has_single_output_writer(self) -> None:
        match = re.search(
            r"function startGitServerProgress\(.*?\n\}\nfunction gitServerOperationButtons",
            APPLICATIONS_INDEX_HTML,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn("gitServerOperationState.textContent", body)
        self.assertNotIn("gitServerOutput.textContent", body)


    def test_git_tools_uses_shared_app_layout_contract(self) -> None:
        expected_snippets = (
            'class="git-tools-shell git-tools-workflow-shell mc-app-shell"',
            'class="git-tools-hero mc-app-workspace"',
            'class="git-workflow-accordion mc-app-workspace"',
            "mc-app-pane",
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)
        self.assertNotRegex(GIT_TOOLS_CSS, r"\.git-tools-app\s*\{[^}]*height:\s*100%")
        self.assertNotRegex(GIT_TOOLS_CSS, r"\.git-tools-shell\s*\{[^}]*height:\s*100%")
        self.assertNotRegex(GIT_TOOLS_CSS, r"\.git-tools-workflow-shell\s*\{[^}]*overflow:\s*auto")
        self.assertNotRegex(
            GIT_TOOLS_CSS,
            r"\.git-tools-workflow-shell\s*\{[^}]*grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)",
        )
        self.assertIn("grid-template-rows: auto auto;", GIT_TOOLS_CSS)

    def test_git_tools_responsive_css_is_owned_by_git_tools_stylesheet(self) -> None:
        self.assertNotIn("git-tools", STATUS_AND_RESPONSIVE_CSS)
        self.assertIn("@container (max-width: 980px)", GIT_TOOLS_CSS)
        self.assertIn("@container (max-width: 620px)", GIT_TOOLS_CSS)
        self.assertIn(".git-tools-hero", GIT_TOOLS_CSS)
        self.assertIn(".git-patch-actions-toolbar", GIT_TOOLS_CSS)

    def test_git_tools_legacy_layout_vocabulary_is_removed(self) -> None:
        legacy_snippets = (
            "git-tools-sidebar",
            "git-tools-detail-toolbar",
            "git-tools-output-grid",
            ".git-tools-detail",
            "git-patch-actions-toolbar",
        )
        for snippet in legacy_snippets:
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, GIT_TOOLS_APP_HTML)


    def test_git_tools_event_bindings_are_consistently_guarded(self) -> None:
        expected_snippets = (
            "function bindGitToolsControl(control, eventName, handler)",
            'bindGitToolsControl(gitStatusRefresh, "click", refreshGitStatus);',
            'bindGitToolsControl(gitPatchesRefresh, "click", refreshGitPatches);',
            'bindGitToolsControl(gitPatchPreview, "click", previewGitPatch);',
            'bindGitToolsControl(gitPatchDryRun, "click", runGitPatchDryRun);',
            'bindGitToolsControl(gitDryRunRefresh, "click", loadGitDryRun);',
        )
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, APPLICATIONS_INDEX_HTML)
        unguarded_snippets = (
            'gitStatusRefresh.addEventListener("click"',
            'gitPatchesRefresh.addEventListener("click"',
            'gitPatchPreview.addEventListener("click"',
            'gitPatchDryRun.addEventListener("click"',
            'gitDryRunRefresh.addEventListener("click"',
        )
        for snippet in unguarded_snippets:
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, APPLICATIONS_INDEX_HTML)



if __name__ == "__main__":
    unittest.main()
