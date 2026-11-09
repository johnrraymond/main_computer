# Secure Local Hub (SLH) — Backend Architecture and Current Capabilities

**Document purpose:** A source-grounded description of the existing SLH backend for operators, application authors, and maintainers. This document describes *the system*, not a proposed interface, screen layout, or user-workflow specification.

**Source baseline:** `main_computer_test-20261107-184414(1).zip` (export named November 7, 2026). Audited November 8, 2026. Later repository changes and actual Windows service configuration are **not** inferred from this export. [Local implementation](../main_computer/slh/) is the primary source of truth for implemented behavior; older design manuals are historical references.

**Evidence terminology**

- **Implemented in source:** An executable path exists and its conditions can be inspected. This does **not** mean it is running, configured, or independently accepted on a deployed machine.
- **Conditional:** Implemented but available only when a distinct service, authorized principal, configured wallet, Worker, Hub, or SEH is actually available.
- **Stub:** The route is registered but explicitly returns `NOT_IMPLEMENTED` / 501, after applicable access checks.
- **Target / not established:** Described by an earlier document, but no matching end-to-end implementation is established by this source inspection.
- **Observed:** Requires a fresh live query or accepted operational report; it is not interchangeable with **configured** or **enabled**.

## 1. System purpose and boundaries

**Governing identity ownership:** [LMS-SLH Unified Identity Contract](lms-slh-identity-ownership.md). **LMS-SLH**, the loopback frontend/local-management SLH service, owns a **single identity model and lifecycle** for the installation default and all locally held identities, regardless of whether they are described as machines or users. The **backend SLH Organization Service** owns their organization-specific enrollment, public-key registry, roles and access decisions. The viewport does not own identities. This is the intended boundary; the current local Authenticator APIs exist but automatic creation of a default installation identity is still **[TARGET]**, not a reported implemented feature.

An SLH is a locally controlled organization-compute authority. It maintains enrolled device identities and authorization, supervises an optional organization-facing service, admits private requests to authenticated Workers, schedules eligible capacity between local and public use, optionally participates in the Stable Hub marketplace under organizational authorization, and maintains local accounting obligations for Pinky Promise. The SEH is an **external relay**, not another SLH administrator or wallet authority.

Keep these principals distinct:

| Principal | Authority or responsibility | Important separation |
| --- | --- | --- |
| LMS-SLH frontend/local-management service | Owns this installation's and its local users' identities: creation, discovery, local custody, authorized signing; also owns local setup/service supervision | Does not enroll itself as organization administrator or serve the public Organization API |
| Main Computer installation / local authority | Owns local bootstrap and service configuration, organization-domain registration and supervisory lifecycle | Local installation identity is distinct from the organization Known Device registry |
| Administrator | Authenticated changes to enrollment, policy, service lifecycle, marketplace configuration, and accounting controls | A device credential is not a wallet spending key |
| Known Device / user | Enrolled public-key identity and active role; may be Worker, requester or administrator | Device enrollment does not grant universal administrative authority |
| Worker | Executes authenticated authorized computation and advertises supported models/capabilities | Does not receive the organizational marketplace wallet private key or MSK |
| Requester | Authenticated originator of private computation, with access to its own request status | Cannot administer the organization merely by being known |
| Organization Service | Separate, explicitly enabled TLS network service for Worker, request and marketplace operations | Does not expose local bootstrap/control endpoints over the network |
| Stable Hub | Authoritative public-work admission/settlement and credit-account balances | A local snapshot is not settled-credit authorization |
| SEH | Remote outbound-connected relay/rendezvous transport | Is not the SLH's identity database or private-job authority |
| Main Computer Reserve | Configured recipient of signed aggregate Pinky Promise transfers in the Hub credit ledger | A Hub-ledger receipt is not external blockchain settlement |

The current authority model is **one local organization**. The implementation removes old synthetic `org_id` database scope from SLH identity, private requests, and wallet binding; the application must not invent a multi-organization selector merely because older manuals contain organization IDs. See [`authority.py`](../main_computer/slh/authority.py), [`private_requests.py`](../main_computer/slh/private_requests.py), and [`organization_wallet.py`](../main_computer/slh/organization_wallet.py).

## 2. Runtime topology and ownership

