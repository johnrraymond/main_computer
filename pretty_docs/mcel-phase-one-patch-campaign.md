# MCEL Phase One — Executable Patch Campaign

## Promise and boundary

One authoritative MCEL application declaration must compile into a validated
Application IR, generate deterministic projections, execute through the real
application host, and pass **fresh independent acceptance and browser proof**.
No repair search, automatic source edits, or automatic promotions are in scope.

Passing compiler tests is not application proof. The existing `mcel_app_prove`
authority, not this document or the Phase One wrapper, determines the final
semantic-runtime status.

## Patch sequence

Apply the Phase One patches in the supplied order with `new_patch.py`.
Each zip contains only complete replacement or newly created repo files,
under `main_computer_test/`.

### Patch 01 — Establish the projection inventory invariant

The Calculator projector emits eight explicit contract artifacts plus the
canonical normalized IR file. Several independent checks held stale inventories
(the nine-file projector, browser virtual assets, and generated-adapter parity)
and the fluent DSL test imposed an obsolete 250-line limit. This patch restores
agreement around the existing projector output without weakening the
projection or runtime parity authorities.

**Invariant:** The Calculator's generated artifact names and hashes must match
an explicit nine-file inventory across projection, virtual assets, and parity;
two projections from unchanged IR must produce identical bytes and hashes.
The fluent authoring test must guard against handwritten builders rather than
limiting the number of semantic declarations.

Acceptance gate:

```powershell
python -m pytest mcel_apps/calculator/tests/test_acceptance.py `
  tests/test_mcel_calculator_shadow_dsl.py `
  tests/test_mcel_calculator_host_bound_runtime.py `
  tests/test_mcel_calculator_parity_evidence.py -q
```

### Patch 02 — Establish the Phase One end-to-end gate

Adds a read-only orchestrator around the *existing* compiler, projector, and
proof authorities. It makes no claims from generated code alone, checks the
compiler/projector semantic fingerprint, requires a DSL-authoritative source,
and always uses fresh acceptance/browser evidence (no `--reuse-evidence`).
It adds no new proof rules and does not alter promotion permissions.

**Invariant:** `status: pass` is possible only if compilation and projection
agree on the semantic source and the independent application proof returns
`semantic-runtime-proven` from fresh execution. Partial progress must fail
closed, retaining the stage that blocked it.

Acceptance gates:

```powershell
python -m pytest tests/test_mcel_phase1_gate.py -q
python tools/mcel_phase1_gate.py --app calculator --json
```

`mcel_phase1_gate.py` returns 0 only on fully proven status, and returns 1 for
any failure, including missing browser dependencies or inaccessible localhost.
Do not relabel environmental failures as successful proof.

### Next patch after real-host evidence — close the remaining proven defect

If the second command fails, inspect its `failedStage` and error. Patch *only*
the failing authority or its contract after reproducing the failure on the
Windows workstation. Rerun both the affected stage's targeted tests and the
entire Phase One gate. Do not implement guessed browser/network remedies from
the failure observed in the sandbox.

## Execution from `C:\Users\subsi\main_computer`

After placing the downloaded zip patches at accessible paths:

```powershell
python new_patch.py "C:\path\mcel_phase1_patch_01.zip" --dry-run
python new_patch.py "C:\path\mcel_phase1_patch_01.zip"
python -m pytest mcel_apps/calculator/tests/test_acceptance.py -q

python new_patch.py "C:\path\mcel_phase1_patch_02.zip" --dry-run
python new_patch.py "C:\path\mcel_phase1_patch_02.zip"
python -m pytest tests/test_mcel_phase1_gate.py tests/test_mcel_app_authoring_pipeline.py -q
python tools/mcel_phase1_gate.py --app calculator --json
```

On a browser-capable workstation, the last command should execute real fresh
Chromium observation, not reuse pre-existing reports. Browser prerequisites are
described by the repository's current Playwright installation path. If Chromium
cannot run, the correct result is `fail` at `prove`, not `pass`.

## End-state acceptance / exit criterion

Do not call Phase One complete until ALL conditions hold on the authoritative
Windows checkout:

1. Calculator DSL compiles and projects deterministically without changing live
   application files.
2. The full package-local acceptance runner passes, with nonzero test counts and
   no blocking stale projection expectations.
3. Fresh browser observation executes the real host and passes its contract.
4. The app proof checks matching package fingerprint and repository provenance,
   IR-native intent coverage, surface conformance, and independent acceptance.
5. `python tools/mcel_phase1_gate.py --app calculator --json` returns exit code
   zero, `status: pass`, and `truthStatus: semantic-runtime-proven` in the proof
   stage. A failure in any authority remains a nonzero exit code.

This is **Phase One for the reference application**, not evidence of generic
support across application families. Making new applications compiler-profile
independent belongs to Phase Two.
