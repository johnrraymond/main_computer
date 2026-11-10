# P5.4g — Worker-page offer registration protocol decision

## Decision

**Migrate the Worker page's `Register / Update Offer` action to authenticated
Hub Worker registration.** Preserve the older REST route for explicitly supported
legacy Hub and operator/lab integrations until independently retired. Do not
implement this migration by directly invoking `Work now`, and do not delete any
legacy REST route in this patch.

This phase is a **read-only protocol decision and acceptance gate**. The
registration migration is not yet implemented. It neither starts Workers nor
contacts a Hub. Run `python -m tools.hub_offer_protocol_decision` to verify the
source-level contract.

## Evidence from current source

The Worker page's `registerWorkerOffer()` in
`main_computer/web/applications/scripts/worker.js` submits to
`/api/applications/worker/register-offer`. The Viewport backend in
`main_computer/viewport_routes_energy.py` verifies that the supplied URL
matches saved Worker network selection, then POSTs to
`/api/hub/v1/workers/register`. P5.4f therefore solved **destination
selection**, not **transport support**.

The FDB Dev-Test Hub explicitly rejects the obsolete REST register route.
The Stable Hub's authenticated Worker registration is an exchange over
`/api/hub/v1/workers/live-session`: `worker.auth` / `hub.auth.accepted`, followed
by live-session verification. The older offer POST does not implement that
protocol. A matching canonical Hub URL cannot make an unsupported REST method
work. Dev-Test also retains a distinct `/api/hub/v1/workers/connect` compatibility
route for the existing *Work now* setup path; this is not the same endpoint as
obsolete `/workers/register`, nor evidence that all networks share one protocol.

`Work now` has a different objective function: it temporarily overrides the
ordinary idle gate and immediately registers a Worker over the authenticated
path. Its Stable Hub registration method initially advertises
`phase="accepting"` and `allowed_to_accept=True`. An ordinary seller-offer
update must **not** inherit this temporary work authorization. Simply calling
that handler from the old offer button would introduce a permission and
availability regression.

## Required next functional patch

1. Preserve the existing Worker-page control and shared settings. Build one
   normal registration/update operation behind it; do not change the selected
   network, or create a second configuration.
2. Validate the saved selected network, canonical Hub identity and chain;
   reject mismatches before opening a transport.
3. Require an authenticated wallet and active Hub-scoped MSK; reuse the
   established live-session handshake and verify Hub-issued Worker identity,
   ring and registered market offer.
4. Distinguish *registering an offer* from *allowing the local machine to
   execute paid work right now*. No Work-now override may be set or extended
   during normal offer updates. If the worker is not idle, its capacity must
   not be advertised as accepting work until ordinary local policy permits it.
5. Support rejection, reconnect, repeated updates, stale keys, network changes
   and active-work drain safely. Never fabricate a registration receipt from
   local persistence alone, and never silently fall back to REST on an
   authenticated network.
6. Verify Dev, Test, Testnet and Mainnet behavior with isolated live-session
   transports and explicit Hub authorization; retain the legacy REST API and
   external consumers pending a separate retirement decision.

## Acceptance for this read-only phase

```powershell
python -m tools.hub_offer_protocol_decision
python -m pytest -q tests/test_hub_offer_protocol_decision_p54g.py tests/test_hub_worker_offer_routing_p54f.py
```

`ok: true` means the current source evidence has been verified. It **does not**
mean the Worker page's offer-registration migration is complete or the old
button can register successfully with Stable or FDB Dev-Test Hubs.
