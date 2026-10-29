# SLH Phase 2A — Working Authentication Core and Full API Surface

**Status:** Implemented authentication/administrative backend; other service APIs registered as explicit `NOT_IMPLEMENTED` stubs. This is a loopback-only development service, **not** a LAN-ready production SLH or an SEH tunnel.

## Governing invariant

No device may receive an SLH session unless its own public signing key was enrolled by an administrator, the device proves possession of that key with a fresh challenge, and the user's/device's account is still active. Admin operations additionally require the enrolled account's `admin` role. A protected, locally presented bootstrap secret is the only exception that establishes the first administrator.

## Run the backend and harness

```powershell
python -m pip install -e .
python -m unittest tests.test_slh_phase2a_auth_api tests.test_slh_backend_seh_phase1 -v
python -m tools.slh_auth_harness verify-local
python -m tools.slh_auth_harness init --state-dir "$env:LOCALAPPDATA\MainComputer\slh"
python -m tools.slh_auth_harness serve --state-dir "$env:LOCALAPPDATA\MainComputer\slh"
python -m tools.slh_auth_harness contracts --state-dir "$env:LOCALAPPDATA\MainComputer\slh"
```

Read the bootstrap secret locally from `bootstrap.secret` inside the state directory and destroy any extra copies after first administrator setup. Do not put this secret, a private device key, or an API bearer token in SEH, Coolify, web pages, console logs, or ordinary USB exports. Use a directory restricted to the administrator account on Windows; the development CLI does not yet enforce production Windows ACL checks or provide hardware-backed recovery. Phase 2A now includes a development Authenticator CLI for persistent **unencrypted** P-256 keys (development only). This is not yet hardware-backed production key storage, and recovery remains unimplemented.

The local service accepts **only** `127.0.0.1` on port 8897. It denies nonloopback peers, suspicious Host headers and cross-origin requests. Do not publish it via Coolify, expose it through a general-purpose reverse proxy, or bind it to a LAN port. SEH end-to-end encryption has not been implemented.

## Main Computer Authenticator v1

The first proof format uses established ECDSA P-256 / SHA-256, not certificates, a private CA, or blockchain wallets. Each device generates and retains its own signing key. The SLH stores only its corresponding public key and the key fingerprint (SHA-256 of DER SubjectPublicKeyInfo). Python reference implementation: `main_computer/slh/authenticator.py`.

Signing object encoding is UTF-8 JSON, sorted keys, compact separators, `ensure_ascii=True`, `allow_nan=False`; signature is ECDSA DER bytes encoded base64url without padding. Keys are PEM-encoded `SubjectPublicKeyInfo`; the public PEM's exact line formatting must remain unchanged between signing and registration. This is a versioned protocol; later native and browser authenticators must match its test vectors and never reuse this key for marketplace wallet transactions.

### Canonical local endpoint and startup failure

The sole Phase 2A loopback API target is `http://127.0.0.1:8897` (canonical port defined in `main_computer/slh/ports.py`). The server, OpenAPI discovery, authenticator CLI, and manual use this constant. An explicit `--port` override is available for isolated labs only, not as a production configuration alternative. Port 8767 belongs to Main Log/OpenClaw; it must never be reused by SLH. `serve` prints "listening" **only after successful bind** and exits nonzero with `SLH_LISTENER_UNAVAILABLE` when occupied.

```powershell
Invoke-RestMethod http://127.0.0.1:8897/v1/bootstrap/status
```

### Persistent development Authenticator CLI

Use separate PowerShell terminals: one serves the SLH API and the other manages the first administrator. The private key is stored as unencrypted PKCS#8 PEM; the CLI does not prompt for a passphrase. Keep the key file local and out of source control.

```powershell
# Terminal 1 (SLH server; after `init`)
python -m tools.slh_auth_harness serve

# Terminal 2 (administrator authenticator setup)
python -m tools.slh_authenticator_cli create --device-id admin-pc
python -m tools.slh_authenticator_cli bootstrap-admin `
  --device-id admin-pc --org-id my-company --admin-id owner --admin-name "Organization Owner"
