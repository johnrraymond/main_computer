# MCEL Application Authoring

MCEL applications are authored through three cooperating surfaces:

```text
HTML structure
+ application contract
+ JavaScript behavior
→ normalized MCEL model
→ resolved runtime
→ browser verification
→ clean source serialization
```

HTML says what exists. The application contract says what it means and what transformations are legal. JavaScript says what happened and which semantic state the application entered.

The three surfaces must converge on the same stable component identities. None of them should create a private, competing layout language.

## Status of this guide

This guide distinguishes between the platform that is live now and the application-layout system that has been browser-proven but is not yet wired into live application markup.

| Area | Current status |
| --- | --- |
| `data-mc` source elements and platform traits | Live |
| `MCEL.compile()`, `audit()`, `repair()`, and `serialize()` | Live |
| Component, route, chrome, proof, and browser APIs on `window.MCEL` | Live |
| Git Tools semantic metadata such as `data-mc-controls`, `data-mc-phase`, and `data-mc-proves` | Present in live HTML and consumed by FLOG |
| Deterministic authored layout-hint compiler | Browser-proven in FLOG, shadow-only |
| Responsive right → bottom → tab → stage resolution | Browser-proven in FLOG, shadow-only |
| Semantic user layout operations and restoration | Browser-proven in FLOG, shadow-only |
| Live `MCEL.defineApplicationContract()` / layout resolver facade | Proposed V1 API, not yet live |

Do not write production code as though the proposed layout facade already exists. The examples in the application-layout sections define the contract that live integration should implement.

## 1. Division of responsibility

### HTML owns concrete structure

HTML owns:

- content;
- native controls;
- labels and accessibility;
- stable element identity;
- useful document order;
- local semantic annotations;
- application hooks that already exist.

Start with valid, understandable HTML:

```html
<section id="git-workflow">
  <h2>Workflow</h2>
  <button type="button" id="git-plan-button">Plan</button>
</section>
```

Then add only the information that ordinary HTML cannot express:

```html
<section
  id="git-workflow"
  data-mc="panel"
  data-mc-component-id="git-tools.workflow"
  data-mc-authority="primary-work"
  data-mc-emits="workflow.state workflow.selection"
  data-mc-persistence="phase-persistent"
>
  <h2>Workflow</h2>
  <button type="button" id="git-plan-button">Plan</button>
</section>
```

Removing the MCEL attributes should leave functional, ordinary HTML.

### The application contract owns declarative law

The application contract owns:

- semantic units;
- parent/child ownership;
- relationships and named signals;
- phases and presentation states;
- hard constraints and soft preferences;
- legal layout zones and fallback order;
- minimum useful dimensions;
- user-mutability;
- material and chrome roles;
- proof obligations.

The contract answers:

> What does this application mean, and which transformations are legal?

### JavaScript owns behavior

JavaScript owns:

- fetching and mutations;
- application state;
- commands and events;
- semantic phase transitions;
- user layout operation dispatch;
- preference persistence;
- invoking compile, audit, repair, proof, and serialization.

JavaScript should not manually reproduce the layout contract through scattered class changes, inline coordinates, or hidden breakpoint logic.

## 2. Live MCEL source contract

The machine-readable platform contract lives at:

```text
main_computer/web/applications/scripts/mcel-contract.js
```

It is exposed as `window.McelLabContract` and includes:

```text
attributes
defaults
schema
layoutPolicies
platformPolicies
contractGuarantees
userSpaceContract
runtimeOwnedAttributes
runtimeOwnedClasses
themes
contractVersion
```

Useful inspection calls include:

```js
McelLabContract.buildContractEnvelope();
McelLabContract.buildUserSpaceContract();
McelLabContract.listContractGuarantees();
McelLabContract.listUserContractClauses();
```

### Source element types

MCEL discovers source elements through `data-mc`.

Current source types are:

| `data-mc` value | Intended use |
| --- | --- |
| `panel` | General semantic surface |
| `feed` | Ordered or updating collection |
| `command-row` | Compact action surface |
| `proof-surface` | Evidence or verification output |
| `smart-region` | Adaptable semantic region |

Example:

