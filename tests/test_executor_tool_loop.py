from __future__ import annotations

import subprocess
from pathlib import Path

from main_computer.docker_executor import DockerExecutor
from main_computer.executor_tool_loop import (
    ExecutorToolLoopConfig,
    extract_executor_tool_request,
    run_executor_tool_loop,
)
from main_computer.models import ChatMessage, ChatResponse


class FakeProvider:
    name = "fake"
    model = "fake-tool-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.messages: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage]) -> ChatResponse:
        self.messages.append(list(messages))
        content = self.responses.pop(0)
        return ChatResponse(content=content, provider=self.name, model=self.model)


def test_extract_executor_tool_request_from_fenced_json() -> None:
    content = """Plan first.

```json
{
  "action": "execute_shell",
  "command": "python -c 'print(1)'",
  "cwd": "/workspace",
  "timeout_s": 10
}
```
"""
    request = extract_executor_tool_request(content)

    assert request is not None
    assert request["action"] == "execute_shell"
    assert request["cwd"] == "/workspace"


def test_tool_loop_returns_approval_request_when_auto_run_is_disabled(tmp_path: Path) -> None:
    provider = FakeProvider([
        '{"action":"execute_shell","description":"inspect","command":"python -c \\"print(123)\\"","cwd":"/workspace","timeout_s":5}'
    ])
    executor = DockerExecutor(image="main-computer-executor:test", runtime_root=tmp_path / "runtime", enabled=True)

    result = run_executor_tool_loop(
        provider=provider,
        prompt="Inspect the input.",
        context_text="workspace context",
        docker_executor=executor,
        config=ExecutorToolLoopConfig(max_steps=3, auto_run=False),
        upload_ids=["upload_0123456789abcdef"],
    )

    assert result.ok is True
    assert result.status == "tool_requested"
    assert result.needs_approval is True
    assert result.tool_request is not None
    assert result.tool_request["input_ids"] == ["upload_0123456789abcdef"]


def test_tool_loop_runs_command_output_back_into_model_until_final(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            '{"action":"execute_shell","command":"python - <<\'PY\'\\nprint(\'hello\')\\nPY","cwd":"/workspace","timeout_s":5}',
            '{"action":"final","content":"Done. Download /api/executor/artifacts/0123456789abcdef/result.txt"}',
        ]
    )
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output_mount = next(command[index + 1] for index, item in enumerate(command) if item == "-v" and command[index + 1].endswith(":/outputs:rw"))
        output_dir = Path(output_mount.split(":", 1)[0])
        (output_dir / "result.txt").write_text("artifact\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="hello\n", stderr="")

    executor = DockerExecutor(
        image="main-computer-executor:test",
        runtime_root=tmp_path / "runtime",
        enabled=True,
        runner=fake_runner,
    )

    result = run_executor_tool_loop(
        provider=provider,
        prompt="Run one calculation.",
        context_text="workspace context",
        docker_executor=executor,
        config=ExecutorToolLoopConfig(max_steps=3, auto_run=True),
    )

    assert result.ok is True
    assert result.status == "complete"
    assert "Done" in result.final_content
    assert calls
    assert any(step.kind == "command_output" for step in result.steps)
    assert "command_output" in provider.messages[-1][-1].content
