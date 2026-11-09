# MCEL SLH Organization Observation and Administration — Oracle V3

Source baseline: `main_computer_test-20261108-160444.zip` (November 8, 2026, 16:04:44).

## Purpose

The `This Computer` SLH tab displays three authorities separately:

1. **Local management service:** the always-installed, loopback-only service at `127.0.0.1:8897`. Running and listening does not establish an SLH organization, administrator, or network listener.
2. **SLH organization authority:** the durable organization setup record **and** an active enrolled administrator device. Legacy migrated setup records may be initialized with no active administrator.
3. **Organization Service:** a distinct, explicitly configured TLS listener. Its configured binding is an IP and port, not the HTTP URL used to obtain its local status.

The `Network SLH` tab remains a separate administration target. A private-network remote identity must be verified with its pinned certificate. No local bearer is forwarded or reused across tabs. Remote administrator login is **not** provided by this patch.

## Identity-ownership boundary for the next UI phase

The loopback service is **LMS-SLH**, owner of **one unified identity lifecycle** for all local actors, including the ordinary identity designated as this installation's default. There is **no machine/user identity-type distinction**. The backend Organization Service is the **authority for organization enrollment and roles**, not the store for LMS-SLH private keys. `administrator_enrollment.local_credential = NOT_VERIFIED` in V3 means **local credential discovery has not occurred**; it does not mean a key is missing or should be re-created. Automatic default identity creation and local identity selection using the **same** identity API are **[TARGET]**, documented in [LMS-SLH Identity Ownership](lms-slh-identity-ownership.md), not an implemented V3 oracle gate.

## Public backend observation contract

`GET /v1/local/status` retains the existing `main-computer.slh-local-status.v1` schema and adds coarse, read-only fields:

- `administrator_enrollment.state`: `NOT_ESTABLISHED`, `ENROLLED`, `MISSING`, `NO_ACTIVE_DEVICE`, or `INCONSISTENT`, derived from authoritative active administrator and device rows. The response never includes names, device identifiers, administrator keys, or enrollment secrets.
- `administrator_enrollment.local_credential`: always `NOT_VERIFIED`. Enrollment does not prove that this computer currently has a usable private key; successful device authentication remains necessary.
- `organization_service.bind_host` and `api_port`: the configured TLS **listening** address. Null means unconfigured or invalid; it is not inferred from the local-management observation URL.
- `organization_service.network_access`: `NOT_CONFIGURED`, `LOOPBACK_ONLY`, `BIND_CONFIGURED_REACHABILITY_UNVERIFIED`, or `UNVERIFIED`. Neither an explicit non-loopback bind nor a locally verified TLS handshake proves that a remote computer can reach the port.
- `organization_service.listening` and `state`: retain the independently verified local listener readiness and process status.

No admin identity, key, bearer token, local setup secret, or remote network address is accepted as a client-supplied observation target on this path.

## MCEL semantic projection

The Overview observation contains explicit strings for setup record, organization established state, administrator enrollment, local credential verification, actual endpoint, network binding, local TLS verification, and a suggested next step. The control-plane URL remains the provenance of the observation only; it is **not** shown as the Organization Service listener.

The generated action applicability contract is:

- No initialized setup and no administrator: offer bootstrap on demand after verifying the resident service.
- Initialized, but no active administrator device: show `REQUIRES_RECOVERY`; do **not** offer bootstrap or login. An authenticated recovery process is not yet implemented.
- Administrator enrolled, organization unconfigured: permit explicit configuration using an established administrator identity.
- Configured, disabled: permit explicit activation.
- Running with locally verified TLS, enrolled administrator and no active session: show login. Local credential possession is still unverified until authentication succeeds.
- Running with active administrator session: authorized Worker management continues to use the existing backend authority.
- Unknown/missing/contradictory observation: fail closed for consequential actions and explain the missing evidence.

Loopback-only binding is explicitly described as local-only. Non-loopback binding is explicitly described as reachability **unverified**. The Network SLH tab remains separate.

## Independent Oracle V3

The contract file is `mcel_oracles/organization-observation/oracle.v3.json`; its acceptance suite is `tests/test_mcel_slh_organization_oracle_v3.py`. It checks public enrollment safety, real configured binding, status projection for eight distinct scenarios, generated browser action gating, and separation of canonical local/network tabs.

V3 **extends**, rather than replaces, the existing Target Selection Oracle V2 and frozen Operational Workflow Oracle V1. The only V1 test fixture change is that a simulated initialized SLH now includes **explicit** admin enrollment and verified local TLS facts. V1's contract and expected behavior are not changed.

Run from the repository root:

```powershell
python -m pytest -q `
  tests/test_mcel_slh_organization_oracle_v3.py `
  tests/test_mcel_slh_target_tabs_oracle_v2.py `
  tests/test_mcel_slh_first_slice.py `
  tests/test_mcel_slh_first_run.py `
  tests/test_mcel_slh_authenticated_workers.py `
  tests/test_slh_gateway_startup.py
```

The snapshot exporter omits earlier untracked `mcel_oracles` files. This patch includes V2's canonical source and contract when absent from the supplied snapshot; it does not regenerate or change frozen V1. Run the V1 oracle against the existing installed checkout where its original contract is available.

### Remaining live acceptance

The isolated/Chromium suites do not establish actual Windows network reachability, or the actual administrator enrollment of the operator's installation. After installation, restart the viewport server and local control service as needed (on demand; **no Windows boot auto-start**). Visit `/applications/slh/local`, inspect the reported actual binding and administrator enrollment, and verify any non-loopback reachability from the **separate Network SLH tab** with an independently obtained TLS certificate fingerprint. Do not rebootstrap an initialized authority.
