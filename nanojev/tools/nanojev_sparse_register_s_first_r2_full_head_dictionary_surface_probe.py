#!/usr/bin/env python3
"""Read-only probe for the one-token S1 full-head dictionary-training lane.

The architecture remains the mature S1/R1 system:

    pass1: Qwen([S1, prompt]) -> R1
    pass2: Qwen([S1 + R1, prompt]) -> answer

This probe reuses the established full-head surface probe for occupancy, R1/bank
geometry, decision-head drift, and true S1 step velocity, then adds the
curriculum-cutover and held-out dictionary metrics.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

DEFAULT_EXPERIMENT = Path(
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_dictionary_v1"
)
BASE_PROBE = "nanojev_sparse_register_s_first_r2_full_head_surface_probe.py"
BRANCH_FILE = "s_first_r2_full_head_dictionary_branch.json"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-dictionary-branch-v1"
ORDERED_LEGACY_DEV_METRICS_DIR = "ordered_legacy_dev_metrics"
ORDERED_LEGACY_METRICS_SCHEMA = "main-computer-nanojev-ordered-legacy-dev-metrics-v1"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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


def pct(value: Any) -> str:
    number = finite(value)
    return "-" if number is None else f"{100.0 * number:5.1f}"


def curriculum_from_config(config: dict[str, Any]) -> dict[str, float]:
    return {
        "legacy": float(config.get("legacy_training_percent", 0.0)),
        "mutation": float(config.get("mutation_training_percent", 0.0)),
        "ast": float(config.get("ast_training_percent", 0.0)),
        "consensus": float(config.get("consensus_training_percent", 0.0)),
        "triad": float(config.get("triad_training_percent", 0.0)),
        "dictionary": float(config.get("dictionary_training_percent", 0.0)),
    }


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("cycle") is not None:
            out.append(row)
    return out


def ordered_legacy_surface(exp: Path) -> dict[int, dict[str, Any]]:
    root = exp / "probes" / ORDERED_LEGACY_DEV_METRICS_DIR
    out: dict[int, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("cycle-*.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if payload.get("schema_version") != ORDERED_LEGACY_METRICS_SCHEMA:
            continue
        try:
            cycle = int(payload["cycle"])
        except (KeyError, TypeError, ValueError):
            continue
        metrics = payload.get("metrics") or {}
        out[cycle] = {
            "cycle": cycle,
            "balanced_accuracy": finite(metrics.get("balanced_accuracy")),
            "pair_win_rate": finite(metrics.get("pair_win_rate")),
            "positive_accuracy": finite(metrics.get("positive_accuracy")),
            "negative_accuracy": finite(metrics.get("negative_accuracy")),
            "pair_count": metrics.get("pair_count"),
            "checkpoint": payload.get("checkpoint"),
            "probe": payload.get("probe"),
        }
    return out


def dictionary_surface(rows: list[dict[str, Any]], fork_cycle: int, ordered_by_cycle: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        cycle = int(row.get("cycle", -1))
        if cycle <= fork_cycle:
            continue
        ordered = ordered_by_cycle.get(cycle) or {}
        out.append({
            "cycle": cycle,
            "legacy_training_percent": finite(row.get("legacy_training_percent")),
            "mutation_training_percent": finite(row.get("mutation_training_percent")),
            "ast_training_percent": finite(row.get("ast_training_percent")),
            "consensus_training_percent": finite(row.get("consensus_training_percent")),
            "triad_training_percent": finite(row.get("triad_training_percent")),
            "dictionary_training_percent": finite(row.get("dictionary_training_percent")),
            "legacy_balanced_accuracy": finite(row.get("legacy_dev_balanced_accuracy")),
            "legacy_pair_win_rate": finite(row.get("legacy_dev_pair_win_rate")),
            "ordered_legacy_balanced_accuracy": finite(row.get("ordered_legacy_dev_balanced_accuracy"))
                if row.get("ordered_legacy_dev_balanced_accuracy") is not None else ordered.get("balanced_accuracy"),
            "ordered_legacy_pair_win_rate": finite(row.get("ordered_legacy_dev_pair_win_rate"))
                if row.get("ordered_legacy_dev_pair_win_rate") is not None else ordered.get("pair_win_rate"),
            "ast_pair_win": finite(row.get("ast_dev_pair_win_rate")),
            "mutation_pair_win": finite(row.get("mutation_dev_pair_win_rate")),
            "consensus_accuracy": finite(row.get("consensus_dev_accuracy")),
            "triad_balanced_relation": finite(row.get("triad_dev_balanced_relation_accuracy")),
            "triad_topology": finite(row.get("triad_dev_topology_accuracy")),
            "dictionary_balanced_accuracy": finite(row.get("dictionary_dev_balanced_accuracy")),
            "dictionary_pair_win_rate": finite(row.get("dictionary_dev_pair_win_rate")),
            "dictionary_positive_accuracy": finite(row.get("dictionary_dev_positive_accuracy")),
            "dictionary_negative_accuracy": finite(row.get("dictionary_dev_negative_accuracy")),
            "dictionary_units_seen": row.get("dictionary_units_seen"),
        })
    return out


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    tools_dir = Path(__file__).resolve().parent
    base = load_module("nanojev_full_head_probe_for_dictionary", tools_dir / BASE_PROBE)
    exp = args.experiment_dir.expanduser().resolve(strict=True)
    branch_path = exp / BRANCH_FILE
    if not branch_path.is_file():
        raise RuntimeError(f"dictionary lane missing branch record: {branch_path}")
    branch = read_json(branch_path)
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"unsupported dictionary branch schema: {branch.get('schema_version')!r}")

    base_args = argparse.Namespace(
        experiment_dir=exp,
        active_epsilon=args.active_epsilon,
        tail_cycles=args.tail_cycles,
        checkpoints=args.checkpoints,
        no_weights=args.no_weights,
        json=False,
    )
    report = base.build_report(base_args)
    config = read_json(exp / "training_config.json")
    rows = load_history(exp / "history.jsonl")
    fork_cycle = int(branch.get("fork_cycle", -1))
    if fork_cycle < 0:
        raise RuntimeError("dictionary branch record lacks fork_cycle")
    report["schema_version"] = "main-computer-nanojev-s1-full-head-dictionary-surface-probe-v1"
    report["dictionary_branch"] = branch
    report["curriculum"] = curriculum_from_config(config)
    ordered_by_cycle = ordered_legacy_surface(exp)
    report["ordered_legacy_surface"] = [ordered_by_cycle[k] for k in sorted(ordered_by_cycle)]
    report["dictionary_surface"] = dictionary_surface(rows, fork_cycle, ordered_by_cycle)
    return report


def print_control_surface(report: dict[str, Any], tail: int) -> None:
    rows = report["history_cycles"][-tail:] if tail > 0 else report["history_cycles"]
    dictionary_by_cycle = {
        int(row["cycle"]): row for row in (report.get("dictionary_surface") or [])
    }
    ordered_by_cycle = {
        int(row["cycle"]): row for row in (report.get("ordered_legacy_surface") or [])
    }
    fork_cycle = int(report["dictionary_branch"].get("fork_cycle", -1))

    base = load_module(
        "nanojev_full_head_probe_control_printer_for_dictionary",
        Path(__file__).resolve().parent / BASE_PROBE,
    )

    print("\nCONTROL / FULL-HEAD / DICTIONARY SURFACE OVER TIME")
    print(
        "src cycle  occ%  hb active C/R1/R2c/R2e  gain  headgrad  fbgrad   "
        "LEG%  LPAIR%  ORD%  OPAIR%  AST%   MUT%  CONS%  TRIAD% TOPO%  DICT%  DPAIR%"
    )
    print(
        "--- ----- ------ ----------------------- ------ -------- ------- ------ ------- ----- ------- ------ ------ "
        "------ ------- ----- ------ -------"
    )
    for row in rows:
        cycle = int(row["cycle"])
        hb = row.get("heartbeat") or {}
        label = base.source_label(
            cycle,
            s1_fork=int(report["s_first_fork_cycle"]),
            r2_fork=int(report["r2_fork_cycle"]),
            full_fork=int(report["full_head_fork_cycle"]),
        )
        if cycle > fork_cycle:
            label = "DCT"
        active = "/".join([
            base.mean_int(hb.get("active")),
            base.mean_int(hb.get("r1_active")),
            base.mean_int(hb.get("r2_candidate_active")),
            base.mean_int(hb.get("r2_effective_active")),
        ]) if hb else "-"
        gain = (hb.get("r2_gain") or {}).get("mean")
        head_grad = (hb.get("head_grad") or {}).get("mean")
        feedback_grad = (hb.get("feedback_grad") or {}).get("mean")
        occ = row.get("occupancy_percent")
        dictionary_row = dictionary_by_cycle.get(cycle) or {}
        ordered_row = ordered_by_cycle.get(cycle) or {}
        print(
            f"{label:>3} {cycle:5d} {('-' if occ is None else f'{occ:5.1f}'):>6} "
            f"{active:>23} {base.fmt(gain):>6} {base.fmt(head_grad):>8} {base.fmt(feedback_grad):>7} "
            f"{pct(dictionary_row.get('legacy_balanced_accuracy')):>6} "
            f"{pct(dictionary_row.get('legacy_pair_win_rate')):>7} "
            f"{pct(ordered_row.get('balanced_accuracy')):>5} "
            f"{pct(ordered_row.get('pair_win_rate')):>7} "
            f"{pct(row.get('ast_pair_win')):>6} {pct(row.get('mutation_pair_win')):>6} "
            f"{pct(dictionary_row.get('consensus_accuracy')):>6} "
            f"{pct(row.get('triad_balanced_relation')):>7} {pct(row.get('triad_topology')):>5} "
            f"{pct(dictionary_row.get('dictionary_balanced_accuracy')):>6} "
            f"{pct(dictionary_row.get('dictionary_pair_win_rate')):>7}"
        )
    print(
        f"CTL=pre-S-first | S1F={report['s_first_fork_cycle']} | S1=S-first | "
        f"FHF={report['full_head_fork_cycle']} (dormant R2 created here) | "
        f"FH=single-S full-head | DCT=dictionary curriculum after cycle {fork_cycle}"
    )
    print("LEG/LPAIR=old single-symbol legacy ruler | ORD/OPAIR=held-out 3-5-symbol ordered-continuation ruler")


def print_dictionary_surface(report: dict[str, Any], tail: int) -> None:
    rows = report.get("dictionary_surface") or []
    if tail > 0:
        rows = rows[-tail:]
    print("\nDICTIONARY CURRICULUM SURFACE")
    if not rows:
        print("No post-cutover dictionary-training cycle has been committed yet.")
        return
    print("cycle  LEG%  LPAIR%  ORD%  OPAIR%  AST%   MUT%  CONS%  TRIAD% TOPO%  DICT%  DPAIR%  D+%    D-%   units")
    print("----- ----- ------- ----- ------- ------ ------ ------ ------- ----- ------ ------- ------ ------ ------")
    for row in rows:
        units = row.get("dictionary_units_seen")
        units_text = "-" if units is None else str(units)
        print(
            f"{row['cycle']:5d} "
            f"{pct(row.get('legacy_balanced_accuracy')):>5} "
            f"{pct(row.get('legacy_pair_win_rate')):>7} "
            f"{pct(row.get('ordered_legacy_balanced_accuracy')):>5} "
            f"{pct(row.get('ordered_legacy_pair_win_rate')):>7} "
            f"{pct(row.get('ast_pair_win')):>6} "
            f"{pct(row.get('mutation_pair_win')):>6} "
            f"{pct(row.get('consensus_accuracy')):>6} "
            f"{pct(row.get('triad_balanced_relation')):>7} "
            f"{pct(row.get('triad_topology')):>5} "
            f"{pct(row.get('dictionary_balanced_accuracy')):>6} "
            f"{pct(row.get('dictionary_pair_win_rate')):>7} "
            f"{pct(row.get('dictionary_positive_accuracy')):>6} "
            f"{pct(row.get('dictionary_negative_accuracy')):>6} "
            f"{units_text:>6}"
        )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    p.add_argument("--active-epsilon", type=float, default=None)
    p.add_argument("--tail-cycles", type=int, default=20, help="0 shows all history")
    p.add_argument("--checkpoints", type=int, default=2, help="retained S1 checkpoints in addition to baselines")
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

    branch = report["dictionary_branch"]
    print("NanoJev S1 full-head dictionary surface probe")
    print(f"experiment: {report['experiment_dir']}")
    print(f"latest: cycle={report['latest_cycle']} global_step={report['latest_global_step']}")
    print(
        f"dictionary fork: cycle={branch.get('fork_cycle')} global_step={branch.get('fork_global_step')} | "
        f"source={branch.get('source_checkpoint')} | selected_latest={branch.get('selected_is_latest')}"
    )
    print(
        "architecture: Qwen frozen | ONE persistent S1 | pass1 [S1,prompt] -> R1 | "
        "pass2 [S1+R1,prompt] -> answer | K=1000 | g2 frozen=0 | pass3 bypassed"
    )
    c = report["curriculum"]
    print(
        "curriculum: "
        f"legacy={c['legacy']:.0f}% mutation={c['mutation']:.0f}% AST={c['ast']:.0f}% "
        f"direct-consensus={c['consensus']:.0f}% triad={c['triad']:.0f}% dictionary={c['dictionary']:.0f}%"
    )
    base = load_module("nanojev_full_head_probe_printer_for_dictionary", Path(__file__).resolve().parent / BASE_PROBE)
    print_control_surface(report, args.tail_cycles)
    print_dictionary_surface(report, args.tail_cycles)
    base.print_s_step_velocity(report)
    base.print_geometry(report)


if __name__ == "__main__":
    main()
