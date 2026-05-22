from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
import os
from pathlib import Path
from typing import Any

import pytest

from main_computer.docker_executor import DockerExecutor
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_json_repair_smoke import (
    WORKING_JSON_VALUE,
    break_json_fragment,
    extract_json_text,
    parse_args,
    run_json_repair_model_smoke,
    working_json_fragment,
)


class FakeRepairProvider:
    name = "fake"
    model = "json-repair"
    fallback = False

    def __init__(self, *, fenced: bool = False) -> None:
        self.calls: list[list[ChatMessage]] = []
        self.fenced = fenced

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls.append(list(messages))
        content = json.dumps(WORKING_JSON_VALUE, indent=2, sort_keys=True)
        if self.fenced:
            content = "```json\n" + content + "\n```"
        return ChatResponse(content=content, provider=self.name, model=self.model)


class FakeDockerRunner:
    def __init__(self) -> None:
        self.version_calls = 0
        self.parse_inputs: list[str] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["docker", "version", "--format"]:
            self.version_calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="25.0.0\n", stderr="")

        assert command[:2] == ["docker", "run"]
        env: dict[str, str] = {}
        iterator = iter(range(len(command)))
        for index in iterator:
            if command[index] == "-e":
                assignment = command[index + 1]
                key, _, value = assignment.partition("=")
                env[key] = value

        text = env.get("JSON_TEXT", "")
        self.parse_inputs.append(text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=f"JSONDecodeError: {exc.msg} at line {exc.lineno} column {exc.colno} (pos {exc.pos})\n",
            )

        stdout = "JSON_PARSE_OK\nCANONICAL_JSON=" + json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _fake_docker(tmp_path: Path, runner: FakeDockerRunner, monkeypatch: pytest.MonkeyPatch) -> DockerExecutor:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_docker = fake_bin / ("docker.exe" if os.name == "nt" else "docker")
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))

    return DockerExecutor(
        image="fake-json-parser:latest",
        runtime_root=tmp_path / "executor",
        enabled=True,
        runner=runner,
    )


def test_break_json_fragment_produces_invalid_json_for_one_two_and_three_breakages() -> None:
    for break_count in (1, 2, 3):
        broken, techniques = break_json_fragment(
            break_count=break_count,
            seed=7000 + break_count,
            text=working_json_fragment(),
        )

        assert len(techniques) == break_count
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)


def test_extract_json_text_accepts_fenced_model_response() -> None:
    raw = "```json\n" + json.dumps(WORKING_JSON_VALUE, indent=2, sort_keys=True) + "\n```"

    assert json.loads(extract_json_text(raw)) == WORKING_JSON_VALUE


def test_json_repair_model_smoke_runs_three_breakage_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeDockerRunner()
    provider = FakeRepairProvider(fenced=True)

    report = run_json_repair_model_smoke(
        repo_dir=tmp_path,
        output_root=tmp_path / "runs",
        provider=provider,
        docker_executor=_fake_docker(tmp_path, runner, monkeypatch),
        run_id="json_repair_test",
        strict=True,
        seed=9001,
        verbose=False,
        stream_model=True,
    )

    assert report.ok
    assert report.scenario == "json_repair_model_docker"
    assert [attempt.break_count for attempt in report.attempts] == [1, 2, 3]
    assert [attempt.semantic_match for attempt in report.attempts] == [True, True, True]
    assert len(provider.calls) == 3
    assert all("Broken JSON:" in call[1].content for call in provider.calls)
    assert all("Docker JSON parser error:" in call[1].content for call in provider.calls)
    assert len(runner.parse_inputs) == 6
    assert Path(report.report_path).exists()


def test_json_repair_model_smoke_reports_failure_when_repaired_json_still_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BadRepairProvider(FakeRepairProvider):
        def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
            self.calls.append(list(messages))
            return ChatResponse(content='{"still": broken}', provider=self.name, model=self.model)

    runner = FakeDockerRunner()

    report = run_json_repair_model_smoke(
        repo_dir=tmp_path,
        output_root=tmp_path / "runs",
        provider=BadRepairProvider(),
        docker_executor=_fake_docker(tmp_path, runner, monkeypatch),
        run_id="json_repair_failure",
        strict=True,
        seed=404,
        verbose=False,
        stream_model=False,
    )

    assert not report.ok
    assert any("repaired JSON did not parse in Docker" in item for item in report.failures)


def test_rag_json_repair_smoke_cli_defaults() -> None:
    args = parse_args([])

    assert args.seed == 1729
    assert args.quiet is False
    assert args.no_stream is False
    assert args.dump_json is False
