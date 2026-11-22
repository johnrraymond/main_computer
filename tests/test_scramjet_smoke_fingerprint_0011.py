"""Isolated tests for Docker smoke image coherence. No Docker daemon required."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mcel_browser_scramjet_smoke_0011', HERE / 'tools/mcel_browser_scramjet_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def make_repo(tmp_path):
    root = tmp_path / 'docker/mcel-browser-scramjet'
    root.mkdir(parents=True)
    for asset in smoke.DOCKER_ASSETS:
        (root / asset).write_text('test input: '+asset, encoding='utf8')
    (smoke.report_dir(tmp_path)).mkdir(parents=True)
    return tmp_path


def test_local_asset_change_invalidates_cached_image(tmp_path):
    repo = make_repo(tmp_path)
    a = smoke.expected_image(repo)
    b = smoke.expected_image(repo)
    assert a == b
    assert a.startswith(smoke.IMAGE+'-c')
    (repo / 'docker/mcel-browser-scramjet/mcel-scramjet-db.js').write_text('changed',encoding='utf8')
    assert smoke.expected_image(repo) != a


def test_running_old_container_never_reused_even_if_homepage_works(tmp_path):
    repo = make_repo(tmp_path)
    stale = {'image':smoke.IMAGE, 'ip':smoke.ISOLATED_IP,
             'url':'http://127.0.0.2:48888/', 'container':'mcel-browser-scramjet-smoke-123abc'}
    smoke.state_path(repo).write_text(json.dumps(stale),encoding='utf8')
    with patch.object(smoke, 'docker_cmd') as docker, patch.object(smoke,'fetch_status') as http:
        assert smoke.status(repo) is None
    docker.assert_not_called()
    http.assert_not_called()


def test_running_container_must_match_actual_docker_image(tmp_path):
    repo = make_repo(tmp_path)
    desired = smoke.expected_image(repo)
    state = {'image':desired, 'fingerprint':smoke.build_fingerprint(repo),
             'ip':smoke.ISOLATED_IP, 'url':'http://127.0.0.2:48888/',
             'container':'mcel-browser-scramjet-smoke-123abc'}
    smoke.state_path(repo).write_text(json.dumps(state),encoding='utf8')
    with patch.object(smoke,'inspect_owned',return_value=True), \
         patch.object(smoke,'docker_cmd',return_value=SimpleNamespace(returncode=0,stdout=smoke.IMAGE+'|true')), \
         patch.object(smoke,'fetch_status') as http:
        assert smoke.status(repo) is None
    http.assert_not_called()


def test_runtime_missing_idb_initializer_is_not_healthy(tmp_path):
    repo = make_repo(tmp_path)
    desired = smoke.expected_image(repo)
    state = {'image':desired, 'fingerprint':smoke.build_fingerprint(repo),
             'ip':smoke.ISOLATED_IP, 'url':'http://127.0.0.2:48888/',
             'container':'mcel-browser-scramjet-smoke-123abc'}
    smoke.state_path(repo).write_text(json.dumps(state),encoding='utf8')
    def fetch(url, timeout=4):
        if url.endswith('mcel-scramjet-db.js'):
            raise smoke.URLError('HTTP Error 404')
        return 200, 100000
    with patch.object(smoke,'inspect_owned',return_value=True), \
         patch.object(smoke,'docker_cmd',return_value=SimpleNamespace(returncode=0,stdout=desired+'|true')), \
         patch.object(smoke,'fetch_status',side_effect=fetch):
        assert smoke.status(repo) is None


def test_reuse_verified_container_without_building(tmp_path):
    repo = make_repo(tmp_path)
    desired = smoke.expected_image(repo)
    state = {'image':desired, 'fingerprint':smoke.build_fingerprint(repo),
             'ip':smoke.ISOLATED_IP, 'url':'http://127.0.0.2:48888/',
             'container':'mcel-browser-scramjet-smoke-123abc'}
    smoke.state_path(repo).write_text(json.dumps(state),encoding='utf8')
    with patch.object(smoke,'inspect_owned',return_value=True), \
         patch.object(smoke,'docker_cmd',return_value=SimpleNamespace(returncode=0,stdout=desired+'|true')), \
         patch.object(smoke,'fetch_status',return_value=(200,100000)), \
         patch.object(smoke,'build_image') as build:
        assert smoke.start(repo) == state
    build.assert_not_called()


def test_stale_smoke_container_is_stopped_and_rebuilt(tmp_path):
    repo = make_repo(tmp_path)
    stale = {'image':smoke.IMAGE, 'ip':smoke.ISOLATED_IP,
             'url':'http://127.0.0.2:48888/', 'container':'mcel-browser-scramjet-smoke-123abc'}
    smoke.state_path(repo).write_text(json.dumps(stale),encoding='utf8')
    commands = []
    def fake_docker(*args, **kw):
        commands.append(args)
        if args[0]=='run':
            return SimpleNamespace(returncode=0,stdout='new-container-id')
        return SimpleNamespace(returncode=0,stdout='')
    with patch.object(smoke,'inspect_owned',return_value=True), \
         patch.object(smoke,'docker_cmd',side_effect=fake_docker), \
         patch.object(smoke,'build_image',return_value=smoke.expected_image(repo)) as build, \
         patch.object(smoke,'port_for',return_value=51999), \
         patch.object(smoke,'fetch_status',return_value=(200,100000)), \
         patch.object(smoke,'resource_preflight',return_value=(True,'all assets')):
        state=smoke.start(repo)
    assert state['image'] == smoke.expected_image(repo)
    assert state['fingerprint'] == smoke.build_fingerprint(repo)
    assert any(c[0]=='rm' and stale['container'] in c for c in commands)
    assert any(c[0]=='run' and smoke.expected_image(repo) == c[-1] for c in commands)
    build.assert_called_once_with(repo,rebuild=False,no_bootstrap=False)


def test_current_tag_with_missing_assets_forces_docker_rebuild(tmp_path):
    repo = make_repo(tmp_path)
    desired = smoke.expected_image(repo)
    state = {'image':desired, 'fingerprint':smoke.build_fingerprint(repo),
             'ip':smoke.ISOLATED_IP, 'url':'http://127.0.0.2:48888/',
             'container':'mcel-browser-scramjet-smoke-123abc'}
    smoke.state_path(repo).write_text(json.dumps(state),encoding='utf8')
    commands=[]
    def fake_docker(*args,**kw):
        commands.append(args)
        if args[0]=='inspect':return SimpleNamespace(returncode=0,stdout=desired+'|true')
        if args[0]=='run':return SimpleNamespace(returncode=0,stdout='new-id')
        return SimpleNamespace(returncode=0,stdout='')
    with patch.object(smoke,'inspect_owned',return_value=True), \
         patch.object(smoke,'docker_cmd',side_effect=fake_docker), \
         patch.object(smoke,'build_image',return_value=desired) as build, \
         patch.object(smoke,'port_for',return_value=51999), \
         patch.object(smoke,'fetch_status',return_value=(200,100000)), \
         patch.object(smoke,'resource_preflight',side_effect=[(False,'mcel-scramjet-db.js:404'),(True,'ok')]):
        newstate=smoke.start(repo)
    assert newstate['image']==desired
    assert any(c[0]=='rm' and state['container'] in c for c in commands)
    build.assert_called_once_with(repo,rebuild=True,no_bootstrap=False)
