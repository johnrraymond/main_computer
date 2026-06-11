from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from main_computer import text_console as prod
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse


def artifact_by_kind(envelope: dict, kind: str) -> dict:
    for artifact in envelope["artifacts"]:
        if artifact["kind"] == kind:
            return artifact
    raise AssertionError(f"missing artifact kind {kind!r}: {envelope}")


def test_text_console_artifact_parser_splits_computer_and_repo_edit_blocks():
    message = """
I will inspect and prepare the edit.

```computer
/act terminal run "dir main_computer" --cwd repo-root
```

Now the edit handoff.

```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "Update README.md in the current repository to mention text-console mount previews.",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```
"""

    envelope = prod.parse_text_console_response_artifacts(message)

    assert envelope["artifact_count"] == 2
    assert [block["kind"] for block in envelope["blocks"]] == ["text", "artifact", "text", "artifact", "text"]
    mount = artifact_by_kind(envelope, "computer_mount")
    assert mount["state"] == "ready"
    assert mount["can_execute"] is True
    assert mount["validation"]["ok"] is True
    assert mount["command_reports"][0]["command"] == "dir main_computer"

    handoff = artifact_by_kind(envelope, "repo_edit_handoff")
    assert handoff["state"] == "ready"
    assert handoff["target_root"] == "repo-root"
    assert handoff["validation"]["ok"] is True


def test_text_console_mount_parser_accepts_relative_cwd_and_normalizes_policy():
    message = """
```computer
/act terminal run "dir" --cwd main_computer
```
"""

    envelope = prod.parse_text_console_response_artifacts(message)
    mount = artifact_by_kind(envelope, "computer_mount")
    report = mount["command_reports"][0]

    assert mount["state"] == "ready"
    assert mount["can_execute"] is True
    assert mount["validation"]["ok"] is True
    assert mount["canonical_commands"] == ['/act terminal run "dir" --cwd main_computer']
    assert report["action"] == "terminal_run"
    assert report["command"] == "dir"
    assert report["cwd"] == "main_computer"
    assert report["terminal_cwd"] == "main_computer"
    assert report["execution_policy"]["mode"] == "preview"
    assert report["execution_policy"]["requires_user_confirmation"] is True


def test_text_console_mount_parser_defaults_missing_cwd_to_repo_root_note():
    message = """
```computer
/act terminal run dir
```
"""

    envelope = prod.parse_text_console_response_artifacts(message)
    mount = artifact_by_kind(envelope, "computer_mount")
    report = mount["command_reports"][0]

    assert mount["state"] == "ready"
    assert mount["can_execute"] is True
    assert mount["validation"]["ok"] is True
    assert report["command"] == "dir"
    assert report["cwd"] == "repo-root"
    assert report["terminal_cwd"] == "."
    assert any("defaulting preview policy to repo-root" in note for note in report["notes"])


def test_text_console_mount_validation_rejects_unsupported_or_absolute_paths():
    message = """
```computer
/act terminal ls main_computer
/act terminal run "dir C:\\Users\\Front" --cwd repo-root
not an act line
```
"""

    envelope = prod.parse_text_console_response_artifacts(message)
    mount = artifact_by_kind(envelope, "computer_mount")

    assert mount["can_execute"] is False
    assert mount["validation"]["ok"] is False
    assert any("unsupported terminal /act form" in failure for failure in mount["validation"]["failures"])
    assert any("absolute Windows path" in failure for failure in mount["validation"]["failures"])
    assert any("non-/act line" in failure for failure in mount["validation"]["failures"])


def test_text_console_repo_edit_validation_rejects_absolute_paths():
    message = """
```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "Update C:\\Users\\Front\\Desktop\\matt\\main_computer\\README.md.",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```
"""

    envelope = prod.parse_text_console_response_artifacts(message)
    handoff = artifact_by_kind(envelope, "repo_edit_handoff")

    assert handoff["state"] == "preview"
    assert handoff["validation"]["ok"] is False
    assert any("absolute Windows path" in failure for failure in handoff["validation"]["failures"])


def test_operator_chat_response_includes_text_console_artifact_metadata(monkeypatch):
    root = Path(__file__).resolve().parents[1]

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def chat(self, messages):
            joined = "\n".join(str(message.content) for message in messages)
            if prod.ACTION_PREFLIGHT_PROMPT in joined:
                return ChatResponse(
                    content='{"needs_mount": true, "needs_edit": false, "needs_answer_only": false, '
                    '"selected_spec_ids": ["terminal"], "reason": "terminal request"}',
                    provider="fake",
                    model="fake-model",
                )
            return ChatResponse(
                content='```computer\n/act terminal run "dir main_computer" --cwd repo-root\n```',
                provider="fake",
                model="fake-model",
            )

    class FakeComputer:
        provider = FakeProvider()

        def context_pack(self, prompt):
            return SimpleNamespace(text="Deterministic workspace context pack", evidence=[], manifest_chars=0)

        def _web_search_context(self, prompt):
            return {"attempted": False, "results": []}, ""

    monkeypatch.setattr(prod.MainComputer, "build", classmethod(lambda cls, config=None: FakeComputer()))

    config = prod.TextConsoleConfig.from_current_directory(
        root,
        provider="ollama",
        model="fake-model",
        base_url="http://127.0.0.1:11434",
        timeout=120.0,
        think=False,
    )
    response = prod.run_text_console_operator_chat(
        text_console_config=config,
        prompt="Use Terminal to list files.",
        base_config=MainComputerConfig(workspace=root),
    )

    envelope = response.metadata["text_console_artifacts"]
    assert envelope["artifact_count"] == 1
    assert envelope["artifacts"][0]["kind"] == "computer_mount"
    assert envelope["artifacts"][0]["can_execute"] is True

def test_text_console_mount_execution_does_not_duplicate_terminal_transcript():
    page = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "text.html").read_text(encoding="utf-8")

    assert 'addEntry("terminal"' not in page
    assert "persistMountExecutionResult(artifact, results, state)" in page
    assert "artifact.execution_results = results.map(serializeTerminalResult)" in page
    assert "appendMountExecutionResult(card, artifact)" in page
    assert "report.terminal_cwd" in page
    assert "body: JSON.stringify({command, cwd, timeout_s: 15})" in page

