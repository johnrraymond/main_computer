# Patch 13 foundation acceptance ledger

Scope is deliberately limited to **Stage 1 semantic foundations** and **Stage 2 correctness**. This package is not full Patch 13 live adoption.

| Requirement | Status | Evidence |
| --- | --- | --- |
| One canonical MCEL Application IR | PASS (offline) | Compiles to `mcel.application-ir.v1`; existing fingerprints and intent/effects boundary retained |
| Distinguish reusable vs app semantic vs source vs presentation | PASS (contract) | Evidence-marked derived `role`; source-only timestamp/envelope bindings; rejects presentation keys |
| Explicit timestamp formats and UTC normalization | PASS (offline) | ISO, CIM DMTF, .NET JSON and explicitly typed epochs; raw and matched format retained |
| Normalized mixed Task Manager producer dates | PASS (offline) | One binding can enumerate ISO, CIM, .NET alternatives without numeric magnitude inference |
| Per-row invalid record and incomplete identity | PASS (offline) | Rejected records and identity quarantine while valid rows survive |
| Duplicate identity safety | PASS (offline) | Preserve collection-level refusal for ambiguous keys |
| Measured zero vs unavailable | PASS (offline) | Existing Patch 11/12 checks and new independent tests |
| Bounded observation metadata and honest unknowns | PASS (contract/offline) | Producer and revision domain, scope, completeness, observed-at when actually provided; absent evidence stays missing/unknown |
| Logical worker vs lease identity | PASS (offline) | Distinct source observations; duplicates flagged and relationships candidate-only |
| Existing Patch 11 and Patch 12 Frobs | PASS (offline) | Invoked directly in Patch 13 Frob |
| Live Task Manager integration | NOT ATTEMPTED | Requires subsequent opt-in live adoption |
| Host-scoped process identity verified | UNVERIFIED | Host identifier not established by current producer |
| Patch 10 hosted DOM/focus/inspector continuity | UNVERIFIED | No hosted adoption or continuity promotion |
| Numeric timestamp browser renderer | UNCHANGED | Existing v2 deferral remains in effect |
| Source-complete absence claims | NOT SUPPORTED | Top-N/filtered observations are not complete system enumeration |
| Authoring economy | NOT MEASURED | Requires real application adoption and equivalent-behavior comparison |

The Patch 13 Frob reports unit counts and runs the Patch 11 and Patch 12 acceptance evaluators. Browser-hosted acceptance, real producer integration, UI design, authorization, and operational execution are all excluded.
