# All-father control plane bootstrap

The all-father deployment starts with a control surface, not with a guessed
mainnet or testnet workload topology.

`runtime/state/all_father.private.yaml` is only the seed/control file. It should
tell the tooling which Coolify hosts participate and how to talk to those
Coolify APIs. It is not a declaration that any Hub, FoundationDB, QBFT,
`hub_admin`, or contract service exists.

## First operation

Push one all-father guard/head container to every Coolify host in the high-level
topology:

```powershell
python tools/allfather_control.py bootstrap-heads --dry-run
```

Then apply:

```powershell
python tools/allfather_control.py bootstrap-heads
```

The head container exposes the remote peer guard API in the 41400 range and
carries only the peer head list. Those `10.x` guard URLs are for head-to-head
traffic inside the remote VPN/control network. They are not assumed to be
reachable from the local operator machine. The head does not start Hub, FDB,
QBFT, hub_admin, or contracts.

The normal bootstrap command does not ask the operator to choose a Coolify
project name. When Coolify requires a project lookup, the control plane uses the
default Coolify project name `My first project`.

## Bootstrap image

The control-plane head is intentionally self-contained. It uses the public
`python:3.12-slim` image and an inline stdlib HTTP guard server in the generated
Compose. Coolify does not need a repository build context and does not pull a
private `main-computer-allfather-head:latest` image for the first bootstrap.

## Discovery before topology

After the heads are deployed, query the live control surface through Coolify:

```powershell
python tools/allfather_control.py discover
```

Discovery does not use SSH, does not curl the remote `10.x` VPN guard URLs from
the local operator machine, and does not create public guard DNS or Traefik
routes. Instead it uses the Coolify API/key path to create or update one
long-running private probe service per Coolify host:

```text
local operator -> Coolify API -> Coolify-managed probe service -> private guard URLs
```

The probe service has no FQDN and no host port. It runs inside the remote
Coolify-managed Docker context, curls the private guard URLs from there, and
writes `ALLFATHER_PROBE_RESULT ...` JSON lines to its container logs plus
`/state/latest-result.json` for remote diagnosis. The probe intentionally stays
running after discovery so we can inspect it in Coolify when something acts up.
A future finalization action can stop or remove probes after the control path is
stable.


The same probe now also checks every discovered all-father super-node guard.
`discover` merges that private probe result into each super-node inventory entry
so the operator can see whether the container is merely present in Coolify or
whether its guard is answering and reporting internal function state.

After updating a probe with newly discovered super-node targets, `discover` waits
briefly for the Coolify metadata callback to include those targets. This avoids
returning a stale probe result that only covered the head guards. If a super-node
guard is unreachable, the probe should still return a target record with the
connection error so `internal_status.observed` can distinguish "checked and
failed" from "not checked yet."

The super-node guard is now a real in-container supervisor. It starts the private
guard first, then converges local functions in order:

```text
guard -> FoundationDB -> Besu/QBFT validator-RPC -> Hub bootstrap listener
```

`discover` reports those internal states from the private guard. A healthy first
node should move from `starting` to states shaped like:

```text
guard: running
foundationdb: running
validator_rpc: running
hub: running-bootstrap-listener
hub_admin: bootstrapped
contracts: deployed, not-required-existing-network, or disabled
```

The first super-node bootstraps the network-level contract set once after its
validator-RPC is live.  Later super-nodes do not redeploy those contracts; they
fetch the shared QBFT genesis and bootnode information from an existing private
super-node guard (`/qbft/bootstrap`) and join from that live network view.  Every
new super-node still receives its own node-scoped `hub_admin` seed so node admin
material is not reused across nodes.

The Hub process is explicitly reported as a bootstrap listener until the full
Main Computer Hub runtime is bundled into the inline Coolify image. That keeps
the status honest: Hub/RPC ports can be supervised while hub-admin and contract
bootstrap status is reported separately by the guard supervisor.


