#!/usr/bin/env python3
"""Read-only surface probe for the two-persistent-token S2->S1 full-head lane.

Reuses the mature full-head probe for behavior, controller occupancy, R1/bank
geometry, decision-head drift, and S1 velocity, then adds the quantities unique
to the new persistent prefix:

  * S1 movement since the S2 cut-over
  * S2 movement away from its native Qwen token-220 seed
  * true consecutive-cycle velocity for S1 and S2
  * cosine between the *effective* S1 seen by Qwen and direct S2 embedding

The fork history row is inherited from the one-token source architecture.  It is
therefore labeled PRE: the first post-fork cycle is the first trained S2 result.
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
    r"C:\Users\subsi\NanoJev\runs\main_computer_code_self_organizing_register_k1000_s_first_r2_full_head_s2_v1"
)
BASE_PROBE = "nanojev_sparse_register_s_first_r2_full_head_surface_probe.py"
BRANCH_FILE = "s_first_r2_full_head_s2_branch.json"
BRANCH_SCHEMA = "main-computer-nanojev-k1000-s-first-r2-full-head-s2-branch-v1"
GENERATOR = "s2_s1_r1_full_nanojev_head_joint_train_v1"
HEARTBEAT_LOG = "s_first_r2_full_head_s2_trainer_compact.log"
HEARTBEAT_EVENT = "s_first_r2_full_head_s2_heartbeat"
S2_TENSOR_KEY = "soft_feedback.s2_base"
CUTOVER_REFERENCE_FILE = "s2_cutover_reference.safetensors"


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


def rms(value) -> float:
    import torch

    return float(torch.sqrt(value.detach().float().square().mean() + 1e-30).item())


def retained_checkpoints(exp: Path) -> list[Path]:
    root = exp / "checkpoints" / "generations"
    if not root.is_dir():
        return []
    out = [path for path in root.iterdir() if path.is_dir() and (path / "head.safetensors").is_file()]

    def key(path: Path):
        try:
            return int(path.name.split("-")[-1])
        except ValueError:
            return -1

    return sorted(out, key=key)


def load_cutover_reference(exp: Path, branch: dict[str, Any]):
    from safetensors.torch import load_file

    configured = branch.get("cutover_reference_file")
    path = Path(str(configured)).expanduser() if configured else exp / CUTOVER_REFERENCE_FILE
    if not path.is_file():
        fallback = exp / CUTOVER_REFERENCE_FILE
        if fallback.is_file():
            path = fallback
        else:
            raise RuntimeError(f"S2 cut-over reference is missing: {path}")
    weights = load_file(str(path), device="cpu")
    required = {"s1_raw_at_cutover", "s1_effective_at_cutover", "s2_seed"}
    missing = sorted(required - set(weights))
    if missing:
        raise RuntimeError(f"S2 cut-over reference lacks tensors: {missing}")
    return path, weights


def checkpoint_s2_geometry(base_probe, checkpoint: Path, reference) -> dict[str, Any]:
    from safetensors.torch import load_file

    weights = load_file(str(checkpoint / "head.safetensors"), device="cpu")
    cfg = read_json(checkpoint / "config.json")
    meta = read_json(checkpoint / "meta.json")
    cycle = int(cfg.get("main_computer_cycle", meta.get("cycle", -1)))
    if S2_TENSOR_KEY not in weights:
        return {"cycle": cycle, "present": False}
    s1_raw = weights["soft_feedback.base"].float()
    s1_effective, strength = base_probe.effective_s(weights)
    s2 = weights[S2_TENSOR_KEY].float()
    return {
        "cycle": cycle,
        "present": True,
        "s1_raw_rms": rms(s1_raw),
        "s1_effective_rms": rms(s1_effective),
        "s2_rms": rms(s2),
        "strength": strength,
        "s1_raw_relative_delta_from_s2_fork": relative_delta(reference["s1_raw_at_cutover"], s1_raw),
        "s1_raw_cosine_to_s2_fork": cosine_flat(reference["s1_raw_at_cutover"], s1_raw),
        "s1_effective_relative_delta_from_s2_fork": relative_delta(
            reference["s1_effective_at_cutover"], s1_effective
        ),
        "s1_effective_cosine_to_s2_fork": cosine_flat(reference["s1_effective_at_cutover"], s1_effective),
        "s2_relative_delta_from_seed": relative_delta(reference["s2_seed"], s2),
        "s2_cosine_to_seed": cosine_flat(reference["s2_seed"], s2),
        "s1_effective_s2_cosine": cosine_flat(s1_effective, s2),
        "_s1_raw": s1_raw,
        "_s1_effective": s1_effective,
        "_s2": s2,
    }


def step_drift(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    from_cycle = int(a["cycle"])
    to_cycle = int(b["cycle"])
    gap = to_cycle - from_cycle
    return {
        "from_cycle": from_cycle,
        "to_cycle": to_cycle,
        "cycle_gap": gap,
        "kind": "STEP" if gap == 1 else "SPAN",
        "s1_raw_relative_delta": relative_delta(a["_s1_raw"], b["_s1_raw"]),
        "s1_raw_cosine": cosine_flat(a["_s1_raw"], b["_s1_raw"]),
        "s1_effective_relative_delta": relative_delta(a["_s1_effective"], b["_s1_effective"]),
        "s1_effective_cosine": cosine_flat(a["_s1_effective"], b["_s1_effective"]),
        "s2_relative_delta": relative_delta(a["_s2"], b["_s2"]),
        "s2_cosine": cosine_flat(a["_s2"], b["_s2"]),
        "s1_s2_cosine_from": a["s1_effective_s2_cosine"],
        "s1_s2_cosine_to": b["s1_effective_s2_cosine"],
    }


def public_geometry(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    tools_dir = Path(__file__).resolve().parent
    base_probe = load_module("nanojev_full_head_probe_for_s2", tools_dir / BASE_PROBE)
    base_probe.GENERATOR = GENERATOR
    base_probe.HEARTBEAT_LOG = HEARTBEAT_LOG
    base_probe.HEARTBEAT_EVENT = HEARTBEAT_EVENT

    exp = args.experiment_dir.expanduser().resolve(strict=True)
    marker = exp / BRANCH_FILE
    if not marker.is_file():
        raise RuntimeError(f"S2 lane missing {marker}")
    branch = read_json(marker)
    if branch.get("schema_version") != BRANCH_SCHEMA:
        raise RuntimeError(f"unsupported S2 branch schema: {branch.get('schema_version')!r}")

    base_args = argparse.Namespace(
        experiment_dir=exp,
        active_epsilon=args.active_epsilon,
        tail_cycles=args.tail_cycles,
        checkpoints=args.checkpoints,
        no_weights=args.no_weights,
        json=False,
    )
    report = base_probe.build_report(base_args)
    report["schema_version"] = "main-computer-nanojev-full-head-s2-surface-probe-v1"
    report["s2_branch"] = branch

    geometry: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    reference_path = None
    if not args.no_weights:
        reference_path, reference = load_cutover_reference(exp, branch)
        retained = retained_checkpoints(exp)
        if args.checkpoints > 0:
            retained = retained[-args.checkpoints:]
        raw = [checkpoint_s2_geometry(base_probe, cp, reference) for cp in retained]
        raw = [item for item in raw if item.get("present")]
        geometry = [public_geometry(item) for item in raw]
        steps = [step_drift(a, b) for a, b in zip(raw, raw[1:])]
    report["s2_cutover_reference"] = str(reference_path) if reference_path is not None else None
    report["s2_geometry"] = geometry
    report["s2_step_drift"] = steps
    return report


def fmt(value: Any, digits: int = 6) -> str:
    number = finite(value)
    return "-" if number is None else f"{number:.{digits}f}"


def print_s2_geometry(report: dict[str, Any]) -> None:
    branch = report["s2_branch"]
    print("\nS2 / S1 PERSISTENT-STATE GEOMETRY")
    print(
        f"S2 fork cycle={branch.get('fork_cycle')} | seed token={branch.get('s2_seed', {}).get('token_id')} "
        f"{branch.get('s2_seed', {}).get('token_text')!r} | fork history row is PRE-S2 behavior"
    )
    rows = report.get("s2_geometry") or []
    if not rows:
        print("No S2 checkpoint tensors inspected.")
        return
    print("cycle  S1fork cos  S1fork relD  S2seed cos  S2seed relD  cos(S1eff,S2)  S1eff rms  S2 rms")
    print("-----  ----------  -----------  ----------  -----------  -------------  ---------  ------")
    for row in rows:
        print(
            f"{int(row['cycle']):5d}  "
            f"{fmt(row.get('s1_effective_cosine_to_s2_fork')):>10}  "
            f"{fmt(row.get('s1_effective_relative_delta_from_s2_fork')):>11}  "
            f"{fmt(row.get('s2_cosine_to_seed')):>10}  "
            f"{fmt(row.get('s2_relative_delta_from_seed')):>11}  "
            f"{fmt(row.get('s1_effective_s2_cosine')):>13}  "
            f"{fmt(row.get('s1_effective_rms'), 4):>9}  "
            f"{fmt(row.get('s2_rms'), 4):>6}"
        )


def print_s2_steps(report: dict[str, Any]) -> None:
    rows = report.get("s2_step_drift") or []
    print("\nS1 / S2 STEP VELOCITY (RETAINED S2 CHECKPOINTS)")
    if not rows:
        print("No adjacent retained S2 checkpoints available yet.")
        return
    print("from->to  gap  kind  S1eff relD  S1eff cos  S2 relD    S2 cos     cos(S1,S2) end")
    print("--------  ---  ----  ----------  ---------  ---------  ---------  ---------------")
    for row in rows:
        print(
            f"{int(row['from_cycle']):3d}->{int(row['to_cycle']):<3d}  "
            f"{int(row['cycle_gap']):3d}  {row['kind']:>4}  "
            f"{fmt(row.get('s1_effective_relative_delta')):>10}  "
            f"{fmt(row.get('s1_effective_cosine')):>9}  "
            f"{fmt(row.get('s2_relative_delta')):>9}  "
            f"{fmt(row.get('s2_cosine')):>9}  "
            f"{fmt(row.get('s1_s2_cosine_to')):>15}"
        )
    true_steps = [row for row in rows if row.get("kind") == "STEP"]
    if true_steps:
        latest = true_steps[-1]
        print(
            f"latest true step {latest['from_cycle']}->{latest['to_cycle']}: "
            f"S1eff relD={fmt(latest.get('s1_effective_relative_delta'))} "
            f"cos={fmt(latest.get('s1_effective_cosine'))} | "
            f"S2 relD={fmt(latest.get('s2_relative_delta'))} "
            f"cos={fmt(latest.get('s2_cosine'))}"
        )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir", type=Path, default=DEFAULT_EXPERIMENT)
    p.add_argument("--active-epsilon", type=float, default=None)
    p.add_argument("--tail-cycles", type=int, default=20, help="0 shows all history")
    p.add_argument("--checkpoints", type=int, default=3)
    p.add_argument("--no-weights", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.tail_cycles < 0 or args.checkpoints < 0:
        raise SystemExit("--tail-cycles and --checkpoints must be >= 0")
    if args.active_epsilon is not None and (
        not math.isfinite(args.active_epsilon) or args.active_epsilon <= 0.0
    ):
        raise SystemExit("--active-epsilon must be finite and > 0")
    report = build_report(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        return

    branch = report["s2_branch"]
    print("NanoJev S2 -> S1/R1 full-head surface probe")
    print(f"experiment: {report['experiment_dir']}")
    print(f"latest: cycle={report['latest_cycle']} global_step={report['latest_global_step']}")
    print(
        f"S2 fork: cycle={branch.get('fork_cycle')} global_step={branch.get('fork_global_step')} | "
        f"source={branch.get('source_checkpoint')} | selected_latest={branch.get('selected_is_latest')}"
    )
    print(
        "architecture: Qwen frozen | [S2,S1,prompt] -> R1 | [S2,S1+R1,prompt] -> answer | "
        "K=1000 unchanged | g2 frozen=0 | pass3 bypassed"
    )
    print(
        f"S2 seed: Qwen token {branch.get('s2_seed', {}).get('token_id')} "
        f"{branch.get('s2_seed', {}).get('token_text')!r} | "
        f"S1->prompt relative positions preserved={branch.get('s1_prompt_relative_positions_preserved')}"
    )

    tools_dir = Path(__file__).resolve().parent
    base_probe = load_module("nanojev_full_head_probe_print_for_s2", tools_dir / BASE_PROBE)
    base_probe.GENERATOR = GENERATOR
    base_probe.HEARTBEAT_LOG = HEARTBEAT_LOG
    base_probe.HEARTBEAT_EVENT = HEARTBEAT_EVENT
    base_probe.print_history(report, args.tail_cycles)
    base_probe.print_geometry(report)
    print_s2_geometry(report)
    print_s2_steps(report)


if __name__ == "__main__":
    main()
