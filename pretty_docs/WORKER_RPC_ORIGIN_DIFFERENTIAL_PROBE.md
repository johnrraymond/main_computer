# Worker Testnet RPC: Origin Differential Probe

This read-only acceptance-harness patch diagnoses MetaMask's "Still connecting to Main Computer Testnet" or HTTP 403 without another wallet rotation or infrastructure mutation. The ordinary Worker page and Chrome's unpacked MetaMask extension are separate HTTP clients.

## Run without rotating the disposable wallet

Apply the patch files (rooted at `main_computer_test/`) to the Main Computer repository. With the existing disposable MetaMask test profile from a previous run and the local Worker web server available:

```text
python tools/worker_browser_acceptance.py rpc-probe
```

The probe opens the existing test-only Chromium profile. It does not import or rotate a wallet, call Connect Wallet, approve prompts, issue signatures, alter the network, or edit Mother/Coolify state. If no MetaMask extension renderer is already open, the standalone probe opens `home.html` for the verified extension ID, performs a read-only fetch, and closes the temporary tab. It may also attach read-only to the exact extension service-worker CDP target. It sends only `eth_chainId` and `eth_blockNumber` to the pinned public testnet endpoint. If no cached disposable test profile exists, the command refuses, rather than creating a new wallet silently.

Results go to `runtime/reports/worker-browser-acceptance/rpc-origin-probe.json`. The console prints a short summary for three surfaces:

- `workerPage`: fetch from `http://127.0.0.1:8765/applications/worker`
- `extensionPage`: fetch from the actual unpacked MetaMask extension page
- `extensionServiceWorker`: fetch from the actual MetaMask service-worker context

Each result distinguishes `http-rejected` plus status, `fetch-rejected` (transport/CORS), `timeout`, `verified-chain`, `valid-block`, and unexpected JSON/chain values. If a context cannot be inspected, the report includes a reason such as `extension-renderer-navigation-failed`, `no-extension-service-worker-target`, or `cdp-service-worker-attach-or-evaluation-failed` instead of silently reporting `available: false`. No inaccessible context counts as a 403 or a successful probe.

These are **comparative probes**, not a substitute for MetaMask's own JSON-RPC engine. A passing extension fetch does not prove that MetaMask's network controller uses identical request headers. To certify Worker acceptance, run:

```text
python tools/worker_browser_acceptance.py run
```

The full harness does not create an extra extension tab or attach to the extension service worker by CDP; those more involved diagnostics run only in explicit `rpc-probe` mode. The full harness also records `rpcProbeBeforeConnect` and, on connection failure, `rpcProbeAfterFailure` inside its existing `acceptance.json` report. It still requires a real provider chain ID, block number, correct disposable address, and Hub multi-session key issuance. The probe does not grant any CORS origins, skip Cloudflare, or loosen the Worker RPC gate.

## Interpretation

- **Worker PASS; extension HTTP 403:** an origin/client-specific HTTP denial exists; inspect the exact extension origin against the route and edge policy, using official Mother preview/verification before any mutation.
- **Worker PASS; extension fetch-rejected:** investigate the extension's actual preflight/CORS or transport path. Do not claim the 403 arose at a particular layer without HTTP evidence.
- **All three PASS; MetaMask still fails:** the actual MetaMask network controller may be sending a different request than the simple probes, or using a different RPC URL. The next investigation should inspect its request metadata, not widen the CORS allowlist.
- **Worker and extension both fail:** first verify the public endpoint and upstream route.
- **Worker PASS; extension unavailable:** the experiment remains inconclusive for that extension origin. Investigate the reported access/target reason before changing Mother or the edge.

Never paste a mnemonic, password, full MetaMask extension HTML, or private signed request into a bug report. The report contains only public origin IDs, method results, status classifications, and expected chain identity.

**Evidence boundary:** This probe cannot establish that Cloudflare, Traefik, or Mother rejected a real MetaMask network-controller request without an actual response or matching policy log. A working Worker-page fetch combined with absent extension probes is not grounds for changing origin allowlists.

## Extension fetch rejection network evidence (v2)

For the exact `testnet-rpc.greatlibrary.io` URL, explicit `rpc-probe` now attempts to enable the Chromium Network domain on the *actual* MetaMask service-worker CDP target. If `fetch()` is rejected, each RPC method retains `kind: fetch-rejected` and adds `errorClass` (sanitized JavaScript exception class) and `networkEvidence` (read-only Chromium events, if supported). No request headers, response bodies, cookies, wallet secrets, or unrelated URLs are captured.

