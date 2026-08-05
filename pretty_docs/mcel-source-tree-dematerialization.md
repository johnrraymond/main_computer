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

Workbench also exposes `generated/mcel.application.normalized.json`. These bytes are computed, not durable application source.

## Ephemeral browser build

Browser packages and the package catalog are materialized only beneath the ignored build root:

```text
runtime/build/mcel/web/applications/mcel-packages/
runtime/build/mcel/web/applications/scripts/mcel-application-package-catalog.js
```

The viewport and observation runner generate this build on demand. The whole `runtime/build/mcel` tree may be deleted at any time and reconstructed from authoritative source.

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

## Remaining compatibility debt

Workbench generation still uses the centralized, versioned compatibility snapshot under `main_computer/mcel_projection_profiles/contract-workbench-v1`. That snapshot is no longer copied into `mcel_apps` or the checked-in browser tree, but it remains a compiler backend. A later change should replace the snapshot bundle with an actual deterministic projector before deleting it.
