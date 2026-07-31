# Graphify Retrieval

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: experimental optional retrieval backend under evaluation. Graphify is not currently the sole mounted retrieval authority.

## Purpose

Graphify adds structural evidence that plain text matching may miss: files, symbols, imports, references, HTML/CSS/JavaScript links, call paths, neighborhoods, and communities.

Graphify is a retriever. It is not an editor, agent, validator, artifact builder, or apply authority.

## High-level operations

- **Build graph** — Extract nodes, edges, source metadata, and a manifest for one scoped target.
- **Query graph** — Rank structurally relevant nodes and files for a request.
- **Explain result** — Preserve the query evidence that led to selection.
- **Find paths** — Trace relationships between candidate sources.
- **Hydrate evidence** — Load exact current source for selected files and verify hashes.
- **Invalidate graph** — Rebuild when the source, scope, ignore rules, extraction options, or Graphify version changes.
- **Compare retrievers** — Run Graphify and the baseline through identical downstream stages.

## Required editing integration

```text
scoped repository or external debug site
→ prompt-independent graph build
→ graph query and relationship expansion
→ candidate selection
→ exact-source hydration
→ source-hash verification
→ existing grounding and generated-editor validation
→ full-file promotion
→ replacement ZIP
→ new_patch.py --dry-run
```

Node labels, paths, summaries, and communities may explain selection, but they cannot substitute for exact source.

## Evidence record

A hydrated Graphify selection should carry:

```json
{
  "path": "repo/relative/path",
  "source_sha256": "...",
  "graph_node_ids": ["..."],
  "selection_reason": "...",
  "relationship_paths": [],
  "exact_source": "..."
}
```

## Safety rules

- Build graphs only inside the intended scope.
- Reject absolute paths and traversal.
- Do not leak expected benchmark targets into retrieval as hidden hints.
- Do not graph live Website Builder sites when an external `debug-*` fixture is available.
- Reject stale graphs after source changes.
- Do not treat graph-query success as grounding or edit success.
- Never let Graphify apply a patch.

## Evaluation surface

Relevant scripts include:

```text
scripts/graphify_main_computer_repo_smoke_v9.py
scripts/graphify_vs_existing_rag_smoke.py
scripts/graphify_vs_existing_rag_ollama_realworld_smoke_v2.py
scripts/graphify_vs_website_editor_debug_site_rag_ollama_smoke_v5.py
```

The Website Builder comparison must keep model stages, validators, promotion, packaging, and dry-run identical between lanes. Only retrieval may differ.

## Adoption gate

Adoption requires equal or better semantic and artifact correctness, no wrong-target or safety regression, exact-source hydration in every edit case, stable results across prompts/sites/repeats, and a repeatable advantage in repairs, model calls, tokens, context quality, or latency.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
