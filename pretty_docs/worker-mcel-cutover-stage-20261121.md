# Worker MCEL: marketplace parity implementation and cutover gates

Baseline: `main_computer_test-20261121-125716.zip` (2026-11-21).

## Scope of this patch

This changes the native `/applications/mcel-semantic-worker` **candidate**, not the canonical legacy `/applications/worker` route. The existing wallet, EIP-1193/MetaMask, multisession key (MSK), request signing, offer synchronization, and Work Now authority remain in `worker.js` and the existing server-side endpoints. The new semantic MCEL operations are bounded adapters, not a new signer.

Hub Choice and the separate Worker network session remain distinct. Wallet and MSK setup stays on marketplace Hub Choice. Mother identities and SLH enrollment are not modified.

### Worker workspace

- Seller policy: explicit Seller Enabled and availability mode fields, independent of Requester fields.
- Seller offer: models, target output tokens, credits per token, maximum concurrency, capability, and execution mode; saved via exact `changed_fields` through the existing Worker settings API.
- Register / Update Offer: invokes the original signed offer-registration handler and checks accepted/deferred/pending Hub result. Requires matching chosen/connected marketplace Hub and seller enabled. Preserves persisted node ID and endpoint rather than overwriting them with retired hidden controls.
- Work Now: typed duration (minutes), reusing the existing MSK-recovery and retry sequence and requiring confirmed backend acknowledgment.
- Finish Work Now: sends the original finish-after-current-request operation through the legacy handler under the same chosen/connected Hub check.
- Pause Selling: disables new paid work through existing settings; it does not claim an active job has been terminated.
- Marketplace inspection: connection, registration, Worker ID/ring, phase, active jobs, heartbeat, error, override expiration/remaining time, with an active override refreshed every five seconds and idle inspection every thirty seconds while the page is visible.
- Hub control: retry and disconnect are exposed as explicit operations; merely choosing a Hub is still not connection or authentication.

### Requester workspace

- Remote enablement and mode, price ceiling, token ceiling, daily limit, ask-before-spend, and only-when-busy settings, scoped to seven exact changed fields.
- Existing backend normalization persists these values. The current server does not itself prove end-to-end enforcement of every Requester budget/consent rule; acceptance must test actual remote job admission before claiming budget guarantees.

### Acceptance evidence in automated tests

- Generated semantic app compiles through MCEL P3, presentation inheritance and identity rail preserved.
- Exact operation/capability contracts restrict unknown inputs and bounded seller/Requester values.
- Simulated signer verifies registration, duration conversion, finish, and mismatched-Hub refusal.
- Browser verifies native forms, settings hydration, scoped saves, role visibility, zero startup writes.
- Existing MCEL wallet, identity, Hub Choice and presentation regressions.
- Legacy signed-registration backend unit tests (no changes to authority implementation).

## NOT YET certified — do not cut over

1. Real MetaMask connection, MSK issue/expiration/recovery, offer registration and deferred-to-accepted transition against each intended actual Hub.
2. An authorized, billable Work Now cycle, completion, active-work drain, and live runtime countdown using actual network state.
3. Requester spending-limit and consent enforcement through an actual remote purchase, including budget-exhaustion refusal.
4. Actual reconnect/disconnect under Hub and wallet failures, and confirmation that no mismatched network is allowed to sign or charge.
5. Full differential legacy/native browser acceptance and rollback of the canonical registry/route if promotion fails.

Cutover criterion: every required legacy workflow has a native success and refusal proof, or explicitly approved retirement. The legacy route and its signers remain available for rollback. Do not infer real-money or real-wallet acceptance from browser mocks.
