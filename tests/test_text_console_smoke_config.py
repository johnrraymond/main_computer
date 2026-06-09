from __future__ import annotations

from pathlib import Path

from main_computer import rag_text_console_control_surface_smoke as smoke


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
