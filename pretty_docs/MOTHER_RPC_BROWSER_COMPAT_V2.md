# Mother RPC browser compatibility — canonical route policy

Mother's normal RPC route writer and the explicit `rpc-browser-compat-v2` maintenance command now use **one canonical origin policy**. No second registry, file, background reconciler, Cloudflare mutation, or deployment mechanism is involved.

## Canonical origins

| Network | Authorized browser origins |
| --- | --- |
| Mainnet | `http://127.0.0.1:8765`, `http://localhost:8765`, production MetaMask `chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn` |
| Testnet | Those same three origins, plus the disposable MetaMask `chrome-extension://dnamoknnfmliijgdpmkcjmgihhhidcgj` |

`TESTNET_METAMASK_ID` is a source-controlled authorization for this **specific** unpacked test extension. An unexpected ID is not automatically trusted. If the harness' installed extension ID changes, investigate why before updating the canonical constant and redeploying via the official Mother mutation harness. Mainnet never receives the fourth origin.

The standard `_route_writer_script()` uses `render_canonical_v2()`; the explicit compatibility command resolves the same policy for previews and guarded writes. An ordinary testnet RPC route rewrite therefore emits the four-origin route, rather than silently removing the previously approved extension. Existing per-Origin Traefik routing, the public backend, and origin-free fallback routers remain unchanged. Unmatched origins are never granted wildcard CORS.

The testnet route produced by the canonical renderer for `coolify-b` / `testnetb-super1` matches the previously verified deployed route fingerprint:

`1101ada180dd3f647ba8c928511fd4c43ef37ba7236a48d4507c7339758d2120`

That fingerprint covers the *rendered route content*, not the state of a remote host. Source/tests do not mutate infrastructure.

## Preview and guarded maintenance

Normal Mother operations already use the canonical renderer. The dedicated maintenance command remains explicit and retains its byte-exact input guard, rollback trap, local `eth_chainId` check, and per-origin POST/CORS assertions. The ordinary writer's existing health check is **chain-ID verification only**; this change does not add a new live CORS check to that path.

Read-only preview:

```text
python mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b
```

Optional `--test-extension-id dnamoknnfmliijgdpmkcjmgihhhidcgj` is accepted on testnet, but must equal the canonical ID; other test IDs are refused before private-state access or mutation. No test-extension ID is accepted on mainnet. A preview reports planned route bytes and origins; it is not proof of the live proxy state.

Explicit maintenance write, **only** when intended and authorized:

```text
python mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --execute-mutations --yes-i-know-this-mutates-target-host
```

Explicit emergency rollback to the byte-exact original legacy route:

```text
python mother_mutate_harness.py rpc-browser-compat-v2 --network testnet --node testnetb-super1 --host coolify-b --rollback --execute-mutations --yes-i-know-this-mutates-target-host
```

Rollback intentionally removes browser compatibility; the next ordinary Mother rewrite returns to canonical policy. It is not a durable policy change. Neither normal rendering nor the explicit command uses SSH, direct Coolify edits, or manual proxy-file mutation.

## External verification

The dedicated mutation's local verification does not prove external Cloudflare behavior. Check public-origin requests independently, with both OPTIONS and POST. For example:

```text
python tools/worker_browser_acceptance/rpc_origin_preflight.py --extension-id dnamoknnfmliijgdpmkcjmgihhhidcgj
```

The browser-style extension-origin POST should return HTTP 200, an exact CORS origin, and a valid testnet chain proof. Python's default HTTP transport can independently receive Cloudflare 1010; that does not establish a failing Traefik route. Full acceptance still requires the real disposable MetaMask/Worker connection.

## Regression tests

```text
python -m pytest -q tests/test_mother_rpc_browser_compat_v2.py tests/test_mother_rpc_route_ensure.py tests/test_mother_testnet_rpc_route_completion.py tests/test_mother_unified_rpc_writer_networks.py tests/test_mother_rpc_canonical_v2_yagni.py
```

The tests verify the canonical four-origin testnet/three-origin mainnet split, the exact deployed-policy fingerprint, repeatable ordinary route renders, compatibility between the normal writer and guarded maintenance writer, rejection of conflicting IDs, and preservation of exact per-origin CORS headers. No live mutation occurs during tests.

The generated helper shell checks require a working Bash interpreter. On Windows, tests that cannot execute Bash are explicitly skipped; a WSL launcher without an installed distribution is not treated as successful execution.
