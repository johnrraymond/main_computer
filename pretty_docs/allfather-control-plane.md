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
