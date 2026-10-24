# MCEL Promise Verification Harness (additive v1)

## Why these promises are being developed

MCEL's primary goal is **not** application reverse engineering or producing a green harness. It is to help authors build good-looking, working web applications with little framework-specific work and broad design freedom because the parts themselves enforce sound composition. The four governing principles—minimal authoring effort, attractive defaults, maximum sound flexibility, correctness through composition—are in `pretty_docs/mcel-authoring-principles.md`.

This harness is a **measurement authority**, not the product. A passing bounded source/serialization/effect test is useful infrastructure, not proof that ordinary authors can achieve a polished web app without learning MCEL internals. The six still-unverified broader platform claims should direct work toward missing library capabilities, not toward weakening acceptance criteria. Unseen-app reconstruction is an optional migration use case rather than a primary authoring prerequisite. Existing manifest IDs and source-bound claim scopes remain unchanged.


The question is not whether MCEL has named laws, registered requirements, or passing structural tests. The question is whether each user-facing MCEL promise has independent, current, source-bound proof for the **exact bounded behavior** claimed.

The two current authorities remain distinct. `McelAppTruthGate` owns application truth; `McelLabEngine.runContractTests()` owns bounded engine contract outcomes. `buildAdoptionCase()` is structural planning evidence, not a measurement of replacement advantage. The harness only projects existing evidence against a versioned manifest. It cannot certify an application or alter adoption/production gates.

## Files and default inspection

- `contracts/mcel_promise_evidence.v1.json`: 14 user-space and platform claims, each with a bounded statement, non-guarantees, an evidence rule, and next missing experiment where applicable.
- `tools/promise_coverage/mcel_promises.py`: read-only coverage view and opt-in orchestration of existing proof commands.
- `tests/test_promise_coverage_mcel.py`: negative controls for missing/stale/mismatched evidence, malformed guarantee outcomes, and falsely green audit summary.

From repository root:

```text
python tools/promise_coverage/mcel_promises.py
python tools/promise_coverage/mcel_promises.py --json
python tools/promise_coverage/mcel_promises.py --require-all
```

**Default mode does not launch tests, create reports, modify source, compile packages, or deploy anything.** It invokes the existing truth audit with `--no-write --json` and reads the canonical engine probe if available. The truth audit determines which existing FLOG and acceptance evidence are canonical, source-bound, and fresh. The promise reporter cannot replace that decision.

## Opt-in local verification

Start the normal Main Computer web application at `http://127.0.0.1:8765` and ensure Chromium/Playwright and Node are available, then:

```text
python tools/promise_coverage/mcel_promises.py --run-canonical --output runtime/reports/mcel-promises/promise-coverage.json
```

The explicitly opted-in path invokes `McelLabEngine.runContractTests()` in Chromium, then invokes the existing **unscoped** FLOG runner, the existing **unscoped** acceptance runner with `--check`, and the repository `--release-gate` using its existing authority. The reporter subsequently obtains a fresh read-only truth audit. Each command's actual exit status is recorded as a separate stage. The release gate may refuse proof because the independent portable certificate is absent; the reporter does not bypass it. No deployments or promotion actions are invoked. These existing tests may still exercise application operation paths and create runtime evidence; run the opt-in mode only in an authorized test environment.

## Interpretation

A `verified` entry means exactly its bounded statement has admitted proof, not that every MCEL feature works. `failed` means a current authoritative result explicitly contradicts a bounded claim. `unverified` means proof is absent, stale, improperly scoped, or not yet implemented. `blocked` means an upstream authority could not be evaluated. The aggregate `incomplete` result is intentionally not a release verdict. `--require-all` exits nonzero unless every registered promise is independently verified.

The first six user clauses may be demonstrated by the engine's own browser-run contract tests. The seventh adoption clause, cross-app layout generality, effect reconciliation, unfamiliar app reconstruction, autonomous authoring, and development advantage retain **unverified** status until their independent experiments are implemented. A green audit in report-only mode is not accepted as promise verification. No green aggregate status is inferred from counts of laws, tests, apps, or plans.

The engine probe is stored in `runtime/reports/mcel-promises/engine-contract-probe.json` and includes a timestamp and the existing MCEL repository source fingerprint. The promise manifest, reporter, tests and documentation are **outside** the existing MCEL source-fingerprint selector, so installing this read-only observer does not invalidate current application evidence. The engine probe records a second, explicit SHA-256 of the versioned promise manifest, so changing the claim map invalidates prior engine-probe coverage without modifying the existing audit authority. Canonical acceptance and FLOG evidence remain managed by the existing producers and gate.

## Next experiments, in dependency order

After collecting the canonical evidence, test the named-app intent/effect receipts with forbidden-effect controls and exercise the same canonical layout authority on two contrasting apps at varied content lengths and viewports. Then execute an unfamiliar-app reconstruction trial. Finally, implement the already specified AI authoring/migration benchmark with semantic equivalence, repair, and effect gates before calculating cost or time advantage. Do not modify the existing compiler, browser proof, truth gate, or release gate merely to increase a score.
