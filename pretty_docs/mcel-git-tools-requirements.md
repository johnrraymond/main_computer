# MCEL Git Tools Requirements

## Status

This is the documentation-first requirements contract for the Git Tools app.

The current implementation already has a substantial Git Tools workbench, project cards, status APIs, patch inventory helpers, Git ignore/filter/secrets workbenches, project-level publishing plans, and a real MCEL semantic adapter for a governed push slice. It does **not** yet have full application semantic readiness through the MCEL adapter registry.

So this document must be read as:

```text
current: scope-limited semantic runtime for governed publishing
planned: full Git Tools semantic runtime for repository inspection, project publishing, file triage, recovery, and advanced Git evidence
```

The purpose of this document is to make Git Tools requirements stable enough that MCEL Lab can later parse them, compare them with the live app, generate finding candidates, and drive code/test updates without relying on loose prose.

```mcel-app
id: git-tools
title: Git Tools
status: specified
current_runtime_status: scope-limited-semantic-runtime
current_semantic_runtime_scope: governed-publish-partial
target_runtime_status: full-application-semantic-runtime
dominant_object: RepositoryProject
primary_user_goal: >
  Inspect repository state, triage files, create safe commits, and publish
  selected project work through governed Git/Gitea actions without exposing
  raw Git plumbing as the default user path.
current_sources:
  - main_computer/web/applications/apps/git-tools.html
  - main_computer/web/applications/scripts/git-tools.js
  - main_computer/web/applications/scripts/git-tools-mcel.js
  - main_computer/web/applications/scripts/git-tools-layout-contract.js
  - main_computer/web/applications/scripts/git-tools-status-api.js
  - main_computer/web/applications/scripts/git-tools-status-refresh-bridge.js
  - main_computer/web/applications/scripts/git-tools-project-panel.js
  - main_computer/web/applications/scripts/git-tools-project-workflow.js
  - main_computer/web/applications/scripts/git-tools-project-card-subscreen.js
  - main_computer/web/applications/scripts/git-tools-project-shared.js
  - main_computer/web/applications/scripts/git-tools-server-panel.js
  - main_computer/web/applications/scripts/git-tools-patch-inventory.js
  - main_computer/web/applications/scripts/git-tools-file-basket.js
  - main_computer/web/applications/scripts/git-tools-file-basket-contract-view.js
  - main_computer/web/applications/scripts/git-tools-gitignore-workbench.js
  - main_computer/web/applications/scripts/git-tools-secrets-filter-workbench.js
  - main_computer/web/applications/scripts/git-tools-semantic-adapter.js
  - main_computer/web/applications/scripts/git-tools-semantic-panel.js
verification:
  - tests/test_mcel_git_tools_layout.py
  - tests/test_mcel_git_tools_semantic_adapter.py
  - tests/test_mcel_git_tools_semantic_panel.py
  - tests/test_git_tools_project_panel.py
  - tests/test_git_tools_patch_inventory.py
  - tests/test_git_tools_file_basket_contract_view.py
  - tests/test_mcel_documentation.py
```

## Roadmap use cases

These use cases define Git Tools as a governed repository workflow app. The existing local-Gitea push slice is the reference implementation; the remaining daily Git workflows should reach the same inspect, evidence, preflight, confirm, execute, receipt, and recover standard.

### Use case 1: push current branch to local Gitea

A user has committed local work and wants to push the current branch to the configured local Gitea remote without using raw Git commands.

```mcel-use-case
id: git-tools.use-case.push-current-branch-local-gitea
app: git-tools
status: partially-implemented
type: roadmap-use-case
primary_object: GovernedPushPlan
user_goal: >
  Inspect repository, branch, and remote evidence, confirm the intended local
  Gitea target, push the current branch explicitly, and receive success or
  recovery evidence.
current_support:
  - scope-limited semantic adapter for governed publishing
  - pushCurrentBranch executable intent
  - preparePush preflight path
  - confirmation and recovery receipts
  - local-Gitea project publishing documentation
planned_support:
  - complete remote inspection intent coverage
  - complete patch inventory and working-tree read intent coverage before push
acceptance:
  - Repository root, current branch, and remote target are visible before push.
  - Push requires explicit confirmation.
  - Commit and push remain separate actions.
  - A successful push produces a receipt tied to the branch and remote.
  - A failed push produces recovery guidance without pretending the remote is updated.
layout_implications:
  - branch and remote identity stay near the push action
  - push evidence is separate from local commit evidence
  - raw Git plumbing remains advanced evidence, not the default path
```

