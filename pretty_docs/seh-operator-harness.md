# SEH operator harness — existing identity / Coolify-C

The top-level `seh_mutate_harness.py` is the operator-facing entry point, in the
same style as `mother_mutate_harness.py`, `fdb_mutate_harness.py`, and
`hub_mutate_harness.py`. The existing `python -m tools.seh_harness` remains the
low-level SEH Control interface.

## Existing `company-primary` on `coolify-c`

First inspect the already-registered, verified private identity without any
Coolify mutation:

```powershell
python .\seh_mutate_harness.py inspect --network mainnet --seh company-primary
```

A read-only intent check does not even contact Coolify:

```powershell
python .\seh_mutate_harness.py deploy-seh --network mainnet --seh company-primary --host coolify-c
```

The **operator-approved mutation** call is:

```powershell
python .\seh_mutate_harness.py deploy-seh --network mainnet --seh company-primary `
  --host coolify-c --execute-mutations --yes-i-know-this-mutates-seh
```

The existing `primary` identity, which was copied from
`networks.mainnet.coolify.controllers.coolify-c`, is retained; the harness does
not copy credentials again, register a new SEH, or change the current
application UUID. An SEH's identity alias (such as `primary`) must not be
mistaken for the name of its Coolify controller (`coolify-c`).

### Public URL

When the existing SEH has a verified `deployment.public_url`, it is reused. If
not, the operator harness performs a **read-only** Coolify GET against the exact
registered application UUID, checks its name and SEH ownership marker, and
requires exactly one configured domain. The resulting hostname is used as an
HTTPS origin. It never silently accepts the old `git-plan.json` URL or the Git
backend default hostname. If the application has no unique configured domain,
pass the actual approved hostname explicitly as `--public-url https://...`.

## New SEHs

Registration of a *new* SEH must supply its organization, SLH, selected Coolify
controller and public HTTPS origin:

```powershell
python .\seh_mutate_harness.py deploy-seh --network mainnet --seh company-secondary `
  --host coolify-b --organization-id YOUR_ORG --slh-id YOUR_SLH `
  --public-url https://YOUR-APPROVED-SEH-HOSTNAME `
  --execute-mutations --yes-i-know-this-mutates-seh
```

No private token, password, or private key is supplied on the command line or
included in receipts. Credentials are resolved through the existing Mother's
verified private state. A changed Coolify controller for an SEH with an
already-recorded application UUID is refused.

## Acceptance and limits

On execution the harness orchestrates the existing SEH operations: optional
registration, optional Coolify identity copy/selection, Git prepare, read-only
preflight, guarded deployment, and status. It honors the existing exact Git
revision, placement, application ownership, and identity checks. A dirty or
unpublished SEH Git source fails closed; when the lower-level verifier emits a
specific safe Git repair command, the operator receipt includes it.

A `DEPLOY_QUEUED_NOT_VERIFIED` receipt means Coolify accepted the deployment
trigger, **not** that public HTTPS/WSS, relay authentication, or production
service readiness has been independently verified. No external deployment is
performed by the patch installation or unit tests. A fresh private state and
networked Coolify controller are required for live execution.

## SLH public signing identity (automatic, guarded)

Git deployment includes the public P-256 signer used by the actual SLH to
establish its outbound SEH tunnel. The private key is never copied to Coolify.

After the operator explicitly authorizes mutations, `deploy-seh` checks the
installation-owned SLH state under `runtime/state/slh` for a fully initialized organization matching the
registered SEH organization ID. It refuses if the SLH state does not exist, if
legacy state requires migration, if the organization or configured SLH ID
conflicts, or if the state cannot be securely accessed. Only for a matching
local SLH does it reuse an existing signer or, if missing, create the signer
once under the protected SLH runtime directory. Identity provisioning does not
initialize a new SLH organization and cannot change its existing signer.

An existing Coolify SEH application is also checked read-only for the existing
`SEH_SLH_PUBLIC_KEY_B64` trust anchor. If it is different, ambiguous, or masked,
the harness refuses before changing Coolify. A rotation/recovery requires a
separate explicit identity procedure; this operator deploy command never
silently replaces the trust anchor.

Consequently, deploying from a **different operator workstation** where the
organization SLH is not installed is not allowed to invent a local signer. Run
from the actual SLH machine or use an explicitly authorized public-identity
provisioning workflow. The normal operator command is otherwise unchanged.

## Legacy SLH state: explicit offline migration

If `deploy-seh` refuses at `slh-identity` with
`LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION`, the machine still holds its
organization SLH authority under the old per-user storage root. **Do not**
provision another key, bypass the guard, or copy private material into Coolify.
This error does not mean the existing Coolify application is invalid.

Stop the running SLH processes before migrating, and inspect the non-mutating
plan first:

```powershell
python -m tools.slh_storage_migrate plan
```

On the actual SLH host, run the explicitly requested, non-destructive migration:

```powershell
python -m tools.slh_storage_migrate slh --execute
```

The utility preserves the legacy source files, verifies the copied files by
SHA-256, refuses symbolic links and an occupied destination, and hardens the
private runtime state ACL. If the destination already contains files, stop and inspect
both authorities; do not delete or merge them automatically. Resume the
existing `company-primary` / `coolify-c` deployment command after successful
migration. The harness formats this guard as a structured `REFUSED` receipt
with `failed_stage: slh-identity` instead of an unhandled traceback.
