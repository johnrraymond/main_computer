# Guarded all-father Coolify container

The all-father container is a lower-stage deployment unit for the Main Computer
network.  It compiles the existing Coolify/FDB/Hub/QBFT structures into one
guarded function cell per Coolify server and per running set.

The cell does not receive a brittle host role such as "hub host" or "validator
host."  Every cell has the same role: `function`.  Its behavior comes from the
compiled manifest:

- network behavior profile: `testnet` or `mainnet`
- running set id: for example `testnet-1`, `mainnet-1`, or `mainnet-2`
- guard endpoint
- desired local counts for FoundationDB, Hub, and QBFT processes
- peer guard endpoints for the other hosts in the top-level all-father network
- process restart rules

## Generate a plan

```bash
python tools/coolify_allfather_container.py plan testnet --set-id testnet-1
```

Mainnet is explicit:

```bash
python tools/coolify_allfather_container.py plan mainnet --set-id mainnet-1 --allow-mainnet
```

## Generate deployable raw compose files

```bash
python tools/coolify_allfather_container.py write testnet --set-id testnet-1 --out runtime/coolify-allfather/testnet-1
```

The output directory contains:

- `allfather-plan.json`
- one `*.manifest.json` per cell
- one `*.compose.yml` per cell
- a short generated README

Each compose file has one service.  The service builds from the committed
`docker/allfather/Dockerfile` and carries the compiled cell manifest in
`MC_ALLFATHER_MANIFEST_B64`.




## Bring up the all-father network first

The first remote deployment step is the guard/head mesh.  This pushes one
all-father super-container service to every Coolify host with the full desired
manifest and port inventory, but with child workloads held down.

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --phase heads \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

The deployed heads answer on their guard ports immediately:

```text
coolify-a testnet-1 guard: http://10.116.0.3:41410
coolify-b testnet-1 guard: http://10.124.0.3:41411
```

In `heads` phase, the manifest still tells each guard how many FoundationDB,
Hub, and validator-RPC nodes it should eventually run, and who its peer guards
are.  The guard starts with `initial_desired_up=false` and `initial_drained=true`
so it can exchange `/identity` and `/topology` before waking the actual nodes.
After the heads can see each other, operators can either POST `/up` to the guard
endpoints or re-apply with `--phase full`.

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --phase full \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

## Multiple sets on the same two hosts

`network_key` is the behavior profile. `set_id` is the running instance.  This
lets the same two physical hosts run layouts such as:

```text
coolify-a:
  testnet-1
  mainnet-1
  mainnet-2

coolify-b:
  testnet-1
  mainnet-1
  mainnet-2
```

Generate those as separate sets:

```bash
python tools/coolify_allfather_container.py write testnet --set-id testnet-1 --out runtime/coolify-allfather/testnet-1
python tools/coolify_allfather_container.py write mainnet --set-id mainnet-1 --allow-mainnet --out runtime/coolify-allfather/mainnet-1
python tools/coolify_allfather_container.py write mainnet --set-id mainnet-2 --allow-mainnet --out runtime/coolify-allfather/mainnet-2
```

The compiler derives non-overlapping guard ports and shifts imported service ports
by set.  For example, `mainnet-2` keeps the mainnet behavior profile but gets its
own state root, FDB namespace, FDB cluster file path, guard ports, and shifted
Hub/FDB/QBFT host ports.

The guard is not told that a host is "the hub host" or "the validator host."  It
is told counts and peers:

```text
desired_counts:
  foundationdb: local count
  hub: local count
  qbft: local count

set_desired_counts:
  allfather_cells: set-wide count
  foundationdb: set-wide count
  hub: set-wide count
  qbft: set-wide count

peer_hosts:
  guard URL and identity for each other all-father cell in the set
```

A peer can call another peer's `/identity` or `/topology` endpoint, compare what
the peer claims with its own manifest, and adjust the top-level topology view
without relying on static host roles.

## Host-local imported service names

Imported topology files may use global names such as `mainnet-hub1`,
`mainnet-hub2`, and `mainnet-hub3`.  The all-father compiler now re-labels
those imported services with host-local counted names inside the compiled cell:

```text
coolify-a on mainnet: mainneta-hub1, mainneta-hub2, ...
coolify-b on mainnet: mainnetb-hub1, mainnetb-hub2, ...
coolify-a on testnet: testneta-hub1, testneta-hub2, ...
coolify-b on testnet: testnetb-hub1, testnetb-hub2, ...
```

