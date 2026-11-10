from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT,
    credit_decimal_text_to_wei,
    credit_wei_product,
    credit_wei_to_decimal_text,
    credit_wei_to_display_text,
    credit_wei_to_whole_credits_floor,
)
from main_computer.energy import EnergyCreditLedger
from main_computer.hub_resource_limits import (
    HubHTTPResourceError, DEFAULT_RESOURCE_POLICY, load_hub_resource_policy,
    validate_http_headers, http_content_length, read_bounded_json, BoundedHubHTTPServer,
)
from main_computer.hub_security import (
    HUB_SECURITY_PROFILE,
    decrypt_hub_envelope,
    derive_hub_session_key,
    encrypt_hub_envelope,
    generate_hub_session_keypair,
    hub_transport_is_encrypted_or_loopback,
)
from main_computer.hub_admin_site import HUB_ADMIN_ROUTES, build_admin_bootstrap_payload, render_hub_admin_html
from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
from main_computer.hub_bridge_backend import HubBridgeBackendError, build_hub_bridge_backend
from main_computer.hub_credit_indexer import HubCreditIndexer, wallet_account_id
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import (
    DEFAULT_WORKER_PAYOUT_PRECISION_PLACES,
    stable_id,
    normalize_worker_payout_precision_places,
)
from main_computer.multisession_key_signing import normalize_address, normalize_chain_id, parse_iso_datetime, recover_personal_sign_address, verify_personal_sign_blob
from main_computer.hub_plex_models import HubAIRequest, HubWorkerSummary
from main_computer.hub_plex_service import AIRequestPlexService
from main_computer.ring_admission import (
    RingAdmissionConfig,
    RingAdmissionDecision,
    load_ring_admission_config,
    normalize_requested_ring,
)
from main_computer.models import ChatAttachment, ChatMessage, ChatResponse

# P5.3b: these records and pure helpers are now owned by a shared registry
# contract. Re-exported here to preserve every existing legacy import path.
from main_computer.hub_registry_shared import (
    HUB_WORKER_STALE_AFTER_SECONDS,
    HUB_WORKER_LEASE_SECONDS,
    HUB_WORKER_INSTANCE_SLOT_LIMIT,
    PHASE9_PRICING_MODE,
    PHASE9_PRICING_TYPE,
    PHASE9_EXECUTION_MODE,
    _hub_as_bool,
    _hub_as_int,
    _hub_as_decimal,
    _hub_decimal_text,
    _hub_credit_wei_from_decimal_text,
    _hub_credit_wei_from_value,
    _hub_credit_wei_from_explicit_or_decimal,
    _hub_credit_public_value_from_wei,
    _hub_credit_display_from_wei,
    _hub_normalized_pricing_payload,
    _hub_pricing_target_output_tokens,
    _hub_pricing_credit_per_token_wei,
    _hub_pricing_credit_wei,
    _hub_ceil_decimal_to_int,
    _phase9_offer_id,
    _phase9_worker_offer_from_payload,
    _clean_node_id,
    _require_allowed_transport,
    HubWorker,
    HubUpstream,
    _hub_worker_is_pull_mode,
)
# Legacy compatibility: exact class and timestamp helper re-exported.
# FDB Dev-Test registry imports its base from hub_registry_json directly.
from main_computer.hub_registry_json import HubRegistry, _utc_now


# P5.3f: constants, errors and legacy route-support helpers have one owner.
from main_computer.hub_http_legacy_support import (
    DEFAULT_HUB_PORT, ALLFATHER_HUB_REMOTE_MANIFEST_PATH,
    HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID, HUB_MULTISESSION_KEY_MAX_AGE_MINUTES,
    HubPaymentRequired, HubCreditAuthorizationError,
    load_allfather_hub_remote_manifest, chat_message_from_dict,
)
# P5.3h: Worker registration/REST-pull client transport is independent of
# Hub server and handler construction. Preserve all historical import names.
from main_computer.hub_worker_client import (
    HUB_WORKER_API_USER_AGENT, _hub_worker_api_headers,
    _hub_worker_http_error_message, register_worker_with_hub,
    _post_hub_worker_json, _worker_pull_response_payload,
    serve_hub_worker_pull,
)

