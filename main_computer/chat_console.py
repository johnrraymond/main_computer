from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from main_computer.models import ChatAttachment, ChatMessage, ChatResponse
from main_computer.output_snippets import parse_fenced_code_snippets


CHAT_CONSOLE_CELL_TYPES = {"ai", "javascript", "python", "basic", "terminal", "mathics", "comment", "output"}
CHAT_CONSOLE_INPUT_TYPES = {"ai", "javascript", "python", "basic", "terminal", "mathics", "comment"}
SUPPORTED_OUTPUT_PART_KINDS = {
    "markdown",
    "text",
    "code",
    "json",
    "terminal",
    "stdout",
    "stderr",
    "mathics",
    "table",
    "plot",
    "image",
    "file",
    "warning",
    "error",
    "action",
}

NOTEBOOK_AI_SYSTEM_PROMPT = """You are writing into a typed notebook console.

Your answer will be rendered in an output cell. Output cells can decorate
reusable snippets with promote buttons.

When you produce reusable code, commands, Mathics expressions, prompts, or
notes, wrap them in fenced code blocks with accurate language tags.

Use:
- javascript or js for runnable JavaScript snippets.
- python or py for runnable Python snippets.
- basic or bas for runnable BASIC snippets.
- mathics, wolfram, or wl for Mathics/Wolfram Language snippets.
- powershell, pwsh, shell, bash, cmd, or terminal for terminal commands.
- prompt or ai for reusable AI prompts.

Mathics/Wolfram snippets must be complete standalone inputs that can be
pasted directly into a Mathics cell and evaluated.

For Mathics/Wolfram snippets:
- Use canonical Wolfram/Mathics capitalization.
- Use Sin[x], Cos[x], Tan[x], Exp[x], Log[x], Sqrt[x].
- Use Integrate[expr, x], D[expr, x], Simplify[expr], FullSimplify[expr].
- Use square brackets for function calls.
- Use * or spaces for multiplication where needed.
- Do not use lowercase function names such as sin[x], cos[x], tan[x].
- Do not put prose, Markdown, LaTeX dollar math, or comments inside mathics code fences.
- Put explanation outside the code fence.
- If the user asks for a mathematical computation, include the runnable Mathics expression as a fenced mathics block when useful.

JavaScript, Python, and BASIC snippets should be complete standalone code
cells. When a value must survive across code-cell languages, store it in the
shared variable context: JavaScript can use vars.name or context.set("name",
value), Python can assign locals or vars["name"], and BASIC can use GETVAR and
SETVAR.

Terminal snippets must be reviewable commands only. Do not claim they were
executed. Do not instruct the system to auto-run terminal commands.

Do not claim that any snippet was executed unless the user actually ran an
evaluation cell and provided the result."""


def now_ms() -> int:
    return int(time.time() * 1000)


def source_hash(source: str) -> str:
    return hashlib.sha256(str(source or "").encode("utf-8")).hexdigest()


def normalize_cell_type(value: str) -> str:
    cell_type = str(value or "").strip().lower()
    if cell_type not in CHAT_CONSOLE_CELL_TYPES:
        raise ValueError(f"Unsupported chat console cell type: {cell_type or '(empty)'}")
    return cell_type


