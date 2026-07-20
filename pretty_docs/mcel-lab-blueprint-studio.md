# MCEL Lab Blueprint Studio

## Status

Design and requirements document for redesigning MCEL Lab into a self-hosting app-aspect inspector and blueprint studio.

This document is intentionally product-facing and implementation-facing. It defines what MCEL Lab must do for users, what app operations it must support, how it must use generic MCEL elements, and how it must prove that MCEL can help generate good-looking, solid applications.

## Product purpose

MCEL Lab should become the tool that helps us design, inspect, test, and repair apps before and during implementation.

The primary goal is:

```text
Generate good-looking apps that are solid.
```

In this context, "good-looking" does not mean decorative. It means the app has a clear dominant object, coherent layout, correct visual hierarchy, safe action placement, visible evidence, and predictable behavior across workflows and viewport sizes.

MCEL Lab should let us say:

```text
This app is about this object.
These are the workflows.
These are the layout zones.
These controls belong here.
These actions are safe, advanced, blocked, or destructive.
These shared capabilities are consumed through app-native projections.
These tests prove the app actually follows the blueprint.
```

Then MCEL Lab should compare that blueprint to the real implementation and produce findings or repair plans.

## Core product law

MCEL Lab must not become another hardcoded MCEL dashboard.

It must be the first self-hosting MCEL workbench: a generic app-aspect inspector built from the same elements it uses to inspect other apps.

The Lab is allowed to expose MCEL internals because it is the MCEL tool. Product apps are not allowed to show MCEL scaffolding or spec cards as a substitute for good UI.

## Dominant object

The Lab's dominant object is:

```text
AppBlueprint
```

An `AppBlueprint` describes one application as a product/workbench, not merely as a route or HTML file.

```text
object AppBlueprint {
  identity: appId
  title: appName
  purpose: productPurpose
  state: draft | valid | invalid | tested | repair-ready
  relationships:
    Aspect[]
    ObjectModel[]
    Workflow[]
    LayoutBinding[]
    CapabilityProjection[]
    ActionPolicy[]
    EvidenceModel[]
    SourceBinding[]
    AcidTest[]
    RepairFinding[]
    PatchPlan[]
}
```

The selected app blueprint is always the thing being inspected, edited, previewed, tested, or repaired.


## Machine-readable MCEL Lab contract

MCEL Lab is now also registered as an MCEL app contract. The Lab's form is defined
semantically first, then projected into the current inspector shell. This keeps the
Lab from being a hardcoded dashboard: its own documentation is the first source that
the requirements registry, Lab comparison payload, and runtime checks can read.

```mcel-app
id: mcel-lab
title: MCEL Lab Blueprint Studio
status: specified
current_runtime_status: structural-only
target_runtime_status: scope-limited-semantic-runtime
dominant_object: AppBlueprint
primary_user_goal: >
  Select an app blueprint, inspect its semantic form and implementation evidence,
  annotate rendered elements, validate findings, and export repair context without
  directly rewriting live implementation files.
current_sources:
  - main_computer/web/applications/apps/mcel-lab.html
  - main_computer/web/applications/scripts/mcel-lab.js
  - main_computer/web/applications/scripts/mcel-app-blueprints-core.js
  - main_computer/web/applications/styles/mcel-lab.css
  - main_computer/web/applications/mcel/annotations/mcel-lab.json
verification:
  - tests/test_mcel_lab_app.py
  - tests/test_mcel_lab_blueprint_studio_documentation.py
  - tests/test_mcel_lab_phase2_shell.py
  - tests/test_mcel_lab_phase3_mounting.py
  - tests/test_mcel_lab_phase4_point_inspection.py
  - tests/test_mcel_lab_phase5_annotations.py
  - tests/test_mcel_app_blueprint_contracts.py
```

### Semantic app-form primitives

These blocks define the Lab's reusable form primitives before any layout projection
decides where they render.

```mcel-form-primitive
id: mcel-lab.form.subject.app-blueprint
app: mcel-lab
status: specified
primitive: subject
meaning: The selected app contract being inspected, validated, annotated, or prepared for repair.
relationships:
  - Owns app identity, object model, workflows, layout bindings, action policy, evidence, source/test bindings, annotations, findings, and repair plans.
  - May represent MCEL Lab itself as a self-hosting target.
  - Is loaded from documentation, blueprint core data, annotations, and runtime evidence.
constraints:
  - AppBlueprint remains the dominant object even when a mounted app preview is visible.
  - Prose, hardcoded JS blueprints, annotations, and runtime evidence must be distinguishable as separate evidence sources.
  - Self-hosting inspection must not imply permission to rewrite the live Lab implementation.
```

```mcel-form-primitive
id: mcel-lab.form.action.inspect-blueprint
app: mcel-lab
status: specified
primitive: action
meaning: Select an app and aspect, inspect the semantic contract and compare it with implementation evidence.
relationships:
  - Acts on mcel-lab.form.subject.app-blueprint.
  - Uses the blueprint inspection work surface as the authoritative workspace.
  - Consumes supporting implementation evidence, selected-element evidence, validation feedback, and annotations.
constraints:
  - Inspection is read-oriented until the user explicitly creates or edits an annotation draft.
  - Aspect navigation must not replace the selected AppBlueprint as the dominant object.
  - Findings must distinguish documented intent from verified runtime facts.
```

