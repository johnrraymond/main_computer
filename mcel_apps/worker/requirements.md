# Worker — MCEL migration candidate

`application.js` is the sole native semantic application source; generated composition is the layout authority. A bounded local-only observer projects existing Worker settings, runtime and network destination. The only navigation destinations are Hub Choice, Worker, and Requester. Hub Choice contains exactly six mutually exclusive radio choices: None, Mainnet, Testnet, Local Test, Local Dev, and SLH/SEH. Selecting SLH/SEH reveals a separately configured managed SLH/SEH HTTPS Hub endpoint. The selection is local until an explicit Apply Hub choice request; saving the endpoint is a separate consequential operation using a single allowed settings key. Only explicitly requested operations have consequential authority. Identity selection does not enroll or authenticate a device. The signed enrollment bridge is request-only: the selected LMS identity requests Worker admission to the local SLH or an already paired direct SLH / SEH endpoint, and the Worker observes Pending / Denied / authenticated role from the organization authority. The Worker never offers an administrator credential input, key redemption, or direct approval. The separate SLH administrator application owns authorization. A verified login receipt is not proof of a persistent managed Worker connection. The canonical Worker UI has been promoted; live approval acceptance remains a separate requirement.

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
  - Worker enrollment is request-only; the SLH notebook owns approval
```

```mcel-finding
id: worker.finding.cutover
app: worker
status: candidate-only
finding: Native Worker is promoted; the signed enrollment bridge is implemented, with live approval and managed registration requiring distinct verification.
```
