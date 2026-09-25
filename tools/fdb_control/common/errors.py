from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RetryClass = Literal["never", "exact-retry", "inspect-first", "contract-open"]
EffectClass = Literal[
    "none",
    "derived-local",
    "control-state",
    "live-deployment",
    "live-fdb",
    "accepted-authority",
]


@dataclass(frozen=True, slots=True)
class FdbErrorEnvelope:
    code: str
    message: str
    module_id: str
    operation_id: str | None
    retry_class: RetryClass
    effect_class: EffectClass


class FdbControlError(RuntimeError):
    """Stable public error envelope for the new FDB control surface."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        module_id: str,
        operation_id: str | None = None,
        retry_class: RetryClass = "never",
        effect_class: EffectClass = "none",
    ) -> None:
        super().__init__(message)
        self.envelope = FdbErrorEnvelope(
            code=_required(code, "code"),
            message=_required(message, "message"),
            module_id=_required(module_id, "module_id"),
            operation_id=operation_id,
            retry_class=retry_class,
            effect_class=effect_class,
        )

    @property
    def code(self) -> str:
        return self.envelope.code

    @property
    def module_id(self) -> str:
        return self.envelope.module_id


def contract_open(code: str, message: str, module_id: str) -> FdbControlError:
    return FdbControlError(
        code=code,
        message=message,
        module_id=module_id,
        retry_class="contract-open",
        effect_class="none",
    )


def _required(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field} must be non-empty")
    return clean
