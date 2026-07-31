# Agent and RAG Module Map

Start here: [Agent and RAG Overview](agent-rag-overview.md)

This map identifies current ownership. Status is based on source imports, routes, CLI surfaces, and smoke/test roles in the inspected snapshot.

## Mounted and shared operations

| Area | Mounted entry | Shared implementation | Status |
| --- | --- | --- | --- |
| RAG-assisted thinking | `main_computer/viewport_routes_rag_assisted_thinking.py` | `rag_assisted_thinking_v4.py`, reused V2/V3 seams, `chat_ai_subprocess.py` | mounted/shared |
| Deterministic repository RAG | callers and harnesses | `rag_retriever.py`, `rag_harness.py`, `thinking_models.py`, `rag_activity.py` | shared |
| Website generated editor | `viewport_routes_applications.py` | `website_builder_generated_editor_pipeline.py`, Website/RAG helpers | mounted/shared |
| Data god mode | `main_computer/cli.py` | `rag_code_edit_agent_guidance_smoke.py`, Hub provider | mounted CLI/reference |
| Aider actions | `main_computer/viewport_routes_aider.py` | `aider_agent.py`, `aider_web_context.py` | mounted/shared |
| Provider transport | route and CLI callers | `models.py`, `providers/base.py`, `providers/ollama.py`, `providers/openai_provider.py`, `providers/hub.py` | shared |

## Retrieval and evaluation modules

| Module | Role | Status |
| --- | --- | --- |
| `main_computer/rag_quality_layer_smoke.py` | lexical, synonym, diversity, packing, contradiction, HyDE gates | contract smoke |
| `main_computer/rag_agentic_retrieval_loop_layer_smoke.py` | step-back, routing, tool retrieval, web fallback, Self-RAG | contract smoke |
| `main_computer/rag_advanced_eval_layer_smoke.py` | RAPTOR, TreeRAG, GraphRAG, evaluation metrics | contract smoke |
| `main_computer/rag_hyde_ollama_docker_smoke_v3.py` | model-backed HyDE evaluation | experimental/operator |
| `main_computer/rag_minefield_ollama_docker_smoke.py` | planner, grader, disambiguator, answer, and critic minefield | experimental/operator |

### Retired Graphify evaluation surface

No Graphify-specific smoke or A/B script remains in this snapshot, and no mounted or shared Python module imports Graphify. The deterministic baseline remains the Website Builder discovery default. `requirements.txt` still carries the `graphifyy==0.9.29` package pin pending a separate dependency-cleanup decision.

## Generated editor ownership

| Boundary | Modules |
| --- | --- |
| Website scope/staging/index | `website_builder_generated_editor_pipeline.py`, `website_builder_rag_pipeline.py` |
| Discovery and promotion | `rag_generated_editor_discovery_grounding_smoke.py` |
| Grounding and patch validation | `rag_generated_editor_claim_grounding_smoke.py` |
| Terminal result | `rag_terminal_result_contract.py` |
| Terminal artifact | `rag_terminal_artifact_contract.py` |
| Website operator/golden path | `rag_chat_website_builder_operator_smoke_v5.py`, `rag_debug_website_golden_path_smoke.py` |
| Game editor | `rag_chat_game_editor_operator_smoke.py`, `rag_game_editor_golden_path_smoke_short.py`, `rag_game_editor_real_edit_smoke.py` |
| Gremlin/code edit | `gremlin_rag_smoke.py`, `rag_gremlin_action_smoke.py`, `rag_gremlin_pyramid_atom_smoke.py` |

## Agent and consensus ownership

| Boundary | Modules |
| --- | --- |
| Ring 3 poisoning/compaction | `rag_code_edit_agent_guidance_smoke.py` |
| God-mode CLI and Hub wiring | `cli.py`, `providers/hub.py` |
| Long-running subprocess events | `chat_ai_subprocess.py` |
| Text-console actions | `rag_text_console_control_surface_smoke.py`, `rag_text_console_operator_action_rag_smoke.py` |
| Context clobs | `rag_text_console_clob_v2_smoke.py` |
| Agent shape | `agent_shape_smoke.py` |
| Aider | `aider_agent.py`, `viewport_routes_aider.py`, `aider_web_context.py` |
| ECC workflow | `main_computer/ecc_workflow.py`, `tools/ecc_workflow.py` |

## Context, multimodal, and transport experiments

```text
main_computer/rag_smoke_logpack_ollama.py
main_computer/rag_smoke_logpack_fast_ollama.py
main_computer/rag_smoke_logpack_compact_ollama.py
main_computer/rag_smoke_xel_taguchi.py
main_computer/rag_smoke_ahbe_ai.py
main_computer/rag_smoke_ahbe_ai_finetuned.py
main_computer/rag_gemma4_image_recognition_smoke.py
main_computer/rag_profile_space_latest_png_rag_smoke.py
main_computer/rag_json_repair_smoke.py
main_computer/rag_ollama_stream_patch_smoke.py
main_computer/rag_ollama_stream_route_matrix_smoke.py
```

These are experimental or diagnostic unless another mounted module explicitly imports them.

## Focused tests

```text
tests/test_rag_retriever.py
tests/test_rag_harness.py
tests/test_rag_smoke_framework.py
tests/test_rag_assisted_thinking_v4.py
tests/test_rag_assisted_thinking_route.py
tests/test_chat_ai_subprocess_streaming.py
tests/test_ollama_provider.py
tests/test_debug_website_golden_path_no_deterministic_cheats.py
tests/test_website_builder_app.py
tests/test_rag_code_edit_agent_guidance_smoke.py
tests/test_data_god_mode_cli.py
tests/test_aider_agent.py
tests/test_agent_shape_smoke.py
tests/test_text_console_operator_action_rag_smoke_clean.py
```

## Compatibility note

Versioned RAG-assisted-thinking modules and older smoke variants are not automatically dead code. V4 imports and reuses earlier seams. Remove or relabel them only after import and test evidence proves they are no longer required.

## Provenance

- Source snapshot: `main_computer_test-20260731-105403.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
