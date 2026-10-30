"""SLH Lab tests the *real* two-service boundary, not a mock Local API."""
from __future__ import annotations

from unittest import mock
from pathlib import Path
import tempfile

import pytest

from tools.slh_seh_lab.run_lab import DEFAULT_TOPOLOGY, plan, verify_local
from tools.slh_seh_lab.service_lifecycle import verify_services


def test_lab_plan_includes_real_process_acceptance():
    result = plan(DEFAULT_TOPOLOGY)
    assert 'two_service_real_process_bootstrap_lifecycle_worker_seh' in result['lanes']
    assert result['no_external_urls'] is True
    assert result['production_mutations'] is False


def test_real_local_service_controls_org_worker_and_seh():
    outcome = verify_services()
    assert outcome['status'] == 'PASS', outcome
    assert len(outcome['gates']) >= 35
    assert all(outcome['gates'].values()), outcome['gates']
    assert outcome['limitations']['paid_marketplace'] == 'SEPARATE_LEGACY_LAB_LANE'


def test_verify_local_requires_both_real_service_and_paid_lanes():
    class DummyHub:
        def __init__(self):
            self.server_port = 18001
            self.payout_ledger_directory = 'in-memory'
        def shutdown(self): pass
        def server_close(self): pass
    class DummyThread:
        def join(self, timeout): pass

    good = {'status': 'PASS', 'pass': True, 'gates': {'real_org_running': True}}
    bad = {'status': 'FAIL', 'pass': False, 'gates': {'real_org_running': False}}
    paid = {'pass': True, 'gates': {'paid': True}}
    with mock.patch('tools.slh_seh_lab.service_lifecycle.verify_services', return_value=good), \
         mock.patch('tools.slh_seh_lab.run_lab._in_memory_hub', return_value=(DummyHub(), DummyThread())), \
         mock.patch('tools.slh_seh_lab.run_lab._local_demo', return_value=paid), \
         mock.patch('tools.slh_seh_lab.run_lab._mock_coolify_gate', return_value={'status': 'PASS'}):
        report = verify_local()
        assert report['status'] == 'PASS'
        assert report['two_service_lab'] == good
        assert report['slh'] == paid
    with mock.patch('tools.slh_seh_lab.service_lifecycle.verify_services', return_value=bad), \
         mock.patch('tools.slh_seh_lab.run_lab._in_memory_hub', return_value=(DummyHub(), DummyThread())), \
         mock.patch('tools.slh_seh_lab.run_lab._local_demo', return_value=paid), \
         mock.patch('tools.slh_seh_lab.run_lab._mock_coolify_gate', return_value={'status': 'PASS'}):
        assert verify_local()['status'] == 'FAIL'


def test_lab_restart_uses_disposable_permissions_not_production_acl():
    """Regression for non-elevated Windows lab restart failing SQLite open.

    This test fails if lab initialization reverts to production icacls hardening.
    It exercises two independent localhost servers against the same lab DB.
    """
    from tools.slh_seh_lab.service_lifecycle import _pending_restart_and_cancel
    with tempfile.TemporaryDirectory(prefix='mc-slh-two-service-lab-') as td:
        with mock.patch('main_computer.slh.local_service.secure_machine_state_dir',
                        side_effect=AssertionError('production ACL invoked for lab state')):
            assert _pending_restart_and_cancel(Path(td))


def test_production_local_service_still_hardens_machine_state(tmp_path):
    """The lab-specific bypass must not change the production default."""
    from main_computer.slh.local_service import create_server
    from main_computer.slh.storage import secure_machine_state_dir
    with mock.patch('main_computer.slh.local_service.secure_machine_state_dir',
                    wraps=secure_machine_state_dir) as protector:
        with create_server(state_dir=tmp_path / 'real-service', port=0):
            pass
        protector.assert_called_once_with(tmp_path / 'real-service')


def test_disposable_protector_refuses_outside_lab_root(tmp_path):
    from tools.slh_seh_lab.service_lifecycle import _protect_disposable_lab_state, ServiceLabFailure
    with pytest.raises(ServiceLabFailure, match='outside the SLH lab'):
        _protect_disposable_lab_state(tmp_path / 'not-a-lab')
