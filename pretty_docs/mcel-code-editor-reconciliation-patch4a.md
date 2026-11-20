# MCEL Code Editor Reconciliation — Patch 4A

## Scope

Read-only semantic and VS Code-workspace audit of the canonical host-bound Code Editor and the incumbent paired-source semantic Code Editor. The tool does **not** edit either application, switch routes, execute capabilities, stage compiler candidates, or promote an application. It builds on Patch 3.

## Run

```powershell
python -m tools.mcel_code_editor_reconciliation_audit `
  --output runtime/reports/mcel-code-editor-reconciliation/code-editor-4a.json
```

The report contains all eight canonical intents, their incumbent intent or generated-editor behavior counterparts, the complete inventory of 31 additional incumbent intents, capability risks, a host-only IR mutation explanation, twelve VS Code workspace roles, and promotion blockers. This is an **audit**, not operational certification.

## Interpretation

* Canonical `application.js` owns a host-bound VS Code-style shell with activity rail, Explorer, editor, inspector, proof dock, and status bar. The canonical paired `application.html` is still missing.
* The paired semantic fixture owns a deeper operational surface (39 intents) with generated document tabs, folder-tree support, and Monaco. No separate Code Editor semantic workspace planner is enabled in its host.
* Similar intent names or user goals are *candidates* for equivalence, not proof. Every explicit write/receipt pathway must be verified before any authority migration.
* The post-host incumbent IR includes an external-job policy attached to the generated document editor. Patch 3 compares against that host projection, which explains why its incumbent comparison fingerprints are different from raw compiler fingerprints.
* VS Code is a reference for **semantics**: primary navigation, hierarchy, editor dominance, tabs, secondary inspector, dockable output, and status. MCEL Theme/Form/Chrome remain the presentation authority.
* The snapshot proves Monaco, tabs, folder-tree rendering and layout infrastructure already exist. It does not establish a fully interactive terminal, real browser geometry parity, or active resizing on the semantic Code Editor route.

## Non-goals

No modifications to existing applications, host routing, MCEL renderer, compiler, or CSS. No unchecked migration to the smaller canonical intent set. No assumption that successful compilation, matching names, or static evidence establishes live behavioral or visual parity.

## Recommended acceptance progression

1. Specify each canonical ↔ incumbent operation's arguments, authority, risk, results and receipts.
2. Preserve all 31 additional incumbent intents or document an explicit conscious decision for every omission.
3. Introduce a paired canonical application only as a separate candidate, using existing layout and renderer components.
4. Certify browser behavior, file editing/dirty state, conflict refusal, Aider, SCM, resize and VS Code semantic workbench geometry.
5. Promote only with an explicit downstream acceptance gate.
