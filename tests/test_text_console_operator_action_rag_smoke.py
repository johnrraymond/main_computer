from __future__ import annotations

import json
from pathlib import Path

from main_computer import rag_text_console_operator_action_rag_smoke as smoke


def test_action_specs_load_terminal_and_repo_edit():
    root = Path(__file__).resolve().parents[1]
    specs = smoke.load_action_specs(root)

    assert {"terminal", "repo_edit"} <= set(specs)
    assert "computer_mount" in specs["terminal"].output_kinds
    assert "repo_edit_handoff" in specs["repo_edit"].output_kinds
    assert '/act terminal run "<command>" --cwd repo-root' in specs["terminal"].text
    assert '"mode": "repo_root_edit_request"' in specs["repo_edit"].text



def test_smoke_final_prompt_includes_runtime_terminal_target_profile():
    root = Path(__file__).resolve().parents[1]
    smoke.add_repo_to_path(root)
    specs = smoke.load_action_specs(root)
    prompt = smoke.selected_action_specs_prompt(specs, ["terminal"])

    assert "Selected text-console action target profiles" in prompt
    assert '"id": "repo-root-powershell-terminal"' in prompt
    assert '"shell": "powershell"' in prompt
    assert "Selected text-console action spec: terminal" in prompt


def test_preflight_validation_requires_expected_specs():
    payload = {
        "needs_mount": True,
        "needs_edit": True,
        "needs_answer_only": False,
        "selected_spec_ids": ["terminal", "repo_edit"],
        "reason": "combined request",
    }

    report = smoke.validate_preflight_payload(
        payload,
        available_spec_ids={"terminal", "repo_edit"},
        expected_spec_ids=("terminal", "repo_edit"),
    )

    assert report["ok"] is True
    assert report["selected_spec_ids"] == ["terminal", "repo_edit"]


def test_preflight_validation_rejects_unknown_or_wrong_specs():
    payload = {
        "needs_mount": True,
        "needs_edit": False,
        "needs_answer_only": False,
        "selected_spec_ids": ["terminal", "browser"],
        "reason": "bad extra spec",
    }

    report = smoke.validate_preflight_payload(
        payload,
        available_spec_ids={"terminal", "repo_edit"},
        expected_spec_ids=("terminal",),
    )

    assert report["ok"] is False
    assert any("unknown specs" in failure for failure in report["failures"])
    assert any("expected selected_spec_ids" in failure for failure in report["failures"])


def test_repo_edit_block_validation_accepts_repo_root_handoff():
    message = """
I will prepare the edit handoff.

```repo-edit
{
  "mode": "repo_root_edit_request",
  "target_root": "repo-root",
  "request_for_editor": "Update README.md to document text-console preview-only computer mount requests.",
  "requires_confirmation": true,
  "blocked_reasons": []
}
```
"""
    blocks = smoke.extract_repo_edit_blocks(message)
    report = smoke.validate_repo_edit_blocks(
        blocks,
        expect_repo_edit=True,
        user_prompt="Update README.md to document text-console preview-only computer mount requests.",
        required_terms=("README", "preview-only"),
    )

    assert len(blocks) == 1
    assert report["ok"] is True


def test_repo_edit_block_validation_rejects_absolute_paths():
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
    blocks = smoke.extract_repo_edit_blocks(message)
    report = smoke.validate_repo_edit_blocks(
        blocks,
        expect_repo_edit=True,
        user_prompt="Update README.md.",
        required_terms=("README",),
    )

    assert report["ok"] is False
    assert any("absolute Windows path" in failure for failure in report["failures"])


def test_mount_validation_uses_existing_broker_grammar():
    message = """
I will request the preview.

```computer
/act terminal run "dir main_computer" --cwd repo-root
```
"""
    report = smoke.parse_and_validate_mounts(
        message,
        expected_mount_commands=('/act terminal run "dir main_computer" --cwd repo-root',),
        expect_mount=True,
        strict_mount_commands=True,
    )

    assert report["ok"] is True
    assert report["validation"]["commands"] == ['/act terminal run "dir main_computer" --cwd repo-root']



def test_mount_validation_can_report_reference_commands_without_exact_matching():
    message = """
I will request the preview.

```computer
/act terminal run "Get-ChildItem main_computer" --cwd repo-root
```
"""
    report = smoke.parse_and_validate_mounts(
        message,
        expected_mount_commands=('/act terminal run "dir main_computer" --cwd repo-root',),
        expect_mount=True,
        strict_mount_commands=False,
    )

    assert report["ok"] is True
    assert report["validation"]["commands"] == ['/act terminal run "Get-ChildItem main_computer" --cwd repo-root']
    assert report["validation"]["reference_commands"] == ['/act terminal run "dir main_computer" --cwd repo-root']
    assert report["validation"]["expected_commands"] == []