```html
<section
  data-mc="panel"
  data-mc-kind="work"
  data-mc-flow="stack"
  data-mc-rank="primary"
  data-mc-state="idle"
  data-mc-density="auto"
  data-mc-size-policy="adaptive"
  data-mc-overflow-policy="contain"
  data-mc-scroll-policy="external"
>
  ...
</section>
```

### Live layout policies

`data-mc-size-policy` accepts:

```text
adaptive
fixed
fluid
intrinsic
```

`data-mc-overflow-policy` accepts:

```text
visible
contain
clip
delegate
paginate
virtualize
expand
collapse
```

`data-mc-scroll-policy` accepts:

```text
never
auto
required
external
child-only
viewport-only
```

These are source policies. Actual rectangles, overflow observations, and scrollbar ownership are runtime evidence and must not be serialized into source.

### Live platform traits

The current contract also recognizes traits for components, state, data, forms, actions, rendering, accessibility, performance, and security.

```html
<section
  data-mc="panel"
  data-mc-component="repository-workflow"
  data-mc-component-kind="component"
  data-mc-slot="body"
  data-mc-prop-contract="selected-project operation-state"
  data-mc-state-owner="view"
  data-mc-state-scope="repository"
  data-mc-state-policy="transactional"
  data-mc-render="island"
  data-mc-hydration="interaction"
  data-mc-focus-policy="restore"
  data-mc-a11y-policy="strict"
  data-mc-performance-budget="small"
  data-mc-security-policy="networked"
>
  ...
</section>
```

Forms and actions can declare their policies directly:

```html
<form
  data-mc-submit="repository.configure"
  data-mc-validation="hybrid"
  data-mc-dirty-policy="warn"
  data-mc-error-policy="inline-and-summary"
>
  <label>
    Remote
    <input name="remote" required>
  </label>

  <button
    type="submit"
    data-mc-action="save-remote"
    data-mc-target="repository.workflow"
    data-mc-event-policy="transaction"
    data-mc-swap-policy="lawful-region"
  >
    Save
  </button>
</form>
```

Declare only policies the application actually needs. Sparse, accurate metadata is better than exhaustive annotation.

## 3. Stable application identity

Every meaningful application region needs a stable semantic identity.

```html
<section
  id="git-evidence-pane"
  data-mc-component-id="git-tools.evidence"
  data-mc-component-label="Operation evidence"
  data-mc-component-owner="git-tools.phase-support"
  data-mc-feature-id="git-tools.publish-proof"
>
```

Prefer semantic IDs:

```text
git-tools.application
git-tools.project-identity
git-tools.command
git-tools.workflow
git-tools.server
git-tools.evidence
git-tools.status
```

Avoid positional identities:

```text
panel-3
right-column-child-2
component-17191
```

Stable identities are the join keys used by HTML, contract data, JavaScript state, user preferences, migration, evidence, and FLOG diagnostics.

## 4. Semantic relationship language

Application relationships describe meaning, not placement.

The Git Tools HTML already uses this vocabulary:

```text
controls
selects
navigates
scopes
reflects
confirms
proves
emits
consumes
```

### Inline HTML form

```html
<header
  data-mc-component-id="git-tools.command"
  data-mc-controls="git-tools.workflow"
  data-mc-emits="command.intent"
>
  ...
</header>
```

```html
<aside
  data-mc-component-id="git-tools.project-selector"
  data-mc-selects="git-tools.workflow"
  data-mc-navigates="git-tools.workflow"
  data-mc-scopes="git-tools.workflow git-tools.server"
>
  ...
</aside>
```

```html
<output
  data-mc-component-id="git-tools.status"
  data-mc-confirms="workflow.state"
  data-mc-consumes="workflow.state operation.state"
>
  ...
</output>
```

```html
<section
  data-mc-component-id="git-tools.evidence"
  data-mc-proves="operation.result"
  data-mc-consumes="command.output publish.result"
>
  ...
</section>
```

Relationship strength is separate from the relationship itself:

```html
data-mc-relationship-strength="controls:hard proves:strong reflects:preferred"
```

Targets are stable component IDs or named signals, not CSS selectors.

### Sidecar contract form

The same information may be declared in a sidecar module:

