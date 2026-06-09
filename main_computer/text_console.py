from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import hashlib
import json
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.router import MainComputer, SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT


TEXT_CONSOLE_AI_SYSTEM_PROMPT = """You are writing into the Main Computer text console.

The text console is rooted at the current local working directory supplied in
the deterministic context pack. Use that context as the local project ground
truth.

The text console supports preview-only local computer mount requests.

A preview-only computer mount request is not execution. When the user explicitly
asks for a computer mount, Terminal mount, Terminal request, or asks you to
request Terminal to run/list/check something, respond with normal assistant
text when useful and include a fenced block tagged exactly as computer.

Inside a computer fence, write one or more exact /act lines only. A single /act
line is one preview mount request. Multiple /act lines in one computer fence are
a sequential plan request.

Use terminal commands that are reviewable. Do not claim that any command,
snippet, mount request, or Terminal action was executed unless the user actually
ran it and provided the result.

Do not say the Terminal or computer-mount interface is unavailable merely
because execution is preview-only. The correct behavior is to request the
preview mount with a computer fence.
"""


@dataclass(frozen=True)
class TextConsoleConfig:
    """Runtime context owner for the text console.

    MainComputerConfig remains a legacy/global config. Text-console context is
    rooted by current_directory/context_root/working_directory instead of by the
    legacy MAIN_COMPUTER_WORKSPACE default.
    """

    current_directory: Path
    context_root: Path
    working_directory: Path
    provider: str
    model: str
    ollama_base_url: str
    ollama_timeout_s: float
    ollama_think: bool | str | None = None

    @classmethod
    def from_current_directory(
        cls,
        current_directory: str | Path,
        *,
        provider: str,
        model: str,
        base_url: str,
        timeout: float,
        think: bool | str | None = None,
    ) -> "TextConsoleConfig":
        current = Path(current_directory).resolve()
        return cls(
            current_directory=current,
            context_root=current,
            working_directory=current,
            provider=str(provider or "ollama"),
            model=str(model or "gemma4:26b"),
            ollama_base_url=str(base_url or "http://127.0.0.1:11434"),
            ollama_timeout_s=float(timeout or 600.0),
            ollama_think=think,
        )

    @classmethod
    def from_repo_root(
        cls,
        *,
        provider: str = "ollama",
        model: str = "gemma4:26b",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 600.0,
        think: bool | str | None = None,
    ) -> "TextConsoleConfig":
        return cls.from_current_directory(
            Path.cwd(),
            provider=provider,
            model=model,
            base_url=base_url,
            timeout=timeout,
            think=think,
        )

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any] | None,
        *,
        fallback_current_directory: str | Path,
        base_config: MainComputerConfig | None = None,
    ) -> "TextConsoleConfig":
        data = payload if isinstance(payload, dict) else {}
        fallback = Path(fallback_current_directory).resolve()
        current = Path(str(data.get("current_directory") or fallback)).resolve()
        context_root = Path(str(data.get("context_root") or current)).resolve()
        working_directory = Path(str(data.get("working_directory") or current)).resolve()
        return cls(
            current_directory=current,
            context_root=context_root,
            working_directory=working_directory,
            provider=str(data.get("provider") or getattr(base_config, "provider", "ollama")),
            model=str(data.get("model") or getattr(base_config, "model", "gemma4:26b")),
            ollama_base_url=str(
                data.get("ollama_base_url")
                or getattr(base_config, "ollama_base_url", "http://127.0.0.1:11434")
            ),
            ollama_timeout_s=float(
                data.get("ollama_timeout_s")
                or getattr(base_config, "ollama_timeout_s", 600.0)
                or 600.0
            ),
            ollama_think=data.get("ollama_think", getattr(base_config, "ollama_think", None)),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "current_directory": str(self.current_directory),
            "context_root": str(self.context_root),
            "working_directory": str(self.working_directory),
            "provider": self.provider,
            "model": self.model,
            "ollama_base_url": self.ollama_base_url,
            "ollama_timeout_s": self.ollama_timeout_s,
            "ollama_think": self.ollama_think,
        }

    def validate_repo_root(self) -> list[str]:
        failures: list[str] = []
        if not self.current_directory.exists():
            failures.append(f"current_directory does not exist: {self.current_directory}")
        if not self.context_root.exists():
            failures.append(f"context_root does not exist: {self.context_root}")
        if not self.working_directory.exists():
            failures.append(f"working_directory does not exist: {self.working_directory}")
        if self.current_directory != self.context_root:
            failures.append(
                "current_directory and context_root differ; this smoke/adoption phase assumes the text console "
                f"is run from repo root: current_directory={self.current_directory}, context_root={self.context_root}"
            )
        if self.working_directory != self.context_root:
            failures.append(
                "working_directory and context_root differ; this smoke/adoption phase assumes one repo-root working "
                f"directory: working_directory={self.working_directory}, context_root={self.context_root}"
            )
        if self.context_root.name.lower() == "dsl":
            failures.append(f"context_root is the legacy dsl fallback: {self.context_root}")
        return failures

    def to_legacy_main_computer_config(
        self,
        config_type: type[MainComputerConfig] = MainComputerConfig,
        *,
        base_config: MainComputerConfig | None = None,
    ) -> MainComputerConfig:
        if base_config is not None:
            return replace(
                base_config,
                workspace=self.context_root,
                provider=self.provider,
                model=self.model,
                ollama_base_url=self.ollama_base_url,
                ollama_timeout_s=self.ollama_timeout_s,
                ollama_think=self.ollama_think,
            )
        return config_type(
            workspace=self.context_root,
            provider=self.provider,
            model=self.model,
            ollama_base_url=self.ollama_base_url,
            ollama_timeout_s=self.ollama_timeout_s,
            ollama_think=self.ollama_think,
        )


