#!/usr/bin/env python3
"""
RAG text-console control-surface smoke test.

This is a contract smoke test for the next text-console renderer work.

It proves the first layer only:

    natural user request
      -> RAG/control interpretation
      -> app capability match
      -> text-console mount plan
      -> renderer-ready callouts

The browser UI renderer can be built on top of this shape later.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


TEXT_CONSOLE_SURFACE = "text_console"


@dataclass(frozen=True)
class AppCapability:
    app_id: str
    label: str
    intents: tuple[str, ...]
    targets: tuple[str, ...]
    actions: tuple[str, ...]
    mountable_surfaces: tuple[str, ...]
    option_hints: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class TextConsolePlan:
    surface: str
    user_request: str
    base_request: str
    with_clause: str
    intent: str
    target: str
    app_id: str
    action: str
    mount: str
    options: dict[str, Any]
    renderer_callouts: tuple[dict[str, Any], ...]


APP_CAPABILITIES: tuple[AppCapability, ...] = (
    AppCapability(
        app_id="file_explorer",
        label="File Explorer",
        intents=("show", "open", "find", "inspect", "list"),
        targets=("hidden files", "changed files", "repo root", "files", "folders"),
        actions=("mount", "show_hidden_files", "filter_changed_files", "open_repo_root"),
        mountable_surfaces=(TEXT_CONSOLE_SURFACE, "chat_console", "viewport"),
        option_hints={
            "hidden files": {"showHiddenFiles": True, "focus": "hidden_files"},
            "changed files": {"filter": "changed_files"},
            "repo root": {"initialLocation": "repo_root"},
            "read only": {"readOnly": True},
            "read-only": {"readOnly": True},
        },
    ),
    AppCapability(
        app_id="source_inspector",
        label="Source Inspector",
        intents=("show", "inspect", "verify", "cite", "open"),
        targets=("quoted passage", "source", "sources", "claim", "citation", "citations"),
        actions=("mount", "highlight_source_span", "show_citations"),
        mountable_surfaces=(TEXT_CONSOLE_SURFACE, "chat_console", "viewport"),
        option_hints={
            "claim": {"highlightClaimSource": True},
            "quoted passage": {"showExactQuotedSpan": True},
            "exact": {"showExactQuotedSpan": True},
            "sources": {"groupByDocument": True},
            "citations": {"groupByDocument": True},
        },
    ),
    AppCapability(
        app_id="document_canvas",
        label="Document Canvas",
        intents=("open", "edit", "show", "continue", "mount"),
        targets=("document canvas", "document", "canvas", "draft", "editor"),
        actions=("mount", "open_document_canvas"),
        mountable_surfaces=(TEXT_CONSOLE_SURFACE, "viewport"),
        option_hints={
            "sources pinned": {"sourcesPanel": "pinned"},
            "comments visible": {"commentsVisible": True},
            "edit suggestions": {"suggestions": True},
            "suggestions enabled": {"suggestions": True},
        },
    ),
    AppCapability(
        app_id="terminal",
        label="Terminal",
        intents=("run", "execute", "test", "dry-run", "dryrun"),
        targets=("dry-run patch", "dry run", "patch", "command", "test", "terminal"),
        actions=("mount", "prefill_command", "run_command"),
        mountable_surfaces=(TEXT_CONSOLE_SURFACE, "chat_console", "viewport"),
        option_hints={
            "verbose": {"verbose": True},
            "dry-run": {"mode": "dry_run"},
            "dry run": {"mode": "dry_run"},
            "repo root locked": {
                "workingDirectory": "repo_root",
                "lockWorkingDirectory": True,
            },
        },
    ),
    AppCapability(
        app_id="spreadsheet",
        label="Spreadsheet",
        intents=("show", "open", "convert", "chart", "mount"),
        targets=("spreadsheet", "table", "csv", "rows", "data"),
        actions=("mount", "import_table", "suggest_charts"),
        mountable_surfaces=(TEXT_CONSOLE_SURFACE, "viewport"),
        option_hints={
            "formulas preserved": {"preserveFormulas": True},
            "chart suggestions": {"chartSuggestions": True},
        },
    ),
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def split_with_clause(text: str) -> tuple[str, str]:
    match = re.search(r"\bwith\b", text, flags=re.IGNORECASE)
    if not match:
        return text.strip(), ""
    return text[: match.start()].strip(), text[match.end() :].strip()


def detect_intent(base_request: str) -> str:
    lowered = normalize(base_request)
    first_word = lowered.split(" ", 1)[0] if lowered else "show"

    aliases = {
        "display": "show",
        "view": "show",
        "list": "show",
        "launch": "open",
        "start": "open",
        "execute": "run",
    }
    return aliases.get(first_word, first_word)


def score_capability(capability: AppCapability, intent: str, full_request: str) -> int:
    lowered = normalize(full_request)
    score = 0

    if intent in capability.intents:
        score += 5

    for target in capability.targets:
        if target in lowered:
            score += 10 + len(target)

    for hint in capability.option_hints:
        if hint in lowered:
            score += 3

    return score


def choose_capability(intent: str, full_request: str) -> AppCapability:
    scored = [
        (score_capability(capability, intent, full_request), capability)
        for capability in APP_CAPABILITIES
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_capability = scored[0]
    if best_score <= 0:
        raise AssertionError(f"No app capability matched request: {full_request!r}")

    if TEXT_CONSOLE_SURFACE not in best_capability.mountable_surfaces:
        raise AssertionError(f"Capability cannot mount in text console: {best_capability.app_id}")

    return best_capability


def infer_target(capability: AppCapability, full_request: str) -> str:
    lowered = normalize(full_request)
    matches = [target for target in capability.targets if target in lowered]
    if matches:
        return sorted(matches, key=len, reverse=True)[0]
    return capability.targets[0]


def infer_action(capability: AppCapability, intent: str, target: str) -> str:
    if target == "hidden files" and "show_hidden_files" in capability.actions:
        return "show_hidden_files"

    if target == "changed files" and "filter_changed_files" in capability.actions:
        return "filter_changed_files"

    if target in {"claim", "source", "sources", "citation", "citations"}:
        if "show_citations" in capability.actions:
            return "show_citations"

    if intent in {"run", "execute", "test", "dry-run", "dryrun"}:
        if "prefill_command" in capability.actions:
            return "prefill_command"

    return "mount"


def infer_options(capability: AppCapability, full_request: str, with_clause: str) -> dict[str, Any]:
    lowered_all = normalize(f"{full_request} {with_clause}")
    options: dict[str, Any] = {}

    for hint, hinted_options in capability.option_hints.items():
        if hint in lowered_all:
            options.update(hinted_options)

    # Direct user language should be enough. The user should not need to name
    # the file explorer or say "with hidden files visible".
    if "hidden files" in lowered_all:
        options.setdefault("showHiddenFiles", True)
        options.setdefault("focus", "hidden_files")

    if "here" in lowered_all:
        options.setdefault("mountLocation", "current_surface")

    return options


def build_renderer_callouts(plan_base: dict[str, Any], capability: AppCapability) -> tuple[dict[str, Any], ...]:
    return (
        {
            "kind": "app_mount",
            "label": f"Open {capability.label}",
            "appId": capability.app_id,
            "surface": plan_base["surface"],
            "action": plan_base["action"],
            "options": plan_base["options"],
        },
        {
            "kind": "explain",
            "label": "Explain this action",
            "surface": plan_base["surface"],
            "payload": {
                "intent": plan_base["intent"],
                "target": plan_base["target"],
                "appId": capability.app_id,
            },
        },
    )


def plan_text_console_request(user_request: str) -> TextConsolePlan:
    base_request, with_clause = split_with_clause(user_request)
    intent = detect_intent(base_request)
    capability = choose_capability(intent, user_request)
    target = infer_target(capability, user_request)
    action = infer_action(capability, intent, target)
    options = infer_options(capability, user_request, with_clause)

    plan_base = {
        "surface": TEXT_CONSOLE_SURFACE,
        "user_request": user_request,
        "base_request": base_request,
        "with_clause": with_clause,
        "intent": intent,
        "target": target,
        "app_id": capability.app_id,
        "action": action,
        "mount": TEXT_CONSOLE_SURFACE,
        "options": options,
    }

    return TextConsolePlan(
        **plan_base,
        renderer_callouts=build_renderer_callouts(plan_base, capability),
    )


def looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False

    has_header = "|" in lines[0]
    has_separator = bool(
        re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", lines[1])
    )
    return has_header and has_separator


def callouts_for_block(kind: str, language: str, text: str) -> list[dict[str, Any]]:
    normalized = normalize(text)

    callouts: list[dict[str, Any]] = []

    if kind == "code":
        callouts.append(
            {
                "kind": "app_mount",
                "label": "Open in Code Editor",
                "appId": "code_editor",
                "surface": TEXT_CONSOLE_SURFACE,
                "options": {"language": language},
            }
        )

        if language in {"python", "py", "bash", "sh", "powershell", "ps1"}:
            callouts.append(
                {
                    "kind": "app_mount",
                    "label": "Run in Terminal",
                    "appId": "terminal",
                    "surface": TEXT_CONSOLE_SURFACE,
                    "options": {
                        "prefill": text,
                        "workingDirectory": "repo_root",
                    },
                }
            )

    if kind == "table":
        callouts.append(
            {
                "kind": "app_mount",
                "label": "Open as Spreadsheet",
                "appId": "spreadsheet",
                "surface": TEXT_CONSOLE_SURFACE,
                "options": {"importFormat": "markdown_table"},
            }
        )

    if "source" in normalized or "citation" in normalized or "citations" in normalized:
        callouts.append(
            {
                "kind": "app_mount",
                "label": "Inspect Sources",
                "appId": "source_inspector",
                "surface": TEXT_CONSOLE_SURFACE,
                "options": {"groupByDocument": True},
            }
        )

    return callouts


def inspect_plain_markdown_block(text: str) -> list[dict[str, Any]]:
    kind = "table" if looks_like_markdown_table(text) else "markdown"
    return [
        {
            "kind": kind,
            "text": text,
            "rendererCallouts": callouts_for_block(kind, "markdown", text),
        }
    ]


def inspect_ai_result_blocks(markdown: str) -> list[dict[str, Any]]:
    """
    Minimal renderer-prep inspector.

    This proves that AI output can be converted into typed renderable blocks
    before the real browser renderer exists.
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


