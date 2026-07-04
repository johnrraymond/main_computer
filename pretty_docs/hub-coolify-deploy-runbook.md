# Hub Coolify Deployment Runbook

This runbook explains how to deploy the hosted Main Computer Hub service with
`tools/coolify_hub_service.py`.

The hosted Hub now uses the experimental FoundationDB-backed Hub implementation
served by `exp-fdb-hub.py`. The legacy non-FDB Hub Dockerfiles have been removed;
Coolify should use `/Dockerfile.hub.exp-fdb` for both `testnet` and `mainnet`.

## Inputs the deployer needs

Run all commands from the repository root.

The deployer reads hosted network settings from
`main_computer/config/hub_networks.json`. The networks currently exposed by the
tool are:

```text
test      local Coolify + local QBFT rehearsal       port 8780   chain 42424241
testnet   https://testnet-hub.greatlibrary.io        port 8785   chain 42424241
mainnet   https://mainnet-hub.greatlibrary.io        port 8790   chain 42424240
```

`test` is intentionally local-Coolify-facing. It uses the same local Coolify
surface as the Website Builder/local-prod smoke and the local Besu/QBFT rehearsal.
When the Website Builder has already published through local Coolify, do not
start a second local Coolify stack. The Hub deployer discovers that existing
target from `MAIN_COMPUTER_COOLIFY_*` environment variables or from
`runtime/applications_service/applications.env` (`COOLIFY_LOCAL_STATE` and
`APP_PORT`). It falls back to `runtime/coolify-local-docker/api-token.txt` only
for repo-local smoke setups. The default project is `Main Computer Local Smoke`,
environment `production`, and server target `localhost`.

For `apply`, provide Coolify access and placement information. `plan` can render
the payload without a token, but `apply` needs a token.

PowerShell example:

```powershell
$env:MAIN_COMPUTER_COOLIFY_TOKEN = "<coolify-api-token>"
$CoolifyUrl = "https://coolify.example.com"
$ProjectName = "Main Computer"
$EnvironmentName = "production"
$ServerName = "chain-host-01"
$GitRepo = "https://github.com/<owner>/<repo>.git"
```

You can use UUID flags instead of names when the Coolify IDs are known:

```text
--coolify-project-uuid
--coolify-environment-uuid
--coolify-server-uuid
--coolify-destination-uuid
--coolify-application-uuid
```

## Local `test` target

Use this before `testnet` or `mainnet` when you want the Hub to land on the same
local Coolify target surface as the local Besu/QBFT rehearsal.

First make sure the local Coolify surface already works. If Website Builder can
publish to local Coolify, this prerequisite is already satisfied. Do **not** run
`coolify-local-docker.py up` again just to deploy the Hub; that can collide with
the already-running local Coolify port.

If you are not running through the Website Builder/app launcher environment,
point the Hub deployer at the same local state explicitly:

```powershell
$env:MAIN_COMPUTER_COOLIFY_STATE_DIR = "C:\path\to\coolify-local-docker"
```

or pass:

```text
--local-coolify-state-dir C:\path\to\coolify-local-docker
```

Then render the Hub plan. Local `test` does not require `--git-repo` because it stages your local working tree directly into the Coolify service workspace:

```powershell
python .\tools\coolify_hub_service.py plan test
```

The local test plan should include a Coolify **service** payload, not an
`applications/public` payload. Local Coolify has already proven this raw
Docker Compose service path through the Besu/QBFT rehearsal, so `apply test`
uses the same target surface and avoids the application-create path that can
500 in local Coolify builds.

```text
resource kind:       service
name:                main-computer-test-hub
dockerfile:          Dockerfile.hub.exp-fdb
coolify_url:         http://127.0.0.1:8000
environment_name:    production
runtime_dir:         /srv/main-computer/hub/test-exp-fdb
cluster_file:        /srv/main-computer/hub/test-exp-fdb/fdb.cluster
namespace:           main-computer-test-exp-fdb
hub public URL:      http://127.0.0.1:8780
operator RPC check:  http://127.0.0.1:30010
Hub container RPC:   http://host.docker.internal:30010
bridge backend:      dev-chain
deployment manifest: /app/runtime/deployments/test/latest.json
```

