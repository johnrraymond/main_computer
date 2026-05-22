from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportCalculatorRoutesMixin:
    def _handle_calculator_mathics_evaluate(self) -> None:
        try:
            body = self._read_json()
            expression = str(body.get("expression", "") or "").strip()
            if not expression:
                self._send_json(
                    {"ok": False, "expression": "", "error": "Mathics expression is required.", "messages": []},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            if len(expression) > 4000:
                self._send_json(
                    {
                        "ok": False,
                        "expression": expression,
                        "error": "Mathics expression is limited to 4000 characters.",
                        "messages": [],
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            timeout_s = max(1.0, min(30.0, float(body.get("timeout_s", 10) or 10)))
            self.server.signal("api-calculator-mathics-evaluate", expression_chars=len(expression), timeout_s=timeout_s)
            if timeout_s <= 1.0:
                self._send_json(
                    {
                        "ok": False,
                        "expression": expression,
                        "error": "Mathics evaluation failed.",
                        "detail": "Mathics evaluation timed out before startup completed.",
                        "messages": [],
                        "warnings": ["Mathics evaluation timed out."],
                        "errors": ["Mathics evaluation timed out."],
                        "outputs": [],
                        "graphics": [],
                        "diagnostics": {"python": sys.executable},
                    }
                )
                return
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(evaluate_mathics_expression, expression, timeout_s)
            try:
                result = future.result(timeout=timeout_s)
            except FutureTimeoutError:
                result = {
                    "ok": False,
                    "expression": expression,
                    "error": "Mathics evaluation failed.",
                    "detail": f"Mathics evaluation exceeded {timeout_s:g} seconds.",
                    "messages": [],
                    "warnings": ["Mathics evaluation timed out."],
                    "errors": ["Mathics evaluation timed out."],
                    "outputs": [],
                    "graphics": [],
                    "diagnostics": {"python": sys.executable},
                }
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-calculator-mathics-evaluate-error", error=exc)
            self._send_json(
                {"ok": False, "expression": "", "error": str(exc), "messages": []},
                status=HTTPStatus.BAD_REQUEST,
            )

    def _handle_calculator_mathics_ask(self) -> None:
        try:
            body = self._read_json()
            prompt = str(body.get("prompt", "") or "").strip()
            if not prompt:
                self._send_json({"ok": False, "error": "Prompt is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            if len(prompt) > 4000:
                self._send_json({"ok": False, "error": "Prompt is limited to 4000 characters."}, status=HTTPStatus.BAD_REQUEST)
                return
            model_prompt = "\n".join(
                [
                    "Translate the user's natural-language symbolic math request into one Mathics/Wolfram-language-style expression.",
                    "Return only the expression. Do not explain.",
                    "Prefer Mathics-compatible syntax.",
                    "Use square brackets for function calls.",
                    "Use capitalized symbolic function names where appropriate.",
                    'Example user: "differentiate sine squared"',
                    "Example expression: D[Sin[x]^2, x]",
                    'Example user: "expand x plus one to the fourth"',
                    "Example expression: Expand[(x + 1)^4]",
                    'Example user: "solve x squared equals four"',
                    "Example expression: Solve[x^2 == 4, x]",
                    f"User request: {prompt}",
                ]
            )
            self.server.signal("api-calculator-mathics-ask", prompt_chars=len(prompt))
            provider = getattr(self.server.computer, "provider", None)
            if provider is not None and hasattr(provider, "chat"):
                response = provider.chat(
                    [
                        ChatMessage(
                            role="system",
                            content="You produce one Mathics/Wolfram-language expression and no prose.",
                        ),
                        ChatMessage(role="user", content=model_prompt),
                    ]
                )
            else:
                response = self.server.computer.chat(model_prompt)
            expression = self._extract_mathics_expression(response.content)
            if not expression:
                self._send_json({"ok": False, "error": "No Mathics expression returned."}, status=HTTPStatus.BAD_GATEWAY)
                return
            self._send_json({"ok": True, "expression": expression, "content": response.content})
        except Exception as exc:
            self.server.signal("api-calculator-mathics-ask-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

    def _extract_mathics_expression(self, model_text: str) -> str:
        cleaned = str(model_text or "").strip()
        cleaned = re.sub(r"```(?:wolfram|mathics|mathematica|text)?", "", cleaned, flags=re.IGNORECASE).replace("```", "")
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        candidate = lines[0] if lines else cleaned.strip()
        candidate = re.sub(r"^(?:expression|mathics|result)\s*:\s*", "", candidate, flags=re.IGNORECASE).strip()
        return candidate.strip("`\"'")[:4000]

    def _handle_calculator_qa(self) -> None:
        try:
            body = self._read_json()
            question = str(body.get("question", "") or "").strip()
            if not question:
                self._send_json({"ok": False, "error": "Question is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            if len(question) > 4000:
                self._send_json({"ok": False, "error": "Question is limited to 4000 characters."}, status=HTTPStatus.BAD_REQUEST)
                return
            raw_context = body.get("context") if isinstance(body.get("context"), dict) else {}
            context = raw_context if isinstance(raw_context, dict) else {}

            def context_text(key: str) -> str:
                value = context.get(key, "")
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False)[:4000]
                return str(value or "")[:4000]

            graph_range = context.get("graph_range") if isinstance(context.get("graph_range"), dict) else {}
            x_min = str(graph_range.get("x_min", "") or "")[:200] if isinstance(graph_range, dict) else ""
            x_max = str(graph_range.get("x_max", "") or "")[:200] if isinstance(graph_range, dict) else ""
            y_min = str(graph_range.get("y_min", "") or "")[:200] if isinstance(graph_range, dict) else ""
            y_max = str(graph_range.get("y_max", "") or "")[:200] if isinstance(graph_range, dict) else ""
            prompt = "\n".join(
                [
                    "You are helping the user understand calculator outputs.",
                    "",
                    "Current calculator context:",
                    f"Basic expression: {context_text('basic_expression')}",
                    f"Basic result: {context_text('basic_result')}",
                    f"Graph expression f(x): {context_text('graph_expression')}",
                    f"Graph status: {context_text('graph_status')}",
                    f"Graph range: x=[{x_min}, {x_max}], y=[{y_min}, {y_max}]",
                    f"Mathics expression: {context_text('mathics_expression')}",
                    f"Mathics output: {context_text('mathics_output')}",
                    "",
                    "User question:",
                    question,
                    "",
                    "Answer the user's question clearly. Use the context when relevant.",
                    "If the context is missing or inconclusive, say so briefly.",
                    "Do not invent missing graph or symbolic outputs.",
                ]
            )
            self.server.signal("api-calculator-qa", question_chars=len(question))
            provider = getattr(self.server.computer, "provider", None)
            if provider is not None and hasattr(provider, "chat"):
                response = provider.chat(
                    [
                        ChatMessage(
                            role="system",
                            content="Answer concise follow-up questions about calculator, graphing, and symbolic math outputs.",
                        ),
                        ChatMessage(role="user", content=prompt),
                    ]
                )
            else:
                response = self.server.computer.chat(prompt)
            self._send_json(
                {
                    "ok": True,
                    "answer": response.content,
                    "provider": getattr(response, "provider", ""),
                    "model": getattr(response, "model", ""),
                }
            )
        except Exception as exc:
            self.server.signal("api-calculator-qa-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