```text
Main Computer viewport, usually 127.0.0.1:8765  [existing host; not SLH API]
           |
           | MCEL SLH Overview / authenticated Worker Bank (first slice)
           v
SLH Local Service -- HTTP 127.0.0.1:8897, loopback only
    |       durable identity, enrollment, admin API, local lifecycle
    |       explicitly enables/supervises separate child process
    v
SLH Organization Service -- TLS; configured API port (default 8899)
    |                      authenticated organizational API
    +-- Worker gateway -- TLS/WSS; configured port (default 8898)
    |      +-- managed Workers / private computation
    |      +-- scheduler / organization-local reserve
    |      +-- automatic Stable Hub marketplace sessions
    +-- Organization wallet read-only Hub observations
    +-- Private request and Pinky Promise local journal
    +-- Periodic Reserve sweeper, when properly authorized
    +-- Optional outbound authenticated SEH connector
                +-- SEH relay / public network
```

**Control process.** [`local_service.py`](../main_computer/slh/local_service.py) binds exclusively to `127.0.0.1:8897`, publishes `/v1/local/status`, creates the local authority, and owns an [`OrganizationController`](../main_computer/slh/organization_controller.py). It refuses silent migration from old state locations. A successful port bind is required before it announces listening. The organization config initially exists in a **disabled** state.

**Organization process.** [`organization_service.py`](../main_computer/slh/organization_service.py) is a separate TLS-only server. Startup requires administrator bootstrap, explicit organization configuration, `enabled`, `activation_confirmed`, and a live local parent controller. Its parent-watch logic shuts down the child if the controlling local process disappears. Worker serving is a separately configured feature. The controller monitors/restarts an explicitly enabled child, subject to a restart limit; this is not permission to auto-start SLH at Windows boot.

**Port defaults.** [`ports.py`](../main_computer/slh/ports.py) sets local API 8897 and managed Worker 8898. The organization API defaults to managed port + 1 (8899). The organization configuration can explicitly select host and ports; a configured `0.0.0.0` listener is not evidence that external reachability, firewall scope, or identity trust has been tested.

**Installation policy on this Windows development machine:** SLH is not to be installed as an automatic Windows-boot resident task. On-demand service testing is distinct from boot policy. Never create a boot task as part of documenting or testing the SLH app.

## 3. Durable state and source of truth

| Data | Implementation authority | Notes |
| --- | --- | --- |
| Machine-owned SLH directory | `runtime/state/slh` via [`storage.py`](../main_computer/slh/storage.py) | Current canonical local installation state; guarded against silently replacing an older authority |
| Enrollment, users, devices, policies, audit | `organization.sqlite3`, [`authority.py`](../main_computer/slh/authority.py) | Process-local bearer sessions are **not** durable |
| Organization service enablement | `organization-service.json`, [`organization_state.py`](../main_computer/slh/organization_state.py) | Initially disabled; local controller owns mutation |
| Scheduler configuration/runtime snapshot | SLH authority SQLite, [`scheduler_policy.py`](../main_computer/slh/scheduler_policy.py) | Persistent policy vs time-limited last runtime observation |
| Wallet binding and observed balance | Organization wallet SQLite, [`organization_wallet.py`](../main_computer/slh/organization_wallet.py) | Stable Hub is authoritative for current spendable balance |
| Private requests, assessments and sweeps | `private_requests.sqlite3`, [`private_requests.py`](../main_computer/slh/private_requests.py), [`pinky_sweep.py`](../main_computer/slh/pinky_sweep.py) | Local liabilities and receipts; not a competing Hub-credit ledger |
| Managed Worker connection/reservation state | In-process [`managed_gateway.py`](../main_computer/slh/managed_gateway.py) | Live snapshot disappears when the service is not running |
| Marketplace settings/authorization | Files in machine SLH state, [`marketplace_auto.py`](../main_computer/slh/marketplace_auto.py) | Do not expose MSK or authorization material through status/diagnostics |
| Service signing identity and TLS leaf | Protected machine state; [`storage.py`](../main_computer/slh/storage.py), [`managed_gateway.py`](../main_computer/slh/managed_gateway.py) | No organizational certificate authority; identity replacement must be explicit |
| Unified LMS-SLH identities (including the default) | One identity format and create/list/get/sign lifecycle; current storage is per-OS-user [`authenticator_store.py`](../main_computer/slh/authenticator_store.py) | Installation default creation/reuse is **[TARGET]**, not implemented; no identity-type division between machines and people. Storage/ACL boundaries still protect private keys; current `protection: none`. |
| Default identity association | **[TARGET]** persistent installation-scoped selection of an ordinary identity | Different installations have different defaults; being default grants no organization privilege |
| Canonical SEH deployment binding | Existing Mother/Coolify deployment state resolved by [`seh_canonical_connection.py`](../main_computer/slh/seh_canonical_connection.py) | Neither Coolify nor SEH is allowed to invent a replacement SLH identity |

