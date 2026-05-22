from __future__ import annotations

import json
from typing import Any


VALID_TERMINAL_RISKS = {"read-only", "write", "destructive", "network", "unknown"}


def parse_terminal_suggestion(raw_content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ValueError("Model did not return valid JSON for a terminal command suggestion.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Model terminal command suggestion must be a JSON object.")
    return parsed


def validate_terminal_command(command: str) -> str:
    normalized = str(command or "").strip()
    if not normalized:
        raise ValueError("Suggested command is required.")
    if len(normalized) > 4000:
        raise ValueError("Suggested command is limited to 4000 characters.")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("Suggested command must be a single line.")
    return normalized


def normalize_terminal_risk(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_TERMINAL_RISKS else "unknown"
