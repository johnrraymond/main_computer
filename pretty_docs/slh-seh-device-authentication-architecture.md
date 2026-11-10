# SLH/SEH Device Authentication, Enrollment and Remote Administration — Implementation Contract

**Status:** Revised architecture specification, October 28, 2026. **Not an implementation claim.** The Phase 1 SLH connection registry and guarded Coolify plan/create/start harness exist; SLH authentication, enrollment, remote administration, SEH tunnel, Android companion, wallet delegation and the real SEH runtime are **not implemented** in the examined October 27 snapshot.

**SLH** means the organization's privately operated Secure Local Hub. **SEH** means its optional public Secure External Hub relay. The SEH is an *untrusted transport intermediary*, notwithstanding its name. See the [Administrator Manual](secure-local-hub-administrator-manual.md), [Secure Worker change specification](secure-worker-managed-mode-spec.md), [SLH protocol](slh-identity-protocol-spec.md), [SEH Coolify harness](seh-secure-external-hub-coolify-harness.md), and [Phase 1 backend](slh-backend-seh-deployment-phase1.md).

## 1. Governing simplification

Main Computer does **not** operate an organizational certificate authority, issue X.509 client certificates, require client CSRs, or maintain internal CA renewal. An SLH stores **enrolled public keys for the same LMS-SLH identity model** and verifies possession with the Main Computer Authenticator. Ordinary enrollment is administrator-approved; **[TARGET]** first-administrator grant and administrator recovery can also be authorized by ORG-SLH's persistent `LOCAL-AUTH-KEY`, with proof of possession of the selected ordinary identity. Policy may require an independently held second identity for other confirmations, without treating a person and a machine as different kinds of cryptographic principals. A device does **not** need a marketplace wallet.

Distinguish **identity**, **organization permissions**, and **other authorities**—not a machine identity class from a human identity class:

| Authority | Credential | What it can do | What it cannot do |
| --- | --- | --- | --- |
| **Unified LMS-SLH identity** (person, machine, Worker, default installation identity, or an independently held second factor) | Same protected signing-key format and same local identity APIs for every actor | Prove possession of its key; sign properly scoped challenges | Automatically become an organization administrator, approve payments, or inherit another identity's privileges |
| **Organization policy and enrollment** | Backend record mapping an enrolled public identity to roles/permissions, revocation and policy | Authorize scoped actions after identity proof | Create a private key or infer privileges from an identity's machine/user label |
| Marketplace | SLH-owned marketplace wallets | Represent virtual public Workers and settle paid Ring 2 jobs | Approve enrollment or impersonate any LMS-SLH identity |
| Deployment | Coolify API token held by SLH | Create/update/observe an SEH target | Authenticate any LMS-SLH identity or act as a signing key |

**Encryption remains mandatory.** The SEH's public address uses ordinary, publicly trusted HTTPS/WSS TLS certificates managed by the host/proxy. Client-to-SLH privacy and SLH-to-SEH service trust use **pinned application public keys** and an established authenticated encrypted-channel protocol; there is no organization-issued X.509 hierarchy. A login signature alone does not encrypt traffic.

## 2. First installation: create the administrator path

**Root-authorization update (November 9, 2026):** The four historical steps below describe earlier host-local bootstrap planning. The governing **[TARGET]** mechanism is [ORG-SLH Root Authorization](slh-local-auth-key-root-authorization.md): ORG-SLH generates/reuses the disk `LOCAL-AUTH-KEY` **when ORG-SLH starts**, LMS-SLH reads it on the trusted host and transmits it over authenticated encryption to the network-accessible ORG-SLH API, and ORG-SLH verifies both the root and an ordinary identity's proof before granting admin. The root is retained for explicit recovery, not destroyed after first enrollment. A remote caller possessing the stolen root could also attempt the grant. The host is trusted for this phase; no new cryptographic identity class or local-only API is introduced.

