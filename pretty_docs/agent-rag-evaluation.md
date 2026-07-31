# Agent and RAG Evaluation

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Evaluation must compare complete operation contracts, not merely whether a provider returned text.

## High-level operations

- **Evaluate retrieval** — Compare candidate evidence quality while holding downstream stages constant.
- **Evaluate agent** — Compare terminal correctness, artifacts, safety, repairs, tokens, and latency.
- **Run adversarial minefield** — Exercise decoys, ambiguity, contradictions, prompt injection, and poisoned evidence.
- **Run hallucination probes** — Check for invented files, anchors, tests, claims, and verification.
- **Run provider diagnostics** — Measure transport, streaming, structured output, timeout, and thinking behavior.
- **Run contract smokes** — Prove stable deterministic boundaries.
- **Run model-backed smokes** — Exercise real providers with explicit nondeterminism and runtime assumptions.
- **Decide adoption** — Apply documented correctness and safety gates before mounting a candidate strategy.

## Required metrics

Evaluation reports should include:

- semantic and terminal correctness;
- target-file accuracy;
- exact-source grounding;
- preservation and unrelated-mutation checks;
- artifact creation and `new_patch.py --dry-run`;
- pass rate and catastrophic-failure rate;
- model calls and repair calls;
- prompt/evaluation token counts;
- context size and evidence composition;
- latency;
- provider/model/version;
- fixture and source hashes.

## Fair A/B rule

When comparing retrievers, keep the model, prompts after retrieval, validators, promotion, packaging, and dry-run identical. Only retrieval and its exact-source hydration may differ.

## Website safety

Model-backed Website Builder evaluation must use external `debug-*` fixtures. Protect and inventory:

```text
runtime/websites
runtime/local-platform/sites.json
deploy/local-platform/generated/docker-compose.websites.yml
```

A created untracked file is still a mutation and must be detected.

## Important suites and harnesses

```text
main_computer/rag_smoke_framework.py
main_computer/rag_quality_layer_smoke.py
main_computer/rag_agentic_retrieval_loop_layer_smoke.py
main_computer/rag_advanced_eval_layer_smoke.py
main_computer/rag_minefield_ollama_docker_smoke.py
main_computer/rag_hallucination_guard_smoke.py
main_computer/rag_hallucination_miner.py
tests/test_debug_website_golden_path_no_deterministic_cheats.py
scripts/graphify_vs_existing_rag_ollama_realworld_smoke_v2.py
scripts/graphify_vs_website_editor_debug_site_rag_ollama_smoke_v5.py
```

## Adoption gate

A candidate should not replace the baseline unless it has equal or better correctness and artifact completion, no safety regression, no wrong-target increase, stable repeated results, and a meaningful operating advantage such as fewer repairs, calls, tokens, or time.

Smoke success is evidence for a contract. It is not proof of all production workloads.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
