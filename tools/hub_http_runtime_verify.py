"""Read-only P5.3e HTTP server/dispatcher ownership and integrity gate.

Usage: python -m tools.hub_http_runtime_verify

Does not open a socket, contact a Hub, access FDB, or mutate payments.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

from main_computer import hub, hub_http_runtime
from main_computer.exp_fdb_hub import (
    ExperimentalFoundationDbHubHttpServer,
    ExperimentalFoundationDbHubServerHandler,
)

# Source fingerprints from the *original P5.3d hub.py*, not AST serialization.
# They are independent of Python minor-version AST representation differences.
EXPECTED_DISPATCHER_SHA256 = "7cc9486937eed879162ead0dffb0fc4aaff6320514dbed8623b35883b91fed08"
EXPECTED_RING_AUDIT_WRITER_SHA256 = "da06387227ab18e5ff7fb7deef1f488973951f55e386e4397282e237eb655c97"
EXPECTED_RING_AUDIT_REPORT_SHA256 = "1dfc6ee6ad5c8e70ff6ff03bae5b4b65a16f6b8746ccd2aece937984fbf58623"


def _source_fingerprint(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        raise ValueError("Source segment unavailable for HTTP runtime fingerprint")
    return hashlib.sha256(segment.encode("utf-8")).hexdigest()


def verify_contract() -> dict[str, object]:
    runtime_source = Path(hub_http_runtime.__file__).read_text(encoding="utf-8")
    runtime_ast = ast.parse(runtime_source)
    legacy_source = Path(hub.__file__).read_text(encoding="utf-8")
    legacy_ast = ast.parse(legacy_source)
    definitions = {n.name: n for n in runtime_ast.body if isinstance(n, ast.ClassDef)}
    def method(cls: str, name: str) -> ast.FunctionDef:
        return next(n for n in definitions[cls].body if isinstance(n, ast.FunctionDef) and n.name == name)

    signature = inspect.signature(hub_http_runtime.HubHttpServer.__init__)
    checks = {
        "dispatcher_reexport_identity": hub.HubDispatcher is hub_http_runtime.HubDispatcher,
        "http_server_reexport_identity": hub.HubHttpServer is hub_http_runtime.HubHttpServer,
        "dev_test_inherits_extracted_server": issubclass(ExperimentalFoundationDbHubHttpServer, hub_http_runtime.HubHttpServer),
        "legacy_routes_still_in_legacy_handler": hub.HubServerHandler.__module__ == "main_computer.hub_http_legacy_handler",
        "dev_test_routes_still_in_dev_test_handler": ExperimentalFoundationDbHubServerHandler.__module__ == "main_computer.exp_fdb_hub",
        "dispatcher_source_unchanged": _source_fingerprint(runtime_source, definitions["HubDispatcher"]) == EXPECTED_DISPATCHER_SHA256,
        "server_audit_write_source_unchanged": _source_fingerprint(runtime_source, method("HubHttpServer", "note_ring_admission_audit_write_failure")) == EXPECTED_RING_AUDIT_WRITER_SHA256,
        "server_audit_diagnostics_source_unchanged": _source_fingerprint(runtime_source, method("HubHttpServer", "ring_admission_audit_diagnostics")) == EXPECTED_RING_AUDIT_REPORT_SHA256,
        "handlers_explicitly_injectable": signature.parameters["handler_cls"].default is None,
        "legacy_facade_no_duplicate_server_classes": not any(
            isinstance(n, ast.ClassDef) and n.name in {"HubHttpServer", "HubDispatcher"}
            for n in legacy_ast.body
        ),
        "runtime_no_top_level_legacy_import": not any(
            (isinstance(n, ast.ImportFrom) and n.module == "main_computer.hub")
            or (isinstance(n, ast.Import) and any(a.name == "main_computer.hub" for a in n.names))
            for n in runtime_ast.body
        ),
    }
    return {
        "ok": all(checks.values()),
        "scope": "read-only, no Hub request, FDB transaction, or payment",
        "runtime_module": "main_computer.hub_http_runtime",
        **checks,
    }


def main() -> int:
    report = verify_contract()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