**Governing local identity custody:** [LMS-SLH Identity Ownership](lms-slh-identity-ownership.md). The frontend/local-management SLH (**LMS-SLH**) manages **one uniform identity model** for the installation default and every other locally held identity; “machine” and “user” describe usage, not different cryptographic identities. The Organization Service receives public keys during explicit enrollment and remains the organization authorization authority. An LMS-SLH identity existing at startup does not authorize first-admin bootstrap or create a backend organization. Automatic default installation identity creation is implemented by LMS-SLH V4-A; root-authorized administrative enrollment is implemented on the ORG-SLH side in V6-A; LMS-SLH consumption is implemented in V6-B; V6-C adds conditional first-admin enrollment on a user-initiated **Login Administrator** click for an established organization with no active administrator identities. Later recovery is separately explicit.

1. **[HISTORICAL PLAN]** Launch **Host an SLH** on the designated organization machine and establish a backend state store, SLH service keypair and local bootstrap material. Under the revised target, ORG-SLH itself generates or reuses `LOCAL-AUTH-KEY` at service startup. The general Main Computer viewport remains on loopback.
2. **[HISTORICAL PLAN; SUPERSEDED FOR FIRST-ADMIN AUTHORIZATION]** The older flow assumed a host-local first-administrator ceremony. The revised target requires an explicit request with the ORG-SLH root and cryptographic proof from the selected **ordinary LMS-SLH identity**, not locality or an identity naming convention. Verify the intended ORG-SLH service identity before transmitting the bearer root.
3. **[TARGET]** Verify that the newly granted administrator authenticates with a fresh challenge and can inspect organizational status. Keep the `LOCAL-AUTH-KEY` durable for explicit later recovery without depending on an existing administrator signer.
4. **[TARGET]** Close the old unauthenticated/one-time bootstrap path; **do not destroy the persistent root**. Normal enrollment follows administrator authorization and audit; explicit root-authorized recovery remains available.

An SLH service public key is a stable *pinned identity*, not a CA. Changing it requires an authenticated rollover or a physical recovery ceremony; SEH discovery must never silently substitute a different key.

## 3. Main Computer Authenticator implementation

The **LMS-SLH-owned local Authenticator** is the **single identity lifecycle interface for all local actors**, including an ordinary identity designated as the installation default. Labels such as `device`, `person`, `phone` or `worker` are application metadata and access-policy context, **not independent identity APIs, types, or key-authority categories**. The shared authenticator **target contract** provides `create_identity`, `public_identity`, `sign_challenge`, `approve_action`, `lock_identity` and `reset_identity`; current API support covers explicit P-256 creation, listing, public metadata and challenge signing, not the entire target contract. Use established platform signing APIs (for example, P-256 ECDSA/SHA-256) rather than a custom cryptographic primitive. Bind every signed challenge to protocol/version, organization, SLH service identity, **signer credential ID**, operation, target/request ID, nonce, expiry and current protected session/channel. The SLH consumes challenges once, validates the signature and checks policy before issuing a short-lived scoped session. Private keys remain under local LMS-SLH custody and must never be sent to the SEH **or the backend SLH Organization Service**. Locally authorized signing may occur through LMS-SLH; the Organization Service stores enrolled public keys and checks roles.

Worker and locally running SLH applications supply built-in authenticators. The Android Authenticator & Wallet companion may use hardware-backed Android Keystore when available, and can require biometric/device confirmation. On Windows prefer native protected key storage/TPM where available. Browser WebCrypto-based keys are a compatibility option with clearly disclosed storage limitations. The phone's personal auth key is separate from any wallet key. The authenticator may approve a particular action without needing a marketplace wallet.

**Harness:** simulated actors receive *independent instances of the same signing-key type* and exercise the production verifier; their “device” or “person” labels do not affect the cryptographic algorithm or role eligibility. Only the act of human approval is simulated; signatures, nonce consumption, expiry, role checks, channel binding and denial paths are not mocked away. Test secrets must never work against production installations.

## 4. One device enrollment engine, two approval methods

Each first-time Worker, Requester, phone or remote administration client generates its own protected device keypair, verifies the intended SLH public identity through an authenticated enrollment channel, and submits a pending request with `device_public_key`, requested role, device label, user claim and public-key fingerprint.

The new-device screen offers an administrator **public display alias** (administrators opt in to listing; avoid disclosing privileged account details) and an optional **administrator-issued enrollment key**. Choosing an administrator alone never grants access.

