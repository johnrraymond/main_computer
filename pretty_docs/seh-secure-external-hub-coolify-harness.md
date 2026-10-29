# SEH — Secure External Hub Coolify Deployment, Administration, and Acceptance Harness

**Canonical name:** **SEH** — **Secure External Hub**, the public-facing encrypted relay and its **SEH harness**.  
**Not:** the Secure Local Hub (SLH), a Ring 2 marketplace Hub, or a Coolify-hosted organization SLH.  
**Status:** specification plus Phase 1 implementation update. A guarded Coolify control-plane facade and isolated `tools.seh_harness` now exist; real SEH relay, service pairing, authenticated E2E tunnel, service-key rotation, phone enrollment and live remote harness **are not implemented**.  
**Credential lifecycle and provisioning security:** [Device Authenticator, Enrollment and Remote Administration Architecture](slh-seh-device-authentication-architecture.md)  
**See also:** [SLH Administrator Manual](secure-local-hub-administrator-manual.md), [Secure Worker Managed Mode](secure-worker-managed-mode-spec.md), [Trust and Protocol Contract](slh-identity-protocol-spec.md).

## 1. The SEH target

An enterprise installs and runs an **SLH on an administrator-controlled private machine**. That SLH app provides a wizard to deploy, configure, monitor, test and retire an **SEH** relay on a Coolify service/application, under an owned public domain such as `hub.secure.company.com`.

```text
                   Internet / public DNS / TLS
Remote phone ---- HTTPS/WSS ----> SEH (Coolify)
Remote requester - HTTPS/WSS -----|   |
Remote worker ---- HTTPS/WSS -----|   | ciphertext + bounded routing metadata
                                     |
                        outbound mutual-auth tunnel
                                     |
                       corporate firewall (no default inbound port)
                                     |
                      SLH on company LAN, locally administered
                             |      |        |
                     identities    workers   private usage ledger
```

**"Secure External" describes the authenticated, encrypted external access service; it does not make the relay a trusted identity authority.** SEH is exposed through HTTPS/WSS and hardened against unauthorized access, but remains an untrusted intermediary for end-to-end SLH communications. Public-domain TLS protects the leg from a phone to the public endpoint; **a second, independent end-to-end authenticated encrypted session** protects the phone/requester-to-SLH conversation against the SEH itself. SEH is not allowed to mint trust, authorize enrollment, store an SLH identity private key or wallet signing key, or learn plaintext private prompts.

### 1.1 Existing repository distinction

| Inspected source | Existing behavior | SEH implication |
| --- | --- | --- |
| `tools/coolify_hub_service.py` | Deploy/modify the public marketplace Hub service | **Do not** reuse as SEH runtime; only reuse generic Coolify API patterns after isolating side effects |
| `tools/coolify_deploy_twiddle.py`, `tools/coolify_twiddle.py`, `main_computer/deployment_controllers.py` | Existing Coolify/deployment utilities | Potential integration patterns, not an implemented SEH deployer |
| `deploy/coolify/local-docker/*`, `tools/local-prod/*` | Local Coolify smoke environment and related tooling | Reuse environment for isolated SEH deployment tests; no production token for smoke |
| `deploy/hub-topology/*-coolify-deployment.json` | Existing real marketplace Hub placement data | **Not** SEH topology or secure-organization configuration |
| `main_computer/app_control.py` | Local viewport, default 127.0.0.1:8765 | SEH administration UI must remain local; do not expose viewport on internet |
| `tools/stable_hub_lab/run_lab.py` | Existing public Hub lab and marketplace settlement tests | **Not** SEH harness; SEH gets separate CLI and proof artifacts |

## 2. Provisioning model and Coolify resource types

Coolify currently offers **Git-backed Docker Compose Application** and **Docker Compose Empty Service** paths. Both can serve an SEH release; source-controlled Git Compose with immutable release provenance is preferable when available. Use a unique resource name and mark the remote target as an **SEH**, never a Ring 2 public Hub. Coolify API resource type and endpoint choice must be selected from the **actual Coolify version** and stored as part of target state, not assumed by naming alone.