Hub deploys default to the real dev-chain/contract bridge backend. Mock-chain is
only for explicit lab/fake-chain runs:

```text
--bridge-backend mock-chain
```

The local `test` service bind-mounts `runtime/deployments` read-only into
`/app/runtime/deployments`, so the default test deployment manifest and any
generated local wallet files stay outside the image but are visible to the Hub.

The rendered local Compose service deliberately does **not** build from the
remote Git URL. Local Coolify deploys raw Docker Compose resources from
`/data/coolify/services/<uuid>`, so the deployer stages a relative build context
there before triggering `/deploy`:

```text
/data/coolify/services/<service-uuid>/hub-src
```

The Compose uses:

```yaml
build:
  context: "./hub-src"
  dockerfile: "Dockerfile.hub.exp-fdb"
```

This mirrors the Website Builder local publish path and avoids Docker Compose
misreading a remote Git context as a giant Dockerfile.

The service publishes `127.0.0.1:8780:8780`, adds
`host.docker.internal:host-gateway`, and bind-mounts the local runtime directory
into `/srv/main-computer/hub/test-exp-fdb`. By default the host runtime directory is:

```text
runtime/hub/test-exp-fdb
```

Set `MAIN_COMPUTER_HUB_TEST_RUNTIME_HOST_DIR` or pass
`--local-hub-runtime-host-dir` to use a different host directory.

Apply to the local Coolify target:

```powershell
python .\tools\coolify_hub_service.py apply test `
  --hub-health-check warn `
  --rpc-check warn
```

No Git commit is required for `apply test`. The deployer copies the current
working-tree files from the repository root into the staged `hub-src` context
before triggering Coolify deploy. Use `--local-source-dir` or
`MAIN_COMPUTER_HUB_TEST_SOURCE_DIR` only when staging from a different checkout.

For `apply test`, the deployer reads the same local token style used by Website
Builder: `MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN`, `MAIN_COMPUTER_COOLIFY_LOCAL_TOKEN_FILE`,
or `<local-coolify-state-dir>/api-token.txt`. The token file may be either a raw
token or the generated key/value format containing `token=...` and `dashboard=...`.
Override these only when the local surface is not discoverable:

```text
--coolify-url
--coolify-project-name
--coolify-environment-name
--coolify-server-name
--local-coolify-state-dir
--local-coolify-token-file
--applications-service-env-file
--hub-chain-rpc-url
--bridge-backend
--dev-chain-deployment-path
--local-hub-runtime-host-dir
--local-source-dir
```

For `testnet` and `mainnet`, `--git-repo` is still required because those are remote application deploys that build from Git.

The `test` profile keeps the operator-facing RPC URL as `http://127.0.0.1:30010`
for preflight checks, but passes `http://host.docker.internal:30010` to the Hub
container by default. If your local Coolify/Docker host needs a different
container-to-host route, pass `--hub-chain-rpc-url`.

## FoundationDB requirement

The hosted Hub requires FoundationDB. The deployer intentionally passes
`--no-fdb-autostart`; the Hub process itself must not start a smoke-only FDB
instance.

For the local `test` target, the Coolify service now owns a one-node
FoundationDB sidecar named `main-computer-test-hub-fdb`. The Hub bootstrap
command writes this sidecar-facing cluster file before starting the Hub:

```text
/srv/main-computer/hub/test-exp-fdb/fdb.cluster
```

with contents:

```text
docker:docker@main-computer-test-hub-fdb:4550
```

Then it runs `fdbcli` until the sidecar accepts `status`, configuring
`single memory` if needed. You do not need to manually create
`runtime/hub/test-exp-fdb/fdb.cluster` for `apply test`.

For `testnet` and `mainnet`, FoundationDB remains external to the Hub
application. Before applying, make sure the Coolify application can read the
cluster file at the container path passed to `--fdb-cluster-file`. A common
mounted path is:

