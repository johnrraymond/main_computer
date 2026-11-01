# Phase 2B — Semantic operational browser runtime (implementation evidence)

This is an opt-in implementation increment of the normative MCEL V1 contract,
not evidence that V1 or Phase 2 is complete.

## Shipped

- `mcel-semantic-operational-runtime.js` consumes the **existing canonical**
  `mcel.application-ir.v1` produced from paired `application.html` and
  `application.js`. The runtime does not parse an additional authoring language.
- Preflight rejects missing hierarchy, invalid member bindings, unknown intent,
  forged risk/effect/capability connections, duplicate regions, and cycles before
  modifying the host element. The canonical Python validator remains authoritative
  at compilation; browser preflight is defense in depth, not a replacement.
- The runtime builds native HTML elements from `interfaceIntent` and `surface.nodes`
  without authored layout markup: forms, controls, collections, state values, and
  operation receipts. It retains logical region hierarchy and author order.
- It handles typed form inputs, enum view changes, validated dynamic selection
  against current keyed collections, typed capability requests/result bindings,
  rejection receipts, and mounting/unmounting. Derived state changes only on
  completed, type-correct authorized responses; stale results are ignored after
  unmount or same-intent supersession.
- Capability execution is **deny-by-default** without a host-installed port.
  `mount(root, ir, {capabilities:{authorize, invoke}})` is the only dispatch path.
  This port is a host integration seam; the embedding host must authenticate and
  authorize requests against its own actual permissions, not trust IR claims.
  IR declarations do not confer runtime authority.
- `mcel-calculator-core-capability-port.js` demonstrates this seam using the
  existing `calculator-core.js` implementation of pure arithmetic. The adapter
  authorizes the exact `arithmetic.compute / evaluateExpression` method and
  validates output; no `eval` and no external privilege are involved.
- Browser tests use real Chromium with actual clicks. Calculator computes
  `2+3*4 == 14` using the existing core, updates visible state, emits a
  `completed` receipt, and denies invalid or unsupported results. A File Manager
  probe loads a typed list, selects a valid row and reads a preview with a
  *controlled test adapter*, never a live filesystem authorization.
- The same shared runtime mounts all four semantic Phase 2 reference probes.
  These probes are **not** the four production applications.

## Explicitly not shipped

- **No promotion** of existing File Manager, Task Manager, Code Editor or
  Calculator production mounts or routes. Those applications remain unchanged.
- No backend registry hookup for filesystem, process management, or source writes.
  A test adapter is not evidence of real backend authorization.
- No general expert layout synthesis or responsive design acceptance yet.
  Native HTML hierarchy renders with browser default layout; Phase 3 owns
  geometry and presentation expertise.
- No shared runtime support yet for all app operation kinds, canonical writes,
  live subscriptions, document lifecycle, cancellation/AbortSignal, structured
  cross-intent supersession, or complete collection update/focus continuity.
- `completed` receipts here are **client execution receipts** only; a production
  host must supply real backend receipts/evidence and enforce source-side authority.

## Run

```powershell
python -m pytest -q tests/test_mcel_phase2b_semantic_runtime.py
python -m tools.mcel_phase2b_semantic_runtime_frob
```

The Frob writes `runtime/reports/mcel-phase2b/report.json` and reports only
`PASS_BROWSER_VERTICAL_SLICE_ONLY`, never four-app parity or V1 completion.

## Browser selection on Windows, macOS, and Linux

The Phase 2B Frob and browser tests use the same actual Playwright browser
launcher. A Unix `chromium` command on `PATH` is **not** required. The launcher
attempts Playwright-managed Chromium, then locally installed Microsoft Edge and
Google Chrome through their Playwright channels, followed by available PATH
executables. Each attempt must successfully launch a browser, not merely detect
a file or command name. A reported PASS requires executed, non-skipped tests.

If no browser launches, run the following within the same activated virtual
environment as the Frob:

```powershell
python -m playwright install chromium
python -m tools.mcel_phase2b_semantic_runtime_frob
```

Alternatively, set `MCEL_PHASE2B_BROWSER_EXECUTABLE` to an existing full browser
executable path (for example, the locally installed Chrome/Edge executable).
That override is strict: a bad explicit path produces an error instead of a
silent fallback. The legacy `MCEL_CHROMIUM_EXECUTABLE` override is also honored.
The Frob records `browserBackend` and `browserDiagnostic` in
`runtime/reports/mcel-phase2b/report.json` for investigation.

This fixes browser-test discovery only. It does not change MCEL capability
authority, production mounts, four-app parity, or expert layout acceptance.
