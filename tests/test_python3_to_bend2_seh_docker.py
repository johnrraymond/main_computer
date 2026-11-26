"""Host-only regressions for the SEH native Docker acceptance lab."""
from pathlib import Path
import json
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from tools.python3_to_bend2 import seh_docker as lab
from tools.python3_to_bend2.library_exports import Fixture

ROOT = Path(__file__).resolve().parents[1]


def test_reference_context_is_exact_and_secret_free():
    service, audit = lab.audit(ROOT)
    with TemporaryDirectory() as directory:
        context = Path(directory)
        lab.stage_reference(ROOT, service, context)
        actual = {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()}
        assert actual == set(service.modules) | {lab.DOCKERFILE}
        for rel in actual:
            assert (context / rel).read_bytes() == (ROOT / rel).read_bytes()
    assert audit['service_certification'] == 'NOT_PERFORMED'


def test_docker_runtime_has_no_host_access():
    docker = lab.Docker('docker', 'unit-test', 5)
    args = docker.run_args('isolated')
    assert args[args.index('--network') + 1] == 'none'
    assert '--read-only' in args and '--cap-drop' in args
    assert not any(a in args for a in ('-v', '--volume', '--mount', '-p', '--publish', '--privileged'))
    assert docker.containers == ['isolated']


def test_native_case_compiler_error_never_counts_as_rejection():
    failure = subprocess.CompletedProcess([], 70, '', 'compile failed')
    fake = SimpleNamespace(call=lambda *a, **k: failure)
    with TemporaryDirectory() as directory, patch.object(lab, 'programs', return_value=('original', 'input')), \
            patch.object(lab, 'translate', return_value='generated'), \
            patch.object(lab, 'export_oracle', return_value=subprocess.CompletedProcess([], 1, '', 'bad')), \
            patch.object(lab, 'compare_export', return_value='VERIFIED_REJECTION') as compare:
        result, _ = lab.native_case(ROOT, Path(directory), fake, 'native', object(), Fixture(error='ValueError'), 'bad')
        assert result['status'] == 'NATIVE_BUILD_FAILED'
        compare.assert_not_called()


def test_wire_round_trip_feeds_real_bend_output_to_second_decoder():
    encoder = SimpleNamespace(fixtures=())
    decoder = SimpleNamespace(fixtures=(Fixture(('{}',)),))
    wire = '{"op":"close","id":"x"}'
    # Real SEH canonical order, not arbitrary insertion order.
    wire = json.dumps(json.loads(wire), sort_keys=True, separators=(',', ':'))
    captures = []

    def fake(root, folder, docker, name, export, fixture, label):
        captures.append((export, fixture, label))
        out = json.dumps(fixture.args[0], sort_keys=True, separators=(',', ':')) + '\n' if export is encoder else 'decoded\n'
        return {'name': label, 'status': 'VERIFIED'}, subprocess.CompletedProcess([], 0, out, '')

    with TemporaryDirectory() as directory, patch.object(lab, 'EXPORTS', {lab.ENCODER: encoder, lab.DECODER: decoder}), \
            patch.object(lab, 'native_case', side_effect=fake):
        results = lab.codec_run(ROOT, Path(directory), None, 'native', {'frames': [{'name': 'live', 'wire': wire}]})
    assert all(r['status'] == 'VERIFIED' for r in results)
    assert next(r for r in results if r['name'] == 'live:python_to_bend_encode')['python_decodes_bend']
    assert captures[-1][1].args == (wire,)
    assert captures[-1][2] == 'live:bend_to_bend_decode'


def test_missing_decoder_is_blocked_not_passed():
    encoder = SimpleNamespace(fixtures=())
    with TemporaryDirectory() as directory, patch.object(lab, 'EXPORTS', {lab.ENCODER: encoder}):
        results = lab.codec_run(ROOT, Path(directory), None, 'native', {'frames': []})
    assert results == [{'name': 'decoder_inventory', 'status': 'BLOCKED', 'detail': f'{lab.DECODER} not installed'}]


