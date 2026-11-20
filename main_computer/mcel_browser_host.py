"""Standalone Browser child page mounted by the existing Applications shell."""
from __future__ import annotations

from pathlib import Path

_WEB = Path(__file__).with_name("web") / "applications"


def render_browser_page() -> str:
    html = (_WEB / "apps/browser-page.html").read_text(encoding="utf-8")
    css = (_WEB / "styles/browser-page.css").read_text(encoding="utf-8")
    js = (_WEB / "scripts/browser-page.js").read_text(encoding="utf-8")
    return html.replace("/* BROWSER_STYLES */", css).replace("/* BROWSER_SCRIPT */", js)