- **Enroll with key:** An administrator has issued a short-lived, one-use, scoped enrollment capability, preferably bound to the exact pending request and device public key. After validation, the server calls the **same approval operation** that the administrator UI uses. A reusable administrator password, login token or private signing key is *never* entered on the user's computer.
- **Request approval:** Without an enrollment key, the request stays in the named administrator's **New Device Authorization Queue**. The administrator can review, annotate, approve, reject, or defer pending **physical inspection** of that machine. A policy may require that an administrator authenticate at the user's physical workstation before approval. Merely entering a transferable code does not prove physical presence.

Both approaches result in the identical `Known Device` record, role assignment, approval audit, and subsequent challenge/response authentication. Neither creates a certificate.

### Canonical enrollment API [TARGET]

```text
POST /v1/enrollment/requests            -> pending_request_id, device_key_fingerprint
GET  /v1/enrollment/admins              -> opt-in public display aliases only
GET  /v1/admin/enrollment/queue          -> authenticated admin; scoped requests
POST /v1/enrollment/approvals            -> pending_request_id + {admin_session OR one_time_capability}
POST /v1/enrollment/complete             -> proves pending device key possession
POST /v1/auth/challenge                  -> nonce, audience, action, expiry, binding
POST /v1/auth/verify                     -> signed proof -> limited session
POST /v1/admin/devices/{id}/revoke       -> admin-authorized revocation, session fencing
```

The server **normalizes the two authorization mechanisms into one `approve_enrollment(...)` decision**, enforces the administrator's rights and physical-presence policy, consumes the one-time capability transactionally, and records `approved_by`, method, device fingerprint, requested/actual permissions and time. A retry must not enroll twice. API names are proposed, not deployed routes.

### Device registry [TARGET]

A device record has immutable `device_id`, organization ID, public key and key fingerprint, plus editable `display_name`, assigned `user_id` or `unattended`, device type, roles, administrator notes, status (`PENDING`, `ACTIVE`, `SUSPENDED`, `REVOKED`), creator/approver, enrollment method, last-seen and active session metadata. Names, IP addresses and notes are descriptive, **not security identifiers**. Changing an assigned person or privileges must obey policy and be audited; replacing a device key requires a controlled re-enrollment/rotation path.

## 5. Personal authorization is distinct from device trust

A Known Device may have an unattended worker policy permitting limited computation with no human present. A user-operated device can require its assigned user's own authenticator at login, session refresh, job submission or high-risk actions. Administrative policy change, enrollment-key issuance, role elevation and Coolify mutation require a fresh **administrator** authorization that identifies the exact action and target. An administrator does not lend their private authenticator key to workers.

## 6. One SLH application: host mode and remote administrator mode

The identical SLH application runs in **Host an SLH** mode on the organization's actual SLH server or **Connect to an SLH** mode on another authorized administrator's machine. It is a thin client for **one authenticated SLH backend administration API**, not a second independent policy engine.

Local and remote administrators with **equivalent roles and conditions get the same authorized data and operations**: organization status, device registry (names, users, notes), enrollment queues, worker capacities and policies, virtual marketplace workers, wallets/credit balances (not secret keys), Pinky Promise local-fulfillment obligations and confirmed Reserve receipts (not secret keys or prompts), SEH/Coolify targets, deployment/harness controls and audit logs. Roles, session freshness and explicit physically local operations may restrict a particular action; *network location by itself is not an administrator privilege*. The older bootstrap-secret flow was local-only. The **[TARGET]** ORG-SLH root-authorized registration/recovery is authorized by possession of the durable bearer root and selected identity proof, **not** by local versus remote network origin.

A remote admin connects directly via LAN/VPN or through the optional SEH, proves their enrolled device identity and their own administrator authenticator, and obtains the same backend scopes. The SLH performs privileged actions without exporting wallets, Coolify tokens, or other secrets to the remote UI. Require step-up approval for high-impact actions, strict audit and explicit disconnect/logout.

## 7. SEH deploy and authentication without an organizational CA