The generated super-node Compose is intentionally a **build-only** service. It
does not set a local `image:` name because Coolify runs a pull phase before
building; a generated local image name would make Docker try to pull a
nonexistent private repository. The inline Dockerfile also avoids brittle
Compose-time shell variables for the FoundationDB install path and validates both
common `fdbserver` package locations (`/usr/sbin/fdbserver` and
`/usr/bin/fdbserver`) before creating a stable `/usr/local/bin/fdbserver`
symlink for the guard supervisor.

The result reader uses Coolify's application-log API (`/api/v1/applications/<application_uuid>/logs`)
when the probe service detail includes an embedded application UUID. Parent
service log paths are kept only as compatibility fallbacks because service-level
`/api/v1/services/<uuid>/logs` paths may return `404` on current Coolify
versions.

Preview the probe payloads without changing Coolify:

```powershell
python tools/allfather_control.py discover --dry-run
```

Mainnet/testnet topology must come from live guard responses and explicit
add/remove operations, not from the private seed file. `discover` reports whether
the Coolify-managed probes were synced and whether any probe result was observed;
it does not treat the private seed file as topology.


## Output verbosity

`discover` uses an operator summary by default. The normal output includes only
the readiness reason, high-level counts, head/probe service IDs, whether a probe
result was observed, and a single compact last error. It does not dump Coolify
API attempt lists, embedded Compose text, server settings, token sources, probe
targets, or failed log-endpoint paths.

Use `--json` when a compact machine-readable diagnostic payload is needed:

```powershell
python tools/allfather_control.py discover --json
```

Use `--verbose` only when a raw diagnostic dump is needed:

```powershell
python tools/allfather_control.py discover --verbose
```

Verbose discovery may print large Coolify API payloads and should not be pasted
into normal chat/debug loops unless those raw records are needed.

## Guardrail

`hub_admin` and contract setup are blocked until discovery shows at least one
live QBFT validator-RPC node. A Hub super-node can be pushed before public cutover,
but public Hub cutover also waits for a live QBFT validator-RPC node.

## Write local artifacts

For inspection only:

```powershell
python tools/allfather_control.py write-heads `
  --out runtime/coolify-allfather/heads
```

This writes one guard/head manifest and one compose file per Coolify host.


## Compact discovery output

`discover` should return `ok=false` until at least one private probe result is
observed from the running probe services. Syncing the probe services is not
enough to call topology discovery healthy.

Normal operator summary:

```powershell
python tools/allfather_control.py discover
```

Detailed compact JSON:

```powershell
python tools/allfather_control.py discover --json
```

Raw diagnostics:

```powershell
python tools/allfather_control.py discover --verbose
```


## Probe result return path

The discovery probe is intentionally private: no FQDN, no Traefik route, no SSH,
and no local operator curl to VPN addresses. Some Coolify installations do not
return application logs through the public API, so the probe also publishes a
compact result back through the Coolify API into its own service description.

The local CLI then reads that service description through the same Coolify
API/key pathway. Logs remain a fallback diagnostic channel, and the probe service
is left running until a later finalization action removes or disables it.



## Super-node inventory in discovery

`discover` also scans the live Coolify service inventory for all-father
super-node service names such as `testneta-super1`, `testneta-super2`,
`mainneta-super1`, and `mainnetb-super1`.

This inventory is grouped under `networks.<network>.hosts.<coolify-host>` so the
operator can see newly added super-nodes even before the super-node guard has
reported detailed internal Hub/FDB/QBFT status. The private state remains seed
material only; it is not used as topology. The current topology is the union of:

```text
Coolify service inventory
Coolify-managed probe results
private guard/head responses, when available
```

Example shape:

```json
{
  "networks": {
    "testnet": {
      "super_node_count": 1,
      "hosts": {
        "coolify-a": {
          "host_prefix": "testneta",
          "super_node_count": 1,
          "super_nodes": [
            {
              "service_name": "testneta-super1",
              "status": "running:healthy",
              "components": {
                "hub": "testneta-hub1",
                "fdb": "testneta-fdb1",
                "validator_rpc": "testneta-validator-rpc1"
              }
            }
          ]
        }
      }
    }
  }
}
```

## Add one super-node

Once the control heads are present, grow a network one all-father super-container
at a time. The operator does not choose ordinals. The tool reads Coolify service
inventory for the selected host and network, counts existing
`<network><host-letter>-superN` services, and creates the next one.

Testnet example:

```powershell
python tools/allfather_control.py add-node testnet `
  --host coolify-a `
  --dry-run
