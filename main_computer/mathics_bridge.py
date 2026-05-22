from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
from typing import Any


MAX_MATHICS_EXPRESSION_CHARS = 4000

_mathics_session = None
_mathics_builtins_loaded = False


class MathicsImportFailure(RuntimeError):
    pass


class MathicsSessionFailure(RuntimeError):
    pass


class MathicsEvaluationFailure(RuntimeError):
    pass


def is_mathics_available() -> bool:
    try:
        return (
            importlib.util.find_spec("mathics.core.load_builtin") is not None
            and importlib.util.find_spec("mathics.session") is not None
        )
    except ModuleNotFoundError:
        return False


def _diagnostics() -> dict[str, str]:
    return {"python": sys.executable}


def _disable_hanging_windows_platform_wmi_probe() -> None:
    """Prevent Python's platform module from blocking on broken Windows WMI.

    Mathics3 imports Pint/Flexcache while loading builtins. On some Windows
    machines, that import path calls platform.system(), and Python 3.12 tries a
    WMI query that can hang forever instead of raising. Python's platform module
    already has a non-WMI fallback when _wmi_query raises OSError, so make that
    fallback deterministic before Mathics is imported.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import platform
    except Exception:
        return
    wmi_query = getattr(platform, "_wmi_query", None)
    if not callable(wmi_query):
        return
    if getattr(wmi_query, "__name__", "") == "_main_computer_disabled_wmi_query":
        return

    def _main_computer_disabled_wmi_query(*args: Any, **kwargs: Any) -> str:
        raise OSError("Windows WMI platform probe disabled because it can hang")

    platform._wmi_query = _main_computer_disabled_wmi_query  # type: ignore[attr-defined]


def _normalize_expression(expression: str) -> str:
    normalized = str(expression or "").strip()
    if not normalized:
        raise ValueError("Mathics expression is required.")
    if len(normalized) > MAX_MATHICS_EXPRESSION_CHARS:
        raise ValueError("Mathics expression is limited to 4000 characters.")
    return normalized


def _mathics_result_head_name(result: Any) -> str:
    for attr in ("get_head_name", "get_head"):
        candidate = getattr(result, attr, None)
        if callable(candidate):
            try:
                return str(candidate() or "")
            except Exception:
                continue
    return str(getattr(result, "head", "") or "")


def _is_graphics_like_result(result: Any, result_text: str) -> tuple[bool, str]:
    text = str(result_text or "").strip()
    head_name = _mathics_result_head_name(result)
    haystack = f"{text} {head_name}"
    if "-Graphics3D-" in haystack or "Graphics3D" in haystack:
        return True, "graphics3d"
    if "-Graphics-" in haystack or "System`Graphics" in haystack or "Graphics" in haystack:
        return True, "plot"
    return False, ""


def _split_mathics_inputs(expression: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    escape = False
    for line in str(expression or "").splitlines():
        stripped = line.strip()
        if not stripped and not current:
            continue
        current.append(line)
        for char in line:
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[({":
                depth += 1
            elif char in "])}" and depth > 0:
                depth -= 1
        if depth == 0 and not in_string:
            chunk = "\n".join(current).strip()
            if chunk:
                chunks.append(chunk)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        chunks.append(tail)
    return chunks or [str(expression or "").strip()]


def _export_graphics_svg(session: Any, expression: str, text_fallback: str, kind: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        exported = session.evaluate(f'ExportString[({expression}), "SVG"]')
        svg_text = str(session.evaluation.format_output(exported) or "")
        if "<svg" not in svg_text:
            raise ValueError("Mathics did not return SVG output.")
        data_base64 = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
        artifact_id = hashlib.sha256(f"{expression}:{svg_text[:128]}".encode("utf-8")).hexdigest()[:16]
        return [
            {
                "id": f"mathics-graphics-{artifact_id}",
                "kind": kind or "plot",
                "mime_type": "image/svg+xml",
                "data_base64": data_base64,
                "text_fallback": text_fallback,
                "metadata": {"export": "ExportString", "format": "SVG"},
            }
        ], warnings
    except Exception as exc:
        warnings.append(f"Mathics graphics export failed: {exc}")
        return [], warnings


def _evaluate_mathics_artifact(session: Any, expression: str) -> tuple[list[dict[str, Any]], list[str]]:
    result = session.evaluate(expression)
    result_text = str(session.evaluation.format_output(result))
    graphics_like, graphics_kind = _is_graphics_like_result(result, result_text)
    if graphics_like:
        graphics, warnings = _export_graphics_svg(session, expression, result_text, graphics_kind)
        if graphics:
            return [
                {
                    "kind": graphics_kind or "graphics",
                    "text": result_text,
                    "mime_type": item["mime_type"],
                    "data_base64": item["data_base64"],
                    "metadata": {"graphics_id": item["id"], **item.get("metadata", {})},
                }
                for item in graphics
            ], warnings
        return [{"kind": "mathics", "text": result_text, "metadata": {"graphics_export": "failed"}}], warnings
    return [{"kind": "mathics", "text": result_text, "metadata": {}}], []


def get_mathics_session():
    global _mathics_session, _mathics_builtins_loaded
    if _mathics_session is not None:
        return _mathics_session

    _disable_hanging_windows_platform_wmi_probe()

    try:
        from mathics.core.load_builtin import import_and_load_builtins
        from mathics.session import MathicsSession
    except Exception as exc:
        raise MathicsImportFailure(f"Mathics3 import failed. {exc}") from exc

    try:
        if not _mathics_builtins_loaded:
            import_and_load_builtins()
            _mathics_builtins_loaded = True
        _mathics_session = MathicsSession(add_builtin=True, catch_interrupt=True)
        return _mathics_session
    except Exception as exc:
        _mathics_session = None
        raise MathicsSessionFailure(f"Mathics3 session initialization failed. {exc}") from exc


def evaluate_mathics_expression(expression: str, timeout_s: float = 10.0) -> dict[str, Any]:
    normalized = _normalize_expression(expression)
    if not is_mathics_available():
        return {
            "ok": False,
            "expression": normalized,
            "error": "Mathics3 import failed.",
            "detail": "Mathics3 could not be imported from this Python interpreter.",
            "messages": [],
            "warnings": [],
            "errors": ["Mathics3 import failed."],
            "outputs": [],
            "graphics": [],
            "diagnostics": _diagnostics(),
        }

    try:
        session = get_mathics_session()
    except MathicsImportFailure as exc:
        return {
            "ok": False,
            "expression": normalized,
            "error": "Mathics3 import failed.",
            "detail": str(exc),
            "messages": [],
            "warnings": [],
            "errors": [str(exc)],
            "outputs": [],
            "graphics": [],
            "diagnostics": _diagnostics(),
        }
    except MathicsSessionFailure as exc:
        return {
            "ok": False,
            "expression": normalized,
            "error": "Mathics3 session initialization failed.",
            "detail": str(exc),
            "messages": [],
            "warnings": [],
            "errors": [str(exc)],
            "outputs": [],
            "graphics": [],
            "diagnostics": _diagnostics(),
        }

    try:
        outputs: list[dict[str, Any]] = []
        warnings: list[str] = []
        for artifact_expression in _split_mathics_inputs(normalized):
            artifact_outputs, artifact_warnings = _evaluate_mathics_artifact(session, artifact_expression)
            outputs.extend(artifact_outputs)
            warnings.extend(artifact_warnings)
    except Exception as exc:
        return {
            "ok": False,
            "expression": normalized,
            "error": "Mathics evaluation failed.",
            "detail": str(exc),
            "messages": [],
            "warnings": [],
            "errors": [str(exc)],
            "outputs": [],
            "graphics": [],
            "diagnostics": _diagnostics(),
        }

    text_outputs = [str(item.get("text", "")) for item in outputs if item.get("kind") in {"text", "mathics"} and item.get("text")]
    result_text = "\n".join(text_outputs) if text_outputs else "\n".join(str(item.get("text", "")) for item in outputs if item.get("text"))
    graphics = [
        {
            "id": str(item.get("metadata", {}).get("graphics_id", "")),
            "kind": str(item.get("kind", "graphics")),
            "mime_type": str(item.get("mime_type", "")),
            "data_base64": str(item.get("data_base64", "")),
            "text_fallback": str(item.get("text", "")),
            "metadata": item.get("metadata", {}),
        }
        for item in outputs
        if item.get("kind") in {"graphics", "graphics3d", "plot"} and item.get("data_base64")
    ]

    return {
        "ok": True,
        "expression": normalized,
        "outputs": outputs,
        "result_text": str(result_text),
        "result_latex": None,
        "messages": [],
        "warnings": warnings,
        "errors": [],
        "graphics": graphics,
        "diagnostics": _diagnostics(),
    }
