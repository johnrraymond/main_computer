from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import hashlib
import json
import re
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



ACTION_SPEC_DIR = Path("main_computer") / "action_specs"

REPO_EDIT_FENCE_RE = re.compile(
    r"```[ \t]*(?:repo-edit|repo_edit)[^\n]*\n(?P<body>.*?)\n?[ \t]*```",
    re.IGNORECASE | re.DOTALL,
)

ACTION_PREFLIGHT_PROMPT = """\
You are the Main Computer text-console action-context preflight.

Your job is to decide which available action specs are relevant to the user's
request. You do not execute commands. You do not create edits. You do not emit
/act lines. You only choose context for a later assistant call.

Return JSON only, with this exact shape:

{
  "needs_mount": false,
  "needs_edit": false,
  "needs_answer_only": true,
  "selected_spec_ids": [],
  "reason": "<brief reason>"
}

Rules:
- Select only spec ids that appear in the available action spec catalog.
- Select terminal when the user asks for Terminal, shell commands, Git, tests,
  file listings, directory listings, command execution, interruption, or active
  terminal reuse.
- Select repo_edit when the user asks to edit, modify, update, create, delete,
  refactor, patch, or otherwise change repository files.
- Select both terminal and repo_edit when the user asks to inspect/run/test and
  prepare an edit in the same request.
- Select no specs for greetings, ordinary explanations, and workspace questions
  that do not ask for a local action or edit.
- Do not infer hidden intent. If the user asks you to explain a command without
  using it, do not select a mount spec.
"""

FINAL_OPERATOR_PROMPT = """\
You are the Main Computer text-console operator.

The previous action-context preflight selected the capability specs provided
below. Treat those specs as the only executable/edit affordance context for this
answer.

Rules:
- It is valid to answer with normal assistant prose only when no action or edit
  is requested.
- If a Terminal preview is requested, use a fenced block tagged exactly
  computer and put only exact /act lines inside it.
- If a repo edit is requested, use a fenced block tagged exactly repo-edit and
  put exactly one JSON object inside it.
- If the user asks for both a mount and an edit, include both blocks in one
  assistant message.
- Preview mounts are not execution. Repo-edit handoffs are not applied edits.
- Do not claim commands ran, files changed, tests passed, or commits happened.
- Do not invent capabilities that were not selected by the preflight.
"""


@dataclass(frozen=True)
class ActionSpec:
    spec_id: str
    app_id: str
    title: str
    keywords: tuple[str, ...]
    output_kinds: tuple[str, ...]
    path: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def runtime_context_text(self) -> str:
        runtime = extract_markdown_section(self.text, "Runtime prompt")
        return runtime if runtime else compact_action_spec_text(self.text)

    @property
    def runtime_sha256(self) -> str:
        return hashlib.sha256(self.runtime_context_text.encode("utf-8")).hexdigest()

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "app_id": self.app_id,
            "title": self.title,
            "keywords": list(self.keywords),
            "output_kinds": list(self.output_kinds),
            "path": self.path,
            "sha256": self.sha256,
            "runtime_sha256": self.runtime_sha256,
        }


@dataclass(frozen=True)
class TextConsoleOperatorRun:
    base_model_input: TextConsoleModelInput
    action_specs: dict[str, ActionSpec]
    preflight_messages: list[ChatMessage]
    preflight_content: str
    preflight_metadata: dict[str, Any]
    preflight_payload: dict[str, Any]
    selected_spec_ids: list[str]
    selected_spec_notes: list[str]
    final_messages: list[ChatMessage]
    final_content: str
    final_provider: str
    final_model: str
    final_metadata: dict[str, Any]

    @property
    def final_request_sha256(self) -> str:
        return text_console_request_sha256(
            self.final_messages,
            model=self.base_model_input.text_console_config.model,
            think=self.base_model_input.text_console_config.ollama_think,
        )

    @property
    def preflight_request_sha256(self) -> str:
        return text_console_request_sha256(
            self.preflight_messages,
            model=self.base_model_input.text_console_config.model,
            think=self.base_model_input.text_console_config.ollama_think,
        )


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    raw = str(text or "")
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end < 0:
        return {}, raw
    header = raw[4:end].strip()
    body = raw[end + len("\n---") :].lstrip("\r\n")
    meta: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, body


def split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def extract_markdown_section(text: str, heading: str) -> str:
    wanted = str(heading or "").strip().lower()
    current: list[str] = []
    in_section = False
    heading_re = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
    for line in str(text or "").splitlines():
        match = heading_re.match(line)
        if match:
            title = match.group(2).strip().lower()
            if in_section:
                break
            in_section = title == wanted
            continue
        if in_section:
            current.append(line)
    return "\n".join(current).strip()


def compact_action_spec_text(text: str, *, limit: int = 1200) -> str:
    _meta, body = parse_front_matter(text)
    body = re.sub(r"```[^`]*```", lambda match: match.group(0)[:400], body, flags=re.DOTALL)
    compact = re.sub(r"\n{3,}", "\n\n", body).strip()
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "\n..."


