# Worker browser acceptance — real Chromium and MetaMask

This is an **opt-in**, separate test harness. It does not change Worker, Wallet,
Hub, Mother, deployment, or RPC code. Uses the genuine MetaMask extension via Playwright's Chromium and the Synpress wallet-control library, without running Synpress's unsupported Windows CLI. RPC and Hub interactions are real.
It does **not** use browser provider mocks, route interception, or a fake chain.

## Requirements

- Windows 11, Python 3.12, Node.js 20+ and npm
- Main Computer web server already running at `http://127.0.0.1:8765`
- No wallet or recovery phrase required; the harness generates an isolated
  disposable test wallet. Never transfer production funds to this wallet.
- Internet access during dependency/browser/official MetaMask extension download
- A working `testnet-rpc.greatlibrary.io` and `testnet-hub.greatlibrary.io`

Commands from `C:\Users\subsi\main_computer`:

```text
python tools/worker_browser_acceptance.py plan
python tools/worker_browser_acceptance.py install
python tools/worker_browser_acceptance.py cache-wallet
python tools/worker_browser_acceptance.py run
```

There are **no mnemonic or password prompts**. `run` creates a disposable
wallet on first use and initializes an isolated persistent Chromium profile with the real MetaMask extension automatically.
`cache-wallet` is optional; it explicitly prepares the same account and Chromium profile.
Subsequent wallet-issuance runs automatically create a fresh wallet because a
previously issued multi-session key must not mask the issuance test. Worker
registration mode instead reuses the current test wallet/key.

The wallet mnemonic and its random MetaMask password are saved locally under
`runtime/state/worker-browser-acceptance/wallet.json` (git-ignored), with
restrictive permissions requested where supported. Chromium caches its encrypted extension state under `runtime/state/worker-browser-acceptance/chromium-profile/`. The extension itself comes from the official pinned MetaMask GitHub release (`v13.53.0`) and lives beside the profile.
These are **test-only secrets stored on your own workstation**. On Windows,
filesystem permissions also depend on the user's NTFS ACL. Never fund the
account with real value, check the files into Git, or share the cached profile.
The launcher prints only the generated public wallet address.

To start from scratch, remove the disposable `runtime/state/worker-browser-acceptance`
folder and the test-only `chromium-profile` directory; the next run regenerates
them. Do not modify production wallet files.

The normal `run` test performs the following real operations:

1. Starts Playwright-managed Chromium with the real MetaMask extension, importing and verifying the disposable wallet on first use
2. Opens the local Worker page immediately and chooses Testnet
3. Clicks **Connect Wallet** on the real Worker UI, which itself requests any necessary testnet switch/addition and performs its RPC proof
4. Approves only recognized network requests for Main Computer Testnet and account requests from the test-only local Worker origin
5. After connection, verifies the full imported account, actual `eth_chainId` (`42424241`) and real `eth_blockNumber` before allowing key issuance
6. Clicks **Request New Key**, approves the real MetaMask signature, verifies
   the Hub issued an active multi-session key (not just that signing succeeded)
7. Writes `runtime/reports/worker-browser-acceptance/acceptance.json`

Wallet mode uses a fresh disposable wallet on subsequent runs, so an existing
Hub-issued key cannot falsely satisfy new issuance without signing.
Worker registration mode may reuse an already-issued active key from the wallet
test; the Hub will verify it again when registration occurs.

Optional live **Worker registration** (still no purchase of credits):

```text
python tools/worker_browser_acceptance.py run --mode worker --authorize-worker-offer
```

This mode additionally enables paid-job acceptance, selects ring 3, runs a
15-minute Work Now override, asserts Hub registration status **Accepted** and
an assigned Worker ID, then requests Finish through the existing UI and
restores the original paid-policy checkbox if the test changed it. An
unexpected crash may leave the override active until its 15-minute expiry;
inspect the Worker UI and stop it manually if necessary.

**This does not claim paid-job execution or settlement certification.** A
separate authorized Captain/Data request and identity-correlated result proof
is needed for that. Never run paid commands automatically during wallet tests.

The test runs headed and serially. On failure, the harness writes a screenshot
of the Worker webpage only (not of the MetaMask extension) to the same reports
directory; logs capture limited event names, not signatures or mnemonics.
The `acceptance.json` status is the durable PASS/FAIL evidence.

If MetaMask changes its onboarding interface and wallet control fails, record the
failing tool message; don't replace the real extension with a mocked provider.
The Chromium integration cannot be fully exercised without the local Worker
server, MetaMask browser cache, and live Testnet Hub.

If extension provisioning refuses, the error specifies the failing release,
checksum, archive, or manifest validation. No production wallet is accessed;
no fake extension is substituted. If a previous test-owned extension directory
is present without a valid release proof, move aside only
`runtime/state/worker-browser-acceptance/metamask-extension` for investigation;
then `run` can provision a verified copy. The disposable wallet and Chromium
profile are separate and are not changed by extension download failures.


