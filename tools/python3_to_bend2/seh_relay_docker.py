"""Focused restricted primary relay acceptance. Never authorizes deployment."""
import hashlib
import json
from pathlib import Path
from .relay_lowering import lower_relay
from .seh_bound_docker import PORT, RECIPE, disposable_identity
from .seh_listener_docker import compare_observations


def run_relay(docker, root, work, native, report):
    report['relay_scope']='PRIMARY_ONLY_LOOPBACK_4096_BYTE_FRAMES_16_STREAMS'
    report['bound_relay_gate']='SOURCE_LOWERING_FAILED'
    directory=Path(__file__).parent
    code=lower_relay(*[(root/p).read_text(encoding='utf-8') for p in ('main_computer/seh/relay.py','main_computer/slh/authenticator.py','main_computer/seh/stream_writer.py')],port=PORT)
    report['relay_generated_sha256']=hashlib.sha256(code.encode()).hexdigest()
    context=work/'relay'; context.mkdir()
    (context/'service.bend').write_text(code,encoding='utf-8')
    (context/'native_crypto.c').write_bytes((directory/'native_crypto.c').read_bytes())
    (context/'Dockerfile').write_text(RECIPE,encoding='utf-8')
    base=docker.call('inspect','--format','{{.Config.Image}}',native).stdout.strip()
    if not base: raise RuntimeError('missing native toolchain image')
    report['bound_relay_gate']='NATIVE_BUILD_FAILED'
    image=docker.build(context,'relay',extra=('--build-arg',f'BASE_IMAGE={base}'))
    reference=f'py3-bend2-seh-reference:{docker.token}'
    private,public=disposable_identity()
    probe='\n'.join((directory/p).read_text(encoding='utf-8') for p in ('seh_bound_probe.py','seh_relay_probe.py'))
    def script(mode):
        return probe+'\nprint(json.dumps(run_relay('+repr(mode)+','+str(PORT)+','+repr(private)+'),sort_keys=True))\n'
    report['bound_relay_gate']='REFERENCE_FAILED'
    name=f'py3-bend2-seh-relay-oracle-{docker.token}'
    result=docker.call(*docker.run_args(name),'-i','--pull=never',reference,'python','-',input=script('reference'),timeout=180)
    expected=json.loads(result.stdout)
    if len(expected)!=40: raise RuntimeError(f'wrong relay reference inventory: {len(expected)}')
    report['relay_reference_count']=len(expected)
    report['bound_relay_gate']='CONFIG_GUARD_FAILED'
    for label,opts in [('missing',()),('invalid',('-e','SEH_SLH_PUBLIC_KEY_B64=disposable-invalid'))]:
        guard=docker.call(*docker.run_args(f'py3-bend2-seh-relay-{label}-{docker.token}'),'--pull=never',*opts,image,check=False,timeout=30)
        if guard.returncode!=1 or ('bound public key required' if label=='missing' else 'invalid bound public key') not in guard.stderr:
            raise RuntimeError(f'relay {label} guard failed')
    report['relay_config_guard']='PASS'
    report['bound_relay_gate']='START_FAILED'
    name=f'py3-bend2-seh-relay-{docker.token}'
    docker.call(*docker.run_args(name),'-d','--pull=never','-e','SEH_SLH_PUBLIC_KEY_B64='+public,image)
    exe=docker.call('exec',name,'readlink','/proc/1/exe').stdout.strip()
    if exe!='/usr/local/bin/seh-native': raise RuntimeError(f'unexpected relay process: {exe}')
    report['relay_process']=exe
    report['bound_relay_gate']='PROBE_FAILED'
    args=docker.run_args(f'py3-bend2-seh-relay-probe-{docker.token}'); args[args.index('--network')+1]=f'container:{name}'
    result=docker.call(*args,'-i','--pull=never',reference,'python','-',input=script('candidate'),timeout=180,check=False)
    if result.returncode: raise RuntimeError(f'relay native probe exit {result.returncode}: {result.stderr[-3000:]}')
    report['relay_cases']=compare_observations(expected,json.loads(result.stdout))
    for row in report['relay_cases']: print(f"SEH_RELAY {row['name']} {row['status']}",flush=True)
    report['bound_relay_gate']='PASS' if all(r['status']=='VERIFIED' for r in report['relay_cases']) else 'DIFFERENTIAL_FAILED'
