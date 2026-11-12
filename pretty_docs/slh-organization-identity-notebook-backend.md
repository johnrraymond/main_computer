# ORG-SLH identity notebook — backend contract

**Status:** Implemented ORG-SLH backend and coordinated MCEL semantic notebook, November 11, 2026. This specification applies identically to the local ORG-SLH and a remotely connected ORG-SLH because both use the same authenticated Organization Service API and each organization owns its own durable state.

## Governing model

An identity in the left MCEL navigation is a **local signing identity** belonging to LMS-SLH. An enrolled identity is an **organization-authorized device** in ORG-SLH. Selecting a local signer does not automatically enroll it, and connecting to a remote ORG-SLH does not confer administrator authority.

The bottom-of-workspace `Organization Identities` notebook has three views, scoped strictly to the currently selected organization:

| Notebook tab | Backing operation | Meaning | Row actions |
| --- | --- | --- | --- |
| **Pending** | `GET /v1/admin/enrollments` with `status == "pending"` | Signed enrollment requests awaiting administrator decision | **Accept**, **Reject** |
| **Enrolled** | `GET /v1/admin/devices` with `status == "active"` | Devices authorized to authenticate | **Delete** |
| **Trash** | `GET /v1/admin/devices/trash` | Revoked devices retained for possible restoration | **Reactivate**, **Remove** |

All three reads require an **authenticated ORG-SLH administrator** bearer session. The existing enrollments endpoint also includes historical `approved` and `denied` requests; the notebook must filter these out of Pending. A disconnected or unauthorized notebook may retain its visual position but must show an explicit unavailable/authorization state; it must never leak another organization's cached rows.

## HTTP actions

These are **ORG-SLH endpoints**, served over its existing TLS transport for remote clients. The local management service is not a second source of organizational authority. All row actions require an authenticated ORG-SLH administrator unless explicitly noted.

| User action | HTTP request | Result |
| --- | --- | --- |
| **Accept** | `POST /v1/enrollment/approvals` body `{"request_id":"<id>"}` | Uses existing approval engine; enforces physical-inspection policy when enabled |
| **Reject** | `POST /v1/admin/enrollments/{id}/deny` body `{}` | Marks the request denied; does **not** enroll or place it in Trash |
| **Delete** | `POST /v1/admin/devices/{id}/revoke` body `{}` | Sets device to `revoked`, records Trash timestamp and administrator; invalidates its bearer sessions immediately; retains credential for possible Reactivate |
| **Reactivate** | `POST /v1/admin/devices/{id}/reactivate` body `{}` | Restores only a device in Trash with an active user; clears Trash marker; requires the device to authenticate again with a fresh challenge |
| **Remove** | `POST /v1/admin/devices/{id}/remove` body `{"confirm":"REMOVE_DEVICE"}` | Permanently removes recoverable organization device, approved enrollment material, and orphaned user record; retains security audit events |
| **Empty Trash** | `POST /v1/admin/devices/trash/empty` body `{"confirm":"EMPTY_TRASH"}` | Permanently removes every currently trashed device in one authority transaction, returning `removed_count`; retains audit evidence |
| **Remove items after 30 days** | `PATCH /v1/admin/policies` body `{"remove_trash_after_30_days":true}` or `false` | Saves organization policy. `GET /v1/admin/policies` reports its current value. |

An administrator must confirm Remove and Empty Trash explicitly in the interface before sending their confirmation payloads. The backend also rejects missing/wrong confirmation payloads. Do not reinterpret confirmation strings as proof of authentication: the administrator bearer is still mandatory.

### Durability and recovery

- A `device_trash` table stores `device_id`, `trashed_at` (Unix epoch seconds), and `deleted_by` with a foreign key to the existing device row. The existing `devices.status` continues to be `active` or `revoked`, preserving existing authorization guards and API clients.
- Existing databases are migrated without deleting enrolled or revoked credentials. Previously revoked devices appear in Trash with `deleted_by="legacy-revocation"`; their Trash clock begins at schema upgrade, because the original revocation time is unknown.
- `remove_trash_after_30_days` is **off by default** and persists in the organization's existing `policies` table, independently of the frontend.
- When enabled, the ORG-SLH process scans Trash at startup and every 60 seconds. It purges entries at least `30 * 24 * 60 * 60` seconds old, measured from the persisted `trashed_at`. It is **not** dependent on a browser, session, open notebook, or Windows boot setting. If ORG-SLH is stopped, overdue entries are handled after the service is next started.
- A sweep failure does not silently wipe unrelated organization state or crash the listener. `/v1/org/status` reports the sweep's latest exception class and last removed count, without including private credentials.
- Reactivate is impossible after permanent removal; rejoining requires a **new signed enrollment request and approval**. Permanent removal does not erase the immutable audit history, another device's local keys, or backups controlled outside ORG-SLH.

