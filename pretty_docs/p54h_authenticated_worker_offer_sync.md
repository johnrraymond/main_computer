# P5.4h — Authenticated Worker Offer Synchronization

## Functional change

The Worker page's **Register / Update Offer** action is no longer a REST
registration client. The previous Viewport POST route
`/api/applications/worker/register-offer` and its direct Hub REST forwarding
helper have been deleted. The page now POSTs to
`/api/applications/worker/offer-sync` using the selected Worker network,
a wallet, the selected ring, and a Hub-scoped active multi-session key (MSK).

On **Mainnet / Testnet**, the existing Stable Hub authenticated WebSocket
`worker.auth` / `hub.auth.accepted` and accepted ping/pong are used. On
**Dev / Test**, the existing authenticated Dev-Test `/workers/connect`
compatibility pathway is used. No new Hub-side registration protocol is
introduced, and no live Stable Hub or SLH deployment is changed.

## Availability and authorization

The backend saves the desired Worker offer independently of registration.
Only **normal** local Worker availability (`normal_allowed`) permits this
action to advertise the offer; the Work now temporary override is neither
created nor used as authority for an ordinary offer update. If normal
availability blocks, the action records a **deferred** offer. The existing
Worker runtime supervisor can synchronize that pending offer when normal
availability later permits it. The Work now button and its bounded timer
remain separate and unchanged.

An authenticated Hub receipt, verified Hub-provided Worker ID and assigned
ring, and the selected network's wallet/MSK identity are required before
the backend records acceptance. Cross-network Hub URLs, network/chain/ring
mismatches, and absent authorization fail before registration. Active jobs
block market replacement, including a job that begins as the existing live
socket is being closed. A failed or changed authorization must not be
recorded as a successful registration. A failed WebSocket authentication
closes the new live session where present.

Dev-Test compatibility registration uses an HTTP request and does not
provide a new atomic revoke-on-lost-response protocol in this patch.
Local integration tests prove normal successful behavior; they cannot
eliminate every network-failure window in a remote deployment. Do not
interpret a local test pass as proof of mainnet paid job completion.

## Compatibility scope

The old **Worker-page Viewport** REST registration route is removed. The
separate legacy Worker REST protocol/client/CLI and the development/lab
consumers identified by P5.4a–g are deliberately retained. They are not
silently rerouted to Stable Hub or deleted without consumer migration.
The Dev-Test Hub's explicit rejection of old REST registration remains.

## Windows acceptance

From the repository root, with Main Computer running if doing live UI
acceptance:

```powershell
python -m tools.hub_offer_protocol_decision
python -m tools.hub_worker_offer_route_verify
python -m pytest -q `
  tests/test_worker_offer_authenticated_p54h.py `
  tests/test_worker_app_registration_proxy.py `
  tests/test_hub_offer_protocol_decision_p54g.py `
  tests/test_hub_worker_offer_routing_p54f.py `
  tests/test_hub_legacy_rest_consumers_p54e.py `
  tests/test_hub_worker_explicit_rest_p54d.py `
  tests/test_hub_interface_p52.py `
  tests/test_worker_app_layout_contract.py
```

For live UI acceptance, with **Dev** selected and an active key, update a
valid seller offer while normal Worker policy is blocked; the response
should say **pending** without granting Work now. Once normal availability
permits, the runtime supervisor may register the offer using the
selected Dev-Test Hub. Repeat on an isolated Test Hub. Stable Hub end-to-end
paid registration should be accepted separately with dedicated test
credentials and no implicit production payment.

Use `python -m tools.hub_worker_offer_route_verify --live` only for a
read-only view of the saved selection; it does **not** execute an offer.
Do not redeploy mainnet or alter SLH boot policy for this patch.
