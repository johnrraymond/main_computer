# MCEL Promise Gap Frob — Patch 4

## Objective

The objective is **not to obtain another green internal check**. It is to measure the distance between a small, independent application specification and a correct, attractive application constructed using only MCEL's public authoring interfaces, and to expose the library capabilities that would remove recurring authoring work.

The promises are: minimal authoring, attractive first render, sound design flexibility, and correctness by composition. One passing compiler or visual geometry check is not proof of these promises.

The Frob deliberately returns `MCEL_PROMISE_GAP_FROB_INCOMPLETE` until all the independent criteria have proof. Its default process exit of zero means that the **diagnostic completed**, not that MCEL is certified. `--require-promise` returns nonzero if the promise is not demonstrated.

## Run it

From the repository root, with Node and Python on PATH:

```text
python tools/mcel_promise_gap_frob.py
```

Optional diagnostic-only mode without a browser:

```text
python tools/mcel_promise_gap_frob.py --no-browser
```

Run tests:

```text
python -m pytest -q tests/test_mcel_promise_gap_frob.py
```

Default output directory: `runtime/reports/mcel-promise-gap/`. Read `barriers.md` first and `report.json` for every observation, diagnostic, source hash, and caveat. With Chromium/Playwright available, the Frob also captures scaffold screenshots at 320, 390, 560, and 1100 pixels. These images are tagged **scaffold only**, because the current generated application is a generic Counter and not an Inventory or Work Orders application.

A user may optionally supply authored candidates and *independently running* local web endpoints:

```text
python tools/mcel_promise_gap_frob.py --candidate inventory-c2=mcel_apps/promise-inventory/application.js --browser-url inventory-c2=http://127.0.0.1:8765/applications/promise-inventory
```

Repeat `--candidate` and `--browser-url` for `work-orders-independent`. The Frob only permits browser observation of explicitly provided localhost/loopback URLs; it does not launch or modify live applications. A successfully compiled candidate is **not** considered a successfully functioning app. Live screenshots and geometry alone are **not** treated as transition, authorization, effect, or aesthetics proof. Additional independent operational evidence remains an open requirement.

## Independent task packet

The fixed task packet, `tools/fixtures/mcel_promise_gap/tasks.v1.json`, is SHA-256-pinned by the runner so a trial cannot silently soften its requirements. Any intentional task change requires an explicit packet revision and new digest. The packet contains two structurally different application problems:

- **Inventory C2**: keyed items, add/update/remove, validation and refusal, row identity, search/sort, per-instance isolation, responsive data/action access, and attributable effects. Its permitted designs include compact operations and an open editorial variant.
- **Work Orders (independent)**: keyed work-order board, governed status transitions, assignment validation, owner/due-date views, keyboard interaction, responsive grouping, and attributable effects. Its competing designs are editorial board and dense operations.

Every case declares negative cases as well as desired successes. Task packets are not application-specific scaffolding templates.

## Diagnostic method and epistemic boundaries

1. Exercise the public `mcel.dsl.v1` compiler with a valid host-bound baseline and invalid orphan action (controls). Both must behave as expected for the harness to be healthy.
2. Exercise declarative keyed lists, composite record schemas, validated forms, and keyed collection surfaces. Distinguish `GAP_OBSERVED` (specific absent public helper), `BLOCKED` (another error), and `AVAILABLE_UNPROVEN` (compiler accepted but application behavior unproven). The exact compiler diagnostic and probe source hash are retained.
3. Generate each task's standard scaffold only in a disposable temporary directory, validate its package, and optionally observe its first-render geometry. A generic starter package is never counted as a completed task.
4. If real application candidates are supplied, compile them without staging or promoting them, inspect application identity, and count authored source. If local URLs are supplied, record browser geometry/screenshots without treating those observations as behavioral proof.
5. Record the unsatisfied obligations in seven ordered families: composition, functional behavior, effect/authority proof, general layout, default aesthetics, flexibility, and authoring economy. Preserve `UNVERIFIED` distinct from `FAIL` and `GAP_OBSERVED`.
6. Write `report.json` and `barriers.md`. The outcome is **INCOMPLETE** unless there is independently verified full application evidence; this version does **not** offer an automatic full-promise certification path.

A missing helper in this DSL proves a gap in **that public declarative path**, not the absence of every equivalent capability in MCEL. Host-bound, low-level IR and special application paths may already support it at greater authoring cost. The Frob must not pretend that those paths are identical.

## Initial actionable finding

The tested public DSL's general declaration builder does not currently expose all of `field.list`, `field.record`, `surface.form`, and `surface.collection` in the signatures exercised by the task probes. The restricted construction runtime reports `MCEL_DSL_EVALUATION_FAILED` with a missing-function diagnostic. This is the first **observed** barrier to straight-line declarative Inventory authoring. The required repair is not to add a tailored Inventory compiler: supply reusable governed collection/schema/form/surface semantics through the public DSL and canonical IR, with tests for validation, stable identities, and row-bound action authority. Re-run the four compiler probes, then execute independent real application transitions.

The other six barrier families remain unverified, rather than automatically failing. They require real, independently observed application behavior, governed write/effect receipts, general layout across different domain structures, independent human first-render review, contrasting designs through identical public contracts, and comparable correct-authoring effort against an existing path.

## Safety and evidence authority

- No Website Builder source or runtime modifications.
- No live application launch, promotion, deployment, server-side changes, or production state mutations.
- Candidate compilation uses `write_candidate=False`.
- Scaffolds are disposable; report output is confined to the chosen report directory.
- The canonical MCEL promise coverage report and existing app statuses are **not** replaced.
- Compiler acceptance, scaffolding success, synthetic receipts, and geometry cannot be promoted into end-to-end proof.

The next repair must be driven by the gap evidence and must retire a general impediment, not merely make this diagnostic print `PASS`.
