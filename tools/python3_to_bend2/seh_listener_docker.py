"""Native, source-derived unbound listener gate. Full service stays blocked."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .listener_lowering import lower_unbound_listener

PORT = 18080
SOURCE = 'main_computer/seh/relay.py'
RECIPE = '''ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS compiler
COPY service.bend /tmp/service.bend
RUN bend /tmp/service.bend -o /tmp/seh-native && \\
    test -x /tmp/seh-native && \\
    test "$(od -An -tx1 -N4 /tmp/seh-native | tr -d ' \\n')" = 7f454c46
FROM ubuntu:24.04
COPY --from=compiler /tmp/seh-native /usr/local/bin/seh-native
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/seh-native"]
'''


def compare_observations(expected, actual):
    return [{'name':k,'status':'VERIFIED' if k in expected and k in actual and expected[k]==actual[k] else 'DIFFERENTIAL_FAILED',
             'expected':expected.get(k),'observed':actual.get(k)} for k in sorted(set(expected)|set(actual))]


def run_listener(docker, root, work, native, report):
    report['listener_gate'] = 'SOURCE_LOWERING_FAILED'
    source = (root / SOURCE).read_text(encoding='utf-8')
    generated = lower_unbound_listener(source, port=PORT)
    report['listener_generated_sha256'] = hashlib.sha256(generated.encode()).hexdigest()
    report['listener_scope'] = 'UNBOUND_ONLY_LOOPBACK_HTTP_WEBSOCKET_REFUSAL'
    context = work / 'listener'
    context.mkdir()
    (context / 'service.bend').write_text(generated,encoding='utf-8')
    (context / 'Dockerfile').write_text(RECIPE,encoding='utf-8')
    # Use exactly the version-checked toolchain (including its existing cache).
    base = docker.call('inspect','--format','{{.Config.Image}}',native).stdout.strip()
    if not base:
        raise RuntimeError('missing native toolchain image')
    report['listener_gate'] = 'NATIVE_BUILD_FAILED'
    candidate = docker.build(context,'listener',extra=('--build-arg',f'BASE_IMAGE={base}'))
    probe = Path(__file__).with_name('seh_listener_probe.py').read_text(encoding='utf-8')
    reference = f'py3-bend2-seh-reference:{docker.token}'
    report['listener_gate'] = 'REFERENCE_FAILED'
    oracle_name = f'py3-bend2-seh-listener-oracle-{docker.token}'
    original = docker.call(*docker.run_args(oracle_name),'-i','--pull=never',reference,'python','-','reference',str(PORT),input=probe,timeout=120)
    expected = json.loads(original.stdout)
    if len(expected) != 10:
        raise RuntimeError(f'wrong independent listener inventory: {len(expected)}')
    report['listener_gate'] = 'BOUND_CONFIG_GUARD_FAILED'
    bad = f'py3-bend2-seh-listener-bound-{docker.token}'
    guard = docker.call(*docker.run_args(bad),'--pull=never','-e','SEH_SLH_PUBLIC_KEY_B64=disposable-invalid',candidate,check=False,timeout=30)
    if guard.returncode != 1 or 'bound service is not translated' not in guard.stderr:
        raise RuntimeError(f'bound configuration did not fail closed: {guard.returncode} {guard.stderr[-500:]}')
    report['listener_bound_guard'] = 'PASS'
    report['listener_gate'] = 'START_FAILED'
    name = f'py3-bend2-seh-listener-{docker.token}'
    docker.call(*docker.run_args(name),'-d','--pull=never',candidate)
    # No shell, Python process or proxy is the candidate entry point.
    identity = docker.call('exec',name,'readlink','/proc/1/exe').stdout.strip()
    if identity != '/usr/local/bin/seh-native':
        raise RuntimeError(f'unexpected listener process: {identity}')
    report['listener_process'] = identity
    report['listener_gate'] = 'PROBE_FAILED'
    # Only the probe joins the candidate's isolated network namespace. No
    # published ports, host mounts, bridge network or external connectivity.
    client = f'py3-bend2-seh-listener-client-{docker.token}'
    args = docker.run_args(client)
    args[args.index('--network')+1] = f'container:{name}'
    observed = docker.call(*args,'-i','--pull=never',reference,'python','-','candidate',str(PORT),input=probe,timeout=120,check=False)
    if observed.returncode:
        logs = docker.call('logs',name,check=False).stdout[-1000:]
        raise RuntimeError(f'native listener probe exit {observed.returncode}: {observed.stderr[-2000:]} {logs}')
    actual = json.loads(observed.stdout)
    report['listener_cases'] = compare_observations(expected,actual)
    for row in report['listener_cases']:
        print(f"SEH_LISTENER {row['name']} {row['status']}",flush=True)
    report['listener_gate'] = 'PASS' if all(row['status']=='VERIFIED' for row in report['listener_cases']) else 'DIFFERENTIAL_FAILED'
