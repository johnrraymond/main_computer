# MCEL acceptance evidence runner

## Purpose

`main_computer/mcel_acceptance_runner.py` converts declared MCEL acceptance
contracts into repository-bound execution evidence.

The runner does not define product truth. Authorities remain separated:

- `pretty_docs/*.md` and validated package `requirements.md` files declare `mcel-acceptance` obligations;
- `main_computer/mcel_acceptance_bindings.json` maps legacy central contracts to concrete pytest selectors;
- package `tests/mcel_acceptance_bindings.json` files map package contracts to package-relative selectors;
- pytest execution proves or rejects those bindings;
- `McelAppTruthGate` consumes the app-level result;
- `mcel_truth_audit.py --release-gate` binds the report to the exact repository
  source fingerprint.

A passing test file cannot prove an acceptance contract unless the binding
catalog explicitly names that contract.

## Commands

Run all declared acceptance contracts and write evidence:

```bash
python main_computer/mcel_acceptance_runner.py
```

Require the acceptance runner itself to return nonzero when an enforceable
contract is not proven:

```bash
python main_computer/mcel_acceptance_runner.py --check
```

Run one app without replacing the canonical all-app report:

```bash
python main_computer/mcel_acceptance_runner.py --app file-explorer
python main_computer/mcel_acceptance_runner.py --app contract-counter --check
```

The scoped report is written under:

```text
runtime/reports/mcel-acceptance/apps/file-explorer/
```

Replacing the canonical report with partial evidence is an explicit operator
action:

```bash
python main_computer/mcel_acceptance_runner.py   --app file-explorer   --overwrite-canonical
```

List declarations and bindings without executing pytest:

```bash
python main_computer/mcel_acceptance_runner.py --list-contracts
```

Canonical artifacts from an unfiltered run:

```text
runtime/reports/mcel-acceptance/mcel-acceptance-report.json
runtime/reports/mcel-acceptance/mcel-acceptance-report.md
```

Every report declares `mcel-evidence-scope-v1`. Canonical reports use
`canonical: true`; filtered reports use `canonical: false` and list their
selected and covered apps.

The release-grade sequence is:

```bash
python main_computer/flog_mcel_runtime_smoke.py
python main_computer/mcel_acceptance_runner.py
python main_computer/mcel_truth_audit.py --release-gate
```

## Contract states

The requirements declaration status controls whether a contract is due now.

Currently enforceable:

- `specified`
- `partially-implemented`
- `implemented`
- `verified`
- `current-plus-planned`

Visible but not currently enforceable:

- `draft`
- `planned`
- `open`

A future contract is reported as `not-due`. It is not silently deleted and it
does not count as executed proof.

An enforceable contract may report:

- `pass`: explicit binding, tests collected, all passed;
- `missing-binding`: no execution mapping exists;
- `no-tests`: pytest collected no tests;
- `fail`: one or more bound tests failed;
- `execution-error`: pytest could not complete reliably.

Only `pass` proves an enforceable contract.

## Binding catalogs

The legacy central binding catalog uses schema:

```text
mcel-acceptance-bindings-v1
```

Each binding contains:

```json
{
  "id": "file-explorer.binding.acceptance.read-only-browse-preview",
  "appId": "file-explorer",
  "acceptanceContractId": "file-explorer.acceptance.read-only-browse-preview",
  "runner": "pytest",
  "selectors": [
    "tests/test_viewport_file_explorer.py"
  ]
}
```

Safety rules:

- contract IDs must exist in the requirements registry;
- binding app IDs must match the declaration;
- one contract may have only one binding;
- selectors must be repository-relative `tests/*.py` pytest selectors;
- absolute paths and `..` traversal are rejected;
- missing test files are an error;
- a zero-test pytest result is not a pass.

The catalogs are execution maps, not second requirements registries.


Generated application packages use:

```text
mcel.package-acceptance-bindings.v1
```

The package manifest declares the file through `tests.acceptanceBindings`. Its selectors are package-relative and must remain beneath the package's declared tests root. Package validation rejects mismatched app identities, unknown acceptance contract IDs, duplicate targets, unsupported runners, traversal, symlink escapes, and missing test files.

The acceptance runner combines central and package sources without merging their authority. Duplicate contract IDs, duplicate binding IDs, or a contract bound both centrally and locally are fatal. Package execution evidence records the source package root, package fingerprint, binding-file hash, declared selector, and resolved repository selector.


Binding precision rule:

- bind a contract to the narrowest pytest selectors that actually prove that
  contract;
- do not bind several unrelated contracts to a whole broad test module when a
  single platform-specific failure would make every contract fail;
- preserve the exact selector, command, stdout, and stderr in each contract
  result so an environment-specific failure remains attributable.

## Evidence schema

The JSON report uses:

```text
mcel-acceptance-evidence-report-v1
```

It includes:

- deterministic repository provenance;
- requirements-registry metadata;
- central binding-catalog hash and package binding-file hashes;
- validated application-package fingerprints;
- app-level pass/fail results;
- contract-level declaration and execution status;
- pytest command, return code, duration, counts, and bounded output;
- missing bindings and failed contract IDs.

The app truth gate reads the report's `results` entries. An app passes
acceptance only when every currently enforceable contract in that app passes.

## Current baseline

Patch 28a binds the specified Calculator no-hidden-mutation contract to focused
boundary tests covering local arithmetic/graph execution, explicit model and
Mathics routes, and the absence of file, Git, revision, checkpoint, or terminal
mutation paths.

Website Builder's three core acceptance contracts use contract-specific pytest
selectors. They no longer execute the entire broad Website Builder test module,
so one unrelated or platform-specific failure cannot be amplified into three
contract failures.

File Explorer, Git Tools, MCEL Lab, and the Website Builder blog-runtime
contract remain bound to their existing focused suites. Planned Calculator,
Code Editor, MCEL Lab, and Website Builder semantic-runtime contracts remain
visible as `not-due`.

The runner still exposes real failures. Precision removes false amplification;
it does not suppress a failing bound contract.

## Package-local baseline

Contract Counter declares `contract-counter.acceptance.operation-control` in `mcel_apps/contract-counter/requirements.md` and binds it locally through `tests/mcel_acceptance_bindings.json`. The bound test executes increment, reset, stale, duplicate, prohibited, and failed-postcondition paths through `mcel-application-runtime.js` layered over `mcel-scm.js`. The app-scoped command returns passing evidence without a central binding-catalog edit.
