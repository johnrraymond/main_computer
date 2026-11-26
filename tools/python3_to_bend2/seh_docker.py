"""Isolated SEH translation lab, not a deployment or full-service certificate.

Only disposable Docker resources and generated reports are written. The real
Python image is the reference; the candidate executes compiler-generated
native binaries. Live wire samples are specialized as inputs, not as results.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from . import translate
from .coolify import _ERROR_MARKER, audit_modules, compare_export, discover, export_oracle
from .library_exports import EXPORTS, Fixture, programs
from .seh_listener_docker import run_listener
from .seh_bound_docker import run_bound
from .seh_relay_docker import run_relay

ROOT = Path(__file__).resolve().parents[2]
ENCODER = 'seh_frame_json'
DECODER = 'seh_frame_json_decode'
DOCKERFILE = 'deploy/seh/Dockerfile'
TOOLCHAIN = '''FROM ubuntu:24.04
ARG BEND_VERSION=2.0.40
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl clang build-essential git xz-utils unzip && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://bend-lang.com/install.sh -o /tmp/install-bend.sh && \
    sh /tmp/install-bend.sh && rm /tmp/install-bend.sh
ENV PATH="/root/.bend/bin:${PATH}"
RUN bend version 2>&1 | grep -F "$BEND_VERSION" && clang --version
CMD ["sleep", "infinity"]
'''
# BendHub libraries are fetched and content-hash checked by Bend itself during
# the build. The runtime image retains the compiler's cache; neither the
# compiler nor generated programs may fetch dependencies during acceptance.
CACHE_LAYER = r'''COPY dependency-probe.bend /tmp/seh-dependency-probe.bend
RUN bend /tmp/seh-dependency-probe.bend -o /tmp/seh-dependency-probe-native && \
    test -x /tmp/seh-dependency-probe-native && \
    rm /tmp/seh-dependency-probe.bend /tmp/seh-dependency-probe-native
'''
EXTERNAL_TOOLCHAIN = "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"

# The compile phase and execution phase have disjoint failures. Never classify
# a compiler failure as a successfully rejected malformed JSON message.
NATIVE_SCRIPT = r'''set -eu
work=$(mktemp -d /tmp/seh-bend.XXXXXX)
trap 'rm -rf "$work"' EXIT
cd "$work"
cat > case.bend
if ! bend "$work/case.bend" -o "$work/case-native" >compile.log 2>&1; then
  cat compile.log >&2; exit 70
fi
if [ ! -x "$work/case-native" ] || [ "$(od -An -tx1 -N4 "$work/case-native" | tr -d ' \n')" != 7f454c46 ]; then
  echo NATIVE_ELF_NOT_GENERATED >&2; exit 71
fi
set +e
"$work/case-native"
rc=$?
set -e
exit "$rc"
'''


def hashes(root, paths):
    result = {}
    for rel in sorted(set(paths)):
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'unsafe source path: {rel}')
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return result


def audit(root, service=None):
    service = service or next(s for s in discover(root) if s.name == 'seh')
    modules = [{'path': path, 'status': status, 'detail': detail}
               for path, status, detail in audit_modules(root, (service,))]
    return service, {'modules': modules, 'missing': list(service.missing),
                     'external': list(service.external), 'dynamic_import_sites': list(service.dynamic_import_sites),
                     'service_certification': 'NOT_PERFORMED',
                     'service_blocker': 'Whole-module emission is not a linked native service; transport, effects, crypto and shared state require translation.'}


def stage_reference(root, service, context):
    # Never send the working tree, .env, runtime, browser profile, or credentials
    # to Docker. Only the inspected source closure and the real Dockerfile.
    if service.missing:
        raise RuntimeError(f'incomplete SEH reference closure: {service.missing}')
    for rel in sorted(set(service.modules) | {DOCKERFILE}):
        src = root / rel
        if src.is_symlink() or not src.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'unsafe reference path: {rel}')
        dst = context / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


class Docker:
    def __init__(self, executable, token, timeout):
        self.executable, self.token, self.timeout = executable, token, timeout
        self.containers, self.images = [], []

    def call(self, *args, input=None, timeout=None, check=True):
        result = subprocess.run([self.executable, *map(str, args)], input=input,
                                capture_output=True, encoding='utf-8', errors='strict',
                                timeout=timeout or self.timeout)
        if check and result.returncode:
            raise RuntimeError(f'Docker {args[0]} exit={result.returncode}: {result.stderr[-4000:]} {result.stdout[-1000:]}')
        return result

    def build(self, context, name, dockerfile=None, extra=()):
        image = f'py3-bend2-seh-{name}:{self.token}'
        self.images.append(image)
        args = ['build', '--label', f'py3-bend2.seh.lab={self.token}', '-t', image]
        if dockerfile:
            args += ['-f', str(dockerfile)]
        self.call(*args, *extra, context, timeout=1800)
        return image

    def run_args(self, name, *, tmp_size='256m'):
        self.containers.append(name)
        return ['run', '--name', name, '--label', f'py3-bend2.seh.lab={self.token}',
                '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--pids-limit', '256',
                '--tmpfs', f'/tmp:rw,exec,nosuid,nodev,size={tmp_size}']

    def cleanup(self):
        errors = []
        for name in reversed(self.containers):
            try:
                r = self.call('rm', '-f', name, check=False, timeout=30)
                if r.returncode and 'No such container' not in r.stderr:
                    errors.append(r.stderr[-500:])
            except Exception as exc:
                errors.append(str(exc))
        for image in reversed(self.images):
            try:
                r = self.call('image', 'rm', image, check=False, timeout=30)
                if r.returncode and 'No such image' not in r.stderr:
                    errors.append(r.stderr[-500:])
            except Exception as exc:
                errors.append(str(exc))
        return errors


def reference_run(docker, root, service, work):
    context = work / 'reference'
    context.mkdir()
    stage_reference(root, service, context)
    image = docker.build(context, 'reference', context / DOCKERFILE)
    script = (Path(__file__).with_name('seh_docker_reference.py')).read_text(encoding='utf-8')
    name = f'py3-bend2-seh-reference-{docker.token}'
    result = docker.call(*docker.run_args(name), '-i', image, 'python', '-', input=script, timeout=120)
    evidence = json.loads(result.stdout)
    if not evidence['cases'] or not evidence['frames']:
        raise RuntimeError('empty reference evidence')
    return evidence


def decoder_template(decoder):
    return next((f for f in decoder.fixtures if not f.error and len(f.args) == 1
                 and type(f.args[0]) is str and not f.kwargs), None) if decoder else None


def dependency_probe(root):
    """Use the installed translator's reviewed valid fixture, never a guessed
    package hash or a Python substitute. Bend resolves the transitive imports.
    """
    decoder = EXPORTS.get(DECODER)
    if decoder is None:
        return None
    fixture = decoder_template(decoder)
    if fixture is None:
        raise RuntimeError('installed decoder has no compatible valid dependency probe')
    _, lowered = programs(root, decoder, fixture)
    return translate(lowered, 'seh_dependency_probe', show_return=True)


def toolchain_run(docker, work, image, version, *, root=ROOT, report=None):
    if report is None:
        report = {}
    report['dependency_gate'] = 'PROBE_GENERATION_FAILED'
    probe = dependency_probe(root)
    report['dependency_gate'] = 'NOT_RUN'
    context = work / 'toolchain'
    context.mkdir()
    if image:
        docker.call('image', 'inspect', image)
        recipe = EXTERNAL_TOOLCHAIN
        extra = ('--build-arg', f'BASE_IMAGE={image}')
    else:
        recipe = TOOLCHAIN
        extra = ('--build-arg', f'BEND_VERSION={version}')
    if probe is not None:
        (context / 'dependency-probe.bend').write_text(probe, encoding='utf-8')
        recipe += CACHE_LAYER
        report['dependency_gate'] = 'BUILD_FAILED'
        print('SEH_DEPENDENCIES provisioning compiler cache during Docker build', flush=True)
    # Always derive a disposable image for the probe, including when supplied
    # an existing toolchain. Do not mutate the user's image or mount its cache.
    if not image or probe is not None:
        (context / 'Dockerfile').write_text(recipe, encoding='utf-8')
        image = docker.build(context, 'toolchain', extra=extra)
    if probe is not None:
        report['dependency_gate'] = 'OFFLINE_PREFLIGHT_FAILED'
    name = f'py3-bend2-seh-native-{docker.token}'
    docker.call(*docker.run_args(name, tmp_size='1024m'), '-d', '--pull=never', image, 'sleep', 'infinity')
    version_result = docker.call('exec', name, 'bend', 'version')
    actual = (version_result.stdout + '\n' + version_result.stderr).strip()
    if not re.search(r'(?<![\d.])' + re.escape(version) + r'(?![\d.])', actual):
        raise RuntimeError(f'Bend version mismatch: expected {version}, observed {actual}')
    docker.call('exec', name, 'clang', '--version')
    if probe is not None:
        observed = docker.call('exec', '-i', name, 'sh', '-c', NATIVE_SCRIPT,
                               input=probe, check=False)
        if observed.returncode:
            raise RuntimeError(f'offline decoder dependency probe exit={observed.returncode}: '
                               f'{observed.stderr[-4000:]} {observed.stdout[-1000:]}')
        report['dependency_gate'] = 'PASS'
        print('SEH_DEPENDENCIES PASS (offline native decoder probe)', flush=True)
    else:
        report['dependency_gate'] = 'BLOCKED_DECODER_NOT_INSTALLED'
    return name, actual


def verified_decoder_rejection(export, fixture, oracle, observed):
    """The decoder promises rejection parity, not Python exception text parity.

    Require the reviewed decoder export, an expected JSON rejection confirmed
    by the independent Python oracle, and the exact native rejection contract.
    Compilation failures, crashes and unrelated runtime errors are not passes.
    """
    if export is not EXPORTS.get(DECODER) or fixture.error not in ('JSONDecodeError', 'ValueError'):
        return False
    if oracle.returncode != 1 or not oracle.stderr.startswith(_ERROR_MARKER):
        return False
    diagnostic = oracle.stderr.removeprefix(_ERROR_MARKER)
    error_prefixes = (fixture.error + ':',)
    if fixture.error == 'JSONDecodeError':
        error_prefixes += ('json.decoder.JSONDecodeError:',)
    return (diagnostic.startswith(error_prefixes) and
            observed.returncode == 1 and oracle.stdout == observed.stdout == '' and
            observed.stderr.removesuffix('\n') == 'ValueError: JSON input rejected by native decoder')


def native_case(root, folder, docker, name, export, fixture, label):
    original, lowered = programs(root, export, fixture)
    emitted = translate(lowered, label, show_return=True)
    # The oracle never sees the generated Bend program, and the compiler
    # never sees the oracle result. Only the same input reaches both.
    path = folder / 'oracle.py'
    path.write_text(original, encoding='utf-8')
    oracle = export_oracle(path, fixture.error)
    observed = docker.call('exec', '-i', name, 'sh', '-c', NATIVE_SCRIPT,
                           input=emitted, check=False)
    rejection_verified = verified_decoder_rejection(export, fixture, oracle, observed)
    verdict = ('NATIVE_BUILD_FAILED' if observed.returncode in (70, 71)
               else 'NATIVE_EXECUTION_FAILED' if fixture.error and observed.returncode != 1
               else 'VERIFIED_REJECTION' if rejection_verified
               else compare_export(oracle, observed, fixture.error))
    return {'name': label, 'status': verdict, 'native_exit': observed.returncode,
            'detail': 'Invalid JSON rejected; diagnostic parity not claimed' if rejection_verified else
            '' if verdict in ('VERIFIED', 'VERIFIED_REJECTION') else
            (observed.stderr[-2000:] or f'expected {oracle.stdout[:500]!r}; got {observed.stdout[:500]!r}')}, observed


def codec_run(root, work, docker, name, reference):
    folder = work / 'oracles'
    folder.mkdir()
    results = []
    decoder = EXPORTS.get(DECODER)
    # The decoder was added after the supplied snapshot. Use the current
    # installed reviewed export, never overwrite it with a stale implementation.
    template = decoder_template(decoder)

    def execute(export, fixture, label):
        try:
            row, observed = native_case(root, folder, docker, name, export, fixture, label)
        except Exception as exc:
            row, observed = {'name': label, 'status': 'EXECUTION_ERROR', 'detail': str(exc)}, None
        results.append(row)
        print(f"SEH_CODEC {label} {row['status']}", flush=True)
        return row, observed

    encoder = EXPORTS[ENCODER]
    for i, fixture in enumerate(encoder.fixtures):
        execute(encoder, fixture, f'encoder_fixture_{i}')
    if decoder:
        for i, fixture in enumerate(decoder.fixtures):
            execute(decoder, fixture, f'decoder_fixture_{i}')
    else:
        results.append({'name': 'decoder_inventory', 'status': 'BLOCKED', 'detail': f'{DECODER} not installed'})
    for sample in reference['frames']:
        label, wire = sample['name'], sample['wire']
        value = json.loads(wire)
        row, observed = execute(encoder, Fixture((value,)), f'{label}:python_to_bend_encode')
        if row['status'] != 'VERIFIED':
            continue
        # Output must match the real frame bytes, including the oracle's
        # print newline. Python also independently decodes the Bend output.
        if observed.stdout != wire + '\n' or json.loads(observed.stdout) != value:
            row['status'] = 'WIRE_DIFFERENTIAL_FAILED'
            print(f'SEH_WIRE {label} WIRE_DIFFERENTIAL_FAILED', flush=True)
            continue
        row['wire_bytes_equal'] = True
        row['python_decodes_bend'] = True
        if template is None:
            results.append({'name': f'{label}:decode', 'status': 'BLOCKED', 'detail': 'no compatible reviewed decoder fixture'})
            continue
        # Two separately generated native runs. The second consumes the actual
        # Bend encoder output, not a prerecorded expected decoder result.
        for direction, incoming in [('python_to_bend_decode', wire),
                                    ('bend_to_bend_decode', observed.stdout.removesuffix('\n'))]:
            execute(decoder, replace(template, args=(incoming,)), f'{label}:{direction}')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('audit', 'reference', 'listener', 'bound', 'relay', 'run'), nargs='?', default='run')
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--docker', default='docker')
    parser.add_argument('--toolchain-image', help='Optional existing local image with Bend and clang; version checked')
    parser.add_argument('--bend-version', default='2.0.40')
    parser.add_argument('--timeout', type=int, default=180, help='Per native fixture timeout, seconds')
    args = parser.parse_args(argv)
    if args.timeout < 1 or not re.fullmatch(r'\d+\.\d+\.\d+', args.bend_version):
        parser.error('positive timeout and x.y.z Bend version required')
    root = args.repo.resolve()
    if args.stage != 'audit' and root != ROOT:
        parser.error('run inside the repository being translated; --repo is audit-only for other trees')
    token = uuid.uuid4().hex[:16]
    docker = Docker(args.docker, token, args.timeout)
    report = {'schema': 'py3-bend2.seh.docker.v1', 'run': token, 'stage': args.stage,
              'service_gate': 'BLOCKED_NOT_LINKED', 'reference_gate': 'NOT_RUN',
              'codec_gate': 'NOT_RUN', 'listener_gate': 'NOT_RUN', 'bound_gate': 'NOT_RUN', 'bound_relay_gate': 'NOT_RUN', 'dependency_gate': 'NOT_RUN', 'source_integrity': 'NOT_CHECKED'}
    before = None
    try:
        service = next(s for s in discover(root) if s.name == 'seh')
        protected = set(service.required) | set(service.modules) | {'tools/seh_control/git_deployment.py'}
        protected |= {p.relative_to(root).as_posix() for p in (root / 'tools/python3_to_bend2').iterdir() if p.is_file() and p.suffix in ('.py', '.bend', '.c')}
        before = hashes(root, protected)
        report['source_sha256'] = before
        _, report['audit'] = audit(root, service)
        for row in report['audit']['modules']:
            print(f"SEH_MODULE {row['status']} {row['path']}: {row['detail']}", flush=True)
        if args.stage != 'audit':
            docker.call('info', '--format', '{{.ServerVersion}}', timeout=30)
            with tempfile.TemporaryDirectory(prefix='seh-bend-docker-') as tmp:
                work = Path(tmp)
                report['reference_gate'] = 'FAIL'
                reference = reference_run(docker, root, service, work)
                report['reference_cases'] = reference['cases']
                report['reference_wire_count'] = len(reference['frames'])
                report['reference_gate'] = 'PASS'
                print(f"SEH_REFERENCE_DOCKER PASS cases={len(reference['cases'])} frames={len(reference['frames'])}", flush=True)
                if args.stage in ('run', 'listener', 'bound', 'relay'):
                    native, report['bend_version'] = toolchain_run(
                        docker, work, args.toolchain_image, args.bend_version, root=root, report=report)
                if args.stage == 'run':
                    report['codec_gate'] = 'FAIL'
                    report['codec_cases'] = codec_run(root, work, docker, native, reference)
                    totals = Counter(row['status'] for row in report['codec_cases'])
                    report['codec_totals'] = dict(totals)
                    report['codec_gate'] = ('PASS' if totals and set(totals) <= {'VERIFIED', 'VERIFIED_REJECTION'} else
                                            'BLOCKED' if set(totals) <= {'VERIFIED', 'VERIFIED_REJECTION', 'BLOCKED'} else 'FAIL')
                if args.stage in ('run', 'listener'):
                    run_listener(docker, root, work, native, report)
                if args.stage in ('run', 'bound'):
                    run_bound(docker, root, work, native, report)
                if args.stage in ('run', 'relay'):
                    run_relay(docker, root, work, native, report)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(f"SEH_LAB_ERROR {report['error']}", flush=True)
    finally:
        report['cleanup_errors'] = docker.cleanup()
        if before is not None:
            try:
                report['source_integrity'] = 'PASS' if hashes(root, protected) == before else 'FAIL'
            except Exception as exc:
                report['source_integrity'] = 'FAIL'
                report['integrity_error'] = str(exc)
        output = root / 'runtime' / 'reports' / 'python3_to_bend2_seh_docker' / token / 'report.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(f"SEH_DEPENDENCY_GATE {report['dependency_gate']}")
        print(f"SEH_CODEC_DOCKER {report['codec_gate']}")
        print(f"SEH_LISTENER_GATE {report['listener_gate']} (unbound laboratory slice only)")
        print(f"SEH_BOUND_GATE {report['bound_gate']} (primary authentication laboratory only)")
        print(f"SEH_RELAY_GATE {report['bound_relay_gate']}")
        print(f"SEH_SOURCE_INTEGRITY {report['source_integrity']}")
        print(f"SEH_SERVICE_GATE {report['service_gate']} (not a native SEH server)")
        print(f'SEH_LAB_REPORT {output}')
    ok = not report.get('error') and not report['cleanup_errors'] and report['source_integrity'] == 'PASS'
    if args.stage != 'audit':
        ok &= report['reference_gate'] == 'PASS'
    if args.stage == 'run':
        ok &= report['codec_gate'] == 'PASS'
    if args.stage in ('run', 'listener'):
        ok &= report['listener_gate'] == 'PASS'
    if args.stage in ('run', 'bound'):
        ok &= report['bound_gate'] == 'PASS'
    if args.stage in ('run', 'relay'):
        ok &= report['bound_relay_gate'] == 'PASS'
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
