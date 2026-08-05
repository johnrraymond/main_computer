# MCEL Source-Tree De-Materialization

## Rule

A promoted MCEL application has one durable authored representation under `mcel_apps/<app>/`: its authoritative `application.js`, package metadata, requirements, runtime UI source, and authored tests. Generated compatibility contracts, normalized application records, browser packages, catalogs, candidates, and proof reports are not application source.

## Logical packages

Package discovery compiles a `dsl-authoritative` application and overlays its deterministic generated files in memory. Validation, package fingerprinting, runtime projection, acceptance, observation, proof, and compatibility consume that logical package. If stale `contracts/`, `generated/`, or `mcel.generated.json` files are present beside the source, package discovery excludes them so they cannot regain authority.

The logical package still exposes the compatibility files expected by the current runtime:

```text
contracts/domain.js
contracts/intents.js
contracts/adapter.js
contracts/surface.js
contracts/layout.js
contracts/acceptance.js
contracts/observation.js
mcel.generated.json
```

Workbench and the unpromoted Calculator shadow authority also expose `generated/mcel.application.normalized.json`. These bytes are computed, not durable application source.

The Calculator shadow package is deliberately source-only. Package discovery compiles
`mcel_apps/calculator/application.js` and overlays its generated contracts in memory,
but browser runtime projection excludes it until host-bound mounting is implemented.
The existing `/applications/calculator` HTML surface therefore remains the only live
Calculator while the DSL authority can compile and project through generic tools.

## Virtual viewport mount and ephemeral browser build

Normal viewport mounting serves the package catalog, runtime manifest, contracts, and browser runtime files directly from the logical package in memory. Starting the viewport, opening the Applications page, and requesting an MCEL package asset do not publish a browser-build tree.

Explicit build, observation, and proof workflows may still materialize the same deterministic bytes beneath the ignored build root:

```text
runtime/build/mcel/web/applications/mcel-packages/
runtime/build/mcel/web/applications/scripts/mcel-application-package-catalog.js
```

The whole `runtime/build/mcel` tree is disposable. It may be deleted at any time and reconstructed from authoritative source when a workflow requires physical browser files.

## Deleted durable duplicates

The repository no longer stores generated contracts or ownership manifests beneath the promoted Counter and Workbench package roots. It no longer stores browser package projections beneath `main_computer/web/applications`, and tests no longer keep complete duplicate copies of the live Counter and Workbench DSL sources.

## Invariants

De-materialization must preserve:

```text
application semantic fingerprint
logical package validity
runtime projection determinism
acceptance and browser evidence
IR-native intent completeness
repository-bound proof
```

Package and catalog fingerprints may change when the logical package implementation or authored tests change. That is not a semantic application change.

## Workbench projection profile de-materialization

Workbench generation no longer reads a checked-in compatibility snapshot. The versioned projector in `main_computer/mcel_projection_profiles/contract_workbench_v1.py` reconstructs the logical compatibility package deterministically from canonical Workbench IR plus compact projection policy.

The copied profile manifest, normalized definition, generated contracts, and promotion-test snapshot under `main_computer/mcel_projection_profiles/contract-workbench-v1/` have been removed. Projection outputs exist only in memory or in disposable compiler-candidate, runtime-build, and promotion-transaction workspaces. Mounting the authoritative DSL package therefore does not create generated files beneath `mcel_apps`, the checked-in browser tree, or the projection-profile source tree.
