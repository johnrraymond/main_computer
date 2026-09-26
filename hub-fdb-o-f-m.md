# Hub-FDB rectification module specification

Status: module companion to `hub-fdb-o-f.md`

Sources reviewed:

```text
hub-fdb.md SHA-256: 7dc80ff74aaff04ef3e955799e9e326242ac48a56d1f562212bcd924f48f6a09
hub-fdb-o.md SHA-256: 07264e046906b35819a843aeb994a2ec04515b2eab6f2d897608799a7ec91a72
hub-fdb-o-f.md SHA-256: 33710051dc7ce20ddd95e2f7037410339a83b0fa7d8da4b5226b17e9ee06c1d8
```

## Target implementation

```text
hub_fdb_rectify_harness.py

tools/hub_control/
    rectify_fdb.py

    common/
        fdb_contract.py
        state.py
        runtime_projection.py
        deployment.py
        observer.py
        verification.py
        operation_store.py
```

## Ownership

`hub_fdb_rectify_harness.py` owns only the public `inspect`/`rectify` operator flow and harness evidence.

`fdb_contract.py` is the sole Hub-side reader/validator for the FDB consumer contract. In the first-birth migration slice it may materialize a missing v1 artifact from canonical non-empty FDB accepted state; after publication, the artifact is the consumed boundary object. This bootstrap does not authorize FDB mutation.

`rectify_fdb.py` composes the rectification protocol and cannot call Hub membership add/remove operations.

`runtime_projection.py` derives only FDB-facing Hub runtime values from the frozen contract.

`deployment.py` applies exact projection changes through the neutral Hub deployment adapter.

`observer.py` and `verification.py` prove adoption/use independently of the deployment API result.

`state.py` records adoption evidence/reference on the Hub side but cannot rewrite FDB authority.

## Contract path

Target authoritative input:

```text
runtime/state/fdb/<network>/consumer-contract.json
```

FDB Control is responsible for publishing it. The rectifier is blocked until that artifact exists and verifies.

## Adoption evidence

Hub-side evidence should record at minimum:

```text
fdb_contract_generation
fdb_contract_sha256
verified_at
verification_reason/proof digest
```

The complete FDB contract does not need to be duplicated into accepted Hub topology.

## Legacy migration

Legacy `cluster_file_path`, namespace, and related values currently found in mixed Hub topology/private state are migration inputs only. The rectifier must not treat them as canonical once the consumer contract exists.

## Code dependency rule

The Hub-FDB rectifier consumes FDB output artifacts. It must not import FDB lifecycle modules or invoke FDB mutation commands.
