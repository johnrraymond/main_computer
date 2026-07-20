# MCEL Code Editor Requirements

## Status

This is the documentation-first requirements contract for the Code Editor / MCEL Code Studio app.

The current implementation already has a live MCEL-style workbench, authored layout hints, source/runtime/serialization boundaries, Aider controls, SCM evidence panels, local workspace persistence, Monaco runtime mounting, and layout-contract tests. It does **not** yet have a full Code Editor domain adapter that makes the app semantically executable through the MCEL adapter registry.

So this document must be read as:

```text
current: structural MWSL workbench + domain-enrichment behavior
planned: executable Code Editor semantic adapter and evidence-backed repair workflow
```

The purpose of this document is to make Code Editor requirements stable enough that MCEL Lab can later parse them, compare them with the live app, generate finding candidates, and drive code/test updates without relying on loose prose.

```mcel-app
id: code-editor
title: Code Editor / MCEL Code Studio
status: specified
current_runtime_status: structural-workbench-with-domain-enrichment
target_runtime_status: full-application-semantic-runtime
dominant_object: SourceWorkspace
primary_user_goal: >
  Inspect, edit, preview, and safely change project source with AI assistance
  while preserving explicit write, patch, execution, and remote-mutation boundaries.
current_sources:
  - main_computer/web/applications/apps/code-editor.html
  - main_computer/web/applications/styles/code-editor.css
  - main_computer/web/applications/scripts/code-editor-mcel-studio.js
  - main_computer/web/applications/scripts/code-editor-layout-contract.js
  - main_computer/web/applications/scripts/code-editor-monaco-adapter.js
  - main_computer/web/applications/scripts/code-editor-aider-actions.js
  - main_computer/web/applications/scripts/code-editor-aider-context.js
  - main_computer/web/applications/scripts/code-editor-aider-output.js
  - main_computer/web/applications/scripts/code-editor-file-map.js
  - main_computer/web/applications/scripts/code-editor-documentation-viewport.js
  - main_computer/web/applications/scripts/code-editor-scm-manifest.js
planned_adapter:
  - main_computer/web/applications/scripts/code-editor-semantic-adapter.js
verification:
  - tests/test_mcel_code_studio_app.py
  - tests/test_mcel_documentation.py
```

## Semantic app form

The Code Editor should be described first as reusable app-form primitives, then rendered
by layout. The current desktop projection may use explorer, editor, inspector, status,
or overlay DOM regions, but the requirement does not depend on left/right placement.
Layout is valid only when it preserves primitive meaning, authority, lifecycle, and
intrusion policy.

```mcel-form-primitive
id: code-editor.form.subject.source-workspace
app: code-editor
status: specified
primitive: subject
meaning: The project/workspace source tree and selected source file that the app helps inspect, edit, and safely change.
relationships:
  - Selected file is part of the source workspace.
  - Source text, diagnostics, SCM evidence, and Aider context derive from the selected workspace subject.
  - Generated runtime or proof artifacts are derived evidence, not canonical source.
constraints:
  - Author-owned source remains canonical.
  - Runtime chrome and generated helper surfaces must not become saved source.
  - Selection identity must remain visible enough to anchor editing and review.
```

```mcel-form-primitive
id: code-editor.form.action.edit-source
app: code-editor
status: specified
primitive: action
meaning: Inspect and change selected source text while preserving explicit save, patch, execution, and remote-mutation boundaries.
relationships:
  - Acts on code-editor.form.subject.source-workspace.
  - Uses code-editor.form.work-surface.selected-source-editor as the authoritative work surface.
  - May consume supporting context, evidence, and feedback without allowing those projections to mutate source implicitly.
constraints:
  - Preview, suggestion, diagnosis, and review are not writes.
  - Save/apply/execute/remote mutation require explicit intents and receipts.
  - Read-only Aider requests cannot mutate files.
```

```mcel-form-primitive
id: code-editor.form.work-surface.selected-source-editor
app: code-editor
status: specified
primitive: work-surface
meaning: The authoritative stable surface where the selected file's source text is edited.
relationships:
  - Enables code-editor.form.action.edit-source.
  - Represents the selected file from code-editor.form.subject.source-workspace.
  - May be implemented by Monaco or a mode-gated fallback, but exactly one editor surface may hold primary authority.
constraints:
  - Must remain visible and usable in authoring mode.
  - Must not be covered, replaced, or out-ranked by supporting context, feedback, proof, preview, or diagnostic projections.
  - Must preserve selected-path and dirty-state evidence.
```

```mcel-form-primitive
id: code-editor.form.context.project-selection
app: code-editor
status: specified
primitive: context
meaning: Supporting context that lets the user choose, understand, and compare source workspace subjects.
relationships:
  - Selects or explains the active source workspace/file subject.
  - Supports editing, review, SCM evidence, and Aider context gathering.
  - May project through any selection affordance that preserves subject identity and editing flow.
constraints:
  - Must not claim primary editor authority.
  - Must not obscure the selected source editor below usable geometry.
  - Must keep the current selected subject traceable when file-backed editing is active.
```

