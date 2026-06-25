# Hub Coolify Deployment Runbook

This runbook explains how to deploy the hosted Main Computer Hub service with
`tools/coolify_hub_service.py` and how to deploy the shared testnet
FoundationDB layer with `tools/coolify_fdb_cluster.py`.

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

### Shared testnet FoundationDB cluster layer

For the two-Coolify-host testnet topology, deploy the FoundationDB layer before
deploying the Hub processes. This is a separate deployment surface from the Hub
application deployer:

```text
tools/coolify_fdb_cluster.py  -> shared FoundationDB layer only
tools/coolify_hub_service.py  -> Hub application/service deploys
```

The placement file is committed at:

```text
deploy/hub-topology/testnet-coolify-deployment.json
```

That file deliberately uses stable symbolic Coolify host names such as
`coolify-a` and `coolify-b`. The real Coolify API base URLs and tokens are
provided at runtime, not committed to Git.

The placement file owns:

```text
network key
topology path
symbolic Coolify host names
private/VPN bind addresses for FoundationDB
FoundationDB instance placement
FoundationDB cluster description/id
FoundationDB cluster file path
FoundationDB namespace
Hub-to-Coolify placement names
```

The command line owns:

```text
symbolic Coolify name -> Coolify API base URL
symbolic Coolify name -> Coolify API token or token environment variable
Coolify project/environment selection
whether to only render the plan or to actually apply/deploy
```

Do not put public droplet addresses, private VPN addresses, or Coolify API tokens
in this runbook. Use placeholders such as `ipaddress1`, `ipaddress2`, `vpnip1`,
and `vpnip2` in documentation and tests.

#### Coolify URL and token binding

Bind the symbolic Coolify names to the actual Coolify API URLs when running the
deployer:

```powershell
$env:COOLIFY_A_TOKEN = "<raw-coolify-api-token-for-coolify-a>"
$env:COOLIFY_B_TOKEN = "<raw-coolify-api-token-for-coolify-b>"

python .\tools\coolify_fdb_cluster.py plan `
  --placement deploy\hub-topology\testnet-coolify-deployment.json `
  --set-coolify-url "coolify-a:http://ipaddress1:8000" `
  --set-coolify-url "coolify-b:http://ipaddress2:8000" `
  --coolify-project-name "My first project" `
  --coolify-environment-name "testnet-fdb"
```

The value format is:

```text
<symbolic-coolify-name>:<coolify-api-base-url>
```

The parser splits on the first colon only, so URLs with their own scheme and port
are valid. If the raw `:8000` Coolify API is plain HTTP, use `http://...`; using
`https://...` against a plain HTTP listener will fail during the version check
with an SSL `WRONG_VERSION_NUMBER` style error.

For live apply, pass the token environment variable bindings and omit
`--dry-run`:

```powershell
python .\tools\coolify_fdb_cluster.py apply `
  --placement deploy\hub-topology\testnet-coolify-deployment.json `
  --set-coolify-url "coolify-a:http://ipaddress1:8000" `
  --set-coolify-url "coolify-b:http://ipaddress2:8000" `
  --set-coolify-token-env "coolify-a:COOLIFY_A_TOKEN" `
  --set-coolify-token-env "coolify-b:COOLIFY_B_TOKEN" `
  --coolify-project-name "My first project" `
  --coolify-environment-name "testnet-fdb" `
  --force-deploy
```

`apply --dry-run` is intentionally non-mutating: it renders the full apply plan
and generated Compose payloads but does not create/update Coolify resources.
Remove `--dry-run` when you are ready for the tool to call Coolify.

#### What the FDB deployer creates

The FDB deployer creates one Coolify Service per symbolic host:

```text
main-computer-testnet-fdb-coolify-a
main-computer-testnet-fdb-coolify-b
```

For the current three-FDB-process testnet layout, the intended logical placement
is:

```text
coolify-a:
  testnet-fdb1 on vpnip1:4550
  testnet-fdb2 on vpnip1:4551

coolify-b:
  testnet-fdb3 on vpnip2:4550
```

The real VPN/private bind addresses come from `servers[].vpn_ip` in the placement
file. The generated Compose publishes FDB only on those configured private/VPN
addresses and ports. Do not publish FoundationDB through public DNS, public
Traefik routes, or a public interface.

Every FDB process and every Hub process must read the same cluster file path:

```text
/data/main-computer/hub/testnet-exp-fdb/fdb.cluster
```

The cluster file contents have this shape:

```text
main-computer-testnet:<stable-cluster-id>@vpnip1:4550,vpnip1:4551,vpnip2:4550
```

Hub services must use the same namespace from the placement file:

```text
main-computer-testnet-exp-fdb-stable-live-sessions
```

The generated FDB containers override the FoundationDB image entrypoint and run a
small shell bootstrap script directly. That is important: if the default image
wrapper runs instead, it can start a stray FDB server on the container's default
port and ignore the intended private/VPN bind ports.

