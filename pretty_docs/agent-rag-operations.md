# Agent and RAG Operations

Start here: [Agent and RAG Overview](agent-rag-overview.md)

This is the quick-scan public operation catalog. Operations describe product-level behavior; individual smoke scripts and helper functions are implementation details.

## Retrieval and evidence

- **Inspect target** — Read a bounded repository, site, game, project, or dataset without mutation.
- **Retrieve evidence** — Find relevant files, symbols, excerpts, logs, records, or media.
- **Route retrieval** — Choose lexical, semantic, graph, web, multimodal, or hybrid retrieval.
- **Expand retrieval** — Broaden evidence with HyDE, step-back, RAPTOR, TreeRAG, or GraphRAG.
- **Grade evidence** — Decide whether evidence is relevant, sufficient, stale, contradictory, or poisoned.
- **Disambiguate evidence** — Resolve competing symbols, repeated literals, duplicate files, or unclear targets.
- **Search external sources** — Retrieve current web evidence when local material is insufficient or stale.
- **Fuse evidence** — Merge local, graph, web, log, and model-derived evidence into one traceable bundle.
- **Compact context** — Reduce large evidence sets while preserving provenance and critical detail.
- **Analyze multimodal evidence** — Ground a result across images, logs, JSON, source, and other media.

## User-facing reasoning

- **Answer question** — Produce a grounded answer without proposing a mutation.
- **Diagnose problem** — Separate verified causes, likely causes, uncertainties, and missing evidence.
- **Plan change** — Select targets, anchors, postconditions, risks, and tests without writing.
- **Clarify request** — Ask for missing scope or intent rather than guessing.
- **Report already satisfied** — Confirm the requested end state already exists and avoid needless edits.

## Change and artifact lifecycle

- **Propose change** — Generate verified full-file end states without touching the live target.
- **Validate proposal** — Check paths, hashes, anchors, preservation rules, scope, and model claims.
- **Promote replacement** — Convert a validated proposal into complete replacement-file content.
- **Build artifact** — Package repository-relative replacement files and explicit operations.
- **Dry-run artifact** — Run `python new_patch.py <artifact.zip> --dry-run` without writing.
- **Apply approved change** — Apply only a current, approved, validated artifact.
- **Verify change** — Confirm postconditions, tests, preservation, and absence of unrelated mutation.

## Agent control

- **Route action** — Decide whether the request needs answering, diagnosis, planning, editing, clarification, or refusal.
- **Plan tool use** — Select safe tools and order their execution.
- **Run tool loop** — Execute bounded plan → tool → observe → verify cycles.
- **Run editor task** — Orchestrate retrieval, grounding, proposal, artifact, dry-run, and optional apply.
- **Run console action** — Convert natural language into validated terminal, computer, or repo-edit actions.
- **Run autonomous task** — Execute a complete operation sequence under an explicit policy.
- **Inspect run** — Return the phase, evidence, calls, repairs, artifacts, and terminal state.
- **Resume run** — Continue an interrupted run without repeating completed writes.
- **Cancel run** — Stop active work while preserving state and audit evidence.

## Byzantine and Ring 3

- **Run consensus decision** — Fan out to untrusted workers and reviewers, then select or reject deterministically.
- **Validate quorum** — Verify membership, role separation, complete visibility, lineage hashes, and configured fault bounds.
- **Reject poisoned evidence** — Detect path escapes, false authority, test weakening, and compromised candidate results.
- **Compact consensus evidence** — Expand, check, verify, fork, and reduce tainted results into a host-verifiable state.
- **Run consensus agent task** — Repeat consensus at action, planning, and edit-generation boundaries.

## Retired Graphify evaluation operations

The repository no longer retains Graphify-specific smoke or A/B harnesses. These operation names describe the retired evaluation surface, not mounted product behavior. The deterministic baseline remains the Website Builder default.

- **Build graph** — Historically extracted a scoped structural graph and source manifest.
- **Query graph** — Historically ranked relevant nodes, files, communities, and relationships.
- **Explain graph result** — Recorded why a node or file was selected.
- **Find graph paths** — Traced relationships between files, symbols, routes, imports, and assets.
- **Hydrate graph evidence** — Resolved graph selections to exact current source and hashes before any editing stage.
- **Invalidate graph** — Rejected or rebuilt stale graphs when source, scope, configuration, or Graphify version changed.

Any future graph retriever must be reintroduced behind the existing grounding, promotion, artifact, and dry-run gates rather than treated as an apply authority.

## Model boundary

- **Call model** — Make a provider-neutral request with recorded metadata.
- **Call structured model** — Request JSON or another schema-constrained response.
- **Stream model** — Emit content, timing, thinking state, and completion metadata incrementally.
- **Repair model output** — Correct malformed or invalid output under a bounded retry budget.
- **Critique model output** — Ask a separate model stage to identify unsupported or unsafe conclusions.
- **Validate trust claims** — Prevent model statements such as "verified" from becoming host authority.
- **Record model call** — Persist provider, prompt hashes, tokens, timing, lineage, and outcome.

## Evaluation and safety

- **Evaluate retrieval** — Compare correctness, evidence quality, context, calls, repairs, tokens, and latency.
- **Evaluate agent** — Compare full workflows, terminal states, artifacts, safety, and catastrophic failures.
- **Run adversarial minefield** — Exercise ambiguity, decoys, contradictions, prompt injection, and poisoned context.
- **Run hallucination probes** — Detect invented files, anchors, tests, claims, or verification.
- **Run provider diagnostics** — Test transport, streaming, timeout, JSON, thinking, and failure behavior.
- **Run contract smokes** — Verify stable boundaries without making smoke scripts the public API.

## Shared operation result

High-level operations should converge on a common envelope containing operation, status, `ok`, request/run identifiers, target scope, evidence, artifacts, warnings, errors, uncertainties, metrics, and an audit record. The exact schema may evolve, but terminal status must remain explicit.

## Provenance

- Source snapshot: `main_computer_test-20260731-105403.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
