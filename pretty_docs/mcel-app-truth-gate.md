# MCEL App Truth Gate

## Purpose

`McelAppTruthGate` produces one domain-neutral truth snapshot for an application without replacing any existing authority.

It joins four independent facts:

1. the MCEL requirements contract;
2. domain-adapter readiness;
3. app-surface registry policy;
4. supplied runtime and acceptance evidence.

The truth gate does not inspect the live DOM, open applications, execute intents, or manufacture proof. Runtime evidence must be supplied by a caller such as the registry-driven FLOG runner.

## Authority boundaries

The truth gate is an aggregator, not a second registry.

- `McelRequirementsRegistry` remains authoritative for declared application requirements.
- `McelDomainAdapterRegistry` remains authoritative for executable semantic readiness.
- `McelAppSurfaceRegistry` remains authoritative for surface enrollment and required conformance layers.
- FLOG and other runners remain authoritative for captured runtime evidence.
- Acceptance runners remain authoritative for acceptance-test results.

The truth gate may summarize those facts and emit findings. It must not promote a missing or weaker fact into a stronger claim.

## Core API

```javascript
McelAppTruthGate.evaluateAppTruth(appId, {
  requirementsRegistry,
  domainAdapterRegistry,
  appSurfaceRegistry,
  runtimeEvidence,
  acceptanceEvidence,
  now,
  maxEvidenceAgeMs
});

McelAppTruthGate.buildTruthSnapshot({
  appIds,
  requirementsRegistry,
  domainAdapterRegistry,
  appSurfaceRegistry,
  runtimeEvidence,
  acceptanceEvidence,
  now,
  maxEvidenceAgeMs
});
```

Registry arguments are optional in the browser. When omitted, the gate reads the already loaded global registries.

`runtimeEvidence` and `acceptanceEvidence` are always inputs. The truth gate never reads interface state directly.

## Stable output

The output schema is:

```text
mcel-app-truth-snapshot-v1
```

Each application entry separates:

```text
requirements
adapter
surface
evidence.runtime
evidence.acceptance
claims
findings
overallStatus
```

The fields remain separate so a healthy surface cannot be mistaken for full semantic readiness.

## Claims

The gate derives these claims:

- `specified`
- `implementationPresent`
- `partiallyImplemented`
- `runtimeSurfaceProven`
- `acceptanceProven`
- `semanticRuntimeProven`
- `verificationComplete`

`semanticRuntimeProven` is intentionally strict. It requires all of the following:

- a present, strict-schema-valid, complete requirements contract;
- `fullApplicationSemanticReady` from the domain-adapter registry;
- fresh runtime evidence that passes the app-surface policy;
- completed runtime diagnosis;
- passing acceptance evidence when acceptance contracts are declared.

A passing FLOG surface report alone never proves full application semantics.

## Overall statuses

The summary status is one of:

- `untracked`
- `specified`
- `partially-implemented`
- `verification-incomplete`
- `runtime-proven`
- `blocked`

The summary is navigation, not the whole truth. Consumers must retain the component states and findings.

For example, this is valid:

```text
overallStatus: runtime-proven
runtimeSurfaceProven: true
semanticRuntimeProven: false
finding: missing-domain-adapter
```

It means the enrolled runtime surface is proven, while executable application semantics are not.

## Evidence freshness

Runtime evidence must include a parseable timestamp. FLOG report-level `generatedAt` is inherited by contained results.

The default freshness window is seven days. Callers may provide `maxEvidenceAgeMs`.

Evidence with no timestamp is not fresh. Stale or timestamp-less evidence cannot produce `runtimeSurfaceProven`.

## Findings

The first truth-gate contract defines these principal finding codes:

- `requirements-contract-missing`
- `requirements-contract-incomplete`
- `requirements-schema-invalid`
- `missing-domain-adapter`
- `required-intent-not-executable`
- `app-not-enrolled`
- `runtime-evidence-missing`
- `runtime-evidence-stale`
- `runtime-evidence-timestamp-missing`
- `runtime-diagnosis-incomplete`
- `surface-policy-failed`
- `acceptance-test-missing`
- `acceptance-test-failed`
- `semantic-readiness-overclaimed`

Blocking findings are reserved for evidence or contracts that directly contradict a claimed proof, such as failed required surface layers, incomplete diagnosis, failed acceptance evidence, or explicit semantic overclaim.

Missing evidence generally produces `verification-incomplete`, not a fabricated failure of the underlying application.

## FLOG input

The gate accepts either a single scenario result, an app-keyed map, or a complete FLOG report:

