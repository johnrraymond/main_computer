#!/usr/bin/env python3
"""Read-only surface probe for the S-first recurrent-R2 K=1000 NanoJev lane.

It combines the inherited control/S-first history with R2-specific runtime telemetry
and checkpoint geometry.  It never runs Qwen or mutates the experiment.

Runtime architecture under test:

    Qwen([S,prompt])           -> C1 -> R1
    Qwen([S+R1,prompt])        -> C2 -> R2
    Qwen([S+R1+R2,prompt])     -> answer

C1 and C2 have separate rank-32 routers but share the same learned K=1000 bank.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_v1"
)
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-branch-v1"
HEARTBEAT_LOG = "s_first_r2_trainer_compact.log"
HEARTBEAT_EVENT = "s_first_r2_heartbeat"
INHERITED_SLOTS = 32


def load_base_probe():
    path = Path(__file__).resolve().parent / "nanojev_sparse_register_surface_probe.py"
    spec = importlib.util.spec_from_file_location("nanojev_sparse_surface_base_for_r2", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import base surface probe: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def summarize(base, values: list[float]) -> dict[str, Any]:
    return base.summarize(values)


def heartbeat_by_cycle(base, path: Path) -> dict[int, dict[str, Any]]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for payload in base.load_jsonl(path, allow_non_json_lines=True):
        if payload.get("event") != HEARTBEAT_EVENT:
            continue
        try:
            cycle = int(payload["cycle"])
        except (KeyError, TypeError, ValueError):
            continue
        mapping = {
            "active": "active_above_threshold",
            "r1_active": "r1_active_above_threshold",
            "r2_candidate_active": "r2_candidate_active_above_threshold",
            "r2_effective_active": "r2_effective_active_above_threshold",
            "dynamic_rms": "dynamic_raw_rms",
            "r1_rms": "r1_dynamic_raw_rms",
            "r2_candidate_rms": "r2_candidate_raw_rms",
            "r2_rms": "r2_dynamic_raw_rms",
            "r2_gain": "r2_gain",
            "coeff_cos": "r1_r2_candidate_coefficient_cosine",
            "residual_cos": "r1_r2_candidate_residual_cosine",
            "d23": "second_to_third_leaf_displacement_rms",
            "nll": "mean_unit_nll",
            "elapsed": "elapsed_training_seconds",
            "step": "cycle_step",
        }
        for dst, src in mapping.items():
            number = finite(payload.get(src))
            if number is not None:
                grouped[cycle][dst].append(number)
    result: dict[int, dict[str, Any]] = {}
    for cycle, values in grouped.items():
        result[cycle] = {
            key: summarize(base, vals)
            for key, vals in values.items()
            if key not in {"elapsed", "step"}
        }
        result[cycle]["heartbeat_count"] = len(values.get("elapsed", []))
        result[cycle]["last_elapsed_training_seconds"] = max(values.get("elapsed", []), default=None)
        result[cycle]["last_cycle_step"] = int(max(values.get("step", []))) if values.get("step") else None
    return result


def history_surface(base, rows, migration, active_epsilon: float, heartbeats):
    output = base.history_surface(
        rows,
        migration=migration,
        active_epsilon=active_epsilon,
        heartbeats={},
    )
    for row in output:
        row["heartbeat"] = heartbeats.get(int(row["cycle"]))
    return output


def rank_summary(base, matrix):
    import torch

    singular = torch.linalg.svdvals(matrix.float())
    return {
        **base._effective_rank_from_singular_values(singular),
        "singular_values": [float(v) for v in singular.tolist()],
    }


def cosine_flat(a, b) -> float | None:
    import torch

    a = a.float().reshape(-1)
    b = b.float().reshape(-1)
    denom = float(a.norm().item() * b.norm().item())
    if denom <= 0.0:
        return None
    return float(torch.dot(a, b).item() / denom)


def checkpoint_surface(base, checkpoint: Path) -> dict[str, Any]:
    from safetensors.torch import load_file

    parent = base.checkpoint_surface(checkpoint, inherited_slots=INHERITED_SLOTS)
    weights = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    required = (
        "soft_feedback.r2_router_down.weight",
        "soft_feedback.r2_router_score.weight",
        "soft_feedback.r2_router_coeff.weight",
        "soft_feedback.r2_gain",
    )
    if not all(key in weights for key in required):
        parent["recurrent_r2"] = None
        parent["_combined_map"] = parent["_effective_map"]
        return parent

    coeff2 = weights["soft_feedback.r2_router_coeff.weight"].float()
    bank = parent["_normalized_bank"].float()
    if coeff2.shape[0] != bank.shape[0]:
        raise RuntimeError(f"R2 coeff/bank row mismatch at {checkpoint}: {coeff2.shape} vs {bank.shape}")
    map1 = parent["_effective_map"].float()
    map2_candidate = coeff2.T @ bank
    gain_parameter = float(weights["soft_feedback.r2_gain"].float().reshape(-1)[0].item())
    gain = math.tanh(gain_parameter)
    map2_effective = map2_candidate * gain
    combined_map = map1 + map2_effective
    parent["recurrent_r2"] = {
        "gain_parameter": gain_parameter,
        "gain": gain,
        "router_coeff_row_l2": base._tensor_quantiles(coeff2.norm(dim=1)),
        "candidate_map": rank_summary(base, map2_candidate),
        "effective_map": rank_summary(base, map2_effective),
        "combined_r1_plus_r2_map": rank_summary(base, combined_map),
        "r1_candidate_r2_map_cosine": cosine_flat(map1, map2_candidate),
        "effective_r2_to_r1_norm_ratio": (
            float(map2_effective.norm().item() / map1.norm().item())
            if float(map1.norm().item()) > 0.0
            else None
        ),
    }
    parent["_r2_candidate_map"] = map2_candidate
    parent["_r2_coeff"] = coeff2
    parent["_combined_map"] = combined_map
    return parent



def infer_zero_r2_fork_from_parent(surface: dict[str, Any]) -> None:
    """Reconstruct the exact cut-over R2 geometry when only the parent head remains."""
    if surface.get("recurrent_r2") is not None:
        return
    r1 = surface["effective_control_map"]
    surface["recurrent_r2"] = {
        "inferred_from_cutover_contract": True,
        "gain_parameter": 0.0,
        "gain": 0.0,
        "router_coeff_row_l2": surface["router_coeff_row_l2"],
        "candidate_map": dict(r1),
        "effective_map": {
            **dict(r1),
            "participation_rank": 0.0,
            "stable_rank": 0.0,
            "spectral_norm": 0.0,
            "frobenius_norm": 0.0,
            "singular_values": [0.0 for _ in r1.get("singular_values", [])],
        },
        "combined_r1_plus_r2_map": dict(r1),
        "r1_candidate_r2_map_cosine": 1.0,
        "effective_r2_to_r1_norm_ratio": 0.0,
    }
    surface["_r2_candidate_map"] = surface["_effective_map"].clone()
    surface["_r2_coeff"] = surface["_router_coeff"].clone()
    surface["_combined_map"] = surface["_effective_map"].clone()

def checkpoint_drift(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    import torch

    a = previous.get("_combined_map", previous["_effective_map"]).float().reshape(-1)
    b = current.get("_combined_map", current["_effective_map"]).float().reshape(-1)
    denom = float(a.norm().item())
    rel = float((b - a).norm().item() / denom) if denom > 0.0 else None
    cos = cosine_flat(a, b)
    bank_a = previous["_normalized_bank"]
    bank_b = current["_normalized_bank"]
    bank_cos = None
    if bank_a.shape == bank_b.shape:
        bank_cos = float((bank_a * bank_b).sum(dim=1).mean().item())
    r2_rel = None
    if previous.get("_r2_coeff") is not None and current.get("_r2_coeff") is not None:
        pa = previous["_r2_coeff"].float()
        pb = current["_r2_coeff"].float()
        if pa.shape == pb.shape:
            d = float(pa.norm().item())
            r2_rel = float((pb - pa).norm().item() / d) if d > 0.0 else None
    return {
        "from_cycle": previous.get("cycle"),
        "to_cycle": current.get("cycle"),
        "combined_map_relative_delta": rel,
        "combined_map_cosine": cos,
        "mean_control_bank_row_cosine": bank_cos,
        "r2_router_coeff_relative_delta": r2_rel,
    }


def clean(surface: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in surface.items() if not key.startswith("_")}


def source_label(cycle: int, *, s1_fork: int, r2_fork: int) -> str:
    if cycle < s1_fork:
        return "CTL"
    if cycle == s1_fork:
        return "S1F"
    if cycle < r2_fork:
        return "S1"
    if cycle == r2_fork:
        return "R2F"
    return "R2"


def fmt(value: Any, digits: int = 3) -> str:
    number = finite(value)
    return "-" if number is None else f"{number:.{digits}f}"


def pct(value: Any) -> str:
    number = finite(value)
    return "-" if number is None else f"{100.0 * number:5.1f}"


def mean_int(summary: dict[str, Any] | None) -> str:
    if not summary or not summary.get("n"):
        return "-"
    return f"{summary['mean']:.0f}"


def resolve_checkpoint(path_value: Any) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value)).expanduser()
    return path if (path / "head.safetensors").is_file() else None


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    base = load_base_probe()
    exp = args.experiment_dir.expanduser().resolve(strict=True)
    for name in ("experiment.json", "training_state.json", "training_config.json", "s_first_r2_branch.json"):
        if not (exp / name).is_file():
            raise RuntimeError(f"R2 lane missing {name}: {exp}")
    experiment = read_json(exp / "experiment.json")
    state = read_json(exp / "training_state.json")
    config = read_json(exp / "training_config.json")
    branch = read_json(exp / "s_first_r2_branch.json")
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"unsupported R2 branch schema: {branch.get('schema_version')!r}")
    r2_fork = int(branch.get("fork_cycle", -1))
    s1_fork = int(branch.get("parent_s_first_fork_cycle", -1))
    if r2_fork < 0 or s1_fork < 0:
        raise RuntimeError("branch metadata lacks S-first/R2 fork cycles")
    migration = experiment.get("lane_migration") if isinstance(experiment.get("lane_migration"), dict) else None
    active_epsilon = args.active_epsilon
    if active_epsilon is None:
        active_epsilon = finite(config.get("register_active_epsilon")) or 0.01

    heartbeats = heartbeat_by_cycle(base, exp / HEARTBEAT_LOG)
    history = history_surface(
        base,
        base.load_jsonl(exp / "history.jsonl"),
        migration,
        float(active_epsilon),
        heartbeats,
    )

    checkpoints: list[Path] = []
    if not args.no_weights:
        if migration:
            migration_cp = resolve_checkpoint(migration.get("source_checkpoint"))
            if migration_cp is not None:
                checkpoints.append(migration_cp)
        fork_cp = resolve_checkpoint(branch.get("target_checkpoint")) or resolve_checkpoint(branch.get("source_checkpoint"))
        if fork_cp is not None and fork_cp not in checkpoints:
            checkpoints.append(fork_cp)
        retained = base.retained_checkpoints(exp)
        if args.checkpoints > 0:
            retained = retained[-args.checkpoints:]
        for path in retained:
            if path not in checkpoints:
                checkpoints.append(path)

    raw_surfaces = [checkpoint_surface(base, path) for path in checkpoints]
    for surface in raw_surfaces:
        if int(surface.get("cycle") or -1) == r2_fork and surface.get("recurrent_r2") is None:
            infer_zero_r2_fork_from_parent(surface)
    drifts = [checkpoint_drift(a, b) for a, b in zip(raw_surfaces, raw_surfaces[1:])]
    return {
        "schema_version": "main-computer-nanojev-s-first-r2-surface-probe-v1",
        "experiment_dir": str(exp),
        "latest_cycle": state.get("cycle"),
        "latest_global_step": state.get("global_step"),
        "active_epsilon": float(active_epsilon),
        "s_first_fork_cycle": s1_fork,
        "r2_fork_cycle": r2_fork,
        "branch": branch,
        "migration": migration,
        "architecture": {
            "pass1": "Qwen([S,prompt]) -> C1 -> R1",
            "pass2": "Qwen([S+R1,prompt]) -> C2 -> R2",
            "pass3": "Qwen([S+R1+R2,prompt]) -> answer",
            "shared_control_bank": True,
        },
        "history_cycles": history,
        "retained_checkpoint_surfaces": [clean(item) for item in raw_surfaces],
        "checkpoint_drifts": drifts,
    }


def print_history(report: dict[str, Any], tail: int) -> None:
    rows = report["history_cycles"][-tail:] if tail > 0 else report["history_cycles"]
    print("\nCONTROL / RECURRENCE SURFACE OVER TIME")
    print("src cycle  occ%  hb active mean C/R1/R2c/R2e    gain   R1rms   R2rms  coeffcos   AST%   MUT%  TRIAD% TOPO%")
    print("--- ----- ------ --------------------------- ------- ------- ------- --------- ------ ------ ------- -----")
    for row in rows:
        hb = row.get("heartbeat") or {}
        label = source_label(
            int(row["cycle"]),
            s1_fork=int(report["s_first_fork_cycle"]),
            r2_fork=int(report["r2_fork_cycle"]),
        )
        active_text = "/".join(
            [
                mean_int(hb.get("active")),
                mean_int(hb.get("r1_active")),
                mean_int(hb.get("r2_candidate_active")),
                mean_int(hb.get("r2_effective_active")),
            ]
        ) if hb else "-"
        occ = row.get("occupancy_percent")
        gain = (hb.get("r2_gain") or {}).get("mean")
        r1 = (hb.get("r1_rms") or {}).get("mean")
        r2 = (hb.get("r2_rms") or {}).get("mean")
        coeff_cos = (hb.get("coeff_cos") or {}).get("mean")
        print(
            f"{label:>3} {int(row['cycle']):5d} {('-' if occ is None else f'{occ:5.1f}'):>6} "
            f"{active_text:>27} {fmt(gain):>7} {fmt(r1):>7} {fmt(r2):>7} {fmt(coeff_cos):>9} "
            f"{pct(row.get('ast_pair_win')):>6} {pct(row.get('mutation_pair_win')):>6} "
            f"{pct(row.get('triad_balanced_relation')):>7} {pct(row.get('triad_topology')):>5}"
        )
    print(
        f"CTL=pre-S-first | S1F=S-first fork {report['s_first_fork_cycle']} | "
        f"S1=S-first training | R2F=R2 fork {report['r2_fork_cycle']} | R2=three-pass training"
    )


def print_geometry(report: dict[str, Any]) -> None:
    surfaces = report["retained_checkpoint_surfaces"]
    if not surfaces:
        print("\nNo checkpoint tensors inspected.")
        return
    print("\nRETAINED CHECKPOINT GEOMETRY")
    for item in surfaces:
        r1 = item["effective_control_map"]
        r2 = item.get("recurrent_r2")
        line = (
            f"cycle {item.get('cycle')}: K={item['control_space_size']} rank={item['register_rank']} | "
            f"R1 rank={r1['participation_rank']:.2f} ||M1||F={r1['frobenius_norm']:.4f}"
        )
        if r2 is None:
            print(line + " | R2=-")
            continue
        candidate = r2["candidate_map"]
        effective = r2["effective_map"]
        combined = r2["combined_r1_plus_r2_map"]
        print(
            line
            + f" | gain={r2['gain']:.6f} | R2cand rank={candidate['participation_rank']:.2f} "
              f"||M2cand||F={candidate['frobenius_norm']:.4f}"
        )
        print(
            f"           effective R2 ||M2||F={effective['frobenius_norm']:.4f} "
            f"M2/M1={fmt(r2['effective_r2_to_r1_norm_ratio'])} | "
            f"cos(M1,M2cand)={fmt(r2['r1_candidate_r2_map_cosine'], 6)} | "
            f"combined rank={combined['participation_rank']:.2f} ||M1+M2||F={combined['frobenius_norm']:.4f}"
        )
    for drift in report["checkpoint_drifts"]:
        print(
            f"drift {drift['from_cycle']} -> {drift['to_cycle']}: "
            f"combined-map rel-delta={fmt(drift['combined_map_relative_delta'])}, "
            f"cos={fmt(drift['combined_map_cosine'], 6)}, "
            f"bank-row mean-cos={fmt(drift['mean_control_bank_row_cosine'], 6)}, "
            f"R2-router rel-delta={fmt(drift['r2_router_coeff_relative_delta'])}"
        )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    p.add_argument("--active-epsilon", type=float, default=None)
    p.add_argument("--tail-cycles", type=int, default=20, help="0 shows all history")
    p.add_argument("--checkpoints", type=int, default=2, help="retained R2 checkpoints in addition to baselines")
    p.add_argument("--no-weights", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.tail_cycles < 0 or args.checkpoints < 0:
        raise SystemExit("--tail-cycles and --checkpoints must be >= 0")
    if args.active_epsilon is not None and (not math.isfinite(args.active_epsilon) or args.active_epsilon <= 0.0):
        raise SystemExit("--active-epsilon must be finite and > 0")
    report = build_report(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        return

    print("NanoJev S-first recurrent-R2 sparse-register surface probe")
    print(f"experiment: {report['experiment_dir']}")
    print(f"latest: cycle={report['latest_cycle']} global_step={report['latest_global_step']}")
    print(f"active threshold: {report['active_epsilon']}")
    branch = report["branch"]
    print(
        f"R2 fork: cycle={branch.get('fork_cycle')} global_step={branch.get('fork_global_step')} | "
        f"source={branch.get('source_checkpoint')}"
    )
    print("architecture: pass1 [S] -> R1 | pass2 [S+R1] -> R2 | pass3 [S+R1+R2] -> answer | shared K=1000 bank")
    print_history(report, args.tail_cycles)
    print_geometry(report)


if __name__ == "__main__":
    main()
