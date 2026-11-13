from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
import types
import unittest
from unittest.mock import patch

import main_computer.mathics_bridge as mathics_bridge
from main_computer.mathics_bridge import evaluate_mathics_expression


class FakeEvaluation:
    def format_output(self, result: object) -> str:
        return f"formatted {result}"


class FakeGraphicsResult:
    def get_head_name(self) -> str:
        return "System`Graphics"


class MathicsBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        mathics_bridge._mathics_session = None
        mathics_bridge._mathics_builtins_loaded = False

    def test_evaluate_rejects_empty_and_long_expressions(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_mathics_expression("")
        with self.assertRaises(ValueError):
            evaluate_mathics_expression("x" * 4001)

    def test_evaluate_returns_import_failure_when_mathics_is_missing(self) -> None:
        with patch("main_computer.mathics_bridge.is_mathics_available", return_value=False):
            result = evaluate_mathics_expression("2 + 2")

        self.assertFalse(result["ok"])
        self.assertEqual(result["expression"], "2 + 2")
        self.assertEqual(result["error"], "Mathics3 import failed.")
        self.assertIn(sys.executable, result["diagnostics"]["python"])

    def test_evaluate_initializes_builtins_before_session_and_formats_result(self) -> None:
        calls: list[str] = []

        class FakeSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                calls.append("session")
                self.evaluation = FakeEvaluation()
                self.add_builtin = add_builtin
                self.catch_interrupt = catch_interrupt

            def evaluate(self, expression: str) -> str:
                calls.append(f"evaluate:{expression}")
                return "4"

        def fake_import_and_load_builtins() -> None:
            calls.append("builtins")

        modules = self._fake_mathics_modules(fake_import_and_load_builtins, FakeSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            result = evaluate_mathics_expression("2+2")

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_text"], "formatted 4")
        self.assertEqual(result["outputs"][0]["text"], "formatted 4")
        self.assertEqual(calls, ["builtins", "session", "evaluate:2+2"])

    def test_concurrent_preparation_and_evaluation_share_one_session(self) -> None:
        calls: list[str] = []

        class FakeSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                calls.append("session")
                self.evaluation = FakeEvaluation()

            def evaluate(self, expression: str) -> str:
                return "4"

        def fake_load() -> None:
            time.sleep(0.025)
            calls.append("builtins")

        modules = self._fake_mathics_modules(fake_load, FakeSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(lambda n: mathics_bridge.get_mathics_session()
                    if n % 2 == 0 else evaluate_mathics_expression("2+2"), range(8)))

        assert calls == ["builtins", "session"]
        assert all(item is mathics_bridge._mathics_session for item in results[::2])
        assert all(item["ok"] for item in results[1::2])

    def test_session_value_error_is_not_reported_as_not_installed(self) -> None:
        def fake_import_and_load_builtins() -> None:
            return None

        class FailingSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                raise ValueError("missing builtins")

        modules = self._fake_mathics_modules(fake_import_and_load_builtins, FailingSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            result = evaluate_mathics_expression("2+2")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Mathics3 session initialization failed.")
        self.assertIn("missing builtins", result["detail"])
        self.assertNotIn("not installed", result["error"].lower())

    def test_evaluation_failure_is_distinct(self) -> None:
        class FailingEvaluationSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                self.evaluation = FakeEvaluation()

            def evaluate(self, expression: str) -> str:
                raise RuntimeError("bad expression")

        modules = self._fake_mathics_modules(lambda: None, FailingEvaluationSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            result = evaluate_mathics_expression("Bad[")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Mathics evaluation failed.")
        self.assertIn("bad expression", result["detail"])

    def test_graphics_result_exports_svg_artifact(self) -> None:
        class GraphicsEvaluation:
            def format_output(self, result: object) -> str:
                if result == "svg-result":
                    return "<svg><path /></svg>"
                return "-Graphics-"

        class GraphicsSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                self.evaluation = GraphicsEvaluation()

            def evaluate(self, expression: str) -> object:
                if expression.startswith("ExportString"):
                    return "svg-result"
                return FakeGraphicsResult()

        modules = self._fake_mathics_modules(lambda: None, GraphicsSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            result = evaluate_mathics_expression("Plot[Sin[x], {x, 0, 2 Pi}]")

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_text"], "-Graphics-")
        self.assertEqual(result["outputs"][0]["kind"], "plot")
        self.assertEqual(result["graphics"][0]["mime_type"], "image/svg+xml")
        self.assertTrue(result["graphics"][0]["data_base64"])
        self.assertEqual(result["warnings"], [])

    def test_graphics_export_failure_keeps_text_fallback_and_warning(self) -> None:
        class GraphicsEvaluation:
            def format_output(self, result: object) -> str:
                return "-Graphics-"

        class FailingExportSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                self.evaluation = GraphicsEvaluation()

            def evaluate(self, expression: str) -> object:
                if expression.startswith("ExportString"):
                    raise RuntimeError("no svg")
                return FakeGraphicsResult()

        modules = self._fake_mathics_modules(lambda: None, FailingExportSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            result = evaluate_mathics_expression("Plot[Sin[x], {x, 0, 2 Pi}]")

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_text"], "-Graphics-")
        self.assertEqual(result["graphics"], [])
        self.assertIn("graphics export failed", result["warnings"][0].lower())

    def test_mixed_text_and_graphics_are_evaluated_as_separate_outputs(self) -> None:
        class MixedEvaluation:
            def format_output(self, result: object) -> str:
                if result == "text-result":
                    return "x"
                if result == "svg-result":
                    return "<svg><path /></svg>"
                return "-Graphics-"

        class MixedSession:
            def __init__(self, add_builtin: bool, catch_interrupt: bool) -> None:
                self.evaluation = MixedEvaluation()

            def evaluate(self, expression: str) -> object:
                if expression == "Simplify[x]":
                    return "text-result"
                if expression.startswith("ExportString"):
                    return "svg-result"
                return FakeGraphicsResult()

        modules = self._fake_mathics_modules(lambda: None, MixedSession)
        with patch.dict("sys.modules", modules), patch("main_computer.mathics_bridge.is_mathics_available", return_value=True):
            result = evaluate_mathics_expression("Simplify[x]\nPlot[Sin[x], {x, 0, 2 Pi}]")

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_text"], "x")
        self.assertEqual([item["kind"] for item in result["outputs"]], ["mathics", "plot"])
        self.assertEqual(result["outputs"][0]["text"], "x")
        self.assertNotEqual(result["outputs"][0]["text"], "x -Graphics-")
        self.assertTrue(result["outputs"][1]["data_base64"])

    def test_preparation_endpoint_reports_readiness_and_import_failure(self) -> None:
        from http import HTTPStatus
        import importlib.util
        from pathlib import Path

        # Load only the route mixin, not the viewport page templates (which
        # depend on the separately exported game component).
        fake_state = types.ModuleType("main_computer.viewport_state")
        fake_state.HTTPStatus = HTTPStatus
        fake_state.MathicsImportFailure = mathics_bridge.MathicsImportFailure
        fake_state.MathicsSessionFailure = mathics_bridge.MathicsSessionFailure
        fake_state.get_mathics_session = mathics_bridge.get_mathics_session
        path = Path(__file__).resolve().parents[1] / "main_computer/viewport_routes_calculator.py"
        spec = importlib.util.spec_from_file_location("_test_mathics_calculator_routes", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"main_computer.viewport_state": fake_state}):
            spec.loader.exec_module(module)

        class FakeHandler(module.ViewportCalculatorRoutesMixin):
            def __init__(self) -> None:
                self.responses = []
                self.server = types.SimpleNamespace(signal=lambda *args, **kwargs: None)

            def _send_json(self, payload, status=HTTPStatus.OK):
                self.responses.append((payload, status))

        handler = FakeHandler()
        with patch.object(module, "get_mathics_session", return_value=object()) as session:
            handler._handle_calculator_mathics_prepare()
        session.assert_called_once_with()
        assert handler.responses == [({"ok": True, "status": "ready"}, HTTPStatus.OK)]

        handler = FakeHandler()
        with patch.object(module, "get_mathics_session", side_effect=mathics_bridge.MathicsImportFailure("missing")):
            handler._handle_calculator_mathics_prepare()
        assert handler.responses[0][1] == HTTPStatus.SERVICE_UNAVAILABLE
        assert handler.responses[0][0]["ok"] is False
        assert "missing" in handler.responses[0][0]["detail"]

    def _fake_mathics_modules(self, import_and_load_builtins, session_class: type) -> dict[str, types.ModuleType]:
        mathics = types.ModuleType("mathics")
        core = types.ModuleType("mathics.core")
        load_builtin = types.ModuleType("mathics.core.load_builtin")
        session = types.ModuleType("mathics.session")
        load_builtin.import_and_load_builtins = import_and_load_builtins
        session.MathicsSession = session_class
        mathics.core = core
        core.load_builtin = load_builtin
        mathics.session = session
        return {
            "mathics": mathics,
            "mathics.core": core,
            "mathics.core.load_builtin": load_builtin,
            "mathics.session": session,
        }


if __name__ == "__main__":
    unittest.main()