```mcel-form-primitive
id: code-editor.form.context.reasoning-evidence
app: code-editor
status: specified
primitive: context
meaning: Supporting explanation, evidence, diagnostics, ownership hints, documentation references, and Aider context that help reason about the selected source subject or proposed action.
relationships:
  - Observes or explains source text, diagnostics, requirements, SCM evidence, Aider plans, and test/source ownership.
  - May be available on demand, adjacent, tabbed, collapsed, or deferred by layout inference.
  - Shares viewport with the primary work surface only when it preserves primary authority and geometry.
constraints:
  - Must not become the selected-file editor.
  - Must not leak as an unowned overlay over the primary work surface.
  - Must remain distinguishable from canonical source and from write/apply controls.
```

```mcel-form-primitive
id: code-editor.form.feedback.integrity-and-activity
app: code-editor
status: specified
primitive: feedback
meaning: Signals about app integrity, contract health, dirty/save state, policy gates, activity, failures, receipts, and recovery posture.
relationships:
  - Observes the source workspace, editor usability, runtime contract, action lifecycle, and persistence state.
  - May render as status text, badges, counters, inline findings, panels, or machine-readable reports.
  - Supports users, developers, and automation without defining a physical slot.
constraints:
  - Ambient feedback must not interrupt or cover the primary work surface.
  - Noticeable or corrective feedback must identify the condition it observes.
  - Feedback projections must be owned so they are not reported as random overlays.
```

```mcel-form-primitive
id: code-editor.form.transient.widget-structure-editing
app: code-editor
status: specified
primitive: transient
meaning: Temporary structure-editing UI used only while an explicit widget or layout editing mode is active.
relationships:
  - Supports structural editing operations rather than ordinary source editing.
  - May cover or annotate the app only while its explicit mode is active.
  - Is shell/tool infrastructure when inert and a transient projection when active.
constraints:
  - Active widget editor panes, selections, and dock previews are forbidden in normal authoring mode.
  - The inert widget-editor root is not itself a visible work surface.
  - Transient structure-editing UI must identify its mode and owner when visible.
```

## Roadmap use cases

These use cases define the product workflow for the Code Editor requirements. They keep source editing, AI assistance, file writes, and execution boundaries visible before any implementation work is treated as complete.

### Use case 1: review and apply an AI-assisted source change

A user opens a project file, asks Aider or another helper to prepare a source change, reviews the proposed diff, applies only the approved file write, and keeps dirty/save/test evidence visible throughout the workflow.

```mcel-use-case
id: code-editor.use-case.review-apply-ai-source-change
app: code-editor
status: planned
type: roadmap-use-case
primary_object: SourceChangeProposal
user_goal: >
  Prepare an AI-assisted source change, inspect the proposed diff and affected
  files, apply only approved edits, and preserve author control over every
  source mutation.
current_support:
  - Aider controls and context/output panels
  - local workspace persistence
  - source/runtime/serialization boundary documentation
  - SCM evidence panels
planned_support:
  - Code Editor semantic adapter for proposal, review, apply, receipt, and recovery
  - structured diff ownership evidence
  - explicit post-apply test/check recommendations
acceptance:
  - The proposed change identifies every affected source file before apply.
  - Runtime/editor chrome is not treated as author-owned source.
  - Applying the proposal requires explicit approval.
  - Unapproved files remain untouched.
  - Dirty state and save/apply receipts stay visible after mutation.
  - Suggested tests or checks are linked to the changed files.
layout_implications:
  - source buffer remains the primary work surface
  - AI proposal and explanation are review surfaces, not authoritative source
  - apply controls require evidence and confirmation near the diff
  - execution controls remain separate from file-write controls
```

### Use case 2: edit and save an author-owned source file

A user selects a project file, edits it in the Code Editor, sees dirty state, saves the file explicitly, and verifies that only the selected author-owned source file changed.

```mcel-use-case
id: code-editor.use-case.edit-save-source-file
app: code-editor
status: planned
type: roadmap-use-case
primary_object: SourceFileDraft
user_goal: >
  Select an author-owned project file, edit it safely, save it explicitly, and
  preserve visible evidence about the path, dirty state, and saved result.
current_support:
  - file map and source selectors
  - Monaco editor adapter
  - local workspace persistence
  - dirty/source boundary requirements
planned_support:
  - semantic save intent with path evidence, write receipt, and stale-state recovery
  - source ownership validation before write
acceptance:
  - The selected file path is visible before editing and saving.
  - Dirty state appears after local draft changes.
  - Save is explicit and writes only the selected author-owned source file.
  - Generated editor chrome and runtime state are not serialized as source.
  - A failed save leaves the draft and recovery guidance visible.
layout_implications:
  - file navigation, source buffer, dirty status, and save evidence remain connected
  - generated/runtime panels are visually secondary to source ownership
  - command execution is not implied by saving a file
```

## Product law

The Code Editor is not a generic text box with a large toolbar. It is a source-safe workbench.

Its core law is:

