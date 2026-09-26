# Hub operator surface

Status: operator-facing companion to `hub.md`

Source reviewed: `hub.md` SHA-256
`dc1def412907396cc63b5456a284d298284c3176e7559a80a70c2724ce8a6be7`

## 1. Purpose and authority

The repository-level `hub_mutate_harness.py` is the normal Hub lifecycle operator surface. Operators express lifecycle intent and logical Hub identity. The harness owns internal stages, operation IDs, run directories, evidence files, deployment identifiers, dependency-contract selection, and resume behavior.

If this document conflicts with `hub.md`, `hub.md` governs.

## 2. Public operations

Exactly three lifecycle intents are exposed initially:

```text
inspect
add-hub
remove-hub
```

The complete public calling convention is:

```text
hub_mutate_harness.py
    {inspect,add-hub,remove-hub}
    [--network <network>]
    [--hub <hub-id>]
    [--allow-full-deletion]
    [--execute-mutations]
    [--yes-i-know-this-mutates-hub]
```

`--network` defaults to `mainnet`.

## 3. Inspect

```powershell
python .\hub_mutate_harness.py inspect
```

or explicitly:

```powershell
python .\hub_mutate_harness.py inspect --network mainnet
```

`inspect` is read-only and takes no `--hub` argument. It reports accepted generation, accepted Hub membership, placement, observed deployment status when available, FDB contract adoption, Chain contract adoption, and verification state.

Mutation authorization flags are invalid with `inspect`.

## 4. Add Hub

Preparation:

```powershell
python .\hub_mutate_harness.py add-hub --hub mainneta-hub1
```

Authorized execution:

```powershell
python .\hub_mutate_harness.py add-hub `
  --hub mainneta-hub1 `
  --execute-mutations `
  --yes-i-know-this-mutates-hub
```

The same command covers ordinary expansion and `0 -> 1` rebirth. There is no rebirth flag. For a network with no Hub accepted state, `inspect` reports `unborn`, generation `0`, and zero Hubs; that is a valid prestate for `add-hub`.

The harness infers placement, controller, Coolify context, current FDB consumer contract, current Chain consumer contract, runtime projection, operation ID, and evidence location.

### First-Hub birth proof

For `unborn -> 1`, `prep` must show the inferred controller/host plus the frozen FDB and Chain contract generation/hash. `do` must not report success merely because Coolify accepted a deployment request. It must prove the public Hub identity, an FDB-backed Hub status read, and exact chain ID/RPC projection before finalization.

The accepted state advances `0 -> 1` only after those proofs.

## 5. Remove Hub

Preparation:

```powershell
python .\hub_mutate_harness.py remove-hub --hub mainnetc-hub1
```

Authorized execution:

```powershell
python .\hub_mutate_harness.py remove-hub `
  --hub mainnetc-hub1 `
  --execute-mutations `
  --yes-i-know-this-mutates-hub
```

Ordinary contraction may proceed down to one accepted Hub.

## 6. Final Hub deletion

The final `1 -> 0` transition requires:

```powershell
python .\hub_mutate_harness.py remove-hub `
  --hub mainneta-hub1 `
  --allow-full-deletion
```

and, after preparation:

```powershell
python .\hub_mutate_harness.py remove-hub `
  --hub mainneta-hub1 `
  --allow-full-deletion `
  --execute-mutations `
  --yes-i-know-this-mutates-hub
```

`--allow-full-deletion` acknowledges the target cardinality of zero. It does not replace mutation authorization.

## 7. Mutation authorization

These flags are paired:

```text
--execute-mutations
--yes-i-know-this-mutates-hub
```

Either one alone is an error.

Without them, a mutation command may inspect and prepare, but it must stop before the first live deployment mutation.

## 8. Automatic resume

There is no public `--resume`.

A second invocation of the same high-level command automatically selects the newest unfinished matching harness run. Matching includes:

```text
operation
network
Hub identity
allow-full-deletion authority
```

A destructive-authority run must not match a non-destructive run.

Completed runs are not resumed.

## 9. Hidden implementation details

The public operator surface must not require or advertise:

```text
--resume
--run-dir
--operation-id
--host
--controller
--coolify-url
--project-uuid
--environment-uuid
--server-uuid
--deployment-id
--fdb-contract
--chain-contract
--rpc-url
--cluster-file
--namespace
```

Internal `prep`, `do`, `operation-inspect`, and `finalize` stages are harness-owned.

## 10. Dependency contracts at add time

`add-hub` freezes the currently accepted FDB and Chain consumer contracts during `prep`. Chain preflight blocks only on RPC/chain-ID failure or a Hub-required contract failure; stale optional/advertised contract addresses are reported but do not block birth. The new Hub must adopt and prove both frozen dependency contracts before finalization.

Existing stale Hubs are not silently rectified by Hub lifecycle operations. That is the job of the dependency rectifiers.

## 11. Console visibility

Each successful stage should emit a compact operator-facing summary. Raw JSON/stdout/stderr remain evidence files.

A successful add is expected to look approximately like:

```text
=== pre-inspect ===
status:               accepted
accepted generation:  4
hubs:                 2
verification:         verified

=== prep ===

=== resolved Hub mutation ===
operation:            add-hub
network:              mainnet
hub:                  mainnetc-hub1
logical controller:   coolify-c
resolved host:        coolify-c
accepted generation:  4
target Hub count:     3
FDB contract:         generation 8
Chain contract:       generation 12
Chain preflight:
  RPC:                 verified
  chain ID:            42424240
  required contracts:  verified
  optional stale:      alpha-beta-lockout
FDB preflight:         contract verified

=== do ===
status:               deployed
deployment action:    created
Hub running:          verified
FDB adoption:         verified
Chain adoption:       verified

=== operation-inspect ===
mutation proof:       verified

=== finalize ===
status:               finalized
accepted generation:  5
accepted authority:   advanced

=== final-inspect ===
status:               accepted
accepted generation:  5
hubs:                 3
verification:         verified
```

## 12. Current implementation status of this patch

This patch freezes and tests the public lifecycle surface and the internal CLI seam. It intentionally does not delegate mutations to the legacy mixed Hub/FDB deployers. The internal mutation implementation remains an explicit structured gap until accepted Hub state, placement, runtime projection, observer, and dependency-contract modules are implemented behind this surface.