```

Apply:

```powershell
python tools/allfather_control.py add-node testnet `
  --host coolify-a
```

Mainnet requires an explicit confirmation:

```powershell
python tools/allfather_control.py add-node mainnet `
  --host coolify-a `
  --allow-mainnet `
  --dry-run
```

The first mainnet node on `coolify-a` is named `mainneta-super1`. Inside that
single super-container the compiled component names are:

```text
mainneta-hub1
mainneta-fdb1
mainneta-validator-rpc1
mainneta-guard1
```

The second mainnet node on the same host becomes `mainneta-super2`, and the first
mainnet node on `coolify-b` becomes `mainnetb-super1`.

The private state file is still not topology. It only supplies Coolify access and
wallet bootstrap material. `add-node` uses live Coolify inventory to pick the
next host-local number.

The generated super-node image is self-contained because Coolify's inline
Compose build context does not include the repository checkout. It keeps the
Besu base image, installs FoundationDB server/client, bakes in the private
guard/supervisor script, and exposes only Hub/RPC through Traefik when public
cutover is explicitly requested. Guard, FoundationDB, and P2P remain private
host/VPN ports.


## Wallet bootstrap and contracts

`tools/allfather_private_state.py migrate-coolify` now copies the Coolify host
seed plus the mainnet/testnet wallet bootstrap slots from
`runtime/state/main_computer.private.yaml` into
`runtime/state/all_father.private.yaml`.

The copied wallet material is limited to the fields needed by all-father
bootstrap actions, such as:

```text
networks.testnet.wallets.hub_admin.private_key
networks.testnet.wallets.deployer.private_key
networks.mainnet.wallets.hub_admin.private_key
networks.mainnet.wallets.deployer.private_key
```

`add-node` materializes missing bootstrap private keys into
`runtime/state/all_father.private.yaml` before it renders or deploys the
super-node. It first reuses copied network-specific or default wallet material.
If `hub_admin` is missing, it generates a new private key for that network. If
this is the first node and contracts are enabled, it also generates a deployer
private key when one is missing. The runtime derives addresses from those keys
when needed; the tool does not invent fake addresses.

When adding the first node for a network, contract bootstrap is requested by
default. Pass `--no-contracts` to create the node without that first-node
contract bootstrap intent:

```powershell
python tools/allfather_control.py add-node testnet `
  --host coolify-a `
  --no-contracts
```

The contract bootstrap is marked as deferred until the node's validator-RPC is
actually live. The command records the intent and deploys the super-container; it
does not make the guard public, does not use SSH, and does not curl VPN URLs from
the local operator machine.

Hub/RPC public cutover is also deferred by default. Only pass `--publish-routes`
when the Hub and RPC surfaces are intentionally ready to be routed by
Coolify/Traefik.



## Add-node bootstrap wallet rules

`add-node` must not block the creation of the first super-node just because
`hub_admin` does not already exist in `all_father.private.yaml`. The super-node
is what provides the validator-RPC surface needed to create/import/register that
admin safely.

The rules are:

```text
hub_admin private key present:
  pass it into the super-node bootstrap intent

hub_admin private key missing:
  generate and persist a new key in all_father.private.yaml
  pass that key into the super-node bootstrap intent
  defer hub_admin registration/use until validator-RPC is live

first node contracts:
  requested by default
  generate and persist a deployer key if missing
  are deferred until validator-RPC and hub_admin are ready

--no-contracts:
  creates the node without contract bootstrap
