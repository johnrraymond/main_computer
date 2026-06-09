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
  - The model returns one ChatGPT-like assistant message string.
  - Executable mount points appear only inside fenced ```computer blocks.
  - Each ```computer block contains one or more exact single-command /act strings.
  - The trusted broker parses each fenced command, applies paranoia and plan
    limits, mints action/plan ids, and emits packets/render mounts.
  - Terminal "run" is the default when the user asks to run something.
  - Paranoia determines whether a runnable terminal command auto-runs,
    requires confirmation, downgrades to prefill, or is blocked.
  - If terminal context shows the user is already working in a relevant terminal,
    "now run git status" should target and clone that terminal with run-in.
  - If the same request is out of the blue, it should create a new terminal.
  - Multiple terminal commands inside one ```computer block become one broker-
    validated terminal plan packet.
  - Terminal owns executed/failed/skipped/interrupted plan state.
  - Assistant prose or non-computer code fences that merely talk about slash
    commands remain inert.

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
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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
PLAN_AUTO_RUN_STEP_LIMITS = {
    "locked": 0,
    "strict": 3,
    "normal": 3,
    "relaxed": 8,
}
DEFAULT_PLAN_REQUEST = "now run git status and then run the tests"
DEFAULT_CHAT_PATHWAY_SETUP_REQUESTS = (
    "hi",
    "What project is active?",
)
DEFAULT_CHAT_PATHWAY_CONTROL_REQUEST = (
    "Use a computer mount and request Terminal to list the files in main_computer. "
    "Do not just describe the files."
)



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
        "command_plans": (
            "multiple terminal commands are represented as a broker/RAG command_plan made from "
            "multiple exact single-command /act strings; the /act parser itself does not parse plans"
        ),
        "terminal_execution_state": "terminal app, not broker, owns executed/failed/skipped/interrupted state",
        "plan_auto_run_limits": PLAN_AUTO_RUN_STEP_LIMITS,
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
                {
                    "canonical": "/act terminal interrupt <terminal-ref>",
                    "action": "interrupt_terminal",
                    "auto_run": "terminal control action; blocked by locked paranoia",
                    "terminal_session": "target an existing terminal session",
                },
            ],
            "terminal_refs": ["active", "last", "term_<id>"],
            "paranoia_policy": {
                "relaxed": "auto-run non-destructive terminal commands",
                "normal": "auto-run read-only and dry-run/test commands; confirm mutation/unknown commands",
                "strict": "auto-run read-only only; confirm dry-run/test and mutation/unknown commands",
                "locked": "never auto-run; prefill only",
            },
            "plan_policy": {
                "plan_shape": "RAG may return kind=command_plan with canonical_commands; each string must parse as one exact /act command",
                "auto_run_step_limits": PLAN_AUTO_RUN_STEP_LIMITS,
                "execution_state_owner": "terminal",
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


SYSTEM_PROMPT = r"""
You are the Main Computer text-console assistant.

Return one normal assistant message as markdown-like text, not JSON.

CRITICAL: When the user asks to run, prepare, stop, interrupt, open, mount, or otherwise control the computer, the response MUST include at least one fenced code block with language `computer`. Never return only prose for an executable/control request.

When you want the computer to do something, include a fenced code block with
language `computer`.

Inside a `computer` fence:
- Write only exact /act commands, one per line.
- Do not include explanations, bullets, JSON, comments, or prose.
- One /act line means one action.
- Multiple /act lines in the same fence mean one sequential terminal plan.
- The /act grammar is strictly single-command only. Never invent /act terminal plan syntax.

Outside a `computer` fence:
- Write normal assistant prose before and/or after the mount point.
- You may explain what you are doing, but prose is non-authoritative and inert.
- The validated commands inside the `computer` fence are the contract.

Use only the supplied app capability registry and terminal context. Do not
invent commands.

Rules:
- If the user asks to run something, prefer /act terminal run <command> --cwd repo-root.
- If terminal_context.open_terminals contains a relevant active terminal and the
  user uses follow-up language like "now run ...", "run ... in the open
  terminal", or "run ... there", use /act terminal run-in active <command> --cwd repo-root.
- If there is no open terminal or the request comes out of the blue, use
  /act terminal run <command> --cwd repo-root so the broker creates a new terminal.
- If the user asks to prepare but not execute a command, use /act terminal prefill <command> --cwd repo-root.
- If the user asks to stop, cancel, halt, or interrupt the active terminal command, use /act terminal interrupt active.
- If the user asks to show hidden files, use /act file manager show hidden files.
- If the user writes a fuzzy command like /list directory .., decode it to /act file manager list directory ...
- Return commands that the trusted parser can accept exactly.

Examples:

User request: now run git status
Terminal context: active terminal term_git with recent_commands ["git status", "git diff"]
Return:
I'll reuse the active terminal for this.

```computer
/act terminal run-in active "git status" --cwd repo-root
```

I'll use the terminal result as the next-turn context.

User request: now run git status
Terminal context: no open terminals
Return:
I'll open a terminal and run the status check.

```computer
/act terminal run "git status" --cwd repo-root
```

I'll use the terminal output to continue.

User request: now run git status and then run the tests
Terminal context: active terminal term_git with recent_commands ["git status", "git diff"]
Return:
I'll run the repository check and then the tests in the active terminal.

```computer
/act terminal run-in active "git status" --cwd repo-root
/act terminal run-in active "python -m pytest" --cwd repo-root
```

I'll stop at the first failing command and use the terminal result as context.

User request: stop that command
Terminal context: active terminal term_git
Return:
I'll interrupt the active terminal command.

```computer
/act terminal interrupt active
```

Then I'll wait for the terminal state update.
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
    terminal_plan_model_payload: dict[str, Any]
    terminal_plan_mount_payload: dict[str, Any]
    terminal_plan_inline_render_blocks: list[dict[str, Any]]
    terminal_plan_packet: dict[str, Any]
    terminal_plan_result_packet: dict[str, Any]
    terminal_plan_render_packet: dict[str, Any]
    terminal_plan_failure_packet: dict[str, Any]
    terminal_plan_failure_result_packet: dict[str, Any]
    terminal_plan_mutation_pause_packet: dict[str, Any]
    terminal_plan_mutation_pause_result_packet: dict[str, Any]
    terminal_plan_locked_packet: dict[str, Any]
    terminal_plan_locked_result_packet: dict[str, Any]
    terminal_plan_interrupt_command_packet: dict[str, Any]
    terminal_plan_interrupt_result_packet: dict[str, Any]
    terminal_plan_max_step_rejection: dict[str, Any]
    contextual_request: str
    contextual_terminal_context: dict[str, Any]
    contextual_model_payload: dict[str, Any]
    contextual_mount_payload: dict[str, Any]
    contextual_inline_render_blocks: list[dict[str, Any]]
    contextual_rag_packet: dict[str, Any]
    out_of_blue_request: str
    out_of_blue_terminal_context: dict[str, Any]
    out_of_blue_model_payload: dict[str, Any]
    out_of_blue_mount_payload: dict[str, Any]
    out_of_blue_inline_render_blocks: list[dict[str, Any]]
    out_of_blue_rag_packet: dict[str, Any]
    chat_pathway_turn_reports: list[dict[str, Any]]
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

    if first == "interrupt":
        if len(tail) != 1:
            return None
        terminal_ref = tail[0]
        return ParsedSlashCommand(
            raw_input=text,
            prefix="/act",
            canonical_command=f"/act terminal interrupt {quote_arg(terminal_ref)}",
            app_phrase="terminal",
            app_id="terminal",
            action_phrase="interrupt",
            action="interrupt_terminal",
            args={"terminal_ref": terminal_ref},
            options={"terminalRef": terminal_ref, "requestedExecution": "interrupt"},
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

    if parsed.action == "interrupt_terminal":
        if level == "locked":
            return TerminalSafetyDecision(
                paranoia=level,
                risk="control",
                requested_mode="interrupt",
                execution_mode="prefill",
                auto_run=False,
                requires_confirmation=False,
                blocked=False,
                reason="Locked paranoia prepares terminal control actions instead of auto-running them.",
            )
        return TerminalSafetyDecision(
            paranoia=level,
            risk="control",
            requested_mode="interrupt",
            execution_mode="auto_run",
            auto_run=True,
            requires_confirmation=False,
            blocked=False,
            reason="Interrupt is a terminal control action that targets an existing terminal.",
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

    if parsed.action in {"run_in_terminal", "prefill_in_terminal", "interrupt_terminal"}:
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
                "Return one assistant message string. Put executable computer actions only "
                "inside fenced ```computer blocks. Inside each computer fence, write one "
                "or more exact single-command /act strings, one per line. Use terminal_context "
                "to decide whether to run in an existing terminal or create a new terminal."
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
    debug_label: str,
    print_ai_response: bool,
) -> tuple[dict[str, Any], str]:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": make_user_payload(request_text, terminal_context)},
        ],
        "stream": False,
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

    content_value = message.get("content")
    content = content_value if isinstance(content_value, str) else ""
    thinking_value = message.get("thinking")
    thinking = thinking_value if isinstance(thinking_value, str) else None

    debug_text = format_ai_response_debug(
        label=debug_label,
        request_text=request_text,
        content=content,
        raw_http_body=raw,
        thinking=thinking,
        force_thinking=not content.strip(),
    )

    if not content.strip():
        print(debug_text)
        raise AssertionError(
            "Ollama response had empty message.content, so there is no executable assistant "
            "message to parse. The smoke intentionally does not promote message.thinking into "
            "an executable ```computer mount. Try the default --think false mode, or pass "
            "--think false explicitly if this was run with --think omit/true."
        )

    if print_ai_response:
        print(debug_text)

    try:
        return parse_assistant_control_message(content), raw
    except AssertionError as exc:
        if not print_ai_response:
            print(debug_text)
        raise AssertionError(
            f"{exc}\n\nThe live AI response failed the text-console mount contract. "
            "The full assistant content and fence diagnostics were printed above."
        ) from exc


