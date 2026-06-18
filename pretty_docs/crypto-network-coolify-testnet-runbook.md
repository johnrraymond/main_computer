# Crypto network Coolify testnet runbook

Status: operator draft, first captured 2026-06-07.

This document records the remote Coolify install path for the crypto-network
machines. It is separate from the Website Builder publishing runbook because the
crypto boxes have extra safety constraints: Besu/QBFT ports, RPC exposure,
persistent chain data, and validator separation.

## Goal

Install Coolify on a testnet machine without disturbing the chain, then use
Coolify as the deployment controller for chain-adjacent application services.

The first target is the **testnet machine**. The install command is simple, but
the operator discipline matters:

```text
install Coolify control plane first
  -> verify ports and Docker state
  -> create the Coolify owner account immediately
  -> lock down dashboard/API exposure
  -> deploy crypto-network Compose resources later
```

Do not treat the Coolify installer as the Besu/QBFT deploy command. Coolify is
the management layer. The chain, Hub, worker, indexer, database, or Website
Builder resources still need explicit Compose/resources after Coolify is healthy.

## Relationship to the chain architecture

The existing architecture note is:

```text
pretty_docs/hub-chain-testnet-mainnet-architecture.md
```

That document defines the intended shape:

```text
fast local development  -> local devnet
mainnet-like rehearsal  -> QBFT testnet
real production chain   -> QBFT mainnet
```

The important rule still applies:

```text
the Hub consumes a chain profile and RPC endpoint;
the Hub is not the blockchain and should not own consensus.
```

Coolify does not change that rule. It can host or supervise supporting services,
but validator duties, validator data, and public RPC policy must remain explicit.


## Single-file topology planner direction

The remote deploy should not stay as a manual sequence of SSH steps. The planned
operator shape is one self-contained planner script:

```text
tools/coolify_qbft_network.py
```

The editable configuration lives inside that file as `NETWORK_SEEDS`. Treat it
like an fstab-style table:

```text
network seed
  hosts
    host id -> ssh address, public address, Coolify URL, runtime root
  services
    service id -> role, host, container IP, RPC host port, P2P host port
```

The key design rule is:

```text
every hosted app/service receives an explicit host port at build time,
even when different services live on different IP addresses
```

That makes the topology promotable. A single-host rehearsal, a two-host split,
and a one-host-per-validator layout are all just different seed tables.

The first planner actions should be safe/read-only:

```powershell
python .\tools\coolify_qbft_network.py list
python .\tools\coolify_qbft_network.py validate test
python .\tools\coolify_qbft_network.py plan test
python .\tools\coolify_qbft_network.py validate testnet
python .\tools\coolify_qbft_network.py plan testnet
python .\tools\coolify_qbft_network.py compose testnet --host testnet-a
python .\tools\coolify_qbft_network.py write testnet --out runtime\coolify-qbft\testnet
```

`test` is the local Coolify-managed QBFT seed. It follows the Website Builder
local-Coolify bootstrap/token-file contract and publishes
`runtime/deployments/test/latest.json`. `testnet` remains the hosted Coolify
rehearsal seed.

The generated output is the handoff into Coolify Raw Docker Compose:

```text
runtime/coolify-qbft/testnet/plan.json
runtime/coolify-qbft/testnet/operator-commands.txt
runtime/coolify-qbft/testnet/docker-compose.<host-id>.yml
```

The script should refuse unsafe layouts before anything is deployed. Topology
minimums come from each seed's `topology_policy`, not from hardcoded
testnet/mainnet assumptions:

```text
duplicate service ids
unknown hosts
container IP outside the Docker subnet
duplicate host ports
validator count below topology_policy.minimum_validators
RPC node count below topology_policy.minimum_rpc_nodes
the mainnet seed without an explicit acknowledgement
```

The current default `testnet` seed is intentionally low-resource: one Besu/QBFT
validator that also owns the operator RPC port. That is a temporary test-machine
constraint, not the long-term fault-tolerant topology. The lightweight `mainnet`
bring-up seed still uses one validator plus one dedicated RPC node and requires
explicit acknowledgement.

Actual deployment can come after the planner proves the layout. The deploy step
should call Coolify or SSH only after the same seed has already rendered a stable
plan and Compose file.

## Correct install sequence for the testnet machine

The crypto-network rule is:

```text
install and verify Docker first
  -> then install Coolify
  -> then deploy crypto-network resources
```

