# MCEL Lab Blueprint Studio Requirements and Specification

## Status

Planning and requirements document for the first redesign patch of MCEL Lab.

This document is intentionally product-facing and implementation-facing at the same time. It defines what MCEL Lab should become, how a user operates it, how it uses MCEL from start to finish, and how it proves value as a tool for generating good-looking, solid application workbenches.

The first implementation patch after this document should not attempt to rebuild every MCEL runtime or every application. It should create one clean, useful MCEL Lab workflow that can inspect an app blueprint, preview its workbench layout, run acid tests, and produce repair findings.

## Core thesis

MCEL Lab should become **MCEL Blueprint Studio**.

The route and application identity can remain `mcel-lab`, but the user-facing job changes from a diagnostic dashboard into an app-design workbench.

MCEL Lab should answer one practical question:

```text
Can MCEL describe an app well enough to generate, preview, test, and repair a good-looking solid app shell?
```

The primary goal is to generate good-looking apps that are solid. In this context, "good-looking" does not mean decorative. It means the app has a coherent shape: a visible dominant object, predictable workflow, clear layout zones, restrained visual hierarchy, explicit action risk, evidence near the action that produced it, and responsive behavior that protects the primary work surface.

## Product purpose

MCEL Lab is the place where Main Computer users and developers design, validate, and repair app workbench blueprints before changing production apps.

The redesigned lab should let a user:

```text
select an app or create a new app blueprint
describe the app purpose and dominant object
define objects, workflows, capabilities, actions, and risk policies
assign those concepts into layout zones
preview a generated app shell at multiple viewport sizes
run MCEL acid tests against the blueprint and the live app implementation
inspect findings and repair recommendations
export a scaffold or patch plan only after the blueprint is coherent
```

The lab is allowed to show MCEL internals because it is the MCEL product surface. Product apps such as Document Editor, Git Tools, Code Editor, Wallet, Spreadsheet, and Website Builder should not display MCEL debug cards or raw contract prose.

## Non-goals for the first redesign

The first redesign is not a full visual app builder. It should not try to replace Code Editor, Layout Builder, Website Builder, or every app-specific runtime.

The first redesign should not:

```text
generate production-ready app code for every app type
provide drag-and-drop freeform design
embed raw MCEL findings into product app pages
rewrite Document Editor or Git Tools directly
execute risky app actions
trust visual previews without acid-test evidence
claim that MCEL can lay out arbitrary apps without a blueprint
```

The first redesign should prove the app-blueprint loop using one or two concrete specimens, with Document Editor as the recommended first blueprint because its desired shape is well understood.

## Dominant object

The dominant object of the redesigned MCEL Lab is:

```text
App Blueprint
```

An app blueprint is not just a JSON dump. It is a product contract.

An app blueprint describes:

```text
purpose
dominant object
secondary objects
primary workflow
secondary workflows
advanced workflows
layout zones
capabilities consumed
provider/consumer projections
primary actions
secondary actions
advanced actions
blocked or risky actions
state model
evidence model
geometry policy
responsive policy
acid tests
repair findings
implementation bindings
```

## User roles

### Product author

A product author uses MCEL Lab to describe what an app should be before implementation.

They care about:

```text
what the app is for
what object the user works on
how the app should look and feel structurally
which actions belong in the primary flow
which controls are advanced
which evidence the user needs to trust the app
```

### Application developer

An application developer uses MCEL Lab to bind a blueprint to real HTML, CSS, JavaScript, tests, and app routes.

They care about:

```text
data-mcel-layout-zone bindings
DOM ownership
CSS grid/flex rules
state transitions
responsive behavior
test fixtures
repair guidance
```

### MCEL maintainer

A MCEL maintainer uses the lab to decide whether a pattern is generic enough to promote into MCEL core.

They care about:

```text
whether the pattern is cross-app
whether it has executable evidence
whether it fails closed
whether it avoids source/runtime contamination
whether product apps remain clean
```

## Primary user operations

### 1. Select blueprint

The user chooses an existing app blueprint or creates a new one.

Required UI:

```text
app selector
blueprint status
blueprint source indicator
last acid-test run
validation state
```

Example labels:

```text
Document Editor
Git Tools
Code Editor
New Blueprint
```

MCEL requirement:

```text
Selecting a blueprint may change the lab preview and findings.
Selecting a blueprint must not mutate the target application.
```

### 2. Define purpose and dominant object

The user defines the app's product promise and the main object the app is about.

Required fields:

```text
purpose
dominantObject
emptyState
primaryUserPromise
```

Example:

```text
purpose: Write, revise, navigate, improve, and restore long-form documents.
dominantObject: Document
```

MCEL value:

```text
A layout cannot be judged until the dominant object is known.
```