```js
export const gitToolsRelationships = Object.freeze([
  {
    subject: "git-tools.command",
    relation: "controls",
    object: "git-tools.workflow",
    strength: "hard",
  },
  {
    subject: "git-tools.project-selector",
    relation: "scopes",
    object: "git-tools.workflow",
    strength: "hard",
  },
  {
    subject: "git-tools.status",
    relation: "confirms",
    object: "workflow.state",
    strength: "hard",
  },
  {
    subject: "git-tools.evidence",
    relation: "proves",
    object: "operation.result",
    strength: "strong",
  },
]);
```

Inline and sidecar declarations must normalize into the same representation. Conflicts should fail closed rather than silently choose one source.

## 5. Phases, authority, and presentation

Applications should state when regions matter.

```html
<section
  data-mc-component-id="git-tools.server"
  data-mc-phase="planning execution"
  data-mc-presentation-set="planning-support operation-support"
  data-mc-authority="phase-support"
  data-mc-deferability="progressive"
>
  ...
</section>
```

```html
<section
  data-mc-component-id="git-tools.evidence"
  data-mc-phase="execution proof-review"
  data-mc-presentation-set="operation-evidence"
  data-mc-authority="evidence"
>
  ...
</section>
```

Useful application-level metadata includes:

```text
data-mc-authority
data-mc-persistence
data-mc-growth
data-mc-deferability
data-mc-layout-affordance
data-mc-phase
data-mc-presentation-set
```

Separate required behavior from preference:

```html
<section
  data-mc-hard-constraints="
    workflow-surface-visible
    project-context-visible
    active-critical-controls-visible
  "
  data-mc-soft-preferences="
    evidence-near-workflow
    support-secondary-dock
    selector-compact-when-inactive
  "
>
```

A hard constraint determines validity. A soft preference can rank valid realizations but cannot repair an invalid one.

## 6. Application contract module

The recommended V1 application contract is immutable, serializable data.

It can be authored as JavaScript or JSON. It should not contain DOM nodes, callbacks, computed browser measurements, or CSS declarations.

```js
export const gitToolsContract = Object.freeze({
  id: "git-tools",
  version: 1,
  root: "git-tools.application",

  units: {
    "git-tools.application": {
      role: "application",
      children: [
        "git-tools.project-identity",
        "git-tools.command-workflow",
        "git-tools.persistent-feedback",
        "git-tools.phase-support",
      ],
      layout: {
        kind: "dock-workbench",
        zones: ["top", "left", "center", "right", "bottom", "tab", "stage"],
      },
    },

    "git-tools.project-identity": {
      role: "identity",
      element: "#git-project-selector-panel",
      layout: {
        prefer: "left",
        allowed: ["left", "top", "trigger"],
        fallback: ["top", "trigger"],
        inactive: "trigger",
        strength: "strong",
      },
      user: {
        id: "repository.project-identity",
        mutable: ["placement", "collapsed"],
      },
    },

    "git-tools.command-workflow": {
      role: "primary-work",
      element: "#git-workflow-accordion",
      layout: {
        prefer: "center",
        allowed: ["center"],
        policy: "command-inline-header",
        minInline: 520,
        minBlock: 360,
        strength: "required",
      },
    },

    "git-tools.persistent-feedback": {
      role: "persistent-feedback",
      layout: {
        prefer: "bottom",
        allowed: ["bottom", "top"],
        fallback: ["top"],
        policy: "shared-horizontal-band",
        strength: "strong",
      },
    },

    "git-tools.phase-support": {
      role: "phase-support",
      layout: {
        prefer: "right",
        allowed: ["right", "bottom", "tab", "stage", "trigger"],
        fallback: ["bottom", "tab", "stage", "trigger"],
        inactive: "trigger",
        policy: "bounded-side-drawer",
        minInline: 300,
        minBlock: 260,
        maxShare: 0.32,
      },
      user: {
        id: "repository.phase-support",
        mutable: ["placement", "share", "collapsed", "tab-group"],
      },
    },
  },
});
```

This is layout intent, not another spelling of CSS.

## 7. Layout-hint language

The layout-hint language lets authors state a good default without forcing FLOG to invent it through broad search.

The core questions for each unit are:

```text
Where does it prefer to live?
Where is it allowed to live?
What is its ordered fallback chain?
How strong is the preference?
What size makes it useful?
What may the user change?
What happens while it is inactive?
```

### Proposed inline V1 vocabulary

The following `data-mc-layout-*` vocabulary has been proven in FLOG fixtures but is not yet part of the live `mcel-contract.js` schema:

```html
<section
  data-mc-layout-prefer="right"
  data-mc-layout-allowed="right bottom tab stage trigger"
  data-mc-layout-fallback="bottom tab stage trigger"
  data-mc-layout-strength="preferred"
  data-mc-layout-policy="bounded-side-drawer"
  data-mc-layout-inactive="trigger"
  data-mc-layout-min-inline="300"
  data-mc-layout-min-block="260"
  data-mc-layout-max-share="0.32"
  data-mc-layout-user-id="repository.phase-support"
  data-mc-layout-user-mutable="placement share collapsed tab-group"
>
```

| Proposed attribute | Meaning |
| --- | --- |
| `data-mc-layout-root` | Stable root layout-unit ID |
| `data-mc-layout` | Root arrangement such as `dock-workbench` |
| `data-mc-layout-zones` | Zones accepted by the parent |
| `data-mc-layout-prefer` | First-choice placement |
| `data-mc-layout-allowed` | Legal placements |
| `data-mc-layout-fallback` | Ordered fallback chain |
| `data-mc-layout-strength` | `required`, `strong`, `preferred`, or `opportunistic` |
| `data-mc-layout-policy` | Internal realization policy |
| `data-mc-layout-inactive` | Inactive realization, such as `trigger` |
| `data-mc-layout-internal` | Intended arrangement of child regions |
| `data-mc-layout-min-inline` | Minimum useful inline dimension |
| `data-mc-layout-min-block` | Minimum useful block dimension |
| `data-mc-layout-max-share` | Maximum parent or root share |
| `data-mc-layout-user-id` | Stable ID used by user preferences |
| `data-mc-layout-user-mutable` | User operations permitted for the unit |

Valid structural placements are expected to include:

```text
top
left
center
right
bottom
tab
stage
trigger
overlay
```

Do not encode fixed screen coordinates as layout hints:

```html
<!-- Wrong abstraction -->
<section
  data-mc-left="843px"
  data-mc-top="126px"
  data-mc-width="371px"
>
```

The contract describes structure. The resolver derives geometry from the hierarchy, content, theme metrics, chrome, and current capacity.

## 8. Responsive presentation contract

Responsive authoring should describe semantic presentation, not only named breakpoints.

```js
export const gitToolsPresentations = Object.freeze({
  "project-selection": {
    active: ["git-tools.project-selector"],
    dominant: "git-tools.project-selector",
    required: ["git-tools.status"],
  },

  "selected-project-default": {
    active: ["git-tools.workflow"],
    dominant: "git-tools.workflow",
    required: [
      "git-tools.project-context",
      "git-tools.command",
      "git-tools.status",
    ],
  },

  planning: {
    wide: {
      dominant: "git-tools.workflow",
      companion: "git-tools.server",
      realization: "right",
    },
    medium: {
      dominant: "git-tools.workflow",
      companion: "git-tools.server",
      realization: "bottom",
    },
    compact: {
      dominant: "git-tools.server",
      required: [
        "git-tools.workflow-summary",
        "git-tools.project-context",
        "git-tools.status",
      ],
      realization: "stage",
    },
  },

  "proof-review": {
    wide: {
      dominant: "git-tools.workflow",
      companion: "git-tools.evidence",
      realization: "right",
    },
    compact: {
      dominant: "git-tools.evidence",
      required: [
        "git-tools.workflow-summary",
        "git-tools.project-context",
        "git-tools.status",
      ],
      realization: "stage",
    },
  },
});
```

The proven Git Tools fallback sequence is:

```text
right dock
→ bottom dock
→ tab workbench
→ sequential stage
```

The live resolver should derive transitions from feasibility and use hysteresis where adjacent valid regions overlap. A viewport label such as `medium` is descriptive; it is not the source of truth.

## 9. Constraints

Keep Boolean validity separate from numeric preference.

