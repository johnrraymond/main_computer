"""Source-bound NanoJev Docker image updates through Main Computer startup.

The Docker command simulator validates actual bootstrap calls without a GPU,
network, or local daemon. It exercises image labels and safe container swaps.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_image_bootstrap as bootstrap
from tools.nanojev_lifecycle_service import NanoJevLifecycle


def _repo(root: Path) -> Path:
    for path in bootstrap.IMAGE_INPUTS:
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text('initial source for ' + path, encoding='utf-8')
    return root / 'docker-compose.nanojev.yml'


class DockerSim:
    def __init__(self, *, image_present=True, image_label='old', running=False, container='old123'):
        self.label = image_label
        self.image_present = image_present
        self.image_id = 'sha256:image-old'
        self.container_id = container
        self.container_image_id = 'sha256:image-old' if container else ''
        self.running = running
        self.calls = []
        self.builds = 0
        self.recreates = 0
        self.stops = 0
        self.create_failure = False
        self.build_failure = False

    def run(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        def success(value=''):
            return SimpleNamespace(returncode=0, stdout=value, stderr='')
        if args[1:3] == ['image', 'inspect']:
            if not self.image_present:
                return SimpleNamespace(returncode=1, stdout='', stderr='No such image: image')
            if '--format' not in args:
                return success('exists')
            template = args[args.index('--format') + 1]
            return success(self.image_id if template == '{{.Id}}' else self.label)
        if args[1] == 'inspect':
            template = args[args.index('--format') + 1]
            if template == '{{.State.Running}}':
                return success('true' if self.running else 'false')
            assert template == '{{.Image}}'
            return success(self.container_image_id)
        assert args[1] == 'compose'
        if 'ps' in args:
            return success((self.container_id + '\n') if self.container_id else '')
        if 'build' in args:
            self.builds += 1
            if self.build_failure:
                return SimpleNamespace(returncode=51)
            self.image_present = True
            self.label = kwargs['env']['MAIN_COMPUTER_NANOJEV_SOURCE_SHA256']
            self.image_id = 'sha256:image-' + str(self.builds)
            kwargs['stdout'].write('Image built with source label\n')
            return success()
        if 'stop' in args:
            self.stops += 1
            assert self.running
            self.running = False
            kwargs['stdout'].write('Container stopped after all requests completed\n')
            return success()
        assert 'create' in args and '--no-build' in args and '--pull' in args
        if self.create_failure:
            return SimpleNamespace(returncode=52)
        if '--force-recreate' in args:
            self.recreates += 1
            assert not self.running, 'Container recreation interrupted live work'
        else:
            assert '--no-recreate' in args
        self.container_id = 'created-' + str(self.recreates + 1)
        self.container_image_id = self.image_id
        kwargs['stdout'].write('Created matching stopped container\n')
        return success()


def _run(root, sim, monkeypatch):
    monkeypatch.setattr(bootstrap.subprocess, 'run', sim.run)
    return bootstrap.run_bootstrap(root=root, compose_file=root / 'docker-compose.nanojev.yml')


def _status(root):
    return json.loads((root / 'runtime/start_stop/nanojev-image-bootstrap.json').read_text())


def test_exact_source_inputs_change_fingerprint_and_runtime_noise_does_not(tmp_path):
    compose = _repo(tmp_path)
    before = bootstrap.source_fingerprint(tmp_path, compose)
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'runtime/temporary.json').write_text('runtime changed')
    assert before == bootstrap.source_fingerprint(tmp_path, compose)
    source = tmp_path / 'docker/nanojev/clef_service.py'
    source.write_text('model service updated')
    after = bootstrap.source_fingerprint(tmp_path, compose)
    assert after != before
    assert len(after) == 64
    (tmp_path / 'tools/nanojev_objective_api.py').unlink()
    with pytest.raises(RuntimeError, match='build source missing'):
        bootstrap.source_fingerprint(tmp_path, compose)


def test_stale_image_build_recreates_stopped_container_once_and_skips_unchanged(monkeypatch, tmp_path):
    _repo(tmp_path)
    sim = DockerSim(image_label='stale', running=False)
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    first = _status(tmp_path)
    assert first['state'] == 'ready'
    assert first['result'] == 'built'
    assert first['container_result'] == 'recreated'
    assert first['source_sha256'] == sim.label
    assert sim.builds == 1 and sim.recreates == 1 and sim.stops == 0
    assert sim.container_image_id == sim.image_id
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    assert _status(tmp_path)['result'] == 'already-current'
    assert _status(tmp_path)['container_result'] == 'already-present'
    assert sim.builds == 1 and sim.recreates == 1


def test_source_change_builds_again_and_never_stops_running_container(monkeypatch, tmp_path):
    _repo(tmp_path)
    sim = DockerSim(image_label='stale', running=True)
    original_id = sim.container_id
    original_image = sim.container_image_id
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    status = _status(tmp_path)
    assert status['state'] == 'pending-activation'
    assert status['container_result'] == 'pending-activation'
    assert sim.image_id != original_image
    assert sim.container_id == original_id and sim.container_image_id == original_image
    assert sim.stops == 0 and sim.recreates == 0
    # Explicit safe activation is performed only by the idle manager.
    result = bootstrap.activate_pending_image(root=tmp_path,
        compose_file=tmp_path / 'docker-compose.nanojev.yml',
        project_name='main-computer-nanojev', docker='docker', image=bootstrap.IMAGE)
    assert result == 'recreated'
    assert _status(tmp_path)['state'] == 'ready'
    assert sim.stops == 1 and sim.recreates == 1 and not sim.running
    assert sim.container_image_id == sim.image_id


def test_second_source_change_invalidates_pending_approval(monkeypatch, tmp_path):
    _repo(tmp_path)
    sim = DockerSim(image_label='old', running=True)
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    (tmp_path / 'docker/nanojev/entrypoint.py').write_text('new unbuilt source')
    with pytest.raises(RuntimeError, match='no longer matches'):
        bootstrap.activate_pending_image(root=tmp_path,
            compose_file=tmp_path / 'docker-compose.nanojev.yml',
            project_name='main-computer-nanojev', docker='docker', image=bootstrap.IMAGE)
    assert sim.stops == 0 and sim.recreates == 0


def test_bad_build_or_create_never_releases_stale_container(monkeypatch, tmp_path):
    _repo(tmp_path)
    sim = DockerSim(image_label='old', running=True)
    sim.build_failure = True
    assert _run(tmp_path, sim, monkeypatch) == 'failed'
    assert _status(tmp_path)['state'] == 'failed'
    assert sim.stops == 0 and sim.recreates == 0
    sim.build_failure = False
    sim.running = False
    sim.create_failure = True
    assert _run(tmp_path, sim, monkeypatch) == 'failed'
    assert sim.stops == 0
    assert _status(tmp_path)['state'] == 'failed'


class PendingController:
    def __init__(self):
        self.phase = 'pending-activation'
        self.running = True
        self.activations = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.backend_url = 'http://127.0.0.1:9766'

    def image_bootstrap(self):
        return {'state': self.phase, 'image': bootstrap.IMAGE, 'source_sha256': 'test'}

    def health(self):
        return self.running

    def activate_pending_image(self):
        self.activations += 1
        self.phase = 'ready'
        self.running = False
        return 'recreated'

    def start(self):
        self.start_calls += 1
        self.running = True

    def stop(self):
        self.stop_calls += 1
        self.running = False


def test_manager_blocks_new_request_during_build_and_reports_pending():
    ctl = PendingController()
    ctl.phase = 'building'
    app = NanoJevLifecycle(ctl, idle_seconds=300)
    status = app.status(refresh=False)
    assert status['image_update_pending'] is True
    assert status['backend_ready'] is False
    with pytest.raises(RuntimeError, match='image update in progress'):
        app.begin_request()
    assert app.active_requests == 0
    assert ctl.activations == 0


def test_manager_defers_activation_until_prior_request_completes():
    ctl = PendingController()
    ctl.phase = 'ready'
    app = NanoJevLifecycle(ctl, idle_seconds=300)
    app.begin_request()
    assert app.active_requests == 1
    ctl.phase = 'pending-activation'
    assert app.sweep() is False
    assert ctl.activations == 0
    with pytest.raises(RuntimeError, match='waiting for active inference'):
        app.begin_request()
    assert app.active_requests == 1
    app.end_request()
    assert app.sweep() is True
    assert ctl.activations == 1
    assert ctl.stop_calls == 0  # activation owns its own stopped-container transition
    assert app.active_requests == 0 and not app.running
    app.begin_request()
    assert ctl.start_calls == 1 and ctl.activations == 1
    assert app.active_requests == 1
    app.end_request()


def test_pinned_manager_automatically_restarts_updated_backend_when_idle():
    ctl = PendingController()
    ctl.phase = 'ready'
    app = NanoJevLifecycle(ctl, idle_seconds=300)
    app.turn_on()
    assert app.pinned
    ctl.phase = 'pending-activation'
    assert app.sweep() is True
    assert ctl.activations == 1 and ctl.start_calls == 1
    assert app.pinned and app.running


def test_source_digest_docker_build_arg_and_status_are_wired_through_start():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / 'docker/nanojev/Dockerfile').read_text()
    compose = (root / 'docker-compose.nanojev.yml').read_text()
    launcher = (root / 'scripts/main-computer-start-stop.ps1').read_text()
    assert 'ARG MAIN_COMPUTER_NANOJEV_SOURCE_SHA256' in dockerfile
    assert 'LABEL org.main-computer.nanojev.source-sha256=' in dockerfile
    assert 'MAIN_COMPUTER_NANOJEV_SOURCE_SHA256:' in compose
    assert 'Start-MainComputerNanoJevImageBootstrap $RootPath $launchContext $pythonCommand' in launcher


def test_a_second_edit_rebuilds_and_recreates_once_more(monkeypatch, tmp_path):
    _repo(tmp_path)
    sim = DockerSim(image_label='old')
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    before = _status(tmp_path)['source_sha256']
    (tmp_path / 'docker/nanojev/clef_service.py').write_text('different service implementation')
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    after = _status(tmp_path)['source_sha256']
    assert after != before and after == sim.label
    assert sim.builds == 2 and sim.recreates == 2
    assert _run(tmp_path, sim, monkeypatch) == 'ready'
    assert sim.builds == 2 and sim.recreates == 2


def test_shutdown_refuses_to_kill_inference_in_flight():
    ctl = PendingController()
    ctl.phase = 'ready'
    app = NanoJevLifecycle(ctl, idle_seconds=300)
    app.begin_request()
    with pytest.raises(RuntimeError, match='active inference'):
        app.shutdown_now()
    assert app.active_requests == 1 and app.running and ctl.stop_calls == 0
    app.end_request()
    app.shutdown_now()
    assert ctl.stop_calls == 1 and app.shutdown_complete


def test_existing_launcher_guard_prevents_force_stopping_active_inference():
    root = Path(__file__).resolve().parents[1]
    helper = (root / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    assert 'state = "nanojev-inference-active"' in helper
    assert 'Retry start.bat after they complete.' in helper
    stop_script = helper.split('function Stop-MainComputer([string]$RootPath',1)[1]
    assert stop_script.index('if ($null -ne $nanoJevManagerStop -and -not') < stop_script.index('$candidates = @{}')
