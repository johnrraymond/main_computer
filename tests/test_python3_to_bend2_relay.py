"""Restricted native relay generation and independent service oracle."""
from pathlib import Path
import json
import re
import socket
import subprocess
import pytest
from tools.python3_to_bend2.relay_lowering import lower_relay
from tools.python3_to_bend2.seh_relay_docker import run_relay, disposable_identity
from tools.python3_to_bend2 import seh_docker as lab
from tools.python3_to_bend2.syntax import TranslationError

ROOT=Path(__file__).resolve().parents[1]
SOURCES=tuple((ROOT/p).read_text(encoding='utf-8') for p in ('main_computer/seh/relay.py','main_computer/slh/authenticator.py','main_computer/seh/stream_writer.py'))


def test_relay_generated_source():
    result=lower_relay(*SOURCES)
    for needle in ('def RelayClient.loop(', 'def RelayTunnel.loop(', 'TCP.try_recv_bytes(s,1,10)', 'TCP.try_send_bytes(s,bytes,1000)', 'Chan.new(RelayMessage,16)', 'Chan.new(String,64)', 'LabB64.encode(bytes)', 'LabB64.decode(text)', 'RelayClient.cleanup(', 'SLH_DISCONNECTED','AUTH_TIMEOUT','INVALID_STREAM_FRAME', 'STREAM_BACKPRESSURE','Chan.close(String,outbox)'):
        assert needle in result
    assert 'RELAY_NOT_TRANSLATED' not in result
    assert 'Chan(Bool)' not in result
    assert result.count('def main() -> IO(Unit):')==1
    assert result==lower_relay(*SOURCES)
    assert 'TCP.listen("127.0.0.1",18081)' in result


@pytest.mark.parametrize('index',[0,2])
def test_source_drift(index):
    sources=list(SOURCES); sources[index]+='\n# unreviewed change\n'
    with pytest.raises(TranslationError,match='source drift'): lower_relay(*sources)


def test_no_local_match_scrutinees():
    result=lower_relay(*SOURCES)
    params=None; seen=set(); name=''
    for line in result.splitlines():
        header=re.match(r'^(?:@unsafe )?def ([\w.]+)\((.*)\) -> ',line)
        if header:
            name=header[1]
            params=set(re.findall(r'(?:^|,\s*)\+?(\w+)\s*:',header[2])) if name.startswith(('Relay','LabBound.','LabWire.','LabState.')) else None
        elif line and not line[0].isspace(): params=None
        if params is not None:
            m=re.match(r'\s+match\s+(.+):\s*$',line)
            if m:
                assert set(m[1].split())<=params,(name,line,params)
                seen.add(name)
    assert len(seen)>70


def test_no_untyped_do_bindings():
    # Pure let bindings are valid outside effectful blocks; check the actual
    # composed output, including nested branches and match arms.
    result = lower_relay(*SOURCES)
    do_scopes = []
    for number, line in enumerate(result.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        while do_scopes and indent <= do_scopes[-1]:
            do_scopes.pop()
        if do_scopes:
            assert not re.match(r'\s*\+?\w+\s*=', line), (number, line)
        if re.match(r'\s*do IO<.*>:\s*$', line):
            do_scopes.append(indent)
    # The same absolute deadline is used for both parts of the receive.
    assert 'RelayIO.read_at(s,op,Nat.add(now,2000n))' in result
    assert 'def RelayIO.read_at(s: Socket,op: U32,+deadline: Nat)' in result
    assert 'LabWire.read(s,1n,deadline,[op])' in result
    assert 'LabWire.header_pair(header,deadline)' in result


def test_real_python_oracle():
    pytest.importorskip('websockets'); pytest.importorskip('cryptography')
    directory=ROOT/'tools/python3_to_bend2'; ns={}
    for p in ('seh_bound_probe.py','seh_relay_probe.py'):
        exec((directory/p).read_text(),ns)
    with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
    private,_=disposable_identity()
    rows=ns['run_relay']('reference',port,private)
    assert len(rows)==40
    assert rows['relay_three_upstream'] and rows['relay_three_downstream'] and rows['relay_reconnect_isolated']
    assert rows['relay_invalid_data_close']==[1008,'INVALID_STREAM_FRAME']
    assert rows['relay_tunnel_loss']==[1012,'SLH_DISCONNECTED']


@pytest.mark.parametrize('mismatch',[False,True])
def test_docker_gate(tmp_path,mismatch):
    fake=lab.Docker('docker','unit',5); calls=[]
    expected={str(i):i for i in range(40)}
    def result(code=0,out='',err=''): return subprocess.CompletedProcess([],code,out,err)
    def call(*args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='inspect': return result(out='verified:base\n')
        if 'readlink' in args: return result(out='/usr/local/bin/seh-native\n')
        if 'py3-bend2-seh-relay-missing-unit' in args: return result(1,err='bound public key required')
        if 'py3-bend2-seh-relay-invalid-unit' in args: return result(1,err='invalid bound public key')
        if kwargs.get('input'):
            rows=dict(expected)
            if mismatch and 'py3-bend2-seh-relay-probe-unit' in args: rows['0']='wrong'
            return result(out=json.dumps(rows))
        return result()
    fake.call=call
    report={'service_gate':'BLOCKED_NOT_LINKED'}
    run_relay(fake,ROOT,tmp_path,'native',report)
    assert report['bound_relay_gate']==('DIFFERENTIAL_FAILED' if mismatch else 'PASS')
    assert report['service_gate']=='BLOCKED_NOT_LINKED'
    assert len(report['relay_cases'])==40
    candidate=[a for a,k in calls if 'py3-bend2-seh-relay-unit' in a and '-d' in a][0]
    assert '--read-only' in candidate and candidate[candidate.index('--network')+1]=='none'
    assert all('PRIVATE KEY' not in str(a) for a,k in calls)
    assert 'python' not in (tmp_path/'relay'/'Dockerfile').read_text().lower()
