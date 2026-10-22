# Mother RPC browser compatibility — opt-in V2.1 experiment

The V2.1 correction preserves the original `rpc-browser-compat-v2` command and upgrades only the separate V2 implementation, tests, and this document. Apply the V2.1 changeset **on top of the existing V2 patch**. It does not modify the harness dispatch or any normal Mother operations.

This update makes **zero modifications to the existing shared route generator, the harness dispatch, or normal Mother deployment logic**. The original V2 patch already installed a three-line command dispatch in `mother_mutate_harness.py`; that dispatch remains unchanged. V2 functionality resides entirely in `tools/mother_rpc_browser_compat_v2.py`.

## V2.1 POST response correction

The original V2 passed OPTIONS but a normal POST omitted the `Access-Control-Allow-Origin` response header: Traefik checked a request after another middleware removed its `Origin`. V2.1 preserves Mother's two original Host routers and adds exact-Origin routers for the two local Worker origins and the MetaMask extension origin. Each exact-Origin router uses a **single** headers middleware that handles OPTIONS, strips `Origin` before forwarding POST to Besu, and sets `Access-Control-Allow-Origin` to the already matched origin explicitly on the POST response. Unknown origins get no CORS permission; no wildcard is used. This requires Traefik v3 `Header` routing support; if the route fails to load the writer must fail closed.

The V2 writer accepts only byte-exact original Mother, deployed V2, or V2.1 files. It performs three live Origin-bearing POST checks against the locally routed Traefik hostname and verifies both the CORS response header and blockchain chain ID **before** removing its backup/rollback trap. If any check fails, the writer exits unsuccessfully and its trap restores the byte-exact route that existed immediately before the attempt. This deliberately does not assert full external Cloudflare browser behavior until separately tested.


## Read-only preview (no infrastructure changes)

From `C:\Users\subsi\main_computer`:

```powershell
python .\mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b
```

The command validates authoritative private state and prints the route, selected backend, permitted origins, and V2 policy fingerprint. Preview does **not** probe the live Traefik file and cannot prove the route will qualify for an upgrade. No implicit mainnet fallback.

## Explicit opt-in testnet mutation

After inspecting preview and confirming the public RPC route is currently served by the named node/host:

```powershell
python .\mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --execute-mutations --yes-i-know-this-mutates-target-host
```

This calls Mother's existing authorized, ephemeral Coolify route writer. The V2 helper will **refuse** to change a missing, custom, unexpectedly formatted, or differently routed Traefik file, while accepting the byte-exact previous V2 deployment. A mainnet legacy route file must be absent, rather than being deleted. The helper saves a copy of the original route and restores it on local verification failure. The existing writer still checks RPC backend and routed chain IDs and performs ephemeral helper cleanup.

## Explicit rollback if V2 fails public browser verification

```powershell
python .\mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --rollback --execute-mutations --yes-i-know-this-mutates-target-host
```

Rollback uses the **same** exact-content guard and the existing authorized Coolify writer; it restores Mother's byte-exact legacy route, and refuses any unknown/retargeted route. Rollback cannot undo independently changed Cloudflare policies (V2 never modifies those).

An explicit successful mutation changes the selected *live public RPC route*; it is not risk-free just because installation is additive. A subsequent ordinary Mother route rewrite could restore the legacy route because the production generator remains deliberately unchanged. Do not integrate V2 automatically until it is accepted.

## Verify read-only after opt-in mutation

```powershell
curl.exe -i -X OPTIONS "https://testnet-rpc.greatlibrary.io/" -A "Mozilla/5.0" -H "Origin: chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type"
```

Expect a 200/204 with `Access-Control-Allow-Origin` and `POST, OPTIONS`. Then test the actual Origin-bearing POST (not only the OPTIONS preflight):

```powershell
curl.exe -i -X POST "https://testnet-rpc.greatlibrary.io/" -H "Origin: http://127.0.0.1:8765" -H "Content-Type: application/json" --data-raw '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
```

The POST must include `Access-Control-Allow-Origin: http://127.0.0.1:8765` and a JSON-RPC `result`. Finally test from the Worker page using browser `fetch()` and test `eth_blockNumber` in MetaMask. A Cloudflare Browser Integrity Check rule may still block MetaMask; V2 does not attempt to manage Cloudflare. There is no guarantee that a verified local Traefik route implies public browser connectivity.

The Worker page and its wallet validation are unchanged. There is no SSH, Cloudflare API integration, contract mutation, or Besu restart.

## Local tests

```powershell
python -m pytest -q tests/test_mother_rpc_browser_compat_v2.py tests/test_mother_rpc_route_ensure.py tests/test_mother_testnet_rpc_route_completion.py tests/test_mother_unified_rpc_writer_networks.py
```

### Windows / WSL test-runner behavior

The generated ephemeral Coolify writer runs under a Linux shell. The local V2
regression suite also includes Bash syntax and simulated POST-verification tests.
On Windows, `bash.exe` may point to WSL even when `/bin/bash` does not exist in
the configured distribution. Previously this caused four apparent test failures,
and misleading passes in four expected-rejection test cases.

The test suite now validates that Bash can **actually execute** a sentinel
command before attempting shell-only tests. It prefers Git for Windows Bash
when installed. When no operational Bash is found, the eight shell-execution
cases are **reported as skipped, never passed**, while portable route-policy,
CORS-response, per-origin and chain-ID assertions continue to run.

On a Linux host with Bash, all eight shell-execution cases run, including
both successful and deliberately failing simulated POST checks. A local skip
is not proof that the shell is valid; the Coolify writer must still pass its
live route/chain/CORS checks during an explicitly authorized deployment.

This adjustment changes only tests and documentation. **No V2 routing,
mutation, default Mother operation, or installed proxy configuration changes.**

## Scope

Only these files are in the patch:

- `tools/mother_rpc_browser_compat_v2.py` (updated V2-only implementation)
- `tests/test_mother_rpc_browser_compat_v2.py` (V2 POST and upgrade-guard regression tests)
- `pretty_docs/MOTHER_RPC_BROWSER_COMPAT_V2.md` (this file)

The previously installed three-line dispatch in `mother_mutate_harness.py` stays unchanged.

Do not apply the earlier `main_computer_mother_rpc_browser_compat_20261021.diff` alongside this V2-only patch. That earlier changeset modifies the legacy generator and is deliberately superseded here.
