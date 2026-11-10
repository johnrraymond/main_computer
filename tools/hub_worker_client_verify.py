"""P5.3h source/identity verification; no remote calls, Worker launch or payment."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from main_computer import hub, hub_worker_client
from main_computer.hub_registry_shared import PHASE9_EXECUTION_MODE, PHASE9_PRICING_TYPE
from tools.hub_worker_http_verify import P53F_CLIENT_SOURCE_SHA256

P53G_EXTRA_SOURCE_SHA256 = {
    '_hub_worker_api_headers': '7a70d62f168553c49f64c7eace51bb4050403588d0adafb36eb6bdaeb84522e8',
    '_hub_worker_http_error_message': 'ef250473da372b2c909dded9eb6ea3b087c72d9ef45fa84681c6028bf220038f',
}
EXTRACTED = ('_hub_worker_api_headers', '_hub_worker_http_error_message',
             'register_worker_with_hub', '_post_hub_worker_json',
             '_worker_pull_response_payload', 'serve_hub_worker_pull')


def verify_contract() -> dict[str, object]:
    source = Path(hub_worker_client.__file__).read_text(encoding='utf-8')
    client_tree = ast.parse(source)
    functions = {n.name: ast.get_source_segment(source,n) for n in client_tree.body if isinstance(n,ast.FunctionDef)}
    facade_source = Path(hub.__file__).read_text(encoding='utf-8')
    facade_functions = {n.name for n in ast.parse(facade_source).body if isinstance(n, ast.FunctionDef)}
    hashes = {**P53G_EXTRA_SOURCE_SHA256, **{name: value for name,value in P53F_CLIENT_SOURCE_SHA256.items() if name != 'serve_hub_worker'}}
    checks={
        'function_source_fingerprints_unchanged': all(
            name in functions and hashlib.sha256(functions[name].encode('utf-8')).hexdigest()==hashes[name]
            for name in EXTRACTED if name != 'serve_hub_worker_pull'),
        'worker_pull_opt_in_policy_present': (
            'allow_legacy_worker_rest: bool = False' in functions['serve_hub_worker_pull']
            and 'if not allow_legacy_worker_rest:' in functions['serve_hub_worker_pull']
        ),
        'historical_import_identity_preserved': all(
            getattr(hub,name) is getattr(hub_worker_client,name)
            for name in EXTRACTED),
        'facade_contains_no_duplicate_client_functions': not any(
            name in facade_functions for name in EXTRACTED),
        'server_entry_points_remain_in_facade': all(name in facade_functions for name in ('serve_hub','serve_hub_worker')),
        'no_legacy_or_fdb_import_in_client': not any(
            isinstance(n,ast.ImportFrom) and n.module in {
                'main_computer.hub','main_computer.exp_fdb_hub','main_computer.stable_hub'}
            for n in ast.walk(client_tree)),
        'pricing_and_execution_mode_shared': (
            hub_worker_client.PHASE9_PRICING_TYPE == PHASE9_PRICING_TYPE and
            hub_worker_client.PHASE9_EXECUTION_MODE == PHASE9_EXECUTION_MODE),
        'worker_user_agent_identity_preserved': hub.HUB_WORKER_API_USER_AGENT == hub_worker_client.HUB_WORKER_API_USER_AGENT,
    }
    return {'ok':all(checks.values()), 'scope':'read-only; no Hub request, Worker launch, FDB operation or payment',
            'client_module':hub_worker_client.__name__, **checks}


def main() -> int:
    result=verify_contract()
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
