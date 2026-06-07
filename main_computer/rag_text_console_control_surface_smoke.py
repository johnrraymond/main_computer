#!/usr/bin/env python3
"""
RAG text-console terminal-context smoke test.

Purpose:
  Prove the text console has one action path shared by exact /act commands,
  AI/RAG-decoded natural language, broker-minted mounts, terminal paranoia, and
  context-sensitive terminal reuse.

This smoke intentionally touches Ollama by default for the RAG pathways. It
should fail if Ollama is not reachable, the model is not pulled, or the model
cannot choose the right canonical terminal command from the supplied context.

Core contract:
  - Exact commands run only when they perfectly match the canonical /act grammar.
  - Fuzzy or natural text goes through Ollama /api/chat.
  - The model returns a canonical /act command, not an executable UI action.
  - The trusted broker parses the canonical command, applies paranoia, mints an
    action id, and emits render packets.
  - Terminal "run" is the default when the user asks to run something.
  - Paranoia determines whether a runnable terminal command auto-runs,
    requires confirmation, downgrades to prefill, or is blocked.
  - If terminal context shows the user is already working in a relevant terminal,
    "now run git status" should target and clone that terminal with run-in.
  - If the same request is out of the blue, it should create a new terminal.
  - Assistant prose that merely talks about slash commands remains inert.

Run from repo root:

  python -S main_computer/rag_text_console_control_surface_smoke.py

Useful options:

  python -S main_computer/rag_text_console_control_surface_smoke.py --paranoia normal
  python -S main_computer/rag_text_console_control_surface_smoke.py --contextual-request "now run git status"
  python -S main_computer/rag_text_console_control_surface_smoke.py --offline-contract-only

Environment:
  OLLAMA_BASE_URL=http://127.0.0.1:11434
  OLLAMA_MODEL=gemma4:26b
  MAIN_COMPUTER_GREMLIN_MODEL=gemma4:26b
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TEXT_CONSOLE_SURFACE = "text_console"
DEFAULT_EXACT_COMMAND = "/act file manager show hidden files"
DEFAULT_EXACT_PATH_COMMAND = r"/act file manager change directory C:\Users\\"
DEFAULT_CONTEXTUAL_REQUEST = "now run git status"
DEFAULT_OUT_OF_BLUE_REQUEST = "now run git status"
DEFAULT_PARANOIA = "normal"
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = (
    os.environ.get("OLLAMA_MODEL")
    or os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL")
    or "gemma4:26b"
)

PARANOIA_LEVELS = ("relaxed", "normal", "strict", "locked")


APP_CAPABILITY_REGISTRY: dict[str, Any] = {
    "surface": TEXT_CONSOLE_SURFACE,
    "canonical_command_shape": "/act <app> <action> [arguments] [--option value]",
    "execution_model": {
        "exact_slash_commands": "execute through broker without RAG only when the parser accepts them exactly",
        "fuzzy_requests": "must be decoded by RAG into a canonical command, then parsed by broker",
        "assistant_prose": "inert; slash-looking text in prose never creates broker mounts",
        "terminal_run_default": "when the user clearly asks to run something, decode to /act terminal run ...",
        "terminal_context_reuse": (
            "when open_terminals shows a relevant active terminal, decode follow-up requests like "
            "'now run git status' to /act terminal run-in active ..."
        ),
        "terminal_context_new": (
            "when no relevant terminal context exists, decode the same request to /act terminal run ... "
            "so the broker creates a new terminal session"
        ),
        "paranoia": "broker decides autoRun/confirmation/prefill/block after parsing the canonical command",
    },
    "apps": [
        {
            "app_phrase": "file manager",
            "canonical_app_id": "file_explorer",
            "mount_depth": 4,
            "implemented_commands": [
                {
                    "canonical": "/act file manager show hidden files",
                    "action": "show_hidden_files",
                    "args": {},
                    "options": {"showHiddenFiles": True, "focus": "hidden_files"},
                },
                {
                    "canonical": "/act file manager list directory <path>",
                    "action": "list_directory",
                    "args": {"path": "path"},
                },
                {
                    "canonical": "/act file manager change directory <path>",
                    "action": "change_directory",
                    "args": {"path": "path"},
                },
            ],
        },
        {
            "app_phrase": "terminal",
            "canonical_app_id": "terminal",
            "mount_depth": 4,
            "implemented_commands": [
                {
                    "canonical": "/act terminal mount",
                    "action": "mount_terminal",
                    "auto_run": False,
                },
                {
                    "canonical": "/act terminal prefill <command> [--cwd repo-root]",
                    "action": "prefill_command",
                    "auto_run": False,
                },
                {
                    "canonical": "/act terminal run <command> [--cwd repo-root]",
                    "action": "run_command",
                    "auto_run": "depends_on_paranoia",
                    "terminal_session": "create new terminal session",
                },
                {
                    "canonical": "/act terminal run-in <terminal-ref> <command> [--cwd repo-root]",
                    "action": "run_in_terminal",
                    "auto_run": "depends_on_paranoia",
                    "terminal_session": "reuse and clone an existing terminal session",
                },
                {
                    "canonical": "/act terminal prefill-in <terminal-ref> <command> [--cwd repo-root]",
                    "action": "prefill_in_terminal",
                    "auto_run": False,
                    "terminal_session": "reuse and clone an existing terminal session without running",
                },
            ],
            "terminal_refs": ["active", "last", "term_<id>"],
            "paranoia_policy": {
                "relaxed": "auto-run non-destructive terminal commands",
                "normal": "auto-run read-only and dry-run/test commands; confirm mutation/unknown commands",
                "strict": "auto-run read-only only; confirm dry-run/test and mutation/unknown commands",
                "locked": "never auto-run; prefill only",
            },
        },
        {
            "app_phrase": "spreadsheet",
            "canonical_app_id": "spreadsheet",
            "mount_depth": 1,
            "implemented_commands": [
                {
                    "canonical": "/act spreadsheet mount",
                    "action": "mount",
                }
            ],
            "desired_but_unsupported_commands": [
                "/act spreadsheet import table current-block",
                "/act spreadsheet import csv <path>",
            ],
        },
    ],
}


SYSTEM_PROMPT = """
You are the Main Computer text-console slash-command resolver.

Use only the supplied app capability registry and terminal context. Do not
invent commands. Return only a JSON object with this shape:

{
  "canonical_command": "/act ...",
  "reason": "brief reason"
}

Rules:
- If the user asks to run something, prefer /act terminal run <command> --cwd repo-root.
- If terminal_context.open_terminals contains a relevant active terminal and the
  user uses follow-up language like "now run ...", "run ... in the open
  terminal", or "run ... there", use /act terminal run-in active <command> --cwd repo-root.
- If there is no open terminal or the request comes out of the blue, use
  /act terminal run <command> --cwd repo-root so the broker creates a new terminal.
- If the user asks to prepare but not execute a command, use /act terminal prefill <command> --cwd repo-root.
- If the user asks to show hidden files, use /act file manager show hidden files.
- If the user writes a fuzzy command like /list directory .., decode it to /act file manager list directory ...
- Return a command that the trusted parser can accept exactly.
- Do not include markdown.

Examples:
User request: now run git status
Terminal context: active terminal term_git with recent_commands ["git status", "git diff"]
Return: {"canonical_command": "/act terminal run-in active \"git status\" --cwd repo-root", "reason": "The active terminal is already a git terminal, so reuse it."}

