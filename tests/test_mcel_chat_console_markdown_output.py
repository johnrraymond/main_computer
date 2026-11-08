"""Regression for live AI Markdown output in both canonical MCEL mounts.

Uses the exact shipped host pages and shared output-part renderer, without a
provider call, filesystem effects, or changes to saved user threads.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_chat_console_host import render_chat_console_page
from tools.mcel_chat_console_layout_oracle import _launch

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("host", ["standalone", "calculator-embedded"])
def test_markdown_ai_output_renders_without_global_terminal_helper(host):
    from playwright.sync_api import sync_playwright

    html = (render_chat_console_page(ROOT, promoted=True) if host == "standalone"
            else render_expanded_page(ROOT, promoted=True))
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.set_content(html, wait_until="load")
                result = page.evaluate('''() => {
                  const output = renderChatConsoleOutputPart({id: 'test-output'}, {
                    kind: 'markdown',
                    content: '## Model answer\\n**bold** and *italic* with `inline code`\\n- first\\n- second\\n\\nLiteral <img src=x onerror="window.__markdownInjected=true"> & text'
                  });
                  document.body.append(output);
                  return {
                    heading: output.querySelector('h4')?.textContent,
                    strong: output.querySelector('strong strong')?.textContent ?? output.querySelector('p strong')?.textContent,
                    italic: output.querySelector('p em')?.textContent,
                    code: output.querySelector('p code')?.textContent,
                    bullets: [...output.querySelectorAll('li')].map(item => item.textContent),
                    escaped: output.querySelector('.chat-output-markdown')?.innerHTML,
                    injectedImage: !!output.querySelector('img'),
                    executed: !!window.__markdownInjected
                  };
                }''')
                assert result["heading"] == "Model answer"
                assert result["strong"] == "bold"
                assert result["italic"] == "italic"
                assert result["code"] == "inline code"
                assert result["bullets"] == ["first", "second"]
                assert "&lt;img" in result["escaped"]
                assert "&amp; text" in result["escaped"]
                assert not result["injectedImage"]
                assert not result["executed"]
                assert not any("renderInlineMarkdown" in error for error in errors), errors
            finally:
                page.close()
        finally:
            browser.close()
