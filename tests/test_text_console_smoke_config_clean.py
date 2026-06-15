from __future__ import annotations

import inspect
from pathlib import Path

from main_computer import rag_text_console_control_surface_smoke as smoke
from main_computer.config import MainComputerConfig
from main_computer.text_console import (
    TEXT_CONSOLE_AI_SYSTEM_PROMPT,
    TextConsoleConfig,
    build_text_console_model_input,
    run_text_console_operator_chat,
)
from main_computer.viewport_routes_applications import ViewportApplicationRoutesMixin


def test_text_console_smoke_config_owns_repo_root_context(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    legacy_bogus_workspace = repo_root / "definitely-not-the-text-console-workspace"

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("MAIN_COMPUTER_WORKSPACE", str(legacy_bogus_workspace))

    _computer, _context_pack, _messages, diagnostics = smoke.build_text_console_intended_model_input(
        request_text=smoke.DEFAULT_CHAT_CONSOLE_MOUNT_REQUEST,
        base_url="http://127.0.0.1:11434",
        model="gemma4:26b",
        timeout=120.0,
        think=False,
    )

    text_console_config = diagnostics["text_console_config"]
    context_text = diagnostics["context_pack_text"]

    assert Path(text_console_config["current_directory"]) == repo_root
    assert Path(text_console_config["context_root"]) == repo_root
    assert Path(text_console_config["working_directory"]) == repo_root
    assert diagnostics["legacy_main_computer_config_adapter"]["workspace"] == str(repo_root)
    assert str(legacy_bogus_workspace) not in context_text
    assert f"Workspace root: {repo_root}" in context_text
    assert "- main_computer: present" in context_text
    assert diagnostics["config_validation_failures"] == []
    assert diagnostics["text_console_model_input_failures"] == []


def test_text_console_config_adapter_overrides_legacy_workspace(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    legacy_bogus_workspace = repo_root / "definitely-not-the-text-console-workspace"

    monkeypatch.chdir(repo_root)

    base_config = MainComputerConfig(
        workspace=legacy_bogus_workspace,
        provider="ollama",
        model="gemma4:26b",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_timeout_s=120.0,
        ollama_think=False,
    )
    text_console_config = TextConsoleConfig.from_current_directory(
        repo_root,
        provider=base_config.provider,
        model=base_config.model,
        base_url=base_config.ollama_base_url,
        timeout=base_config.ollama_timeout_s,
        think=base_config.ollama_think,
    )

    adapted = text_console_config.to_legacy_main_computer_config(MainComputerConfig, base_config=base_config)

    assert adapted.workspace == repo_root
    assert adapted.provider == base_config.provider
    assert adapted.model == base_config.model
    assert adapted.ollama_base_url == base_config.ollama_base_url
    assert adapted.ollama_timeout_s == base_config.ollama_timeout_s
    assert adapted.ollama_think is False


def test_production_text_console_model_input_uses_text_console_root(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    legacy_bogus_workspace = repo_root / "definitely-not-the-text-console-workspace"

    monkeypatch.chdir(repo_root)

    base_config = MainComputerConfig(
        workspace=legacy_bogus_workspace,
        provider="ollama",
        model="gemma4:26b",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_timeout_s=120.0,
        ollama_think=False,
    )
    text_console_config = TextConsoleConfig.from_current_directory(
        repo_root,
        provider=base_config.provider,
        model=base_config.model,
        base_url=base_config.ollama_base_url,
        timeout=base_config.ollama_timeout_s,
        think=base_config.ollama_think,
    )

    model_input = build_text_console_model_input(
        text_console_config=text_console_config,
        source=smoke.DEFAULT_CHAT_CONSOLE_MOUNT_REQUEST,
        base_config=base_config,
    )

    context_text = model_input.context_pack.text
    assert model_input.legacy_config.workspace == repo_root
    assert str(legacy_bogus_workspace) not in context_text
    assert f"Workspace root: {repo_root}" in context_text
    assert TEXT_CONSOLE_AI_SYSTEM_PROMPT in [message.content for message in model_input.messages]
    assert any("preview-only local computer mount requests" in message.content for message in model_input.messages)


def test_smoke_uses_production_text_console_config_component():
    assert smoke.TextConsoleConfig is TextConsoleConfig


def test_api_chat_handler_uses_text_console_route_not_chat_console_route():
    source = inspect.getsource(ViewportApplicationRoutesMixin._handle_chat)

    assert "TextConsoleConfig.from_current_directory" in source
    assert "self.server.debug_root" in source
    assert "run_text_console_operator_chat" in source
    assert "build_text_console_model_input" not in source
    assert "chat_response_from_text_console_model_input" not in source
    assert "self.server.computer.context_pack(prompt)" not in source
    assert "inline_test_provider" in source


def test_text_console_intended_smoke_injects_action_grammar_prompt(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)

    _computer, _context_pack, _messages, diagnostics = smoke.build_text_console_intended_model_input(
        request_text=smoke.DEFAULT_CHAT_CONSOLE_MOUNT_REQUEST,
        base_url="http://127.0.0.1:11434",
        model="gemma4:26b",
        timeout=120.0,
        think=False,
    )

    messages = diagnostics["messages"]
    grammar_messages = [
        item for item in messages
        if smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND in item["content"]
    ]

    assert grammar_messages
    assert diagnostics["text_console_action_grammar_prompt_chars"] > 0
    assert "/act list" in diagnostics["text_console_action_grammar_prompt"]
    assert smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND in diagnostics["request_payload"]["messages"][-2]["content"]
    assert diagnostics["request_payload"]["messages"][-1]["role"] == "user"


def test_text_console_mount_command_validation_accepts_only_canonical_terminal_mounts():
    good_payload = smoke.parse_assistant_control_message(
        "I will request a preview-only terminal action.\n\n"
        "```computer\n"
        f"{smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND}\n"
        "```"
    )

    good_report = smoke.validate_text_console_mount_commands(
        good_payload,
        expected_commands=[smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND],
    )

    assert good_report["ok"]
    assert good_report["commands"] == [smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND]

    bad_examples = [
        "/act list main_computer",
        "/act terminal ls -R main_computer",
        "/act terminal run dir main_computer --cwd repo-root",
        "/act terminal run \"dir main_computer\"",
        "/act terminal run \"dir main_computer\" --cwd repo-root",
    ]

    for command in bad_examples:
        payload = smoke.parse_assistant_control_message(f"```computer\n{command}\n```")
        report = smoke.validate_text_console_mount_commands(
            payload,
            expected_commands=[smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND],
        )
        assert not report["ok"], command
        assert report["failures"], command


def test_inline_mount_render_contract_does_not_require_surrounding_prose():
    render = smoke.RenderPacket(
        answer_markdown="",
        command_card={},
        broker_mounts=[],
        mounted_object={"canonical_command": smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND},
    )

    cases = [
        (
            "bare mount",
            "```computer\n"
            f"{smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND}\n"
            "```",
            ["computer_mount"],
        ),
        (
            "leading prose only",
            "I will request a preview-only terminal action.\n\n"
            "```computer\n"
            f"{smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND}\n"
            "```",
            ["markdown", "computer_mount"],
        ),
        (
            "leading and trailing prose",
            "I will request a preview-only terminal action.\n\n"
            "```computer\n"
            f"{smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND}\n"
            "```\n\n"
            "Paste the terminal result back here after it runs.",
            ["markdown", "computer_mount", "markdown"],
        ),
    ]

    for label, message, expected_kinds in cases:
        payload = smoke.parse_assistant_control_message(message)
        mount = smoke.first_computer_mount(payload)
        blocks = smoke.render_inline_assistant_message_blocks(payload, {mount["mountId"]: render})

        assert [block["kind"] for block in blocks] == expected_kinds, label
        assert smoke.validate_inline_assistant_mount_render_blocks(payload, blocks) == [], label
        assert blocks[expected_kinds.index("computer_mount")]["source"] == smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND



def test_bare_smoke_summary_is_concise_and_points_to_full_report(capsys, tmp_path):
    record = {
        "label": "terminal mount request",
        "ok": True,
        "expect_mount": True,
        "model_input": {
            "text_console_config": {"context_root": str(Path.cwd())},
            "message_count": 5,
            "input_chars": 1234,
            "context_pack_chars": 456,
            "request_sha256": "abc123",
            "text_console_action_grammar_prompt_sha256": "grammar123",
        },
        "response": (
            "I will request a preview-only terminal action.\n\n"
            "```computer\n"
            f"{smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND}\n"
            "```"
        ),
        "mount_command_validation": {
            "ok": True,
            "commands": [smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND],
            "expected_commands": [smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND],
            "failures": [],
            "invalid_lines": [],
        },
        "ollama_terminal_metrics": {
            "done_reason": "stop",
            "prompt_eval_count": 1581,
            "eval_count": 44,
        },
    }
    report = smoke.SmokeReport(
        ok=True,
        base_url="http://127.0.0.1:11434",
        model="gemma4:26b",
        used_ollama=True,
        paranoia="normal",
        exact_render_packet={"mounted_object": {"canonical_command": "/act file manager show hidden files"}},
        exact_path_render_packet={},
        terminal_exact_run_packet={"mounted_object": {"canonical_command": '/act terminal run "git status" --cwd repo-root'}},
        terminal_run_in_active_packet={"mounted_object": {"canonical_command": '/act terminal run-in active "git status" --cwd repo-root'}},
        terminal_out_of_blue_packet={},
        terminal_ambiguous_packet={},
        terminal_mutation_normal_packet={"mounted_object": {"canonical_command": '/act terminal run "python new_patch.py patch.zip" --cwd repo-root'}},
        terminal_mutation_relaxed_packet={},
        terminal_locked_packet={"mounted_object": {"canonical_command": '/act terminal run "git status" --cwd repo-root'}},
        terminal_plan_model_payload={},
        terminal_plan_mount_payload={},
        terminal_plan_inline_render_blocks=[],
        terminal_plan_packet={},
        terminal_plan_result_packet={},
        terminal_plan_render_packet={},
        terminal_plan_failure_packet={},
        terminal_plan_failure_result_packet={},
        terminal_plan_mutation_pause_packet={},
        terminal_plan_mutation_pause_result_packet={},
        terminal_plan_locked_packet={},
        terminal_plan_locked_result_packet={},
        terminal_plan_interrupt_command_packet={},
        terminal_plan_interrupt_result_packet={},
        terminal_plan_max_step_rejection={},
        contextual_request="now run git status",
        contextual_terminal_context={},
        contextual_model_payload={"computer_mounts": [{"canonicalCommands": ['/act terminal run-in active "git status" --cwd repo-root']}]},
        contextual_mount_payload={},
        contextual_inline_render_blocks=[],
        contextual_rag_packet={},
        out_of_blue_request="now run git status",
        out_of_blue_terminal_context={},
        out_of_blue_model_payload={"computer_mounts": [{"canonicalCommands": ['/act terminal run "git status" --cwd repo-root']}]},
        out_of_blue_mount_payload={},
        out_of_blue_inline_render_blocks=[],
        out_of_blue_rag_packet={},
        text_console_intended_pathway_smoke=[record],
        inert_prose_blocks=[],
        warnings=[],
        failures=[],
    )

    full_report_path = tmp_path / "smoke_report.json"
    smoke.print_concise_smoke_summary(report, report_path=full_report_path)
    output = capsys.readouterr().out

    assert "RAG text-console terminal-context smoke: PASS" in output
    assert f"full_report={full_report_path}" in output
    assert "Text-console intended pathway:" in output
    assert "mount_validation: PASS" in output
    assert smoke.DEFAULT_EXPECTED_TEXT_CONSOLE_MOUNT_COMMAND in output
    assert "raw_stream_events" not in output
    assert "Full diagnostic report" not in output