### Use case 2: add a file or directory to `.gitignore`

A user sees generated or unwanted files in the working tree and wants to add the right ignore rule without confusing ignored, untracked, and already-tracked files.

```mcel-use-case
id: git-tools.use-case.add-ignore-rule
app: git-tools
status: planned
type: roadmap-use-case
primary_object: GitIgnoreRulePlan
user_goal: >
  Select an untracked file or directory, preview the proposed .gitignore rule,
  understand whether the target is already tracked, apply the ignore change, and
  refresh repository evidence.
current_support:
  - Git ignore/filter workbench files
  - repository status and file-basket surfaces
  - git-tools-gitignore-workbench.js source boundary
planned_support:
  - semantic addIgnoreRule intent
  - tracked-vs-untracked preflight
  - .gitignore write receipt and recovery guidance
acceptance:
  - The selected file or directory is visible before creating the rule.
  - The proposed .gitignore entry is previewed before write.
  - Already-tracked files warn that ignore alone will not untrack them.
  - The .gitignore file is changed only after explicit confirmation.
  - Repository status refreshes after the rule is written.
layout_implications:
  - ignore actions live near file-triage evidence
  - tracked/untracked/ignored states must be visually distinct
  - destructive untracking is not hidden inside the ignore workflow
```

### Use case 3: switch branches safely

A user wants to switch to another branch while preserving or resolving local work instead of silently overwriting files.

```mcel-use-case
id: git-tools.use-case.switch-branch-safely
app: git-tools
status: planned
type: roadmap-use-case
primary_object: BranchSwitchPlan
user_goal: >
  Inspect the current branch, available branch targets, and dirty working-tree
  state, then switch branches only when local work is safe or explicitly handled.
current_support:
  - repository identity and branch evidence surfaces
  - status APIs and semantic state classification
planned_support:
  - inspectBranches intent
  - branch switch preflight for dirty/conflicting changes
  - branch-switch receipt and recovery guidance
acceptance:
  - Current branch and target branch are visible before switching.
  - Dirty working-tree state blocks unsafe switching by default.
  - The user must choose a safe path such as commit, stash, discard, or cancel.
  - Switching branches refreshes repository and working-tree state.
  - Local work is never silently discarded.
layout_implications:
  - branch navigation is a repository state transition, not a raw command box
  - dirty state must stay visible near branch-switch controls
  - discard paths require a danger boundary
```

### Use case 4: select files, stage, and commit

A user wants to create a local commit from a deliberate subset of changed files while leaving unrelated work untouched.

```mcel-use-case
id: git-tools.use-case.select-files-stage-commit
app: git-tools
status: planned
type: roadmap-use-case
primary_object: CommitPlan
user_goal: >
  Inspect changed files, preview diffs, select the files that belong together,
  stage only those files, write a commit message, create the commit, and keep
  unselected changes untouched.
current_support:
  - file basket and contract view sources
  - status APIs
  - patch inventory helpers
  - secrets/filter workbench surfaces
planned_support:
  - inspectWorkingTree executable intent
  - selectFilesForCommit intent
  - stageSelectedFiles intent
  - commitSelectedFiles intent
  - commit receipt and recovery guidance
acceptance:
  - Changed, deleted, and untracked files are grouped by status.
  - The user can preview diffs before selecting files.
  - Stage/commit includes only selected files.
  - Secrets, generated junk, and oversized artifacts are flagged before commit.
  - Commit message is required before creating local history.
  - Unselected changes remain in the working tree after commit.
layout_implications:
  - file selection, diff evidence, and commit controls stay connected
  - commit evidence is local-repository evidence, not remote publish evidence
  - push remains a separate governed action after commit
```

## Product law

Git Tools is not a terminal with buttons. It is a repository evidence and governed-publishing workbench.

Its core law is:

