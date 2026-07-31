# Graphify Retrieval

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: retired experimental evaluation record. Graphify is not mounted and is not the default retrieval authority.

## Purpose

Graphify was evaluated as a way to add structural evidence that plain text matching may miss: files, symbols, imports, references, HTML/CSS/JavaScript links, call paths, neighborhoods, and communities.

Graphify is a retriever. It is not an editor, agent, validator, artifact builder, or apply authority.

## Current repository state

- No Graphify smoke or A/B scripts are retained in this snapshot.
- No mounted or shared Python module imports Graphify.
- The deterministic retrieval baseline remains the Website Builder default.
- `requirements.txt` still pins `graphifyy==0.9.29`; that dependency is retained separately from the retired harnesses and may be removed in a dedicated dependency cleanup.
- Historical benchmark artifacts are evidence records, not runtime dependencies.

## Historical integration contract

The evaluation kept Graphify behind exact-source hydration and the existing generated-editor safety gates:

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

Node labels, paths, summaries, and communities could explain selection, but they could not substitute for exact source.

A hydrated selection used this general evidence shape:

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

## Decision record

The final two-site, two-repeat Website Builder gate produced 32 paired runs:

| Metric | Baseline | Graphify |
| --- | ---: | ---: |
| Semantic passes | 23/32 | 21/32 |
| Average quality | 0.8633 | 0.8125 |
| Model calls | 122 | 134 |
| Prompt-evaluation tokens | 213,403 | 276,000 |
| Repairs | 27 | 40 |
| Elapsed time | 1,807.462 s | 2,152.003 s |
| Exact excerpt context | 30,478 chars | 91,082 chars |

Graphify performed strongly on the prompt-injection fixture, but it did not meet the broader adoption gate. It returned lower aggregate correctness while using more context, calls, repairs, tokens, and time. The standalone Graphify smoke and comparison scripts were therefore removed, and the baseline remained the default.

The gate run also detected unrelated tracked repository changes during execution. That prevents treating the run as a pristine archival benchmark, but it does not supply evidence that Graphify met the adoption threshold.

## Safety rules retained for any future graph retriever

- Build graphs only inside the intended scope.
- Reject absolute paths and traversal.
- Do not leak expected benchmark targets into retrieval as hidden hints.
- Do not graph live Website Builder sites when an external `debug-*` fixture is available.
- Reject stale graphs after source changes.
- Do not treat graph-query success as grounding or edit success.
- Require exact-source hydration before selecting anchors or proposing edits.
- Never let Graphify apply a patch.

## Future reintroduction gate

A future graph retriever must demonstrate equal or better semantic and artifact correctness, no wrong-target or safety regression, exact-source hydration in every edit case, stable results across prompts/sites/repeats, and a repeatable operating advantage. Reintroduction requires a new harness built against the then-current editor contract; deleted historical scripts must not be treated as current authority.

## Provenance

- Source snapshot: `main_computer_test-20260731-105403.zip`
- Evidence status: source-inspected documentation with recorded model-backed decision data.
- Model-backed verification: not run for this documentation update; historical gate result recorded.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