Expected healthy FDB container logs begin with a Main Computer bootstrap message
for the configured FDB instance and configured private/VPN port. They should not
dump the shell environment, and they should not say that the image wrapper is
starting a default server on a Docker-internal address and default port.

#### Host-level verification

After apply/deploy, SSH to each Coolify host and confirm that the expected FDB
ports are listening on the private/VPN interface only.

Set placeholders on each host before running checks:

```bash
VPNIP1="vpnip1"
VPNIP2="vpnip2"
FDB_CLUSTER="main-computer-testnet:<stable-cluster-id>@${VPNIP1}:4550,${VPNIP1}:4551,${VPNIP2}:4550"
```

Check local listeners:

```bash
ss -ltnp | grep -E '(:4550|:4551)' || true
```

Check TCP reachability from the host:

```bash
python3 - <<'PY'
import os
import socket

vpnip1 = os.environ["VPNIP1"]
vpnip2 = os.environ["VPNIP2"]
endpoints = [
    ("testnet-fdb1", vpnip1, 4550),
    ("testnet-fdb2", vpnip1, 4551),
    ("testnet-fdb3", vpnip2, 4550),
]

for name, host, port in endpoints:
    sock = socket.socket()
    sock.settimeout(4)
    try:
        sock.connect((host, port))
        print(f"OK   {name} {host}:{port}")
    except Exception as exc:
        print(f"FAIL {name} {host}:{port} -> {type(exc).__name__}: {exc}")
    finally:
        sock.close()
PY
```

Run an FDB client status check without letting the FoundationDB image start its
default server:

```bash
tmpdir="$(mktemp -d)"
printf '%s\n' "$FDB_CLUSTER" > "$tmpdir/fdb.cluster"

docker run --rm --network host \
  --entrypoint fdbcli \
  -v "$tmpdir/fdb.cluster:/etc/foundationdb/fdb.cluster:ro" \
  foundationdb/foundationdb:7.4.6 \
  -C /etc/foundationdb/fdb.cluster \
  --exec "status minimal" \
  --timeout 10

rm -rf "$tmpdir"
```

Interpretation:

```text
TCP fails for local private/VPN addresses:
  Docker publish, bind address, or local firewall is wrong.

TCP fails only for the other host:
  Private/VPN routing or inter-host firewalling is wrong.

TCP succeeds but fdbcli fails:
  The ports are reachable, but the FDB cluster is not configured/healthy yet.

fdbcli status minimal succeeds:
  The shared FDB cluster is reachable from that host.
```

#### Common failure signatures

`HTTP 401` during the Coolify version check means the tool reached Coolify but
the token used for that symbolic host was rejected. Confirm that the token
environment variable is set in the same shell and that the raw token belongs to
that specific Coolify instance.

`SSL: WRONG_VERSION_NUMBER` during the Coolify version check usually means the
command used `https://` for a plain HTTP Coolify API listener. Use the scheme that
the Coolify API actually serves.

A plan printed by `apply` with `"dry_run": true` means the command included
`--dry-run`; no Coolify resources were changed.

FDB logs that dump the whole shell environment mean the generated shell argv was
wrong and the container ran `set` rather than the bootstrap script. Use the fixed
deployer and force a redeploy.

FDB logs that show the FoundationDB image wrapper starting a default server on a
Docker-internal address and default port mean the generated Compose did not
override the image entrypoint. Use the fixed deployer and force a redeploy.

A cluster file containing a Docker service alias from an old Hub sidecar means a
stale per-Hub FDB sidecar is still writing to the same runtime path. Stop/remove
the old sidecar or move the shared FDB layer to a clean runtime directory before
trusting the testnet cluster.

Do not launch the multi-hub testnet services against isolated per-Hub sidecars.
All three Hubs must use the shared cluster file and namespace above.


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
  --coolify-server-uuid "<coolify-server-uuid>" `
  --git-repo https://github.com/<owner>/<repo> `
  --fdb-cluster-file /data/main-computer/hub/testnet-exp-fdb/fdb.cluster `
  --fdb-namespace main-computer-testnet-exp-fdb-stable-live-sessions `
  --force-deploy `
  --rpc-check warn `
  --hub-health-check warn `
  --enable-bridge-writes `
  --sync-bridge-signer
```

That command is the single-service testnet Hub calling convention. For the
three-hub testnet topology, deploy the shared FoundationDB layer first and make
each Hub service read the shared cluster file from:

```text
/data/main-computer/hub/testnet-exp-fdb/fdb.cluster
```

Do not use isolated per-Hub FoundationDB sidecars for the three-hub testnet
topology.

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
--no-deploy               Create/update the Coolify app but do not trigger deploy.
--force-deploy            Force a deploy trigger when Coolify supports it.
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