```text
Repository state must be inspected before mutation.
Publishing must be governed, preflighted, confirmed, and receipted.
Project-level actions belong on the selected project, not hidden inside server plumbing.
Raw Git commands remain advanced or prohibited unless an explicit execution adapter exists.
Remote sync is a user-governed action, never an implicit side effect of save, inspect, refresh, or checkpoint workflows.
```

```mcel-requirement
id: git-tools.repository.evidence-first
app: git-tools
status: specified
type: product-law
aspect: evidence
object: RepositoryProject
requirement: >
  Git Tools must show repository evidence before any commit, push, remote
  repair, ignore-rule generation, file filtering, or recovery action. The user
  should be able to see which repository, branch, remote, files, gates, and
  backend result govern the action.
current_state: >
  The current app has project cards, status API integration, patch inventory,
  file basket workbenches, and a semantic panel. The full read/inspect intent
  coverage is still incomplete in the MCEL domain adapter.
acceptance:
  - Repository root and selected project identity are visible.
  - Branch, dirty state, ahead/behind state, and remote target are visible before push.
  - File selection and ignore/filter evidence are visible before commit or triage.
  - Backend errors are shown as evidence, not hidden behind generic failure labels.
  - Receipts are retained for governed actions.
```

```mcel-requirement
id: git-tools.push.governed
app: git-tools
status: partially-implemented
type: safety-law
aspect: actions
object: RepositoryProject
requirement: >
  Push actions must be governed by current repository state, remote target,
  branch identity, preflight checks, explicit confirmation, execution receipts,
  and recovery classification.
current_state: >
  The Git Tools semantic adapter currently exposes refreshStatus and
  pushCurrentBranch as executable, preparePush as preflight-only, and manual
  command execution as prohibited. This proves a governed-publish slice but
  not full app readiness.
acceptance:
  - Refresh status happens before risky push decisions.
  - Push target is explicit and not inferred as GitHub-only.
  - Preflight blocks missing repository, missing branch, missing remote, stale state, and backend errors.
  - Confirmation is required before mutation.
  - Push execution emits a receipt with repository, branch, remote, result, and recovery hints.
```

```mcel-requirement
id: git-tools.project-card.primary-publishing
app: git-tools
status: planned
type: workflow-law
aspect: workflows
object: ProjectCard
requirement: >
  Everyday project publishing belongs on the selected project card. The user
  should not need to open a server-control panel to push the current project to
  local Gitea or to an explicit remote origin.
current_state: >
  The desired project-level publishing model is documented in
  pretty_docs/git-tools-project-level-publishing.md. This requirements contract
  treats that note as one concrete workflow slice of the broader Git Tools app.
acceptance:
  - Each active project card shows publishing readiness.
  - Push to Local Gitea is available when the local target can be verified or prepared.
  - Push Remote Origin is available only when origin is configured and governed.
  - Server lifecycle and repository publishing are visually separated.
  - Advanced server repair remains available without becoming the primary project path.
```

```mcel-requirement
id: git-tools.remote-sync.explicit
app: git-tools
status: specified
type: safety-law
aspect: actions
object: RemoteTarget
requirement: >
  Remote synchronization must always be explicit, governed, and separable from
  refresh, inspect, save, checkpoint, commit, and local archive workflows.
non_goals:
  - Do not push merely because a project was inspected.
  - Do not push merely because a file was saved in another app.
  - Do not hard-code origin as GitHub.
  - Do not create or rewrite remotes without visible evidence and confirmation.
acceptance:
  - Local Gitea and origin are displayed as distinct remote targets.
  - Remote push requires a selected target and confirmation.
  - Remote creation or repair is separated from push execution.
  - Remote sync receipts identify the target remote and URL family.
```

```mcel-requirement
id: git-tools.manual-command.prohibited
app: git-tools
status: specified
type: safety-law
aspect: actions
object: GitCommand
requirement: >
  Raw manual Git command execution must not be exposed as a normal Git Tools
  semantic intent. Until a separate command-execution adapter exists, manual
  command execution is prohibited by policy and should remain advanced evidence
  or delegated to Terminal with explicit boundaries.
current_state: >
  The current Git Tools semantic adapter classifies runManualCommand as
  prohibited.
acceptance:
  - Default Git Tools UI does not invite arbitrary Git command execution.
  - The semantic adapter reports runManualCommand as prohibited.
  - Advanced evidence can show the equivalent Git operations without executing raw commands.
  - Recovery guidance points to governed actions before suggesting terminal use.
```

