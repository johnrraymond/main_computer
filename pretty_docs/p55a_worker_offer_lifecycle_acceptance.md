# P5.5a — authenticated Worker offer lifecycle acceptance

## Boundary

P5.4h migrated the Worker page's **Register / Update Offer** action from
legacy REST to the selected Hub's authenticated Worker registration path.
P5.5a adds *lifecycle acceptance* for that behavior. It makes no production
changes and does not retire other clients of the legacy REST interface.

The Worker supervisor, not an offer-form submission, decides when ordinary
local availability permits advertising work. **Work now** remains a separate,
explicit, time-bounded authorization.

## Required gates

| Gate | Evidence |
| --- | --- |
| Authenticated identity | Same wallet and selected-Hub MSK carried to both initial and replacement registration |
| Canonical Hub | Dev/Test versus Mainnet/Testnet always resolves from saved Worker selection; no URL supplied by the browser can redirect it |
| Offer identity | Updated token price reaches the second Hub registration, rather than replaying the first offer |
| Busy / idle | Updating while Windows reports an active user closes the previous offer and defers the replacement; a subsequent supervisor pass publishes it only after normal idle policy permits |
| Work now isolation | No offer-sync call sets or changes the Work now timer, and an existing Work now override cannot authorize ordinary offer sync |
| Opt-in / active work | Disabled seller cannot publish; an in-flight Worker job blocks market replacement |
| Failure / network switch | Wrong network, chain, URL or MSK fails before dispatch; failed Hub receipt cannot be accepted; a deferred Dev offer cannot silently register on Test after network switching |

These tests use the real Viewport offer-sync handler, saved settings,
registration payload normalization, availability policy, and supervisor
pending-offer entry point. Hub registration and close operations are **recorded
isolated doubles**; no Stable Hub, Dev-Test Hub, SLH, SEH, FoundationDB, wallet,
or paid requester is contacted. Existing P5.4h tests separately exercise a
local Dev-Test HTTP registration flow. P5.5c will address isolated Stable Hub
WebSocket integration; P5.5d/e address relay and accounting.

## Windows acceptance

From the repository root in the project's active virtual environment:

```powershell
python -m pytest -q tests/test_worker_offer_lifecycle_p55a.py
```

For the combined P5.5a / P5.4h / Hub-interface boundary:

```powershell
python -m pytest -q `
  tests/test_worker_offer_lifecycle_p55a.py `
  tests/test_worker_offer_authenticated_p54h.py `
  tests/test_worker_app_registration_proxy.py `
  tests/test_hub_offer_protocol_decision_p54g.py `
  tests/test_hub_worker_offer_routing_p54f.py `
  tests/test_hub_legacy_rest_consumers_p54e.py `
  tests/test_hub_worker_explicit_rest_p54d.py `
  tests/test_hub_interface_p52.py
```

**Accept** only if all selected tests pass on Windows, with zero failures or
unexpected skips. These results authorize P5.5a's tested lifecycle boundary,
not live mainnet paid-work or accounting acceptance. The local project export
may omit `game_projects/webgl-demo/game.json`; that export-only fixture is not
part of this patch and is not needed in a complete checkout.