User request: now run git status
Terminal context: no open terminals
Return: {"canonical_command": "/act terminal run \"git status\" --cwd repo-root", "reason": "There is no open terminal to reuse, so create a new one."}
""".strip()


@dataclass(frozen=True)
class ParsedSlashCommand:
    raw_input: str
    prefix: str
    canonical_command: str
    app_phrase: str
    app_id: str
    action_phrase: str
    action: str
    args: dict[str, str]
    options: dict[str, Any]


@dataclass(frozen=True)
class BrokerAction:
    action_id: str
    canonical_command: str
    app_id: str
    app_label: str
    action: str
    surface: str
    args: dict[str, str]
    options: dict[str, Any]


@dataclass(frozen=True)
class RenderPacket:
    answer_markdown: str
    command_card: dict[str, Any]
    broker_mounts: list[dict[str, Any]]
    mounted_object: dict[str, Any]


@dataclass(frozen=True)
class TerminalSafetyDecision:
    paranoia: str
    risk: str
    requested_mode: str
    execution_mode: str
    auto_run: bool
    requires_confirmation: bool
    blocked: bool
    reason: str


@dataclass(frozen=True)
class SmokeReport:
    ok: bool
    base_url: str
    model: str
    used_ollama: bool
    paranoia: str
    exact_render_packet: dict[str, Any]
    exact_path_render_packet: dict[str, Any]
    terminal_exact_run_packet: dict[str, Any]
    terminal_run_in_active_packet: dict[str, Any]
    terminal_out_of_blue_packet: dict[str, Any]
    terminal_ambiguous_packet: dict[str, Any]
    terminal_mutation_normal_packet: dict[str, Any]
    terminal_mutation_relaxed_packet: dict[str, Any]
    terminal_locked_packet: dict[str, Any]
    contextual_request: str
    contextual_terminal_context: dict[str, Any]
    contextual_model_payload: dict[str, Any]
    contextual_rag_packet: dict[str, Any]
    out_of_blue_request: str
    out_of_blue_terminal_context: dict[str, Any]
    out_of_blue_model_payload: dict[str, Any]
    out_of_blue_rag_packet: dict[str, Any]
    inert_prose_blocks: list[dict[str, Any]]
    warnings: list[str]
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def parse_bool(text: str) -> bool:
    value = normalize(text)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {text!r}")


def one_line(text: str, limit: int = 500) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def compact_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def lex_command(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None

    for char in str(text).strip():
        if quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue

        if char in {"'", '"'}:
            quote = char
            continue

        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue

        current.append(char)

    if quote:
        return []
    if current:
        tokens.append("".join(current))
    return tokens


def quote_arg(value: Any) -> str:
    text = str(value)
    if not text:
        return '""'
    if re.search(r"\s", text) or any(char in text for char in ['"', "\\"]):
        return '"' + text.replace('"', r'\"') + '"'
    return text


def quote_command(command: str) -> str:
    return '"' + command.replace('"', r'\"') + '"'


def canonicalize_app_tokens(tokens: list[str]) -> tuple[str | None, int]:
    lowered = [normalize(token).replace("_", "-") for token in tokens]
    candidates = [
        (("file", "manager"), "file manager", 2),
        (("file-manager",), "file manager", 1),
        (("files",), "file manager", 1),
        (("file", "explorer"), "file manager", 2),
        (("file-explorer",), "file manager", 1),
        (("terminal",), "terminal", 1),
        (("term",), "terminal", 1),
        (("spreadsheet",), "spreadsheet", 1),
        (("sheet",), "spreadsheet", 1),
    ]
    for pattern, app_phrase, length in candidates:
        if tuple(lowered[:length]) == pattern:
            return app_phrase, length
    return None, 0


def normalize_cwd(value: str) -> str:
    lowered = normalize(value).replace("_", "-")
    aliases = {
        "repo": "repo-root",
        "repo-root": "repo-root",
        "repository": "repo-root",
        "repository-root": "repo-root",
        "cwd": "current",
        "current": "current",
        ".": ".",
    }
    return aliases.get(lowered, value)


def split_options(tokens: list[str]) -> tuple[list[str], dict[str, str]]:
    positionals: list[str] = []
    options: dict[str, str] = {}
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            positionals.append(token)
            index += 1
            continue

        key = token[2:].strip().lower().replace("_", "-")
        if not key:
            options["parse_error"] = "empty option"
            index += 1
            continue

        if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
            options[key] = tokens[index + 1]
            index += 2
        else:
            options[key] = "true"
            index += 1

    return positionals, options


def parse_file_manager_command(text: str, tokens: list[str], app_len: int) -> ParsedSlashCommand | None:
    rest = tokens[1 + app_len :]
    rest_norm = [normalize(token).replace("_", "-") for token in rest]

    if rest_norm == ["show", "hidden", "files"]:
        return ParsedSlashCommand(
            raw_input=text,
            prefix="/act",
            canonical_command="/act file manager show hidden files",
            app_phrase="file manager",
            app_id="file_explorer",
            action_phrase="show hidden files",
            action="show_hidden_files",
            args={},
            options={"showHiddenFiles": True, "focus": "hidden_files"},
        )

    if len(rest_norm) >= 3 and rest_norm[:2] == ["list", "directory"]:
        path = " ".join(rest[2:]).strip()
        if not path:
            return None
        return ParsedSlashCommand(
            raw_input=text,
            prefix="/act",
            canonical_command=f"/act file manager list directory {quote_arg(path)}",
            app_phrase="file manager",
            app_id="file_explorer",
            action_phrase="list directory",
            action="list_directory",
            args={"path": path},
            options={"path": path},
        )

    if len(rest_norm) >= 3 and rest_norm[:2] == ["change", "directory"]:
        path = " ".join(rest[2:]).strip()
        if not path:
            return None
        return ParsedSlashCommand(
            raw_input=text,
            prefix="/act",
            canonical_command=f"/act file manager change directory {quote_arg(path)}",
            app_phrase="file manager",
            app_id="file_explorer",
            action_phrase="change directory",
            action="change_directory",
            args={"path": path},
            options={"path": path, "initialLocation": path},
        )

    return None


def parse_terminal_command(text: str, tokens: list[str], app_len: int) -> ParsedSlashCommand | None:
    rest = tokens[1 + app_len :]
    if not rest:
        return None

    first = normalize(rest[0]).replace("_", "-")
    tail = rest[1:]

    if first == "mount":
        return ParsedSlashCommand(
            raw_input=text,
            prefix="/act",
            canonical_command="/act terminal mount",
            app_phrase="terminal",
            app_id="terminal",
            action_phrase="mount",
            action="mount_terminal",
            args={},
            options={"autoRun": False, "executionMode": "mount"},
        )

    if first not in {"run", "prefill", "run-in", "prefill-in"}:
        return None

    terminal_ref = ""
    if first in {"run-in", "prefill-in"}:
        if not tail:
            return None
        terminal_ref = tail[0]
        tail = tail[1:]

    command_parts, options = split_options(tail)
    if options.get("parse_error") or not command_parts:
        return None

    command = " ".join(command_parts).strip()
    if not command:
        return None

    cwd = normalize_cwd(str(options.get("cwd") or "repo-root"))
    args = {"command": command, "cwd": cwd}
    if terminal_ref:
        args["terminal_ref"] = terminal_ref

    requested_execution = "run" if first in {"run", "run-in"} else "prefill"
    action_by_first = {
        "run": "run_command",
        "prefill": "prefill_command",
        "run-in": "run_in_terminal",
        "prefill-in": "prefill_in_terminal",
    }
    action = action_by_first[first]

    canonical_parts = ["/act terminal", first]
    if terminal_ref:
        canonical_parts.append(quote_arg(terminal_ref))
    canonical_parts.append(quote_command(command))
    canonical_command = " ".join(canonical_parts) + f" --cwd {quote_arg(cwd)}"

    if "paranoia" in options:
        canonical_command += f" --paranoia {quote_arg(str(options['paranoia']))}"
        args["paranoia"] = str(options["paranoia"])

    return ParsedSlashCommand(
        raw_input=text,
        prefix="/act",
        canonical_command=canonical_command,
        app_phrase="terminal",
        app_id="terminal",
        action_phrase=first,
        action=action,
        args=args,
        options={
            "command": command,
            "cwd": cwd,
            "terminalRef": terminal_ref,
            "requestedExecution": requested_execution,
        },
    )


def parse_spreadsheet_command(text: str, tokens: list[str], app_len: int) -> ParsedSlashCommand | None:
    rest = tokens[1 + app_len :]
    rest_norm = [normalize(token).replace("_", "-") for token in rest]
    if rest_norm != ["mount"]:
        return None

    return ParsedSlashCommand(
        raw_input=text,
        prefix="/act",
        canonical_command="/act spreadsheet mount",
        app_phrase="spreadsheet",
        app_id="spreadsheet",
        action_phrase="mount",
        action="mount",
        args={},
        options={},
    )


def exact_parse_slash_command(text: str) -> ParsedSlashCommand | None:
    tokens = lex_command(text)
    if not tokens:
        return None

    prefix = normalize(tokens[0])
    if prefix not in {"/act", "/action"}:
        return None

    app_phrase, app_len = canonicalize_app_tokens(tokens[1:])
    if not app_phrase:
        return None

    if app_phrase == "file manager":
        return parse_file_manager_command(text, tokens, app_len)
    if app_phrase == "terminal":
        return parse_terminal_command(text, tokens, app_len)
    if app_phrase == "spreadsheet":
        return parse_spreadsheet_command(text, tokens, app_len)
    return None


def route_user_input(text: str) -> dict[str, Any]:
    parsed = exact_parse_slash_command(text)
    if parsed:
        return {
            "requires_rag": False,
            "reason": "exact canonical slash command matched",
            "parsed": asdict(parsed),
        }

    return {
        "requires_rag": True,
        "reason": "input is not an exact canonical command; route through RAG",
        "input": text,
    }


def classify_terminal_command(command: str) -> str:
    text = normalize(command).replace("\\", "/")

    destructive_patterns = [
        r"\brm\s+-rf\b",
        r"\bdel\s+/",
        r"\berase\s+",
        r"\bformat\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-fd\b",
    ]
    if any(re.search(pattern, text) for pattern in destructive_patterns):
        return "destructive"

    mutation_patterns = [
        r"\bpython(?:\.exe)?\s+new_patch\.py\b(?!.*--dry-run)",
        r"\bgit\s+(commit|push|merge|rebase|cherry-pick|stash|checkout\s+-f)\b",
        r"\bnpm\s+install\b",
        r"\bpip\s+install\b",
    ]
    if any(re.search(pattern, text) for pattern in mutation_patterns):
        return "mutation"

    dry_run_patterns = [
        r"--dry-run",
        r"\bpytest\b",
        r"\bpython\s+-m\s+pytest\b",
        r"\bunittest\b",
    ]
    if any(re.search(pattern, text) for pattern in dry_run_patterns):
        return "dry_run_or_test"

    read_only_prefixes = (
        "git status",
        "git diff",
        "git log",
        "dir",
        "ls",
        "pwd",
        "type ",
        "cat ",
        "get-childitem",
        "python -s main_computer/",
        "python -s ",
    )
    if any(text == prefix.strip() or text.startswith(prefix) for prefix in read_only_prefixes):
        return "read_only"

    return "unknown"


def decide_terminal_safety(parsed: ParsedSlashCommand, paranoia: str) -> TerminalSafetyDecision:
    level = normalize(parsed.args.get("paranoia") or paranoia or DEFAULT_PARANOIA)
    if level not in PARANOIA_LEVELS:
        level = DEFAULT_PARANOIA

    if parsed.action == "mount_terminal":
        return TerminalSafetyDecision(
            paranoia=level,
            risk="none",
            requested_mode="mount",
            execution_mode="mount",
            auto_run=False,
            requires_confirmation=False,
            blocked=False,
            reason="Terminal mount has no command to run.",
        )

    if parsed.action in {"prefill_command", "prefill_in_terminal"}:
        return TerminalSafetyDecision(
            paranoia=level,
            risk=classify_terminal_command(parsed.args.get("command", "")),
            requested_mode="prefill",
            execution_mode="prefill",
            auto_run=False,
            requires_confirmation=False,
            blocked=False,
            reason="User requested prefill, so the command is not auto-run.",
        )

    command = parsed.args.get("command", "")
    risk = classify_terminal_command(command)

    if risk == "destructive":
        return TerminalSafetyDecision(
            paranoia=level,
            risk=risk,
            requested_mode="run",
            execution_mode="blocked",
            auto_run=False,
            requires_confirmation=False,
            blocked=True,
            reason="Destructive command is blocked by the terminal broker.",
        )

    if level == "relaxed":
        return TerminalSafetyDecision(
            paranoia=level,
            risk=risk,
            requested_mode="run",
            execution_mode="auto_run",
            auto_run=True,
            requires_confirmation=False,
            blocked=False,
            reason="Relaxed paranoia auto-runs non-destructive terminal commands.",
        )

    if level == "normal":
        if risk in {"read_only", "dry_run_or_test"}:
            return TerminalSafetyDecision(
                paranoia=level,
                risk=risk,
                requested_mode="run",
                execution_mode="auto_run",
                auto_run=True,
                requires_confirmation=False,
                blocked=False,
                reason="Normal paranoia auto-runs read-only and dry-run/test commands.",
            )
        return TerminalSafetyDecision(
            paranoia=level,
            risk=risk,
            requested_mode="run",
            execution_mode="confirmation_required",
            auto_run=False,
            requires_confirmation=True,
            blocked=False,
            reason="Normal paranoia requires confirmation for mutation or unknown terminal commands.",
        )

    if level == "strict":
        if risk == "read_only":
            return TerminalSafetyDecision(
                paranoia=level,
                risk=risk,
                requested_mode="run",
                execution_mode="auto_run",
                auto_run=True,
                requires_confirmation=False,
                blocked=False,
                reason="Strict paranoia auto-runs read-only commands only.",
            )
        return TerminalSafetyDecision(
            paranoia=level,
            risk=risk,
            requested_mode="run",
            execution_mode="confirmation_required",
            auto_run=False,
            requires_confirmation=True,
            blocked=False,
            reason="Strict paranoia requires confirmation for non-read-only terminal commands.",
        )

    return TerminalSafetyDecision(
        paranoia=level,
        risk=risk,
        requested_mode="run",
        execution_mode="prefill",
        auto_run=False,
        requires_confirmation=False,
        blocked=False,
        reason="Locked paranoia never auto-runs terminal commands.",
    )


def make_git_terminal_context() -> dict[str, Any]:
    return {
        "active_terminal_id": "term_git",
        "open_terminals": [
            {
                "terminal_session_id": "term_git",
                "terminal_view_id": "view_git_001",
                "label": "Terminal 1",
                "cwd": "repo-root",
                "active": True,
                "last_command": "git diff",
                "recent_commands": ["git status", "git diff", "git log --oneline"],
                "topic": "git",
            }
        ],
    }


def make_empty_terminal_context() -> dict[str, Any]:
    return {"active_terminal_id": None, "open_terminals": []}


def make_ambiguous_terminal_context() -> dict[str, Any]:
    return {
        "active_terminal_id": None,
        "open_terminals": [
            {
                "terminal_session_id": "term_git",
                "terminal_view_id": "view_git_001",
                "label": "Terminal 1",
                "cwd": "repo-root",
                "active": False,
                "last_command": "git diff",
                "recent_commands": ["git status", "git diff"],
                "topic": "git",
            },
            {
                "terminal_session_id": "term_tests",
                "terminal_view_id": "view_tests_001",
                "label": "Terminal 2",
                "cwd": "repo-root",
                "active": False,
                "last_command": "python -m pytest",
                "recent_commands": ["python -m pytest"],
                "topic": "tests",
            },
        ],
    }


def resolve_terminal_reference(ref: str, terminal_context: dict[str, Any] | None) -> dict[str, Any] | None:
    context = terminal_context or {}
    terminals = context.get("open_terminals")
    if not isinstance(terminals, list) or not terminals:
        return None

    normalized_ref = normalize(ref)
    if normalized_ref in {"active", "current", "open"}:
        active_id = context.get("active_terminal_id")
        if active_id:
            for terminal in terminals:
                if terminal.get("terminal_session_id") == active_id:
                    return terminal
        active = [terminal for terminal in terminals if terminal.get("active")]
        if len(active) == 1:
            return active[0]
        return None

    if normalized_ref == "last":
        return terminals[-1] if len(terminals) == 1 else None

    for terminal in terminals:
        if normalize(terminal.get("terminal_session_id")) == normalized_ref:
            return terminal
        if normalize(terminal.get("label")) == normalized_ref:
            return terminal

    return None


def resolve_action_terminal_context(parsed: ParsedSlashCommand, terminal_context: dict[str, Any] | None) -> ParsedSlashCommand:
    if parsed.app_id != "terminal":
        return parsed

    args = dict(parsed.args)
    options = dict(parsed.options)

    if parsed.action in {"run_in_terminal", "prefill_in_terminal"}:
        ref = args.get("terminal_ref", "active")
        resolved = resolve_terminal_reference(ref, terminal_context)
        if resolved is None:
            args["terminal_resolution"] = "needs_terminal_selection"
            args["terminal_ref"] = ref
            options["terminalResolution"] = "needs_terminal_selection"
            return ParsedSlashCommand(
                **{**asdict(parsed), "args": args, "options": options}
            )

        args["terminal_session_id"] = str(resolved.get("terminal_session_id") or "")
        args["parent_view_id"] = str(resolved.get("terminal_view_id") or "")
        args["terminal_ref"] = ref
        args["terminal_label"] = str(resolved.get("label") or args["terminal_session_id"])
        args["terminal_resolution"] = "reused_existing_terminal"
        if "cwd" not in args or args.get("cwd") == "repo-root":
            args["cwd"] = str(resolved.get("cwd") or args.get("cwd") or "repo-root")
        options["terminalResolution"] = "reused_existing_terminal"
        options["terminalSessionId"] = args["terminal_session_id"]
        options["parentViewId"] = args["parent_view_id"]
        return ParsedSlashCommand(
            **{**asdict(parsed), "args": args, "options": options}
        )

    if parsed.action in {"run_command", "prefill_command", "mount_terminal"}:
        digest_source = parsed.canonical_command + json.dumps(parsed.args, sort_keys=True)
        session_id = "term_" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:8]
        args.setdefault("terminal_session_id", session_id)
        args.setdefault("parent_view_id", "")
        args.setdefault("terminal_resolution", "created_new_terminal")
        options.setdefault("terminalResolution", "created_new_terminal")
        options.setdefault("terminalSessionId", session_id)
        return ParsedSlashCommand(
            **{**asdict(parsed), "args": args, "options": options}
        )

    return parsed


def terminal_view_id(action: BrokerAction) -> str:
    digest_source = action.canonical_command + json.dumps(action.args, sort_keys=True)
    return "view_" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]


def make_user_payload(request_text: str, terminal_context: dict[str, Any] | None) -> str:
    return compact_json(
        {
            "surface": TEXT_CONSOLE_SURFACE,
            "user_request": request_text,
            "terminal_context": terminal_context or make_empty_terminal_context(),
            "app_capability_registry": APP_CAPABILITY_REGISTRY,
            "task": (
                "Resolve user_request into a canonical slash command. "
                "Use terminal_context to decide whether to run in an existing terminal "
                "or create a new terminal."
            ),
        }
    )


def call_ollama_chat(
    *,
    base_url: str,
    model: str,
    request_text: str,
    terminal_context: dict[str, Any] | None,
    timeout: float,
    think: bool | None,
) -> tuple[dict[str, Any], str]:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": make_user_payload(request_text, terminal_context)},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 700,
        },
    }
    if think is not None:
        payload["think"] = think

    http_request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(http_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(
            f"Ollama HTTP error from {url}: {exc.code} {exc.reason}. Body: {one_line(body)}"
        ) from exc
    except URLError as exc:
        raise AssertionError(
            f"Could not reach Ollama at {base_url!r}. "
            f"Start Ollama and pull the model, for example: ollama pull {model}. "
            f"Original error: {exc}"
        ) from exc

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Ollama returned non-JSON HTTP body: {one_line(raw)}") from exc

    message = envelope.get("message")
    if not isinstance(message, dict):
        raise AssertionError(f"Ollama response missing message object: {one_line(raw)}")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AssertionError(f"Ollama response missing message.content text: {one_line(raw)}")

    return parse_model_payload(content), raw


def strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return stripped


def extract_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object start found")

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("no balanced JSON object found")


def parse_model_payload(content: str) -> dict[str, Any]:
    text = strip_json_code_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = json.loads(extract_balanced_json_object(text))

    if not isinstance(parsed, dict):
        raise AssertionError(f"Model did not return a JSON object: {type(parsed).__name__}")

    return parsed


def deterministic_model_payload_for_offline_contract(
    request_text: str,
    terminal_context: dict[str, Any] | None,
) -> dict[str, Any]:
    lowered = normalize(request_text)
    context = terminal_context or make_empty_terminal_context()
    terminals = context.get("open_terminals") if isinstance(context, dict) else []
    has_terminal = isinstance(terminals, list) and bool(terminals)
    active_terminal = resolve_terminal_reference("active", context)

    if "git status" in lowered or ("run" in lowered and "status" in lowered):
        if active_terminal is not None and ("now" in lowered or "open terminal" in lowered or "there" in lowered):
            return {
                "canonical_command": '/act terminal run-in active "git status" --cwd repo-root',
                "reason": "The active terminal is already a git terminal, so reuse and clone it.",
            }
        if has_terminal and "open terminal" in lowered and active_terminal is None:
            return {
                "needs_terminal_selection": True,
                "reason": "The request refers to an open terminal, but the context has no unambiguous active terminal.",
            }
        return {
            "canonical_command": '/act terminal run "git status" --cwd repo-root',
            "reason": "No relevant terminal context was supplied, so create a new terminal.",
        }

    if "dry" in lowered and "patch" in lowered:
        return {
            "canonical_command": '/act terminal run "python new_patch.py patch.zip --dry-run" --cwd repo-root',
            "reason": "The user asks to run a patch dry-run command.",
        }
    if "list" in lowered and "directory" in lowered and ".." in lowered:
        return {
            "canonical_command": "/act file manager list directory ..",
            "reason": "The fuzzy slash request asks the file manager to list the parent directory.",
        }
    if "hidden" in lowered and "file" in lowered:
        return {
            "canonical_command": "/act file manager show hidden files",
            "reason": "The request asks the file manager to show hidden files.",
        }
    raise AssertionError(f"offline contract fixture does not know how to resolve: {request_text!r}")


def canonical_command_from_model(model_payload: dict[str, Any]) -> str:
    if model_payload.get("needs_terminal_selection"):
        raise AssertionError(f"Model requested terminal selection instead of a canonical command: {model_payload!r}")
    value = model_payload.get("canonical_command")
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"Model payload missing canonical_command string: {model_payload!r}")
    return value.strip()


def mint_broker_action(parsed: ParsedSlashCommand) -> BrokerAction:
    # Deterministic id keeps the smoke stable while still proving the id is
    # broker-minted from parsed runtime state, not scraped from assistant prose.
    digest = hashlib.sha256(
        (parsed.canonical_command + json.dumps(parsed.args, sort_keys=True)).encode("utf-8")
    ).hexdigest()[:12]
    app_label = {
        "file_explorer": "File Manager",
        "terminal": "Terminal",
        "spreadsheet": "Spreadsheet",
    }.get(parsed.app_id, parsed.app_phrase.title())
    return BrokerAction(
        action_id=f"act_{digest}",
        canonical_command=parsed.canonical_command,
        app_id=parsed.app_id,
        app_label=app_label,
        action=parsed.action,
        surface=TEXT_CONSOLE_SURFACE,
        args=dict(parsed.args),
        options=dict(parsed.options),
    )


def build_file_manager_render_packet(action: BrokerAction, *, origin: str) -> RenderPacket:
    label_by_action = {
        "show_hidden_files": "Showing hidden files in File Manager.",
        "list_directory": f"Listing directory {action.args.get('path', '.')!r} in File Manager.",
        "change_directory": f"Changing File Manager directory to {action.args.get('path', '.')!r}.",
    }
    answer = label_by_action.get(action.action, f"Mounted {action.app_label}.")

    mounted_object = {
        "kind": "mounted_app",
        "appId": action.app_id,
        "label": action.app_label,
        "action": action.action,
        "surface": action.surface,
        "args": action.args,
        "options": action.options,
        "display": {
            "title": action.app_label,
            "subtitle": action.canonical_command,
        },
    }
    return render_packet_for_action(action, origin=origin, answer_markdown=answer, mounted_object=mounted_object)


def build_terminal_selection_packet(action: BrokerAction, *, origin: str) -> RenderPacket:
    mounted_object = {
        "kind": "terminal_selection_required",
        "appId": "terminal",
        "label": "Terminal",
        "action": action.action,
        "surface": action.surface,
        "args": action.args,
        "options": action.options,
        "terminal": {
            "terminalRef": action.args.get("terminal_ref", "active"),
            "resolution": "needs_terminal_selection",
            "autoRun": False,
            "requiresSelection": True,
        },
        "display": {
            "title": "Choose a terminal",
            "subtitle": action.canonical_command,
        },
    }
    return RenderPacket(
        answer_markdown="Choose which open terminal should run this command.",
        command_card={
            "kind": "canonical_command",
            "label": "Equivalent command",
            "text": action.canonical_command,
            "origin": origin,
        },
        broker_mounts=[],
        mounted_object=mounted_object,
    )


def build_terminal_render_packet(action: BrokerAction, *, origin: str, paranoia: str) -> RenderPacket:
    if action.args.get("terminal_resolution") == "needs_terminal_selection":
        return build_terminal_selection_packet(action, origin=origin)

    parsed = ParsedSlashCommand(
        raw_input=action.canonical_command,
        prefix="/act",
        canonical_command=action.canonical_command,
        app_phrase="terminal",
        app_id="terminal",
        action_phrase=action.action,
        action=action.action,
        args=action.args,
        options=action.options,
    )
    decision = decide_terminal_safety(parsed, paranoia)
    command = action.args.get("command", "")
    cwd = action.args.get("cwd", "repo-root")
    session_id = action.args.get("terminal_session_id", "")
    parent_view_id = action.args.get("parent_view_id", "")
    view_id = terminal_view_id(action)
    created_new = action.args.get("terminal_resolution") == "created_new_terminal"
    reused_existing = action.args.get("terminal_resolution") == "reused_existing_terminal"

    if action.action == "mount_terminal":
        answer = "Mounted Terminal."
    elif action.action in {"prefill_command", "prefill_in_terminal"}:
        if reused_existing:
            answer = f"Prepared Terminal command in {action.args.get('terminal_label', session_id)} without running it: {command!r}."
        else:
            answer = f"Prepared Terminal command without running it: {command!r}."
    elif decision.auto_run:
        if reused_existing:
            answer = f"Running Terminal command in {action.args.get('terminal_label', session_id)}: {command!r}."
        else:
            answer = f"Running Terminal command in a new terminal: {command!r}."
    elif decision.blocked:
        answer = f"Blocked Terminal command: {command!r}."
    elif decision.requires_confirmation:
        answer = f"Terminal command requires confirmation before running: {command!r}."
    else:
        answer = f"Prepared Terminal command instead of auto-running it: {command!r}."

    mounted_object = {
        "kind": "mounted_app",
        "appId": "terminal",
        "label": "Terminal",
        "action": action.action,
        "surface": action.surface,
        "args": action.args,
        "options": {
            **action.options,
            "command": command,
            "cwd": cwd,
            "autoRun": decision.auto_run,
            "executionMode": decision.execution_mode,
            "requiresConfirmation": decision.requires_confirmation,
            "blocked": decision.blocked,
            "paranoia": decision.paranoia,
            "risk": decision.risk,
            "terminalSessionId": session_id,
            "terminalViewId": view_id,
            "parentViewId": parent_view_id,
            "createdNewSession": created_new,
            "reusedExistingSession": reused_existing,
        },
        "terminal": {
            "terminalSessionId": session_id,
            "terminalViewId": view_id,
            "parentViewId": parent_view_id,
            "clonedFromViewId": parent_view_id or None,
            "createdNewSession": created_new,
            "reusedExistingSession": reused_existing,
            "targetResolution": action.args.get("terminal_resolution", ""),
            "terminalRef": action.args.get("terminal_ref", ""),
            "command": command,
            "cwd": cwd,
            "requestedMode": decision.requested_mode,
            "executionMode": decision.execution_mode,
            "autoRun": decision.auto_run,
            "requiresConfirmation": decision.requires_confirmation,
            "blocked": decision.blocked,
            "risk": decision.risk,
            "paranoia": decision.paranoia,
            "reason": decision.reason,
        },
        "display": {
            "title": "Terminal",
            "subtitle": action.canonical_command,
        },
    }
    return render_packet_for_action(action, origin=origin, answer_markdown=answer, mounted_object=mounted_object)


def render_packet_for_action(action: BrokerAction, *, origin: str, answer_markdown: str, mounted_object: dict[str, Any]) -> RenderPacket:
    command_card = {
        "kind": "canonical_command",
        "label": "Equivalent command",
        "text": action.canonical_command,
        "origin": origin,
    }
    broker_mounts = [
        {
            "kind": "auto_mount",
            "action_id": action.action_id,
            "canonical_command": action.canonical_command,
            "surface": action.surface,
            "placement": "inline_after_answer",
        }
    ]
    return RenderPacket(
        answer_markdown=answer_markdown,
        command_card=command_card,
        broker_mounts=broker_mounts,
        mounted_object=mounted_object,
    )


def build_render_packet(action: BrokerAction, *, origin: str, paranoia: str) -> RenderPacket:
    if action.app_id == "terminal":
        return build_terminal_render_packet(action, origin=origin, paranoia=paranoia)
    if action.app_id == "file_explorer":
        return build_file_manager_render_packet(action, origin=origin)

    mounted_object = {
        "kind": "mounted_app",
        "appId": action.app_id,
        "label": action.app_label,
        "action": action.action,
        "surface": action.surface,
        "args": action.args,
        "options": action.options,
        "display": {"title": action.app_label, "subtitle": action.canonical_command},
    }
    return render_packet_for_action(
        action,
        origin=origin,
        answer_markdown=f"Mounted {action.app_label}.",
        mounted_object=mounted_object,
    )


def broker_parse_and_render(
    canonical_command: str,
    *,
    origin: str,
    paranoia: str = DEFAULT_PARANOIA,
    terminal_context: dict[str, Any] | None = None,
) -> tuple[ParsedSlashCommand, BrokerAction, RenderPacket]:
    parsed = exact_parse_slash_command(canonical_command)
    if not parsed:
        raise AssertionError(f"Broker rejected non-exact canonical command: {canonical_command!r}")

    parsed = resolve_action_terminal_context(parsed, terminal_context)
    action = mint_broker_action(parsed)
    packet = build_render_packet(action, origin=origin, paranoia=paranoia)
    return parsed, action, packet


def inspect_ai_result_blocks(markdown: str) -> list[dict[str, Any]]:
    """
    Minimal renderer-prep inspector.

    It may detect slash-command examples for display, but it must not create
    broker mounts from prose. Executable controls come only from broker_mounts.
    """
    blocks: list[dict[str, Any]] = []

    code_fence_pattern = re.compile(
        r"```(?P<language>[a-zA-Z0-9_+-]*)\n(?P<body>.*?)```",
        flags=re.DOTALL,
    )

    cursor = 0
    for match in code_fence_pattern.finditer(markdown):
        before = markdown[cursor : match.start()].strip()
        if before:
            blocks.extend(inspect_plain_markdown_block(before))

        language = match.group("language") or "text"
        body = match.group("body")
        blocks.append(
            {
                "kind": "code",
                "language": language,
                "text": body,
                "rendererCallouts": callouts_for_block("code", language, body),
                "brokerMounts": [],
                "slashCommandExamples": slash_examples_in_text(body, executable=False, reason="code block is inert"),
            }
        )
        cursor = match.end()

    rest = markdown[cursor:].strip()
    if rest:
        blocks.extend(inspect_plain_markdown_block(rest))

    return blocks


def inspect_plain_markdown_block(text: str) -> list[dict[str, Any]]:
    kind = "table" if looks_like_markdown_table(text) else "markdown"
    return [
        {
            "kind": kind,
            "text": text,
            "rendererCallouts": callouts_for_block(kind, "markdown", text),
            "brokerMounts": [],
            "slashCommandExamples": slash_examples_in_text(text, executable=False, reason="assistant prose is inert"),
        }
    ]


def slash_examples_in_text(text: str, *, executable: bool, reason: str) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for match in re.finditer(r"(^|\s)(/act(?:ion)?\s+[^\n`]+)", text):
        examples.append(
            {
                "text": match.group(2).strip(),
                "executable": executable,
                "reason": reason,
            }
        )
    return examples


def looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False

    has_header = "|" in lines[0]
    has_separator = bool(re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", lines[1]))
    return has_header and has_separator


def callouts_for_block(kind: str, language: str, text: str) -> list[dict[str, Any]]:
    if kind == "code":
        callouts = [
            {
                "kind": "app_mount",
                "label": "Open in Code Editor",
                "appId": "code_editor",
                "surface": TEXT_CONSOLE_SURFACE,
                "options": {"language": language},
            }
        ]

        if language in {"python", "py"}:
            callouts.append(
                {
                    "kind": "app_mount",
                    "label": "Run in Terminal",
                    "appId": "terminal",
                    "surface": TEXT_CONSOLE_SURFACE,
                    "options": {"prefill": text, "workingDirectory": "repo-root"},
                }
            )

        return callouts

    if kind == "table":
        return [
            {
                "kind": "app_mount",
                "label": "Open as Spreadsheet",
                "appId": "spreadsheet",
                "surface": TEXT_CONSOLE_SURFACE,
                "options": {"importFormat": "markdown_table"},
            }
        ]

    return []


def validate_common_render_packet(
    packet: RenderPacket,
    *,
    expected_command: str,
    expected_action: str,
    expected_app_id: str,
) -> list[str]:
    failures: list[str] = []

    if packet.command_card.get("text") != expected_command:
        failures.append(f"command card text expected {expected_command!r}, got {packet.command_card.get('text')!r}")
    if packet.mounted_object.get("appId") != expected_app_id:
        failures.append(f"mounted app expected {expected_app_id!r}, got {packet.mounted_object.get('appId')!r}")
    if packet.mounted_object.get("action") != expected_action:
        failures.append(f"mounted action expected {expected_action!r}, got {packet.mounted_object.get('action')!r}")
    if packet.mounted_object.get("surface") != TEXT_CONSOLE_SURFACE:
        failures.append(f"mounted surface must be text_console, got {packet.mounted_object.get('surface')!r}")
    if not packet.broker_mounts and packet.mounted_object.get("kind") != "terminal_selection_required":
        failures.append("render packet should include broker_mounts for executable auto-mounts")
    for mount in packet.broker_mounts:
        if not str(mount.get("action_id", "")).startswith("act_"):
            failures.append(f"broker mount action id must be broker-minted, got {mount.get('action_id')!r}")
        if mount.get("canonical_command") != expected_command:
            failures.append("broker mount canonical command must match command card")
    return failures


def validate_file_manager_packet(
    packet: RenderPacket,
    *,
    expected_command: str,
    expected_action: str,
) -> list[str]:
    return validate_common_render_packet(
        packet,
        expected_command=expected_command,
        expected_action=expected_action,
        expected_app_id="file_explorer",
    )


def validate_terminal_packet(
    packet: RenderPacket,
    *,
    expected_command: str,
    expected_action: str,
    expected_auto_run: bool | None = None,
    expected_execution_mode: str | None = None,
    expected_risk: str | None = None,
    expected_created_new: bool | None = None,
    expected_reused_existing: bool | None = None,
    expected_session_id: str | None = None,
    expected_parent_view_id: str | None = None,
) -> list[str]:
    failures = validate_common_render_packet(
        packet,
        expected_command=expected_command,
        expected_action=expected_action,
        expected_app_id="terminal",
    )

    terminal = packet.mounted_object.get("terminal")
    if not isinstance(terminal, dict):
        failures.append("terminal mounted object must include terminal metadata")
        return failures

    if expected_auto_run is not None and terminal.get("autoRun") is not expected_auto_run:
        failures.append(f"terminal autoRun expected {expected_auto_run}, got {terminal.get('autoRun')!r}")
    if expected_execution_mode is not None and terminal.get("executionMode") != expected_execution_mode:
        failures.append(f"terminal executionMode expected {expected_execution_mode!r}, got {terminal.get('executionMode')!r}")
    if expected_risk is not None and terminal.get("risk") != expected_risk:
        failures.append(f"terminal risk expected {expected_risk!r}, got {terminal.get('risk')!r}")
    if expected_created_new is not None and terminal.get("createdNewSession") is not expected_created_new:
        failures.append(f"terminal createdNewSession expected {expected_created_new}, got {terminal.get('createdNewSession')!r}")
    if expected_reused_existing is not None and terminal.get("reusedExistingSession") is not expected_reused_existing:
        failures.append(f"terminal reusedExistingSession expected {expected_reused_existing}, got {terminal.get('reusedExistingSession')!r}")
    if expected_session_id is not None and terminal.get("terminalSessionId") != expected_session_id:
        failures.append(f"terminal session id expected {expected_session_id!r}, got {terminal.get('terminalSessionId')!r}")
    if expected_parent_view_id is not None and terminal.get("parentViewId") != expected_parent_view_id:
        failures.append(f"terminal parent view id expected {expected_parent_view_id!r}, got {terminal.get('parentViewId')!r}")
    if expected_reused_existing and not terminal.get("clonedFromViewId"):
        failures.append("reused terminal should be cloned from a previous view")
    return failures


def validate_terminal_selection_packet(packet: RenderPacket) -> list[str]:
    failures: list[str] = []
    if packet.broker_mounts:
        failures.append("terminal selection packet must not auto-mount")
    if packet.mounted_object.get("kind") != "terminal_selection_required":
        failures.append(f"expected terminal_selection_required, got {packet.mounted_object.get('kind')!r}")
    terminal = packet.mounted_object.get("terminal", {})
    if terminal.get("requiresSelection") is not True:
        failures.append("ambiguous terminal packet must require selection")
    return failures


def validate_inert_prose_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    joined = json.dumps(blocks, sort_keys=True)
    if "/act terminal run-in" not in joined:
        failures.append("inert prose inspector did not capture terminal run-in slash-command example text")
    if any(block.get("brokerMounts") for block in blocks):
        failures.append("assistant prose/code blocks must not create broker mounts")
    for block in blocks:
        for example in block.get("slashCommandExamples", []):
            if example.get("executable") is not False:
                failures.append("slash command examples in prose must be marked executable=false")
    return failures


def sample_inert_assistant_prose() -> str:
    return """The feature can talk about slash commands without executing them.