def test_changed_source_detected():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / 'relay.py'
        path.write_text('before')
        before = lab.hashes(root, ['relay.py'])
        path.write_text('after')
        assert lab.hashes(root, ['relay.py']) != before


def test_reference_black_box_actual_python_service():
    # Do not import or run the service in the pytest process. The reference
    # driver starts disposable loopback-only subprocesses with fresh keys.
    import importlib.util
    import pytest
    if not all(importlib.util.find_spec(n) for n in ('cryptography', 'websockets')):
        pytest.skip('reference dependencies absent')
    from tools.python3_to_bend2.seh_docker_reference import run
    result = run()
    assert len(result['cases']) == 7
    assert len(result['frames']) == 12
    assert 'bidirectional_stream_and_close' in result['cases']


def test_dependency_probe_is_generated_from_installed_valid_decoder():
    invalid = Fixture(('bad',), error='ValueError')
    valid = Fixture(('{}',))
    decoder = SimpleNamespace(fixtures=(invalid, valid))
    with patch.object(lab, 'EXPORTS', {lab.DECODER: decoder}), \
            patch.object(lab, 'programs', return_value=('oracle', 'lowered')) as programs, \
            patch.object(lab, 'translate', return_value='generated dependency probe') as translate:
        assert lab.dependency_probe(ROOT) == 'generated dependency probe'
        programs.assert_called_once_with(ROOT, decoder, valid)
        translate.assert_called_once_with('lowered', 'seh_dependency_probe', show_return=True)


def _fake_toolchain_docker(*, fail_offline=False, fail_build=False):
    docker = lab.Docker('docker', 'unit-test', 5)
    events = []

    def call(*args, **kwargs):
        events.append(('call', args, kwargs))
        if args[:1] == ('exec',) and args[-2:] == ('bend', 'version'):
            return subprocess.CompletedProcess([], 0, 'bend 2.0.40', '')
        if args[-1] == lab.NATIVE_SCRIPT and fail_offline:
            return subprocess.CompletedProcess([], 70, '', 'missing package')
        return subprocess.CompletedProcess([], 0, '', '')

    def build(context, name, **kwargs):
        events.append(('build', (context / 'Dockerfile').read_text(),
                       (context / 'dependency-probe.bend').read_text(), kwargs))
        if fail_build:
            raise RuntimeError('missing package at build')
        return 'disposable-toolchain'

    docker.call, docker.build = call, build
    return docker, events


def test_dependency_provisioning_build_then_isolated_execution():
    for supplied in (None, 'existing:toolchain'):
        with TemporaryDirectory() as directory, patch.object(lab, 'dependency_probe', return_value='generated'):
            docker, events = _fake_toolchain_docker()
            report = {}
            lab.toolchain_run(docker, Path(directory), supplied, '2.0.40', report=report)
        assert report['dependency_gate'] == 'PASS'
        built = next(e for e in events if e[0] == 'build')
        assert 'COPY dependency-probe.bend' in built[1]
        assert 'RUN bend /tmp/seh-dependency-probe.bend' in built[1]
        assert built[2] == 'generated'
        if supplied:
            assert 'FROM ${BASE_IMAGE}' in built[1]
            assert f'BASE_IMAGE={supplied}' in built[3]['extra']
        else:
            assert 'FROM ubuntu:24.04' in built[1]
        run = next(e for e in events if e[0] == 'call' and e[1][0] == 'run')
        assert '--network' in run[1] and run[1][run[1].index('--network')+1] == 'none'
        assert '--read-only' in run[1]
        assert not any(arg in run[1] for arg in ('--mount', '-v', '--volume'))
        offline = events[-1]
        assert offline[1][-1] == lab.NATIVE_SCRIPT and offline[2]['input'] == 'generated'
        assert events.index(built) < events.index(run) < events.index(offline)


