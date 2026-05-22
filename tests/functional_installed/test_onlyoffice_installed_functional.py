from __future__ import annotations

import re
import time
from urllib.parse import quote

import pytest


pytestmark = [
    pytest.mark.installed_functional,
    pytest.mark.onlyoffice_functional,
]


def _expect(page):
    try:
        from playwright.sync_api import expect
    except Exception as exc:  # pragma: no cover - guarded by fixture
        pytest.skip(f"Playwright expect API is required: {exc}")
    return expect



def _wait_for_fake_ollama_request(fake_ollama_server, *, start_count: int = 0, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if len(fake_ollama_server.requests) > start_count:
            return fake_ollama_server.requests[-1]
        time.sleep(0.1)
    raise AssertionError(
        f"Embedded AI chat did not reach the fake Ollama-compatible server within {timeout_s:.0f}s; "
        f"request_count={len(fake_ollama_server.requests)}"
    )


def _wait_for_notebook_output_text(page, *, token: str, timeout_s: float = 20.0) -> str:
    """Return rendered notebook output text containing token.

    The prompt textarea also contains the expected token.  Restrict polling to
    output cells and use textContent instead of Playwright visibility because
    chat output sections can be clipped/collapsed while still being rendered in
    the notebook DOM.
    """

    output_selector = "#onlyoffice-advanced-chat-notebook .chat-cell-output .chat-output-part"
    deadline = time.monotonic() + timeout_s
    last_outputs: list[str] = []
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            outputs = page.locator(output_selector).evaluate_all(
                "(nodes) => nodes.map((node) => node.textContent || '')"
            )
            last_outputs = [str(item) for item in outputs]
            for text in last_outputs:
                if token in text:
                    return text
        except Exception as exc:  # pragma: no cover - diagnostic path for browser timing failures
            last_error = repr(exc)
        time.sleep(0.1)

    joined_outputs = "\n--- output cell ---\n".join(last_outputs)
    detail = joined_outputs if joined_outputs else "<no output cell text captured>"
    if last_error:
        detail = f"{detail}\nlast polling error: {last_error}"
    raise AssertionError(
        f"Timed out after {timeout_s:.0f}s waiting for {token!r} in notebook output cells.\n{detail}"
    )


def _open_onlyoffice(page, viewport_app):
    expect = _expect(page)
    page.goto(f"{viewport_app.base_url}/applications/onlyoffice", wait_until="domcontentloaded")
    page.wait_for_function("() => document.body.dataset.activeApp === 'onlyoffice'")
    expect(page.locator("#onlyoffice-app")).to_be_visible()
    expect(page.get_by_role("button", name="ONLYOFFICE")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#onlyoffice-server-status")).to_contain_text("Document Server online", timeout=10_000)


def _create_workbook_from_ui(page, name: str) -> None:
    expect = _expect(page)
    page.once("dialog", lambda dialog: dialog.accept(name))
    page.get_by_role("button", name="New XLSX").click()
    expect(page.locator("#onlyoffice-current-path")).to_contain_text(name, timeout=15_000)
    expect(page.locator("#onlyoffice-file-list")).to_contain_text(name)
    page.wait_for_function(
        "() => window.__onlyofficeFunctional && window.__onlyofficeFunctional.docEditorConstructed >= 1"
    )
    expect(page.locator("#onlyoffice-status")).to_contain_text("ONLYOFFICE editor", timeout=15_000)


def _reset_editor_viewport_fix(page) -> None:
    page.evaluate(
        """() => {
            if (window.onlyofficeResetEditorViewportFix) {
              window.onlyofficeResetEditorViewportFix({cancelTimers: true});
            }
            const app = document.querySelector("#onlyoffice-app");
            app?.classList.remove("onlyoffice-contained-editor-active");
            document.documentElement.classList.remove("onlyoffice-contained-editor-page");
            document.body.classList.remove("onlyoffice-contained-editor-page");
        }"""
    )


def _click_onlyoffice_file_item(page, workbook_name: str) -> None:
    """Click a workbook button after leaving the contained editor viewport.

    The contained editor mode intentionally hides the library while the embedded
    editor iframe is maximized.  The functional test needs to return to the
    workbook library before reopening an earlier file.  Use the UI button's
    click handler after making the library visible instead of calling the
    backend open endpoint directly.
    """

    _reset_editor_viewport_fix(page)
    page.wait_for_function(
        """(name) => {
            const app = document.querySelector("#onlyoffice-app");
            const list = document.querySelector("#onlyoffice-file-list");
            if (!app || !list || app.classList.contains("onlyoffice-contained-editor-active")) {
              return false;
            }
            return Array.from(list.querySelectorAll(".onlyoffice-file-item")).some((button) => (
              button.dataset.path === name || (button.textContent || "").includes(name)
            ));
        }""",
        arg=workbook_name,
        timeout=10_000,
    )
    page.evaluate(
        """(name) => {
            const button = Array.from(document.querySelectorAll("#onlyoffice-file-list .onlyoffice-file-item")).find((candidate) => (
              candidate.dataset.path === name || (candidate.textContent || "").includes(name)
            ));
            if (!button) throw new Error(`Workbook button not found: ${name}`);
            button.click();
        }""",
        workbook_name,
    )


def _extract_executable_javascript_from_output(text: str) -> str:
    """Extract the JavaScript body from notebook output text.

    Chat notebook output articles include UI chrome such as the "output" tab,
    Copy/Delete buttons, and can concatenate markdown paragraph text without
    whitespace.  Keep this helper deliberately narrow: the formula functional
    must execute a returned ONLYOFFICE Automation API snippet, not prose.
    """

    raw = str(text or "").strip()
    if not raw:
        return ""

    fenced = re.search(r"```(?:javascript|js)?\s*([\s\S]*?)```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()

    # Remove notebook chrome if a broader DOM node ever slips through.
    raw = re.sub(r"^(?:output|copy|delete output|\d+/\d+)+", "", raw, flags=re.I).strip()

    start_match = re.search(
        r"\b(?:const|let|var)\s+\w+\s*=\s*Api\.GetActiveSheet\s*\(|\bApi\.GetActiveSheet\s*\(",
        raw,
    )
    if not start_match:
        return ""

    return raw[start_match.start():].strip()


def _wait_for_notebook_output_code(page, *, token: str, timeout_s: float = 20.0) -> str:
    """Return executable JavaScript rendered by a notebook output part.

    Use only the output body/part nodes, not the entire output cell article.
    The article also contains the tab label "output"; reading that whole node is
    what previously produced invalid code beginning with "outputconst ...".
    """

    output_selector = (
        "#onlyoffice-advanced-chat-notebook .chat-cell-output .chat-output-part, "
        "#onlyoffice-advanced-chat-notebook .chat-console-cell-output .chat-output-part"
    )
    deadline = time.monotonic() + timeout_s
    last_outputs: list[str] = []
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            outputs = page.locator(output_selector).evaluate_all(
                """(nodes) => nodes.map((node) => {
                    const codeNode = node.querySelector("pre code, pre, code");
                    const markdown = node.querySelector(".chat-output-markdown");
                    const textFromChildren = (root) => Array.from(root.children || [])
                      .map((child) => child.textContent || "")
                      .filter(Boolean)
                      .join("\\n");
                    const codeText = codeNode ? (codeNode.textContent || "") : "";
                    const bodyText = markdown ? textFromChildren(markdown) : textFromChildren(node);
                    const fullText = bodyText || node.textContent || "";
                    return {codeText, fullText};
                })"""
            )
            last_outputs = []
            for item in outputs:
                code_text = str(item.get("codeText", "") if isinstance(item, dict) else "")
                full_text = str(item.get("fullText", "") if isinstance(item, dict) else item)
                last_outputs.append(code_text or full_text)
                for candidate in (code_text, full_text):
                    if token not in candidate:
                        continue
                    extracted = _extract_executable_javascript_from_output(candidate)
                    if extracted:
                        return extracted
        except Exception as exc:  # pragma: no cover - diagnostic path for browser timing failures
            last_error = repr(exc)
        time.sleep(0.1)

    joined_outputs = "\n--- output part ---\n".join(last_outputs)
    detail = joined_outputs if joined_outputs else "<no output part text captured>"
    if last_error:
        detail = f"{detail}\nlast polling error: {last_error}"
    raise AssertionError(
        f"Timed out after {timeout_s:.0f}s waiting for executable notebook output containing {token!r}.\n{detail}"
    )


def test_installed_onlyoffice_workbook_lifecycle_save_upload_callback_and_reopen(
    playwright_page,
    viewport_app,
    fake_onlyoffice_server,
    uploaded_xlsx,
):
    """Drive the installed UI through the main ONLYOFFICE workbook lifecycle."""

    page = playwright_page
    expect = _expect(page)
    _open_onlyoffice(page, viewport_app)

    workbook_name = "FunctionalGoldenPath.xlsx"
    _create_workbook_from_ui(page, workbook_name)

    expect(page.locator("#onlyoffice-contained-editor-stage")).to_be_visible(timeout=10_000)
    expect(page.locator("iframe.onlyoffice-contained-editor-frame")).to_be_visible(timeout=10_000)

    _reset_editor_viewport_fix(page)
    page.get_by_role("button", name="Save now").click()
    expect(page.locator("#onlyoffice-status")).to_contain_text("Force-save requested", timeout=10_000)
    assert fake_onlyoffice_server.commands, "Save now should POST a forcesave command to the ONLYOFFICE server"
    assert fake_onlyoffice_server.commands[-1].get("c") == "forcesave"
    assert fake_onlyoffice_server.commands[-1].get("key"), "forcesave command should include a document key"

    saved_name = "SavedByOnlyOfficeCallback.xlsx"
    source_url = f"{viewport_app.base_url}/api/applications/onlyoffice/file?path={quote(workbook_name)}"
    callback = page.evaluate(
        """async ({savedName, sourceUrl}) => {
            const response = await fetch(`/api/applications/onlyoffice/callback?path=${encodeURIComponent(savedName)}`, {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({status: 2, url: sourceUrl})
            });
            return {status: response.status, payload: await response.json()};
        }""",
        {"savedName": saved_name, "sourceUrl": source_url},
    )
    assert callback["status"] == 200
    assert callback["payload"] == {"error": 0}

    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("#onlyoffice-file-list")).to_contain_text(saved_name, timeout=10_000)

    page.set_input_files("#onlyoffice-upload-file", str(uploaded_xlsx))
    expect(page.locator("#onlyoffice-current-path")).to_contain_text("UploadedFunctional.xlsx", timeout=15_000)
    expect(page.locator("#onlyoffice-file-list")).to_contain_text("UploadedFunctional.xlsx", timeout=10_000)

    _click_onlyoffice_file_item(page, workbook_name)
    expect(page.locator("#onlyoffice-current-path")).to_contain_text(workbook_name, timeout=15_000)
    page.wait_for_function(
        "() => window.__onlyofficeFunctional && window.__onlyofficeFunctional.docEditorConstructed >= 3"
    )

    files_payload = page.evaluate(
        """async () => {
            const response = await fetch("/api/applications/onlyoffice/files", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: "{}"
            });
            return await response.json();
        }"""
    )
    assert files_payload["ok"] is True
    paths = {item["path"] for item in files_payload["files"]}
    assert {workbook_name, saved_name, "UploadedFunctional.xlsx"} <= paths


def test_installed_onlyoffice_advanced_code_addons_and_embedded_ai_chat(
    playwright_page,
    viewport_app,
    fake_ollama_server,
):
    """Exercise Advanced tools: JS/Python/BASIC add-ons plus a short AI notebook request."""

    page = playwright_page
    expect = _expect(page)
    _open_onlyoffice(page, viewport_app)
    _create_workbook_from_ui(page, "FunctionalAdvancedAndAi.xlsx")

    page.get_by_role("button", name="Advanced").click()
    expect(page.locator("#stage-advanced-pane")).to_be_visible(timeout=10_000)
    expect(page.locator("#onlyoffice-advanced-code-panel")).to_be_visible()
    expect(page.locator("#onlyoffice-advanced-chat-panel")).to_be_visible()
    expect(page.locator("#onlyoffice-advanced-chat-notebook")).to_be_visible(timeout=10_000)

    cases = [
        ("javascript", "const total = 40 + 2;"),
        ("python", "total = 40 + 2"),
        ("basic", 'PRINT "42"'),
    ]
    for language, source in cases:
        page.locator(f'input[name="onlyoffice-advanced-code-type"][value="{language}"]').check()
        page.locator("#onlyoffice-advanced-code-source").fill(source)
        page.get_by_role("button", name="Attach to selected cell(s)").click()
        expect(page.locator("#onlyoffice-advanced-code-status")).to_contain_text(
            "attached to B2:C3",
            timeout=5_000,
        )

    records = page.evaluate("() => window.__onlyofficeFunctional")
    assert records["connectorCreated"] >= 1
    assert len(records["comments"]) == 3
    comments = "\n\n".join(comment["text"] for comment in records["comments"])
    assert "Language: JS" in comments
    assert "Language: Python" in comments
    assert "Language: BASIC" in comments
    assert "Workbook: FunctionalAdvancedAndAi.xlsx" in comments

    notebook = page.locator("#onlyoffice-advanced-chat-notebook")
    textarea = notebook.locator("textarea").first
    expect(textarea).to_be_visible(timeout=10_000)
    textarea.fill("Reply with the token ONLYOFFICE_AI_OK and nothing else.")

    # The prompt itself contains ONLYOFFICE_AI_OK, so do not assert on the full
    # notebook text.  Click the Run button for the exact input cell, wait until
    # the backend AI subprocess reaches the fake Ollama server, then assert the
    # token appears in an output cell rendered by the UI.
    starting_request_count = len(fake_ollama_server.requests)
    ai_cell = textarea.locator(
        "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' chat-cell ')][1]"
    )
    ai_cell.get_by_role("button", name=re.compile(r"^Run$")).click()
    request = _wait_for_fake_ollama_request(
        fake_ollama_server,
        start_count=starting_request_count,
        timeout_s=30.0,
    )
    rendered_output = _wait_for_notebook_output_text(
        page,
        token="ONLYOFFICE_AI_OK",
        timeout_s=20.0,
    )
    assert "ONLYOFFICE_AI_OK" in rendered_output

    assert request.get("stream") is True
    assert request.get("model") == "functional-fast"
    assert request.get("think") is False
    prompt_text = "\n".join(message.get("content", "") for message in request.get("messages", []))
    assert "ONLYOFFICE_AI_OK" in prompt_text

def test_installed_onlyoffice_ai_formula_fills_eleventh_cell_with_sum(
    playwright_page,
    viewport_app,
    fake_ollama_server,
):
    """Ask embedded AI for a formula add-on, execute the returned code, and verify A11."""

    page = playwright_page
    expect = _expect(page)
    _open_onlyoffice(page, viewport_app)
    _create_workbook_from_ui(page, "FunctionalAiFormulaSum.xlsx")

    page.get_by_role("button", name="Advanced").click()
    expect(page.locator("#stage-advanced-pane")).to_be_visible(timeout=10_000)
    expect(page.locator("#onlyoffice-advanced-chat-notebook")).to_be_visible(timeout=10_000)

    numbers = [3, 5, 8, 13, 21, 34, 55, 89, 144, 233]
    expected_sum = sum(numbers)
    seeded = page.evaluate(
        """(values) => {
            const api = window.__onlyofficeFunctional;
            if (!api || typeof api.setCellValue !== "function") {
              throw new Error("fake ONLYOFFICE sheet helpers are not installed");
            }
            values.forEach((value, index) => api.setCellValue(`A${index + 1}`, value));
            api.selectRange("A1:A10");
            return api.cellSnapshot();
        }""",
        numbers,
    )
    assert seeded["selectedAddress"] == "A1:A10"
    assert seeded["cells"]["A1"]["value"] == numbers[0]
    assert seeded["cells"]["A10"]["value"] == numbers[-1]

    notebook = page.locator("#onlyoffice-advanced-chat-notebook")
    textarea = notebook.locator("textarea").first
    expect(textarea).to_be_visible(timeout=10_000)
    textarea.fill(
        "\n".join(
            [
                "ONLYOFFICE_FORMULA_SUM_A1_A10",
                "The selected spreadsheet range is A1:A10 and contains ten numbers:",
                ", ".join(str(value) for value in numbers),
                "Return ONLY JavaScript for the ONLYOFFICE Automation API.",
                "Set A11 to a formula that sums A1:A10. Do not hard-code the numeric sum.",
            ]
        )
    )

    starting_request_count = len(fake_ollama_server.requests)
    ai_cell = textarea.locator(
        "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' chat-cell ')][1]"
    )
    ai_cell.get_by_role("button", name=re.compile(r"^Run$")).click()
    request = _wait_for_fake_ollama_request(
        fake_ollama_server,
        start_count=starting_request_count,
        timeout_s=30.0,
    )
    ai_output = _wait_for_notebook_output_code(
        page,
        token="=SUM(A1:A10)",
        timeout_s=20.0,
    )
    assert "A11" in ai_output
    assert "=SUM(A1:A10)" in ai_output
    assert str(expected_sum) not in ai_output, "AI should produce a formula, not hard-code the known sum"

    prompt_text = "\n".join(message.get("content", "") for message in request.get("messages", []))
    assert "ONLYOFFICE_FORMULA_SUM_A1_A10" in prompt_text
    assert "A1:A10" in prompt_text
    assert "A11" in prompt_text
    assert all(str(value) in prompt_text for value in numbers)

    execution = page.evaluate(
        """(source) => {
            const api = window.__onlyofficeFunctional;
            if (!api || typeof api.executeAutomationSource !== "function") {
              throw new Error("fake ONLYOFFICE automation execution helper is not installed");
            }
            return api.executeAutomationSource(source);
        }""",
        ai_output,
    )
    a11 = execution["cells"]["A11"]
    assert a11["formula"] == "=SUM(A1:A10)"
    assert a11["value"] == expected_sum
    assert execution["formulaExecutions"][-1] == {
        "address": "A11",
        "formula": "=SUM(A1:A10)",
        "value": expected_sum,
    }
    assert f"A11={expected_sum}" in str(execution["result"])

