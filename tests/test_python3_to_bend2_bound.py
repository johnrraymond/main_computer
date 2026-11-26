"""Source-linked bound stage, independent native crypto and oracle regressions."""
from pathlib import Path
import base64
import hashlib
import json
import re
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
import pytest
from tools.python3_to_bend2 import bound_lowering as lower
from tools.python3_to_bend2 import seh_bound_docker as gate
from tools.python3_to_bend2 import seh_docker as lab
from tools.python3_to_bend2.syntax import TranslationError

ROOT=Path(__file__).resolve().parents[1]
RELAY=(ROOT/'main_computer/seh/relay.py').read_text(encoding='utf-8')
AUTH=(ROOT/'main_computer/slh/authenticator.py').read_text(encoding='utf-8')


def test_generated_bound_program_uses_runtime_policy_and_no_relay():
    result=lower.lower_bound_listener(RELAY,AUTH)
    for needle in ('def py3_health(', 'def py3_challenge(', 'def py3_statement(', 'def py3_accepted(',
                   'PyJSON.decode(PyHeap.empty()', 'IO.spawn(Unit,LabBound.task', 'TCP.try_recv_bytes',
                   'TCP.listen("127.0.0.1",18081)', 'Chan.new(Bool,1)', 'Chan.new(Unit,80)',
                   'Process.run("/usr/local/bin/py3-native-crypto"', 'RELAY_NOT_TRANSLATED'):
        assert needle in result
    assert 'LAB_PORT' not in result and 'LAB_NONCE_SIZE' not in result
    assert result.count('def main() -> IO(Unit):')==1
    assert '"nonce","32"' in result
    assert 'SLH_ALREADY_CONNECTED' in result
    assert 'SEH_NOT_BOUND' not in result
    assert result==lower.lower_bound_listener(RELAY+'\n# comment\n',AUTH+'\n# comment\n')


@pytest.mark.parametrize('which,old,new',[
    ('relay','nonce = secrets.token_urlsafe(32)','nonce = secrets.token_urlsafe(16)'),
    ('relay',"result.get('type') != 'seh.auth.proof'","result.get('type') == 'seh.auth.proof'"),
    ('relay',"ws.close(code=1008, reason='SLH_ALREADY_CONNECTED')","ws.close(code=1000, reason='SLH_ALREADY_CONNECTED')"),
    ('auth','ec.ECDSA(hashes.SHA256())','ec.ECDSA(hashes.SHA384())'),
    ('auth','len(signature_raw) > 128','len(signature_raw) > 256'),
])
def test_source_drift_is_rejected(which,old,new):
    a,b=RELAY,AUTH
    if which=='relay': a=a.replace(old,new); assert a!=RELAY
    else: b=b.replace(old,new); assert b!=AUTH
    with pytest.raises(TranslationError,match='unsupported control flow'):
        lower.lower_bound_listener(a,b)


def test_generated_matches_only_parameters():
    src=lower.lower_bound_listener(RELAY,AUTH)
    params=None; seen=set(); name=''
    for line in src.splitlines():
        header=re.match(r'^(?:@unsafe )?def ([\w.]+)\((.*)\) -> ',line)
        if header:
            name=header[1]
            params=set(re.findall(r'(?:^|,\s*)\+?(\w+)\s*:',header[2])) if name.startswith(('LabBound.','LabWire.','LabState.','LabCrypto.')) else None
        elif line and not line[0].isspace(): params=None
        if params is not None:
            m=re.match(r'\s+match\s+(.+):\s*$',line)
            if m:
                assert set(m[1].split())<=params,(name,line,params)
                seen.add(name)
    assert len(seen)>=35


def test_bound_oracle_actual_python_service():
    pytest.importorskip('websockets'); pytest.importorskip('cryptography')
    from tools.python3_to_bend2 import seh_bound_probe as probe
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
    private,_=gate.disposable_identity()
    observed=probe.run('reference',port,private)
    assert len(observed)==22
    assert observed['replay']==[1008,'SLH_IDENTITY_REJECTED']
    assert observed['duplicate_tunnel']==[1008,'SLH_ALREADY_CONNECTED']
    assert observed['ready_after_auth'][0]==200 and observed['final_not_ready'][0]==503
    assert observed['active_tunnel_ping'] is True


@pytest.fixture(scope='module')
def crypto_binary(tmp_path_factory):
    cc=shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if not cc: pytest.skip('C compiler unavailable; Docker gate compiles the adapter')
    path=tmp_path_factory.mktemp('crypto')/'crypto'
    source=ROOT/'tools/python3_to_bend2/native_crypto.c'
    result=subprocess.run([cc,'-O2','-Wall','-Wextra','-Werror',str(source),'-lcrypto','-o',str(path)],capture_output=True,text=True)
    if result.returncode and ('openssl/' in result.stderr and ('No such file' in result.stderr or 'file not found' in result.stderr) or 'cannot find -lcrypto' in result.stderr):
        pytest.skip('OpenSSL development headers/library unavailable; Docker installs them')
    assert result.returncode==0,result.stderr
    return path


