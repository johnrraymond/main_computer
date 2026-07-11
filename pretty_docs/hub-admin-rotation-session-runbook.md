# Hub admin rotation session runbook

Use the high-level `rotate` command for normal Hub admin signer rotation. The lower-level
commands remain available for debugging, but operators should normally run the session
workflow.

## Start a local dev rotation

Create the session with the target network, Hub, officer, and a durable session slug:

```powershell
python .\tools\hub_admin_rotation.py rotate --network dev --hub dev-hub1 --office O0 --session "first-dev-hub1-local"
```

The command prints only the next step. A normal first response is:

```text
rotation first-dev-hub1-local: created
stage: staged address2 0x71f462...
next: run rotate again to authorize
```

After the first call, resume with the session slug:

```powershell
python .\tools\hub_admin_rotation.py rotate --session "first-dev-hub1-local"
```

## Expected local sequence

The console output should stay minimal:

```text
rotation first-dev-hub1-local
stage: authorized address2
next: run rotate again to switch hub config
```

```text
rotation first-dev-hub1-local
stage: switched hub config to address2
next: start/restart hub, then run rotate again
  .\scripts\main-computer-start-stop.ps1 dev-hub-start -Root "$PWD"
```

Start or restart the local Hub when instructed:

```powershell
.\scripts\main-computer-start-stop.ps1 dev-hub-start -Root "$PWD"
```

Then resume:

```powershell
python .\tools\hub_admin_rotation.py rotate --session "first-dev-hub1-local"
```

The command must not revoke the old signer until the Hub reports the new signer. If the
Hub is down or still reports the old signer, the command blocks and prints the required
restart/start command.

After verification, continue with the same command:

```text
rotation first-dev-hub1-local
stage: verified hub on address2
next: run rotate again to revoke old signer
```

```text
rotation first-dev-hub1-local
stage: revoked address1
next: run rotate again to delete old private key
```

```text
rotation first-dev-hub1-local
complete: active address2 0x71f4626cb3723f8eEB486F1b12E9316843074bb0
next: archive session
  python .\tools\hub_admin_rotation.py rotate --session "first-dev-hub1-local" --finished
```

Finalize explicitly:

```powershell
python .\tools\hub_admin_rotation.py rotate --session "first-dev-hub1-local" --finished
```

This archives the session under:

```text
runtime/rotations/hub-admin/archive/<timestamp>-first-dev-hub1-local
```

## Session files

Active sessions live under:

```text
runtime/rotations/hub-admin/<session>/
```

Each session has:

```text
session.json
events.jsonl
```

The console output is intentionally short. Detailed audit information belongs in the
session files.

## Remote networks

For `testnet` and `mainnet`, session creation is gated by the deployed escrow shape.
The first `rotate` call checks the configured deployment metadata, RPC, escrow code, the
`authorizedBridgeControllers(address)` getter, and the authorize proposal entrypoint before
writing a session or staging a key.

If the configured network still points at the old single-controller escrow shape, the
command fails before creating the session:

```text
rotation testnet-hub1-rotation
error: testnet is not ready for hub-admin rotation
reason: deployed escrow does not support authorizedBridgeControllers/proposeAuthorizeBridgeController
session: not created
action required: deploy the new HubCreditBridgeEscrow shape, update deployment metadata, then run this command again
```

The failure path intentionally says `action required:` instead of `next:` because the
operator must place the new contract shape and update deployment metadata before the
rotation workflow is valid.

## Dry runs

Use `--dry-run` to see the next action without mutating session or private state:

```powershell
python .\tools\hub_admin_rotation.py rotate --network dev --hub dev-hub1 --office O0 --session "first-dev-hub1-local" --dry-run
```

Dry-run output uses `would:` wording and should not say that an action completed.
