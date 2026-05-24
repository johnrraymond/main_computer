# Hub Backend/API Phased Plan

## Snapshot grounding

The current repository already contains a first hub implementation. The plan below builds on that instead of replacing it wholesale.

Current hub-related pieces:

- `main_computer/hub.py`
  - stdlib HTTP hub server and worker server
  - `HubRegistry` JSON-backed worker and upstream-hub registry
  - `HubDispatcher` request forwarding
  - high-security session relay endpoints
  - legacy plaintext chat endpoint guarded behind explicit opt-out
  - worker payout queue through the energy-credit ledger
- `main_computer/providers/hub.py`
  - client-side provider adapter that calls the hub
  - default high-security request flow
- `main_computer/hub_security.py`
  - temporary key exchange and encrypted envelope helpers
- `main_computer/config.py`
  - hub URL, node IDs, timeout, root path, security flags, and worker credit settings
- `tests/test_hub.py`
  - current coverage for worker registration, encrypted dispatch, upstream forwarding, and payout claiming
- `main_computer/viewport_server.py` and `main_computer/web/energy.html`
  - existing local configuration surface for hub settings

The next backend/API work should separate hub business logic from HTTP handlers so the hub website can call stable API endpoints while the request plexing service remains testable and reusable.

## Product goal

Create a hub backend/API that lets the hub website submit AI work, observe progress, manage worker machines, and inspect accounting without coupling the website to worker internals.

The first backend service should be the AI request plexing service: a service that accepts a normalized AI request, selects or leases an appropriate worker machine, relays the request, tracks the request lifecycle, handles retries/failures, and returns the final response or stream to the caller.

## Design principles

- Keep the hub website talking only to the hub API, never directly to workers.
- Preserve the existing high-security hub-blind envelope path by default.
- Treat legacy plaintext as a development or compatibility mode only.
- Split request plexing from transport code.
- Make every request traceable by `request_id`, `session_id`, `client_node_id`, `worker_node_id`, and model.
- Keep worker selection deterministic enough for tests but extensible for scheduling.
- Keep raw snapshot patching safe: no deletion-by-omission assumptions.
- Build narrow, testable increments over the current hub instead of a broad rewrite.

## Target backend shape

```text
hub website
  -> hub backend/api
       -> AIRequestPlexService
            -> WorkerRegistryService
            -> WorkerLeaseService
            -> WorkerTransportClient
            -> RequestStateStore
            -> EnergySettlementService
            -> HubSecurityService
       -> worker machines
```

The key new abstraction is `AIRequestPlexService`. HTTP handlers should delegate to it instead of directly selecting workers and posting JSON.

## Phase 0: API contract and service boundary

Goal: define the hub website contract and isolate the plexing boundary without changing behavior.

Deliverables:

- Add a service-level contract document or module-level docstrings for:
  - request submission
  - worker discovery
  - request status
  - cancellation
  - result retrieval
  - health/status
  - payout/accounting views
- Define stable request/response DTOs:
  - `HubAIRequest`
  - `HubAIResponse`
  - `HubRequestStatus`
  - `HubWorkerSummary`
  - `HubRequestLease`
  - `HubDispatchError`
- Keep the existing `/api/hub/*` endpoints working.
- Add versioned aliases for website-facing endpoints under `/api/hub/v1/*`.
- Add tests proving old endpoints and new aliases return compatible data.

Recommended initial website-facing API:

```text
GET  /api/hub/v1/health
GET  /api/hub/v1/status
GET  /api/hub/v1/workers
POST /api/hub/v1/workers/register
GET  /api/hub/v1/models
POST /api/hub/v1/requests
GET  /api/hub/v1/requests/{request_id}
POST /api/hub/v1/requests/{request_id}/cancel
GET  /api/hub/v1/payouts?node_id=...
POST /api/hub/v1/payouts/claim
```

Phase 0 acceptance criteria:

- Existing hub tests pass unchanged.
- New API contract tests exercise the versioned website endpoints.
- The website can load hub status and worker list without knowing worker URLs.

