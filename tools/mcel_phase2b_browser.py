"""Launch a real Chromium-compatible browser for Phase 2B acceptance.

Do not equate "browser exists" with the presence of Unix-specific commands on
PATH. Playwright-managed Chromium, Edge and Chrome are supported on Windows,
Linux and macOS. This module is test infrastructure, not the MCEL runtime.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


class BrowserUnavailable(RuntimeError):
    """None of the explicit or discoverable browser launch options worked."""


def browser_launch_options() -> list[tuple[str, dict[str, Any]]]:
    """Ordered, nonduplicated launch strategies; explicit override is strict."""
    for name in ("MCEL_PHASE2B_BROWSER_EXECUTABLE", "MCEL_CHROMIUM_EXECUTABLE"):
        value = os.environ.get(name, "").strip().strip('"')
        if value:
            path = Path(value).expanduser()
            if not path.is_file():
                raise BrowserUnavailable(f"{name} does not name an existing browser executable: {value}")
            return [(f"{name}={path}", {"executable_path": str(path)})]

    options: list[tuple[str, dict[str, Any]]] = [
        ("playwright-chromium", {}),
        ("msedge", {"channel": "msedge"}),
        ("chrome", {"channel": "chrome"}),
    ]
    seen: set[str] = set()
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "msedge", "chrome"):
        found = shutil.which(name)
        if found and found not in seen:
            seen.add(found)
            options.append((f"PATH:{name}", {"executable_path": found}))
    return options


def launch_semantic_browser(playwright: Any) -> tuple[Any, str]:
    """Return a successfully launched browser and its source, or diagnostics."""
    failures: list[str] = []
    for label, options in browser_launch_options():
        try:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"], **options)
            return browser, label
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            failures.append(f"{label}: {message[:250]}")
    raise BrowserUnavailable(
        "No Chromium-compatible browser could be launched through Playwright. "
        "Run `python -m playwright install chromium` in this environment, or install Chrome/Edge. "
        "Alternatively set MCEL_PHASE2B_BROWSER_EXECUTABLE to the full browser executable path. "
        "Launch attempts: " + " | ".join(failures)
    )
