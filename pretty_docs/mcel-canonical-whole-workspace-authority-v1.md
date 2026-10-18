# MCEL Patch 14 — canonical structural control of application workspaces

## Source of authority

A `mcel.layout-tree.v1` declaration in the application DSL is validated in
`mcel_application_ir.py`, preserved in the normalized semantic fingerprint,
projected into the generated `contracts/layout.js`, and mounted by the generic
host-bound application runtime. The host must match **one** canonical root.
No Calculator/Code Editor CSS file is rewritten by this patch.

Patch 13 governed one nested control surface in each app. Patch 14 extends the
canonical root to Calculator's shell and Code Editor's shell. The generated
layout covers Calculator's workspace panes and graphing controls, and Code
Editor's workbench rail, sidebar, editor, inspector, status and proof dock.
The application retains control over its DOM, internal editor, graph engine,
workbench interactions and visible/invisible application modes.

## Structural area variants

A canonical group may declare up to eight `variants`, each with a list of
explicitly constrained CSS `tracks` and rectangular named `areas` populated
from declared child IDs. Example:

```javascript
{
  id: 'workspace', selector: '#workspace', role: 'group',
  variants: [
    { tracks: ['minmax(0, 1fr)', 'minmax(0, 1fr)'],
      areas: ['editor inspector'] },
    { tracks: ['minmax(0, 1fr)'],
      areas: ['editor', 'inspector'] }
  ],
  children: [
    {id: 'editor', selector: '.editor', role: 'region'},
    {id: 'inspector', selector: '.inspector', role: 'region'}
  ]
}
```

The IR rejects unbounded CSS tracks, unknown area names, malformed rectangles,
undeclared node properties, and incompatible row/column definitions. Browser
validation repeats the contract independently. The runtime trials every
candidate in an inert DOM clone, applies structural `display`,
`grid-template-columns`, `grid-template-areas`, `gap` and named child
`grid-area` with explicit runtime CSS ownership, verifies computed tracks and
child visibility, compares candidate heights and unused cells, and restores
preexisting inline CSS exactly on rejection or unmount. Authored CSS remains a
styling input, not proof that a candidate is conformant.

In application-defined compact modes, a pane that is hidden by the host stays
hidden: MCEL must not turn it on merely because an alternative references it.
Each candidate area map must match the visible declared regions. Visible
undeclared direct siblings cause a candidate to be rejected. Candidate
acceptance includes the independent atomic text-split detector when available,
so a candidate cannot win on compactness if it splits text elsewhere within
its governed surface.

## Runtime and readiness

A node with `requireVisible: true` is a required runtime region. If the host
has not yet made it measurable, status is `deferred`, **not** `pass` or a CSS
fallback. The generic mount observes host visibility and readiness transitions
and re-evaluates when conditions change. Host-owned `hidden` attribute changes
also trigger reevaluation in active layouts; resizing uses the existing
`ResizeObserver`. MCEL-generated inline style changes never by themselves
trigger another mode transition.

Calculator's previous workspace-inference controller no longer competes for
its layout once canonical ownership is claimed. The standalone inference API
continues to exist for other consumers.

For large Code Editor panes, `verifyControls: false` disables the earlier
"every descendant button is at least 40px" heuristic, because the editor
contains intentionally smaller controls. It **does not certify** every nested
interaction target, keyboard focus path, editor/Monaco mode, or accessibility
contract. The canonical surface boundary and grid placement are governed;
application-internal presentation and behavior are not wholly MCEL authored.

## Evidence and exclusions

Tests compile real application DSL packages, inspect fingerprinted generated
layout modules, run the host-bound mount with real Calculator/Code Editor HTML
and CSS in Chromium, test graphing/basic/compact arrangements, required-hidden
Code Editor startup, automatic visibility changes, independent text integrity,
wrong CSS variants, fail-closed behavior, and unmount restoration.

Tests stub host capability facades to focus on layout behavior: this is **not**
proof of Code Editor transaction execution, complete application behavior,
full keyboard/accessibility certification, or fully IR-native presentation.
`presentationAuthority` remains `existing-host-html` and
`fullSemanticRuntimeProven` remains a separate, not-yet-earned gate.

Runtime application is reversible and **never authorizes permanent edits** to
source CSS, DOM templates, or semantic application source.