```

The migration command copies network-specific wallet slots when available and
also preserves top-level `wallets.defaults` or direct top-level wallet slots as
fallback bootstrap material. This is still private bootstrap material, not
network topology.


## FoundationDB add-node intent

`add-node` also compiles the FDB bootstrap intent for the new super-node. This is
important because a second super-node must not start a separate, isolated FDB
cluster.

The all-father private file carries only FDB seed identity:

```text
networks.<network>.foundationdb.cluster_description
networks.<network>.foundationdb.cluster_id
```

If that identity is missing, `add-node` generates it and persists it next to the
wallet bootstrap material.

For the first super-node in a network, the manifest says:

```text
foundationdb.action = initialize-new-cluster
foundationdb.current_coordinators = [new-node]
```

For later super-nodes, the manifest says:

```text
foundationdb.action = join-existing-cluster
foundationdb.join_cluster_file = current existing coordinator set
foundationdb.target_cluster_file_after_reconfigure = existing + new coordinator
foundationdb.coordinator_reconfigure_required = true
```

That gives the runtime guard a safe sequence: join the existing FDB cluster first,
wait for health, then perform the coordinator reconfiguration. It keeps the
private file as seed material and uses Coolify inventory/live discovery for the
actual node count.

## Remove one super-node

Removal shrinks a network one host-local super-container at a time. The operator
does not choose or renumber ordinals. The command reads live Coolify service
inventory for the selected network and host, removes the highest existing
`<network><host-letter>-superN` service, and leaves lower-numbered nodes
unchanged.

For the failed first testnet node on `coolify-a`, dry-run:

```powershell
python tools/allfather_control.py remove-node testnet `
  --host coolify-a `
  --dry-run
```

Apply:

```powershell
python tools/allfather_control.py remove-node testnet `
  --host coolify-a
```

Mainnet removal requires an explicit confirmation:

```powershell
python tools/allfather_control.py remove-node mainnet `
  --host coolify-a `
  --allow-mainnet `
  --dry-run
```

If removing the selected node leaves no super-nodes in that network, the command
cleans generated first-node seed material from `runtime/state/all_father.private.yaml`
so the network can be added again from a pristine all-father bootstrap state.
Generated `hub_admin` and `deployer` keys are removed only when they were created
by `tools/allfather_control.py:add-node`; migrated or hand-supplied wallet keys
are preserved. FDB cluster seed identity is also cleared when the network becomes
empty.

Pass `--keep-seed-material` only when you intentionally want to preserve the
generated wallet/FDB seed material after the last node is removed.

`remove-node` deletes the Coolify service through the Coolify API/key path. It
does not use SSH, does not expose guard routes, does not curl VPN URLs from the
operator machine, and does not renumber surviving nodes.

After Coolify accepts the delete request, `remove-node` waits for live Coolify
service inventory to stop reporting the deleted service before it cleans
generated seed material or reports the network as pristine. This protects the
next `add-node` from seeing a stale just-deleted `super1` record and incorrectly
creating `super2`.

`add-node` also refuses to proceed when live inventory is non-contiguous, for
example when Coolify reports `testneta-super2` but no `testneta-super1`. Shrink
with `remove-node` until the host-local inventory is pristine, then add again.


## Super-node image and public routing

`add-node` now builds an all-father super-node image from the Besu/QBFT base
image instead of running the workload cell from the simple Python control-head
image. The generated service is still guard-first: the private guard keeps the
container alive while FDB, Hub, validator-RPC, `hub_admin`, and contract
bootstrap converge in order.

The super-node compose uses an inline Dockerfile whose base image defaults to
`hyperledger/besu:latest`. The build installs only the guard/support tools it
needs on top of that base image, then verifies that `besu` exists before the
image is accepted.

The built image also replaces the inherited Besu image entrypoint with the
all-father guard entrypoint. Coolify's Compose validator expects the service
`entrypoint` field to remain `null`, so the entrypoint override must live in the
built image itself. This keeps `command` from being passed to the Besu binary and
ensures the private guard binds `0.0.0.0:41414` for `/healthz`, `/identity`,
`/status`, `/topology`, and `/processes`.

Private ports remain private host bindings:

- guard -> VPN/private host bind
- FDB -> VPN/private host bind
- Besu P2P -> VPN/private host bind

Hub and RPC are the only world-facing services. They are exposed through Traefik
labels only when `--publish-routes` is used, and no guard, FDB, or P2P public
router is generated.

