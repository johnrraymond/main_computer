# Hub-FDB rectifier operator surface

Status: operator companion to `hub-fdb.md`

Source reviewed: `hub-fdb.md` SHA-256
`7dc80ff74aaff04ef3e955799e9e326242ac48a56d1f562212bcd924f48f6a09`

## Public commands

```powershell
python .\hub_fdb_rectify_harness.py inspect
python .\hub_fdb_rectify_harness.py rectify
```

`--network` may be added with `mainnet` as the default when implemented. No Hub membership arguments are part of the initial surface.

## `inspect`

Read-only. It reports:

```text
canonical FDB consumer contract generation/hash
accepted Hubs
per-Hub adopted FDB contract generation/hash
current/stale/unverifiable/blocked classification
actual FDB-use verification
```

## `rectify`

Meaning:

> Bring accepted Hubs that require FDB rectification onto the currently accepted FDB consumer contract without changing Hub membership or FDB authority.

The rectifier chooses the stale Hubs; the operator does not manually supply cluster files, namespaces, coordinator endpoints, operation IDs, or Coolify UUIDs.

## First-birth bootstrap behavior

`add-hub` may bootstrap a missing FDB consumer-contract artifact from verified canonical FDB accepted state. The operator still supplies no cluster file, coordinator, namespace, or FDB generation. If FDB is accepted-empty, Hub birth is blocked.

## Safety boundary

Rectification must stop before live mutation when the canonical FDB contract cannot be validated. It must never create or delete Hub membership and must never invoke FDB lifecycle mutation.

## Retry behavior

A rerun re-inspects adoption. Hubs already proven current are not blindly rewritten. Failed/unverified Hubs remain work to complete.

## Initial YAGNI boundary

Do not initially expose:

```text
--hub
--contract-generation
--contract-file
--cluster-file
--namespace
--coordinators
--force-all
```

Add targeting only when a demonstrated operational need exists.