```javascript
const snapshot = McelAppTruthGate.buildTruthSnapshot({
  runtimeEvidence: flogReport,
  acceptanceEvidence: {
    "file-explorer": {
      status: "pass",
      testCount: 3,
      timestamp: "2026-07-27T10:00:00Z"
    }
  }
});
```

For a FLOG report, the gate reads `results` and carries the report's `generatedAt`, schema, and version into each normalized evidence entry.

## Non-goals for Patch 25a

Patch 25a does not:

- render a new MCEL Lab panel;
- change FLOG report files;
- create Lab findings automatically;
- execute repository-wide acceptance tests;
- promote any app to semantic-runtime readiness;
- alter application user interfaces.

Those are consumer/integration concerns for later patches.

## Patch 25b consumers

Patch 25b keeps the truth gate as the only aggregation authority and adds two consumers.

### FLOG integration

The registry-driven runtime FLOG runner asks the browser-loaded `McelAppTruthGate` to evaluate each scenario after diagnostics are captured.

Each trial and compact result may now include:

```text
appTruthAvailable
appTruth
```

The report also includes the latest gate-built:

```text
appTruthSnapshot
```

The snapshot is produced by `McelAppTruthGate.buildTruthSnapshot`, not reconstructed by Python. It therefore preserves the same findings and no-overclaim rules as the browser API.

FLOG's existing `summary.status` remains the runtime surface-smoke verdict. Truth-gate findings are attached as broader MCEL evidence and do not silently convert missing requirements, adapter, or acceptance proof into a failed browser scenario.

The Markdown report includes an **App truth** table that separates:

- overall truth status;
- runtime-surface proof;
- acceptance proof;
- full semantic-runtime proof;
- finding codes.

Diagnostic events include the scenario's attached `appTruth` object when available.

### MCEL Lab integration

MCEL Lab renders a selected-app **App truth** card. It shows the joined state without replacing the blueprint, mount, registry, or diagnostic authorities.

The card reports:

```text
requirements
adapter
runtime surface
acceptance
semantic runtime
```

Truth-gate findings are copied into the Lab findings list with their original finding codes and messages. Lab does not invent equivalent local findings.

When no supplied report exists, Lab may capture a current silent MCEL diagnosis and supply it to the truth gate as live runtime evidence. This is labeled as live diagnosis, not FLOG evidence.

A consumer API allows a complete FLOG report or acceptance result to be supplied explicitly:

```javascript
McelLabAppTruthConsumer.setRuntimeEvidence(flogReport);
McelLabAppTruthConsumer.setAcceptanceEvidence(acceptanceReport);
McelLabAppTruthConsumer.refresh();
McelLabAppTruthConsumer.clearEvidence();
McelLabAppTruthConsumer.selectedAppTruth();
```

The same API is available at:

```javascript
window.MCEL.labAppTruthConsumer
```

Blueprint aliases may differ from app-surface IDs. The Lab consumer evaluates the blueprint ID and declared aliases, then displays which registered truth ID was selected. It does not merge facts from unrelated IDs or alter any registry.

## Consumer guarantees

Patch 25b adds these guarantees:

- consumers retain the complete truth component states;
- FLOG runtime pass/fail remains separate from broader truth status;
- MCEL Lab labels the evidence source;
- missing acceptance evidence remains missing;
- a mounted preview is not treated as runtime proof;
- a green surface does not become full semantic readiness;
- truth findings retain their original code and message;
- consumers do not create a second registry or recompute truth findings.

## Patch 26 repository audit consumer

`main_computer/mcel_truth_audit.py` is the repository/CI consumer of the same
truth authority. It loads the registries and `McelAppTruthGate` under Node,
supplies optional FLOG and acceptance evidence, and writes
`mcel-repository-truth-audit-v1`.

The audit retains the complete `mcel-app-truth-snapshot-v1` as
`truthSnapshot`. It does not reproduce truth-gate findings in Python.

Default CI enforcement is intentionally narrow:

```bash
python main_computer/mcel_truth_audit.py --check
```

It fails only on truth-gate findings carrying `blocking: true` or on audit
integrity failures. Legacy enrollment gaps, missing evidence, and stale
evidence remain visible but non-blocking by default.

Release workflows may explicitly require current proof:

```bash
python main_computer/mcel_truth_audit.py \
  --check \
  --require-fresh-runtime \
  --require-acceptance
```

Promotion readiness is advisory. The audit reports the evidence needed for
`legacy → runtime-baseline` and `runtime-baseline → semantic-runtime`, but only
an explicit registry patch may change an app's declared maturity.