```mcel-requirement
id: git-tools.file-triage.explicit-selection
app: git-tools
status: planned
type: workflow-law
aspect: workflows
object: FileTriage
requirement: >
  Changed-file triage must be explicit. The user should be able to select
  exactly which files are included, ignored, filtered, committed, or pushed
  as branch work, with evidence for why each file is safe or risky.
current_state: >
  The app has file basket, file basket contract view, gitignore workbench, and
  secrets/filter workbench code. These should be unified under a Git Tools
  semantic file-triage model.
acceptance:
  - Changed files are grouped by tracked, untracked, ignored, generated, secret-risk, and patch artifact status.
  - Commit preparation uses explicit file selection.
  - Ignore-rule previews are shown before modifying .gitignore.
  - Secret/filter checks are visible before commit and push.
  - Selected-file branch work is separate from full-project publish.
```

```mcel-requirement
id: git-tools.secrets-filter.required-gates
app: git-tools
status: planned
type: safety-law
aspect: evidence
object: SafetyGate
requirement: >
  Secret detection, filter readiness, local.secrets status, and pre-commit
  hook readiness must be first-class gates for commit and push workflows when
  those features are available in the repository.
acceptance:
  - Git Tools displays whether local.secrets is configured.
  - Git Tools displays whether .githooks/pre-commit is present or required.
  - Secret-risk files are blocked or escalated before commit.
  - Filter readiness appears in the same evidence surface as file triage.
  - Gate bypass requires an explicit advanced-policy decision and receipt.
```

```mcel-requirement
id: git-tools.adapter.truth-gated-readiness
app: git-tools
status: specified
type: semantic-runtime
aspect: actions
object: GitToolsAdapter
requirement: >
  Git Tools must not be reported as fully semantically ready until every
  required intent is classified, executable or intentionally prohibited, and
  every risky intent has preflight, receipts, evidence mapping, failure classes,
  and recovery coverage.
current_state: >
  The current adapter is correctly reported as scope-limited rather than full:
  refreshStatus and pushCurrentBranch are executable; preparePush is
  preflight-only; inspectWorkingTree, inspectRemotes, and inspectPatchInventory
  are declared-only; runManualCommand is prohibited.
acceptance:
  - MCEL truth gate reports runtimeCoreReady only for the governed-publish slice.
  - MCEL truth gate does not report fullApplicationSemanticReady while read/inspect intents are declared-only.
  - Declared-only intents have corresponding implementation milestones.
  - Intent and recovery coverage audits remain machine-readable.
```

```mcel-requirement
id: git-tools.recovery.receipts
app: git-tools
status: partially-implemented
type: recovery-law
aspect: evidence
object: RecoveryPlan
requirement: >
  Failed or blocked Git Tools actions must produce durable recovery guidance
  that identifies the failure class, safe retry path, prohibited next actions,
  and evidence needed before the user retries or escalates.
current_state: >
  The semantic adapter has recovery classification and recovery coverage for
  the governed push slice.
acceptance:
  - Backend-unavailable, refresh-failed, stale-state, missing-remote, and push-failed cases are classified.
  - Recovery plans state whether retry is safe.
  - Recovery plans state which actions remain prohibited.
  - Receipts can be reviewed after the immediate action completes.
  - Recovery coverage participates in semantic readiness.
```

## Workbench anatomy

The Git Tools app should normally be inspected through these regions.

```mcel-region
id: git-tools.region.identity
app: git-tools
status: specified
region: identity
role: repository-identity-header
responsibility: >
  Identify the selected project, repository root, branch, remote target, backend
  freshness, and semantic runtime scope.
purpose: Active project, repository root, branch identity, backend state age, current runtime scope, and selected remote target.
expected_elements:
  - selected project name
  - repository root
  - current branch
  - active remote target
  - semantic runtime status
  - last refresh age
must_not_contain:
  - destructive controls
  - raw command input
```

