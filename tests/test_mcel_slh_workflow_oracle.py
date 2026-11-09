"""SLH oracle contracts; unchanged after compiler remediation."""
from __future__ import annotations

from pathlib import Path
import re
import socket

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_slh_first_run import FirstRunError, SlhFirstRunController
from tools import mcel_operational_workflow_oracle as oracle


def test_slh_application_remains_layout_free_and_uses_an_observation_source():
    ir, composition = compile_slh_application()
    assert not ir['layouts']
    assert composition['policy'] == 'mcel.semantic-structure.p3'
    assert any(s['authority'] == 'derived' for s in ir['states'])
    assert any(i['id'] == 'intent:refresh-overview' for i in ir['intents'])
    assert all('application.html' not in str(surface) for surface in ir['surfaces'])


def test_existing_canonical_addresses_remain_native_links():
    contract = oracle.read_contract()
    page = render_slh_page()
    for address in (contract['navigation']['overview'], contract['navigation']['worker_bank']):
        assert re.search(r'<a\s+href="' + re.escape(address) + r'"', page)
    assert 'href="javascript:' not in page


def test_a_missing_local_service_does_not_allow_bootstrap(tmp_path):
    with socket.socket() as free_listener:
        free_listener.bind(('127.0.0.1', 0))
        unused_port = free_listener.getsockname()[1]
    ctl = SlhFirstRunController(state_dir=tmp_path / 'empty', identity_dir=tmp_path / 'identities',
                                local_port=unused_port)
    assert ctl.status()['local'] == 'UNREACHABLE'
    try:
        ctl.bootstrap(admin_id='owner', admin_name='Test', device_id='owner-pc',
                      acknowledge_p256_only=True)
    except FirstRunError as exc:
        assert exc.code == 'SLH_LOCAL_UNAVAILABLE'
    else:
        raise AssertionError('Bootstrap must not succeed without a running SLH authority')


def test_visible_conditions_are_not_authorization():
    """Invariant: no UI visibility clause may rewrite consequential effect risk."""
    ir, _ = compile_slh_application()
    for effect in ir['effects']:
        if effect['id'] in {f'effect:{name}.request' for name in (
                'start-local', 'bootstrap-administrator', 'configure-organization',
                'activate-organization', 'login-administrator', 'logout-administrator', 'rename-worker')}:
            assert effect['risk'] == 'consequential'