```js
export const gitToolsConstraints = Object.freeze({
  required: [
    "active-critical-controls-visible",
    "no-foreign-control-interception",
    "no-undeclared-overlap",
    "project-context-resolvable",
    "active-stage-has-return-path",
    "primary-work-meets-phase-floor",
  ],

  preferred: [
    "phase-support-near-workflow",
    "project-and-status-share-band",
    "inactive-support-as-triggers",
  ],
});
```

A realization that violates a required constraint is invalid. It is not a lower-scoring valid candidate.

Ranking begins only after the hard gates pass.

## 10. Theme and chrome roles

Application contracts identify visual roles without selecting final colors or effects:

```js
export const gitToolsVisualRoles = Object.freeze({
  "git-tools.workflow": {
    material: "primary-work-surface",
    chromeZone: "application-center",
  },
  "git-tools.server": {
    material: "support-surface",
    chromeZone: "application-support",
  },
  "git-tools.evidence": {
    material: "proof-surface",
    chromeZone: "application-support",
  },
  "git-tools.status": {
    material: "status-emitter",
    chromeZone: "persistent-feedback",
  },
});
```

The same roles may be interpreted by different theme systems.

A black-on-white theme may use structural rules, restrained depth, text, and icons. A black-glass theme may use absorption, edge emission, and controlled status illumination. Application semantics and layout capabilities should not change with the theme.

Current live themes registered by `mcel-contract.js` are:

```text
theme-machine
theme-local
theme-saas
theme-editorial
theme-luxury
theme-civic
theme-accessible
theme-debug
```

Named future themes such as Vulcan and Enterprise Yellow are design targets, not registered live theme IDs yet.

Chrome is runtime framing. Source may declare chrome participation, but generated chrome nodes and browser measurements remain disposable runtime output.

## 11. JavaScript behavior code

Application code should manipulate semantic state.

```js
async function publishProject(projectId) {
  const operation = await gitApi.publish(projectId);

  appState.transition({
    phase: "execution",
    signals: {
      "operation.state": "running",
      "operation.id": operation.id,
    },
  });
}
```

```js
function receivePublishResult(result) {
  appState.transition({
    phase: "proof-review",
    signals: {
      "operation.state": "complete",
      "operation.result": result,
    },
  });
}
```

The layout resolver observes the semantic phase and resolves the current presentation.

Avoid imperative geometry code:

```js
// Do not create a second layout language in behavior code.
evidencePanel.style.right = "0";
evidencePanel.style.width = "380px";
workflowPanel.classList.add("workflow-with-evidence-open");
document.body.classList.add("proof-layout");
```

CSS classes may implement a resolved plan, but application behavior should request the plan semantically.

## 12. User layout operations

User movement should produce semantic dock-tree operations rather than raw coordinates.

### Dock

```json
{
  "id": "dock-evidence-right",
  "kind": "dock",
  "userId": "repository.phase-support",
  "placement": "right",
  "relativeTo": "repository.command-workflow"
}
```

### Resize share

```json
{
  "id": "resize-evidence",
  "kind": "resize-share",
  "userId": "repository.phase-support",
  "share": 0.24
}
```

### Tab

```json
{
  "id": "tab-evidence-with-workflow",
  "kind": "tab-with",
  "userId": "repository.phase-support",
  "targetUserId": "repository.command-workflow"
}
```

### Collapse

```json
{
  "id": "collapse-project-identity",
  "kind": "collapse",
  "userId": "repository.project-identity",
  "collapsed": true
}
```

The proven operation vocabulary is:

```text
dock
tab-with
resize-share
collapse
undo
reset
```

User-mutability is opt-in:

```text
placement
share
collapsed
tab-group
```

The runtime should retain a temporarily infeasible preference and restore it when enough capacity returns.

## 13. Authority and conflict resolution

Resolve competing sources in this order:

1. Semantic, accessibility, and critical-control invariants.
2. Physical feasibility.
3. Required application constraints.
4. User-persisted preferences.
5. Authored strong and preferred layout hints.
6. Generic defaults.
7. Opportunistic theme effects.

The user may move evidence from right to bottom.

The user may not:

- hide the only project identity;
- cover a critical control;
- collapse required primary work;
- remove the return path from a sequential stage;
- force a unit below its minimum useful dimensions.