def test_offline_contract_fixtures_pass(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root)

    rc = smoke.main(["--offline-contract-only"])

    assert rc == 0


def test_runtime_prompt_sections_keep_final_action_context_compact():
    root = Path(__file__).resolve().parents[1]
    specs = smoke.load_action_specs(root)

    terminal_prompt = smoke.selected_action_specs_prompt(specs, ["terminal"])
    repo_prompt = smoke.selected_action_specs_prompt(specs, ["repo_edit"])

    assert "Selected text-console action spec: terminal" in terminal_prompt
    assert '/act terminal run "<command>" --cwd repo-root' in terminal_prompt
    assert len(terminal_prompt) < len(specs["terminal"].text)

    assert "Selected text-console action spec: repo_edit" in repo_prompt
    assert '"mode": "repo_root_edit_request"' in repo_prompt
    assert len(repo_prompt) < len(specs["repo_edit"].text)


def test_preflight_messages_do_not_include_full_workspace_context():
    class Config:
        current_directory = Path("repo").resolve()
        context_root = Path("repo").resolve()
        working_directory = Path("repo").resolve()

    class ModelInput:
        text_console_config = Config()
        messages = []

    root = Path(__file__).resolve().parents[1]
    specs = smoke.load_action_specs(root)
    messages = smoke.build_preflight_messages(
        model_input=ModelInput(),
        specs=specs,
        request_text="Use Terminal to list files.",
    )
    joined = "\n".join(str(message.content) for message in messages)

    assert "Available text-console action spec catalog" in joined
    assert "Deterministic workspace context pack" not in joined
    assert "Main computer file manifest" not in joined


def test_preflight_booleans_are_diagnostic_selected_specs_are_authoritative():
    payload = {
        "needs_mount": False,
        "needs_edit": False,
        "needs_answer_only": True,
        "selected_spec_ids": ["terminal"],
        "reason": "selected the terminal spec but booleans drifted",
    }

    report = smoke.validate_preflight_payload(
        payload,
        available_spec_ids={"terminal", "repo_edit"},
        expected_spec_ids=("terminal",),
    )

    assert report["ok"] is True
    assert report["derived"]["needs_mount"] is True
    assert report["boolean_notes"]


def test_production_text_console_operator_chat_uses_action_specs(monkeypatch):
    import main_computer.text_console as prod
    from main_computer.config import MainComputerConfig
    from main_computer.models import ChatResponse
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    calls = []

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def chat(self, messages):
            calls.append(messages)
            joined = "\n".join(str(message.content) for message in messages)
            if prod.ACTION_PREFLIGHT_PROMPT in joined:
                return ChatResponse(
                    content=json.dumps(
                        {
                            "needs_mount": True,
                            "needs_edit": False,
                            "needs_answer_only": False,
                            "selected_spec_ids": ["terminal"],
                            "reason": "User asked Terminal to list files.",
                        }
                    ),
                    provider="fake",
                    model="fake-model",
                )
            return ChatResponse(
                content='```computer\n/act terminal run "Get-ChildItem main_computer" --cwd repo-root\n```',
                provider="fake",
                model="fake-model",
            )

    class FakeComputer:
        provider = FakeProvider()

        def context_pack(self, prompt):
            return SimpleNamespace(
                text=(
                    "Deterministic workspace context pack:\n"
                    f"Workspace root: {root}\n"
                    "Main computer file manifest:\n"
                    "- main_computer:\n"
                    "  - text_console.py\n"
                ),
                evidence=[],
                manifest_chars=64,
            )

        def _web_search_context(self, prompt):
            return {"attempted": False, "results": []}, ""

    fake_computer = FakeComputer()
    monkeypatch.setattr(prod.MainComputer, "build", classmethod(lambda cls, config=None: fake_computer))

    text_console_config = prod.TextConsoleConfig.from_current_directory(
        root,
        provider="ollama",
        model="fake-model",
        base_url="http://127.0.0.1:11434",
        timeout=120.0,
        think=False,
    )
    response = prod.run_text_console_operator_chat(
        text_console_config=text_console_config,
        prompt="Use Terminal to list the files in main_computer.",
        base_config=MainComputerConfig(workspace=root),
    )

    assert response.content == '```computer\n/act terminal run "Get-ChildItem main_computer" --cwd repo-root\n```'
    operator = response.metadata["text_console_operator"]
    assert operator["selected_spec_ids"] == ["terminal"]
    assert len(calls) == 2

    preflight_joined = "\n".join(message.content for message in calls[0])
    final_joined = "\n".join(message.content for message in calls[1])
    assert "Available text-console action spec catalog" in preflight_joined
    assert "Deterministic workspace context pack" not in preflight_joined
    assert "Selected text-console action spec: terminal" in final_joined
    assert '/act terminal run "<command>" --cwd repo-root' in final_joined