**Production prerequisites:** company-owned domain/DNS; reachable Coolify API with narrowly scoped bearer token; project + environment + destination server; public HTTPS proxy with WebSocket pass-through and valid certificate; signed, pinned SEH image (or reviewed Git revision); container resource limits; log retention; and organization approval. Self-hosted Coolify installations may require API enablement and IP allowlisting. Use Coolify's current documentation and actual API schema for API calls; the following are **semantic operations**, not literal existing routes.

- Coolify docs: https://coolify.io/docs/api/overview
- Compose app/service types: https://coolify.io/docs/applications/builds/docker-compose
- Service model: https://coolify.io/docs/services
- Networking / proxy and TLS: https://coolify.io/docs/core/networking-in-coolify

### 2.1 Target fields stored on the local SLH

```yaml
# Illustrative target schema ONLY; this file is not a valid current deployment manifest.
schema: main-computer.seh-target.v1
id: enterprise-seh-primary
organization_id: example-org
local_slh_id: example-slh
name: Company Public Relay
public_url: https://hub.secure.company.com
coolify:
  base_url: https://coolify.company.com
  resource_kind: compose_application  # OR compose_service
  project_uuid: <resolve-with-authorized-read-api>
  environment_uuid: <resolve-with-authorized-read-api>
  server_uuid: <resolve-with-authorized-read-api>
  resource_uuid: <assigned-after-create>
  credential_ref: <local-secret-store-reference-only>
release:
  image_digest: <pinned-reviewed-image-digest>
  source_commit: <approved-release-commit>
  desired_config_digest: <sha256-of-canonical-redacted-config>
relay:
  allowed_org_ids: [example-org]
  outbound_tunnel_required: true
  end_to_end_encryption_required: true
  public_https_required: true
  listener_internal_port: 8080  # candidate, not a current code constant
```

The *configuration record* must never contain Coolify bearer-token plaintext, SLH identity private keys, SLH wallet private keys, private prompts, or long-lived invitation secrets. A `credential_ref` resolves to local OS secret storage only after SLH administrator authorization.

### 2.2 SEH container contract (future runtime)

The eventual image should:

1. Be independently versioned, signed/pinned, non-root and least-privileged; run only the **SEH relay**, no organization scheduler or wallet logic.
2. Bind a private container HTTP/WebSocket listener (candidate `8080`); let Coolify-managed proxy publish `https://hub.secure.company.com`. Do not map an extra direct public host port unless intentionally required and firewall-controlled.
3. Provide `GET /health/live` (process liveness, no secrets) and `GET /health/ready` (relay loop, config, resource health). A separate privileged diagnostics route may report authorized SLH tunnel state only to authenticated SLH admins; do not leak organization metadata publicly.
4. Provide authenticated `wss` relay rendezvous that maps an approved `slh_id`/organization to its existing outbound encrypted tunnel; reject unknown, expired or disabled identities. Per-connection quotas, message-size limits, timeouts, abuse rate limits and tracing are mandatory.
5. Persist **minimal** durable configuration/relay identity and audit metadata only if needed; never retain raw forwarded messages or full decrypted payloads. Health check must not require exposing a private SLH.
6. Support graceful drain and rolling update with bounded reconnect/retry. Never report a successful remote action merely because a frame was forwarded.

**Example Compose sketch — not runnable until image and endpoint exist:**

```yaml
services:
  seh:
    image: example.invalid/main-computer/seh@sha256:<PINNED_DIGEST>
    restart: unless-stopped
    environment:
      SEH_PUBLIC_ORIGIN: https://hub.secure.company.com
      SEH_RELAY_IDENTITY_REF: ${SEH_RELAY_IDENTITY_REF:?}
      SEH_AUDIT_MODE: metadata_only
    expose:
      - "8080"
    read_only: true
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "<SEH_PROVIDED_HEALTH_CLIENT>", "--check", "ready"]
      interval: 30s
      timeout: 5s
      retries: 3
```

