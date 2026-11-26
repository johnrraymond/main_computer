from pathlib import Path
import json
import socket
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.python3_to_bend2 import listener_lowering as lower
from tools.python3_to_bend2 import seh_listener_docker as gate
from tools.python3_to_bend2 import seh_docker as lab
from tools.python3_to_bend2.syntax import TranslationError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / gate.SOURCE).read_text(encoding='utf-8')


def test_source_derived_callbacks_and_native_entry():
    generated = lower.lower_unbound_listener(SOURCE)
    assert 'def py3_main(' in generated and 'def py3_startup(' in generated
    assert 'PyJSON.render(' in generated
    assert 'def main() -> IO(Unit):' in generated
    assert 'TCP.listen("127.0.0.1", 18080)' in generated
    assert 'TCP.accept(' in generated and 'TCP.send_bytes(' in generated
    assert 'String.eq(path, "/healthz")' in generated
    assert 'String.eq(path, "/readyz")' in generated
    assert 'Some{"200 OK"}' in generated and 'Some{"503 Service Unavailable"}' in generated
    assert 'SEH_NOT_BOUND' not in (ROOT / 'tools/python3_to_bend2/listener_transport.bend').read_text()
    assert '[136, 15, 3, 240, 83, 69, 72, 95, 78, 79, 84, 95, 66, 79, 85, 78, 68]' in generated
    assert 'bound service is not translated' in generated
    assert 'Process.run(' not in generated


def test_mutated_application_data_is_recompiled_not_recorded():
    altered = SOURCE.replace("code=1008, reason='SEH_NOT_BOUND'", "code=1001, reason='OTHER'").replace("'/healthz'", "'/live'").replace('HTTPStatus.SERVICE_UNAVAILABLE', 'HTTPStatus.BAD_REQUEST')
    emitted = lower.lower_unbound_listener(altered,port=19000)
    assert 'String.eq(path, "/live")' in emitted and 'TCP.listen("127.0.0.1", 19000)' in emitted
    assert 'Some{"400 Bad Request"}' in emitted
    assert '[136, 7, 3, 233, 79, 84, 72, 69, 82]' in emitted
    assert emitted != lower.lower_unbound_listener(SOURCE)


@pytest.mark.parametrize('old,new', [
    ('if not encoded:', 'if encoded:'),
    ('ws.close(code=1008, reason=\'SEH_NOT_BOUND\')', 'ws.send("unsafe")'),
    ('max_size=8192,', 'max_size=9999,'),
    ('server.serve_forever()', 'server.shutdown()'),
    ('return None\n        print', 'return 7\n        print'),
])
def test_unsupported_application_changes_fail_closed(old,new):
    modified = SOURCE.replace(old,new)
    assert modified != SOURCE
    with pytest.raises(TranslationError):
        lower.lower_unbound_listener(modified)


def test_independent_reference_actual_service():
    pytest.importorskip('websockets')
    pytest.importorskip('cryptography')
    from tools.python3_to_bend2 import seh_listener_probe as probe
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    with probe.original(port):
        result = probe.observe(port)
    assert len(result) == 10
    assert result['invalid_upgrade_status'] == 400
    assert result['unknown_http_status'] == 426
    assert result['ws:/unknown'] == [1008,'SEH_NOT_BOUND']


def test_compare_requires_exact_inventory_and_bytes():
    assert gate.compare_observations({'x':[200,'a']},{'x':[200,'a']})[0]['status']=='VERIFIED'
    assert all(r['status']=='DIFFERENTIAL_FAILED' for r in gate.compare_observations({'x':[200,'a'],'y':1},{'x':[200,'b'],'z':1}))


def test_native_container_and_probe_isolation_and_guard():
    fake = lab.Docker('docker','test',5)
    events = []
    expected = {str(i):i for i in range(10)}
    def result(code=0,out='',err=''):
        return subprocess.CompletedProcess([],code,out,err)
    def call(*args,**kwargs):
        events.append((args,kwargs))
        if args[0]=='inspect': return result(out='base:verified\n')
        if 'reference' in args: return result(out=json.dumps(expected))
        if 'SEH_SLH_PUBLIC_KEY_B64=disposable-invalid' in args: return result(1,err='bound service is not translated')
        if 'readlink' in args: return result(out='/usr/local/bin/seh-native\n')
        if 'candidate' in args: return result(out=json.dumps(expected))
        return result()
    def build(context,name,**kwargs):
        assert 'ENTRYPOINT ["/usr/local/bin/seh-native"]' in (context/'Dockerfile').read_text()
        assert 'USER 65534:65534' in (context/'Dockerfile').read_text()
        assert '7f454c46' in (context/'Dockerfile').read_text()
        assert 'TCP.listen' in (context/'service.bend').read_text()
        assert kwargs['extra']==('--build-arg','BASE_IMAGE=base:verified')
        return 'candidate:image'
    fake.call=call; fake.build=build
    with TemporaryDirectory() as tmp:
        report={}
        gate.run_listener(fake,ROOT,Path(tmp),'native-toolchain',report)
    assert report['listener_gate']=='PASS' and report['listener_bound_guard']=='PASS'
    assert len(report['listener_cases'])==10
    runs=[a for a,k in events if a[0]=='run']
    assert len(runs)==4
    assert all('--read-only' in a and not any(x in a for x in ('-p','--publish','-v','--mount','--privileged')) for a in runs)
    assert [a[a.index('--network')+1] for a in runs]==['none','none','none','container:py3-bend2-seh-listener-test']


def test_build_failure_does_not_certify_listener():
    fake=SimpleNamespace(call=lambda *a,**k:SimpleNamespace(stdout='base'),build=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('compiler failed')))
    with TemporaryDirectory() as tmp:
        report={}
        with pytest.raises(RuntimeError,match='compiler failed'):
            gate.run_listener(fake,ROOT,Path(tmp),'native',report)
    assert report['listener_gate']=='NATIVE_BUILD_FAILED'
    assert 'listener_cases' not in report


def test_generated_listener_matches_only_definition_parameters():
    """Bend 2.0.40 forbids matching on locally computed binders.

    Require a stricter subset for our listener: every scrutinee must be a
    definition parameter, even when the compiler permits constructor fields.
    Inspect the final emitted program, including transport and policy helpers.
    """
    import re
    generated = lower.lower_unbound_listener(SOURCE)
    params = None
    name = ''
    seen = set()
    for line in generated.splitlines():
        header = re.match(r'^(?:@unsafe )?def ([\w.]+)\((.*)\) -> ', line)
        if header:
            name = header[1]
            params = (set(re.findall(r'(?:^|,\s*)\+?(\w+)\s*:', header[2]))
                      if name.startswith(('LabNet.', 'LabPolicy.')) else None)
        elif line and not line[0].isspace():
            params = None
        if params is not None:
            match = re.match(r'\s+match\s+(.+):\s*$', line)
            if match:
                assert set(match[1].split()) <= params, (name, line, params)
                seen.add(name)
    assert {'LabPolicy.status_first', 'LabPolicy.status_second',
            'LabPolicy.text_value', 'LabNet.header_empty', 'LabNet.header_hit',
            'LabNet.path_valid', 'LabNet.ws_key_valid', 'LabNet.upgrade_token',
            'LabNet.upgrade_valid', 'LabNet.read_bounded', 'LabNet.read_complete',
            'LabNet.read_more', 'LabNet.read_poll', 'LabNet.read_result',
            'LabNet.serve_result', 'LabNet.guard_empty'} <= seen
