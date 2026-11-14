#!/usr/bin/env python3
"""Read-only probe for the NanoJev sparse-register control surface over time.

The probe consumes artifacts already written by a sparse-register experiment:

* history.jsonl for cycle-level occupancy/performance history;
* k1000_trainer_compact.log for 30-second K=1000 heartbeat summaries;
* retained checkpoint head.safetensors files for parameter-space surface geometry;
* experiment.json / training_state.json for migration and latest-state metadata.

It never runs Qwen, changes a checkpoint, or writes into the experiment directory.
Task labels are not used for routing or reconstruction.  The goal is to make the
control surface observable without printing slot identities or thousand-element
coefficient vectors.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_v1"
)
DEFAULT_ACTIVE_EPSILON = 0.01
INHERITED_SLOT_COUNT = 32
HEARTBEAT_LOG = "k1000_trainer_compact.log"


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def load_jsonl(path: Path, *, allow_non_json_lines: bool = False) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            if allow_non_json_lines and not raw.startswith("{"):
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL at {path}:{lineno}: {exc}") from exc
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def quantile(values: Iterable[float], q: float) -> float | None:
    data = sorted(float(v) for v in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return data[lo]
    frac = pos - lo
    return data[lo] * (1.0 - frac) + data[hi] * frac


def summarize(values: Iterable[float]) -> dict[str, float | int | None]:
    data = [float(v) for v in values if finite_float(v) is not None]
    if not data:
        return {"n": 0, "min": None, "mean": None, "median": None, "p90": None, "max": None}
    return {
        "n": len(data),
        "min": min(data),
        "mean": statistics.fmean(data),
        "median": quantile(data, 0.5),
        "p90": quantile(data, 0.9),
        "max": max(data),
    }


def infer_control_space_size(row: dict[str, Any], migration: dict[str, Any] | None) -> int | None:
    direct = row.get("control_space_size")
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass
    usage = row.get("register_usage")
    if isinstance(usage, dict):
        coeff = usage.get("aggregate_mean_abs_coefficients")
        if isinstance(coeff, list):
            return len(coeff)
        occupied = usage.get("occupied_slots")
        dormant = usage.get("dormant_slots")
        if isinstance(occupied, list) and isinstance(dormant, list):
            return len(occupied) + len(dormant)
    if migration:
        cycle = int(row.get("cycle", -1))
        source_cycle = int(migration.get("source_cycle", -1))
        if cycle <= source_cycle:
            return int(migration.get("old_control_space_size", 0)) or None
        return int(migration.get("new_control_space_size", 0)) or None
    return None


def occupied_count(row: dict[str, Any], *, active_epsilon: float) -> int | None:
    usage = row.get("register_usage")
    if not isinstance(usage, dict):
        return None
    if usage.get("occupied_slot_count") is not None:
        try:
            return int(usage["occupied_slot_count"])
        except (TypeError, ValueError):
            pass
    occupied = usage.get("occupied_slots")
    if isinstance(occupied, list):
        return len(occupied)
    coeff = usage.get("aggregate_mean_abs_coefficients")
    if isinstance(coeff, list):
        return sum(1 for value in coeff if finite_float(value) is not None and float(value) >= active_epsilon)
    return None


def heartbeat_by_cycle(path: Path) -> dict[int, dict[str, Any]]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"active": [], "dynamic_rms": [], "nll": [], "elapsed": [], "step": []}
    )
    for payload in load_jsonl(path, allow_non_json_lines=True):
        if payload.get("event") != "k1000_heartbeat":
            continue
        try:
            cycle = int(payload["cycle"])
        except (KeyError, TypeError, ValueError):
            continue
        fields = {
            "active": payload.get("active_above_threshold"),
            "dynamic_rms": payload.get("dynamic_raw_rms"),
            "nll": payload.get("mean_unit_nll"),
            "elapsed": payload.get("elapsed_training_seconds"),
            "step": payload.get("cycle_step"),
        }
        for key, value in fields.items():
            numeric = finite_float(value)
            if numeric is not None:
                grouped[cycle][key].append(numeric)

    out: dict[int, dict[str, Any]] = {}
    for cycle, values in grouped.items():
        out[cycle] = {
            "heartbeat_count": len(values["elapsed"]),
            "active": summarize(values["active"]),
            "dynamic_raw_rms": summarize(values["dynamic_rms"]),
            "mean_unit_nll": summarize(values["nll"]),
            "last_elapsed_training_seconds": max(values["elapsed"]) if values["elapsed"] else None,
            "last_cycle_step": int(max(values["step"])) if values["step"] else None,
        }
    return out


def history_surface(
    history_rows: list[dict[str, Any]],
    *,
    migration: dict[str, Any] | None,
    active_epsilon: float,
    heartbeats: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    previous_occupied: int | None = None
    previous_k: int | None = None
    for payload in history_rows:
        if payload.get("cycle") is None:
            continue
        cycle = int(payload["cycle"])
        k = infer_control_space_size(payload, migration)
        occupied = occupied_count(payload, active_epsilon=active_epsilon)
        delta_occupied = None
        if occupied is not None and previous_occupied is not None and k == previous_k:
            delta_occupied = occupied - previous_occupied
        occupancy_pct = None
        if occupied is not None and k:
            occupancy_pct = 100.0 * occupied / k
        rows.append(
            {
                "cycle": cycle,
                "global_step": payload.get("global_step"),
                "control_space_size": k,
                "occupied_slot_count": occupied,
                "occupancy_percent": occupancy_pct,
                "delta_occupied_same_k": delta_occupied,
                "heartbeat": heartbeats.get(cycle),
                "legacy_pair_win": payload.get("legacy_dev_pair_win_rate"),
                "mutation_pair_win": payload.get("mutation_dev_pair_win_rate"),
                "ast_pair_win": payload.get("ast_dev_pair_win_rate"),
                "triad_balanced_relation": payload.get("triad_dev_balanced_relation_accuracy"),
                "triad_topology": payload.get("triad_dev_topology_accuracy"),
                "triad_non_ambiguous_topology": payload.get("triad_dev_non_ambiguous_topology_accuracy"),
                "training_seconds": payload.get("training_seconds"),
                "cycle_total_seconds": payload.get("cycle_total_seconds"),
            }
        )
        if occupied is not None:
            previous_occupied = occupied
            previous_k = k
    return rows


def _tensor_quantiles(tensor) -> dict[str, float]:
    values = tensor.detach().float().reshape(-1).cpu().tolist()
    return {
        "min": min(values),
        "p10": float(quantile(values, 0.10)),
        "p50": float(quantile(values, 0.50)),
        "p90": float(quantile(values, 0.90)),
        "p99": float(quantile(values, 0.99)),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


def _effective_rank_from_singular_values(singular_values) -> dict[str, float]:
    import torch

    s = singular_values.detach().float()
    energy = s.square()
    total = float(energy.sum().item())
    if total <= 0.0:
        return {"participation_rank": 0.0, "stable_rank": 0.0, "spectral_norm": 0.0, "frobenius_norm": 0.0}
    p = energy / energy.sum()
    participation = float((1.0 / p.square().sum()).item())
    spectral = float(s.max().item())
    fro = math.sqrt(total)
    stable = total / (spectral * spectral) if spectral > 0.0 else 0.0
    return {
        "participation_rank": participation,
        "stable_rank": stable,
        "spectral_norm": spectral,
        "frobenius_norm": fro,
    }


def checkpoint_surface(checkpoint: Path, *, inherited_slots: int) -> dict[str, Any]:
    try:
        import torch
        import torch.nn.functional as F
        from safetensors.torch import load_file
    except ImportError as exc:
        raise RuntimeError(
            "checkpoint surface analysis requires torch and safetensors; run with the NanoJev venv"
        ) from exc

    head = checkpoint / "head.safetensors"
    if not head.is_file():
        raise RuntimeError(f"checkpoint has no head.safetensors: {checkpoint}")
    weights = load_file(str(head), device="cpu")
    bank_key = "soft_feedback.control_bank"
    coeff_key = "soft_feedback.router_coeff.weight"
    if bank_key not in weights or coeff_key not in weights:
        raise RuntimeError(f"checkpoint lacks sparse-register tensors: {checkpoint}")

    bank = weights[bank_key].float()
    coeff = weights[coeff_key].float()
    if bank.ndim != 2 or coeff.ndim != 2:
        raise RuntimeError(f"unexpected sparse-register tensor shape at {checkpoint}")
    if bank.shape[0] != coeff.shape[0]:
        raise RuntimeError(f"control-bank/router row mismatch at {checkpoint}: {bank.shape} vs {coeff.shape}")

    k, hidden = int(bank.shape[0]), int(bank.shape[1])
    rank = int(coeff.shape[1])
    normalized_bank = F.normalize(bank, p=2.0, dim=1)
    row_norm = coeff.norm(dim=1)

    gram = normalized_bank @ normalized_bank.T
    eye = torch.eye(k, dtype=gram.dtype)
    off = gram - eye
    denom = max(k * (k - 1), 1)
    orth_mse = float(off.square().sum().item() / denom)
    abs_off = off.abs()
    mask = ~torch.eye(k, dtype=torch.bool)
    off_values = abs_off[mask]

    effective_map = coeff.T @ normalized_bank
    singular = torch.linalg.svdvals(effective_map)
    effective_rank = _effective_rank_from_singular_values(singular)

    old_n = min(int(inherited_slots), k)
    old_map = coeff[:old_n].T @ normalized_bank[:old_n]
    if old_n < k:
        new_map = coeff[old_n:].T @ normalized_bank[old_n:]
    else:
        new_map = torch.zeros_like(old_map)
    old_norm = float(old_map.norm().item())
    new_norm = float(new_map.norm().item())
    total_norm = float(effective_map.norm().item())

    cycle = None
    config_path = checkpoint / "config.json"
    if config_path.is_file():
        config = read_json(config_path)
        cycle = config.get("main_computer_cycle")

    thresholds = (1e-8, 1e-6, 1e-4, 1e-3, 1e-2)
    row_nonzero = {f"gt_{threshold:g}": int((row_norm > threshold).sum().item()) for threshold in thresholds}

    return {
        "cycle": int(cycle) if cycle is not None else None,
        "checkpoint": str(checkpoint),
        "control_space_size": k,
        "hidden_size": hidden,
        "register_rank": rank,
        "router_coeff_row_l2": _tensor_quantiles(row_norm),
        "router_coeff_rows_above_l2_threshold": row_nonzero,
        "inherited_32_router_row_l2": _tensor_quantiles(row_norm[:old_n]) if old_n else None,
        "new_router_row_l2": _tensor_quantiles(row_norm[old_n:]) if old_n < k else None,
        "control_bank": {
            "row_norm": _tensor_quantiles(bank.norm(dim=1)),
            "offdiag_abs_mean": float(off_values.mean().item()) if off_values.numel() else 0.0,
            "offdiag_abs_p90": float(torch.quantile(off_values, 0.90).item()) if off_values.numel() else 0.0,
            "offdiag_abs_max": float(off_values.max().item()) if off_values.numel() else 0.0,
            "orthogonality_penalty": orth_mse,
        },
        "effective_control_map": {
            **effective_rank,
            "inherited_32_frobenius_norm": old_norm,
            "new_968_frobenius_norm": new_norm,
            "new_to_inherited_norm_ratio": (new_norm / old_norm) if old_norm > 0.0 else None,
            "total_frobenius_norm": total_norm,
            "singular_values": [float(v) for v in singular.tolist()],
        },
        "_effective_map": effective_map,
        "_normalized_bank": normalized_bank,
        "_router_coeff": coeff,
    }


def checkpoint_drift(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, float | None]:
    import torch

    a = previous["_effective_map"].reshape(-1)
    b = current["_effective_map"].reshape(-1)
    denom = float(a.norm().item())
    rel = float((b - a).norm().item() / denom) if denom > 0.0 else None
    cos_denom = float(a.norm().item() * b.norm().item())
    cosine = float(torch.dot(a, b).item() / cos_denom) if cos_denom > 0.0 else None

    bank_a = previous["_normalized_bank"]
    bank_b = current["_normalized_bank"]
    mean_row_cosine = None
    if bank_a.shape == bank_b.shape:
        mean_row_cosine = float((bank_a * bank_b).sum(dim=1).mean().item())

    coeff_a = previous["_router_coeff"]
    coeff_b = current["_router_coeff"]
    coeff_rel = None
    if coeff_a.shape == coeff_b.shape:
        coeff_denom = float(coeff_a.norm().item())
        coeff_rel = float((coeff_b - coeff_a).norm().item() / coeff_denom) if coeff_denom > 0.0 else None

    return {
        "effective_map_relative_delta": rel,
        "effective_map_cosine": cosine,
        "mean_control_bank_row_cosine": mean_row_cosine,
        "router_coeff_relative_delta": coeff_rel,
    }


def migration_baseline_surface(experiment: dict[str, Any]) -> Path | None:
    migration = experiment.get("lane_migration")
    if not isinstance(migration, dict):
        return None
    source = migration.get("source_checkpoint")
    if not source:
        return None
    path = Path(str(source)).expanduser()
    return path if (path / "head.safetensors").is_file() else None


def retained_checkpoints(experiment_dir: Path) -> list[Path]:
    root = experiment_dir / "checkpoints" / "generations"
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("cycle-*") if p.is_dir())


def clean_checkpoint_payload(surface: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in surface.items() if not k.startswith("_")}


def fmt_pct(value: Any) -> str:
    numeric = finite_float(value)
    return "-" if numeric is None else f"{100.0 * numeric:5.1f}"


def fmt_num(value: Any, digits: int = 3) -> str:
    numeric = finite_float(value)
    return "-" if numeric is None else f"{numeric:.{digits}f}"


def print_history(rows: list[dict[str, Any]], migration: dict[str, Any] | None, tail_cycles: int) -> None:
    selected = rows[-tail_cycles:] if tail_cycles > 0 else rows
    print("\nCONTROL SURFACE OVER TIME")
    print("cycle   K   occupied   occ%   dOcc   hb active min/mean/max   rms mean   AST%   MUT%   TRIAD%  TOPO%")
    print("----- ---- --------- ------ ------ ------------------------ ---------- ------ ------ ------- ------")
    source_cycle = int(migration.get("source_cycle", -1)) if migration else -1
    for row in selected:
        hb = row.get("heartbeat") or {}
        active = hb.get("active") or {}
        active_triplet = "-"
        if active.get("n"):
            active_triplet = f"{int(active['min'])}/{active['mean']:.0f}/{int(active['max'])}"
        marker = "*" if row["cycle"] == source_cycle else " "
        occupied = row["occupied_slot_count"]
        occupied_text = "-" if occupied is None else str(occupied)
        docc = row["delta_occupied_same_k"]
        docc_text = "-" if docc is None else f"{docc:+d}"
        occ_pct = row["occupancy_percent"]
        occ_pct_text = "-" if occ_pct is None else f"{occ_pct:5.1f}"
        rms = (hb.get("dynamic_raw_rms") or {}).get("mean")
        print(
            f"{marker}{row['cycle']:4d} {str(row['control_space_size'] or '-'):>4} {occupied_text:>9} "
            f"{occ_pct_text:>6} {docc_text:>6} {active_triplet:>24} {fmt_num(rms):>10} "
            f"{fmt_pct(row['ast_pair_win']):>6} {fmt_pct(row['mutation_pair_win']):>6} "
            f"{fmt_pct(row['triad_balanced_relation']):>7} {fmt_pct(row['triad_topology']):>6}"
        )
    if migration:
        print(f"* migration source cycle {source_cycle}; K={migration.get('old_control_space_size')} -> {migration.get('new_control_space_size')}")


def print_checkpoint_surface(checkpoints: list[dict[str, Any]], drifts: list[dict[str, Any]]) -> None:
    if not checkpoints:
        print("\nNo retained checkpoint tensors found for parameter-space surface analysis.")
        return
    print("\nRETAINED CHECKPOINT CONTROL GEOMETRY")
    for item in checkpoints:
        eff = item["effective_control_map"]
        coeff = item["router_coeff_row_l2"]
        bank = item["control_bank"]
        print(
            f"cycle {item.get('cycle')}: K={item['control_space_size']} rank={item['register_rank']} | "
            f"router-row L2 p50={coeff['p50']:.5f} p90={coeff['p90']:.5f} max={coeff['max']:.5f} | "
            f"effective-rank={eff['participation_rank']:.2f}/{item['register_rank']} "
            f"stable-rank={eff['stable_rank']:.2f} | map ||.||F={eff['frobenius_norm']:.4f}"
        )
        print(
            f"           inherited32 ||map||F={eff['inherited_32_frobenius_norm']:.4f} | "
            f"new ||map||F={eff['new_968_frobenius_norm']:.4f} | "
            f"new/old={fmt_num(eff['new_to_inherited_norm_ratio'])} | "
            f"bank offdiag mean={bank['offdiag_abs_mean']:.6f} max={bank['offdiag_abs_max']:.6f}"
        )
    for drift in drifts:
        print(
            f"drift {drift['from_cycle']} -> {drift['to_cycle']}: "
            f"effective-map rel-delta={fmt_num(drift['effective_map_relative_delta'])}, "
            f"cos={fmt_num(drift['effective_map_cosine'], 6)}, "
            f"bank-row mean-cos={fmt_num(drift['mean_control_bank_row_cosine'], 6)}, "
            f"router rel-delta={fmt_num(drift['router_coeff_relative_delta'])}"
        )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    experiment_dir = args.experiment_dir.expanduser().resolve(strict=True)
    experiment_path = experiment_dir / "experiment.json"
    state_path = experiment_dir / "training_state.json"
    if not experiment_path.is_file() or not state_path.is_file():
        raise RuntimeError(f"not a NanoJev experiment lane: {experiment_dir}")

    experiment = read_json(experiment_path)
    state = read_json(state_path)
    migration = experiment.get("lane_migration") if isinstance(experiment.get("lane_migration"), dict) else None
    active_epsilon = args.active_epsilon
    if active_epsilon is None:
        config_path = experiment_dir / "training_config.json"
        if config_path.is_file():
            config = read_json(config_path)
            active_epsilon = finite_float(config.get("register_active_epsilon"))
    if active_epsilon is None:
        active_epsilon = DEFAULT_ACTIVE_EPSILON

    heartbeats = heartbeat_by_cycle(experiment_dir / HEARTBEAT_LOG)
    history_rows = load_jsonl(experiment_dir / "history.jsonl")
    surface_history = history_surface(
        history_rows,
        migration=migration,
        active_epsilon=float(active_epsilon),
        heartbeats=heartbeats,
    )

    checkpoint_paths = retained_checkpoints(experiment_dir)
    if args.checkpoints > 0:
        checkpoint_paths = checkpoint_paths[-args.checkpoints :]
    checkpoint_surfaces: list[dict[str, Any]] = []
    drifts: list[dict[str, Any]] = []
    if not args.no_weights:
        baseline = migration_baseline_surface(experiment)
        analysis_paths: list[Path] = []
        if baseline is not None and baseline not in checkpoint_paths:
            analysis_paths.append(baseline)
        analysis_paths.extend(checkpoint_paths)
        raw_surfaces = [checkpoint_surface(path, inherited_slots=INHERITED_SLOT_COUNT) for path in analysis_paths]
        for previous, current in zip(raw_surfaces, raw_surfaces[1:]):
            drift = checkpoint_drift(previous, current)
            drift["from_cycle"] = previous.get("cycle")
            drift["to_cycle"] = current.get("cycle")
            drifts.append(drift)
        checkpoint_surfaces = [clean_checkpoint_payload(item) for item in raw_surfaces]

    latest_result = state.get("last_result") if isinstance(state.get("last_result"), dict) else None
    return {
        "schema_version": "main-computer-nanojev-control-surface-probe-v1",
        "experiment_dir": str(experiment_dir),
        "latest_generation": state.get("latest_generation"),
        "latest_cycle": state.get("cycle"),
        "latest_global_step": state.get("global_step"),
        "active_epsilon": float(active_epsilon),
        "migration": migration,
        "history_cycles": surface_history,
        "latest_result_summary": {
            "occupied_slot_count": (
                ((latest_result or {}).get("register_usage") or {}).get("occupied_slot_count")
                if latest_result
                else None
            ),
            "ast_dev_pair_win_rate": (latest_result or {}).get("ast_dev_pair_win_rate"),
            "mutation_dev_pair_win_rate": (latest_result or {}).get("mutation_dev_pair_win_rate"),
            "triad_dev_balanced_relation_accuracy": (latest_result or {}).get("triad_dev_balanced_relation_accuracy"),
            "triad_dev_topology_accuracy": (latest_result or {}).get("triad_dev_topology_accuracy"),
        },
        "retained_checkpoint_surfaces": checkpoint_surfaces,
        "checkpoint_drifts": drifts,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Read-only NanoJev K=1000 control-surface history probe")
    p.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    p.add_argument("--active-epsilon", type=float, default=None, help="Override occupancy threshold; default uses training_config.json")
    p.add_argument("--tail-cycles", type=int, default=20, help="Human table cycles to show; 0 shows all history")
    p.add_argument("--checkpoints", type=int, default=2, help="Retained checkpoints to inspect in addition to migration source if available")
    p.add_argument("--no-weights", action="store_true", help="Skip safetensors/torch parameter-space analysis")
    p.add_argument("--json", action="store_true", help="Emit the complete machine-readable report instead of the human readout")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.tail_cycles < 0:
        raise SystemExit("--tail-cycles must be >= 0")
    if args.checkpoints < 0:
        raise SystemExit("--checkpoints must be >= 0")
    if args.active_epsilon is not None and (not math.isfinite(args.active_epsilon) or args.active_epsilon <= 0.0):
        raise SystemExit("--active-epsilon must be finite and > 0")

    report = build_report(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        return

    print("NanoJev sparse-register control-surface probe")
    print(f"experiment: {report['experiment_dir']}")
    print(f"latest: cycle={report['latest_cycle']} global_step={report['latest_global_step']}")
    print(f"active threshold: {report['active_epsilon']}")
    migration = report.get("migration")
    if migration:
        print(
            f"migration: cycle {migration.get('source_cycle')} | "
            f"K={migration.get('old_control_space_size')} -> K={migration.get('new_control_space_size')} | "
            f"preservation max abs diff={migration.get('residual_probe_max_abs_diff')}"
        )

    print_history(report["history_cycles"], migration, args.tail_cycles)
    print_checkpoint_surface(report["retained_checkpoint_surfaces"], report["checkpoint_drifts"])


if __name__ == "__main__":
    main()
