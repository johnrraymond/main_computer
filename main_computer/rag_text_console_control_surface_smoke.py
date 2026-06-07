#!/usr/bin/env python3
"""
RAG text-console Ollama control-surface smoke test.

Purpose:
  Prove the text console can use a real Ollama model as the RAG/control
  interpreter before the rich renderer is built on top of that contract.

This smoke intentionally touches Ollama by default. It should fail if Ollama is
not reachable, the model is not pulled, or the model cannot return the required
structured text-console plan.

Run from repo root:

  python -S main_computer/rag_text_console_control_surface_smoke.py

Useful options:

  python -S main_computer/rag_text_console_control_surface_smoke.py --model gemma4:26b
  python -S main_computer/rag_text_console_control_surface_smoke.py --base-url http://127.0.0.1:11434
  python -S main_computer/rag_text_console_control_surface_smoke.py --request "Show me the hidden files with repo root selected and read only."
  python -S main_computer/rag_text_console_control_surface_smoke.py --offline-contract-only

Environment:
  OLLAMA_BASE_URL=http://127.0.0.1:11434
  OLLAMA_MODEL=gemma4:26b
  MAIN_COMPUTER_GREMLIN_MODEL=gemma4:26b

Contract under test:
  natural language request
    -> Ollama model receives RAG capability context
    -> model emits structured text-console mount plan
    -> validator confirms app/action/options/callouts
    -> deterministic result-block inspector proves renderer-ready callouts
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_REQUEST = "Show me the hidden files."
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = (
    os.environ.get("OLLAMA_MODEL")
    or os.environ.get("MAIN_COMPUTER_GREMLIN_MODEL")
    or "gemma4:26b"
)


APP_CAPABILITY_REGISTRY: dict[str, Any] = {
    "surface": "text_console",
    "apps": [
        {
            "app_id": "file_explorer",
            "label": "File Explorer",
            "mountable_surfaces": ["text_console", "chat_console", "viewport"],
            "capabilities": [
                {
                    "intent": "show",
                    "targets": ["files", "hidden files", "changed files", "repo root"],
                    "actions": ["mount", "show_hidden_files", "filter_changed_files"],
                    "options_schema": {
                        "showHiddenFiles": "boolean",
                        "focus": "hidden_files | changed_files | repo_root",
                        "initialLocation": "repo_root | current_directory",
                        "readOnly": "boolean",
                    },
                }
            ],
        },
        {
            "app_id": "source_inspector",
            "label": "Source Inspector",
            "mountable_surfaces": ["text_console", "chat_console", "viewport"],
            "capabilities": [
                {
                    "intent": "show",
                    "targets": ["source", "sources", "claim", "citation", "quoted passage"],
                    "actions": ["mount", "show_citations", "highlight_source_span"],
                    "options_schema": {
                        "highlightClaimSource": "boolean",
                        "showExactQuotedSpan": "boolean",
                        "groupByDocument": "boolean",
                    },
                }
            ],
        },
        {
            "app_id": "document_canvas",
            "label": "Document Canvas",
            "mountable_surfaces": ["text_console", "viewport"],
            "capabilities": [
                {
                    "intent": "open",
                    "targets": ["document", "canvas", "draft", "editor"],
                    "actions": ["mount", "open_document_canvas"],
                    "options_schema": {
                        "sourcesPanel": "pinned | hidden | floating",
                        "commentsVisible": "boolean",
                        "suggestions": "boolean",
                    },
                }
            ],
        },
        {
            "app_id": "terminal",
            "label": "Terminal",
            "mountable_surfaces": ["text_console", "chat_console", "viewport"],
            "capabilities": [
                {
                    "intent": "run",
                    "targets": ["command", "test", "patch", "dry run", "terminal"],
                    "actions": ["mount", "prefill_command", "run_command"],
                    "options_schema": {
                        "mode": "dry_run | normal",
                        "verbose": "boolean",
                        "workingDirectory": "repo_root | current_directory",
                        "lockWorkingDirectory": "boolean",
                    },
                }
            ],
        },
        {
            "app_id": "spreadsheet",
            "label": "Spreadsheet",
            "mountable_surfaces": ["text_console", "viewport"],
            "capabilities": [
                {
                    "intent": "show",
                    "targets": ["table", "spreadsheet", "csv", "rows", "data"],
                    "actions": ["mount", "import_table", "suggest_charts"],
                    "options_schema": {
                        "importFormat": "markdown_table | csv",
                        "preserveFormulas": "boolean",
                        "chartSuggestions": "boolean",
                    },
                }
            ],
        },
    ],
}


SYSTEM_PROMPT = """
You are the text-console RAG control interpreter for Main Computer.

Use only the supplied app capability registry. Do not invent apps, actions, or
surfaces. Translate the user's natural language request into one structured
mount/control plan for the text console.

Return only a single JSON object. Do not use markdown.