The SLH's SQLite databases are **local SLH domain records**, not a replacement for Mother identity authority, the external marketplace ledger, or the Stable Hub's credit settlement.

## 4. Device identity, authentication and administration

**LMS-SLH identity boundary — implemented versus target.** [`local_service.py`](../main_computer/slh/local_service.py) mounts `LocalAuthenticatorAPI`, providing `GET /v1/local/authenticator/status`, `GET`/`POST /v1/local/authenticator/identities`, `GET /v1/local/authenticator/identities/{device_id}` and `POST /v1/local/authenticator/identities/{device_id}/sign`. The current `POST` requires an explicit identity ID, and local credentials are user-scoped and unencrypted at rest (with file/ACL restrictions); **automatic selection/creation of an ordinary LMS-SLH default identity and user-safe multi-OS-user key custody are not implemented**. These are lifecycle/access concerns, not separate machine and user cryptographic systems. The Organization Service validates enrolled public keys; it does not generate or hold these users' local private keys.

**Implemented:** P-256 device signing keys and challenge verification; the SLH persists public keys/fingerprints, not device private keys. Enrollment uses an administrator-reviewed queue. First-administrator setup is loopback-only and guarded by a locally protected bootstrap secret. The source includes a staged bootstrap transaction and explicit P-256 activation path; the older direct bootstrap endpoint remains in the route table. Device challenge is fresh and single use, and bearer sessions expire after 15 minutes and on process restart. Sources: [`authenticator.py`](../main_computer/slh/authenticator.py), [`authority.py`](../main_computer/slh/authority.py), [`api.py`](../main_computer/slh/api.py).

**Enrollment paths:** A device signs its request; an authorized administrator either approves with an active admin session or issues a request-scoped, expiring, single-use enrollment key. Both paths use one approval engine. Optional physical-inspection policy requires administrator attestation; it is **not** automatic proof of presence. Requests remain unauthorized until approval.

**Available administration:** Read devices/users/enrollment queue/audit, inspect and deny enrollment, issue enrollment keys, edit device name/notes, revoke a device, and update physical-inspection policy. Revocation is checked during privileged authentication; revoking the last active administrator is protected. Real role boundaries distinguish `admin`, `worker`, and `requester`. See [`authority.py`](../main_computer/slh/authority.py) and [`api.py`](../main_computer/slh/api.py).

**Not established:** General first-admin recovery (`/v1/bootstrap/recovery`) and personal-authenticator challenge routes (`/v1/auth/people/*`) remain 501 stubs. Do not present a phone-based individual MFA or automated credential-recovery flow as live merely because it appears in older target specifications.

## 5. Organization Service control and observable health

The administrator-controlled local API supports:

| Action or observation | Real local API | Backend behavior |
| --- | --- | --- |
| Read local SLH status | `GET /v1/local/status` | Public loopback-only observation with bootstrap and organization state |
| Read organization service summary | `GET /v1/local/services` | Administrator; reports child status and local service |
| Read/change child configuration | `GET/PATCH /v1/local/organization/config` | Administrator; cannot reconfigure an enabled service |
| Enable/disable/restart child | `POST /v1/local/organization/{enable,disable,restart}` | Administrator; explicit lifecycle mutation |
| Inspect SEH state / request activation | `GET /v1/local/seh/status`, `POST /v1/local/seh/activate` | Administrator; state is not equivalent to tunnel verification |
| Read service-side operational observations | `GET /v1/org/status` on TLS organization API | Process, Worker-serving, outbound SEH and sweeper indicators; not authorization to administer |
| Read service-side admin summary | `GET /v1/org/admin/services` | Authenticated administrator on TLS organization API |

`OrganizationController.status()` distinguishes `not_configured`, `disabled`, `starting`, `running`, `failed`, `stopped_unexpectedly`, and `configuration_error`, plus listening and error evidence. The source also distinguishes SEH `enabled`, startup attempts, connection errors, and independently **verified** outbound connection state. A UI consumer should not reduce these to one ambiguous green/red flag.

Source: [`organization_controller.py`](../main_computer/slh/organization_controller.py), [`organization_service.py`](../main_computer/slh/organization_service.py), [`local_service.py`](../main_computer/slh/local_service.py).

## 6. Worker Bank, real Worker sessions, and scheduling

