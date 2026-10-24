from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from main_computer.config import DEFAULT_HUB_NETWORK, MainComputerConfig
from main_computer.contract_config import ContractConfigError, get_contract_address, load_contract_config
from main_computer.credit_units import credit_decimal_text_to_wei, credit_wei_to_decimal_text
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_plex_service import PHASE9_EXECUTION_MODE, PHASE9_PRICING_MODE
from main_computer.hub_credit_bridge_completion import JsonRpcClient
from main_computer.hub_credit_models import normalize_address
from main_computer.hub_networks import HubNetworkConfigError, load_hub_network_registry


_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
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
    parser.add_argument("--private-key", default="", help="Development-only wallet override. Mainnet/testnet officer keys come from verified Mother state.")
    parser.add_argument("--private-state", type=Path, default=None, help="Retired option; legacy YAML wallet-state overrides are no longer supported.")
    parser.add_argument("--ring", default="3", help="Requested hub worker ring. Defaults to 3.")
    parser.add_argument("--ring0", dest="ring", action="store_const", const="0", help="Shortcut for --ring 0.")
    parser.add_argument("--ring1", dest="ring", action="store_const", const="1", help="Shortcut for --ring 1.")
    parser.add_argument("--ring2", dest="ring", action="store_const", const="2", help="Shortcut for --ring 2.")
    parser.add_argument("--ring3", dest="ring", action="store_const", const="3", help="Shortcut for --ring 3.")
    parser.add_argument("--model", default="", help="Hub model id. Defaults to MainComputerConfig/from env.")
    parser.add_argument("--hub-url", default="", help="Hub base URL. Defaults to the selected network hub; captain defaults to mainnet.")
    parser.add_argument("--client-node-id", default="main-computer-captain-cli", help="Hub client node id.")
    parser.add_argument("--network", choices=("mainnet", "testnet"), default="", help="Deployment network key (mainnet default; select testnet explicitly).")
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

    live_session_hub = False
    live_request_authorization: dict[str, Any] = {}
    stable_session_hub = False
    if not options.no_hub:
        from main_computer.hub_cli_live_session import hub_uses_live_sessions
        live_session_hub = hub_uses_live_sessions(runtime.config.hub_url)
        if live_session_hub and effective_execute:
            # Discover the exact Stable Hub signing domain before any on-chain deposit.
            from main_computer.hub_cli_live_session import (
                identify_hub_protocol, issue_cli_multisession_authorization,
            )
            hub_identity = identify_hub_protocol(runtime.config.hub_url, timeout_s=options.timeout_s)
            stable_session_hub = hub_identity.get("service") == "main_computer.stable_hub"
            if stable_session_hub:
                live_request_authorization = issue_cli_multisession_authorization(
                    runtime.config.hub_url, wallet=runtime.wallet, timeout_s=options.timeout_s,
                    stable_identity=hub_identity,
                )
            else:
                live_request_authorization = issue_cli_multisession_authorization(
                    runtime.config.hub_url, wallet=runtime.wallet, timeout_s=options.timeout_s,
                )
            if stable_session_hub and use_bridge:
                # Refund endpoint is explicitly unavailable on Stable Hub.
                # Do not represent the legacy direct refund as enabled.
                result["hub"]["bridge"]["refund_enabled"] = False

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
        if live_session_hub:
            quote = {"skipped": True, "reason": "live_worker_price_selected_at_dispatch"}
        else:
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
            pending_path = _captain_pending_bridge_path(runtime, smoke_id=smoke_id, cwd=repo_cwd)
            pending_deposit = _read_captain_pending_bridge(
                pending_path, runtime=runtime, amount_wei=bridge_credit_wei
            )
            _require_hub_bridge_completion_ready(
                runtime.config.hub_url, chain_id=runtime.chain_id,
                contract_address=runtime.bridge_escrow_address,
                controller_address=runtime.bridge_controller_address,
                timeout_s=options.timeout_s,
            )
            # Replay a saved transaction only while the Hub and chain still
            # recognize it. The local JSON is a recovery hint, not credit truth.
            completion = None
            if pending_deposit is not None:
                try:
                    completion = _complete_captain_bridge_deposit(
                        runtime, pending_deposit, timeout_s=options.timeout_s, bridge_metadata=bridge,
                    )
                except CaptainCliError as exc:
                    if not _is_unknown_bridge_deposit_error(exc, pending_deposit["deposit_id"]):
                        raise
                    # HTTP 400 from the Hub is not by itself permission to pay
                    # again. Independently establish that the old transaction and
                    # deposit are absent from the selected live chain.
                    _require_absent_pending_bridge_on_chain(
                        runtime, pending_deposit, timeout_s=options.timeout_s,
                    )
                    warning = _log_captain_accounting_drift(
                        pending_path, runtime=runtime, deposit=pending_deposit,
                    )
                    bridge.setdefault("accounting_warnings", []).append(warning)
                    try:
                        pending_path.unlink()
                    except OSError as unlink_error:
                        raise CaptainCliError(
                            f"Stale bridge hint could not be retired: {pending_path}; "
                            "refusing a second deposit."
                        ) from unlink_error
                    pending_deposit = None
            if pending_deposit is None:
                pending_deposit = send_captain_bridge_deposit(
                    runtime,
                    deposit_credit_wei=bridge_credit_wei,
                    smoke_id=smoke_id,
                    timeout_s=options.timeout_s,
                )
                pending_deposit = {
                    **pending_deposit,
                    "wallet_address": runtime.wallet.address,
                    "amount_credit_wei": str(bridge_credit_wei),
                }
                _save_captain_pending_bridge(pending_path, pending_deposit)
                completion = _complete_captain_bridge_deposit(
                    runtime, pending_deposit, timeout_s=options.timeout_s, bridge_metadata=bridge,
                )
            bridge["deposit"] = pending_deposit
            bridge["completion"] = completion
            # The Hub has confirmed the deposit's idempotent FDB credit.
            # A retry after a lost HTTP response replays the same ID and is
            # then safe to clear, rather than broadcasting another payment.
            result["hub"]["balance_after_bridge"] = _fetch_hub_credit_balance(
                runtime.config.hub_url,
                wallet_address=runtime.wallet.address,
                timeout_s=options.timeout_s,
            )
            pending_path.unlink(missing_ok=True)
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
            bridge_deposit_id=(
                str(result.get("hub", {}).get("bridge", {}).get("deposit", {}).get("deposit_id") or "")
                if use_bridge
                else ""
            ),
        )
        submit_payload["execution_mode"] = PHASE9_EXECUTION_MODE
        if live_session_hub:
            submit_payload["ring"] = f"ring-{requested_ring}"
            submit_payload["capabilities"] = ["chat.completions"]
            submit_payload["multisession_authorization"] = live_request_authorization
            if stable_session_hub:
                submit_payload = build_stable_hub_request_payload(
                    submit_payload, request_id=idempotency_key, max_credits=max_credits,
                )
        result["hub"]["submit"] = _post_hub_json(
            runtime.config.hub_url,
            "/api/hub/v1/work/requests" if live_session_hub else "/api/hub/v1/requests",
            submit_payload,
            timeout_s=options.timeout_s,
        )
        request_id = _submitted_request_id(result["hub"]["submit"])
        if request_id and options.poll_seconds and options.poll_seconds > 0:
            if stable_session_hub:
                result["hub"]["poll"] = _poll_stable_work_session(
                    result["hub"]["submit"], timeout_s=options.timeout_s,
                    poll_seconds=options.poll_seconds,
                )
                if _hub_request_state(result["hub"]["poll"]) == "completed":
                    result["hub"]["result"] = dict(result["hub"]["poll"].get("accepted_session") or {})
            else:
                result["hub"]["poll"] = _poll_hub_request(
                    runtime.config.hub_url, request_id,
                    timeout_s=options.timeout_s, poll_seconds=options.poll_seconds,
                )
                if _hub_request_state(result["hub"]["poll"]) == "completed":
                    result["hub"]["result"] = _pickup_hub_request_result(
                        runtime.config.hub_url, request_id, account_id=account_id,
                        client_node_id=options.client_node_id, timeout_s=options.timeout_s,
                    )
        if use_bridge:
            result["hub"]["balance_after_request"] = _fetch_hub_credit_balance(
                runtime.config.hub_url,
                wallet_address=runtime.wallet.address,
                timeout_s=options.timeout_s,
            )
            if options.bridge_refund:
                bridge = result["hub"]["bridge"]
                if stable_session_hub:
                    # Stable Hub deliberately has no verified refund/reconciliation
                    # contract. Never bypass it by signing a local chain refund.
                    bridge["refund"] = {
                        "ok": False, "skipped": True,
                        "reason": "stable_hub_refund_reconciliation_not_implemented",
                    }
                elif not request_id:
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
                        deposit_id=str(bridge.get("deposit", {}).get("deposit_id") or ""),
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

    if profile is None and network == "testnet":
        raise CaptainCliError("Testnet network profile is unavailable; refusing fallback to mainnet configuration.")

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
        network=network,
    )
    rpc_url = str(options.rpc_url or _chain_value(deployment, "rpc_url") or _chain_value(deployment, "host_rpc_url") or config.chain_rpc_url or "").strip()
    chain_id = int(options.chain_id or _chain_value(deployment, "chain_id") or config.chain_id or 0)
    xlag_address = str(options.xlag_address or _contract_address(deployment, "xlag-bridge-reserve", "XLagBridgeReserve") or config.xlag_contract_address or "").strip()
    bridge_escrow_address = str(_contract_address(deployment, "hub_credit_bridge_escrow", "HubCreditBridgeEscrow") or "").strip()
    bridge_controller_address = _bridge_controller_address(deployment)
    if network in {"mainnet", "testnet"}:
        # Mother publishes each network's current contract registry. Historical
        # deployment manifests are not payment authority: their escrow can be
        # retired, and their hub_admin identifies a different (older) Hub.
        try:
            loaded = load_contract_config(network, repo_root=cwd, required=True)
        except ContractConfigError as exc:
            raise CaptainCliError(f"Cannot verify current {network} contracts: {exc}") from exc
        assert loaded is not None
        contract_path, contract_registry = loaded
        bridge_escrow_address = get_contract_address(contract_registry, "hub_credit_bridge_escrow")
        xlag_address = str(options.xlag_address or get_contract_address(contract_registry, "xlag-bridge-reserve") or "").strip()
        if not _ADDRESS_RE.fullmatch(bridge_escrow_address):
            raise CaptainCliError(f"Current {network} contract registry lacks a valid bridge escrow: {contract_path}")
        # The current Hub owns its bridge signer. Validate the signer's address,
        # network and escrow with its read-only readiness endpoint before
        # depositing; never compare against a historic manifest hub_admin.
        bridge_controller_address = ""
    if not rpc_url:
        raise CaptainCliError(f"Deployment is missing a chain RPC URL: {deployment_path}")
    if not _ADDRESS_RE.fullmatch(xlag_address):
        raise CaptainCliError(f"Deployment is missing a valid XLagBridgeReserve address: {deployment_path}")

    hub_url = str(options.hub_url or config.hub_url).strip().rstrip("/")
    runtime_config = replace(
        config,
        hub_url=hub_url,
        hub_network=network,
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

def _mother_officer_wallet(selector: str, *, network: str, deployment_path: Path) -> CaptainWallet:
    """Resolve signing authority from Mother's verified private-state closure only.

    The deployment manifest is a public projection and is never consulted here.
    No environment override, wallet-file fallback or development key is permitted.
    """
    from tools.mother.common.ethereum_identity import is_address, is_private_key, private_key_to_address
    from tools.mother.common.models import OperationIdentity
    from tools.mother.common.paths import MotherPaths
    from tools.mother.common.private_state import read_private_state

    repo_root = _repo_root_for_deployment_path(deployment_path)
    paths = MotherPaths(runtime_state_root=repo_root / "runtime" / "state").resolve_private_state_paths()
    operation = OperationIdentity(
        operation_id=f"captain-wallet-{network}-inspect",
        request_id="captain-wallet",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )
    try:
        verified = read_private_state(paths, operation=operation)
        document = json.loads(verified.canonical_object_bytes.decode("utf-8"))
    except Exception as exc:
        raise CaptainCliError(
            f"Cannot verify Mother private state for {network} at {paths.identity_file} "
            f"({type(exc).__name__}); refusing wallet authorization."
        ) from exc
    networks = document.get("networks") if isinstance(document, dict) else None
    network_doc = networks.get(network) if isinstance(networks, dict) else None
    wallets = network_doc.get("wallets") if isinstance(network_doc, dict) else None
    if not isinstance(wallets, dict):
        raise CaptainCliError(f"Mother has no authoritative {network} officer wallets.")

    roles = ("captain", "o1", "o2", "o3")
    raw = str(selector or "captain").strip()
    office_index = _office_index_for_selector(_selector_key(raw))
    if office_index is None and _ADDRESS_RE.fullmatch(raw):
        for index, role in enumerate(roles):
            entry = wallets.get(role)
            if isinstance(entry, dict) and str(entry.get("address") or "").lower() == raw.lower():
                office_index = index
                break
    if office_index is None or office_index >= len(roles):
        raise CaptainCliError(f"No Mother {network} officer matches selector {selector!r}.")

    role = roles[office_index]
    entry = wallets.get(role)
    if not isinstance(entry, dict):
        raise CaptainCliError(f"Mother {network} officer {role} is missing.")
    address = entry.get("address")
    if not is_address(address) or int(address[2:], 16) == 0:
        raise CaptainCliError(f"Mother {network} officer {role} has an invalid address.")
    private_key = entry.get("private_key")
    if not private_key:
        raise CaptainCliError(
            f"Mother {network} officer {role} has no private key for Hub multi-session authorization."
        )
    if not is_private_key(private_key):
        raise CaptainCliError(f"Mother {network} officer {role} has an invalid private key.")
    if private_key_to_address(private_key).lower() != address.lower():
        raise CaptainCliError(f"Mother {network} officer {role} signing key does not match its address.")

    office = _DEV_OFFICE_DEFAULTS[office_index]
    return CaptainWallet(
        selector=raw,
        office=str(office["office"]),
        title=str(office["title"]),
        address=address,
        private_key=private_key,
    )


def resolve_captain_wallet(
    selector: str,
    *,
    deployment: dict[str, Any],
    deployment_path: Path,
    private_key_override: str = "",
    private_state_path: Path | None = None,
    network: str = "",
) -> CaptainWallet:
    # A public manifest may contain stale or incorrect network metadata: never
    # let its contents redirect a mainnet/testnet wallet to development keys.
    path_network = Path(deployment_path).parent.name.lower()
    requested_network = str(network or "").strip().lower()
    if path_network in {"mainnet", "testnet"} and requested_network and requested_network != path_network:
        raise CaptainCliError(
            f"Requested network {requested_network!r} conflicts with deployment path network {path_network!r}."
        )
    selected_network = requested_network or (
        path_network if path_network in {"mainnet", "testnet"}
        else _deployment_network_name(deployment_path, deployment)
    )
    if selected_network in {"mainnet", "testnet"}:
        if private_key_override or private_state_path is not None:
            raise CaptainCliError(
                f"{selected_network} officer signing identities must come from verified Mother state; "
                "--private-key and --private-state overrides are not supported."
            )
        return _mother_officer_wallet(selector, network=selected_network, deployment_path=deployment_path)

    if private_state_path is not None:
        raise CaptainCliError(
            "--private-state is retired; development wallets must use an explicit "
            "--private-key, environment variable, wallet file, or local dev-chain default."
        )

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
    bridge_deposit_id: str = "",
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
    if bridge_deposit_id:
        metadata["bridge_deposit_id"] = str(bridge_deposit_id).strip()
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
    return send_captain_contract_transaction(
        runtime,
        contract_address=runtime.xlag_address,
        private_key=runtime.wallet.private_key,
        function_signature=function_signature,
        function_args=[smoke_id, memo],
        timeout_s=timeout_s,
    )



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

def _complete_captain_bridge_deposit(
    runtime: CaptainRuntime, deposit: dict[str, Any], *, timeout_s: float,
    bridge_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "deposit_id": deposit["deposit_id"],
        "wallet_address": runtime.wallet.address,
        "tx_hash": deposit["transaction_hash"],
        "contract_address": runtime.bridge_escrow_address,
        "chain_id": runtime.chain_id,
    }
    try:
        return _post_hub_json(
            runtime.config.hub_url, "/api/hub/v1/credits/wallet-funding/complete",
            payload, timeout_s=timeout_s,
        )
    except CaptainCliError as exc:
        if not _is_missing_bridge_completion_metadata_error(exc):
            raise
        if bridge_metadata is not None:
            bridge_metadata["completion_fallback"] = {
                "ok": True,
                "reason": "hub completion endpoint is missing bridge deployment metadata",
                "failed_endpoint": "/api/hub/v1/credits/wallet-funding/complete",
                "fallback_endpoint": "/api/hub/v1/credits/wallet-funding/import",
                "error": str(exc),
            }
        # Preserve the existing legacy Hub fallback without relaxing the
        # current-chain funding proof required by Stable Hub.
        return _post_hub_json(
            runtime.config.hub_url, "/api/hub/v1/credits/wallet-funding/import",
            build_bridge_wallet_funding_import_payload(runtime, deposit),
            timeout_s=timeout_s,
        )


def _is_unknown_bridge_deposit_error(exc: BaseException, deposit_id: str) -> bool:
    """Only the exact Hub HTTP 400 for this ID can initiate drift recovery."""
    message = str(exc)
    marker = " with HTTP 400: "
    if marker not in message or "/wallet-funding/complete" not in message.split(marker, 1)[0]:
        return False
    try:
        response = json.loads(message.split(marker, 1)[1])
    except json.JSONDecodeError:
        return False
    return (isinstance(response, dict) and response.get("ok") is False
            and response.get("error") == f"Unknown bridge deposit: {deposit_id}")


def _require_absent_pending_bridge_on_chain(
    runtime: CaptainRuntime, deposit: dict[str, Any], *, timeout_s: float,
) -> None:
    """Fail closed unless the live chain disproves the cached funding claim.

    An absent escrow mapping alone is not enough: an old transaction could
    still be pending or its result unknown. Check code, receipt and mempool.
    """
    from main_computer.hub_credit_bridge_completion import _DEPOSIT_RECORD_SELECTOR, _split_words

    rpc = JsonRpcClient(runtime.rpc_url, timeout_s=timeout_s)
    tx_hash = deposit["transaction_hash"]
    try:
        if rpc.chain_id() != runtime.chain_id:
            raise ValueError("current RPC chain id differs from selected network")
        code = rpc.rpc("eth_getCode", [runtime.bridge_escrow_address, "latest"])
        if not isinstance(code, str) or len(code) <= 2 or not code.startswith("0x"):
            raise ValueError("current escrow contract is missing")
        record_data = "0x" + _DEPOSIT_RECORD_SELECTOR + deposit["deposit_id"][2:]
        words = _split_words(rpc.eth_call(to=runtime.bridge_escrow_address, data=record_data), min_words=5)
        if any(int(word, 16) for word in words[:5]):
            raise ValueError("escrow still has nonempty deposit state")
        if rpc.transaction_receipt(tx_hash) is not None:
            raise ValueError("funding transaction has a receipt on the current chain")
        if rpc.rpc("eth_getTransactionByHash", [tx_hash]) is not None:
            raise ValueError("funding transaction is known to the current chain or pending pool")
    except Exception as exc:
        raise CaptainCliError(
            f"Hub rejected cached deposit {deposit['deposit_id']}, but current-chain absence "
            f"could not be established ({exc}); refusing another payment."
        ) from exc


def _log_captain_accounting_drift(
    pending_path: Path, *, runtime: CaptainRuntime, deposit: dict[str, Any],
) -> dict[str, str]:
    """Warn durably when a local history hint contradicts Hub + chain truth."""
    event = {
        "event": "LOCAL_ACCOUNTING_DRIFT",
        "network": runtime.network,
        "chain_id": str(runtime.chain_id),
        "escrow": runtime.bridge_escrow_address,
        "deposit_id": deposit["deposit_id"],
        "transaction_hash": deposit["transaction_hash"],
        "local": "pending",
        "hub": "unknown",
        "chain": "deposit_and_transaction_absent",
        "action": "retire_stale_local_record_and_fund_fresh",
    }
    line = json.dumps(event, sort_keys=True)
    logging.getLogger(__name__).warning("%s", line)
    try:
        log_path = pending_path.parent / "accounting-drift.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        # Diagnostic logging cannot restore financial authority to a local
        # record. Keep the warning visible even if the log volume is read-only.
        logging.getLogger(__name__).warning("Accounting drift log append failed: %s", exc)
    return event


def _captain_pending_bridge_path(runtime: CaptainRuntime, *, smoke_id: str, cwd: Path) -> Path:
    identity = (
        f"{runtime.chain_id}|{normalize_address(runtime.bridge_escrow_address)}|"
        f"{normalize_address(runtime.wallet.address)}|{smoke_id}"
    )
    return cwd / "runtime" / "state" / "captain" / "pending-bridge-deposits" / (
        hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    )


def _read_captain_pending_bridge(
    path: Path, *, runtime: CaptainRuntime, amount_wei: int
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptainCliError(f"Pending bridge funding record is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise CaptainCliError(f"Pending bridge funding record is invalid: {path}")
    deposit_id = str(raw.get("deposit_id") or "")
    tx_hash = str(raw.get("transaction_hash") or "")
    if (not _BYTES32_RE.fullmatch(deposit_id) or not _TX_HASH_RE.fullmatch(tx_hash)
            or normalize_address(raw.get("wallet_address")) != normalize_address(runtime.wallet.address)
            or str(raw.get("amount_credit_wei")) != str(amount_wei)):
        raise CaptainCliError(f"Pending bridge funding record does not match this request: {path}")
    return raw


def _save_captain_pending_bridge(path: Path, deposit: dict[str, Any]) -> None:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            # The receipt may contain extra RPC fields; these are public data,
            # never the signing key. Keep a minimal durable recovery record.
            saved = {
                "deposit_id": deposit["deposit_id"],
                "transaction_hash": deposit["transaction_hash"],
                "wallet_address": deposit["wallet_address"],
                "amount_credit_wei": deposit["amount_credit_wei"],
                "receipt": deposit.get("receipt", {}),
            }
            json.dump(saved, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows can temporarily deny replacement of a recently observed file;
        # preserve the recovery record and retry transient sharing denials.
        attempts = 0
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or attempts >= 20:
                    raise
                attempts += 1
                time.sleep(min(0.25, 0.01 * attempts))
    except Exception as exc:
        raise CaptainCliError(
            f"Bridge deposit was sent, but its recovery record could not be saved. "
            f"DO NOT send another deposit; inspect {temporary or path}: {exc}"
        ) from exc
    # On an unrecoverable publication failure, preserve the temporary JSON:
    # it is the only local receipt needed to recover an already-sent deposit.


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
    deposit_id: str,
    controller_private_key: str,
    timeout_s: float,
) -> dict[str, Any]:
    """Ask the Hub to perform controller-only bridge reconciliation.

    The requester wallet signs only its own deposit.  The Hub owns the bridge
    controller key and independently derives the authorized spend/refund from
    the terminal request, its ledger charges, and the bound on-chain deposit.
    Client-supplied amounts are assertions only; they are never signing
    authority. ``controller_private_key`` remains accepted for CLI compatibility
    but is intentionally ignored.
    """

    del controller_private_key
    payload = {
        "request_id": str(request_id or "").strip(),
        "deposit_id": str(deposit_id or "").strip(),
        "wallet_address": runtime.wallet.address,
        "expected_bridge_credit_wei": str(max(0, int(bridge_credit_wei or 0))),
        "expected_charged_credit_wei": str(max(0, int(charged_credit_wei or 0))),
        "metadata": {
            "mode": "captain-hub-signed-bridge-refund-v2",
            "smoke_id": smoke_id,
        },
    }
    result = _post_hub_json(
        runtime.config.hub_url,
        "/api/hub/v1/credits/bridge-reconciliation/execute",
        payload,
        timeout_s=timeout_s,
    )
    result.setdefault("request_id", str(request_id or "").strip())
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
    # Requester signing stays in this process; only the signed transaction goes
    # to the configured RPC. No Foundry or container runtime is involved.
    try:
        from eth_account import Account
        from eth_abi import encode
        from eth_utils import keccak, to_checksum_address
    except ImportError as exc:
        raise CaptainCliError(
            "eth-account is required for captain transaction signing. "
            "Install the project requirements with python -m pip install -r requirements.txt."
        ) from exc

    try:
        signer = Account.from_key(private_key)
        if normalize_address(signer.address) != normalize_address(runtime.wallet.address):
            raise CaptainCliError("Captain signing key does not match the requester wallet.")
        contract = to_checksum_address(contract_address)
        name, types_text = function_signature.rstrip(")").split("(", 1)
        types = types_text.split(",") if types_text else []
        if len(types) != len(function_args):
            raise ValueError("argument count mismatch")
        values = []
        for typ, value in zip(types, function_args):
            if typ.startswith("uint"):
                values.append(int(value))
            elif typ == "bytes32":
                values.append(bytes.fromhex(value.removeprefix("0x")))
            elif typ == "address":
                values.append(to_checksum_address(value))
            elif typ == "string":
                values.append(value)
            else:
                raise ValueError("unsupported captain ABI type")
        data = "0x" + (keccak(text=function_signature)[:4] + encode(types, values)).hex()
        value = int(value_wei)
        if value < 0:
            raise ValueError("negative transaction value")
    except CaptainCliError:
        raise
    except Exception:
        raise CaptainCliError("Captain transaction key or ABI arguments are invalid.") from None

    rpc = JsonRpcClient(runtime.rpc_url, timeout_s=timeout_s)
    stage = "RPC preparation"
    tx_hash = ""
    try:
        chain_id = rpc.chain_id()
        if chain_id != int(runtime.chain_id):
            raise CaptainCliError(
                f"RPC chain id {chain_id} does not match captain deployment chain id {runtime.chain_id}."
            )
        nonce = rpc.get_transaction_count(signer.address)
        gas_price = rpc.gas_price()
        stage = "gas estimation"
        gas_estimate = rpc.estimate_gas({
            "from": signer.address, "to": contract, "value": hex(value), "data": data,
            "gasPrice": hex(gas_price),
        })
        if gas_estimate <= 0 or gas_price < 0:
            raise CaptainCliError("RPC returned invalid gas parameters.")
        # Use the same legacy fee model as the existing Python Hub signer.
        transaction = {
            "chainId": chain_id, "nonce": nonce, "to": contract,
            "value": value, "data": data,
            "gas": (gas_estimate * 120 + 99) // 100 + 10_000,
            "gasPrice": gas_price,
        }
        stage = "signing"
        signed = Account.sign_transaction(transaction, private_key)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            raw = getattr(signed, "rawTransaction", None)
        if raw is None:
            raise CaptainCliError("Python signer returned no raw transaction.")
        tx_hash = "0x" + bytes(signed.hash).hex()
        stage = "submission"
        returned_hash = rpc.send_raw_transaction(bytes(raw))
        if returned_hash.lower() != tx_hash.lower():
            raise CaptainCliError("RPC returned a different transaction hash.")
        stage = "receipt confirmation"
        receipt = _wait_for_receipt(rpc, tx_hash, timeout_s=max(1.0, timeout_s))
    except Exception as exc:
        detail = str(exc).replace(private_key, "<redacted>") if private_key else str(exc)
        if stage == "signing":
            detail = "Python transaction signing failed."
        recovery = (
            f" Transaction hash: {tx_hash}. Check its status before retrying; submission is not retried automatically."
            if tx_hash else " No transaction was submitted."
        )
        raise CaptainCliError(f"Captain transaction failed during {stage}: {detail}.{recovery}") from None
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
        "transport": "python-signed-json-rpc",
        "command": [],
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


def build_stable_hub_request_payload(
    legacy_payload: dict[str, Any], *, request_id: str, max_credits: int,
) -> dict[str, Any]:
    """Translate Captain's authenticated request into Stable Hub's exact schema."""
    messages = legacy_payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise CaptainCliError("Stable Hub Captain work requires a nonempty message list.")
    return {
        "request_id": str(request_id),
        "work": {
            "ring": str(legacy_payload.get("ring") or "ring-3"),
            "capabilities": ["chat.completions"],
            "model": str(legacy_payload.get("model") or "hub-auto"),
            "messages": messages,
            "max_price": {"amount": str(max_credits), "unit": "compute_credit"},
            "metadata": dict(legacy_payload.get("metadata") or {}),
        },
        "multisession_authorization": dict(legacy_payload.get("multisession_authorization") or {}),
    }


def _poll_stable_work_session(
    accepted: dict[str, Any], *, timeout_s: float, poll_seconds: float,
) -> dict[str, Any]:
    """Poll the owner Hub's verified continuation URL, never the legacy REST route."""
    url = str(accepted.get("continuation_url") or "")
    expected_hub = accepted.get("execution_hub")
    owner_url = str(expected_hub.get("hub_url") or "") if isinstance(expected_hub, dict) else ""
    session_id = str(accepted.get("session_id") or "")
    if not url or not owner_url or not session_id:
        raise CaptainCliError("Stable Hub did not return a complete session continuation authority.")
    parsed = urlparse(url)
    owner = urlparse(owner_url)
    if (parsed.scheme not in {"http", "https"} or parsed.scheme != owner.scheme
        or parsed.netloc != owner.netloc or parsed.username or parsed.password
        or parsed.query or parsed.fragment
        or parsed.path != f"/api/hub/v1/work/sessions/{session_id}/stream"):
        raise CaptainCliError("Stable Hub continuation URL does not match its execution Hub and session.")
    deadline = time.monotonic() + max(0.0, float(poll_seconds))
    while True:
        current = _get_hub_json(owner_url, parsed.path, timeout_s=timeout_s)
        if str(current.get("session_id") or "") != session_id:
            raise CaptainCliError("Stable Hub continuation returned a different session ID.")
        if _hub_request_state(current) in {"completed", "failed", "cancelled"}:
            return current
        if time.monotonic() >= deadline:
            return current
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


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


def _require_hub_bridge_completion_ready(
    hub_url: str, *, chain_id: int, contract_address: str,
    controller_address: str, timeout_s: float,
) -> None:
    """Read-only Hub preflight before broadcasting any wallet-funding deposit."""
    status = _get_hub_json(
        hub_url, "/api/hub/v1/credits/wallet-funding/completion", timeout_s=timeout_s,
    )
    if status.get("ok") is not True:
        error = str(status.get("error") or "missing or invalid bridge signer")
        raise CaptainCliError(
            f"Hub bridge completion is unavailable ({error}); refusing to send an on-chain deposit."
        )
    deployment = status.get("deployment")
    if not isinstance(deployment, dict):
        raise CaptainCliError("Hub bridge completion did not identify its deployment; refusing on-chain deposit.")
    if str(deployment.get("chain_id") or "") != str(chain_id):
        raise CaptainCliError("Hub bridge completion chain differs from the selected wallet network; refusing deposit.")
    if str(deployment.get("contract_address") or "").lower() != str(contract_address or "").lower():
        raise CaptainCliError("Hub bridge completion escrow differs from selected wallet contract; refusing deposit.")
    hub_controller = str(deployment.get("bridge_controller_address") or "").strip()
    if not controller_address and not _ADDRESS_RE.fullmatch(hub_controller):
        raise CaptainCliError("Hub bridge completion has no valid controller; refusing deposit.")
    if controller_address and hub_controller.lower() != str(controller_address).lower():
        raise CaptainCliError("Hub bridge completion controller differs from the selected bridge controller; refusing deposit.")


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
    state = str(request.get("state") or request.get("status") or "").strip().lower() if isinstance(request, dict) else ""
    return "completed" if state == "succeeded" else state


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
            "accepted_session",
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
