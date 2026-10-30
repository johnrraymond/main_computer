# SEH Harness — First-Class Coolify Identity and Remote Deployment (Patches 1–3)

The SEH infrastructure record lives in Mother's **verified private identity state**:
`runtime/state/mother/identity.private.yaml`, under `networks.<network>.sehs.<seh>`.
This is a control-plane record; the SEH runtime itself receives neither the
Coolify token nor the SLH private signing key.

## Record model

```yaml
networks:
  mainnet:
    sehs:
      company-primary:
        schema: main-computer.seh-identity.v1
        organization_id: my-company
        slh_id: my-slh
        coolify:
          active_identity: primary
          identities:
            primary:
              controller:
                url: https://coolify.example.org
                api_token: "<private; never printed>"
                project_uuid: project-a
                server_uuid: server-a
                enabled: true
              copied_from: networks.mainnet.coolify.controllers.coolify-a
              source_generation: 10
              copied_at: "2026-10-28T00:00:00Z"
        deployment:
          enabled: true
          public_url: https://seh.example.org
          image: registry.example.org/main-computer/seh
          image_digest: "sha256:<64-lowercase-hex>"
          environment_uuid: env-a
          slh_public_key_b64: "<SLH-public-identity>"
          # service_uuid: populated only after verified Coolify creation
```

This is an illustrative *shape*, not a ready-to-use deployment. Environment,
public DNS, image digest, and SLH public signing key must be the actual values.

`controller` is a complete **independent copy**: later edits or deletion of
its source do not change the SEH. Source `api_token_env`/`api_token_file` are
resolved at copying time and stored as a private `api_token` value. The
`copied_from` path and `source_generation` provide provenance, not a live link.
Only two source path shapes are accepted:
`networks.<network>.coolify.controllers.<id>` and
`networks.<network>.sehs.<seh>.coolify.identities.<alias>`.

## One CLI, one backend

All commands below use `python -m tools.seh_harness` and the same backend that
SLH can subsequently call. Read-only discovery:

```powershell
python -m tools.seh_harness coolify-sources
python -m tools.seh_harness list --network mainnet
```

The harness automatically reads the verified private-state binding and uses
that exact binding as the atomic compare-and-swap predecessor. **No manual
`--approve-generation` is required.** Each mutation still requires `--execute`;
a concurrent change to Mother's private state is refused at commit time.
`--approve-generation` remains an optional strict pin for scripted callers
that intentionally want to reject a newer state. Example workflow:

```powershell
python -m tools.seh_harness reserve --network mainnet --seh company-primary `
  --organization-id my-company --slh-id my-slh --execute
python -m tools.seh_harness coolify-copy --network mainnet --seh company-primary `
  --source networks.mainnet.coolify.controllers.coolify-a --as primary `
  --execute
python -m tools.seh_harness inspect --network mainnet --seh company-primary
```

To refresh the credential after its source changes, use `coolify-refresh --as
primary --execute`. To copy a different
controller into an existing alias, use `coolify-copy ... --replace` with
explicit approval. `coolify-select --as <alias>` changes the active identity.

Create a **nonsecret** JSON configuration for the specific SEH deployment at
`runtime/deployments/mainnet/sehs/company-primary/deployment.json`.
The path follows `runtime/deployments/<network>/sehs/<seh>/deployment.json` and
is resolved automatically by `configure` from `--network` and `--seh`.

This is a local, editable deployment-input file, **not** the authoritative
identity record. The approved configuration is committed into Mother's verified
private state by `configure`. Never put Coolify tokens, SLH private keys, or
wallet credentials in this JSON file.

```json
{
  "enabled": true,
  "public_url": "https://external.greatlibrary.io",
  "image": "registry.example.org/main-computer/seh",
  "image_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "environment_uuid": "environment-a",
  "slh_public_key_b64": "<actual-public-key>"
}
```

```powershell
python -m tools.seh_harness configure --network mainnet --seh company-primary --execute
python -m tools.seh_harness plan --network mainnet --seh company-primary
python -m tools.seh_harness preflight --network mainnet --seh company-primary
```

An explicit `--deployment-file` still works for scripted or legacy callers.
If the default file is missing, `configure --execute` creates a nonsecret
runtime draft. It defaults the intended relay public origin to
`https://external.greatlibrary.io`, **not** to a Coolify server IP address.
That hostname is an operator-selected intent; it does **not** prove Cloudflare
DNS proxy status, an origin certificate, a routing rule, or WSS reachability.