Rejected or remediated preferences should produce an explanation record:

```js
{
  requested: "right",
  rejectedBy: "minimum-inline-capacity",
  selected: "bottom",
  preferenceRetained: true,
}
```

## 14. Live `MCEL` JavaScript facade

The current public facade is defined in:

```text
main_computer/web/applications/scripts/mcel-core.js
```

Current live entry points include:

```js
MCEL.compile(sourceHtml, options);
MCEL.serialize(runtimeRoot, options);
MCEL.repair(runtimeRoot, options);
MCEL.audit(sourceHtml, runtimeRoot, options);
MCEL.inspect(element, options);

MCEL.runProof(options);
MCEL.runBrowserProof(runtimeRootOrHtml, options);
MCEL.buildEvidencePacket(options);
MCEL.buildSubsumptionLattice();
MCEL.buildAdoptionCase(options);
MCEL.buildUserSpaceContract();
MCEL.listUserContractClauses();

MCEL.defineComponent(name, manifest, options);
MCEL.createComponentInstance(name, options);
MCEL.transition(instance, transitionName, payload);
MCEL.checkLayoutContract(instance, observation);
MCEL.checkStyleContract(instance, observation);
MCEL.serializeComponent(instance, options);
MCEL.repairComponent(instance, strategyName, payload);

MCEL.defineRoute(name, manifest, options);
MCEL.createRouteInstance(name, options);
MCEL.enterRoute(instance, paramsOrOptions, query);
MCEL.leaveRoute(instance, options);

MCEL.listChromes();
MCEL.normalizeChrome(chrome);
MCEL.describeChrome(chrome);
MCEL.applyChrome(runtimeHtml, options);
```

### Current compile, audit, and serialization lifecycle

```js
const sourceRoot = document.querySelector("#git-tools-app");
const sourceHtml = sourceRoot.outerHTML;

const compiled = MCEL.compile(sourceHtml, {
  theme: "theme-machine",
  reason: "git-tools:mount",
});

const audit = MCEL.audit(sourceHtml, compiled.runtimeRoot, {
  reason: "git-tools:mount-audit",
});

if (audit.failed) {
  throw new Error("MCEL application audit failed");
}

sourceRoot.replaceWith(compiled.runtimeRoot);
```

Before save or export:

```js
const serialized = MCEL.serialize(compiled.runtimeRoot, {
  reason: "git-tools:save",
});

if (!serialized.serializerClean) {
  throw new Error("MCEL runtime state leaked into source");
}

saveSource(serialized.sourceHtml);
```

Never save `runtimeRoot.innerHTML` directly.

### Proposed application-layout facade

The application-layout APIs below are the intended V1 surface. They are not present in `mcel-core.js` yet:

```js
MCEL.defineApplicationContract(contract);
MCEL.resolveLayout(context);
MCEL.applyUserLayoutOperation(operation);
MCEL.exportUserLayoutPreferences();
MCEL.resetUserLayout();
```

A future bootstrap may look like:

```js
const compiled = MCEL.compile(sourceHtml, {
  theme: currentTheme(),
  chrome: currentChrome(),
  applicationContract: gitToolsContract,
  applicationState: state.snapshot(),
  userLayoutHints: loadLayoutPreferences(),
});
```

Until that integration exists, keep application contract modules and behavior code shaped for the normalized model without pretending the live facade already consumes them.

## 15. Recommended application file structure

```text
main_computer/web/applications/
  apps/
    git-tools.html

  scripts/
    git-tools-contract.js
    git-tools-state.js
    git-tools-actions.js
    git-tools-layout.js
    git-tools.js

  styles/
    git-tools.css
```

### `git-tools.html`

Contains:

- ordinary markup;
- native semantics;
- stable IDs;
- sparse local `data-mc-*` declarations;
- no browser measurements;
- no generated wrappers.

### `git-tools-contract.js`

Contains immutable data:

- units;
- relationships;
- phases;
- legal layout placements;
- fallback order;
- hard and soft constraints;
- visual roles;
- user-mutability.

### `git-tools-state.js`

Contains:

- application state;
- current phase;
- named signals;
- semantic transitions.

