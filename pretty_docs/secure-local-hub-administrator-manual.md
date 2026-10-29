# Secure Local Hub (SLH) — Administrator and Operator Manual

**Document type:** Pretty Docs operator manual and implementation target  
**Design status:** SLH/Worker/SEH functionality remains a SPECIFICATION; Phase 1 backend connection catalog and guarded Coolify plan/create/start controller exist  
**Repository baseline:** `main_computer_test-20261027-161536.zip` (October 27, 2026)  
**Security authority and credential lifecycle:** [Device Authenticator, Enrollment and Remote Administration Architecture](slh-seh-device-authentication-architecture.md)  
**Related documents:** [Worker Managed Mode Specification](secure-worker-managed-mode-spec.md), [SLH Security and Protocol Contract](slh-identity-protocol-spec.md), [SEH Secure External Hub Coolify Deployment and Harness](seh-secure-external-hub-coolify-harness.md)

> **Read this first.** The steps below specify the intended Main Computer interface and behavior. They are not instructions to execute against today's application: the SLH app, managed-worker mode, phone companion, and SEH relay do not yet exist. A Phase 1 backend and isolated `tools.seh_harness` **control-plane** test tool now exist, but neither provides a real SEH relay, secure enrollment, nor phone authentication. Wherever a screen, button, API, or CLI command is proposed, it is marked **[TARGET]**. Existing verifiable behavior is marked **[EXISTS]**. Do not mistake the examples for a tested deployment or copy example IPs/domains into production.

## 1. Purpose: what a business is operating

The Secure Local Hub (SLH) is a **locally running Main Computer application and persistent backend service** owned by an organization. It manages a private pool of physical computers, enrolls workers and requesters, controls access to company resources, and optionally participates in the existing Ring 2 marketplace through multiple **logical** public workers and **SLH-controlled wallets**. Individual physical worker machines need neither marketplace wallets nor marketplace signing keys.

The **Secure External Hub (SEH)** is **not** the SLH. It is a separately deployable, publicly reachable **untrusted relay**. Its name describes its *trust position*, **not** permission to use plaintext HTTP or weak security. An administrator provisions and manages the SEH from the local SLH application; Coolify hosts the relay. Phones and authorized remote devices can reach their SLH through SEH while maintaining **end-to-end authenticated encryption to the SLH**. SEH has no device-approval key, wallet key, access-policy authority, or permission to inspect a private job.

The first enrollment of a **device** is administrator-approved. Once enrolled, its **human user** authenticates with their own authenticator when the SLH policy requires it. An enrolled device is not a perpetual login session. SLH and Secure Worker apps each supply a built-in authenticator capability; an optional Android Main Computer Authenticator & Wallet app can act as an independent signer/approver. The administrator may require two independent factors for privileged operations.

### 1.1 Roles

| Role | Owns / can do | Cannot do by default |
| --- | --- | --- |
| SLH administrator | Configure local SLH, approve first enrollment, issue/revoke credentials, set policy, connect/deploy SEH, control public worker delegation | Automatically sign on behalf of every worker/user |
| Organization SLH backend | Known Device public-key registry, authentication-policy enforcement, scheduling, local credit journal, public worker proxy | Prove unreported private work occurred |
| Secure Worker | Execute assigned jobs; authenticate by device credential and operator policy | Possess SLH marketplace wallet keys by default |
| Requester | Authenticate, request private work, receive results | Enroll itself or modify SLH policy without admin authority |
| Phone Authenticator/Wallet | Approve operations, authenticate person, optionally sign wallet transactions | Implicitly become a worker or organization administrator |
| SEH public relay | Accept authorized network tunnels, rendezvous, forward opaque frames, expose minimal health | Mint identities, authorize business actions, decrypt work, settle credits |
| Public Ring 2 Hub | Marketplace admission, credits, authorization, work settlement | Access private SLH jobs or act as SLH identity authority |

### 1.2 Normal topology