```text
/data/main-computer/fdb/fdb.cluster
```

If `--fdb-cluster-file` is omitted, the remote deployer defaults to:

```text
/data/main-computer/hub/<network>-exp-fdb/fdb.cluster
```

### Shared testnet Hub/FDB deploy packets

For `testnet` and `mainnet`, treat the committed topology and placement files as
the catalog of Hub/FDB components the operator knows how to deploy. They do not
have to mean "everything in this file is serving right now." The active serving
shape is selected by a local deploy packet.

The deploy packet is a single local JSON file:

```text
deploy/packets/testnet-packet.json
deploy/packets/mainnet-packet.json
```

These files are local operator state and should not be committed. When an
existing packet is replaced with different contents, the prep command archives
the previous copy under:

```text
deploy/packets/archive/<network>/
```

After a generation has been applied and verified, save the verified packet as:

```text
deploy/packets/testnet-deployed.json
deploy/packets/mainnet-deployed.json
```

The packet model keeps the catalog and the active deploy separate:

```text
topology/placement files = every known Hub/FDB component
packet file             = the Hub/FDB components enabled for this generation
```

Components known in the catalog but not selected in the packet remain present in
the packet with `enabled: false`. The deployers still render config for every
known Coolify host so a host with no enabled Hubs/FDB instances receives a
non-serving config for that network instead of being silently ignored.

The packet currently covers only the Hub/FDB layers. Besu/QBFT validators and
RPC nodes still use the separate chain deploy path.

List the known deployable components through the cluster-level control surface:

```powershell
python .\tools\coolify_cluster.py list-components testnet
```

The normal operator path is now the cluster orchestrator, not the lower-level FDB
or Hub deployer. The orchestrator builds the candidate packet, sanity-checks the
private/local inputs, writes the packet to the known local path, then runs the
FDB and Hub stages from that same packet.

Generate and inspect a candidate packet for the full current two-host testnet
shape without touching Coolify:

```powershell
python .\tools\coolify_cluster.py preflight testnet `
  --hubs testnet-hub1,testnet-hub2,testnet-hub3 `
  --fdb testnet-fdb1,testnet-fdb2,testnet-fdb3 `
  --git-repo "https://github.com/johnrraymond/main_computer"
```

The preflight action does **not** write `deploy/packets/testnet-packet.json`. It
builds the packet in memory and checks the things the later plan/apply will need:

```text
selected Hub ids exist in the catalog
selected FDB ids exist in the catalog
the packet would enable at least one Hub and one FDB instance
the default packet path is usable
runtime/state/main_computer.private.yaml exists unless --no-private-state is used
coolify.hosts contains entries matching coolify-a/coolify-b
each host has a URL and usable api_token/api_token_env/api_token_file
--git-repo is present for the Hub stage
```

If preflight fails, it prints structured problems plus remediation commands, for
example commands to list components, sync/create the private state file, or rerun
with explicit `--set-coolify-url` and token flags. Use `--no-private-state` only
for deliberate override/debug runs where all URLs and tokens are supplied on the
CLI.

Plan the full cluster. This first passes the same preflight, then writes the
candidate packet to `deploy/packets/testnet-packet.json`, then renders both the
FDB and Hub plans from that packet:

```powershell
python .\tools\coolify_cluster.py plan testnet `
  --hubs testnet-hub1,testnet-hub2,testnet-hub3 `
  --fdb testnet-fdb1,testnet-fdb2,testnet-fdb3 `
  --git-repo "https://github.com/johnrraymond/main_computer"
```

Apply the full cluster. This also performs the lookahead before writing the
packet or making Coolify calls. If lookahead fails, no packet is written and no
Coolify apply starts:

```powershell
python .\tools\coolify_cluster.py apply testnet `
  --hubs testnet-hub1,testnet-hub2,testnet-hub3 `
  --fdb testnet-fdb1,testnet-fdb2,testnet-fdb3 `
  --git-repo "https://github.com/johnrraymond/main_computer" `
  --force-deploy
```

