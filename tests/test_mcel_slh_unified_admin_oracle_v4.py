"""V4 red-first acceptance oracle for shared local/network SLH administration.

This is intentionally an oracle update only. Old implementation MUST fail the
shared-app and remote-administration assertions. No real service is started or
mutated by these tests.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from main_computer.mcel_slh_application import ROUTES, render_slh_page
from main_computer.mcel_slh_target_navigation import TARGET_ROUTES

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=json.loads((ROOT/'tests/fixtures/mcel_oracles/unified-administration/oracle.v4.json')
                    .read_text(encoding='utf-8'))
GENERATED=re.compile(r'<script\s+type="application/json"\s+id="mcel-generated-data">(.*?)</script>',re.S)


def _page_model(view: str) -> tuple[str, dict]:
    html=render_slh_page(view)
    match=GENERATED.search(html)
    assert match, f'No generated MCEL semantic data for {view}'
    data=json.loads(match.group(1))
    assert isinstance(data.get('ir'),dict)
    return html,data['ir']


def _ids(ir: dict, name: str) -> set[str]:
    return {record['id'] for record in ir[name]}


def test_v4_contract_is_complete_and_supersedes_only_the_wrong_v2_v3_assumptions():
    contract=CONTRACT
    assert contract['schema']=='mcel.slh.unified-administration-oracle.v4'
    assert 'separate semantic apps' in contract['supersedes'][0]
    assert contract['application']['shared_app_id']=='slh'
    assert set(contract['application']['target_kinds'])=={'local','network'}
    assert contract['application']['canonical_target_urls']==TARGET_ROUTES
    assert contract['application']['no_authored_html_layout']
    assert contract['application']['same_surface_composition_after_authentication']
    composition=contract['application']['viewport_composition_contract']
    assert composition['gate']=='mcel.viewport-composition-frob.v2'
    assert composition['local_remote_identical_post_auth_scroll_ownership']
    assert not composition['application_root_scrollable']
    assert composition['navigation_and_workspace_scrolling_independent']
    assert not composition['ordinary_control_and_status_cards_scrollable']
    assert composition['normal_content_must_pass_not_only_synthetic_overflow']
    assert {'intent:refresh-workers','intent:rename-worker','intent:login-administrator'} <= set(
        contract['application']['required_admin_intents'])
    assert all((ROOT/path).is_file() for path in contract['retained_oracles'])
    assert len(set(x['id'] for x in contract['scenarios']))==len(contract['scenarios'])
    assert len(contract['scenarios'])>=15
    assert contract['acceptance']['no_live_mutation']


@pytest.mark.parametrize('case',CONTRACT['scenarios'],ids=lambda x:x['id'])
def test_v4_scenario_decisions_are_noncontradictory(case):
    """Specification consistency; does not pretend to test an unimplemented UI."""
    assert case['target'] in CONTRACT['application']['target_kinds']
    assert isinstance(case['primary'],str) and case['primary']
    forbidden=case.get('forbidden',[])
    assert case['primary'] not in forbidden
    assert len(set(forbidden))==len(forbidden)
    if case['administration']:
        assert case['primary']!='intent:start-local'
    if case.get('surface')=='shared-admin':
        assert case['administration'] is True
    if case['target']=='network':
        assert case['primary'] not in {'intent:start-local','intent:bootstrap-administrator',
                                        'intent:configure-organization','intent:activate-organization'}
    if case.get('worker_state')=='NOT_OBSERVED':
        assert 'report-zero-workers' in forbidden


def test_v4_local_and_remote_mount_one_semantic_application_not_two():
    """Intentionally RED against V2's dedicated slh-network app."""
    html_local,ir_local=_page_model('overview')
    html_remote,ir_remote=_page_model('network')
    expected=CONTRACT['application']['shared_app_id']
    assert ir_local['application']['appId']==expected
    assert ir_remote['application']['appId']==expected, (
        'Network currently mounts an entirely different MCEL application')
    assert 'data-slh-target="local" aria-selected="true"' in html_local
    assert 'data-slh-target="network" aria-selected="true"' in html_remote


def test_v4_authenticated_admin_operations_are_declared_for_both_targets():
    """Common domain operations must not be missing from a remote semantic app."""
    required=set(CONTRACT['application']['required_admin_intents'])
    local=_page_model('overview')[1]
    remote=_page_model('network')[1]
    assert required<=_ids(local,'intents')
    assert required<=_ids(remote,'intents'), 'Network target lacks authenticated admin operations'
    for ir in [local,remote]:
        assert not ir.get('layouts')
        assert not any(x.get('htmlBindings') for x in ir['surfaces'])


def test_v4_worker_bank_and_overview_share_domain_projections():
    """Same source-derived projections, not visually similar duplicated markup."""
    required=set(CONTRACT['application']['shared_domain_state_ids'])
    local=_page_model('overview')[1]
    remote=_page_model('network')[1]
    assert required<=_ids(local,'states')
    assert required<=_ids(remote,'states'), 'Network app has no shared Worker/overview state'
    assert _ids(local,'states') & required == _ids(remote,'states') & required


def test_v4_both_targets_expose_native_canonical_navigation():
    assert ROUTES['/applications/slh']=='overview'
    assert ROUTES['/applications/slh/workers']=='worker-bank'
    for target,url in CONTRACT['application']['canonical_target_urls'].items():
        html,_=_page_model('overview' if target=='local' else 'network')
        assert f'href="{url}"' in html
        assert f'data-slh-target="{target}" aria-selected="true"' in html
        assert 'href="javascript:' not in html
    # The network target must have a stable, directly loadable Worker Bank URL.
    assert '/applications/slh/network/workers' in ROUTES, (
        'Network Worker Bank has no browser-native canonical URL')


def test_v4_authenticated_local_and_network_use_same_semantic_admin_surface():
    """No two independently authored post-login MCEL surfaces."""
    local=_page_model('overview')[1]
    remote=_page_model('network')[1]
    local_surfaces={(s['id'],s['purpose']) for s in local['surfaces']}
    remote_surfaces={(s['id'],s['purpose']) for s in remote['surfaces']}
    assert local_surfaces & remote_surfaces, (
        'No common semantic administration surface; V2 split is still present')
    assert _ids(local,'intents') & {'intent:refresh-workers','intent:rename-worker'} == (
        _ids(remote,'intents') & {'intent:refresh-workers','intent:rename-worker'})


def test_v4_remote_pre_auth_must_not_grant_local_organization_authority():
    """Already enforced by the retained V2 pin/origin tests and V3 boundaries.

    This independent check protects the oracle from accidentally considering a
    successful TLS observation a successful administrator login.
    """
    cases={x['id']:x for x in CONTRACT['scenarios']}
    for scenario in ['network-unverified','network-verified','network-unenrolled',
                     'network-enrollment-pending','network-pin-mismatch','network-expired']:
        assert cases[scenario]['administration'] is False
    assert 'intent:rename-worker' in cases['network-verified']['forbidden']
    assert 'automatic-admin-grant' in cases['network-unenrolled']['forbidden']
    assert 'reuse-local-bearer' in cases['switch-target']['forbidden']
    assert 'any-HTTP-request-to-target' in cases['network-pin-mismatch']['forbidden']
    assert CONTRACT['authority']['role_source'].startswith('selected Organization Service')