```text
External employee's phone / requester
       | HTTPS/WSS to company public domain
       v
SEH [untrusted relay] -- Coolify -- hub.secure.company.com (example)
       | opaque, end-to-end authenticated/encrypted relay stream
       | SLH establishes outbound tunnel (no inbound LAN NAT required)
       v
SLH [local authority] -- 10.x.x.x:8766 example -- admined via Main Computer on 127.0.0.1:8765
       |                 |                    |
       | encrypted auth  | encrypted auth     | isolated Ring 2 sessions
       v                 v                    v
Secure Worker A     Requester B       Public Main Computer Hub
(no wallet)         (private job)     (SLH wallets / logical workers)
```

## 2. Before you start

**[TARGET]** Prerequisites: an installed Main Computer instance on an administrator-controlled machine; OS permission to run a persistent local service, inspect network interfaces and removable media, and configure restricted firewall rules; a real private LAN or VPN; a backup plan; time synchronization; and a phone or another approved administrator authenticator. For remote access, also supply DNS for the public domain, a reachable Coolify deployment with proper authorization, and an approved container-image version or source release.

**[EXISTS]** The present Main Computer local viewport defaults to `127.0.0.1:8765` (`main_computer/app_control.py`); that administrative viewport must **remain loopback-only**. **[TARGET]** SLH receives a **separate** inbound listener, provisionally `8766/TCP`, bound to a selected legitimate LAN interface, with an authenticated encrypted protocol and firewall restrictions. Do **not** change the existing viewport's bind address to `0.0.0.0` to implement SLH.

### 2.1 Address and name discovery

1. **[TARGET]** Open **Applications → Secure Local Hub → Create / Open Hub**.
2. **[TARGET]** Select **Network → Discover interfaces**. Show every usable assigned address, interface identifier, subnet, and link status. Automatically prefer a suitable **assigned** `10/8` address; fall back to other private ranges and offer manual selection. Never invent a `10.*` address or assume one is free.
3. **[TARGET]** Select a private LAN interface and port (suggested `8766`). Do not bind to all interfaces unless the administrator explicitly permits each exposed network and firewall scope.
4. **[TARGET]** Set **Discoverable name**. Suggested mDNS default: `mc-slh.local` on networks supporting multicast DNS. A longer friendly name such as `hub.secure.main.computer.local` is a **configurable company DNS alias** only when organizational DNS actually resolves it. `.local` is special-use multicast DNS and multi-label behavior is not universally interoperable. Show the verified IP:port fallback.
5. **[TARGET]** Press **Check reachability**. Validate binding, OS firewall, DNS/mDNS announcement, verified SLH public-key identity, and at least one independent test client. Display a specific failed gate; never silently claim discoverability on remote VPN or segmented networks.
6. **[TARGET]** Press **Activate SLH** to start the backend as an OS-managed service independent of the window lifecycle. Closing the UI must not stop active workloads.

### 2.2 Initialize the SLH public identity and Known Device registry

1. **[TARGET]** Use **Host an SLH → Initialize** on the SLH machine, generate a locally protected SLH service keypair and organizational ID, and initialize the device/user/administrator registry. No internal CA, certificate signer or device CSR is created.
2. **[TARGET]** Display the SLH's public-key fingerprint to the administrator. Share it securely during enrollment by QR, USB or independently checked fingerprint; a discovered DNS name alone is not proof of identity.
3. **[TARGET]** Initialize the **first administrator through a locally protected setup session**, enroll their own authenticator, establish protected recovery and permanently lock the first-admin setup path. A remote unclaimed device may never establish the first administrator.
4. **[TARGET]** Confirm the SLH listener uses established authenticated encryption, that its service public key is pinned by enrolled clients, and that the ordinary local Main Computer viewport remains loopback-only.
5. **[TARGET]** Show **Device Registry**, **New Device Authorization Queue**, **Admin Accounts**, **SEH Relays**, **Sessions**, and **Audit** in the host admin interface. The same views are available to equally authorized administrators connecting from their own SLH app.

**Failure rule:** unknown, revoked, changed-key or incorrectly authenticated devices fail closed. Replacing the SLH service identity requires explicit authorized re-verification, never automatic trust of a new key from an SEH relay.

## 3. Enroll the administrator's authenticator and phone

### 3.1 First bootstrap and remote administration

