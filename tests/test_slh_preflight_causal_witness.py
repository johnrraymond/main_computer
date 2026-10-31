"""The paid stress lane must retain evidence BEFORE fleet startup."""
from pathlib import Path
from types import SimpleNamespace
import json

from tools.slh_seh_lab.integrated_stress import _baseline_connection_evidence
from tools.slh_seh_lab.real_paid_service import _record_preliminary_worker_checkpoint


class Worker:
    device_id = 'worker-1'
    is_alive = True
    close_reason = ''
    last_error = ''
    local_adapter_port = 45678

    def transport_diagnostics(self):
        return [{'event': 'socket_connected', 'local_port': 45678}]


class Proxy:
    def __init__(self, workers):
        self.workers = workers

    def status(self):
        return {'workers': self.workers, 'sessions': {}, 'automatic': {'last_result': {'reason':'policy'}}}


def test_busy_registered_worker_is_not_classified_as_missing():
    data = _baseline_connection_evidence(
        worker_id='worker-1', status=Proxy([{'device_id':'worker-1', 'available':False,
                                          'reserved':1}]).status(), workers=[Worker()],
        adapter=SimpleNamespace(), api_port=1, org_token='token')
    assert data['org_original_worker_registered'] is True
    assert data['org_original_worker_available'] is False
    assert data['org_original_worker_reservations'] == 1
    assert data['client_is_alive'] is True
    assert 'connection_timelines' not in data


def test_missing_worker_fetches_gateway_audit(monkeypatch):
    import tools.slh_seh_lab.integrated_stress as target
    monkeypatch.setattr(target, '_fetch_gateway_diagnostics', lambda *a: {
        'events':[{'event':'gateway_handler_exception','device_id':'worker-1',
                   'exception_class':'ConnectionClosedOK'}]})
    data = target._baseline_connection_evidence(
        worker_id='worker-1', status=Proxy([]).status(), workers=[Worker()],
        adapter=SimpleNamespace(), api_port=1, org_token='token')
    assert data['org_original_worker_registered'] is False
    assert data['connection_timelines']['gateway_events'][0]['event'] == 'gateway_handler_exception'


def test_preliminary_paid_checkpoints_survive_as_distinct_stages(tmp_path: Path):
    path = tmp_path / 'integrated-stress-events.json'
    for stage in ('before_preliminary_paid_job','after_preliminary_paid_job'):
        _record_preliminary_worker_checkpoint(
            trace_file=path, stage=stage, worker=Worker(),
            proxy=Proxy([{'device_id':'worker-1','available':True,'reserved':0}]),
            api_port=1, org_token='token', adapter=SimpleNamespace())
    rows = json.loads(path.read_text())['events']
    assert [r['stage'] for r in rows] == ['before_preliminary_paid_job',
                                           'after_preliminary_paid_job']
    assert all(r['connection_timelines']['workers']['worker-1']['worker_transport_events']
               for r in rows)
    assert all(isinstance(r['unix_ns'], int) for r in rows)


def test_preflight_does_not_only_dump_exception_type():
    text = (Path(__file__).parents[1] / 'tools/slh_seh_lab/integrated_stress.py').read_text()
    assert "type(client.last_error).__name__" not in text
    assert "_baseline_connection_evidence(" in text
    assert "previous.get('events'" in text
