"""Typed, secret-safe Mother error envelopes and stable process exit codes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass, fields, is_dataclass
import re
from typing import Any


RETRY_CLASSES = frozenset(
    {"never", "same-request", "after-reobserve", "operator-decision"}
)
AUTHORITY_EFFECTS = frozenset(
    {
        "none",
        "ledger-only",
        "live-state-maybe-changed",
        "local-pointer-determined",
        "network-head-determined",
    }
)

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)"
        r"\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
)

_NAMESPACE_EXIT_CODES = {
    "MOTHER_INPUT_": 2,
    "MOTHER_SCHEMA_": 3,
    "MOTHER_STATE_": 4,
    "MOTHER_CONFLICT_": 5,
    "MOTHER_TRANSPORT_": 6,
    "MOTHER_TARGET_": 7,
    "MOTHER_AUTH_": 8,
    "MOTHER_ROLLBACK_": 9,
    "MOTHER_MEMBERSHIP_": 10,
    "MOTHER_RECOVERY_": 11,
    "MOTHER_LIVE_": 12,
    "MOTHER_OPEN_": 13,
}


def _redact_text(value: str, secret_values: Iterable[str] = ()) -> str:
    rendered = str(value)
    for secret in secret_values:
        if secret:
            rendered = rendered.replace(str(secret), "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)\\bBearer"):
            rendered = pattern.sub("Bearer [REDACTED]", rendered)
        else:
            rendered = pattern.sub(
                lambda match: f"{match.group(1)}=[REDACTED]",
                rendered,
            )
    return rendered


def _contains_secret(value: Any, secrets: tuple[str, ...]) -> bool:
    if not secrets:
        return False
    if isinstance(value, Mapping):
        return any(
            _contains_secret(key, secrets) or _contains_secret(item, secrets)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_secret(item, secrets) for item in value)
    if is_dataclass(value):
        return any(
            _contains_secret(getattr(value, field.name), secrets)
            for field in fields(value)
        )
    rendered = str(value)
    return any(secret and secret in rendered for secret in secrets)


def _tuple_field(value: Iterable[Any], name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be an iterable of values, not a scalar")
    try:
        return tuple(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be iterable") from exc


@dataclass(frozen=True, slots=True)
class MotherError(Exception):
    """Expected failure crossing a Mother public module boundary."""

    code: str
    message: str
    operation_id: str
    module_id: str
    retry_class: str
    authority_effect: str
    durable_effect_refs: tuple[Any, ...] = ()
    evidence_refs: tuple[Any, ...] = ()
    allowed_next_actions: tuple[str, ...] = ()
    cause_class: str = ""
    secret_values: InitVar[tuple[str, ...] | list[str]] = ()

    def __post_init__(self, secret_values: tuple[str, ...] | list[str]) -> None:
        if not isinstance(self.code, str) or not self.code.startswith("MOTHER_"):
            raise ValueError("code must be a MOTHER_* error code")
        if self.retry_class not in RETRY_CLASSES:
            raise ValueError(f"unknown retry_class: {self.retry_class!r}")
        if self.authority_effect not in AUTHORITY_EFFECTS:
            raise ValueError(f"unknown authority_effect: {self.authority_effect!r}")

        for name in ("message", "operation_id", "module_id", "cause_class"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")

        for name in ("durable_effect_refs", "evidence_refs", "allowed_next_actions"):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                object.__setattr__(self, name, _tuple_field(value, name))

        if any(not isinstance(action, str) for action in self.allowed_next_actions):
            raise TypeError("allowed_next_actions must contain strings")

        secrets = tuple(str(value) for value in secret_values if value)
        public_values = (
            self.code,
            self.message,
            self.operation_id,
            self.module_id,
            self.retry_class,
            self.authority_effect,
            self.durable_effect_refs,
            self.evidence_refs,
            self.allowed_next_actions,
            self.cause_class,
        )
        if any(_contains_secret(value, secrets) for value in public_values):
            raise ValueError("secret-bearing value may not appear in MotherError")
        if any(_redact_text(str(value)) != str(value) for value in public_values):
            raise ValueError("secret-shaped value may not appear in MotherError")

        Exception.__init__(self, f"{self.code}: {self.message}")

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def wrap_vendor_error(
    error: BaseException,
    *,
    code: str,
    operation_id: str,
    module_id: str,
    retry_class: str,
    authority_effect: str,
    durable_effect_refs: Iterable[Any] = (),
    evidence_refs: Iterable[Any] = (),
    allowed_next_actions: Iterable[str] = (),
    secret_values: Iterable[str] = (),
    message: str | None = None,
) -> MotherError:
    """Translate a vendor exception into a typed, redacted Mother envelope.

    Durable and evidence references retain their declared types.  Only free-text
    fields are redacted.  A secret embedded in a typed reference is rejected by
    ``MotherError`` instead of coercing that reference into a string.
    """

    secrets = tuple(str(value) for value in secret_values if value)
    source_message = str(error) if message is None else str(message)
    actions = _tuple_field(allowed_next_actions, "allowed_next_actions")
    if any(not isinstance(action, str) for action in actions):
        raise TypeError("allowed_next_actions must contain strings")

    return MotherError(
        code=code,
        message=_redact_text(source_message, secrets),
        operation_id=_redact_text(operation_id, secrets),
        module_id=_redact_text(module_id, secrets),
        retry_class=retry_class,
        authority_effect=authority_effect,
        durable_effect_refs=_tuple_field(
            durable_effect_refs,
            "durable_effect_refs",
        ),
        evidence_refs=_tuple_field(evidence_refs, "evidence_refs"),
        allowed_next_actions=tuple(
            "[REDACTED]"
            if _redact_text(action, secrets) != action
            else action
            for action in actions
        ),
        cause_class=type(error).__name__,
        secret_values=secrets,
    )


def exit_code_for(error: MotherError) -> int:
    """Map a typed failure namespace to a stable non-zero process code."""

    if not isinstance(error, MotherError):
        raise TypeError("exit_code_for requires MotherError")
    for prefix, exit_code in _NAMESPACE_EXIT_CODES.items():
        if error.code.startswith(prefix):
            return exit_code
    return 1


__all__ = [
    "AUTHORITY_EFFECTS",
    "MotherError",
    "RETRY_CLASSES",
    "exit_code_for",
    "wrap_vendor_error",
]
