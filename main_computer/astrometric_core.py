from __future__ import annotations

"""Core astrometric lensing tools derived from the bundled black-hole prototype.

The uploaded ``black_hole-main`` project contains C++/GLSL implementations of
Schwarzschild geodesic tracing around Sagittarius A*.  This module keeps the
same physical constants and null-geodesic state model, but exposes a small,
dependency-free Python core that the Main Computer applications surface can call
safely from the local viewport server.
"""

from dataclasses import dataclass
import math
from typing import Any, Mapping


C_M_PER_S = 299_792_458.0
G_M3_KG_S2 = 6.67430e-11
SAGITTARIUS_A_MASS_KG = 8.54e36
DEFAULT_RAY_COUNT = 21
DEFAULT_START_RADIUS_RS = 42.0
DEFAULT_IMPACT_MIN_RS = 2.25
DEFAULT_IMPACT_MAX_RS = 14.0
DEFAULT_STEP_RS = 0.035
DEFAULT_MAX_STEPS = 2600
DEFAULT_TRAIL_POINTS = 180


class AstrometricInputError(ValueError):
    """Raised when a client supplies an invalid astrometric simulation request."""


@dataclass(frozen=True)
class RayState:
    r: float
    phi: float
    dr: float
    dphi: float


@dataclass(frozen=True)
class RayResult:
    impact_rs: float
    status: str
    steps: int
    closest_rs: float
    deflection_deg: float | None
    weak_field_deflection_deg: float | None
    final_angle_deg: float | None
    path: tuple[tuple[float, float], ...]


def schwarzschild_radius(mass_kg: float) -> float:
    """Return the Schwarzschild radius for ``mass_kg`` in meters."""

    if not math.isfinite(mass_kg) or mass_kg <= 0:
        raise AstrometricInputError("mass_kg must be a positive finite number.")
    return 2.0 * G_M3_KG_S2 * mass_kg / (C_M_PER_S * C_M_PER_S)


def _bounded_float(
    payload: Mapping[str, Any],
    key: str,
    default: float,
    *,
    min_value: float,
    max_value: float,
) -> float:
    raw = payload.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise AstrometricInputError(f"{key} must be a number.") from exc
    if not math.isfinite(value) or value < min_value or value > max_value:
        raise AstrometricInputError(f"{key} must be between {min_value:g} and {max_value:g}.")
    return value


def _bounded_int(
    payload: Mapping[str, Any],
    key: str,
    default: int,
    *,
    min_value: int,
    max_value: int,
) -> int:
    raw = payload.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AstrometricInputError(f"{key} must be an integer.") from exc
    if value < min_value or value > max_value:
        raise AstrometricInputError(f"{key} must be between {min_value} and {max_value}.")
    return value


def _impact_values(min_abs_rs: float, max_abs_rs: float, ray_count: int) -> list[float]:
    if ray_count == 1:
        return [0.0]
    half = ray_count // 2
    if half <= 1:
        positives = [max_abs_rs]
    else:
        step = (max_abs_rs - min_abs_rs) / float(half - 1)
        positives = [min_abs_rs + step * idx for idx in range(half)]
    values = [-value for value in reversed(positives)]
    if ray_count % 2:
        values.append(0.0)
    values.extend(positives)
    return values[:ray_count]


def _initial_state(x: float, y: float, direction_x: float = 1.0, direction_y: float = 0.0) -> RayState:
    r = math.hypot(x, y)
    if r <= 0:
        raise AstrometricInputError("initial ray radius must be positive.")
    phi = math.atan2(y, x)
    dr = direction_x * math.cos(phi) + direction_y * math.sin(phi)
    dphi = (-direction_x * math.sin(phi) + direction_y * math.cos(phi)) / r
    return RayState(r=r, phi=phi, dr=dr, dphi=dphi)