# P5.3g: the legacy Worker-side HTTP listener and route handler now have one
# owner.  Keep their constants and classes available under historical imports.
from main_computer.hub_worker_http import (
    DEFAULT_HUB_WORKER_PORT, HUB_WORKER_CHAT_PATH,
    HUB_WORKER_SESSION_START_PATH, HUB_WORKER_SESSION_CHAT_PATH,
    HubWorkerHttpServer, HubWorkerHandler,
)


# P5.3i: the legacy message/identity helpers live outside the Hub facade.
# Every old import name remains the exact same callable object.
from main_computer.hub_facade_helpers import (
    _stable_request_id, _stable_session_id, chat_message_to_dict,
    chat_response_from_dict, serving_hub_identity_for_server,
)

# P5.3e: Hub runtime server/dispatch now has a non-legacy module owner.
# The same class objects remain available through the historical import path.
from main_computer.hub_http_runtime import HubDispatcher, HubHttpServer


# P5.3d: HTTP framing and bounded JSON semantics have a single owner.
# Keep the historical import path for legacy applications and test doubles.
from main_computer.hub_http_shared import (
    _JsonHandler, HubJSONRequestHandler, DEFAULT_HUB_STATUS_SLOW_LOG_SECONDS,
)


# P5.3f: historical import remains the *same class object*; the route
# implementation is now owned by hub_http_legacy_handler.
from main_computer.hub_http_legacy_handler import HubServerHandler


def serve_hub(config: MainComputerConfig, host: str = "127.0.0.1", port: int = DEFAULT_HUB_PORT, *, verbose: bool = True) -> None:
    server = HubHttpServer((host, port), config, verbose=verbose)
    scheme_note = "https required for remote peers; local http is allowed for loopback development"
    print(f"Main Computer hub server: http://{host}:{server.server_port}")
    print(
        f"Hub network: {config.hub_network} ({config.hub_network_kind}) "
        f"chain_id={config.chain_id} rpc={config.chain_rpc_url} runtime={server.hub_root}"
    )
    print(f"Hub security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}; {scheme_note}")
    print(f"Hub admin/control site: http://{host}:{server.server_port}/admin")
    print(
        "Hub endpoints: GET /admin, GET /api/hub/v1/admin/bootstrap, GET /api/hub/status, "
        "GET /api/hub/payouts?node_id=..., POST /api/hub/v1/workers/register, "
        "POST /api/hub/v1/workers/heartbeat, POST /api/hub/v1/workers/poll, "
        "POST /api/hub/v1/workers/results, "
        
        "POST /api/hub/sessions/start, POST /api/hub/sessions/chat, "
        "POST /api/hub/remote-overflow/safe-chat, POST /api/hub/v1/credits/multisession-keys/validate"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub server stopped.")
    finally:
        server.server_close()


def serve_hub_worker(
    config: MainComputerConfig,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_HUB_WORKER_PORT,
    hub_url: str | None = None,
    public_endpoint: str | None = None,
    verbose: bool = True,
) -> None:
    server = HubWorkerHttpServer((host, port), config, chat_fn, verbose=verbose)
    endpoint = (public_endpoint or f"http://{host}:{server.server_port}").rstrip("/")
    if hub_url:
        report = register_worker_with_hub(
            hub_url=hub_url,
            node_id=config.hub_worker_node_id,
            endpoint=endpoint,
            model=config.model,
            credits_per_request=config.hub_credits_per_request,
            timeout_s=10.0,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        if verbose:
            print(f"Registered hub worker {config.hub_worker_node_id}: {report.get('worker', {})}")
    print(f"Main Computer hub worker: {endpoint}")
    print(f"Worker security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}")
    print(f"Worker endpoint: POST {HUB_WORKER_SESSION_START_PATH}, POST {HUB_WORKER_SESSION_CHAT_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub worker stopped.")
    finally:
        server.server_close()