1. **[TARGET]** On the physical SLH host, choose **Host an SLH → Create First Administrator**. This is the only special local-only administrative setup operation.
2. **[TARGET]** The administrator chooses an account identifier/display alias, enrolls a protected personal authenticator in their local SLH app or phone, confirms a challenge for **become initial SLH administrator**, and establishes recovery.
3. **[TARGET]** On successful setup, disable all unauthenticated initial-admin routes. New administrators are enrolled through the normal admin-approved device and user process.
4. **[TARGET]** From any other computer, install Main Computer and select **SLH → Connect to Existing SLH**; discover an SLH over LAN/VPN or enter its SEH domain. Verify the intended SLH identity; enroll the administrator's new device through the common enrollment flow; authenticate the administrator's own signer.
5. **[TARGET]** The remote SLH application then renders the **same backend-derived admin panels, data and actions** as the host-local UI, subject to the same roles and step-up policy. The host-only first-admin/recovery exception is explicit. Neither local nor remote UI receives hidden marketplace keys or Coolify tokens.

### 3.2 Android Main Computer Authenticator & Wallet

1. **[TARGET]** Choose **Add phone** from the local SLH app. Offer **QR enrollment**, a short-lived local web invitation, **USB-connected Android** transfer where supported, or **Copy signed APK to removable media** for offline delivery.
2. **[TARGET]** For an uninstalled phone, do not promise passive LAN discovery or automatic OS identification. Present a QR invitation or ask the user to connect the phone by USB; Android installation is explicitly user-approved. Never silently sideload or require debug mode for routine installation.
3. **[TARGET]** Distribute a verifiably signed APK from an authenticated source or a verified offline installer. Show package identity, version, digest/signing identity, and enrollment invitation expiry. Maintain compatibility with current Android install/developer requirements.
4. **[TARGET]** On the phone, install and open **Main Computer Authenticator & Wallet → Join organization**. Verify the organizational SLH root fingerprint by a separate trusted channel (local screen, prevalidated QR, or administrator-confirmed pairing code). A relay alone cannot establish trust.
5. **[TARGET]** Phone generates its own app/device authentication key in protected hardware where possible; send its public key and short-lived pairing proof. SLH administrator approves the pending enrollment request.
6. **[TARGET]** After device trust is established, enroll the **person** using their own authenticator. Present individual identity and assigned role. Do not share the administrator's approval credential with employees.
7. **[TARGET]** Optional: **Create wallet**, **Import wallet**, or **Skip wallet**. Phone device-auth keys and marketplace/wallet signing keys are separate. Import/export must have explicit authorization and encrypted handling.
8. **[TARGET]** Test approval: SLH presents a harmless named action; phone shows SLH identity, requested action, subject, expiry, and nonce; user confirms; SLH verifies the signed approval before executing.

The phone may later reach this SLH through LAN discovery **or** through an SEH relay. Both routes use the same previously established organizational trust.

## 4. Enroll workers, requesters, phones and administrator devices

Every first-time client generates a protected per-device signing keypair and submits its public key. The SLH administrator approves **that exact key**. No client certificate or internal CA is needed. Once approved, the client uses the **Known Device** authentication path; its user may additionally authenticate with their own built-in or phone authenticator per policy.

### 4.1 The two first-connection choices — same backend approval

1. **[TARGET]** In Worker, Requester, phone, or SLH **Connect** mode, discover or enter the SLH address and verify its public-key identity out of band.
2. **[TARGET]** The client generates its own private signing key locally; creates an enrollment request with proposed device name, user, intended type, public key and fingerprint; and shows an opt-in list of administrator display aliases.
3. **[TARGET]** Select an administrator. The UI offers **Administrator-issued enrollment key (optional)** and **Request administrator approval**.
4. **[TARGET]** With a valid one-time enrollment key scoped to the request or device public key, **Enroll with key** invokes the *same backend approval operation* as an administrator approving from the SLH queue. The key is never an administrator's reusable password or signing key.
5. **[TARGET]** Without an enrollment key, the request waits in that administrator's **New Device Authorization Queue**. The administrator may approve, deny, comment, assign an owner, or mark **Physical inspection required**. Company policy may require the administrator to log in at the device before approving it.
6. **[TARGET]** The common approval service verifies authorization and policy, consumes any key once, records administrator/method/time, and activates one immutable device identity. It then challenges the device to prove possession of its private key.
7. **[TARGET]** On later connections, device signs a fresh nonce-bound Known Device challenge. The user's separate authenticator is required only when the SLH says it is. Machine enrollment does not itself grant administration privileges.

