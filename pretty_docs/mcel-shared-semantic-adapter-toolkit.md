# MCEL Shared Semantic Adapter Toolkit

Contract:

```text
mcel.semantic-adapter-toolkit.conformance.v1
```

Patch 33 freezes the reusable semantic-adapter toolkit that Patch 32 extracted from the two proven semantic-runtime adapters:

- File Explorer: bounded read-only navigation and inspection.
- Calculator: multi-lane deterministic/provider-backed computation.

The toolkit is intentionally narrow. It gives adapters shared primitives, but it does not grant semantic-runtime proof by itself.

## Status

- Toolkit script: `main_computer/web/applications/scripts/mcel-semantic-adapter-toolkit.js`
- Toolkit version: `mcel-semantic-adapter-toolkit-v1`
- Conformance contract: `mcel-semantic-adapter-toolkit-conformance-v1`
- Primary consumers: `CalculatorSemanticAdapter`, `FileExplorerSemanticAdapter`
- Truth authority remains: `McelAppTruthGate` plus repository-bound runtime and acceptance evidence.

## Public API contract

A conforming toolkit export must expose the following public API:

```text
VERSION
INTENT_STATUSES
CONTRACT_ID
CONTRACT_VERSION
clonePlain
nowIso
safeString
normalizedId
semanticStatusFor
cloneIntentDeclaration
intentDefinitionFor
listIntentDefinitions
recoveryCoverageAudit
appendBoundedReceipt
listBoundedReceipts
preflightResult
dispatchAction
listConformanceClauses
buildConformanceContract
validateToolkitConformance
```

The required intent status vocabulary is:

```text
executable
preflight-only
declared-only
prohibited
planned
```

`validateToolkitConformance()` returns a deterministic report:

```text
schema: mcel-semantic-adapter-toolkit-conformance-report-v1
contractId: mcel.semantic-adapter-toolkit.conformance.v1
contractVersion: mcel-semantic-adapter-toolkit-conformance-v1
passed: boolean
missingPublicApi: string[]
missingIntentStatuses: string[]
failedClauseIds: string[]
```

## Conformance clauses

The contract currently has five clauses.

### Plain state snapshots (`mcel.semantic-adapter-toolkit.clone-plain.v1`, `state-snapshot`)

`clonePlain` must clone adapter-visible data and strip callable runtime bindings. This keeps state snapshots, receipt ledgers, and evidence maps serializable.

### Intent declarations (`mcel.semantic-adapter-toolkit.intent-declaration.v1`, `intent-declaration`)

`semanticStatusFor`, `cloneIntentDeclaration`, `intentDefinitionFor`, and `listIntentDefinitions` must preserve the distinction between:

```text
current executable scope
preflight-only planned scope
declared-only vocabulary
explicitly prohibited operations
future planned work
```

For File Explorer, this preserves the bounded read-only scope and keeps `deleteFile`, `moveOrRename`, and `runFileCommand` prohibited. For Calculator, this preserves explicit compute/helper lanes and prevents hidden fallback between lanes.

### Preflight and receipts (`mcel.semantic-adapter-toolkit.preflight-receipt.v1`, `preflight-and-receipts`)

`preflightResult`, `appendBoundedReceipt`, and `listBoundedReceipts` must keep allow/block decisions explicit and must preserve bounded execution evidence without mutating unrelated adapter state.

### Execution dispatch (`mcel.semantic-adapter-toolkit.dispatch.v1`, `execution-dispatch`)

`dispatchAction` must report missing or throwing runtime bindings as explicit failed semantic results. It must not throw through the semantic adapter boundary for normal runtime-binding failures.

### Recovery coverage (`mcel.semantic-adapter-toolkit.recovery-coverage.v1`, `recovery-coverage`)

`recoveryCoverageAudit` must derive pass/fail status from declared failure classes. Required classes that are not covered keep the recovery audit failing.

## Non-goals

This toolkit does not:

```text
define application domain vocabulary
authorize a new semantic-runtime scope
convert another application to semantic runtime
replace a requirements contract
replace runtime FLOG evidence
replace acceptance evidence
make browser/runtime proof unnecessary
```

## Adoption rules for future adapters

A future adapter may use the toolkit only when the adapter still declares its own:

```text
adapter id
adapter version
semantic runtime scope
intent catalog
prohibited operations
preflight checks
runtime bindings
receipt schema
failure classes
recovery guidance
coverage audits
```

Shared helpers may remove duplicated mechanics. They may not blur scope boundaries or silently convert planned/prohibited work into executable behavior.

## Verification

Focused verification for this contract:

```powershell
python -m pytest tests/test_mcel_semantic_adapter_toolkit.py tests/test_mcel_calculator_semantic_adapter.py tests/test_mcel_file_explorer_semantic_adapter.py
```

Release verification still requires:

```powershell
python main_computer/mcel_acceptance_runner.py
python main_computer/flog_mcel_runtime_smoke.py
python main_computer/mcel_truth_audit.py --release-gate
```

The final repository truth audit remains authoritative because it binds runtime evidence, acceptance evidence, and exact source provenance.
