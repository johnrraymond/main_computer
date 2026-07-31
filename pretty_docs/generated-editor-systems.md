# Generated Editor Systems

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Generated editors turn grounded evidence into proposals. They do not grant models direct write authority.

## Shared operation chain

```text
select and scope target
→ stage an isolated workspace
→ build a prompt-independent index
→ model chooses terminal mode and evidence
→ host verifies exact paths, literals, and anchors
→ model grounds the intended change
→ model proposes replacement content
→ host validates the proposal
→ promote to complete replacement files
→ package a replacement artifact
→ new_patch.py --dry-run
→ optional explicit approval and guarded apply
→ verify postconditions
```

## Website Builder

Status: mounted generated-editor path with shared smoke-named helpers.

Primary files:

```text
main_computer/viewport_routes_applications.py
main_computer/website_builder_generated_editor_pipeline.py
main_computer/website_builder_rag_pipeline.py
main_computer/rag_generated_editor_discovery_grounding_smoke.py
main_computer/rag_generated_editor_claim_grounding_smoke.py
main_computer/rag_terminal_result_contract.py
main_computer/rag_terminal_artifact_contract.py
```

Model stages may include terminal classification, discovery, discovery repair, anchor-option selection, grounding, grounding repair, patch proposal, and patch repair. Deterministic code must verify every stage before promotion.

Model-backed smokes must use external `debug-*` website fixtures and must inventory live Website Builder paths before and after the run.

## Game Editor

Status: operator and proposal-only harnesses; do not infer a general live-apply contract.

Primary files:

```text
main_computer/rag_chat_game_editor_operator_smoke.py
main_computer/rag_game_editor_golden_path_smoke_short.py
main_computer/rag_game_editor_real_edit_smoke.py
```

The chat operator path is read-only. The golden-path harness produces scoped replacement payloads in diagnostics output without mutating the source project.

## Gremlin and code-edit pathways

Status: operator/contract harnesses.

Primary files:

```text
main_computer/gremlin_rag_smoke.py
main_computer/rag_gremlin_action_smoke.py
main_computer/rag_gremlin_pyramid_atom_smoke.py
main_computer/rag_code_edit_agent_guidance_smoke.py
```

These paths use retrieved repository evidence, exact edits, sandboxing, verification, and in some cases long-running or Byzantine agent control.

## Terminal result modes

`main_computer/rag_terminal_result_contract.py` recognizes mode-specific terminal results, including diagnosis-only, edit proposal, full-file replacement, patch artifact, already-applied no-op, and runtime verification report.

An intermediate proposal is nonterminal until its declared result-mode contract passes.

## Preservation and scope

A valid generated edit must prove:

- the target path is within scope;
- claimed evidence exists in current source;
- replacement files are complete final contents;
- non-target files remain unchanged unless explicitly included;
- create, modify, and delete intent is explicit;
- the artifact root matches the intended repository;
- dry-run succeeds without hidden fuzz.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