FoundationDB processes use the same host-local counting style, for example
`mainneta-fdb1` and `mainnetb-fdb1`.  The manifest still preserves the imported
source names as `source_hub_id`, `source_id`, or `source_name`, so operators can
trace a compiled host-local function back to the original Coolify/FDB/Hub
placement structure.

This makes add/remove operations easier: each physical host has its own `hub1`,
`hub2`, `fdb1`, and `fdb2` sequence, while `network_key` and `set_id` provide the
outer scope for multiple mainnet/testnet sets on the same hosts.

The compiler also emits host-local public Hub URLs that match the current DNS
shape:

```text
mainneta-hub1.greatlibrary.io
mainneta-hub2.greatlibrary.io
mainnetb-hub1.greatlibrary.io
testneta-hub1.greatlibrary.io
testneta-hub2.greatlibrary.io
testnetb-hub1.greatlibrary.io
```

The imported placement URLs are retained as `source_public_url` for traceability.

## Primary public Traefik routes

The two public HTTP surfaces that matter for each super-container are:

```text
<host-prefix>-hubN.greatlibrary.io -> same super-container internal Hub port
<host-prefix>-rpcN.greatlibrary.io -> same super-container internal validator-RPC port
```

Examples:

```text
mainneta-hub1.greatlibrary.io -> mainneta super node 1 Hub process
mainneta-rpc1.greatlibrary.io -> mainneta super node 1 validator-RPC process
mainnetb-hub1.greatlibrary.io -> mainnetb super node 1 Hub process
mainnetb-rpc1.greatlibrary.io -> mainnetb super node 1 validator-RPC process
```

The compiler emits these routes in `public_routes`,
`identity.public_routes`, `MC_ALLFATHER_PUBLIC_ROUTES`, and
`MC_ALLFATHER_PUBLIC_ROUTES_B64`.  The generated compose also carries Traefik
labels such as:

```text
traefik.http.routers.mainneta-hub1.rule=Host(`mainneta-hub1.greatlibrary.io`)
traefik.http.services.mainneta-hub1-svc.loadbalancer.server.port=<hub-port>
traefik.http.routers.mainneta-rpc1.rule=Host(`mainneta-rpc1.greatlibrary.io`)
traefik.http.services.mainneta-rpc1-svc.loadbalancer.server.port=<rpc-port>
```

Hub and RPC ports remain visible in the port inventory, but they are no longer
directly host-published by the all-father compose.  Traefik is responsible for
public HTTP routing.  Guard remains private/operator-facing, while FoundationDB
and Besu P2P remain raw TCP/VPN surfaces.

## Guard ports

The guard listens inside the container on:

```text
41414/tcp
```

Default host mappings:

```text
test/testnet set 1: 41410, 41411, ...
mainnet set 1:      41420, 41421, ...
test/testnet set 2: 41430, 41431, ...
mainnet set 2:      41440, 41441, ...
```

The compiler intentionally avoids already-used/noisy high ports seen in the repo:

```text
40010
40321
47000
```

## Imported service ports

The compiler now emits a single `port_inventory` in the plan, each cell
manifest, and the guard `/identity` payload.  The generated compose environment
also lists the same inventory in readable service-group variables:

```text
MC_ALLFATHER_GUARD_PORTS
MC_ALLFATHER_FDB_PORTS
MC_ALLFATHER_HUB_PORTS
MC_ALLFATHER_QBFT_PORTS
MC_ALLFATHER_PUBLIC_ROUTES
MC_ALLFATHER_PUBLIC_ROUTES_B64
MC_ALLFATHER_PORT_SUMMARY
MC_ALLFATHER_PORT_INVENTORY_B64
```

Each entry records:

```text
name
group
kind
protocol
bind_host
publish_host
host_port
container_port
published
visibility
notes
```

The important defaults are:

```text
guard: 41414 inside the container, 41410+ testnet/test host ports, 41420+ mainnet host ports
FDB:   the committed FoundationDB placement ports, usually 4550/4551, shifted per set when needed
Hub:   the Hub network bind port as the internal HTTP target for Traefik
QBFT:  validator-RPC Besu JSON-RPC as the internal HTTP target for Traefik; Besu P2P remains host-published for peers
```

## Validator-RPC normalization

The all-father compiler no longer preserves validator-only or RPC-only branches.
Every compiled QBFT process is a validator-RPC node:

```text
role: validator-rpc
roles:
  - validator
  - rpc
```

If the imported QBFT seed has no service for a Coolify host, the compiler creates
a synthetic host-local validator-RPC entry so every deployed super-container has
its own RPC endpoint.  Imported source service names are preserved as
`source_id` or `source_name`.

