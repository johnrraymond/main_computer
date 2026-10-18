"""Authenticated, read-only Coolify precheck for a new Mother testnet controller.

The Coolify API identifies a *configured* server, not necessarily the Linux
host's current VPN interface or OS hostname. Never claim those are confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any
from urllib.parse import quote

from .coolify_state import (
    CoolifyController,
    CoolifyObservationError,
    get_coolify_json,
)


class TestnetPrecheckError(ValueError):
    """Reject untrusted or mismatched live Coolify identity."""


@dataclass(frozen=True)
class TestnetPrecheckResult:
    project_name: str
    server_name: str
    server_configured_ip: str
    warnings: tuple[str, ...]


def _items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        value = payload
    elif isinstance(payload, dict):
        value = payload.get(key, payload.get("data"))
    else:
        value = None
    if not isinstance(value, list):
        raise TestnetPrecheckError(f"Coolify {key} response was not a list")
    return [item for item in value if isinstance(item, dict)]


def run_testnet_precheck(
    *, controller_url: str, token: str,
    project_uuid: str, server_uuid: str, project_name: str,
    public_ip: str, vpn_ip: str, droplet_hostname: str,
    timeout: float = 5.0, opener: Any = None,
) -> TestnetPrecheckResult:
    """GET only; verify credentials/project/server without writing local or remote state.

    ``opener`` is injectable solely to permit isolated transport tests. The
    default uses Mother's no-redirect bounded Coolify HTTP observer.
    """
    controller = CoolifyController(
        network="testnet", controller_id="coolify-b", base_url=controller_url,
        api_token=token, enabled=True, project_name_hint=project_name,
        mutation_authority="observe-only",
    )
    kwargs = {"authenticated": True, "timeout": timeout, "max_response_bytes": 262144}
    if opener is not None:
        kwargs["opener"] = opener

    def fetch(path: str, name: str) -> Any:
        try:
            response = get_coolify_json(controller, path, **kwargs)
        except CoolifyObservationError as exc:
            # Error codes are safe; never echo raw exception text or token material.
            raise TestnetPrecheckError(f"Coolify {name} precheck request failed ({exc.code})") from None
        except OSError:
            raise TestnetPrecheckError(f"Coolify {name} precheck request failed (transport error)") from None
        if 300 <= response.status < 400:
            raise TestnetPrecheckError(
                f"Coolify {name} precheck returned HTTP {response.status} redirect "
                f"({response.redirect_hint or 'target not reported'}); "
                "redirects are not followed to protect the API token"
            )
        if response.status != 200:
            raise TestnetPrecheckError(f"Coolify {name} precheck returned HTTP {response.status} (requires API read access)")
        return response.payload

    projects = _items(fetch("/api/v1/projects", "projects"), "projects")
    matching_projects = [p for p in projects if p.get("uuid") == project_uuid]
    if len(matching_projects) != 1:
        raise TestnetPrecheckError("configured project UUID not found exactly once in authenticated Coolify projects")
    actual_project_name = matching_projects[0].get("name")
    if not isinstance(actual_project_name, str) or actual_project_name != project_name:
        raise TestnetPrecheckError("configured project name differs from authenticated Coolify project")

    # Server detail confirms both token scope and the exact UUID. It is
    # stronger than seeing an unrelated server in a list from another team.
    if not server_uuid or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in server_uuid):
        raise TestnetPrecheckError("server UUID contains unsupported characters")
    detail = fetch("/api/v1/servers/" + quote(server_uuid, safe=""), "server")
    if not isinstance(detail, dict) or detail.get("uuid") != server_uuid:
        raise TestnetPrecheckError("authenticated Coolify server UUID differs from configured server UUID")
    name = detail.get("name", "")
    name = name if isinstance(name, str) else ""
    ip = detail.get("ip", "")
    ip = ip.strip() if isinstance(ip, str) else ""
    warnings = []
    if ip:
        try:
            configured_address = ipaddress.ip_address(ip)
        except ValueError:
            warnings.append("Coolify server uses a non-numeric SSH target; cannot compare it to the VPN/public IP")
        else:
            if str(configured_address) not in {public_ip, vpn_ip}:
                raise TestnetPrecheckError("Coolify server SSH address differs from both the proposed public and VPN addresses")
            warnings.append("Coolify server SSH address matches a proposed IP, but does not prove its live interface assignment")
    else:
        warnings.append("Coolify server has no exposed SSH target IP; host address is not independently verified")

    warnings.append(
        f"VPN {vpn_ip} is not independently attested by Coolify API; verify using 'ip -o -4 addr show' on the actual Coolify B host"
    )
    if name.lower() != droplet_hostname.lower():
        warnings.append(
            f"Coolify server display name differs from proposed OS hostname ({droplet_hostname}); verify using 'hostname' on Coolify B"
        )
    else:
        warnings.append("Coolify server display name matches proposed hostname, but OS hostname remains unverified")
    return TestnetPrecheckResult(actual_project_name, name, ip, tuple(warnings))