### 4.2 Administrator device list

**[TARGET]** Open **SLH → Devices**. Each entry includes stable ID and public-key fingerprint, editable friendly name, assigned user or unattended-machine flag, device type (worker/requester/phone/admin client), permissions, status, last seen, enrollment approver/method and **administrator notes**. Support review/approve, inspect-before-approval, disable, revoke, key rollover and role edits. Notes/labels do not establish identity. All list and mutation functions use the identical authenticated admin API from host-local and remote SLH clients.

### 4.3 Offline / USB sneaker-net

**[TARGET]** Export a short-lived invitation, SLH public identity/fingerprint and setup instructions to an automatically offered removable drive (or administrator-chosen path). The worker creates its private key locally and returns the enrollment request/public key through USB. The administrator approves using the same queue/approval API; the device imports a signed or authenticated **approval record**, not a client certificate. No administrator/private key is copied to USB. Offline storage/transport by itself does not permit performing live work; subsequent network sessions still require authenticated encryption and current policy.

### 4.4 Authentication policy after enrollment

**[TARGET]** Admin sets: device-only unattended worker permissions, required personal authenticator by role/action, session expiration, high-risk admin step-up, remote SEH eligibility, physical-inspection enrollment rule, and revoke/disable behavior. The administrator's personal authenticator is never shared with workers. The SEH and direct LAN paths call the same SLH verification and authorization functions.

## 5. Operate the worker pool

1. **[TARGET]** Open **Workers → Fleet**; inspect each machine's identity, key status, operator role, capabilities, heartbeat, capacity, current job source, and policy. Enrollment is separate from activation for paid marketplace work.
2. **[TARGET]** Set **Private Priority / Public Allowance / Maintenance**. Advertise only genuinely available capacity to the public Hub; existing public jobs may not be silently displaced by new private requests.
3. **[TARGET]** On **Marketplace**, create/select organizational wallet(s) held by SLH, and advertise as many logical public worker sessions as policy/capacity permits. SLH authenticates those sessions using existing Ring 2 rules and forwards paid jobs to walletless physical workers **after public authorization**.
4. **[TARGET]** On **Private Requests**, select allowed requester groups and worker pools. Requester authenticates to SLH; SLH selects and reserves a worker and returns an authenticated internal route, or uses an authorized local relay. Private prompts and responses do not enter the public Ring 2 Hub or SEH unless explicitly allowed by approved routing policy; private job content should remain end-to-end encrypted across any relay.
5. **[TARGET]** On **Credits → Pink-promise journal**, show organizational **settled/spendable** credits, reserved allowance, pending internal routing charges, journal sequence, last reconciled batch, and failures. Example: public earnings `+10,000`, 20 internal leases at illustrative `100` credits `-2,000`, available `8,000` after settlement, ignoring other holds. These figures are examples, not fixed tariffs.
6. **[TARGET]** Run reconciliation. The SLH **promises** to report private use honestly; cryptography cannot prove undisclosed private jobs occurred. Do not suggest a trustless proof of every job. Enforce idempotency, allowances and double-spend prevention across multiple SLHs.

**Accounting distinction:** worker earnings are not necessarily immediately spendable; wait for marketplace completion/settlement and bridge/credit-account transfer per existing rules. Never derive a private charge from the text of a private prompt, and never make an external Hub inspect it.

## 6. Provision and administer an SEH on Coolify from the local SLH application

An **SEH** is the **Secure External Hub**, a public-facing relay and its accompanying deployment/acceptance **harness**. There is no separate SLH deployed into Coolify for this use case. The SLH remains local.

### 6.1 Administrator workflow — no manual server edits required

