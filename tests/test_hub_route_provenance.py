"""Read-only Hub feature provenance gate regressions."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'tools/hub_route_provenance.py'
SPEC = importlib.util.spec_from_file_location('hub_route_provenance_tested', MODULE)
assert SPEC and SPEC.loader
under_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(under_test)


def test_missing_feature_fails_even_when_git_clean_and_pushed(tmp_path, monkeypatch):
    (tmp_path / 'main_computer').mkdir()
    (tmp_path / 'main_computer/stable_hub.py').write_text('def do(): pass\n')
    (tmp_path / 'tools/worker_browser_acceptance').mkdir(parents=True)
    (tmp_path / 'tools/worker_browser_acceptance/captain_bridge_audit.py').write_text('')
    rev = 'f' * 40
    def git(_root, *args):
        if args == ('rev-parse', 'HEAD'): return True, rev
        if args == ('ls-remote','origin','refs/heads/main'): return True, rev + '\trefs/heads/main'
        if args[:2] == ('show','HEAD:main_computer/stable_hub.py'): return True, 'def do(): pass'
        if args[:1] == ('status',): return True, ''
        raise AssertionError(args)
    monkeypatch.setattr(under_test, '_git', git)
    result = under_test.inspect_local_and_git(tmp_path)
    assert result['headMatchesOriginMain'] is True
    assert result['hubSourceClean'] is True
    assert result['workingHubRoutePresent'] is False
    assert result['committedHubRoutePresent'] is False


def test_route_uncommitted_fails_even_if_in_local_source(tmp_path, monkeypatch):
    (tmp_path / 'main_computer').mkdir()
    (tmp_path / 'main_computer/stable_hub.py').write_text(f'if path == "{under_test.DEFAULT_ROUTE}": pass')
    (tmp_path / 'tools/worker_browser_acceptance').mkdir(parents=True)
    (tmp_path / 'tools/worker_browser_acceptance/captain_bridge_audit.py').write_text('')
    monkeypatch.setattr(under_test, '_git', lambda repo, *args: (True, ' M main_computer/stable_hub.py') if args[0]=='status' else (True, 'unrelated'))
    result = under_test.inspect_local_and_git(tmp_path)
    assert result['workingHubRoutePresent'] is True
    assert result['committedHubRoutePresent'] is False
    assert result['hubSourceClean'] is False


def test_live_deposit_route_http_400_proves_validator_only():
    def open_url(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, 'bad request', {},
                  io.BytesIO(b'{"ok":false,"error":"chain_id must be a positive decimal integer"}'))
    result = under_test.probe_hub_route('https://testnet-hub.example', opener=open_url)
    assert result['routeVerified'] is True
    assert result['httpStatus'] == 400


@pytest.mark.parametrize(('status', 'body'), [
    (404, b'{"ok":false,"error":"not_found"}'),
    (400, b'{"ok":false,"error":"Cloudflare policy blocked"}'),
    (500, b'{"ok":false,"error":"server failed"}'),
])
def test_live_route_refuses_uncorroborated_http_responses(status, body):
    def open_url(req, timeout):
        raise urllib.error.HTTPError(req.full_url, status, 'error', {},io.BytesIO(body))
    assert under_test.probe_hub_route('https://testnet-hub.example', opener=open_url)['routeVerified'] is False


def test_live_probe_is_read_only_and_refuses_plain_http():
    assert under_test.probe_hub_route('http://testnet-hub.example')['routeVerified'] is False
    def open_url(req, timeout):
        assert req.get_method() == 'GET'
        assert req.full_url.endswith('/api/hub/v1/credits/bridge-deposit')
        return SimpleNamespace(__enter__=lambda self: self)
    # Only a documented 400 validator signature is positive evidence.
    assert under_test.DEFAULT_ROUTE.startswith('/api/hub/v1/')


def test_current_patch_requires_updated_audit_and_remote_commit(monkeypatch, tmp_path):
    (tmp_path / 'main_computer/config').mkdir(parents=True)
    (tmp_path / 'main_computer/config/hub_networks.json').write_text(json.dumps({
        'networks': {'testnet': {'hub_public_url': 'https://testnet-hub.example'}}}))
    source = {
        'workingHubRoutePresent':True, 'committedHubRoutePresent':True,
        'localAuditCallsExactLookup':False, 'headMatchesOriginMain':True,
        'hubSourceClean':True,
    }
    monkeypatch.setattr(under_test, 'inspect_local_and_git', lambda *args, **kwargs: source)
    monkeypatch.setattr(under_test, 'probe_hub_route', lambda *args, **kwargs: {'routeVerified':True})
    result = under_test.check(tmp_path,network='testnet',urls=[])
    assert result['verified'] is False
    assert result['interpretation'] == 'local-audit-not-updated'


def test_redeploy_feature_gate_opt_in_and_refuses_unrelated_operations():
    import importlib.util
    entry = MODULE.parents[1] / 'hub_mutate_harness.py'
    spec = importlib.util.spec_from_file_location('test_hub_mutate_feature_gate', entry)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = ['redeploy-hubs', '--network', 'testnet', '--require-hub-route', under_test.DEFAULT_ROUTE]
    args = module._parser().parse_args(argv)
    assert args.require_hub_route == under_test.DEFAULT_ROUTE
    with pytest.raises(module.HarnessError, match='applies only'):
        module.Harness(module._parser().parse_args(['inspect', '--require-hub-route',under_test.DEFAULT_ROUTE]))
    with pytest.raises(SystemExit):
        module._parser().parse_args(['redeploy-hubs', '--require-hub-route','/api/hub/v1/unknown'])


def test_real_git_porcelain_retains_leading_xy_column(tmp_path):
    # This is the exact bug observed on Windows: `_git().strip()` used to
    # turn Git's ` M tools/...` into `M tools/...` before path slicing.
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'user.name', 'Fixture'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'user.email', 'fixture@example.invalid'], check=True)
    audit = tmp_path / under_test.AUDIT_FILE
    audit.parent.mkdir(parents=True)
    audit.write_text('original\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', under_test.AUDIT_FILE], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-qm', 'fixture'], check=True)
    audit.write_text('changed\n')
    ok, status = under_test._git(tmp_path, 'status', '--porcelain=v1', '--untracked-files=all',
                                 '--', under_test.HUB_FILE, under_test.AUDIT_FILE)
    assert ok is True
    assert status == f' M {under_test.AUDIT_FILE}'


def test_porcelain_modified_audit_is_not_falsely_clean(tmp_path, monkeypatch):
    (tmp_path / 'main_computer').mkdir()
    (tmp_path / under_test.HUB_FILE).write_text(f'if path == "{under_test.DEFAULT_ROUTE}": pass')
    audit = tmp_path / under_test.AUDIT_FILE
    audit.parent.mkdir(parents=True)
    audit.write_text('def inspect_hub_historical_deposit(): pass\nrow.update(inspect_hub_historical_deposit())\n')
    rev = 'a' * 40
    def git(_repo, *args):
        if args[:1] == ('status',): return True, f' M {under_test.AUDIT_FILE}'
        if args[:2] == ('show', 'HEAD:' + under_test.HUB_FILE): return True, f'if path == "{under_test.DEFAULT_ROUTE}": pass'
        if args[:1] == ('rev-parse',): return True, rev
        if args[:1] == ('ls-remote',): return True, rev + '\trefs/heads/main'
        raise AssertionError(args)
    monkeypatch.setattr(under_test, '_git', git)
    result = under_test.inspect_local_and_git(tmp_path)
    assert result['auditChangedLocally'] is True
    assert result['hubSourceClean'] is True
    assert result['gitStatusParseVerified'] is True


def test_porcelain_modified_hub_blocks_source_ready(tmp_path, monkeypatch):
    (tmp_path / 'main_computer').mkdir()
    (tmp_path / under_test.HUB_FILE).write_text(f'if path == "{under_test.DEFAULT_ROUTE}": pass')
    (tmp_path / 'tools/worker_browser_acceptance').mkdir(parents=True)
    (tmp_path / under_test.AUDIT_FILE).write_text('')
    rev = 'a' * 40
    def git(_repo, *args):
        if args[:1] == ('status',): return True, f' M {under_test.HUB_FILE}'
        if args[:1] == ('show',): return True, f'if path == "{under_test.DEFAULT_ROUTE}": pass'
        if args[:1] == ('rev-parse',): return True, rev
        if args[:1] == ('ls-remote',): return True, rev + '\trefs/heads/main'
        raise AssertionError(args)
    monkeypatch.setattr(under_test, '_git', git)
    result = under_test.inspect_local_and_git(tmp_path)
    assert result['hubSourceClean'] is False
    assert result['auditChangedLocally'] is False


def test_porcelain_malformed_output_fails_closed(tmp_path, monkeypatch):
    (tmp_path / 'main_computer').mkdir()
    (tmp_path / under_test.HUB_FILE).write_text('')
    (tmp_path / 'tools/worker_browser_acceptance').mkdir(parents=True)
    (tmp_path / under_test.AUDIT_FILE).write_text('')
    monkeypatch.setattr(under_test, '_git', lambda r, *args: (True, 'M file') if args[0] == 'status' else (True,''))
    result = under_test.inspect_local_and_git(tmp_path)
    assert result['gitStatusParseVerified'] is False
    assert result['hubSourceClean'] is False


@pytest.mark.parametrize('response_body', [b'<html>Forbidden</html>', b'{"ok":false,"error":"denied"}'])
def test_live_403_never_claims_route_missing_or_present(response_body):
    def open_url(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(response_body))
    report = under_test.probe_hub_route('https://testnet-hub.example', opener=open_url)
    assert report['result'] == 'access-denied-http-403'
    assert report['routeVerified'] is False
    assert report['httpStatus'] == 403


def test_live_probe_uses_existing_captain_api_client_contract(monkeypatch):
    # Unlike the prior Accept-only GET, these are the headers accepted by the
    # existing Captain read-only Hub requests, without importing Captain CLI.
    monkeypatch.delenv('MAIN_COMPUTER_HUB_USER_AGENT', raising=False)
    def fake_open(req, timeout):
        assert req.get_method() == 'GET'
        assert req.get_header('Accept') == 'application/json'
        assert req.get_header('User-agent') == 'main-computer-captain-cli/1.0 (+https://greatlibrary.io)'
        assert req.get_header('X-main-computer-client') == 'captain-cli'
        assert req.data is None
        assert '?' not in req.full_url
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {},
            io.BytesIO(b'{"ok": false, "error":"chain_id must be a positive decimal integer"}'))
    result = under_test.probe_hub_route('https://testnet-hub.example', opener=fake_open)
    assert result['result'] == 'route-validator-present'
    assert result['routeVerified'] is True


def test_live_probe_honors_captain_configured_api_user_agent(monkeypatch):
    monkeypatch.setenv('MAIN_COMPUTER_HUB_USER_AGENT', 'authorized-captain-client/1.0')
    def fake_open(req, timeout):
        assert req.get_header('User-agent') == 'authorized-captain-client/1.0'
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {},
            io.BytesIO(b'{"ok": false, "error":"chain_id must be a positive decimal integer"}'))
    assert under_test.probe_hub_route('https://testnet-hub.example', opener=fake_open)['routeVerified']


def test_403_cloudflare_mitigation_classified_without_claiming_route_verified():
    def fake_open(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden',
            {'Server': 'cloudflare', 'cf-mitigated': 'challenge'},
            io.BytesIO(b'<html>access blocked</html>'))
    result = under_test.probe_hub_route('https://testnet-hub.example', opener=fake_open)
    assert result['accessControlHint'] == 'upstream-cloudflare-challenge'
    assert result['routeVerified'] is False


def test_403_browser_integrity_classified_without_raw_block_page():
    secret = 'private-page-secret'
    def fake_open(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {},
            io.BytesIO(('Error code: 1010 ' + secret).encode()))
    result = under_test.probe_hub_route('https://testnet-hub.example', opener=fake_open)
    assert result['accessControlHint'] == 'upstream-cloudflare-browser-integrity'
    assert secret not in json.dumps(result)