Do not run the Coolify quick installer on a testnet/mainnet rehearsal host until
Docker responds cleanly. Coolify can install Docker automatically, but for a
chain host we need a known-good, pinned Docker stack before any genesis,
validator keys, or Besu data directories exist.

SSH to the testnet machine as root:

```bash
ssh root@<TESTNET_MACHINE_IP>
```

Run a host preflight before installing Docker or Coolify:

```bash
hostnamectl || true
df -h
df -ih
free -h
sudo ss -tulpn | grep -E ':(8000|6001|6002|80|443|8545|8546|30303|30010)\b' || true
```

Install Docker from Docker's official Ubuntu apt repository, not Ubuntu
`docker.io`, not Snap, and not `podman-docker`:

```bash
set -e

sudo install -m 0755 -d /etc/apt/keyrings

sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc

sudo chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
```

For this project, pin a conservative Docker 28 stack before installing Coolify:

```bash
sudo apt-get install -y \
  docker-ce=5:28.5.2-1~ubuntu.24.04~noble \
  docker-ce-cli=5:28.5.2-1~ubuntu.24.04~noble \
  containerd.io=1.7.29-1~ubuntu.24.04~noble \
  docker-buildx-plugin=0.30.1-1~ubuntu.24.04~noble \
  docker-compose-plugin=2.40.3-1~ubuntu.24.04~noble

sudo apt-mark hold \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

sudo apt-get remove -y docker-model-plugin || true
sudo systemctl enable --now containerd docker
```

If `apt-get update` does not show `download.docker.com`, stop and fix the apt
source before trying the pinned install. Symptoms of a missing Docker apt source
look like:

```text
Package docker-ce is not available
Unable to locate package containerd.io
Command 'docker' not found
```

Do not fall back to `apt install docker.io`, `apt install podman-docker`, or a
Snap Docker package for the testnet machine.

Verify Docker before running Coolify:

```bash
docker version
docker compose version
timeout 5 curl --unix-socket /var/run/docker.sock http://localhost/_ping
timeout 5 docker ps
timeout 5 docker info >/tmp/docker-info.txt && echo "docker info ok"
```

Expected Docker socket result:

```text
OK
```

If `docker ps`, `docker info`, or raw socket `/_ping` hangs, do not install
Coolify yet. Restart or rebuild the Docker host before any chain state is
created.

Only after Docker passes should the Coolify quick installer be run:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

If the shell is not root, run:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

After installation, check that Coolify containers exist and Docker still answers
quickly:

```bash
timeout 10 docker ps -a --filter "name=coolify"
timeout 5 curl --unix-socket /var/run/docker.sock http://localhost/_ping
```

Open the dashboard at the URL printed by the installer, usually:

```text
http://<TESTNET_MACHINE_IP>:8000
```

Create the owner/admin account immediately. Do not leave the first-run
registration page exposed.

## Crypto-network preflight checklist

Before running the installer on a machine that already hosts chain services,
capture the live port and container state:

```bash
hostnamectl || true
df -h
free -h
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}' || true
sudo ss -tulpn
```

Look specifically for conflicts with Coolify's direct-access ports:

```text
8000/tcp  Coolify dashboard direct HTTP
6001/tcp  Coolify realtime communications
6002/tcp  Coolify terminal access
80/tcp    reverse-proxy HTTP / ACME
443/tcp   reverse-proxy HTTPS
22/tcp    SSH
```

If any existing chain, Hub, or reverse-proxy service already owns these ports,
stop and decide the topology before installing. Do not guess.

Also inventory chain-facing ports separately. Common Besu/QBFT shapes include:

```text
8545/tcp   JSON-RPC inside a private network or controlled public endpoint
8546/tcp   WebSocket RPC, only if intentionally enabled
30303/tcp  devp2p / validator peer traffic
```

The exact ports for this project must come from the live Compose/systemd files
and the deployment runtime. The local QBFT smoke lab uses a host RPC mapping of
`127.0.0.1:30010` to a Besu RPC container port of `8545`, but a remote testnet
should not blindly copy local smoke ports.

## Firewall policy for the testnet host

During first install, allow only what is needed:

```text
22/tcp    operator IP only
80/tcp    public if Coolify will issue HTTPS certificates on this host
443/tcp   public if Coolify will serve dashboard/resources over HTTPS
8000/tcp  operator IP only until dashboard domain works
6001/tcp  operator IP only during initial setup if needed
6002/tcp  operator IP only during initial setup if needed
```

After the dashboard is reachable through its HTTPS domain, close direct public
access to:

