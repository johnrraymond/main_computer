# SLH Backend, Managed Connection Contracts and SEH Deploy Harness — Phase 1

**Status:** initial *control-plane implementation* against Main Computer's October 27, 2026 snapshot. It is not a working Secure Local Hub network listener, SEH relay, phone authenticator, wallet proxy, or complete Coolify deployment. This distinction is enforced in the harness result.

**Device-authentication implementation contract:** [Device Authenticator, Enrollment and Remote Administration Architecture](slh-seh-device-authentication-architecture.md).  
**Companion specifications:** [Secure Local Hub Administrator Manual](secure-local-hub-administrator-manual.md), [Managed Secure Worker](secure-worker-managed-mode-spec.md), [Trust/Protocol Contract](slh-identity-protocol-spec.md), [SEH Coolify Harness](seh-secure-external-hub-coolify-harness.md).

## 1. Project buckets and boundaries

| Bucket | Backend responsibility | Current state |
| --- | --- | --- |
| **SLH** | Organization identity/trust, first admin enrollment, personal authorizers, virtual public workers, delegated wallets, local scheduling, private credit write-down | Connection catalog and SEH deploy-controller facade **implemented**; LAN listener, credentials, sessions, ledger **not implemented** |
| **SEH** | Internet-facing HTTPS/WSS relay; outbound SLH tunnel; ciphertext forwarding with no key/authorization authority | Coolify *plan/preflight/create/start control plane* implemented; runnable relay image/tunnel **not implemented** |
| **Secure Worker/Requester integration** | User-selectable direct Ring 2 vs managed SLH vs remote SEH; walletless managed mode; operator's personal authenticator | API message family contract **published**; runtime/browser changes **not implemented** |

All execution, credit issuance, Known Device enrollment, wallet signing and sensitive administrative operations remain **default-deny** until dedicated authenticated services exist. **No LAN port or viewport route is opened by this patch.**

## 2. Backend API surface

The implementation is Python-callable and intentionally independent of the browser UI:

```python
from main_computer.slh import SLHControlBackend, SEHTarget
from main_computer.slh.coolify import CoolifyHTTP

backend = SLHControlBackend()
catalog = backend.connection_catalog()  # SLH, SEH, mainnet, testnet, test, dev
api_contract = backend.managed_work_api()  # NOT_IMPLEMENTED — describes envelopes
probe = backend.probe_marketplace_hub("test")  # optional read-only GET /api/hub/v1/health

# t = SEHTarget.from_dict(configuration)
# p = backend.seh_plan(t)                         # no networking
# status = backend.seh_preflight(t, client)       # read-only Coolify inventory
# receipt = backend.seh_create(t, client, digest=p['plan_digest'], target_id=t.target_id)
```

The `catalog` separates `local_slh`, `external_seh` and `marketplace` profiles. It uses `main_computer.hub_networks.load_hub_network_registry` for the four configured marketplace URLs. A direct marketplace client still needs its own marketplace credential; a managed client is intended to delegate public marketplace authorization to the SLH, but delegation is **not yet implemented**. The SEH carries a transport path, not a second marketplace network or wallet service.

No HTTP `/api/slh/...` management endpoint exists yet. The local Main Computer UI will later call the backend through an **authenticated, loopback-only management route**; do not expose the viewport API through the SLH worker listener or SEH.

### Managed Worker/Requester API contract to implement next

The first administrative trust ceremony now has **one approval engine and two authorization paths**: a new device submits a public-key request with selected admin display alias; either an approved one-time enrollment key or an authenticated administrator action from the queue calls that same approval operation. The device then proves its key and becomes a Known Device. Physical-inspection-required policies may delay approval. Human authenticators remain distinct from device keys and marketplace wallets.

Subsequent machine messages: `slh.worker.hello`, `slh.worker.heartbeat`, `slh.work.offer`, `slh.work.accepted`, `slh.work.authorized`, `slh.work.result`, `slh.route.request`, `slh.route.grant`. The SLH may advertise multiple logical Ring 2 workers backed by separate, fenced physical worker slots and its own marketplace wallet(s). Public payload must not reach a physical worker until the existing public Hub has authorized the job. Private direct requester-to-worker execution instead issues an SLH-signed local route ticket and writes down the service fee exactly once.

Remote clients authenticate **to the SLH through the SEH**, rather than to the SEH in place of the SLH, using independently authenticated end-to-end encrypted communication. SEH only transfers ciphertext and bounded relay metadata. All of these messages are **contracts, not live endpoints** in Phase 1.

## 3. SEH deployment lifecycle

- `plan` — pure, deterministic desired-state digest and Docker Compose sketch. No external side effects. Pinned `image@sha256:<digest>` is mandatory.
- `preflight` — read-only Coolify project/server/environment identity checks and `GET /api/v1/services` inventory; for adopting an explicitly selected UUID also `GET /api/v1/services/{uuid}`. Refuses resources with a wrong name/ownership marker. Does **not** yet validate DNS, TLS, WebSocket, or relay identity.
- `deploy --execute` — **creates only a new SEH service** using Coolify `POST /api/v1/services`, after exact digest and target-id approval. It refuses duplicate name collision and unknown resources, never retries an ambiguous create, never silently changes an existing resource. Records created service UUID in its output. Initial state is `CREATED_NOT_STARTED`.
- `start --execute` — queues start for a previously identified service with an exact ownership marker and UUID approval. Initial state is `START_QUEUED`, **not Ready**. Domain/proxy configuration and real relay acceptance are future gates.
- `verify-local` — executes in-process fake Coolify create/preflight/refusal cases and reports mocked gates, never calling Coolify or claiming working WSS, end-to-end auth, worker execution, or settlement.

