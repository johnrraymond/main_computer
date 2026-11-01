# SLH Pinky Promise: Idle-Compute Funding and Local Secure Model Access

**Status:** Governing economic architecture and implementation contract. P3 adds an atomic, durable, fixed 0.1-credit **assessment ledger** for locally completed P2 secure-model requests. This is **not** evidence of an organization-wallet debit, a live Reserve bridge, or a delayed settlement contract. The existing local paid-work lab uses synthetic funds; see [SLH laboratory exit](slh-seh-laboratory-exit.md) and [P3 verification](slh-pinky-p3-testing.md).

**Authority:** This document defines the meaning of **Pinky Promise** for Secure Local Hub (SLH) organizations. Earlier references to a generic "pink-promise charge," a fee for every local route ticket, a floating market-price lookup, or an automatic percentage withheld from public marketplace earnings must be interpreted according to this specification. The current target pricing rule is **fixed-per-request v1**, not dynamic quote discovery.

## Thesis and two separate transactions

An organization operates its own authenticated SLH, with enrolled **Workers** that supply compute and **Requesters** that consume authorized compute. The scheduler protects local demand and permits eligible *idle* Worker capacity to earn credits by selling compute through the existing Stable Hub marketplace. The SLH controls an **organization marketplace wallet address**; physical Workers do not need to hold marketplace wallet keys.

That earnings activity is **not itself the Pinky Promise payment event**. Earnings accumulate under the organization wallet, subject to real Hub settlement, fees, holds, and withdrawal/bridge rules.

The **Pinky Promise** becomes due when a requester obtains **secure model access successfully fulfilled locally by an organizational Worker**. The local job is not routed through the public marketplace. For a simple, stable reference, **one standard open-market request is assigned a reference value of 1 credit, associated with 1,000 output tokens**. The intra-organization Pinky Promise assessment is **10% of that fixed reference value = 0.1 credit per qualifying completed local request**. The organization, not the individual requester or Worker, owes this contribution to the **Main Computer Reserve** and funds it from its organization-controlled credits. Public-market earnings are a separate transaction.

| Activity | Trigger | Economic effect |
| --- | --- | --- |
| Public sale of idle organizational compute | A Stable Hub job is authorized, performed, and financially settled | Earn available credits for the SLH organization wallet under the actual Hub ledger; **no Pinky Promise assessment merely for earning** |
| Private secure model access inside the organization | An authenticated qualifying request completes with a verified local service result | Commit **0.1 credit per request** (10% of the fixed 1-credit reference price); neither prompt nor response is disclosed to the public Hub |
| Pinky Promise sweep | Periodic SLH timer finds accumulated unpaid obligations and spendable wallet credits | Make one aggregate, idempotent Hub-ledger transfer to the configured Reserve-controlled wallet |

This is **not** a universal 10% tax on all local SLH traffic, not a 10% slice of public Worker earnings, and not a charge to the local requester unless the organization separately adopts a requester-billing policy. A request is an authorized, distinct secure-model service transaction, not a transport packet, streaming chunk, retry, session renewal, or route ticket.

## Fixed reference price — policy v1

| Unit or event | v1 policy value |
| --- | ---: |
| Open-market **reference** for one standard request | **1 credit** |
| Reference output quantity | **1,000 output tokens** |
| Pinky Promise rate | **10%** |
| Pinky Promise assessment for one qualifying local request | **0.1 credit** |
| Ten separate qualifying completed local requests | **1.0 credit total** |

The 1,000-output-token figure anchors the reference price; it is **not** a requirement to count output tokens, submit prompt content, obtain a real-time external quote, or charge a new 0.1 credit for every additional 1,000 tokens within the same request. For **v1**, each qualifying completed intra-organization request creates **one 0.1-credit obligation**, regardless of its actual output-token count. A future change for unusually large requests, request limits, or token-based billing requires an explicit **versioned policy revision**, not an implicit multiplier. The 1-credit reference is the accounting convention for this Pinky Promise, not an assertion that all live public marketplace offers currently charge exactly 1 credit.

