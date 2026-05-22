#!/usr/bin/env python3
"""
Standalone Main Computer browser shell.

Install once:
    pip install playwright
    python -m playwright install chromium

Run:
    python standalone_browser.py

Open a specific URL:
    python standalone_browser.py http://127.0.0.1:8765/applications/spreadsheet

Commands while running:
    go <url>
    reload
    back
    forward
    title
    url
    js <javascript>
    screenshot <file.png>
    quit
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import NoReturn


DEFAULT_URL = "http://127.0.0.1:8765/applications/spreadsheet"
PROFILE_DIR = pathlib.Path(".main_computer_browser_profile").resolve()


def die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return DEFAULT_URL
    if "://" not in value and not value.startswith("about:"):
        return "https://" + value
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a controllable Chromium shell for Main Computer.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=950)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        die(
            "Playwright is not installed.\n\n"
            "Install it with:\n"
            "    pip install playwright\n"
            "    python -m playwright install chromium\n\n"
            f"Import error: {exc}"
        )

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=args.headless,
            viewport={"width": args.width, "height": args.height},
            args=[
                f"--window-size={args.width},{args.height}",
                "--new-window",
                "--disable-infobars",
            ],
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(normalize_url(args.url), wait_until="domcontentloaded")

        print("Main Computer browser shell is open.")
        print(f"Profile: {PROFILE_DIR}")
        print(f"URL: {page.url}")
        print("Type `help` for commands.")

        while True:
            try:
                command = input("browser> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not command:
                continue

            verb, _, rest = command.partition(" ")
            verb = verb.lower()
            rest = rest.strip()

            try:
                if verb in {"quit", "exit", "q"}:
                    break

                if verb == "help":
                    print(
                        "Commands:\n"
                        "  go <url>\n"
                        "  reload\n"
                        "  back\n"
                        "  forward\n"
                        "  title\n"
                        "  url\n"
                        "  js <javascript>\n"
                        "  screenshot <file.png>\n"
                        "  quit"
                    )

                elif verb == "go":
                    if not rest:
                        print("Usage: go <url>")
                        continue
                    page.goto(normalize_url(rest), wait_until="domcontentloaded")
                    print(page.url)

                elif verb == "reload":
                    page.reload(wait_until="domcontentloaded")
                    print("reloaded")

                elif verb == "back":
                    page.go_back(wait_until="domcontentloaded")
                    print(page.url)

                elif verb == "forward":
                    page.go_forward(wait_until="domcontentloaded")
                    print(page.url)

                elif verb == "title":
                    print(page.title())

                elif verb == "url":
                    print(page.url)

                elif verb == "js":
                    if not rest:
                        print("Usage: js <javascript>")
                        continue
                    result = page.evaluate(rest)
                    print(repr(result))

                elif verb == "screenshot":
                    target = pathlib.Path(rest or "browser-screenshot.png")
                    page.screenshot(path=str(target), full_page=True)
                    print(f"saved {target}")

                else:
                    print(f"Unknown command: {verb}")

            except Exception as exc:
                print(f"error: {exc}")

        context.close()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())