## Phase 1: AI request plexing service

Goal: create the service that does the core work of plexing AI requests out to worker machines.

Suggested file:

- `main_computer/hub_plex_service.py`

Suggested service interface:

```python
class AIRequestPlexService:
    def submit(self, request: HubAIRequest) -> HubRequestStatus: ...
    def dispatch_sync(self, request: HubAIRequest) -> HubAIResponse: ...
    def get_status(self, request_id: str) -> HubRequestStatus: ...
    def cancel(self, request_id: str) -> HubRequestStatus: ...
```

Initial responsibilities:

1. Normalize prompt/messages/model/client metadata.
2. Create a stable request record.
3. Select a worker from the registry.
4. Create or reuse the high-security session relay flow.
5. Forward the request to the selected worker.
6. Mark lifecycle states:
   - `queued`
   - `leasing_worker`
   - `dispatching`
   - `running`
   - `completed`
   - `failed`
   - `cancelled`
7. Queue energy-credit payout after successful completion.
8. Return response metadata that the website can render.

Initial scheduling policy:

- Prefer workers whose declared model matches the requested model.
- Fall back to any available worker only when no exact model match exists.
- Round-robin by oldest `last_seen_at`, preserving current behavior.
- Mark unreachable workers offline and retry once on another available worker.
- Fail clearly when no worker or upstream hub is available.

Phase 1 data model:

```text
HubRequestRecord
  request_id
  client_node_id
  model
  state
  created_at
  updated_at
  selected_worker_node_id
  selected_upstream_hub_node_id
  session_id
  error
  response_summary
  credits_queued
  security_mode
```

Storage for phase 1 can remain JSON-backed under `runtime/hub`, matching `HubRegistry`. Move to SQLite only after the API contract stabilizes.

Phase 1 acceptance criteria:

- `HubDispatcher.chat()` delegates to `AIRequestPlexService.dispatch_sync()`.
- The old encrypted `HubProvider` path still passes.
- A test can inspect request state before and after completion.
- Worker offline retry is covered by a targeted unit test.
- Payout queue behavior remains compatible with `tests/test_hub.py`.

## Phase 2: Website-ready request API

Goal: expose the plexing service to the hub website.

Deliverables:

- `POST /api/hub/v1/requests`
  - accepts normalized messages or prompt
  - returns `request_id`, current state, selected model, and polling URL
- `GET /api/hub/v1/requests/{request_id}`
  - returns lifecycle state, worker assignment, metadata, error, and final response when complete
- `POST /api/hub/v1/requests/{request_id}/cancel`
  - marks cancellable queued/running requests as cancelled
- `GET /api/hub/v1/workers`
  - returns public worker summaries, not secrets or raw internal envelopes
- `GET /api/hub/v1/models`
  - returns models advertised by workers and upstream hubs

Website behavior:

- Submit request.
- Poll request status initially.
- Display worker assignment, elapsed time, state, and final response.
- Render errors with actionable messages.
- Avoid exposing worker endpoint URLs unless the user is in an admin/debug view.

Phase 2 acceptance criteria:

- The website can submit and poll a non-streaming request.
- Error states are stable JSON, not traceback strings.
- Worker endpoint details are redacted from non-debug responses.
- Existing CLI hub provider behavior remains unchanged.

## Phase 3: Streaming and progress events

Goal: support long AI responses without making the website wait for one blocking HTTP response.

Recommended first transport:

```text
GET /api/hub/v1/requests/{request_id}/events
```

Use Server-Sent Events first because it is simpler than WebSockets and works well for status/result streams.

Event types:

- `request.accepted`
- `worker.selected`
- `request.started`
- `token.delta`
- `request.completed`
- `request.failed`
- `request.cancelled`
- `worker.offline`

Phase 3 acceptance criteria:

- Non-streaming clients still work.
- Website can subscribe to events and update the UI live.
- Worker disconnects are converted into `request.failed` or retry events.
- Streamed final result matches the stored final result.

## Phase 4: Worker management API

Goal: make the hub backend operationally useful for multiple worker machines.