```mcel-form-primitive
id: mcel-lab.form.work-surface.blueprint-inspection
app: mcel-lab
status: specified
primitive: work-surface
meaning: The stable surface where the selected AppBlueprint aspect, mounted preview, selected evidence, and repair context are inspected.
relationships:
  - Enables mcel-lab.form.action.inspect-blueprint.
  - Represents the selected AppBlueprint and current aspect.
  - Hosts mounted app preview evidence without granting that preview primary Lab authority.
constraints:
  - Must remain visible and usable when MCEL Lab is active.
  - Must keep selected app, selected aspect, and mounted route evidence traceable.
  - Must not be covered or out-ranked by unowned feedback, transient overlays, or debug/proof internals.
```

```mcel-form-primitive
id: mcel-lab.form.context.app-and-aspect-selection
app: mcel-lab
status: specified
primitive: context
meaning: Supporting context that chooses which AppBlueprint and which aspect are being inspected.
relationships:
  - Selects the active subject for the blueprint inspection work surface.
  - Filters the visible evidence, annotations, findings, and repair context.
  - May render as controls, lists, command choices, tabs, or another inferred projection.
constraints:
  - Must keep the selected app and aspect recoverable from visible UI or machine-readable state.
  - Must not claim primary work-surface authority.
  - Must not make physical placement part of the semantic contract.
```

```mcel-form-primitive
id: mcel-lab.form.context.implementation-evidence
app: mcel-lab
status: specified
primitive: context
meaning: Supporting evidence about DOM elements, source files, CSS ownership, tests, annotations, validation findings, and repair candidates.
relationships:
  - Explains the selected AppBlueprint, selected aspect, and selected rendered element.
  - May be gathered from mounted previews, point inspection, annotation maps, source bindings, test bindings, and registry payloads.
  - Supports repair planning without becoming a direct patch applicator.
constraints:
  - Evidence must identify its source and freshness when it is used to justify a finding.
  - Implementation evidence must not be confused with the target requirement itself.
  - Derived repair context must remain reviewable before patch generation.
```

```mcel-form-primitive
id: mcel-lab.form.feedback.validation-and-mount-state
app: mcel-lab
status: specified
primitive: feedback
meaning: Signals about selected app state, mount readiness, inspection mode, annotation save state, validation findings, export readiness, and repair-plan readiness.
relationships:
  - Observes app selection, aspect selection, mounted preview state, selected element state, annotation state, and validation results.
  - May render as badges, receipts, inline findings, result summaries, or machine-readable packets.
  - Serves users, developers, and automation without defining a physical slot.
constraints:
  - Ambient feedback must not interrupt or obscure blueprint inspection.
  - Corrective feedback must identify the condition it observes.
  - Feedback projections must have an owner so they are not diagnosed as random overlays.
```

```mcel-form-primitive
id: mcel-lab.form.constraint.self-hosting-safety
app: mcel-lab
status: specified
primitive: constraint
meaning: Safety law that lets MCEL Lab inspect and draft changes to its own blueprint without directly mutating its live implementation.
relationships:
  - Protects mcel-lab.form.subject.app-blueprint when selectedApp is mcel-lab.
  - Applies to annotation edits, repair plans, export packets, and patch artifact generation.
  - Separates draft intent from implementation mutation.
constraints:
  - MCEL Lab may edit its own blueprint draft.
  - MCEL Lab must not directly rewrite or apply its own live implementation.
  - Self-hosting repair output must be reviewable as an artifact before any local patch workflow applies it.
```

```mcel-form-primitive
id: mcel-lab.form.transient.point-inspection
app: mcel-lab
status: specified
primitive: transient
meaning: Temporary inspection UI used while the user is selecting a rendered element and capturing evidence.
relationships:
  - Supports element selection, bounding-box evidence, annotation drafting, and source/test ownership hints.
  - Is active only while inspect mode is enabled or a selected element receipt is being reviewed.
  - May annotate the mounted preview without mutating the mounted app.
constraints:
  - Must be explicitly mode-bound and reversible.
  - Must not fire the mounted app's ordinary actions while selecting an element.
  - Must identify selected element evidence separately from user-authored annotation intent.
```

```mcel-form-primitive
id: mcel-lab.form.interruption.unsafe-repair-boundary
app: mcel-lab
status: specified
primitive: interruption
meaning: Attention-demanding boundary used when a repair, removal, or self-hosting operation could be mistaken for a verified implementation fact or direct mutation.
relationships:
  - Protects patch planning, self-hosting edits, removal candidates, and destructive annotations.
  - Can block export or require review when evidence is stale or unsafe.
  - Explains recovery actions before any patch artifact is generated.
constraints:
  - Must interrupt or block when the user attempts direct self-mutation.
  - Must require evidence before deletion or rework candidates become patch guidance.
  - Must separate possible fixes from verified facts.
```

### Machine-readable roadmap and contract families

```mcel-use-case
id: mcel-lab.use-case.inspect-blueprint-from-doc-contract
app: mcel-lab
status: planned
type: roadmap-use-case
primary_object: AppBlueprint
user_goal: >
  Select an app, inspect its semantic form primitives, compare the declared
  contract with implementation evidence, and identify gaps before changing code.
acceptance:
  - The selected app and selected aspect are visible.
  - The Lab can show form primitives sourced from the requirements registry.
  - Evidence and findings identify whether they come from docs, runtime, source, tests, or annotations.
```

