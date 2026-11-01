# MCEL Phase 2C — hosted semantic Calculator candidate

## Scope and evidence

This patch adds a first-class **opt-in** viewport GET route at
`/applications/mcel-semantic-calculator-candidate`. This route does not replace
`/applications/calculator`, does not alter the four existing application
sources, and is not an MCEL V1 completion claim.

The route compiles the **existing paired** semantic Calculator fixture
`tools/fixtures/mcel_phase2_reference_apps/calculator/application.js` and its
`application.html` through `compile_dsl_application`. It rejects unknown app
identity, unexpected intents, broadened authority, missing hierarchy or a
hand-authored layout tree. The browser receives the compiler's canonical IR,
the actual `calculator-core.js`, the shared semantic operational runtime, and
an explicitly authorized Calculator-only capability port. No separately
hand-authored Calculator UI or operation handler is used.

The resulting semantic Calculator can accept an expression, evaluate using the
same local arithmetic core as the existing production Calculator, publish
validated results and receipts, and refuse invalid expressions. The candidate
supports **basic arithmetic only**. It is not functional parity with the real
Calculator's graphing, Mathics or other supported operations.

## Testing

```powershell
python -m tools.mcel_phase2c_hosted_candidate_frob
# To verify against a running viewport server, including actual HTTP routing:
python -m tools.mcel_phase2c_hosted_candidate_frob --hosted-url http://127.0.0.1:8765/applications/mcel-semantic-calculator-candidate
```

The local Frob verifies paired compilation, static authority scoping, HTTP
content delivery and real Chromium interaction with the exact generated page.
These are different from a browser navigating the running viewport route.
Only the explicit `--hosted-url` form, with a successful browser navigation and
operation, earns `PASS_HOSTED_CANDIDATE_ONLY`.

## Remaining V1 obligations

- Convert the full **production** Calculator source to paired HTML/JavaScript
  without dropping graphing or external operations, then compare behavior.
- Establish governed backend adapters and preservation tests for File Manager,
  Task Manager and Code Editor, including consequential/source-write effects.
- Add cancellation, stable dynamic collection reconciliation, complete state
  and editing lifecycle and source-backed external receipts where applicable.
- Perform expert semantic interface synthesis, responsive tests and design
  acceptance; a simple native DOM tree is not yet an expertly designed UI.
- Preserve old routes and controls until separately certified, with rollback.

No `_v1` vocabulary pins are added to application sources.