**Implemented under the enabled Worker service:** A TLS-pinned managed-worker gateway at `/v1/managed/workers/live-session` authenticates enrolled Workers, registers supported models and operations, and maintains process-local connections, reservations, private capacity claims, public advertisements, and withdrawal status. [`ManagedScheduler.snapshot()`](../main_computer/slh/managed_gateway.py) exposes device ID, model list, reservations, availability, public-advertised state, withdrawal status, claimed private capacity, generation, and reservation types. This live snapshot is **not** the same as the durable device registry.

**Scheduler policy** is versioned in SQLite and changed with an `expected_revision` concurrency check. The current defaults, from [`scheduler_policy.py`](../main_computer/slh/scheduler_policy.py), are:

| Policy field | Default | Meaning / qualification |
| --- | --- | --- |
| `mode` | `private_first` | Also supports `percentage`, `round_robin`, `dedicated` |
| `max_public_percent` | `80` | Usual ceiling for publicly advertised capacity when funded |
| `min_organization_percent` | `20` | Minimum organization-retained share when local credit reserve is satisfied |
| `min_private_idle_percent` | `0` | Separate optional idle floor, not synonymous with reserved organization capacity |
| `local_credit_reserve_credits` | `1` | Fresh verified wallet credits needed for funded local-reserve behavior |
| `rotation_seconds` | `300` | Relevant to round-robin placement |
| `dedicated_public`, `dedicated_private` | Empty | Explicit device assignment sets |
| `credit_cap` | `null` | A non-null cap currently pauses new public advertising without a verified cap meter |
| `credit_period` | `day` | Configurable period; do not imply an active earnings-cap engine |

**Scheduling nuance:** When the reserve is not verified/satisfied, the scheduler can make eligible idle Workers available for public **credit recovery**, including a single Worker; this overrides the normal default 80/20 standby ceiling, but not an administrator-customized public cap or already busy/dedicated private Workers. Existing authorized public jobs are not preempted. A private request is separately gated on eligible Worker capacity and current verified credits; an unverified balance does not authorize private work. [`compute_plan()`](../main_computer/slh/scheduler_policy.py) is the decision authority, not an approximate display calculation.

**Observability:** `GET /v1/admin/scheduler/policy` returns durable configuration. `GET /v1/admin/scheduler/status` returns policy plus a persisted last runtime report. Runtime status is `NOT_OBSERVED`, `OBSERVED`, or `STALE` (the current freshness threshold is 10 seconds), and this loopback API's `credit_meter` summary is a placeholder, **not** a live balance feed. The organization API's marketplace status can expose a live Worker snapshot while the Worker service is running. Admin connection diagnostics are available at `GET /v1/org/admin/marketplace/connection-diagnostics` with an optional bounded device filter.

**Important non-equivalence:** `GET /v1/workers`, `POST /v1/workers/register`, and `POST /v1/workers/heartbeat` in the generic `/v1` route table are still **501 stubs**. Real Worker management happens through the managed gateway and Organization Service paths, not those generic endpoints.

Sources: [`managed_gateway.py`](../main_computer/slh/managed_gateway.py), [`scheduler_policy.py`](../main_computer/slh/scheduler_policy.py), [`organization_service.py`](../main_computer/slh/organization_service.py).

## 7. Private requests and supported execution

When Worker serving is enabled, the Organization Service creates [`PrivateRequests`](../main_computer/slh/private_requests.py) and binds the scheduler's credit provider to a net-of-commitments wallet observation. `POST /v1/work` and `GET /v1/work/{id}` are implemented for an authenticated principal with the **requester** role; they are not general administrator job endpoints.

The private-request path validates request IDs, models, operation contracts, supported Worker capacity, input sizes, and available credit before admission. It supports model-chat operations and explicitly contracted native NanoJev CLEF operations; it rejects unsupported operation shapes and simple echo as a substitute for genuine private work. Admission, dispatched execution, success, failure and uncertain post-dispatch outcomes are recorded durably. Repeating a request ID with the same payload returns the existing record rather than re-executing; reusing the ID for different work is refused.

The persisted ledger stores request/result hashes and status metadata, **not private prompts or Worker responses**. A successful fulfillment and its Pinky Promise obligation commit in one local transaction. After a crash, a previously dispatched job may be `OUTCOME_UNKNOWN`; it must not be silently replayed or billed as successful.

Sources: [`private_requests.py`](../main_computer/slh/private_requests.py), [`managed_gateway.py`](../main_computer/slh/managed_gateway.py), [`model_execution_contract.py`](../main_computer/model_execution_contract.py).

## 8. Public marketplace, organization wallet and credits

