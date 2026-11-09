"""Read-only P5.3d shared HTTP handler ownership and integrity verification.

Usage: python -m tools.hub_http_shared_verify

Does not open a socket, start a Hub, contact FoundationDB or mutate credits.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from main_computer import hub, hub_http_shared
from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler

# Portable fingerprint of the *exact class source* in the pre-extraction Hub.
# Unlike ast.dump this is insensitive to Python 3.12 vs 3.13 AST changes.
EXPECTED_HANDLER_SOURCE_SHA256 = '1f70bc88b7361138707bb897293a5ed41f32ae73dc56159f4aa6d5e92b1da6e5'


def _source_fingerprint(path: Path, name: str) -> str:
    source = path.read_text(encoding='utf-8')
    klass = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == name)
    return hashlib.sha256(ast.get_source_segment(source, klass).encode('utf-8')).hexdigest()


def verify_contract() -> dict[str, object]:
    parsed=ast.parse(Path(hub_http_shared.__file__).read_text(encoding='utf-8'))
    checks = {
        'legacy_json_handler_identity_preserved': hub._JsonHandler is hub_http_shared._JsonHandler,
        'new_handler_export_identity': hub_http_shared.HubJSONRequestHandler is hub._JsonHandler,
        'shared_handler_source_unchanged': (
            _source_fingerprint(Path(hub_http_shared.__file__), '_JsonHandler') == EXPECTED_HANDLER_SOURCE_SHA256
        ),
        'legacy_routes_still_in_legacy_handler': hub.HubServerHandler.__module__ == 'main_computer.hub_http_legacy_handler',
        'legacy_hub_server_reexported_from_shared_runtime': hub.HubHttpServer.__module__ == 'main_computer.hub_http_runtime',
        'legacy_and_worker_handlers_use_shared_base': (
            hub._JsonHandler in hub.HubServerHandler.__mro__
            and hub._JsonHandler in hub.HubWorkerHandler.__mro__
        ),
        'dev_test_inherits_same_shared_base': hub._JsonHandler in ExperimentalFoundationDbHubServerHandler.__mro__,
        'shared_module_does_not_import_legacy_hub': not any(
            isinstance(node,ast.ImportFrom) and node.module in {'main_computer.hub','main_computer.exp_fdb_hub'}
            for node in ast.walk(parsed)
        ),
        'shared_status_constant_identity': (
            hub.DEFAULT_HUB_STATUS_SLOW_LOG_SECONDS == hub_http_shared.DEFAULT_HUB_STATUS_SLOW_LOG_SECONDS
        ),
    }
    return {'ok': all(checks.values()), 'scope': 'read-only, no Hub request or payment',
            'shared_http_module': 'main_computer.hub_http_shared', **checks}


def main() -> int:
    report=verify_contract()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
