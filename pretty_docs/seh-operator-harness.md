# SEH operator harness — infrastructure deployment, independent of SLH onboarding

> **Historical implementation; not the intended production provisioning lifecycle.**
> This standalone infrastructure harness may queue a Coolify deploy and leaves
> new SEHs unbound. Its separate `bind-slh` operation does not configure the
> SLH's resident automatic-connection supervisor or prove WSS authentication.
> The governing design is SLH-owned provisioning and automatic reconnection.
> Preserve registered application UUIDs, remote trust, and local keys until
> the governed lifecycle is implemented and accepted. A queued status is not
> operational success.

`seh_mutate_harness.py` is the operator-facing entry point following the Mother,
FDB and Hub harness pattern. It deploys the SEH *infrastructure*. Deploying an
SEH is not the same operation as binding an organization's SLH to its relay.

## Read-only inspection of the sole registered deployment

Inspect the one existing SLH-owned SEH deployment without selecting a name:

```powershell
python .\seh_mutate_harness.py inspect
python .\seh_mutate_harness.py diagnostic
```

These read-only commands resolve the sole legacy record from Mother's verified
private state. No SEH alias, controller alias, SLH ID, or URL may be passed as
a target selector. With no registration, inspection reports that plainly; a
diagnostic refuses. Multiple registrations refuse automatic selection before
signing-key lookup or Coolify access. Neither command changes the registry,
signing identity, Coolify application, or running tunnel.

The old named registry and the infrastructure-only mutation commands still
exist until the separate governed migration patches. **Do not use them as a
replacement for SLH-owned provisioning.** The existing Coolify application
UUID, public URL, and approved trust key must be preserved throughout migration.

## Canonical Mother migration (Patch 2)

Mother's existing single named SEH registry is a legacy migration input only.
On this patch, the operator explicitly approves the observed generation and
migrates it to `networks.<network>.seh_deployment`. No SEH/organization alias
or independent SLH identifier survives inside that canonical deployment record.

```powershell
python .\seh_mutate_harness.py inspect
python .\seh_mutate_harness.py migrate-state --approve-generation <OBSERVED_GENERATION> --execute-mutations --yes-i-know-this-mutates-seh
python .\seh_mutate_harness.py inspect
```

This is a **local Mother-state mutation**. Back up the verified private state
through the existing Mother recovery procedure beforehand. It requires exactly
one legacy SEH, one selected controller copy, a registered application UUID,
matching controller origin, and a fresh generation. It preserves the Coolify
application UUID, placement, copied controller token, and recorded public URL.
It does not contact Coolify, modify its application metadata or signing key,
or update any running SLH connection. Repeated migration is unchanged.

The prior `git.application_name` is deliberately not carried into the new
Mother record; the old name may still exist in the live Coolify application
until a separate guarded remote metadata reconciliation. Once migrated, legacy
named mutation paths refuse to write a new `sehs` record. Remote deployment
and trust diagnostics for the new schema remain blocked until the follow-up
integration patch; do not invoke them as if they are already supported.

## Deployment safety and verification

The operator command verifies Mother's private-state controller selection,
Coolify project/environment/server placement, registered application ownership,
exact published Git revision, and deployment idempotency. The Coolify token
is read only from the verified controller identity and never printed.

When redeploying an existing application without a new explicit SLH binding,
only infrastructure environment variables are updated. Existing SLH trust
variables are read before and after update and must remain unchanged. A
partial or ambiguous existing binding blocks deployment before the trigger.

`DEPLOY_QUEUED_NOT_VERIFIED` means Coolify accepted the deployment trigger,
**not** that public HTTPS/WSS or SLH connectivity was verified. New unbound
relays are expressly **not** operationally ready until enrolled. A deployment
source with uncommitted or unpushed SEH runtime files fails closed.

No part of installing this patch contacts Coolify or migrates SLH state.


### Operator console next step

Like `hub_mutate_harness.py`, `seh_mutate_harness.py` prints a standalone,
PowerShell-ready next command when the operator is stopped at the mutation
approval boundary or when Git source must be committed and pushed. The JSON
receipt remains the only content on stdout; the operator instructions appear
on stderr, after the receipt, so JSON-processing callers remain compatible.
The harness never automatically commits or pushes source changes, and a
queued Coolify deployment is never reported as verified WSS readiness.