def assert_plan(
    request: str,
    *,
    app_id: str,
    action: str | None = None,
    option_contains: dict[str, Any] | None = None,
) -> TextConsolePlan:
    plan = plan_text_console_request(request)

    assert plan.surface == TEXT_CONSOLE_SURFACE, asdict(plan)
    assert plan.mount == TEXT_CONSOLE_SURFACE, asdict(plan)
    assert plan.app_id == app_id, asdict(plan)

    if action is not None:
        assert plan.action == action, asdict(plan)

    if option_contains:
        for key, expected in option_contains.items():
            actual = plan.options.get(key)
            assert actual == expected, {
                "request": request,
                "key": key,
                "expected": expected,
                "actual": actual,
                "plan": asdict(plan),
            }

    assert plan.renderer_callouts, asdict(plan)
    assert plan.renderer_callouts[0]["kind"] == "app_mount", asdict(plan)
    assert plan.renderer_callouts[0]["surface"] == TEXT_CONSOLE_SURFACE, asdict(plan)

    return plan


def run_smoke() -> None:
    plans = [
        assert_plan(
            "Show me the hidden files.",
            app_id="file_explorer",
            action="show_hidden_files",
            option_contains={"showHiddenFiles": True, "focus": "hidden_files"},
        ),
        assert_plan(
            "Show me the hidden files with repo root selected and read only.",
            app_id="file_explorer",
            action="show_hidden_files",
            option_contains={
                "showHiddenFiles": True,
                "focus": "hidden_files",
                "initialLocation": "repo_root",
                "readOnly": True,
            },
        ),
        assert_plan(
            "Show me the source for this claim.",
            app_id="source_inspector",
            action="show_citations",
            option_contains={"highlightClaimSource": True},
        ),
        assert_plan(
            "Open the document canvas with sources pinned and edit suggestions enabled.",
            app_id="document_canvas",
            option_contains={"sourcesPanel": "pinned", "suggestions": True},
        ),
        assert_plan(
            "Run the dry-run patch with verbose output and repo root locked.",
            app_id="terminal",
            action="prefill_command",
            option_contains={
                "mode": "dry_run",
                "verbose": True,
                "workingDirectory": "repo_root",
                "lockWorkingDirectory": True,
            },
        ),
        assert_plan(
            "Show me this table as a spreadsheet with chart suggestions.",
            app_id="spreadsheet",
            option_contains={"chartSuggestions": True},
        ),
    ]

    sample_ai_result = """Here is the patch command:

```python
print("hello from text console")
```

| file | status |
| --- | --- |
| main_computer/web/text.html | candidate |
| main_computer/web/applications/scripts/chat-console.js | reference |

Source-backed answer: inspect citations before applying.
"""

    blocks = inspect_ai_result_blocks(sample_ai_result)

    assert any(block["kind"] == "code" for block in blocks), blocks
    assert any(
        callout["appId"] == "terminal"
        for block in blocks
        for callout in block["rendererCallouts"]
    ), blocks

    assert any(block["kind"] == "table" for block in blocks), blocks
    assert any(
        callout["appId"] == "spreadsheet"
        for block in blocks
        for callout in block["rendererCallouts"]
    ), blocks

    assert any(
        callout["appId"] == "source_inspector"
        for block in blocks
        for callout in block["rendererCallouts"]
    ), blocks

    print("RAG text-console control surface smoke: PASS")
    print()
    print("Example resolved command:")
    print(json.dumps(asdict(plans[0]), indent=2, sort_keys=True))
    print()
    print("Example inspected AI result blocks:")
    print(json.dumps(blocks, indent=2, sort_keys=True))


if __name__ == "__main__":
    run_smoke()