Generate a smaller packet and plan/apply it by changing only the selected ids:

```powershell
python .\tools\coolify_cluster.py plan testnet `
  --hubs testnet-hub1,testnet-hub2 `
  --fdb testnet-fdb1 `
  --git-repo "https://github.com/johnrraymond/main_computer"
```

`--hubs` and `--fdb` are comma-separated component ids. The `-hubs` and `-fdb`
aliases are also accepted for operator convenience, but the double-dash spelling
is preferred in docs and scripts.

The normal path uses private state automatically for Coolify host URL, token,
project, server, and destination context. The expected local file is:

```text
runtime/state/main_computer.private.yaml
```

and it should contain manually maintained Coolify host slots such as:

```yaml
coolify:
  project_name: Main Computer
  # project_uuid is also supported when you prefer UUIDs.
  # fdb_environment_name and hub_environment_name are optional; the defaults are
  # <network>-fdb and <network>-hubs.
  hosts:
    A:
      name: coolify-a
      url: http://<coolify-a-public-ip>:8000/
      api_token: "<stored locally; do not commit or print>"
      # Optional lookahead/apply helpers:
      # server_name: "<Coolify server name>"
      # server_uuid: "<Coolify server uuid>"
      # destination_uuid: "<Coolify destination uuid>"
    B:
      name: coolify-b
      url: http://<coolify-b-public-ip>:8000/
      api_token: "<stored locally; do not commit or print>"
      # Optional per-host project_uuid is supported when hosts use different
      # Coolify projects; a shared project_name is simpler when both use the
      # same project.
```

The orchestrator and lower-level deployers match packet/placement host names
such as `coolify-a` and `coolify-b` to those private-state slots. The
`coolify.project_name` or `coolify.project_uuid` value is also read from the same
private file, so the normal `coolify_cluster.py` commands do not need a
`--coolify-project-name` flag. CLI `--set-coolify-url`, project, server,
destination, and token flags are still available as explicit overrides, but
should not be the normal operator path. Pass `--no-private-state` only for
deliberate override/debug runs where all required private values are supplied on
the CLI.

The orchestrator preflight checks that the private state has enough information
for the later apply path before it writes the candidate packet or calls Coolify.
For example, a missing `coolify.project_name`/`project_uuid` is reported as a
preflight problem instead of surfacing later as a lower-level Coolify apply
exception.

For plan/apply, pass the network name instead of the packet filename. The
standard packet path is resolved automatically:

```text
testnet -> deploy/packets/testnet-packet.json
mainnet -> deploy/packets/mainnet-packet.json
```

Use `--packet <path>` only when intentionally testing a non-standard packet
location.

The lower-level layer scripts remain available for debugging a stage in
isolation after a packet exists:

```powershell
python .\tools\coolify_fdb_cluster.py plan testnet
python .\tools\coolify_hub_cluster.py plan testnet `
  --git-repo "https://github.com/johnrraymond/main_computer"
```

The FDB deployer creates one Coolify Service per known symbolic host:

```text
main-computer-testnet-fdb-coolify-a
main-computer-testnet-fdb-coolify-b
```

For hosts with enabled FDB instances, the rendered Compose starts those FDB
processes and writes the packet-selected cluster file, for example:

```text
/data/main-computer/hub/testnet-exp-fdb/fdb.cluster
```

For known hosts with no enabled FDB instances, the rendered Compose writes the
same packet-selected cluster file and keeps a non-serving config container alive.
That host is therefore explicitly configured for "no enabled testnet FDB here"
instead of being omitted.

Plan the Hub layer from the same packet:

```powershell
$GitRepo = "https://github.com/johnrraymond/main_computer"

python .\tools\coolify_hub_cluster.py plan testnet `
  --git-repo $GitRepo
```

Apply the Hub layer from the packet:

```powershell
python .\tools\coolify_hub_cluster.py apply testnet `
  --git-repo $GitRepo `
  --force-deploy
