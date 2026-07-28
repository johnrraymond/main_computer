"""Production no-op and deterministic injected Mother faultpoints."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Iterable
from threading import Lock
from typing import Any


class SimulatedInterruption(RuntimeError):
    """Test-only interruption at an exact named hit count."""

    def __init__(self, faultpoint: str, hit_number: int) -> None:
        if not isinstance(faultpoint, str) or not faultpoint:
            raise ValueError("faultpoint must be a non-empty string")
        if type(hit_number) is not int or hit_number <= 0:
            raise ValueError("hit_number must be a positive integer")
        self.faultpoint = faultpoint
        self.hit_number = hit_number
        super().__init__(f"simulated interruption at {faultpoint} hit {hit_number}")


class FaultpointController:
    __slots__ = ("_schedule", "_counts", "_production", "_lock")

    def __init__(
        self,
        schedule: Mapping[str, Iterable[int] | int] | None = None,
        *,
        production: bool = False,
    ) -> None:
        normalized: dict[str, frozenset[int]] = {}
        for name, raw_hits in (schedule or {}).items():
            if not isinstance(name, str) or not name:
                raise ValueError("faultpoint names must be non-empty strings")
            if type(raw_hits) is bool:
                raise ValueError("faultpoint hit numbers must be positive integers")
            if type(raw_hits) is int:
                hits = (raw_hits,)
            else:
                try:
                    hits = tuple(raw_hits)
                except TypeError as exc:
                    raise TypeError(
                        "faultpoint schedule values must be an integer or iterable of integers"
                    ) from exc
            if not hits or any(type(hit) is not int or hit <= 0 for hit in hits):
                raise ValueError("faultpoint hit numbers must be positive integers")
            if len(set(hits)) != len(hits):
                raise ValueError("faultpoint schedules may not contain duplicates")
            normalized[name] = frozenset(hits)
        self._schedule = normalized
        self._counts: defaultdict[str, int] = defaultdict(int)
        self._production = production
        self._lock = Lock()

    @classmethod
    def production(cls) -> "FaultpointController":
        return cls(production=True)

    @classmethod
    def injected(
        cls,
        schedule: Mapping[str, Iterable[int] | int],
    ) -> "FaultpointController":
        if not isinstance(schedule, Mapping):
            raise TypeError("schedule must be a mapping")
        return cls(schedule=schedule, production=False)

    def hit(self, name: str, context: Any = None) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("faultpoint name must be a non-empty string")
        if self._production:
            return None

        # Never inspect, copy, retain, or mutate context.  It is accepted only
        # to keep the hook useful at typed call sites.
        with self._lock:
            self._counts[name] += 1
            hit_number = self._counts[name]
            should_interrupt = hit_number in self._schedule.get(name, ())
        if should_interrupt:
            raise SimulatedInterruption(name, hit_number)
        return None

    def hit_count(self, name: str) -> int:
        with self._lock:
            return self._counts.get(name, 0)


_PRODUCTION = FaultpointController.production()


def hit(name: str, context: Any = None) -> None:
    """Production hook: a true no-op."""

    return _PRODUCTION.hit(name, context)


__all__ = ["FaultpointController", "SimulatedInterruption", "hit"]