def load_action_specs(root: Path, *, spec_dir: Path = ACTION_SPEC_DIR) -> dict[str, ActionSpec]:
    directory = root / spec_dir
    if not directory.exists():
        raise RuntimeError(f"Action spec directory does not exist: {directory}")

    specs: dict[str, ActionSpec] = {}
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, _body = parse_front_matter(text)
        spec_id = str(meta.get("spec_id") or path.stem).strip()
        app_id = str(meta.get("app_id") or spec_id).strip()
        title = str(meta.get("title") or spec_id).strip()
        spec = ActionSpec(
            spec_id=spec_id,
            app_id=app_id,
            title=title,
            keywords=split_csv(meta.get("keywords", "")),
            output_kinds=split_csv(meta.get("output_kinds", "")),
            path=path.relative_to(root).as_posix(),
            text=text,
        )
        if spec.spec_id in specs:
            raise RuntimeError(f"Duplicate action spec id {spec.spec_id!r}: {path}")
        specs[spec.spec_id] = spec

    if not specs:
        raise RuntimeError(f"No action specs found in {directory}")
    return specs


def action_spec_catalog_prompt(specs: dict[str, ActionSpec]) -> str:
    return (
        "Available text-console action spec catalog:\n\n"
        + json.dumps([spec.catalog_entry() for spec in specs.values()], indent=2, ensure_ascii=False, sort_keys=True)
    )


def selected_action_specs_prompt(specs: dict[str, ActionSpec], selected_spec_ids: list[str]) -> str:
    chunks: list[str] = []
    for spec_id in selected_spec_ids:
        spec = specs[spec_id]
        chunks.append(
            f"Selected text-console action spec: {spec.spec_id}\n"
            f"title: {spec.title}\n"
            f"path: {spec.path}\n"
            f"spec_sha256: {spec.sha256}\n"
            f"runtime_sha256: {spec.runtime_sha256}\n\n"
            f"{spec.runtime_context_text.strip()}"
        )
    if not chunks:
        return "No action specs were selected. Answer normally without computer or repo-edit blocks unless the user corrects the request."
    return "\n\n---\n\n".join(chunks)


