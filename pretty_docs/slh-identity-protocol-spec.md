# SLH Trust, Enrollment, Authentication, Administration, Routing and Credit Protocol — Target Contract

**Status:** design specification only for new network/security paths (October 28, 2026). Phase 1 supplies a connection catalog and guarded SEH Coolify control-plane operations; it does **not** supply the endpoints below. **No organizational CA, client certificates or CSRs** are part of this revised design.

**Identity lifecycle clarification (November 8, 2026):** The **LMS-SLH frontend/local-management service** owns **one cryptographic identity system for every local actor**. The default installation identity and identities used by people or machines have exactly the same structure and key capabilities; only custody, labels, enrollment and roles differ. The backend SLH Organization Service owns organizational enrollment and authorization, not private keys. See [LMS-SLH Identity Ownership](lms-slh-identity-ownership.md). The protocol below is historical design and does not establish automatic default identity creation as an implemented capability.

**Companions:** [SLH Pinky Promise economy (governing financial contract)](slh-pinky-promise-economy.md), [Device Authenticator Architecture](slh-seh-device-authentication-architecture.md), [Administrator Manual](secure-local-hub-administrator-manual.md), [Secure Worker](secure-worker-managed-mode-spec.md), [SEH Harness](seh-secure-external-hub-coolify-harness.md).

## 1. Non-negotiable invariants

1. Administrator authorizes a device's **first enrollment**. Two UI paths, **Enroll with key** and **Approve from New Device Authorization Queue**, use **one backend approval method**. Selected administrator aliases grant no authority.
2. Devices generate their own private authentication keys and register public keys. Known Device login proves key possession with one-time, session-bound challenges. Editable names, owners, notes and addresses do not identify a device cryptographically.
3. Every local actor uses the **same LMS-SLH identity model**. Policy may require a second independently controlled identity to approve an action, but **person and machine are not separate credential types**. Marketplace wallet keys remain distinct financial authority.
4. Local SLH admin and an equally authorized administrator's separate SLH application use **the same backend admin API**, information model and role checks. Only first-admin bootstrap and true hardware recovery must be host-local.
5. Direct SLH and SEH-connected clients follow **identical** enrollment/auth/session/authorization APIs. The SEH is a public HTTPS/WSS relay, not an authentication server; keep E2E authenticated encryption from client to SLH.
6. Walletless workers may be represented by many SLH-owned public wallet sessions. Public marketplace job authorization occurs before forwarding work, and physical-worker capacity is never overbooked.
7. Private B2SelfB jobs remain private. For **successfully fulfilled qualifying local secure model access**, the SLH records an honest, idempotent **Pinky Promise** obligation of **0.1 credit per request**, using a fixed **1-credit open-market reference for 1,000 output tokens**; the organization wallet later pays the Main Computer Reserve. This is not live market-price discovery or token-based intra-org billing. A route ticket alone is not chargeable fulfillment, and the public Hub cannot verify undisclosed internal work.

## 2. Principal IDs and persisted records

| Record | Security-relevant fields |
| --- | --- |
| `organization` | immutable org ID; approved SLH service public key(s); supported upstreams |
| `identity_enrollment` (logical principal) | identity ID, enrolled public key/fingerprint, status, roles, policy, enrolled_by, method, last_seen; **same key model regardless of actor** |
| `admin_user` (optional organization account/display association) | account/display alias and scoped permissions/recovery associations; **not a separate cryptographic identity type** |
| `device` (legacy API/registry name) | existing device ID and public-key enrollment, descriptive user/type/status labels, roles, last_seen; **not an identity type distinct from people** |
| `device_metadata` | editable name, descriptive administrator notes, location/group, capabilities; never used as key identifiers |
| `enrollment` | pending request ID, device key, selected admin alias, requested role, challenge, scope, expiry, physical-inspection requirement |
| `enrollment_capability` | only salted/hashed bearer verifier stored, one use, expiration, bound request/device/org/admin scope; redeemed atomically |
| `challenge` | exact intended action, issuer/recipient, nonces, TTL, channel binding and consumed state |
| `session` | authenticated LMS-SLH identity, optionally a second LMS-SLH identity required by policy, role, policy epoch, expiry and revocation fence |
| `seh_binding` | scoped Coolify deployment and approved relay public key, tunnel/session status, revocation epoch |
| `marketplace` | four upstream profiles, logical workers, delegated wallets and safe spent/earned balance bookkeeping |