def validate_evaluation_cell(cell: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(cell, dict):
        raise ValueError("Cell payload is required.")
    cell_type = normalize_cell_type(str(cell.get("type", "")))
    if cell_type == "output":
        raise ValueError("Output cells do not evaluate.")
    if cell_type == "comment":
        raise ValueError("Comment cells do not evaluate.")
    source = str(cell.get("source", "") or "").strip()
    if not source:
        raise ValueError(f"{cell_type.capitalize()} cell source is required.")
    if len(source) > 4000 and cell_type in {"mathics", "terminal"}:
        raise ValueError(f"{cell_type.capitalize()} cell source is limited to 4000 characters.")
    return cell_type, source


def attachment_from_payload(payload: dict[str, Any]) -> ChatAttachment | None:
    if not isinstance(payload, dict):
        return None
    data = str(payload.get("data_base64", "") or "")
    if not data:
        return None
    return ChatAttachment(
        id=str(payload.get("id", "") or ""),
        filename=Path(str(payload.get("filename", "") or "attachment")).name,
        mime_type=str(payload.get("mime_type", "") or "application/octet-stream"),
        data_base64=data,
        kind=str(payload.get("kind", "") or "file"),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def build_notebook_ai_messages(source: str, attachments: list[dict[str, Any]] | None = None) -> list[ChatMessage]:
    chat_attachments = [item for item in (attachment_from_payload(payload) for payload in attachments or []) if item]
    return [
        ChatMessage(role="system", content=NOTEBOOK_AI_SYSTEM_PROMPT),
        ChatMessage(role="user", content=str(source or ""), attachments=chat_attachments),
    ]


def output_part(kind: str, title: str, content: Any, **extra: Any) -> dict[str, Any]:
    if kind not in SUPPORTED_OUTPUT_PART_KINDS:
        raise ValueError(f"Unsupported output part kind: {kind}")
    part = {
        "id": f"part-{source_hash(f'{kind}:{title}:{content}')[:12]}",
        "kind": kind,
        "title": title,
        "content": content,
        "language": extra.pop("language", ""),
        "metadata": extra.pop("metadata", {}),
        "snippets": extra.pop("snippets", []),
    }
    part.update(extra)
    return part


def ai_response_to_parts(response: ChatResponse) -> list[dict[str, Any]]:
    snippets = parse_fenced_code_snippets(response.content)
    parts: list[dict[str, Any]] = [
        output_part(
            "markdown",
            "AI response",
            response.content,
            snippets=snippets,
            metadata={"provider": response.provider, "model": response.model},
        )
    ]
    return parts


def terminal_result_to_parts(result: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [
        output_part("terminal", "Command", str(result.get("command", "")), metadata={"cwd": str(result.get("cwd", ""))}),
        output_part("stdout", "stdout", str(result.get("stdout", ""))),
    ]
    stderr = str(result.get("stderr", "") or "")
    if stderr:
        parts.append(output_part("stderr", "stderr", stderr))
    metadata = {
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "timed_out": bool(result.get("timed_out")),
        "cwd": result.get("cwd"),
    }
    if result.get("exit_code") not in {0, "0"}:
        parts.append(output_part("warning", "Terminal warning", f"Command exited with {result.get('exit_code')}", metadata=metadata))
    else:
        parts[0]["metadata"].update(metadata)
    return parts


def mathics_result_to_parts(result: dict[str, Any], expression: str) -> list[dict[str, Any]]:
    if result.get("ok"):
        parts: list[dict[str, Any]] = []
        outputs = result.get("outputs") if isinstance(result.get("outputs"), list) else []
        if outputs:
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind", "") or "mathics")
                if kind in {"graphics", "graphics3d", "plot"}:
                    mime_type = str(item.get("mime_type", "") or "image/svg+xml")
                    data_base64 = str(item.get("data_base64", "") or "")
                    data_url = f"data:{mime_type};base64,{data_base64}" if data_base64 else ""
                    parts.append(
                        output_part(
                            "plot",
                            "Mathics graphics",
                            str(item.get("text", "") or ""),
                            metadata={
                                "mime_type": mime_type,
                                "data_url": data_url,
                                "expression": expression,
                                **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
                            },
                        )
                    )
                elif kind == "warning":
                    parts.append(output_part("warning", "Mathics warning", str(item.get("text", "")), metadata={"expression": expression}))
                elif kind == "error":
                    parts.append(output_part("error", "Mathics error", str(item.get("text", "")), metadata={"expression": expression}))
                elif item.get("text") is not None:
                    parts.append(output_part("mathics", "Mathics result", str(item.get("text", "")), metadata={"expression": expression, **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})}))
            for warning in result.get("warnings", []) or []:
                parts.append(output_part("warning", "Mathics warning", str(warning), metadata={"expression": expression}))
            for error in result.get("errors", []) or []:
                parts.append(output_part("error", "Mathics error", str(error), metadata={"expression": expression}))
            return parts or [output_part("mathics", "Mathics result", "", metadata={"expression": expression, "messages": result.get("messages", [])})]
        result_text = str(result.get("result_text", "") or "")
        if result_text:
            parts.append(output_part("mathics", "Mathics result", result_text, metadata={"expression": expression, "messages": result.get("messages", [])}))
        for graphic in result.get("graphics", []) or []:
            if not isinstance(graphic, dict):
                continue
            mime_type = str(graphic.get("mime_type", "") or "image/svg+xml")
            data_base64 = str(graphic.get("data_base64", "") or "")
            data_url = f"data:{mime_type};base64,{data_base64}" if data_base64 else ""
            parts.append(
                output_part(
                    "plot",
                    "Mathics graphics",
                    graphic.get("text_fallback", "") or "",
                    metadata={
                        "mime_type": mime_type,
                        "data_url": data_url,
                        "expression": expression,
                        "graphics_id": graphic.get("id", ""),
                        **(graphic.get("metadata") if isinstance(graphic.get("metadata"), dict) else {}),
                    },
                )
            )
        for warning in result.get("warnings", []) or []:
            parts.append(output_part("warning", "Mathics warning", str(warning), metadata={"expression": expression}))
        for error in result.get("errors", []) or []:
            parts.append(output_part("error", "Mathics error", str(error), metadata={"expression": expression}))
        return parts or [output_part("mathics", "Mathics result", "", metadata={"expression": expression, "messages": result.get("messages", [])})]
    return [
        output_part("error", "Mathics error", str(result.get("error", "Mathics evaluation failed.")), metadata={"expression": expression, "messages": result.get("messages", [])})
    ]


def build_output_cell(source_cell: dict[str, Any], parts: list[dict[str, Any]], status: str = "ok", provider: str = "", model: str = "") -> dict[str, Any]:
    source_cell_id = str(source_cell.get("id", "") or f"cell-{now_ms()}")
    created = now_ms()
    return {
        "id": f"out-{source_cell_id}-{created}",
        "type": "output",
        "source_cell_id": source_cell_id,
        "variant_group_id": f"variants-{source_cell_id}",
        "variant_index": int(source_cell.get("variant_index", 0) or 0),
        "parts": parts,
        "status": status,
        "provider": provider,
        "model": model,
        "created_at": created,
        "updated_at": created,
        "provenance": {
            "source_cell_id": source_cell_id,
            "source_cell_type": source_cell.get("type", ""),
            "source_cell_source_hash": source_hash(str(source_cell.get("source", "") or "")),
            "variant_index": int(source_cell.get("variant_index", 0) or 0),
            "created_at": created,
        },
    }
