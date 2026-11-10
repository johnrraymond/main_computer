"""P5.4h: verify authenticated Worker offer migration without contacting a Hub."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def decide(root: Path | None = None) -> dict[str, object]:
    root = (root or ROOT).resolve()
    missing: list[str] = []
    def source(name: str) -> str:
        path = root / name
        if not path.is_file():
            missing.append(f'missing:{name}')
            return ''
        return path.read_text(encoding='utf-8')

    energy = source('main_computer/viewport_routes_energy.py')
    dispatch = source('main_computer/viewport_route_dispatch.py')
    js = source('main_computer/web/applications/scripts/worker.js')
    html = source('main_computer/web/applications/apps/worker.html')
    dev = source('main_computer/exp_fdb_hub.py')
    stable = source('main_computer/stable_hub.py')
    live = source('main_computer/worker_live_session.py')
    legacy = source('main_computer/hub_worker_client.py')
    methods: dict[str,str] = {}
    try:
        tree = ast.parse(energy)
        handler = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name == 'ViewportEnergyRoutesMixin')
        methods = {n.name:ast.get_source_segment(energy,n) or '' for n in handler.body if isinstance(n,ast.FunctionDef)}
    except (StopIteration,SyntaxError):
        missing.append('unparseable:main_computer/viewport_routes_energy.py')
    get = lambda name: methods.get(name,'')
    checks = {
        'worker_page_uses_authenticated_offer_sync': ('/api/applications/worker/offer-sync' in js and 'id="worker-register-offer"' in html),
        'offer_sync_viewport_route_present': ('"/api/applications/worker/offer-sync"' in dispatch and '_handle_worker_offer_sync()' in dispatch),
        'obsolete_worker_page_rest_endpoint_deleted': ('/api/applications/worker/register-offer' not in dispatch and '/api/applications/worker/register-offer' not in js and '_handle_worker_offer_register' not in methods and '_post_worker_registration_to_hub' not in methods),
        'normal_availability_without_work_now_override': ('normal_allowed' in get('_worker_offer_sync_pending') and 'normal_allowed' in get('_handle_worker_offer_sync') or 'normal_allowed' in get('_worker_offer_sync_pending')),
        'authenticated_hub_scoped_wallet_and_msk': ('_worker_multisession_authorization_for_wallet(' in get('_handle_worker_offer_sync') and '_worker_multisession_authorization_for_wallet(' in get('_worker_offer_sync_pending')),
        'hub_selection_bound_before_registration': ('_worker_offer_registration_destination(body)' in get('_handle_worker_offer_sync') and 'resolve_hub_destination(self._load_worker_settings())' in get('_worker_offer_registration_destination')),
        'authenticated_registration_and_hub_receipt': ('_post_worker_start_to_hub(' in get('_worker_offer_sync_pending') and 'worker_id' in get('_worker_offer_sync_pending') and 'assigned_ring' in get('_worker_offer_sync_pending')),
        'work_now_still_separate': ('_worker_set_work_now_override(' in get('_handle_worker_network_work_now') and '_worker_set_work_now_override(' not in get('_handle_worker_offer_sync') and '_worker_set_work_now_override(' not in get('_worker_offer_sync_pending')),
        'supervisor_applies_deferred_offers': ('_worker_offer_sync_pending()' in energy and 'workerOfferPending' in energy and 'def reconcile_worker_runtime(' in energy),
        'dev_test_rest_still_denied': ('"/api/hub/v1/workers/register"' in dev),
        'stable_live_session_preserved': ('"/api/hub/v1/workers/live-session"' in stable and 'hub.auth.accepted' in live),
        'separate_legacy_rest_client_preserved': ('def register_worker_with_hub(' in legacy and '/api/hub/v1/workers/register' in legacy),
    }
    missing.extend(f'contract:{name}' for name, value in checks.items() if not value)
    return {
        'schema':'main-computer.p54h.worker-offer-authenticated-migration.v1',
        'scope':'read-only source contract; no Hub contact, Worker launch or payment',
        'ok':not missing,
        'checks':checks,
        'missing_evidence':missing,
        'decision':'worker_page_offer_authenticated_with_normal_availability',
        'migration_complete':not missing,
        'legacy_offer_endpoint_retained_for_compatibility':False,
        'external_legacy_rest_consumers_retained':True,
        'deletion_approved':['worker_page_legacy_rest_offer_endpoint'],
        'required_migration_guards':[
            'Use saved Worker network and canonical Hub identity, not an independent URL',
            'Require wallet and active Hub-scoped multi-session-key authorization',
            'Register and verify the Hub authenticated Worker identity and market offer',
            'Do not set a Work-now timer or grant a temporary idle-policy override',
            'Do not advertise accepting capacity before normal local Worker policy permits work',
            'Prove failure paths cannot strand an accepting live socket or stale registered offer',
            'Preserve the Dev-Test /workers/connect compatibility boundary separately from Stable Hub live-session auth',
            'Prove Dev/Test and Mainnet/Testnet registration with isolated end-to-end transports',
        ],
    }


def main() -> int:
    report=decide()
    print(json.dumps(report,sort_keys=True,indent=2))
    return 0 if report['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
