# MCEL Code Editor: independent host-bound IR-native intent proof

The Code Editor proof is registered with the generic `mcel_app_ir_native_proof` authority. It binds the live compiled Code Editor DSL, generated package and runtime projection, the real generated adapter mounted in Chromium, and actual source effects in a **disposable** `ViewportServer` workspace. It does not write to the application repository.

## Evidence

- The current DSL declares eight intents. Their runtime-method bindings and effect ownership are validated against the normalized application IR.
- The browser runs the real Code Editor core, view model, capabilities, host HTML/CSS, canonical layout runtime, and generated ESM adapter. Each intent is invoked through the generic host-bound mount.
- Source inspection and file opening call the isolated real editor service. Draft changes remain local.
- Explicit save writes `src/proof.js`; independent Python file reads and server transaction receipt hashes establish its exact bytes.
- A concurrent external modification invalidates the stale draft source hash. The second save is denied and the externally changed bytes remain intact.
- Discard and close perform only local-state operations.
- Read-only patch preparation returns a server-issued transaction handle without changing source bytes. Apply without approval is refused.
- Approved reviewed application uses the real transaction endpoint and its returned write receipt; independent disk bytes are checked again.
- All 13 compiler-declared scenario bindings are covered by the corresponding observable intent, including their named edit, save, preview, and reviewed-apply examples.
- Missing Chromium, missing generated authority, incomplete effects, mismatched receipts or source changes cause the proof to refuse.

**Scope:** `host-bound-ir-native-authoritative`, paired with separately certified canonical layouts. This is **not** `fullSemanticRuntimeProven`, a standalone package-host truth gate, or authorization to promote Code Editor source/HTML from a new compiler. The application profile deliberately leaves promotion unsupported.

## Commands

```powershell
python -m pytest tests/test_mcel_code_editor_ir_native_effect_proof.py -q
python tools/mcel_phase1_host_bound_prove.py --app code-editor --check --json
python tools/mcel_layout_certify.py --app code-editor --with-intent-proof --json
```

The proof always creates a new temporary workspace. A saved JSON result must never substitute for a fresh browser observation or current package-scoped acceptance. The source-only export used for patch transport may omit unrelated game web assets; the real repository must contain its regular game bundle files for the viewport server import.