`example.invalid`, the digest placeholder, the environment variable names and the health client are **deliberately non-functional specification placeholders**. Do not put this sketch into Coolify as a running service. An implemented image must provide documented variables and a real healthcheck; Coolify's public domain must map HTTPS/WSS to its actual internal listener port.

### 2.3 SEH identity registration without an organizational CA [TARGET]

The SLH uses a **scoped Coolify API key** to deploy one exact pinned SEH service. The service generates its own private relay key, proves possession of its public key, and redeems a short-lived one-time invitation bound to the authorized organization, Coolify resource UUID and image/config digest. After a privileged administrator approves, the SLH registers the SEH public key; the SEH pins the SLH service public key. There is **no organizational CA, CSR, or internal client certificate issuance**.

SLH then initiates its outbound authenticated encrypted relay tunnel. Remote Workers/Requesters/phones/SLH admin applications use the **same Known Device and personal-authenticator APIs as direct SLH clients** through independently authenticated E2E sessions. The SEH is not authorized to approve devices or users, decrypt protected content, or issue sessions. Public HTTPS/WSS certificates at the Coolify proxy remain necessary and are distinct from organizational authentication.

**Remote administrator parity:** a separate computer with the SLH application may use the SEH to reach the organization's actual SLH backend. After device enrollment and the administrator's personal authentication, it receives the same role-authorized admin data/actions as the host-local SLH UI; secrets stay on SLH.

## 3. SLH administrator application — remote deploy wizard

Place under **Applications → Secure Local Hub → Remote Access → SEH**. Only users with `seh.deploy` / `seh.admin` roles may act. A company can create multiple SEH deployments; each has its own Coolify resource UUID, domain, image digest, target state, and relay identity.

### Screen A — choose target

- **[TARGET]** `Add SEH` → `Managed by Coolify`.
- Enter Coolify API base URL; click **Connect** to perform read-only API/permission check.
- Choose from **fetched** authorized projects, environments and servers; do not ask operator to guess UUIDs if discovery works.
- Enter public hostname, DNS-ownership verification option, approved release channel/version, and expected remote-access policy.
- Specify whether new resource or existing **explicitly recognized SEH** should be adopted; refuse unknown resources.
- Secrets are entered through secure UI and stored as local secret references; never included in downloadable reports.

### Screen B — preflight (read-only)

**[TARGET]** `Validate target` runs:

| Check | Pass requirement | Failure action |
| --- | --- | --- |
| Coolify identity | API responds; authorized scope on chosen team | Fix URL/token/allowlist |
| Resource target | Selected server/project/environment exist and user has rights | Select valid target |
| Domain/DNS | Public hostname is controlled and resolves as intended | Configure DNS; don't deploy for another organization's domain |
| HTTPS/proxy | Proxy capable of TLS and WebSocket upgrade | Fix Coolify proxy configuration |
| Image | Release pinned and signed, digest allowed | Choose approved image |
| Unique ownership | New name/UUID not already an unrelated Hub or app | Choose new resource / explicit adoption |
| Organization policy | Admin authenticated; remote access permitted | Approve policy before deploy |
| Egress | SLH can initiate outgoing authenticated tunnel | Configure outbound firewall/proxy |

Preflight **must not** create, mutate or delete Coolify resources. Show structured evidence and exact reasons, not a generic red error.

### Screen C — preview and approval

**[TARGET]** `Preview deployment`: render diff of new or updated remote services, domain, ports, image digests, environment keys (values redacted), resource limits, volumes, health gates, requested Coolify actions and possible disruption. Require authenticated admin approval with action text including target public URL, Coolify instance ID and resource UUID. The preview is a proposal; a hash of previewed desired state is bound to approval to prevent unnoticed mutation between preview and execution.

