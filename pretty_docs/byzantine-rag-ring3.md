# Byzantine RAG and Ring 3

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: mounted CLI/reference pathway plus deterministic and live-provider contract harnesses.

## Trust model

Every worker, reviewer, verifier, merge result, and Hub response is untrusted. No individual AI result receives mutation authority.

```text
host-owned prompt, scope, evidence, and constraints
→ untrusted worker fanout
→ separate reviewer fanout with complete worker visibility
→ host validation of records, membership, lineage, and policy
→ deterministic host selection or rejection
→ selected result authorizes only the next phase
```

Consensus is not apply authority.

## High-level operations

- **Run consensus decision** — Collect worker and reviewer candidates, then select or reject deterministically.
- **Validate quorum** — Check configured membership, observed membership, role separation, complete visibility, and hashes.
- **Reject poison** — Reject path traversal, forbidden files, test weakening, shell authority, false verification, and constraint overrides.
- **Compact evidence** — Expand inquiry/check/verify/merge/fork evidence and collapse it into one host-verifiable state.
- **Run consensus agent task** — Repeat consensus independently for action selection, planning, and edit generation.
- **Resume/recover** — Preserve phase lineage and avoid replaying completed mutation steps after interruption.

## Ring 3 poisoning path

`main_computer/rag_code_edit_agent_guidance_smoke.py` models poisoned candidates and validates each one before selection. The host may continue only when a unique policy-valid result survives.

Key implementation names include:

```text
Ring3PoisoningConsensusAgent
ring3_worker_results_for_scenario
validate_ring3_worker_result
select_ring3_consensus_candidate
run_ring3_poisoning_consensus_apply
```

## Evidence expansion and compaction

The evidence-compaction path performs bounded inquiry, check, verify, merge, fork, observation, and host compaction. Verifiers may also be compromised, so their claims are mined for evidence rather than trusted.

Key implementation names include:

```text
Ring3EvidenceCompactionAgent
ring3_inquiry_results_for_scenario
ring3_check_packets
ring3_verify_results_for_check
ring3_merge_results_from_verify
fork_and_observe_ring3_candidate
run_ring3_evidence_compaction_apply
```

## Data god mode

`main_computer/cli.py` exposes the full reference pathway through `main-computer data ... --god-mode`. The CLI applies minimum worker/reviewer counts, can use a Ring 3 Hub worker pool, and reports whether the full Byzantine reference path completed.

Each AI-derived phase collapses before the next phase:

```text
action consensus
→ host action boundary
→ planning consensus
→ host planning boundary
→ editor consensus
→ host editor boundary
→ static preflight
→ sandbox
→ host apply
→ verification
→ commit boundary
```

## Fault-model limit

This is a repository-specific Byzantine reference contract with explicit configured counts and modeled fault bounds. Documentation and reports must not describe it as a universal proof of Byzantine fault tolerance.

## Commands

```powershell
python -m main_computer.rag_code_edit_agent_guidance_smoke --ring3-poisoning-smoke
python -m main_computer.rag_code_edit_agent_guidance_smoke --ring3-evidence-compaction-smoke
main-computer data "inspect this task" --god-mode --agent
```

## Focused tests

```text
tests/test_rag_code_edit_agent_guidance_smoke.py
tests/test_data_god_mode_cli.py
```

These cover poisoning, evidence compaction, worker/reviewer contracts, Ring 3 metadata, CLI option handling, Hub integration, and reference-path reporting.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