```mcel-use-case
id: mcel-lab.use-case.self-host-refactor-context
app: mcel-lab
status: planned
type: roadmap-use-case
primary_object: AppBlueprint
user_goal: >
  Inspect MCEL Lab itself, annotate rendered elements, distinguish user intent
  from verified facts, and export reviewable repair context without directly
  rewriting the live Lab implementation.
acceptance:
  - selectedApp can be mcel-lab.
  - Annotation edits remain draft state until exported.
  - Self-hosting repair output is reviewable and does not apply itself.
```

```mcel-region
id: mcel-lab.region.app-root
app: mcel-lab
status: implemented
region: lab-app-root
role: app-boundary
responsibility: Owns the MCEL Lab application boundary and exposes the selected AppBlueprint as the dominant object.
```

```mcel-region
id: mcel-lab.region.selection-context
app: mcel-lab
status: implemented
region: app-and-aspect-selection-context
role: supporting-context
responsibility: Projects app and aspect selection primitives without making their physical placement normative.
```

```mcel-region
id: mcel-lab.region.aspect-map
app: mcel-lab
status: implemented
region: aspect-map-projection
role: navigation-context
responsibility: Exposes inspectable blueprint aspects and keeps the selected aspect traceable.
```

```mcel-region
id: mcel-lab.region.blueprint-workspace
app: mcel-lab
status: implemented
region: blueprint-inspection-workspace
role: primary-work-surface
responsibility: Projects the selected AppBlueprint aspect and mounted preview evidence as the main inspection workspace.
```

```mcel-region
id: mcel-lab.region.mounted-preview
app: mcel-lab
status: partially-implemented
region: mounted-app-preview-projection
role: implementation-evidence-context
responsibility: Shows a contained app preview as evidence while preserving AppBlueprint authority.
```

```mcel-region
id: mcel-lab.region.annotation-workspace
app: mcel-lab
status: partially-implemented
region: annotation-and-selection-evidence
role: evidence-context
responsibility: Projects selected-element evidence and annotation drafts without treating draft notes as verified facts.
```

```mcel-region
id: mcel-lab.region.feedback-and-findings
app: mcel-lab
status: partially-implemented
region: validation-feedback-and-findings
role: feedback-context
responsibility: Projects mount state, validation state, findings, export readiness, and repair-plan readiness as owned feedback.
```

```mcel-requirement
id: mcel-lab.requirement.semantic-form-source-of-truth
app: mcel-lab
status: specified
type: product-law
aspect: semantic-form
object: AppBlueprint
requirement: MCEL Lab must treat parsed requirements documentation as the first source for app-form primitives before layout placement is inferred.
acceptance:
  - Lab payload includes the selected app's mcel-form-primitive blocks.
  - The Lab can distinguish form primitives from layout projections.
  - A missing primitive is reported as a contract gap rather than filled by hardcoded placement assumptions.
```

```mcel-requirement
id: mcel-lab.requirement.self-hosting-contract
app: mcel-lab
status: specified
type: safety-boundary
aspect: self-hosting
object: AppBlueprint
requirement: MCEL Lab must be able to inspect its own blueprint while preventing direct self-application of implementation changes.
acceptance:
  - selectedApp may be mcel-lab.
  - Self-hosting annotation and repair output remain drafts or artifacts.
  - Live Lab source files are not rewritten by the Lab runtime.
```

```mcel-requirement
id: mcel-lab.requirement.registry-derived-aspect-inspection
app: mcel-lab
status: specified
type: product-law
aspect: registry-integration
object: AppBlueprint
requirement: MCEL Lab must expose requirements-registry contract summaries as inspectable blueprint evidence.
acceptance:
  - App contract summaries appear in the Lab comparison payload.
  - Form primitive counts and first primitive summaries are available for each registered app.
  - Runtime adapter readiness is compared separately from documentation claims.
```

```mcel-requirement
id: mcel-lab.requirement.contained-mounted-preview
app: mcel-lab
status: specified
type: safety-boundary
aspect: mounted-preview
object: MountedApp
requirement: Mounted app previews must be contained evidence projections and must not take over MCEL Lab authority or trigger unsafe app actions during inspection.
acceptance:
  - Mount state is visible.
  - Inspect mode can select rendered elements without firing ordinary app actions.
  - The mounted preview remains evidence about the selected AppBlueprint.
```

```mcel-requirement
id: mcel-lab.requirement.point-inspection-annotations
app: mcel-lab
status: specified
type: product-law
aspect: annotations
object: RenderedElement
requirement: MCEL Lab must capture selected-element evidence separately from user-authored refactor annotation intent.
acceptance:
  - Selected element evidence includes selector and bounding box when available.
  - Annotation intent distinguishes remove, rework, move, hide, merge, investigate, and keep decisions.
  - Stale or unsupported annotations cannot silently become repair facts.
```

```mcel-requirement
id: mcel-lab.requirement.findings-to-repair-context
app: mcel-lab
status: specified
type: product-law
aspect: findings
object: RepairFinding
requirement: Findings must be reviewable as evidence-backed repair context before patch artifacts are generated.
acceptance:
  - Findings identify the affected aspect and evidence source.
  - Repair candidates include source/test ownership hints when known.
  - Patch guidance is generated only from reviewable findings and annotations.
```

```mcel-requirement
id: mcel-lab.requirement.patch-artifact-boundary
app: mcel-lab
status: specified
type: safety-boundary
aspect: patch-output
object: PatchPlan
requirement: MCEL Lab may generate repair context and patch plans, but patch application must remain an explicit external workflow.
acceptance:
  - Exported repair context is reviewable before application.
  - Generated patch artifacts use repo-relative paths.
  - The Lab does not imply deletion or mutation semantics without explicit artifact support.
```