### Screen D — deploy and observe

**[TARGET]** `Deploy SEH` executes an idempotent, checkpointed workflow:

1. Revalidate target/approval and obtain a scoped Coolify credential from local secret store.
2. Create or update **only** the intended SEH resource using Coolify's supported application/Service API, tied to explicit UUIDs and a recorded desired-state digest.
3. Set image/source revision, internal port, healthcheck, public domain and **secret references** through the supported API; do not grant the SEH broad Coolify or machine administration rights.
4. Trigger deployment; persist Coolify deployment UUID; poll status with backoff and timeout; stream **sanitized** deployment diagnostics to SLH UI.
5. Verify external certificate/HTTPS identity and actual WebSocket upgrade through the public domain, then internal SEH runtime health. `HTTP 200` alone is insufficient.
6. Pair SEH relay identity with the intended SLH via an administrator-approved challenge; SLH establishes authenticated outbound tunnel. No auto-enrollment of an arbitrary remote relay merely because it exists at the configured URL.
7. Run **SEH harness** active probes, including real phone/requester end-to-end authentication, and **negative security probes**. Only then mark target **Ready**.

If any step fails, mark deployment **Incomplete** and show exactly which resource was created, whether it remains running, and the safe retry/rollback action. Never silently delete a resource that may contain unrelated organizational state. Retry must not create duplicate containers/domains or accidentally share relay credential material across targets.

### Screen E — operations

**[TARGET]** `SEH Relays` shows target state (`Not configured`, `Preflight passed`, `Deploying`, `Deployed`, `Tunnel connected`, `Ready`, `Degraded`, `Paused`, `Retired`), domain/SSL, Coolify resource UUID, image digest, service health, last successful phone probe, active sessions, last harness result and sanitized alerts.

Operators can `Run harness`, `Pause new remote sessions`, `Disconnect relay`, `Rotate relay credential`, `Deploy approved update`, `Rollback approved version`, and `Retire resource`. Each destructive action names the intended UUID and requires authenticated administrative approval; local SLH operation continues if SEH is down.

## 4. SEH harness — the named test target

**Canonical test/tool names [TARGET]:**

```text
tools/seh_harness.py
# or module:
python -m tools.seh_harness

tests/test_seh_relay_contract.py
tests/test_seh_coolify_deployment.py
tests/test_seh_end_to_end_remote_auth.py

deploy/seh/compose.yml
runtime/reports/seh-harness/<run-id>/report.json
```

**Phase 1 update:** `tools/seh_harness.py` and `tests/test_slh_backend_seh_phase1.py` exist and implement control-plane profiles, plan, read-only preflight, guarded create/start, and isolated mock tests. The proposed `test_seh_*` tests, runnable relay image, actual TLS/WSS tunnel, and phone authenticator harness are **not implemented**. Some CLI subcommands below remain targets, not executable Phase 1 commands. The **SEH harness** must not be conflated with `tools.stable_hub_lab.run_lab`, the public marketplace Hub lab, or the existing Coolify Hub deployer. SEH deploy verification and marketplace accounting are separate gates.

### 4.1 Harness subcommands (future CLI)

```powershell
# MIXED TARGETS — verify-local and verify-coolify are Phase 1 commands; verify-remote, verify-security and report are NOT implemented
python -m tools.seh_harness preflight --target runtime/seh/targets/local-smoke.json
python -m tools.seh_harness verify-local --isolate
python -m tools.seh_harness verify-coolify --target runtime/seh/targets/local-smoke.json --read-only
python -m tools.seh_harness verify-remote --target runtime/seh/targets/local-smoke.json --require-real-wss --require-e2e-auth
python -m tools.seh_harness verify-security --target runtime/seh/targets/local-smoke.json --simulate-untrusted-relay
python -m tools.seh_harness report --latest
```