### Non-negotiable authority checks

- Reject unauthenticated requests and authenticated non-admin principals for every administrator operation, including reads, policy changes, and bulk removal.
- Reject revocation of the **last active administrator** (`LAST_ADMIN_PROTECTED`). This guard also applies when Delete targets the current administrator identity.
- Deleting immediately revokes the device's existing authenticated sessions. Reactivate must not restore any previously issued bearer tokens.
- Reject Reactivate of a device not actually in Trash (`DEVICE_NOT_IN_TRASH`) and reject inactive users (`USER_NOT_ACTIVE`).
- Reject permanent removal of an active device. Delete must occur first so access has already been revoked.
- Scope every operation and retention policy to its own ORG-SLH SQLite authority; never select an organization by a caller-controlled ID.
- An administrator Reject action retains its signed request in history as `denied`; it does not grant access or erase audit evidence.
- Physical inspection and the existing signed enrollment approval engine remain in force for Accept. The notebook must not introduce a bypass through a new enrollment endpoint.

## Backend acceptance

`python -m pytest -q tests/test_slh_identity_trash_backend.py`

This suite covers Pending → Enrolled → Trash → Enrolled, permanent Remove, Reject behavior, last-admin safety, authorization, explicit destructive confirmation, durable 30-day retention, real process-independent sweep execution, legacy migration, and organization isolation. The existing cryptographic enrollment/authentication suites remain necessary regression gates.

## MCEL notebook implementation

The semantic source `mcel_apps/slh/application.js` declares a single typed
`record-notebook` for both This Computer and Network SLH. The shared MCEL
semantic composition creates a lower-workspace collection **after** the
existing configuration and lifecycle workstreams; it does not relocate the
navigation or right-side inspection/activity rail. This is a general record
notebook semantic and is not a hand-authored HTML layout or hard-coded SLH
subpage. Other MCEL keyed collections retain their previous composition.

The three tabs execute allowlisted, authenticated organization reads.
Accept, Reject, Delete, Reactivate, Remove, Empty Trash, and the persisted
30-day retention checkbox execute existing ORG-SLH authority methods through
the selected target's pinned encrypted transport. Delete, Remove, and Empty
Trash require explicit user confirmation. The Trash footer is outside the
notebook's record scroll area and only appears when Trash is selected.

A local administrator explicitly logs in with an LMS-SLH signing identity;
no automatic enrollment or authentication occurs on page mount. The local
adapter keeps the ORG-SLH bearer inside its JavaScript port closure and
uses a same-origin, loopback-only bridge with a TLS-pinned ORG-SLH upstream.
The remote adapter keeps only an opaque, short-lived ticket; the server
resolves it to a pinned ORG-SLH connection, verifying the certificate for
every request. A remote non-administrator can connect but cannot authorize
the notebook. Switching authentication status clears notebook state.

The notebook renders explicit authorization-required, loading, empty, and
failed states; errors are recorded by the existing MCEL operation receipt
machinery in the right-side activity/details rail. Successful mutations
refresh the organization lists and retention policy. Failed mutations never
optimistically remove or create a row; only the ORG-SLH authority can
change enrollment or revocation state.

### Coordinated oracle and regression gates

- `tests/fixtures/mcel_oracles/identity-notebook/oracle.v1.json` is the
  machine-readable notebook contract, including tab-specific intents,
  fixed policy footer, placement, confirmations, and target isolation.
- `tests/test_mcel_slh_identity_notebook_oracle_v1.py` verifies the compiled
  semantic surface, actual local and remote browser actions, row IDs,
  credentials/target scoping, policy footer, denied actions, and state clearing.
- `tests/test_slh_identity_trash_backend.py` verifies the resident authority,
  enrollment/revocation/restoration/removal, durable retention, and the
  policy-enforcing process-independent sweeper.

Run: `python -m pytest -q tests/test_mcel_slh_identity_notebook_oracle_v1.py tests/test_slh_identity_trash_backend.py`.

A separate existing V4 worker-bank parity oracle remains deliberately red
for unimplemented worker-summary/refresh/rename semantics; the identity
notebook does not claim to close those unrelated gates.