### 3. Model objects and relationships

The user defines the objects the app owns or consumes.

Example Document Editor objects:

```text
Document
DocumentTab
Chapter
Section
Selection
AISuggestion
Revision
Export
Checkpoint
```

Example Git Tools objects:

```text
Repository
Branch
WorkingTree
Patch
Commit
Remote
CommandOutput
OperationLog
```

MCEL requirement:

```text
Every primary layout zone should be explainable by the dominant object or one of its immediate relationships.
```

### 4. Define workflows

The user defines the normal flow before placing controls.

Workflow categories:

```text
primary
secondary
advanced
destructive
background
```

Example Document Editor workflows:

```text
primary: OpenDocument -> Navigate -> Write -> Format -> Save
aiAssist: SelectText -> AskAI -> ReviewSuggestion -> AcceptOrDiscard
history: ViewHistory -> CompareRevision -> RestoreAsNewVersion
export: PrepareExport -> PreviewExport -> ExportFile
```

MCEL value:

```text
Workflow controls should not be peers just because they are all buttons.
```

### 5. Assign layout zones

The user maps objects, workflow stages, actions, and evidence into layout roles.

Standard layout zones:

```text
identity
menu
toolbar
navigation
primary
companion
inspector
evidence
advanced
status
```

Document Editor target:

```text
menu: global and less-used commands
toolbar: compact common writing controls
navigation: document tabs, chapters, outline
primary: document page/editor
companion: AI, history, comments, selected-text tools
evidence: diff preview, AI suggestion preview, export result
advanced: raw Git details, debug internals, provider-specific controls
status: save state, autosave, word count, conflict state
```

MCEL value:

```text
The lab should make bad placement visible before code is changed.
```

### 6. Define capabilities consumed

Apps should share capabilities without dumping provider UIs into consumer apps.

Example:

```text
Document Editor consumes GitBackedHistory as DocumentHistory.
```

Correct projection:

```text
autosave
checkpoint
version timeline
compare
restore as new version
advanced Git details
```

Incorrect projection:

```text
raw reset
checkout
rebase
manual Git command
remote push as a primary document control
```

MCEL value:

```text
The lab prevents integration from becoming app dumping.
```

### 7. Define action risk and evidence

Every meaningful action should have an impact class.

Recommended impact classes:

```text
inspect
localPreview
localEdit
localWrite
localGitWrite
commandExecutionBlocked
remoteMutationBlocked
paymentBlocked
destructiveBlocked
```

Every non-trivial action should declare evidence.

Example:

```text
CreateCheckpoint -> CheckpointStatus, RevisionTimelineEntry, GitCommitEvidence
RestoreAsNewVersion -> DiffPreview, RestoreSummary, NewVersionEntry
AskAI -> Proposal, Rationale, TargetSelection
AcceptSuggestion -> DocumentMutationEvidence, UndoAvailability
```

MCEL value:

```text
Good apps show the user what happened and why it is safe.
```

### 8. Preview workbench

The center of MCEL Lab should render a generated workbench preview from the blueprint.

The preview is not the full target app. It is a semantic shell showing:

```text
zones
dominant object placement
primary workflow placement
companion/evidence placement
advanced collapse behavior
status placement
responsive projections
```

Preview modes:

```text
desktop
tablet
mobile
nav open
nav collapsed
companion open
companion collapsed
advanced shown
advanced hidden
```

MCEL value:

```text
The preview proves whether the blueprint produces a coherent app shape before implementation.
```

### 9. Run acid tests

The user runs MCEL acid tests from the lab.

Acid tests should cover:

```text
contract completeness
DOM binding completeness
zone uniqueness
placement legality
visual hierarchy
geometry policy
responsive collapse order
state-transition behavior
risk boundary visibility
evidence placement
absence of product-page MCEL pollution
```

The lab should show pass, warn, fail, and not-covered states.

### 10. Generate repair plan

When a blueprint or implementation fails, the lab should produce repair findings in product terms and code terms.

Good finding:

```text
Document Editor primary page is below readable width because companion and navigation lanes remain open.
Expected collapse order: companion -> navigation.
Repair: collapse companion before allowing primary page below the MCEL primaryMinReadableWidth policy.
```

Bad finding:

```text
Expected 760 got 612.
```

MCEL value:

```text
The lab translates failed tests into repairs that preserve the product intent.
```

## Required app layout

The redesigned MCEL Lab should itself prove the app-workbench pattern.

### Top bar

Purpose:

```text
select app
show blueprint validity
switch viewport preview
run acid test
export scaffold or repair plan
```

Required controls:

```text
Blueprint selector
Viewport selector
Run Acid Test
Show Implementation Binding
Export Plan
```