**Coolify provisioning [TARGET]:** SLH holds a scoped Coolify API token locally; its admin (local or remote) approves an immutable deployment plan, and the SLH calls Coolify to create/start a pinned SEH runtime. A Coolify deployment result proves neither SEH identity nor user authorization. The ordinary public HTTPS/WSS certificate is handled at the SEH/Coolify proxy.

**Service pairing [TARGET]:** SLH generates a single-use, short-lived, deployment-bound SEH invitation. The newly installed SEH generates its own service signing/handshake keypair and proves possession; the SLH verifies the invitation, expected resource UUID, digest and public-key fingerprint and registers **that SEH public key**, without issuing a certificate. SEH in turn pins the intended SLH service public key. The SLH opens an authenticated outbound tunnel with established authenticated-encryption primitives. Relay key rotation, revoked/replaced SEH, cross-organization substitution and reconnects are tested separately.

**Remote client [TARGET]:** Worker, Requester, Android phone or remote SLH administrator reaches the SEH public WSS address and establishes a *separate* authenticated encrypted client-to-SLH channel. The SEH cannot decrypt private payloads, present itself as the SLH, authenticate a user, approve enrollment or authorize a wallet. The Known Device and personal-authenticator requests are **identical** to direct-LAN requests after the transport is established.

## 8. Offline, recovery, rotation and scope

- **USB/sneaker-net:** Export an invitation plus the *public SLH identity/fingerprint* and safe metadata; use removable-drive auto-detection only as a suggested location. The device generates its private key locally and sends its public-key enrollment request by USB; administrator approval/capability response returns by USB. No certificates or device/private wallet keys need to travel. Actual live worker sessions still need a network route, unless a separate offline job-carrier mode is implemented.
- **Device key rollover:** Approved user/admin authorizes replacing a device public key, proves possession of the new key, atomically changes the registry and fences old sessions. Stolen/revoked keys fail new logins immediately; active sessions are terminated according to security policy.
- **Lost phone/admin:** Revoke affected identity enrollments where authorized; use another administrator or the explicit **[TARGET]** `LOCAL-AUTH-KEY` grant to enroll a replacement identity, retaining org state. No arbitrary public-relay caller can reset administration without valid root authorization and signing-key proof.
- **SLH identity recovery:** Retain a protected backup and explicitly re-verify a replacement service public key across enrolled clients; never silently trust a changed identity advertised by SEH.
- **SEH compromise:** Revoke the paired relay public key and terminate its tunnel; replace/redeploy without resetting the organization's known-device registry.
- **Public TLS:** The internet-facing SEH still needs a normal publicly trusted TLS certificate. This does not recreate an organizational CA.

## 9. Acceptance gates (not yet implemented)

| Gate | Required proof |
| --- | --- |
| A0 | **[TARGET]** Persistent ORG-SLH root and identity-key possession are required for first administrator grant/recovery; wrong root denied regardless of network origin |
| A1 | Worker/Requester/phone creates unique local key; unapproved identity denied |
| A2 | Key-based and admin-queue enrollments converge on same approval code and registry state |
| A3 | Expired, replayed, cross-device or cross-org enrollment keys denied; physical-presence policies enforced |
| A4 | Known Device challenge verifies real signatures; replay/expiry/revocation/session misuse denied |
| A5 | Device label, user and notes editable without rewriting or weakening device identity |
| A6 | Same admin API and permissions from host app, remote LAN app, and SEH-connected app; sensitive secrets never exported |
| A7 | Remote and local Worker/Requester Known Device login parity; SEH cannot impersonate SLH or read plaintext |
| A8 | Scoped Coolify deployment and SEH service public-key pairing; revoked relay rejected |
| A9 | Simulated authenticators use genuine keys and production verifier, no always-true bypass |
| A10 | Marketplace wallets remain SLH-held; walletless worker work and private write-down have separate acceptance gates |

**Proof categories:** `NOT_IMPLEMENTED`, `NOT_TESTED`, `MOCK_PASS`, `ISOLATED_CRYPTO_PASS`, `LIVE_PASS`, `FAIL`. The earlier Phase 1 mock harness PASS proves only deployment planning and guards, not these authentication gates.