Private signing keys are never present in SLH's device registry and never appear in a remote UI response. Store all sensitive tokens, session secrets, marketplace keys and Coolify credentials separately in protected server-side storage.

## 3. Bootstrap, initial enrollment and Known Device login

### 3.1 First administrator

`POST /v1/bootstrap/admin` is **host-local setup only**: cannot be invoked through LAN/SEH, opens only while uninitialized, checks local installation authority, enrolls the first administrator authenticator, provisions recovery, atomically closes first-claim path and audits the event. Remote management is available **after** this initialization.

### 3.2 Two enrollment approvals; one implementation

```text
POST /v1/enrollment/requests      (device_public_key, user_label, device_label, type, requested_roles, admin_alias)
GET  /v1/enrollment/admins        (publicly disclosed opt-in aliases only, rate limited)
GET  /v1/admin/enrollment/queue   (requires authenticated admin with enrollment permission)
POST /v1/enrollment/approvals     (pending_request_id, authorization={admin_session | single_use_key})
POST /v1/enrollment/complete      (pending_request_id, proof_of_device_key)
```

**Common transaction:** ensure pending request and key ownership, normalize authorization to an approved admin actor/scope, verify administrator policy and physical-presence requirements, consume key once if supplied, set exact device roles and owner, record `enrolled_by`/method/notes, and activate one Known Device entry. Replays or concurrent approval races cannot mint multiple identities or elevate role scope. Approvals must be auditable without storing the raw enrollment key. A user choosing an administrator alias does not prove the admin consented.

The queue supports `PENDING`, `REQUIRES_PHYSICAL_INSPECTION`, `APPROVED`, `DENIED`, `EXPIRED`; no worker/requester access while pending. Physical-presence policy is enforced through an administrator-authenticated local ceremony or an explicitly attested mechanism, **not just a transferable invitation key**.

### 3.3 Known Device

```text
POST /v1/auth/challenge          (device_id, intended_operation, client_hello, channel_binding)
POST /v1/auth/verify             (challenge_id, credential_id, signature)
GET  /v1/auth/session            (scopes, expiration, person_auth_required, policy_epoch)
POST /v1/auth/logout             (invalidate session)
```

Sign a canonical, versioned representation containing organization ID, expected SLH identity/key fingerprint, device ID, audience, intended operation, target, nonce, expiry and secure-channel transcript binding. Enforce real signatures, single-use challenge, clock skew policy, role check, rate limit and session revocation. Optional additional approval can require another independently held **LMS-SLH identity** and an explicit scoped operation prompt; it is not a distinct human-only key system. No known-device challenge is a payment authorization.

## 4. SLH administration: shared host/remote backend

```text
GET  /v1/admin/status
GET  /v1/admin/devices
PATCH /v1/admin/devices/{device_id}       # friendly name, user assignment, notes, policy-controlled roles
POST /v1/admin/devices/{device_id}/revoke
GET  /v1/admin/enrollment/queue
POST /v1/enrollment/approvals            # same approval API from UI or enrollment-key redemption
GET  /v1/admin/workers
GET  /v1/admin/marketplace
GET  /v1/admin/credits
GET  /v1/admin/seh
POST /v1/admin/seh/deployment-plan
POST /v1/admin/seh/deploy
GET  /v1/admin/audit
```

The host app uses the same local API behind a local authenticated channel as an enrolled remote SLH app uses over LAN/VPN or SEH. Authorization checks **never** use `client_is_loopback` as a substitute for administrator identity or role; initial bootstrap/hardware recovery are explicit exceptions. Enrolled remote admins must have access to the same data and actions **for identical scopes**, including administration of Coolify-deployed SEH targets. Responses omit secret material; requests to spend, deploy or change access require operation-specific step-up and produce audit records.