1. **[TARGET]** Open **SLH → Remote Access → SEH Relays → Add SEH**.
2. **[TARGET]** Select **Deployment type: Coolify**. Enter a trusted Coolify API base URL (`https://coolify.company.com`), choose the **project**, **environment**, **target server**, and public domain (example `hub.secure.company.com`). Require the domain to be owned and DNS-controlled by the enterprise. Prefer a scoped Coolify API token with service/application create/deploy/read permissions; store it in OS secret storage, never in a browser setting or exported config file.
3. **[TARGET]** Press **Test Coolify access**. Read-only: check API reachability, token permissions, target UUIDs, proxy/HTTPS availability, DNS readiness, and image release compatibility. No resources created yet.
4. **[TARGET]** Press **Preview SEH deployment**. Display the intended container image **pinned by immutable digest**, service/Compose resource, environment variables (secret values redacted), public domain and HTTPS termination, storage requirements, health-check paths, and created resource identifiers. Distinguish *new* from *adopt existing*.
5. **[TARGET]** Press **Deploy SEH** and approve with the administrator's authenticator. Local SLH uses the Coolify API to create/update the designated SEH resource; it must not deploy an SLH identity database, wallet service, private worker runtime, or uncontrolled ingress into the LAN.
6. **[TARGET]** Wait for Coolify deployment to report success, then check HTTPS certificate, SEH application health, and public WebSocket reachability. Persist Coolify resource UUID, deployment UUID, desired config digest, and SLH-to-SEH relay identity locally. Do not equate HTTP 200 with an accepted end-to-end relay.
7. **[TARGET]** Establish an **outbound, mutually authenticated** SLH connection to the new SEH. Bind the SEH to a particular organization/SLH identity using one-time approval; require an audit record. SEH only brokers ciphertext to an authorized local SLH tunnel.
8. **[TARGET]** Run **SEH Harness → Test remote phone connection**: a pre-enrolled phone outside the LAN connects to `https://hub.secure.company.com`, validates its SLH's previously verified identity, authenticates independently, and performs a harmless signed request. Verify the public relay cannot forge authorizations or read private payloads.
9. **[TARGET]** Mark **Ready for remote users** only when all required SEH harness gates pass, not merely when Coolify deploys a container.
10. **[TARGET]** From SLH UI, use **Pause remote access**, **Rotate relay credentials**, **Redeploy pinned version**, **Run SEH harness**, **View sanitized logs**, and **Retire SEH**. Stop/delete actions require explicit target confirmation and must never touch unrelated Coolify resources.

The full provision/deploy/update/rollback implementation contract is [SEH Coolify Deployment and Harness](seh-secure-external-hub-coolify-harness.md). The SEH harness is named **SEH**, not "SLH harness".

### 6.2 SEH service-key pairing and remote administration [TARGET]

The administrator (from the physical SLH app **or** an enrolled remote SLH app with matching permissions) approves an exact Coolify plan and the SLH uses its scoped Coolify API key. The token stays inside the SLH's protected storage, never in the deployed SEH. Coolify's public proxy manages the normal internet-facing HTTPS/WSS certificate; the organization issues **no internal certificates**.

1. **[TARGET]** Verify the exact project, server, resource UUID, domain, immutable image/config digest and required deployment permissions.
2. **[TARGET]** Deploy and start the pinned SEH service; report `RUNNING_UNPAIRED`, not `Ready`, until identity and relay tests pass.
3. **[TARGET]** Generate a one-time SEH pairing capability scoped to the expected deployment. The new SEH creates its own service keypair and submits its public key/proof of possession.
4. **[TARGET]** SLH verifies pairing, expected Coolify resource and administrator approval, and **registers the SEH public key**. SEH pins the intended SLH service public key. There is no CSR, organizational CA, or SEH client certificate.
5. **[TARGET]** SLH establishes an authenticated outbound encrypted connection and rejects unknown/revoked relay keys. Remote clients independently establish an end-to-end encrypted session with the pinned SLH identity **inside** the public HTTPS/WSS SEH path.
6. **[TARGET]** Test a remote administrator using their *own* SLH app: known-device login, personal admin signer, full role-equivalent admin API access and action-scoped audit. SEH must be unable to read admin data, grant roles, or impersonate either endpoint.