For example, /act terminal run-in active "git status" --cwd repo-root should be displayed as prose here,
not converted into an auto-running terminal clone.
"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG text-console terminal-context smoke test.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Ollama base URL. Default: %(default)s")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model. Default: %(default)s")
    parser.add_argument("--paranoia", choices=PARANOIA_LEVELS, default=DEFAULT_PARANOIA, help="Terminal auto-run paranoia level.")
    parser.add_argument("--exact-command", default=DEFAULT_EXACT_COMMAND, help="Exact file-manager slash command that must run without RAG.")
    parser.add_argument("--exact-path-command", default=DEFAULT_EXACT_PATH_COMMAND, help="Exact file-manager path slash command that must preserve path args.")
    parser.add_argument("--contextual-request", default=DEFAULT_CONTEXTUAL_REQUEST, help="Natural follow-up request to resolve using active terminal context.")
    parser.add_argument("--out-of-blue-request", default=DEFAULT_OUT_OF_BLUE_REQUEST, help="Natural request to resolve without any terminal context.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Ollama HTTP timeout in seconds.")
    parser.add_argument(
        "--think",
        choices=("omit", "true", "false"),
        default="omit",
        help="Set Ollama think flag for models that support it. Default omits the field.",
    )
    parser.add_argument(
        "--offline-contract-only",
        action="store_true",
        help=(
            "Do not call Ollama. This only exercises the local parser/broker/renderer contract. "
            "Default behavior calls Ollama and fails if unavailable."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    think: bool | None
    if args.think == "omit":
        think = None
    else:
        think = parse_bool(args.think)

    warnings: list[str] = []
    failures: list[str] = []
    raw_ollama_responses: list[str] = []

    exact_route = route_user_input(args.exact_command)
    if exact_route.get("requires_rag"):
        failures.append(f"exact command unexpectedly routed to RAG: {args.exact_command!r}")
        exact_packet = RenderPacket("", {}, [], {})
    else:
        exact_parsed, exact_action, exact_packet = broker_parse_and_render(
            exact_route["parsed"]["canonical_command"],
            origin="exact_user_slash_command",
            paranoia=args.paranoia,
        )
        failures.extend(
            validate_file_manager_packet(
                exact_packet,
                expected_command=exact_parsed.canonical_command,
                expected_action=exact_parsed.action,
            )
        )

    exact_path_route = route_user_input(args.exact_path_command)
    if exact_path_route.get("requires_rag"):
        failures.append(f"exact path command unexpectedly routed to RAG: {args.exact_path_command!r}")
        exact_path_packet = RenderPacket("", {}, [], {})
    else:
        exact_path_parsed, exact_path_action, exact_path_packet = broker_parse_and_render(
            exact_path_route["parsed"]["canonical_command"],
            origin="exact_user_slash_command",
            paranoia=args.paranoia,
        )
        failures.extend(
            validate_file_manager_packet(
                exact_path_packet,
                expected_command=exact_path_parsed.canonical_command,
                expected_action=exact_path_parsed.action,
            )
        )
        if "path" not in exact_path_packet.mounted_object.get("options", {}):
            failures.append("exact path command did not preserve path option")

    terminal_run_command = '/act terminal run "git status" --cwd repo-root'
    terminal_run_parsed, terminal_run_action, terminal_run_packet = broker_parse_and_render(
        terminal_run_command,
        origin="exact_user_slash_command",
        paranoia=args.paranoia,
        terminal_context=make_empty_terminal_context(),
    )
    expected_read_only_auto = args.paranoia in {"relaxed", "normal", "strict"}
    expected_read_only_mode = "auto_run" if expected_read_only_auto else "prefill"
    failures.extend(
        validate_terminal_packet(
            terminal_run_packet,
            expected_command=terminal_run_parsed.canonical_command,
            expected_action="run_command",
            expected_auto_run=expected_read_only_auto,
            expected_execution_mode=expected_read_only_mode,
            expected_risk="read_only",
            expected_created_new=True,
            expected_reused_existing=False,
        )
    )

    git_context = make_git_terminal_context()
    terminal_run_in_command = '/act terminal run-in active "git status" --cwd repo-root'
    terminal_run_in_parsed, terminal_run_in_action, terminal_run_in_packet = broker_parse_and_render(
        terminal_run_in_command,
        origin="exact_user_slash_command",
        paranoia=args.paranoia,
        terminal_context=git_context,
    )
    failures.extend(
        validate_terminal_packet(
            terminal_run_in_packet,
            expected_command=terminal_run_in_parsed.canonical_command,
            expected_action="run_in_terminal",
            expected_auto_run=expected_read_only_auto,
            expected_execution_mode=expected_read_only_mode,
            expected_risk="read_only",
            expected_created_new=False,
            expected_reused_existing=True,
            expected_session_id="term_git",
            expected_parent_view_id="view_git_001",
        )
    )

    ambiguous_parsed, ambiguous_action, ambiguous_packet = broker_parse_and_render(
        terminal_run_in_command,
        origin="exact_user_slash_command",
        paranoia=args.paranoia,
        terminal_context=make_ambiguous_terminal_context(),
    )
    failures.extend(validate_terminal_selection_packet(ambiguous_packet))

    mutation_command = '/act terminal run "python new_patch.py patch.zip" --cwd repo-root'
    mutation_normal_parsed, mutation_normal_action, mutation_normal_packet = broker_parse_and_render(
        mutation_command,
        origin="exact_user_slash_command",
        paranoia="normal",
        terminal_context=make_empty_terminal_context(),
    )
    failures.extend(
        validate_terminal_packet(
            mutation_normal_packet,
            expected_command=mutation_normal_parsed.canonical_command,
            expected_action="run_command",
            expected_auto_run=False,
            expected_execution_mode="confirmation_required",
            expected_risk="mutation",
            expected_created_new=True,
        )
    )

    mutation_relaxed_parsed, mutation_relaxed_action, mutation_relaxed_packet = broker_parse_and_render(
        mutation_command,
        origin="exact_user_slash_command",
        paranoia="relaxed",
        terminal_context=make_empty_terminal_context(),
    )
    failures.extend(
        validate_terminal_packet(
            mutation_relaxed_packet,
            expected_command=mutation_relaxed_parsed.canonical_command,
            expected_action="run_command",
            expected_auto_run=True,
            expected_execution_mode="auto_run",
            expected_risk="mutation",
            expected_created_new=True,
        )
    )

    locked_parsed, locked_action, locked_packet = broker_parse_and_render(
        terminal_run_command,
        origin="exact_user_slash_command",
        paranoia="locked",
        terminal_context=make_empty_terminal_context(),
    )
    failures.extend(
        validate_terminal_packet(
            locked_packet,
            expected_command=locked_parsed.canonical_command,
            expected_action="run_command",
            expected_auto_run=False,
            expected_execution_mode="prefill",
            expected_risk="read_only",
            expected_created_new=True,
        )
    )

    if args.offline_contract_only:
        warnings.append("offline_contract_only was used; Ollama was not touched")
        contextual_model_payload = deterministic_model_payload_for_offline_contract(args.contextual_request, git_context)
        out_of_blue_model_payload = deterministic_model_payload_for_offline_contract(args.out_of_blue_request, make_empty_terminal_context())
        used_ollama = False
    else:
        contextual_model_payload, raw = call_ollama_chat(
            base_url=args.base_url,
            model=args.model,
            request_text=args.contextual_request,
            terminal_context=git_context,
            timeout=args.timeout,
            think=think,
        )
        raw_ollama_responses.append(raw)
        out_of_blue_model_payload, raw = call_ollama_chat(
            base_url=args.base_url,
            model=args.model,
            request_text=args.out_of_blue_request,
            terminal_context=make_empty_terminal_context(),
            timeout=args.timeout,
            think=think,
        )
        raw_ollama_responses.append(raw)
        used_ollama = True

    try:
        contextual_command = canonical_command_from_model(contextual_model_payload)
        contextual_parsed, contextual_action, contextual_packet = broker_parse_and_render(
            contextual_command,
            origin="rag_decoded_contextual_terminal_request",
            paranoia=args.paranoia,
            terminal_context=git_context,
        )
        if contextual_parsed.action != "run_in_terminal":
            failures.append(f"contextual request should resolve to run_in_terminal, got {contextual_parsed.action!r}")
        failures.extend(
            validate_terminal_packet(
                contextual_packet,
                expected_command=contextual_parsed.canonical_command,
                expected_action="run_in_terminal",
                expected_auto_run=expected_read_only_auto,
                expected_execution_mode=expected_read_only_mode,
                expected_risk="read_only",
                expected_created_new=False,
                expected_reused_existing=True,
                expected_session_id="term_git",
                expected_parent_view_id="view_git_001",
            )
        )
    except Exception as exc:
        contextual_packet = RenderPacket("", {}, [], {})
        failures.append(f"contextual RAG terminal reuse failed: {exc}")

    try:
        out_of_blue_command = canonical_command_from_model(out_of_blue_model_payload)
        out_of_blue_parsed, out_of_blue_action, out_of_blue_packet = broker_parse_and_render(
            out_of_blue_command,
            origin="rag_decoded_out_of_blue_terminal_request",
            paranoia=args.paranoia,
            terminal_context=make_empty_terminal_context(),
        )
        if out_of_blue_parsed.action != "run_command":
            failures.append(f"out-of-blue request should resolve to run_command/new terminal, got {out_of_blue_parsed.action!r}")
        failures.extend(
            validate_terminal_packet(
                out_of_blue_packet,
                expected_command=out_of_blue_parsed.canonical_command,
                expected_action="run_command",
                expected_auto_run=expected_read_only_auto,
                expected_execution_mode=expected_read_only_mode,
                expected_risk="read_only",
                expected_created_new=True,
                expected_reused_existing=False,
            )
        )
    except Exception as exc:
        out_of_blue_packet = RenderPacket("", {}, [], {})
        failures.append(f"out-of-blue RAG terminal creation failed: {exc}")

    inert_prose_blocks = inspect_ai_result_blocks(sample_inert_assistant_prose())
    failures.extend(validate_inert_prose_blocks(inert_prose_blocks))

    report = SmokeReport(
        ok=not failures,
        base_url=args.base_url,
        model=args.model,
        used_ollama=used_ollama,
        paranoia=args.paranoia,
        exact_render_packet=asdict(exact_packet),
        exact_path_render_packet=asdict(exact_path_packet),
        terminal_exact_run_packet=asdict(terminal_run_packet),
        terminal_run_in_active_packet=asdict(terminal_run_in_packet),
        terminal_out_of_blue_packet=asdict(out_of_blue_packet),
        terminal_ambiguous_packet=asdict(ambiguous_packet),
        terminal_mutation_normal_packet=asdict(mutation_normal_packet),
        terminal_mutation_relaxed_packet=asdict(mutation_relaxed_packet),
        terminal_locked_packet=asdict(locked_packet),
        contextual_request=args.contextual_request,
        contextual_terminal_context=git_context,
        contextual_model_payload=contextual_model_payload,
        contextual_rag_packet=asdict(contextual_packet),
        out_of_blue_request=args.out_of_blue_request,
        out_of_blue_terminal_context=make_empty_terminal_context(),
        out_of_blue_model_payload=out_of_blue_model_payload,
        out_of_blue_rag_packet=asdict(out_of_blue_packet),
        inert_prose_blocks=inert_prose_blocks,
        warnings=warnings,
        failures=failures,
    )

    if failures:
        print("RAG text-console terminal-context smoke: FAIL", file=sys.stderr)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        if raw_ollama_responses:
            print("Raw Ollama response envelopes:", file=sys.stderr)
            for raw in raw_ollama_responses:
                print(raw, file=sys.stderr)
        return 1

    print("RAG text-console terminal-context smoke: PASS")
    print(f"used_ollama={used_ollama} base_url={args.base_url!r} model={args.model!r} paranoia={args.paranoia!r}")
    print()
    print("Exact terminal run creates a new terminal packet:")
    print(json.dumps(asdict(terminal_run_packet), indent=2, sort_keys=True))
    print()
    print("Exact terminal run-in active clones existing terminal packet:")
    print(json.dumps(asdict(terminal_run_in_packet), indent=2, sort_keys=True))
    print()
    print("Ambiguous terminal context packet:")
    print(json.dumps(asdict(ambiguous_packet), indent=2, sort_keys=True))
    print()
    print("RAG contextual terminal reuse payload:")
    print(json.dumps(contextual_model_payload, indent=2, sort_keys=True))
    print()
    print("RAG contextual terminal reuse render packet:")
    print(json.dumps(asdict(contextual_packet), indent=2, sort_keys=True))
    print()
    print("RAG out-of-blue terminal creation payload:")
    print(json.dumps(out_of_blue_model_payload, indent=2, sort_keys=True))
    print()
    print("RAG out-of-blue terminal creation render packet:")
    print(json.dumps(asdict(out_of_blue_packet), indent=2, sort_keys=True))
    print()
    print("Terminal mutation at normal paranoia packet:")
    print(json.dumps(asdict(mutation_normal_packet), indent=2, sort_keys=True))
    print()
    print("Terminal mutation at relaxed paranoia packet:")
    print(json.dumps(asdict(mutation_relaxed_packet), indent=2, sort_keys=True))
    print()
    print("Terminal locked paranoia packet:")
    print(json.dumps(asdict(locked_packet), indent=2, sort_keys=True))
    print()
    print("Inert assistant prose blocks:")
    print(json.dumps(inert_prose_blocks, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
