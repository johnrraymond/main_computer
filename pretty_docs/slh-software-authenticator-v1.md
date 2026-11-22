# Software Authenticator — SLH / SEH identity factor (first implementation)

## Objective

Preserve the existing LMS-held P-256 identity, private signing key, ordinary
challenge/response, local root authorization, and existing enrollment-token
approval. Add a **distinct software OTP authenticator** for administrators
for administrator-side authorization of pending Worker/Requester enrollment.
The requesting device submits only its own access request; it does not collect
an administrator identity, passkey, or approval credential. The identity is
**not** the OTP and the OTP is **not** the P-256 private key.

## Implemented protocol

- Per-SLH, per-local-identity 256-bit random secret stored by LMS-SLH in a
  file with hardened local permissions. Nothing derives the OTP secret from
  the P-256 signing key or the marketplace wallet.
- TOTP per RFC 6238, HMAC-SHA256, eight decimal digits, 30-second steps.
- First pairing: selected administrator logs in to ORG-SLH using existing P-256
  challenge signing through pinned TLS. Admin-side software creates and stores
  a separate random factor for that *specific* ORG service signing-key
  fingerprint. The Authenticator secret is transferred only through the
  pinned, authenticated TLS session and encrypted with a 256-bit local ORG
  sealing key before storing it in the organization database.
- A pairing code proves seed possession. The time step used to pair is marked
  consumed; the next time step is the first eligible enrollment approval.
- Admin reveal: the trusted local CLI reads the current code. It never prints
  the pairing secret or the P-256 key. This is a temporary operator interface
  until the MCEL SLH page gets a sensitive ephemeral show/hide control.
- The existing `/v1/enrollment/approvals` endpoint accepts
  `{request_id, admin_identity, one_time_code}` as a **third, backend** approval method.
  The `admin_identity` must match the request's `selected_admin`, must have an
  active administrator device, and must have an enrolled software factor.
  This API capability does not authorize putting administrator fields or OTP
  redemption controls into the requester's enrollment dialog.
- The code is accepted at most once per identity/SLH 30-second step, using an
  atomic SQLite write that also approves the enrollment. Failed attempts are
  persisted; five incorrect numeric codes lock that administrator factor for
  300 seconds. The verifier accepts the current/adjacent TOTP step only.
- Physical-inspection policy remains an independent prerequisite. OTP can
  enroll Worker and Requester roles; new administrator grants still require
  authenticated P-256 administrator approval.
- P-256 session approval and existing random enrollment capability remain
  operational; no migration or deletion of existing identity records.
- An authenticated administrator can revoke this additional software factor;
  P-256 identity and sessions are not revoked. Re-pair requires explicit
  local reset after server-side revocation.

## HTTP contracts

Authenticated ORG-SLH:

```
GET  /v1/admin/authenticator/software-otp
POST /v1/admin/authenticator/software-otp
     {"service_fingerprint":"<ORG service P-256 fingerprint>",
      "secret":"<256-bit base32 TOTP secret>", "code":"<8 digits>"}
POST /v1/admin/authenticator/software-otp/revoke
     {}
POST /v1/enrollment/approvals
     {"request_id":"<pending request>", "admin_identity":"<selected admin id>",
      "one_time_code":"<8 digits>"}
```

Local loopback Authenticator API (operator-focused, distinct from ORG-SLH):

```
POST /v1/local/authenticator/identities/<id>/software-otp
     {"action":"activate"|"current",
      "service_fingerprint":"<ORG service P-256 fingerprint>"}
```

Local HTTP never discloses the pairing secret. Native Python local management
may retrieve it transiently during the authenticated pairing operation.

## CLI — first functional interface

From an interactive LMS operator's own computer (without a dongle):

```
python -m tools.slh_software_otp pair --identity <admin-device-id> --address <SLH-LAN-IP> --port 8899
```

If the SLH TLS pin is unknown, it returns the observed fingerprint but does
not pair. Independently verify the fingerprint, then rerun with:

```
python -m tools.slh_software_otp pair --identity <admin-device-id> --address <SLH-LAN-IP> --port 8899 --confirm-pin <verified-TLS-fingerprint>
```

The pairing prints the SLH **service** fingerprint (distinct from its TLS
certificate fingerprint) for future local code reveal:

```
python -m tools.slh_software_otp show --identity <admin-device-id> --service-fingerprint <SLH-service-fingerprint>
```

Administrator P-256 private keys and OTP pairing secrets are never printed.
Code reveal is deliberately an explicit operator command, not a resident
background operation.

Revoke the OTP factor using the authenticated administrator session:

```
python -m tools.slh_software_otp reset --identity <admin-device-id> --address <SLH-LAN-IP> --port 8899 --confirm-reset
```

This revokes the ORG verifier and then deletes the matching local factor. If
server-side revocation succeeds but the local removal fails, the operator must
manually reconcile the local factor; never silently generate new authority.

## Not implemented / next UI milestone

- MCEL SLH **Show Current One-Time Code / Hide / Copy** sensitive-content
  dialog, with bounded TTL and no persistence into MCEL application state,
  activity logs, localStorage or snapshots.
- Worker → Hub Choice → SLH/SEH → **Request Enrollment** dialog: choose
  the SLH/SEH and requesting local identity, submit a signed request, and show
  Pending / Approved / Rejected status. No administrator identity, passkey,
  one-time code, `For Admin Use Only` expander, or approval/redeem action
  belongs in this user-facing dialog.
- Administrator approval or rejection occurs separately in the SLH
  administrator enrollment queue, under that administrator's own authorized
  identity/session. An administrator-side OTP proof, if supported, stays
  entirely on the administrator side of that workflow.
- Native target discovery, SEH cross-network browser adapter admission, and
  live user/administrator acceptance in the running installation.

Do not claim the native Worker is enrolled merely because a request is
submitted. Verification and audited approval belong to ORG-SLH, not the
requesting browser.

## Known security and deployment limits

A copied short OTP is a *transferable* short-lived secret. It cannot be proof
of independent administrator presence or resist phishing as strongly as a
request-bound signature. Short-lived codes are limited to subordinate role
onboarding, rate-limited, single-use, and remain subject to SLH's physical
inspection policy. Protect the admin laptop and the TLS session. The shared
secret is encrypted at rest but the ORG host necessarily retains the means to
verify codes; its security is lower than a non-exportable hardware key.

The first patch is a backend/CLI acceptance candidate, not a claim that the
MCEL show/hide user experience or a live remote SEH path is complete.