The same command also fills known project/server placement from the copied
Coolify identity and reads the existing SLH **public** relay key. It attempts
read-only discovery of the environment UUID using the copied controller's
Coolify project environment inventory. If one environment matches the
configured name, or there is exactly one candidate, it selects that UUID;
multiple candidates remain explicitly ambiguous. It reuses an existing
**digest-pinned, registry-qualified SEH image** only when the verified state
or a recognized Coolify SEH service exposes one unambiguous image/digest pair.
The lab's `main-computer-seh:local` image ID is **not** a published image
manifest digest and cannot be assumed usable by a remote Coolify host.

```powershell
# No mutation: inspect currently discoverable deployment inputs.
python -m tools.seh_harness discover --network mainnet --seh company-primary
# Autofill missing fields in the runtime draft, then commit only if complete.
python -m tools.seh_harness configure --network mainnet --seh company-primary --execute
```

`discover` is read-only. On `configure`, only empty draft fields are filled;
explicit operator values are preserved. When complete, `configure` commits the
verified Mother state generation through the existing compare-and-swap.
Otherwise it reports `DRAFT_CREATED` or `NEEDS_CONFIGURATION` with exact
`missing_fields` and nonsecret `blockers`; no remote mutation is performed.

**Critical management transport boundary:** an SEH controller copied from a
public-IP `http://...:8000` Coolify address is **not** a safe origin for
credentialed discovery or deployment. The discovery client refuses to transmit
the bearer token over that public HTTP connection. Configure a valid HTTPS
Coolify management origin or a protected local tunnel first; this is separate
from the public `external.greatlibrary.io` SEH hostname. A default public
hostname does not change the management-controller URL. Do not bypass this by
sending secrets through a public unencrypted API endpoint.

A selected Coolify service is not declared remotely deployed or publicly
reachable merely because its image or environment was discovered. Public DNS,
Cloudflare proxying, HTTPS/WSS, and actual relay operation remain explicit
acceptance gates in the integrated lab.

An explicit `--deployment-file` still works for scripted or legacy callers.
A missing explicit override fails rather than silently writing a new file.

The plan yields `plan_digest` and `target_id`. For a reviewed deployment:

```powershell
python -m tools.seh_harness deploy --network mainnet --seh company-primary `
  --execute --approve-digest <plan_digest> `
  --approve-target company-primary
python -m tools.seh_harness status --network mainnet --seh company-primary
```

After successful Coolify creation, `deploy` commits `service_uuid` through
Mother's generation-bound verified successor. It reports **created, not
started or verified**. To start, retrieve a new plan/digest and run `start
--execute --approve-digest <digest>
--approve-uuid <service_uuid>`.

If Coolify created a service but the private-state commit failed, **do not
retry `deploy`**. Inspect Coolify and use `recover --approve-uuid <uuid>
--execute`. Recovery requires an exact
matching SEH service name and ownership marker before recording the UUID.

## Invariants and boundaries

- **No direct YAML overwrite**: all state mutations use Mother's verified
  `prepare_private_state_successor` + `replace_verified_private_state` CAS.
- The source controller's `observe-only` policy remains untouched. Explicit
  SEH mutation flags do not grant general Mother/FDB/Hub mutation authority.
- Tokens remain in the protected private state and in-memory Coolify client;
  all discovery, inspect, plan, reports, and standard errors are redacted.
- An SEH can hold multiple identities, with exactly one active selector.
- The legacy `--target <json>` mode remains available during migration.
- This patch does **not** replace the separate live deployment acceptance lab,
  nor does it claim verified public WSS or external settlement. Coolify
  create/start receipts are not proof that a remote relay is ready.

## Git-backed remote SEH deployment (Hub Control parity)