```

The Hub deployer also creates one Coolify Service per known symbolic host:

```text
main-computer-testnet-hubs-coolify-a
main-computer-testnet-hubs-coolify-b
```

For hosts with enabled Hubs, the generated Compose writes the packet-selected
Hub topology to:

```text
/data/main-computer/hub/testnet-exp-fdb/deploy-packet-topology.json
```

and starts each enabled Hub with:

```text
--topology /data/main-computer/hub/testnet-exp-fdb/deploy-packet-topology.json
--cluster-file /data/main-computer/hub/testnet-exp-fdb/fdb.cluster
--namespace main-computer-testnet-exp-fdb-stable-live-sessions
--no-fdb-autostart
--require-multisession-auth
```

For known hosts with no enabled Hubs, the generated Compose writes the same
packet-selected topology and FDB cluster file, then keeps a non-serving config
container alive. The host is therefore configured to serve no Hubs for that
network generation.

The public entry alias, `https://testnet-hub.greatlibrary.io`, is managed by
the default Traefik sidecar. The sidecar adds a long-running non-routed config
manager container to each Coolify Hub service. No install flag is needed. Pass
`--no-traefik-sidecar` only to disable that public-entry manager. On hosts with
enabled Hubs, the manager writes the packet-selected Traefik dynamic config under
`/data/coolify/proxy/dynamic`. On known hosts with no enabled Hubs, it removes
the stale per-host public-entry config and stays healthy so old packet routing
does not survive a contraction.

Enable or trust the shared public entry only after the enabled concrete Hub
hostnames are healthy. Verify the concrete Hubs from the packet directly first.

After apply and verification, promote the candidate packet to the local deployed
record:

```powershell
Copy-Item deploy\packets\testnet-packet.json deploy\packets\testnet-deployed.json
```

Do not promote a packet merely because the plan rendered successfully. Promote it
only after the remote services are updated and the enabled Hubs/FDB layer match
the intended generation.


## Render the plan

Mainnet:

```powershell
python .\tools\coolify_hub_service.py plan mainnet `
  --hub-implementation exp-fdb `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-mainnet-exp-fdb
```

The mainnet plan should include:

```text
name:                main-computer-mainnet-hub
dockerfile_location: /Dockerfile.hub.exp-fdb
runtime_dir:         /data/main-computer/hub/mainnet-exp-fdb
cluster_file:        /data/main-computer/fdb/fdb.cluster
namespace:           main-computer-mainnet-exp-fdb
health_check_path:   /api/hub/status
```

Testnet:

```powershell
python .\tools\coolify_hub_service.py plan testnet `
  --hub-implementation exp-fdb `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-testnet-exp-fdb
```

The testnet plan should use the same Dockerfile and the testnet port/profile:

```text
name:                main-computer-testnet-hub
dockerfile_location: /Dockerfile.hub.exp-fdb
runtime_dir:         /data/main-computer/hub/testnet-exp-fdb
cluster_file:        /data/main-computer/fdb/fdb.cluster
namespace:           main-computer-testnet-exp-fdb
```

## Apply to Coolify

Mainnet:

```powershell
python .\tools\coolify_hub_service.py apply mainnet `
  --hub-implementation exp-fdb `
  --coolify-url $CoolifyUrl `
  --coolify-project-name $ProjectName `
  --coolify-environment-name $EnvironmentName `
  --coolify-server-name $ServerName `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-mainnet-exp-fdb
```

Testnet canonical bridge-signer deployment:

```powershell
python .\tools\coolify_hub_service.py apply testnet `
  --hub-implementation exp-fdb `
  --coolify-url $CoolifyUrl `
  --coolify-project-name "My first project" `
  --coolify-environment-name "testnet-hub" `
  --coolify-server-uuid "c11j1nrxs7m2q6of6jmbxoxm" `
  --git-repo https://github.com/johnrraymond/main_computer `
  --fdb-cluster-file /data/main-computer/hub/testnet-exp-fdb/fdb.cluster `
  --fdb-namespace main-computer-testnet-exp-fdb `
  --force-deploy `
  --rpc-check warn `
  --hub-health-check warn `
  --enable-bridge-writes `
  --sync-bridge-signer