```text
The author-owned source is canonical.
Generated editor chrome is runtime-only.
Dirty drafts do not become source until an explicit save or reviewed commit.
Aider and execution actions must be previewed, governed, and receipted before mutation.
```

```mcel-requirement
id: code-editor.source.canonical
app: code-editor
status: specified
type: product-law
aspect: source
object: SourceWorkspace
requirement: >
  The Code Editor must treat author-owned source files as canonical. Runtime
  editor chrome, generated previews, proof panels, Monaco DOM, diagnostic
  widgets, and transient Aider output must never become saved source unless
  an explicit serializer says they are safe source content.
current_state: >
  The existing Code Studio example and layout contract already distinguish
  author-owned source, generated runtime, serialized clean source, and contract
  report surfaces.
acceptance:
  - The source editor remains the primary editable surface.
  - Generated runtime nodes are marked as runtime-only.
  - Clean serialization omits generated runtime nodes.
  - Contract failures block unsafe serialization/export.
  - Dirty runtime drafts remain visible until committed or discarded.
```

```mcel-requirement
id: code-editor.mutation.explicit-boundaries
app: code-editor
status: specified
type: safety-law
aspect: actions
object: SourceWorkspace
requirement: >
  File writes, patch application, command execution, package installation, and
  remote Git mutation must never happen as a side effect of inspecting,
  previewing, mounting, serializing, or asking Aider for a plan.
non_goals:
  - Do not make run/build/test controls primary until execution policy exists.
  - Do not apply Aider output without a reviewed patch or explicit write receipt.
  - Do not push, mirror, or sync remote state from the Code Editor by default.
acceptance:
  - Read-only Aider requests are routed separately from mutating Aider runs.
  - Save/write actions are visually and semantically distinct from preview actions.
  - Patch apply requires a reviewed patch or equivalent evidence packet.
  - Code execution remains blocked or advanced until a command-execution adapter exists.
  - Remote Git operations are delegated to Git Tools or advanced evidence surfaces.
```

```mcel-requirement
id: code-editor.evidence.near-action
app: code-editor
status: specified
type: product-law
aspect: evidence
object: SourceWorkspace
requirement: >
  Every risky Code Editor action must have adjacent evidence explaining what
  will change, which files are in scope, which policy gate is active, and how
  the user can recover or inspect the receipt.
acceptance:
  - Save, Aider run, patch apply, runtime repair, and execution controls expose receipts.
  - SCM evidence is available before write or patch decisions.
  - Aider output is tied to repository path, selected files, instruction, and archive identity.
  - Status remains visible while an action is pending, blocked, running, failed, or completed.
```

```mcel-requirement
id: code-editor.layout.source-primary
app: code-editor
status: specified
type: layout-law
aspect: layout
object: SourceWorkspace
requirement: >
  The selected author-owned source file must remain the primary work surface.
  File navigation, Aider context, MCEL tools, diagnostics, proof evidence,
  documentation viewport, runtime preview, and advanced repair tools must support
  the selected-file editor without replacing it, overlaying it, or competing with
  it as the dominant object.
acceptance:
  - The active selected-file editor owns the primary work-surface projection.
  - File map and open editors remain navigation, not primary content.
  - Aider, MCEL tools, and diagnostics are secondary support surfaces.
  - Evidence and history can be expanded without hiding dirty state or the editor.
  - Advanced/runtime/proof controls are collapsed, owned by supporting projections, or mode-gated by default.
```

```mcel-requirement
id: code-editor.layout.authoring-cockpit
app: code-editor
status: specified
type: layout-law
aspect: layout
object: SourceWorkspace
requirement: >
  Code Editor authoring mode must render the semantic app form: a primary
  selected-source editing work surface, supporting project-selection context,
  optional reasoning/evidence context, ambient integrity/activity feedback, and
  explicit mode-bound transients. The requirement is semantic, not directional;
  layout may project those primitives as panes, drawers, tabs, compact chrome, or
  overlays only when the projection preserves primary editor authority,
  usability, lifecycle, and intrusion policy.
acceptance:
  - Every visible authoring surface projects from a declared app, shell, or tool primitive.
  - Exactly one authoritative selected-file editor work surface is visible and usable in authoring mode.
  - Project-selection context remains available enough to identify or change the selected subject.
  - Reasoning, evidence, diagnostics, and assistant context may be visible, collapsed, tabbed, or deferred without becoming primary.
  - Ambient feedback is owned, low-intrusion, and does not cover or shrink the primary editor below minimum geometry.
  - Mode-bound transients such as active widget editing are hidden unless the explicit mode allows them.
  - Runtime preview, source HTML, fallback textarea, generated runtime rails, and proof docks remain hidden unless an explicit mode allows them.
```

```mcel-requirement
id: code-editor.aider.plan-before-apply
app: code-editor
status: specified
type: workflow-law
aspect: workflows
object: AiderContext
requirement: >
  Aider must operate through a plan/review/apply workflow. The default path is
  to gather context, produce or preview a plan, show affected files and evidence,
  then require explicit user confirmation before applying changes.
current_state: >
  The current app already has Aider repository settings, file map selection,
  instruction fields, dry-run controls, archive history, output rendering, and
  read-only request routing.
acceptance:
  - Selected files are visible before an Aider request runs.
  - Read-only/editor-reading requests cannot mutate files.
  - Dry-run or preview is the default safe path for change-producing instructions.
  - Apply uses a distinct action and produces a receipt.
  - Aider context archives can be inspected after the run.
```

