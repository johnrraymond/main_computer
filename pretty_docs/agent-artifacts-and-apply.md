# Agent Artifacts and Apply

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Artifacts are the boundary between a validated proposal and repository mutation.

## High-level operations

- **Promote replacement** — Convert an excerpt-level or patch-level proposal into complete final file contents.
- **Build artifact** — Package repository-relative replacement files and explicit supported operations.
- **Validate artifact** — Check paths, roots, files, hashes, modes, and terminal contracts.
- **Dry-run artifact** — Test with `new_patch.py` without writing.
- **Approve artifact** — Bind operator approval to a specific artifact hash and target state.
- **Apply artifact** — Recheck state and copy only approved replacements.
- **Verify applied state** — Confirm file hashes, postconditions, tests, and mutation scope.

## Replacement ZIP contract

A raw replacement ZIP should contain only repository-relative paths and complete final file contents. It must not contain absolute paths or `..` traversal.

Raw snapshot mode compares included files to the target repository. It does **not** infer deletion from omitted files. A deletion requires an explicit delete-capable mode or a separately documented operation.

## `new_patch.py` boundary

Normal validation:

```powershell
python new_patch.py replacement-files.zip --dry-run
```

Fuzzy overwrite is opt-in:

```powershell
python new_patch.py replacement-files.zip --dry-run --allowfuzz
```

Do not describe a fuzzy result as exact. Do not enable `--allowfuzz` automatically.

## Terminal contracts

Primary files:

```text
main_computer/rag_terminal_artifact_contract.py
main_computer/rag_terminal_result_contract.py
main_computer/rag_generated_editor_discovery_grounding_smoke.py
new_patch.py
```

`rag_terminal_artifact_contract.py` recognizes mode-aware artifacts such as snapshot ZIPs and verified bundles. `rag_terminal_result_contract.py` decides whether a result mode is terminal and promotable.

## Apply preconditions

- artifact paths are safe and rooted correctly;
- target files still match expected source hashes;
- create/modify/delete intent matches the live state;
- exact dry-run passed;
- operator approval is present for live apply;
- no unapproved fuzz is required;
- verification steps are known.

## Verification layers

Keep these claims separate:

1. Artifact bytes are internally valid.
2. Artifact is compatible with the target repository.
3. Apply changed the intended files.
4. Focused tests passed.
5. Runtime behavior is correct.

Passing an earlier layer does not prove a later one.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
