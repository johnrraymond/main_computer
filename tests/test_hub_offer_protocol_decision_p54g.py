"""P5.4g decision remains checked after P5.4h migration."""
from __future__ import annotations

import json
from pathlib import Path
from tools.hub_offer_protocol_decision import decide, main


def test_offer_button_now_uses_authenticated_workflow():
    result=decide()
    assert result['ok'] is True, result['missing_evidence']
    assert result['migration_complete'] is True
    assert result['checks']['worker_page_uses_authenticated_offer_sync'] is True
    assert result['checks']['offer_sync_viewport_route_present'] is True
    assert result['checks']['obsolete_worker_page_rest_endpoint_deleted'] is True


def test_hub_identity_and_msk_guard_are_preserved():
    result=decide()
    for name in ('hub_selection_bound_before_registration','authenticated_hub_scoped_wallet_and_msk',
                 'authenticated_registration_and_hub_receipt','dev_test_rest_still_denied',
                 'stable_live_session_preserved'):
        assert result['checks'][name] is True


def test_normal_availability_and_work_now_stay_independent():
    result=decide()
    for name in ('normal_availability_without_work_now_override','work_now_still_separate',
                 'supervisor_applies_deferred_offers'):
        assert result['checks'][name] is True


def test_retirement_limited_to_viewport_rest_endpoint():
    result=decide()
    assert result['deletion_approved']==['worker_page_legacy_rest_offer_endpoint']
    assert result['external_legacy_rest_consumers_retained'] is True
    assert result['legacy_offer_endpoint_retained_for_compatibility'] is False


def test_missing_source_fails_closed(tmp_path:Path):
    result=decide(tmp_path)
    assert result['ok'] is False and result['missing_evidence']
    assert result['migration_complete'] is False


def test_removing_dev_test_rest_denial_fails_closed(tmp_path:Path):
    source=Path(__file__).resolve().parents[1]
    needed=('main_computer/viewport_routes_energy.py',
            'main_computer/web/applications/scripts/worker.js',
            'main_computer/web/applications/apps/worker.html',
            'main_computer/viewport_route_dispatch.py',
            'main_computer/exp_fdb_hub.py',
            'main_computer/stable_hub.py',
            'main_computer/worker_live_session.py',
            'main_computer/hub_worker_client.py')
    for rel in needed:
        target=tmp_path/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((source/rel).read_bytes())
    dev=tmp_path/'main_computer/exp_fdb_hub.py'
    dev.write_text(dev.read_text(encoding='utf-8').replace('"/api/hub/v1/workers/register"','"/api/hub/v1/workers/deleted-register"'),encoding='utf-8')
    report=decide(tmp_path)
    assert report['ok'] is False
    assert 'contract:dev_test_rest_still_denied' in report['missing_evidence']


def test_readonly_verifier_reports_migration(capsys):
    assert main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['ok'] is True
    assert result['scope'].startswith('read-only')
    assert result['migration_complete'] is True
