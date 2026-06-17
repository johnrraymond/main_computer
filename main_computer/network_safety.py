from __future__ import annotations

from typing import Any


ANVIL_DEFAULT_OFFICES = {
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
}

REMOTE_AUTHORITY_KINDS = {"mainnet", "testnet"}

ENERGY_EXPECTED_CONTRACTS = (
    ("alpha-beta-lockout", "AlphaBetaLockout"),
    ("xlag-bridge-reserve", "XLagBridgeReserve"),
    ("hub_credit_bridge_escrow", "HubCreditBridgeEscrow"),
)


def hex_to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        return int(text, 16 if text.lower().startswith("0x") else 10)
    except (TypeError, ValueError):
        return None


def manifest_contract_entries(payload: object, *, require_address: bool = False) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        return {}

    merged: dict[str, dict[str, object]] = {}
    for section_name in ("deployments", "contracts"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        for name, entry in section.items():
            clean_name = str(name).strip()
            if not clean_name or not isinstance(entry, dict):
                continue
            address = str(entry.get("address") or "").strip()
            if require_address and not address:
                continue
            merged[clean_name] = dict(entry)
    return merged


def manifest_contract_address_map(entries: dict[str, dict[str, object]]) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for name, entry in entries.items():
        address = str(entry.get("address") or "").strip()
        if address:
            addresses[name] = address
    return addresses


def manifest_office_entries(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    raw_offices = payload.get("offices")
    if not isinstance(raw_offices, list):
        return []

    offices: list[dict[str, object]] = []
    for raw_office in raw_offices:
        if not isinstance(raw_office, dict):
            continue
        address = str(raw_office.get("address") or "").strip()
        if not address:
            continue
        normalized = address.lower()
        offices.append(
            {
                "office": str(raw_office.get("office") or ""),
                "title": str(raw_office.get("title") or raw_office.get("office") or address),
                "address": address,
                "default_anvil": normalized in ANVIL_DEFAULT_OFFICES,
            }
        )
    return offices


def authority_status(profile: object, payload: object) -> dict[str, object]:
    network_key = str(getattr(profile, "network_key", "") or "").strip()
    kind = str(getattr(profile, "kind", "") or "").strip().lower()
    offices = manifest_office_entries(payload)
    default_offices = [
        str(office.get("title") or office.get("office") or office.get("address") or "office")
        for office in offices
        if office.get("default_anvil")
    ]

    if not offices:
        return {
            "authority_status": "unknown",
            "authority_warning": "deployment manifest does not include office authority",
            "authority_default_offices": [],
            "offices": offices,
            "authority_unsafe": False,
        }

    if default_offices and kind in REMOTE_AUTHORITY_KINDS:
        return {
            "authority_status": "unsafe",
            "authority_warning": (
                f"{network_key} authority is unsafe: "
                f"{', '.join(default_offices)} match default Anvil office identities."
            ),
            "authority_default_offices": default_offices,
            "offices": offices,
            "authority_unsafe": True,
        }

    if default_offices:
        return {
            "authority_status": "default-dev-authority",
            "authority_warning": (
                f"{network_key} is using default Anvil office identities for local validation."
            ),
            "authority_default_offices": default_offices,
            "offices": offices,
            "authority_unsafe": False,
        }

    return {
        "authority_status": "rotated",
        "authority_warning": "",
        "authority_default_offices": [],
        "offices": offices,
        "authority_unsafe": False,
    }


def manifest_identity_warnings(profile: object, payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []

    warnings: list[str] = []
    manifest_environment = str(payload.get("environment") or "").strip()
    network_key = str(getattr(profile, "network_key", "") or "").strip()
    if manifest_environment and network_key and manifest_environment != network_key:
        warnings.append(f"Manifest environment {manifest_environment!r} does not match registry network {network_key!r}.")

    chain = payload.get("chain")
    manifest_chain_id = chain.get("chain_id") if isinstance(chain, dict) else None
    profile_chain_id = getattr(profile, "chain_id", None)
    if manifest_chain_id is not None and profile_chain_id is not None:
        manifest_chain_id_int = hex_to_int(manifest_chain_id)
        profile_chain_id_int = hex_to_int(profile_chain_id)
        if manifest_chain_id_int is None or profile_chain_id_int is None:
            warnings.append(
                f"Deployment manifest chain_id {manifest_chain_id!r} or network chain_id {profile_chain_id!r} is invalid."
            )
        elif manifest_chain_id_int != profile_chain_id_int:
            warnings.append(
                f"Manifest chain id {manifest_chain_id_int} does not match expected chain id {profile_chain_id_int}."
            )

    return warnings


def manifest_metadata(payload: object) -> dict[str, object]:
    manifest = payload if isinstance(payload, dict) else {}
    chain = manifest.get("chain") if isinstance(manifest.get("chain"), dict) else {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    return {
        "manifest_environment": str(manifest.get("environment") or "") if isinstance(manifest, dict) else "",
        "manifest_chain_id": hex_to_int(chain.get("chain_id")) if isinstance(chain, dict) else None,
        "run_id": str(manifest.get("run_id") or "") if isinstance(manifest, dict) else "",
        "created_at": str(manifest.get("created_at") or "") if isinstance(manifest, dict) else "",
        "source_kind": str(source.get("kind") or source.get("source_kind") or "") if isinstance(source, dict) else "",
    }


def classify_network_safety(*, unsafe: list[str] | tuple[str, ...] = (), degraded: list[str] | tuple[str, ...] = ()) -> str:
    return "unsafe" if unsafe else ("degraded" if degraded else "healthy")
