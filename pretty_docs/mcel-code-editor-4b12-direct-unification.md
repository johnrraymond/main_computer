# MCEL Code Editor 4B.1 / 4B.2 — Direct canonical unification

Baseline: November 19, 2026 13:41 repository snapshot with Patch 3 and Patch 4A.

## Authoritative application

- `mcel_apps/code-editor/application.js` and `application.html` now declare one Code Editor with `appId=code-editor`.
- Its operational IR retains the complete 39-intent, 13-capability, 36-effect incumbent semantic surface, including source reads/writes, document editor, review/approve, Aider, documentation, SCM, proof and in-memory runtime functions.
- The generated document-editor runtime continues to own edit, discard, close and tab interactions; they are not duplicate privileged intents.
- The active native Code Editor route compiles the package, not the test fixture. The fixture is retained temporarily as historical test evidence, not an application authority. Retirement belongs to 4B.6.
- `mcel.app.json` uses the 2.0.0 paired HTML+JS contract with an explicit native route. The former host-bound `blueprint.json` must be removed by the finalizer.

## Authority boundary

The Code Editor's `source-reader` and other observations remain `external-read`; `source-writer` and `review-applier` remain `source-write`; Aider lifecycle mutations remain `consequential`. The browser port retains its exact allowlist, stale-source proof, observed-file evidence, review handle, pending/uncertain Aider write block and receipt checks. `code-editor` uses the exact same allowance as the old `semantic-code-editor` during this incremental migration.

## Package-catalog integration

Native paired-source apps with an explicit identity-matched route produce a metadata-only catalog projection. The projection never copies executable JS or claims an automatically created generic adapter. New paired apps without an adopted route remain source-only and are not falsely advertised as live.

## Deliberate development incompatibilities

The retired Code Editor host-bound projection tests are now obsolete and will fail until rewritten for the paired-source runtime. The old projection module and static Code Editor HTML are not yet deleted (4B.6). The live route is canonical; the old SPA include has become inert scaffolding. No Code Editor VS Code-style layout reconstruction was attempted in 4B.1/4B.2; that is 4B.3.

## Apply

1. Extract patch ZIP into repository root, preserving paths.
2. Before restart or running pytest, run `python -m tools.mcel_code_editor_4b12_finalize` from repository root. The finalizer safely removes the known old Code Editor blueprint and verifies the canonical compiler, native host, port adapter and package catalog. It refuses to delete modified blueprints.
3. Run `python -m pytest -q tests/test_mcel_code_editor_4b12_unification.py tests/test_mcel_semantic_other_apps.py`.

The archive overlay by itself is **not** a valid installed migration because ZIP extraction cannot delete a retired file.
