"""Dual-employment acceptance; only the deployed target discovery is mocked."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.slh_seh_lab import dual_employment as lane


def test_local_real_relay_same_worker_both_employers():
    result = lane.run(local_relay=True)
    assert result['employment_status'] == 'PASS', result
    assert result['status'] == 'PASS'
    assert result['reason'] == 'verified'
    assert result['great_library_status'] == 'REPRESENTED_BY_PROTOCOL_MOCK'
    assert result['transport'] == 'LOCAL_REAL_RELAY'
    assert set(result['checks']) == lane.REQUIRED_CHECKS
    assert all(value is True for value in result['checks'].values())
    public, private = result['observations']
    assert public['generation'] == private['generation']
    assert public['request_id'] != private['request_id']
    assert public['reservation_types'] == ['public']
    assert private['reservation_types'] == ['private']
    assert private['prior_hub_state'] == 'closed'
    assert result['public_marketplace']['charged_credit_wei'] == str(lane.UNIT)
    assert result['public_marketplace']['earned_credit_wei'] == str(lane.UNIT)
    assert result['org_accounting']['assessed_credit_wei'] == str(lane.UNIT // 5)


def test_preflight_refusal_never_falls_back():
    with patch.object(lane, 'canonical_relay', side_effect=lane.Refused('isolated-channel-not-supported')), \
         patch.object(lane, '_in_memory_hub') as hub:
        result = lane.run()
    hub.assert_not_called()
    assert result['status'] == result['employment_status'] == 'UNVERIFIED'
    assert result['transport'] == 'CANONICAL_DEPLOYED_WSS_ISOLATED_CHANNEL'
    assert result['reason'] == 'isolated-channel-not-supported'


@pytest.mark.parametrize('capabilities,ready', [
    ({'isolated_lab_channels':False,'production_tunnel_untouched':True},True),
    ({'isolated_lab_channels':True,'production_tunnel_untouched':False},True),
    ({'isolated_lab_channels':True,'production_tunnel_untouched':True},False),
])
def test_deployed_discovery_refuses_missing_isolation_or_primary(capabilities,ready):
    with patch('tools.slh_seh_connector.default_state',return_value=Path('never-read')), \
         patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
               return_value=SimpleNamespace(seh_url='wss://example.invalid')), \
         patch.object(lane,'relay_observation',side_effect=[{'live':True},{'slh_connected':ready},capabilities]), \
         patch('tools.slh_seh_connector.service_identity') as identity:
        with pytest.raises(lane.Refused): lane.canonical_relay()
    identity.assert_not_called()


def test_deployed_discovery_uses_existing_key_read_only():
    key=object()
    with patch('tools.slh_seh_connector.default_state',return_value=Path('never-read')), \
         patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
               return_value=SimpleNamespace(seh_url='wss://example.invalid')), \
         patch.object(lane,'relay_observation',side_effect=[
             {'live':True},{'slh_connected':True},{'isolated_lab_channels':True,'production_tunnel_untouched':True}]), \
         patch('tools.slh_seh_connector.service_identity',return_value=(key,'public')) as identity:
        assert lane.canonical_relay()==('wss://example.invalid',key)
    identity.assert_called_once_with(Path('never-read'),create=False)


def test_gate_never_accepts_truthy_non_boolean():
    report={'checks':{}}
    with pytest.raises(lane.Refused):lane.require(report,'unverified',1)
    assert report['checks']=={'unverified':False}


def test_preflight_capability_http_failure_has_exact_evidence():
    from urllib.error import HTTPError
    from io import BytesIO
    from unittest.mock import MagicMock
    def http(request, timeout):
        path = request.full_url.rsplit('/', 1)[-1]
        assert request.get_header('User-agent') == 'Mozilla/5.0'
        assert request.get_header('Accept') == 'application/json'
        if path == 'lab-capabilities':
            raise HTTPError(request.full_url, 404, 'missing', {}, BytesIO(b'private response'))
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"service":"main_computer.seh","live":true,"slh_connected":true}'
        return response
    with patch('tools.slh_seh_connector.default_state',return_value=Path('never-read')), \
         patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',return_value=SimpleNamespace(seh_url='wss://example.invalid')), \
         patch.object(lane,'build_opener') as opener, \
         patch('tools.slh_seh_connector.service_identity') as key, \
         patch.object(lane,'_in_memory_hub') as hub:
        opener.return_value.open.side_effect = http
        report = lane.run(preflight_only=True)
    assert report['status'] == report['employment_status'] == 'UNVERIFIED'
    assert report['stage'] == 'relay-preflight-lab-capabilities'
    assert report['reason'] == 'lab-capabilities-http-404'
    assert report['http_status'] == 404
    assert report['relay_preflight']['readyz']['slh_connected'] is True
    assert report['relay_preflight']['healthz']['live'] is True
    assert 'private response' not in str(report)
    key.assert_not_called(); hub.assert_not_called()


def test_preflight_only_success_does_not_claim_employment():
    with patch.object(lane,'canonical_relay',return_value=('wss://example.invalid',object())), \
         patch.object(lane,'exercise') as exercise:
        report=lane.run(preflight_only=True)
    assert report['status']=='PASS_PREFLIGHT_ONLY'
    assert report['employment_status']=='UNVERIFIED'
    assert report['great_library_status']=='NOT_TESTED'
    exercise.assert_not_called()


def test_resolution_http_failure_distinguished():
    from urllib.error import HTTPError
    with patch('tools.slh_seh_connector.default_state',return_value=Path('never-read')), \
         patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',side_effect=HTTPError('https://example.invalid',401,'denied',{},None)), \
         patch.object(lane,'relay_observation') as observe:
        report=lane.run(preflight_only=True)
    assert report['stage']=='canonical-resolution'
    assert report['reason']=='canonical-resolution-failed-http-401'
    assert report['http_status']==401
    observe.assert_not_called()
