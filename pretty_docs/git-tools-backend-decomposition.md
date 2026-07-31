# Git Tools Backend Decomposition Plan

## Status

Planning document only. No implementation is authorized by this document.

The canonical authority for upcoming MCEL code work remains
`pretty_docs/mcel-status-and-roadmap.md`. A later governance patch must authorize
one bounded Git Tools slice before production code, routes, browser behavior, or
maturity declarations change.

This plan records how the current Git Tools backend can be decomposed without
breaking the application contract. It distinguishes three separate concerns:

1. splitting `main_computer/git_tools.py` into cohesive Python components;
2. running independent read-only evidence operations concurrently;
3. changing the browser from one aggregate status request to several smaller
   versioned requests.

Those concerns must not be implemented as one rewrite.

## Current architecture

The current backend is a large but persistent service, not a script that is
started from scratch for every browser request.

- `main_computer/viewport_server.py` constructs one `GitToolsService` instance
  and keeps it on the viewport server.
- `main_computer/viewport_routes_git.py` delegates Git API handlers to that
  service.
- `main_computer/viewport_route_dispatch.py` preserves the public route surface,
  including `/api/applications/git/status`.
- `main_computer/web/applications/scripts/git-tools-status-api.js` centralizes
  browser API calls.
- `main_computer/web/applications/scripts/git-tools-semantic-adapter.js`
  translates backend evidence into the MCEL semantic state used by the app.

`main_computer/git_tools.py` is currently about 4,600 lines and combines
repository inspection, patch inventory, project workflows, commit jobs, secrets
scanning, archive workflows, console shims, operation coordination, and Local
Gitea integration.

The aggregate `git_status()` read path currently gathers repository-root,
branch, HEAD, short status, recent history, remotes, patch inventory, and
capabilities before returning one compatibility response.

File size is therefore a maintainability and test-isolation concern. The
latency opportunity comes from reducing redundant Git work and allowing
independent read-only evidence operations to run concurrently. Splitting the
file alone does not make requests parallel.

## Goals

- Reduce the responsibility and review surface of `git_tools.py`.
- Preserve current imports, routes, request bodies, response fields, error
  semantics, and MCEL behavior during extraction.
- Isolate read-only repository evidence from mutation workflows.
- Permit bounded concurrency for independent read operations after equivalence
  is proven.
- Allow individual evidence panels to fail or refresh independently in a later
  browser cutover.
- Keep every migration stage reversible.
- Preserve exact runtime and acceptance evidence before advancing to the next
  stage.

## Non-goals

This plan does not authorize:

- removing or renaming `GitToolsService`;
- changing `/api/applications/git/status`;
- changing current JSON field names or error semantics;
- browser fan-out during the first extraction;
- parallel Git mutations;
- weaker preflight, confirmation, receipt, or recovery behavior;
- raw command execution as the default user path;
- application maturity promotion;
- source mutation by MCEL Lab;
- a single patch that decomposes the entire backend.

## Compatibility contract

Until an explicitly authorized retirement stage, all of the following remain
stable:

```python
from main_computer.git_tools import GitToolsService
```

- The constructor remains compatible with existing server and test callers.
- Existing public service methods remain available.
- Existing Git routes remain supported.
- `/api/applications/git/status` keeps its request and response contract.
- The current aggregate status fields remain present:
  `ok`, `repo_dir`, `git_root`, `is_git_repo`, `has_head`, `branch`, `ahead`,
  `behind`, `dirty`, `changed_count`, `untracked_count`, `short_status`,
  `recent_commits`, `remotes`, `patching`, and `capabilities`.
- Existing invalid-path, non-repository, missing-HEAD, patch-unavailable, and
  subprocess-failure behavior remains characterized.
- `git-tools-semantic-adapter.js` continues to receive an equivalent normalized
  model.
- Mutation operations continue to run through the existing guarded operation
  boundary.

The compatibility façade is removed only after the versioned replacement has
passed deployed equivalence evidence and a separately authorized retirement
patch.

## Proposed component boundaries

The exact filenames may be adjusted during implementation, but component
responsibilities should remain cohesive:

```text
main_computer/
├── git_tools.py
└── git_tools_components/
    ├── command_runner.py
    ├── repository_evidence.py
    ├── patch_inventory.py
    ├── project_registry.py
    ├── project_workflows.py
    ├── commit_jobs.py
    ├── secrets_scanning.py
    ├── archive_workflows.py
    ├── console_shims.py
    ├── gitea_service.py
    └── operation_coordinator.py
```

`git_tools.py` remains the compatibility façade. It owns construction and
delegation while extracted components own focused behavior.

The first extraction should be `repository_evidence.py` because the status
read path is read-only, externally visible, and can be characterized without
changing mutation behavior.

## Planned read model

A later versioned API may split the aggregate status response into a small
number of coherent evidence resources:

```text
/api/applications/git/evidence/core
/api/applications/git/evidence/history
/api/applications/git/evidence/remotes
/api/applications/git/evidence/patches
/api/applications/git/evidence/capabilities
```

These paths are design targets, not implemented routes.

### Core repository evidence

The core resource should cover repository root, HEAD, branch, ahead/behind,
dirty state, changed count, untracked count, and short status. Where compatible
with current behavior, one porcelain-v2 branch-status query should replace
multiple redundant Git calls.

### Independent evidence

Recent history, remotes, patch inventory, and capabilities may be gathered
independently. A failure in one evidence source must not erase successful
evidence from the others.

### Evidence identity

Every versioned response should identify the refresh and repository state it
observed. The final schema should include at least:

```json
{
  "schema": "git-tools.repository-evidence.v1",
  "refresh_id": "generated-per-refresh",
  "repo_dir": "repository-relative-or-resolved-path",
  "git_root": "resolved-root",
  "observed_at": "timestamp",
  "revision": {
    "head_oid": "observed-head",
    "status_hash": "deterministic-status-identity"
  }
}
```

