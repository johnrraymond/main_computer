# P5.2 — Unified Hub interface: operator acceptance

This patch **does not** change Hub server protocols, Worker connection ownership,
settings persistence, payment authorization, or production infrastructure. The
Worker page, standalone Chat Console, and plugged-in Chat Console continue to
read and change the same Worker settings through the existing Viewport API.

The Worker page's effective network selection is resolved against the canonical
Hub network registry. Dev and Test target their local Dev-Test Hubs; public
Testnet and Mainnet target Stable Hub profiles. A missing selection means **no
Hub**, never the registry's default public network.

The older `RemoteHubExecutionGateway` invokes only the Dev-Test safe-chat
surface. That surface is a **simulation**, even when its local accounting
records a charge. It cannot be substituted for real paid Stable Hub Worker
execution. If Testnet, Mainnet, SLH or no network is selected, the old surface
is reported as unsupported rather than performing simulated remote work or
falling back to Dev/Test. Actual public paid requester work must continue
through its separately authenticated operational transport. This patch does
not implement a new public Chat Console requester transport.

## Focused tests

From the repository root on Windows:

```powershell
python -m pytest -q `
  tests/test_hub_interface_p52.py `
  tests/test_chat_console_remote_worker_modal_contract.py `
  tests/test_dev_test_hub_p3_isolation.py `
  tests/test_startup_hub_flags_independent.py `
  tests/test_worker_hub_identity_health.py
```

## Read-only verification

```powershell
python -m tools.hub_interface_verify
python -m tools.hub_interface_verify --live
```

The first command checks the five-network protocol/destination matrix. The
second reads the running local Viewport's canonical Worker settings, reports
selected network, resolved destination, and overflow status **without printing
MSKs, wallet signatures, or passwords**. Neither command requests remote work,
modifies settings, deploys services, or spends credits.

## Interactive acceptance

1. Select Dev, Test, Testnet, and Mainnet on the Worker page one at a time.
   After each save, run the live verifier and confirm it reports that selection.
2. Enable/disable paid overflow in the standalone Chat Console and its plugged-in
   version; verify the Worker page reflects the same setting in both directions.
3. Switch the selected network with the Chat Console open, then close and reopen
   both interfaces. Verify the selection persists and no alternative network
   is used silently.
4. With Testnet or Mainnet selected, try the *old* Dev-Test-only overflow
   readiness route. It must say unsupported, not contact local Dev or report
   a simulated completion as paid Worker execution.
5. The existing Stable Hub paid requester acceptance battery remains a
   separate requirement. Do not use these read-only checks as proof of a
   production paid Worker request.

No Windows-boot SLH policy, `--no-dev-hub`, `--no-slh-hub`, Mother identity,
or deployment setting changes are part of this patch.
