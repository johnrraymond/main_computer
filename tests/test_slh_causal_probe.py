"""Causal isolation for Worker loss; not a substitute for Windows acceptance."""
from dataclasses import replace
from pathlib import Path

from tools.slh_seh_lab.integrated_stress import StressProfile


def test_probe_opt_in_preserves_legacy_stress_profile():
    default = StressProfile(8, 16, 4, 1729)
    assert default.causal_probe is False
    assert replace(default, causal_probe=True).causal_probe is True


def test_probe_passed_into_actual_real_process_paid_lane(monkeypatch):
    from tools.slh_seh_lab import run_lab
    observed = {}

    class FakeServer:
        server_port = 12345
        payout_ledger_directory = object()
        def shutdown(self): pass
        def server_close(self): pass

    class FakeThread:
        def join(self, timeout): pass

    monkeypatch.setattr(run_lab, '_in_memory_hub', lambda: (FakeServer(), FakeThread()))
    import tools.slh_seh_lab.real_paid_service as paid

    def fake_accept(**kwargs):
        observed.update(kwargs)
        return {'pass':True,'status':'PASS','gates':{},
                'integrated_stress':{'pass':True,'status':'PASS'}}

    monkeypatch.setattr(paid, 'verify_real_process_paid', fake_accept)
    result = run_lab.verify_integrated_network_stress(
        workers=64, requests=64, concurrency=8, seed=1729, causal_probe=True)
    assert result['pass']
    assert observed['stress_profile'].causal_probe
    assert observed['stress_profile'].workers == 64


def test_causal_checkpoint_precedes_paid_job_and_reserve_change():
    source = (Path(__file__).parents[1] / 'tools/slh_seh_lab/integrated_stress.py').read_text()
    milestones = [
        "assert_fleet('before_prepaid_reserve_change')",
        "assert_fleet('after_prepaid_reserve_change')",
        "assert_fleet('after_prepaid_reserve_recovery')",
        "assert_fleet('immediately_before_paid_requests')",
        "record('paid_requests_starting'",
        "assert_fleet('paid_settled_before_reserve_change')",
        "# Enforce a more restrictive private reserve",
        "assert_fleet('paid_settled_after_reserve_change')",
    ]
    locations = [source.index(stage) for stage in milestones]
    assert locations == sorted(locations)
    assert "record('causal_first_worker_loss'" in source
    assert "raise ServiceLabFailure('Integrated causal probe first observed Worker loss at '" in source


def test_adapter_records_first_close_cause_counts_without_secrets():
    import threading
    from main_computer.slh.seh_transport import SEHLocalClientAdapter
    from tools.slh_seh_lab.integrated_stress import _adapter_first_closes
    adapter = object.__new__(SEHLocalClientAdapter)
    adapter.stats_lock = threading.Lock()
    adapter.first_close_causes = {'worker_tcp_eof_first': 2, 'relay_websocket_ended_first': 1}
    assert _adapter_first_closes(adapter) == adapter.first_close_causes
