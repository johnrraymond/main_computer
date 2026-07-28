from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping


@dataclass
class OpenContractGuard:
    """Runtime proof that a contract-open mutation failed before all effects."""

    expected_error: str
    observed_error: str | None = None
    lock_acquisitions: int = 0
    staging_actions: int = 0
    durable_writes: int = 0
    external_effects: int = 0

    def record_lock_acquisition(self) -> None:
        self.lock_acquisitions += 1

    def record_staging_action(self) -> None:
        self.staging_actions += 1

    def record_durable_write(self) -> None:
        self.durable_writes += 1

    def record_external_effect(self) -> None:
        self.external_effects += 1

    def record_error(self, error: Any) -> str:
        code = self._extract_error_code(error)
        self.observed_error = code
        return code

    @contextmanager
    def expect_rejection(self) -> Iterator["OpenContractGuard"]:
        try:
            yield self
        except BaseException as exc:
            self.record_error(exc)
        else:
            if self.observed_error is None:
                raise AssertionError(
                    f"expected contract-open rejection {self.expected_error}, "
                    "but no error was observed"
                )

    def verify(self) -> None:
        assert self.observed_error == self.expected_error, (
            f"expected exact contract-open error {self.expected_error}, "
            f"observed {self.observed_error!r}"
        )
        assert self.lock_acquisitions == 0, (
            f"contract-open path acquired {self.lock_acquisitions} lock(s)"
        )
        assert self.staging_actions == 0, (
            f"contract-open path performed {self.staging_actions} staging action(s)"
        )
        assert self.durable_writes == 0, (
            f"contract-open path performed {self.durable_writes} durable write(s)"
        )
        assert self.external_effects == 0, (
            f"contract-open path performed {self.external_effects} external effect(s)"
        )

    @staticmethod
    def _extract_error_code(error: Any) -> str:
        if isinstance(error, str):
            return error
        if isinstance(error, Mapping):
            for key in ("code", "error_code"):
                value = error.get(key)
                if isinstance(value, str):
                    return value
        for attribute in ("code", "error_code"):
            value = getattr(error, attribute, None)
            if isinstance(value, str):
                return value
        raise AssertionError(
            "contract-open rejection must expose an exact string code through "
            "a string value, mapping code/error_code, or object code/error_code"
        )
