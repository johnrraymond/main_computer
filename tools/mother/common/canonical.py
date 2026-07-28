"""Deterministic canonical encoders for Mother hashed values."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import json
import unicodedata
from collections.abc import Mapping
from typing import Any


def _normalize_string(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        raise TypeError("floats are forbidden in canonical Mother values")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("raw bytes are not a canonical scalar")
    if is_dataclass(value):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            canonical_key = _normalize_string(key)
            if canonical_key in normalized:
                raise ValueError(
                    f"duplicate mapping key after Unicode normalization: {canonical_key!r}"
                )
            normalized[canonical_key] = _normalize(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("sets have no canonical sequence order")
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """Encode a value as compact, sorted, NFC-normalized UTF-8 JSON."""

    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # JSON string quoting is a valid YAML double-quoted scalar and avoids
        # YAML 1.1/1.2 ambiguity for values such as yes, null, 01, and dates.
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unsupported YAML scalar: {type(value).__name__}")


def _emit_yaml(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [prefix + "{}"]
        lines: list[str] = []
        for key, item in value.items():
            rendered_key = json.dumps(key, ensure_ascii=False)
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{prefix}{rendered_key}:")
                lines.extend(_emit_yaml(item, indent + 2))
            else:
                scalar_lines = _emit_yaml(item, 0)
                lines.append(f"{prefix}{rendered_key}: {scalar_lines[0].lstrip()}")
        return lines
    if isinstance(value, list):
        if not value:
            return [prefix + "[]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                lines.append(prefix + "-")
                lines.extend(_emit_yaml(item, indent + 2))
            else:
                scalar_lines = _emit_yaml(item, 0)
                lines.append(prefix + "- " + scalar_lines[0].lstrip())
        return lines
    return [prefix + _yaml_scalar(value)]


def canonical_yaml(value: Any) -> bytes:
    """Encode a value as deterministic, ambiguity-free UTF-8 YAML."""

    normalized = _normalize(value)
    return ("\n".join(_emit_yaml(normalized)) + "\n").encode("utf-8")


__all__ = ["canonical_json", "canonical_yaml"]