```mcel-intent
id: mcel-lab.intent.select-app-blueprint
app: mcel-lab
status: specified
intent: Select an app blueprint and load its contract summary, aspect map, and current comparison state.
risk: read-only
requires:
  - app id
  - requirements registry payload or blueprint core data
produces:
  - selected AppBlueprint identity
  - visible selected app evidence
```

```mcel-intent
id: mcel-lab.intent.inspect-aspect
app: mcel-lab
status: specified
intent: Select an AppBlueprint aspect and reveal its primitives, evidence, findings, and next actions.
risk: read-only
requires:
  - selected app blueprint
  - aspect id
produces:
  - selected aspect evidence
  - aspect-specific work surface content
```

```mcel-intent
id: mcel-lab.intent.mount-app-preview
app: mcel-lab
status: planned
intent: Mount or refresh a contained preview of the selected app as implementation evidence.
risk: local-state
requires:
  - selected app blueprint
  - mount route or app root selector
produces:
  - mount receipt
  - contained preview evidence
```

```mcel-intent
id: mcel-lab.intent.inspect-rendered-element
app: mcel-lab
status: planned
intent: Enable point inspection and capture selected rendered element evidence from the mounted preview.
risk: local-state
requires:
  - mounted preview
  - explicit inspect mode
produces:
  - selected element receipt
  - selector, bounding-box, DOM, source, and test hints when available
```

```mcel-intent
id: mcel-lab.intent.annotate-refactor-candidate
app: mcel-lab
status: planned
intent: Save a draft annotation describing whether a rendered element should be removed, reworked, moved, hidden, merged, investigated, or kept.
risk: local-state
requires:
  - selected element evidence
  - user annotation intent
produces:
  - draft refactor annotation
  - annotation state feedback
```

```mcel-intent
id: mcel-lab.intent.validate-blueprint-contract
app: mcel-lab
status: planned
intent: Validate the selected AppBlueprint against required primitives, regions, evidence, tests, and safety boundaries.
risk: read-only
requires:
  - selected app blueprint
  - requirements registry payload
  - available implementation evidence
produces:
  - validation findings
  - missing evidence list
```

```mcel-intent
id: mcel-lab.intent.export-repair-context
app: mcel-lab
status: planned
intent: Export AI-readable repair context from selected findings, annotations, source bindings, test bindings, and safety boundaries.
risk: local-state
requires:
  - reviewed findings
  - selected annotations
  - source and test ownership hints
produces:
  - refactor export packet
  - reviewable patch planning context
```

```mcel-acceptance
id: mcel-lab.acceptance.semantic-app-form-first-slice
app: mcel-lab
status: planned
requires:
  - MCEL Lab has a parsed mcel-app contract.
  - MCEL Lab declares semantic form primitives before layout projections.
  - The requirements registry payload includes mcel-lab and its primitive summaries.
  - Runtime checks can identify the Lab's primary blueprint inspection work surface.
  - Implementation work is not marked verified until the Lab renders primitive evidence from the registry.
```

```mcel-finding
id: mcel-lab.finding.form-primitives-not-yet-first-class-ui
app: mcel-lab
status: open
aspect: semantic-form
severity: warning
problem: MCEL Lab has prose and hardcoded blueprint aspects, but its UI does not yet render parsed mcel-form-primitive blocks as a first-class app aspect.
desired_behavior: The Lab should show subjects, actions, work surfaces, context, feedback, constraints, transients, and interruptions from the requirements registry before layout placement is inferred.
```

```mcel-runtime-check
id: mcel-lab.runtime.primary-blueprint-workspace
app: mcel-lab
status: specified
mode: default
contract: mcel-lab.contract.default.blueprint-studio-health
check: primary-surface
check_category: surface
focus: blueprint-workspace
severity: critical
observes:
  - mcel-lab.form.work-surface.blueprint-inspection
expects:
  - Selected AppBlueprint workspace is visible and usable.
primary_surface_id: mcel-lab.form.work-surface.blueprint-inspection
host_selector: .mcel-lab-blueprint-primary
editor_selector: #mcel-blueprint-work-surface
min_width: 640
min_height: 420
failure_message: Selected app/aspect work surface is missing or unusable.
next_probe: lab.form.detector
```

```mcel-runtime-check
id: mcel-lab.runtime.required-semantic-projections
app: mcel-lab
status: specified
mode: default
contract: mcel-lab.contract.default.blueprint-studio-health
check: required-regions-visible
check_category: form
focus: semantic-projections
severity: error
observes:
  - mcel-lab.form.subject.app-blueprint
  - mcel-lab.form.context.app-and-aspect-selection
  - mcel-lab.form.feedback.validation-and-mount-state
expects:
  - App root, selection context, aspect map, primary blueprint workspace, and owned feedback are present.
required_regions:
  - mcel-lab.region.app-root | #mcel-lab-app | Lab app root
  - mcel-lab.region.selection-context | #mcel-blueprint-app-select | App selection context
  - mcel-lab.region.selection-context | #mcel-blueprint-aspect-select | Aspect selection context
  - mcel-lab.region.aspect-map | .mcel-lab-blueprint-navigation | Aspect map projection
  - mcel-lab.region.blueprint-workspace | .mcel-lab-blueprint-primary | Blueprint inspection workspace
  - mcel-lab.region.feedback-and-findings | #mcel-blueprint-work-badge | Mount and validation feedback
failure_message: MCEL Lab semantic form projections are missing from the rendered workbench.
next_probe: lab.form.detector
```

