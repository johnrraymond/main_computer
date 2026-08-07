"""Calculator browser observation wrapper for the generic host-bound MCEL observer."""

from __future__ import annotations

from pathlib import Path

from main_computer.mcel_calculator_host_bound_profile import (
    APP_ID,
    CAPABILITY_INTENTS,
    INTENT_PAYLOADS,
    LOCAL_INTENTS,
    build_calculator_browser_observation_profile,
)
from main_computer.mcel_host_bound_browser_observation import (
    HostBoundBrowserObservationError,
    HostBoundBrowserObservationResult,
    run_host_bound_browser_observation,
)


class CalculatorBrowserObservationError(HostBoundBrowserObservationError):
    """Raised when the fresh Calculator browser parity observation cannot pass."""


CalculatorBrowserObservationResult = HostBoundBrowserObservationResult
CALCULATOR_BROWSER_OBSERVATION_PROFILE = build_calculator_browser_observation_profile()


def run_calculator_browser_observation(
    *,
    repo_root: Path,
    headed: bool = False,
    operation_prefix: str = "candidate",
    require_browser: bool = True,
) -> CalculatorBrowserObservationResult:
    """Run fresh generated Calculator adapter observation inside Chromium."""

    try:
        return run_host_bound_browser_observation(
            CALCULATOR_BROWSER_OBSERVATION_PROFILE,
            repo_root=repo_root,
            headed=headed,
            operation_prefix=operation_prefix,
            require_browser=require_browser,
        )
    except HostBoundBrowserObservationError as exc:
        raise CalculatorBrowserObservationError(str(exc)) from exc
