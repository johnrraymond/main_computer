# SLH target selection: local versus network (Oracle V2)

> **Historical milestone, not the current SLH administration oracle.** The separate-app and remote-no-login requirements in this document are superseded by `mcel-slh-unified-admin-oracle-v4.md`. The existing network TLS, origin, target, and authority-isolation safety constraints remain in force.


The first question at `/applications/slh` is **which SLH the user intends to
administer**. SLH's always-installable Local Management Service and the
organization's operational Service are distinct authorities. Neither the
presence nor the initialization of the former, by itself, proves that an
administrator identity has been created or enrolled for the latter.

## Two browser-native target tabs

- **This Computer:** `/applications/slh/local`. Local discovery, setup, login,
  and Worker administration remain under the existing, accepted MCEL SLH
  semantic application. The old `/applications/slh` and
  `/applications/slh/workers` URLs remain usable, as does
  `/applications/slh/local/workers`.
- **Network SLH:** `/applications/slh/network`. A separate, read-only MCEL
  semantic application for identifying an *existing* remote Organization
  Service. No local setup action, resident process start, local bearer, or
  local administrator identity is reused. Browser refresh and modified-click
  behavior follow normal real `href` navigation.

The tab strip is a generated navigation boundary between two distinct
semantically sourced application targets. Workflow content is still compiled
and composed by MCEL; no authored network application HTML is introduced.

## Remote identity verification at this milestone

The Network SLH tab accepts three inputs: the private-network IP address,
Organization Service port (default 8899), and the externally verified SHA-256
fingerprint of its TLS certificate. The local viewport performs one bounded
same-origin, read-only POST to a backend that connects directly, checks the
fingerprint on the **same TLS session before sending HTTP**, then reads
`/v1/org/identity` and `/v1/org/status`. IP literals are limited to RFC1918
IPv4 or ULA IPv6 (no DNS, loopback, public destinations, redirects, or proxy).

A successful network observation means **identity verified, not authenticated**.
There is no remote administrative credential or privileged operation in this
milestone. Remote administrator onboarding/authentication and Worker controls
must be implemented as separately authorized steps; displaying a successful
TLS observation must never imply they have occurred. The supplied fingerprint
must be verified out-of-band before using it. The browser never handles a
private key or administrator bearer for this operation.

A local SLH is never auto-started at Windows boot by this patch.

## Oracle V2

`mcel_oracles/target-selection/oracle.v2.json` captures:

- a target selection *before* local or remote lifecycle work;
- two canonical, browser-native tabs and existing local URL compatibility;
- separate local and network semantic app identities;
- no implicit inference of administrator enrollment from Local Management
  Service initialization;
- pinned private-network TLS and read-only unauthenticated remote inspection;
- failed/unsafe targets rejected before any network connection or HTTP;
- no local bearer leakage or local setup on the network tab.

The V1 operational-workflow oracle remains **frozen and unchanged**. It still
applies to the *local* SLH workflow after selecting This Computer. Do not
replace its accepted state-transition checks with V2's target-selection tests.

## Installation: one direct patch against the 2026-11-08 15:42:14 snapshot

This ZIP is constructed directly against `main_computer_test-20261108-154214.zip`.
It includes the two updated existing files **as well as** all new source,
network inspection, and V2 oracle files. No builder, second patch or manifest.

From the repository root:

```powershell
python new_patch.py "$HOME\Downloads\mcel_slh_target_tabs_v2_direct_patch_20261108_154214.zip"
```

Then restart the viewport and run:

```powershell
python -m pytest -q tests/test_mcel_slh_target_tabs_oracle_v2.py
python -m pytest -q `
  tests/test_mcel_operational_conditions.py `
  tests/test_mcel_operational_workflow_oracle.py `
  tests/test_mcel_slh_workflow_oracle.py `
  tests/test_mcel_slh_workflow_browser_oracle.py
```

Check `/applications/slh/local` and `/applications/slh/network` in a browser.

**Acceptance is not implied for live remote administration**: V2 only verifies
a remote Organization Service identity and its public operational status.