- `networkEvidence.kind: cors` with `corsError` is direct browser evidence of a CORS/preflight rejection. An optional `httpStatuses` list is an HTTP status **observed by Chromium**, which may not be readable from JavaScript.
- `networkEvidence.kind: network-error` with `netError` is a Chromium transport failure such as `net::ERR_CONNECTION_RESET`.
- `networkEvidence.kind: blocked` indicates a browser-side blocked reason.
- `networkEvidence.kind: http-response-observed` indicates a response status was observed but does **not** prove the JavaScript fetch had access to it.
- `networkEvidence.kind: no-network-events` means Chromium did not emit a matching event for this probe; it is inconclusive, not a pass or a 403.
- `networkEvidence.kind: network-domain-unavailable` means this CDP target did not allow Network instrumentation. It cannot establish CORS or transport.

A MetaMask extension renderer that navigates during the probe is reported as `renderer-navigated-during-probe`; a closed renderer as `renderer-closed`. Raw exception messages are not serialized into the report. Re-run `rpc-probe` without wallet rotation; it cannot alter Mother or Cloudflare. Do not apply any infrastructure mutation solely because `fetch-rejected` appears.


## Fetch execution-stage evidence (v3)

A service-worker `fetch-rejected` with `networkEvidence.kind: no-network-events`
is not evidence of HTTP 403, nor even evidence that a fetch reached Chromium's
network stack. Each failed RPC call now includes:

- `errorStage: build-request`: the read-only probe failed *before* invoking fetch (for example, unsupported `AbortSignal.timeout`). Fix the probe/runtime, not Mother.
- `errorStage: invoke-fetch`: calling the fetch API threw synchronously, before it returned a promise.
- `errorStage: await-fetch`: a fetch promise was returned but rejected; investigate request/CORS/network policy, using independent preflight evidence and MetaMask's own controller request where available.
- `errorStage: read-json`: fetch succeeded, but parsing threw unexpectedly; inspect the safe JSON-result classification, not wallet state.

The reported `errorClass` is from a fixed allowlist of exception names, and
`runtimeFeatures` contains only the booleans `fetch` and `abortSignalTimeout`.
Neither raw error text nor request or response contents are recorded.
`no-network-events` remains inconclusive because the debugging target may not
emit network events through this CDP attachment. Re-run the same explicit
`rpc-probe` command; no wallet rotation or infrastructure mutation is required.

## Independent HTTP preflight comparison (v3)

If CDP still provides no extension-worker network events, compare the route's
responses to exactly the two known origins with the Python standard library:

```text
python tools/worker_browser_acceptance/rpc_origin_preflight.py --extension-id dnamoknnfmliijgdpmkcjmgihhhidcgj
```

This sends an OPTIONS preflight and one read-only `eth_chainId` POST with each
Origin header. It does not simulate MetaMask's complete request headers or
Chromium's CORS enforcement, so it is independent evidence about the HTTP
response, **not** proof of the specific request that MetaMask sent. It prints
status, whether the allow-origin header matched, and whether POST and
Content-Type are permitted. It never prints the response body or request
headers and refuses redirects. A CORS failure can occur even if Python can
read an HTTP 200 body because Python does not enforce browser CORS.

## HTTP client fingerprint controls (v4)

The standalone Python preflight may return HTTP 403 for **both** the Worker-page
Origin and the unpacked-extension Origin, even if an actual Chromium Worker-page
fetch returns HTTP 200. This **does not** establish an extension-only CORS
failure. The Python client differs from Chromium in User-Agent, TLS/browser
fingerprint, connection behavior, and possibly proxy routing.

The preflight now also reports `clientHeaderControls` with three read-only
`eth_chainId` POSTs: a no-Origin control using default Python `urllib`, then
Worker and extension origins under an identical browser-*style User-Agent
header*. The latter remain Python requests and are **not** equivalent to
Chromium. If the User-Agent-only change turns 403 into 200, the response is
sensitive to that header. If not, TLS/client fingerprinting, intermediary
rules, and actual browser-request metadata remain open questions.

Each response includes only safe evidence: `server` (`cloudflare` or
`other-or-absent`), optional `cfRay`, optional `edgeChallenge`, and optional
`edgeErrorCode` (currently recognized 1010/1020). Neither arbitrary response
headers nor error-page text are serialized. `cfRay` can be matched against
Cloudflare Security Events to identify the enforcement rule without changing
Mother's route. HTTP 403 by itself does not establish which service generated
it; a missing Access-Control-Allow-Origin on a denied response is a
*consequence*, not proof that the CORS allowlist caused the denial.

Do not modify any route/Cloudflare setting based only on the Python client
comparison. Require a browser-origin failure with its actual status or a
corresponding policy event before selecting a mutation.
