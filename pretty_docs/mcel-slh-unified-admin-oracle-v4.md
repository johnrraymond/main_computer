# MCEL SLH Unified Administration — Oracle V4 (November 10, 2026)

## Contract and precedence

**V4 is the controlling oracle for SLH administration.** V2 remains the target-navigation,
private-address, TLS-pin, and read-only *preauthentication inspection* regression suite.
V3 remains the local observational truth and separation-of-authorities suite.
V1 remains the operational sequencing baseline, except that its historical multi-step
local-bootstrap presentation does not override the subsequent accepted single
**Login Administrator** entry for fresh local installations.

The former V2 requirement for **two different MCEL semantic applications** and the
former V3 assertion that network targets must **never show administrator login**
are withdrawn. They verified a historical incomplete implementation and must not
block the corrected architecture.

## Normative invariant

For an equivalent verified organizational state, administrator role, and target
capabilities, the local and remote SLH use **one** generated MCEL administration
surface, shared components, observation schemas, intents, and consequence policies.
Only the target-specific connection, trust verification, startup/recovery, and
preauthentication enrollment steps differ. Changing targets never transfers
organizational authority.

### Connection and enrollment

This Computer: select target; deliberately start the local SLH when stopped;
select an ordinary LMS-SLH signing identity; present **Login Administrator**.
Authorized fresh-local establishment may run behind that action; an established
organization does **not** implicitly grant a new administrator. A lost/invalid
administrator requires an explicit recovery flow. Do not install a Windows boot
task for SLH.

Network SLH: select a stable target; validate private IP literal and independently
verified SHA-256 certificate pin; confirm the remote Organization Service identity;
select an ordinary local LMS-SLH signer; present **Login Administrator**.
Remote login uses the selected organization's challenge, device enrollment and
role verification. An unrecognized identity may submit a signed enrollment request,
but only a target-authorized administrator can approve it. A TLS fingerprint match
is **not** proof of administrator authorization. No new "remote admin identity"
class is allowed.

### Authenticated administration

Both targets expose the same Overview and Worker Bank semantics and operational
capabilities. The same read-only observations, signed mutation requests, audit
receipts, UI components, and state applicability rules apply. Additional capabilities
must be driven by observed authority and service capability, **not** target kind.
Local-only service startup/initial establishment remain constrained to the local
target; they never become remote admin actions.

### Target and session isolation

A target is an immutable, validated connection definition. Each connection checks
the TLS pin **before sending any HTTP or bearer**, including after reconnects.
Administrator tokens are target-bound and short-lived, absent from browser URLs,
localStorage, persistent state, logs, and generated MCEL observations. The viewport
may use an opaque, origin- and target-scoped server session. Switching target
must never reuse another target's token, follow an unverified redirect, downgrade
transport, or silently execute on localhost. An expired/revoked session returns
to login without resetting enrollments. Failed Worker observation is
`NOT_OBSERVED`, not an observed empty fleet.

Every semantic destination must have a real, refreshable `href` URL. The current
local alias URLs are retained. The remote Worker Bank minimum canonical address
is `/applications/slh/network/workers`, with stable identified-target URLs
required as multi-target support expands. Modified clicks retain native behavior.

## Tests, expected state, and implementation gate

The machine contract lives at
`tests/fixtures/mcel_oracles/unified-administration/oracle.v4.json`.
The executable tests are
`tests/test_mcel_slh_unified_admin_oracle_v4.py`.

The V4 suite includes contract-consistency checks that should pass immediately,
plus **deliberately red implementation gates** for shared MCEL app identity,
common administrative intents, common Worker/Overview projections, shared surface,
and browser-native remote Worker Bank navigation. These failures prove the
current implementation does **not** meet the new oracle, rather than indicating
a faulty test.

The preexisting V2/V3 assertions that mandated *separate app IDs* and *no remote
login capability* have been retired. Existing real network validation, request
origin checks, prior local login, and administrator role security remain in force.
Do not mark V4 accepted until all red gates have become green **and** isolated
fake-target integration tests exercise remote challenge/signature enrollment,
target session binding, wrong-pin rejection, logout/expiry, and prevention of
cross-target Worker mutations. Semantic parity is necessary but not sufficient
for operational acceptance.

```powershell
python -m pytest -q tests/test_mcel_slh_unified_admin_oracle_v4.py
python -m pytest -q tests/test_mcel_slh_target_tabs_oracle_v2.py tests/test_mcel_slh_organization_oracle_v3.py
```

This is an **oracle-only** patch. It does not claim the common administration
implementation or remote administrator authentication exists yet.
