#!/usr/bin/env python3
"""Summarize the neutral-start TinyStories standard-vs-triangular memorization race."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_tinystories_memorization_zero_ab_train as trainer

SCHEMA = "main-computer-tinystories-memorization-zero-ab-probe-v1"
DEFAULT_RUN_DIR = trainer.DEFAULT_OUTPUT


def _load_rows(run_dir: Path, geometry: str) -> list[dict[str, Any]]:
    path = run_dir / geometry / "trajectory.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != trainer.TRAJECTORY_SCHEMA:
        raise RuntimeError(f"unsupported {geometry} trajectory schema: {payload.get('schema_version')}")
    return list(payload.get("rows") or [])


def _first_threshold(rows: list[dict[str, Any]], threshold: int) -> dict[str, Any] | None:
    row = next((row for row in rows if int(row["correct"]) >= int(threshold)), None)
    if row is None:
        return None
    return {
        "correct": int(row["correct"]),
        "optimizer_steps": int(row["optimizer_steps"]),
        "reuse_depth": int(row["reuse_depth"]),
        "train_loss": float(row["train_loss"]),
        "train_seconds": float(row["cumulative_train_seconds"]),
        "wall_seconds": float(row["cumulative_wall_seconds"]),
    }


def _winner(left: dict[str, Any] | None, right: dict[str, Any] | None, field: str) -> str | None:
    if left is None and right is None:
        return None
    if left is not None and right is None:
        return "standard"
    if right is not None and left is None:
        return "triangular"
    assert left is not None and right is not None
    a = float(left[field])
    b = float(right[field])
    if a < b:
        return "standard"
    if b < a:
        return "triangular"
    return "tie"


def summarize(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).expanduser().resolve(strict=True)
    contract = json.loads((run_dir / "race_contract.json").read_text(encoding="utf-8"))
    standard = _load_rows(run_dir, "standard")
    triangular = _load_rows(run_dir, "triangular")

    by_step_standard = {int(row["optimizer_steps"]): row for row in standard}
    by_step_triangular = {int(row["optimizer_steps"]): row for row in triangular}
    steps = sorted(set(by_step_standard) | set(by_step_triangular))
    curve = []
    for step in steps:
        row: dict[str, Any] = {"optimizer_steps": step}
        for geometry, lookup in (("standard", by_step_standard), ("triangular", by_step_triangular)):
            item = lookup.get(step)
            row[geometry] = None if item is None else {
                "correct": int(item["correct"]),
                "accuracy": float(item["train_accuracy"]),
                "loss": float(item["train_loss"]),
                "train_seconds": float(item["cumulative_train_seconds"]),
                "wall_seconds": float(item["cumulative_wall_seconds"]),
            }
        if row["standard"] is not None and row["triangular"] is not None:
            s = row["standard"]
            t = row["triangular"]
            if s["correct"] > t["correct"]:
                row["winner_by_correct"] = "standard"
            elif t["correct"] > s["correct"]:
                row["winner_by_correct"] = "triangular"
            elif s["loss"] < t["loss"]:
                row["winner_by_correct"] = "standard-loss-tiebreak"
            elif t["loss"] < s["loss"]:
                row["winner_by_correct"] = "triangular-loss-tiebreak"
            else:
                row["winner_by_correct"] = "tie"
        curve.append(row)

    thresholds = {}
    for threshold in trainer.THRESHOLDS:
        s = _first_threshold(standard, threshold)
        t = _first_threshold(triangular, threshold)
        thresholds[str(threshold)] = {
            "standard": s,
            "triangular": t,
            "winner_by_optimizer_steps": _winner(s, t, "optimizer_steps"),
            "winner_by_train_seconds": _winner(s, t, "train_seconds"),
            "winner_by_wall_seconds": _winner(s, t, "wall_seconds"),
        }

    def arm_summary(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        best_correct = max(int(row["correct"]) for row in rows)
        first_best = next(row for row in rows if int(row["correct"]) == best_correct)
        return {
            "depths_completed": len(rows),
            "best_correct": best_correct,
            "first_best_optimizer_steps": int(first_best["optimizer_steps"]),
            "first_best_loss": float(first_best["train_loss"]),
            "final_correct": int(rows[-1]["correct"]),
            "final_loss": float(rows[-1]["train_loss"]),
            "final_optimizer_steps": int(rows[-1]["optimizer_steps"]),
            "cache_seconds": float(rows[0]["cache_seconds"]),
            "final_train_seconds": float(rows[-1]["cumulative_train_seconds"]),
            "final_wall_seconds": float(rows[-1]["cumulative_wall_seconds"]),
        }

    return {
        "ok": True,
        "schema_version": SCHEMA,
        "run_dir": str(run_dir),
        "source_zero_mode": contract["source_zero_mode"],
        "source_checkpoint": contract.get("source_checkpoint"),
        "source_cycle": int(contract["source_cycle"]),
        "source_global_step": int(contract["source_global_step"]),
        "question_bank_sha256": contract["question_bank_sha256"],
        "questions": int(contract["questions"]),
        "max_optimizer_steps": int(contract["max_optimizer_steps"]),
        "standard": arm_summary(standard),
        "triangular": arm_summary(triangular),
        "thresholds": thresholds,
        "curve": curve,
    }


def self_test() -> dict[str, Any]:
    a = {"optimizer_steps": 8, "train_seconds": 2.0, "wall_seconds": 3.0}
    b = {"optimizer_steps": 12, "train_seconds": 1.5, "wall_seconds": 4.0}
    if _winner(a, b, "optimizer_steps") != "standard":
        raise AssertionError("optimizer-step comparator drifted")
    if _winner(a, b, "train_seconds") != "triangular":
        raise AssertionError("training-time comparator drifted")
    if _winner(None, b, "optimizer_steps") != "triangular":
        raise AssertionError("unreached-threshold comparator drifted")
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "thresholds": list(trainer.THRESHOLDS),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    print(json.dumps(summarize(args.run_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
