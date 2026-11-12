# MCEL organization identity notebook — coordinated oracle V1

**Status:** Semantic oracle and UI implementation, 2026-11-11.

## Governing contract

The single semantic SLH application renders a persistent **Organization
Identities** notebook in the lower main workspace of both This Computer and
Network SLH. The selected ORG-SLH is the sole authority for its records.
Neither the browser nor the local signing identity directory is an enrollment
authority. The existing configuration, Service Lifecycle, left navigation,
and right-side Service Details/Operation Evidence rail retain their placement.

The notebook is a reusable `record-notebook` MCEL node: a typed list with a
record key and label, bound read intent, and per-row typed consequential
intents. Its generated composition is a workspace collection downstream of
configuration and lifecycle, with no paired HTML source or authored pixel
coordinates. Ordinary MCEL collections are not reordered by the notebook rule.

## Required semantics

| Tab | Records | Row actions |
| --- | --- | --- |
| Pending | Pending enrollment requests; never denied or approved requests | Accept, Reject |
| Enrolled | Active enrolled organization devices | Delete → Trash |
| Trash | Revoked retained device records | Reactivate, Remove |

Each row carries its real organization-issued `id`; displaying a name must
never substitute that name for the action's record key. Failed reads have an
explicit unavailable state. Without administrator authentication, no
organization records are rendered or fetched.

Below the **Trash record scroll area**, place the independently anchored
checkbox **Remove items after 30 days** and button **Empty Trash**. The
checkbox reads and writes the organization's persisted policy and reflects
the confirmed saved value after a failure. Empty Trash is a button and
requires human confirmation. Delete and Remove also require human
confirmation. Accept uses existing approval logic and cannot bypass a
required physical-inspection attestation.

Consequential operations must be authorized against the current organization's
administrator session. Success refreshes the changed organization lists;
failure preserves records and produces an operation receipt visible in the
existing right-side activity rail. A change of authenticated target clears
all old tab and policy state, so a disconnected notebook never shows data
from the previous session. The existing ORG-SLH authority alone decides
last-administrator protection, access revocation, restoration, purge, and
retention durability.

## Acceptance and instrumentation

The fixture `tests/fixtures/mcel_oracles/identity-notebook/oracle.v1.json`
contains exact semantic IDs, required actions, fixed footer, authority
expectations, and target identity. Acceptance is executable in
`tests/test_mcel_slh_identity_notebook_oracle_v1.py` using the actual compiled
MCEL page in Chromium and a deterministic fake HTTP boundary. The test uses
both local and remote target routes, clicks real controls, checks action
arguments and target-bound tickets, and verifies the separate scroll and
footer containment and failed-authority behavior.

Backend lifecycle and retention acceptance is in
`tests/test_slh_identity_trash_backend.py`.

```powershell
python -m pytest -q tests/test_mcel_slh_identity_notebook_oracle_v1.py tests/test_slh_identity_trash_backend.py
```

The exercise is self-contained: it does not start ORG-SLH, mutate a real
organization, or require a public network. Existing unrelated worker-bank
parity gates are not relabeled as satisfied by this notebook change.
