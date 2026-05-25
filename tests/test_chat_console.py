from __future__ import annotations

import unittest
from pathlib import Path

from main_computer.chat_console import (
    NOTEBOOK_AI_SYSTEM_PROMPT,
    ai_response_to_parts,
    build_notebook_ai_messages,
    build_output_cell,
    mathics_result_to_parts,
    validate_evaluation_cell,
)
from main_computer.models import ChatResponse
from main_computer.viewport import APPLICATIONS_INDEX_HTML


APPLICATIONS_HTML = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications.html"


class ChatConsoleHelperTests(unittest.TestCase):
    def test_evaluate_rejects_empty_input_sources(self) -> None:
        for cell_type in ("ai", "javascript", "python", "basic", "terminal", "mathics"):
            with self.subTest(cell_type=cell_type):
                with self.assertRaises(ValueError):
                    validate_evaluation_cell({"type": cell_type, "source": "   "})

    def test_comment_and_output_do_not_evaluate(self) -> None:
        for cell_type in ("comment", "output"):
            with self.subTest(cell_type=cell_type):
                with self.assertRaises(ValueError):
                    validate_evaluation_cell({"type": cell_type, "source": "hello"})

    def test_ai_response_parts_include_manual_snippets(self) -> None:
        parts = ai_response_to_parts(
            ChatResponse(
                content="```wl\nExpand[(x + 1)^2]\n```",
                provider="fake",
                model="fake-model",
            )
        )
        self.assertEqual(parts[0]["kind"], "markdown")
        self.assertEqual(parts[0]["snippets"][0]["kind"], "mathics")
        self.assertFalse(parts[0]["snippets"][0]["metadata"]["auto_promote"])

    def test_ai_response_parts_promote_code_snippets_to_code_cells(self) -> None:
        parts = ai_response_to_parts(
            ChatResponse(
                content="```python\nx = 2\n```\n```js\nvars.y = 3\n```\n```basic\nPRINT 4\n```",
                provider="fake",
                model="fake-model",
            )
        )

        snippets = parts[0]["snippets"]
        self.assertEqual([snippet["language"] for snippet in snippets], ["python", "js", "basic"])
        self.assertEqual([snippet["kind"] for snippet in snippets], ["code", "code", "code"])
        self.assertEqual(snippets[0]["suggested_target_cell_types"][0], "python")
        self.assertEqual(snippets[1]["suggested_target_cell_types"][0], "javascript")
        self.assertEqual(snippets[2]["suggested_target_cell_types"][0], "basic")

    def test_output_cell_can_hold_mixed_parts(self) -> None:
        cell = build_output_cell(
            {"id": "cell-1", "type": "ai", "source": "hello", "variant_index": 2},
            [
                {"id": "a", "kind": "markdown", "title": "A", "content": "hi", "language": "", "metadata": {}, "snippets": []},
                {"id": "b", "kind": "warning", "title": "B", "content": "watch", "language": "", "metadata": {}, "snippets": []},
            ],
        )
        self.assertEqual(cell["type"], "output")
        self.assertEqual(len(cell["parts"]), 2)
        self.assertEqual(cell["provenance"]["variant_index"], 2)

    def test_ai_messages_preserve_image_attachment_payload(self) -> None:
        messages = build_notebook_ai_messages(
            "describe this",
            [{"id": "img", "filename": "x.png", "mime_type": "image/png", "data_base64": "abcd", "kind": "image"}],
        )
        self.assertEqual(messages[-1].attachments[0].mime_type, "image/png")

    def test_notebook_ai_prompt_guides_standalone_mathics_snippets(self) -> None:
        self.assertIn("complete standalone inputs", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("pasted directly into a Mathics cell", NOTEBOOK_AI_SYSTEM_PROMPT)

    def test_notebook_ai_prompt_requires_canonical_mathics_capitalization(self) -> None:
        self.assertIn("canonical Wolfram/Mathics capitalization", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("Sin[x]", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("Cos[x]", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("Tan[x]", NOTEBOOK_AI_SYSTEM_PROMPT)

    def test_notebook_ai_prompt_forbids_lowercase_mathics_functions(self) -> None:
        self.assertIn("Do not use lowercase function names", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("sin[x]", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("cos[x]", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("tan[x]", NOTEBOOK_AI_SYSTEM_PROMPT)

    def test_notebook_ai_prompt_keeps_mathics_fences_code_only(self) -> None:
        self.assertIn("Do not put prose, Markdown, LaTeX dollar math, or comments inside mathics code fences", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("Put explanation outside the code fence", NOTEBOOK_AI_SYSTEM_PROMPT)

    def test_notebook_ai_prompt_keeps_terminal_snippets_manual(self) -> None:
        self.assertIn("Terminal snippets must be reviewable commands only", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("Do not instruct the system to auto-run terminal commands", NOTEBOOK_AI_SYSTEM_PROMPT)

    def test_notebook_ai_prompt_guides_code_cell_shared_variables(self) -> None:
        self.assertIn("JavaScript, Python, and BASIC snippets should be complete standalone code", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("shared variable context", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn('context.set("name"', NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn('vars["name"]', NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("GETVAR", NOTEBOOK_AI_SYSTEM_PROMPT)
        self.assertIn("SETVAR", NOTEBOOK_AI_SYSTEM_PROMPT)

    def test_frontend_threads_output_variants_by_selected_branch(self) -> None:
        html = APPLICATIONS_INDEX_HTML
        self.assertIn("thread_parent_output_cell_id", html)
        self.assertIn("function getChatConsoleChildrenForOutput", html)
        self.assertIn("function getChatConsoleSelectedOutputForSource", html)
        self.assertIn("function renderChatConsoleVisibleThread", html)
        self.assertIn("function renderChatConsoleCellAndSelectedContinuation", html)
        self.assertIn("function collectChatConsoleCellAndSelectedContinuation", html)
        self.assertIn('if (cell.type === "output" && nextCell && nextCell.type !== "output")', html)
        self.assertIn('if (visibleCell.type === "output" && nextCell && nextCell.type !== "output")', html)
        self.assertIn("source.selected_output_variant_index = targetIndex", html)
        self.assertIn("thread_parent_output_cell_id: outputCell.id", html)
        self.assertIn("cell.promoted_from?.promoted_from_output_cell_id || null", html)
        self.assertIn("function chatConsoleCanApplyEvaluationResult", html)
        self.assertIn("function chatConsolePersistEvaluationOutputToThread", html)
        self.assertIn("function chatConsoleApplyOrPersistEvaluationOutput", html)
        self.assertIn("const evaluationThreadId = chatConsoleState?.id ||", html)
        self.assertIn("chatConsoleNormalizeOutputForSource(outputCell, sourceCell, variantIndex)", html)
        self.assertIn("ensureChatConsoleContinuationAfterOutput(outputCell, sourceCell)", html)
        self.assertIn("chatConsoleApplyOrPersistEvaluationOutput(", html)
        self.assertIn("store.saveThread(threadId, draft, {replace: true, makeActive: false})", html)
        self.assertNotIn("+ 1 + variantIndex", html)

    def test_frontend_copies_output_cells_as_structured_rich_blocks(self) -> None:
        html = APPLICATIONS_INDEX_HTML
        self.assertIn('wrap.className = "chat-output-cell-block"', html)
        self.assertIn('chatConsoleButton("Copy", () => copyChatConsoleOutputCell(cell.id))', html)
        self.assertIn("function serializeOutputCellToPlainText", html)
        self.assertIn("function serializeOutputCellToHtml", html)
        self.assertIn("function serializeOutputCellToJson", html)
        self.assertIn("async function copyChatConsoleOutputCell", html)
        self.assertIn("text/plain", html)
        self.assertIn("text/html", html)
        self.assertIn("application/x-main-computer-output-cell+json", html)
        self.assertIn("metadata?.data_url", html)
        self.assertIn("Snippet", html)

    def test_frontend_splits_cell_tabs_and_simple_insert_widget(self) -> None:
        html = APPLICATIONS_INDEX_HTML
        self.assertIn('const chatConsoleInputCellTypes = chatConsoleCellTypes.filter((item) => item.type !== "output")', html)
        self.assertIn('const chatConsoleOutputCellTypes = chatConsoleCellTypes.filter((item) => item.type === "output")', html)
        self.assertIn('const tabItems = cell.type === "output" ? chatConsoleOutputCellTypes : chatConsoleInputCellTypes', html)
        self.assertIn("function renderChatConsoleInsertStrip", html)
        self.assertIn('target.append(renderChatConsoleInsertStrip(""))', html)
        self.assertIn('chatConsoleButton("+ Insert", () => addChatConsoleCell("ai", "", afterId))', html)
        self.assertIn('button.dataset.chatConsoleInsert = "ai"', html)
        self.assertIn('type: "javascript", label: "JS"', html)
        self.assertIn('type: "python", label: "Python"', html)
        self.assertIn('type: "basic", label: "BASIC"', html)
        self.assertIn('const chatConsoleCodeCellTypes = new Set(["javascript", "python", "basic"])', html)
        self.assertIn('label: "Calc"', html)
        self.assertIn("evaluateChatConsoleCalculatorCell", html)
        self.assertIn("chat-console-output-indent", html)


    def test_frontend_includes_reusable_chat_thread_store(self) -> None:
        html = APPLICATIONS_INDEX_HTML
        self.assertIn("main-computer-chat-thread-store-v2", html)
        self.assertIn("main-computer-chat-console-v1", html)
        self.assertIn("window.MainComputerChatThreads", html)
        self.assertIn("function chatThreadStoreLoad", html)
        self.assertIn("function chatThreadCreate", html)
        self.assertIn("function chatThreadSetActive", html)
        self.assertIn("function chatThreadClone", html)
        self.assertIn("function chatThreadSearch", html)
        self.assertIn("function chatThreadBuildSearchText", html)
        self.assertLess(html.index("chatThreadStoreStorageKey"), html.index("const chatConsoleStorageKey"))

    def test_chat_thread_store_script_contract(self) -> None:
        script = (APPLICATIONS_HTML.parent / "applications" / "scripts" / "chat-thread-store.js").read_text(encoding="utf-8")
        for expected in (
            "chatThreadStoreStorageKey",
            "chatThreadStoreLegacyStorageKey",
            "chatThreadStoreNormalizeThread",
            "chatThreadStoreReadLegacyNotebook",
            "chatThreadSaveActiveThread",
            "chatThreadSaveThread",
            "options.makeActive !== false",
            "saveThread: chatThreadSaveThread",
            "chatThreadArchive",
            "chatThreadDelete",
            "buildSearchText: chatThreadBuildSearchText",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)
        self.assertNotIn("document.querySelector", script)
        self.assertNotIn("chatConsoleNotebook", script)



    def test_frontend_exposes_chat_thread_selector_ui(self) -> None:
        html = APPLICATIONS_INDEX_HTML
        for expected in (
            'id="chat-thread-new"',
            'id="chat-thread-search"',
            'id="chat-thread-list"',
            'id="chat-thread-active-title"',
            'id="chat-thread-active-meta"',
            'id="chat-thread-rename"',
            'id="chat-thread-clone"',
            'id="chat-thread-archive"',
            'id="chat-thread-copy-link"',
            "window[apiName] = {",
            "mount: controllerMount",
            "function chatConsoleMountThreadController",
            "function chatConsoleRenderThreadController",
            "chatConsoleThreadController?.select",
            "chatConsoleThreadController?.create",
            "chatConsoleThreadController?.rename",
            "chatConsoleThreadController?.clone",
            "chatConsoleThreadController?.archive",
            "chatConsoleThreadController?.copyLink",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, html)

    def test_chat_thread_controller_script_contract(self) -> None:
        script = (APPLICATIONS_HTML.parent / "applications" / "scripts" / "chat-thread-controller.js").read_text(encoding="utf-8")
        for expected in (
            'const apiName = "MainComputerChatThreadController"',
            "function controllerMount",
            "options.getActiveThreadId",
            "options.setActiveThreadId",
            "options.beforeThreadChange",
            "options.afterThreadChange",
            "copyThreadLink",
            "window[apiName]",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)
        self.assertNotIn("chatConsoleState", script)
        self.assertNotIn("chatConsoleNotebook", script)

    def test_frontend_chat_console_state_uses_thread_store_active_thread(self) -> None:
        html = APPLICATIONS_INDEX_HTML
        self.assertIn("function chatConsoleThreadStoreApi", html)
        self.assertIn("threadStore.load();", html)
        self.assertIn("const activeThread = threadStore.getActive();", html)
        self.assertIn("return migrateChatConsoleState(JSON.parse(JSON.stringify(activeThread)))", html)
        self.assertIn("threadStore.saveActiveThread(chatConsoleState)", html)
        self.assertIn("chatConsoleLoadThreadFromUrl();", html)
        self.assertIn("chatConsoleSetThreadUrl(chatConsoleState.id);", html)
        self.assertIn("chatConsoleMountThreadController();", html)
        self.assertIn("chatConsoleRenderThreadController()", html)

    def test_chat_console_ai_subprocess_supports_mounted_scoped_context(self) -> None:
        script = (APPLICATIONS_HTML.parent.parent / "chat_ai_subprocess.py").read_text(encoding="utf-8")
        self.assertIn('scoped_context = command.get("scoped_context")', script)
        self.assertIn("scoped_context_text = str(scoped_context.get", script)
        self.assertIn("if scoped_context_text:", script)
        self.assertIn('web_search_context, web_search_text = {"disabled": True, "reason": "mounted_editor_scope"}, ""', script)
        self.assertIn('ChatMessage(role="system", content=scoped_context_text)', script)


    def test_mathics_result_to_parts_keeps_text_only_result(self) -> None:
        parts = mathics_result_to_parts({"ok": True, "result_text": "4", "messages": [], "graphics": []}, "2+2")

        self.assertEqual(parts[0]["kind"], "mathics")
        self.assertEqual(parts[0]["content"], "4")

    def test_mathics_result_to_parts_adds_plot_parts_with_data_urls(self) -> None:
        parts = mathics_result_to_parts(
            {
                "ok": True,
                "result_text": "-Graphics-",
                "messages": [],
                "graphics": [{"id": "g1", "mime_type": "image/svg+xml", "data_base64": "PHN2Zy8+", "text_fallback": "-Graphics-", "metadata": {}}],
            },
            "Plot[Sin[x], {x, 0, 2 Pi}]",
        )

        self.assertEqual([part["kind"] for part in parts], ["mathics", "plot"])
        self.assertEqual(parts[1]["metadata"]["data_url"], "data:image/svg+xml;base64,PHN2Zy8+")

    def test_mathics_result_to_parts_preserves_ordered_mixed_outputs(self) -> None:
        parts = mathics_result_to_parts(
            {
                "ok": True,
                "result_text": "x",
                "outputs": [
                    {"kind": "mathics", "text": "x", "metadata": {}},
                    {"kind": "plot", "text": "-Graphics-", "mime_type": "image/svg+xml", "data_base64": "PHN2Zy8+", "metadata": {}},
                ],
                "warnings": [],
                "errors": [],
            },
            "Simplify[x]\nPlot[Sin[x], {x, 0, 2 Pi}]",
        )

        self.assertEqual([part["kind"] for part in parts], ["mathics", "plot"])
        self.assertEqual(parts[0]["content"], "x")
        self.assertNotEqual(parts[0]["content"], "x -Graphics-")
        self.assertEqual(parts[1]["metadata"]["data_url"], "data:image/svg+xml;base64,PHN2Zy8+")

    def test_mathics_result_to_parts_preserves_warnings_with_text_fallback(self) -> None:
        parts = mathics_result_to_parts(
            {"ok": True, "result_text": "-Graphics-", "messages": [], "graphics": [], "warnings": ["Mathics graphics export failed: no svg"]},
            "Plot[Sin[x], {x, 0, 2 Pi}]",
        )

        self.assertEqual([part["kind"] for part in parts], ["mathics", "warning"])
        self.assertIn("-Graphics-", parts[0]["content"])


if __name__ == "__main__":
    unittest.main()