def _energy_for_null_ray(state: RayState, rs_m: float) -> float:
    f = 1.0 - rs_m / state.r
    if f <= 0:
        return 0.0
    dt_dlambda = math.sqrt((state.dr * state.dr) / (f * f) + (state.r * state.r * state.dphi * state.dphi) / f)
    return f * dt_dlambda


def _rhs(state: RayState, *, energy: float, rs_m: float) -> RayState:
    r = max(state.r, rs_m * 1.000001)
    f = max(1.0 - rs_m / r, 1.0e-9)
    dt_dlambda = energy / f
    ddr = (
        -(rs_m / (2.0 * r * r)) * f * (dt_dlambda * dt_dlambda)
        + (rs_m / (2.0 * r * r * f)) * (state.dr * state.dr)
        + (r - rs_m) * (state.dphi * state.dphi)
    )
    ddphi = -2.0 * state.dr * state.dphi / r
    return RayState(r=state.dr, phi=state.dphi, dr=ddr, dphi=ddphi)


def _add_scaled(state: RayState, delta: RayState, scale: float) -> RayState:
    return RayState(
        r=state.r + delta.r * scale,
        phi=state.phi + delta.phi * scale,
        dr=state.dr + delta.dr * scale,
        dphi=state.dphi + delta.dphi * scale,
    )


def _rk4_step(state: RayState, step_m: float, *, energy: float, rs_m: float) -> RayState:
    k1 = _rhs(state, energy=energy, rs_m=rs_m)
    k2 = _rhs(_add_scaled(state, k1, step_m / 2.0), energy=energy, rs_m=rs_m)
    k3 = _rhs(_add_scaled(state, k2, step_m / 2.0), energy=energy, rs_m=rs_m)
    k4 = _rhs(_add_scaled(state, k3, step_m), energy=energy, rs_m=rs_m)
    return RayState(
        r=state.r + (step_m / 6.0) * (k1.r + 2.0 * k2.r + 2.0 * k3.r + k4.r),
        phi=state.phi + (step_m / 6.0) * (k1.phi + 2.0 * k2.phi + 2.0 * k3.phi + k4.phi),
        dr=state.dr + (step_m / 6.0) * (k1.dr + 2.0 * k2.dr + 2.0 * k3.dr + k4.dr),
        dphi=state.dphi + (step_m / 6.0) * (k1.dphi + 2.0 * k2.dphi + 2.0 * k3.dphi + k4.dphi),
    )


def _cartesian_position(state: RayState) -> tuple[float, float]:
    return state.r * math.cos(state.phi), state.r * math.sin(state.phi)


def _cartesian_velocity(state: RayState) -> tuple[float, float]:
    dx = state.dr * math.cos(state.phi) - state.r * state.dphi * math.sin(state.phi)
    dy = state.dr * math.sin(state.phi) + state.r * state.dphi * math.cos(state.phi)
    return dx, dy