Required JSON shape:
{
  "surface": "text_console",
  "intent": "show | open | run | inspect | compare | convert",
  "target": "short target phrase from the user request",
  "app_id": "one app_id from the registry",
  "action": "one supported action from that app",
  "mount": "text_console",
  "options": {},
  "renderer_callouts": [
    {
      "kind": "app_mount",
      "label": "short user-facing label",
      "appId": "same as app_id",
      "surface": "text_console",
      "action": "same as action",
      "options": {}
    }
  ]
}

Important mapping examples:
- "Show me the hidden files." means file_explorer.show_hidden_files mounted in text_console
  with {"showHiddenFiles": true, "focus": "hidden_files"}.
- "Show me the source for this claim." means source_inspector.show_citations mounted in text_console
  with {"highlightClaimSource": true}.
- "Run the dry-run patch with verbose output and repo root locked." means terminal.prefill_command
  mounted in text_console with {"mode": "dry_run", "verbose": true, "workingDirectory": "repo_root",
  "lockWorkingDirectory": true}.
""".strip()


@dataclass(frozen=True)
class SmokeReport:
    ok: bool
    base_url: str
    model: str
    request: str
    used_ollama: bool
    plan: dict[str, Any]
    inspected_blocks: list[dict[str, Any]]
    warnings: list[str]
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def one_line(text: str, limit: int = 500) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def parse_bool(text: str) -> bool:
    value = normalize(text)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {text!r}")


def compact_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def make_user_payload(request_text: str) -> str:
    return compact_json(
        {
            "surface": "text_console",
            "user_request": request_text,
            "app_capability_registry": APP_CAPABILITY_REGISTRY,
            "task": (
                "Resolve the user_request into the required JSON plan. "
                "The output will be consumed by a text-console renderer."
            ),
        }
    )


def call_ollama_chat(
    *,
    base_url: str,
    model: str,
    request_text: str,
    timeout: float,
    think: bool | None,
) -> tuple[dict[str, Any], str]:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": make_user_payload(request_text)},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 800,
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

    return parse_model_plan(content), raw


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


def parse_model_plan(content: str) -> dict[str, Any]:
    text = strip_json_code_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = json.loads(extract_balanced_json_object(text))

    if not isinstance(parsed, dict):
        raise AssertionError(f"Model did not return a JSON object: {type(parsed).__name__}")

    for wrapper_key in ("plan", "text_console_plan", "mount_plan", "result"):
        wrapped = parsed.get(wrapper_key)
        if isinstance(wrapped, dict):
            parsed = wrapped
            break

    return parsed


def option_truthy(options: dict[str, Any], key: str) -> bool:
    value = options.get(key)
    return value is True or normalize(value) == "true"


def validate_text_console_plan(plan: dict[str, Any], request_text: str) -> list[str]:
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(plan.get("surface") == "text_console", f"surface must be text_console, got {plan.get('surface')!r}")
    require(plan.get("mount") == "text_console", f"mount must be text_console, got {plan.get('mount')!r}")
    require(normalize(plan.get("intent")) == "show", f"intent must be show for {request_text!r}, got {plan.get('intent')!r}")
    require("hidden" in normalize(plan.get("target")), f"target must mention hidden files, got {plan.get('target')!r}")
    require(plan.get("app_id") == "file_explorer", f"app_id must be file_explorer, got {plan.get('app_id')!r}")
    require(plan.get("action") == "show_hidden_files", f"action must be show_hidden_files, got {plan.get('action')!r}")

    options = plan.get("options")
    if not isinstance(options, dict):
        failures.append(f"options must be an object, got {type(options).__name__}")
        options = {}

    require(option_truthy(options, "showHiddenFiles"), f"options.showHiddenFiles must be true, got {options.get('showHiddenFiles')!r}")
    require(options.get("focus") == "hidden_files", f"options.focus must be hidden_files, got {options.get('focus')!r}")

    callouts = plan.get("renderer_callouts")
    require(isinstance(callouts, list) and bool(callouts), "renderer_callouts must be a non-empty list")
    if isinstance(callouts, list) and callouts:
        first = callouts[0]
        if not isinstance(first, dict):
            failures.append(f"first renderer callout must be object, got {type(first).__name__}")
        else:
            require(first.get("kind") == "app_mount", f"first callout kind must be app_mount, got {first.get('kind')!r}")
            require(first.get("appId") == "file_explorer", f"first callout appId must be file_explorer, got {first.get('appId')!r}")
            require(first.get("surface") == "text_console", f"first callout surface must be text_console, got {first.get('surface')!r}")

    return failures


def deterministic_plan_for_offline_contract(request_text: str) -> dict[str, Any]:
    lowered = normalize(request_text)
    if "hidden" not in lowered or "file" not in lowered:
        raise AssertionError("--offline-contract-only only supports the hidden-files contract request")

    return {
        "surface": "text_console",
        "intent": "show",
        "target": "hidden files",
        "app_id": "file_explorer",
        "action": "show_hidden_files",
        "mount": "text_console",
        "options": {
            "showHiddenFiles": True,
            "focus": "hidden_files",
        },
        "renderer_callouts": [
            {
                "kind": "app_mount",
                "label": "Open File Explorer",
                "appId": "file_explorer",
                "surface": "text_console",
                "action": "show_hidden_files",
                "options": {
                    "showHiddenFiles": True,
                    "focus": "hidden_files",
                },
            }
        ],
    }


def inspect_ai_result_blocks(markdown: str) -> list[dict[str, Any]]:
    """
    Minimal renderer-prep inspector.

    This is deliberately deterministic. The model owns the RAG/control plan;
    this inspector shows the next layer the UI renderer can build on.
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
            }
        )
        cursor = match.end()

    rest = markdown[cursor:].strip()
    if rest:
        blocks.extend(inspect_plain_markdown_block(rest))

    return blocks