```mcel-requirement
id: code-editor.adapter.executable-semantics
app: code-editor
status: planned
type: semantic-runtime
aspect: actions
object: SourceWorkspace
requirement: >
  Code Editor must eventually expose a domain adapter through the MCEL domain
  adapter registry. That adapter must model state, objects, intents, preflight,
  execution receipts, evidence mapping, failure classes, and recovery options.
current_state: >
  Code Editor has planner/domain-enrichment data and a rich workbench, but no
  Code Editor semantic adapter is registered as a full executable runtime.
acceptance:
  - Adapter lists SourceWorkspace, FileTree, ActiveFile, DirtyDraft, AiderContext, SCMEvidence, and ExecutionPolicy objects.
  - Adapter exposes safe read/inspect intents as executable.
  - Adapter exposes save/apply/run intents as preflighted, receipted, and policy-gated.
  - Adapter classifies failure and recovery paths for stale source, dirty draft, failed Aider run, blocked write, blocked execution, and serialization failure.
  - MCEL truth gate does not report fullApplicationSemanticReady until intent and recovery coverage are complete.
```

## Workbench anatomy

The Code Editor should normally be inspected as an owned-region authoring cockpit.
The selected-source editor, project-selection context, reasoning/evidence context,
ambient feedback, and mode-bound transients must each have an explicit responsibility.
Current DOM regions are layout projections of those primitives, not the requirement
itself. Runtime checks should treat the selected-source editor as the only primary
work surface and supporting/feedback projections as non-primary surfaces.


```mcel-region
id: code-editor.region.identity
app: code-editor
status: specified
region: identity
role: identity-header
responsibility: >
  Identify the active workspace, route, active file, dirty state, runtime
  version, gate status, and persistence state.
purpose: Active workspace, route, active file, dirty state, runtime version, gate status, and persistence state.
expected_elements:
  - workspace root identity
  - active file path
  - dirty/save status
  - policy gate status
  - persistence status
must_not_contain:
  - raw provider logs as primary identity
  - destructive controls
```

```mcel-region
id: code-editor.region.navigation
app: code-editor
status: specified
region: navigation
role: project-navigation
responsibility: >
  Let the user choose files, project context, open editors, and selected-file
  sets without applying patches or executing commands.
form_primitives:
  - code-editor.form.context.project-selection
purpose: File map, project tree, open editors, selected files, and repository context.
expected_elements:
  - file search
  - repository file tree
  - marked files
  - open editors
  - active file switcher
must_not_contain:
  - patch apply as a row action
  - command execution as a file-navigation shortcut
```

```mcel-region
id: code-editor.region.primary
app: code-editor
status: specified
region: primary
role: primary-authoring-surface
responsibility: >
  Own the selected-file editor, draft review, concrete diffs, and explicit preview
  modes while preventing supporting tools from becoming the source of truth.
form_primitives:
  - code-editor.form.work-surface.selected-source-editor
  - code-editor.form.action.edit-source
purpose: Selected-file editor, author-owned draft, selected path evidence, diff review when requested, and runtime preview only when explicitly selected.
expected_elements:
  - selected-file Monaco editor
  - selected file path
  - language and dirty-state evidence
  - line gutter
  - active file draft
  - diff preview when reviewing a concrete change
must_not_contain:
  - default raw Aider transcript dump
  - MCEL proof or diagnostics projection not assigned to an owned supporting or feedback primitive
  - unreviewed patch application
  - remote sync controls
  - fallback textarea while Monaco is active
```

```mcel-region
id: code-editor.region.inspector
app: code-editor
status: specified
region: supporting-reasoning-evidence-projection
role: secondary-context-and-feedback-surface
responsibility: >
  Project optional reasoning, evidence, diagnostics, Aider context, SCM manifests,
  source ownership, test ownership, documentation references, and action-specific
  preflight information without becoming the primary editor. A desktop renderer may
  currently place this projection beside the editor, but MCEL treats that placement
  as layout inference rather than the requirement.
purpose: Supporting reasoning/evidence/feedback projection for surfaces that should not leak into or compete with the selected-source editor.
form_primitives:
  - code-editor.form.context.reasoning-evidence
  - code-editor.form.feedback.integrity-and-activity
expected_elements:
  - Aider instruction
  - selected file list
  - MCEL tools
  - diagnosis history
  - contract findings
  - source ownership hints
  - test ownership hints
  - SCM manifest
  - documentation viewport
  - policy explanations
must_not_contain:
  - selected-file source edits that bypass the primary editor
  - unscoped write controls
  - hidden command execution
  - proof or diagnostic projections that cover the primary work surface
```