Example readable compose environment values for a testnet cell:

```text
MC_ALLFATHER_GUARD_PORTS=allfather-guard=10.116.0.3:41410->41414/tcp
MC_ALLFATHER_FDB_PORTS=testneta-fdb1=10.116.0.3:4550->4550/tcp,testneta-fdb2=10.116.0.3:4551->4551/tcp
MC_ALLFATHER_HUB_PORTS=testneta-hub1=container:8785/tcp,testneta-hub2=container:8786/tcp
MC_ALLFATHER_QBFT_PORTS=testneta-validator-rpc1-rpc=container:8545/tcp,testneta-validator-rpc1-p2p=0.0.0.0:30311->30303/tcp
MC_ALLFATHER_PUBLIC_ROUTES=testneta-hub1=https://testneta-hub1.greatlibrary.io->8785/tcp,testneta-rpc1=https://testneta-rpc1.greatlibrary.io->8545/tcp
```

## Guard API

The guard answers immediately, before child processes finish converging.

```text
GET  /healthz
GET  /identity
GET  /topology
GET  /status
GET  /processes
POST /up
POST /down
POST /drain
POST /wake
POST /wake?name=<process-name>
```

## Recovery model

The guard converges the local manifest toward desired state.  Recovery is
serialized:

```text
observe local children
refresh exit state
choose at most one dead desired child
respect cooldown
wake one process
wait for the next tick
```

The default tick is 10 seconds and the restart budget is one process per tick.
This prevents the all-father cell from waking a large crashed process set all at
once.

The v1 guard serializes local recovery.  The manifest already carries the FDB
namespace that a later peer-lease layer can use so guards can claim restarts
across cells without thundering herds.


## All-father private state

The all-father deployment path should not depend on the full
`runtime/state/main_computer.private.yaml` file.  Initialize the smaller
all-father private state by copying only the Coolify API/project/server bindings:

```bash
python tools/allfather_private_state.py migrate-coolify --dry-run
python tools/allfather_private_state.py migrate-coolify --force
```

By default this reads:

```text
runtime/state/main_computer.private.yaml
```

and writes:

```text
runtime/state/all_father.private.yaml
```

The generated file has this shape:

```yaml
schema_version: 1
kind: main_computer.all_father.private_state.v1
generated_from:
  path: runtime/state/main_computer.private.yaml
  copied_sections:
    - coolify
coolify:
  hosts:
    A:
      name: coolify-a
      url: https://...
      api_token: ...
```

Wallets, chain network private keys, and broader app runtime settings are not
copied.  The all-father compiler defaults to this new file for remote Coolify
commands, while `--private-state <path>` remains available for explicit
overrides.

## Unified remote Coolify commands

The same compiler can render local files or talk to remote Coolify.  The remote
verbs follow the existing Coolify tool shape: `plan` to inspect the payload,
`apply --dry-run` to verify the remote plan without API writes, and `apply` to
create/update and deploy the services.

Remote plan for a testnet set:

```bash
python tools/coolify_allfather_container.py plan testnet \
  --set-id testnet-1 \
  --coolify \
  --set-coolify-url coolify-a:https://coolify-a.example.invalid \
  --set-coolify-url coolify-b:https://coolify-b.example.invalid \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

Remote dry-run using the default all-father private state for Coolify URLs/tokens:

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --phase heads \
  --dry-run \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

Remote head apply:

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --phase heads \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

Remote full apply, after the head guards are reachable:

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --phase full \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

Mainnet remains opt-in:

```bash
python tools/coolify_allfather_container.py apply mainnet \
  --set-id mainnet-1 \
  --allow-mainnet \
  --dry-run \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

The default remote Coolify environment is `<set-id>-allfather`, so a host can run
`testnet-1`, `mainnet-1`, and `mainnet-2` as separate Coolify services without
colliding names or ports.  The service name is derived from the set id and the
physical Coolify server name, while behavior still comes from the network
profile and desired counts.


## Deployment boundary

This is a simplification cell, not a claim of production fault isolation.  If
multiple validators run in one all-father container, they share the same kernel,
container lifecycle, and state root.  That can prove the deployment and
maintenance path, but it is not equivalent to independent validator hosts.

The first safe deployment step is `apply --phase heads`, which deploys the
guard/head mesh to every Coolify host.  Only after those guards answer should the
actual Hub, FoundationDB, and validator-RPC nodes be woken or deployed in
`--phase full`.