```mcel-runtime-check
id: mcel-lab.runtime.visual-integrity-baseline
app: mcel-lab
status: specified
mode: default
contract: mcel-lab.contract.default.blueprint-studio-health
check: visual-integrity-baseline
check_category: layout
focus: semantic-projection-readability
severity: critical
observes:
  - mcel-lab.form.work-surface.blueprint-inspection
  - mcel-lab.form.context.app-and-aspect-selection
  - mcel-lab.form.context.rendered-element-evidence
  - mcel-lab.form.feedback.validation-and-mount-state
expects:
  - Every rendered semantic projection owns its visible text, controls, and child surfaces.
  - Readable content must not paint across neighboring semantic surfaces.
  - Stacked cards, buttons, summaries, feedback rows, and evidence panels must not overlap each other.
  - Scroll containers must contain overflow instead of letting content visually overwrite nearby regions.
geometry_policies:
  - owned-semantic-projections-must-not-overlap
  - readable-text-must-remain-inside-owning-surface
  - scroll-containers-must-contain-child-content
  - primary-work-surface-must-not-be-occluded-by-context-or-feedback
failure_message: MCEL Lab has a visual-integrity failure: semantic projections collide, bleed, clip, or overwrite readable content.
next_probe: layout.visualIntegrityProbe
```

```mcel-runtime-check
id: mcel-lab.runtime.self-hosting-safety-boundary
app: mcel-lab
status: specified
mode: default
contract: mcel-lab.contract.default.blueprint-studio-health
check: lifecycle-contract-preserved
check_category: contract
focus: self-hosting-safety
severity: warning
observes:
  - mcel-lab.form.constraint.self-hosting-safety
  - mcel-lab.form.interruption.unsafe-repair-boundary
expects:
  - Self-hosting inspection can create draft annotations or export context but cannot directly rewrite live Lab implementation files.
lifecycle_assertions:
  - self-hosting-draft-does-not-apply-itself
  - repair-export-remains-reviewable-before-patch-workflow
geometry_policies:
  - semantic-form-projections-must-not-obscure-blueprint-workspace
overlay_policy:
  - point-inspection-transient-is-mode-bound
failure_message: MCEL Lab self-hosting safety boundary is not observable.
next_probe: lab.self-hosting.boundary
```


## Primary user requirements

MCEL Lab must support these user requirements.

### Select and load an app

The user can select an app blueprint from the application set.

Required operations:

```text
Select app
Load blueprint
Load implementation bindings
Show source files
Show current validation status
```

The first required targets are:

```text
Document Editor
MCEL Lab itself
Git Tools
Code Editor
```

Document Editor is the first useful product-app target because its layout expectations are familiar and easy to judge. MCEL Lab itself is the self-hosting target.

### Inspect every aspect of an app

The Lab must let the user inspect each aspect of a selected app.

Required aspects:

```text
Product identity
Object model
Workflow map
Layout binding
Action and risk policy
Capability projection
Evidence model
Source binding
Acid tests
Findings
Repair plan
```

The Lab should not collapse these into a single blob of planner output. Each aspect needs a usable UI surface.

### Mount and annotate rendered elements

The Lab must let the user mount an app into an inspectable preview, point at rendered elements, and attach durable refactor intent.

The primary operation is:

```text
mount app -> point at element -> capture evidence -> annotate intent -> save annotation -> export AI refactor context
```

The user must be able to select an element and say:

```text
This element needs to be removed or reworked because it is not doing anything.
This element is misplaced and should move to Advanced.
This element is duplicate UI and should be merged.
This element is confusing and should be reworked.
This element should stay, but needs a clearer purpose and tests.
```

The Lab must treat these as investigation-backed refactor candidates, not blind deletion requests.

When an element is selected, the Lab should capture:

```text
selector
stable data attributes
visible text
layout zone
MCEL role
bounding box
nearby elements
event-handler/source hints
CSS ownership hints
test ownership hints
documentation hints
```

The annotation must be saved with the app blueprint and included in the refactor export packet.

### Edit the blueprint safely

The user can edit the app blueprint as a draft.

Required operations:

```text
Edit purpose
Edit dominant object
Edit object model
Edit workflows
Edit layout zones
Edit placement rules
Edit action risk policy
Edit capability projections
Edit acid-test policy
Validate draft
Compare draft to implementation
```

Edits are draft blueprint changes until they are exported or turned into a patch. MCEL Lab must not silently rewrite live source files.

### Preview the generated workbench

The user can preview the app shell generated from the blueprint.

Required preview modes:

```text
Desktop
Narrow desktop
Tablet
Mobile
Navigation open/closed
Companion open/closed
Advanced hidden/shown
```

The preview is not a replacement for the app. It is a generated proof surface that shows whether the blueprint can produce a coherent app layout.

### Run acid tests

The user can run MCEL acid tests against the blueprint and the implementation.

Required test families:

```text
Static contract tests
DOM binding tests
Placement tests
Action policy tests
Source binding tests
Rendered geometry tests
Workflow behavior tests
Capability projection tests
Product cleanliness tests
```

The acid tests must prove that MCEL labels control real behavior. They must not stop at proving that data attributes exist.

### Review findings and repair plans

The user can review findings and repair plans.

Required finding types:

```text
Missing dominant object
Missing required layout zone
Ambiguous layout owner
Primary surface not protected
Advanced controls in primary toolbar
Provider UI dumped into consumer app
Action risk not classified
Evidence missing or detached
Source binding missing
Acid test missing
Rendered layout violates geometry policy
Product UI contains debug/spec scaffolding
```

Required repair output:

```text
Human-readable finding
Affected aspect
Affected source binding
Recommended layout or source change
Risk level
Test that should pass after repair
Patch plan, when available
```

### Generate patch artifacts safely

The Lab may generate repair plans and patch artifacts. It must not directly apply its own repair to the live repo.

Allowed:

```text
Generate patch plan
Generate replacement-file patch zip
Generate documentation update
Generate tests
Export blueprint JSON
```

Forbidden:

```text
Directly mutate live source without review
Apply its own patch silently
Overwrite running app implementation
Execute provider-native destructive actions during proof
```

This keeps the Lab compatible with the existing `new_patch.py` replacement-file workflow.

### Export AI refactor context

The Lab must export selected annotations and app evidence as an AI-ready refactor context.

The export is not a vague screenshot or prose note. It must include enough structured evidence for an AI repair pass to make a precise replacement-file patch.

A refactor export packet should contain:

```text
manifest.json
app-blueprint.json
annotations.json
dom-snapshot.html
layout-report.json
source-map.json
css-ownership-hints.json
js-handler-hints.json
tests-to-update.json
refactor-brief.md
```

The human-readable brief must include:

```text
selected app
selected aspect
selected element or source target
user assessment
current problem
desired behavior
allowed fixes
forbidden fixes
dependency checks
source hints
test expectations
new_patch.py delivery expectation
```

For the common "this is not doing anything" case, the brief must say that the element is a dead/unwired/duplicate/misplaced candidate and must be investigated before deletion.

## Generic MCEL element requirement

The initial design must use generic MCEL elements that the system defines, adding new reusable elements only when needed.

Bad design:

```text
Hardcode "MCEL Lab left panel"
Hardcode "MCEL Lab center preview"
Hardcode "MCEL Lab findings drawer"
```

Good design:

```text
Use element.core.app
Use element.core.collection
Use element.layout.navigation-zone
Use element.layout.primary-work-zone
Use element.layout.inspector-zone
Use element.layout.evidence-zone
Use element.layout.status-zone
Use element.layout.advanced-zone
Use element.inspection.aspect-map
Use element.inspection.source-binding
Use element.inspection.repair-finding
```

The Lab should prove that the generic element set is expressive enough to build a real MCEL workbench. When it is not expressive enough, we add reusable inspection elements rather than Lab-only hacks.

## Required generic elements

The Lab design must start with these existing generic families.

### Core elements

```text
element.core.app
element.core.region
element.core.panel
element.core.collection
element.core.field
element.core.action
element.core.status-feed
```

### App/workbench elements

```text
element.app.dominant-object
element.app.workflow-map
element.app.action-hierarchy
element.app.progressive-disclosure
element.app.product-composition
```

### Layout elements

```text
element.layout.menu-zone
element.layout.toolbar-zone
element.layout.navigation-zone
element.layout.primary-work-zone
element.layout.inspector-zone
element.layout.companion-zone
element.layout.evidence-zone
element.layout.status-zone
element.layout.advanced-zone
```

### Resource and proof elements

```text
element.resource.artifact-workspace
element.resource.source-buffer
element.resource.patch-inventory
element.proof.specimen-model
element.proof.evidence-graph
element.proof.law
element.proof.fixture
```

### Agent and render elements

```text
element.agent.reasoning-trace
element.agent.suggestion
element.render.preview
element.render.visual-surface
```

## New reusable inspection elements

The redesign may add new elements when they represent reusable inspection concepts.

Required candidates:

```text
element.inspection.aspect-map
element.inspection.aspect-panel
element.inspection.blueprint-editor
element.inspection.source-binding
element.inspection.implementation-delta
element.inspection.acid-test-result
element.inspection.repair-finding
element.inspection.repair-plan
```

These are not MCEL-Lab-only concepts. Code Editor, Git Tools, Document Editor, Website Builder, and future repair tools can also use them.

### `element.inspection.aspect-map`

Represents the list/tree of inspectable aspects for an app blueprint.

Required state:

```text
selectedAspect
availableAspects
aspectStatus
missingAspects
```

### `element.inspection.blueprint-editor`

Represents safe editing of the selected blueprint draft.

Required state:

```text
draftBlueprint
dirtyState
validationState
sourceBlueprint
implementationDelta
```

### `element.inspection.source-binding`

Connects blueprint claims to implementation files.

Required state:

```text
sourceFiles
domSelectors
cssSelectors
scripts
tests
docs
route
ownership
```

### `element.inspection.implementation-delta`

Shows what the blueprint says versus what the implementation does.

Required state:

```text
aspect
expected
observed
evidence
severity
repairHint
```

### `element.inspection.acid-test-result`

Shows static, rendered, workflow, and risk-policy test results.

Required state:

```text
testId
testFamily
status
evidence
failureMessage
repairFinding
```

### `element.inspection.repair-finding`

Represents a user-actionable finding.

Required state:

```text
findingId
aspect
severity
summary
affectedFiles
evidence
recommendedChange
blockingTest
```

### `element.refactor.annotation-map`

A durable map of user-authored element annotations for a mounted app.

This element is reusable across all apps. It records the user's product/design/refactor intent at the point of inspection and connects that intent to MCEL evidence, DOM evidence, source hints, and exportable repair context.

### `element.refactor.element-annotation`

