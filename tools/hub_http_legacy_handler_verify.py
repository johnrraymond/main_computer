"""Read-only P5.3f legacy HTTP route-handler extraction acceptance.

    python -m tools.hub_http_legacy_handler_verify

No network, FDB transaction, Hub startup, Worker action or paid request.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from main_computer import hub, exp_fdb_hub, hub_http_legacy_handler, hub_http_legacy_support
from main_computer.hub_http_runtime import HubHttpServer
from main_computer.hub_http_shared import HubJSONRequestHandler

# P5.3e HubServerHandler source itself: every route and wallet authorization
# condition is part of this fixed fingerprint, not just a count of methods.
ORIGINAL_P53E_HANDLER_SHA256 = "f157a0dd168412a87a10a5e7228b0dbdea4eb7d3e2304a2abd2766d0ae5d6920"
ORIGINAL_P53E_SUPPORT_SHA256 = {
    "HubPaymentRequired": "8d10685d4e470d53133ac4d6d56904e86258e80e7b8de5277655b816cc307f35",
    "HubCreditAuthorizationError": "5dd8951536022c79e97494d692ba074ae50e2c0f11db0627543742230932e5e3",
    "chat_message_from_dict": "b3a2873af93bab9b81905f3d24d973b4628d751ef83f8690b8d9469b1296793b",
    "load_allfather_hub_remote_manifest": "617f8754c81ef2a0d40015ef5f7d3ca8feb7a268b1133142d7f2f797503aea08",
}


def _class_source_and_ast(module_path: Path, name: str) -> tuple[str, ast.ClassDef]:
    source = module_path.read_text(encoding="utf-8")
    klass = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == name)
    segment = ast.get_source_segment(source, klass)
    if segment is None:
        raise ValueError(f"Missing class source segment: {name}")
    return segment, klass


def verify_contract() -> dict[str, object]:
    source, cls = _class_source_and_ast(Path(hub_http_legacy_handler.__file__), "HubServerHandler")
    legacy_ast = ast.parse(Path(hub.__file__).read_text(encoding="utf-8"))
    route_ast = ast.parse(Path(hub_http_legacy_handler.__file__).read_text(encoding="utf-8"))
    dev_ast = ast.parse(Path(exp_fdb_hub.__file__).read_text(encoding="utf-8"))
    support_source = Path(hub_http_legacy_support.__file__).read_text(encoding="utf-8")
    support_methods = {
        n.name: ast.get_source_segment(support_source, n)
        for n in ast.parse(support_source).body
        if isinstance(n, (ast.ClassDef, ast.FunctionDef))
    }
    support_unchanged = all(
        name in support_methods
        and hashlib.sha256(support_methods[name].encode("utf-8")).hexdigest() == expected
        for name, expected in ORIGINAL_P53E_SUPPORT_SHA256.items()
    )
    gateway_msk = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_handle_multisession_key_validate")
    checks = {
        "legacy_import_reexport_identity": hub.HubServerHandler is hub_http_legacy_handler.HubServerHandler,
        "dev_test_direct_base_identity": exp_fdb_hub.ExperimentalFoundationDbHubServerHandler.__bases__ == (hub_http_legacy_handler.HubServerHandler,),
        "shared_http_base_unchanged": hub_http_legacy_handler.HubServerHandler.__bases__ == (HubJSONRequestHandler,),
        "http_server_runtime_owner_unchanged": hub.HubHttpServer is HubHttpServer,
        "route_handler_source_identical_to_p53e": hashlib.sha256(source.encode("utf-8")).hexdigest() == ORIGINAL_P53E_HANDLER_SHA256,
        "wallet_msk_validation_preserved": gateway_msk is not None,
        "legacy_facade_no_duplicate_handler": not any(isinstance(n,ast.ClassDef) and n.name == "HubServerHandler" for n in legacy_ast.body),
        "route_module_no_legacy_or_fdb_imports": not any(
            isinstance(n,ast.ImportFrom) and n.module in {"main_computer.hub", "main_computer.exp_fdb_hub"}
            for n in route_ast.body
        ),
        "dev_test_module_no_facade_import": not any(
            isinstance(n,ast.ImportFrom) and n.module == "main_computer.hub"
            for n in dev_ast.body
        ),
        "support_error_identity": hub.HubCreditAuthorizationError is hub_http_legacy_support.HubCreditAuthorizationError,
        "support_payment_error_identity": hub.HubPaymentRequired is hub_http_legacy_support.HubPaymentRequired,
        "support_message_parser_identity": hub.chat_message_from_dict is hub_http_legacy_support.chat_message_from_dict,
        "support_manifest_loader_identity": hub.load_allfather_hub_remote_manifest is hub_http_legacy_support.load_allfather_hub_remote_manifest,
        "support_chain_constant_identity": hub.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID == hub_http_legacy_support.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
        "all_extracted_support_sources_unchanged": support_unchanged,
        "worker_http_now_in_separate_module": hub.HubWorkerHandler.__module__ == "main_computer.hub_worker_http",
    }
    return {
        "ok": all(checks.values()),
        "scope": "read-only, no Hub request, FDB transaction or payment",
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "handler_module": hub_http_legacy_handler.__name__,
        "remaining_legacy_worker_http_owner": hub.HubWorkerHandler.__module__,
        **checks,
    }


def main() -> int:
    report = verify_contract()
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
