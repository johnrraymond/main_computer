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

Testnet:

```powershell
python .\tools\coolify_hub_service.py apply testnet `
  --hub-implementation exp-fdb `
  --coolify-url $CoolifyUrl `
  --coolify-project-name $ProjectName `
  --coolify-environment-name "testnet" `
  --coolify-server-name $ServerName `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-testnet-exp-fdb
```

For `testnet` and `mainnet`, the apply command creates or updates the normal
public Coolify application name:

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