COMPUTER_FENCE_RE = re.compile(
    r"```[ \t]*(?P<language>computer)[^\n]*\n(?P<body>.*?)```",
    flags=re.DOTALL | re.IGNORECASE,
)


CODE_FENCE_START_RE = re.compile(r"```[ \t]*(?P<language>[^\n`]*)", flags=re.IGNORECASE)


def assistant_control_message_diagnostics(content: str) -> dict[str, Any]:
    message = str(content)
    fence_languages = [match.group("language").strip() for match in CODE_FENCE_START_RE.finditer(message)]
    computer_fence_starts = [
        match.group("language").strip()
        for match in CODE_FENCE_START_RE.finditer(message)
        if normalize(match.group("language").strip()).startswith("computer")
    ]
    act_like_lines = [
        line.strip()
        for line in message.splitlines()
        if "/act" in line
    ]

    likely_issue = "unknown"
    if not fence_languages and not act_like_lines:
        likely_issue = "no_code_fence_or_act_lines"
    elif computer_fence_starts and len(COMPUTER_FENCE_RE.findall(message)) == 0:
        likely_issue = "computer_fence_started_but_not_closed_or_missing_newline"
    elif fence_languages and not computer_fence_starts:
        likely_issue = "code_fences_present_but_not_language_computer"
    elif act_like_lines and not computer_fence_starts:
        likely_issue = "act_lines_outside_computer_fence"
    elif len(COMPUTER_FENCE_RE.findall(message)) > 0:
        likely_issue = "computer_fence_present_check_fence_body"

    return {
        "messageLength": len(message),
        "lineCount": len(message.splitlines()) or 1,
        "codeFenceMarkerCount": message.count("```"),
        "codeFenceStartLanguages": fence_languages,
        "computerFenceStartCount": len(computer_fence_starts),
        "completeComputerFenceCount": len(COMPUTER_FENCE_RE.findall(message)),
        "actLikeLineCount": len(act_like_lines),
        "actLikeLines": act_like_lines[:20],
        "likelyIssue": likely_issue,
        "oneLinePreview": one_line(message, limit=800),
    }


def format_ai_response_debug(
    *,
    label: str,
    request_text: str,
    content: str,
    raw_http_body: str | None = None,
    thinking: str | None = None,
    force_thinking: bool = False,
) -> str:
    diagnostics = assistant_control_message_diagnostics(content)
    has_thinking = isinstance(thinking, str) and bool(thinking.strip())
    lines = [
        "",
        f"=== Ollama assistant response: {label} ===",
        f"request: {request_text}",
        "diagnostics:",
        json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True),
        f"message_content_length: {len(content)}",
        f"message_thinking_length: {len(thinking) if isinstance(thinking, str) else 0}",
        "content:",
        content if content else "<empty message.content>",
    ]
    if has_thinking:
        lines.extend(
            [
                "thinking:",
                thinking if force_thinking else one_line(thinking, limit=2000),
            ]
        )
    lines.append("=== end Ollama assistant response ===")
    if raw_http_body is not None:
        lines.extend(
            [
                f"raw_http_body_length: {len(raw_http_body)}",
                f"raw_http_body_preview: {one_line(raw_http_body, limit=2400)}",
            ]
        )
    return "\n".join(lines)