Record the **pricing policy identifier/version**, fixed 1-credit reference, 0.1-credit assessment, request ID, successful-fulfillment evidence and obligation ID. Do **not** build a per-request market-price discovery service or depend on a live rate quote to assess v1. Exact decimal or integer minor-unit arithmetic must represent 0.1 credit without floating-point drift; verify that the authoritative credit system supports the selected unit before allowing settlement.

## Default operating behavior and organizational choice

**Intended product default after authorized setup:** Keep the Pinky Promise **enabled at 10%** and make an administrator-approved portion of otherwise idle Worker capacity eligible for marketplace work. This provides a normal way for the organization to acquire the credits needed for secure model access. The scheduler should be capable of adapting the public idle allocation to the organization's verified earnings, outstanding obligations, and resource constraints rather than assuming all machines or all idle time can be sold.

**The organization may explicitly remove/disable the Pinky Promise through an authorized administrative policy decision.** This is a deliberate choice, not an automatic consequence of insufficient wallet funds. Disabling it affects *future eligible local services* from the effective policy revision onward; it cannot erase obligations already committed. The administrator must see the consequences, including loss of the normal Reserve-contribution arrangement, before approving the change. Whether other Main Computer service entitlements then change is a separate service-policy decision, **not specified or enforced here**.

The economic default creates a strong reason to contribute idle compute; it is not an assertion that physically idle machines must work, that every organization will earn enough, or that buying/transferring credits from another authorized source is impossible. Worker opt-in/eligibility, device security, administrator consent, actual capability, job reservations, private holdout, and marketplace authorization remain controlling constraints. Do not expose Worker capacity or a wallet secret simply because this is the intended default.

**Current implementation distinction:** The existing Organization Service marketplace controller is **disabled by default** until the administrator configures participation and installs proper Hub authorization. Existing scheduler defaults (`private_first`, `max_public_percent=75`, `min_private_idle_percent=25`) are capacity limits, **not** a Pinky Promise balance-target mechanism and not a guarantee of marketplace sale. The earning-by-default policy described above is a *target for configured organizations*, not a statement that the current service already automatically opts organizations into public trading. See [scheduler policy](slh-scheduler-policy-phase2d.md) and [integrated lab](slh-seh-integrated-hub-lab.md).

## End-to-end transaction flow

```text
Enrolled organizational Workers -- idle capacity / scheduler --> Stable Hub paid market
                         |                                       |
                         |                           finalized Worker earnings
                         |                                       v
                         |                         SLH organization wallet
                         |                                       |
Authenticated local Requester -> SLH -> eligible private Worker     |
                         |              |                         |
                         |      secure model result               |
                         v              v                         |
                  fixed 1-credit reference                         |
                         |                                        |
              Pinky Promise = 0.1 credit                           |
                         |                                        |
                  durable obligation                               |
                         |                              periodic SLH sweep
                         +-------------------------------+--------+
                                                         |
                             one signed, idempotent aggregate transfer
                                                         |
                                                         v
                                Reserve-controlled credit account (Hub ledger)
```

The **SEH** is a remote transport relay when needed. It is not a pricing authority, marketplace wallet custodian, issuer of organizational obligations, or recipient of the Pinky Promise. Private prompts, model weights, and responses remain within the authorized private execution path.

## Pricing, commitment, and settlement contract

