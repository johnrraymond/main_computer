# Mother deployment command runbook

Status: operator-facing command-contract companion for `tools/mother_deploy.py`.

Scope: this document explains which stable-ish deployment command families the
operator should call, when to call them, what artifact each call must produce,
and which values should be carried forward into the next call. It is a runbook
for operator/assistant sessions. It is not a replacement for the implementation
or for the safety contracts in `mother.md`.

The examples use local PowerShell and the helper used in the live runbooks:

```powershell
Invoke-MotherJson @(
    "<command>",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    ...
)
```

`Invoke-MotherJson` is a wrapper around:

```powershell
python .\tools\mother_deploy.py <command> ...
```

The command names and flag names below are the stable operator surface for this
deployment lane. Artifact hashes, operation IDs, release expiry times, service
UUIDs, and evidence paths are data, not command syntax.

## 1. Ground rules for every script call

1. Run every command from the repository root.
2. Always include `--network` and `--node`.
3. Use one target node per A-side birth/proof run unless a later runbook
   explicitly says otherwise.
4. Verify before execute.
5. Releases are single-use. A claimed, failed, interrupted, or expired release is
   not reused.
6. `--execute` is the live-mutation boundary. Without `--execute`, an `apply-*`
   or `rollback-*` command is an inspection/preflight.
7. Persist artifacts with `--write-*` when the next phase needs a path.
8. Carry forward both path and SHA-256 from every artifact-producing command.
9. If a command exits nonzero, read the matching evidence artifact before issuing
   a new release.
10. Do not force deploy, delete containers, delete volumes, or manually mutate
    Coolify while a Mother `--execute` command is running.

Use stable timeouts for Coolify-bound live runs:

```powershell
"--timeout", "30",
"--max-response-bytes", "4194304",
"--max-wait-seconds", "900",
"--poll-interval-seconds", "5"
```

Use stable release windows for operator-mediated releases:

```powershell
"--max-age-seconds", "900"
"--expires-in-seconds", "900"
```

Shorter defaults may be acceptable in tests. The operator runbook should use the
explicit values above so the assistant can reason about expiry and polling.

## 2. Stable artifact naming in an operator session

Use predictable PowerShell variable names. The assistant should ask for these
names or their displayed values before deciding the next command.

| Artifact | Path variable | SHA variable |
|---|---|---|
| Identity transaction | `$A1IdentityTransactionPath` | `$A1IdentityTransactionSha` |
| Identity release | `$A1IdentityReleasePath` | `$A1IdentityReleaseSha` |
| Identity execution | `$A1IdentityExecutionPath` | `$A1IdentityExecutionSha` |
| Identity rollback result | `$A1IdentityRollbackPath` | `$A1IdentityRollbackSha` |
| Identity rollback evidence | `$A1IdentityRollbackEvidencePath` | `$A1IdentityRollbackEvidenceSha` |
| Genesis transaction | `$A1GenesisTransactionPath` | `$A1GenesisTransactionSha` |
| Genesis release | `$A1GenesisReleasePath` | `$A1GenesisReleaseSha` |
| Genesis execution | `$A1GenesisExecutionPath` | `$A1GenesisExecutionSha` |
| Genesis rollback result | `$A1GenesisRollbackPath` | `$A1GenesisRollbackSha` |
| Genesis rollback evidence | `$A1GenesisRollbackEvidencePath` | `$A1GenesisRollbackEvidenceSha` |
| Genesis reapply execution | `$A1GenesisReapplyExecutionPath` | `$A1GenesisReapplyExecutionSha` |
| Genesis-birth release | `$A1GenesisBirthReleasePath` | `$A1GenesisBirthReleaseSha` |
| Genesis-birth execution/evidence | `$A1GenesisBirthExecutionPath` or evidence path | `$A1GenesisBirthExecutionSha` or evidence SHA |

If a command returns a nested `*.path` and `*.sha256`, assign both immediately.
Then print a compact object with those values and the expiry or observed time.

## 3. Universal stage/verify/release/apply pattern

