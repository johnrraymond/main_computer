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
  The source editor must be the primary work surface. File navigation,
  Aider context, output, proof docks, documentation viewport, runtime preview,
  and advanced repair tools must support the source surface without competing
  with it as the dominant object.
acceptance:
  - The active source editor owns the center/primary region.
  - File map and open editors remain navigation, not primary content.
  - Aider controls are inspector/action support, not the app's dominant object.
  - Evidence and history can be expanded without hiding dirty state.
  - Advanced/runtime/proof controls are collapsed or secondary by default.
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

The Code Editor should normally be inspected through these regions.

```mcel-region
id: code-editor.region.identity
app: code-editor
region: identity
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
region: navigation
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
region: primary
purpose: Active source file, draft editing, diff/preview when reviewing a concrete change, and runtime preview when explicitly selected.
expected_elements:
  - source editor
  - line gutter
  - active file draft
  - diff preview
  - runtime preview tab
must_not_contain:
  - default raw Aider transcript dump
  - unreviewed patch application
  - remote sync controls
```

```mcel-region
id: code-editor.region.inspector
app: code-editor
region: inspector
purpose: Aider context, selected-file evidence, SCM manifest, documentation viewport, and action-specific preflight information.
expected_elements:
  - Aider instruction
  - selected file list
  - SCM manifest
  - documentation viewport
  - policy explanations
must_not_contain:
  - unscoped write controls
  - hidden command execution
```

```mcel-region
id: code-editor.region.evidence
app: code-editor
region: evidence
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
region: advanced
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
region: status
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
risk: safe-read
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
risk: safe-read
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
risk: local-draft
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
risk: local-write
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
risk: safe-or-planned-read
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
risk: local-write
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
risk: command-execution
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