Parallel results must not be presented as one coherent snapshot when their
repository identity differs.

## Concurrency law

Read-only evidence may run concurrently after characterization and equivalence
tests pass.

Mutation operations remain serialized per repository and must rerun their own
preflight. Read evidence displayed by the browser is informative; it does not
authorize a later write.

Mutation examples include:

- writing `.gitignore`;
- staging or committing files;
- creating or switching branches;
- changing remotes;
- pushing;
- applying patches;
- archiving or moving repository files;
- starting or configuring Local Gitea;
- creating remote repositories.

A later per-repository operation coordinator may allow independent repositories
to mutate concurrently, but one repository must never have overlapping
mutations.

Mutation requests should eventually carry an expected repository revision.
When HEAD or status identity has changed since the displayed preflight, the
operation must return a stale-preflight result and require a fresh review.

## Browser refresh law

When the browser eventually requests several evidence resources, it must use a
refresh generation and cancellation boundary.

A slower earlier refresh must not overwrite a newer completed refresh.
Successful panels may update independently, but the semantic adapter must know
which results belong to the same refresh and repository identity.

`Promise.allSettled()` is appropriate for independent reads only after the
client has:

- an `AbortController` or equivalent cancellation mechanism;
- a monotonically increasing refresh generation;
- explicit loading, partial, failed, and stale states;
- compatibility normalization into the current semantic model.

## Migration slices

### Slice 1 — Characterize the compatibility response

Add fixtures and tests for clean, dirty, untracked, detached-HEAD, no-HEAD,
non-repository, remote-configured, patch-unavailable, and invalid-path cases.

No production behavior changes.

Acceptance boundary:

- the legacy aggregate response is captured structurally;
- error semantics are explicit;
- current MCEL intents and receipts remain unchanged.

Rollback: remove tests only; no runtime code has changed.

### Slice 2 — Extract repository evidence

Create the focused repository-evidence component and delegate the existing
`GitToolsService.git_status()` read operations to it.

No new routes and no frontend changes.

Acceptance boundary:

- imports and constructor remain compatible;
- legacy responses match the characterization fixtures;
- existing Git Tools tests, runtime evidence, acceptance evidence, and truth
  release gate pass.

Rollback: restore the façade method body while retaining characterization tests.

### Slice 3 — Internally parallelize independent reads

Keep the legacy status endpoint and response shape. Use a bounded executor
inside the backend to gather independent read-only evidence.

Acceptance boundary:

- completion order does not change the response;
- one failed read preserves the established compatibility behavior;
- no mutation method enters the executor;
- repeated and concurrent refresh tests are deterministic.

Rollback: return to serial component calls without changing routes or the UI.

### Slice 4 — Add versioned evidence endpoints

Add the small read-only endpoints while retaining `/status`.

Acceptance boundary:

- every endpoint has a versioned schema;
- refresh and repository identity are explicit;
- route authorization and path validation match the legacy boundary;
- the legacy endpoint remains canonical for the browser.

Rollback: remove the new route registrations; the app remains on `/status`.

### Slice 5 — Browser shadow comparison

Have the browser fetch the versioned resources without rendering from them.
Assemble and compare the shadow result with the legacy response.

Acceptance boundary:

- differences are reported as diagnostics rather than silently normalized;
- users still see the legacy proven model;
- stale refreshes cannot win;
- partial failures are observable.

Rollback: disable the shadow requests.

### Slice 6 — Browser cutover

Render from the versioned evidence model while retaining `/status` as a
fallback for one migration cycle.

Acceptance boundary:

- semantic adapter output remains equivalent;
- panels expose independent loading and failure states;
- deployed runtime and acceptance evidence remain exact;
- fallback usage is observable.

Rollback: switch the status API back to the legacy endpoint.

### Slice 7 — Compatibility retirement

Remove the legacy aggregator or façade only through a separate authorization
after deployed evidence shows no unexplained shadow differences and no fallback
use.

Acceptance boundary:

- all callers have migrated;
- no tests import removed internals;
- canonical runtime, acceptance, and truth evidence pass;
- rollback instructions identify the last compatible artifact.

## Required proof before each cutover

Every implementation slice must provide:

- touched-file inventory and a narrow artifact boundary;
- focused unit and route tests;
- compatibility-response comparison where applicable;
- concurrency tests with deliberately reordered completion;
- stale-refresh protection tests;
- mutation serialization tests;
- deployed Git Tools runtime evidence;
- Git Tools acceptance evidence;
- exact repository-bound canonical evidence;
- a passing MCEL truth release gate;
- no application maturity change unless separately authorized.

A targeted verification run must not be described as full repository proof.

## Documentation and registry boundary

This document contains no machine-readable `mcel-*` fenced blocks and does not
alter the requirements registry.

The app-level product contract remains
`pretty_docs/mcel-git-tools-requirements.md`. Project-publishing behavior remains
documented in `pretty_docs/git-tools-project-level-publishing.md`. This plan
describes backend migration mechanics only.

When a technical slice is authorized, its implementation documentation should
link back here and state which slice is active, which compatibility boundary is
preserved, and what proof closed the slice.

## Decision record

The approved direction is:

1. preserve `GitToolsService` as a stable façade;
2. characterize before extracting;
3. extract read-only repository evidence first;
4. prove equivalent serial delegation;
5. introduce bounded internal read concurrency;
6. add versioned smaller endpoints;
7. shadow-compare before browser cutover;
8. keep mutations serialized and revision-checked;
9. retire compatibility only after separate authorization and deployed proof.

This sequence permits substantial architectural change without weakening the
current Git Tools product contract or MCEL truth boundary.