**Automatic marketplace controller.** The Organization Service owns [`AutomaticMarketplace`](../main_computer/slh/marketplace_auto.py) alongside the real Worker gateway. Its administrator endpoints are:

| Endpoint | Function |
| --- | --- |
| `GET /v1/org/admin/marketplace/status` | Sessions, live Worker scheduler snapshot, automatic controller summary |
| `GET/PATCH /v1/org/admin/marketplace/config` | Read/change enablement, Hub origin and interval with revision check |
| `POST /v1/org/admin/marketplace/authorization` | Record approved multisession authorization |
| `POST /v1/org/admin/marketplace/wallet/bind` | Bind an existing verified wallet/account/Hub identity |
| `POST /v1/org/admin/marketplace/reconcile` | Trigger authorized reconciliation; not an override for disabled participation |
| `GET /v1/org/admin/marketplace/connection-diagnostics` | Bounded, nonsecret Worker/relay connection evidence |

The controller only runs when the Worker-serving organization process is active. It reconciles public logical sessions with scheduler decisions, drains stale public sessions on disable/Hub or key changes, and does not interrupt an already authorized paid job. Workers do not get the organization MSK or wallet signer. The generic `GET /v1/marketplace` and `POST /v1/marketplace/workers` remain 501 stubs.

**Wallet observation.** [`OrganizationWallet`](../main_computer/slh/organization_wallet.py) binds one verified Stable Hub, account, wallet address and MSK digest. It checks expected Hub/cluster/chain identity and transport trust, then retrieves the Hub's balance. `GET /v1/wallets` and `GET /v1/credits` return **read-only** organization-wallet views; `POST /v1/accounting/reconcile` refreshes them. They are conditional on the Organization Service supplying its wallet backend. If unbound or stale, the consumer must not manufacture a zero balance or assume spending permission. Even a `CURRENT` observation returns `spendable_authorized: false`: debit authority belongs to an independently authorized, current Hub transaction.

Source: [`marketplace_auto.py`](../main_computer/slh/marketplace_auto.py), [`organization_wallet.py`](../main_computer/slh/organization_wallet.py), [`organization_service.py`](../main_computer/slh/organization_service.py).

## 9. Pinky Promise — current policy and settlement boundary

**The active implementation in this snapshot is the V2 economic rule, not the original fixed 0.1-credit rule.** [`pinky_promise.py`](../main_computer/slh/pinky_promise.py) identifies policy `local_reference_20_percent_v2`; [`ManagedScheduler.private_quote()`](../main_computer/slh/managed_gateway.py) selects an eligible Worker's normal marketplace price and assesses **20% of that price**, rounded up in credit minor units. At a quoted **1 credit**, the assessment is **0.2 credit**. An older completed request or older legacy policy may retain a historical **0.1-credit** assessment. Never revise old obligations retrospectively.

An administrator can inspect and change the enabled policy through `GET/PATCH /v1/admin/pinky-promise/policy`, with expected revision and explicit acknowledgement when disabling future assessments. Existing obligations persist. Each successful eligible private request records one liability atomically with fulfillment; pending, failed and uncertain outcomes are not successful billable completions.

**P4 sweeper.** [`PinkySweep`](../main_computer/slh/pinky_sweep.py) starts with the Worker-serving Organization Service, attempts a sweep at startup and every 300 seconds, and offers admin status / an explicitly requested sweep through `GET /v1/accounting/sweeps` and `POST /v1/accounting/sweep`. `GET /v1/accounting/obligations` returns a bounded liability view. The sweeper requires an operator-verified `MAIN_COMPUTER_RESERVE_WALLET_ADDRESS` and protected `SLH_PINKY_SWEEP_SIGNER_FILE` corresponding to the bound organization wallet. It batches unpaid liabilities (up to 1,000 items), prioritizes a previously pending sweep, and retries with a stable sweep identity; confirmation requires an authoritative Hub credit-ledger receipt.

No signer, wrong wallet, insufficient settled credits, stale balance, or unavailable Hub means the debt remains outstanding. The local sweep journal does not itself authorize a spend. A receipt proves a transfer to a **Reserve-controlled account on the Stable Hub ledger**, not an external XLag on-chain settlement.

**Documentation caution:** Earlier [`slh-pinky-promise-economy.md`](slh-pinky-promise-economy.md) and old phase tests document the fixed 0.1-credit policy. Those remain useful history but must not be treated as current pricing authority over the V2 executable code.

## 10. SEH connectivity and remote trust

