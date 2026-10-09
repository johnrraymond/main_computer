#!/usr/bin/env python3
"""Run arbitrary browser JavaScript as a Playwright/Chromium smoke test.

This is intentionally a small generic primitive rather than an app-specific
harness.  It can execute a JavaScript snippet against:

* a clean synthetic HTML page (default),
* an HTML fixture supplied with ``--html-file``, or
* a real page supplied with ``--url``.

The user snippet is compiled as an async function, so top-level ``await`` and
``return`` are both valid inside the snippet.  The variable ``args`` contains
the JSON value supplied with ``--args-json`` or ``--args-file``.

Pass/fail semantics:

* throwing/rejecting always fails;
* a boolean return value uses that boolean as the smoke result;
* an object containing ``ok`` uses ``bool(result.ok)``;
* any other successfully serializable return value passes;
* uncaught page errors fail by default;
* console errors are evidence by default and can be made fatal with
  ``--fail-on-console-error``.

Examples (PowerShell, from the repository root)::

    python .\\tools\\playwright_chromium_code_smoke.py `
      --code 'return {ok: document.readyState === "complete" || document.readyState === "interactive"};'

    python .\\tools\\playwright_chromium_code_smoke.py `
      --code-file .\\runtime\\probe.js

    python .\\tools\\playwright_chromium_code_smoke.py `
      --url http://127.0.0.1:8000/applications/webgl `
      --code-file .\\runtime\\webgl-probe.js `
      --screenshot .\\runtime\\webgl-probe.png

    Get-Content .\\runtime\\probe.js -Raw | `
      python .\\tools\\playwright_chromium_code_smoke.py --stdin
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "main-computer.playwright-chromium-code-smoke.v1"
DEFAULT_HTML = """<!doctype html>
<html>
<head><meta charset=\"utf-8\"><title>Playwright Chromium Code Smoke</title></head>
<body><main id=\"smoke-root\"></main></body>
</html>"""
DEFAULT_VIEWPORT = (1280, 800)
MAX_CAPTURED_EVENTS = 500


class CodeSmokeError(RuntimeError):
    """Raised for harness/setup failures rather than user-JavaScript failures."""


@dataclass(frozen=True)
class BrowserLaunch:
    browser: Any
    mode: str
    executable_path: str | None
    errors: tuple[str, ...]


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return value


def parse_viewport(text: str) -> tuple[int, int]:
    normalized = text.strip().lower()
    if "x" not in normalized:
        raise argparse.ArgumentTypeError("viewport must be WIDTHxHEIGHT")
    width_text, height_text = normalized.split("x", 1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("viewport must be WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("viewport dimensions must be positive")
    return width, height


def _json_load_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_args(args_json: str | None, args_file: Path | None) -> Any:
    if args_json is not None and args_file is not None:
        raise CodeSmokeError("Use only one of --args-json or --args-file.")
    if args_file is not None:
        return _json_load_file(args_file)
    if args_json is not None:
        return json.loads(args_json)
    return None


def load_code(*, code: str | None, code_file: Path | None, stdin: bool) -> str:
    selected = int(code is not None) + int(code_file is not None) + int(stdin)
    if selected != 1:
        raise CodeSmokeError("Choose exactly one code source: --code, --code-file, or --stdin.")
    if code_file is not None:
        source = code_file.read_text(encoding="utf-8")
    elif stdin:
        source = sys.stdin.read()
    else:
        source = str(code)
    if not source.strip():
        raise CodeSmokeError("JavaScript source is empty.")
    return source


def _candidate_system_chromium_paths() -> Iterable[str]:
    env_candidates = (
        os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
        os.environ.get("MCEL_CHROMIUM_EXECUTABLE"),
        os.environ.get("CHROMIUM_EXECUTABLE"),
    )
    for candidate in env_candidates:
        if candidate:
            yield candidate

    for command in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "msedge",
        "msedge.exe",
    ):
        resolved = shutil.which(command)
        if resolved:
            yield resolved

    # shutil.which() does not normally find GUI browser installs on Windows.
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    suffixes = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Chromium/Application/chrome.exe"),
    )
    for root in roots:
        if not root:
            continue
        for suffix in suffixes:
            candidate = Path(root) / suffix
            if candidate.exists():
                yield str(candidate)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = os.path.normcase(os.path.abspath(item))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def chromium_launch_args(*, headed: bool) -> list[str]:
    """Return launch flags suitable for deterministic smoke timing.

    Headed Chromium on Windows can suppress requestAnimationFrame when the
    browser window is occluded or backgrounded.  These flags keep a smoke
    page scheduled even when Playwright's window is not the foreground GUI
    window.  They are harmless in headless mode, but keep the extra policy
    scoped to headed runs so normal headless behavior stays minimal.
    """

    features = ["BlockInsecurePrivateNetworkRequests"]
    args = [
        "--no-proxy-server",
        "--proxy-bypass-list=*",
    ]
    if headed:
        args.extend(
            [
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ]
        )
        features.append("CalculateNativeWinOcclusion")
    args.append("--disable-features=" + ",".join(features))
    return args


def launch_chromium(playwright: Any, *, headed: bool) -> BrowserLaunch:
    """Launch Playwright Chromium, then fall back to an installed Chromium."""

    from playwright.sync_api import Error as PlaywrightError

    errors: list[str] = []
    common_args = chromium_launch_args(headed=headed)
    try:
        browser = playwright.chromium.launch(headless=not headed, args=common_args)
        return BrowserLaunch(browser, "playwright-managed", None, tuple(errors))
    except PlaywrightError as exc:
        errors.append(f"playwright-managed: {exc}")

    fallback_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        *common_args,
    ]
    for executable in _dedupe(_candidate_system_chromium_paths()):
        try:
            browser = playwright.chromium.launch(
                headless=not headed,
                executable_path=executable,
                args=fallback_args,
            )
            return BrowserLaunch(browser, "system-executable", executable, tuple(errors))
        except PlaywrightError as exc:
            errors.append(f"{executable}: {exc}")

    raise CodeSmokeError("Chromium launch failed: " + " | ".join(errors))


def classify_result(result: Any) -> tuple[bool, str]:
    if isinstance(result, bool):
        return result, "boolean-return"
    if isinstance(result, dict) and "ok" in result:
        return bool(result.get("ok")), "object-ok-field"
    return True, "successful-execution"


def _serialize_exception(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
    }


def _append_bounded(target: list[Any], item: Any) -> None:
    if len(target) < MAX_CAPTURED_EVENTS:
        target.append(item)


def run_smoke(
    *,
    source: str,
    user_args: Any = None,
    url: str | None = None,
    html_file: Path | None = None,
    headed: bool = False,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
    timeout_seconds: float = 30.0,
    settle_ms: int = 0,
    wait_until: str = "domcontentloaded",
    screenshot: Path | None = None,
    fail_on_console_error: bool = False,
    allow_page_errors: bool = False,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CodeSmokeError(
            "Playwright is required. Install dependencies and run `python -m playwright install chromium`."
        ) from exc

    if url and html_file:
        raise CodeSmokeError("Use at most one of --url or --html-file.")
    if settle_ms < 0:
        raise CodeSmokeError("settle_ms must be >= 0")

    console: list[dict[str, Any]] = []
    page_errors: list[dict[str, Any]] = []
    failed_requests: list[dict[str, Any]] = []
    browser_request_count = 0
    result: Any = None
    execution_error: dict[str, Any] | None = None
    launch: BrowserLaunch | None = None
    page_url: str | None = None
    browser_version: str | None = None
    navigation_ms = 0.0
    execution_ms = 0.0
    total_started = time.perf_counter()

    with sync_playwright() as playwright:
        launch = launch_chromium(playwright, headed=headed)
        browser_version = launch.browser.version
        context = launch.browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(timeout_seconds * 1000.0)
        page.set_default_navigation_timeout(timeout_seconds * 1000.0)

        def on_console(message: Any) -> None:
            _append_bounded(
                console,
                {
                    "type": message.type,
                    "text": message.text,
                    "location": dict(message.location or {}),
                },
            )

        def on_page_error(error: Any) -> None:
            _append_bounded(
                page_errors,
                {
                    "name": getattr(error, "name", type(error).__name__),
                    "message": str(error),
                    "stack": getattr(error, "stack", None),
                },
            )

        def on_request(_: Any) -> None:
            nonlocal browser_request_count
            browser_request_count += 1

        def on_request_failed(request: Any) -> None:
            failure = request.failure
            if callable(failure):
                failure = failure()
            _append_bounded(
                failed_requests,
                {
                    "url": request.url,
                    "method": request.method,
                    "resourceType": request.resource_type,
                    "failure": failure,
                },
            )

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("request", on_request)
        page.on("requestfailed", on_request_failed)

        try:
            nav_started = time.perf_counter()
            if url:
                page.goto(url, wait_until=wait_until, timeout=timeout_seconds * 1000.0)
            else:
                html = html_file.read_text(encoding="utf-8") if html_file else DEFAULT_HTML
                page.set_content(html, wait_until="domcontentloaded", timeout=timeout_seconds * 1000.0)
            navigation_ms = (time.perf_counter() - nav_started) * 1000.0
            page_url = page.url

            # In headed mode make the smoke page the active tab before user
            # JavaScript starts.  Together with the launch flags above this
            # prevents Windows/Chromium occlusion policy from collapsing rAF
            # to a single frame when the browser opens behind another window.
            if headed:
                page.bring_to_front()
                page.wait_for_timeout(100)

            runner = r"""
