from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from main_computer.config import MainComputerConfig


DEPLOYMENT_CURRENT_PATH = Path("runtime") / "deployments" / "current.json"
DEV_CHAIN_LATEST_PATH = Path("runtime") / "dev-chain" / "latest.json"
_DEPLOYMENT_RUNTIME_SOURCE = "deployment-runtime"
_DEV_CHAIN_RUNTIME_SOURCE = "runtime-dev-chain"


def apply_dev_chain_runtime_config(config: MainComputerConfig, runtime_root: Path) -> MainComputerConfig:
    """Overlay the published deployment runtime onto a config.

    Dev and production should share the same app-facing contract configuration
    shape. Prefer runtime/deployments/current.json, a sanitized deployment
    publication without signing material. Fall back to the legacy dev-chain
    latest.json only when the production-shaped publication is absent.
    """

    deployment_path = runtime_root / DEPLOYMENT_CURRENT_PATH
    if deployment_path.exists():
        return _apply_runtime_file(
            config,
            deployment_path,
            source=_DEPLOYMENT_RUNTIME_SOURCE,
            invalid_message="deployment runtime current.json must contain a JSON object",
        )

    legacy_path = runtime_root / DEV_CHAIN_LATEST_PATH
    return _apply_runtime_file(
        config,
        legacy_path,
        source=_DEV_CHAIN_RUNTIME_SOURCE,
        invalid_message="dev-chain runtime latest.json must contain a JSON object",
        missing_ok=True,
    )


def _apply_runtime_file(
    config: MainComputerConfig,
    path: Path,
    *,
    source: str,
    invalid_message: str,
    missing_ok: bool = False,
) -> MainComputerConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return config
        return replace(
            config,
            dev_chain_runtime_path=path,
            dev_chain_runtime_source="missing",
            dev_chain_runtime_error=None,
        )
    except (OSError, json.JSONDecodeError) as exc:
        return replace(
            config,
            dev_chain_runtime_path=path,
            dev_chain_runtime_source="invalid",
            dev_chain_runtime_error=str(exc),
        )

    if not isinstance(data, dict):
        return replace(
            config,
            dev_chain_runtime_path=path,
            dev_chain_runtime_source="invalid",
            dev_chain_runtime_error=invalid_message,
        )

    return _apply_runtime_data(config, path, data, source)


def _apply_runtime_data(config: MainComputerConfig, path: Path, data: dict[str, Any], source: str) -> MainComputerConfig:
    chain = data.get("chain")
    if not isinstance(chain, dict):
        chain = {}

    deployments = data.get("deployments")
    if not isinstance(deployments, dict):
        deployments = data.get("contracts")
    if not isinstance(deployments, dict):
        deployments = {}

    run_id = _clean_text(data.get("run_id"))
    host_rpc_url = _clean_text(chain.get("rpc_url")) or _clean_text(chain.get("host_rpc_url"))
    chain_id = _coerce_int(chain.get("chain_id"))
    xlag_address = _deployment_address(deployments, "xlag-bridge-reserve")
    alpha_address = _deployment_address(deployments, "alpha-beta-lockout")
    offices = _office_records(data.get("offices"))

    updates: dict[str, Any] = {
        "dev_chain_run_id": run_id,
        "dev_chain_runtime_path": path,
        "dev_chain_runtime_source": source,
        "dev_chain_runtime_error": None,
        "dev_chain_offices": offices,
    }

    if host_rpc_url and _is_default_source(config.energy_chain_rpc_url_source):
        updates["energy_chain_rpc_url"] = host_rpc_url
        updates["energy_chain_rpc_url_source"] = source

    if chain_id is not None and _is_default_source(config.energy_chain_id_source):
        updates["energy_chain_id"] = chain_id
        updates["energy_chain_id_source"] = source

    if chain_id is not None and _is_default_source(config.xlag_chain_id_source):
        updates["xlag_chain_id"] = chain_id
        updates["xlag_chain_id_source"] = source

    if xlag_address and _is_default_source(config.xlag_contract_address_source):
        updates["xlag_contract_address"] = xlag_address
        updates["xlag_contract_address_source"] = source

    if alpha_address and _is_default_source(config.alpha_beta_lockout_contract_address_source):
        updates["alpha_beta_lockout_contract_address"] = alpha_address
        updates["alpha_beta_lockout_contract_address_source"] = source

    return replace(config, **updates)


def _is_default_source(source: str | None) -> bool:
    return not source or source.startswith("default")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _deployment_address(deployments: dict[str, Any], key: str) -> str | None:
    deployment = deployments.get(key)
    if not isinstance(deployment, dict):
        return None
    address = _clean_text(deployment.get("address"))
    if address and re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        return address
    return None


def _office_records(value: Any) -> tuple[dict[str, str | None], ...]:
    if not isinstance(value, list):
        return ()

    records: list[dict[str, str | None]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        address = _clean_text(item.get("address"))
        if not address or not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
            continue
        records.append(
            {
                "office": _clean_text(item.get("office")) or f"O{index}",
                "title": _clean_text(item.get("title")),
                "address": address,
            }
        )
    return tuple(records)