### `git-tools-actions.js`

Contains:

- API calls;
- commands;
- mutations;
- conversion of results into state transitions.

### `git-tools-layout.js`

Contains only integration with the generic MCEL layout runtime:

- contract registration;
- user-operation dispatch;
- preference loading and saving;
- reset and undo;
- resolver explanations.

It should not contain application-specific pixel geometry.

### `git-tools.js`

Bootstraps the pieces.

## 16. Normalization and consensus rules

HTML and sidecar contract data may overlap, but there is one normalized model.

Recommended merge rules:

1. Native HTML semantics remain authoritative for element type, labels, and accessibility.
2. Stable `data-mc-component-id` values bind source elements to contract units.
3. Sidecar contract data supplies non-local relationships and presentation rules.
4. Repeated identical declarations are accepted.
5. Contradictory declarations fail closed.
6. Missing required contract targets fail closed.
7. Browser observations never mutate the source contract.
8. User preferences remain a separate overlay, not source edits.
9. Generated runtime structure is disposable.
10. Serialization returns clean source without runtime evidence.

The normalized model should contain:

```text
semantic units
ownership
relationships
phases and signals
layout capabilities
fallback order
constraints
visual roles
user mutability
resolved presentation
```

HTML, JavaScript, themes, chrome, user preferences, and FLOG all meet at this boundary.

## 17. What belongs in source

Keep in authored source or its sidecar contract:

- native semantic markup;
- stable IDs;
- component ownership;
- state and data policies;
- relationships;
- phases;
- hard constraints and soft preferences;
- legal layout placements;
- fallback order;
- minimum useful dimensions;
- user-mutability;
- material and chrome roles.

Do not place in source:

- measured rectangles;
- computed focus share;
- browser overflow facts;
- generated wrappers;
- repair records;
- FLOG scores;
- screenshot paths;
- pointer interception samples;
- temporary drag coordinates;
- current responsive remediation;
- runtime proof results.

Those are runtime state or evidence.

## 18. Verification

FLOG verifies the resolved result. It should not be the first place layout intent is invented.

The intended path is:

```text
annotated HTML
+ application contract
+ application state
+ user preference overlay
→ deterministic hinted realization
→ browser rendering
→ FLOG hard gates and counterexamples
```

FLOG should verify:

- all stable relationship targets resolve;
- every required phase has a legal presentation;
- every sequential stage has a return path;
- active critical controls are visible and actionable;
- no foreign surface intercepts controls;
- no undeclared overlap exists;
- painted ownership meets phase floors;
- responsive transitions cover the supported domain;
- hysteresis prevents resize oscillation;
- user preferences remediate and restore correctly;
- serialization stays clean.

Candidate search is a fallback for incomplete or failed hints, not the default authoring mechanism.

## 19. Authoring checklist

Before calling an MCEL application properly authored:

- The HTML remains understandable without MCEL.
- Native controls, labels, and landmarks are correct.
- MCEL source elements use supported live `data-mc` types.
- Every major region has a stable semantic ID.
- Ownership is explicit.
- Relationship targets resolve to stable units or named signals.
- Required and preferred behavior are separate.
- Phase membership is explicit.
- Primary work and persistent context are identifiable.
- The application contract is immutable and serializable.
- Layout units declare legal placements and fallback order.
- Required units declare useful minimum dimensions.
- User-mutability is opt-in.
- JavaScript changes semantic state rather than pixel geometry.
- User preferences contain semantic operations, not raw coordinates.
- Generated runtime information is absent from source.
- Compilation is deterministic.
- Audit and proof fail closed.
- Repair touches only generated runtime.
- Serialization returns clean source.
- FLOG verifies the default and representative user-mutated layouts.

## Bottom line

MCEL application authoring is not merely HTML with many attributes.

It is a consensus among three first-class sources:

```text
HTML
  owns concrete structure and native meaning

application contract
  owns semantic law, layout capabilities, phases,
  responsive fallbacks, user mutability, and visual roles

JavaScript
  owns state transitions, commands, persistence,
  and semantic layout operations
```

The contract is the shared language. HTML, JavaScript, themes, chrome, user preferences, the resolver, and FLOG all converge on it.