```mcel-region
id: code-editor.region.evidence
app: code-editor
status: specified
region: evidence
role: evidence-and-receipts-panel
responsibility: >
  Show Aider output, SCM evidence, contract reports, regression results,
  receipts, and recovery guidance for reviewed actions.
purpose: Aider output, SCM evidence packets, contract reports, regression harness output, receipts, and recovery guidance.
expected_elements:
  - Aider output
  - SCM evidence panel
  - contract report
  - replay comparison
  - regression harness result
  - receipts
must_not_contain:
  - source edits that bypass the primary editor
```

```mcel-region
id: code-editor.region.advanced
app: code-editor
status: specified
region: advanced
role: advanced-runtime-boundary
responsibility: >
  Contain runtime preview internals, Monaco repair, VRAM experiments,
  serialization details, and helper generation away from ordinary editing.
purpose: Runtime preview internals, Monaco mount/repair, VRAM/documentation experiments, serialization internals, and repair helpers.
expected_elements:
  - runtime mount
  - runtime repair
  - VRAM widget
  - serialization internals
  - helper generation
must_not_contain:
  - primary save path
  - ordinary file editing controls
```

```mcel-region
id: code-editor.region.status
app: code-editor
status: specified
region: status
role: persistent-status-strip
responsibility: >
  Keep draft, save, policy, gate, activity, failure, and receipt state visible
  without becoming a hidden source of truth.
form_primitives:
  - code-editor.form.feedback.integrity-and-activity
purpose: Persistent action state that tells the user whether drafts, persistence, gates, policies, and actions are clean, dirty, blocked, running, failed, or complete.
expected_elements:
  - dirty state
  - save state
  - Aider activity state
  - policy gate state
  - persistence state
  - last receipt summary
must_not_contain:
  - hidden source of truth
```

## Semantic intents

The app should eventually expose these intents to the MCEL domain adapter registry.

```mcel-intent
id: code-editor.intent.inspect-workspace
app: code-editor
intent: inspectWorkspace
status: specified
risk: read-only
default_execution: executable
requires:
  - workspace identity
  - file map availability
produces:
  - SourceWorkspace object
  - FileTree object
  - SCMEvidence summary
receipt: mcel-code-editor-inspect-workspace-receipt
```

```mcel-intent
id: code-editor.intent.open-file
app: code-editor
intent: openFile
status: specified
risk: read-only
default_execution: executable
requires:
  - file path
  - file-map membership or explicit path evidence
produces:
  - ActiveFile object
  - editor draft
  - dirty state
receipt: mcel-code-editor-open-file-receipt
```

```mcel-intent
id: code-editor.intent.edit-draft
app: code-editor
intent: editDraft
status: specified
risk: local-state
default_execution: executable
requires:
  - active file
  - editor focus
produces:
  - DirtyDraft object
  - visible dirty state
receipt: mcel-code-editor-edit-draft-receipt
```

```mcel-intent
id: code-editor.intent.save-file
app: code-editor
intent: saveFile
status: planned
risk: local-file-mutation
default_execution: preflight-required
requires:
  - active file
  - dirty draft
  - stale-source check
  - write policy
produces:
  - saved source
  - clear or updated dirty state
  - write receipt
receipt: mcel-code-editor-save-file-receipt
```

```mcel-intent
id: code-editor.intent.preview-aider-plan
app: code-editor
intent: previewAiderPlan
status: specified
risk: read-only
default_execution: preflight-required
requires:
  - repository path
  - selected files or explicit scope
  - instruction draft
  - mutation policy classification
produces:
  - AiderPlan
  - affected-file candidates
  - evidence packet
receipt: mcel-code-editor-aider-plan-receipt
```

```mcel-intent
id: code-editor.intent.apply-reviewed-patch
app: code-editor
intent: applyReviewedPatch
status: planned
risk: local-file-mutation
default_execution: confirmation-required
requires:
  - reviewed patch or replacement-file artifact
  - source freshness check
  - tests or required checks
  - rollback/recovery path
produces:
  - changed files
  - receipt
  - updated evidence packet
receipt: mcel-code-editor-apply-reviewed-patch-receipt
```

```mcel-intent
id: code-editor.intent.run-code
app: code-editor
intent: runCode
status: planned
risk: execution
default_execution: prohibited-until-execution-adapter
requires:
  - command-execution adapter
  - sandbox policy
  - user confirmation
  - output capture
  - cancellation path
produces:
  - execution receipt
  - stdout/stderr evidence
  - recovery guidance
receipt: mcel-code-editor-run-code-receipt
```

## Documentation-to-code workflow

This document should be useful before implementation and after implementation.

The intended lifecycle is:

```text
write requirement block
→ parse and validate the block
→ compare with Code Editor app blueprint and adapter state
→ create MCEL Lab finding when implementation is missing or conflicting
→ link finding to source/test candidates
→ generate repair brief
→ patch code/tests
→ mark requirement implemented only after verification
```