```text
8000/tcp
6001/tcp
6002/tcp
```

Keep chain RPC private unless the testnet plan explicitly requires public RPC.
If public RPC is required, put a clear policy in the deploy plan before opening
it:

```text
who can call it
which methods are enabled
whether WebSocket is enabled
rate limits
TLS/proxy path
monitoring and abuse response
```

Never open validator private keys, validator data directories, database volumes,
or internal Docker networks as a side effect of making Coolify reachable.

## DNS names to decide before production-like testnet use

Use separate names for dashboard, application services, and RPC. For example:

```text
Coolify dashboard: https://coolify-testnet.example.com
Hub API:           https://hub-testnet.example.com
Indexer/API:       https://indexer-testnet.example.com
RPC, if public:    https://rpc-testnet.example.com
```

Do not reuse the dashboard hostname for a chain RPC endpoint or a website.

## API token for Main Computer automation

After the dashboard is initialized:

```text
Settings -> Advanced -> API Access: enabled
Security -> API Tokens -> create token
```

Store the token outside Git. Coolify tokens are bearer tokens, scoped to the
active team. For a deployment automation token, prefer the smallest permissions
that work. If a bootstrap step temporarily needs broad access, rotate to a
narrower long-lived token afterward.

Main Computer runtime state should refer to the token indirectly, for example
through an environment variable or local runtime file, not through source code.


## No-SSH apply path

The preferred remote testnet path is now a Coolify-API apply run, not a manual
SSH/mkdir/paste workflow.

Operator command shape:

```powershell
python .\tools\coolify_qbft_network.py apply testnet --all `
  --single-host root@157.245.92.74 `
  --coolify-url "http://157.245.92.74:8000" `
  --coolify-token "<token>" `
  --public-rpc
```

Use `--coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN` instead of
`--coolify-token` when possible.

The generated Docker Compose includes a one-shot service:

```text
qbft-bootstrap
```

`qbft-bootstrap` runs inside the Coolify-managed stack and writes the chain
identity files into a persistent named Docker volume before Besu starts. For the
current low-resource default testnet seed, that means one validator and no
dedicated RPC sidecar:

```text
genesis.json
qbftConfigFile.json
static-nodes-all.json
validator-1/data/key
validator-1/static-nodes.json
network-metadata.json
```

The validator owns the public operator RPC port when `--public-rpc` is used. A
future promoted testnet seed can reintroduce four validators plus a dedicated
non-validator RPC node once the remote test machine can support it.

That removes the manual host-prep command:

```bash
mkdir -p /srv/main-computer/qbft-testnet/runtime
```

The script refuses to regenerate an existing network unless the Compose
environment explicitly sets:

```text
QBFT_RESET_CHAIN=true
```

For first public operator access without SSH tunneling, `--public-rpc` exposes
the operator RPC host port. In the current single-Besu testnet seed, that RPC
port belongs to `validator-1`; in the future four-validator seed it should move
back to a dedicated non-validator RPC node. Single-host validator P2P is not
published outside the Coolify private network. Use a firewall/DNS policy before
keeping public RPC open.

The apply phases are:

```text
coolify-check
coolify-sync
wait-rpc
deploy-contracts
```

For `test` and `testnet`, the contract deployment phase generates non-default
Ring 0 office wallets by default. Use `--no-generate-offices` only when you
intentionally want to supply/keep an explicit authority set. Mainnet does not
auto-generate authority wallets unless the operator passes `--generate-offices`.

`coolify-sync` first discovers the Coolify project and server, then discovers or
creates the target environment before creating/updating the service through the
API. Coolify 4.1.x service creation requires a real project, server, and project
environment; if the environment does not exist, Coolify returns `Environment not
found.` The script lists `/api/v1/projects/<project_uuid>/environments` and
creates the default `test` environment with `POST /api/v1/projects/<project_uuid>/environments`
before calling `POST /api/v1/services`.

Coolify's project API documents environment list/create endpoints under
`/projects/{uuid}/environments`, and API requests use bearer tokens scoped to
the active team.

### Fresh Coolify rebuild: no service UUID exists yet

On a brand-new Coolify install, `coolify-discover` can return projects, servers,
and environments, but no services:

```text
services : {}
```

That means there is no Coolify service UUID to pass yet. Do not pass an empty
string such as `--coolify-service-uuid ""`; an empty service UUID is not a create
request. `--coolify-service-uuid` is only for adopting or updating an existing
Coolify service.