1. **Authorize private access.** Authenticate the requester and permitted model/capability, reserve the actual local Worker, and bind a unique request ID. The request must be identified as *locally fulfilled secure model access*, not simply any local network packet or a ticket that was issued but never used.
2. **Apply the fixed reference schedule.** Use the versioned `fixed_per_request_v1` policy: one standard market-reference request is 1 credit (reference 1,000 output tokens), and each qualifying local completed request is assessed 0.1 credit. Record the policy version and price constants with the request; **no live market quote, model-by-model price matching, or token metering is needed for v1**. If the pricing policy is unavailable or unrecognized, fail closed rather than invent a price.
3. **Commit on verified fulfillment.** Once the service reaches its defined successful/chargeable terminal state, commit exactly one assessment with the versioned fixed-price policy snapshot. Authentication or route issuance alone does not generate a completed-use payment. Failed, refused, timed-out, or cancelled requests require an explicit no-charge/uncertain-outcome rule; never equate an unknown outcome with successful fulfillment.
4. **Compute the obligation.** `pinky_promise_due = 0.1 credit × qualifying_completed_request_count`; one unique completed local request contributes exactly `0.1 credit`. Use deterministic fixed-point or integer minor-unit arithmetic, not a floating-point accumulation or a dynamic quote. This is an organizational liability, *not* a debit from the requester's account unless separately agreed.
5. **Accumulate locally.** Every successful eligible request commits exactly 0.1 credit to the P3 obligation journal. No payment is initiated for that request. The balance of unpaid obligations accumulates until a periodic sweep.
6. **Sweep periodically.** The Organization Service checks every 300 seconds (and on service start). With an authorized wallet signer and a configured Main Computer Reserve destination, it selects whole unpaid 0.1-credit obligations up to its freshly reconciled spendable Hub balance and submits **one aggregate transfer**. If short of funds, the remainder stays due. There are no per-request payments, notices, or marketplace price inquiries.
7. **Update the existing Hub credit bridge/accounting.** A wallet-signed aggregate instruction to the Stable Hub debits the organization account and credits the configured Reserve account atomically in the authoritative Hub ledger, using a stable sweep ID. The Hub returns its recorded receipt; only then does the SLH mark the batch confirmed. A missing response leaves a pending batch for retry with the **same** sweep ID. This internal credit transfer is not a blockchain withdrawal or proof that an external XLag Reserve smart contract received funds. The internal Reserve-controlled account must be configured and its ownership validated by the operator. No credit burn or extra settlement contract is implied.

**Illustrative accounting (P4 rule, not production payment attestation):** The organization has **500 settled credits** earned selling idle compute. It fulfills **20 separate qualifying local secure-model requests** without buying them on the public marketplace. At **0.1 credit each**, the SLH commits **2 credits total** in Pinky Promise obligations and, after authorized periodic sweep, transfers **2 credits** to the configured Reserve account, leaving **498 credits** before other activity. A **single** qualifying request would instead contribute **0.1 credit**, not 2 credits. These contributions are not withheld from public earnings and do not debit the local requesters.

## Trust, accounting, and failure invariants

- **Separate identities:** requester, physical Worker, organization, marketplace account/wallet address, and Reserve are distinct parties. Workers never receive the organization's marketplace private key or signing authority for the Reserve payment.
- **Two ledgers / one reconciliation:** marketplace earnings and public charge settlements are authoritative at the Stable Hub/underlying credit system; local fulfilled-use obligations are authoritative in the SLH's durable private-use journal until confirmed and reconciled through the authorized bridge. Journal entries cannot mint external credits.
- **No private-data disclosure:** accounting evidence may include request/obligation IDs, successful-fulfillment status, pricing policy version, fixed reference price and 0.1-credit assessment, but not the confidential prompt, response, hidden Worker data or private model artifacts. No token-content inspection or external price query is necessary. External Hub sees only the public paid-work and necessary aggregate settlement/payment records.
- **No phantom work or fees:** unpublished private usage cannot be cryptographically proven by the public Hub. The organization explicitly undertakes to report eligible successful use honestly; this honesty commitment is why the name **Pinky Promise** matters.
- **Exactly once at each boundary:** one fulfilled request creates at most one obligation. Many obligations map to one sweep. One sweep ID creates at most one Hub-ledger debit and corresponding Reserve credit. The SLH retries a pending batch without changing its ID or amount; errors never mark local obligations paid.
- **Insufficient funds and outages:** hold the obligation as pending with age and amount; do not spend the same available credit twice, debit a Worker wallet, fabricate a transfer, or silently claim the Promise was fulfilled. Service admission restrictions or debt grace-period policy require separate explicit specification.
- **Local priority:** already-authorized public jobs are not interrupted to service local work; public capacity is limited to scheduler-eligible idle resources with the configured private reserve and resource protection.

## Laboratory acceptance still required

