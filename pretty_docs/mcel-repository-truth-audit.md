# MCEL repository truth audit and CI gate

## Purpose

`main_computer/mcel_truth_audit.py` turns the browser-side MCEL app truth
snapshot into a repository report and an opt-in CI gate.

The audit does **not** create another requirements, adapter, or surface truth
system. It loads the existing authorities under Node:

- `McelRequirementsRegistry`
- `McelDomainAdapterRegistry`
- `McelAppSurfaceRegistry`
- `McelAppTruthGate`

It then supplies optional FLOG/runtime and acceptance evidence and wraps the
canonical `mcel-app-truth-snapshot-v1` result in a deterministic repository
report.


## Acceptance evidence runner

Generate repository-bound acceptance evidence before the release gate:

```bash
python main_computer/mcel_acceptance_runner.py
```

The runner writes:

```text
runtime/reports/mcel-acceptance/mcel-acceptance-report.json
runtime/reports/mcel-acceptance/mcel-acceptance-report.md
```

It discovers `mcel-acceptance` declarations from the requirements registry and
executes only explicit mappings from
`main_computer/mcel_acceptance_bindings.json`. Planned/draft/open contracts
remain visible as `not-due`; enforceable contracts cannot pass without
collected, passing pytest evidence.

The accepted report schema is:

```text
mcel-acceptance-evidence-report-v1
```

See `pretty_docs/mcel-acceptance-evidence.md` for binding and status rules.

The complete release sequence is:

```bash
python main_computer/flog_mcel_runtime_smoke.py
python main_computer/mcel_acceptance_runner.py
python main_computer/mcel_truth_audit.py --release-gate
```

## Commands

Generate JSON and Markdown reports without failing on app gaps:

```bash
python main_computer/mcel_truth_audit.py
```

Use the default FLOG report when it exists:

```text
runtime/reports/flog/mcel-runtime/mcel-runtime-flog-report.json
```

Supply evidence explicitly:

```bash
python main_computer/mcel_truth_audit.py \
  --runtime-evidence runtime/reports/flog/mcel-runtime/mcel-runtime-flog-report.json \
  --acceptance-evidence runtime/reports/mcel-acceptance/mcel-acceptance-report.json
```

Enable the default CI gate:

```bash
python main_computer/mcel_truth_audit.py --check
```

Require fresh runtime proof and declared acceptance proof as additional CI
policies:

```bash
python main_computer/mcel_truth_audit.py \
  --check \
  --require-fresh-runtime \
  --require-acceptance
```


Run the release-grade gate against the canonical report paths:

```bash
python main_computer/mcel_truth_audit.py --release-gate
```

`--release-gate` is shorthand for:

```text
--check
--require-fresh-runtime
--require-acceptance
--require-repo-match
```

It does not select a newer app-scoped report over the canonical all-app
artifacts. Use `--latest-runtime-evidence` or `--latest-acceptance-evidence`
only when canonical discovery outside the conventional paths is intentional.
Declared partial reports are excluded from latest-report discovery.

Reports are written to:

```text
runtime/reports/mcel-truth-audit/mcel-repository-truth-audit.json
runtime/reports/mcel-truth-audit/mcel-repository-truth-audit.md
```

Use `--no-write --json` for machine consumption without creating files.

## Evidence discovery and repository binding

Runtime evidence can be selected from the newest schema-valid canonical FLOG
report:

```bash
python main_computer/mcel_truth_audit.py --latest-runtime-evidence
```

Acceptance evidence can be selected from the newest schema-valid canonical
acceptance report:

```bash
python main_computer/mcel_truth_audit.py --latest-acceptance-evidence
```

Reports declaring `mcel-evidence-scope-v1` with `canonical: false` are skipped
during this discovery. They remain usable as explicit diagnostic inputs, but
they cannot satisfy the repository release gate.

Explicit evidence paths and their corresponding `--latest-*` options are
mutually exclusive.

FLOG and acceptance reports carry a versioned **MCEL authority-source**
repository fingerprint using:

```text
mcel-repository-provenance-v3
sha256-source-path-content-v2
```

The prior `v2` selector hashed every Git-tracked and non-ignored untracked
file in the monorepo. This caused unrelated Mother and NanoJev changes to
invalidate Calculator and Code Editor proofs. In `v3`, both Git and snapshot
modes apply the same conservative MCEL authority-source selector:

- the shared `main_computer/` host and verifier implementation;
- generated app sources under `mcel_apps/` and the local platform host;
- versioned `runtime/websites/` sources and the Code Editor browser fixture;
- MCEL, FLOG, and Code Editor tools and regression tests, reviewed-patch code,
  MCEL specifications, and relevant root configurations.

Unrelated root Mother mutation code, `tools/mother/`, Mother-only tests, and
NanoJev documentation are **not** certified application source and do not
invalidate an MCEL certificate. The strict package generator still binds
all generated asset bytes, host projection fingerprints, semantic fingerprints,
and the full declared intent set independently. An MCEL authority-source edit
or newly introduced matching file changes the v3 source fingerprint.