For first creation, use the project UUID, server UUID, and desired environment
name returned by discovery, and omit `--coolify-service-uuid`:

```powershell
python .\tools\coolify_qbft_network.py apply testnet --all `
  --single-host root@<TESTNET_MACHINE_IP> `
  --coolify-url "http://<TESTNET_MACHINE_IP>:8000" `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --coolify-project-uuid "<project-uuid-from-discover>" `
  --coolify-server-uuid "<server-uuid-from-discover>" `
  --coolify-environment "test" `
  --public-rpc
```

Use the Coolify server UUID even when the discovered server is named `localhost`
and its IP is `host.docker.internal`; that is Coolify's local server record. The
`--single-host root@<TESTNET_MACHINE_IP>` argument is still the SSH/runtime host
that the QBFT planner uses for public addresses and RPC inference.

After the first successful apply, run discovery again. The newly-created service
will appear under `services`, and its `uuid` becomes the value to reuse later
with `--coolify-service-uuid` for explicit updates.

The dashboard URL must be a normal base URL:

```text
http://<TESTNET_MACHINE_IP>:8000
```

Do not include a duplicated scheme or misplaced slash, for example
`http://http://<TESTNET_MACHINE_IP>/:8000`.

The script auto-selects project/server when there is exactly one project and one
server. If there are multiple choices, inspect them:

```powershell
python .\tools\coolify_qbft_network.py coolify-discover testnet `
  --coolify-url "http://157.245.92.74:8000" `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN
```

Then rerun with explicit names or UUIDs:

```powershell
python .\tools\coolify_qbft_network.py apply testnet --all `
  --single-host root@157.245.92.74 `
  --coolify-url "http://157.245.92.74:8000" `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --coolify-project-uuid "<project-uuid>" `
  --coolify-server-uuid "<server-uuid>" `
  --coolify-environment test `
  --public-rpc
```

The service creation request sends `environment_uuid` plus `environment_name`
and sends `docker_compose_raw` as base64, because Coolify 4.1 rejects the plain
`docker_compose` field on `POST /api/v1/services`.

If the installed Coolify build still refuses service creation by API, create one
`Docker Compose Empty` resource once in the UI and rerun with:

```powershell
python .\tools\coolify_qbft_network.py apply testnet --all `
  --coolify-url "http://157.245.92.74:8000" `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --coolify-service-uuid "<uuid>" `
  --single-host root@157.245.92.74 `
  --public-rpc
```

That fallback still keeps the actual chain deployment in the script. The manual
UI step is only resource adoption if the Coolify API cannot create an empty
Compose service on that version.


### Service exists but Coolify shows `Starting (unhealthy)`

After the first create attempt, the service record can exist even when the
deployment did not reach Docker container creation. First rediscover and capture
the service UUID:

```powershell
$disc = python .\tools\coolify_qbft_network.py coolify-discover testnet `
  --coolify-url "http://<TESTNET_MACHINE_IP>:8000" `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --quiet | ConvertFrom-Json

$disc.services | Format-Table uuid,name,status,project_uuid,server_uuid

$serviceUuid = ($disc.services | Where-Object {
  $_.name -eq "main-computer-qbft-testnet-testnet-a"
}).uuid

$serviceUuid
```

Then rerun against that existing service instead of trying to create a second
one:

```powershell
python .\tools\coolify_qbft_network.py apply testnet --all `
  --single-host root@<TESTNET_MACHINE_IP> `
  --coolify-url "http://<TESTNET_MACHINE_IP>:8000" `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --coolify-service-uuid $serviceUuid `
  --public-rpc
```

If the Coolify UI log tabs show only messages like these:

```text
Error response from daemon: No such container: qbft-bootstrap-<suffix>
Error response from daemon: No such container: rpc-1-<suffix>
Error response from daemon: No such container: validator-1-<suffix>
```

do not treat the per-container log panes as the root cause. That symptom means
Coolify has a service/container naming plan, but Docker never created the
container or the failed deployment removed it before logs were available. Look
for the deployment/worker error instead.

From the testnet machine, collect the Docker and Coolify evidence before
deleting anything:

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' \
  | grep -E 'qbft|validator|rpc|coolify' || true

docker compose ls --all | grep -E 'qbft|coolify' || true
docker network ls | grep -E 'qbft|coolify' || true
docker volume ls | grep -E 'qbft|coolify' || true