The SEH harness also supports the **Git-backed Coolify application path**. This
is independent of the older Compose/image-digest path. It uses the *active,
first-class Coolify identity copied into the selected SEH*; it does not have a
hardcoded Coolify host. If the selected identity changes, `prepare` resolves the
new controller's own project, environment, server, and API token.

### Operator workflow

```powershell
python -m tools.seh_harness prepare --network mainnet --seh company-primary
python -m tools.seh_harness preflight --network mainnet --seh company-primary
python -m tools.seh_harness deploy --network mainnet --seh company-primary --execute
python -m tools.seh_harness status --network mainnet --seh company-primary
```

The default project is `My first project`, environment is `<network>-hubs`
(`mainnet-hubs` on mainnet), and the public URL is
`https://external.greatlibrary.io`. These are **names to resolve and verify**, not
fabricated UUIDs or proof of Cloudflare configuration. On another Coolify host
with a differently named project/environment, use `--project-name` and
`--environment-name` with `prepare`; no controller IP, token, or UUID arguments
are needed. You may set `--public-url` to a different approved HTTPS host.

The harness requires the committed, pushed GitHub revision of the SEH Dockerfile
and its build/runtime inputs. It refuses missing, dirty, or unpushed source and
never commits or pushes automatically. Source paths include
`deploy/seh/Dockerfile` and the relay/signature modules. The generated nonsecret
plan is `runtime/deployments/<network>/sehs/<seh>/git-plan.json`. It binds the
verified Mother identity generation/content hash, selected controller,
placement, exact Git SHA, and requested public URL. The `prepare` and
`preflight` commands do not mutate Coolify or Mother identity state.

`deploy --execute` revalidates that plan, performs exact-name/ownership and
project/environment/server collision checks, then creates or reconciles a
Coolify **Git Dockerfile application**. It records the application UUID with
Mother's verified compare-and-swap **before** environment configuration and
remote build submission. An interrupted run can be resumed by preparing again;
existing applications are never adopted on name alone. Repeated deploys do not
create duplicate applications. Hub Control's Coolify deploy trigger is reused.

A deployed application receives **only the SLH public signing identity** and
nonsecret relay settings. Coolify credentials remain inside Mother's protected
state. The controller's selected public-HTTP management origin is temporarily
accepted by this harness under the operator's explicitly accepted risk; it is
not a global relaxation of unrelated controller or Worker TLS checks.

The deployment receipt reports `DEPLOY_QUEUED` and deliberately does **not**
claim that public DNS, Cloudflare proxying, TLS/WSS, SLH tunnel readiness, or a
paid marketplace computation has been verified. Those are later acceptance
checks. The prior legacy `--target` / pinned-image interface remains usable.

### Read-only public DNS check in `status`

Git application `status` returns both the observed Coolify application health
and a separate `dns` object. It checks the selected HTTPS public hostname
against the live application's `domains`/`fqdn` and resolves its A/AAAA addresses
through the **operator machine's system DNS resolver**. `DNS_CLOUDFLARE_EDGE`
means every resolved address belongs to Cloudflare's published edge IP ranges;
`DNS_NOT_CLOUDFLARE` means the hostname resolves but not to Cloudflare edge
addresses. `DNS_MIXED_CLOUDFLARE_AND_ORIGIN`, `DNS_NONPUBLIC_ADDRESSES`,
`DNS_NOT_FOUND`, and `DNS_LOOKUP_FAILED` are separately reported.

`dns_ready: true` requires both Cloudflare edge addresses and a matching live
Coolify application hostname. When Coolify doesn't expose its configured
hostname, the DNS result can still show resolved edge addresses, but
`dns_ready` remains false. DNS failures never cause a `running:healthy` Coolify
application to be misreported as unhealthy, and the DNS query does not mutate
anything or use an API token. A successful DNS result **does not prove** that
Cloudflare's origin is correct, HTTPS is reachable, or public WSS succeeds;
`public_wss_verified` remains false until separately tested. The local system
resolver view is not a full authoritative-zone audit, so a local DNS override
or stale resolver cache can affect the result.
