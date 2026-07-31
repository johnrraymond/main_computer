# RAG Retrieval Strategies

Start here: [Agent and RAG Overview](agent-rag-overview.md)

## Common contract

Every retriever should accept a bounded target and return an evidence bundle with repository-relative paths, exact excerpts or media references, source hashes, selection reasons, uncertainties, and retrieval metrics. Retrieval output is evidence, not mutation authority.

## Strategy catalog

| Strategy | One-line role | Current source/status |
| --- | --- | --- |
| Deterministic lexical | Rank exact terms and bounded chunks reproducibly. | `main_computer/rag_retriever.py`; shared |
| Harness decomposition | Let a model classify the task, then retrieve and generate a grounded plan. | `main_computer/rag_harness.py`; shared/harness |
| Semantic synonym | Recover relevant evidence when wording differs. | `main_computer/rag_quality_layer_smoke.py`; contract smoke |
| HyDE | Generate a hypothetical answer or document to improve retrieval. | quality and HyDE smokes; experimental |
| Step-back | Retrieve broader concepts before returning to the specific task. | `main_computer/rag_agentic_retrieval_loop_layer_smoke.py`; contract smoke |
| Retriever routing | Choose among local, web, tool, graph, or other retrievers. | agentic-loop smoke; contract smoke |
| Self-RAG | Retrieve, grade, critique, and retry adaptively. | agentic-loop smoke; contract smoke |
| RAPTOR | Retrieve from hierarchical summaries and source leaves. | `main_computer/rag_advanced_eval_layer_smoke.py`; contract smoke |
| TreeRAG | Locate a relevant branch, then expand only that branch. | advanced-eval smoke; contract smoke |
| GraphRAG | Use entities and relationships for local/global graph retrieval. | advanced-eval smoke; conceptual contract |
| Graphify | Build/query a real source graph, then hydrate selected source. | scripts under `scripts/graphify_*`; experimental |
| Web retrieval | Use current external sources when local evidence is missing or stale. | `main_computer/ai_web_search.py`, web-search smokes; mounted/shared by caller |
| Multimodal | Retrieve and reason over images plus text evidence. | multimodal smokes; experimental/operator |
| Hybrid | Merge lexical, semantic, graph, web, or multimodal evidence. | desired common interface; not one sole authority |

## Retrieval pipeline

```text
scope target
→ normalize request
→ choose strategy
→ retrieve bounded candidates
→ grade relevance and sufficiency
→ disambiguate conflicts
→ hydrate exact current source or media references
→ verify hashes and scope
→ pack context under budget
→ return evidence bundle
```

## Quality gates

A retrieval result should report:

- target scope and root;
- strategy and configuration;
- candidate and selected counts;
- exact source paths and hashes;
- context characters or tokens;
- duplicate and diversity behavior;
- contradictions and negative evidence;
- stale or missing evidence;
- whether external retrieval was used;
- whether the result is sufficient for answering, planning, or editing.

## Editing rule

Graph labels, semantic summaries, hypothetical documents, and model-generated descriptions are never editable source. Before a generated editor chooses anchors, selected evidence must resolve to exact current bytes from the scoped target.

## Source and smoke map

Key implementations and harnesses:

```text
main_computer/rag_retriever.py
main_computer/rag_harness.py
main_computer/rag_quality_layer_smoke.py
main_computer/rag_agentic_retrieval_loop_layer_smoke.py
main_computer/rag_advanced_eval_layer_smoke.py
main_computer/rag_hyde_ollama_docker_smoke_v3.py
main_computer/rag_minefield_ollama_docker_smoke.py
main_computer/rag_smoke_disambiguation_two.py
main_computer/rag_smoke_which_is.py
```

Focused deterministic tests include `tests/test_rag_retriever.py`, `tests/test_rag_harness.py`, `tests/test_rag_smoke_framework.py`, and the smoke-specific test modules.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