## Opt-in complete Testnet work cycle (live Captain request)

The existing `run` command remains **wallet-only**, and `--mode worker` remains
registration-only. To test an actual dispatched job on the browser-registered
Worker, explicitly authorize both the work offer **and** one testnet Captain
credit-bearing smoke request:

```text
python tools/worker_browser_acceptance.py run --mode work-cycle --authorize-worker-offer --authorize-captain-spend
```

`work-cycle` connects the isolated disposable wallet, verifies an active Hub
multi-session key, enables paid-job acceptance and uses the actual **Work now**
UI at ring 3. It verifies the browser Worker has a live Hub WebSocket, then
launches `python -m main_computer.cli captain smoke ... --network testnet` to
submit **one** uniquely identified request. The Captain path can deposit up to
one testnet compute credit and incur network fees; there is no automatic retry
of the credit-bearing request. It does not use production wallets or Mainnet.

Acceptance additionally requires the same Hub request and session IDs in the
Captain terminal result and the browser Worker's own last WebSocket offer and
terminal result (read from the viewport's **local, read-only** runtime-status
endpoint). Registration, successful submission, a result from another Worker,
or an active-jobs counter alone never satisfy this test. The Hub currently
chooses the matching ring-3 worker; the harness **does not force** its selection.
If a different Worker gets the job, the test refuses certification rather than
reporting success. A functional local AI and a funded Captain testnet account
are prerequisites to a full PASS.

The test writes only request/session IDs and short status proofs to
`runtime/reports/worker-browser-acceptance/acceptance.json`, never the signed
requests or issued key material. The existing `finally` block requests Finish
and restores the old paid-job setting; a crash can leave Work now enabled until
its 15-minute timeout. This test does not prove settlement, payouts, or refund.

## Native Windows fix

`npm exec -- synpress` is not invoked. That CLI refuses native Windows; the
Python launcher now starts `node native_acceptance.mjs`, which directly starts
Playwright's bundled Chromium with an official unpacked MetaMask release and
uses the Synpress **wallet interaction library** for onboarding/approvals.
The old Synpress cache-builder/Playwright-fixture files have been removed.

The pinned release is downloaded automatically once from GitHub's official
`MetaMask/metamask-extension` release during `run` or `cache-wallet`. The
launcher checks the exact tag and Chrome ZIP asset, fetches the **official
release `SHA256SUMS`**, and refuses the download unless the ZIP's SHA-256
matches that published checksum (and agrees with the GitHub asset digest,
when provided). It then checks the unpacked Chrome manifest for a supported
`manifest_version` and a valid numeric `version`. The version embedded in
`manifest.json` is logged even if different from the published release tag;
the verified ZIP, rather than an assumed manifest-version spelling, is the
release identity. A release-proof file inside the test extension directory
allows subsequent invocations to reuse the installation without downloading
again; altered/missing proof or an altered manifest fails closed. It does not access your personal Chrome or
MetaMask profile. Updating the pinned MetaMask version is a separate test
maintenance task; never use a third-party extension mirror.

This method has been checked with offline launcher tests and JS syntax tests,
but its complete live extension onboarding needs validation on Windows.
A real MetaMask release may change its UI selectors; a failure will be reported
as a test failure rather than substituted with a simulated provider.

## MetaMask v13 onboarding compatibility

The pinned MetaMask 13.53.0 has changed onboarding controls compared with the
Synpress 4 wallet importer. The native runner now uses a local, version-aware
Playwright onboarding adapter (`metamask_onboarding.mjs`) rather than the
Synpress `importWallet()` method, which incorrectly waits for the removed
`#metametrics-opt-in` control. The adapter detects the actual wallet-import
forms, treats analytics consent as an optional screen, declines analytics if
present, imports the self-generated disposable recovery phrase, and completes
password setup. It refuses to enter the recovery phrase unless the active
page has a `chrome-extension://` origin and the numbered import fields match.
It reports **only a fixed screen classification** on failure: it never dumps
phrase/password values, DOM snapshots, MetaMask screenshots, or browser traces.

Offline regression tests can be run with
`python -m pytest -q tests/test_worker_browser_acceptance_harness.py`; they
include real Node execution of the adapter against simulated screen states.
They are *not* proof of successful onboarding in the real browser. The next
Windows run still needs to establish that MetaMask's pinned build exposes the
expected controls. Later Synpress network and permission-screen methods may
also be incompatible with v13; an onboarding PASS does not certify those.

Do not alter the extension ZIP or bypass its release checksum to accommodate
UI changes. Rerunning the launcher after an unsuccessful first-time import
will reset only the disposable Chromium profile if onboarding did not verify the
new disposable wallet's public address. The wallet marker attests to that
wallet identity, **not** testnet readiness or Worker acceptance: the Worker
page itself negotiates the testnet after Connect Wallet, and the acceptance
report records chain/RPC success only after the real provider checks pass.

