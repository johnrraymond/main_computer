"""Version-set, production gate, Docker ownership, resource and rollback invariants."""
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import json
import subprocess

from main_computer.mcel_scramjet_docker import ScramjetDockerManager, PRODUCTION_IMAGE, ScramjetError
from tools import mcel_browser_scramjet_v2 as v2
from tools import mcel_browser_scramjet_v2_smoke as smoke

ROOT = Path(__file__).resolve().parent.parent


def test_pinned_v2_set_and_separate_v1_image():
    data=json.loads((ROOT/v2.SOURCE/'package.json').read_text(encoding='utf8'))['dependencies']
    assert data['@mercuryworkshop/scramjet']==v2.V2_CORE=='2.0.67-alpha.2'
    assert data['@mercuryworkshop/scramjet-controller']==v2.V2_CONTROLLER=='0.0.14'
    assert data['@mercuryworkshop/libcurl-transport']=='2.0.5'
    assert data['@mercuryworkshop/epoxy-transport']=='3.0.1'
    assert '@mercuryworkshop/bare-mux' not in data
    assert v2.image(ROOT,'smoke') != v2.image(ROOT,'app')
    assert PRODUCTION_IMAGE != v2.image(ROOT,'app')


def test_v2_build_context_is_fingerprinted(monkeypatch,tmp_path):
    source=tmp_path/v2.SOURCE
    source.mkdir(parents=True)
    for name in v2.ASSETS:(source/name).write_text('initial '+name,encoding='utf8')
    a=v2.fingerprint(tmp_path)
    (source/'sw.js').write_text('changed',encoding='utf8')
    assert a != v2.fingerprint(tmp_path)


def test_promotion_and_rollback_are_reversible(tmp_path):
    source=tmp_path/v2.SOURCE;source.mkdir(parents=True)
    for name in v2.ASSETS:(source/name).write_text(name)
    assert v2.enabled(tmp_path) is False
    v2.promote(tmp_path)
    assert v2.enabled(tmp_path) is True
    (source/'sw.js').write_text('changed')
    assert v2.enabled(tmp_path) is False  # source drift disables v2
    v2.rollback(tmp_path)
    assert v2.enabled(tmp_path) is False


def test_main_manager_selects_engine_only_after_promotion(tmp_path):
    source=tmp_path/v2.SOURCE;source.mkdir(parents=True)
    for name in v2.ASSETS:(source/name).write_text(name)
    manager=ScramjetDockerManager(tmp_path)
    try:
        with patch.object(manager,'_start_v2',return_value={'version':v2.V2_CORE}) as v2_start, \
             patch('tools.mcel_browser_scramjet_smoke.require_docker',side_effect=RuntimeError('v1 branch')):
            # Without a promoted marker, normal v1 branch remains selected.
            try:manager._start()
            except ScramjetError as exc:assert 'v1 branch' in str(exc)
            v2_start.assert_not_called()
            v2.promote(tmp_path)
            assert manager._start()=={'version':v2.V2_CORE}
            v2_start.assert_called_once()
    finally:manager.close()


def test_same_v2_image_only_reused_if_resources_verified(tmp_path):
    source=tmp_path/v2.SOURCE;source.mkdir(parents=True)
    for name in v2.ASSETS:(source/name).write_text(name)
    v2.promote(tmp_path)
    manager=ScramjetDockerManager(tmp_path)
    record={'image':v2.image(tmp_path,'app'),'container':'mcel-browser-scramjet-v2-app-1234abcd',
            'url':'http://127.0.0.3:49152/'}
    try:
        with patch.object(manager,'_owned',return_value=True), \
             patch('tools.mcel_browser_scramjet_smoke.fetch_status',side_effect=lambda url,timeout=3:(200,2000)) as f:
            assert manager._healthy(record) is True
            assert f.call_count==len(v2.REQUIRED)
            assert manager._healthy({**record,'image':PRODUCTION_IMAGE}) is False
    finally:manager.close()


