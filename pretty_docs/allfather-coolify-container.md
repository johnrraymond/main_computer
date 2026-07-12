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
Hub:   the Hub network bind port as the local base, incremented locally and shifted per set
QBFT:  the existing Besu RPC/P2P host ports from the QBFT plan, shifted on the host side per set
```

Example readable compose environment values for a testnet cell:

```text
MC_ALLFATHER_GUARD_PORTS=allfather-guard=10.116.0.3:41410->41414/tcp
MC_ALLFATHER_FDB_PORTS=testnet-fdb1=10.116.0.3:4550->4550/tcp,testnet-fdb2=10.116.0.3:4551->4551/tcp
MC_ALLFATHER_HUB_PORTS=testnet-hub1=10.116.0.3:8785->8785/tcp,testnet-hub2=10.116.0.3:8786->8786/tcp
MC_ALLFATHER_QBFT_PORTS=validator-1-rpc=127.0.0.1:30010->8545/tcp,validator-1-p2p=10.116.0.3:30303->30303/tcp
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

Remote dry-run using private state for Coolify URLs/tokens:

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --dry-run \
  --private-state runtime/state/main_computer.private.yaml \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

Remote apply:

```bash
python tools/coolify_allfather_container.py apply testnet \
  --set-id testnet-1 \
  --private-state runtime/state/main_computer.private.yaml \
  --coolify-project-name main-computer \
  --coolify-server-name local-docker
```

Mainnet remains opt-in:

```bash
python tools/coolify_allfather_container.py apply mainnet \
  --set-id mainnet-1 \
  --allow-mainnet \
  --dry-run \
  --private-state runtime/state/main_computer.private.yaml \
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

The first safe deployment step is to compile and inspect the raw compose files,
then deploy one all-father service per Coolify server.
