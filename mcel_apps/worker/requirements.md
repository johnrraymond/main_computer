# Worker — MCEL migration candidate

`application.js` is the sole native semantic application source; generated composition is the layout authority. A bounded local-only observer projects existing Worker settings, runtime and network destination. The only navigation destinations are Hub Choice, Worker, and Requester. Hub Choice contains exactly six mutually exclusive radio choices: None, Mainnet, Testnet, Local Test, Local Dev, and SLH/SEH. Selecting SLH/SEH reveals a separately configured managed SLH/SEH HTTPS Hub endpoint. The selection is local until an explicit Apply Hub choice request; saving the endpoint is a separate consequential operation using a single allowed settings key. Only explicitly requested destination, policy, and SLH target updates have consequential authority. Identity and SLH worker enrollment **are not implemented** by this candidate; choosing SLH does not constitute enrollment. Marketplace wallet signing, funding, registration and multitask work execution remain in the incumbent Worker pending typed-port migration and acceptance. The canonical `/applications/worker` must not be promoted until the missing bridges are closed.

```mcel-app
id: worker
title: Worker
status: migration-candidate
current_runtime_status: structural-only
primary_user_goal: Inspect and deliberately configure a local Worker without accidentally invoking a credential or payment authority.
```

```mcel-use-case
id: worker.use-case.observe
app: worker
user_goal: See current destination, runtime and policy from a local, non-mutating observation.
acceptance: No Hub probe, signed connection, wallet, SLH admin privilege, or enrollment is performed.
```

```mcel-region
id: worker.region.generated
app: worker
role: generated-application-surface
responsibility: Generate a three-destination navigation rail, Hub Choice radio workspace, conditional SLH/SEH endpoint form, Worker policy workspace, Requester context, and supporting observations from semantic states and intents.
```

```mcel-requirement
id: worker.requirement.authority
app: worker
type: safety
requirement: An SLH destination does not verify the identity, an authenticated session, or a registered Worker.
acceptance: The observer reports SLH enrollment NOT_VERIFIED; the port refuses undeclared operations.
```

```mcel-intent
id: worker.intent.select-destination
app: worker
risk: consequential
requires: An explicitly selected observed destination and requested ring.
produces: Typed destination receipt and refreshed server observation.
```

```mcel-acceptance
id: worker.acceptance.candidate
app: worker
requires:
  - Valid generated P3 composition with no authored Worker HTML layout
  - Typed Worker capability boundaries for read, destination, policy, and a single-field SLH target update
  - Local-only non-mutating Worker observation, excluding credentials and private keys
  - No canonical Worker registry promotion until identity, SLH and marketplace bridges pass
```

```mcel-finding
id: worker.finding.cutover
app: worker
status: candidate-only
finding: Native generated presentation works; full marketplace parity, SLH enrollment and identity authority are not yet migrated or certified.
```