def inspect_plain_markdown_block(text: str) -> list[dict[str, Any]]:
    if looks_like_markdown_table(text):
        return [
            {
                "kind": "table",
                "text": text,
                "rendererCallouts": callouts_for_block("table", "markdown", text),
            }
        ]

    return [
        {
            "kind": "markdown",
            "text": text,
            "rendererCallouts": callouts_for_block("markdown", "text", text),
        }
    ]


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
                "surface": "text_console",
                "options": {"language": language},
            }
        ]

        if language in {"python", "py"}:
            callouts.append(
                {
                    "kind": "app_mount",
                    "label": "Run in Terminal",
                    "appId": "terminal",
                    "surface": "text_console",
                    "options": {"prefill": text, "workingDirectory": "repo_root"},
                }
            )

        return callouts

    if kind == "table":
        callouts = [
            {
                "kind": "app_mount",
                "label": "Open as Spreadsheet",
                "appId": "spreadsheet",
                "surface": "text_console",
                "options": {"importFormat": "markdown_table"},
            }
        ]

        if "source" in normalize(text) or "citation" in normalize(text):
            callouts.append(
                {
                    "kind": "app_mount",
                    "label": "Inspect Sources",
                    "appId": "source_inspector",
                    "surface": "text_console",
                    "options": {"groupByDocument": True},
                }
            )

        return callouts

    if "source" in normalize(text) or "citation" in normalize(text):
        return [
            {
                "kind": "app_mount",
                "label": "Inspect Sources",
                "appId": "source_inspector",
                "surface": "text_console",
                "options": {"groupByDocument": True},
            }
        ]

    return []


def validate_result_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []

    def has_callout(app_id: str) -> bool:
        return any(
            callout.get("appId") == app_id
            for block in blocks
            for callout in block.get("rendererCallouts", [])
            if isinstance(callout, dict)
        )

    if not any(block.get("kind") == "code" for block in blocks):
        failures.append("result block inspector did not detect a code block")
    if not any(block.get("kind") == "table" for block in blocks):
        failures.append("result block inspector did not detect a table block")
    if not has_callout("terminal"):
        failures.append("result block inspector did not attach terminal callout to code")
    if not has_callout("spreadsheet"):
        failures.append("result block inspector did not attach spreadsheet callout to table")
    if not has_callout("source_inspector"):
        failures.append("result block inspector did not attach source inspector callout")

    return failures


def sample_ai_result() -> str:
    return """Here is the patch command:

```python
print("hello from text console")
```

| file | status |
| --- | --- |
| main_computer/web/text.html | candidate |
| main_computer/web/applications/scripts/chat-console.js | reference |

Source-backed answer: inspect citations before applying.
"""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG text-console Ollama control-surface smoke test.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Ollama base URL. Default: %(default)s")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model. Default: %(default)s")
    parser.add_argument("--request", default=DEFAULT_REQUEST, help="Natural language command to resolve.")
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
            "Do not call Ollama. This only exercises the local validator/renderer contract. "
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
    raw_ollama_response = ""

    if args.offline_contract_only:
        warnings.append("offline_contract_only was used; Ollama was not touched")
        plan = deterministic_plan_for_offline_contract(args.request)
        used_ollama = False
    else:
        plan, raw_ollama_response = call_ollama_chat(
            base_url=args.base_url,
            model=args.model,
            request_text=args.request,
            timeout=args.timeout,
            think=think,
        )
        used_ollama = True

    failures.extend(validate_text_console_plan(plan, args.request))

    blocks = inspect_ai_result_blocks(sample_ai_result())
    failures.extend(validate_result_blocks(blocks))

    report = SmokeReport(
        ok=not failures,
        base_url=args.base_url,
        model=args.model,
        request=args.request,
        used_ollama=used_ollama,
        plan=plan,
        inspected_blocks=blocks,
        warnings=warnings,
        failures=failures,
    )

    if failures:
        print("RAG text-console Ollama control surface smoke: FAIL", file=sys.stderr)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        if raw_ollama_response:
            print("Raw Ollama response envelope:", file=sys.stderr)
            print(raw_ollama_response, file=sys.stderr)
        return 1

    print("RAG text-console Ollama control surface smoke: PASS")
    print(f"used_ollama={used_ollama} base_url={args.base_url!r} model={args.model!r}")
    print()
    print("Model resolved command:")
    print(json.dumps(plan, indent=2, sort_keys=True))
    print()
    print("Renderer-ready inspected AI result blocks:")
    print(json.dumps(blocks, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
