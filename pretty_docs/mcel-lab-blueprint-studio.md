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
