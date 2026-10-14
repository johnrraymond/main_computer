# Stable / Experimental Hub crash lab

**LAB-ONLY.** The crash lab operates on an existing local FoundationDB cluster and deliberately kills isolated Hub processes. It never resets FoundationDB, never touches the production Hub, and never transacts on a blockchain. Every trial receives an isolated namespace, isolated loopback ports, and a distinct generated smoke topology.

The lab checks the Stable source fingerprint before and after a run and runs the existing Stable baseline test modules before any Hub starts. The `fault_runner` instruments ledger and Stable FDB transaction boundaries only in a crash-lab subprocess, without changing either Hub's runtime code.

## Preflight baseline

```powershell
python -m tools.hub_crash_lab.run_lab --mode baseline --hub stable
```

## Combined post-commit restart campaign

```powershell
python -m tools.hub_crash_lab.run_lab `
  --mode restart --hub both --rounds 10 `
  --cluster-file .\.foundationdb\docker.cluster
```

This is the existing 3-node Stable / 3-port Experimental completed-session durability test, now invoked for both lanes. It kills processes **after** the client workload completes and compares preserved session, hold, charge, and earning records. It does **not** inject mid-transaction failures.

## Deterministic in-flight post-commit crash tests

First try a single round in each lane:

```powershell
python -m tools.hub_crash_lab.run_lab `
  --mode inflight --hub both --fault both --rounds 1 `
  --cluster-file .\.foundationdb\docker.cluster
```

Then increase rounds on a green installation:

```powershell
python -m tools.hub_crash_lab.run_lab `
  --mode inflight --hub both --fault both --rounds 10 `
  --cluster-file .\.foundationdb\docker.cluster
```

`--fault reserve` wraps `StableHubPayoutLedgerDirectory.create_hold` and `ExperimentalFoundationDbCreditLedger.create_hold_credit_wei`; `--fault charge` wraps `StableHubPayoutLedgerDirectory.spend_request_credit` and `ExperimentalFoundationDbCreditLedger.charge_hold_credit_wei`.

Each wrapper calls the real ledger method first. In Experimental, the ledger method owns its FDB transaction and the crash marker is written after that transaction returns. In Stable, the accepted-work handler wraps reservation **and accepted-session creation in an outer FDB transaction**. Stable's ledger hook therefore **stages** its record identity in the current transaction attempt and writes the crash marker only after the outermost `FoundationDbStableWorkerSessionStore.atomic()` has returned after commit. If the outer transaction fails or is retried, no marker is emitted from the abandoned attempt. The marker is flushed to disk and the Hub blocks **before the handler receives the final result**. The external controller confirms which lab-owned process emitted the marker, force-kills it (including the Windows virtual-env interpreter child), restarts the target Hub without injection, reads FDB-backed records, checks that the exact hold or charge is present once, and confirms the record remains stable after a second restart. Charges also require the associated worker earning to survive. The test workload is a separate subprocess so a blocked request cannot hang the controller.

A PASS verifies *after-FDB-commit/before-handler-response* durability. It does **not** establish a pre-commit atomicity test, automatic end-to-end client retry, unfinished Temporal workflow recovery, claim/batch recovery, or on-chain payout reconciliation. Those are subsequent fault boundaries. No code should report those as proven based on this lab alone.

## Reports and safety

`runtime/hub-crash-lab/last-run.json` contains per-lane results. Event logs, marker evidence, client logs, source hashes, and isolated topologies are saved under `runtime/hub-crash-lab/<run_id>/`. The runner refuses occupied ports, nonexistent FDB cluster files, non-smoke topologies, non-loopback chain RPCs, and Stable baseline regressions. It never repairs ledger records, changes Stable runtime files, or auto-starts FDB.

Run the unit tests separately:

```powershell
python -m pytest -q tests/test_hub_crash_lab.py
```

## Expanded recovery gates (Stable and Experimental)

The following modes were added **without modifying Stable or Experimental Hub runtime source**. The crash hook is installed only in lab-launched child interpreters; it never loads into normal production Hubs. Every trial uses a new FDB namespace, loopback-only smoke topology, lab-owned child processes and zero blockchain transactions. The Stable baseline suite and source-hash check remain mandatory.