```mcel-region
id: git-tools.region.navigation
app: git-tools
status: specified
region: navigation
role: repository-navigation
responsibility: >
  Let the user choose projects, workflow tabs, file baskets, patch inventory
  views, and support areas without mutating Git state.
purpose: Project list, active project cards, repository selection, patch inventory navigation, and workflow tabs.
expected_elements:
  - project cards
  - project search or picker
  - patch inventory entry points
  - file basket entry points
  - server/support navigation
must_not_contain:
  - default raw Git command execution
  - unconfirmed remote mutation
```

```mcel-region
id: git-tools.region.primary
app: git-tools
status: specified
region: primary
role: repository-workbench
responsibility: >
  Own the selected repository workflow, changed-file triage, project publishing
  strip, status summary, and commit/publish content.
purpose: Selected repository workbench, project publishing strip, changed-file triage, status summary, and current workflow content.
expected_elements:
  - selected project card
  - publishing readiness strip
  - working tree summary
  - changed-file groups
  - commit or publish workflow content
must_not_contain:
  - hidden server lifecycle controls
  - unrelated global Git administration as the dominant surface
```

```mcel-region
id: git-tools.region.inspector
app: git-tools
status: specified
region: inspector
role: preflight-inspector
responsibility: >
  Show remote configuration, selected-file evidence, ignore-rule previews,
  policy gates, and action-specific confirmation details.
purpose: Preflight details, remote configuration, selected-file evidence, policy gates, and action-specific explanations.
expected_elements:
  - preflight checklist
  - remote target details
  - selected files
  - secret/filter gate status
  - ignore-rule preview
  - confirmation prompt details
must_not_contain:
  - mutation controls without adjacent evidence
```

```mcel-region
id: git-tools.region.evidence
app: git-tools
status: specified
region: evidence
role: evidence-and-recovery-panel
responsibility: >
  Show status API output, semantic adapter evidence, intent coverage, receipts,
  backend errors, and recovery plans.
purpose: Status API output, semantic panel evidence, patch inventory, receipts, backend errors, and recovery plans.
expected_elements:
  - semantic adapter status
  - intent coverage
  - recovery coverage
  - receipts
  - backend errors
  - patch inventory evidence
must_not_contain:
  - source-of-truth controls that bypass the primary project workflow
```

```mcel-region
id: git-tools.region.actions
app: git-tools
status: specified
region: actions
role: governed-action-panel
responsibility: >
  Group Git actions by risk and route mutations through inspect, preflight,
  confirmation, execution, receipt, and recovery.
purpose: Governed repository actions grouped by risk: refresh, inspect, prepare, commit, publish, repair, and advanced support.
expected_elements:
  - refresh status
  - inspect working tree
  - inspect remotes
  - inspect patch inventory
  - prepare push
  - push selected target
  - commit selected files
must_not_contain:
  - arbitrary Git command input as a normal action
```

```mcel-region
id: git-tools.region.status
app: git-tools
status: specified
region: status
role: repository-status-strip
responsibility: >
  Keep clean, dirty, stale, blocked, running, failed, and completed
  repository/action status visible after refresh and mutation.
purpose: Persistent state line for clean/dirty/stale/blocked/running/failed/completed repository and adapter status.
expected_elements:
  - repository clean or dirty state
  - stale status warning
  - backend availability
  - running action
  - last receipt summary
  - policy block reason
must_not_contain:
  - hidden action results
```

```mcel-region
id: git-tools.region.advanced
app: git-tools
status: specified
region: advanced
role: advanced-git-boundary
responsibility: >
  Isolate server lifecycle, local Gitea administration, mirror setup, remote
  repair, raw payloads, and support diagnostics.
purpose: Server lifecycle, local Gitea administration, mirror setup, remote repair, raw evidence, and support diagnostics.
expected_elements:
  - server lifecycle controls
  - local Gitea diagnostics
  - remote repair helpers
  - mirror setup
  - raw backend payloads
  - advanced recovery notes
must_not_contain:
  - the ordinary project-card publish path
```

## Semantic intents

The app should expose these intents to the MCEL domain adapter registry.

