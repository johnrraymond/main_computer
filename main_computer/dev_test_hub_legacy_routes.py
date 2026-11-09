"""Explicit P5.3 boundary for legacy endpoints inherited by the FDB Dev-Test Hub.

The FDB Dev-Test Hub still subclasses the historical Hub HTTP handler and
state interfaces.  An inherited route is available *only* when declared here:
adding a route to the historical handler cannot silently publish it through
the FDB Dev-Test Hub.  This is routing ownership, not a protocol rewrite or an
authorization bypass; all allowed routes still run their existing handler.

Only legacy routes actually implemented by HubServerHandler are listed.  The
FDB Dev-Test Hub's own /health, live-session, work-request, session-stream,
hub-identity and topology paths are handled before this boundary.
"""
from __future__ import annotations

from main_computer.hub_admin_site import HUB_ADMIN_ROUTES


_LEGACY_GET_EXACT = frozenset(HUB_ADMIN_ROUTES) | frozenset({
    "/api/hub/status", "/api/hub/v1/status", "/api/hub/v1/health",
    "/api/hub/v1/workers", "/api/hub/v1/models",
    # These are deliberately inherited *denials*, not public read access.
    "/api/hub/v1/admin/bootstrap", "/api/hub/v1/metrics",
    "/api/hub/v1/ring-control/feedback-summary", "/api/hub/v1/requests",
    "/api/hub/v1/credits", "/api/hub/payouts", "/api/hub/v1/payouts",
})

_LEGACY_GET_PREFIXES = (
    "/api/hub/v1/workers/",  # Existing handler refuses private reads.
    "/api/hub/v1/requests/", # Existing handler refuses private reads.
    "/api/hub/v1/credits/",  # Existing handler refuses private reads.
    "/api/hub/v1/bridge/",   # Existing handler refuses private reads.
)

_LEGACY_POST_EXACT = frozenset({
    "/api/hub/remote-overflow/safe-chat", # Dev-Test simulation; not paid execution.
    "/api/hub/v1/credits/multisession-keys/request",
    "/api/hub/v1/credits/multisession-keys/revoke",
    "/api/hub/v1/credits/multisession-keys/validate",
    "/api/hub/workers/connect", "/api/hub/v1/workers/connect",
    "/api/hub/workers/stream-events", "/api/hub/v1/workers/stream-events",
    "/api/hub/sessions/start", "/api/hub/sessions/chat",
    "/api/hub/v1/credits/wallet-funding/complete",
    "/api/hub/v1/requests/quote", "/api/hub/v1/requests",
    "/api/hub/chat",
})


def inherited_legacy_hub_route(method: str, path: str) -> bool:
    """True only for declared legacy routes the Dev-Test Hub still depends on.

    The caller must pass a parsed URL path, without query or fragment. Unknown
    paths are 404, not delegated to historical handler's future additions.
    """
    if method == "GET":
        return path in _LEGACY_GET_EXACT or any(path.startswith(p) for p in _LEGACY_GET_PREFIXES)
    if method == "POST":
        if path in _LEGACY_POST_EXACT:
            return True
        prefix, suffix = "/api/hub/v1/requests/", "/feedback"
        return (path.startswith(prefix) and path.endswith(suffix)
                and bool(path[len(prefix):-len(suffix)].strip("/"))
                and "/" not in path[len(prefix):-len(suffix)].strip("/"))
    return False