def test_v2_production_wisp_does_not_allow_loopback():
    server=(ROOT/v2.SOURCE/'server.mjs').read_text(encoding='utf8')
    assert 'wisp.options.allow_loopback_ips = false' in server
    assert 'wisp.options.allow_private_ips = false' in server
    assert 'wisp.options.hostname_whitelist = [smokeFixtureHost, ...smokePublicHostPatterns]' in server
    assert 'wisp.options.allow_private_ips = false' in server
    assert "smokeHostPolicy: smoke ? 'fixture-plus-public-acceptance'" in server
    assert "req.headers.origin !== 'http://' + host" in server


def test_frontend_uses_v2_controller_and_urlwatcher_not_v1_api():
    js=(ROOT/v2.SOURCE/'mcel-surface.js').read_text(encoding='utf8')
    for token in ('controller.wait()', 'UrlWatcherPlugin(', 'controller.createFrame(',
                  'new EpoxyClient({wisp})','/controller/controller.api.js'):
        assert token in js
    assert '$scramjetLoadController()' not in js
    assert 'BareMux' not in js
    assert "frame.addEventListener('urlchange'" not in js


def test_v2_smoke_runs_real_mcelshell_and_gates_promotion():
    source=(ROOT/'tools/mcel_browser_scramjet_v2_smoke.py').read_text(encoding='utf8')
    assert 'MockTrustedMCEL' in source
    assert 'mcel-v2-end-to-end-ast-fetch' in source
    assert 'v2-301-redirect' in source
    assert 'v2-final-url-authority' in source
    assert "if passed and args.promote_after_pass" in source
    assert "elif args.promote_after_pass" in source
    dispatch=(ROOT/'tools/mcel_browser_scramjet_smoke.py').read_text(encoding='utf8')
    assert "'--v1' not in sys.argv[1:]" in dispatch


def test_v2_production_owned_container_name_and_label(tmp_path):
    source=tmp_path/v2.SOURCE;source.mkdir(parents=True)
    for name in v2.ASSETS:(source/name).write_text(name)
    v2.promote(tmp_path)
    manager=ScramjetDockerManager(tmp_path)
    tag=v2.image(tmp_path,'app')
    record={'image':tag,'container':'mcel-browser-scramjet-v2-app-1234abcd',
            'url':'http://127.0.0.3:49152/'}
    try:
        with patch.object(manager,'_docker',return_value=SimpleNamespace(returncode=0,stdout=f'true|{tag}|true')) as inspect:
            assert manager._owned(record)
            assert 'inspect' in inspect.call_args.args
            assert not manager._owned({**record,'container':'some-other-app-1234abcd'})
    finally:manager.close()


def test_v2_smoke_requires_a_verified_activated_worker_for_promotion():
    code=(ROOT/'tools/mcel_browser_scramjet_v2_smoke.py').read_text(encoding='utf8')
    assert "check('v2-service-worker-activated'" in code
    assert "'v2-service-worker-activated'" in code.split("required=(",1)[1]
    assert "getRegistration('/')" in code
    assert "'worker_failure_diagnostic'" in code
    assert "'mcel_status'" in code and "'surface_error'" in code


def test_v2_worker_uses_its_registration_not_old_page_controller():
    code=(ROOT/v2.SOURCE/'mcel-surface.js').read_text(encoding='utf8')
    assert 'const worker = await waitForRegisteredWorker(registration)' in code
    assert 'active.scriptURL === expectedScript' in code
    assert "registration.scope !== expectedScope" in code
    assert 'navigator.serviceWorker.controller || registration.active' not in code
    assert "'service-worker-activation'" in code


def test_public_acceptance_uses_libcurl_and_runs_before_private_network_refusal():
    surface=(ROOT/v2.SOURCE/'mcel-surface.js').read_text(encoding='utf8')
    code=(ROOT/'tools/mcel_browser_scramjet_v2_smoke.py').read_text(encoding='utf8')
    assert "qs.get('transport') === 'epoxy' ? 'epoxy' : 'libcurl'" in surface
    assert "import('/epoxy/index.mjs')" in surface
    assert "import('/libcurl/index.mjs')" in surface
    assert "new EpoxyClient({wisp})" in surface
    assert "'epoxy/index.mjs'" in str(v2.REQUIRED)
    assert "default='libcurl'" in code
    assert "args.promote_after_pass and args.transport != 'libcurl'" in code
    assert "check('v2-transport-selection'" in code
    assert "'v2-transport-selection'" in code.split('required=(',1)[1]
    assert code.index("check('public-youtube',True)") < code.index("denied=remote.locator('body')")
    assert "check('v2-fixture-after-public',True)" in code
    assert 'ignore_https_errors=False' in code
    assert 'CURLOPT_SSL_VERIFYPEER' not in surface


