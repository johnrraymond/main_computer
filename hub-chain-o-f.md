# Hub-Chain rectification functionality specification

Status: functional companion to `hub-chain-o.md`

Sources:

```text
hub-chain.md SHA-256: 17c8a602b1d17156d8ed1544b430be8c03251a17093932bd26e2ff938d9d24e1
hub-chain-o.md SHA-256: 2a6f35dd6e2b4a81e66c32a95998c97a2214cc9cb308b87a8241a36ff6c53652
```

## Functional chain

### HUB-CHAIN-OF-CON-001 — Load canonical Chain consumer contract

Read, schema-validate, integrity-check, and compatibility-check the Chain consumer contract. During the first-birth migration slice, materialize v1 from canonical chain identity/RPC and deployment-owned `runtime/deployments/<network>/latest.json` when present. Validate the manifest network and chain ID, extract deployed contract addresses as advertised feature identities, record source provenance, and preserve an explicit `required_contract_keys` list.

For the current Hub lifecycle contract, `required_contract_keys` is empty. A checked-in `<network>_contracts.json` file is only a no-manifest bootstrap fallback and may not override deployment-owned identities.

### HUB-CHAIN-OF-HUB-001 — Load accepted Hub membership

Read Hub accepted state without changing membership.

### HUB-CHAIN-OF-OBS-001 — Observe Hub Chain adoption

Determine each Hub's adopted contract generation/hash and independently probe the canonical RPC and expected chain ID. Probe advertised application contracts as evidence without turning them into blockers unless the frozen contract explicitly marks them required.

### HUB-CHAIN-OF-CLS-001 — Classify rectification state

Produce `current`, `stale`, `unverifiable`, or `blocked` for every accepted Hub.

### HUB-CHAIN-OF-PLAN-001 — Freeze rectification target

Freeze the exact Chain contract and exact Hubs requiring mutation.

### HUB-CHAIN-OF-RUN-001 — Render Chain-facing Hub projection

Derive RPC, chain ID, advertised contract identities, compatibility settings, and any required runtime values strictly from the frozen contract.

### HUB-CHAIN-OF-DEP-001 — Apply Hub runtime update

Update only Chain-facing Hub runtime projection. Hub membership remains unchanged.

### HUB-CHAIN-OF-VER-001 — Prove Chain contract adoption

At minimum prove:

```text
RPC reachable
reported chain ID equals frozen contract
any explicitly required application contracts validate
advertised contracts are classified as verified, missing-code, or unverifiable
Hub reports/records frozen contract generation/hash
```

For the current v1 core Hub contract, the application-contract requirement set is empty. Missing `hub_credit_bridge_escrow` code therefore cannot by itself fail Hub birth.

Stronger feature-level proofs may be added later without changing lifecycle authority.

### HUB-CHAIN-OF-STATE-001 — Record adoption evidence

Record generation/hash/proof evidence without copying Chain authority into Hub accepted topology.

## `inspect` pipeline

```text
CON-001 -> HUB-001 -> OBS-001 -> CLS-001
```

## `rectify` pipeline

```text
CON-001 -> HUB-001 -> OBS-001 -> CLS-001
-> PLAN-001 -> RUN-001 -> DEP-001 -> VER-001 -> STATE-001
```

## Forbidden effects

No functionality may:

- add/remove Hubs;
- alter validators or RPC membership;
- deploy or upgrade chain contracts;
- mutate Mother accepted authority;
- infer Chain truth from stale Hub configuration;
- treat a missing advertised feature contract as permission to change chain state.