python -m tools.slh_authenticator_cli login --device-id admin-pc
```

Preserve the local unencrypted key file before first bootstrap: there is currently no completed recovery workflow. Do not put it under the server's `bootstrap.secret` directory or in source control. The CLI login verifies a real server session and immediately logs out without printing bearer tokens. Windows ACL/hardware-key integration remains future work. The current local key file has **no encryption** and should not be used outside development.

### First administrator

Use `GET /v1/bootstrap/status` to identify unclaimed local installation. The first administrator locally signs:

```json
{"version":1,"action":"slh.admin.bootstrap","org_id":"example-org","admin_id":"administrator-1","device_id":"admin-device-1","public_key":"<public PEM>"}
```

`POST /v1/bootstrap/admin` contains those identifying fields, `admin_name`, `bootstrap_secret`, and the base64url `proof` over the exact signed object. Only loopback may call it; it can succeed once. After commit, the bootstrap-secret file is removed. Future administrators must be added through ordinary enrollment and authenticated administrator approval. Recovery remains **unimplemented** and cannot bypass bootstrap closure.

### Enrollment: one operation, two authorization paths

1. The device creates a keypair, chooses an SLH administrator from `GET /v1/enrollment/admins`, and signs its full enrollment request object (with `version=1`, `action=slh.device.enrollment.request`, and the SLH's `org_id`).
2. `POST /v1/enrollment/requests` records the pending request, public key fingerprint, requested role, device name and claimed owner. The request does not authorize execution.
3. An authenticated administrator reviews `GET /v1/admin/enrollments`; optionally records an inspection **attestation** at `POST /v1/admin/enrollments/{id}/inspect`.
4. The administrator either uses `POST /v1/enrollment/approvals` with their active administrator session, **or** creates a one-time, request-bound enrollment key via `POST /v1/admin/enrollments/{id}/keys`. The client supplies that key to the **same** approvals endpoint.
5. In both cases the same transactional approval method creates a Known Device entry. Unused, expired, incorrectly scoped, or replayed keys do not grant authorization.

`physical_inspection_required` is a configurable policy. When true, approval and key issuance are refused until an administrator explicitly attests the inspection. **This is an administrator attestation, not an automated proof of physical presence.**

### Known Device authentication

1. `POST /v1/auth/challenge` with `device_id` produces a fresh, organization-bound, 60-second, single-use login challenge.
2. The device's authenticator signs exactly that returned JSON object.
3. `POST /v1/auth/verify` with `device_id`, `challenge_id`, and `signature` verifies identity and grants a random bearer session with a 15-minute lifetime.
4. The local API verifies session and current device/user revocation on each privileged request. `POST /v1/auth/logout` revokes the process-local session. All sessions are invalid after SLH restart.

A registered device authenticates a **machine**. Separate individual-user/phone MFA is specified but not implemented in this phase. Unattended worker policies, wallets, remote encrypted sessions and worker execution are not yet live.

## Implemented API

All routes below are prefixed by `/v1`.

| Group | Routes | Access |
| --- | --- | --- |
| Bootstrap | `GET /bootstrap/status`, `POST /bootstrap/admin` | Status public; bootstrap local-only secret |
| Discovery | `GET /capabilities`, `GET /enrollment/admins` | Public |
| Enrollment | `POST /enrollment/requests`, `GET /enrollment/requests/{id}/status`, `POST /enrollment/approvals` | Request public; approval admin session or request-bound key |
| Auth | `POST /auth/challenge`, `POST /auth/verify`, `GET /auth/session`, `POST /auth/logout` | Challenge public; session and logout require bearer |
| Admin state | `GET /admin/status`, `GET /admin/users`, `GET /admin/audit` | Administrator |
| Devices | `GET /admin/devices`, `GET /admin/devices/{id}`, `PATCH /admin/devices/{id}`, `POST /admin/devices/{id}/revoke` | Administrator |
| Enrollment queue | `GET /admin/enrollments`, `POST /admin/enrollments/{id}/keys`, `POST /admin/enrollments/{id}/inspect`, `POST /admin/enrollments/{id}/deny` | Administrator |
| Policy | `GET /admin/policies`, `PATCH /admin/policies` | Administrator |
| Connections | `GET /connections` | Administrator, existing six-profile catalog |
| Contract | `GET /openapi.json` | Public, generated from route registry |

Administrative metadata includes `device_id`, assigned owner, role, friendly name, notes, public-key fingerprint, status, and audit events. The device's private key is never part of a server record. Two administrators with equivalent roles reach the same API and permissions from the same authenticated client abstraction. **Actual remote transport parity is still untested until the SEH end-to-end channel exists.**

## Explicitly stubbed API

`/bootstrap/recovery`; `/connections/{id}/status`; `/admin/seh/plan`, `/admin/seh/deploy`, `/admin/seh/start`; `/seh/status`, `/seh/pair`, `/seh/tunnels`; `/workers`, `/workers/register`, `/workers/heartbeat`; `/requesters`, `/work`, `/routes`; `/marketplace`, `/marketplace/workers`; `/wallets`, `/credits`, `/accounting`, `/accounting/reconcile`; `/auth/people/challenge`, `/auth/people/verify`.

After applicable access checks, each stub returns HTTP 501 with `NOT_IMPLEMENTED`. The existing separate `SLHControlBackend` Coolify plan, preflight, create and start functionality remains available through its original guarded harness; no new administrative HTTP mutation permission is implied by the stubs.

## Testing and security boundaries

`tests/test_slh_phase2a_auth_api.py` uses real P-256 signatures for bootstrap, login, cross-organization and tampered proofs, one-time enrollment key scope, queue approval, simulated physical inspection policy, revocation, restart, local transport checks, and contract stubs. `tests/test_slh_backend_seh_phase1.py` continues to cover the old isolated Coolify controller. The combined passing tests **do not** prove production-grade rate limiting, UI authenticators, secure private key storage, LAN or SEH network readiness, SEH end-to-end encryption, marketplace wallet delegation, or credit write-downs.

Current first patch still needs subsequent work for Windows ACL validation, OS/hardware-protected client key persistence, user-linked multi-device accounts, administrator recovery, distributed session lifecycle, network rate limiting, and independently reviewed authenticated encryption before any production LAN or SEH exposure.

## Live HTTP enrollment lifecycle harness (Phase 2A follow-up)

The `tools.slh_enrollment_lifecycle` harness exercises both actual SLH enrollment authorization paths through **the same** `POST /v1/enrollment/approvals` endpoint, then proves that each newly approved device can perform Known Device challenge/signature login. It uses real P-256 keypairs and signed requests rather than bypassing cryptographic verification. It does not alter SLH authority logic or enable any LAN/SEH connectivity.

### Default isolated verification (does not touch your organization)

```powershell
python -m tools.slh_enrollment_lifecycle verify-isolated
python -m unittest tests.test_slh_enrollment_lifecycle -v
```

This creates a temporary SLH database and HTTP server bound to a random local port, bootstraps a disposable administrator and issues two independently signed device enrollment requests. The first is approved using the administrator's authenticated session; the second is approved using a short-lived, single-use key issued by that same administrator. Both requests appear in the pending queue and cannot authenticate until approval. Both can log in as Known Devices afterward, but neither gains admin permissions. The key cannot be replayed. The harness revokes the two test devices and verifies their final statuses.

### Explicit live verification of your running SLH

First ensure the existing SLH is listening on its canonical loopback endpoint and that `admin-pc` is the enrolled administrator authenticator. Then run:

```powershell
python -m tools.slh_enrollment_lifecycle verify-live `
  --admin-device admin-pc `
  --execute-mutations
