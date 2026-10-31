"""Contract tests for the real network stress runner and bounded profiles."""
import pytest
from tools.slh_seh_lab.integrated_stress import StressProfile

@pytest.mark.parametrize('workers,requests,concurrency', [
    (1, 2, 1), (257, 2, 1), (2, 1, 1), (2, 2049, 1), (2, 4, 0), (2, 4, 5), (2, 65, 65)])
def test_integrated_stress_rejects_invalid_bounds(workers, requests, concurrency):
    with pytest.raises(ValueError):
        StressProfile(workers=workers, requests=requests, concurrency=concurrency)


def test_integrated_stress_has_safe_bounded_default():
    p = StressProfile()
    assert (p.workers, p.requests, p.concurrency, p.seed) == (8,16,4,1729)


def test_integrated_network_stress_uses_same_real_hub_lab(monkeypatch):
    """Tests CLI wiring without creating a network or masquerading as acceptance."""
    from tools.slh_seh_lab import run_lab
    got = {}
    class Dummy:
        server_port = 12345
        payout_ledger_directory = object()
        def shutdown(self): got['shutdown'] = True
        def server_close(self): got['closed'] = True
    class Thread:
        def join(self, timeout): got['joined'] = timeout
    monkeypatch.setattr(run_lab,'_in_memory_hub',lambda:(Dummy(),Thread()))
    def exercise(**kwargs):
        got.update(kwargs)
        return {'pass':True,'status':'PASS','gates':{'production_boundary':True},
                'integrated_stress':{'pass':True,'status':'PASS'}}
    import tools.slh_seh_lab.real_paid_service as paid
    monkeypatch.setattr(paid,'verify_real_process_paid',exercise)
    result = run_lab.verify_integrated_network_stress(workers=4,requests=8,concurrency=2,seed=99)
    assert result['pass'] and result['integrated_stress']['pass']
    assert got['hub_url'] == 'http://127.0.0.1:12345'
    assert got['stress_profile'] == StressProfile(4,8,2,99)
    assert got['shutdown'] and got['closed'] and got['joined'] == 3


def test_integrated_stress_does_not_replay_baseline_policy_storm():
    """The large-fleet lane must not be gated on single-Worker churn tests."""
    import ast
    from pathlib import Path
    source=Path(__file__).parents[1]/'tools/slh_seh_lab/real_paid_service.py'
    module=ast.parse(source.read_text(encoding='utf-8'))
    fn=next(x for x in module.body if isinstance(x, ast.FunctionDef)
            and x.name=='verify_real_process_paid')
    guards=[x for x in ast.walk(fn) if isinstance(x,ast.If)
            and isinstance(x.test, ast.Compare)
            and isinstance(x.test.left, ast.Name)
            and x.test.left.id == 'stress_profile']
    assert any(any(isinstance(v, ast.Constant) and v.value is None for v in x.test.comparators)
               and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id=='run_integrated_stress' for node in x.orelse for n in ast.walk(node))
               and not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                           and n.func.id=='run_integrated_stress' for node in x.body for n in ast.walk(node))
               for x in guards)

def test_integrated_transport_is_not_counted_as_capacity_refusal():
    from pathlib import Path
    source=(Path(__file__).parents[1]/'tools/slh_seh_lab/integrated_stress.py').read_text()
    assert 'transport_errors' in source
    assert "_gate(not transport_errors" in source


def test_status_worker_rows_do_not_satisfy_name_membership():
    from tools.slh_seh_lab.integrated_stress import _registered_worker_ids
    rows = [{'device_id': 'a'}, {'device_id': 'b'}]
    assert _registered_worker_ids({'workers':rows}) == {'a', 'b'}
    assert set(['a']) & _registered_worker_ids({'workers':rows})
    assert not (set(['c']) & _registered_worker_ids({'workers':rows}))


def test_worker_close_evidence_redacts_exception_text():
    from types import SimpleNamespace
    from tools.slh_seh_lab.integrated_stress import _worker_close_evidence
    class Client:
        device_id='test-worker'
        is_alive=False
        close_reason='reader_error'
        last_error='ConnectionResetError: token=never-copy-this'
    info=_worker_close_evidence([Client()], {'test-worker'})
    assert info['test-worker']['client_error_class']=='CONNECTION_RESET'
    assert 'never-copy-this' not in str(info)


def test_worker_close_evidence_extracts_only_public_websocket_close_code():
    from tools.slh_seh_lab.integrated_stress import _worker_close_evidence
    class Client:
        device_id='test-worker'
        is_alive=False
        close_reason='reader_error'
        last_error='Hub closed the worker live-session websocket (close_code=1011). secret=do-not-show'
    info=_worker_close_evidence([Client()], {'test-worker'})
    assert info['test-worker']['websocket_close_code']==1011
    assert 'do-not-show' not in str(info)