docker logs --tail=300 coolify 2>&1 || true
docker logs --tail=300 coolify-worker 2>&1 || true
docker logs --tail=300 coolify-queue 2>&1 || true
```

Also verify that the chain image can be pulled and started on that host:

```bash
docker pull hyperledger/besu:latest
docker run --rm hyperledger/besu:latest --version
```

Common root causes at this point are Compose validation errors, image pull
failures, port conflicts, Coolify worker/queue failures, or `qbft-bootstrap`
failing before the validator and RPC services are created. On a fresh host with
no chain state, deleting the failed Coolify service and recreating it is usually
safe. Once any QBFT volumes or validator keys exist, preserve evidence and state
first; do not prune Docker volumes or networks as a debugging shortcut.


## What is safe to deploy through Coolify first

First resource should be a harmless smoke service, not the validator set:

```text
tiny nginx/static smoke
  -> public HTTPS route works
  -> logs visible
  -> restart works
  -> persistent state is not involved
```

Then deploy chain-adjacent services in layers:

```text
Coolify smoke service
  -> Hub/API pointing at an already-known RPC
  -> indexer or worker with explicit environment
  -> database volumes with backups
  -> optional public RPC proxy
  -> only then consider chain node/validator resources
```

Validator deployment deserves its own explicit runbook. Do not hide validator
migration inside a generic Coolify install.

## Minimum verification after installing Coolify

From the testnet machine:

```bash
docker ps --filter "name=coolify"
curl -I http://127.0.0.1:8000 || true
```

From the operator workstation:

```bash
curl -I http://<TESTNET_MACHINE_IP>:8000
```

After the dashboard domain is configured:

```bash
curl -I https://coolify-testnet.example.com
```

After API access is enabled and a token is created:

```bash
curl https://coolify-testnet.example.com/api/v1/version \
  -H "Authorization: Bearer <COOLIFY_TOKEN>"
```

A passing install means:

```text
Coolify dashboard is reachable by the operator
owner account exists
API token works
80/443 routing works if a domain is configured
direct 8000/6001/6002 exposure has been removed or restricted
existing chain ports still behave as before
```

## Docker version failure mode captured during first droplet attempt

The first DigitalOcean testnet attempt exposed a Docker daemon API hang after
Coolify startup:

```text
timeout 5 docker ps                         -> exit 124
timeout 5 docker info                       -> exit 124
timeout 5 docker inspect coolify            -> exit 124
timeout 5 docker logs --tail=20 coolify     -> exit 124
timeout 5 docker exec coolify ...           -> exit 124
timeout 5 curl --unix-socket /var/run/docker.sock http://localhost/_ping -> exit 124
```

The host was a clean official Docker repository install, not a mixed Ubuntu/Snap
install, but it was running a newer stack:

```text
docker-ce 29.5.3
containerd.io 2.2.4
runc 1.3.5
docker-compose-plugin 5.1.4
daemon: containerd-snapshotter=true storage-driver=overlayfs
```

Docker Engine 29 uses the containerd image store by default on fresh installs.
For a normal throwaway app server that may be acceptable; for a chain host, a
Docker API hang is a hard stop before genesis, validator keys, or Besu data are
created.

Operator rule from that failure:

```text
Do not install Coolify first and hope Docker is fine.
Install/pin Docker first, prove Docker API responsiveness, then install Coolify.
```

If the Docker API wedges before chain state exists, prefer rebuilding or
downgrading/pinning Docker over continuing to debug on a production-like chain
host.

## Stop conditions

Stop and reassess if any of these happen:

```text
port 80 or 443 is already owned by an existing production reverse proxy
port 8000 is already a chain/Hub service
Docker is not healthy before install
the installer replaces or disrupts existing Docker networking unexpectedly
the first-run Coolify registration page is exposed publicly
existing RPC/validator traffic changes after install
```

The safe recovery posture is to preserve chain data first, then repair Coolify.
Do not delete Docker volumes or prune networks on a live chain host without a
separate backup/rollback decision.

## Source anchors

Current upstream docs used for this runbook:

```text
https://docs.docker.com/engine/install/ubuntu/
https://docs.docker.com/engine/storage/containerd/
https://docs.docker.com/engine/storage/drivers/
https://coolify.io/docs/get-started/installation
https://coolify.io/docs/knowledge-base/server/firewall
https://coolify.io/docs/api-reference/authorization
https://coolify.io/docs/api-reference/api/operations/get-environments
https://coolify.io/docs/api-reference/api/operations/list-services
https://coolify.io/docs/api-reference/api/operations/update-service-by-uuid
```
