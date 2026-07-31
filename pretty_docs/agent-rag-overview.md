# Agent and RAG Overview

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: source-inspected system guide.

## The system in four lines

```text
Retrieval finds and verifies evidence.
Agents choose and coordinate operations.
Consensus selects among untrusted AI-derived candidates.
Deterministic host code controls paths, validation, artifacts, approval, apply, and verification.
```

Main Computer does not have one monolithic "RAG feature." It has a retrieval platform, several retrieval strategies, multiple agent surfaces, generated editors, a Byzantine/Ring 3 controller, provider and repair infrastructure, and evaluation harnesses.

## Read next

| Need | Document |
| --- | --- |
| Scan the public operations | [Agent and RAG Operations](agent-rag-operations.md) |
| Understand retrieval methods | [RAG Retrieval Strategies](rag-retrieval-strategies.md) |
| Understand graph-backed retrieval | [Graphify Retrieval](graphify-retrieval.md) |
| Understand Ring 3 trust and consensus | [Byzantine RAG and Ring 3](byzantine-rag-ring3.md) |
| Understand Website, game, and code editors | [Generated Editor Systems](generated-editor-systems.md) |
| Understand natural-language console actions | [Text Console Agent](text-console-agent.md) |
| Understand large-context representations | [Context Compaction](context-compaction.md) |
| Understand image and mixed-media evidence | [Multimodal RAG](multimodal-rag.md) |
| Understand provider calls and repair | [Model Boundary](model-boundary.md) |
| Understand replacement artifacts and apply | [Agent Artifacts and Apply](agent-artifacts-and-apply.md) |
| Understand retries and recovery | [Agent Runtime and Recovery](agent-runtime-and-recovery.md) |
| Understand tests and adoption gates | [Agent and RAG Evaluation](agent-rag-evaluation.md) |
| Find implementation ownership | [Agent and RAG Module Map](agent-rag-module-map.md) |

## Status vocabulary

- **Mounted** — reached by a current route or CLI surface.
- **Shared** — reused implementation behind a mounted surface.
- **Operator harness** — executable workflow used for controlled operation or diagnosis.
- **Contract smoke** — deterministic or model-backed proof of a boundary.
- **Experimental** — evaluation code not yet the sole mounted authority.
- **Compatibility/historical** — retained because newer code still reuses seams or tests older behavior.

A filename ending in `_smoke.py` does not by itself establish status. Some smoke-named modules are imported by mounted code; others are isolated evaluation harnesses.

## Mounted and shared surfaces

- RAG-assisted thinking is mounted through `main_computer/viewport_routes_rag_assisted_thinking.py` and uses V4.
- Website Builder generated editing is mounted through `main_computer/viewport_routes_applications.py` and `main_computer/website_builder_generated_editor_pipeline.py`.
- Data god mode is exposed by `main_computer/cli.py` and delegates to the Byzantine code-agent reference path.
- Aider actions are mounted through `main_computer/viewport_routes_aider.py`.
- Provider-neutral model values and providers live under `main_computer/models.py` and `main_computer/providers/`.
- Graphify adapters and A/B scripts remain experimental evaluation surfaces.

## Non-negotiable safety boundaries

1. Scope the repository, website, game, or project before retrieval.
2. Treat model and Hub outputs as untrusted data.
3. Resolve evidence back to exact current source before editing.
4. Keep proposal, validation, promotion, packaging, dry-run, approval, apply, and verification as separate gates.
5. Use repository-relative paths and reject absolute paths or `..` traversal.
6. Do not infer deletions from omitted files in a raw replacement ZIP.
7. Do not enable fuzzy overwrite unless the operator explicitly supplies `--allowfuzz`.
8. Use external `debug-*` website fixtures for model-backed Website Builder tests.
9. A successful provider call is not proof of grounding, artifact correctness, apply success, or runtime correctness.
10. Consensus may authorize the next phase, but only deterministic host code may mutate state.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