@dataclass(frozen=True)
class TextConsoleModelInput:
    text_console_config: TextConsoleConfig
    legacy_config: MainComputerConfig
    computer: MainComputer
    context_pack: Any
    web_search_context: dict[str, Any]
    web_search_text: str
    messages: list[ChatMessage]

    @property
    def input_chars(self) -> int:
        return sum(len(str(message.content or "")) for message in self.messages)

    @property
    def request_payload(self) -> dict[str, Any]:
        return text_console_request_payload(
            self.messages,
            model=self.text_console_config.model,
            think=self.text_console_config.ollama_think,
        )

    @property
    def request_sha256(self) -> str:
        return text_console_request_sha256(
            self.messages,
            model=self.text_console_config.model,
            think=self.text_console_config.ollama_think,
        )


def text_console_request_payload(
    messages: list[ChatMessage],
    *,
    model: str,
    think: bool | str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": item.role, "content": item.content} for item in messages],
        "stream": True,
    }
    if think is not None:
        payload["think"] = think
    return payload


def text_console_request_sha256(
    messages: list[ChatMessage],
    *,
    model: str,
    think: bool | str | None,
) -> str:
    payload = text_console_request_payload(messages, model=model, think=think)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_console_request_bytes(
    messages: list[ChatMessage],
    *,
    model: str,
    think: bool | str | None,
) -> int:
    payload = text_console_request_payload(messages, model=model, think=think)
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def build_text_console_model_input(
    *,
    text_console_config: TextConsoleConfig,
    source: str,
    base_config: MainComputerConfig | None = None,
) -> TextConsoleModelInput:
    legacy_config = text_console_config.to_legacy_main_computer_config(
        MainComputerConfig,
        base_config=base_config,
    )
    computer = MainComputer.build(legacy_config)
    context_pack = computer.context_pack(source)
    web_search_context, web_search_text = computer._web_search_context(source)
    messages = [
        ChatMessage(role="system", content=ROUTER_SYSTEM_PROMPT),
        ChatMessage(role="system", content=context_pack.text),
        *([ChatMessage(role="system", content=web_search_text)] if web_search_text else []),
        ChatMessage(role="system", content=TEXT_CONSOLE_AI_SYSTEM_PROMPT),
        ChatMessage(role="user", content=str(source or "")),
    ]
    return TextConsoleModelInput(
        text_console_config=text_console_config,
        legacy_config=legacy_config,
        computer=computer,
        context_pack=context_pack,
        web_search_context=web_search_context,
        web_search_text=web_search_text,
        messages=messages,
    )


def chat_response_from_text_console_model_input(model_input: TextConsoleModelInput) -> ChatResponse:
    provider_response = model_input.computer.provider.chat(model_input.messages)
    context_pack = model_input.context_pack
    return ChatResponse(
        content=provider_response.content,
        provider=provider_response.provider,
        model=provider_response.model,
        metadata={
            **provider_response.metadata,
            "workspace_context": {
                "manifest_chars": int(getattr(context_pack, "manifest_chars", 0) or 0),
                "evidence": [asdict(item) for item in list(getattr(context_pack, "evidence", ()) or ())],
            },
            "web_search": model_input.web_search_context,
            "text_console_config": model_input.text_console_config.to_payload(),
            "legacy_main_computer_config_adapter": {
                "workspace": str(getattr(model_input.legacy_config, "workspace", "")),
                "provider": str(getattr(model_input.legacy_config, "provider", "")),
                "model": str(getattr(model_input.legacy_config, "model", "")),
                "ollama_base_url": str(getattr(model_input.legacy_config, "ollama_base_url", "")),
                "ollama_timeout_s": getattr(model_input.legacy_config, "ollama_timeout_s", None),
                "ollama_think": getattr(model_input.legacy_config, "ollama_think", None),
            },
            "text_console_model_input": {
                "message_count": len(model_input.messages),
                "message_chars": [len(str(item.content or "")) for item in model_input.messages],
                "input_chars": model_input.input_chars,
                "request_sha256": model_input.request_sha256,
            },
        },
    )


def run_text_console_chat(
    *,
    text_console_config: TextConsoleConfig,
    prompt: str,
    base_config: MainComputerConfig | None = None,
) -> ChatResponse:
    model_input = build_text_console_model_input(
        text_console_config=text_console_config,
        source=prompt,
        base_config=base_config,
    )
    return chat_response_from_text_console_model_input(model_input)
