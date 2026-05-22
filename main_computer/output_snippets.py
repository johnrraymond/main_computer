from __future__ import annotations

import hashlib
import re
from typing import Any


FENCED_CODE_RE = re.compile(r"```([A-Za-z0-9_+.-]*)\s*\n(.*?)```", re.DOTALL)

MATHICS_ALIASES = {"mathics", "wolfram", "wl"}
TERMINAL_ALIASES = {"powershell", "pwsh", "shell", "bash", "cmd", "terminal"}
AI_PROMPT_ALIASES = {"prompt", "ai"}
CODE_CELL_ALIASES = {
    "javascript": "javascript",
    "js": "javascript",
    "python": "python",
    "py": "python",
    "basic": "basic",
    "bas": "basic",
}
GENERIC_CODE_ALIASES = {"typescript", "json", "yaml", "text"}


def classify_snippet_language(language: str) -> tuple[str, list[str]]:
    normalized = str(language or "").strip().lower()
    if normalized in MATHICS_ALIASES:
        return "mathics", ["mathics", "comment"]
    if normalized in TERMINAL_ALIASES:
        return "terminal", ["terminal", "comment"]
    if normalized in AI_PROMPT_ALIASES:
        return "ai_prompt", ["ai", "comment"]
    if normalized in CODE_CELL_ALIASES:
        return "code", [CODE_CELL_ALIASES[normalized], "comment", "ai"]
    if normalized in GENERIC_CODE_ALIASES:
        return "generic_code", ["comment", "ai"]
    return "generic_code", ["comment", "ai"]


def snippet_id(language: str, content: str) -> str:
    digest = hashlib.sha256(f"{language}\0{content}".encode("utf-8")).hexdigest()[:16]
    return f"snippet-{digest}"


def parse_fenced_code_snippets(markdown: str) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for match in FENCED_CODE_RE.finditer(str(markdown or "")):
        language = (match.group(1) or "text").strip().lower()
        content = match.group(2).strip("\n")
        kind, target_types = classify_snippet_language(language)
        snippets.append(
            {
                "id": snippet_id(language, content),
                "kind": kind,
                "language": language or "text",
                "content": content,
                "title": f"{language or 'text'} snippet",
                "suggested_target_cell_types": target_types,
                "metadata": {"auto_promote": False},
            }
        )
    return snippets


def markdown_text_snippet(markdown: str) -> dict[str, Any] | None:
    text = str(markdown or "").strip()
    if not text:
        return None
    return {
        "id": snippet_id("markdown", text),
        "kind": "markdown_or_text",
        "language": "markdown",
        "content": text,
        "title": "note snippet",
        "suggested_target_cell_types": ["comment"],
        "metadata": {"auto_promote": False},
    }
