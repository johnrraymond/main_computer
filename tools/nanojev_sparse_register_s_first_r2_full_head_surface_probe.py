#!/usr/bin/env python3
"""Read-only probe for joint NanoJev-head training over frozen Qwen.

The cut-over re-zeros and freezes R2 gain by default, so the active path is
S-first/R1 with the third Qwen pass bypassed while S, strength, the shared bank,
R1 machinery, R2 router parameters, and the decision head may co-adapt.  A later
--train-r2-gain run can release recurrence.  This probe records that mode and
measures drift from the exact post-cut-over checkpoint.
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
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_v1"
)
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-branch-v1"
GENERATOR = "s_first_recurrent_r2_full_nanojev_head_joint_train_v1"
HEARTBEAT_LOG = "s_first_r2_full_head_trainer_compact.log"
HEARTBEAT_EVENT = "s_first_r2_full_head_heartbeat"
INHERITED_SLOTS = 32


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module: {path}")
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
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def summarize(base, values: list[float]) -> dict[str, Any]:
    return base.summarize(values)


def heartbeat_by_cycle(base, path: Path) -> dict[int, dict[str, Any]]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    if not path.is_file():
        return {}
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
            "r2_rms": "r2_dynamic_raw_rms",
            "r2_gain": "r2_gain",
            "coeff_cos": "r1_r2_candidate_coefficient_cosine",
            "head_grad": "head_grad_norm",
            "feedback_grad": "soft_feedback_grad_norm",
            "nll": "mean_unit_nll",
            "elapsed": "elapsed_training_seconds",
            "step": "cycle_step",
        }
        for dst, src in mapping.items():
            number = finite(payload.get(src))
            if number is not None:
                grouped[cycle][dst].append(number)
    out: dict[int, dict[str, Any]] = {}
    for cycle, values in grouped.items():
        out[cycle] = {
            key: summarize(base, vals)
            for key, vals in values.items()
            if key not in {"elapsed", "step"}
        }
        out[cycle]["heartbeat_count"] = len(values.get("elapsed", []))
        out[cycle]["last_elapsed_training_seconds"] = max(values.get("elapsed", []), default=None)
        out[cycle]["last_cycle_step"] = int(max(values.get("step", []))) if values.get("step") else None
    return out


def history_surface(base, rows, migration, active_epsilon: float, heartbeats):
    output = base.history_surface(rows, migration=migration, active_epsilon=active_epsilon, heartbeats={})
    for row in output:
        row["heartbeat"] = heartbeats.get(int(row["cycle"]))
    return output


def cosine_flat(a, b) -> float | None:
    import torch

    aa = a.float().reshape(-1)
    bb = b.float().reshape(-1)
    denom = float(aa.norm().item() * bb.norm().item())
    if denom <= 0.0:
        return None
    return float(torch.dot(aa, bb).item() / denom)


def relative_delta(a, b) -> float | None:
    aa = a.float().reshape(-1)
    bb = b.float().reshape(-1)
    denom = float(aa.norm().item())
    if denom <= 0.0:
        return None
    return float((bb - aa).norm().item() / denom)


def effective_s(weights):
    import torch

    raw = weights["soft_feedback.base"].float().reshape(1, -1)
    direction = torch.tanh(raw)
    rms = torch.sqrt(direction.square().mean(dim=-1, keepdim=True) + 1e-8)
    normalized = direction / rms
    embedding_rms = weights["soft_feedback.embedding_rms"].float().reshape(1, 1)
    strength = torch.sigmoid(weights["soft_feedback.strength_logit"].float().reshape(1, 1))
    return (normalized * embedding_rms * strength).reshape(-1), float(strength.item())


def decision_vector(weights):
    import torch

    keys = sorted(key for key in weights if not key.startswith("soft_feedback."))
    if not keys:
        raise RuntimeError("checkpoint contains no inherited decision-head tensors")
    return torch.cat([weights[key].float().reshape(-1) for key in keys]), keys


def learned_head_drift(fork_weights, current_weights) -> dict[str, Any]:
    s0 = fork_weights["soft_feedback.base"].float()
    s1 = current_weights["soft_feedback.base"].float()
    hs0, strength0 = effective_s(fork_weights)
    hs1, strength1 = effective_s(current_weights)
    d0, keys0 = decision_vector(fork_weights)
    d1, keys1 = decision_vector(current_weights)
    if keys0 != keys1:
        raise RuntimeError("decision-head tensor keys changed after full-head fork")
    return {
        "raw_s_relative_delta": relative_delta(s0, s1),
        "raw_s_cosine": cosine_flat(s0, s1),
        "effective_s_relative_delta": relative_delta(hs0, hs1),
        "effective_s_cosine": cosine_flat(hs0, hs1),
        "strength_at_fork": strength0,
        "strength_current": strength1,
        "strength_delta": strength1 - strength0,
        "decision_head_relative_delta": relative_delta(d0, d1),
        "decision_head_cosine": cosine_flat(d0, d1),
        "decision_head_parameter_tensors": len(keys0),
        "decision_head_parameter_count": int(d0.numel()),
    }


def resolve_checkpoint(path_value: Any) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value)).expanduser()
    return path if (path / "head.safetensors").is_file() else None


def source_label(cycle: int, *, s1_fork: int, r2_fork: int, full_fork: int) -> str:
    if cycle < s1_fork:
        return "CTL"
    if cycle == s1_fork:
        return "S1F"
    # Direct pre-R2 cut-over creates dormant R2 and full-head training at the
    # same generation.  Give that shared fork the FHF label rather than making
    # it look like an already-trained R2 checkpoint.
    if cycle == full_fork:
        return "FHF"
    if cycle < r2_fork:
        return "S1"
    if cycle == r2_fork:
        return "R2F"
    if cycle < full_fork:
        return "R2"
    return "FH"


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


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    tools_dir = Path(__file__).resolve().parent
    r2 = load_module("nanojev_r2_probe_for_full_head", tools_dir / "nanojev_sparse_register_s_first_r2_surface_probe.py")
    base = r2.load_base_probe()
    exp = args.experiment_dir.expanduser().resolve(strict=True)
    for name in (
        "experiment.json", "training_state.json", "training_config.json",
        "s_first_r2_branch.json", "s_first_r2_full_head_branch.json",
    ):
        if not (exp / name).is_file():
            raise RuntimeError(f"full-head lane missing {name}: {exp}")
    experiment = read_json(exp / "experiment.json")
    state = read_json(exp / "training_state.json")
    config = read_json(exp / "training_config.json")
    r2_branch = read_json(exp / "s_first_r2_branch.json")
    branch = read_json(exp / "s_first_r2_full_head_branch.json")
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"unsupported full-head branch schema: {branch.get('schema_version')!r}")
    if experiment.get("soft_feedback_generator") != GENERATOR or config.get("soft_feedback_generator") != GENERATOR:
        raise RuntimeError("full-head generator contract mismatch")
    if config.get("decision_head_frozen") is not False:
        raise RuntimeError("training_config does not declare the inherited decision head trainable")
    full_fork = int(branch.get("fork_cycle", -1))
    r2_fork = int(r2_branch.get("fork_cycle", -1))
    s1_fork = int(r2_branch.get("parent_s_first_fork_cycle", -1))
    if min(full_fork, r2_fork, s1_fork) < 0:
        raise RuntimeError("branch metadata lacks fork cycles")
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
        r2_fork_cp = resolve_checkpoint(r2_branch.get("target_checkpoint")) or resolve_checkpoint(r2_branch.get("source_checkpoint"))
        if r2_fork_cp is not None and r2_fork_cp not in checkpoints:
            checkpoints.append(r2_fork_cp)
        full_fork_cp = resolve_checkpoint(branch.get("target_checkpoint")) or resolve_checkpoint(branch.get("source_checkpoint"))
        if full_fork_cp is not None and full_fork_cp not in checkpoints:
            checkpoints.append(full_fork_cp)
        retained = base.retained_checkpoints(exp)
        if args.checkpoints > 0:
            retained = retained[-args.checkpoints:]
        for path in retained:
            if path not in checkpoints:
                checkpoints.append(path)

    raw_surfaces = [r2.checkpoint_surface(base, path) for path in checkpoints]
    fork_cp = resolve_checkpoint(branch.get("target_checkpoint")) or resolve_checkpoint(branch.get("source_checkpoint"))
    head_drifts: list[dict[str, Any]] = []
    head_step_drifts: list[dict[str, Any]] = []
    if fork_cp is not None:
        from safetensors.torch import load_file

        fork_weights = load_file(str(fork_cp / "head.safetensors"), device="cpu")
        full_head_weights: list[tuple[int, dict[str, Any]]] = []
        for path, surface in zip(checkpoints, raw_surfaces):
            current = load_file(str(path / "head.safetensors"), device="cpu")
            cycle = int(surface.get("cycle"))
            head_drifts.append({"cycle": cycle, **learned_head_drift(fork_weights, current)})
            if cycle >= full_fork:
                full_head_weights.append((cycle, current))
        for (from_cycle, previous), (to_cycle, current) in zip(full_head_weights, full_head_weights[1:]):
            cycle_gap = to_cycle - from_cycle
            head_step_drifts.append({
                "from_cycle": from_cycle,
                "to_cycle": to_cycle,
                "cycle_gap": cycle_gap,
                "consecutive_cycle": cycle_gap == 1,
                **learned_head_drift(previous, current),
            })
    drifts = [r2.checkpoint_drift(a, b) for a, b in zip(raw_surfaces, raw_surfaces[1:])]

    return {
        "schema_version": "main-computer-nanojev-s-first-r2-full-head-surface-probe-v1",
        "experiment_dir": str(exp),
        "latest_cycle": state.get("cycle"),
        "latest_global_step": state.get("global_step"),
        "active_epsilon": float(active_epsilon),
        "s_first_fork_cycle": s1_fork,
        "r2_fork_cycle": r2_fork,
        "full_head_fork_cycle": full_fork,
        "r2_gain_trainable": bool(config.get("r2_gain_trainable", False)),
        "r2_third_pass_bypassed": bool(config.get("r2_third_pass_bypassed_when_gain_frozen", not bool(config.get("r2_gain_trainable", False)))),
        "branch": branch,
        "r2_branch": r2_branch,
        "migration": migration,
        "history_cycles": history,
        "checkpoint_surfaces": [{k: v for k, v in item.items() if not k.startswith("_")} for item in raw_surfaces],
        "checkpoint_drifts": drifts,
        "full_head_drift_from_fork": head_drifts,
        "full_head_step_drift": head_step_drifts,
        "latest_consecutive_s_step": next(
            (item for item in reversed(head_step_drifts) if item["consecutive_cycle"]),
            None,
        ),
    }


def print_history(report: dict[str, Any], tail: int) -> None:
    rows = report["history_cycles"][-tail:] if tail > 0 else report["history_cycles"]
    print("\nCONTROL / FULL-HEAD SURFACE OVER TIME")
    print("src cycle  occ%  hb active C/R1/R2c/R2e  gain  headgrad  fbgrad   AST%   MUT%  TRIAD% TOPO%")
    print("--- ----- ------ ----------------------- ------ -------- ------- ------ ------ ------- -----")
    for row in rows:
        hb = row.get("heartbeat") or {}
        label = source_label(
            int(row["cycle"]),
            s1_fork=int(report["s_first_fork_cycle"]),
            r2_fork=int(report["r2_fork_cycle"]),
            full_fork=int(report["full_head_fork_cycle"]),
        )
        active = "/".join([
            mean_int(hb.get("active")), mean_int(hb.get("r1_active")),
            mean_int(hb.get("r2_candidate_active")), mean_int(hb.get("r2_effective_active")),
        ]) if hb else "-"
        gain = (hb.get("r2_gain") or {}).get("mean")
        head_grad = (hb.get("head_grad") or {}).get("mean")
        feedback_grad = (hb.get("feedback_grad") or {}).get("mean")
        occ = row.get("occupancy_percent")
        print(
            f"{label:>3} {int(row['cycle']):5d} {('-' if occ is None else f'{occ:5.1f}'):>6} "
            f"{active:>23} {fmt(gain):>6} {fmt(head_grad):>8} {fmt(feedback_grad):>7} "
            f"{pct(row.get('ast_pair_win')):>6} {pct(row.get('mutation_pair_win')):>6} "
            f"{pct(row.get('triad_balanced_relation')):>7} {pct(row.get('triad_topology')):>5}"
        )
    print(
        f"CTL=pre-S-first | S1F={report['s_first_fork_cycle']} | S1=S-first | "
        f"FHF={report['full_head_fork_cycle']} (dormant R2 created here) | "
        "FH=joint head training (R2 gain frozen unless explicitly released)"
    )


def print_s_step_velocity(report: dict[str, Any]) -> None:
    rows = report.get("full_head_step_drift") or []
    print("\nS STEP VELOCITY (RETAINED FULL-HEAD CHECKPOINTS)")
    if not rows:
        print("No adjacent retained full-head checkpoints available for S-step measurement.")
        return
    print("from->to  gap  kind  S relΔ    S cos      effective-S relΔ  eff cos     head relΔ  head cos")
    print("--------  ---  ----  --------  ----------  ----------------  ----------  ---------  ----------")
    for row in rows:
        kind = "STEP" if row.get("consecutive_cycle") else "SPAN"
        print(
            f"{int(row['from_cycle']):>3}->{int(row['to_cycle']):<3} "
            f"{int(row['cycle_gap']):>4}  {kind:>4}  "
            f"{fmt(row.get('raw_s_relative_delta'), 6):>8}  "
            f"{fmt(row.get('raw_s_cosine'), 6):>10}  "
            f"{fmt(row.get('effective_s_relative_delta'), 6):>16}  "
            f"{fmt(row.get('effective_s_cosine'), 6):>10}  "
            f"{fmt(row.get('decision_head_relative_delta'), 6):>9}  "
            f"{fmt(row.get('decision_head_cosine'), 6):>10}"
        )
    latest = report.get("latest_consecutive_s_step")
    if latest is not None:
        print(
            "latest true S[t-1]->S[t]: "
            f"{latest['from_cycle']}->{latest['to_cycle']} | "
            f"S relΔ={fmt(latest.get('raw_s_relative_delta'), 6)} "
            f"S cos={fmt(latest.get('raw_s_cosine'), 6)} | "
            f"effective-S relΔ={fmt(latest.get('effective_s_relative_delta'), 6)} "
            f"cos={fmt(latest.get('effective_s_cosine'), 6)}"
        )
    else:
        print("No true consecutive-cycle S step is retained; SPAN rows are not S[t-1]->S[t].")


def print_geometry(report: dict[str, Any]) -> None:
    surfaces = report["checkpoint_surfaces"]
    if not surfaces:
        print("\nNo checkpoint tensors inspected.")
        return
    drift_by_cycle = {int(x["cycle"]): x for x in report["full_head_drift_from_fork"] if x.get("cycle") is not None}
    print("\nRETAINED CHECKPOINT GEOMETRY / FULL-HEAD DRIFT")
    for item in surfaces:
        cycle = int(item.get("cycle"))
        r1 = item["effective_control_map"]
        r2 = item.get("recurrent_r2")
        r2_text = "R2=-"
        if r2 is not None:
            r2_text = (
                f"gain={r2['gain']:.6f} M2/M1={fmt(r2['effective_r2_to_r1_norm_ratio'])} "
                f"cos(M1,M2cand)={fmt(r2['r1_candidate_r2_map_cosine'], 6)}"
            )
        print(
            f"cycle {cycle}: K={item['control_space_size']} rank={item['register_rank']} | "
            f"R1 rank={r1['participation_rank']:.2f} ||M1||F={r1['frobenius_norm']:.4f} | {r2_text}"
        )
        hd = drift_by_cycle.get(cycle)
        if hd is not None:
            print(
                f"           from FH fork: S relΔ={fmt(hd['raw_s_relative_delta'], 6)} "
                f"S cos={fmt(hd['raw_s_cosine'], 6)} | effective-S relΔ={fmt(hd['effective_s_relative_delta'], 6)} "
                f"cos={fmt(hd['effective_s_cosine'], 6)} | strength Δ={fmt(hd['strength_delta'], 6)}"
            )
            print(
                f"                         decision-head relΔ={fmt(hd['decision_head_relative_delta'], 6)} "
                f"cos={fmt(hd['decision_head_cosine'], 6)}"
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
    p.add_argument("--checkpoints", type=int, default=2, help="retained full-head checkpoints in addition to baselines")
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

    print("NanoJev S-first R2 full-head surface probe")
    print(f"experiment: {report['experiment_dir']}")
    print(f"latest: cycle={report['latest_cycle']} global_step={report['latest_global_step']}")
    print(f"active threshold: {report['active_epsilon']}")
    branch = report["branch"]
    print(
        f"full-head fork: cycle={branch.get('fork_cycle')} global_step={branch.get('fork_global_step')} | "
        f"source={branch.get('source_checkpoint')} | selected_latest={branch.get('selected_is_latest')}"
    )
    gain_cutover = branch.get("r2_gain_cutover") if isinstance(branch.get("r2_gain_cutover"), dict) else {}
    print(
        "R2 gain cut-over: "
        f"source={fmt(gain_cutover.get('source_r2_gain_parameter'), 6)} -> "
        f"target={fmt(gain_cutover.get('target_r2_gain_parameter'), 6)} | "
        f"rezeroed={gain_cutover.get('rezeroed')}"
    )
    if report["r2_gain_trainable"]:
        print("architecture: Qwen frozen | R2 gain RELEASED | pass1 [S] -> R1 | pass2 [S+R1] -> R2 | pass3 [S+R1+R2]")
    else:
        print("architecture: Qwen frozen | R2 gain FROZEN=0 | pass1 [S] -> R1 | pass2 [S+R1] -> answer | pass3 bypassed")
        print("R2 recurrence remains available for an explicit later --train-r2-gain release.")
    print_history(report, args.tail_cycles)
    print_s_step_velocity(report)
    print_geometry(report)


if __name__ == "__main__":
    main()
