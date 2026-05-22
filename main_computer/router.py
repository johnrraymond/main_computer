from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from main_computer.ai_web_search import (
    DEFAULT_WEB_SEARCH_LABEL,
    DEFAULT_WEB_SEARCH_MAX_RESULTS,
    DEFAULT_WEB_SEARCH_MODE,
    DEFAULT_WEB_SEARCH_PROVIDER,
    WebSearchFn,
    build_ai_web_search_context,
    format_ai_web_search_context,
)
from main_computer.catalog import ProjectCatalog, WorkspaceContextPack
from main_computer.chat_console import build_notebook_ai_messages
from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_trust_contract_chat import (
    chat_response_from_trust_result,
    run_trust_contract_chat_request,
)
from main_computer.providers import HubProvider, LLMProvider, OllamaProvider, OpenAIProvider


SYSTEM_PROMPT = """You are Main Computer, the central local AI layer for this Windows workspace.
Use the workspace map as grounding context.
When the user asks about projects, refer to known local project names.
Be direct about provider limits: you can reason over the project map now, and deeper file or command access should be exposed as explicit tools in future versions."""

TERMINAL_SUGGESTION_SYSTEM_PROMPT = """You translate user requests into one PowerShell command for the Main Computer terminal.
Return JSON only. Do not include Markdown. Do not execute anything.
The user will review the command before pressing Enter.
Prefer safe/read-only commands when possible.
If the request is ambiguous, return the safest useful command."""


