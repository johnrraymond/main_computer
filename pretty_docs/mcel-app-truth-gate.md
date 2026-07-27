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
