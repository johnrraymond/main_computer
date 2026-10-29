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
  "public_url": "https://seh.example.org",
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
If the default file is missing, the first `configure --execute` creates an
**uncommitted, nonsecret draft** in the correct runtime location, printing
`DRAFT_CREATED`, its path, and the missing fields. It reuses known project,
server, and environment identifiers from the copied Coolify identity, and
reads the local SLH's **existing public** relay key when present. It never
creates or exports a private key, copies a Coolify token into the draft,
substitutes an image digest, or guesses a public URL.

Edit only the genuinely unknown fields in that file and rerun the **same**
`configure --execute` command. An incomplete enabled draft returns
`NEEDS_CONFIGURATION` and lists missing fields without modifying Mother's
verified identity. Only a complete validated configuration is committed using
the verified generation-bound state mutation. Draft creation never deploys
anything. Explicit nonexistent `--deployment-file` overrides still fail
rather than silently creating a file at an unexpected location.

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