The existing paid-work lab demonstrates synthetic-funded Hub credit settlement. P2 and P3 implement private computation and fixed assessment; **P4 implements periodic aggregate transfers into the configured Reserve-controlled Hub account**, with focused tests. Full real-service economic acceptance is still required before the fail-closed [laboratory-exit](slh-seh-laboratory-exit.md) gate can be cleared. There is no dynamic market quote or per-request Reserve transfer in v1.

Require at minimum: one successful qualifying local request with exactly **0.1-credit** obligation and one Reserve receipt; ten distinct successful requests with exactly **1.0 credit** total obligation; token counts above and below 1,000 **without repricing the same request**; public earnings funding a previously insufficient wallet; a request failure with no fee; retry/replay of the same local request with no duplicate charge; crash before/after assessment and transfer submission; Hub downtime and eventual confirmation; an attempted transfer with inadequate spendable balance; administrator removal of the Promise with prior commitments preserved; unknown or missing pricing-policy revision fails closed; and a privacy check that no local content crosses SEH or public Hub. Some have focused P2–P4 tests; the complete multi-Hub Windows economic acceptance remains **NOT ACCEPTED** until it is executed and evidenced.

## Related specifications

- [SLH Administrator and Operator Manual](secure-local-hub-administrator-manual.md)
- [SLH scheduler and private holdout](slh-scheduler-policy-phase2d.md)
- [Managed Worker/Requester specification](secure-worker-managed-mode-spec.md)
- [SLH–SEH integrated paid-work lab](slh-seh-integrated-hub-lab.md)
- [Compute Credit settlement architecture](hub-compute-credit-settlement-phase0.md)

## P4 implementation: periodic aggregate Pinky Promise sweep

**Implementation state (October 31, 2026):** The Organization Service now runs a five-minute batch sweeper when its private Worker service is active. P3 assessment does not contact the Hub. The sweeper reads settled spendable balance from P1's authenticated Hub wallet binding and collects up to 1,000 whole unpaid obligations per sweep, never more than the available balance. The Hub performs a wallet-signed atomic transfer from the organization wallet account to the **configured Reserve wallet account** in its existing credit ledger. Both balances and an idempotent receipt are recorded there; the SLH stores the receipt and paid-item mapping locally. No Worker, Requester or other organization receives an announcement.

**Explicit operator configuration, required before the first transfer:**

- `MAIN_COMPUTER_RESERVE_WALLET_ADDRESS`: the real, administrator-confirmed Reserve-controlled wallet address. Set the same address in the Stable Hub process and the SLH Organization Service environment. The implementation does not invent a Reserve wallet or choose an arbitrary destination.
- `SLH_PINKY_SWEEP_SIGNER_FILE`: on the SLH machine only, the path to the protected hexadecimal private key for **the exact organization wallet already bound in P1**. The Worker/MSK authorization is not a spending key and is not reused for transfers. Keep this file out of the repository, logs, Workers and SEH; lock its permissions to the Organization Service account. A signer must be explicitly provisioned by an authorized administrator; the SLH never generates a substitute key.

When either parameter, valid wallet authorization, correct signer, or a reachable Hub is missing, no transfer is made. The debt remains outstanding. After initial configuration the sweeper runs unattended. Administrator-only `GET /v1/accounting/sweeps` reads its local status; `POST /v1/accounting/sweep` requests a sweep immediately for diagnostics, without changing the normal cadence. Both use the organization's existing device-session authorization. These are local accounting controls, not notifications to Requesters.

**Accounting boundaries:** `CONFIRMED` means the specified Reserve-controlled wallet has received credits on the authoritative Stable Hub ledger, not that an on-chain XLag contract has received them. A policy requirement for actual chain settlement would be a separate explicit capability and must not be inferred from this receipt. The existing Hub's atomic backend protects the shared balances; the stable sweep ID protects retries and crashes. The private SQLite sweep journal does not become a competing source of spendable credit. Pending batches take priority over preparing new ones. The P0 laboratory-exit blocker remains in place pending integrated Windows/failure/three-Hub acceptance.
