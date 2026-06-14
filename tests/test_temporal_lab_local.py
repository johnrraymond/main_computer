from __future__ import annotations

import json

from tools.temporal_lab.local_temporal import (
    DEFAULT_CONTAINER_NAME,
    DEFAULT_IMAGE,
    DEFAULT_NAMESPACE,
    DEFAULT_TASK_QUEUE,
    TemporalConfig,
    build_run_command,
    config_from_args,
    main,
)


def _parser_args(**overrides: object) -> object:
    values = {
        "container_name": DEFAULT_CONTAINER_NAME,
        "image": DEFAULT_IMAGE,
        "namespace": DEFAULT_NAMESPACE,
        "task_queue": DEFAULT_TASK_QUEUE,
        "volume": "vol",
        "grpc_port": 7233,
        "ui_port": 8233,
        "bind_host": "127.0.0.1",
        "public_bind": False,
        "persist": False,
    }
    values.update(overrides)
    return type("Args", (), values)()


def test_build_run_command_defaults_to_localhost_and_in_memory_dev_server() -> None:
    config = TemporalConfig()
    command = build_run_command(config)

    assert command[:4] == ["docker", "run", "-d", "--name"]
    assert DEFAULT_CONTAINER_NAME in command
    assert DEFAULT_IMAGE in command
    assert f"127.0.0.1:{config.grpc_port}:7233" in command
    assert f"127.0.0.1:{config.ui_port}:8233" in command
    assert "-v" not in command
    assert f"{config.volume}:/data" not in command
    assert "--db-filename" not in command
    assert "/data/temporal.db" not in command
    assert "--namespace" in command
    assert DEFAULT_NAMESPACE in command


def test_build_run_command_can_opt_into_persistent_dev_state() -> None:
    config = TemporalConfig(persist=True)
    command = build_run_command(config)

    assert "-v" in command
    assert f"{config.volume}:/data" in command
    assert "--db-filename" in command
    assert "/data/temporal.db" in command


def test_public_bind_switches_to_all_interfaces() -> None:
    config = config_from_args(_parser_args(public_bind=True))

    assert config.bind_host == "0.0.0.0"
    assert "0.0.0.0:7233:7233" in build_run_command(config)


def test_persist_flag_reaches_config() -> None:
    config = config_from_args(_parser_args(persist=True))

    assert config.persist is True
    assert "--db-filename" in build_run_command(config)


def test_env_command_prints_actionable_values(capsys) -> None:
    exit_code = main(["env"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"status": "ready"' in captured.out
    assert '"persistent": false' in captured.out
    assert 'TEMPORAL_ADDRESS="localhost:7233"' in captured.out
    assert f'TEMPORAL_NAMESPACE="{DEFAULT_NAMESPACE}"' in captured.out
    assert f'TEMPORAL_TASK_QUEUE="{DEFAULT_TASK_QUEUE}"' in captured.out

    # The first block is JSON, which gives scripts a stable machine-readable shape.
    json_start = captured.out.index("{")
    json_end = captured.out.index("\n}\n") + 2
    payload = json.loads(captured.out[json_start:json_end])
    assert payload["namespace"] == DEFAULT_NAMESPACE
    assert payload["task_queue"] == DEFAULT_TASK_QUEUE
    assert payload["persistent"] is False
