# Captain and Data network selection

Captain and Data default to **mainnet**. Select the separate testnet Hub, chain, deployment state and officer identity with `--network testnet`. The supported command-network keys are `mainnet` and `testnet`.

From the Main Computer project root:

```text
python -m main_computer.cli captain "Make it so" --network testnet --prepare-only
python -m main_computer.cli data "Make it so" --network testnet --prepare-only
python -m main_computer.cli data "What is 4 + 9?" --god-mode --agent --network testnet
python -m main_computer.cli data engage computer --agent --network testnet
python -m main_computer.cli captain smoke john luc picard engage computer --network testnet
```

The network flag also works **before** the free-text prompt: `python -m main_computer.cli data --network testnet --god-mode --agent "What is 4 + 9?"`.

Without the flag, mainnet remains the default. To select it explicitly use `--network mainnet`.

The selected network determines:

- Hub URL from `main_computer/config/hub_networks.json` (`https://testnet-hub.greatlibrary.io` for testnet).
- Captain RPC, chain ID, deployment manifest, and Mother-verified officer signing authority. The testnet chain ID is `42424241`.
- Data/O3 wallet-account lookup from `runtime/deployments/<network>/latest.json` and the corresponding network's bridge prefunding authority.
- Captain and Data worker-pull registration destination.

Testnet does **not** inherit mainnet's `MAIN_COMPUTER_DATA_O3_WALLET_ADDRESS`, `MAIN_COMPUTER_O3_WALLET_ADDRESS`, or `MAIN_COMPUTER_DATA_O3_HUB_ACCOUNT_ID`. If an operator needs an explicit testnet override, use `MAIN_COMPUTER_TESTNET_DATA_O3_WALLET_ADDRESS` (or `MAIN_COMPUTER_TESTNET_O3_WALLET_ADDRESS`) and `MAIN_COMPUTER_TESTNET_DATA_O3_HUB_ACCOUNT_ID`. Prefer the verified network deployment's O3 address; bridge prefunding still checks it against Mother's independently verified officer authority.

Missing selected-network registry data does not trigger a fallback to the other network. Explicit Hub URL and wallet/account overrides remain available for intentional operator overrides; only use endpoints and addresses belonging to the selected network.

The Captain and Data prompts can carry bridge deposits, spend Hub credits, and invoke workers. Use `--prepare-only` for nonexecuting Captain/Data shortcut checks. Data god-mode live Hub agent execution is not a dry run. Tests do not prove that live testnet bridge signing and funding are currently available.