The top bar should be compact. It must not become a stack of reports.

### Left lane: blueprint outline

Purpose:

```text
navigate the selected app blueprint
```

Sections:

```text
Overview
Purpose
Dominant Object
Objects
Workflows
Capabilities
Layout Zones
Actions
Risk Policy
Geometry Policy
Tests
Bindings
```

The left lane is not an element catalog dump. It is a blueprint table of contents.

### Center lane: workbench preview

Purpose:

```text
show generated app shell from blueprint
```

The center should visually dominate the lab.

It should include:

```text
semantic layout zones
sample object placement
workflow placement
preview of responsive collapse
selected zone highlight
```

For Document Editor, the preview should show:

```text
top menu and compact toolbar
left document navigation
center document page
right AI/history companion
bottom compact status
```

### Right lane: findings and acid tests

Purpose:

```text
show why the blueprint is or is not ready
```

Sections:

```text
Acid Test Summary
Layout Findings
Risk Findings
Capability Projection Findings
Implementation Binding Findings
Repair Plan
```

The right lane should use short actionable findings first, with advanced logs collapsed.

### Bottom status

Purpose:

```text
persistent validity and operation state
```

Examples:

```text
Blueprint valid
3 failures
Last acid test: 12:41 PM
Preview: desktop 1440px
Implementation binding: partial
```

## MCEL data model

A first implementation can represent the blueprint as plain JavaScript data, but the shape should be stable enough to promote later.

Recommended schema:

```js
{
  id: "document-editor",
  title: "Document Editor",
  purpose: "Write, revise, navigate, improve, and restore long-form documents.",
  dominantObject: "Document",
  objects: [
    { id: "Document", relationships: ["Selection", "Revision", "AISuggestion"] }
  ],
  workflows: {
    primary: ["OpenDocument", "Navigate", "Write", "Format", "Save"],
    aiAssist: ["SelectText", "AskAI", "ReviewSuggestion", "AcceptOrDiscard"],
    history: ["ViewHistory", "CompareRevision", "RestoreAsNewVersion"]
  },
  layout: {
    identity: ["DocumentTitle", "CurrentVersion"],
    menu: ["File", "Edit", "View", "Insert", "Format", "Tools"],
    toolbar: ["UndoRedo", "SaveStatus", "FormatControls", "AIQuickAction"],
    navigation: ["DocumentTabs", "ChapterList", "OutlineTree"],
    primary: ["DocumentPage", "DocumentBody"],
    companion: ["AIAssistant", "SelectionTools", "HistoryInspector"],
    evidence: ["DiffPreview", "SuggestionPreview", "ExportResult"],
    advanced: ["GitTechnicalDetails", "DebugInternals"],
    status: ["DirtyState", "AutosaveState", "WordCount", "ConflictWarning"]
  },
  geometryPolicy: {
    primaryMinReadableWidth: 760,
    topChromeMaxHeight: 120,
    collapseOrder: ["companion", "navigation"],
    primaryOverflow: "scroll-inside-canvas"
  },
  actionPolicy: {
    inspect: ["ViewOutline", "ViewHistory", "PreviewSuggestion", "PreviewDiff"],
    localEdit: ["TypeText", "FormatText"],
    localWrite: ["SaveDocument", "AutosaveDocument", "CreateCheckpoint"],
    proposalOnly: ["AskAI", "GenerateRewrite"],
    mutationBoundary: ["AcceptSuggestion", "RestoreAsNewVersion"],
    blockedAdvanced: ["RawGitReset", "RemoteSyncWithoutConfirmation"]
  }
}
```

## MCEL contract rules

The lab should enforce these rules when it evaluates a blueprint.

### Dominant object rule

```text
Every app must declare a dominant object.
The primary zone must be dedicated to that object or its main editable/viewable surface.
```

### Zone uniqueness rule

```text
Each required layout role must have one canonical owner.
Containers may group zones, but they must not impersonate the zone unless they are the visible owner.
```

### Placement rule

```text
Controls belong where their product role says they belong, not where implementation convenience puts them.
```

Examples:

```text
formatting -> toolbar
document outline -> navigation
AI suggestions -> companion/evidence
raw Git details -> advanced
manual commands -> advanced/blocked
save status -> status or compact toolbar
```

### Primary protection rule

```text
The primary work surface must be protected before side lanes are preserved.
```

For a document app this means:

```text
collapse companion before clipping the page
collapse navigation before making the page unreadable
use internal page scrolling only when needed
```

### Capability projection rule

```text
A consumer app may expose capability-native value, not raw provider UI.
```

Example:

```text
Document Editor exposes version history.
It does not expose raw Git reset as a primary document control.
```

### Evidence rule