Deliverables:

- Worker heartbeats.
- Worker capability advertisement:
  - models
  - context limits
  - supports streaming
  - supports tools
  - hardware labels
  - queue capacity
- Admin worker actions:
  - enable
  - disable
  - drain
  - forget
- Worker health snapshots:
  - last seen
  - in-flight request count
  - recent failure count
  - average latency
  - current state

Suggested endpoints:

```text
POST /api/hub/v1/workers/{node_id}/heartbeat
POST /api/hub/v1/workers/{node_id}/enable
POST /api/hub/v1/workers/{node_id}/disable
POST /api/hub/v1/workers/{node_id}/drain
DELETE /api/hub/v1/workers/{node_id}
```

Phase 4 acceptance criteria:

- Scheduler avoids disabled and draining workers.
- Offline detection does not require a failed user request.
- Website can show worker health and active capacity.

## Phase 5: Persistence upgrade and audit trail

Goal: move from simple JSON files to a durable request and accounting store.

Recommended path:

- Keep `hub_workers.json` compatibility.
- Introduce SQLite under `runtime/hub/hub.sqlite`.
- Store:
  - workers
  - worker capabilities
  - request records
  - request events
  - payout queue records
  - admin actions
- Add one migration utility that imports existing JSON state.

Phase 5 acceptance criteria:

- Restarting the hub preserves recent request history.
- Request status survives process restart for completed/failed requests.
- The payout claim flow remains auditable.
- Tests use temporary SQLite files and do not touch the developer runtime.

## Phase 6: Auth, admin safety, and deployment boundary

Goal: make the hub safe enough for a website and remote workers.

Deliverables:

- API auth modes:
  - local dev no-auth on loopback only
  - bearer token for admin endpoints
  - worker registration token
  - optional signed worker heartbeat
- CORS policy explicitly scoped to the hub website origin.
- Rate limits for request submission and worker registration.
- Redaction for prompt content in admin logs.
- Clear production rule: remote peers must use HTTPS.

Phase 6 acceptance criteria:

- Public website routes cannot register arbitrary workers.
- Admin routes reject missing/invalid credentials.
- Insecure HTTP remains limited to loopback or explicit dev-network opt-in.
- Security tests cover rejection paths.

## Phase 7: Advanced scheduling and marketplace hooks

Goal: make the hub useful across many workers and future energy-market settlement.

Potential scheduler inputs:

- model match
- queue depth
- recent latency
- failure rate
- advertised context window
- cost in credits
- worker trust score
- locality
- user preference
- request priority

Potential settlement improvements:

- signed completion receipt
- worker claim batching
- fraud/failure dispute state
- on-chain claim export
- admin reconciliation page

Phase 7 acceptance criteria:

- Scheduler decisions are explainable in request metadata.
- Settlement logic remains decoupled from request transport.
- The website can show why a worker was selected.

## Immediate implementation slice

Start with a small refactor, not a rewrite:

1. Add `main_computer/hub_plex_models.py` for DTOs.
2. Add `main_computer/hub_plex_service.py` with `AIRequestPlexService`.
3. Move worker selection and synchronous encrypted dispatch out of `HubDispatcher` into the service.
4. Keep `HubDispatcher` as a compatibility facade.
5. Add `/api/hub/v1/status`, `/api/hub/v1/workers`, and `/api/hub/v1/requests`.
6. Add tests for:
   - DTO normalization
   - exact model worker selection
   - fallback worker selection
   - unavailable worker retry
   - request lifecycle state
   - versioned endpoint compatibility

The first user-visible win should be: the hub website can call one backend endpoint to submit an AI request and then poll one backend endpoint to see where the request went and what happened.

## Out of scope for the first slice

- Replacing the stdlib HTTP server with FastAPI or another framework.
- Replacing JSON persistence with SQLite.
- Full streaming token relay.
- Public internet deployment.
- On-chain automatic settlement.
- Broad UI redesign.

Those are worthwhile later, but the first backend/API milestone should make request plexing explicit, tested, and callable from the hub website.