def test_threaded_followup_smoke_uses_chat_messages_not_prompt_history(monkeypatch):
    import main_computer.text_console as prod
    from main_computer.models import ChatResponse
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    specs = smoke.load_action_specs(root)
    fixture = smoke.default_threaded_fixtures()[0]
    calls = []

    class ThreadAwareProvider:
        name = "fake"
        model = "fake-model"

        def chat(self, messages):
            calls.append(messages)
            joined = "\n".join(str(message.content) for message in messages)
            if smoke.ACTION_PREFLIGHT_PROMPT in joined:
                return ChatResponse(
                    content=json.dumps(
                        {
                            "needs_mount": True,
                            "needs_edit": False,
                            "needs_answer_only": False,
                            "selected_spec_ids": ["terminal"],
                            "reason": "The follow-up refers to the prior Terminal listing in this thread.",
                        }
                    ),
                    provider="fake",
                    model="fake-model",
                )

            last_user = [message.content for message in messages if message.role == "user"][-1]
            prior_joined = "\n".join(str(message.content) for message in messages[:-1])
            if (
                last_user == fixture.followup_prompt
                and fixture.prior_assistant_content in prior_joined
                and "Terminal result from an explicitly executed text-console mount" in prior_joined
            ):
                return ChatResponse(
                    content="```computer\n" + "\n".join(fixture.expected_mount_commands) + "\n```",
                    provider="fake",
                    model="fake-model",
                )

            return ChatResponse(
                content="I do not know what should be recursive.",
                provider="fake",
                model="fake-model",
            )

    class FakeComputer:
        provider = ThreadAwareProvider()

        def context_pack(self, prompt):
            return SimpleNamespace(
                text=(
                    "Deterministic workspace context pack:\n"
                    f"Workspace root: {root}\n"
                    "Main computer file manifest:\n"
                    "- main_computer:\n"
                    "  - text_console.py\n"
                ),
                evidence=[],
                manifest_chars=64,
            )

        def _web_search_context(self, prompt):
            return {"attempted": False, "results": []}, ""

    monkeypatch.setattr(prod.MainComputer, "build", classmethod(lambda cls, config=None: FakeComputer()))

    report = smoke.run_threaded_fixture(
        root=root,
        fixture=fixture,
        specs=specs,
        base_url="http://127.0.0.1:11434",
        model="fake-model",
        timeout=120.0,
        think=False,
        offline_contract_only=False,
    )

    assert report["ok"] is True
    assert report["threaded_chat_shape"]["ok"] is True
    assert len(calls) == 2

    final_messages = calls[1]
    assert final_messages[-1].role == "user"
    assert final_messages[-1].content == "now make it recursive."
    assert "Previous conversation:" not in final_messages[-1].content
    assert fixture.prior_assistant_content not in final_messages[-1].content
    assert fixture.prior_terminal_result.strip() not in final_messages[-1].content

    prior_joined = "\n".join(message.content for message in final_messages[:-1])
    assert fixture.prior_user_prompt in prior_joined
    assert fixture.prior_assistant_content in prior_joined
    assert "Terminal result from an explicitly executed text-console mount" in prior_joined
    assert "```computer\n" + "\n".join(fixture.expected_mount_commands) + "\n```" == report["final_response"]["content"]


def test_threaded_message_shape_validation_rejects_history_stuffed_user_prompt():
    from main_computer.models import ChatMessage

    fixture = smoke.default_threaded_fixtures()[0]
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(
            role="user",
            content=(
                "Previous conversation:\n"
                + fixture.prior_assistant_content
                + "\n\n"
                + fixture.followup_prompt
            ),
        ),
    ]

    report = smoke.validate_threaded_chat_message_shape(messages, fixture=fixture)

    assert report["ok"] is False
    assert any("latest user message must be the raw follow-up prompt" in failure for failure in report["failures"])
    assert any("prompt-history hack marker" in failure for failure in report["failures"])

def test_production_action_specs_have_compact_runtime_sections():
    import main_computer.text_console as prod

    root = Path(__file__).resolve().parents[1]
    specs = prod.load_action_specs(root)
    prompt = prod.selected_action_specs_prompt(specs, ["terminal", "repo_edit"])

    assert "Selected text-console action spec: terminal" in prompt
    assert "Selected text-console action spec: repo_edit" in prompt
    assert '/act terminal run "<command>" --cwd repo-root' in prompt
    assert '"mode": "repo_root_edit_request"' in prompt
    assert len(prompt) < len(specs["terminal"].text) + len(specs["repo_edit"].text)
