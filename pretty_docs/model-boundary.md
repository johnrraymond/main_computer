# Model Boundary

Start here: [Agent and RAG Overview](agent-rag-overview.md)

The model boundary owns transport and structured-output behavior. It does not own path safety, grounding truth, artifact promotion, approval, or live mutation.

## Shared provider surface

Primary files:

```text
main_computer/models.py
main_computer/providers/base.py
main_computer/providers/ollama.py
main_computer/providers/openai_provider.py
main_computer/providers/hub.py
main_computer/chat_ai_subprocess.py
```

`main_computer/models.py` defines provider-neutral messages and responses. Provider implementations translate those values to Ollama, OpenAI, or Hub transport behavior.

## High-level operations

- **Call model** — Submit messages and return provider-neutral content plus metadata.
- **Call structured model** — Request JSON or another schema-defined payload.
- **Stream model** — Emit content deltas, timing, thinking state, and completion data.
- **Repair output** — Retry malformed JSON or invalid schemas under a bounded budget.
- **Critique output** — Use a separate stage to identify unsupported or unsafe claims.
- **Record call** — Persist provider, model, prompt hashes, timing, tokens, and lineage.
- **Classify failure** — Distinguish transport, timeout, parse, schema, grounding, policy, and task failure.
- **Validate trust claims** — Prevent model statements from becoming deterministic authority.

## Structured-output path

```text
host builds bounded prompt
→ provider call
→ raw response retained
→ JSON extraction
→ schema validation
→ deterministic policy validation
→ bounded repair when allowed
→ explicit terminal status
```

A parseable JSON object may still be unsupported, unsafe, stale, or outside scope.

## Diagnostic harnesses

```text
main_computer/rag_json_repair_smoke.py
main_computer/rag_ollama_stream_patch_smoke.py
main_computer/rag_ollama_stream_route_matrix_smoke.py
main_computer/rag_smoke_test_ollama_streaming.py
tests/test_ollama_provider.py
tests/test_chat_ai_subprocess_streaming.py
```

These exercise malformed JSON repair, Ollama generate/chat routes, streaming behavior, thinking metadata, and subprocess event translation.

## Failure taxonomy

- **Transport failure** — request could not complete.
- **Provider rejection** — provider returned an error.
- **Timeout/cancel** — operation stopped before a complete response.
- **Parse failure** — response could not be decoded.
- **Schema failure** — decoded output violates the expected shape.
- **Grounding failure** — claims do not match exact evidence.
- **Policy failure** — output requests disallowed authority or scope.
- **Task failure** — valid output did not satisfy the requested end state.

Only the last four layers can establish whether a model result is useful for the operation.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