### Gate A — pre-commit rollback

```powershell
python -m tools.hub_crash_lab.run_lab `
  --mode precommit --hub both --fault both --rounds 1 `
  --cluster-file .\.foundationdb\docker.cluster
```

A lab wrapper traps a staged FDB hold or charge **while the transaction callback is still active**, writes a durable marker and blocks. The controller terminates the owning Hub, restarts it, and checks that **zero committed holds/charges and zero worker earnings** exist for the interrupted request. It restarts again and repeats the check. Stable instruments its outer FDB store *save inside the transaction* (not the nested ledger method return). Experimental instruments its ledger `_write_dict` while the FDB transaction is still active. This is rollback after a real process kill, not an exception test. It does not assert anything about the intended client's eventual automatic resubmission.

### Gate B — claim/batch commit and idempotent recovery

```powershell
python -m tools.hub_crash_lab.run_lab `
  --mode settlement --hub both --fault both --rounds 1 `
  --cluster-file .\.foundationdb\docker.cluster
```

The workload creates real worker earnings, invokes a claim or settlement batch through the Hub's existing HTTP API, and the lab hook blocks after the FDB commit but before the response. After killing/restarting the Hub, the controller verifies the exact persisted claim or batch, retries the **same** earning/claim set with the **original idempotency key** via the isolated FDB ledger and requires the identical obligation ID and unchanged original record. It restarts a second time and reads again. Neither simulated nor real blockchain settlement is executed in this gate.

### Gate C — owner process dies; another Hub continues

```powershell
python -m tools.hub_crash_lab.run_lab `
  --mode failover --hub both --fault charge --rounds 1 `
  --cluster-file .\.foundationdb\docker.cluster
```

Three Stable processes and **three independently killable Experimental processes** share their lane's isolated FDB namespace. After the charge commits, the owning process is killed. While it remains **dead**, a surviving Hub must serve the exact committed charge through its HTTP API, and the controller checks FDB preservation. Only then is the owner restarted and the records rechecked twice. `--fault reserve` is also supported, but Experimental's legacy hold-list API is disabled, so the peer's health plus direct FDB durability check is performed and this limitation is explicit in the report. This gate demonstrates surviving-Hub availability and state visibility; it does *not* demonstrate resumption of an unfinished worker execution by a different Hub.

### Result classification and troubleshooting

A PASS in these modes requires a lab-owned marker, verified process termination, relevant persisted-record checks (including the absence of the interrupted operation in precommit mode), and post-restart consistency. For claim/batch, it additionally requires an idempotent replay of the original obligation. `runtime/hub-crash-lab/last-run.json` contains the top-level result; per-trial markers, `events.json`, `client.log` and `hub-*-generation-*.log` are in a separate randomly named run directory. On failure, the runner stops the affected trial and prints its lane, fault, and round, preserving evidence.

The new tests do **not** establish transactional blockchain payout recovery, cross-Hub worker-execution takeover, full Temporal in-flight replay, or repair of legacy financial state. Those remain separate acceptance gates.

### Stable owner-failover charge driver

`--mode failover --hub stable --fault charge` uses a dedicated one-result
paid-work driver, rather than the integrated market/reconnect/payout stress
workload. It authenticates a worker and requester using real multisession keys,
accepts exactly one work offer, verifies the FDB-backed hold is visible via
continuation, and submits one terminal result. It does **not** read the result
acknowledgment, retry the result, or continue into duplicate-terminal and
reconnect testing: the fault runner is expected to pause the charge after its
outermost FDB transaction commits but before that acknowledgment. The external
controller then kills that Hub and verifies the charge through a surviving
Hub before restarting the owner.

The driver writes `client-result.json` at phase
`first_result_sent_awaiting_charge_crash` for diagnostics. It does not claim
a pass from that file: the external controller still requires a valid real
commit-boundary crash marker and correct FDB and survivor HTTP evidence. The
normal Stable integrated stress workload is unchanged and is still exercised
by `--mode restart` and the Stable regression suite. No Hub runtime files
are altered by this lab-only patch.