```mcel-intent
id: git-tools.intent.refresh-status
app: git-tools
intent: refreshStatus
status: implemented
current_adapter_status: executable
risk: read-only
default_execution: executable
requires:
  - repository or project context when available
produces:
  - RepositoryState object
  - status receipt
  - backend evidence
receipt: git-tools-refresh-status-receipt
```

```mcel-intent
id: git-tools.intent.inspect-working-tree
app: git-tools
intent: inspectWorkingTree
status: planned
current_adapter_status: declared-only
risk: read-only
default_execution: executable
requires:
  - repository root
produces:
  - WorkingTree object
  - changed-file groups
  - tracked and untracked file evidence
receipt: git-tools-inspect-working-tree-receipt
```

```mcel-intent
id: git-tools.intent.inspect-remotes
app: git-tools
intent: inspectRemotes
status: planned
current_adapter_status: declared-only
risk: read-only
default_execution: executable
requires:
  - repository root
produces:
  - RemoteTarget objects
  - local Gitea target evidence
  - origin target evidence
  - missing or malformed remote findings
receipt: git-tools-inspect-remotes-receipt
```

```mcel-intent
id: git-tools.intent.inspect-patch-inventory
app: git-tools
intent: inspectPatchInventory
status: planned
current_adapter_status: declared-only
risk: read-only
default_execution: executable
requires:
  - repository root
produces:
  - PatchInventory object
  - patch artifact list
  - dry-run applicability evidence
receipt: git-tools-inspect-patch-inventory-receipt
```

```mcel-intent
id: git-tools.intent.prepare-push
app: git-tools
intent: preparePush
status: partially-implemented
current_adapter_status: preflight-only
risk: read-only
default_execution: preflight-only
requires:
  - fresh repository state
  - current branch
  - selected remote target
  - backend availability
produces:
  - push preflight decision
  - block reasons or confirmation requirements
  - recovery hints
receipt: git-tools-prepare-push-receipt
```

```mcel-intent
id: git-tools.intent.push-current-branch
app: git-tools
intent: pushCurrentBranch
status: partially-implemented
current_adapter_status: executable
risk: remote-mutation
default_execution: governed-executable
requires:
  - successful preflight
  - explicit confirmation
  - current branch
  - selected remote target
  - non-stale repository state
produces:
  - push execution receipt
  - updated repository evidence
  - recovery classification on failure
receipt: git-tools-push-current-branch-receipt
```

```mcel-intent
id: git-tools.intent.run-manual-command
app: git-tools
intent: runManualCommand
status: prohibited
current_adapter_status: prohibited
risk: execution
default_execution: prohibited
requires:
  - explicit future command-execution adapter
produces:
  - policy block receipt
receipt: git-tools-run-manual-command-block-receipt
```

```mcel-intent
id: git-tools.intent.commit-selected-files
app: git-tools
intent: commitSelectedFiles
status: planned
current_adapter_status: not-registered
risk: local-repository-mutation
default_execution: preflight-required
requires:
  - explicit file selection
  - commit message
  - clean secrets/filter gates or explicit block reason
produces:
  - commit preflight
  - commit receipt
  - selected file evidence
receipt: git-tools-commit-selected-files-receipt
```

```mcel-intent
id: git-tools.intent.prepare-local-gitea-target
app: git-tools
intent: prepareLocalGiteaTarget
status: planned
current_adapter_status: not-registered
risk: remote-mutation
default_execution: preflight-required
requires:
  - repository root
  - local Gitea backend availability
  - explicit repository target
produces:
  - local Gitea remote evidence
  - create-or-repair plan
  - target URL receipt
receipt: git-tools-prepare-local-gitea-target-receipt
```

```mcel-intent
id: git-tools.intent.preview-ignore-rule
app: git-tools
intent: previewIgnoreRule
status: planned
current_adapter_status: not-registered
risk: local-file-mutation
default_execution: preflight-required
requires:
  - selected file or pattern
  - generated/secret-risk classification
produces:
  - .gitignore preview
  - affected-file explanation
  - rollback guidance
receipt: git-tools-preview-ignore-rule-receipt
```

## Acceptance criteria

