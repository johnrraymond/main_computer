"""P5.3i read-only check of the legacy Hub compatibility facade.

    python -m tools.hub_facade_verify

No Hub is started, no Worker is registered, and no payment or FDB
operation occurs. The frozen surface hash records P5.3h compatibility names.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from main_computer import (
    hub, hub_facade_helpers, hub_http_legacy_handler,
    hub_http_runtime, hub_registry_json, hub_worker_client, hub_worker_http,
)

HELPER_SOURCE_SHA256 = {
    '_stable_request_id': 'd42f77e89389a625111a566eaf1bc0dceef07f31efd6d650ee59d3d9ed24e1b1',
    '_stable_session_id': 'ddd5d63611bd1f9666a3e784748ee984d195726254a8d483ea9f6dfe9e0899dd',
    'chat_message_to_dict': '01d33778de3dd9cd7c9e131a55b209ec19ac2d0181710f566d97631b9ffd5b6e',
    'chat_response_from_dict': 'd8833c4069e1b228076eaf8b27f7e6c8c76106c4034df60b1921c63309d4785b',
    'serving_hub_identity_for_server': '43e529f917865a1d17d23cc0de7a6b00e62599b525633ab26525c5992dd7008a',
}
FACADE_EXPORT_NAMES_SHA256 = 'cb318fc1665854c7a8f895e654b402dbe706a5036cb31a1faf475d79b791448b'
HELPER_NAMES = (
    '_stable_request_id', '_stable_session_id',
    'chat_message_to_dict', 'chat_response_from_dict',
    'serving_hub_identity_for_server',
)
COMPATIBILITY_OWNER_NAMES = {
    'HubRegistry': (hub_registry_json, 'HubRegistry'),
    'HubHttpServer': (hub_http_runtime, 'HubHttpServer'),
    'HubDispatcher': (hub_http_runtime, 'HubDispatcher'),
    'HubServerHandler': (hub_http_legacy_handler, 'HubServerHandler'),
    'HubWorkerHttpServer': (hub_worker_http, 'HubWorkerHttpServer'),
    'HubWorkerHandler': (hub_worker_http, 'HubWorkerHandler'),
    'register_worker_with_hub': (hub_worker_client, 'register_worker_with_hub'),
    'serve_hub_worker_pull': (hub_worker_client, 'serve_hub_worker_pull'),
}


def verify_contract() -> dict[str, object]:
    source = Path(hub_facade_helpers.__file__).read_text(encoding='utf-8')
    helpers_tree = ast.parse(source)
    helpers = {
        node.name: ast.get_source_segment(source, node)
        for node in helpers_tree.body if isinstance(node, ast.FunctionDef)
    }
    facade_source = Path(hub.__file__).read_text(encoding='utf-8')
    facade_tree = ast.parse(facade_source)
    facade_functions = {
        node.name for node in facade_tree.body if isinstance(node, ast.FunctionDef)
    }
    exports = sorted(name for name in vars(hub) if not name.startswith('__'))
    checks = {
        'facade_exports_identical_to_p53h': (
            len(exports) == 139 and
            hashlib.sha256('\n'.join(exports).encode('utf-8')).hexdigest()
            == FACADE_EXPORT_NAMES_SHA256
        ),
        'helper_source_identical_to_p53h': all(
            name in helpers and
            hashlib.sha256(helpers[name].encode('utf-8')).hexdigest()
            == HELPER_SOURCE_SHA256[name] for name in HELPER_NAMES
        ),
        'helper_reexport_identity': all(
            getattr(hub, name) is getattr(hub_facade_helpers, name)
            for name in HELPER_NAMES
        ),
        'no_duplicate_helper_functions_in_facade': not any(
            name in facade_functions for name in HELPER_NAMES
        ),
        'only_server_entrypoints_defined_in_facade': facade_functions == {
            'serve_hub', 'serve_hub_worker',
        },
        'server_entrypoints_still_locally_patchable': (
            hub.serve_hub.__globals__ is vars(hub)
            and hub.serve_hub_worker.__globals__ is vars(hub)
        ),
        'legacy_compatibility_owner_identity': all(
            getattr(hub, name) is getattr(module, canonical)
            for name, (module, canonical) in COMPATIBILITY_OWNER_NAMES.items()
        ),
        'helper_no_hub_or_fdb_import': not any(
            isinstance(node, ast.ImportFrom) and
            node.module in {'main_computer.hub', 'main_computer.exp_fdb_hub',
                            'main_computer.stable_hub'}
            for node in ast.walk(helpers_tree)
        ),
    }
    return {
        'ok': all(checks.values()),
        'scope': 'read-only; no Hub request, Worker launch, FDB operation or payment',
        'facade': 'main_computer.hub',
        'helper_module': hub_facade_helpers.__name__,
        'historical_export_count': len(exports),
        'locally_owned_entrypoints': sorted(facade_functions),
        'no_functional_deletion': True,
        **checks,
    }


def main() -> int:
    result = verify_contract()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