def trace_ray(
    *,
    mass_kg: float = SAGITTARIUS_A_MASS_KG,
    impact_rs: float,
    start_radius_rs: float = DEFAULT_START_RADIUS_RS,
    step_rs: float = DEFAULT_STEP_RS,
    max_steps: int = DEFAULT_MAX_STEPS,
    trail_points: int = DEFAULT_TRAIL_POINTS,
) -> RayResult:
    """Trace one 2D null ray and return an astrometric summary.

    Coordinates in the returned path are normalized to Schwarzschild radii so
    browser clients can draw the ray fan without knowing the raw meter scale.
    """

    rs_m = schwarzschild_radius(mass_kg)
    start_m = start_radius_rs * rs_m
    impact_m = impact_rs * rs_m
    step_m = step_rs * rs_m
    state = _initial_state(-start_m, impact_m)
    energy = _energy_for_null_ray(state, rs_m)
    sample_every = max(1, max_steps // max(12, trail_points))
    path: list[tuple[float, float]] = [(-start_radius_rs, impact_rs)]
    closest_m = state.r
    status = "max-steps"
    steps_taken = 0

    for step_index in range(1, max_steps + 1):
        if state.r <= rs_m * 1.001:
            status = "captured"
            break
        try:
            state = _rk4_step(state, step_m, energy=energy, rs_m=rs_m)
        except (OverflowError, ValueError):
            status = "unstable"
            break
        if not all(math.isfinite(value) for value in (state.r, state.phi, state.dr, state.dphi)):
            status = "unstable"
            break
        steps_taken = step_index
        closest_m = min(closest_m, state.r)
        x, y = _cartesian_position(state)
        if step_index % sample_every == 0:
            path.append((x / rs_m, y / rs_m))
        if state.r <= rs_m * 1.001:
            status = "captured"
            break
        if x >= start_m:
            status = "escaped"
            break
        if abs(x) > start_m * 2.5 or abs(y) > start_m * 2.5:
            status = "escaped"
            break

    x, y = _cartesian_position(state)
    final_point = (x / rs_m, y / rs_m)
    if path[-1] != final_point:
        path.append(final_point)

    final_angle_deg: float | None = None
    deflection_deg: float | None = None
    weak_field_deflection_deg: float | None = None
    if status == "escaped":
        vx, vy = _cartesian_velocity(state)
        if math.isfinite(vx) and math.isfinite(vy) and (vx or vy):
            final_angle_deg = math.degrees(math.atan2(vy, vx))
            deflection_deg = final_angle_deg
    if abs(impact_rs) > 0:
        weak_field_deflection_deg = math.degrees(2.0 / abs(impact_rs))
        if impact_rs < 0:
            weak_field_deflection_deg = -weak_field_deflection_deg

    return RayResult(
        impact_rs=impact_rs,
        status=status,
        steps=steps_taken,
        closest_rs=closest_m / rs_m,
        deflection_deg=deflection_deg,
        weak_field_deflection_deg=weak_field_deflection_deg,
        final_angle_deg=final_angle_deg,
        path=tuple(path),
    )


def simulate_astrometric_lensing(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run a compact ray-fan simulation for the astrometric application."""

    data: Mapping[str, Any] = payload or {}
    mass_kg = _bounded_float(data, "mass_kg", SAGITTARIUS_A_MASS_KG, min_value=1.0e20, max_value=1.0e42)
    start_radius_rs = _bounded_float(
        data,
        "start_radius_rs",
        DEFAULT_START_RADIUS_RS,
        min_value=8.0,
        max_value=250.0,
    )
    impact_min_rs = _bounded_float(
        data,
        "impact_min_rs",
        DEFAULT_IMPACT_MIN_RS,
        min_value=0.0,
        max_value=80.0,
    )
    impact_max_rs = _bounded_float(
        data,
        "impact_max_rs",
        DEFAULT_IMPACT_MAX_RS,
        min_value=0.5,
        max_value=160.0,
    )
    if impact_min_rs > impact_max_rs:
        raise AstrometricInputError("impact_min_rs must be less than or equal to impact_max_rs.")
    ray_count = _bounded_int(data, "ray_count", DEFAULT_RAY_COUNT, min_value=1, max_value=81)
    max_steps = _bounded_int(data, "max_steps", DEFAULT_MAX_STEPS, min_value=120, max_value=8000)
    step_rs = _bounded_float(data, "step_rs", DEFAULT_STEP_RS, min_value=0.002, max_value=0.25)
    trail_points = _bounded_int(data, "trail_points", DEFAULT_TRAIL_POINTS, min_value=20, max_value=500)

    rs_m = schwarzschild_radius(mass_kg)
    impacts = _impact_values(impact_min_rs, impact_max_rs, ray_count)
    rays = [
        trace_ray(
            mass_kg=mass_kg,
            impact_rs=impact,
            start_radius_rs=start_radius_rs,
            step_rs=step_rs,
            max_steps=max_steps,
            trail_points=trail_points,
        )
        for impact in impacts
    ]

    escaped = sum(1 for ray in rays if ray.status == "escaped")
    captured = sum(1 for ray in rays if ray.status == "captured")
    unstable = sum(1 for ray in rays if ray.status == "unstable")
    deflected = [abs(ray.deflection_deg) for ray in rays if ray.deflection_deg is not None]
    max_deflection = max(deflected) if deflected else None
    closest = min((ray.closest_rs for ray in rays), default=None)

    return {
        "ok": True,
        "model": "schwarzschild-null-geodesic-2d",
        "source": {
            "project": "black_hole-main",
            "files": ["2D_lensing.cpp", "black_hole.cpp", "geodesic.comp"],
            "notes": [
                "Uses the uploaded prototype constants G, c, and Sagittarius A* mass.",
                "Uses the same polar null-ray state variables r, phi, dr, dphi and RK4 stepping.",
            ],
        },
        "parameters": {
            "mass_kg": mass_kg,
            "schwarzschild_radius_m": rs_m,
            "start_radius_rs": start_radius_rs,
            "impact_min_rs": impact_min_rs,
            "impact_max_rs": impact_max_rs,
            "ray_count": ray_count,
            "step_rs": step_rs,
            "max_steps": max_steps,
            "trail_points": trail_points,
        },
        "summary": {
            "escaped": escaped,
            "captured": captured,
            "unstable": unstable,
            "max_deflection_deg": max_deflection,
            "closest_approach_rs": closest,
        },
        "rays": [
            {
                "impact_rs": ray.impact_rs,
                "status": ray.status,
                "steps": ray.steps,
                "closest_rs": ray.closest_rs,
                "deflection_deg": ray.deflection_deg,
                "weak_field_deflection_deg": ray.weak_field_deflection_deg,
                "final_angle_deg": ray.final_angle_deg,
                "path": [list(point) for point in ray.path],
            }
            for ray in rays
        ],
    }


def astrometric_defaults() -> dict[str, Any]:
    rs_m = schwarzschild_radius(SAGITTARIUS_A_MASS_KG)
    return {
        "ok": True,
        "defaults": {
            "mass_kg": SAGITTARIUS_A_MASS_KG,
            "schwarzschild_radius_m": rs_m,
            "ray_count": DEFAULT_RAY_COUNT,
            "start_radius_rs": DEFAULT_START_RADIUS_RS,
            "impact_min_rs": DEFAULT_IMPACT_MIN_RS,
            "impact_max_rs": DEFAULT_IMPACT_MAX_RS,
            "step_rs": DEFAULT_STEP_RS,
            "max_steps": DEFAULT_MAX_STEPS,
            "trail_points": DEFAULT_TRAIL_POINTS,
        },
        "presets": [
            {
                "id": "sagittarius-a-core",
                "label": "Sagittarius A* core",
                "mass_kg": SAGITTARIUS_A_MASS_KG,
                "start_radius_rs": DEFAULT_START_RADIUS_RS,
                "impact_min_rs": DEFAULT_IMPACT_MIN_RS,
                "impact_max_rs": DEFAULT_IMPACT_MAX_RS,
            },
            {
                "id": "wide-field",
                "label": "Wide-field weak lensing",
                "mass_kg": SAGITTARIUS_A_MASS_KG,
                "start_radius_rs": 80.0,
                "impact_min_rs": 8.0,
                "impact_max_rs": 34.0,
                "step_rs": 0.05,
                "max_steps": 3400,
            },
            {
                "id": "near-photon-sphere",
                "label": "Near photon sphere",
                "mass_kg": SAGITTARIUS_A_MASS_KG,
                "start_radius_rs": 30.0,
                "impact_min_rs": 1.6,
                "impact_max_rs": 8.0,
                "step_rs": 0.02,
                "max_steps": 3800,
            },
        ],
    }