def strip_json_code_fence(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def parse_jsonish(text: str) -> dict[str, Any]:
    raw = strip_json_code_fence(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


def compact_context_pack_text(text: str, *, max_chars: int = 2200, max_manifest_lines: int = 80) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw

    out: list[str] = []
    manifest_lines = 0
    in_manifest = False
    dropped_sections = {
        "Matched file excerpts:",
        "Pinned guidance excerpts:",
    }

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped in dropped_sections:
            out.append(f"{stripped} [omitted from operator action-RAG compact context]")
            break

        if stripped == "Main computer file manifest:":
            in_manifest = True
            out.append(line)
            continue

        if stripped.endswith(":") and stripped != "Main computer file manifest:":
            in_manifest = False

        if in_manifest and line.startswith("  - "):
            manifest_lines += 1
            if manifest_lines > max_manifest_lines:
                out.append(f"  - ... [{manifest_lines - 1}+ manifest entries truncated]")
                break

        out.append(line)
        if len("\n".join(out)) >= max_chars:
            out.append("... [context truncated for operator action-RAG]")
            break

    compact = "\n".join(out).strip()
    return compact if compact else raw[:max_chars].rstrip() + "\n... [context truncated for operator action-RAG]"


def compact_model_messages_for_operator(model_input: TextConsoleModelInput) -> list[ChatMessage]:
    messages = list(model_input.messages)
    context_text = str(getattr(model_input.context_pack, "text", "") or "")
    compact_context = compact_context_pack_text(context_text)
    compacted: list[ChatMessage] = []
    replaced_context = False
    for index, message in enumerate(messages):
        if not replaced_context and index == 1 and message.role == "system":
            compacted.append(ChatMessage(role="system", content=compact_context))
            replaced_context = True
        else:
            compacted.append(message)
    return compacted


def build_action_preflight_messages(
    *,
    model_input: TextConsoleModelInput,
    specs: dict[str, ActionSpec],
    request_text: str,
) -> list[ChatMessage]:
    config = model_input.text_console_config
    root_hint = (
        "Text-console runtime root hint:\n"
        f"- current_directory: {config.current_directory}\n"
        f"- context_root: {config.context_root}\n"
        f"- working_directory: {config.working_directory}"
    )
    return [
        ChatMessage(role="system", content=ACTION_PREFLIGHT_PROMPT),
        ChatMessage(role="system", content=action_spec_catalog_prompt(specs)),
        ChatMessage(role="system", content=root_hint),
        ChatMessage(role="user", content=str(request_text or "")),
    ]


def normalize_selected_spec_ids(
    payload: dict[str, Any],
    *,
    available_spec_ids: set[str],
) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    selected = payload.get("selected_spec_ids")
    if not isinstance(selected, list):
        notes.append("preflight selected_spec_ids was not a list; using no selected specs")
        return [], notes

    selected_ids: list[str] = []
    for item in selected:
        spec_id = str(item).strip()
        if not spec_id:
            continue
        if spec_id not in available_spec_ids:
            notes.append(f"preflight selected unknown spec {spec_id!r}; ignoring it")
            continue
        if spec_id not in selected_ids:
            selected_ids.append(spec_id)
    return selected_ids, notes


def build_operator_final_messages(
    *,
    model_input: TextConsoleModelInput,
    specs: dict[str, ActionSpec],
    selected_spec_ids: list[str],
) -> list[ChatMessage]:
    base_messages = compact_model_messages_for_operator(model_input)
    inserted = [
        ChatMessage(role="system", content=FINAL_OPERATOR_PROMPT),
        ChatMessage(role="system", content=selected_action_specs_prompt(specs, selected_spec_ids)),
    ]
    if base_messages and base_messages[-1].role == "user":
        return [*base_messages[:-1], *inserted, base_messages[-1]]
    return [*base_messages, *inserted]


def run_text_console_operator(
    *,
    text_console_config: TextConsoleConfig,
    prompt: str,
    base_config: MainComputerConfig | None = None,
) -> TextConsoleOperatorRun:
    base_model_input = build_text_console_model_input(
        text_console_config=text_console_config,
        source=prompt,
        base_config=base_config,
    )
    specs = load_action_specs(text_console_config.context_root)
    preflight_messages = build_action_preflight_messages(
        model_input=base_model_input,
        specs=specs,
        request_text=prompt,
    )
    preflight_response = base_model_input.computer.provider.chat(preflight_messages)
    preflight_content = str(getattr(preflight_response, "content", "") or "")
    preflight_metadata = dict(getattr(preflight_response, "metadata", {}) or {})

    selected_notes: list[str] = []
    try:
        preflight_payload = parse_jsonish(preflight_content)
    except Exception as exc:
        preflight_payload = {
            "needs_mount": False,
            "needs_edit": False,
            "needs_answer_only": True,
            "selected_spec_ids": [],
            "reason": f"preflight JSON parse failed: {exc!r}",
        }
        selected_notes.append(f"preflight JSON parse failed: {exc!r}")

    selected_spec_ids, normalize_notes = normalize_selected_spec_ids(
        preflight_payload,
        available_spec_ids=set(specs),
    )
    selected_notes.extend(normalize_notes)

    final_messages = build_operator_final_messages(
        model_input=base_model_input,
        specs=specs,
        selected_spec_ids=selected_spec_ids,
    )
    final_response = base_model_input.computer.provider.chat(final_messages)

    return TextConsoleOperatorRun(
        base_model_input=base_model_input,
        action_specs=specs,
        preflight_messages=preflight_messages,
        preflight_content=preflight_content,
        preflight_metadata=preflight_metadata,
        preflight_payload=preflight_payload,
        selected_spec_ids=selected_spec_ids,
        selected_spec_notes=selected_notes,
        final_messages=final_messages,
        final_content=str(getattr(final_response, "content", "") or ""),
        final_provider=str(getattr(final_response, "provider", base_model_input.computer.provider.name) or ""),
        final_model=str(getattr(final_response, "model", base_model_input.computer.provider.model) or ""),
        final_metadata=dict(getattr(final_response, "metadata", {}) or {}),
    )


def chat_response_from_text_console_operator_run(run: TextConsoleOperatorRun) -> ChatResponse:
    model_input = run.base_model_input
    context_pack = model_input.context_pack
    return ChatResponse(
        content=run.final_content,
        provider=run.final_provider,
        model=run.final_model,
        metadata={
            **run.final_metadata,
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
            "text_console_operator": {
                "action_specs": [spec.catalog_entry() for spec in run.action_specs.values()],
                "selected_spec_ids": run.selected_spec_ids,
                "selected_spec_notes": run.selected_spec_notes,
                "preflight": {
                    "content": run.preflight_content,
                    "payload": run.preflight_payload,
                    "message_count": len(run.preflight_messages),
                    "message_chars": [len(str(item.content or "")) for item in run.preflight_messages],
                    "request_sha256": run.preflight_request_sha256,
                    "metadata": run.preflight_metadata,
                },
                "final": {
                    "message_count": len(run.final_messages),
                    "message_chars": [len(str(item.content or "")) for item in run.final_messages],
                    "request_sha256": run.final_request_sha256,
                },
            },
        },
    )


def run_text_console_operator_chat(
    *,
    text_console_config: TextConsoleConfig,
    prompt: str,
    base_config: MainComputerConfig | None = None,
) -> ChatResponse:
    run = run_text_console_operator(
        text_console_config=text_console_config,
        prompt=prompt,
        base_config=base_config,
    )
    return chat_response_from_text_console_operator_run(run)


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