The `scope` field is `git-tracked-and-unignored-mcel-authority-v3` or
`snapshot-mcel-authority-v3`; `selectionMethod` remains
`git-tracked-and-unignored` or `snapshot-source-roots`. Snapshot fallback
records candidate source roots scanned (filtered by the same authority selector)
as `sourceRoots`.

Mutable and generated runtime reports, caches, profiles, and temporary output
remain excluded. Earlier v2 attestations are **not** silently grandfathered
in: the verifier refuses an unsupported source schema. Regenerate runtime,
acceptance, and live provider evidence **once** after installing this change.
Keep all genuine MCEL authority sources stable while certifying.

Evidence binding is reported as:

- `exact`: produced from the current repository source state;
- `mismatch`: produced from a different source state;
- `unbound`: no repository provenance was declared;
- `unsupported`: provenance uses an unknown schema or algorithm;
- `absent`: no evidence report was selected.

The audit also reports an evidence-input status separately from fingerprint
binding:

- `canonical-exact`: canonical evidence is present and exactly bound;
- `missing-canonical`: the conventional canonical report is absent;
- `partial`: the selected report is app/scenario scoped;
- `stale`: runtime evidence exceeds the configured age window;
- `fingerprint-mismatch`: provenance belongs to another source state.

These statuses prevent a partial run from being mistaken for repository-wide
proof or from being diagnosed merely as unrelated apps losing conformance.

Explicitly mismatched or unsupported evidence is never forwarded to
`McelAppTruthGate`, so it cannot prove runtime or acceptance truth. A mismatch
is an audit-integrity failure. Use `--require-repo-match` to make unbound legacy
evidence fail the check as well.

Acceptance evidence should copy the current top-level
`repositoryProvenance` object into its own report envelope. The current object
is available in both the FLOG report and the repository truth-audit JSON.

## Node runtime discovery

The audit executes the canonical browser-side truth authorities under Node.
It resolves the executable in this order:

1. the explicit `--node` value, when supplied;
2. a system `node` available on `PATH`;
3. the Playwright-bundled Node runtime from the installed Python package.

This means a normal Playwright-backed virtual environment can run the audit
from a fresh PowerShell session without manually adding Playwright's driver
directory to `PATH`:

```powershell
python main_computer/mcel_truth_audit.py --check
```

Use an explicit executable only when a particular runtime must be selected:

```powershell
python main_computer/mcel_truth_audit.py --check --node C:\tools\nodejs\node.exe
```

An invalid explicit `--node` value is an execution error; the audit does not
silently ignore an operator-selected runtime and fall back to another one.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The audit completed; report-only mode always uses this when execution is trustworthy, and check mode found no blocking reasons. |
| `1` | `--check` found a blocking declared-policy violation or an enabled required-evidence gap. |
| `2` | The audit could not execute reliably, for example because Node is unavailable, an explicit evidence file is malformed, or a core authority cannot load. |

## Default enforcement

Default `--check` fails only on:

1. Findings produced by `McelAppTruthGate` with `blocking: true`.
2. Audit integrity failures, such as a statically discovered domain adapter
   failing to load.

Examples of truth-gate blocking findings include:

- `requirements-schema-invalid`
- `requirements-contract-incomplete`
- `runtime-diagnosis-incomplete`
- `surface-policy-failed`
- `acceptance-test-failed`
- `semantic-readiness-overclaimed`

The following remain visible but non-blocking by default:

- legacy or unenrolled apps;
- missing domain adapters;
- missing runtime evidence;
- stale runtime evidence;
- missing acceptance evidence;
- partial implementation.

This preserves the rule that CI only fails when an app violates the level of
conformance it has declared. An incomplete legacy app is not treated as a
broken enrolled app.

`--require-fresh-runtime` and `--require-acceptance` deliberately promote
evidence gaps into audit-policy failures. App-level proof gaps retain their
existing codes, while repository-level evidence selection uses distinct codes:

- `audit-required-runtime-proof-missing`
- `audit-required-acceptance-proof-missing`
- `audit-canonical-runtime-evidence-missing`
- `audit-canonical-acceptance-evidence-missing`
- `audit-runtime-evidence-partial-scope`
- `audit-acceptance-evidence-partial-scope`
- `audit-runtime-evidence-stale`
- `audit-evidence-repository-mismatch`

`audit-required-acceptance-proof-missing` is emitted only when the app has no
repository-bound acceptance evidence. If evidence is present and fails, the
truth gate's `acceptance-test-failed` finding is the single blocking reason;
the audit does not also mislabel the same failure as missing proof.

## Report schema

The repository envelope uses:

```text
mcel-repository-truth-audit-v1
```

Important sections:

- `configuration`: check mode, release-gate mode, and enabled evidence policies;
- `repositoryProvenance`: the exact current source fingerprint;
- `evidenceInputs`: selection mode, path, schema, timestamp, SHA-256, provenance, and repository-binding status;
- `authorities`: whether the four canonical truth authorities loaded;
- `sourceInventory`: repository-relative paths and hashes for authority and
  domain-adapter sources;