## 5. Address/discovery and SEH transport

On LAN, SLH chooses an actual assigned interface address and dedicated listener; local names (e.g. `mc-slh.local`) and QR/USB bundles are discovery hints. Client trusts the previously verified SLH **public-key fingerprint**, not DNS alone. A public SEH has a normal valid HTTPS certificate at `hub.secure.company.com`, while the client and SLH run an independently authenticated encrypted channel over its WSS relay. Untrusted SEH can see limited metadata/traffic patterns but must not decrypt prompts, sign approvals, or substitute an SLH identity.

For service provisioning, Coolify API deployment and SEH pairing are separate: single-use deploy-bound invitation, SEH-generated keypair, proof of possession, authenticated SLH registration of relay **public key**, reciprocal pinning of SLH public key, then SLH-originated authenticated outbound tunnel. No internal cert/CSR/CA is required. Explicit rotation/revocation and cross-organization checks apply.

## 6. Managed Worker / Requester messages and credit rules

```text
slh.worker.hello          (known_device, resource_capabilities, public_worker_eligibility)
slh.worker.heartbeat      (capacity, current leases, service epoch)
slh.work.offer            (lease_id, service policy, origin_class, no unauthorized public work payload)
slh.work.accepted         (lease reservation)
slh.work.authorized       (actual execution inputs, only once upstream public authorization received)
slh.work.result           (receipt, output to correct requester/public origin)
slh.work.failed           (typed failure)
slh.work.cancel           (fenced cancellation)
slh.private.route         (short-lived requester/worker capability, E2E authorization)
slh.credit.write_down     (idempotent private service usage record for reconciliation)
```

For a public paid work session, preserve Ring 2 `offer → accept/reserve → hub.work.authorized → execute → settlement`. SLH is the marketplace signer; managed physical workers have no wallet. SLH policy allocates physical resources between public and private demand with one shared capacity ledger. A private request uses an authenticated local route and does not disclose its prompt to the public Hub. Only eligible **secure model access** that reaches verified successful fulfillment creates the **Pinky Promise** obligation (**0.1 credit per qualifying completed request**, using versioned `fixed_per_request_v1` policy) for the organization wallet to pay to the Main Computer Reserve; it is **not assessed at route commitment**, requires no live external market quote, and does not multiply by token count. The local journal is commercial trust accounting, not proof of hidden execution. Keep wallet earnings, spendable credits, outstanding Pinky Promise obligations, and confirmed Reserve settlement separate; see [Pinky Promise specification](slh-pinky-promise-economy.md).

## 7. Harness and negative acceptance

| Gate | Positive | Negative |
| --- | --- | --- |
| P0 | Local first admin created once; remote admin application subsequently connects | SEH or LAN client cannot claim uninitialized SLH |
| P1 | Both enrollment paths converge on the same approval method and result | Invalid, expired or reused key rejected; self-approval rejected |
| P2 | Pending admin queue can defer for physical inspection | Transferable invitation does not bypass physical policy |
| P3 | Registry supports name/owner/notes changes | Changes don't alter public-key identity or grant implicit roles |
| P4 | Real known-device signing, expiry and revocation | Unknown key, replay or revoked session rejected |
| P5 | Identical administrator API data and permissions host-local, remote LAN and SEH | No privileged data/keys leaked to SEH/remote UI |
| P6 | Walletless managed Worker and Requester can authenticate directly and via SEH | Wrong SLH pinned key / relay tamper denied |
| P7 | SEH pair/deploy and authenticated E2E tunnel pass isolated and live probes | Cross-org SEH or forged signer denied |
| P8 | Role-scoped person challenge from built-in and phone-like simulated signer | Wrong person/action/operation denied |
| P9 | Physical capacity and marketplace accounting preserved | No unapproved public work or double debit |

**Evidence status:** All above are target gates. Existing Phase 1 `verify-local --isolate` is only a Coolify control-plane mock pass, not proof of secure authentication, actual worker delegation or remote UI functionality.