```mcel-requirement
id: code-editor.docs.requirements-are-machine-readable
app: code-editor
status: specified
type: documentation-law
aspect: source
object: RequirementBlock
requirement: >
  Code Editor requirements must remain human-readable Markdown and
  machine-readable MCEL blocks. Tools may parse these blocks, but the prose
  around them remains the product explanation.
acceptance:
  - Each block has a stable id.
  - Each block has an app field.
  - Requirement and intent statuses distinguish specified, planned, implemented, and verified.
  - Planned requirements are not reported as implemented.
  - Acceptance criteria are present for product laws and safety laws.
```

## Acceptance criteria for implementation completeness

Code Editor should not be called a full MCEL semantic runtime until these are true.

```mcel-acceptance
id: code-editor.acceptance.full-semantic-runtime
app: code-editor
status: planned
requires:
  - Code Editor has a registered domain adapter.
  - inspectWorkspace, openFile, and editDraft are executable safe/local-draft intents.
  - saveFile is preflighted, stale-checked, receipted, and recoverable.
  - previewAiderPlan separates read-only planning from mutation.
  - applyReviewedPatch requires reviewed patch evidence and recovery path.
  - runCode remains prohibited until a command-execution adapter exists.
  - source serialization excludes runtime-only chrome.
  - generated Monaco/runtime DOM never becomes saved source by accident.
  - file writes, patch application, command execution, and remote Git mutation are never hidden side effects.
  - MCEL Lab can produce structured findings from Code Editor annotations.
  - Browser-level workflow covers open file, edit draft, save, Aider preview, blocked apply, and recovery state.
```

## Runtime-observable diagnosis contract

The Code Editor contract must be diagnoseable while the app is running. The authoring
golden path is now a semantic app-form contract: default authoring mode exposes exactly
one usable selected-source editor work surface, preserves enough project-selection
context and ambient feedback to orient the user, allows supporting reasoning/evidence
projections as non-primary surfaces, and keeps runtime, source-model, fallback, proof,
generated rail, and mode-bound transient surfaces out of the primary editor unless an
explicit mode allows them.

```mcel-runtime-check
id: code-editor.runtime-check.authoring-primary-monaco
app: code-editor
status: specified
mode: authoring
contract: code-editor.contract.authoring.monaco-golden-path
check: primary-surface
check_category: surface
focus: primary-editor
severity: critical
primary_surface_id: code-editor.surface.monaco-selected-file-editor
host_selector: "#code-studio-runtime-monaco"
editor_selector: ".monaco-editor"
min_width: 800
min_height: 600
observes:
  - "#code-studio-runtime-monaco"
  - ".monaco-editor"
expects:
  - Monaco host is visible and at least 800px wide by 600px tall.
  - Monaco editor instance is visible and at least 800px wide by 600px tall.
  - No fallback or source-model editor surface competes with Monaco in authoring mode.
failure_message: Authoring mode must expose one usable Monaco selected-file editor.
next_probe: layout.ownerProbe
source_binding: code-editor.binding.authoring-monaco-surface
test_binding: code-editor.test.authoring-monaco-diagnosis
```

```mcel-runtime-check
id: code-editor.runtime-check.authoring-required-regions
app: code-editor
status: specified
mode: authoring
contract: code-editor.contract.authoring.monaco-golden-path
check: required-regions-visible
check_category: layout
focus: required-regions
severity: critical
observes:
  - "#code-editor-app"
  - ".code-studio-sidebar"
  - ".code-studio-editor-group"
  - ".code-studio-statusbar"
expects:
  - Code Editor root is present and visible.
  - Explorer region is present and visible.
  - Editor group is present and visible.
  - Status bar is present and visible.
required_regions:
  - code-editor.region.root | #code-editor-app | Code Editor app root
  - code-editor.region.explorer | .code-studio-sidebar | Explorer
  - code-editor.region.editor-group | .code-studio-editor-group | Editor group
  - code-editor.region.statusbar | .code-studio-statusbar | Status bar
failure_message: Authoring mode must preserve the app root, explorer, editor group, and status bar.
next_probe: layout.baseline
source_binding: code-editor.binding.authoring-monaco-surface
test_binding: code-editor.test.authoring-monaco-diagnosis
```


