# Wallet — canonical native MCEL treasury

The declarative application source is `application.js`. The canonical `/applications/wallet` shell child is rendered from its P3 semantic composition and uses the shared presentation controller. No incumbent Wallet UI is required.

```mcel-app
id: wallet
title: Wallet
status: canonical-cutover
current_runtime_status: structural-only
primary_user_goal: Monitor and transfer native funds and Hub Compute Credits for user and ORG-SLH wallets across Mainnet, Testnet, Test, and Dev.
```

```mcel-use-case
id: wallet.use-case.inspect
app: wallet
user_goal: See all authorized browser accounts, cached public wallet addresses, watched addresses, and authenticated ORG-SLH wallets across configured networks.
acceptance: Show distinct network and Hub scope, current balances, held credits, cumulative bridge deposits, on-chain escrow where configured, transaction history and explicit unavailable states.
```

```mcel-region
id: wallet.region.generated
app: wallet
role: generated-application-surface
responsibility: Generate portfolio, wallet details, source and destination selection, ORG-SLH identity and target selection, transfer review and receipt evidence.
```

```mcel-requirement
id: wallet.requirement.authority
app: wallet
type: safety
requirement: A read proof or MSK cannot spend. A managed ORG signer must be explicitly configured, match the verified ORG binding, and remain inside its service. Native sends require the matching browser account and verified chain checkpoint.
acceptance: No debit without exact network/cluster scope, recipient and amount, explicit confirmation, matching signature and atomic Hub accounting; no cross-network ledger transfer.
```

```mcel-intent
id: wallet.intent.send-credits
app: wallet
risk: consequential
requires: Fresh observation, exact amount and recipient, user review, source-wallet signature.
produces: Hub-verified receipt or chain receipt, or an explicitly unverified result; Hub retries reuse the original signed intent and expired uncommitted intents are atomically sealed.
```

```mcel-acceptance
id: wallet.acceptance.candidate
app: wallet
requires:
  - Valid native semantic P3 composition without authored HTML
  - Canonical semantic shell registry and route
  - Separate observation and consequential transfer capabilities
  - Explicit confirmation for transfer and retry
  - Wallet-scoped balances, history and bridge evidence
  - Disposable wallet tests and explicit distinction from live acceptance
```

```mcel-finding
id: wallet.finding.promotion
app: wallet
status: canonical-cutover
finding: Canonical route is implemented. Live acceptance requires updated deployed Hubs and ORG-SLH services plus real wallet authorization; this source snapshot cannot establish those facts.
```

## Runtime authority

The network registry is the only source of user-wallet RPC and Hub destinations. Test and Testnet must be kept distinct even though they share a chain ID. The browser can only enumerate accounts its provider exposes; saved addresses and explicit watch addresses are monitored without importing keys. Local ORG-SLH wallet discovery reads the installation-owned persisted binding in read-only mode, without identity selection or starting ORG-SLH. The current organization store has a singleton binding, not one independent binding per network. The app shows the saved address even when live Hub reads fail. Live local inspection uses only the matching existing Hub read credential; no credential is exposed to the browser. Remote ORG-SLH wallets come from authenticated marketplace bindings. Known direct TLS-pinned ORG-SLH targets are discovered and additional targets can be entered with an independent pin check. New ORG spending authority can be enabled by configuring `MAIN_COMPUTER_ORG_WALLET_TRANSFER_SIGNER_FILE` in that ORG-SLH process to a protected, matching EVM private-key file. It is not a browser secret or an MSK. The Pinky-specific signer is not implicitly authorized for general transfers.

The Stable Hub must run the accompanying ledger changes. Escrow deposited, completed, rectified, withdrawn and remaining units are chain observations, not additional spendable credits. Only available Hub credits are transferable; held credits remain protected. No cross-network bridge, autonomous escrow withdrawal or native server-side ORG key export is implied. Native sends use the browser signer and network checkpoint. Native transaction history beyond submitted receipts requires a chain indexer and is not inferred from balances.
