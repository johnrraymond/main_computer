# Hub-FDB rectification architecture

Status: architectural baseline for the FDB dependency edge consumed by Hubs.

## Purpose

FDB Control owns FDB truth. Hub Control owns Hub membership. Hub-FDB rectification owns only the adoption edge between them.

```text
FDB Control -> FDB consumer contract -> Hub-FDB rectifier -> accepted Hubs
```

The rectifier may update FDB-facing Hub runtime projection. It may not add/remove Hubs or mutate FDB topology.

## First-birth publication bridge

The final authority remains FDB Control. During the first Hub-Control birth slice, repositories that predate the durable artifact may not yet have `consumer-contract.json` even though they have verified FDB accepted state. To make the first Hub birth executable without a new operator ceremony, the Hub-side contract adapter may deterministically materialize the v1 consumer-contract artifact from canonical FDB accepted state when it is missing. This is a migration/bootstrap bridge, not a transfer of FDB authority. Future FDB mutations should publish the same artifact directly.

The bootstrap is blocked when FDB accepted topology is empty or malformed.

## Canonical contract

FDB must publish one durable consumer contract per network, targeted at:

```text
runtime/state/fdb/<network>/consumer-contract.json
```

The contract contains only Hub-consumable truth, such as:

```text
schema/version
network
FDB cluster identity
usable connection/cluster-file contents
namespace
transport/TLS requirements when applicable
compatibility requirements
contract generation
content hash
```

FDB topology and the FDB consumer contract are different objects. A topology change that does not change what Hubs consume need not force Hub rectification.

## Per-Hub adoption state

For each accepted Hub, the rectifier classifies FDB adoption as:

```text
current
stale
unverifiable
blocked
```

`current` requires both contract identity match and successful FDB-use proof. A matching stored hash without usable FDB is insufficient.

## Safety

The rectifier fails before mutation if the canonical FDB consumer contract is absent, malformed, unverifiable, or incompatible with the Hub runtime.

Partial fleet success is recoverable: already-current Hubs stay current and a retry continues only where required.