```mcel-runtime-check
id: code-editor.runtime-check.authoring-supporting-projection-policy
app: code-editor
status: specified
mode: authoring
contract: code-editor.contract.authoring.monaco-golden-path
check: secondary-surface-policy
check_category: form
focus: supporting-context-feedback-projection
severity: warning
observes:
  - ".code-studio-inspector"
  - "[data-code-studio-workbench-region=\"scm-ai-inspector\"]"
  - "#code-editor-mcel-tools-toggle"
  - "#code-editor-diagnostics-counter"
expects:
  - Supporting reasoning, evidence, diagnostics, and assistant context are allowed in authoring mode as non-primary projections.
  - Supporting projections may be visible, collapsed, tabbed, deferred, or trigger-only without becoming the primary editor.
  - MCEL tools, diagnosis history, contract findings, source ownership, and test ownership must project from owned context or feedback primitives, or from an explicit mode.
  - Supporting projections must not cover the Monaco editor or reduce it below its minimum geometry.
form_primitives:
  - code-editor.form.context.reasoning-evidence
  - code-editor.form.feedback.integrity-and-activity
optional_regions:
  - code-editor.region.inspector | .code-studio-inspector | Supporting reasoning/evidence projection
allowed_regions:
  - code-editor.allowed.mcel-tools-toggle | #code-editor-mcel-tools-toggle | MCEL tools toggle projection
  - code-editor.allowed.diagnostics-counter | #code-editor-diagnostics-counter | Ambient integrity feedback projection
geometry_policies:
  - supporting-projection-visible-min-width-240
  - supporting-projection-max-width-ratio-0.40
  - supporting-projection-must-collapse-before-primary-breaks
overlay_policy:
  - diagnostics-owned-by-supporting-or-feedback-projection-are-allowed
  - diagnostics-covering-primary-editor-are-forbidden
semantic_policy:
  - supporting-context-must-not-claim-primary-authority
  - ambient-feedback-must-not-cover-primary-work-surface
failure_message: Supporting context and feedback projections are allowed when they do not compete with the selected-source editor.
next_probe: semanticProjection.containment
source_binding: code-editor.binding.authoring-cockpit-layout
test_binding: code-editor.test.authoring-cockpit-diagnosis
```

```mcel-runtime-check
id: code-editor.runtime-check.authoring-forbidden-surfaces
app: code-editor
status: specified
mode: authoring
contract: code-editor.contract.authoring.monaco-golden-path
check: forbidden-surfaces-hidden
check_category: overlays
focus: forbidden-surfaces
severity: critical
observes:
  - "[data-code-studio-pane=\"source\"]"
  - "[data-code-studio-pane=\"serialized\"]"
  - "[data-code-studio-pane=\"contract\"]"
  - ".code-studio-runtime-window"
  - ".code-studio-runtime-layout"
  - ".code-studio-runtime-files"
  - "#code-studio-runtime-draft"
  - ".code-studio-runtime-fallback"
  - ".code-studio-proof-dock"
  - "#code-studio-bottom-panel"
  - "#mc-widget-editor-pane.open"
  - ".mc-widget-selection:not([hidden])"
  - ".mc-widget-dock-preview:not([hidden])"
expects:
  - Source model pane is hidden.
  - Serialized and contract panes are hidden.
  - Generated runtime window/layout/file rail are absent from the default path.
  - Fallback textarea is not visible in the Monaco golden path.
  - Proof docks and active widget editor overlays are not visible in authoring mode; the inert widget-editor shell is not treated as a visible overlay.
forbids:
  - code-editor.forbidden.source-pane | [data-code-studio-pane="source"] | MCEL source model pane
  - code-editor.forbidden.serialized-pane | [data-code-studio-pane="serialized"] | Serialized output pane
  - code-editor.forbidden.contract-pane | [data-code-studio-pane="contract"] | Contract report pane
  - code-editor.forbidden.runtime-scaffold.window | .code-studio-runtime-window | Generated runtime window scaffold
  - code-editor.forbidden.runtime-scaffold.layout | .code-studio-runtime-layout | Generated runtime layout scaffold
  - code-editor.forbidden.runtime-file-rail | .code-studio-runtime-files | Generated runtime file rail
  - code-editor.forbidden.fallback-textarea | #code-studio-runtime-draft, .code-studio-runtime-fallback | Fallback textarea
  - code-editor.forbidden.proof-dock | .code-studio-proof-dock, #code-studio-bottom-panel | MCEL proof/evidence dock
  - code-editor.forbidden.widget-overlay | #mc-widget-editor-pane.open, .mc-widget-selection:not([hidden]), .mc-widget-dock-preview:not([hidden]) | Active widget editor overlay
failure_message: MCEL diagnostic/runtime scaffolding must not leak into Code Editor authoring mode.
next_probe: overlay.detector
source_binding: code-editor.binding.authoring-monaco-surface
test_binding: code-editor.test.authoring-monaco-diagnosis
```

```mcel-runtime-check
id: code-editor.runtime-check.authoring-lifecycle
app: code-editor
status: specified
mode: authoring
contract: code-editor.contract.authoring.monaco-golden-path
check: lifecycle-contract-preserved
check_category: lifecycle
focus: startup-file-click-resize
severity: critical
observes:
  - startup
  - file-click
  - resize
expects:
  - Startup authoring mode has exactly one primary Monaco editor.
  - Clicking another file keeps exactly one primary Monaco editor.
  - Resize keeps the Monaco host and editor useful.
lifecycle_assertions:
  - startup-authoring-mode-has-one-primary-editor
  - file-click-keeps-one-primary-editor
  - resize-keeps-primary-editor-usable
  - mcel-diagnostics-hidden-in-authoring
failure_message: File selection and reload must preserve the Code Editor authoring contract.
next_probe: startup.timeline
source_binding: code-editor.binding.authoring-monaco-surface
test_binding: code-editor.test.authoring-monaco-diagnosis
```