def test_epoxy_diagnostic_cannot_promote_even_if_requested(monkeypatch):
    import pytest
    monkeypatch.chdir(ROOT)
    with pytest.raises(SystemExit) as caught:
        smoke.main(['run','--transport','epoxy','--public','--promote'])
    assert caught.value.code == 2


def test_public_smoke_does_not_forbid_its_own_https_cohort():
    from pathlib import Path
    source=(ROOT/v2.SOURCE/'server.mjs').read_text(encoding='utf8')
    # This tests the effective Wisp policy wiring, not a comment or just
    # an unrelated list of public host patterns.
    assert 'wisp.options.hostname_whitelist = [smokeFixtureHost, ...smokePublicHostPatterns]' in source
    for pattern in ('/^example\\.org$/i', '/(?:^|\\.)youtube\\.com$/i',
                    '/(?:^|\\.)ytimg\\.com$/i', '/(?:^|\\.)googleapis\\.com$/i'):
        assert pattern in source
    assert "smokeHostPolicy: smoke ? 'fixture-plus-public-acceptance'" in source
    assert 'wisp.options.allow_loopback_ips = false' in source
    assert 'wisp.options.allow_private_ips = false' in source
    test_code=(ROOT/'tools/mcel_browser_scramjet_v2_smoke.py').read_text(encoding='utf8')
    assert "check('v2-public-wisp-host-policy',allowed" in test_code
    assert "'v2-public-wisp-host-policy'" in test_code.split("required=(",1)[1]


def test_production_transport_and_promotion_gate_are_the_same():
    surface=(ROOT/v2.SOURCE/'mcel-surface.js').read_text(encoding='utf8')
    cli=(ROOT/'tools/mcel_browser_scramjet_v2_smoke.py').read_text(encoding='utf8')
    assert "const transportName = qs.get('transport') === 'epoxy' ? 'epoxy' : 'libcurl'" in surface
    assert "default='libcurl'" in cli
    assert "args.promote_after_pass and args.transport != 'libcurl'" in cli
    assert "frameUrl.searchParams.set('transport', 'epoxy');" in cli
    assert "if transport == 'epoxy':" in cli


def test_passing_libcurl_public_acceptance_can_promote(monkeypatch):
    monkeypatch.chdir(ROOT)
    fake_sidecar={'image':'test-image','url':'http://127.0.0.2:12345/'}
    with patch('tools.mcel_browser_scramjet_smoke.require_docker'), \
         patch.object(smoke,'status',return_value=None), \
         patch.object(smoke,'start',return_value=fake_sidecar), \
         patch.object(smoke,'stop'), \
         patch.object(smoke,'acceptance',return_value=True) as accept, \
         patch.object(v2,'promote') as promote:
        assert smoke.main(['run','--public','--promote']) == 0
        assert accept.call_args.kwargs['transport'] == 'libcurl'
        assert accept.call_args.kwargs['public'] is True
        promote.assert_called_once_with(ROOT)


def test_failed_libcurl_public_acceptance_cannot_promote(monkeypatch):
    monkeypatch.chdir(ROOT)
    with patch('tools.mcel_browser_scramjet_smoke.require_docker'), \
         patch.object(smoke,'status',return_value=None), \
         patch.object(smoke,'start',return_value={'image':'test-image','url':'http://127.0.0.2:12345/'}), \
         patch.object(smoke,'stop'), \
         patch.object(smoke,'acceptance',return_value=False), \
         patch.object(v2,'promote') as promote:
        assert smoke.main(['run','--public','--promote']) == 1
        promote.assert_not_called()
