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


Discover the newest schema-valid reports and run the release-grade gate:

```bash
python main_computer/mcel_truth_audit.py --release-gate
```

`--release-gate` is shorthand for:

```text
--check
--latest-runtime-evidence
--latest-acceptance-evidence
--require-fresh-runtime
--require-acceptance
--require-repo-match
```

Reports are written to:

```text
runtime/reports/mcel-truth-audit/mcel-repository-truth-audit.json
runtime/reports/mcel-truth-audit/mcel-repository-truth-audit.md
```

Use `--no-write --json` for machine consumption without creating files.

## Evidence discovery and repository binding

Runtime evidence can be selected from the newest schema-valid FLOG report:

```bash
python main_computer/mcel_truth_audit.py --latest-runtime-evidence
```

Acceptance evidence can be selected from the newest report whose schema
contains `acceptance`:

```bash
python main_computer/mcel_truth_audit.py --latest-acceptance-evidence
```

Explicit evidence paths and their corresponding `--latest-*` options are
mutually exclusive.

FLOG reports carry a repository fingerprint representing source state in a
`repositoryProvenance` object using:

```text
mcel-repository-provenance-v2
sha256-source-path-content-v2
```

The fingerprint represents source authority rather than the entire working
tree.

When Git metadata and Git are available, the selection method is
`git-tracked-and-unignored`: tracked files plus non-ignored untracked files are
hashed in deterministic path order. This captures local source edits and new
source files while respecting repository ignore rules.

Snapshot exports do not contain `.git`. In that mode the selection method is
`snapshot-source-roots`. The fallback hashes deterministic source roots,
including application code, tests, MCEL documents, contracts, deployment
configuration, tools, game projects, and selected versioned website sources.

Both modes explicitly exclude mutable or generated state such as runtime
reports, databases, sessions, browser profiles, logs, caches, patch reports,
generated build trees, and temporary diagnostic output. Changing those files
after FLOG runs must not invalidate evidence. Changing included source files
must change the fingerprint.

The provenance object records both `scope` and `selectionMethod`, plus
`sourceRoots` for snapshot fallback mode. Version-1 provenance is intentionally
unsupported after this correction; regenerate FLOG and acceptance evidence
after applying Patch 27a.

Evidence binding is reported as:

- `exact`: produced from the current repository source state;
- `mismatch`: produced from a different source state;
- `unbound`: no repository provenance was declared;
- `unsupported`: provenance uses an unknown schema or algorithm;
- `absent`: no evidence report was selected.

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
evidence gaps into audit-policy failures. Their finding codes are separate from
truth-gate implementation findings:

- `audit-required-runtime-proof-missing`
- `audit-required-acceptance-proof-missing`

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