**Coolify API token:** supplied through environment variable `SLH_COOLIFY_TOKEN` only when remote API calls are requested; never saved in the JSON target, plan, report, or Compose. Use TLS for remote Coolify; loopback HTTP may be enabled only under the explicit `local_lab` target flag. The HTTP client rejects redirects to avoid forwarding credentials to unintended destinations.

**Known limitation:** the deployment call does not configure a public domain or establish a functioning SEH service. It creates a pinned Compose *resource*, not a verified relay. Production rollout is blocked until an actual SEH image exists and Coolify domain, proxy HTTPS/WSS, SLH tunnel pairing and end-to-end authenticator harness gates pass. The example image is deliberately undeployable.

## 4. Operator commands (from repository root, `.venv` active)

```powershell
python -m tools.seh_harness profiles
python -m tools.seh_harness contracts
python -m tools.seh_harness probe-hub --network test
python -m tools.seh_harness probe-hub --network dev
python -m tools.seh_harness plan --target deploy/seh/target.example.json
python -m tools.seh_harness verify-local --isolate --report runtime/reports/seh-harness/local-contract.json
python -m unittest tests.test_slh_backend_seh_phase1 -v
```

To run a read-only preflight against a company Coolify server, copy and edit `deploy/seh/target.example.json` with actual project/environment/server IDs, a reviewed immutable SEH image digest and public domain. Supply the token through the secure local environment (never commit it):

```powershell
$env:SLH_COOLIFY_TOKEN = '<TOKEN_FROM_YOUR_SECRETS_MANAGER>'
python -m tools.seh_harness verify-coolify --target deploy/seh/company.json --read-only
```

**Remote mutation is deliberately non-default.** After separately inspecting the plan and obtaining the two exact approvals, creating a new resource is invoked with:

```powershell
python -m tools.seh_harness deploy --target deploy/seh/company.json `
  --execute --approve-target 'company-relay-1' --approve-digest '<SHA256_FROM_CURRENT_PLAN>'
```

This prints the created UUID and `CREATED_NOT_STARTED`. Save that UUID in the target before running a separate explicitly authorized `start` command. Avoid deploying placeholder images. **This CLI command is not yet a substitute for future phone-based administrator authorization.** A trusted local operator must execute it; a future SLH admin UI will enforce real administrative approval before calling the backend.

## 5. Acceptance gates and next infrastructure slices

**Proven in Phase 1:** six connection profiles resolved from existing network registry; documented managed-mode API types; pinned-image Compose generated; dry-run does not mutate; mock Coolify create requires exact approval; duplicate resources and wrong ownership markers rejected; mock creation/start never labeled Ready; token excluded from targets/reports.

**Not proven:** Coolify live deployment; real relay container; worker key enrollment/rotation; phone security; worker/requester authentication; walletless work; multiwallet marketplace delegation; private routing/write-down; real WebSocket handshake; public HTTPS; outbound SLH tunnel; secure end-to-end SEH encryption.

Recommended next slices, before UI:

1. **SLH local trust service:** first-admin bootstrap, device public-key registry, common approval engine, personal authenticator, protected encrypted listener on selected LAN interface.
2. **Managed Worker/Requester transport:** walletless device sessions, capacity and reservation accounting, direct encrypted private route tickets, durable runtime supervisor.
3. **SLH Ring 2 upstream:** virtual worker sessions backed by SLH-owned wallets, separate mainnet/testnet/test/dev profiles, immutable public authorization ordering, existing payout reconciliation.
4. **SEH runtime and tunnel:** pinned relay image, public WSS/HTTPS, outbound SLH session, session isolation and actual end-to-end encrypted remote authentication.
5. **SEH live harness:** Coolify full life cycle, actual DNS/TLS/WSS/phone/worker/security probes; **only then** a Ready gate and corresponding app UI.

The Coolify control plane follows current documented service APIs: https://coolify.io/docs/api/endpoints/services/create-service, https://coolify.io/docs/api/endpoints/services/start-service-by-uuid, https://coolify.io/docs/api/endpoints/services/get-service-by-uuid.

## 6. Revised Phase 2 security and administration lock (supersedes earlier CA requirements)

**No organizational CA or internal client certificates.** Phase 2 begins with host-local first-admin bootstrap, protected per-device public/private signing keys, a Known Device registry with administrator notes, and a shared `approve_enrollment` operation accepting either authorized administrator session or single-use key. Worker, Requester, phone and a remote SLH administration client all reuse this flow. Personal authenticator credentials remain separately enrolled and required under policy.

The locally running SLH application serves as **Host an SLH** or **Connect to an SLH**. Both local and remote administrators use the same SLH backend API and receive identical role-authorized status, enrollment, device, worker, marketplace, wallet/credit, SEH/Coolify and audit information. First-admin bootstrap and actual physical recovery remain host-only.

SEH provisioning still requires a scoped Coolify API key and guarded target/digest approval. SEH service identity now uses deployment-bound **public-key enrollment and pinning**, not an organizational CA. Direct/SEH clients authenticate via the same Known Device challenge/response protocol over end-to-end encrypted connections. The SEH public website still requires ordinary HTTPS/WSS TLS.

The Phase 1 `verify-local --isolate` result remains a **control-plane mock pass**. Extend the test harness with real simulated signer keypairs, shared approval-engine parity, physical-inspection gate, remote-admin API parity, relay impersonation and key compromise/revocation cases before claiming security readiness.
