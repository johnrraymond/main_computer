# P5.4f — Worker offer-registration Hub binding

## Decision

Preserve the Worker page's **Register / Update Offer** action, its REST
registration implementation, and the separate authenticated **Work now**
workflow. Do not promote the legacy REST endpoint to the production
live-session protocol or delete it without retirement acceptance.

## Previous discrepancy

`POST /api/applications/worker/register-offer` previously accepted a supplied
`hub_url` (falling back to `server.config.hub_url`) and posted to
`/api/hub/v1/workers/register` without checking the saved Worker network.
The Work now pathway already enforced a separate selected-network check.

## New boundary

The offer-registration backend now reads the existing persisted Worker settings,
resolves the effective network through `hub_interface.resolve_hub_destination`,
and requires the request's Hub URL to equal the canonical selected-network URL.
A missing selection, an SLH-managed selection, missing URL, or mismatched URL
fails before attempting an HTTP request. No network or URL is inferred from
request-body metadata or the default configured Hub. The response on success
includes non-secret routing diagnostics.

The matched request is still sent through the **existing legacy REST** route.
A Hub that does not support that endpoint may reject the operation. This patch
establishes destination binding, **not proof of production offer registration**
or replacement of the authenticated live-session workflow.

## Windows verification

```powershell
python -m tools.hub_worker_offer_route_verify
python -m tools.hub_worker_offer_route_verify --live
python -m pytest -q tests/test_hub_worker_offer_routing_p54f.py
```

The verifier is read-only. `--live` performs only a GET from the local
Viewport settings endpoint and does not register a Worker or spend credits.

Test coverage checks accepted target resolution, rejected foreign URLs, stale
selection, disabled/SLH network selections, and real loopback rejection. No
production Hub deployment, payment, storage, or Worker supervisor is changed.

## Remaining acceptance

Examine whether the UI's older **Register / Update Offer** button should remain
for legacy-compatible Hubs or be replaced by a modern signed Worker registration
flow. That is a separate functional decision. Neither this patch nor its unit
tests establishes that the Stable or FDB Dev-Test Hub accepts legacy REST offers.
