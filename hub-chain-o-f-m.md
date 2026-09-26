# Hub-Chain rectification module specification

Status: module companion to `hub-chain-o-f.md`

Sources reviewed:

```text
hub-chain.md SHA-256: 17c8a602b1d17156d8ed1544b430be8c03251a17093932bd26e2ff938d9d24e1
hub-chain-o.md SHA-256: 2a6f35dd6e2b4a81e66c32a95998c97a2214cc9cb308b87a8241a36ff6c53652
hub-chain-o-f.md SHA-256: 796430d3a5db3bbc69bf104cade1b57dcd82e91cc9bcf3dc5a1d8efd82a02430
```

## Target implementation

```text
hub_chain_rectify_harness.py

tools/hub_control/
    rectify_chain.py

    common/
        chain_contract.py
        state.py
        runtime_projection.py
        deployment.py
        observer.py
        verification.py
        operation_store.py
```

## Ownership

`hub_chain_rectify_harness.py` owns the public `inspect`/`rectify` flow and harness evidence only.

`chain_contract.py` is the sole Hub-side reader/validator for the Chain consumer contract. During first-birth migration it materializes v1 from canonical network/private chain identity plus deployment-owned `runtime/deployments/<network>/latest.json` when present. A checked-in `<network>_contracts.json` file is only a no-manifest fallback and may not override deployment-owned identities.

The module owns the distinction between core Hub chain requirements and advertised application features. Its current `HUB_REQUIRED_CONTRACT_KEYS` set is empty because Hub lifecycle requires RPC/chain identity, not a live bridge application contract. Advertised contract code is probed and retained as non-blocking evidence. A future runtime requirement must explicitly promote a contract key before missing code can block Hub lifecycle.

`rectify_chain.py` composes the adoption protocol and cannot invoke Hub membership add/remove operations or Chain lifecycle mutation.

`runtime_projection.py` derives only chain-facing Hub runtime values from the frozen contract.

`deployment.py` applies the exact frozen projection through the neutral Hub deployment adapter.

`observer.py` and `verification.py` prove RPC/chain identity and Hub adoption independently of deployment API success. Feature-contract checks remain separate evidence unless explicitly required by the contract.

`state.py` records adoption evidence/reference without becoming a Chain authority copy.

## Contract path

Target authoritative input:

```text
runtime/state/chain/<network>/consumer-contract.json
```

The appropriate Chain/Mother authority publishes it. The rectifier remains blocked until the artifact exists and its core requirements verify.

## Adoption evidence

Hub-side evidence should record at minimum:

```text
chain_contract_generation
chain_contract_sha256
verified_at
RPC/chain-id proof
explicit required-contract proof digest
advertised-contract status evidence
```

## Legacy migration

Legacy RPC URLs and deployed-contract values currently embedded in Hub/network configuration are migration evidence only once the canonical Chain consumer contract exists.

## Code dependency rule

The rectifier consumes Chain output artifacts. It must not import blockchain-service feature requirements or Mother lifecycle authority into Hub lifecycle policy.

## Deployment-contract authority

`common/chain_contract.py` resolves advertised contract identities from `runtime/deployments/<network>/latest.json` first and uses checked-in `<network>_contracts.json` only as a no-manifest bootstrap fallback.
