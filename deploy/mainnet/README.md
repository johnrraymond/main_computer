# Mainnet chain and Hub deployment handoff

This handoff ties the committed mainnet chain seed, contract deployment, and
hosted Hub deploy into one operator sequence. Run all commands from the
repository root.

The sequence is intentionally fail-closed:

1. plan the mainnet QBFT chain with `--allow-mainnet`
2. apply the Coolify chain service
3. wait for the mainnet RPC endpoint
4. deploy contracts with an explicit deployer key environment variable and
   explicit non-Anvil office addresses
5. deploy the mainnet Hub against `runtime/deployments/mainnet/latest.json`

## Required operator inputs

Set these in the operator shell. Keep tokens and private keys out of command
history and committed files.

```powershell
$MainnetSsh = "root@<MAINNET_MACHINE_IP>"
$MainnetAddress = "<MAINNET_MACHINE_IP>"
$ChainCoolifyUrl = "https://<mainnet-chain-coolify>"
$HubCoolifyUrl = "https://<mainnet-hub-coolify>"
$GitRepo = "https://github.com/<owner>/<repo>.git"

$env:MAIN_COMPUTER_MAINNET_CHAIN_COOLIFY_TOKEN = "<chain-coolify-api-token>"
$env:MAIN_COMPUTER_MAINNET_HUB_COOLIFY_TOKEN = "<hub-coolify-api-token>"
$env:MAIN_COMPUTER_MAINNET_DEPLOYER_PRIVATE_KEY = "<0x32-byte-deployer-key>"

$MainnetOffices = "0x1111111111111111111111111111111111111111,0x2222222222222222222222222222222222222222,0x3333333333333333333333333333333333333333,0x4444444444444444444444444444444444444444"
```

Replace the four office addresses with the actual Captain, First Officer, Second
Officer, and Third Officer addresses. The mainnet contract deploy path rejects
default Anvil office addresses.

## 1. Render the mainnet chain plan

```powershell
python .\tools\coolify_qbft_network.py plan mainnet `
  --allow-mainnet `
  --single-host $MainnetSsh `
  --target-address $MainnetAddress `
  --coolify-url $ChainCoolifyUrl `
  --public-rpc
```

## 2. Dry-run the chain apply

```powershell
python .\tools\coolify_qbft_network.py apply mainnet `
  --allow-mainnet `
  --single-host $MainnetSsh `
  --target-address $MainnetAddress `
  --coolify-url $ChainCoolifyUrl `
  --coolify-token-env MAIN_COMPUTER_MAINNET_CHAIN_COOLIFY_TOKEN `
  --public-rpc `
  --dry-run
```

Remove `--dry-run` only after the rendered Compose and host ports match the
mainnet host.

## 3. Deploy the mainnet contracts

Dry-run first:

```powershell
python .\tools\coolify_qbft_network.py deploy-contracts mainnet `
  --allow-mainnet `
  --single-host $MainnetSsh `
  --target-address $MainnetAddress `
  --public-rpc `
  --deployment-private-key-env MAIN_COMPUTER_MAINNET_DEPLOYER_PRIVATE_KEY `
  --deployment-offices $MainnetOffices `
  --deployment-environment mainnet `
  --deployment-output-dir runtime\deployments `
  --dry-run
```

Remove `--dry-run` only after the command includes:

```text
--environment mainnet
--chain-id 42424240
--private-key-env MAIN_COMPUTER_MAINNET_DEPLOYER_PRIVATE_KEY
--offices <four explicit non-Anvil addresses>
```

A successful deploy writes:

```text
runtime/deployments/mainnet/latest.json
```

## 4. Deploy the mainnet Hub

The single-Hub service path remains the canonical first mainnet Hub deployment:

```powershell
python .\tools\coolify_hub_service.py apply mainnet `
  --hub-implementation exp-fdb `
  --coolify-url $HubCoolifyUrl `
  --coolify-token-env MAIN_COMPUTER_MAINNET_HUB_COOLIFY_TOKEN `
  --coolify-project-name "Main Computer" `
  --coolify-environment-name "mainnet-hub" `
  --coolify-server-name "mainnet-a" `
  --git-repo $GitRepo `
  --fdb-cluster-file /data/main-computer/hub/mainnet-exp-fdb/fdb.cluster `
  --fdb-namespace main-computer-mainnet-exp-fdb-stable-live-sessions `
  --force-deploy
```

The single-Hub service should report the mainnet network key and chain ID at:

```text
https://mainnet-hub.greatlibrary.io/api/hub/status
```

## Optional Hub topology placement

The committed placement files are ready for the cluster-style Hub deployer when
you want the Hub layer represented through `tools/coolify_hub_cluster.py`. In
that mode, verify the concrete host first and then the public entry alias:

```text
https://mainnet-hub1.greatlibrary.io/api/hub/status
https://mainnet-hub.greatlibrary.io/api/hub/status
```

Render the cluster-style plan with:

```powershell
python .\tools\coolify_hub_cluster.py plan `
  --placement deploy\hub-topology\mainnet-coolify-deployment.json `
  --set-coolify-url "mainnet-a:$HubCoolifyUrl" `
  --coolify-token-env MAIN_COMPUTER_MAINNET_HUB_COOLIFY_TOKEN `
  --coolify-project-name "Main Computer" `
  --coolify-environment-name "mainnet-hubs" `
  --git-repo $GitRepo
```

Treat the placement file as topology/configuration, not as a secret store.