```mcel-acceptance
id: git-tools.acceptance.semantic-readiness
app: git-tools
status: specified
requires:
  - refreshStatus is executable.
  - inspectWorkingTree is executable or intentionally reclassified.
  - inspectRemotes is executable or intentionally reclassified.
  - inspectPatchInventory is executable or intentionally reclassified.
  - preparePush is no longer an ambiguous preflight-only gap unless that status is intentionally documented.
  - pushCurrentBranch remains preflighted, confirmed, and receipted.
  - runManualCommand remains prohibited until a command-execution adapter exists.
  - MCEL truth gate reports fullApplicationSemanticReady only after intent and recovery coverage are complete.
```

```mcel-acceptance
id: git-tools.acceptance.governed-push
app: git-tools
status: specified
requires:
  - autosave or refresh does not push.
  - selected project identity is visible before push.
  - selected remote target is visible before push.
  - preflight blocks stale repository state.
  - confirmation is required before push.
  - success and failure receipts include recovery guidance.
  - Local Gitea and origin remain separate user decisions.
```

```mcel-acceptance
id: git-tools.acceptance.project-publishing
app: git-tools
status: specified
requires:
  - project card exposes publishing readiness.
  - Push to Local Gitea is project-level.
  - Push Remote Origin is project-level.
  - server lifecycle remains support/advanced.
  - remote repair is not confused with push execution.
```

```mcel-acceptance
id: git-tools.acceptance.file-triage
app: git-tools
status: specified
requires:
  - changed files are grouped by status and risk.
  - selected-file commit does not include unselected files.
  - ignore-rule edits are previewed before writing.
  - secret/filter gates are visible before commit and push.
  - generated and patch-artifact files are not silently committed.
```

```mcel-acceptance
id: git-tools.acceptance.recovery
app: git-tools
status: specified
requires:
  - backend unavailable has a retry-safe recovery path.
  - status refresh failure blocks push.
  - missing remote explains target setup.
  - push failure preserves evidence.
  - prohibited manual command explains the safer governed alternatives.
```

## MCEL Lab finding seeds

These are not runtime findings yet. They are documentation-authored seeds that MCEL Lab should eventually compare against the app and adapter.

```mcel-finding
id: git-tools.finding.declared-only-read-intents
app: git-tools
status: open
aspect: actions
severity: high
problem: >
  inspectWorkingTree, inspectRemotes, and inspectPatchInventory are currently
  declared-only in the semantic adapter even though they are safe-read intents
  needed for full Git Tools semantic readiness.
desired_behavior: >
  Implement these as executable read intents or intentionally reclassify them
  with a documented reason and acceptance tests.
required_checks:
  - tests/test_mcel_git_tools_semantic_adapter.py
  - tests/test_mcel_domain_adapter_registry.py
```

```mcel-finding
id: git-tools.finding.prepare-push-gap
app: git-tools
status: open
aspect: workflows
severity: medium
problem: >
  preparePush is preflight-only while pushCurrentBranch is executable. That may
  be acceptable as an intermediate state, but the product law should clarify
  whether preparePush is an independent user-visible intent or an internal
  preflight phase of push.
desired_behavior: >
  Make preparePush intentionally preflight-only, or make it an executable
  receipt-producing preparation intent with its own UI evidence.
required_checks:
  - tests/test_mcel_git_tools_semantic_adapter.py
  - tests/test_mcel_git_tools_semantic_panel.py
```

```mcel-finding
id: git-tools.finding.project-card-publishing-contract
app: git-tools
status: open
aspect: layout
severity: medium
problem: >
  The project-level publishing requirements are documented separately from the
  MCEL semantic adapter contract. The implementation should connect project
  card publishing controls to the same governed push semantics.
desired_behavior: >
  Project card actions should show the same target, preflight, confirmation,
  receipt, and recovery evidence as the semantic panel.
required_checks:
  - tests/test_git_tools_project_panel.py
  - tests/test_git_tools_project_workflow.py
  - tests/test_mcel_git_tools_semantic_panel.py
```