```

**This is a mutating command.** It creates two temporary device identities, enrollment requests, approved worker records, and audit entries in the live SLH. By default it revokes both test workers and removes their local test-key files after verification; revoked device and audit records remain intentionally durable. It never revokes the administrator or prints its signing key, session bearer, or the issued enrollment key.

If the administrator has enabled `physical_inspection_required`, the live harness refuses to enroll anything automatically. A real administrator must inspect and attest the machines, and this harness must not falsely claim that physical presence was proven.

To intentionally leave the two test workers as enrolled Known Devices for subsequent experiments, explicitly add `--retain-devices`. Their development keys remain in the local authenticator directory. Never treat these unprotected development identities as production worker credentials.

The live harness can only reach the local `127.0.0.1` SLH service; it does **not** demonstrate remote SEH transport, phone authentication, actual Worker app integration, wallet delegation, or production-grade private-key custody.

### Evidence and failure behavior

A passing report contains `mode`, `org_id`, `administrator`, two per-device results (`queue` and `key`), `single_approval_endpoint=/v1/enrollment/approvals`, and `cleanup=verified_revoked` unless retention was requested. A failure returns a nonzero exit code. Failed cleanup is reported as failure rather than as a successful run. Use `--execute-mutations` only with an organization whose test records you are authorized to create.