A single annotation attached to a rendered element, layout zone, source file, workflow, or app aspect.

Required fields:

```text
id
appId
target selector or source path
annotation kind
user assessment
desired outcome
allowed outcomes
forbidden outcomes
required checks
source hints
test expectations
```

### `element.refactor.removal-candidate`

A selected element that appears dead, unused, duplicated, misleading, or harmful.

Removal candidates must require dependency checks before any deletion is proposed.

Required checks:

```text
source references
event handlers
CSS selectors
tests
documentation
feature flags
debug/dev gates
replacement path
duplicate controls
```

### `element.refactor.rework-candidate`

A selected element that has a useful purpose but is unclear, ugly, misplaced, incomplete, or not wired to meaningful behavior.

Allowed outcomes include:

```text
rework
move
hide behind advanced/dev-only disclosure
merge with duplicate control
replace with clearer control
keep with better label, purpose, and tests
```

### `element.refactor.refactor-export-packet`

An AI-ready export bundle that packages blueprint state, annotations, DOM evidence, source bindings, findings, and test expectations for a safe refactor.

The export packet must explain:

```text
what the user selected
why it was marked
what MCEL knows about it
what source files likely own it
what fixes are allowed
what fixes are forbidden
what must be verified before deletion or rewiring
what tests should be added or updated
```

## Lab layout specification

The redesigned Lab should use this generic workbench layout.

```text
Top:
  selected app
  selected aspect
  blueprint status
  validate
  preview
  run acid test
  generate repair plan

Left:
  app list
  blueprint outline
  aspect navigator

Center:
  selected aspect workspace
  blueprint editor
  generated workbench preview
  implementation comparison

Right:
  findings
  evidence
  acid-test results
  repair recommendations

Bottom:
  validation status
  dirty state
  selected source files
  patch artifact state
```

Generic MCEL binding:

```text
layout {
  identity:
    SelectedApp
    BlueprintStatus

  navigation:
    AppList
    AspectNavigator
    BlueprintOutline

  primary:
    SelectedAspectWorkspace
    WorkbenchPreview
    BlueprintEditor

  inspector:
    SelectedAspectDetails
    SourceBindings
    ImplementationDelta

  evidence:
    AcidTestResults
    Findings
    RepairPlan

  actions:
    LoadApp
    ValidateBlueprint
    RunAcidTest
    PreviewWorkbench
    GenerateRepairPatch

  status:
    LastValidation
    CurrentAspect
    DirtyState
    PatchArtifactState

  advanced:
    RawPlannerData
    ElementRegistry
    DebugTrace
    SerializedBlueprint
}
```

## Aspect model

Every app should be inspected through the same reusable aspects.

### Product identity

Questions:

```text
What is this app for?
What is the dominant object?
What is the primary workflow?
What is secondary?
What is advanced?
```

### Object model

Questions:

```text
What objects does the app expose?
Which object is primary?
How are objects related?
What object does each action affect?
```

### Workflow map

Questions:

```text
What is the normal path through the app?
What are alternate workflows?
What is advanced-only?
What must never happen silently?
```

### Layout binding

Questions:

```text
What are the required layout zones?
Where are they in the DOM?
What CSS controls them?
Does rendered geometry obey the blueprint?
Does the primary surface stay dominant?
```

### Action and risk policy

Questions:

```text
What actions are inspect-only?
What actions edit local state?
What actions write files?
What actions execute commands?
What actions mutate remote/provider state?
What actions are destructive?
```

### Capability projection

Questions:

```text
What shared capabilities does the app consume?
Who provides each capability?
How is the provider capability projected into the consumer app?
Is provider-native UI dumped into the wrong product surface?
```

### Evidence model

Questions:

```text
What proves the app did what it claims?
Where are logs, diffs, previews, test results, and findings?
Is evidence attached to the action that produced it?
```

### Source binding

Questions:

```text
Which files implement this app?
Which scripts own behavior?
Which CSS owns layout?
Which tests cover it?
Which docs specify it?
```

### Refactor annotations

The Lab must expose a first-class annotation aspect.

This aspect shows every saved annotation for the mounted app, including removal, rework, move, hide, merge, and investigate candidates.

The user should be able to inspect annotations by:

```text
selected element
layout zone
workflow
source file
priority
kind
required checks
repair status
```

Annotation kinds:

```text
remove
rework
move
hide
merge
investigate
```

The Lab must distinguish user intent from verified implementation facts.

Example:

```text
User intent:
  This PDF Debug button does not belong in the primary Document Editor toolbar.

MCEL evidence:
  Current zone: toolbar.
  Expected zone: advanced/dev-only.
  Required check: verify handlers, tests, docs, and replacement path.

Allowed repairs:
  remove if unused
  move to Advanced if still useful
  hide behind dev-only/debug mode

Forbidden repairs:
  leave in primary toolbar
  delete required export functionality without replacement
```

### Acid tests

Questions:

```text
What laws must this app pass?
Which laws are static?
Which require rendered geometry?
Which require workflow simulation?
Which require source inspection?
```

### Repair plan

Questions:

```text
What should change?
Which source files are affected?
Which tests prove the repair?
Can the repair be packaged as a replacement-file patch?
```

## Self-hosting requirement

MCEL Lab must be able to load itself as a selected app.

```text
selectedApp: mcel-lab
dominantObject: AppBlueprint
```

When self-loaded, the Lab must inspect itself through the same generic aspects.

Required self-inspection:

