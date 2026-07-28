# MCEL File Explorer Semantic Adapter

## Purpose

`FileExplorerSemanticAdapter` is the domain adapter for the current File Explorer application.

It proves the bounded read-only application scope rather than merely describing the rendered surface. The adapter is registered with `McelDomainAdapterRegistry` and is loaded before the specimen planner.

## Current semantic-runtime scope

The declared scope is:

```text
bounded-read-only-file-explorer-v1
```

Current executable intents:

- `inspectRoots`
- `selectRoot`
- `listDirectory`
- `navigateUp`
- `searchCurrentFolder`
- `previewEntry`
- `classifyEntry`

Current prohibited intents:

- `deleteFile`
- `moveOrRename`
- `runFileCommand`

`openInOwningApp` remains planned and outside the current readiness derivation. The adapter does not silently open another application, save a file, create a revision, stage Git state, or execute a command.

The truth gate preserves that distinction: the three prohibited mutation/command intents are complete policy classifications, while `openInOwningApp` is reported as an explicitly excluded planned intent. Neither creates a false `required-intent-not-executable` finding for the current bounded read-only scope.

## Runtime ownership

The File Explorer UI routes its existing roots, list, search, and read requests through `FileExplorerSemanticAdapter.requestEndpoint(...)` when the adapter is available. The underlying API remains authoritative for filesystem boundary enforcement and read-only response data.

The adapter maintains semantic state for:

- trusted roots;
- selected root;
- current relative path;
- current directory entries;
- selected entry;
- preview evidence;
- bounded search evidence;
- read-only execution receipts.

## Readiness proof

The adapter provides all domain-registry methods required for runtime-core readiness:

```text
getState
listObjects
listIntents
preflightIntent
executeIntent
buildReceipt
mapEvidence
classifyFailure
buildRecoveryOptions
```

It also provides derived intent-coverage and recovery-coverage audits.

Full readiness is limited to the current bounded read-only application scope. Planned cross-app handoff is listed separately and cannot be mistaken for an executable current intent.

## Safety rules

- Absolute paths and `..` traversal are blocked before transport.
- Unknown roots are rejected once trusted roots have been observed.
- Search requires a non-empty query.
- Delete, move/rename, and command execution always produce blocked receipts.
- Executable intents are read-only.
- A failed request produces a classified failure and structured recovery guidance.
- No mutation fallback exists.

## Verification

Primary tests:

```text
tests/test_mcel_file_explorer_semantic_adapter.py
tests/test_viewport_file_explorer.py
tests/test_mcel_file_explorer_surface.py
tests/test_mcel_file_explorer_layout_fit.py
```

Repository truth is proven only after the adapter, runtime FLOG evidence, and acceptance evidence all bind to the same repository source fingerprint.