**Guardrail:** `verify-local` creates only isolated disposable local relay/SLH/phone simulators in an explicitly marked test namespace. `verify-coolify --read-only` must not modify any deployed resource. A separate `deploy --execute --approve-target <exact-uuid>` option may be added later for explicit mutation, and must never be the default for harness verification. Local tests use ephemeral real signing identities and never production SLH keys, wallet private keys or Coolify secrets.

### 4.2 Verification gates

| Gate | Setup / positive check | Negative check | Evidence |
| --- | --- | --- | --- |
| I0 — isolate target | Correct Coolify UUID/project/server; SEH name/type distinct from real Hub | Reject public Ring 2 Hub targets | Resolved immutable target evidence |
| I1 — deployment | Pinned SEH release created/updated; deployment status healthy | Reject unapproved image, duplicate name, wrong UUID | Coolify resource/deployment UUID + digest |
| I2 — public HTTPS | Valid cert, expected SAN, HTTPS reachable | Wrong public TLS certificate/hostname rejected | TLS probe summary, no secrets |
| I3 — WebSocket | Real WSS upgrade + bounded frames through proxy | Malformed/replayed/oversized frames rejected | Handshake/protocol test |
| I4 — outbound pairing | Only approved SLH service public key connects to its SEH tunnel | Unapproved SLH cannot claim another org | Tunnel ID + approved fingerprint |
| I5 — remote phone | Enrolled phone validates SLH identity end-to-end, authenticates and completes scope-limited action | Public relay cannot present alternate SLH key or impersonate administrator | Signed challenge result (redacted) |
| I6 — remote worker/requester | Only policy-enabled remote user/worker/admin connects | Disabled role denied; no policy bypass | Role decision IDs |
| I7 — privacy | Private opaque test payload round-trips to approved SLH/worker | SEH cannot decrypt, log or modify without failure | Canary digest checks; sanitization review |
| I8 — compromise | Simulate relay replay, cross-tenant substitution, reconnect, outage | Cannot forge admin approval, approve new device, or recover wallet keys | Attack matrix with failures |
| I9 — lifecycle | Pause/reconnect/rotate/redeploy without identity confusion | Old revoked relay keys or Known Devices rejected | Stateful event sequence |
| I10 — recovery | Restart/redeploy + state restore / controlled rollback | No duplicate resources, lost approved-device records, or stuck persistent permissions | Prior/current digests, UUIDs |
| I11 — logging | Sanitized report reproducible across runs | No private keys, Coolify tokens, prompts, signed replayable auth present | Redaction scan + proof |

**Pass conditions:** all required gates must pass; a skipped gate is recorded as **NOT TESTED**, not PASS. Any test that substitutes a mock for an external Coolify action must say **mocked**, not **live deployment verified**. Include actual timestamps, environment, build/source digests, expected domain, test result, and known limitations. No production credit transfers are necessary for SEH testing.

### 4.3 Harness report shape (future)

```json
{
  "schema": "main-computer.seh-harness-report.v1",
  "run_id": "example-isolated-test-run",
  "target_kind": "seh",
  "test_mode": "local-isolated",
  "status": "NOT_EXECUTED",
  "coolify": {"resource_uuid": null, "deployment_uuid": null},
  "release_digest": null,
  "slh_fingerprint": "redacted-or-public-fingerprint-only",
  "gates": [{"id": "I0", "status": "NOT_TESTED", "evidence_path": null}],
  "redaction_check": "NOT_TESTED",
  "limitations": ["Illustrative schema only; no SEH runtime was executed"]
}
```

The report in the specification is deliberately `NOT_EXECUTED`; no tool execution is claimed by documenting it.

## 5. Administrator walkthrough — local setup, remote admin, worker and phone