- `loaderDiagnostics`: deterministic adapter loading evidence;
- `summary`: truth-status, maturity, blocking, and promotion counts;
- `apps`: compact per-app enforcement and promotion summaries;
- `truthSnapshot`: the unchanged canonical `McelAppTruthGate` snapshot.

The audit does not rewrite truth-gate findings. Per-app enforcement reasons
retain `source: mcel-app-truth-gate` when they come from the authority.

## Declared maturity levels

The audit normalizes surface-registry maturity into three promotion levels:

| Registry maturity | Audit level |
|---|---|
| `legacy` or unenrolled | `legacy` |
| `runtime-baseline` | `runtime-baseline` |
| `host-workbench` | `runtime-baseline` |
| `semantic-runtime` | `semantic-runtime` |

The raw registry maturity remains in every app summary.

## Promotion rules

Promotion output is advisory. It never edits the app-surface registry.

### Legacy → runtime baseline

An app is ready for review when all of these are true:

- requirements are present, strict-schema valid, and complete;
- a registered adapter proves runtime-core readiness;
- fresh runtime evidence is present;
- runtime diagnosis completed;
- the runtime scenario passed;
- an app-surface policy is registered;
- no truth-gate blocking finding exists.

The app must still be explicitly enrolled and assigned required runtime layers
in the registry during the promotion patch.

### Runtime baseline → semantic runtime

An app is ready for review when all of these are true:

- requirements are fully specified;
- the domain adapter proves full-application semantic readiness;
- the declared app-surface runtime policy is freshly proven;
- declared acceptance evidence passes;
- no truth-gate blocking finding exists.

### Semantic runtime

A semantic-runtime app has no automatic next level. The audit reports whether
its current full semantic proof is complete. A surface-only pass is not enough.

## Freshness

The default runtime-evidence freshness window is seven days (`168` hours).
Override it with:

```bash
python main_computer/mcel_truth_audit.py --max-evidence-age-hours 24
```

Evidence without a parseable timestamp cannot count as fresh.

## CI example

A conservative repository gate that fails only on declared violations:

```bash
python main_computer/mcel_truth_audit.py --check
```

A release gate that discovers the latest evidence, requires freshness and
acceptance, and binds both reports to the exact repository source state:

```bash
python main_computer/mcel_truth_audit.py --release-gate
```

Override the default seven-day freshness window when needed:

```bash
python main_computer/mcel_truth_audit.py \
  --release-gate \
  --max-evidence-age-hours 24
```

The JSON and Markdown reports should be retained as CI artifacts so a failed
gate remains explainable.

## Patch 19: source-bound portable package promotion

The canonical 19-intent evidence is produced by the *real live* standalone
Chromium/Ollama/Mathics proof, not by a model stub or a copied pass verdict.
The audit separately evaluates that evidence before granting **portable package
promotion eligibility** for `calculator` and `code-editor`. It does not silently
upgrade either application's declared maturity, rewrite a package manifest, or
switch its production presentation or capability authority.

From the repository root, after installing Patch 19, run:

```powershell
python -m pytest tests/test_mcel_portable_repository_certification.py -q
python tools/mcel_portable_intent_probe.py `
  --authorize-external `
  --model gemma4:26b-a4b-it-q4_K_M `
  --external-timeout-s 90 `
  --output .\runtime\state\mcel\port-proof-live-v4.json `
  --json > ..\archive\live-probe-v4.json
python tools/mcel_portable_certify.py `
  --proof .\runtime\state\mcel\port-proof-live-v4.json `
  --json
python main_computer/mcel_truth_audit.py --require-portable-certification --check
```

The certifier writes the canonical UTF-8 evidence at
`runtime/reports/mcel-portable/mcel-portable-certification.json`.
`--release-gate` also requires this independently verified portable certificate,
in addition to its existing canonical runtime, acceptance, freshness, and
repository matching policies. It is not sufficient for a proof to say
`passed=true`: the verifier regenerates the exact standalone packages, checks
all asset digests, presentation manifests, current repository provenance,
receipt hashes, bounded semantic fixtures, negative authorization checks and
byte-accurate filesystem effects. The verifier re-runs on every truth audit.

The proof and certificate are **local, hash-bound evidence**, not remotely signed
attestations. Source provenance and file hashes detect changes against the current
checkout; they do not authenticate an adversary with permission to rewrite both
source and evidence. A new source change invalidates the old proof. The older
`live-probe-v3.json` passes the earlier bounded fixture but **does not** contain
Patch 19 source bindings, so it cannot be imported as a promotable certificate;
run the v4 probe after applying the patch. The verifier intentionally restricts
certification to the recorded bounded semantic fixtures; general host behavioral
parity, provider authorization in production, and arbitrary user expressions
remain outside this particular certificate.

The audit reports `portableCertification` and per-app `portablePromotion`,
with `sourceAuthorityModified=false`. If a normal truth-gate policy already
blocks an app, its portable promotion remains blocked even if its independent
package proof passes. Missing or refused evidence becomes a blocking
`audit-portable-certification-not-proven` finding when required.