Most governed deployment operations use this pattern:

```text
stage-*             read state and produce a transaction
verify-*-transaction verify the staged transaction
release-*           create an expiring operator release
verify-*-release    verify the release before execution
apply-*             inspect without --execute
apply-* --execute   claim the release and mutate live state
verify-*-evidence   verify persisted final evidence, when available
rollback-*          inspect/execute rollback while rollback boundary is open
verify-*-rollback   independently verify rollback effect
```

Do not skip the inspection call merely because a release verified. The inspection
call confirms the executor, rollback boundary, blocker list, release-claim state,
and mutation plan without performing the live mutation.


## 4. Golden path test plan and current position

Treat the golden path as a test plan, not as a slogan. Each component below has
an input boundary, a command family, a proof artifact, and a pass condition. Do
not advance merely because a host looks healthy; advance when the component's
evidence says the previous component passed.

### 4.1 Current session checkpoint

This checkpoint records where the live `mainnet` / `mainneta-super1` run was
when this runbook section was written. Replace it with newer evidence when the
run advances.

```text
network = mainnet
node = mainneta-super1
target_service_uuid = lmjwoglwv7ryvrfsbfuu4o7k
superseded_service_uuid = pc20bsxvq3ykjnpzque08l63
superseded_project_state = no stale project leftovers observed
Coolify UI state = Running (healthy)
Besu state = running, healthy, producing blocks
FDB state = running, healthy
Hub state = running, healthy
Guardian state = running, healthy
one-shot init state = exited 0
one-shot cleanup state = exited 0
```

The manual Coolify force deploy proved the corrected Compose health model on the
host. It did not, by itself, complete the Mother genesis-birth proof. The current
golden-path location is therefore:

```text
Current component = G5 genesis-birth execution proof
Completed evidence = G5 release verified clean
Next command = apply-genesis-birth --execute
Expected next evidence = deployment-genesis-birth evidence status pass
Expected next_phase = stage-soft-replica-configuration
```

The fresh release that was verified during this checkpoint was:

```text
release_path = runtime/state/mother/actions/deployment-genesis-birth-releases/20260806T013741Z-d176818faef8e1cf.json
release_sha256 = d176818faef8e1cf53a333411c50db3c4101c2bdfa1c536d4364e8e920f041bb
expires_at = 2026-08-06T01:52:41Z
precleanup_compose_sha = 4f1a58157a282a2378f8dfae2f417878e22075f2b3aff0bb103fd185431005df
proof_compose_sha = 6655b0f781da83c980589662f56e4cb066d74f794ea67a274ae5d86c06b40c40
release_verification.clean = true
remaining_blocker_codes = [MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTOR_NOT_RUN]
```

If that release has expired or was claimed, do not reuse it. Create a new
genesis-birth release, verify it, then execute the new release.

### 4.2 Golden path components