1. **[TARGET]** On the organization's SLH host choose **Host an SLH**, bind the dedicated LAN interface and establish the first administrator account using a locally protected setup path; initialize SLH service identity and public-key fingerprint, **not a CA**.
2. **[TARGET]** Install Secure Worker on another machine; generate its device keypair, choose an administrator alias, and submit a pending enrollment. Approve by one-time admin-issued enrollment key **or** from the administrator's SLH queue, using the **same approval API**. Honor physical-inspection-required policy.
3. **[TARGET]** Install Main Computer on a separate administrator laptop. Open **SLH → Connect to Existing SLH**, enroll that device, authenticate the administrator personally, and verify that the remote admin gets the **same role-authorized UI data and operations** as on the SLH host.
4. **[TARGET]** Enroll an Android phone through approved device-key registration; personal approval signer is distinct from any phone marketplace wallet. Confirm harmless action approval directly on LAN.
5. **[TARGET]** From either equally authorized administrator SLH app, configure Coolify server/project/environment and `hub.secure.company.com`, preflight, approve the exact pinned-image plan and deploy SEH using the SLH-held Coolify API key.
6. **[TARGET]** Pair SEH and SLH *service public keys*, establish authenticated SLH outbound tunnel, test public HTTPS/WSS and independent end-to-end encrypted channel. Report `Ready` only after harness passes.
7. **[TARGET]** Move phone/remote admin outside LAN and reconnect via SEH. Verify pinned SLH identity, Known Device login, user/administrator signer, permission parity and that SEH cannot see private API payloads.
8. **[TARGET]** Revoke an enrolled device or paired SEH and verify immediate denial/fencing. Disable SEH and confirm LAN SLH and private scheduling remain operational.

Until these are implemented, the sequence remains an acceptance target rather than a deployable runbook.

## 6. Implementation sequence, resource ownership, rollback

| Patch | Deliverable | Gate |
| --- | --- | --- |
| I1 | SEH wire protocol + isolated relay image and Compose target | local identity, WSS, opacity and traffic limits tested |
| I2 | SLH outbound tunnel client + service public-key pairing and admin approval | no unapproved cross-organization tunnel |
| I3 | Coolify target registry, secret reference and preview/read-only preflight | cannot mutate with read-only actions; no secret leaks |
| I4 | Coolify provisioning, deployment and status reconciler from SLH app | idempotent target UUID, pinned release, remote health |
| I5 | Local phone/worker/remote-admin path + per-user policy and E2E auth | real remote end-to-end authorization; relay MITM rejected |
| I6 | `tools.seh_harness` + reports + fault/compromise campaign | I0–I11 pass in isolated mode; live tests separately documented |
| I7 | Operational lifecycle (rotation, pause, rollback, retire) | controlled recovery without unrelated Coolify mutation |

Rollback must retain local administrative access and private worker functionality. If Coolify is unavailable, local SLH continues scheduling; if relay is compromised, admin revokes relay identity from **local SLH first** and then redeploys/rotates. Removing an SEH service cannot roll back the SLH's Known Device registry, personal authenticator enrollments, or organizational credit ledger.

## 7. Naming and delivery checklist

- [ ] User manual calls the private installation **SLH** and the public relay **SEH**.
- [ ] Coolify deployment resource and harness are **SEH** targets.
- [ ] No public-facing **SLH** management server is deployed merely to provide relay access.
- [ ] Local Main Computer admin viewport remains loopback-only.
- [ ] End-to-end SLH identity and encryption are independent of SEH's public HTTPS.
- [ ] First device enrollment always uses the common SLH admin approval service, by one-time enrollment key or authenticated queue approval.
- [ ] Every worker/requester/operator subsequently uses **their own authenticator** if organization policy requires it; host and remote SLH administrators have role-equivalent API access.
- [ ] Device keys, personal authenticator keys, SLH/SEH service keys, wallet keys and Coolify tokens remain separate.
- [ ] SEH harness verifies deployment, real WSS, remote authorization, untrusted relay attacks, and lifecycle.
- [ ] A failed/skipped harness step is not reported as a completed production deployment.
