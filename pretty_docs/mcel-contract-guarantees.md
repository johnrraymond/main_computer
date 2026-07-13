# MCEL Contract Guarantees

MCEL should not be trusted because a module says it owns a "law". In MCEL, a law is only an implementation module. A useful guarantee exists only when it has an explicit contract entry, a bounded scope, stated preconditions, stated non-guarantees, a fail-closed behavior, and executable evidence.

The contract manifest lives in:

```text
main_computer/web/applications/scripts/mcel-contract.js
```

The current contract envelope is exposed by `McelLabContract.buildContractEnvelope()`. It deliberately uses narrow guarantees instead of broad platform promises.

## What MCEL can currently guarantee

The current executable guarantees are:

- `mcel.contract.source-intent-is-input.v1`
- `mcel.contract.generated-runtime-is-discardable.v1`
- `mcel.contract.serializer-cleans-runtime-state.v1`
- `mcel.contract.repair-is-schema-bounded.v1`
- `mcel.contract.validation-is-reporting-not-trust.v1`
- `mcel.contract.browser-facts-are-runtime-only.v1`

These guarantees are absolute only inside their stated scopes. They are not claims that every Main Computer UI is automatically correct. They are claims about MCEL-owned source elements, MCEL-owned runtime artifacts, MCEL serialization, MCEL repair, and MCEL reports.

## What MCEL does not guarantee

MCEL does not guarantee:

- arbitrary non-MCEL DOM is semantically owned;
- invalid source values are preserved byte-for-byte after compile;
- user-authored content placed inside generated nodes can be recovered;
- caller code is safe when it ignores `serializerClean=false`, failed proof reports, failed contract guarantees, or uncovered guarantees;
- browser measurements remain true after viewport, CSS, or content changes;
- application-local layout sidecars are generic platform guarantees merely because they pass one application's tests;
- `data-mcel-layout-*` runtime traits are durable source policy;
- MCEL is a better platform replacement without evidence gates.

## Application-local layout guarantees

Code Editor and Git Tools currently have live application-local layout contracts. Their guarantees are scoped to those applications and their evidence:

```text
code-editor-layout-contract.js
git-tools-layout-contract.js
flog_code_editor_live_smoke.py
```

A layout behavior becomes a global MCEL guarantee only after it has:

- a stable generic contract entry;
- a normalized schema in `mcel-contract.js`;
- a public implementation in the MCEL facade/runtime;
- stated preconditions and non-guarantees;
- cross-application proof.

Until then, treat app-local layout reports as named workflow evidence, not as additions to the global contract envelope.

## Required fail-closed behavior

A MCEL feature is not trusted merely because it renders. It is trusted only when the relevant contract entry is covered and passing.

If evidence is missing, MCEL must expose one of these states instead of silently passing:

```text
failed test
failed guarantee
uncovered guarantee
serializerClean=false
a11yValid=false
adoption hold verdict
warning event
```

## How to verify

Run the contract-focused checks:

```bash
python -m pytest tests/test_mcel_contract_guarantees.py tests/test_mcel_runtime_package.py -q
```

For the full MCEL path, also rebuild the runtime and run the targeted MCEL tests:

```bash
python tools/build_mcel_runtime.py
python -m pytest tests/test_mcel_contract_guarantees.py tests/test_mcel_architecture_boundaries.py tests/test_mcel_lab_app.py tests/test_mcel_runtime_package.py -q
```

## Rule for future MCEL work

Do not add another "law" unless it either maps to an existing contract guarantee or adds a new explicit guarantee with:

1. an identifier;
2. a bounded scope;
3. exact preconditions;
4. non-guarantees;
5. fail-closed behavior;
6. executable evidence coverage.

Without that, it is terminology, not a contract.
