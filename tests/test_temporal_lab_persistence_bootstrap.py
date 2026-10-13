"""Offline contracts for idempotent, non-destructive Temporal Docker bootstrap."""
from __future__ import annotations

import json
import subprocess

import pytest

from tools.temporal_lab import local_temporal as lab


def result(stdout: str = "", *, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_existing_temporal_image_uid_is_read_instead_of_guessed(monkeypatch):
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        return result("1000:1000\n")

    monkeypatch.setattr(lab, "run_command", fake_run)
    assert lab._temporal_image_uid("temporalio/temporal:latest") == 1000
    assert len(commands) == 1
    assert commands[0][:3] == ["docker", "image", "inspect"]


@pytest.mark.parametrize("user,uid", [("1000", 1000), ("", 0), ("1001:1002", 1001)])
def test_temporal_image_uid_forms(monkeypatch, user, uid):
    monkeypatch.setattr(lab, "run_command", lambda *args, **kwargs: result(user))
    assert lab._temporal_image_uid("some-image") == uid


def test_missing_temporal_image_is_pulled_before_volume_preparation(monkeypatch):
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if args[1:3] == ["image", "inspect"]:
            return result("", returncode=1) if len(commands) == 1 else result("1000\n")
        return result()

    monkeypatch.setattr(lab, "run_command", fake_run)
    assert lab._temporal_image_uid("my-temporal") == 1000
    assert [cmd[:2] for cmd in commands] == [
        ["docker", "image"], ["docker", "pull"], ["docker", "image"]
    ]


def test_named_temporal_image_user_is_resolved_using_image_id(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return result("temporal\n")
        if args[:3] == ["docker", "run", "--rm"]:
            return result("1000\n")
        raise AssertionError(args)

    monkeypatch.setattr(lab, "run_command", fake_run)
    assert lab._temporal_image_uid("temporalio/temporal:latest") == 1000
    assert calls[1] == ["docker", "run", "--rm", "--network", "none",
                        "--entrypoint", "id", "temporalio/temporal:latest", "-u"]


@pytest.mark.parametrize("configured", ["temporal", "temporal:temporal"])
def test_named_user_resolution_failure_is_fail_closed(monkeypatch, configured):
    def fake_run(args, **kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            return result(configured)
        return result(returncode=1, stderr="id not available")

    monkeypatch.setattr(lab, "run_command", fake_run)
    with pytest.raises(lab.TemporalBootstrapError, match="was not changed"):
        lab._temporal_image_uid("temporalio/temporal:latest")


def test_named_user_malformed_id_is_rejected(monkeypatch):
    def fake_run(args, **kwargs):
        return result("temporal\n" if args[1] == "image" else "1000 extra\n")
    monkeypatch.setattr(lab, "run_command", fake_run)
    with pytest.raises(lab.TemporalBootstrapError, match="Unable to resolve"):
        lab._temporal_image_uid("temporalio/temporal:latest")


def test_persistent_volume_initializer_preserves_existing_files(monkeypatch):
    commands = []
    monkeypatch.setattr(lab, "_temporal_image_uid", lambda _: 1000)

    def fake_run(args, **kwargs):
        commands.append(args)
        return result()

    monkeypatch.setattr(lab, "run_command", fake_run)
    cfg = lab.TemporalConfig(persist=True, volume="kept-db")
    lab.prepare_persistent_volume(cfg)
    assert len(commands) == 2
    command = commands[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[3:5]
    assert "type=volume,src=kept-db,dst=/data" in command
    assert lab.DEFAULT_VOLUME_HELPER_IMAGE in command
    assert "chown -R 1000 /data" in command[-1]
    assert "chmod -R u+rwX /data" in command[-1]
    assert "volume rm" not in " ".join(command)
    assert "rm -rf" not in command[-1]
    assert "/data/temporal.db" not in command[-1]  # database untouched
    verification = commands[1]
    assert ["--user", "1000"] == verification[verification.index("--user"):verification.index("--user")+2]
    assert verification[-1] == "test -w /data"
    assert "type=volume,src=kept-db,dst=/data" in verification


def test_failed_unprivileged_writability_check_is_fail_closed(monkeypatch):
    commands = []
    monkeypatch.setattr(lab, "_temporal_image_uid", lambda _: 1000)
    def fake_run(args, **kwargs):
        commands.append(args)
        return result(returncode=1, stderr="not writable") if len(commands) == 2 else result()
    monkeypatch.setattr(lab, "run_command", fake_run)
    with pytest.raises(lab.TemporalBootstrapError, match="not writable"):
        lab.prepare_persistent_volume(lab.TemporalConfig(persist=True))
    assert len(commands) == 2


def test_initializer_failure_does_not_create_temporal_container(monkeypatch):
    monkeypatch.setattr(lab, "_temporal_image_uid", lambda _: 1000)
    monkeypatch.setattr(lab, "run_command", lambda *args, **kwargs: result(returncode=1, stderr="permission denied"))
    with pytest.raises(lab.TemporalBootstrapError, match="No data was deleted"):
        lab.prepare_persistent_volume(lab.TemporalConfig(persist=True))


def test_new_persistent_server_prepares_volume_before_creating_container(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, "require_docker", lambda: calls.append("require"))
    monkeypatch.setattr(lab, "docker_container_exists", lambda _: False)
    monkeypatch.setattr(lab, "prepare_persistent_volume", lambda _: calls.append("initialize"))
    monkeypatch.setattr(lab, "create_container", lambda _: calls.append("create"))
    monkeypatch.setattr(lab, "wait_for_temporal", lambda *args, **kwargs: calls.append("ready"))
    monkeypatch.setattr(lab, "print_ready", lambda _: calls.append("print"))
    lab.up(lab.TemporalConfig(persist=True), pull=False, timeout_seconds=10)
    assert calls == ["require", "initialize", "create", "ready", "print"]


def test_stopped_failed_container_is_replaced_without_deleting_volume(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, "require_docker", lambda: None)
    monkeypatch.setattr(lab, "docker_container_exists", lambda _: True)
    monkeypatch.setattr(lab, "docker_container_running", lambda _: False)
    monkeypatch.setattr(lab, "prepare_persistent_volume", lambda _: calls.append("initialize"))
    monkeypatch.setattr(lab, "remove_container", lambda _: calls.append("remove-container"))
    monkeypatch.setattr(lab, "create_container", lambda _: calls.append("create"))
    monkeypatch.setattr(lab, "wait_for_temporal", lambda *args, **kwargs: calls.append("ready"))
    monkeypatch.setattr(lab, "print_ready", lambda _: None)
    lab.up(lab.TemporalConfig(persist=True), pull=False, timeout_seconds=10)
    assert calls == ["initialize", "remove-container", "create", "ready"]


def _running_meta(cfg, *, persistent=True, mounted=True):
    return {
        "Mounts": ([{"Type": "volume", "Name": cfg.volume, "Destination": "/data", "RW": True}] if mounted else []),
        "Config": {"Cmd": ["server", "start-dev", "--db-filename", cfg.db_filename] if persistent else ["server", "start-dev"]},
    }


@pytest.mark.parametrize("mounted,persistent", [(False, False), (True, False)])
def test_existing_in_memory_container_is_rejected_for_persistent_recovery(monkeypatch, mounted, persistent):
    cfg = lab.TemporalConfig(persist=True)
    monkeypatch.setattr(lab, "run_command", lambda *args, **kwargs: result(json.dumps(_running_meta(cfg, mounted=mounted, persistent=persistent))))
    with pytest.raises(lab.TemporalBootstrapError, match="retry `up --persist`"):
        lab.verify_running_persistence(cfg)


def test_reused_persistent_container_matches_mount_and_db_command(monkeypatch):
    cfg = lab.TemporalConfig(persist=True)
    monkeypatch.setattr(lab, "run_command", lambda *args, **kwargs: result(json.dumps(_running_meta(cfg))))
    lab.verify_running_persistence(cfg)


def test_running_in_memory_server_does_not_falsely_pass_persistent_bootstrap(monkeypatch):
    calls = []
    cfg = lab.TemporalConfig(persist=True)
    monkeypatch.setattr(lab, "require_docker", lambda: None)
    monkeypatch.setattr(lab, "docker_container_exists", lambda _: True)
    monkeypatch.setattr(lab, "docker_container_running", lambda _: True)
    monkeypatch.setattr(lab, "run_command", lambda *args, **kwargs: result(json.dumps(_running_meta(cfg, mounted=False))))
    monkeypatch.setattr(lab, "create_container", lambda _: calls.append("create"))
    with pytest.raises(lab.TemporalBootstrapError, match="retry `up --persist`"):
        lab.up(cfg, pull=False, timeout_seconds=10)
    assert calls == []


def test_in_memory_default_does_not_need_volume_bootstrap(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, "require_docker", lambda: None)
    monkeypatch.setattr(lab, "docker_container_exists", lambda _: False)
    monkeypatch.setattr(lab, "prepare_persistent_volume", lambda _: calls.append("initialize"))
    monkeypatch.setattr(lab, "create_container", lambda _: calls.append("create"))
    monkeypatch.setattr(lab, "wait_for_temporal", lambda *args, **kwargs: None)
    monkeypatch.setattr(lab, "print_ready", lambda _: None)
    lab.up(lab.TemporalConfig(persist=False), pull=False, timeout_seconds=10)
    assert calls == ["create"]


def test_volume_preparation_failure_leaves_old_container_untouched(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, "require_docker", lambda: None)
    monkeypatch.setattr(lab, "docker_container_exists", lambda _: True)
    monkeypatch.setattr(lab, "docker_container_running", lambda _: False)

    def fail_prepare(_):
        calls.append("init-failed")
        raise lab.TemporalBootstrapError("cannot chown")

    monkeypatch.setattr(lab, "prepare_persistent_volume", fail_prepare)
    monkeypatch.setattr(lab, "remove_container", lambda _: calls.append("remove-container"))
    monkeypatch.setattr(lab, "create_container", lambda _: calls.append("create"))
    with pytest.raises(lab.TemporalBootstrapError, match="cannot chown"):
        lab.up(lab.TemporalConfig(persist=True), pull=False, timeout_seconds=10)
    assert calls == ["init-failed"]


def test_running_persistent_server_is_left_untouched(monkeypatch):
    cfg = lab.TemporalConfig(persist=True)
    commands = []
    monkeypatch.setattr(lab, "require_docker", lambda: None)
    monkeypatch.setattr(lab, "docker_container_exists", lambda _: True)
    monkeypatch.setattr(lab, "docker_container_running", lambda _: True)
    monkeypatch.setattr(lab, "run_command", lambda *args, **kwargs: result(json.dumps(_running_meta(cfg))))
    monkeypatch.setattr(lab, "prepare_persistent_volume", lambda _: commands.append("initialize"))
    monkeypatch.setattr(lab, "create_container", lambda _: commands.append("create"))
    monkeypatch.setattr(lab, "remove_container", lambda _: commands.append("remove"))
    monkeypatch.setattr(lab, "wait_for_temporal", lambda *args, **kwargs: commands.append("ready"))
    monkeypatch.setattr(lab, "print_ready", lambda _: None)
    lab.up(cfg, pull=False, timeout_seconds=10)
    assert commands == ["ready"]