```mcel-finding
id: git-tools.finding.file-triage-semantic-model
app: git-tools
status: open
aspect: evidence
severity: medium
problem: >
  File basket, gitignore, secrets/filter, and patch inventory helpers exist as
  app features, but the MCEL semantic model does not yet unify them as file
  triage objects and intents.
desired_behavior: >
  Define FileTriage, ChangedFile, IgnoreRulePreview, SafetyGate, and
  PatchInventory objects in the Git Tools adapter before claiming full app
  semantics.
required_checks:
  - tests/test_git_tools_file_basket_contract_view.py
  - tests/test_git_tools_patch_inventory.py
  - tests/test_mcel_git_tools_semantic_adapter.py
```

## Non-goals

```mcel-requirement
id: git-tools.non-goal.git-terminal
app: git-tools
status: specified
type: non-goal
aspect: actions
object: GitCommand
requirement: >
  Git Tools must not become the default arbitrary Git terminal. Its purpose is
  governed repository workflows with evidence, not unrestricted command entry.
acceptance:
  - Raw commands remain prohibited or advanced.
  - Ordinary users can inspect, triage, commit, and publish without writing commands.
  - Dangerous operations are represented as governed intents, not shell strings.
```

```mcel-requirement
id: git-tools.non-goal.remote-by-default
app: git-tools
status: specified
type: non-goal
aspect: actions
object: RemoteTarget
requirement: >
  Git Tools must not treat remote sync as automatic background behavior. Remote
  publishing is a discrete user action with target evidence, preflight,
  confirmation, execution, and receipt.
acceptance:
  - Refresh never pushes.
  - File save never pushes.
  - Commit never pushes unless the user separately chooses a push action.
  - Local and remote publication can be audited independently.
```


## Runtime diagnosis contract

```mcel-runtime-check
id: git-tools.runtime-check.default-primary-workflow
app: git-tools
status: specified
mode: default
contract: git-tools.contract.default.app-health
check: primary-surface
severity: critical
primary_surface_id: git-tools.surface.workflow
host_selector: "#git-project-workflow-surface"
editor_selector: "#git-project-workflow-surface"
min_width: 420
min_height: 320
observes:
  - "#git-project-workflow-surface"
expects:
  - Git Tools project workflow surface is visible and usable.
  - The workflow surface is not collapsed by rails or proof panels.
failure_message: Git Tools default mode must expose a usable workflow surface.
next_probe: layout.ownerProbe
source_binding: git-tools.binding.project-workflow
test_binding: git-tools.test.semantic-adapter
```

```mcel-runtime-check
id: git-tools.runtime-check.default-required-regions
app: git-tools
status: specified
mode: default
contract: git-tools.contract.default.app-health
check: required-regions-visible
severity: critical
observes:
  - "#git-tools-app"
  - ".git-tools-shell"
  - "#git-project-selector-panel"
  - "#git-project-workflow-surface"
required_regions:
  - git-tools.region.root | #git-tools-app | Git Tools app root
  - git-tools.region.shell | .git-tools-shell | Git Tools shell
  - git-tools.region.project-selector | #git-project-selector-panel | Project selector
  - git-tools.region.workflow | #git-project-workflow-surface | Project workflow surface
expects:
  - Root, shell, project selector, and workflow surface remain visible.
failure_message: Git Tools default mode must preserve project selection and workflow.
next_probe: layout.baseline
source_binding: git-tools.binding.project-workflow
test_binding: git-tools.test.semantic-adapter
```

```mcel-runtime-check
id: git-tools.runtime-check.default-overlay-policy
app: git-tools
status: specified
mode: default
contract: git-tools.contract.default.app-health
check: overlay-policy
severity: warning
observes:
  - "#mc-widget-editor-root"
  - "[data-mcel-proof-surface]"
  - ".floating-tab"
  - ".side-tab"
expects:
  - MCEL/widget/proof overlays are not visible while running the default Git Tools workflow.
forbids:
  - shared.overlay.widget-editor | #mc-widget-editor-root | Widget editor overlay
  - shared.overlay.proof-surface | [data-mcel-proof-surface] | MCEL proof surface
  - shared.overlay.floating-tab | .floating-tab, .side-tab | Floating diagnostic tab
failure_message: Git Tools default mode should not be covered by diagnostic overlays.
next_probe: overlay.detector
source_binding: git-tools.binding.project-workflow
test_binding: git-tools.test.semantic-adapter
```
