# Agent Runtime and Recovery

Start here: [Agent and RAG Overview](agent-rag-overview.md)

The runtime keeps long-running model and tool work observable, cancellable, resumable, and separated from mutation authority.

## High-level operations

- **Start run** — Create a run identifier, policy snapshot, target scope, and audit record.
- **Inspect run** — Return current phase, terminal status, evidence, calls, repairs, and artifacts.
- **Stream run events** — Emit request, wait, content, tool, repair, artifact, and terminal events.
- **Cancel run** — Stop active provider or tool work without silently undoing completed writes.
- **Resume run** — Continue only resumable phases and preserve original lineage.
- **Retry phase** — Repeat a failed phase under explicit limits.
- **Recover after restart** — Reconstruct state from persisted records and reject stale targets.
- **Finalize run** — Write one explicit terminal result and retention metadata.

## Mounted and reference components

```text
main_computer/chat_ai_subprocess.py
main_computer/viewport_routes_rag_assisted_thinking.py
main_computer/rag_assisted_thinking_v4.py
main_computer/rag_code_edit_agent_guidance_smoke.py
```

`chat_ai_subprocess.py` owns subprocess lifecycle, provider event translation, cancellation, and logs for mounted chat/RAG work. The code-edit guidance harness exercises long-running agent steering, restart probes, Ring 3 calls, and recovery-shaped reports.

## Run phases

A general run may include:

```text
intake
→ scope
→ retrieval
→ model or consensus stage
→ deterministic validation
→ repair
→ proposal
→ artifact
→ dry-run
→ approval
→ apply
→ verification
→ terminal result
```

Not every operation uses every phase.

## Resume rules

- Preserve request, policy, scope, source hashes, and prior successful outputs.
- Do not repeat an apply or commit merely because a process restarted.
- Recheck target hashes before resuming edit, artifact, or apply stages.
- Give each retry a new attempt identifier linked to the original phase.
- Enforce bounded retry and repair counts.
- Mark non-resumable states explicitly.

## Audit record

A run record should retain event times, model/provider metadata, prompt and evidence hashes, selected paths, validator outcomes, rejected candidates, repair attempts, artifact hashes, approval data, apply results, verification results, warnings, and terminal status.

Audit summaries should explain inputs, observations, decisions, uncertainties, and next state without storing private model chain-of-thought.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
