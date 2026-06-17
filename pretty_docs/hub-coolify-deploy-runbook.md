# Hub Coolify Deployment Runbook

This runbook explains how to deploy the public Main Computer Hub service with
`tools/coolify_hub_service.py`.

Use it for the hosted `testnet` and `mainnet` Hub services. Local developer Hub
startup remains documented in the root `README.md`.

## Deployment choices

There are two Hub implementations:

```text
regular   The normal Hub served by `python -m main_computer.cli hub`.
exp-fdb   The experimental FoundationDB-backed Hub served by `exp-fdb-hub.py`.
```

The regular Hub is the default and keeps the existing public service name:

```text
main-computer-<network>-hub
```

The experimental Hub is side-by-side by default so it does not overwrite the
regular Hub during testing:

```text
main-computer-<network>-exp-fdb-hub
```

Only pass `--replace-regular-hub` when you intentionally want the experimental
Hub to update the regular public service name.

## Inputs the deployer needs

Run all commands from the repository root.

The deployer reads the hosted network profile from
`main_computer/config/hub_networks.json`. The remote networks currently exposed
by the tool are:

```text
testnet   https://testnet-hub.greatlibrary.io   port 8785   chain 42424241
mainnet   https://mainnet-hub.greatlibrary.io   port 8790   chain 42424240
```

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

## Deploy the regular Hub

First render the plan. This does not call Coolify:

```powershell
python .\tools\coolify_hub_service.py plan mainnet `
  --git-repo $GitRepo
```

The regular mainnet plan should include:

```text
name:                main-computer-mainnet-hub
dockerfile_location: /Dockerfile.hub.mainnet
start_command:       --network mainnet --host 0.0.0.0 --port 8790 --hub-runtime-dir /data/main-computer/hub/mainnet
health_check_path:   /api/hub/status
```

Apply the regular Hub to Coolify:

```powershell
python .\tools\coolify_hub_service.py apply mainnet `
  --coolify-url $CoolifyUrl `
  --coolify-project-name $ProjectName `
  --coolify-environment-name $EnvironmentName `
  --coolify-server-name $ServerName `
  --git-repo $GitRepo
```

For testnet, change the network argument:

```powershell
python .\tools\coolify_hub_service.py apply testnet `
  --coolify-url $CoolifyUrl `
  --coolify-project-name $ProjectName `
  --coolify-environment-name "testnet" `
  --coolify-server-name $ServerName `
  --git-repo $GitRepo
```

The apply command creates or updates the Coolify application, ensures the Hub
state volume is present unless `--no-create-storage` is passed, triggers a
deploy unless `--no-deploy` is passed, and checks `/api/hub/status` unless
`--no-wait-hub` or `--hub-health-check skip` is passed.

## Deploy the experimental FoundationDB Hub side-by-side

The experimental Hub requires a running FoundationDB cluster outside of the Hub
process. The deployer intentionally passes `--no-fdb-autostart`; the hosted
container must not start a local smoke-only FDB instance.

Before applying, make sure the Coolify application will have a valid cluster file
at the container path passed to `--fdb-cluster-file`. A common mounted path is:

```text
/data/main-computer/fdb/fdb.cluster
```

Render the side-by-side experimental plan:

```powershell
python .\tools\coolify_hub_service.py plan mainnet `
  --hub-implementation exp-fdb `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-mainnet-exp-fdb
```

The side-by-side experimental mainnet plan should include:

```text
name:                main-computer-mainnet-exp-fdb-hub
dockerfile_location: /Dockerfile.hub.exp-fdb
runtime_dir:         /data/main-computer/hub/mainnet-exp-fdb
cluster_file:        /data/main-computer/fdb/fdb.cluster
namespace:           main-computer-mainnet-exp-fdb
```

Apply the side-by-side experimental Hub:

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

The experimental Dockerfile is `/Dockerfile.hub.exp-fdb`. It installs the
`foundationdb` Python package and a native `libfdb_c.so` client during image
build, then starts:

```text
python /app/exp-fdb-hub.py
```

The generated Coolify start command adds the hosted network identity, chain ID,
chain RPC URL, Hub URL, FDB namespace, and `--no-fdb-autostart`.

## Replace the regular Hub with the experimental Hub

Do this only after the side-by-side service has been validated.

Render the replacement plan first:

```powershell
python .\tools\coolify_hub_service.py plan mainnet `
  --hub-implementation exp-fdb `
  --replace-regular-hub `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-mainnet-exp-fdb
```

The replacement plan should use the regular service name with the experimental
Dockerfile:

```text
name:                main-computer-mainnet-hub
dockerfile_location: /Dockerfile.hub.exp-fdb
```

Apply the replacement:

```powershell
python .\tools\coolify_hub_service.py apply mainnet `
  --hub-implementation exp-fdb `
  --replace-regular-hub `
  --coolify-url $CoolifyUrl `
  --coolify-project-name $ProjectName `
  --coolify-environment-name $EnvironmentName `
  --coolify-server-name $ServerName `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/fdb/fdb.cluster `
  --fdb-namespace main-computer-mainnet-exp-fdb
```

The experimental Hub advertises the selected hosted network through
`/api/hub/status`, so a replacement mainnet service reports the mainnet network
identity even though the storage backend is FoundationDB.

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

For the regular Hub, the status should report the selected network and chain ID.

For the experimental Hub side-by-side or replacement path, also confirm:

```text
the Coolify application uses /Dockerfile.hub.exp-fdb
the start command contains --cluster-file and --namespace
the start command contains --no-fdb-autostart
the mounted FDB cluster file exists inside the container
the FDB namespace is unique for the environment being tested
```

If the experimental Hub cannot read the cluster file or cannot load the native
FDB client, it should be treated as a deployment/runtime failure rather than as a
chain or Coolify routing problem.
