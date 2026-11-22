from pathlib import Path
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch
import json

MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'mcel_browser_site_diagnostic_0013.py'
spec = importlib.util.spec_from_file_location('site_diagnostic_0013', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_rejects_wrong_sidecar_ip(tmp_path):
    s=tmp_path/'runtime'/'reports'/'mcel-browser-scramjet-app'
    s.mkdir(parents=True)
    (s/'container-state.json').write_text(json.dumps({'container':'mcel-browser-scramjet-app-1234abcd',
        'image':'mcel-browser-scramjet-app:revision', 'url':'http://127.0.0.1:1111/'}))
    try:
        module.load_sidecar(tmp_path)
    except RuntimeError as error:
        assert '127.0.0.3' in str(error)
    else:
        raise AssertionError('Unsafe address accepted')


def test_requires_verified_docker_container(tmp_path):
    s=tmp_path/'runtime'/'reports'/'mcel-browser-scramjet-app'
    s.mkdir(parents=True)
    (s/'container-state.json').write_text(json.dumps({'container':'mcel-browser-scramjet-app-1234abcd',
        'image':'mcel-browser-scramjet-app:v4','url':'http://127.0.0.3:52000/'}))
    with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,
              stdout='false|mcel-browser-scramjet-app:v4|true',stderr='')):
        try:
            module.load_sidecar(tmp_path)
        except RuntimeError as error:
            assert 'authenticated' in str(error)
        else:
            raise AssertionError('Unverified container accepted')
    with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,
              stdout='true|mcel-browser-scramjet-app:v4|true\n',stderr='')):
        assert module.load_sidecar(tmp_path)['url']=='http://127.0.0.3:52000'


def test_docker_probe_has_fixed_destinations():
    with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,
              stdout='{"host":"example.org","https":{"status":200}}\n',stderr='')) as run:
        result=module.docker_connection_probe('mcel-browser-scramjet-app-1234abcd')
        assert result['results'][0]['host']=='example.org'
        assert run.call_args.args[0][:4]==['docker','exec','mcel-browser-scramjet-app-1234abcd','node']
        script=run.call_args.args[0][-1]
        assert "['example.org','youtube.com','www.youtube.com']" in script
        assert 'https.request' in script
        assert '1.1.1.3' in script and '1.1.1.1' in script
