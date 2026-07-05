from __future__ import annotations

import math

import pytest

from main_computer.astrometric_core import (
    AstrometricInputError,
    SAGITTARIUS_A_MASS_KG,
    simulate_astrometric_lensing,
    schwarzschild_radius,
    trace_ray,
)
from main_computer.viewport_state import _application_route_target


def test_sagittarius_a_schwarzschild_radius_matches_uploaded_black_hole_constant() -> None:
    radius = schwarzschild_radius(SAGITTARIUS_A_MASS_KG)

    assert radius == pytest.approx(1.269e10, rel=2e-3)


def test_trace_ray_reports_capture_and_escape_states() -> None:
    captured = trace_ray(impact_rs=0.0, start_radius_rs=24.0, step_rs=0.03, max_steps=1800)
    escaped = trace_ray(impact_rs=12.0, start_radius_rs=36.0, step_rs=0.04, max_steps=2400)

    assert captured.status == "captured"
    assert captured.closest_rs <= 1.05
    assert escaped.status == "escaped"
    assert escaped.closest_rs > 1.0
    assert escaped.deflection_deg is not None
    assert math.isfinite(escaped.deflection_deg)


def test_simulate_astrometric_lensing_returns_serializable_ray_fan() -> None:
    result = simulate_astrometric_lensing(
        {
            "ray_count": 7,
            "start_radius_rs": 28,
            "impact_min_rs": 3,
            "impact_max_rs": 10,
            "step_rs": 0.04,
            "max_steps": 2000,
            "trail_points": 60,
        }
    )

    assert result["ok"] is True
    assert result["model"] == "schwarzschild-null-geodesic-2d"
    assert result["summary"]["escaped"] >= 2
    assert result["summary"]["captured"] >= 1
    assert len(result["rays"]) == 7
    assert all(isinstance(point, list) for ray in result["rays"] for point in ray["path"])


def test_simulate_astrometric_lensing_rejects_out_of_range_inputs() -> None:
    with pytest.raises(AstrometricInputError):
        simulate_astrometric_lensing({"ray_count": 500})

    with pytest.raises(AstrometricInputError):
        simulate_astrometric_lensing({"impact_min_rs": 20, "impact_max_rs": 2})


def test_astrometric_application_route_is_registered() -> None:
    assert _application_route_target("/applications/astrometric") == "astrometric"
    assert _application_route_target("/apps/astrometric") == "astrometric"
