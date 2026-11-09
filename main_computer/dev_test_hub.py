"""Dev-Test Hub boundary checks, separate from Stable Hub mainnet/testnet routing.

This module intentionally has no Hub, FoundationDB, or deployment imports.  Startup
calls its preflight before stopping an existing local Hub or starting a process.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit


class DevTestHubConfigurationError(ValueError):
    """A Dev-Test Hub configuration would cross the local-test boundary."""


# These are the LOCAL chain profiles; public testnet has the same chain ID as
# local 'test', so the RPC and listener addresses must ALSO be local.
LOCAL_CHAIN_IDS = {"dev": 42424242, "test": 42424241}


def _is_loopback(host: str) -> bool:
    clean = str(host or "").strip().strip("[]").lower()
    if clean == "localhost":
        return True
    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def _local_url(value: str, *, label: str) -> str:
    try:
        uri = urlsplit(str(value or "").strip())
        port = uri.port
        valid = (_is_loopback(uri.hostname or "") and uri.scheme == "http" and port
                 and uri.username is None and uri.password is None
                 and uri.path in {"", "/"} and not uri.query and not uri.fragment)
    except (ValueError, TypeError):
        valid = False
        uri = None
    if not valid:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub {label} must be an explicit loopback http://host:port URL; got {value!r}."
        )
    assert uri is not None
    return uri.geturl().rstrip("/")


def _local_bind(value: str) -> None:
    local = _is_loopback(value)
    if not local:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub bind host must be loopback, not {value!r}."
        )


def validate_dev_test_hub(
    *, network: str, network_kind: str, chain_id: object,
    chain_rpc_url: str, hub_urls: list[str], bind_host: str,
    requested_network: str = "", topology: str | Path | None = None,
) -> dict[str, object]:
    """Fail closed for public endpoints, public chains, or mismatched topology."""
    selected = str(network or "").strip().lower()
    if selected not in LOCAL_CHAIN_IDS:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub may use only 'dev' or local 'test', not {network!r}. "
            "Use the Stable Hub runtime for public testnet/mainnet."
        )
    if str(network_kind or "").strip().lower() != selected:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub network kind must be {selected!r}; got {network_kind!r}."
        )
    requested = str(requested_network or "").strip().lower()
    if requested and requested != selected:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub requested network {requested!r} conflicts with resolved {selected!r}."
        )
    try:
        parsed_id = int(str(chain_id).strip(), 0)
    except (TypeError, ValueError):
        try:
            parsed_id = int(chain_id)
        except (TypeError, ValueError):
            parsed_id = -1
    if parsed_id != LOCAL_CHAIN_IDS[selected]:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub {selected!r} requires chain ID {LOCAL_CHAIN_IDS[selected]}, got {chain_id!r}."
        )
    _local_bind(bind_host)
    _local_url(chain_rpc_url, label="chain RPC")
    if not hub_urls:
        raise DevTestHubConfigurationError("Dev-Test Hub requires at least one explicit local Hub URL.")
    cleaned_urls = [_local_url(value, label="endpoint") for value in hub_urls]

    if topology is not None and str(topology).strip():
        path = Path(topology)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise DevTestHubConfigurationError(f"Dev-Test Hub topology cannot be read: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DevTestHubConfigurationError("Dev-Test Hub topology must be a JSON object.")
        net = payload.get("network")
        if not isinstance(net, dict):
            raise DevTestHubConfigurationError("Dev-Test Hub topology is missing its network object.")
        validate_dev_test_hub(
            network=str(net.get("network_key") or ""),
            network_kind=str(net.get("network_kind") or ""),
            chain_id=net.get("chain_id"),
            chain_rpc_url=str(net.get("chain_rpc_url") or ""),
            hub_urls=[str(value) for value in payload.get("entry_urls", [])],
            bind_host=bind_host,
            requested_network=selected,
        )
        if str(net.get("chain_rpc_url") or "").rstrip("/") != str(chain_rpc_url).rstrip("/"):
            raise DevTestHubConfigurationError("Dev-Test Hub topology RPC differs from the launch RPC.")
        topology_urls = [_local_url(str(u), label="topology entry") for u in payload.get("entry_urls", [])]
        if set(topology_urls) != set(cleaned_urls):
            raise DevTestHubConfigurationError("Dev-Test Hub topology entries differ from the launch Hub URLs.")
        for hub in payload.get("hubs", []):
            if not isinstance(hub, dict):
                raise DevTestHubConfigurationError("Dev-Test Hub topology has an invalid Hub entry.")
            for field in ("hub_url", "public_url"):
                if hub.get(field):
                    url = _local_url(str(hub[field]), label=f"topology {field}")
                    if url not in topology_urls:
                        raise DevTestHubConfigurationError(
                            f"Dev-Test Hub topology {field} is outside declared entry URLs."
                        )
    return {"role": "dev-test-hub", "network": selected, "chain_id": parsed_id,
            "hub_urls": cleaned_urls, "chain_rpc_url": str(chain_rpc_url)}



# The Viewport can run in the Docker development stack. These are its
# documented *local-container* aliases, not arbitrary DNS hostnames.
LOCAL_DEV_ROUTE_ALIASES = frozenset({"hub", "dev-hub", "dev-test-hub", "host.docker.internal"})


def validate_dev_test_route(url: str) -> str:
    """Refuse old-protocol application requests to public Stable Hub endpoints."""
    value = str(url or "").strip()
    try:
        uri = urlsplit(value)
        host = (uri.hostname or "").lower()
        port = uri.port
        valid = ((host in LOCAL_DEV_ROUTE_ALIASES or _is_loopback(host))
                 and uri.scheme == "http" and port
                 and uri.username is None and uri.password is None
                 and uri.path in {"", "/"} and not uri.query and not uri.fragment)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DevTestHubConfigurationError(
            f"Dev-Test Hub application route must target a local Hub endpoint, not {url!r}."
        )
    return value.rstrip("/")


def validate_dev_test_config(config: object) -> dict[str, object]:
    return validate_dev_test_hub(
        network=str(getattr(config, "hub_network", "")),
        network_kind=str(getattr(config, "hub_network_kind", "")),
        chain_id=getattr(config, "chain_id", None),
        chain_rpc_url=str(getattr(config, "chain_rpc_url", "")),
        hub_urls=[str(getattr(config, "hub_url", ""))],
        bind_host=str(getattr(config, "hub_bind_host", "")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Dev-Test Hub isolation preflight")
    parser.add_argument("preflight", nargs="?", choices=["preflight"], default="preflight")
    parser.add_argument("--network", required=True)
    parser.add_argument("--network-kind", required=True)
    parser.add_argument("--requested-network", default="")
    parser.add_argument("--chain-id", required=True)
    parser.add_argument("--chain-rpc-url", required=True)
    parser.add_argument("--hub-url", action="append", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--topology", default="")
    args = parser.parse_args(argv)
    try:
        result = validate_dev_test_hub(
            network=args.network, network_kind=args.network_kind,
            requested_network=args.requested_network, chain_id=args.chain_id,
            chain_rpc_url=args.chain_rpc_url, hub_urls=args.hub_url,
            bind_host=args.host, topology=args.topology or None,
        )
    except DevTestHubConfigurationError as exc:
        parser.exit(2, f"DEV_TEST_HUB_PREFLIGHT_REFUSED: {exc}\n")
    print(json.dumps({"status": "DEV_TEST_HUB_PREFLIGHT_PASS", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