@dataclass
class MainComputer:
    config: MainComputerConfig
    catalog: ProjectCatalog
    provider: LLMProvider
    web_search_fn: WebSearchFn | None = None
    web_search_max_results: int = DEFAULT_WEB_SEARCH_MAX_RESULTS
    web_search_provider: str = DEFAULT_WEB_SEARCH_PROVIDER
    web_search_mode: str = DEFAULT_WEB_SEARCH_MODE
    web_search_label: str = DEFAULT_WEB_SEARCH_LABEL

    @classmethod
    def build(cls, config: MainComputerConfig | None = None) -> "MainComputer":
        config = config or MainComputerConfig.from_env()
        catalog = ProjectCatalog(config.workspace)

        if config.provider == "ollama":
            provider = OllamaProvider(
                model=config.model,
                base_url=config.ollama_base_url,
                timeout_s=config.ollama_timeout_s,
                think=config.ollama_think,
                fallback=config.fallback,
            )
        elif config.provider == "openai":
            provider = OpenAIProvider(model=config.model, base_url=config.openai_base_url, fallback=config.fallback)
        elif config.provider == "hub":
            provider = HubProvider(
                model=config.model,
                hub_url=config.hub_url,
                timeout_s=config.hub_timeout_s,
                client_node_id=config.hub_client_node_id,
                high_security=config.hub_high_security,
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
                fallback=config.fallback,
            )
        else:
            raise ValueError(f"Unknown provider: {config.provider}")

        return cls(config=config, catalog=catalog, provider=provider)

    def context_pack(self, prompt: str) -> WorkspaceContextPack:
        return self.catalog.build_context_pack(prompt)

    def _web_search_context(self, prompt: str) -> tuple[dict, str]:
        context = build_ai_web_search_context(
            prompt,
            search_fn=self.web_search_fn,
            max_results=self.web_search_max_results,
            provider=self.web_search_provider,
            mode=self.web_search_mode,
            label=self.web_search_label,
        )
        return context.as_dict(), format_ai_web_search_context(context)

    def chat(self, prompt: str, context_pack: WorkspaceContextPack | None = None) -> ChatResponse:
        if self.config.fallback:
            print(f"[fallback][router] chat prompt chars={len(prompt)}", flush=True)
        context_pack = context_pack or self.context_pack(prompt)
        web_search_context, web_search_text = self._web_search_context(prompt)
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(role="system", content=context_pack.text),
            *([ChatMessage(role="system", content=web_search_text)] if web_search_text else []),
            ChatMessage(role="user", content=prompt),
        ]
        if self.config.fallback:
            print(f"[fallback][router] sending {len(messages)} messages to {self.provider.name}/{self.provider.model}", flush=True)
        response = self.provider.chat(messages)
        return ChatResponse(
            content=response.content,
            provider=response.provider,
            model=response.model,
            metadata={
                **response.metadata,
                "workspace_context": {
                    "manifest_chars": context_pack.manifest_chars,
                    "evidence": [asdict(item) for item in context_pack.evidence],
                },
                "web_search": web_search_context,
            },
        )

    def suggest_terminal_command(self, prompt: str, cwd: str = ".") -> ChatResponse:
        if self.config.fallback:
            print(f"[fallback][router] terminal suggestion prompt chars={len(prompt)} cwd={cwd}", flush=True)
        context_pack = self.context_pack(prompt)
        messages = [
            ChatMessage(role="system", content=TERMINAL_SUGGESTION_SYSTEM_PROMPT),
            ChatMessage(role="system", content=context_pack.text),
            ChatMessage(
                role="user",
                content="\n".join(
                    [
                        "Return a JSON object with exactly these keys: command, description, risk.",
                        "Risk must be one of: read-only, write, destructive, network, unknown.",
                        f"Working directory: {cwd}",
                        f"User request: {prompt}",
                    ]
                ),
            ),
        ]
        if self.config.fallback:
            print(f"[fallback][router] sending {len(messages)} messages to {self.provider.name}/{self.provider.model}", flush=True)
        response = self.provider.chat(messages)
        return ChatResponse(
            content=response.content,
            provider=response.provider,
            model=response.model,
            metadata={
                **response.metadata,
                "workspace_context": {
                    "manifest_chars": context_pack.manifest_chars,
                    "evidence": [asdict(item) for item in context_pack.evidence],
                },
            },
        )

    def chat_console_ai(self, source: str, attachments: list[dict] | None = None) -> ChatResponse:
        if self.config.fallback:
            print(f"[fallback][router] chat console source chars={len(source)} attachments={len(attachments or [])}", flush=True)
        context_pack = self.context_pack(source)
        web_search_context, web_search_text = self._web_search_context(source)
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(role="system", content=context_pack.text),
            *([ChatMessage(role="system", content=web_search_text)] if web_search_text else []),
            *build_notebook_ai_messages(source, attachments or []),
        ]
        if self.config.fallback:
            print(f"[fallback][router] sending {len(messages)} messages to {self.provider.name}/{self.provider.model}", flush=True)
        response = self.provider.chat(messages)
        return ChatResponse(
            content=response.content,
            provider=response.provider,
            model=response.model,
            metadata={
                **response.metadata,
                "workspace_context": {
                    "manifest_chars": context_pack.manifest_chars,
                    "evidence": [asdict(item) for item in context_pack.evidence],
                },
                "web_search": web_search_context,
            },
        )

    def rag_trust_contract_chat(self, source: str, attachments: list[dict] | None = None, *, deadline_ms: int = 30_000) -> ChatResponse:
        if self.config.fallback:
            print(f"[fallback][router] trust-contract chat source chars={len(source)} attachments={len(attachments or [])}", flush=True)
        context_pack = self.context_pack(source)
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            *build_notebook_ai_messages(source, attachments or []),
        ]
        evidence: list[dict] = []
        if context_pack.text:
            evidence.append({
                "evidence_id": "workspace_context",
                "source": "workspace_context_pack",
                "text": context_pack.text,
                "trust": "workspace_context",
            })
        for index, item in enumerate(context_pack.evidence):
            evidence.append({
                "evidence_id": f"workspace_file_{index + 1}",
                "source": item.path,
                "text": f"{item.path}\n{item.reason}".strip(),
                "trust": item.kind,
            })
        result = run_trust_contract_chat_request(
            prompt=source,
            messages=messages,
            evidence=evidence,
            provider=self.provider,
            deadline_ms=deadline_ms,
        )
        response = chat_response_from_trust_result(result)
        return ChatResponse(
            content=response.content,
            provider=response.provider,
            model=response.model,
            metadata={
                **response.metadata,
                "workspace_context": {
                    "manifest_chars": context_pack.manifest_chars,
                    "evidence": [asdict(item) for item in context_pack.evidence],
                },
            },
        )
