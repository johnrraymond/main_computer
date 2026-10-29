# Secure Worker — Managed SLH Mode: Existing-App Change Specification

**Type:** Pretty Docs product and implementation specification  
**Status:** TARGET / not implemented in inspected October 27, 2026 snapshot  
**Applies to:** existing Main Computer **Worker** application; do not create an unrelated worker UI  
**Security implementation contract:** [Device Authenticator, Enrollment and Remote Administration Architecture](slh-seh-device-authentication-architecture.md)  
**Read with:** [SLH Administrator Manual](secure-local-hub-administrator-manual.md), [SLH Identity and Protocol](slh-identity-protocol-spec.md), [SEH Coolify Harness](seh-secure-external-hub-coolify-harness.md)

## 1. Baseline: what exists today

The inspected snapshot contains an operationally relevant, wallet-oriented browser Worker app:

| Current source | Observed responsibility | Extension point |
| --- | --- | --- |
| `main_computer/web/applications/apps/worker.html` | Worker UI; Automatic Hub Selection (`mainnet`, `testnet`, `test`, `dev`), Seller Offer, Active Hub Session, Work Now, wallet/readiness, multisession keys, faucet/bridge, paid overflow | Add **connection mode** and secure managed enrollment panel without breaking existing controls |
| `main_computer/web/applications/scripts/worker.js` | Browser-side Worker session, wallet flow, runtime status and offer interactions | Add managed SLH state/commands as independent mode; do not make wallet a managed-mode prerequisite |
| `main_computer/web/applications/styles/worker.css` | Worker layout and styles | Integrate managed controls within existing MCEL Worker layout |
| `main_computer/viewport_route_dispatch.py` | Worker API routing | Add SLH-managed worker endpoints, server-side authorization, no remote mutation of general app APIs |
| `main_computer/viewport_routes_energy.py` | Worker network/runtime handlers and status | Extend structured runtime status with managed trust and SLH session states |
| `main_computer/worker_runtime_supervisor.py` | Backend owner of Worker runtime reconciliation, independent of browser-open state | Add managed-LAN session supervisor; preserve restart resilience |
| `main_computer/stable_hub_worker_sessions.py` and `stable_hub.py` | Public Hub accepted sessions, worker leases, authorization and settlement | SLH public-facing proxy reuses public contract; managed physical workers **do not** pretend to be public wallet clients |
| `tools/worker_browser_acceptance.py` | Opt-in local Chromium + MetaMask acceptance; local URL only | Preserve public-mode tests; add separate managed-worker acceptance without wallet |
| `tests/test_worker_app_layout_contract.py` | Worker DOM and layout expectations | Add managed-mode checks; keep older IDs/contracts stable |

Existing UI **labels and DOM IDs** to retain: `worker-auto-hub-mainnet`, `worker-auto-hub-testnet`, `worker-auto-hub-test`, `worker-auto-hub-dev`, `worker-rental-enabled`, `worker-seller-availability-mode-totally-idle`, `worker-seller-availability-mode-ai-idle`, `worker-network-ring`, `worker-connect-wallet`, `worker-request-multisession-key`, `worker-network-work-now`, `worker-register-offer`. Existing worker API paths include `/api/applications/worker/network-session`, `/runtime-status`, `/runtime-sync`, `/work-now`. Do **not** overload these paths with unauthenticated remote enrollment semantics.

The current Worker presents **only a public-Hub selection**. It does **not** presently implement SLH discovery, device-key enrollment, personal SLH authentication, managed worker mode, a native Android authenticator, or an SEH relay session. UI mockups and API names below are requirements only.

## 2. Two connection modes in the existing Worker application

- **Public Marketplace (existing):** Worker follows the current Ring 2 wallet/multisession-key protocol and Hub authorization order. Do not change the existing default simply by adding managed mode.
- **Managed by SLH (target):** Worker authenticates as an enrolled Known Device, accepts policy-limited jobs without its own marketplace wallet, and sends its capabilities and availability to the organization's SLH. It may connect directly over LAN/VPN or through optional SEH. The SLH represents many physical workers using its own marketplace wallets.

**Trust rule:** Device signing key ≠ personal user authenticator key ≠ SLH marketplace wallet. The SLH stores public device keys and administrator-approved scopes, not a certificate chain. User approvals, when required, use the individual's own built-in or phone signer.

## 3. Proposed Worker UI controls and states

| Panel | States / actions |
| --- | --- |
| Connection | Public Marketplace / Managed by SLH; transition drains already accepted work |
| SLH selection | Discover local SLH, manually enter address, optional remote SEH domain; always verify pinned SLH identity |
| First connect | Select an administrator's public display alias, enter optional one-time enrollment key, **Enroll with key** or **Request admin approval** |
| Enrollment | Not enrolled / Pending admin / Physical inspection needed / Approved / Denied / Revoked |
| Authenticator | Device key generated/protected; Known Device authentication; personal built-in or phone approval when required |
| Managed status | Authenticating / Connected / Restricted / Disconnected / Revoked; show actual backend evidence |
| Worker capabilities | GPU/CPU/model/slots, capacity reservations, heartbeat and resource policy |
| USB | Export SLH public identity+invitation / export device public-key request / import approval; do not export private keys |
| Remote | Direct SLH / SEH relay / Untrusted endpoint / Relay unavailable, with same Known Device permission checks |

The user interface must never claim enrollment succeeded because an enrollment key was typed; only the SLH's common approval response and verified device-key proof can establish success.

## 4. First connection and subsequent authentication

### A. Direct SLH, LAN or VPN

