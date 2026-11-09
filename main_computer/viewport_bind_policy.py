"""Local-only binding boundary for the trusted Main Computer Viewport.

This is deliberately independent of Hub/SLH/SEH network configuration.
Docker's development app may bind inside its own isolated container network;
the Compose *host publication* is always loopback-only.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

LOOPBACK_HOST = "127.0.0.1"
_CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))


def _explicit_dev_container_bind() -> bool:
    return (
        sys.platform != "win32"
        and os.environ.get("MAIN_COMPUTER_VIEWPORT_CONTAINER_INTERNAL_BIND") == "1"
        and any(marker.is_file() for marker in _CONTAINER_MARKERS)
    )


def require_local_viewport_bind(host: str, *, service: str = "Viewport", container_internal: bool = False) -> str:
    """Fail closed on non-loopback endpoints *before* process or socket changes.

    The only supported local address is the numeric IPv4 loopback literal.
    A dedicated internal Docker exception exists solely for the Viewport,
    never for the heartbeat control listener or on a Windows host.
    """
    if host == LOOPBACK_HOST:
        return LOOPBACK_HOST
    if container_internal and host == "0.0.0.0" and _explicit_dev_container_bind():
        return host
    raise ValueError(
        f"{service} must bind to {LOOPBACK_HOST}; rejected {host!r}. "
        "The development container's internal wildcard bind requires the "
        "explicit container setting and container runtime marker."
    )
