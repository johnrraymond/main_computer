"""Read-only P5.3g Worker HTTP ingress extraction verification.

    python -m tools.hub_worker_http_verify

This command opens no socket, starts no Worker, and performs no Hub/payment action.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from main_computer import hub, hub_worker_http, hub_worker_client
from main_computer.hub_http_shared import HubJSONRequestHandler
from main_computer.hub_resource_limits import BoundedHubHTTPServer

# SHA256 of exact class source under P5.3f. Cross-Python-version portable.
P53F_SOURCE_SHA256 = {
    'HubWorkerHttpServer': '2a02308147255b3beef3cc51d5586d3ab3dbe7b55056df3c4eb1871b708ae08f',
    'HubWorkerHandler': '565dbabb413416f8b0bb03ae0326122c4e0eebbfc8f63d51c95d3abdb7207b42',
}

# Preserve the old worker-registration, REST-pull and authenticated
# live-session selection functions exactly. This patch only moves the HTTP
# listener classes; Worker clients stay in hub.py.
P53F_CLIENT_SOURCE_SHA256 = {
    'register_worker_with_hub': '36b3c1559021255cc52a0287deb5c59f0620744a441b199f24cf2c34a82db1a3',
    '_post_hub_worker_json': 'e699fd48d019aa2b8b8e6db3a1d30650829fdc8db40adc18f8dd348343893adb',
    '_worker_pull_response_payload': 'bc6b13c8105787c8284e3edb003149fd52032335c01055028cd27b99bcf61eb3',
    'serve_hub_worker_pull': '726a5aabdd8edd4008bba25fff30b697c15466702139ae27c50ca538d3cbaf74',
    'serve_hub_worker': '3c4001fe12645c4be0c2bb91e98c6d8f3f9d59c030d9a0c1667778b7ed07dc58',
}


def _class_sources(module_path: Path) -> dict[str, str]:
    source = module_path.read_text(encoding='utf-8')
    return {
        node.name: ast.get_source_segment(source, node)
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef)
    }


def verify_contract() -> dict[str, object]:
    sources = _class_sources(Path(hub_worker_http.__file__))
    facade_source = Path(hub.__file__).read_text(encoding='utf-8')
    facade_classes = _class_sources(Path(hub.__file__))
    parsed = ast.parse(Path(hub_worker_http.__file__).read_text(encoding='utf-8'))
    facade_funcs = {
        n.name: ast.get_source_segment(facade_source, n)
        for n in ast.parse(facade_source).body if isinstance(n, ast.FunctionDef)
    }
    client_source = Path(hub_worker_client.__file__).read_text(encoding='utf-8')
    client_funcs = {
        n.name: ast.get_source_segment(client_source, n)
        for n in ast.parse(client_source).body if isinstance(n, ast.FunctionDef)
    }
    checks = {
        'source_fingerprints_match_p53f': all(
            key in sources and hashlib.sha256(sources[key].encode('utf-8')).hexdigest() == sha
            for key, sha in P53F_SOURCE_SHA256.items()
        ),
        'legacy_handler_export_identity': hub.HubWorkerHandler is hub_worker_http.HubWorkerHandler,
        'legacy_server_export_identity': hub.HubWorkerHttpServer is hub_worker_http.HubWorkerHttpServer,
        'worker_handler_shared_http_base': hub_worker_http.HubWorkerHandler.__bases__ == (HubJSONRequestHandler,),
        'worker_server_resource_limited_base': hub_worker_http.HubWorkerHttpServer.__bases__ == (BoundedHubHTTPServer,),
        'legacy_facade_no_duplicate_worker_http_classes': all(name not in facade_classes for name in P53F_SOURCE_SHA256),
        'new_module_no_facade_or_fdb_import': not any(
            isinstance(node, ast.ImportFrom) and node.module in {
                'main_computer.hub', 'main_computer.exp_fdb_hub', 'main_computer.stable_hub'
            } for node in ast.walk(parsed)
        ),
        'constants_reexported_identically': all(
            getattr(hub, name) == getattr(hub_worker_http, name)
            for name in ('DEFAULT_HUB_WORKER_PORT', 'HUB_WORKER_CHAT_PATH',
                         'HUB_WORKER_SESSION_START_PATH', 'HUB_WORKER_SESSION_CHAT_PATH')
        ),
        'worker_pull_and_registration_now_in_client': all(
            name in client_funcs and name not in facade_funcs
            for name in P53F_CLIENT_SOURCE_SHA256 if name != 'serve_hub_worker'
        ),
        'client_protocol_functions_source_unchanged': all(
            name in client_funcs and hashlib.sha256(client_funcs[name].encode('utf-8')).hexdigest() == sha
            for name, sha in P53F_CLIENT_SOURCE_SHA256.items() if name != 'serve_hub_worker'
        ) and hashlib.sha256(facade_funcs['serve_hub_worker'].encode('utf-8')).hexdigest()
            == P53F_CLIENT_SOURCE_SHA256['serve_hub_worker'],
        'client_legacy_import_identity': all(
            getattr(hub, name) is getattr(hub_worker_client, name)
            for name in P53F_CLIENT_SOURCE_SHA256 if name != 'serve_hub_worker'
        ),
        'client_no_facade_or_fdb_import': not any(
            isinstance(n, ast.ImportFrom) and n.module in {
                'main_computer.hub', 'main_computer.exp_fdb_hub', 'main_computer.stable_hub'
            } for n in ast.walk(ast.parse(client_source))
        ),
    }
    return {'ok': all(checks.values()),
            'scope': 'read-only; no network, Worker, FoundationDB transaction or payment',
            'module': hub_worker_http.__name__, **checks}


def main() -> int:
    report = verify_contract()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