The optional SEH path is **resident and outbound** from the managed SLH gateway. [`seh_canonical_connection.py`](../main_computer/slh/seh_canonical_connection.py) resolves the one registered Coolify SEH application UUID against existing Mother identity/deployment state, validates placement/source/metadata and public hostname, and checks that the remote registered SLH public key matches the SLH's preserved identity. This resolver is read-only. [`seh_autoconnect.py`](../main_computer/slh/seh_autoconnect.py) supervises reconnect attempts; [`seh_transport.py`](../main_computer/slh/seh_transport.py) implements the relay transport.

Local `POST /v1/local/seh/activate` and organization `POST /v1/org/admin/seh/activate` are implemented as separate, gated controls. Activation initiation is **not** evidence that the outbound WSS tunnel is authenticated. Read `GET /v1/local/seh/status` or `GET /v1/org/status` for the distinction between enabled, attempted, connected and verified, plus error classification. A missing/deleted remote application, mismatched key, stale Mother generation or unverified trust must not be papered over by creating a substitute SLH identity.

The generic `/v1/seh/status`, `/v1/seh/pair`, `/v1/seh/tunnels`, and `/v1/admin/seh/{plan,deploy,start}` API entries are **still 501 stubs**, despite the separate working harness/controller pathway. They are not aliases for the canonical operational controls.

A successful local tunnel or lab relay does not on its own establish complete remote mobile-administrator application parity, Internet deployment readiness, or end-to-end production security acceptance. Keep those claims tied to their particular recorded acceptance lanes.

Source: [`organization_controller.py`](../main_computer/slh/organization_controller.py), [`seh_canonical_connection.py`](../main_computer/slh/seh_canonical_connection.py), [`seh_autoconnect.py`](../main_computer/slh/seh_autoconnect.py), [`organization_service.py`](../main_computer/slh/organization_service.py).

## 11. Authoritative API surfaces — summary

These are distinct interfaces and cannot be interchanged merely because their names sound similar.

| Surface | Exposure / authentication | Status in source |
| --- | --- | --- |
| `http://127.0.0.1:8897/v1/local/*` | Loopback-only, local administration; most actions require enrolled admin session | Implemented with controller dependencies |
| Same listener `/v1/bootstrap/*`, `/v1/enrollment/*`, `/v1/auth/*`, `/v1/admin/*` | P-256 bootstrap/enrollment/login/admin controls | Implemented except explicit stubs |
| Organization API TLS port (default 8899), `/v1/org/status` | Network-facing TLS, public nonsecret observation | Implemented when child running |
| Organization API `/v1/org/admin/*` | TLS + administrator session | Implemented for services, SEH activation and marketplace control |
| Organization API selected `/v1/work`, `/v1/wallets`, `/v1/credits`, `/v1/accounting/*` | TLS + device role or administrator; backing services conditional | Implemented conditionally |
| Managed Worker TLS/WSS port (default 8898) | Enrolled Worker plus pinned TLS transport | Implemented when Worker serving enabled |
| Generic `/v1/workers`, `/v1/requesters`, `/v1/routes`, `/v1/marketplace`, and generic `/v1/seh/*` | Documented in route registry but 501 | Stub, not the real Worker/marketplace/SEH control surface |
| `GET /v1/capabilities` and `GET /openapi.json` | Discovery | Runtime API registration; use `x-implementation` and required-access metadata, not route presence alone |

**Important:** Local HTTP must not be opened to the LAN or reverse-proxied as a substitute for TLS and device trust. Browser-side access should not carry administrator bearer tokens, signers or Coolify credentials. A future MCEL app needs a separately specified server-side authorization adapter to these operational surfaces; this document does not claim that adapter already exists.

## 12. Operational tools and verification boundaries

The repository already includes substantial operational tooling. These are **tools**, not SLH application screens:

