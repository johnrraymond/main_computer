# Context Compaction

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: a mix of shared context budgeting, operator harnesses, and experimental representation research.

## Purpose

Context compaction reduces repeated or oversized evidence while preserving enough provenance for the model and host to recover the source meaning.

## High-level operations

- **Pack bounded context** — Select chunks under character or token budgets.
- **Deduplicate evidence** — Avoid repeated files, excerpts, and terminal output.
- **Create context handle** — Store a large object externally and pass a compact reference.
- **Render context excerpt** — Expose only the portion needed for the current request.
- **Encode AI-readable logs** — Compress repetitive logs into a self-describing representation.
- **Evaluate retention** — Test whether compressed context still supports held-out questions.
- **Invalidate context object** — Rebuild when its source bytes or representation rules change.

## Current approaches

| Approach | Role | Status |
| --- | --- | --- |
| RAG chunk packing | Bound exact retrieved context. | shared |
| Text-console clobs | Side-load repository trees and large tool results. | operator/contract smoke |
| Logpack | Encode repetitive logs for model question answering. | experimental |
| XEL | Add compact semantic cues to encoded log structures. | experimental |
| XEL Taguchi | Tune language rules against visible and locked holdout corpora. | experimental |
| Ahbe AI | Explore compact idea-oriented representations. | experimental |

Relevant files include:

```text
main_computer/rag_text_console_clob_v2_smoke.py
main_computer/rag_smoke_logpack_ollama.py
main_computer/rag_smoke_logpack_fast_ollama.py
main_computer/rag_smoke_logpack_compact_ollama.py
main_computer/rag_smoke_xel_taguchi.py
main_computer/rag_smoke_ahbe_ai.py
main_computer/rag_smoke_ahbe_ai_finetuned.py
```

## Required provenance

A compacted object should record source identifiers and hashes, representation version, creation time, byte/token counts before and after, retained excerpts, and the questions or policies used to evaluate retention.

## Safety rules

- Compression must not erase the path or hash needed to retrieve exact source.
- A model-readable encoding is not proof that all source facts were retained.
- Editing must hydrate exact source rather than edit compressed text.
- Holdout evaluation must remain separate from tuning data.
- Secrets and unrelated large outputs should be filtered before context creation.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