```text
Every risky, generated, or state-changing action must have a visible evidence location.
```

### Product cleanliness rule

```text
Raw MCEL contracts, acid-test internals, and debug scaffolding are allowed in MCEL Lab.
They must not appear in ordinary product app primary flows.
```

## Required acid tests for the redesigned lab

### Blueprint completeness

The selected blueprint must define:

```text
purpose
dominant object
at least one primary workflow
layout zones
action policy
geometry policy
acid-test policy
```

### Layout preview completeness

The preview must show:

```text
identity or equivalent top context
primary work zone
at least one secondary/support zone
status or validity indicator
advanced area hidden or collapsed
```

### App-specific Document Editor preview

The Document Editor blueprint preview must show:

```text
document navigation
document page
AI/history companion
compact toolbar
status
```

### Findings usefulness

At least one fixture should produce a failing finding that includes:

```text
product problem
violated MCEL rule
expected behavior
repair recommendation
```

### No product pollution

The lab may show debug/spec internals, but product app HTML fixtures must not include visible MCEL contract cards.

## Operations manual

### Normal operation: create or inspect a blueprint

1. Open MCEL Lab.
2. Select an app blueprint.
3. Review purpose and dominant object.
4. Inspect layout zones.
5. Preview the generated workbench.
6. Run acid tests.
7. Read findings.
8. Export a repair plan or scaffold only if the blueprint is coherent.

### Normal operation: evaluate Document Editor

1. Select `Document Editor`.
2. Verify dominant object is `Document`.
3. Confirm layout preview shows left navigation, center page, right companion, compact toolbar, and status.
4. Run acid tests.
5. Check for findings about page clipping, toolbar dumping, hidden status, or non-persistent companion lanes.
6. Use repair findings to change `document.html`, `document.css`, and `document-app.js`.

### Normal operation: evaluate Git Tools

1. Select `Git Tools`.
2. Verify dominant object is `Repository`.
3. Confirm primary preview emphasizes selected repo, status, changed files, patch/commit, and evidence output.
4. Confirm remote, mirror, server lifecycle, reset, and manual commands are advanced.
5. Run acid tests.
6. Use findings to write a better Git Tools requirements document before changing the UI.

### Advanced operation: promote a pattern

A pattern may move from MCEL Lab blueprint data into MCEL core only after it has:

```text
at least two app blueprints using it
executable acid tests
clear non-goals
fail-closed behavior
source/runtime boundary documentation
product cleanliness verification
```

## First implementation target

The first implementation patch after this document should redesign MCEL Lab enough to support one useful flow:

```text
Select Document Editor
View the Document Editor blueprint
Preview a generated workbench shell
Run acid tests
Show useful findings
Keep advanced MCEL internals collapsed
```

Recommended visible layout:

```text
top: app selector, viewport selector, run acid test
left: blueprint outline
center: generated workbench preview
right: acid-test findings and repair plan
bottom: compact validation status
```

Recommended technical files:

```text
main_computer/web/applications/apps/mcel-lab.html
main_computer/web/applications/styles/mcel-lab.css
main_computer/web/applications/scripts/mcel-lab.js
main_computer/web/applications/scripts/mcel-specimen-planner.js
tests/test_mcel_lab_blueprint_studio.py
```

## Acceptance criteria for the first implementation

The first implementation should be considered useful only if all of these are true:

```text
MCEL Lab has a clear App Blueprint Studio workflow.
Document Editor blueprint is visible and understandable.
The center preview is generated from blueprint data, not hand-written prose only.
Acid-test findings are visible in the right lane.
Raw MCEL internals are available but not the main page.
The page helps a developer decide how to build a good-looking solid app.
No MCEL cards or contract prose are added to ordinary product apps.
Tests verify the blueprint, preview, findings, and no-pollution rule.
```

## Relationship to existing MCEL documents

This document complements:

```text
pretty_docs/mcel-system-guide.md
pretty_docs/mcel-user-space-contract.md
pretty_docs/mcel-application-authoring.md
pretty_docs/mcel-contract-guarantees.md
pretty_docs/mcel-debug-observability.md
```

The system guide explains what MCEL is. The user-space contract explains how source, runtime, repair, and serialization are bounded. The application-authoring guide explains how app markup and behavior participate in MCEL. This document defines the product requirements for the app that makes those concepts operational for designing better applications.

## Final principle

MCEL Lab should make MCEL valuable by turning semantic app descriptions into visible, testable, repairable app blueprints.

The lab succeeds when it helps create apps that are:

```text
clear in purpose
centered on the right dominant object
predictable in workflow
clean in layout
safe in action boundaries
honest about evidence
responsive without clipping the primary work surface
free of debug/spec pollution in product pages
solid enough to survive acid tests
```