def test_native_crypto_python_interop(crypto_binary):
    from main_computer.slh.authenticator import generate_identity,sign_object,canonical_bytes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    key,pem=generate_identity(); wrong,_=generate_identity()
    encoded=base64.b64encode(pem.encode()).decode()
    def call(*args,body=b''):
        return subprocess.run([str(crypto_binary),*args],input=body,capture_output=True,timeout=5)
    assert call('validate',encoded).returncode==0
    for bad in ('','!','YQ==',encoded+'!','A'*5000):
        assert call('validate',bad).returncode!=0
    for curve in (ec.SECP384R1(),ec.SECP256K1()):
        other=ec.generate_private_key(curve).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        assert call('validate',base64.b64encode(other).decode()).returncode!=0
    other=rsa.generate_private_key(public_exponent=65537,key_size=2048).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    assert call('validate',base64.b64encode(other).decode()).returncode!=0
    nonces=set()
    for _ in range(16):
        got=call('nonce','32'); assert got.returncode==0
        nonce=got.stdout.decode(); assert len(nonce)==43 and len(base64.urlsafe_b64decode(nonce+'='))==32
        assert nonce not in nonces; nonces.add(nonce)
        obj={'version':2,'action':'seh.slh.connect','nonce':nonce}
        raw=canonical_bytes(obj); signature=sign_object(key,obj)
        assert call('verify',encoded,signature,body=raw).returncode==0
        assert call('verify',encoded,sign_object(wrong,obj),body=raw).returncode!=0
        assert call('verify',encoded,signature,body=raw+b' ').returncode!=0
        assert call('verify',encoded,signature,body=b'A'*8193).returncode!=0
    for bad in ('!','A','AAAA','A'*512):
        assert call('verify',encoded,bad,body=b'{}').returncode!=0
    for bad in ('0','-1','65','xx'):
        assert call('nonce',bad).returncode!=0


def test_docker_bound_isolation_and_exact_differential_gate():
    fake=lab.Docker('docker','unit',5); calls=[]
    expected={str(i):i for i in range(22)}
    def result(code=0,out='',err=''): return subprocess.CompletedProcess([],code,out,err)
    def call(*args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='inspect': return result(out='verified:base\n')
        if 'readlink' in args: return result(out='/usr/local/bin/seh-native\n')
        if 'py3-bend2-seh-bound-missing-unit' in args: return result(1,err='bound public key required')
        if 'py3-bend2-seh-bound-invalid-unit' in args: return result(1,err='invalid bound public key')
        if kwargs.get('input'): return result(out=json.dumps(expected))
        return result()
    def build(context,name,**kwargs):
        assert set(x.name for x in context.iterdir())=={'Dockerfile','service.bend','native_crypto.c'}
        assert 'USER 65534:65534' in (context/'Dockerfile').read_text()
        assert 'ENTRYPOINT ["/usr/local/bin/seh-native"]' in (context/'Dockerfile').read_text()
        return 'candidate:bound'
    fake.call=call; fake.build=build
    with TemporaryDirectory() as tmp:
        report={}; gate.run_bound(fake,ROOT,Path(tmp),'toolchain',report)
    assert report['bound_gate']=='PASS' and report['bound_relay_gate']=='BLOCKED_NOT_TRANSLATED'
    runs=[(args,kw) for args,kw in calls if args[0]=='run']
    assert len(runs)==5
    assert all('--read-only' in args and not any(x in args for x in ('-p','--publish','-v','--mount','--privileged')) for args,_ in runs)
    assert [a[a.index('--network')+1] for a,_ in runs]==['none','none','none','none','container:py3-bend2-seh-bound-unit']
    assert all('PRIVATE KEY' not in str(args) for args,_ in runs)
    assert sum('PRIVATE KEY' in kw.get('input','') for _,kw in runs)==2
    assert 'PRIVATE KEY' not in str(report)


def test_generated_process_argv_uses_linear_list():
    """Process.run expects the Base List<String> spelling (multiplicity &1)."""
    src = lower.lower_bound_listener(RELAY, AUTH)
    assert 'def LabCrypto.call(args: List<&1, String>, input: String)' in src
    assert 'def LabCrypto.call(args: List<&2, String>' not in src
    assert 'Process.run("/usr/local/bin/py3-native-crypto", args, input, 4096, 2000)' in src
    assert 'LabCrypto.call(["nonce","32"],"")' in src
    assert 'LabCrypto.call(["verify",key,signature],statement)' in src
    assert 'LabCrypto.call(["validate",key],"")' in src


def test_generated_route_path_explicitly_allows_reuse():
    src = lower.lower_bound_listener(RELAY, AUTH)
    assert 'def LabBound.ws_route(s: Socket,+path: String,key: String,state: Chan(Bool))' in src
    assert 'LabBound.ws_primary(String.eq(path,"/v1/seh/slh"),s,path,key,state)' in src