```

That command is the known-good testnet calling convention for the remote
exp-FDB Hub that owns its FoundationDB sidecar and writes `fdb.cluster` in the
same persistent host directory mounted by the Hub service:

```text
/data/main-computer/hub/testnet-exp-fdb
```

`--enable-bridge-writes` selects the non-smoke `bridge-signer` mode.
`--sync-bridge-signer` pushes the local bridge controller signer bundle through
the Coolify service environment path before deploy. Keep both flags together for
the signed testnet Hub. Do **not** replace this with `--enable-smoke-bridge`;
the smoke bridge is only for explicit admin smoke tests.

Because the remote service builds from Git, commit and push the matching Hub
code before using `--force-deploy`. The local deployer can update Coolify
configuration immediately, but the container image only sees changes that are
available from the configured Git branch or commit.

For `testnet` and `mainnet`, the apply command creates or updates the normal
public Coolify application/service name:

```text
main-computer-<network>-hub
```

For local `test`, the apply command creates or updates a Coolify Docker Compose
service with that same stable name instead of using `/api/v1/applications/public`.
That mirrors the local Besu/QBFT Coolify path and avoids local application-create
500s.

Hosted application deploys also ensure the Hub state volume is present unless
`--no-create-storage` is passed. Local `test` service deploys carry their volume
inside the generated Compose file. Both paths trigger a deploy unless
`--no-deploy` is passed and check `/api/hub/status` unless `--no-wait-hub` or
`--hub-health-check skip` is passed.

## Dockerfile

The only hosted Hub Dockerfile is:

```text
/Dockerfile.hub.exp-fdb
```

It installs the `foundationdb` Python package and a native `libfdb_c.so` client
during image build, then starts:

```text
python /app/exp-fdb-hub.py
```

The generated Coolify start command adds the hosted network identity, chain ID,
chain RPC URL, Hub URL, FDB namespace, and `--no-fdb-autostart`.

## Useful safety flags

Use these when staging the deployment:

```text
--dry-run                 For `apply`, render the apply plan without Coolify calls.
--no-deploy               Create/update the Coolify app/service but do not trigger deploy.
--force-deploy            Force a deploy trigger when Coolify supports it.
--no-traefik-sidecar      Disable the default public-entry Traefik manager sidecar.
--no-private-state        Disable runtime/state/main_computer.private.yaml lookup.
--fdb-only                Cluster orchestrator: prepare packet and run only FDB stage.
--hubs-only               Cluster orchestrator: prepare packet and run only Hub stage.
--packet <path>           Override deploy/packets/<network>-packet.json for debug runs.
--no-archive              Do not archive a replaced candidate packet.
--rpc-check warn          Warn instead of failing if the chain RPC check fails.
--rpc-check skip          Skip the pre-deploy chain RPC check.
--hub-health-check warn   Warn instead of failing if public Hub status is unhealthy.
--hub-health-check skip   Skip the post-deploy Hub status check.
--no-wait-hub             Alias-style skip for the post-deploy Hub wait.
--no-create-storage       Do not create the persistent Hub state storage.
```

For mainnet, the default RPC and Hub health checks are stricter than testnet. Use
`warn` or `skip` only when an operator has already decided that the failed check
is expected.

## Post-deploy checks

Check the public Hub status endpoint:

```powershell
Invoke-RestMethod https://mainnet-hub.greatlibrary.io/api/hub/status
Invoke-RestMethod https://testnet-hub.greatlibrary.io/api/hub/status
```

Confirm:

```text
the Coolify application uses /Dockerfile.hub.exp-fdb
the start command contains --cluster-file and --namespace
the start command contains --no-fdb-autostart
the mounted FDB cluster file exists inside the container
the FDB namespace is unique for the environment being tested
the status network object reports the expected network key and chain ID
```

If the Hub cannot read the cluster file or cannot load the native FDB client, it
should be treated as a deployment/runtime failure rather than as a chain or
Coolify routing problem.