async ({source, args}) => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const wrapped = '"use strict";\n' + source + '\n//# sourceURL=playwright-chromium-code-smoke-user.js';
  const fn = new AsyncFunction('args', wrapped);
  return await fn(args);
}
"""
            execution_started = time.perf_counter()
            try:
                result = page.evaluate(runner, {"source": source, "args": user_args})
            except Exception as exc:  # Playwright serializes browser JS exceptions here.
                execution_error = _serialize_exception(exc)
            execution_ms = (time.perf_counter() - execution_started) * 1000.0

            if settle_ms:
                page.wait_for_timeout(settle_ms)

            if screenshot is not None:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=True)
        finally:
            context.close()
            launch.browser.close()

    result_ok, result_rule = classify_result(result) if execution_error is None else (False, "execution-error")
    fatal_page_errors = bool(page_errors) and not allow_page_errors
    fatal_console_errors = fail_on_console_error and any(item.get("type") == "error" for item in console)
    ok = bool(result_ok and execution_error is None and not fatal_page_errors and not fatal_console_errors)

    failed_reasons: list[str] = []
    if execution_error is not None:
        failed_reasons.append("execution-error")
    if not result_ok and execution_error is None:
        failed_reasons.append("result-signaled-failure")
    if fatal_page_errors:
        failed_reasons.append("page-error")
    if fatal_console_errors:
        failed_reasons.append("console-error")

    return {
        "ok": ok,
        "schema": SCHEMA,
        "source": {
            "kind": "url" if url else ("html-file" if html_file else "synthetic-html"),
            "url": url,
            "htmlFile": str(html_file) if html_file else None,
        },
        "browser": {
            "engine": "chromium",
            "version": browser_version,
            "headed": headed,
            "launchMode": launch.mode if launch is not None else None,
            "executablePath": launch.executable_path if launch is not None else None,
            "launchFallbackErrors": list(launch.errors) if launch is not None else [],
            "viewport": {"width": viewport[0], "height": viewport[1]},
            "pageUrl": page_url,
            "launchArgs": chromium_launch_args(headed=headed),
        },
        "execution": {
            "result": result,
            "resultRule": result_rule,
            "error": execution_error,
            "failedReasons": failed_reasons,
            "navigationMs": navigation_ms,
            "executionMs": execution_ms,
            "totalMs": (time.perf_counter() - total_started) * 1000.0,
            "settleMs": settle_ms,
            "timeoutSeconds": timeout_seconds,
        },
        "console": console,
        "pageErrors": page_errors,
        "network": {
            "requestCount": browser_request_count,
            "failedRequests": failed_requests,
        },
        "artifacts": {
            "screenshot": str(screenshot) if screenshot is not None else None,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run arbitrary JavaScript inside a real Playwright Chromium page and emit a JSON smoke receipt."
    )
    code_group = parser.add_mutually_exclusive_group(required=True)
    code_group.add_argument("--code", help="JavaScript statements. Top-level await/return are supported.")
    code_group.add_argument("--code-file", type=Path, help="UTF-8 JavaScript file to execute.")
    code_group.add_argument("--stdin", action="store_true", help="Read JavaScript from stdin.")

    page_group = parser.add_mutually_exclusive_group()
    page_group.add_argument("--url", help="Navigate to this page before executing the snippet.")
    page_group.add_argument("--html-file", type=Path, help="Load this UTF-8 HTML fixture before executing the snippet.")

    arg_group = parser.add_mutually_exclusive_group()
    arg_group.add_argument("--args-json", help="JSON value exposed to the snippet as `args`.")
    arg_group.add_argument("--args-file", type=Path, help="JSON file exposed to the snippet as `args`.")

    parser.add_argument("--headed", action="store_true", help="Show Chromium instead of running headless.")
    parser.add_argument("--viewport", type=parse_viewport, default=DEFAULT_VIEWPORT, metavar="WIDTHxHEIGHT")
    parser.add_argument("--timeout-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--settle-ms", type=int, default=0, help="Wait after the snippet before collecting final errors/screenshot.")
    parser.add_argument(
        "--wait-until",
        choices=("commit", "domcontentloaded", "load", "networkidle"),
        default="domcontentloaded",
        help="Navigation readiness state used with --url.",
    )
    parser.add_argument("--screenshot", type=Path, help="Optional full-page PNG/JPEG screenshot path.")
    parser.add_argument("--output", type=Path, help="Optional JSON report path. The report is always printed to stdout.")
    parser.add_argument(
        "--fail-on-console-error",
        action="store_true",
        help="Treat console.error messages as smoke failures.",
    )
    parser.add_argument(
        "--allow-page-errors",
        action="store_true",
        help="Record uncaught page errors without failing the smoke.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        if ns.settle_ms < 0:
            raise CodeSmokeError("--settle-ms must be >= 0.")
        source = load_code(code=ns.code, code_file=ns.code_file, stdin=ns.stdin)
        user_args = load_args(ns.args_json, ns.args_file)
        report = run_smoke(
            source=source,
            user_args=user_args,
            url=ns.url,
            html_file=ns.html_file,
            headed=ns.headed,
            viewport=ns.viewport,
            timeout_seconds=ns.timeout_seconds,
            settle_ms=ns.settle_ms,
            wait_until=ns.wait_until,
            screenshot=ns.screenshot,
            fail_on_console_error=ns.fail_on_console_error,
            allow_page_errors=ns.allow_page_errors,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "schema": SCHEMA,
            "harnessError": _serialize_exception(exc),
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if ns.output is not None:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
