#!/usr/bin/env python3
"""Live scoreboard for the persistent standard-vs-triangular TinyStories lineages.

The declared leader always comes from the latest *shared committed cycle*, so both
champions were measured on the same persisted predev bank.  If training is currently
mid-cycle, partial lineage/candidate progress is shown separately and is never promoted
to an apples-to-oranges winner.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import nanojev_tinystories_parallel_lineage_train as trainer

SCHEMA = "main-computer-tinystories-parallel-lineage-probe-v1"
DEFAULT_RUN_DIR = trainer.DEFAULT_OUTPUT


def read_json_optional(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def lineage_view(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "cycle": int(state["cycle"]),
        "champion_checkpoint": state["champion_checkpoint"],
        "champion_global_step": int(state["champion_global_step"]),
        "champion_predev_accuracy": state.get("champion_predev_accuracy"),
        "champion_predev_loss": state.get("champion_predev_loss"),
        "promotions": int(state["promotions"]),
        "attempted_optimizer_steps_total": int(state["attempted_optimizer_steps_total"]),
        "train_seconds_total": float(state["train_seconds_total"]),
        "eval_seconds_total": float(state["eval_seconds_total"]),
        "cache_seconds_total": float(state["cache_seconds_total"]),
        "updated_unix": float(state["updated_unix"]),
    }


def load_history(run_dir: Path, limit: int) -> list[dict[str, Any]]:
    paths = sorted((run_dir / "cycles").glob("cycle-*/result.json")) if (run_dir / "cycles").is_dir() else []
    if int(limit) > 0:
        paths = paths[-int(limit):]
    rows = []
    for path in paths:
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("schema_version") != trainer.CYCLE_RESULT_SCHEMA:
            continue
        rows.append({
            "cycle": int(row["cycle"]),
            "leader": row["leader"],
            "standard": {
                "accuracy": float(row["standard"]["champion_predev_accuracy"]),
                "loss": float(row["standard"]["champion_predev_loss"]),
                "champion_global_step": int(row["standard"]["champion_global_step"]),
                "attempted_optimizer_steps_total": int(row["standard"]["attempted_optimizer_steps_total"]),
            },
            "triangular": {
                "accuracy": float(row["triangular"]["champion_predev_accuracy"]),
                "loss": float(row["triangular"]["champion_predev_loss"]),
                "champion_global_step": int(row["triangular"]["champion_global_step"]),
                "attempted_optimizer_steps_total": int(row["triangular"]["attempted_optimizer_steps_total"]),
            },
            "train_sha256": row["train_sha256"],
            "predev_sha256": row["predev_sha256"],
        })
    return rows


def summarize(run_dir: Path, *, history_limit: int = 12) -> dict[str, Any]:
    run_dir = Path(run_dir).expanduser().resolve(strict=True)
    experiment = json.loads((run_dir / "experiment.json").read_text(encoding="utf-8"))
    shared = json.loads((run_dir / "training_state.json").read_text(encoding="utf-8"))
    progress = read_json_optional(run_dir / "progress.json")
    lineage_states = {
        geometry: read_json_optional(run_dir / "lineages" / geometry / "state.json")
        for geometry in trainer.GEOMETRIES
    }
    in_progress = {
        geometry: read_json_optional(run_dir / "lineages" / geometry / "in_progress.json")
        for geometry in trainer.GEOMETRIES
    }
    history = load_history(run_dir, history_limit)

    committed_cycle = int(shared["cycle"])
    latest = None
    if shared.get("latest_cycle_result"):
        latest_path = Path(str(shared["latest_cycle_result"]))
        if latest_path.is_file():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))

    leader_counts = {"standard": 0, "triangular": 0, "tie": 0}
    for row in history:
        leader_counts[str(row["leader"])] = leader_counts.get(str(row["leader"]), 0) + 1

    pending_cycles = {
        geometry: (
            None
            if state is None or int(state["cycle"]) <= committed_cycle
            else int(state["cycle"])
        )
        for geometry, state in lineage_states.items()
    }

    committed_scoreboard = None
    if latest is not None:
        committed_scoreboard = {
            "cycle": int(latest["cycle"]),
            "leader": latest["leader"],
            "same_predev_bank": latest["predev_sha256"],
            "same_train_bank": latest["train_sha256"],
            "standard": {
                "accuracy": float(latest["standard"]["champion_predev_accuracy"]),
                "loss": float(latest["standard"]["champion_predev_loss"]),
                "champion_global_step": int(latest["standard"]["champion_global_step"]),
                "promotions": int(latest["standard"]["promotions"]),
                "attempted_optimizer_steps_total": int(latest["standard"]["attempted_optimizer_steps_total"]),
                "train_seconds_total": float(latest["standard"]["train_seconds_total"]),
            },
            "triangular": {
                "accuracy": float(latest["triangular"]["champion_predev_accuracy"]),
                "loss": float(latest["triangular"]["champion_predev_loss"]),
                "champion_global_step": int(latest["triangular"]["champion_global_step"]),
                "promotions": int(latest["triangular"]["promotions"]),
                "attempted_optimizer_steps_total": int(latest["triangular"]["attempted_optimizer_steps_total"]),
                "train_seconds_total": float(latest["triangular"]["train_seconds_total"]),
            },
        }

    return {
        "ok": True,
        "schema_version": SCHEMA,
        "run_dir": str(run_dir),
        "source_zero_mode": experiment["source_zero_mode"],
        "source_checkpoint": experiment.get("source_checkpoint"),
        "source_cycle": int(experiment["source_cycle"]),
        "source_global_step": int(experiment["source_global_step"]),
        "continuous": True,
        "committed_cycle": committed_cycle,
        "committed_leader": shared.get("leader"),
        "committed_scoreboard": committed_scoreboard,
        "lineages": {geometry: lineage_view(state) for geometry, state in lineage_states.items()},
        "pending_lineage_cycles": pending_cycles,
        "in_progress": in_progress,
        "trainer_progress": progress,
        "history_window": history,
        "history_leader_counts": leader_counts,
        "history_limit": int(history_limit),
        "comparison_rule": "same-cycle same-predev loss-first, accuracy-second; exact ties remain ties",
    }


def self_test() -> dict[str, Any]:
    a = {
        "cycle": 3,
        "champion_checkpoint": "x",
        "champion_global_step": 12,
        "champion_predev_accuracy": 0.5,
        "champion_predev_loss": 0.7,
        "promotions": 2,
        "attempted_optimizer_steps_total": 48,
        "train_seconds_total": 10.0,
        "eval_seconds_total": 5.0,
        "cache_seconds_total": 3.0,
        "updated_unix": 1.0,
    }
    view = lineage_view(a)
    if view is None or view["cycle"] != 3 or view["promotions"] != 2:
        raise AssertionError(view)
    if trainer.compare_lineages(
        {"champion_predev_accuracy": 0.6, "champion_predev_loss": 0.5},
        {"champion_predev_accuracy": 0.7, "champion_predev_loss": 0.4},
    ) != "triangular":
        raise AssertionError("trainer comparator drifted")
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "comparison_rule": "committed shared cycle only",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--history", type=int, default=12)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    print(json.dumps(summarize(args.run_dir, history_limit=args.history), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