### MetaMask 13.x fresh-install onboarding

For a new isolated Chromium profile, the harness now prefers the `home.html` onboarding
page opened by MetaMask itself. If there is no onboarding tab, it explicitly opens
`chrome-extension://<verified-extension-id>/home.html#/onboarding/welcome` instead of
opening `home.html` without a route and guessing which screen was rendered.
An already certified profile retains its normal wallet-home path.

MetaMask can display a Terms of Use dialog before the wallet-import controls;
the browser runner scrolls the dialog until **Agree** becomes enabled, then
continues with the existing disposable-wallet import. It does not bypass the
wallet's UI, preauthorize the Worker, or consider a profile certified until
wallet onboarding and test-network configuration complete successfully.

On an unfamiliar screen, stdout reports only a fixed screen category, whitelisted
route class, loading state and up to 12 recognized `data-testid` identifiers.
The harness does **not** save extension screenshots, raw DOM, traces, field values,
recovery phrases or passwords. A failed import never writes the certification
marker. No credentials from a personal or production MetaMask profile are used.

Offline coverage exercises extension-opened onboarding tabs, fallback to the
canonical route, retained wallet-home navigation, the Terms of Use scroll/agree
sequence, and safe diagnostics. Real MetaMask acceptance must still be run on
Windows with the locally verified release archive.

## Worker-first network acceptance

MetaMask 13.53.0 has removed the old `network-display` element used by
Synpress's `addNetwork()` and `switchNetwork()`. Those pre-Worker calls have
been removed. They were redundant: the actual Worker application's Connect
Wallet handler already performs `wallet_switchEthereumChain`, then
`wallet_addEthereumChain` when necessary, and validates its own network/RPC.
The harness tests **that implementation**, rather than configuring the wallet
behind the Worker's back.

The acceptance runner opens the local Worker page as soon as it has positively
verified the test-only wallet address. On connection it handles known real
MetaMask network-add, network-switch, and local account-connection prompts,
never a generic signing or transaction confirmation. Unknown dialogs cause a
refusal, not a silent approval. After the Worker reports connected, the harness
independently checks the full account against the generated wallet, the exact
chain ID, and a real block-number response before proceeding to multi-session
key issuance. Wallet-home screenshots alone are not passing evidence.

To exercise the isolated network approval state machine offline, run:

```text
python -m pytest -q tests/test_worker_browser_acceptance_harness.py
```

The live Windows acceptance run is still the required authority. MetaMask may
change the text or route of approval popups; if a prompt is unrecognized, the
harness will time out without authorizing unrelated operations.

### MetaMask 13.53 network-add approval and connection diagnostics

The v13.53 **Add Main Computer Testnet** request shown by the real Worker
may present a **Confirm** button, not an **Add network** button. The approval
adapter now recognizes that screen even when Chrome displays it in an extension
side panel. It will click Confirm *only* when it can verify all of the following
from the actual extension UI: the Main Computer Testnet network name, the local
127.0.0.1:8765 or localhost:8765 requester, and the
`testnet-rpc.greatlibrary.io` RPC host. The actual chain ID is separately
verified using the injected MetaMask provider after the Worker connects. Other
signing and transaction confirmations remain outside this adapter.

The Worker's button may continue displaying **Connect Wallet** while network
approval is still pending. The harness no longer treats that label alone as
failure after three seconds. Instead it observes the Worker status and its
`[worker-wallet] connect.*` event names. A genuine `Connect failed:` status is
reported, while an inaccessible/unrecognized MetaMask popup produces a safe
page-type and predicate-count diagnostic. The harness does not copy extension
page text, prompts, hidden values, wallet phrases, passwords or signatures.

An actual Windows run is still required to establish that Playwright can
interact with this Chrome installation's MetaMask side panel and that the RPC
can return a block number. If the panel is inaccessible to Playwright, the
harness refuses and reports that boundary rather than fabricating success.

## Approval diagnostics

During **Connect Wallet**, the harness subscribes to the Worker's existing
`main-computer-worker-wallet` events and reports **event names only** (for
example `connect.wallet.addChain.start`, `connect.wallet.rpcProof.done`,
`connect.ethers.requestAccounts.start`, or `connect.failed`). It does not log
or save event details, provider parameters, signatures, passwords, or recovery
words. General-purpose status labels are supplemental, and unrecognized
labels are not reported as the misleading `worker.other` phase.

If an approval cannot be found, the failure report classifies both Playwright
extension pages and any Chrome DevTools extension targets visible to the test
browser. Target identifiers, full URLs, DOM content, and wallet secrets are
not retained. A side panel visible to the user might not appear in
`context.pages()`; a CDP target classification helps diagnose that boundary
without clicking a generic confirmation or bypassing approval. The harness
still refuses to certify a wallet until the real account, chain, and RPC proof
are complete.
