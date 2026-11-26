"""Focused authenticated primary-tunnel Docker gate; full relay stays blocked."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
from .bound_lowering import lower_bound_listener
from .seh_listener_docker import compare_observations

PORT=18081
RECIPE=r'''ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS compiler
RUN apt-get update && apt-get install -y --no-install-recommends libssl-dev && rm -rf /var/lib/apt/lists/*
COPY native_crypto.c /tmp/native_crypto.c
RUN cc -O2 -Wall -Wextra -Werror /tmp/native_crypto.c -lcrypto -o /tmp/py3-native-crypto
COPY service.bend /tmp/service.bend
RUN bend /tmp/service.bend -o /tmp/seh-native && \
    test -x /tmp/seh-native && \
    test "$(od -An -tx1 -N4 /tmp/seh-native | tr -d ' \n')" = 7f454c46
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 && rm -rf /var/lib/apt/lists/*
COPY --from=compiler /tmp/seh-native /usr/local/bin/seh-native
COPY --from=compiler /tmp/py3-native-crypto /usr/local/bin/py3-native-crypto
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/seh-native"]
'''


def disposable_identity():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key=ec.generate_private_key(ec.SECP256R1())
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode('ascii')
    public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)).decode('ascii')
    return private,public


def run_bound(docker,root,work,native,report):
    report['bound_scope']='PRIMARY_AUTHENTICATION_ONLY_NO_CLIENT_RELAY'
    report['bound_gate']='SOURCE_LOWERING_FAILED'
    report['bound_relay_gate']='BLOCKED_NOT_TRANSLATED'
    directory=Path(__file__).parent
    code=lower_bound_listener((root/'main_computer/seh/relay.py').read_text(encoding='utf-8'),
                              (root/'main_computer/slh/authenticator.py').read_text(encoding='utf-8'),port=PORT)
    report['bound_generated_sha256']=hashlib.sha256(code.encode()).hexdigest()
    context=work/'bound'; context.mkdir()
    (context/'service.bend').write_text(code,encoding='utf-8')
    (context/'native_crypto.c').write_bytes((directory/'native_crypto.c').read_bytes())
    (context/'Dockerfile').write_text(RECIPE,encoding='utf-8')
    base=docker.call('inspect','--format','{{.Config.Image}}',native).stdout.strip()
    if not base: raise RuntimeError('missing native toolchain image')
    report['bound_gate']='NATIVE_BUILD_FAILED'
    image=docker.build(context,'bound',extra=('--build-arg',f'BASE_IMAGE={base}'))
    reference=f'py3-bend2-seh-reference:{docker.token}'
    private,public=disposable_identity()
    probe=(directory/'seh_bound_probe.py').read_text(encoding='utf-8')
    def script(mode):
        # Private fixture is delivered only to the Python oracle/probe via
        # stdin. It never appears in command arguments, reports or image layers.
        return probe+'\nprint(json.dumps(run('+repr(mode)+','+str(PORT)+','+repr(private)+'),sort_keys=True))\n'
    report['bound_gate']='REFERENCE_FAILED'
    name=f'py3-bend2-seh-bound-oracle-{docker.token}'
    result=docker.call(*docker.run_args(name),'-i','--pull=never',reference,'python','-',input=script('reference'),timeout=150)
    expected=json.loads(result.stdout)
    if len(expected)!=22: raise RuntimeError(f'wrong bound reference inventory: {len(expected)}')
    report['bound_gate']='CONFIG_GUARD_FAILED'
    for label,opts in [('missing',()),('invalid',('-e','SEH_SLH_PUBLIC_KEY_B64=disposable-invalid'))]:
        name=f'py3-bend2-seh-bound-{label}-{docker.token}'
        guard=docker.call(*docker.run_args(name),'--pull=never',*opts,image,check=False,timeout=30)
        expected_error='bound public key required' if label=='missing' else 'invalid bound public key'
        if guard.returncode!=1 or expected_error not in guard.stderr:
            raise RuntimeError(f'bound {label} guard did not fail closed: {guard.returncode} {guard.stderr[-500:]}')
    report['bound_config_guard']='PASS'
    report['bound_gate']='START_FAILED'
    name=f'py3-bend2-seh-bound-{docker.token}'
    docker.call(*docker.run_args(name),'-d','--pull=never','-e','SEH_SLH_PUBLIC_KEY_B64='+public,image)
    exe=docker.call('exec',name,'readlink','/proc/1/exe').stdout.strip()
    if exe!='/usr/local/bin/seh-native': raise RuntimeError(f'unexpected bound process: {exe}')
    report['bound_process']=exe
    report['bound_gate']='PROBE_FAILED'
    client=f'py3-bend2-seh-bound-probe-{docker.token}'
    args=docker.run_args(client); args[args.index('--network')+1]=f'container:{name}'
    result=docker.call(*args,'-i','--pull=never',reference,'python','-',input=script('candidate'),timeout=150,check=False)
    if result.returncode:
        raise RuntimeError(f'bound native probe exit {result.returncode}: {result.stderr[-3000:]}')
    actual=json.loads(result.stdout)
    report['bound_cases']=compare_observations(expected,actual)
    for row in report['bound_cases']:
        print(f"SEH_BOUND {row['name']} {row['status']}",flush=True)
    report['bound_gate']='PASS' if all(r['status']=='VERIFIED' for r in report['bound_cases']) else 'DIFFERENTIAL_FAILED'
    if report['bound_gate']=='PASS': report['bound_relay_guard']='PASS'