| Tool | What it provides | Caution |
| --- | --- | --- |
| [`tools/slh_auth_harness.py`](../tools/slh_auth_harness.py) | Local bootstrap/identity harness and route contract inspection | The harness's `serve` is not the full resident child-service topology |
| [`tools/slh_authenticator_cli.py`](../tools/slh_authenticator_cli.py) | Development authenticator identity, enrollment and login operations | Never print/store private keys in app logs or commit them |
| [`tools/slh_scheduler_admin.py`](../tools/slh_scheduler_admin.py) | Admin `show`, `status`, deliberate `set` via the loopback API | `set` requires explicit mutation opt-in |
| [`tools/slh_enrollment_lifecycle.py`](../tools/slh_enrollment_lifecycle.py) | Isolated/live authenticated device enrollment testing | Can create real records; use a governed test context |
| [`tools/slh_managed_gateway.py`](../tools/slh_managed_gateway.py) | Standalone managed Worker listener and optional dev/test marketplace proxy | Different lifecycle from resident Organization Service |
| [`tools/slh_seh_lab/verify_battery.py`](../tools/slh_seh_lab/verify_battery.py) | Selected SLH/SEH lanes, optional FDB and live-read-only checks | Scope-specific results, not a substitute for all production acceptance |
| [`tools/slh_seh_lab/run_lab.py`](../tools/slh_seh_lab/run_lab.py) | Isolated integrated/paid workload and failure-lab operations | Some modes create network activity and durable test state |
| [`tools/seh_harness.py`](../tools/seh_harness.py) | Canonical SEH operator/deployment harness | Deployment mutation and identity checks must be explicit |
| [`tools/slh_storage_migrate.py`](../tools/slh_storage_migrate.py) | Explicit offline legacy-state migration | Do not silently initialize a second authority |

A safe **read-only** first local check, *only when SLH is already running*, is:

```powershell
Invoke-RestMethod http://127.0.0.1:8897/v1/local/status
```

To inspect repository-level configuration tests without starting the service:

```powershell
python -m tools.slh_auth_harness contracts
```

The second command may initialize local harness state according to that CLI's implementation; use a disposable `--state-dir` if a pure nonmutating check is required. Do **not** add an automatic boot task on this Windows machine.

**Status/acceptance interpretation:** A passing isolated, smoke, FDB, or hosted test proves only its named scope. A current live SLH state requires an authenticated check against the running process and its configured endpoints. Historical documents, particularly [`slh-seh-laboratory-exit.md`](slh-seh-laboratory-exit.md), can describe earlier blocked lanes; do not silently promote their older status descriptions or assert those blockers have been cleared without reviewing current reports.

## 13. Existing documentation map and discrepancies

| Source document | Useful for | Treat with care |
| --- | --- | --- |
| [`secure-local-hub-administrator-manual.md`](secure-local-hub-administrator-manual.md) | Concept, operator roles, historical target workflows | Explicitly an older **TARGET** manual; example ports, UI steps and status claims are not today's backend contract |
| [`slh-phase2a-authentication-api.md`](slh-phase2a-authentication-api.md) | Original loopback identity and enrollment design | Older `org_id`, state directory and stub-status assumptions have been superseded in places |
| [`slh-seh-device-authentication-architecture.md`](slh-seh-device-authentication-architecture.md) | Device trust, separate personal auth and SEH boundary | Some phone/recovery requirements remain targets |
| [`slh-scheduler-policy-phase2d.md`](slh-scheduler-policy-phase2d.md) | Scheduling rationale and originally defined policy controls | Prefer source defaults and current `compute_plan()` for live semantics |
| [`slh-managed-worker-passthrough-phase2bc.md`](slh-managed-worker-passthrough-phase2bc.md) | Public-to-private capacity handoff rationale | Verify actual generation/withdrawal state and nonpreemption in code |
| [`slh-pinky-promise-economy.md`](slh-pinky-promise-economy.md) | Historical V1 economic rationale and Hub sweep boundary | Fixed 0.1-credit rule is superseded by source V2's 20% of Worker quoted price for new requests |
| [`slh-pinky-shared-hub-integration.md`](slh-pinky-shared-hub-integration.md) | Specific integrated lab evidence | Does not prove universal deployment acceptance |
| [`slh-seh-integrated-hub-lab.md`](slh-seh-integrated-hub-lab.md) | Transport and paid-job laboratory test design | Check source/report timestamps before treating old results as current |
| [`slh-seh-laboratory-exit.md`](slh-seh-laboratory-exit.md) | Fail-closed, broader acceptance requirements | Historical blocker text does not negate later narrowly passing tests, nor does a narrow PASS clear broader requirements |
| [`seh-operator-harness.md`](seh-operator-harness.md) | SEH operator control process | Separate from public organization API stubs |

**Documentation reconciliation work, not application design:**

1. Mark fixed 0.1-credit Pinky Promise text as **historical V1** where still described as current, and explicitly link to V2 20%-of-Worker-price source.
2. Remove outdated requirements to create or choose an `org_id` from current SLH operator instructions; preserve them only as historical protocol examples.
3. Replace old default state-directory and port examples when documenting the current resident topology; distinguish old harness commands from production-facing service operation.
4. Clarify that the **real** Worker, marketplace and canonical SEH paths coexist with **stubbed generic** REST routes; an OpenAPI route's presence is not proof of a backing service.
5. Keep each lab's acceptance statement scoped to what was tested, with explicit environment and report time; avoid conflating an isolated passing lane with real hosted readiness.

