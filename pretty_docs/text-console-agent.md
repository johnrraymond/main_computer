# Text Console Agent

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: operator and contract-smoke surfaces for natural-language terminal and repo-action workflows.

## Purpose

The text-console agent converts a user request into a bounded assistant response containing normal prose plus validated action mounts. It separates model interpretation from trusted execution.

## High-level operations

- **Decode action** — Convert fuzzy language into a canonical action or request clarification.
- **Retrieve capability specs** — Select only the action documents relevant to the current request.
- **Emit computer mounts** — Place exact `/act` commands inside fenced `computer` blocks.
- **Emit repo-edit handoffs** — Produce a structured edit handoff without applying it.
- **Validate mounts** — Parse, limit, and safety-check every emitted command.
- **Reuse terminal context** — Preserve prior turns and selected tool results for follow-up requests.
- **Create clobs** — Side-load large outputs and pass compact references instead of full blobs.
- **Execute approved action** — Let the trusted broker run only a validated canonical action.

## Contract boundary

```text
user text
→ action-spec retrieval
→ model response
→ trusted parser
→ paranoia and plan limits
→ action/plan identifiers
→ explicit execution boundary
```

The model may suggest an action. The broker decides whether the action grammar, scope, and policy permit execution.

## Main harnesses

```text
main_computer/rag_text_console_control_surface_smoke.py
main_computer/rag_text_console_operator_action_rag_smoke.py
main_computer/rag_text_console_clob_v2_smoke.py
tests/test_text_console_operator_action_rag_smoke_clean.py
```

`rag_text_console_control_surface_smoke.py` exercises exact `/act` commands, AI-decoded natural language, broker-minted mounts, terminal paranoia, and context reuse.

`rag_text_console_operator_action_rag_smoke.py` verifies action-spec preflight, threaded follow-ups, `computer` mounts, and `repo-edit` handoffs without executing or applying them.

`rag_text_console_clob_v2_smoke.py` verifies that large repository trees or tool outputs are stored as side-loaded clobs and are not pasted wholesale into later model context.

## Safety rules

- Exact commands execute only through the canonical grammar.
- Natural language always passes through validation before execution.
- A fenced command is not approval.
- Tool outputs should be referenced by bounded handles when large.
- Repository edits use the generated-editor/artifact path rather than direct shell mutation.
- Prompt text, terminal output, and retrieved capability docs remain distinct provenance classes.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
