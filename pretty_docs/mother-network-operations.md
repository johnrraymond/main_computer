# Mother network operations — current implementation status

Status: operational map based on the October 17, 2026 code snapshot and the operator's Mother generation-23 registration output. This page describes **implemented and observed** pathways, not permission to bypass deployment gates. Recheck live Mother state before acting; the generation number may advance.

## Authority and network inventory

The authoritative network identity is the verified Mother state at `runtime/state/mother/identity.private.yaml`, with its metadata, generation binding, and recovery manifest. Never use the retired `runtime/state/main_computer.private.yaml`, `runtime/state/all_father.private.yaml`, or a manually edited Mother YAML as deployment authority. Never print API tokens or private keys.

Current operator observation:

| Network | Chain ID | Controller(s) | Deployment state |
| --- | ---: | --- | --- |
| mainnet | 42424240 | coolify-a, coolify-c | Existing running infrastructure; do not reset to create testnet |
| testnet | 42424241 | coolify-b | Identities registered and verified in Mother generation 23; **Besu not deployed** |

The testnet target is `testnetb-super1`, with one reserved QBFT validator/RPC identity on Coolify B. The registered project UUID is `vf1n3kzj0l46ls0fizmjpijj` and the server UUID is `i52ohskcdas3jf21davzaedk`. No Coolify service is created by identity registration.

## Read-only operator checks

From the repository root, using the active Python environment:

```powershell
python -m tools.mother_coolify controllers
python -m tools.mother_deploy plan --network testnet
```

The `controllers` command enumerates Mother-bound controllers without exposing tokens. The generic `plan` command can emit `MOTHER_DEPLOY_MUTATION_AUTHORITY_DISABLED` and `MOTHER_DEPLOY_EXECUTOR_NOT_IMPLEMENTED` for testnet. Those are **real blockers for that planner**, not proof that every Mother executor is absent. Other Mother deployment modules exist; don't infer an executable testnet procedure from them.

The single-node initial-bootstrap command exposed by the inspected `tools/mother_deploy.py` is explicitly restricted to `mainnet`. Do not run that command for testnet, and do not resurrect the deleted `coolify_qbft_network.py` or the old Coolify cluster helpers.

## Testnet registration and precheck

The implemented `python -m tools.mother_identity register-testnet` operation accepts explicit Coolify URL, public IP, VPN IP, hostname, project UUID, and server UUID. Its authenticated precheck uses read-only Coolify API GET requests; it verifies project/server identity but **does not necessarily verify the host's VPN interface or OS hostname**. Its read-only preview does not reserve keys. An authorized `--write` reserves network identities as a verified Mother successor, guarded by the current state binding. An existing testnet registration must not be overwritten by repeating `--write`.

The current testnet registration has **already been completed**; do not rerun it to deploy Besu. `--allow-insecure-http-api` explicitly permits transmitting the Coolify API token over unencrypted HTTP to the configured controller. Prefer a protected transport when available; never publish the token.

On the Coolify B host, confirm the proposed host coordinates independently:

```bash
hostname
ip -o -4 addr show
```

The registered `10.124.0.3` VPN address and `coolify-b` hostname remain **unattested in the reported API precheck**. Don't treat a syntactically valid IP as a discovered interface address.

## Deployment boundary — chain, FoundationDB, Hub

- **Chain:** Mother owns validator/QBFT identity and network lifecycle. The requested next deployment is a *single Besu validator with RPC on Coolify B* using Mother-owned release/execution safeguards, but that testnet execution pathway is not yet proven or authorized by the reported planner. No chain deployment may be claimed complete without observed RPC chain ID `42424241`, validator identity, block production, and accepted evidence.
- **FoundationDB:** `fdb_mutate_harness.py` and `tools/fdb_control/` own FDB mutation; do not substitute the removed `tools/coolify_fdb_cluster.py`.
- **Hub:** `hub_mutate_harness.py` and `tools/hub_control/` own hosted Hub mutation; do not substitute removed `tools/coolify_hub_cluster.py` or `tools/coolify_cluster.py`. `tools/coolify_hub_service.py` is still present for older/local tooling, but its existence is not authority for hosted lifecycle mutation.

**Documentation authority:** `mother.md` now permits chain-first network birth and scopes integrated super-node stages to their legacy profile. This documentation revision does **not** implement or authorize chain-only execution. The testnet Besu release and verification boundary must still be implemented and accepted before deployment; neither an older combined-service Compose generator nor generic `plan` output is an executable testnet release.

## Contract publication and evidence

Contract deployment remains a separate explicit command: `python tools/mainnet-operator.py deploy-contracts ...`; its `prepare-keys` command has been retired. Only approved, funded identities may deploy, and the chain ID must be verified. Public addresses belong in `runtime/deployments/<network>/` and the network-scoped application configuration, **never** copied back into private identity state. For a chain reset and contract redeploy, see `pretty_docs/mainnet-chain-redeploy-runbook.md`, which requires a pre-existing verified chain and does not create Besu services.

Mother's verified generation history is not to be rewritten to erase historical references. Allfather may remain in historical evidence, but must not be active infrastructure authority, a runtime fallback, or a deployer. This document supersedes the retired `hub-coolify-deploy-runbook.md` and the old Hub-chain future-topology planning note for **current operational guidance**.

## Architecture specification location

The current architectural document families live under `pretty_docs/`: `mother*.md`, `fdb*.md`, `hub*.md`, `hub-chain*.md`, and `hub-fdb*.md`. The root copies are retired; do not consult root-level stale duplicates. The PowerShell exporter includes these through `pretty_docs` rather than a separate root file inventory.