## 14. What this audit establishes—and what it does not

**Established from code:** SLH local and organization service separation, current state ownership, device and admin authority, managed Worker/scheduler operations, conditional private requests, public marketplace/controller and wallet observation, current Pinky Promise pricing/sweeper, and outbound canonical SEH architecture. Source pointers above make each statement independently inspectable.

**Not established by the original backend audit:** That SLH is running on the user's Windows machine; that a device is currently enrolled; that any Worker is connected; that a wallet is funded; that the SEH is presently authenticated; that a Reserve sweep was executed; or that remote phone administration is operational. These remain live-state questions, regardless of passing isolated tests.

## 15. MCEL SLH application integration update — November 8, 2026

**Later than this document's November 7 backend source baseline:** The MCEL SLH first slice and subsequent authenticated Worker Bank patch now provide canonical `/applications/slh` and `/applications/slh/workers` routes, synthesized read-only Overview, authenticated enrolled/live Worker inventory and an audited Worker-label change. The user's targeted tests passed **17/17** after patch installation. That verifies the tested MCEL integration, **not** a live configured local Organization Service. Source: [`mcel_apps/slh/application.js`](../mcel_apps/slh/application.js), [`main_computer/mcel_slh_application.py`](../main_computer/mcel_slh_application.py), [`main_computer/mcel_slh_worker_bridge.py`](../main_computer/mcel_slh_worker_bridge.py).

**The next missing application capability is first-run orchestration, not more Worker Bank rendering.** A brand-new or stopped local SLH must be usable as an entry state. The app should guide an administrator through a guarded on-demand loopback service start, local bootstrap or existing-identity access, explicit Organization Service configuration and enablement, verified TLS listening, and Organization Service login before privileged Worker operations. Its current development-only login helper (`tools.mcel_slh_admin_session`) presumes that service is already running, so `ORG_TRANSPORT_UNAVAILABLE` is an expected *transport failure* on an unavailable service; it is **not** proof that the machine has never been configured. Sessions issued in the control plane are not transferable to the Organization Service. No automatic Windows boot task should be introduced on this development machine.

The governing user journey and its distinct state/failure cases are now documented in the [Administrator and Operator Manual](secure-local-hub-administrator-manual.md#2-using-slh-from-first-launch-to-operating-workers). That manual expresses user outcomes and authority boundaries; MCEL still owns interface synthesis. This integration note does not revise the November 7 backend capability evidence or claim finished first-run/production acceptance.

## MCEL first-run orchestration (November 8, 2026)

The subsequent semantic application now exposes an explicit first-run path from an absent loopback SLH service to an authenticated Organization Service: [`mcel_slh_first_run.py`](../main_computer/mcel_slh_first_run.py), the authored [`application.js`](../mcel_apps/slh/application.js), the bounded [`mcel-semantic-slh-ports.js`](../main_computer/web/applications/scripts/mcel-semantic-slh-ports.js), and the viewport's same-origin SLH setup route.

- On-demand local startup invokes the existing SLH loopback service without installing a Windows boot task or implicitly enabling remote service. Status reads never start a process.
- First administrator bootstrap uses the existing guarded P-256 transaction and the current OS user's local device identity. The user must explicitly acknowledge that personal TOTP has **not** been implemented. If an authority already exists, repeat bootstrap is refused.
- An authorized local admin can configure an explicitly selected `127.0.0.1` or currently assigned private IPv4 listener, with selected Worker/API ports. SEH remains disabled. Configuration does not activate service.
- Explicit activation is not accepted as ready until the Organization Service reports **running and TLS-listening**. In-app login signs a new challenge for **that separate Organization Service**; a local-control bearer is not reused. The short-lived Organization Service token is retained only in the browser capability closure, not semantic state, persistent storage or displayed receipts; generated sign-out revokes the token in the Organization Service.
- Consequential viewport endpoints require loopback caller, an exact loopback `Host`, matching same-origin `Origin`, JSON content type, and an exact operation header. This trusts the interactive local OS account; it is not a general remote/phone authentication ceremony.

**Acceptance scope:** The disposable first-run browser test and the existing Worker Bank integration tests have passed locally. This is not a current production-host assertion. In-app Worker enrollment and startup, network interface discovery, shutdown/restart controls, personal TOTP and recovery remain future work. The existing `/v1/bootstrap/recovery` endpoint is still a stub.
