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
    )

    assert report["ok"] is True
    assert report["validation"]["commands"] == ['/act terminal run "dir main_computer" --cwd repo-root']


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