| ID | Component | Objective | Required command families | Pass condition | Evidence to paste back |
|---|---|---|---|---|---|
| G0 | Patch and local repo gate | Apply the intended code/docs patch without archive-root or wrapper errors | `new_patch.py --dry-run`, `new_patch.py` | Dry run and apply exit 0; touched-file list matches scope | Artifact name, SHA-256, dry-run/apply output |
| G1 | Operator/session baseline | Establish stable variables, target node, service UUIDs, and monitor panes | read-only PowerShell variable display; Coolify host monitor | Target UUID and stale UUID are known; monitor shows no unexpected port owner | Variable object, monitor block |
| G2 | Identity install proof | Install reserve identity through governed release/execution flow | `stage-identity`, `verify-identity-transaction`, `release-identity`, `verify-identity-release`, `apply-identity`, rollback/reapply if required | Identity execution evidence clean and path/SHA captured | transaction/release/execution path and SHA values |
| G3 | Genesis apply / rollback / reapply proof | Prove genesis can be applied, rolled back, verified, and reapplied before birth | `stage-genesis`, `verify-genesis-transaction`, `release-genesis`, `verify-genesis-release`, `apply-genesis`, `rollback-genesis`, `verify-genesis-rollback`, reapply | Rollback verification exists; reapply execution path/SHA captured | genesis execution, rollback evidence, reapply execution path/SHA |
| G4 | Genesis-birth release gate | Freeze the exact corrected proof Compose and cleanup plan | `release-genesis-birth`, `verify-genesis-birth-release` | `clean=true`; only remaining blocker is executor not run | release path/SHA, expiry, precleanup/proof compose SHA, verification JSON |
| G5 | Genesis-birth execution proof | Let Mother claim and execute the verified release, then prove the live super-node | `apply-genesis-birth --execute`, `verify-genesis-birth-evidence` | `status=pass`; Coolify service is `running:healthy`; blocks advance; Hub/FDB/guardian proofs pass | raw output summary, evidence path, final summary fields |
| G6 | Soft-replica configuration | Start the post-birth replica lane only after birth evidence names it as next phase | `stage-soft-replica`, `verify-soft-replica-transaction`, `release-soft-replica`, `verify-soft-replica-release`, `apply-soft-replica` | Soft-replica evidence clean; source node remains healthy | next-phase value, transaction/release/evidence paths |
| G7 | Validator/topology continuation | Continue validator admission, routing, and topology only after replica evidence allows it | command family named by verified evidence | Evidence for previous phase is clean and names the next phase | verified evidence and explicit `next_phase` |

The assistant should select the next command from the highest incomplete
component, not from intuition. When a component fails, stop at that component,
collect evidence, and patch or rerun only that component's bounded scope.

### 4.3 Component G5 command block

When the current checkpoint is still valid and the release has not expired, G5 is
the next command. Use the release variables produced by G4:

```powershell
$ApplyArgs = @(
    "apply-genesis-birth",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisBirthReleasePath,
    "--acknowledge-release-sha256", $A1GenesisBirthReleaseSha,
    "--max-age-seconds", "900",
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--max-wait-seconds", "900",
    "--poll-interval-seconds", "5",
    "--operation-id",
        ("apply-genesis-birth-golden-path-g5-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--execute"
)

$A1GenesisBirthRaw = & python -X faulthandler .\tools\mother_deploy.py @ApplyArgs 2>&1
$A1GenesisBirthExit = $LASTEXITCODE
$A1GenesisBirthRawText = ($A1GenesisBirthRaw | Out-String)

$A1GenesisBirthRawText | Set-Content `
    ".\runtime\state\mother\last-apply-genesis-birth-output.txt"
```

Immediately summarize the matching evidence:

```powershell
$EvidenceItem = Get-ChildItem `
    ".\runtime\state\mother\evidence\deployment-genesis-birth\*.json" `
    -ErrorAction SilentlyContinue |
    Where-Object {
        (Get-Content -Raw $_.FullName -ErrorAction SilentlyContinue) -like
            "*$A1GenesisBirthReleaseSha*"
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -ne $EvidenceItem) {
    $Evidence = Get-Content -Raw $EvidenceItem.FullName | ConvertFrom-Json
    [pscustomobject]@{
        evidence_path = $EvidenceItem.FullName
        status = $Evidence.status
        failure_code = $Evidence.failure.code
        failure_message = $Evidence.failure.message
        service_running_healthy = $Evidence.summary.service_running_healthy
        blocks_advancing = $Evidence.summary.blocks_advancing
        hub_healthy = $Evidence.summary.hub_healthy
        hub_local_rpc_verified = $Evidence.summary.hub_local_rpc_verified
        validator_set_verified = $Evidence.summary.validator_set_verified
        complete_super_node_proven = $Evidence.summary.complete_super_node_proven
        complete = $Evidence.summary.complete
        next_phase = $Evidence.summary.next_phase
    } | Format-List
} else {
    Write-Host "NO_MATCHING_BIRTH_EVIDENCE_FOR_RELEASE"
}

