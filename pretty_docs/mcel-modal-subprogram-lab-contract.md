# MCEL modal subprogram — Lab geometry/lifecycle fixture

## Purpose

The MCEL Lab now includes an independent modal-subprogram acid fixture. It is **not** an SEH application and does not call LMS, ORG-SLH, Coolify, or any deployment harness. A deterministic mock operation exercises the presentation lifecycle: immediate `SUBMITTING`, `ACCEPTED`, `RUNNING`, streaming stdout/stderr, `COMPLETED` or `FAILED`, optional safe cancellation, dismissal, and reconnection to the same mock operation state.

The reusable `McelSemanticModalSubprogram` module supplies modal definition, validation, geometry allocation, region mounting, and presentation lifecycle. The Lab-only `McelModalSubprogramLab` module supplies mock operation state and content. This division is intentional: dismissing a modal does not cancel or reset its observed operation.

## Semantic geometry contract

The modal requests a preferred width and height, a **hard minimum** width and height, and **soft maximum requested percentages** of the viewport width and height. If the hard minimum exceeds available space, the allocation honors the minimum and the viewport provides scrolling/panning of the whole modal. The minimum is not silently weakened by the percentage request.

Each region declares a semantic ID, budget type, and overflow behavior:

- `header`: fixed 104px, wrapping content within budget.
- `details`: fixed 118px, non-growing content.
- `output`: single flexible budget, at least 142px, scroll owner for growing mock output.
- `actions`: fixed 74px, persistent controls.

All allocations are validated before the modal opens. The sum of minimum budgets must fit within the modal's hard minimum height. The canonical modal commits width, height, and **all region heights** before becoming visible. Subsequent status and log updates **never** request or trigger new geometry. Only reopening or an external viewport-size/zoom event may compute a new allocation. Dynamic content is subordinate to the committed budgets, not an excuse to grow the header or footer.

The implementation intentionally uses a single flex-budget region for this fixture; it does not claim to be a complete general layout solver for every possible modal composition. A later generalization can extend permitted region relationships without changing the lifecycle boundary or treating content growth as implicit resize authority.

## Reproduce in MCEL Lab

In the **Theme, Form & Chrome** workspace, find **Modal subprogram · lifecycle / geometry acid test**. Run mock completion or mock refusal. The modal opens *before* the mock accepts work; the mock then emits timed state transitions. Add 180 output lines to stress overflow. Dismiss the modal before completion, then use **Reopen / reconnect** to recover the same mock operation and output without restarting it. Cancel operation is enabled only during its safe mock stages. **Reset mock state** clears the simulated service (not just the modal).

Expand **Inspect modal semantic contract and committed geometry** to see the source definition and the actual allocated budgets. Resize or zoom the browser to exercise a new geometry commit, including an undersized viewport where hard minimums exceed available presentation space.

## Oracle/acceptance

`tests/test_mcel_modal_subprogram_lab.py` verifies the registered canonical element, script inclusion, hard versus soft constraints, unsatisfiable budget rejection, browser geometry stability during 180 appended mock lines, single output-region scrolling, dismissal without cancellation, same-operation reconnection after completion, mock preflight refusal, and viewport-oversize presentation. The SLH LMS operation monitor is now migrated to the same `McelSemanticModalSubprogram` primitive. The Lab remains independent and mock-only. `tests/test_mcel_slh_canonical_modal_integration.py` verifies the real SLH adapter’s modal lifecycle, committed region geometry under long stdout/stderr output, one scrollable output region, independent dismissal/reopening, and viewport-scoped oversize presentation.

## SLH production adapter

`mcel_slh_application.py` loads the canonical modal runtime and stylesheet before the SLH management adapter. `mcel-semantic-slh-management-modal.js` provides a semantic definition, not CSS width/height calculations. Its contract requests a preferred 680 × 590 px, a hard 440 × 460 px minimum, and soft maximum requests of 88% width and 86% height of the **visible management workspace**. Fixed header (116 px), details (142 px), and actions (70 px) budgets surround one flexible, scroll-owning output region (minimum 132 px). This is an app-specific *declaration*, not app-owned geometry computation.

The canonical MCEL runtime determines and commits the region budgets on opening. Incoming progress, status updates, and stdout/stderr never recompute them. Browser viewport changes and changes to the visible management viewport are explicit geometry events; if the hard minimum exceeds that viewport, the viewport permits scrolling rather than breaking the modal’s budgets. The decorative frame border is painted without consuming any budget.

The SLH adapter still handles synchronous click-to-`SUBMITTING` handoff, polling LMS operation snapshots, stream-labeled stdout/stderr, safe cancellation requests, and persistent result evidence. The MCEL modal runtime owns mounting, positioning, focus, Escape, dismissal, viewport geometry, and scroll containment. On `BUSY` the underlying management workspace is inert; on an incomplete/status-unavailable notice it is not. Target navigation remains outside the management workspace and accessible. Dismissing the modal does not cancel LMS work or clear its durable operation receipt. Both local and authenticated Network SLH use this adapter and the same canonical geometry contract.

The production SLH adapter does not load the Lab mock or send mock requests. No LMS, ORG-SLH, harness, Mother, or Coolify protocol was changed as part of the presentation migration.