```mcel-source-binding
id: code-editor.binding.authoring-monaco-surface
app: code-editor
status: specified
target: code-editor.contract.authoring.monaco-golden-path
source_candidates:
  - main_computer/web/applications/apps/code-editor.html
  - main_computer/web/applications/scripts/code-editor-mcel-studio.js
  - main_computer/web/applications/scripts/code-editor-monaco-adapter.js
  - main_computer/web/applications/styles/code-editor.css
binding_confidence: high
verification:
  - Runtime diagnosis must report the Monaco selected-file editor as usable.
  - File selection must update the Monaco model without remounting MCEL proof scaffolding.
```


```mcel-source-binding
id: code-editor.binding.authoring-cockpit-layout
app: code-editor
status: specified
target: code-editor.layout.authoring-cockpit
source_candidates:
  - main_computer/web/applications/apps/code-editor.html
  - main_computer/web/applications/styles/code-editor.css
  - main_computer/web/applications/scripts/code-editor-layout-contract.js
  - main_computer/web/applications/scripts/mcel-self-diagnosis.js
  - main_computer/web/applications/scripts/mcel-diagnostics-counter-widget.js
binding_confidence: high
verification:
  - Runtime diagnosis must preserve project-selection context, the primary editor work surface, supporting context/feedback projections, and ambient status feedback.
  - Diagnostics must be classified by primitive role: supporting context, ambient feedback, explicit transient, or forbidden unowned overlay.
  - Supporting projections must collapse, defer, or reflow before they reduce the Monaco selected-file editor below usable geometry.
```

```mcel-test-binding
id: code-editor.test.authoring-monaco-diagnosis
app: code-editor
status: specified
target: code-editor.contract.authoring.monaco-golden-path
test_candidates:
  - tests/test_mcel_code_studio_app.py
missing_tests:
  - Browser lifecycle smoke test for startup, file click, and resize diagnosis.
verification:
  - Unit tests cover registry-derived diagnostic contracts.
  - Browser diagnosis report remains the runtime source of truth.
```


```mcel-test-binding
id: code-editor.test.authoring-cockpit-diagnosis
app: code-editor
status: specified
target: code-editor.layout.authoring-cockpit
test_candidates:
  - tests/test_mcel_requirements_registry.py
  - tests/test_mcel_code_studio_app.py
missing_tests:
  - Browser containment smoke test for expanded, collapsed, tabbed, deferred, and compact supporting-context projections.
  - Browser lifecycle smoke test proving resize preserves the primary editor before supporting projections consume space.
verification:
  - Registry tests confirm semantic form primitives parse and supporting projections compile as optional regions.
  - Browser diagnosis should report unowned or mode-invalid projections separately from primary-surface failures.
```

## First useful findings for MCEL Lab

When MCEL Lab starts reading this document, these are the first Code Editor findings it should be able to produce.

```mcel-finding
id: code-editor.finding.semantic-adapter-missing
app: code-editor
aspect: actions
severity: high
status: open
problem: >
  Code Editor has a rich workbench and domain-enrichment plan, but no full
  Code Editor semantic adapter is registered with the MCEL domain adapter
  registry.
desired_behavior: >
  The adapter exposes state, objects, intents, preflight, receipts, evidence,
  failure classification, and recovery coverage.
required_checks:
  - Verify registry entry for code-editor.
  - Verify safe read intents are executable.
  - Verify write/execution intents are blocked, preflighted, or receipted.
```

```mcel-finding
id: code-editor.finding.execution-policy-missing
app: code-editor
aspect: actions
severity: high
status: open
problem: >
  Run/build/test behavior belongs to a command-execution risk family and
  should not be treated as an ordinary editor action.
desired_behavior: >
  Execution controls remain prohibited or advanced until a command-execution
  adapter defines sandbox, confirmation, cancellation, output capture, and receipts.
required_checks:
  - Search for run/build/test controls.
  - Confirm none are primary safe actions.
  - Confirm blocked/prohibited status is visible in the UI and adapter plan.
```

```mcel-finding
id: code-editor.finding.docs-to-implementation-gap
app: code-editor
aspect: findings
severity: medium
status: open
problem: >
  Requirements in this document are not yet automatically compared with the
  live Code Editor implementation.
desired_behavior: >
  MCEL Lab parses these blocks, compares them with app blueprint and adapter
  state, and creates structured findings for missing or conflicting behavior.
required_checks:
  - Parse all mcel-* blocks in this document.
  - Map block IDs to app regions, intents, tests, and source candidates.
  - Prevent planned requirements from being reported as verified.
```

## Non-goals

The Code Editor requirements do not claim that MCEL should replace all editor technology.

```text
Do not replace Monaco as an editor engine.
Do not make Aider output the source of truth.
Do not run shell commands from preview or inspection.
Do not hide raw evidence from advanced users.
Do not expose raw Git plumbing as the normal editor workflow.
Do not promote Code Editor to full semantic runtime before adapter coverage is proven.
```

## Summary

The Code Editor is the right next app for documentation-first MCEL requirements because it has real product risk: file writes, patch application, command execution, AI-generated changes, runtime editor chrome, serialization boundaries, and evidence needs.

The current app already demonstrates many MCEL workbench ideas. This document turns those ideas into stable requirements that MCEL Lab can later parse and enforce.