if ($A1GenesisBirthExit -ne 0) {
    throw "apply-genesis-birth failed with exit code $A1GenesisBirthExit"
}
```

### 4.4 Golden path monitor proof

For G4 and G5, the monitor is a witness, not the authority. It should prove these
host facts while Mother evidence proves the governed phase:

```text
port 30303 owner = mainneta-super1-lmjwoglwv7ryvrfsbfuu4o7k
stale project leftovers = empty
mainneta-super1 = running / healthy
mother-super-node-fdb = running / healthy
mother-super-node-hub = running / healthy
mother-genesis-proof-guardian = running / healthy
mother-genesis-init = exited / exit 0
mother-superseded-service-cleanup = exited / exit 0
Besu = producing increasing blocks
```

If the monitor and Mother evidence disagree, Mother evidence controls phase
advancement and the monitor output becomes diagnostic input for the bounded fix.


## 5. Identity installation command sequence

Use this sequence when installing reserved identity variables onto the A-side
Coolify service.

### 4.1 Stage identity

Call when standby evidence exists and identity material is present in private
state.

```powershell
$A1IdentityTransaction = Invoke-MotherJson @(
    "stage-identity",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--standby-evidence", $A1StandbyEvidencePath,
    "--operation-id",
        ("stage-identity-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-transaction"
)
```

Carry forward:

```powershell
$A1IdentityTransactionPath = $A1IdentityTransaction.transaction_artifact.path
$A1IdentityTransactionSha  = $A1IdentityTransaction.transaction_artifact.sha256
```

### 4.2 Verify identity transaction

Call immediately after staging, before any release.

```powershell
$A1IdentityTransactionVerification = Invoke-MotherJson @(
    "verify-identity-transaction",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--transaction", $A1IdentityTransactionPath,
    "--max-age-seconds", "900"
)
```

Expected read-only facts:

```text
clean = true
staged_scope = install-reserved-identity
mutation_count = 2
secret_reference_count = 2
persisted_secret_value_count = 0
transaction_apply_authorized = false
live_execution_authorized = false
live_mutation_performed = false
```

### 4.3 Release identity

Call after transaction verification passes.

```powershell
$A1IdentityRelease = Invoke-MotherJson @(
    "release-identity",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--transaction", $A1IdentityTransactionPath,
    "--acknowledge-identity-transaction-sha256", $A1IdentityTransactionSha,
    "--max-age-seconds", "900",
    "--expires-in-seconds", "900",
    "--operation-id",
        ("release-identity-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-release"
)
```

Carry forward:

```powershell
$A1IdentityReleasePath = $A1IdentityRelease.release_artifact.path
$A1IdentityReleaseSha  = $A1IdentityRelease.release_artifact.sha256
```

### 4.4 Verify identity release

Call before inspection or execution.

```powershell
$A1IdentityReleaseVerification = Invoke-MotherJson @(
    "verify-identity-release",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1IdentityReleasePath,
    "--max-age-seconds", "900"
)
```

### 4.5 Inspect identity apply

Call without `--execute` to prove the release is usable and the executor is
implemented.

```powershell
$A1IdentityInspection = Invoke-MotherJson @(
    "apply-identity",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1IdentityReleasePath,
    "--acknowledge-release-sha256", $A1IdentityReleaseSha,
    "--max-age-seconds", "900"
)
```

Expected inspection facts:

```text
execute_requested = false
transaction_apply_authorized = true
live_execution_authorized = true
live_mutation_performed = false
remaining_blocker_codes = []
rollback_implemented = true
```

### 4.6 Execute identity apply

Call once. A nonzero exit consumes the release if the claim was written.

```powershell
$A1IdentityExecution = Invoke-MotherJson @(
    "apply-identity",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1IdentityReleasePath,
    "--acknowledge-release-sha256", $A1IdentityReleaseSha,
    "--max-age-seconds", "900",
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--operation-id",
        ("apply-identity-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--execute"
)
```

Carry forward:

```powershell
$A1IdentityExecutionPath = $A1IdentityExecution.execution_artifact.path
$A1IdentityExecutionSha  = $A1IdentityExecution.execution_artifact.sha256
```

### 4.7 Roll back and verify identity when required

Call rollback inspection first:

```powershell
$A1IdentityRollbackInspection = Invoke-MotherJson @(
    "rollback-identity",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--execution", $A1IdentityExecutionPath,
    "--acknowledge-execution-sha256", $A1IdentityExecutionSha,
    "--operation-id",
        ("inspect-identity-rollback-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
)
```

Execute rollback only if the rollback boundary is open:

```powershell
$A1IdentityRollback = Invoke-MotherJson @(
    "rollback-identity",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--execution", $A1IdentityExecutionPath,
    "--acknowledge-execution-sha256", $A1IdentityExecutionSha,
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--operation-id",
        ("rollback-identity-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--execute"
)
```

Verify rollback independently and write evidence because genesis staging depends
on this proof:

```powershell
$A1IdentityRollbackVerification = Invoke-MotherJson @(
    "verify-identity-rollback",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--rollback-result", $A1IdentityRollbackPath,
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--operation-id",
        ("verify-identity-rollback-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-evidence"
)
```

Carry forward:

```powershell
$A1IdentityRollbackEvidencePath = $A1IdentityRollbackVerification.evidence_artifact.path
$A1IdentityRollbackEvidenceSha  = $A1IdentityRollbackVerification.evidence_artifact.sha256
```

## 6. First-genesis command sequence

Use this sequence after identity has been applied, rolled back, verified absent,
and reapplied with the same commitments.

### 5.1 Stage genesis

Call after a complete identity execution and identity rollback evidence exist.

```powershell
$A1GenesisTransaction = Invoke-MotherJson @(
    "stage-genesis",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--identity-execution", $A1IdentityReapplyExecutionPath,
    "--identity-rollback-verification", $A1IdentityRollbackEvidencePath,
    "--operation-id",
        ("stage-genesis-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-transaction"
)
```

Carry forward:

```powershell
$A1GenesisTransactionPath = $A1GenesisTransaction.transaction_artifact.path
$A1GenesisTransactionSha  = $A1GenesisTransaction.transaction_artifact.sha256
```

### 5.2 Verify genesis transaction

```powershell
$A1GenesisTransactionVerification = Invoke-MotherJson @(
    "verify-genesis-transaction",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--transaction", $A1GenesisTransactionPath
)
```

Expected read-only facts:

```text
clean = true
staged_scope = compile-first-genesis-and-replica-admission
initial_node = mainneta-super1
initial_validator_count = 1
replica_admission_count = 0
persisted_secret_value_count = 0
```

### 5.3 Release genesis

Call after transaction verification passes.

```powershell
$A1GenesisRelease = Invoke-MotherJson @(
    "release-genesis",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--transaction", $A1GenesisTransactionPath,
    "--acknowledge-genesis-transaction-sha256", $A1GenesisTransactionSha,
    "--expires-in-seconds", "900",
    "--operation-id",
        ("release-genesis-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-release"
)
```

Carry forward:

```powershell
$A1GenesisReleasePath = $A1GenesisRelease.release_artifact.path
$A1GenesisReleaseSha  = $A1GenesisRelease.release_artifact.sha256
```

### 5.4 Verify genesis release

```powershell
$A1GenesisReleaseVerification = Invoke-MotherJson @(
    "verify-genesis-release",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisReleasePath,
    "--max-age-seconds", "900"
)
```

### 5.5 Inspect genesis apply

```powershell
$A1GenesisApplyInspection = Invoke-MotherJson @(
    "apply-genesis",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisReleasePath,
    "--acknowledge-release-sha256", $A1GenesisReleaseSha,
    "--max-age-seconds", "900"
)
```

Expected inspection facts:

```text
execute_requested = false
transaction_apply_authorized = true
live_execution_authorized = true
rollback_implemented = true
genesis_birth_blocked_pending_genesis_rollback_cycle = true
```

### 5.6 Execute genesis apply

```powershell
$A1GenesisExecution = Invoke-MotherJson @(
    "apply-genesis",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisReleasePath,
    "--acknowledge-release-sha256", $A1GenesisReleaseSha,
    "--max-age-seconds", "900",
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--operation-id",
        ("apply-genesis-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--execute"
)
```

Carry forward:

```powershell
$A1GenesisExecutionPath = $A1GenesisExecution.execution_artifact.path
$A1GenesisExecutionSha  = $A1GenesisExecution.execution_artifact.sha256
```

### 5.7 Roll back and verify genesis when required

Inspect first:

```powershell
$A1GenesisRollbackInspection = Invoke-MotherJson @(
    "rollback-genesis",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--execution", $A1GenesisExecutionPath,
    "--acknowledge-execution-sha256", $A1GenesisExecutionSha,
    "--operation-id",
        ("inspect-genesis-rollback-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
)
```

Execute if the rollback boundary is open:

```powershell
$A1GenesisRollback = Invoke-MotherJson @(
    "rollback-genesis",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--execution", $A1GenesisExecutionPath,
    "--acknowledge-execution-sha256", $A1GenesisExecutionSha,
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--max-wait-seconds", "180",
    "--poll-interval-seconds", "2",
    "--operation-id",
        ("rollback-genesis-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--execute"
)
```

Verify rollback and write evidence for genesis-birth:

```powershell
$A1GenesisRollbackVerification = Invoke-MotherJson @(
    "verify-genesis-rollback",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--rollback-result", $A1GenesisRollbackPath,
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--operation-id",
        ("verify-genesis-rollback-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-evidence"
)
```

Carry forward:

```powershell
$A1GenesisRollbackEvidencePath = $A1GenesisRollbackVerification.evidence_artifact.path
$A1GenesisRollbackEvidenceSha  = $A1GenesisRollbackVerification.evidence_artifact.sha256
```

## 7. Genesis-birth command sequence

Use this sequence after the same genesis has been applied, rolled back, verified,
and reapplied. The target service UUID is the intended long-lived service. The
superseded service UUID is the stale project that must no longer own exposed
ports.

### 7.1 Release genesis birth

Call after the corrected health-model Compose is in the codebase and genesis
reapplication proof exists.

```powershell
$A1GenesisBirthRelease = Invoke-MotherJson @(
    "release-genesis-birth",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--execution", $A1GenesisReapplyExecutionPath,
    "--acknowledge-genesis-execution-sha256", $A1GenesisReapplyExecutionSha,
    "--genesis-rollback-verification", $A1GenesisRollbackEvidencePath,
    "--superseded-service-uuid", "pc20bsxvq3ykjnpzque08l63",
    "--acknowledge-superseded-service-removal",
        "REMOVE:mainneta-super1:pc20bsxvq3ykjnpzque08l63",
    "--expires-in-seconds", "900",
    "--operation-id",
        ("release-genesis-birth-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--write-release"
)
```

Carry forward:

```powershell
$A1GenesisBirthReleasePath = $A1GenesisBirthRelease.release_artifact.path
$A1GenesisBirthReleaseSha  = $A1GenesisBirthRelease.release_artifact.sha256
```

Display:

```powershell
[pscustomobject]@{
    release_path = $A1GenesisBirthReleasePath
    release_sha256 = $A1GenesisBirthReleaseSha
    expires_at = $A1GenesisBirthRelease.expires_at
    precleanup_compose_sha =
        $A1GenesisBirthRelease.proof_plan.precleanup_proof_compose.sha256
    proof_compose_sha =
        $A1GenesisBirthRelease.proof_plan.proof_compose.sha256
    cleanup_service =
        $A1GenesisBirthRelease.proof_plan.
            superseded_service.host_container_cleanup.service
    stale_project =
        $A1GenesisBirthRelease.proof_plan.
            superseded_service.host_container_cleanup.compose_project_label
} | Format-List
```

### 7.2 Verify genesis-birth release

```powershell
$A1GenesisBirthReleaseVerification = Invoke-MotherJson @(
    "verify-genesis-birth-release",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisBirthReleasePath,
    "--max-age-seconds", "900"
)
```

Expected read-only facts:

```text
clean = true
transaction_apply_authorized = true
live_execution_authorized = false
remaining_blocker_codes = [MOTHER_DEPLOY_GENESIS_BIRTH_EXECUTOR_NOT_RUN]
host_cleanup_service = mother-superseded-service-cleanup
host_cleanup_guardian_proof_required = true
manual_ssh_required = false
public_endpoint_created = false
guardian_internal_only = true
persistent_volume_cleanup_performed = false
```

### 7.3 Inspect genesis-birth apply

Call without `--execute`. This is safe even if a previous release was already
claimed; it reports `release_already_claimed`.

```powershell
$A1GenesisBirthInspection = Invoke-MotherJson @(
    "apply-genesis-birth",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisBirthReleasePath,
    "--acknowledge-release-sha256", $A1GenesisBirthReleaseSha,
    "--max-age-seconds", "900",
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--max-wait-seconds", "900",
    "--poll-interval-seconds", "5"
)
```

Expected inspection facts:

```text
execute_requested = false
executor_implemented = true
release_already_claimed = false
transaction_apply_authorized = true
live_execution_authorized = true
remaining_blocker_codes = []
```

### 7.4 Execute genesis-birth apply

Use raw capture for live execution so stderr, tracebacks, or empty output are
preserved even when the process exits nonzero.

```powershell
$ApplyArgs = @(
    "apply-genesis-birth",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--release", $A1GenesisBirthReleasePath,
    "--acknowledge-release-sha256", $A1GenesisBirthReleaseSha,
    "--max-age-seconds", "900",
    "--timeout", "30",
    "--max-response-bytes", "4194304",
    "--max-wait-seconds", "900",
    "--poll-interval-seconds", "5",
    "--operation-id",
        ("apply-genesis-birth-mainneta-super1-" +
         [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")),
    "--execute"
)

$A1GenesisBirthRaw = & python -X faulthandler .\tools\mother_deploy.py @ApplyArgs 2>&1
$A1GenesisBirthExit = $LASTEXITCODE
$A1GenesisBirthRawText = ($A1GenesisBirthRaw | Out-String)

$A1GenesisBirthRawText | Set-Content `
    ".\runtime\state\mother\last-apply-genesis-birth-output.txt"
```

If exit code is zero, parse JSON:

```powershell
$A1GenesisBirthExecution = $A1GenesisBirthRawText | ConvertFrom-Json
$A1GenesisBirthExecution | ConvertTo-Json -Depth 100
```

If exit code is nonzero, do not rerun that release. Find evidence by release SHA:

```powershell
$EvidenceItem = Get-ChildItem `
    ".\runtime\state\mother\evidence\deployment-genesis-birth\*.json" `
    -ErrorAction SilentlyContinue |
    Where-Object {
        (Get-Content -Raw $_.FullName -ErrorAction SilentlyContinue) -like
            "*$A1GenesisBirthReleaseSha*"
    } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
```

### 7.5 Verify genesis-birth evidence

Call after successful execution or after failure evidence exists.

```powershell
$A1GenesisBirthEvidenceVerification = Invoke-MotherJson @(
    "verify-genesis-birth-evidence",
    "--network", "mainnet",
    "--node", "mainneta-super1",
    "--evidence", $A1GenesisBirthEvidencePath,
    "--max-age-seconds", "900"
)
```

Expected final success facts:

```text
status = pass
service_running_healthy = true
blocks_advancing = true
hub_healthy = true
hub_local_rpc_verified = true
validator_set_verified = true
complete_super_node_proven = true
complete = true
next_phase = stage-soft-replica-configuration
```

## 8. Coolify host monitor commands

Run host monitors in a separate SSH session before a live `--execute` command.
These monitors are read-only.

### 8.1 Deployment health monitor

```bash
TARGET_UUID="lmjwoglwv7ryvrfsbfuu4o7k"
STALE_PROJECT="pc20bsxvq3ykjnpzque08l63"

while true; do
  clear
  date -u '+=== %Y-%m-%dT%H:%M:%SZ ==='

  echo
  echo "=== PORT 30303 OWNER ==="
  docker ps --filter publish=30303 \
    --format 'name={{.Names}} status={{.Status}} ports={{.Ports}}' || true

  echo
  echo "=== STALE PROJECT LEFTOVERS ==="
  docker ps -a \
    --filter "label=com.docker.compose.project=$STALE_PROJECT" \
    --format 'name={{.Names}} status={{.Status}} image={{.Image}}' || true

  echo
  echo "=== TARGET STACK HEALTH ==="
  docker ps -a --format '{{.Names}}' |
  grep "$TARGET_UUID" |
  sort |
  while read c; do
    docker inspect "$c" --format 'name={{.Name}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} service={{index .Config.Labels "com.docker.compose.service"}} image={{.Config.Image}}'
  done

  echo
  echo "=== BESU LAST BLOCKS ==="
  for c in $(docker ps -a --format '{{.Names}}' | grep 'mainneta-super1'); do
    echo "--- $c ---"
    docker logs --timestamps --tail 40 "$c" 2>&1 |
      grep -E 'Produced empty block|Imported|ERROR|WARN' || true
  done

  sleep 5
done
```

Expected healthy model:

```text
mainneta-super1                  running / healthy
mother-super-node-fdb            running / healthy
mother-super-node-hub            running / healthy
mother-genesis-proof-guardian    running / healthy
mother-genesis-init              exited / exit 0
mother-superseded-service-cleanup exited / exit 0
stale project leftovers          empty
Besu                              producing new blocks
```

### 7.2 Event stream

```bash
docker events \
  --filter type=container \
  --filter type=image \
  --format '{{.Time}} {{.Type}} {{.Action}} name={{.Actor.Attributes.name}} image={{.Actor.Attributes.image}}' |
grep --line-buffered -E \
'foundationdb|lmjwoglwv7ryvrfsbfuu4o7k|pc20bsxvq3ykjnpzque08l63|mother-superseded-service-cleanup|mother-super-node-fdb|mainneta-super1'
```

## 9. What to paste back to the assistant

After every command family, paste the compact output and any final JSON. The
assistant should not infer state from memory when these fields are available.

### 8.1 After a release

Paste:

```text
release_path
release_sha256
expires_at
proof_compose_sha
precleanup_compose_sha, when present
```

### 8.2 After a verification

Paste:

```text
clean
network
nodes
service_uuid, when present
remaining_blocker_codes
transaction_apply_authorized
live_execution_authorized
```

### 8.3 After an execution

Paste:

```text
exit_code
raw_output_empty
evidence_path, if any
status
failure_code
failure_message
summary
```

### 8.4 After host observation

Paste:

```text
port owner
stale project leftovers
target stack health
FDB health
Hub health
Guardian health
latest Besu block production lines
```

## 10. Recovery rules for consumed releases

A release is consumed if a claim file exists beneath the matching claims
directory. For genesis-birth:

```powershell
$ClaimPath = ".\runtime\state\mother\actions\deployment-genesis-birth-execution-claims\$A1GenesisBirthReleaseSha.json"
Test-Path $ClaimPath
```

If the claim exists:

1. Do not reuse the release.
2. Look for evidence by release SHA.
3. If no evidence exists, collect the claim file and raw output file.
4. Patch the claim-to-evidence gap or create a fresh release only after the cause
   is understood.

## 11. Next-phase selector after genesis birth

Use the `next_phase` field from verified genesis-birth evidence.

| `next_phase` | Next command family |
|---|---|
| `stage-soft-replica-configuration` | `stage-soft-replica` |
| `manual-review-required` | inspect evidence, host state, and failure code before issuing a new release |
| missing or unknown | do not continue; verify evidence and command surface first |

The next command family after a clean birth is:

```text
stage-soft-replica
verify-soft-replica-transaction
release-soft-replica
verify-soft-replica-release
apply-soft-replica
```

After soft-replica sync and validator admission, follow the same
stage/verify/release/verify/apply/verify-evidence pattern. Do not jump phases
based on host health alone; use Mother evidence `next_phase`.