See the '+newlink+' for exact bootstrap/enrollment and harness security rules.

### 6.3 Manual Coolify fallback for administrators (TARGET until SEH image exists)

If GUI-to-Coolify orchestration is not yet implemented, the documented **future** manual path is: log into the company's Coolify instance, create a **Git-backed Docker Compose Application** when source-tracked deployments are preferred or a **Docker Compose Empty Service** for a managed custom stack; select server/project/environment; configure a published HTTPS domain mapped to the SEH relay's internal HTTP/WebSocket listener; reference secret variables through Coolify; deploy a **built and signed, pinned SEH image**; inspect deployment logs and run the local SLH's SEH connection and acceptance checks.

**Do not execute this against production today:** the inspected snapshot contains no runnable SEH image, no SEH Compose stack and no SLH application controls to administer one. Existing `tools/coolify_hub_service.py` deploys the **public marketplace Hub**, which is a different target and must not be repurposed as an SEH.

Current Coolify references (verify against the installed Coolify version): [API overview](https://coolify.io/docs/api/overview), [Docker Compose resources](https://coolify.io/docs/applications/builds/docker-compose), [Service model](https://coolify.io/docs/services), [Networking and TLS](https://coolify.io/docs/core/networking-in-coolify).

## 7. Day-2 operations and emergency procedures

| Situation | Administrator action | Expected result |
| --- | --- | --- |
| Worker lost/stolen | Revoke device public key and fence active sessions | Other Known Devices retain independent identities |
| Phone or personal authenticator lost | Revoke relevant personal/device keys; use approved secondary admin or local recovery | Lost signer cannot approve actions |
| Worker reassigned | Update user, permissions and notes through admin API | Stable cryptographic device ID retained; change audited |
| Device signing key compromised | Revoke device and require approved new enrollment/key rotation | No silent trust reuse |
| SLH service identity compromised | Local protected recovery and explicit client re-verification | SEH cannot silently replace SLH identity |
| SEH compromised | Revoke paired relay public key, disconnect tunnel, deploy/approve replacement | No ability to read E2E payload or approve users |
| Coolify API token compromised | Revoke/rotate via Coolify; update SLH secret reference | Management rights removed without changing device identities |
| USB drive lost | Expire/revoke unused invitations; generate replacement | No reusable administrator key stored on media |
| Remote admin laptop lost | Revoke its Known Device identity and sessions | Administrator account can survive and enroll a replacement under policy |
| Marketplace connection offline | Bound internal spending allowance, queue honest pink-promise write-downs | No double-spend on reconnect |

**Observability [TARGET]:** SLH listener, public-key identity/fingerprint, device approval queue, person and device sessions, known-device changes, worker/public virtual sessions, SEH tunnel health, Coolify deployment IDs and sanitized audit. Never log authenticator private keys, one-time enrollment capabilities, marketplace wallet secrets, Coolify tokens or private prompts.

## 8. Definition of done

- Local physical SLH installation initializes one protected first-admin path; remote initial-admin takeover is rejected.
- Worker/Requester/phone/remote-admin clients generate their own device signing keys. **Enroll with key** and **admin approves from queue** execute the same policy-controlled approval code.
- Administrators can require physical presence before approval and see device name, owner, notes, roles, fingerprint, status and audit.
- The same Known Device and optional personal-authenticator challenge works directly on LAN and through SEH.
- A locally logged-in SLH administrator and equivalently privileged administrator using their own SLH application receive the same authorized backend information/actions, including SEH/Coolify and marketplace controls, without disclosing secrets.
- SEH deploys through a scoped Coolify key, pairs its service public key, relays end-to-end encrypted traffic, and cannot forge authentication or administrator approvals.
- SLH can proxy authorized public worker sessions under its own wallets and record/reconcile private pink-promise charges independently.
- SEH harness and managed-auth harness report real negative tests and separate mock/control-plane/live states accurately.

**Implementation status:** specification only beyond the Phase 1 connection catalog and guarded Coolify deploy planning. No existing security or UI acceptance gate is claimed passed by this document.
