from __future__ import annotations

from types import SimpleNamespace

import pytest

from main_computer import chat_ai_subprocess as subprocess_module
from main_computer.models import ChatResponse


@pytest.mark.parametrize("scoped", [False, True], ids=["normal-chat", "embedded-editor"])
def test_chat_console_ai_child_returns_completed_streamed_model_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path, scoped: bool
) -> None:
    """A completed model stream must yield a normal result, not a worker-live-session NameError."""
    emitted: list[dict] = []

    class CaptureStdout:
        def emit(self, message: dict) -> None:
            emitted.append(message)

    class FakeProvider:
        name = "ollama"
        model = "gemma4:26b"

        def __init__(self) -> None:
            self.stream_callback = None
            self.messages = None

        def chat(self, messages) -> ChatResponse:
            self.messages = messages
            if self.stream_callback is not None:
                self.stream_callback({
                    "type": "content_delta",
                    "provider": self.name,
                    "model": self.model,
                    "delta": "OK",
                    "content_preview": "OK",
                    "content_chars": 2,
                })
            return ChatResponse(
                content="OK",
                provider=self.name,
                model=self.model,
                metadata={"done_reason": "stop"},
            )

    provider = FakeProvider()
    original_callback = lambda event: None
    provider.stream_callback = original_callback
    computer = SimpleNamespace(
        provider=provider,
        context_pack=lambda source: SimpleNamespace(text="context pack"),
        _web_search_context=lambda source: ({"disabled": True}, ""),
    )
    monkeypatch.setattr(subprocess_module.MainComputer, "build", lambda config: computer)
    monkeypatch.setattr(subprocess_module, "ai_control_prompt_text", lambda key, text: text)

    command = {
        "run_id": "chat-ai-result-regression",
        "source": "Reply OK",
        "attachments": [],
        "config": {"workspace": str(tmp_path)},
        "thread_id": "thread-chat-test",
    }
    if scoped:
        command["scoped_context"] = {"text": "editor scope", "label": "editor"}

    payload = subprocess_module._run_chat_console_ai_child(
        command, CaptureStdout(), log_file=str(tmp_path / "session.log")
    )

    assert payload == {
        "response": {
            "content": "OK",
            "provider": "ollama",
            "model": "gemma4:26b",
            "metadata": {"done_reason": "stop"},
        }
    }
    assert provider.stream_callback is original_callback
    assert any(
        item.get("type") == "activity"
        and item.get("event", {}).get("title") == "Model text transmitted"
        for item in emitted
    )
    assert any(
        item.get("type") == "activity"
        and item.get("event", {}).get("title") == "AI notebook subprocess completed"
        for item in emitted
    )
    assert all(item.get("type") != "result" for item in emitted)
    assert any(getattr(message, "role", "") == "user" for message in provider.messages)