1. Worker generates a device signing keypair using protected local storage; none of this is a blockchain wallet.
2. The user chooses a verified SLH and selects an available enrollment administrator alias, without thereby acquiring any administrator rights.
3. The Worker submits the device public key, label, proposed user, requested type/permissions and challenge-proof as one pending enrollment record.
4. A **valid administrator-issued, single-use enrollment capability** calls the same `approve_enrollment` server action that the administrator queue uses. **Without a key**, the request goes to the selected administrator's New Device Authorization Queue.
5. The administrator may approve from their SLH application locally or remotely, or mark the request **Physical inspection required** and approve only after authenticating on the user's machine. A transferable key alone does not prove physical presence.
6. SLH stores a Known Device registry entry; Worker proves possession of its enrolled key with a fresh challenge. User signs in with their personal authenticator only when SLH policy requires it. Unattended devices may receive narrowly limited machine-only worker authority.

### B. USB / sneaker-net

Export a short-lived invitation and SLH public identity, have the device generate a local signing key and export only its enrollment request/public key, then import the request into the same administrator queue. Administrator approval (or scoped enrollment capability) is exported back as a protected response. There are **no CSR, client certificates or organizational CA**. This transports enrollment approval; it does not itself implement offline computation.

### C. Phone and SEH

A previously enrolled phone may authenticate through the public SEH using end-to-end SLH encryption and approve requests **only if its current human owner has delegated administrator permission**. A previously unknown remote client uses the same pending enrollment/approval path as a LAN device. The SEH cannot decide admission or substitute a different SLH identity.

### D. Built-in Main Computer Authenticator

Worker includes a common authenticator client with `create_identity`, `get_public_identity`, `sign_challenge`, `approve_action`, `lock_identity` and `reset_identity`. Prefer native protected keys/TPM when available; browser-backed storage limitations must be explicit. Distinct real test identities sign the same bounded, scoped challenges in the harness; no verification shortcut.

## 5. Proposed backend contracts [TARGET — not deployed]

The local viewport remains loopback-only. It invokes its own local Worker adapter, which calls the organization's authenticated SLH API; do not expose general viewport admin routes on the LAN or SEH.

```text
POST /api/applications/worker/managed/discover           # local adapter [TARGET]
POST /api/applications/worker/managed/connect            # local adapter [TARGET]
GET  /api/applications/worker/managed/status             # local adapter [TARGET]

POST /v1/enrollment/requests                             # common SLH enrollment record
GET  /v1/enrollment/admins                               # public display aliases, opt-in only
POST /v1/enrollment/approvals                            # same approval engine for key or admin session
POST /v1/enrollment/complete                             # device proves possession
POST /v1/auth/challenge                                  # fresh channel-bound challenge
POST /v1/auth/verify                                     # signed proof / scoped session
```

The worker execution protocol retains `slh.worker.hello`, `slh.worker.heartbeat`, `slh.work.offer`, `slh.work.accepted`, `slh.work.authorized`, `slh.work.result`, `slh.work.failed`, and `slh.work.cancel`. Do not forward public marketplace work before existing `hub.work.authorized`. Every request carries unique IDs, bounded size, nonce/expiry where applicable, authorization scope and idempotency. Use the [SLH protocol specification](slh-identity-protocol-spec.md) for the authoritative target.

## 6. Backend integration and scheduling

- Extend `WorkerRuntimeSupervisor` (or add sibling `ManagedWorkerRuntimeSupervisor`) so managed sessions reconnect after UI closes/restarts, rotate device authentication keys through authorized rollover without losing approved capacity, and report truthful status.
- Preserve the public Hub's **offer → accept/reserve → `hub.work.authorized` → execute** order when SLH is an intermediary for Ring 2 jobs. Do not forward public workload content to a physical worker until marketplace authorization.
- SLH worker connection maintains a physical-capacity record independent of the number of advertised public logical workers. A single 2-slot device cannot be used as 4 public + 2 private concurrent slots.
- For private work, authenticate requester, authorize requested capability/model, reserve worker, issue short-lived route ticket and record the discounted pink-promise charge exactly once at a defined routing-lease commit point. Never call private usage a public market settlement.
- If SLH hands a requester a direct LAN worker endpoint, bind route tickets to actual worker identity, requester, capability and expiry; secure the requester-worker path independently of the scheduler.
- If the SLH relays a private job, it is an **authorized trusted local** relay; the **SEH public relay** must not gain access to plaintext under the guise of local SLH.
- Revocations, mode switches, worker disconnects and service restarts fence old sessions and fail closed without double committing public credit settlements or local private debits.

## 7. Implementation slices and gates

1. Extend existing Worker connection mode selector and authenticator component, without breaking current Ring 2 public mode.
2. Add direct SLH transport and stable pinned SLH identity verification; create/store per-device key in managed mode.
3. Add common pending-enrollment request with selected admin alias and optional one-time enrollment key; bind it to SLH's **one approval API**.
4. Implement Known Device signed challenges, session fencing, optional current-user authenticator and policies for unattended workers.
5. Support offline USB enrollment transfer and optional SEH end-to-end transport without changing enrollment identity or permissions.
6. Feed actual worker capabilities, capacity and outcomes to the SLH worker scheduler; verify multiworker wallet delegation later.

**Acceptance:** two newly installed Workers use the different enrollment approval methods, converge on identical registry state, and prove their device keys; revoked/unknown machines fail. At least one pending request is deferred for physical inspection. A remote Worker performs the same Known Device exchange via SEH, with no leaked private keys and no marketplace wallet on the Worker. A simulated authenticator must exercise real signing and failure checks. All of these are **TARGET** tests, not existing results.
