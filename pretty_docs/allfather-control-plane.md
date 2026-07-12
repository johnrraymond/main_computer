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

The head container exposes the private/operator guard API in the 41400 range and
carries only the peer head list. It does not start Hub, FDB, QBFT, hub_admin, or
contracts.

The normal bootstrap command does not ask the operator to choose a Coolify
project name. When Coolify requires a project lookup, the control plane uses the
default Coolify project name `My first project`.

## Discovery before topology

After the heads answer, query the live control surface:

```powershell
python tools/allfather_control.py discover
```

Discovery calls each head guard and merges `/identity`, `/topology`, and
`/status`. Mainnet/testnet topology must come from live guard responses and
explicit add/remove operations, not from the private seed file.

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