def test_dependency_failure_is_reported_once_before_codec_gate():
    import pytest
    for fail_build, fail_offline, expected in ((True, False, 'BUILD_FAILED'),
                                              (False, True, 'OFFLINE_PREFLIGHT_FAILED')):
        with TemporaryDirectory() as directory, patch.object(lab, 'dependency_probe', return_value='generated'):
            docker, events = _fake_toolchain_docker(fail_build=fail_build, fail_offline=fail_offline)
            report = {}
            with pytest.raises(RuntimeError, match='missing package'):
                lab.toolchain_run(docker, Path(directory), None, '2.0.40', report=report)
            assert report['dependency_gate'] == expected
            if fail_build:
                assert not any(e[0] == 'call' and e[1][0] == 'run' for e in events)



def test_decoder_rejection_contract_with_real_python_oracle():
    decoder = object()
    fixture = Fixture(('{',), error='JSONDecodeError')
    observed = subprocess.CompletedProcess([], 1, '', 'ValueError: JSON input rejected by native decoder\n')
    with TemporaryDirectory() as directory, patch.object(lab, 'EXPORTS', {lab.DECODER: decoder}), \
            patch.object(lab, 'programs', return_value=('import json\ndef main(): return json.loads("{")\n', 'lowered')), \
            patch.object(lab, 'translate', return_value='generated'):
        fake = SimpleNamespace(call=lambda *a, **k: observed)
        row, _ = lab.native_case(ROOT, Path(directory), fake, 'native', decoder, fixture, 'bad')
        assert row['status'] == 'VERIFIED_REJECTION'
        assert 'diagnostic parity not claimed' in row['detail']


def test_decoder_rejection_is_narrow_and_fail_closed():
    decoder = object()
    fixture = Fixture(('{',), error='JSONDecodeError')
    valid_oracle = subprocess.CompletedProcess([], 1, '', lab._ERROR_MARKER + 'json.decoder.JSONDecodeError: malformed\n')
    rejection = subprocess.CompletedProcess([], 1, '', 'ValueError: JSON input rejected by native decoder\n')
    with patch.object(lab, 'EXPORTS', {lab.DECODER: decoder}):
        assert lab.verified_decoder_rejection(decoder, fixture, valid_oracle, rejection)
        for wrong in (
            subprocess.CompletedProcess([], 70, '', rejection.stderr),
            subprocess.CompletedProcess([], 71, '', rejection.stderr),
            subprocess.CompletedProcess([], 139, '', rejection.stderr),
            subprocess.CompletedProcess([], 0, '', rejection.stderr),
            subprocess.CompletedProcess([], 1, 'partial result', rejection.stderr),
            subprocess.CompletedProcess([], 1, '', 'ValueError: unrelated runtime failure\n'),
            subprocess.CompletedProcess([], 1, '', rejection.stderr + 'unexpected crash\n'),
        ):
            assert not lab.verified_decoder_rejection(decoder, fixture, valid_oracle, wrong)
        for wrong in (
            subprocess.CompletedProcess([], 0, '{}', ''),
            subprocess.CompletedProcess([], 1, '', 'json.decoder.JSONDecodeError: malformed'),
            subprocess.CompletedProcess([], 1, '', lab._ERROR_MARKER + 'TypeError: wrong type'),
            subprocess.CompletedProcess([], 1, 'partial result', valid_oracle.stderr),
        ):
            assert not lab.verified_decoder_rejection(decoder, fixture, wrong, rejection)
        assert not lab.verified_decoder_rejection(object(), fixture, valid_oracle, rejection)
        assert not lab.verified_decoder_rejection(decoder, Fixture(('{}',)), valid_oracle, rejection)
        assert not lab.verified_decoder_rejection(decoder, Fixture(error='TypeError'), valid_oracle, rejection)
        value_error_oracle = subprocess.CompletedProcess([], 1, '', lab._ERROR_MARKER + 'ValueError: malformed\n')
        assert lab.verified_decoder_rejection(decoder, Fixture(error='ValueError'), value_error_oracle, rejection)