def _message_history_preview(messages: list[Any]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        role = str(getattr(message, "role", "") or "")
        content = str(getattr(message, "content", "") or "")
        attachments = getattr(message, "attachments", None)
        preview.append(
            {
                "index": index,
                "role": role,
                "content_chars": len(content),
                "content_preview": one_line(content, limit=500),
                "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
            }
        )
    return preview


def call_chat_console_ai_pathway(
    *,
    base_url: str,
    model: str,
    request_text: str,
    timeout: float,
    think: bool | str | None,
    debug_label: str,
) -> dict[str, Any]:
    """Call Ollama through the same message/provider path used by chat-console AI cells.

    This intentionally does not use the stricter smoke-specific control prompt or
    structured JSON user payload. It builds the router context + notebook prompt
    messages the UI subprocess builds, then lets the normal OllamaProvider.chat
    streaming path produce the assistant response.
    """

    from main_computer.chat_console import ai_response_to_parts, build_notebook_ai_messages
    from main_computer.config import MainComputerConfig
    from main_computer.models import ChatMessage
    from main_computer.router import MainComputer, SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT

    config = MainComputerConfig.from_env()
    config = replace(
        config,
        provider="ollama",
        model=model,
        ollama_base_url=base_url,
        ollama_timeout_s=timeout,
        ollama_think=think if think is not None else config.ollama_think,
        workspace=Path.cwd(),
    )
    computer = MainComputer.build(config)
    provider = getattr(computer, "provider", None)
    if provider is None or not hasattr(provider, "chat"):
        raise AssertionError("chat-console pathway smoke could not build an Ollama chat provider")

    context_pack = computer.context_pack(request_text)
    web_search_context, web_search_text = computer._web_search_context(request_text)
    messages = [
        ChatMessage(role="system", content=ROUTER_SYSTEM_PROMPT),
        ChatMessage(role="system", content=context_pack.text),
        *([ChatMessage(role="system", content=web_search_text)] if web_search_text else []),
        *build_notebook_ai_messages(request_text, []),
    ]
    response = provider.chat(messages)
    content = str(getattr(response, "content", "") or "")
    output_parts = ai_response_to_parts(response)
    mount_request_parts = [part for part in output_parts if part.get("kind") == "mount_request"]
    diagnostics = assistant_control_message_diagnostics(content)

    return {
        "label": debug_label,
        "request": request_text,
        "provider": str(getattr(response, "provider", "") or ""),
        "model": str(getattr(response, "model", "") or ""),
        "content": content,
        "content_chars": len(content),
        "diagnostics": diagnostics,
        "message_history": _message_history_preview(messages),
        "web_search": web_search_context,
        "workspace_context": {
            "manifest_chars": getattr(context_pack, "manifest_chars", None),
            "evidence_count": len(getattr(context_pack, "evidence", []) or []),
        },
        "output_parts": output_parts,
        "mount_request_parts": mount_request_parts,
        "mount_request_part_count": len(mount_request_parts),
        "metadata": getattr(response, "metadata", {}) if isinstance(getattr(response, "metadata", {}), dict) else {},
    }


def synthetic_chat_console_ai_pathway_report(
    *,
    request_text: str,
    debug_label: str,
    content: str,
    provider: str = "offline-fixture",
    model: str = "offline-fixture",
) -> dict[str, Any]:
    from main_computer.chat_console import ai_response_to_parts
    from main_computer.models import ChatResponse

    response = ChatResponse(content=content, provider=provider, model=model, metadata={})
    output_parts = ai_response_to_parts(response)
    mount_request_parts = [part for part in output_parts if part.get("kind") == "mount_request"]
    return {
        "label": debug_label,
        "request": request_text,
        "provider": provider,
        "model": model,
        "content": content,
        "content_chars": len(content),
        "diagnostics": assistant_control_message_diagnostics(content),
        "message_history": [],
        "web_search": {"offline": True},
        "workspace_context": {"offline": True},
        "output_parts": output_parts,
        "mount_request_parts": mount_request_parts,
        "mount_request_part_count": len(mount_request_parts),
        "metadata": {},
    }


def validate_chat_pathway_turn_report(
    report: dict[str, Any],
    *,
    expect_mount: bool,
    expect_terminal_commands: bool = False,
) -> list[str]:
    label = str(report.get("label") or "")
    content = str(report.get("content") or "")
    diagnostics = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else assistant_control_message_diagnostics(content)
    mount_request_parts = report.get("mount_request_parts") if isinstance(report.get("mount_request_parts"), list) else []
    failures: list[str] = []

    if report.get("error"):
        failures.append(f"chat-console pathway turn {label!r} raised before producing a response: {report.get('error')}")
        return failures

    complete_computer_fence_count = int(diagnostics.get("completeComputerFenceCount") or 0)
    content_chars = len(content)

    if expect_mount:
        if complete_computer_fence_count < 1:
            failures.append(
                "chat-console pathway turn "
                f"{label!r} expected a computer mount but received none; "
                f"content_chars={content_chars}; diagnostics={json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)}"
            )
        if not mount_request_parts:
            failures.append(
                "chat-console pathway turn "
                f"{label!r} expected the UI part renderer to emit a mount_request part but got none"
            )

        try:
            payload = parse_assistant_control_message(content)
        except AssertionError as exc:
            failures.append(f"chat-console pathway turn {label!r} could not parse a valid assistant computer mount: {exc}")
            return failures

        commands: list[str] = []
        invalid_lines: list[str] = []
        for mount in payload.get("computer_mounts", []):
            commands.extend(str(command) for command in mount.get("canonicalCommands", []))
            invalid_lines.extend(str(line) for line in mount.get("invalidLines", []))
        if invalid_lines:
            failures.append(f"chat-console pathway turn {label!r} had invalid lines inside computer mount: {invalid_lines!r}")
        if not commands:
            failures.append(f"chat-console pathway turn {label!r} produced a computer mount with no exact /act commands")

        for command in commands:
            try:
                parsed = exact_parse_slash_command(command)
            except Exception as exc:
                failures.append(f"chat-console pathway turn {label!r} produced unparseable /act command {command!r}: {exc}")
                continue
            if expect_terminal_commands and parsed.app_id != "terminal":
                failures.append(
                    f"chat-console pathway turn {label!r} should request Terminal commands, "
                    f"but parsed {command!r} as app_id={parsed.app_id!r}"
                )
    else:
        if complete_computer_fence_count > 0 or mount_request_parts:
            failures.append(
                "chat-console pathway setup turn "
                f"{label!r} should not request a computer mount; diagnostics="
                f"{json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)}"
            )

    return failures


def run_chat_pathway_mount_expectation_smoke(
    *,
    base_url: str,
    model: str,
    setup_requests: list[str],
    control_request: str,
    timeout: float,
    think: bool | str | None,
    offline: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run a scripted UI-chat-path smoke with per-turn mount expectations."""

    turns: list[dict[str, Any]] = [
        {
            "label": f"chat setup turn {index}",
            "request": request,
            "expect_mount": False,
            "expect_terminal_commands": False,
        }
        for index, request in enumerate(setup_requests, start=1)
    ]
    turns.append(
        {
            "label": "chat terminal mount request turn",
            "request": control_request,
            "expect_mount": True,
            "expect_terminal_commands": True,
        }
    )

    reports: list[dict[str, Any]] = []
    failures: list[str] = []

    for turn in turns:
        label = str(turn["label"])
        request_text = str(turn["request"])
        if offline:
            if turn["expect_mount"]:
                content_payload = assistant_message_from_commands(
                    commands=['/act terminal run "dir main_computer" --cwd repo-root'],
                    leading_text="I'll request a terminal mount to list the project files.",
                    trailing_text="This offline fixture only proves the parser/render contract.",
                )
                content = str(content_payload.get("message") or "")
            else:
                content = "System online. I can help with the current workspace."
            report = synthetic_chat_console_ai_pathway_report(
                request_text=request_text,
                debug_label=label,
                content=content,
            )
        else:
            try:
                report = call_chat_console_ai_pathway(
                    base_url=base_url,
                    model=model,
                    request_text=request_text,
                    timeout=timeout,
                    think=think,
                    debug_label=label,
                )
            except Exception as exc:
                report = {
                    "label": label,
                    "request": request_text,
                    "expect_mount": bool(turn["expect_mount"]),
                    "error": repr(exc),
                    "content": "",
                    "content_chars": 0,
                    "diagnostics": assistant_control_message_diagnostics(""),
                    "output_parts": [],
                    "mount_request_parts": [],
                    "mount_request_part_count": 0,
                }

        report["expect_mount"] = bool(turn["expect_mount"])
        report["expect_terminal_commands"] = bool(turn["expect_terminal_commands"])
        reports.append(report)
        failures.extend(
            validate_chat_pathway_turn_report(
                report,
                expect_mount=bool(turn["expect_mount"]),
                expect_terminal_commands=bool(turn["expect_terminal_commands"]),
            )
        )

    return reports, failures


def call_live_control_payload_or_fixture(
    *,
    base_url: str,
    model: str,
    request_text: str,
    terminal_context: dict[str, Any] | None,
    timeout: float,
    think: bool | None,
    debug_label: str,
    print_ai_response: bool,
    fallback_payload: dict[str, Any],
) -> tuple[dict[str, Any], str, str | None]:
    try:
        payload, raw = call_ollama_chat(
            base_url=base_url,
            model=model,
            request_text=request_text,
            terminal_context=terminal_context,
            timeout=timeout,
            think=think,
            debug_label=debug_label,
            print_ai_response=print_ai_response,
        )
        return payload, raw, None
    except AssertionError as exc:
        return fallback_payload, "", str(exc)



def commands_from_computer_fence(source: str) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    invalid_lines: list[str] = []

    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/act "):
            commands.append(stripped)
        else:
            invalid_lines.append(stripped)

    return commands, invalid_lines


def derived_payload_from_computer_commands(commands: list[str]) -> dict[str, Any]:
    if len(commands) == 1:
        return {
            "canonical_command": commands[0],
            "reason": "Derived from a broker-validated assistant ```computer mount.",
        }

    return {
        "kind": "command_plan",
        "canonical_commands": commands,
        "mode": "sequential",
        "stop_on_failure": True,
        "reason": "Derived from a broker-validated multi-command assistant ```computer mount.",
    }


def parse_assistant_control_message(content: str) -> dict[str, Any]:
    message = content.strip()
    if not message:
        raise AssertionError("assistant message was empty")

    mounts: list[dict[str, Any]] = []
    for index, match in enumerate(COMPUTER_FENCE_RE.finditer(message), start=1):
        source = match.group("body").strip()
        commands, invalid_lines = commands_from_computer_fence(source)
        mount_id = "mount_" + hashlib.sha256(f"{index}\n{source}".encode("utf-8")).hexdigest()[:12]
        source_range = {"start": match.start(), "end": match.end()}

        derived_payload: dict[str, Any] = {}
        if commands:
            derived_payload = derived_payload_from_computer_commands(commands)

        mounts.append(
            {
                "mountId": mount_id,
                "language": "computer",
                "sourceRange": source_range,
                "source": source,
                "canonicalCommands": commands,
                "invalidLines": invalid_lines,
                "derivedPayload": derived_payload,
                "leadingText": message[: match.start()],
                "trailingText": message[match.end() :],
            }
        )

    if not mounts:
        diagnostics = assistant_control_message_diagnostics(message)
        raise AssertionError(
            "AI response did not contain an executable ```computer mount. "
            f"Diagnostics: {json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)}. "
            f"Content: {one_line(message)}"
        )

    return {
        "kind": "assistant_text_console_message",
        "message": message,
        "computer_mounts": mounts,
    }


def assistant_message_from_commands(
    *,
    leading_text: str,
    commands: list[str],
    trailing_text: str,
) -> dict[str, Any]:
    message = (
        leading_text.strip()
        + "\n\n```computer\n"
        + "\n".join(commands)
        + "\n```\n\n"
        + trailing_text.strip()
    )
    return parse_assistant_control_message(message)


def first_computer_mount(model_payload: dict[str, Any]) -> dict[str, Any]:
    if model_payload.get("kind") != "assistant_text_console_message":
        raise AssertionError(f"Expected assistant_text_console_message, got {model_payload!r}")

    mounts = model_payload.get("computer_mounts")
    if not isinstance(mounts, list) or not mounts:
        raise AssertionError(f"Assistant message has no computer mounts: {model_payload!r}")

    mount = mounts[0]
    if not isinstance(mount, dict):
        raise AssertionError(f"Assistant computer mount is not an object: {mount!r}")
    return mount


def payload_from_first_computer_mount(model_payload: dict[str, Any]) -> dict[str, Any]:
    if model_payload.get("kind") != "assistant_text_console_message":
        return model_payload

    mount = first_computer_mount(model_payload)
    invalid_lines = mount.get("invalidLines") or []
    if invalid_lines:
        raise AssertionError(f"Computer mount contains non-/act lines: {invalid_lines!r}")

    payload = mount.get("derivedPayload")
    if not isinstance(payload, dict) or not payload:
        raise AssertionError(f"Computer mount did not derive a command payload: {mount!r}")
    return payload


def render_inline_assistant_message_blocks(
    assistant_payload: dict[str, Any],
    mount_renders: dict[str, Any],
) -> list[dict[str, Any]]:
    if assistant_payload.get("kind") != "assistant_text_console_message":
        raise AssertionError(f"Expected assistant_text_console_message, got {assistant_payload!r}")

    message = assistant_payload.get("message")
    mounts = assistant_payload.get("computer_mounts")
    if not isinstance(message, str) or not isinstance(mounts, list):
        raise AssertionError(f"Malformed assistant message payload: {assistant_payload!r}")

    blocks: list[dict[str, Any]] = []
    cursor = 0
    for mount in mounts:
        if not isinstance(mount, dict):
            raise AssertionError(f"Malformed mount: {mount!r}")
        source_range = mount.get("sourceRange")
        if not isinstance(source_range, dict):
            raise AssertionError(f"Mount missing sourceRange: {mount!r}")
        start = int(source_range.get("start", -1))
        end = int(source_range.get("end", -1))
        if start < cursor or end < start or end > len(message):
            raise AssertionError(f"Invalid mount sourceRange {source_range!r} for message length {len(message)}")

        before = message[cursor:start]
        if before.strip():
            blocks.append({"kind": "markdown", "text": before.strip(), "brokerMounts": []})

        mount_id = mount.get("mountId")
        render = mount_renders.get(mount_id)
        if render is None:
            raise AssertionError(f"No broker render supplied for computer mount {mount_id!r}")

        if isinstance(render, RenderPacket):
            render_payload: Any = asdict(render)
        else:
            render_payload = render

        blocks.append(
            {
                "kind": "computer_mount",
                "mountId": mount_id,
                "language": "computer",
                "source": mount.get("source", ""),
                "sourceRange": source_range,
                "brokerRender": render_payload,
            }
        )
        cursor = end

    rest = message[cursor:]
    if rest.strip():
        blocks.append({"kind": "markdown", "text": rest.strip(), "brokerMounts": []})

    return blocks


def strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    exact_fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
    if exact_fence:
        return exact_fence.group(1).strip()

    embedded_fence = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if embedded_fence:
        return embedded_fence.group(1).strip()

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


def extract_probable_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no probable JSON object found")
    return text[start : end + 1]


def strip_loose_json_string(value: str) -> str:
    stripped = value.strip().rstrip(",").strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        stripped = stripped[1:-1]
    return stripped.replace('\\"', '"').replace("\\\\", "\\").strip()


def loose_json_string_field(text: str, field: str) -> str | None:
    match = re.search(
        rf'"{re.escape(field)}"\s*:\s*"(.*?)"\s*(?=,|\}})',
        text,
        flags=re.DOTALL,
    )
    if not match:
        return None
    return strip_loose_json_string('"' + match.group(1) + '"')


def loose_json_bool_field(text: str, field: str) -> bool | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def loose_json_act_command_field(text: str, field: str) -> str | None:
    match = re.search(
        rf'"{re.escape(field)}"\s*:\s*"(/act\s+.*?)"\s*(?=,|\}})',
        text,
        flags=re.DOTALL,
    )
    if not match:
        return None
    return strip_loose_json_string('"' + match.group(1) + '"')


def loose_json_act_command_array(text: str, field: str) -> list[str]:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*\[(.*?)\]', text, flags=re.DOTALL)
    if not match:
        return []

    body = match.group(1)
    commands: list[str] = []
    for command_match in re.finditer(r'"(/act\s+.*?)"\s*(?=,|\]|$)', body, flags=re.DOTALL):
        command = strip_loose_json_string('"' + command_match.group(1) + '"')
        if command:
            commands.append(command)
    return commands


def parse_loose_model_payload(text: str) -> dict[str, Any]:
    candidate = extract_probable_json_object(text)
    payload: dict[str, Any] = {}

    kind = loose_json_string_field(candidate, "kind")
    if kind is not None:
        payload["kind"] = kind

    canonical_commands = loose_json_act_command_array(candidate, "canonical_commands")
    if canonical_commands:
        payload["canonical_commands"] = canonical_commands
        payload.setdefault("kind", "command_plan")

    canonical_command = loose_json_act_command_field(candidate, "canonical_command")
    if canonical_command is not None:
        payload["canonical_command"] = canonical_command

    mode = loose_json_string_field(candidate, "mode")
    if mode is not None:
        payload["mode"] = mode

    stop_on_failure = loose_json_bool_field(candidate, "stop_on_failure")
    if stop_on_failure is not None:
        payload["stop_on_failure"] = stop_on_failure

    needs_terminal_selection = loose_json_bool_field(candidate, "needs_terminal_selection")
    if needs_terminal_selection is not None:
        payload["needs_terminal_selection"] = needs_terminal_selection

    reason = loose_json_string_field(candidate, "reason")
    if reason is not None:
        payload["reason"] = reason

    if not (
        payload.get("needs_terminal_selection")
        or isinstance(payload.get("canonical_command"), str)
        or payload.get("kind") == "command_plan"
    ):
        raise ValueError(f"could not recover model payload from text: {one_line(text)}")

    return payload


def parse_model_payload(content: str) -> dict[str, Any]:
    text = strip_json_code_fence(content)

    decode_errors: list[str] = []
    for candidate_factory in (
        lambda: text,
        lambda: extract_balanced_json_object(text),
        lambda: extract_probable_json_object(text),
    ):
        try:
            parsed = json.loads(candidate_factory())
            break
        except (json.JSONDecodeError, ValueError) as exc:
            decode_errors.append(str(exc))
    else:
        try:
            parsed = parse_loose_model_payload(text)
        except ValueError as exc:
            raise ValueError(
                "Could not parse Ollama message.content as a model payload. "
                f"Errors: {decode_errors}. Content: {one_line(text)}"
            ) from exc

    if not isinstance(parsed, dict):
        raise AssertionError(f"Model did not return a JSON object: {type(parsed).__name__}")

    return parsed

def validate_assistant_control_message_parser() -> list[str]:
    failures: list[str] = []

    if "```computer" not in SYSTEM_PROMPT:
        failures.append("SYSTEM_PROMPT must instruct the model to use fenced ```computer mounts")
    if "MUST include at least one fenced code block" not in SYSTEM_PROMPT:
        failures.append("SYSTEM_PROMPT must explicitly require a computer fence for executable/control requests")
    if "Return only a JSON object" in SYSTEM_PROMPT or '"canonical_command"' in SYSTEM_PROMPT:
        failures.append("SYSTEM_PROMPT should not ask the model for JSON command payloads")

    sample = """I'll run both checks in the active terminal.

```computer
/act terminal run-in active "git status" --cwd repo-root
/act terminal run-in active "python -m pytest" --cwd repo-root
```

I'll stop at the first failing command and use the result as context.
"""

    try:
        parsed = parse_assistant_control_message(sample)
    except Exception as exc:
        failures.append(f"assistant message with computer mount was not parsed: {exc}")
        return failures

    if parsed.get("kind") != "assistant_text_console_message":
        failures.append(f"assistant message parser returned wrong kind: {parsed!r}")

    mounts = parsed.get("computer_mounts")
    if not isinstance(mounts, list) or len(mounts) != 1:
        failures.append(f"expected one computer mount, got {mounts!r}")
        return failures

    mount = mounts[0]
    source_range = mount.get("sourceRange", {})
    start = source_range.get("start")
    end = source_range.get("end")
    message = parsed.get("message", "")
    if not isinstance(start, int) or not isinstance(end, int) or "```computer" not in message[start:end]:
        failures.append(f"mount sourceRange should cover the fenced computer block, got {source_range!r}")

    if "I'll run both checks" not in mount.get("leadingText", ""):
        failures.append("computer mount did not preserve leading assistant text")
    if "use the result as context" not in mount.get("trailingText", ""):
        failures.append("computer mount did not preserve trailing assistant text")

    derived = mount.get("derivedPayload")
    if not isinstance(derived, dict) or derived.get("kind") != "command_plan":
        failures.append(f"multi-line computer mount should derive command_plan, got {derived!r}")
    else:
        commands = derived.get("canonical_commands")
        if commands != [
            '/act terminal run-in active "git status" --cwd repo-root',
            '/act terminal run-in active "python -m pytest" --cwd repo-root',
        ]:
            failures.append(f"derived canonical_commands mismatch: {commands!r}")
        if derived.get("stop_on_failure") is not True:
            failures.append("derived computer plan should default stop_on_failure to true")

    non_computer = """This is documentation only.

```text
/act terminal run "git status" --cwd repo-root
```

Do not execute it.
"""
    try:
        parse_assistant_control_message(non_computer)
        failures.append("non-computer code fence should not become an executable assistant control message")
    except AssertionError:
        pass

    empty_content_debug = format_ai_response_debug(
        label="empty-content-regression",
        request_text="now run git status and then run the tests",
        content="",
        raw_http_body='{"message":{"content":"","thinking":"planned but not emitted"}}',
        thinking="planned but not emitted",
        force_thinking=True,
    )
    if "<empty message.content>" not in empty_content_debug:
        failures.append("empty-content Ollama debug output should make empty message.content explicit")
    if "thinking:" not in empty_content_debug or "planned but not emitted" not in empty_content_debug:
        failures.append("empty-content Ollama debug output should include message.thinking for diagnosis")

    return failures


def deterministic_model_payload_for_offline_contract(
    request_text: str,
    terminal_context: dict[str, Any] | None,
) -> dict[str, Any]:
    lowered = normalize(request_text)
    context = terminal_context or make_empty_terminal_context()
    terminals = context.get("open_terminals") if isinstance(context, dict) else []
    has_terminal = isinstance(terminals, list) and bool(terminals)
    active_terminal = resolve_terminal_reference("active", context)

    if any(word in lowered for word in ("stop", "interrupt", "cancel", "halt")) and "command" in lowered:
        return assistant_message_from_commands(
            leading_text="I'll interrupt the active terminal command.",
            commands=["/act terminal interrupt active"],
            trailing_text="Then I'll wait for the terminal state update.",
        )

    wants_git_status = "git status" in lowered or ("run" in lowered and "status" in lowered)
    wants_tests = "pytest" in lowered or "test" in lowered or "tests" in lowered
    wants_sequence = "then" in lowered or " and " in f" {lowered} "
    if wants_git_status and wants_tests and wants_sequence:
        if active_terminal is not None:
            return assistant_message_from_commands(
                leading_text="I'll run the repository check and then the tests in the active terminal.",
                commands=[
                    '/act terminal run-in active "git status" --cwd repo-root',
                    '/act terminal run-in active "python -m pytest" --cwd repo-root',
                ],
                trailing_text="I'll stop at the first failing command and use the terminal result as context.",
            )
        return assistant_message_from_commands(
            leading_text="I'll open a terminal and run the repository check followed by the tests.",
            commands=[
                '/act terminal run "git status" --cwd repo-root',
                '/act terminal run "python -m pytest" --cwd repo-root',
            ],
            trailing_text="I'll stop at the first failing command and use the terminal result as context.",
        )

    if wants_git_status:
        if active_terminal is not None and ("now" in lowered or "open terminal" in lowered or "there" in lowered):
            return assistant_message_from_commands(
                leading_text="I'll reuse the active terminal for this git status check.",
                commands=['/act terminal run-in active "git status" --cwd repo-root'],
                trailing_text="I'll use the terminal result as the next-turn context.",
            )
        if has_terminal and "open terminal" in lowered and active_terminal is None:
            return assistant_message_from_commands(
                leading_text="I'll ask the broker to resolve which open terminal should run this.",
                commands=['/act terminal run-in active "git status" --cwd repo-root'],
                trailing_text="The frontend should show a terminal selection mount if the active terminal is ambiguous.",
            )
        return assistant_message_from_commands(
            leading_text="I'll open a terminal and run the git status check.",
            commands=['/act terminal run "git status" --cwd repo-root'],
            trailing_text="I'll use the terminal output to continue.",
        )

    if "dry" in lowered and "patch" in lowered:
        return assistant_message_from_commands(
            leading_text="I'll run the patch dry-run command in a terminal.",
            commands=['/act terminal run "python new_patch.py patch.zip --dry-run" --cwd repo-root'],
            trailing_text="I'll inspect the dry-run output before suggesting any apply step.",
        )

    if "list" in lowered and "directory" in lowered and ".." in lowered:
        return assistant_message_from_commands(
            leading_text="I'll ask the file manager to list the parent directory.",
            commands=["/act file manager list directory .."],
            trailing_text="I'll use the listing as context.",
        )

    if "hidden" in lowered and "file" in lowered:
        return assistant_message_from_commands(
            leading_text="I'll ask the file manager to show hidden files.",
            commands=["/act file manager show hidden files"],
            trailing_text="Hidden files should now be visible in the file manager.",
        )

    raise AssertionError(f"offline contract fixture does not know how to resolve: {request_text!r}")


def canonical_command_from_model(model_payload: dict[str, Any]) -> str:
    payload = payload_from_first_computer_mount(model_payload)

    if payload.get("needs_terminal_selection"):
        raise AssertionError(f"Model requested terminal selection instead of a canonical command: {payload!r}")
    value = payload.get("canonical_command")
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"Model payload missing canonical_command string: {payload!r}")
    return value.strip()


def model_payload_kind(model_payload: dict[str, Any]) -> str:
    payload = payload_from_first_computer_mount(model_payload)

    if payload.get("needs_terminal_selection"):
        return "terminal_selection"
    if payload.get("kind") == "command_plan":
        return "command_plan"
    if isinstance(payload.get("canonical_command"), str):
        return "canonical_command"
    raise AssertionError(f"Unsupported model payload shape: {payload!r}")


def canonical_commands_from_plan_payload(model_payload: dict[str, Any]) -> list[str]:
    payload = payload_from_first_computer_mount(model_payload)

    if payload.get("kind") != "command_plan":
        raise AssertionError(f"Expected command_plan payload, got {payload!r}")

    commands = payload.get("canonical_commands")
    if not isinstance(commands, list) or len(commands) < 2:
        raise AssertionError("command_plan must contain at least two canonical_commands")

    cleaned: list[str] = []
    for command in commands:
        if not isinstance(command, str) or not command.strip():
            raise AssertionError(f"Invalid plan command: {command!r}")
        cleaned.append(command.strip())

    return cleaned


def plan_step_id(index: int) -> str:
    return f"step_{index}"


def stable_plan_id(canonical_commands: list[str], terminal_session_id: str) -> str:
    digest_source = terminal_session_id + "\n" + "\n".join(canonical_commands)
    return "plan_" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]


def terminal_plan_view_id(plan_id: str, steps: list[dict[str, Any]]) -> str:
    digest_source = plan_id + json.dumps(
        [{"stepId": step.get("stepId"), "command": step.get("command")} for step in steps],
        sort_keys=True,
    )
    return "view_plan_" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]


def plan_risk_label(risk: str) -> str:
    return "test" if risk == "dry_run_or_test" else risk


def broker_plan_rejection(
    *,
    reason: str,
    canonical_commands: list[str],
    paranoia: str,
    max_auto_run_steps: int,
    auto_run_steps: int,
) -> dict[str, Any]:
    return {
        "kind": "broker_plan_rejection",
        "reason": reason,
        "paranoia": paranoia,
        "maxAutoRunSteps": max_auto_run_steps,
        "autoRunSteps": auto_run_steps,
        "canonicalCommands": canonical_commands,
    }


def broker_build_terminal_plan_packet(
    model_payload: dict[str, Any],
    *,
    origin: str,
    paranoia: str,
    terminal_context: dict[str, Any] | None,
) -> dict[str, Any]:
    canonical_commands = canonical_commands_from_plan_payload(model_payload)
    level = normalize(paranoia or DEFAULT_PARANOIA)
    if level not in PARANOIA_LEVELS:
        level = DEFAULT_PARANOIA

    steps: list[dict[str, Any]] = []
    terminal_session_id = ""
    auto_run_steps = 0

    for index, canonical_command in enumerate(canonical_commands, start=1):
        parsed = exact_parse_slash_command(canonical_command)
        if not parsed:
            raise AssertionError(f"Plan command did not parse exactly: {canonical_command!r}")
        if parsed.app_id != "terminal":
            raise AssertionError(f"Plan command must target terminal, got {parsed.app_id!r}")

        parsed = resolve_action_terminal_context(parsed, terminal_context)
        if parsed.args.get("terminal_resolution") == "needs_terminal_selection":
            raise AssertionError(f"Plan command needs terminal selection: {canonical_command!r}")

        action = mint_broker_action(parsed)
        decision = decide_terminal_safety(parsed, level)
        session_id = parsed.args.get("terminal_session_id", "")
        if not session_id:
            raise AssertionError(f"Plan command did not resolve a terminal session: {canonical_command!r}")
        if not terminal_session_id:
            terminal_session_id = session_id
        elif session_id != terminal_session_id:
            raise AssertionError(
                f"Plan commands must target the same terminal session: {terminal_session_id!r} != {session_id!r}"
            )

        if decision.auto_run:
            auto_run_steps += 1

        steps.append(
            {
                "stepId": plan_step_id(index),
                "actionId": f"{action.action_id}_step_{index}",
                "canonicalCommand": parsed.canonical_command,
                "command": parsed.args.get("command", ""),
                "cwd": parsed.args.get("cwd", "repo-root"),
                "terminalSessionId": session_id,
                "parentViewId": parsed.args.get("parent_view_id", ""),
                "risk": plan_risk_label(decision.risk),
                "rawRisk": decision.risk,
                "executionMode": decision.execution_mode,
                "autoRun": decision.auto_run,
                "requiresConfirmation": decision.requires_confirmation,
                "blocked": decision.blocked,
                "status": "pending",
                "brokerDecisionReason": decision.reason,
            }
        )

    max_auto_run_steps = PLAN_AUTO_RUN_STEP_LIMITS[level]
    if auto_run_steps > max_auto_run_steps:
        return broker_plan_rejection(
            reason="too_many_auto_run_steps",
            canonical_commands=canonical_commands,
            paranoia=level,
            max_auto_run_steps=max_auto_run_steps,
            auto_run_steps=auto_run_steps,
        )

    payload = payload_from_first_computer_mount(model_payload)
    plan_id = stable_plan_id(canonical_commands, terminal_session_id)
    return {
        "kind": "terminal_command_plan_packet",
        "planId": plan_id,
        "terminalSessionId": terminal_session_id,
        "mode": str(payload.get("mode") or "sequential"),
        "stopOnFailure": bool(payload.get("stop_on_failure", True)),
        "origin": origin,
        "paranoia": level,
        "brokerPolicy": {
            "autoRunStepLimit": max_auto_run_steps,
            "autoRunSteps": auto_run_steps,
            "executionStateOwner": "terminal",
        },
        "steps": steps,
    }


FAKE_TERMINAL_EXIT_CODES = {
    "git status": 0,
    "python -m pytest": 0,
    "python -m pytest failing_test": 1,
    "python new_patch.py patch.zip --dry-run": 0,
}


def fake_terminal_consume_plan_packet(
    packet: dict[str, Any],
    *,
    interrupt_step_id: str | None = None,
) -> dict[str, Any]:
    if packet.get("kind") != "terminal_command_plan_packet":
        raise AssertionError(f"Fake terminal expected terminal_command_plan_packet, got {packet.get('kind')!r}")

    result_steps: list[dict[str, Any]] = []
    plan_status = "completed"
    stopped = False

    for step in packet.get("steps", []):
        result_step = dict(step)

        if stopped:
            result_step["status"] = "skipped"
            result_steps.append(result_step)
            continue

        if step.get("blocked"):
            result_step["status"] = "blocked"
            plan_status = "blocked"
            stopped = True
            result_steps.append(result_step)
            continue

        if step.get("executionMode") == "prefill":
            result_step["status"] = "prepared"
            if plan_status == "completed":
                plan_status = "prepared"
            result_steps.append(result_step)
            continue

        if step.get("requiresConfirmation"):
            result_step["status"] = "requires_confirmation"
            plan_status = "paused"
            stopped = True
            result_steps.append(result_step)
            continue

        if not step.get("autoRun"):
            result_step["status"] = "prepared"
            if plan_status == "completed":
                plan_status = "prepared"
            result_steps.append(result_step)
            continue

        if interrupt_step_id == step.get("stepId"):
            result_step["status"] = "interrupted"
            plan_status = "paused"
            stopped = True
            result_steps.append(result_step)
            continue

        exit_code = FAKE_TERMINAL_EXIT_CODES.get(str(step.get("command") or ""), 0)
        result_step["exitCode"] = exit_code
        if exit_code == 0:
            result_step["status"] = "executed"
        else:
            result_step["status"] = "failed"
            plan_status = "failed"
            if packet.get("stopOnFailure", True):
                stopped = True
        result_steps.append(result_step)

    return {
        "kind": "terminal_plan_result_packet",
        "planId": packet["planId"],
        "terminalSessionId": packet["terminalSessionId"],
        "terminalViewId": terminal_plan_view_id(packet["planId"], result_steps),
        "status": plan_status,
        "steps": result_steps,
    }


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
    elif action.action == "interrupt_terminal":
        answer = f"Interrupting Terminal command in {action.args.get('terminal_label', session_id)}."
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
            "commandKind": "interrupt" if action.action == "interrupt_terminal" else "command",
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


def build_terminal_plan_render_packet(
    plan_packet: dict[str, Any],
    result_packet: dict[str, Any],
    *,
    origin: str,
) -> RenderPacket:
    steps = result_packet.get("steps", [])
    mounted_object = {
        "kind": "mounted_app",
        "appId": "terminal",
        "label": "Terminal",
        "surface": TEXT_CONSOLE_SURFACE,
        "terminal": {
            "kind": "terminal_plan_view",
            "planId": plan_packet.get("planId"),
            "terminalSessionId": plan_packet.get("terminalSessionId"),
            "terminalViewId": result_packet.get("terminalViewId"),
            "status": result_packet.get("status"),
            "steps": steps,
        },
        "display": {
            "title": "Terminal",
            "subtitle": "Terminal command plan",
        },
    }
    return RenderPacket(
        answer_markdown=f"Running a {len(plan_packet.get('steps', []))}-step terminal plan in Terminal 1.",
        command_card={
            "kind": "canonical_command_plan",
            "label": "Equivalent command plan",
            "commands": [step.get("canonicalCommand") for step in plan_packet.get("steps", [])],
            "origin": origin,
        },
        broker_mounts=[
            {
                "kind": "auto_mount",
                "plan_id": plan_packet.get("planId"),
                "surface": TEXT_CONSOLE_SURFACE,
                "placement": "inline_after_answer",
            }
        ],
        mounted_object=mounted_object,
    )


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


def validate_terminal_plan_packet(
    packet: dict[str, Any],
    *,
    expected_step_count: int,
    expected_session_id: str,
) -> list[str]:
    failures: list[str] = []
    if packet.get("kind") != "terminal_command_plan_packet":
        failures.append(f"expected terminal_command_plan_packet, got {packet.get('kind')!r}")
        return failures

    if not packet.get("planId"):
        failures.append("plan packet must include a planId")
    if packet.get("terminalSessionId") != expected_session_id:
        failures.append(f"plan session expected {expected_session_id!r}, got {packet.get('terminalSessionId')!r}")

    steps = packet.get("steps")
    if not isinstance(steps, list) or len(steps) != expected_step_count:
        failures.append(f"plan should include {expected_step_count} steps, got {len(steps) if isinstance(steps, list) else steps!r}")
        return failures

    action_ids = [step.get("actionId") for step in steps]
    if len(set(action_ids)) != len(action_ids):
        failures.append("plan step action ids must be unique")
    for index, step in enumerate(steps, start=1):
        expected_step_id = plan_step_id(index)
        if step.get("stepId") != expected_step_id:
            failures.append(f"step {index} id expected {expected_step_id!r}, got {step.get('stepId')!r}")
        if not step.get("canonicalCommand"):
            failures.append(f"{expected_step_id} missing canonicalCommand")
        elif exact_parse_slash_command(str(step["canonicalCommand"])) is None:
            failures.append(f"{expected_step_id} canonicalCommand does not parse exactly")
        if step.get("terminalSessionId") != expected_session_id:
            failures.append(f"{expected_step_id} expected terminal session {expected_session_id!r}, got {step.get('terminalSessionId')!r}")
        if "command" not in step:
            failures.append(f"{expected_step_id} missing command")
        if "cwd" not in step:
            failures.append(f"{expected_step_id} missing cwd")
        if step.get("risk") not in {"read_only", "test", "mutation", "unknown", "destructive", "control"}:
            failures.append(f"{expected_step_id} unexpected risk {step.get('risk')!r}")
        if step.get("executionMode") not in {"auto_run", "confirmation_required", "prefill", "blocked"}:
            failures.append(f"{expected_step_id} unexpected executionMode {step.get('executionMode')!r}")
        if step.get("status") != "pending":
            failures.append(f"{expected_step_id} broker packet status must be pending, got {step.get('status')!r}")
    return failures


def validate_terminal_plan_result_packet(
    packet: dict[str, Any],
    *,
    expected_status: str,
    expected_step_statuses: list[str],
) -> list[str]:
    failures: list[str] = []
    if packet.get("kind") != "terminal_plan_result_packet":
        failures.append(f"expected terminal_plan_result_packet, got {packet.get('kind')!r}")
        return failures
    if packet.get("status") != expected_status:
        failures.append(f"plan result status expected {expected_status!r}, got {packet.get('status')!r}")
    steps = packet.get("steps")
    if not isinstance(steps, list):
        failures.append("plan result steps must be a list")
        return failures
    actual_statuses = [str(step.get("status")) for step in steps]
    if actual_statuses != expected_step_statuses:
        failures.append(f"plan step statuses expected {expected_step_statuses!r}, got {actual_statuses!r}")
    for step in steps:
        status = step.get("status")
        if status in {"executed", "failed"} and "exitCode" not in step:
            failures.append(f"{step.get('stepId')} terminal-owned {status!r} state must include exitCode")
    return failures


def validate_terminal_plan_render_packet(packet: RenderPacket) -> list[str]:
    failures: list[str] = []
    if packet.command_card.get("kind") != "canonical_command_plan":
        failures.append("plan render must use canonical_command_plan command card")
    if not packet.broker_mounts:
        failures.append("plan render should include a broker mount")
    terminal = packet.mounted_object.get("terminal", {})
    if terminal.get("kind") != "terminal_plan_view":
        failures.append(f"mounted terminal should be terminal_plan_view, got {terminal.get('kind')!r}")
    if not terminal.get("planId"):
        failures.append("terminal plan view missing planId")
    if not terminal.get("terminalSessionId"):
        failures.append("terminal plan view missing terminalSessionId")
    if not terminal.get("terminalViewId"):
        failures.append("terminal plan view missing terminalViewId")
    steps = terminal.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        failures.append("terminal plan view should include at least two steps")
    return failures


def validate_terminal_interrupt_packet(packet: RenderPacket, *, expected_session_id: str) -> list[str]:
    failures = validate_terminal_packet(
        packet,
        expected_command=packet.command_card.get("text", ""),
        expected_action="interrupt_terminal",
        expected_auto_run=True,
        expected_execution_mode="auto_run",
        expected_risk="control",
        expected_created_new=False,
        expected_reused_existing=True,
        expected_session_id=expected_session_id,
    )
    terminal = packet.mounted_object.get("terminal", {})
    if terminal.get("commandKind") != "interrupt":
        failures.append("interrupt packet terminal commandKind should be interrupt")
    return failures


def validate_plan_rejection(packet: dict[str, Any], *, expected_reason: str, expected_max: int) -> list[str]:
    failures: list[str] = []
    if packet.get("kind") != "broker_plan_rejection":
        failures.append(f"expected broker_plan_rejection, got {packet.get('kind')!r}")
    if packet.get("reason") != expected_reason:
        failures.append(f"rejection reason expected {expected_reason!r}, got {packet.get('reason')!r}")
    if packet.get("maxAutoRunSteps") != expected_max:
        failures.append(f"rejection maxAutoRunSteps expected {expected_max!r}, got {packet.get('maxAutoRunSteps')!r}")
    if packet.get("autoRunSteps", 0) <= expected_max:
        failures.append("rejection should report autoRunSteps above the limit")
    return failures


def validate_inert_prose_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    examples = [
        example.get("text")
        for block in blocks
        for example in block.get("slashCommandExamples", [])
    ]
    joined = json.dumps(blocks, sort_keys=True)
    if '/act terminal run "git status" --cwd repo-root' not in examples:
        failures.append("inert prose inspector did not capture first terminal slash-command example text")
    if '/act terminal run "python -m pytest" --cwd repo-root' not in examples:
        failures.append("inert prose inspector did not capture second terminal slash-command example text")
    if "canonical_command_plan" in joined or "terminal_command_plan_packet" in joined:
        failures.append("assistant prose containing multiple slash commands must not create a command plan")
    if any(block.get("brokerMounts") for block in blocks):
        failures.append("assistant prose/code blocks must not create broker mounts")
    for block in blocks:
        for example in block.get("slashCommandExamples", []):
            if example.get("executable") is not False:
                failures.append("slash command examples in prose must be marked executable=false")
    return failures


def sample_inert_assistant_prose() -> str:
    return """The feature can talk about slash commands without executing them.

For example:

/act terminal run "git status" --cwd repo-root
/act terminal run "python -m pytest" --cwd repo-root

Those lines are documentation only, not a broker-generated command plan.
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
    parser.add_argument("--plan-request", default=DEFAULT_PLAN_REQUEST, help="Natural multi-command request that should resolve to a command_plan.")
    parser.add_argument(
        "--chat-pathway-setup-request",
        action="append",
        default=None,
        help=(
            "Setup request for the UI chat-console pathway smoke. May be supplied multiple "
            "times. These turns must not produce computer mounts. Defaults to a greeting "
            "and workspace-orientation request."
        ),
    )
    parser.add_argument(
        "--chat-pathway-control-request",
        default=DEFAULT_CHAT_PATHWAY_CONTROL_REQUEST,
        help=(
            "Final UI chat-console pathway request that should make the model produce a "
            "previewable ```computer mount with exact /act Terminal command(s)."
        ),
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Ollama HTTP timeout in seconds.")
    parser.add_argument(
        "--think",
        choices=("omit", "true", "false"),
        default="false",
        help=(
            "Set Ollama think flag for models that support it. Default is false so the "
            "model spends tokens on message.content, not a separate thinking field."
        ),
    )
    parser.add_argument(
        "--offline-contract-only",
        action="store_true",
        help=(
            "Do not call Ollama. This only exercises the local parser/broker/renderer contract. "
            "Default behavior calls Ollama and fails if unavailable."
        ),
    )
    parser.add_argument(
        "--no-print-ai-responses",
        action="store_true",
        help=(
            "Suppress full live Ollama assistant messages on successful parses. "
            "Failures still print the full AI response and fence diagnostics."
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
    chat_pathway_turn_reports: list[dict[str, Any]] = []
    chat_pathway_setup_requests = [
        str(item or "").strip()
        for item in (args.chat_pathway_setup_request or list(DEFAULT_CHAT_PATHWAY_SETUP_REQUESTS))
        if str(item or "").strip()
    ]

    failures.extend(validate_assistant_control_message_parser())

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
        plan_model_payload = deterministic_model_payload_for_offline_contract(args.plan_request, git_context)
        interrupt_model_payload = deterministic_model_payload_for_offline_contract("stop that command", git_context)
        chat_pathway_turn_reports, chat_pathway_failures = run_chat_pathway_mount_expectation_smoke(
            base_url=args.base_url,
            model=args.model,
            setup_requests=chat_pathway_setup_requests,
            control_request=args.chat_pathway_control_request,
            timeout=args.timeout,
            think=think,
            offline=True,
        )
        failures.extend(chat_pathway_failures)
        used_ollama = False
    else:
        chat_pathway_turn_reports, chat_pathway_failures = run_chat_pathway_mount_expectation_smoke(
            base_url=args.base_url,
            model=args.model,
            setup_requests=chat_pathway_setup_requests,
            control_request=args.chat_pathway_control_request,
            timeout=args.timeout,
            think=think,
            offline=False,
        )
        failures.extend(chat_pathway_failures)

        contextual_model_payload, raw, error = call_live_control_payload_or_fixture(
            base_url=args.base_url,
            model=args.model,
            request_text=args.contextual_request,
            terminal_context=git_context,
            timeout=args.timeout,
            think=think,
            debug_label="contextual terminal reuse",
            print_ai_response=not args.no_print_ai_responses,
            fallback_payload=deterministic_model_payload_for_offline_contract(args.contextual_request, git_context),
        )
        if raw:
            raw_ollama_responses.append(raw)
        if error:
            failures.append(f"structured smoke Ollama call failed for contextual terminal reuse: {error}")

        out_of_blue_model_payload, raw, error = call_live_control_payload_or_fixture(
            base_url=args.base_url,
            model=args.model,
            request_text=args.out_of_blue_request,
            terminal_context=make_empty_terminal_context(),
            timeout=args.timeout,
            think=think,
            debug_label="out-of-blue terminal creation",
            print_ai_response=not args.no_print_ai_responses,
            fallback_payload=deterministic_model_payload_for_offline_contract(args.out_of_blue_request, make_empty_terminal_context()),
        )
        if raw:
            raw_ollama_responses.append(raw)
        if error:
            failures.append(f"structured smoke Ollama call failed for out-of-blue terminal creation: {error}")

        plan_model_payload, raw, error = call_live_control_payload_or_fixture(
            base_url=args.base_url,
            model=args.model,
            request_text=args.plan_request,
            terminal_context=git_context,
            timeout=args.timeout,
            think=think,
            debug_label="contextual terminal plan",
            print_ai_response=not args.no_print_ai_responses,
            fallback_payload=deterministic_model_payload_for_offline_contract(args.plan_request, git_context),
        )
        if raw:
            raw_ollama_responses.append(raw)
        if error:
            failures.append(f"structured smoke Ollama call failed for contextual terminal plan: {error}")

        interrupt_model_payload, raw, error = call_live_control_payload_or_fixture(
            base_url=args.base_url,
            model=args.model,
            request_text="stop that command",
            terminal_context=git_context,
            timeout=args.timeout,
            think=think,
            debug_label="terminal interrupt",
            print_ai_response=not args.no_print_ai_responses,
            fallback_payload=deterministic_model_payload_for_offline_contract("stop that command", git_context),
        )
        if raw:
            raw_ollama_responses.append(raw)
        if error:
            failures.append(f"structured smoke Ollama call failed for terminal interrupt: {error}")
        used_ollama = True

    contextual_mount_payload: dict[str, Any] = {}
    out_of_blue_mount_payload: dict[str, Any] = {}
    plan_mount_payload: dict[str, Any] = {}
    contextual_inline_render_blocks: list[dict[str, Any]] = []
    out_of_blue_inline_render_blocks: list[dict[str, Any]] = []
    terminal_plan_inline_render_blocks: list[dict[str, Any]] = []

    try:
        contextual_mount_payload = payload_from_first_computer_mount(contextual_model_payload)
        out_of_blue_mount_payload = payload_from_first_computer_mount(out_of_blue_model_payload)
        plan_mount_payload = payload_from_first_computer_mount(plan_model_payload)
    except Exception as exc:
        failures.append(f"assistant computer mount extraction failed: {exc}")

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
        contextual_inline_render_blocks = render_inline_assistant_message_blocks(
            contextual_model_payload,
            {first_computer_mount(contextual_model_payload)["mountId"]: contextual_packet},
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
        out_of_blue_inline_render_blocks = render_inline_assistant_message_blocks(
            out_of_blue_model_payload,
            {first_computer_mount(out_of_blue_model_payload)["mountId"]: out_of_blue_packet},
        )
    except Exception as exc:
        out_of_blue_packet = RenderPacket("", {}, [], {})
        failures.append(f"out-of-blue RAG terminal creation failed: {exc}")

    empty_plan_payload: dict[str, Any] = {}
    terminal_plan_packet: dict[str, Any] = {}
    terminal_plan_result_packet: dict[str, Any] = {}
    terminal_plan_render_packet = RenderPacket("", {}, [], {})
    terminal_plan_failure_packet: dict[str, Any] = {}
    terminal_plan_failure_result_packet: dict[str, Any] = {}
    terminal_plan_mutation_pause_packet: dict[str, Any] = {}
    terminal_plan_mutation_pause_result_packet: dict[str, Any] = {}
    terminal_plan_locked_packet: dict[str, Any] = {}
    terminal_plan_locked_result_packet: dict[str, Any] = {}
    terminal_plan_interrupt_command_packet = RenderPacket("", {}, [], {})
    terminal_plan_interrupt_result_packet: dict[str, Any] = {}
    terminal_plan_max_step_rejection: dict[str, Any] = {}

    try:
        if model_payload_kind(plan_model_payload) != "command_plan":
            failures.append(f"plan request should resolve to command_plan, got {plan_model_payload!r}")
        terminal_plan_packet = broker_build_terminal_plan_packet(
            plan_model_payload,
            origin="rag_decoded_contextual_terminal_plan",
            paranoia="normal",
            terminal_context=git_context,
        )
        if terminal_plan_packet.get("kind") == "broker_plan_rejection":
            failures.append(f"normal two-step plan should not be rejected: {terminal_plan_packet!r}")
        else:
            failures.extend(
                validate_terminal_plan_packet(
                    terminal_plan_packet,
                    expected_step_count=2,
                    expected_session_id="term_git",
                )
            )
            plan_commands = [step.get("canonicalCommand", "") for step in terminal_plan_packet.get("steps", [])]
            if not all(command.startswith('/act terminal run-in active ') for command in plan_commands):
                failures.append(f"contextual plan should reuse active terminal for every step, got {plan_commands!r}")

            terminal_plan_result_packet = fake_terminal_consume_plan_packet(terminal_plan_packet)
            failures.extend(
                validate_terminal_plan_result_packet(
                    terminal_plan_result_packet,
                    expected_status="completed",
                    expected_step_statuses=["executed", "executed"],
                )
            )
            terminal_plan_render_packet = build_terminal_plan_render_packet(
                terminal_plan_packet,
                terminal_plan_result_packet,
                origin="rag_decoded_contextual_terminal_plan",
            )
            failures.extend(validate_terminal_plan_render_packet(terminal_plan_render_packet))
            terminal_plan_inline_render_blocks = render_inline_assistant_message_blocks(
                plan_model_payload,
                {first_computer_mount(plan_model_payload)["mountId"]: terminal_plan_render_packet},
            )
            if not (
                len(terminal_plan_inline_render_blocks) == 3
                and terminal_plan_inline_render_blocks[0].get("kind") == "markdown"
                and terminal_plan_inline_render_blocks[1].get("kind") == "computer_mount"
                and terminal_plan_inline_render_blocks[2].get("kind") == "markdown"
            ):
                failures.append(
                    "plan assistant message should render as leading markdown, inline computer mount, trailing markdown"
                )
    except Exception as exc:
        terminal_plan_packet = empty_plan_payload
        failures.append(f"contextual RAG terminal plan failed: {exc}")

    failure_plan_payload = {
        "kind": "command_plan",
        "canonical_commands": [
            '/act terminal run-in active "git status" --cwd repo-root',
            '/act terminal run-in active "python -m pytest failing_test" --cwd repo-root',
            '/act terminal run-in active "python new_patch.py patch.zip --dry-run" --cwd repo-root',
        ],
        "mode": "sequential",
        "stop_on_failure": True,
        "reason": "Synthetic stop-on-failure plan fixture.",
    }
    try:
        terminal_plan_failure_packet = broker_build_terminal_plan_packet(
            failure_plan_payload,
            origin="synthetic_stop_on_failure_plan",
            paranoia="normal",
            terminal_context=git_context,
        )
        failures.extend(
            validate_terminal_plan_packet(
                terminal_plan_failure_packet,
                expected_step_count=3,
                expected_session_id="term_git",
            )
        )
        terminal_plan_failure_result_packet = fake_terminal_consume_plan_packet(terminal_plan_failure_packet)
        failures.extend(
            validate_terminal_plan_result_packet(
                terminal_plan_failure_result_packet,
                expected_status="failed",
                expected_step_statuses=["executed", "failed", "skipped"],
            )
        )
    except Exception as exc:
        failures.append(f"stop-on-failure terminal plan failed: {exc}")

    mutation_pause_plan_payload = {
        "kind": "command_plan",
        "canonical_commands": [
            '/act terminal run-in active "git status" --cwd repo-root',
            '/act terminal run-in active "python new_patch.py patch.zip" --cwd repo-root',
        ],
        "mode": "sequential",
        "stop_on_failure": True,
        "reason": "Synthetic partial auto-run then mutation pause fixture.",
    }
    try:
        terminal_plan_mutation_pause_packet = broker_build_terminal_plan_packet(
            mutation_pause_plan_payload,
            origin="synthetic_mutation_pause_plan",
            paranoia="normal",
            terminal_context=git_context,
        )
        failures.extend(
            validate_terminal_plan_packet(
                terminal_plan_mutation_pause_packet,
                expected_step_count=2,
                expected_session_id="term_git",
            )
        )
        pause_steps = terminal_plan_mutation_pause_packet.get("steps", [])
        if len(pause_steps) == 2:
            if pause_steps[0].get("autoRun") is not True:
                failures.append("mutation pause step_1 should be broker-authorized for auto-run")
            if pause_steps[1].get("executionMode") != "confirmation_required":
                failures.append("mutation pause step_2 should require confirmation under normal paranoia")
        terminal_plan_mutation_pause_result_packet = fake_terminal_consume_plan_packet(terminal_plan_mutation_pause_packet)
        failures.extend(
            validate_terminal_plan_result_packet(
                terminal_plan_mutation_pause_result_packet,
                expected_status="paused",
                expected_step_statuses=["executed", "requires_confirmation"],
            )
        )
    except Exception as exc:
        failures.append(f"mutation-pause terminal plan failed: {exc}")

    try:
        terminal_plan_locked_packet = broker_build_terminal_plan_packet(
            mutation_pause_plan_payload,
            origin="synthetic_locked_plan",
            paranoia="locked",
            terminal_context=git_context,
        )
        failures.extend(
            validate_terminal_plan_packet(
                terminal_plan_locked_packet,
                expected_step_count=2,
                expected_session_id="term_git",
            )
        )
        locked_steps = terminal_plan_locked_packet.get("steps", [])
        if any(step.get("autoRun") for step in locked_steps):
            failures.append("locked plan must not mark any step autoRun")
        if any(step.get("executionMode") != "prefill" for step in locked_steps):
            failures.append("locked plan should prefill every step")
        terminal_plan_locked_result_packet = fake_terminal_consume_plan_packet(terminal_plan_locked_packet)
        failures.extend(
            validate_terminal_plan_result_packet(
                terminal_plan_locked_result_packet,
                expected_status="prepared",
                expected_step_statuses=["prepared", "prepared"],
            )
        )
    except Exception as exc:
        failures.append(f"locked terminal plan failed: {exc}")

    try:
        interrupt_command = canonical_command_from_model(interrupt_model_payload)
        interrupt_parsed, interrupt_action, terminal_plan_interrupt_command_packet = broker_parse_and_render(
            interrupt_command,
            origin="rag_decoded_terminal_interrupt",
            paranoia="normal",
            terminal_context=git_context,
        )
        if interrupt_parsed.action != "interrupt_terminal":
            failures.append(f"interrupt request should resolve to interrupt_terminal, got {interrupt_parsed.action!r}")
        failures.extend(
            validate_terminal_interrupt_packet(
                terminal_plan_interrupt_command_packet,
                expected_session_id="term_git",
            )
        )
        if terminal_plan_packet.get("kind") == "terminal_command_plan_packet":
            terminal_plan_interrupt_result_packet = fake_terminal_consume_plan_packet(
                terminal_plan_packet,
                interrupt_step_id="step_2",
            )
            failures.extend(
                validate_terminal_plan_result_packet(
                    terminal_plan_interrupt_result_packet,
                    expected_status="paused",
                    expected_step_statuses=["executed", "interrupted"],
                )
            )
    except Exception as exc:
        failures.append(f"terminal interrupt plan flow failed: {exc}")

    max_step_plan_payload = {
        "kind": "command_plan",
        "canonical_commands": [
            '/act terminal run-in active "git status" --cwd repo-root',
            '/act terminal run-in active "git diff" --cwd repo-root',
            '/act terminal run-in active "git log --oneline" --cwd repo-root',
            '/act terminal run-in active "pwd" --cwd repo-root',
        ],
        "mode": "sequential",
        "stop_on_failure": True,
        "reason": "Synthetic too-many-auto-run-steps fixture.",
    }
    try:
        terminal_plan_max_step_rejection = broker_build_terminal_plan_packet(
            max_step_plan_payload,
            origin="synthetic_max_step_policy_plan",
            paranoia="normal",
            terminal_context=git_context,
        )
        failures.extend(
            validate_plan_rejection(
                terminal_plan_max_step_rejection,
                expected_reason="too_many_auto_run_steps",
                expected_max=PLAN_AUTO_RUN_STEP_LIMITS["normal"],
            )
        )
    except Exception as exc:
        failures.append(f"max-step terminal plan policy failed: {exc}")

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
        terminal_plan_model_payload=plan_model_payload,
        terminal_plan_mount_payload=plan_mount_payload,
        terminal_plan_inline_render_blocks=terminal_plan_inline_render_blocks,
        terminal_plan_packet=terminal_plan_packet,
        terminal_plan_result_packet=terminal_plan_result_packet,
        terminal_plan_render_packet=asdict(terminal_plan_render_packet),
        terminal_plan_failure_packet=terminal_plan_failure_packet,
        terminal_plan_failure_result_packet=terminal_plan_failure_result_packet,
        terminal_plan_mutation_pause_packet=terminal_plan_mutation_pause_packet,
        terminal_plan_mutation_pause_result_packet=terminal_plan_mutation_pause_result_packet,
        terminal_plan_locked_packet=terminal_plan_locked_packet,
        terminal_plan_locked_result_packet=terminal_plan_locked_result_packet,
        terminal_plan_interrupt_command_packet=asdict(terminal_plan_interrupt_command_packet),
        terminal_plan_interrupt_result_packet=terminal_plan_interrupt_result_packet,
        terminal_plan_max_step_rejection=terminal_plan_max_step_rejection,
        contextual_request=args.contextual_request,
        contextual_terminal_context=git_context,
        contextual_model_payload=contextual_model_payload,
        contextual_mount_payload=contextual_mount_payload,
        contextual_inline_render_blocks=contextual_inline_render_blocks,
        contextual_rag_packet=asdict(contextual_packet),
        out_of_blue_request=args.out_of_blue_request,
        out_of_blue_terminal_context=make_empty_terminal_context(),
        out_of_blue_model_payload=out_of_blue_model_payload,
        out_of_blue_mount_payload=out_of_blue_mount_payload,
        out_of_blue_inline_render_blocks=out_of_blue_inline_render_blocks,
        out_of_blue_rag_packet=asdict(out_of_blue_packet),
        chat_pathway_turn_reports=chat_pathway_turn_reports,
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
    print("Chat-console pathway scripted mount expectation turns:")
    print(json.dumps(chat_pathway_turn_reports, indent=2, sort_keys=True))
    print()
    print("RAG contextual assistant message payload:")
    print(json.dumps(contextual_model_payload, indent=2, sort_keys=True))
    print()
    print("RAG contextual terminal reuse render packet:")
    print(json.dumps(asdict(contextual_packet), indent=2, sort_keys=True))
    print()
    print("RAG out-of-blue assistant message payload:")
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
    print("RAG contextual assistant terminal plan payload:")
    print(json.dumps(plan_model_payload, indent=2, sort_keys=True))
    print()
    print("Broker terminal plan packet:")
    print(json.dumps(terminal_plan_packet, indent=2, sort_keys=True))
    print()
    print("Fake terminal plan result packet:")
    print(json.dumps(terminal_plan_result_packet, indent=2, sort_keys=True))
    print()
    print("Text console terminal plan render packet:")
    print(json.dumps(asdict(terminal_plan_render_packet), indent=2, sort_keys=True))
    print()
    print("Inline assistant message render blocks for terminal plan:")
    print(json.dumps(terminal_plan_inline_render_blocks, indent=2, sort_keys=True))
    print()
    print("Stop-on-failure plan result packet:")
    print(json.dumps(terminal_plan_failure_result_packet, indent=2, sort_keys=True))
    print()
    print("Mutation pause plan result packet:")
    print(json.dumps(terminal_plan_mutation_pause_result_packet, indent=2, sort_keys=True))
    print()
    print("Locked plan result packet:")
    print(json.dumps(terminal_plan_locked_result_packet, indent=2, sort_keys=True))
    print()
    print("Interrupt command packet:")
    print(json.dumps(asdict(terminal_plan_interrupt_command_packet), indent=2, sort_keys=True))
    print()
    print("Interrupt plan result packet:")
    print(json.dumps(terminal_plan_interrupt_result_packet, indent=2, sort_keys=True))
    print()
    print("Max-step policy rejection packet:")
    print(json.dumps(terminal_plan_max_step_rejection, indent=2, sort_keys=True))
    print()
    print("Inert assistant prose blocks:")
    print(json.dumps(inert_prose_blocks, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
