from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.config import DEFAULT_HUB_NETWORK, MainComputerConfig
from main_computer.container_runtime import resolve_container_runtime
from main_computer.credit_units import credit_decimal_text_to_wei, credit_wei_to_decimal_text
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_plex_service import PHASE9_EXECUTION_MODE, PHASE9_PRICING_MODE
from main_computer.hub_credit_bridge_completion import JsonRpcClient
from main_computer.hub_credit_models import normalize_address
from main_computer.hub_networks import HubNetworkConfigError, load_hub_network_registry


_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
PRIVATE_STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"
_TX_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")

DEFAULT_STATE_FILE = Path("runtime/deployments/dev/latest.json")
LEGACY_STATE_FILE = Path("runtime/dev-chain/latest.json")
_DEFAULT_HUB_USER_AGENT = "main-computer-captain-cli/1.0 (+https://greatlibrary.io)"

# Public Anvil defaults. These are used only for the local dev chain when the
# deployment manifest lists the standard O0-O3 office addresses without private
# runtime wallet files.
_DEV_OFFICE_DEFAULTS: tuple[dict[str, str], ...] = (
    {
        "office": "O0",
        "title": "Captain",
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    {
        "office": "O1",
        "title": "First Officer",
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "private_key": "0x59c6995e998f97a5a0044966f094538c9e4361d023d65a14d6007a1df0863d9",
    },
    {
        "office": "O2",
        "title": "Second Officer",
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "private_key": "0x5de4111afa1a4b582f56a49c1b5f05b7ec3a943b11f071d72da14ef03ea64d35",
    },
    {
        "office": "O3",
        "title": "Third Officer",
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "private_key": "0x7c85211829461a5d643dad689a8271d5cf81952292a5c7e8929659f978a1d6b2",
    },
)


@dataclass(frozen=True)
class CaptainInvocation:
    smoke: bool
    selector: str
    prompt: str
    option_tokens: tuple[str, ...]


@dataclass(frozen=True)
class CaptainWallet:
    selector: str
    office: str
    title: str
    address: str
    private_key: str = ""

    @property
    def is_office(self) -> bool:
        return bool(self.office)


@dataclass(frozen=True)
class CaptainRuntime:
    config: MainComputerConfig
    deployment_path: Path
    deployment: dict[str, Any]
    wallet: CaptainWallet
    rpc_url: str
    chain_id: int
    xlag_address: str
    network: str
    bridge_escrow_address: str
    bridge_controller_address: str


class CaptainCliError(RuntimeError):
    """Raised for operator-facing captain CLI errors."""


def parse_captain_invocation(argv: list[str]) -> CaptainInvocation:
    free_tokens, option_tokens = _split_free_and_option_tokens(argv)
    tokens = list(free_tokens)
    smoke = False
    selector = "captain"

    if tokens and _is_smoke_token(tokens[0]):
        smoke = True
        tokens.pop(0)

    if tokens:
        consumed, parsed_selector = _consume_selector(tokens)
        if consumed:
            selector = parsed_selector
            del tokens[:consumed]

    if tokens and _is_smoke_token(tokens[0]):
        smoke = True
        tokens.pop(0)

    prompt = " ".join(tokens).strip()
    return CaptainInvocation(smoke=smoke, selector=selector, prompt=prompt, option_tokens=tuple(option_tokens))


def build_captain_options_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main-computer captain",
        description=(
            "Connect as the Captain/officer backend wallet. Captain prompts default to "
            "mainnet bridge funding, a ring-3 hub request, and bridge refund cleanup."
        ),
    )
    parser.add_argument("--prompt", default="", help="Prompt/memo override. Otherwise free text before the first --option is used.")
    parser.add_argument("--wallet", default="", help="Wallet/officer selector or 0x address. Defaults to captain/O0.")
    parser.add_argument("--officer", default="", help="Officer selector, for example O1, first, second, or third.")
    parser.add_argument("--private-key", default="", help="Private key for a non-dev backend wallet. Never printed.")
    parser.add_argument("--private-state", type=Path, default=None, help="Private YAML/JSON state. Defaults to runtime/state/main_computer.private.yaml when present.")
    parser.add_argument("--ring", default="3", help="Requested hub worker ring. Defaults to 3.")
    parser.add_argument("--ring0", dest="ring", action="store_const", const="0", help="Shortcut for --ring 0.")
    parser.add_argument("--ring1", dest="ring", action="store_const", const="1", help="Shortcut for --ring 1.")
    parser.add_argument("--ring2", dest="ring", action="store_const", const="2", help="Shortcut for --ring 2.")
    parser.add_argument("--ring3", dest="ring", action="store_const", const="3", help="Shortcut for --ring 3.")
    parser.add_argument("--model", default="", help="Hub model id. Defaults to MainComputerConfig/from env.")
    parser.add_argument("--hub-url", default="", help="Hub base URL. Defaults to the selected network hub; captain defaults to mainnet.")
    parser.add_argument("--client-node-id", default="main-computer-captain-cli", help="Hub client node id.")
    parser.add_argument("--network", default="", help="Deployment network key. Captain prompts default to mainnet unless explicitly overridden.")
    parser.add_argument("--state", type=Path, default=None, help="Deployment state JSON. Defaults to runtime/deployments/<network>/latest.json.")
    parser.add_argument("--rpc-url", default="", help="Override chain RPC URL for the smoke transaction.")
    parser.add_argument("--chain-id", type=int, default=0, help="Override expected chain id for display/payload.")
    parser.add_argument("--xlag-address", default="", help="Override XLagBridgeReserve address.")
    parser.add_argument("--stipend-credits", type=int, default=1, help="Fixed hub credits to issue before --yes submit. Defaults to 1.")
    parser.add_argument("--max-credits", type=int, default=0, help="Max credits the hub request may spend. Defaults to stipend credits.")
    parser.add_argument("--smoke-id", default="", help="Explicit bytes32 smoke id or friendly label to hash.")
    parser.add_argument("--idempotency-key", default="", help="Hub quote/request idempotency key. Defaults to the smoke id.")
    parser.add_argument("--timeout-s", type=float, default=30.0, help="HTTP/RPC timeout in seconds.")
    parser.add_argument("--poll-seconds", type=float, default=0.0, help="Poll submitted hub request for this many seconds after execution. Captain smoke defaults to 90 seconds.")
    parser.add_argument("--yes", "--execute", dest="execute", action="store_true", help="Execute immediately. Captain smoke executes by default unless --prepare-only is set.")
    parser.add_argument("--prepare-only", "--no-execute", dest="prepare_only", action="store_true", help="Only quote/prepare; do not bridge, submit, or refund.")
    parser.add_argument("--no-hub", action="store_true", help="Skip hub quote/submit and run only the local chain smoke path.")
    parser.add_argument("--no-chain", action="store_true", help="Skip the on-chain smoke transaction.")
    parser.add_argument("--no-stipend", action="store_true", help="Do not issue fixed hub credits before submit in legacy no-bridge mode.")
    parser.add_argument("--no-bridge", action="store_true", help="Skip the mainnet bridge deposit/refund flow and use the legacy stipend path.")
    parser.add_argument("--bridge-credits", default="1", help="Compute credits to bridge for captain smoke. Defaults to 1.")
    parser.add_argument("--no-bridge-refund", dest="bridge_refund", action="store_false", help="Do not release leftover bridge credits back to the wallet after the AI response.")
    parser.set_defaults(bridge_refund=True)
    parser.add_argument("--bridge-controller-private-key", default="", help="Private key for the bridge controller/hub admin wallet used for refund release. Never printed.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def run_captain(argv: list[str], *, config: MainComputerConfig | None = None, cwd: Path | None = None) -> int:
    invocation = parse_captain_invocation(argv)
    option_parser = build_captain_options_parser()
    options = option_parser.parse_args(list(invocation.option_tokens))
    base_config = config or MainComputerConfig.from_env()
    repo_cwd = cwd or Path.cwd()
    _apply_captain_live_defaults(options, base_config=base_config, cwd=repo_cwd)

    prompt = (str(options.prompt).strip() or invocation.prompt).strip()
    if not prompt:
        raise CaptainCliError("captain needs a prompt/memo after 'captain' or via --prompt.")

    selector = str(options.wallet or options.officer or invocation.selector or "captain").strip() or "captain"
    runtime = build_captain_runtime(
        base_config,
        options=options,
        selector=selector,
        cwd=repo_cwd,
    )
    smoke_id = normalize_smoke_id(options.smoke_id or f"main-computer captain smoke:{runtime.wallet.address}:{prompt}")

    effective_execute = bool(options.execute) and not bool(getattr(options, "prepare_only", False))
    max_credits = max(0, int(options.max_credits or 0)) or max(1, int(options.stipend_credits or 1))
    requested_ring = _parse_ring(options.ring)
    if options.idempotency_key:
        idempotency_key = str(options.idempotency_key).strip()
    elif invocation.smoke and effective_execute:
        idempotency_key = normalize_smoke_id(f"{smoke_id}:hub-request:{time.time_ns()}")
    else:
        idempotency_key = str(smoke_id).strip()

    account_id = wallet_account_id(runtime.wallet.address)
    use_bridge = bool(not options.no_hub and not options.no_bridge)
    bridge_credit_wei = _bridge_credit_wei(options.bridge_credits) if use_bridge else 0

    result: dict[str, Any] = {
        "ok": True,
        "mode": "captain-smoke" if invocation.smoke else "captain",
        "smoke": bool(invocation.smoke),
        "execute": bool(effective_execute),
        "prompt": prompt,
        "selector": selector,
        "wallet": {
            "selector": runtime.wallet.selector,
            "office": runtime.wallet.office,
            "title": runtime.wallet.title,
            "address": runtime.wallet.address,
            "account_id": account_id,
            "has_private_key": bool(runtime.wallet.private_key),
        },
        "hub": {
            "enabled": not bool(options.no_hub),
            "network": runtime.network,
            "url": runtime.config.hub_url,
            "ring": requested_ring,
            "account_id": account_id,
            "bridge_enabled": use_bridge,
        },
        "chain": {
            "enabled": not bool(options.no_chain),
            "rpc_url": runtime.rpc_url,
            "chain_id": runtime.chain_id,
            "xlag_address": runtime.xlag_address,
            "bridge_escrow_address": runtime.bridge_escrow_address,
            "bridge_controller_address": runtime.bridge_controller_address,
            "smoke_id": smoke_id,
        },
    }

    if use_bridge:
        result["hub"]["bridge"] = {
            "enabled": True,
            "deposit_credit_wei": str(bridge_credit_wei),
            "deposit_credits_display": credit_wei_to_decimal_text(bridge_credit_wei),
            "refund_enabled": bool(options.bridge_refund),
        }

    if not options.no_hub:
        if use_bridge:
            result["hub"]["balance_start"] = _fetch_hub_credit_balance(
                runtime.config.hub_url,
                wallet_address=runtime.wallet.address,
                timeout_s=options.timeout_s,
            )
        quote_payload = build_hub_request_payload(
            prompt=prompt,
            model=options.model or base_config.model,
            client_node_id=options.client_node_id,
            wallet_address=runtime.wallet.address,
            max_credits=max_credits,
            requested_ring=requested_ring,
            idempotency_key=idempotency_key,
            smoke_id=smoke_id,
            quote_id="",
        )
        quote = _post_hub_json(
            runtime.config.hub_url,
            "/api/hub/v1/requests/quote",
            quote_payload,
            timeout_s=options.timeout_s,
        )
        result["hub"]["quote"] = quote

    if not effective_execute:
        result["ok"] = True
        _print_captain_result(result, json_output=bool(options.json))
        return 0

    if not options.no_hub:
        if use_bridge:
            bridge = result["hub"]["bridge"]
            bridge["deposit"] = send_captain_bridge_deposit(
                runtime,
                deposit_credit_wei=bridge_credit_wei,
                smoke_id=smoke_id,
                timeout_s=options.timeout_s,
            )
            completion_payload = {
                "deposit_id": bridge["deposit"]["deposit_id"],
                "wallet_address": runtime.wallet.address,
                "tx_hash": bridge["deposit"]["transaction_hash"],
                "contract_address": runtime.bridge_escrow_address,
                "chain_id": runtime.chain_id,
            }
            try:
                bridge["completion"] = _post_hub_json(
                    runtime.config.hub_url,
                    "/api/hub/v1/credits/wallet-funding/complete",
                    completion_payload,
                    timeout_s=options.timeout_s,
                )
            except CaptainCliError as exc:
                if not _is_missing_bridge_completion_metadata_error(exc):
                    raise
                import_payload = build_bridge_wallet_funding_import_payload(runtime, bridge["deposit"])
                bridge["completion_fallback"] = {
                    "ok": True,
                    "reason": "hub completion endpoint is missing bridge deployment metadata",
                    "failed_endpoint": "/api/hub/v1/credits/wallet-funding/complete",
                    "fallback_endpoint": "/api/hub/v1/credits/wallet-funding/import",
                    "error": str(exc),
                }
                bridge["completion"] = _post_hub_json(
                    runtime.config.hub_url,
                    "/api/hub/v1/credits/wallet-funding/import",
                    import_payload,
                    timeout_s=options.timeout_s,
                )
            result["hub"]["balance_after_bridge"] = _fetch_hub_credit_balance(
                runtime.config.hub_url,
                wallet_address=runtime.wallet.address,
                timeout_s=options.timeout_s,
            )
        elif not options.no_stipend:
            result["hub"]["stipend"] = _post_hub_json(
                runtime.config.hub_url,
                "/api/hub/v1/credits/admin/issue",
                {
                    "account_id": account_id,
                    "owner_address": runtime.wallet.address,
                    "credits": max(1, int(options.stipend_credits or 1)),
                    "memo": f"captain smoke stipend {smoke_id}",
                    "metadata": {
                        "mode": "captain-smoke-stipend-v1",
                        "requested_ring": requested_ring,
                        "smoke_id": smoke_id,
                    },
                },
                timeout_s=options.timeout_s,
            )
        quote_id = _quote_id(result.get("hub", {}).get("quote"))
        submit_payload = build_hub_request_payload(
            prompt=prompt,
            model=options.model or base_config.model,
            client_node_id=options.client_node_id,
            wallet_address=runtime.wallet.address,
            max_credits=max_credits,
            requested_ring=requested_ring,
            idempotency_key=idempotency_key,
            smoke_id=smoke_id,
            quote_id=quote_id,
        )
        submit_payload["execution_mode"] = PHASE9_EXECUTION_MODE
        result["hub"]["submit"] = _post_hub_json(
            runtime.config.hub_url,
            "/api/hub/v1/requests",
            submit_payload,
            timeout_s=options.timeout_s,
        )
        request_id = _submitted_request_id(result["hub"]["submit"])
        if request_id and options.poll_seconds and options.poll_seconds > 0:
            result["hub"]["poll"] = _poll_hub_request(
                runtime.config.hub_url,
                request_id,
                timeout_s=options.timeout_s,
                poll_seconds=options.poll_seconds,
            )
            if _hub_request_state(result["hub"]["poll"]) == "completed":
                result["hub"]["result"] = _pickup_hub_request_result(
                    runtime.config.hub_url,
                    request_id,
                    account_id=account_id,
                    client_node_id=options.client_node_id,
                    timeout_s=options.timeout_s,
                )
        if use_bridge:
            result["hub"]["balance_after_request"] = _fetch_hub_credit_balance(
                runtime.config.hub_url,
                wallet_address=runtime.wallet.address,
                timeout_s=options.timeout_s,
            )
            if options.bridge_refund:
                bridge = result["hub"]["bridge"]
                if not request_id:
                    bridge["refund"] = {"ok": False, "skipped": True, "reason": "hub request id was not returned"}
                elif _hub_request_state(result.get("hub", {}).get("poll")) not in {"completed", "failed", "cancelled"}:
                    bridge["refund"] = {
                        "ok": False,
                        "skipped": True,
                        "reason": "hub request is not terminal; leaving bridged credits available",
                        "request_state": _hub_request_state(result.get("hub", {}).get("poll")),
                    }
                else:
                    charges = _fetch_hub_request_charges(
                        runtime.config.hub_url,
                        request_id,
                        timeout_s=options.timeout_s,
                    )
                    bridge["charges"] = charges
                    bridge["refund"] = send_captain_bridge_refund(
                        runtime,
                        bridge_credit_wei=bridge_credit_wei,
                        charged_credit_wei=_sum_charge_credit_wei(charges),
                        smoke_id=smoke_id,
                        request_id=request_id,
                        controller_private_key=str(options.bridge_controller_private_key or ""),
                        timeout_s=options.timeout_s,
                    )
                    if bridge["refund"].get("hub_record_payload"):
                        bridge["refund"]["hub_record"] = _post_hub_json(
                            runtime.config.hub_url,
                            "/api/hub/v1/credits/bridge-reconciliation/record",
                            bridge["refund"]["hub_record_payload"],
                            timeout_s=options.timeout_s,
                        )
                    result["hub"]["balance_after_refund"] = _fetch_hub_credit_balance(
                        runtime.config.hub_url,
                        wallet_address=runtime.wallet.address,
                        timeout_s=options.timeout_s,
                    )

    if not options.no_chain:
        if not runtime.wallet.private_key:
            raise CaptainCliError(
                f"No private key is configured for {runtime.wallet.selector} {runtime.wallet.address}. "
                "Use --private-key or a dev deployment with known local office keys."
            )
        chain_result = send_captain_smoke_transaction(
            runtime,
            smoke_id=smoke_id,
            memo=prompt,
            timeout_s=options.timeout_s,
        )
        result["chain"]["transaction"] = chain_result

    _print_captain_result(result, json_output=bool(options.json))
    return 0

def _apply_captain_network_defaults(options: argparse.Namespace, *, base_config: MainComputerConfig, cwd: Path) -> None:
    """Select the mainnet hub/deployment by default for captain prompt requests.

    Explicit captain options always win.  Until the operator selects another
    network, a bare `main-computer captain <prompt>` should use the same mainnet
    hub profile as the captain smoke command instead of falling back to the
    local development hub URL from the process config.
    """

    if not str(getattr(options, "network", "") or "").strip():
        options.network = "mainnet"
    network = str(options.network or "mainnet").strip()

    try:
        profile = load_hub_network_registry().get(network)
    except (HubNetworkConfigError, FileNotFoundError):
        profile = None

    if profile is not None:
        if not str(getattr(options, "hub_url", "") or "").strip():
            options.hub_url = profile.hub_url
        if getattr(options, "state", None) is None and profile.deployment_manifest_path is not None:
            state_path = profile.deployment_manifest_path
            if not state_path.is_absolute():
                state_path = cwd / state_path
            options.state = state_path
        if not str(getattr(options, "rpc_url", "") or "").strip() and profile.chain_rpc_url:
            options.rpc_url = profile.chain_rpc_url
        if not int(getattr(options, "chain_id", 0) or 0) and profile.chain_id:
            options.chain_id = int(profile.chain_id)


def _apply_captain_live_defaults(options: argparse.Namespace, *, base_config: MainComputerConfig, cwd: Path) -> None:
    """Make bare `captain ...` exercise the live mainnet bridge path by default."""

    _apply_captain_network_defaults(options, base_config=base_config, cwd=cwd)

    if not bool(getattr(options, "prepare_only", False)):
        options.execute = True
    if not float(getattr(options, "poll_seconds", 0.0) or 0.0):
        options.poll_seconds = 90.0

    # The bridge escrow deposit/release is the chain-backed smoke for the
    # mainnet bridge path. Keep the legacy XLag smoke transaction available in
    # --no-bridge mode, but do not add an unrelated second transaction by
    # default for the bridged captain prompt.
    if not bool(getattr(options, "no_bridge", False)):
        options.no_chain = True


def _apply_captain_smoke_defaults(options: argparse.Namespace, *, base_config: MainComputerConfig, cwd: Path) -> None:
    """Backward-compatible smoke helper; smoke uses the same live defaults."""

    _apply_captain_live_defaults(options, base_config=base_config, cwd=cwd)


def _bridge_credit_wei(value: Any) -> int:
    amount = credit_decimal_text_to_wei(value, default="1", minimum_wei=1)
    if amount <= 0:
        raise CaptainCliError("--bridge-credits must be positive.")
    return amount


def _bridge_controller_address(deployment: dict[str, Any]) -> str:
    contracts = deployment.get("contracts") if isinstance(deployment.get("contracts"), dict) else {}
    escrow = contracts.get("hub_credit_bridge_escrow") if isinstance(contracts.get("hub_credit_bridge_escrow"), dict) else {}
    controller = str(escrow.get("bridge_controller_address") or "").strip()
    if _ADDRESS_RE.fullmatch(controller):
        return controller
    hub_admin = deployment.get("hub_admin") if isinstance(deployment.get("hub_admin"), dict) else {}
    controller = str(hub_admin.get("address") or "").strip()
    return controller if _ADDRESS_RE.fullmatch(controller) else ""




def build_captain_runtime(
    config: MainComputerConfig,
    *,
    options: argparse.Namespace,
    selector: str,
    cwd: Path,
) -> CaptainRuntime:
    network = str(options.network or os.environ.get("MAIN_COMPUTER_HUB_NETWORK") or config.hub_network or DEFAULT_HUB_NETWORK or "dev").strip()
    deployment_path = Path(options.state) if options.state else _default_state_path(network, cwd)
    deployment = _load_json(deployment_path)
    wallet = resolve_captain_wallet(
        selector,
        deployment=deployment,
        deployment_path=deployment_path,
        private_key_override=options.private_key,
        private_state_path=getattr(options, "private_state", None),
    )
    rpc_url = str(options.rpc_url or _chain_value(deployment, "rpc_url") or _chain_value(deployment, "host_rpc_url") or config.chain_rpc_url or "").strip()
    chain_id = int(options.chain_id or _chain_value(deployment, "chain_id") or config.chain_id or 0)
    xlag_address = str(options.xlag_address or _contract_address(deployment, "xlag-bridge-reserve", "XLagBridgeReserve") or config.xlag_contract_address or "").strip()
    bridge_escrow_address = str(_contract_address(deployment, "hub_credit_bridge_escrow", "HubCreditBridgeEscrow") or "").strip()
    bridge_controller_address = _bridge_controller_address(deployment)
    if not rpc_url:
        raise CaptainCliError(f"Deployment is missing a chain RPC URL: {deployment_path}")
    if not _ADDRESS_RE.fullmatch(xlag_address):
        raise CaptainCliError(f"Deployment is missing a valid XLagBridgeReserve address: {deployment_path}")

    hub_url = str(options.hub_url or config.hub_url).strip().rstrip("/")
    runtime_config = replace(
        config,
        hub_url=hub_url,
        chain_rpc_url=rpc_url,
        chain_id=chain_id,
        xlag_contract_address=xlag_address,
    )
    return CaptainRuntime(
        config=runtime_config,
        deployment_path=deployment_path,
        deployment=deployment,
        wallet=wallet,
        rpc_url=rpc_url,
        chain_id=chain_id,
        xlag_address=xlag_address,
        network=network,
        bridge_escrow_address=bridge_escrow_address,
        bridge_controller_address=bridge_controller_address,
    )

def resolve_captain_wallet(
    selector: str,
    *,
    deployment: dict[str, Any],
    deployment_path: Path,
    private_key_override: str = "",
    private_state_path: Path | None = None,
) -> CaptainWallet:
    raw = str(selector or "captain").strip()
    normalized = _selector_key(raw)
    offices = _office_records(deployment)
    office_index = _office_index_for_selector(normalized)
    address = raw if _ADDRESS_RE.fullmatch(raw) else ""
    office_record: dict[str, Any] | None = None
    if office_index is not None and office_index < len(offices):
        office_record = dict(offices[office_index])
        address = str(office_record.get("address") or "").strip()
    elif address:
        for item in offices:
            if str(item.get("address", "")).lower() == address.lower():
                office_record = dict(item)
                office_index = _office_number(str(item.get("office", "")))
                break
    else:
        raise CaptainCliError(f"Unknown captain wallet/officer selector: {selector!r}")

    if not _ADDRESS_RE.fullmatch(address):
        raise CaptainCliError(f"Selected wallet does not have a valid address: {selector!r}")

    private_key = _normalize_private_key(private_key_override)
    if not private_key:
        private_key = _private_key_from_env(office_index=office_index, selector=normalized)
    if not private_key and office_record is not None:
        private_key = _normalize_private_key(office_record.get("private_key") or office_record.get("privateKey"))
    if not private_key and office_record is not None:
        private_key = _private_key_from_wallet_path(deployment_path=deployment_path, record=office_record)
    if not private_key:
        private_key = _private_key_from_private_state(
            deployment_path=deployment_path,
            private_state_path=private_state_path,
            deployment=deployment,
            office_index=office_index,
            selector=normalized,
            address=address,
        )
    if not private_key and office_index is not None:
        private_key = _default_dev_office_private_key(deployment=deployment, office_index=office_index, address=address)

    office = str(office_record.get("office", f"O{office_index}") if office_record else "").strip()
    title = str(office_record.get("title", "Wallet") if office_record else "Wallet").strip()
    return CaptainWallet(
        selector=raw,
        office=office,
        title=title,
        address=address,
        private_key=private_key,
    )


def build_hub_request_payload(
    *,
    prompt: str,
    model: str,
    client_node_id: str,
    wallet_address: str,
    max_credits: int,
    requested_ring: int,
    idempotency_key: str,
    smoke_id: str,
    quote_id: str = "",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "mode": "captain-smoke-hub-request-v1",
        "captain_smoke": True,
        "smoke_id": smoke_id,
        "wallet_address": wallet_address,
        "requested_ring": requested_ring,
        "ring": requested_ring,
        "pricing_mode": PHASE9_PRICING_MODE,
        "execution_mode": PHASE9_EXECUTION_MODE,
        "worker_pull_v0": True,
    }
    if quote_id:
        metadata["quote_id"] = quote_id
    return {
        "model": str(model or "hub-auto"),
        "client_node_id": str(client_node_id or "main-computer-captain-cli"),
        "messages": [{"role": "user", "content": prompt}],
        "account_id": wallet_account_id(wallet_address),
        "max_credits": max(1, int(max_credits or 1)),
        "idempotency_key": idempotency_key,
        "metadata": metadata,
    }


def send_captain_smoke_transaction(
    runtime: CaptainRuntime,
    *,
    smoke_id: str,
    memo: str,
    timeout_s: float,
) -> dict[str, Any]:
    function_signature = "finalizeWalletSmokeTest(bytes32,string)" if runtime.wallet.is_office else "frobByAnyUser(bytes32,string)"
    command = _cast_send_command(
        repo_root=_repo_root_for_deployment_path(runtime.deployment_path),
        network_name=_chain_value(runtime.deployment, "network"),
        rpc_url=runtime.rpc_url,
        private_key=runtime.wallet.private_key,
        contract_address=runtime.xlag_address,
        function_signature=function_signature,
        function_args=[smoke_id, memo],
    )
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=None,
    )
    if completed.returncode != 0:
        raise CaptainCliError(
            "captain smoke chain transaction failed.\n"
            f"command={_redact_private_key(command)}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )

    tx_hash = _parse_transaction_hash((completed.stdout or "") + "\n" + (completed.stderr or ""))
    rpc = JsonRpcClient(runtime.rpc_url, timeout_s=timeout_s)
    receipt = _wait_for_receipt(rpc, tx_hash, timeout_s=max(1.0, timeout_s))
    gas = _gas_summary(receipt)
    return {
        "function": function_signature,
        "transaction_hash": tx_hash,
        "block_number": gas["block_number"],
        "gas_used": gas["gas_used"],
        "gas_price_wei": gas["gas_price_wei"],
        "gas_cost_wei": gas["gas_cost_wei"],
        "gas_cost_native": gas["gas_cost_native"],
        "receipt": receipt,
        "command": _redact_private_key(command),
    }




def _receipt_block_number(receipt: dict[str, Any] | None) -> int:
    if not isinstance(receipt, dict):
        return 0
    return _hex_int(receipt.get("blockNumber"))


def _receipt_log_index_for_contract(receipt: dict[str, Any] | None, contract_address: str) -> int:
    if not isinstance(receipt, dict):
        return 0
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        return 0
    normalized_contract = ""
    try:
        normalized_contract = normalize_address(contract_address)
    except Exception:
        normalized_contract = str(contract_address or "").strip().lower()
    first_log_index = 0
    for raw in logs:
        if not isinstance(raw, dict):
            continue
        log_index = _hex_int(raw.get("logIndex"))
        if not first_log_index:
            first_log_index = log_index
        try:
            log_address = normalize_address(str(raw.get("address") or ""))
        except Exception:
            log_address = str(raw.get("address") or "").strip().lower()
        if normalized_contract and log_address == normalized_contract:
            return log_index
    return first_log_index


def build_bridge_wallet_funding_import_payload(runtime: CaptainRuntime, deposit: dict[str, Any]) -> dict[str, Any]:
    """Build the manual wallet-funding import payload from the local deposit receipt.

    The live hub completion route can fail when the hub lacks its private bridge
    deployment/admin manifest.  The wallet-funding import route is the public
    ledger primitive that accepts the normalized deposit receipt data the captain
    CLI already has after broadcasting the deposit transaction.
    """

    receipt = deposit.get("receipt") if isinstance(deposit.get("receipt"), dict) else {}
    amount_wei = str(deposit.get("amount_credit_wei") or deposit.get("amount_units") or "").strip()
    tx_hash = str(deposit.get("transaction_hash") or deposit.get("tx_hash") or "").strip()
    if not _TX_HASH_RE.fullmatch(tx_hash):
        raise CaptainCliError("Bridge funding import cannot continue because the deposit transaction hash is missing or invalid.")
    if not amount_wei:
        raise CaptainCliError("Bridge funding import cannot continue because the deposit amount is missing.")
    return {
        "wallet_address": runtime.wallet.address,
        "payer_address": runtime.wallet.address,
        "chain_id": runtime.chain_id,
        "contract_address": runtime.bridge_escrow_address,
        "tx_hash": tx_hash,
        "log_index": _receipt_log_index_for_contract(receipt, runtime.bridge_escrow_address),
        "block_number": _receipt_block_number(receipt),
        "payment_asset": "native",
        "payment_amount_base_units": amount_wei,
        "credits_granted_wei": amount_wei,
        "memo": f"captain smoke bridge wallet funding import {deposit.get('deposit_id', '')}",
        "metadata": {
            "mode": "captain-smoke-bridge-import-fallback-v1",
            "deposit_id": str(deposit.get("deposit_id") or ""),
            "completion_fallback": True,
        },
    }


def _is_missing_bridge_completion_metadata_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    if "/wallet-funding/complete" not in text and "wallet-funding/complete" not in text:
        return False
    return (
        "hub_credit_bridge_escrow" in text
        and (
            "current.json" in text
            or "deployment" in text
            or "manifest" in text
            or "metadata" in text
        )
    )

def send_captain_bridge_deposit(
    runtime: CaptainRuntime,
    *,
    deposit_credit_wei: int,
    smoke_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    if not _ADDRESS_RE.fullmatch(runtime.bridge_escrow_address):
        raise CaptainCliError(f"Deployment is missing a valid HubCreditBridgeEscrow address: {runtime.deployment_path}")
    if not runtime.wallet.private_key:
        raise CaptainCliError(
            f"No private key is configured for bridge deposit wallet {runtime.wallet.address}. "
            "Use --private-key or configure the selected captain wallet."
        )
    deposit_id = normalize_smoke_id(f"{smoke_id}:bridge-deposit:{runtime.wallet.address}:{time.time_ns()}")
    memo = f"captain smoke bridge deposit {smoke_id}"
    tx = send_captain_contract_transaction(
        runtime,
        contract_address=runtime.bridge_escrow_address,
        private_key=runtime.wallet.private_key,
        function_signature="depositFor(address,uint256,bytes32,string)",
        function_args=[runtime.wallet.address, str(deposit_credit_wei), deposit_id, memo],
        value_wei=deposit_credit_wei,
        timeout_s=timeout_s,
    )
    return {
        **tx,
        "deposit_id": deposit_id,
        "wallet_address": runtime.wallet.address,
        "account_id": wallet_account_id(runtime.wallet.address),
        "amount_credit_wei": str(deposit_credit_wei),
        "amount_credits_display": credit_wei_to_decimal_text(deposit_credit_wei),
        "memo": memo,
    }


def send_captain_bridge_refund(
    runtime: CaptainRuntime,
    *,
    bridge_credit_wei: int,
    charged_credit_wei: int,
    smoke_id: str,
    request_id: str,
    controller_private_key: str,
    timeout_s: float,
) -> dict[str, Any]:
    charged_credit_wei = max(0, int(charged_credit_wei or 0))
    bridge_credit_wei = max(0, int(bridge_credit_wei or 0))
    rectified_credit_wei = min(charged_credit_wei, bridge_credit_wei)
    refund_credit_wei = max(0, bridge_credit_wei - rectified_credit_wei)
    result: dict[str, Any] = {
        "ok": True,
        "request_id": request_id,
        "charged_credit_wei": str(charged_credit_wei),
        "rectified_credit_wei": str(rectified_credit_wei),
        "refund_credit_wei": str(refund_credit_wei),
        "refund_credits_display": credit_wei_to_decimal_text(refund_credit_wei),
    }
    if refund_credit_wei <= 0 and rectified_credit_wei <= 0:
        result.update({"skipped": True, "reason": "no rectification or refund is required"})
        return result

    private_key = _resolve_bridge_controller_private_key(runtime, override=controller_private_key)
    if not private_key:
        raise CaptainCliError(
            "Bridge refund requires the bridge controller private key. "
            "Use --bridge-controller-private-key, MAIN_COMPUTER_BRIDGE_CONTROLLER_PRIVATE_KEY, "
            "MAIN_COMPUTER_HUB_ADMIN_PRIVATE_KEY, or a hub_admin wallet_path in the deployment manifest."
        )

    if rectified_credit_wei > 0:
        rectification_id = normalize_smoke_id(
            f"{smoke_id}:bridge-rectify:{request_id}:{runtime.wallet.address}:{rectified_credit_wei}"
        )
        result["rectification_id"] = rectification_id
        result["rectification"] = send_captain_contract_transaction(
            runtime,
            contract_address=runtime.bridge_escrow_address,
            private_key=private_key,
            function_signature="rectifySpend(address,uint256,bytes32,string)",
            function_args=[
                runtime.wallet.address,
                str(rectified_credit_wei),
                rectification_id,
                f"captain smoke spend rectification {request_id}",
            ],
            value_wei=0,
            timeout_s=timeout_s,
        )

    if refund_credit_wei > 0:
        withdrawal_id = normalize_smoke_id(
            f"{smoke_id}:bridge-withdrawal:{request_id}:{runtime.wallet.address}:{refund_credit_wei}"
        )
        result["withdrawal_id"] = withdrawal_id
        result["withdrawal"] = send_captain_contract_transaction(
            runtime,
            contract_address=runtime.bridge_escrow_address,
            private_key=private_key,
            function_signature="releaseWithdrawal(address,address,uint256,bytes32,string)",
            function_args=[
                runtime.wallet.address,
                runtime.wallet.address,
                str(refund_credit_wei),
                withdrawal_id,
                f"captain smoke bridge refund {request_id}",
            ],
            value_wei=0,
            timeout_s=timeout_s,
        )

    # The current reconciliation endpoint names the integer unit fields
    # *_credits, while the bridge contract and ledger carry the atomic unit
    # amount through metadata/display fields.  Keep the exact atomic amounts in
    # metadata so operators can audit the chain movement even on hubs that still
    # store only whole-credit reconciliation rows.
    record_rectified = _whole_credit_floor(rectified_credit_wei)
    record_withdrawn = _whole_credit_floor(refund_credit_wei)
    if record_rectified > 0 or record_withdrawn > 0:
        result["hub_record_payload"] = {
            "account_id": wallet_account_id(runtime.wallet.address),
            "rectified_credits": record_rectified,
            "withdrawn_credits": record_withdrawn,
            "rectification_id": result.get("rectification_id", ""),
            "withdrawal_id": result.get("withdrawal_id", ""),
            "recipient_address": runtime.wallet.address,
            "memo": f"captain smoke bridge refund {request_id}",
            "metadata": {
                "mode": "captain-smoke-bridge-refund-v1",
                "smoke_id": smoke_id,
                "request_id": request_id,
                "bridge_credit_wei": str(bridge_credit_wei),
                "charged_credit_wei": str(charged_credit_wei),
                "rectified_credit_wei": str(rectified_credit_wei),
                "refund_credit_wei": str(refund_credit_wei),
                "rectification_tx_hash": (
                    result.get("rectification", {}).get("transaction_hash")
                    if isinstance(result.get("rectification"), dict)
                    else ""
                ),
                "withdrawal_tx_hash": (
                    result.get("withdrawal", {}).get("transaction_hash")
                    if isinstance(result.get("withdrawal"), dict)
                    else ""
                ),
            },
        }
    else:
        result["hub_record_skipped"] = {
            "reason": "reconciliation endpoint currently records whole-credit rows only",
            "rectified_credit_wei": str(rectified_credit_wei),
            "refund_credit_wei": str(refund_credit_wei),
        }
    return result


def send_captain_contract_transaction(
    runtime: CaptainRuntime,
    *,
    contract_address: str,
    private_key: str,
    function_signature: str,
    function_args: list[str],
    value_wei: int = 0,
    timeout_s: float,
) -> dict[str, Any]:
    command = _cast_send_command(
        repo_root=_repo_root_for_deployment_path(runtime.deployment_path),
        network_name=_chain_value(runtime.deployment, "network"),
        rpc_url=runtime.rpc_url,
        private_key=private_key,
        contract_address=contract_address,
        function_signature=function_signature,
        function_args=function_args,
        value_wei=value_wei,
    )
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=None,
    )
    if completed.returncode != 0:
        raise CaptainCliError(
            "captain bridge transaction failed.\n"
            f"command={_redact_private_key(command)}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
    tx_hash = _parse_transaction_hash((completed.stdout or "") + "\n" + (completed.stderr or ""))
    rpc = JsonRpcClient(runtime.rpc_url, timeout_s=timeout_s)
    receipt = _wait_for_receipt(rpc, tx_hash, timeout_s=max(1.0, timeout_s))
    gas = _gas_summary(receipt)
    return {
        "function": function_signature,
        "transaction_hash": tx_hash,
        "block_number": gas["block_number"],
        "gas_used": gas["gas_used"],
        "gas_price_wei": gas["gas_price_wei"],
        "gas_cost_wei": gas["gas_cost_wei"],
        "gas_cost_native": gas["gas_cost_native"],
        "receipt": receipt,
        "command": _redact_private_key(command),
    }


def _whole_credit_floor(credit_wei: int) -> int:
    # Avoid importing the constant into this operator-facing module just for
    # display/recording; keep the representation consistent with
    # credit_wei_to_decimal_text.
    return max(0, int(credit_wei or 0)) // 10**18


def _resolve_bridge_controller_private_key(runtime: CaptainRuntime, *, override: str = "") -> str:
    for value in (
        override,
        os.environ.get("MAIN_COMPUTER_BRIDGE_CONTROLLER_PRIVATE_KEY"),
        os.environ.get("MAIN_COMPUTER_HUB_ADMIN_PRIVATE_KEY"),
    ):
        if value:
            return _normalize_private_key(value)

    hub_admin = runtime.deployment.get("hub_admin") if isinstance(runtime.deployment.get("hub_admin"), dict) else {}
    inline = _normalize_private_key(hub_admin.get("private_key") or hub_admin.get("privateKey") or "")
    if inline:
        return inline

    wallet_path_text = str(
        os.environ.get("MAIN_COMPUTER_HUB_ADMIN_WALLET_PATH")
        or os.environ.get("MAIN_COMPUTER_BRIDGE_CONTROLLER_WALLET_PATH")
        or hub_admin.get("wallet_path")
        or hub_admin.get("walletPath")
        or ""
    ).strip()
    if not wallet_path_text:
        return ""
    raw = Path(wallet_path_text)
    candidates = [raw] if raw.is_absolute() else [
        _repo_root_for_deployment_path(runtime.deployment_path) / raw,
        runtime.deployment_path.parent / raw,
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = _load_json(candidate)
        address = str(payload.get("address") or "").strip()
        if runtime.bridge_controller_address and _ADDRESS_RE.fullmatch(address):
            if normalize_address(address) != normalize_address(runtime.bridge_controller_address):
                raise CaptainCliError(
                    f"Bridge controller wallet address {address} does not match deployment {runtime.bridge_controller_address}."
                )
        return _normalize_private_key(payload.get("private_key") or payload.get("privateKey"))
    return ""



def normalize_smoke_id(value: str) -> str:
    raw = str(value or "main-computer-captain-smoke").strip()
    if _BYTES32_RE.fullmatch(raw):
        return raw.lower()
    return "0x" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _split_free_and_option_tokens(argv: list[str]) -> tuple[list[str], list[str]]:
    for index, token in enumerate(argv):
        if str(token).startswith("--") and str(token) != "--":
            return list(argv[:index]), list(argv[index:])
    return list(argv), []


def _is_smoke_token(token: str) -> bool:
    return _selector_key(token) in {"smoke", "smoketest", "smoke-test", "smoke_testing", "smoke-testing"}


def _consume_selector(tokens: list[str]) -> tuple[int, str]:
    if not tokens:
        return 0, ""
    first = str(tokens[0]).strip()
    key = _selector_key(first)
    if _ADDRESS_RE.fullmatch(first):
        return 1, first
    if key == "wallet" and len(tokens) >= 2:
        return 2, str(tokens[1]).strip()
    if key in {"officer", "office"} and len(tokens) >= 2:
        return 2, str(tokens[1]).strip()
    if _office_index_for_selector(key) is not None:
        return 1, first
    return 0, ""


def _selector_key(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return re.sub(r"[\s]+", "-", text)


def _office_index_for_selector(key: str) -> int | None:
    normalized = _selector_key(key)
    mapping = {
        "captain": 0,
        "o0": 0,
        "office0": 0,
        "office-0": 0,
        "ring0": 0,
        "ring-0": 0,
        "first": 1,
        "first-officer": 1,
        "o1": 1,
        "office1": 1,
        "office-1": 1,
        "ring1": 1,
        "ring-1": 1,
        "second": 2,
        "second-officer": 2,
        "o2": 2,
        "office2": 2,
        "office-2": 2,
        "ring2": 2,
        "ring-2": 2,
        "third": 3,
        "third-officer": 3,
        "o3": 3,
        "office3": 3,
        "office-3": 3,
        "ring3": 3,
        "ring-3": 3,
    }
    return mapping.get(normalized)


def _office_number(value: str) -> int | None:
    match = re.fullmatch(r"[Oo](\d+)", str(value or "").strip())
    if not match:
        return None
    return int(match.group(1))


def _parse_ring(value: object) -> int:
    text = str(value or "3").strip().lower()
    if text.startswith("ring"):
        text = text[4:].lstrip("-_")
    try:
        ring = int(text)
    except ValueError as exc:
        raise CaptainCliError(f"--ring must be an integer or ringN value: {value!r}") from exc
    if ring < 0 or ring > 3:
        raise CaptainCliError("--ring must be between 0 and 3.")
    return ring


def _default_state_path(network: str, cwd: Path) -> Path:
    network = str(network or "dev").strip() or "dev"
    candidate = cwd / "runtime" / "deployments" / network / "latest.json"
    if candidate.exists():
        return candidate
    if DEFAULT_STATE_FILE.exists():
        return DEFAULT_STATE_FILE
    return cwd / LEGACY_STATE_FILE



def _repo_root_for_deployment_path(path: Path) -> Path:
    resolved = Path(path)
    # runtime/deployments/<network>/latest.json -> repo root
    if len(resolved.parents) >= 4 and resolved.parent.parent.name == "deployments" and resolved.parent.parent.parent.name == "runtime":
        return resolved.parent.parent.parent.parent
    if len(resolved.parents) >= 3 and resolved.parent.name == "deployments" and resolved.parent.parent.name == "runtime":
        return resolved.parent.parent.parent
    return Path.cwd()

def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaptainCliError(f"Deployment state file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CaptainCliError(f"Deployment state file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CaptainCliError(f"Deployment state root must be a JSON object: {path}")
    return payload


def _chain_value(deployment: dict[str, Any], key: str) -> Any:
    chain = deployment.get("chain") if isinstance(deployment.get("chain"), dict) else {}
    return chain.get(key)


def _contract_address(deployment: dict[str, Any], *names: str) -> str:
    lowered = {name.lower() for name in names}
    for container_name in ("contracts", "deployments"):
        container = deployment.get(container_name)
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            key_text = str(key).lower()
            target = ""
            address: Any = value
            if isinstance(value, dict):
                target = str(value.get("target") or value.get("contract") or value.get("name") or "").lower()
                address = value.get("address")
            if key_text in lowered or any(name in target for name in lowered):
                text = str(address or "").strip()
                if _ADDRESS_RE.fullmatch(text):
                    return text
    return ""


def _office_records(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    offices = deployment.get("offices")
    if not isinstance(offices, list) or not offices:
        return [dict(item) for item in _DEV_OFFICE_DEFAULTS]

    records: list[dict[str, Any]] = []
    for index, raw in enumerate(offices[:4]):
        if not isinstance(raw, dict):
            continue
        fallback = _DEV_OFFICE_DEFAULTS[index] if index < len(_DEV_OFFICE_DEFAULTS) else {}
        address = str(raw.get("address") or raw.get("account") or fallback.get("address") or "").strip()
        records.append(
            {
                "office": str(raw.get("office") or fallback.get("office") or f"O{index}"),
                "title": str(raw.get("title") or fallback.get("title") or f"Office {index}"),
                "address": address,
                "private_key": str(raw.get("private_key") or raw.get("privateKey") or "").strip(),
                "wallet_path": str(raw.get("wallet_path") or raw.get("walletPath") or "").strip(),
            }
        )
    while len(records) < 4:
        records.append(dict(_DEV_OFFICE_DEFAULTS[len(records)]))
    return records


def _normalize_private_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("0x"):
        text = "0x" + text
    if not _PRIVATE_KEY_RE.fullmatch(text):
        raise CaptainCliError("Private key must be a 32-byte 0x-prefixed hex value.")
    return text


def _private_key_from_env(*, office_index: int | None, selector: str) -> str:
    names: list[str] = []
    if office_index is not None:
        names.extend(
            [
                f"MAIN_COMPUTER_DEV_OFFICE_{office_index}_PRIVATE_KEY",
                f"MAIN_COMPUTER_OFFICE_{office_index}_PRIVATE_KEY",
            ]
        )
        if office_index == 0:
            names.append("MAIN_COMPUTER_CAPTAIN_PRIVATE_KEY")
        else:
            names.append(f"MAIN_COMPUTER_OFFICER_{office_index}_PRIVATE_KEY")
    safe_selector = re.sub(r"[^A-Z0-9]+", "_", selector.upper()).strip("_")
    if safe_selector:
        names.append(f"MAIN_COMPUTER_{safe_selector}_PRIVATE_KEY")
    for name in names:
        value = os.environ.get(name)
        if value:
            return _normalize_private_key(value)
    return ""



def _deployment_network_name(deployment_path: Path, deployment: dict[str, Any] | None = None) -> str:
    chain = deployment.get("chain") if isinstance(deployment, dict) else {}
    if isinstance(chain, dict):
        network = str(chain.get("network_name") or chain.get("target_environment") or "").strip()
        if network:
            return network
    resolved = Path(deployment_path)
    if resolved.parent.name:
        return resolved.parent.name
    return ""


def _private_state_candidate_paths(*, deployment_path: Path, private_state_path: Path | None) -> list[Path]:
    repo_root = _repo_root_for_deployment_path(deployment_path)
    candidates: list[Path] = []
    env_path = str(os.environ.get("MAIN_COMPUTER_PRIVATE_STATE_PATH") or "").strip()
    if env_path:
        raw = Path(env_path)
        candidates.append(raw if raw.is_absolute() else repo_root / raw)
    if private_state_path is not None:
        raw = Path(private_state_path)
        candidates.append(raw if raw.is_absolute() else repo_root / raw)
    candidates.append(repo_root / PRIVATE_STATE_RELATIVE_PATH)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _strip_yaml_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    out: list[str] = []
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and in_double:
            out.append(char)
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            out.append(char)
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            out.append(char)
            continue
        if char == "#" and not in_single and not in_double:
            break
        out.append(char)
    return "".join(out).rstrip()


def _parse_private_state_scalar(value: str) -> Any:
    text = _strip_yaml_comment(value).strip()
    if text in {"", "null", "Null", "NULL", "~"}:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _parse_simple_private_state_yaml(text: str) -> dict[str, Any]:
    """Parse the private-state subset used for nested wallet key maps.

    The runtime package keeps core dependencies minimal, so the captain command
    does not require PyYAML just to read operator wallet keys.  This parser is
    intentionally small: it handles indented mapping entries with scalar values,
    which is enough for networks.<network>.wallets.<role>.{address,private_key}.
    """

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        raw = line.rstrip()
        if raw.lstrip().startswith("- "):
            continue
        stripped = raw.strip()
        if ":" not in stripped:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, value = stripped.split(":", 1)
        key = key.strip().strip('"').strip("'")
        if not key:
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(-1, root)]
        parent = stack[-1][1]
        value = value.strip()
        if not value or value.startswith("#"):
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_private_state_scalar(value)
    return root


def _load_private_state(path: Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        payload = json.loads(text)
    else:
        payload = _parse_simple_private_state_yaml(text)
    return payload if isinstance(payload, dict) else {}


def _wallet_role_candidates(*, office_index: int | None, selector: str) -> list[str]:
    aliases_by_office = {
        0: ("captain", "o0", "office0", "officer0"),
        1: ("o1", "first", "first_officer", "office1", "officer1"),
        2: ("o2", "second", "second_officer", "office2", "officer2"),
        3: ("o3", "third", "third_officer", "office3", "officer3"),
    }
    candidates: list[str] = []
    if selector:
        candidates.append(selector)
    if office_index in aliases_by_office:
        candidates.extend(aliases_by_office[int(office_index)])
    unique: list[str] = []
    for item in candidates:
        key = _selector_key(item)
        if key and key not in unique:
            unique.append(key)
    return unique


def _private_key_from_private_state(
    *,
    deployment_path: Path,
    private_state_path: Path | None,
    deployment: dict[str, Any],
    office_index: int | None,
    selector: str,
    address: str,
) -> str:
    network = _deployment_network_name(deployment_path, deployment)
    if not network:
        return ""

    for path in _private_state_candidate_paths(deployment_path=deployment_path, private_state_path=private_state_path):
        if not path.exists():
            continue
        state = _load_private_state(path)
        networks = state.get("networks") if isinstance(state.get("networks"), dict) else {}
        network_state = networks.get(network) if isinstance(networks.get(network), dict) else {}
        wallets = network_state.get("wallets") if isinstance(network_state.get("wallets"), dict) else {}
        for role in _wallet_role_candidates(office_index=office_index, selector=selector):
            entry = wallets.get(role)
            if not isinstance(entry, dict):
                continue
            entry_address = str(entry.get("address") or "").strip()
            if entry_address and _ADDRESS_RE.fullmatch(entry_address) and normalize_address(entry_address) != normalize_address(address):
                continue
            private_key = _normalize_private_key(entry.get("private_key") or entry.get("privateKey") or "")
            if private_key:
                return private_key
    return ""


def _private_key_from_wallet_path(*, deployment_path: Path, record: dict[str, Any]) -> str:
    path_text = str(record.get("wallet_path") or "").strip()
    if not path_text:
        return ""
    raw = Path(path_text)
    candidates = [raw] if raw.is_absolute() else [
        _repo_root_for_deployment_path(deployment_path) / raw,
        deployment_path.parent / raw,
    ]
    for candidate in candidates:
        if candidate.exists():
            payload = _load_json(candidate)
            return _normalize_private_key(payload.get("private_key") or payload.get("privateKey"))
    return ""


def _default_dev_office_private_key(*, deployment: dict[str, Any], office_index: int, address: str) -> str:
    chain_id = int(_chain_value(deployment, "chain_id") or 0)
    if chain_id != 42424242:
        return ""
    if office_index < 0 or office_index >= len(_DEV_OFFICE_DEFAULTS):
        return ""
    default = _DEV_OFFICE_DEFAULTS[office_index]
    if str(default["address"]).lower() != address.lower():
        return ""
    return default["private_key"]


def _cast_send_command(
    *,
    repo_root: Path,
    network_name: Any,
    rpc_url: str,
    private_key: str,
    contract_address: str,
    function_signature: str,
    function_args: list[str],
    value_wei: int = 0,
) -> list[str]:
    value_args = ["--value", str(max(0, int(value_wei or 0)))] if int(value_wei or 0) > 0 else []
    local_cast = shutil.which("cast")
    if local_cast:
        command = [
            local_cast,
            "send",
            contract_address,
            function_signature,
            *function_args,
            *value_args,
            "--rpc-url",
            rpc_url,
            "--private-key",
            private_key,
            "--json",
        ]
        return command

    runtime = resolve_container_runtime(cwd=repo_root, probe=False)
    command = runtime.container_args("run", "--rm")
    network = str(network_name or "").strip()
    if network:
        command.extend(["--network", network])
    command.extend(
        [
            "-v",
            f"{_docker_mount_path(repo_root)}:/workspace",
            "-w",
            "/workspace/contracts",
            "--entrypoint",
            "cast",
            "ghcr.io/foundry-rs/foundry:latest",
            "send",
            contract_address,
            function_signature,
            *function_args,
            *value_args,
            "--rpc-url",
            rpc_url,
            "--private-key",
            private_key,
            "--json",
        ]
    )
    return command


def _docker_mount_path(path: Path) -> str:
    resolved = Path(path).resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def _parse_transaction_hash(output: str) -> str:
    for text in [output]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("transactionHash", "transaction_hash", "hash"):
                value = str(payload.get(key) or "").strip()
                if _TX_HASH_RE.fullmatch(value):
                    return value
            receipt = payload.get("receipt")
            if isinstance(receipt, dict):
                for key in ("transactionHash", "transaction_hash", "hash"):
                    value = str(receipt.get(key) or "").strip()
                    if _TX_HASH_RE.fullmatch(value):
                        return value
    match = _TX_HASH_RE.search(output)
    if match:
        return match.group(0)
    raise CaptainCliError(f"could not parse transaction hash from cast output: {output!r}")


def _wait_for_receipt(rpc: JsonRpcClient, tx_hash: str, *, timeout_s: float) -> dict[str, Any]:
    deadline = time.time() + max(1.0, timeout_s)
    while time.time() <= deadline:
        receipt = rpc.transaction_receipt(tx_hash)
        if isinstance(receipt, dict):
            status = receipt.get("status")
            if isinstance(status, str) and status.lower() == "0x0":
                raise CaptainCliError(f"captain smoke transaction failed: {tx_hash}")
            return receipt
        time.sleep(0.5)
    raise CaptainCliError(f"Timed out waiting for captain smoke receipt: {tx_hash}")


def _hex_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(text, 16 if text.startswith("0x") else 10)
    except ValueError:
        return default


def _gas_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    gas_used = _hex_int(receipt.get("gasUsed"))
    gas_price = _hex_int(receipt.get("effectiveGasPrice") or receipt.get("gasPrice"))
    gas_cost = gas_used * gas_price if gas_used and gas_price else 0
    return {
        "block_number": _hex_int(receipt.get("blockNumber")),
        "gas_used": gas_used,
        "gas_price_wei": gas_price,
        "gas_cost_wei": gas_cost,
        "gas_cost_native": credit_wei_to_decimal_text(gas_cost) if gas_cost else "unknown",
    }


def _hub_http_headers(*, json_body: bool = False) -> dict[str, str]:
    user_agent = str(os.environ.get("MAIN_COMPUTER_HUB_USER_AGENT") or "").strip() or _DEFAULT_HUB_USER_AGENT
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
        "X-Main-Computer-Client": "captain-cli",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _hub_http_error(url: str, exc: HTTPError, body: str) -> CaptainCliError:
    message = f"Hub request failed for {url} with HTTP {exc.code}: {body}"
    if exc.code == 403 and "error code: 1010" in body.lower():
        message += (
            "\nCloudflare rejected this HTTP client signature. The CLI now sends explicit API client "
            "headers; if this still fails, the mainnet hub Cloudflare rules need to allow "
            f"{_DEFAULT_HUB_USER_AGENT!r} or set MAIN_COMPUTER_HUB_USER_AGENT to an allowed client signature."
        )
    return CaptainCliError(message)


def _post_hub_json(hub_url: str, path: str, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    url = hub_url.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_hub_http_headers(json_body=True),
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_s or 30.0))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise _hub_http_error(url, exc, body) from exc
    except URLError as exc:
        raise CaptainCliError(f"Hub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptainCliError("Hub returned a non-object JSON response.")
    if data.get("error") and not data.get("ok", False):
        raise CaptainCliError(str(data.get("error")))
    return data


def _poll_hub_request(hub_url: str, request_id: str, *, timeout_s: float, poll_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(0.0, poll_seconds)
    last: dict[str, Any] = {}
    while time.time() <= deadline:
        last = _get_hub_json(hub_url, f"/api/hub/v1/requests/{request_id}", timeout_s=timeout_s)
        request = last.get("request") if isinstance(last.get("request"), dict) else last
        state = str(request.get("state", "") if isinstance(request, dict) else "").lower()
        if state in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(1.0)
    return last or {"ok": False, "error": "poll timeout", "request_id": request_id}


def _get_hub_json(hub_url: str, path: str, *, timeout_s: float) -> dict[str, Any]:
    url = hub_url.rstrip("/") + path
    request = Request(url, headers=_hub_http_headers(), method="GET")
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_s or 30.0))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise _hub_http_error(url, exc, body) from exc
    except URLError as exc:
        raise CaptainCliError(f"Hub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptainCliError("Hub returned a non-object JSON response.")
    return data


def _fetch_hub_credit_balance(hub_url: str, *, wallet_address: str, timeout_s: float) -> dict[str, Any]:
    query = urlencode({"wallet_address": wallet_address})
    return _get_hub_json(hub_url, f"/api/hub/v1/credits/balance?{query}", timeout_s=timeout_s)


def _fetch_hub_request_charges(hub_url: str, request_id: str, *, timeout_s: float) -> dict[str, Any]:
    query = urlencode({"request_id": request_id, "limit": "100"})
    return _get_hub_json(hub_url, f"/api/hub/v1/credits/charges?{query}", timeout_s=timeout_s)


def _sum_charge_credit_wei(charges_response: Any) -> int:
    if not isinstance(charges_response, dict):
        return 0
    charges = charges_response.get("charges")
    if not isinstance(charges, list):
        return 0
    total = 0
    for item in charges:
        if not isinstance(item, dict):
            continue
        total += _as_nonnegative_int(item.get("charged_credit_wei"), default=0)
    return total


def _as_nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


def _hub_request_state(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    request = payload.get("request") if isinstance(payload.get("request"), dict) else payload
    return str(request.get("state", "") if isinstance(request, dict) else "").strip().lower()


def _is_hub_request_result_not_found_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    if "with http 404" not in text and "not found" not in text:
        return False
    if "/api/hub/v1/requests/" not in text:
        return False
    return "/result" in text or "/pickup" in text


def _pickup_hub_request_result(
    hub_url: str,
    request_id: str,
    *,
    account_id: str,
    client_node_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    query = urlencode({"account_id": account_id, "client_node_id": client_node_id})
    result_path = f"/api/hub/v1/requests/{request_id}/result?{query}"
    try:
        return _get_hub_json(hub_url, result_path, timeout_s=timeout_s)
    except CaptainCliError as result_exc:
        if not _is_hub_request_result_not_found_error(result_exc):
            raise
        pickup_path = f"/api/hub/v1/requests/{request_id}/pickup?{query}"
        try:
            payload = _get_hub_json(hub_url, pickup_path, timeout_s=timeout_s)
        except CaptainCliError as pickup_exc:
            if not _is_hub_request_result_not_found_error(pickup_exc):
                raise
            status_payload = _get_hub_json(hub_url, f"/api/hub/v1/requests/{request_id}", timeout_s=timeout_s)
            if isinstance(status_payload, dict):
                status_payload.setdefault("result_pickup_fallback", True)
                status_payload.setdefault(
                    "result_pickup_warning",
                    "Hub did not expose /result or /pickup; using request status payload instead.",
                )
            return status_payload
        if isinstance(payload, dict):
            payload.setdefault("result_pickup_fallback", True)
            payload.setdefault("result_pickup_endpoint", "pickup")
        return payload


def _extract_ai_response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    seen: set[int] = set()
    candidates: list[dict[str, Any]] = []

    def add_candidate(value: Any, *, depth: int = 0) -> None:
        if not isinstance(value, dict):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        candidates.append(value)
        if depth >= 5:
            return
        for key in (
            "request",
            "result",
            "response",
            "output",
            "message",
            "choice",
            "worker_result",
            "result_payload",
            "payload",
            "data",
        ):
            child = value.get(key)
            if isinstance(child, dict):
                add_candidate(child, depth=depth + 1)
            elif isinstance(child, list):
                for item in child:
                    add_candidate(item, depth=depth + 1)

    add_candidate(payload)
    for item in candidates:
        for key in (
            "response_summary",
            "content",
            "text",
            "output_text",
            "answer",
            "completion",
            "message_text",
        ):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        choices = item.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"].strip()
                if isinstance(first.get("text"), str):
                    return first["text"].strip()
        messages = item.get("messages")
        if isinstance(messages, list) and messages:
            for message in reversed(messages):
                if isinstance(message, dict) and str(message.get("role", "")).lower() in {"assistant", "model", "worker"}:
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
    return ""



def _quote_id(quote_response: Any) -> str:
    if not isinstance(quote_response, dict):
        return ""
    quote = quote_response.get("quote") if isinstance(quote_response.get("quote"), dict) else quote_response
    return str(quote.get("quote_id") or "").strip() if isinstance(quote, dict) else ""


def _submitted_request_id(submit_response: Any) -> str:
    if not isinstance(submit_response, dict):
        return ""
    request = submit_response.get("request")
    if isinstance(request, dict):
        return str(request.get("request_id") or request.get("id") or "").strip()
    return str(submit_response.get("request_id") or "").strip()


def _redact_private_key(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, item in enumerate(redacted[:-1]):
        if item == "--private-key":
            redacted[index + 1] = "<redacted>"
    return redacted


def _print_captain_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    wallet = result.get("wallet", {})
    hub = result.get("hub", {})
    chain = result.get("chain", {})
    print("Captain linkup prepared.")
    print(f"Wallet: {wallet.get('title') or wallet.get('selector')} {wallet.get('address')}")
    if wallet.get("account_id"):
        print(f"Credit account: {wallet.get('account_id')}")
    print(f"Prompt: {result.get('prompt', '')}")
    if hub.get("enabled"):
        hub_label = f"{hub.get('network') or 'hub'} {hub.get('url') or ''}".strip()
        print(f"Hub: {hub_label} ring{hub.get('ring', 3)}")
        _print_credit_balance("Credits at start", hub.get("balance_start"))
        quote = hub.get("quote") if isinstance(hub.get("quote"), dict) else {}
        quote_payload = quote.get("quote") if isinstance(quote.get("quote"), dict) else quote
        if isinstance(quote_payload, dict) and quote_payload:
            quoted = quote_payload.get("quoted_credits_display") or quote_payload.get("estimated_credits") or quote_payload.get("quoted_credits")
            max_credits = quote_payload.get("max_credits")
            worker = quote_payload.get("selected_worker_node_id") or quote_payload.get("selected_offer_id") or ""
            print(f"Agent fee quote: {quoted} compute credits (max {max_credits})")
            if worker:
                print(f"Selected offer/worker: {worker}")

        bridge = hub.get("bridge") if isinstance(hub.get("bridge"), dict) else {}
        if bridge:
            print(f"Bridge deposit target: {bridge.get('deposit_credits_display', '0')} credits")
            deposit = bridge.get("deposit") if isinstance(bridge.get("deposit"), dict) else {}
            if deposit:
                print(f"Bridge deposit id: {deposit.get('deposit_id')}")
                print(f"Bridge deposit tx: {deposit.get('transaction_hash')}")
            completion = bridge.get("completion") if isinstance(bridge.get("completion"), dict) else {}
            if completion:
                completion_tx = completion.get("completion_tx_hash") or completion.get("tx_hash") or ""
                print(f"Bridge completion: {'sent' if completion.get('completion_sent') else 'recorded'} {completion_tx}".rstrip())
            _print_credit_balance("Credits after bridge", hub.get("balance_after_bridge"))

        if hub.get("stipend"):
            print(f"Stipend issued: {hub['stipend'].get('delta_credits_display', hub['stipend'].get('credits', 'ok'))}")
        if hub.get("submit"):
            request_id = _submitted_request_id(hub.get("submit"))
            print(f"Hub request: {request_id or 'submitted'}")
        if hub.get("poll"):
            print(f"Hub request state: {_hub_request_state(hub.get('poll')) or 'unknown'}")
        ai_text = _extract_ai_response_text(hub.get("result") if hub.get("result") else hub.get("poll"))
        if ai_text:
            print("AI result:")
            print(ai_text)
        _print_credit_balance("Credits after request", hub.get("balance_after_request"))

        if bridge and bridge.get("refund"):
            refund = bridge.get("refund") if isinstance(bridge.get("refund"), dict) else {}
            if refund.get("skipped"):
                print(f"Bridge refund skipped: {refund.get('reason', 'unknown')}")
            else:
                print(f"Bridge refund requested: {refund.get('refund_credits_display', '0')} credits")
                if refund.get("withdrawal_id"):
                    print(f"Bridge withdrawal id: {refund.get('withdrawal_id')}")
                withdrawal = refund.get("withdrawal") if isinstance(refund.get("withdrawal"), dict) else {}
                if withdrawal:
                    print(f"Bridge withdrawal tx: {withdrawal.get('transaction_hash')}")
            _print_credit_balance("Credits after refund", hub.get("balance_after_refund"))

    if chain.get("enabled"):
        print(f"Smoke ID: {chain.get('smoke_id')}")
        tx = chain.get("transaction") if isinstance(chain.get("transaction"), dict) else {}
        if tx:
            print(f"Tx: {tx.get('transaction_hash')}")
            print(f"Block: {tx.get('block_number')}")
            print(f"Gas used: {tx.get('gas_used')}")
            print(f"Gas price: {tx.get('gas_price_wei')} wei")
            print(f"Gas cost: {tx.get('gas_cost_wei')} wei ({tx.get('gas_cost_native')} native)")
        else:
            print("No legacy XLag smoke block created.")


def _print_credit_balance(label: str, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    account = payload.get("account") if isinstance(payload.get("account"), dict) else payload
    if not isinstance(account, dict):
        return
    display = (
        account.get("available_credits_display")
        or credit_wei_to_decimal_text(account.get("available_credit_wei", account.get("available_credits", 0)))
    )
    bridge_display = (
        account.get("bridge_completed_credits_display")
        or credit_wei_to_decimal_text(account.get("bridge_completed_credit_wei", account.get("bridge_completed_credits", 0)))
    )
    spent_display = (
        account.get("spent_credits_display")
        or credit_wei_to_decimal_text(account.get("spent_credit_wei", account.get("spent_credits", 0)))
    )
    print(f"{label}: available={display}, bridged={bridge_display}, spent={spent_display}")