```text
Product identity:
  MCEL Lab is an app blueprint inspector and repair planner.

Object model:
  AppBlueprint, Aspect, Finding, AcidTest, SourceBinding, RepairPlan, PatchArtifact.

Workflow map:
  SelectApp -> InspectAspect -> EditBlueprint -> PreviewWorkbench -> RunAcidTest -> ReviewFindings -> GenerateRepairPatch.

Layout binding:
  app selector, aspect navigator, primary aspect workspace, findings/evidence panel, status band, advanced debug drawer.

Action policy:
  inspect-only, draft edit, preview, acid test, generate patch, no direct live overwrite.

Source binding:
  main_computer/web/applications/apps/mcel-lab.html
  main_computer/web/applications/scripts/mcel-lab.js
  main_computer/web/applications/scripts/mcel-specimen-planner.js
  main_computer/web/applications/scripts/mcel-elements-core.js
  main_computer/web/applications/scripts/mcel-toolkit-core.js
  pretty_docs/mcel-lab-blueprint-studio.md
  relevant tests.
```

Self-editing safety law:

```text
MCEL Lab may edit its own blueprint draft.
MCEL Lab may preview its own redesign.
MCEL Lab may generate a replacement-file patch for itself.
MCEL Lab must not directly rewrite or apply its own live implementation.
```

## First implementation target

The first useful redesign should support two target blueprints:

```text
Document Editor
MCEL Lab
```

### Document Editor target

The Lab must show that Document Editor is a writing workbench.

Expected blueprint summary:

```text
dominant object: Document
layout: left navigation, center page, right AI/history companion, compact toolbar, status band
primary workflow: navigate -> write -> edit -> review -> save
risk law: AI output is proposal-only until accepted
history law: restore creates a new version
geometry law: primary page is protected before side lanes are preserved
```

Expected findings examples:

```text
Document page is clipped.
Right companion disappears after document load.
Toolbar contains advanced controls.
Status is scattered across multiple rows.
AI output can mutate source without preview.
```

### MCEL Lab target

The Lab must show itself as a blueprint inspector.

Expected blueprint summary:

```text
dominant object: AppBlueprint
layout: app/aspect navigation, aspect workspace, findings/evidence panel, status band, advanced debug
primary workflow: select app -> inspect aspect -> preview -> acid test -> repair plan
safety law: generate patch artifacts, do not self-apply silently
```

Expected findings examples:

```text
Raw planner output is primary instead of advanced.
Aspect coverage is incomplete.
Source binding is missing for mcel-lab.js.
Acid test results are not attached to findings.
Repair plans do not reference affected files.
```

## Acid-test requirements

The Lab's acid tests must be able to exercise MCEL end-to-end.

Required acid-test chain:

```text
MCEL blueprint
-> generic elements
-> DOM layout binding
-> CSS/geometry policy
-> JS behavior policy
-> source bindings
-> findings
-> repair plan
```

The tests must fail when MCEL is present but ineffective.

Examples:

```text
The app has a primary zone, but the primary surface is clipped.
The app has an action policy, but destructive actions are visually primary.
The app has a capability projection, but raw provider UI is dumped into the consumer app.
The app has source bindings, but no tests or docs cover the selected aspect.
The Lab can inspect other apps, but cannot inspect itself.
```

## Good-looking app acceptance criteria

A blueprint is not valid merely because every zone exists.

A blueprint is valid when MCEL can prove:

```text
The dominant object is visible.
The primary workflow is obvious.
The primary work surface is visually protected.
Navigation and companion areas support the primary work instead of competing with it.
Advanced and dangerous controls are separated from primary actions.
Evidence is attached to the action that produced it.
State is visible without wasting space.
Capabilities are projected through the consumer app's language.
Debug/spec internals stay inside MCEL Lab or advanced drawers.
Rendered geometry follows the declared policy.
```

## First documentation-to-code acceptance criteria

The first real UI redesign patch for MCEL Lab should not begin until this document is satisfied by code-level tests that assert:

```text
The Lab route has a selected AppBlueprint as its dominant object.
The Lab UI uses generic layout zones.
The Lab exposes reusable aspect navigation.
The Lab can load Document Editor as a blueprint.
The Lab can load MCEL Lab itself as a blueprint.
The Lab shows source bindings for the selected app.
The Lab lets users point at rendered elements and save refactor annotations.
The Lab can export AI-ready refactor context with annotation evidence.
The Lab shows findings and acid-test results as evidence.
Raw planner/debug data is advanced, not primary.
No Lab-only hardcoded element is used where a generic MCEL element exists.
New elements are inspection-generic, not route-specific.
```

## Non-goals for the first redesign

The first redesign should not attempt to fully repair every app.

Non-goals:

```text
Automatically rewrite product apps in place
Implement full drag-and-drop app building
Replace Code Editor
Replace Git Tools
Replace Document Editor
Run destructive or remote actions during proof
Expose MCEL debug cards in product apps
```

## Summary

MCEL Lab should become the system's first self-hosting MCEL workbench.

It should let users inspect every meaningful aspect of an app, point at rendered elements, save durable refactor annotations, edit the blueprint safely, preview the generated workbench, run acid tests, review findings, export AI-ready refactor context, and generate patch-ready repair plans.

It should be built from generic MCEL elements. New elements are allowed only when they describe reusable inspection concepts.

The redesigned Lab is valuable only if it helps generate good-looking, solid apps. That means it must connect product intent, generic element semantics, layout geometry, source bindings, acid tests, and repair output into one coherent workflow.
