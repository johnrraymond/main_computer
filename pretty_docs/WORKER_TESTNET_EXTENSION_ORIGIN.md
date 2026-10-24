# Worker browser acceptance: testnet RPC extension-origin compatibility

The official MetaMask Web Store extension uses ID `nkbihfbeogaeaoehlefnkodbefgpgknn`.
Our verified GitHub release is installed **unpacked** for a disposable Chromium
acceptance test and may receive a different path-derived ID. The existing Mother
V2.1 RPC route allows the canonical production extension Origin and the two
local Worker Origins, not the unpacked test extension's Origin.

This patch provides an opt-in, testnet-only exact Origin through the existing
Mother route-mutation harness. It does not alter the verified MetaMask
extension, Mother’s normal route writer, the Worker or its RPC requirements.

1. Apply the patch and run `python tools/worker_browser_acceptance.py cache-wallet`.
   The runner prints `Disposable MetaMask extension ID: <id>` (public, not a secret).
2. Preview the Mother route with the actual 32-letter ID:

   `python mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --test-extension-id YOUR_EXTENSION_ID`

   Confirm the preview target is the actual current testnet route. Refuse to
   execute if Mother reports another route, host, or backend.
3. Authorize the mutation explicitly through Mother:

   `python mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --test-extension-id YOUR_EXTENSION_ID --execute-mutations --yes-i-know-this-mutates-target-host`

   The ephemeral Coolify writer accepts only a byte-exact known legacy/V2/V2.1
   or proposed route, checks Origin-bearing POST response headers and the
   actual chain ID for **each** allowed Origin, and rolls back on failure.
   No SSH or manual route modification is permitted.
4. Rerun `python tools/worker_browser_acceptance.py run`. The test extension ID
   should stay stable because the unpacked extension's directory is unchanged.
   Full acceptance still requires a real wallet chain/block RPC proof and Hub
   key issuance. Public Cloudflare policy could independently block a request;
   if so, record the response and do not widen CORS to a wildcard.

Rollback requires the same ID, `--rollback`, and both explicit mutation flags.
Rollback restores Mother’s legacy route bytes; restoring V2.1 without the test
Origin would require a separate reviewed policy mutation. Do not silently
change routes, network IDs, or production wallet profiles.
