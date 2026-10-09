from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cutover = load(
    "parallel_lineage_cutover_test",
    "nanojev_tinystories_parallel_lineage_cutover.py",
)
trainer = load(
    "parallel_lineage_train_test",
    "nanojev_tinystories_parallel_lineage_train.py",
)
probe = load(
    "parallel_lineage_probe_test",
    "nanojev_tinystories_parallel_lineage_probe.py",
)


def test_continuous_default_and_balanced_fresh_cycle_plans():
    assert trainer.DEFAULT_MAX_CYCLES == 0
    train = trainer.center_train.objective_unit_plan(1, label="train_units")
    predev = trainer.center_train.objective_unit_plan(1, label="predev_units")
    assert sum(train.values()) == 16
    assert sum(predev.values()) == 16
    assert tuple(train) == tuple(trainer.base.TASKS)


def test_lineage_comparison_is_loss_first_and_symmetric_on_exact_ties():
    standard = {"champion_predev_accuracy": 0.80, "champion_predev_loss": 0.51}
    triangular = {"champion_predev_accuracy": 0.70, "champion_predev_loss": 0.50}
    assert trainer.compare_lineages(standard, triangular) == "triangular"

    triangular = {"champion_predev_accuracy": 0.90, "champion_predev_loss": 0.51}
    assert trainer.compare_lineages(standard, triangular) == "triangular"

    triangular = dict(standard)
    assert trainer.compare_lineages(standard, triangular) == "tie"


def test_cycle_training_seed_is_repeatable_but_changes_by_cycle():
    assert trainer.cycle_training_seed(123, 7) == trainer.cycle_training_seed(123, 7)
    assert trainer.cycle_training_seed(123, 7) != trainer.cycle_training_seed(123, 8)


def test_bank_round_trip_preserves_exact_fingerprint_order(tmp_path, monkeypatch):
    monkeypatch.setattr(trainer.base, "serialize_question", lambda q: dict(q))
    monkeypatch.setattr(trainer.base, "deserialize_question", lambda row, _api: dict(row))
    api = SimpleNamespace(question_fingerprint=lambda q: q["fp"])
    questions = [{"fp": "a", "value": 1}, {"fp": "b", "value": 2}]
    path = tmp_path / "bank.json"
    trainer.write_bank(
        path=path,
        kind="train",
        questions=questions,
        fingerprints=["a", "b"],
        plan={"legacy": 2},
        data_cycle=17,
        retry=0,
        rejected=[],
        cycle=3,
    )
    loaded, payload = trainer.load_bank(path, api)
    assert loaded == questions
    assert payload["question_fingerprints"] == ["a", "b"]


def test_probe_declares_only_shared_committed_leader_when_one_arm_is_ahead(tmp_path):
    run = tmp_path / "run"
    (run / "lineages" / "standard").mkdir(parents=True)
    (run / "lineages" / "triangular").mkdir(parents=True)
    cycle_dir = run / "cycles" / "cycle-000003"
    cycle_dir.mkdir(parents=True)

    experiment = {
        "source_zero_mode": "preserved-cycle-000000-checkpoint",
        "source_checkpoint": "zero",
        "source_cycle": 0,
        "source_global_step": 0,
    }
    (run / "experiment.json").write_text(json.dumps(experiment), encoding="utf-8")

    result = {
        "schema_version": trainer.CYCLE_RESULT_SCHEMA,
        "cycle": 3,
        "leader": "triangular",
        "train_sha256": "train3",
        "predev_sha256": "predev3",
        "standard": {
            "champion_checkpoint": "s3",
            "champion_global_step": 20,
            "champion_predev_accuracy": 0.55,
            "champion_predev_loss": 0.70,
            "promotions": 2,
            "attempted_optimizer_steps_total": 48,
            "train_seconds_total": 10.0,
            "eval_seconds_total": 5.0,
            "cache_seconds_total": 3.0,
        },
        "triangular": {
            "champion_checkpoint": "t3",
            "champion_global_step": 24,
            "champion_predev_accuracy": 0.60,
            "champion_predev_loss": 0.65,
            "promotions": 3,
            "attempted_optimizer_steps_total": 48,
            "train_seconds_total": 11.0,
            "eval_seconds_total": 6.0,
            "cache_seconds_total": 4.0,
        },
    }
    result_path = cycle_dir / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    (run / "training_state.json").write_text(json.dumps({
        "schema_version": trainer.SCHEMA,
        "cycle": 3,
        "latest_cycle_result": str(result_path),
        "leader": "triangular",
        "updated_unix": 1.0,
    }), encoding="utf-8")

    def lineage_state(geometry: str, cycle: int):
        return {
            "schema_version": trainer.SCHEMA,
            "geometry": geometry,
            "cycle": cycle,
            "champion_checkpoint": f"{geometry}{cycle}",
            "champion_global_step": 28,
            "champion_predev_accuracy": 0.99 if cycle == 4 else 0.60,
            "champion_predev_loss": 0.01 if cycle == 4 else 0.65,
            "promotions": 4,
            "attempted_optimizer_steps_total": 64,
            "train_seconds_total": 14.0,
            "eval_seconds_total": 7.0,
            "cache_seconds_total": 5.0,
            "updated_unix": 2.0,
        }

    # Standard has already committed cycle 4 locally, triangular has not.  The
    # live probe must still report the shared cycle-3 leader, not compare unlike banks.
    (run / "lineages" / "standard" / "state.json").write_text(
        json.dumps(lineage_state("standard", 4)), encoding="utf-8"
    )
    (run / "lineages" / "triangular" / "state.json").write_text(
        json.dumps(lineage_state("triangular", 3)), encoding="utf-8"
    )

    summary = probe.summarize(run, history_limit=12)
    assert summary["committed_cycle"] == 3
    assert summary["committed_leader"] == "triangular"
    assert summary["committed_scoreboard"]["leader"] == "triangular"
    assert summary["pending_lineage_cycles"]["standard"] == 4
    assert summary["pending_lineage_cycles"]["triangular"] is None


def test_all_self_tests_pass():
    assert cutover.self_test()["ok"] is True
    assert trainer.self_test()["ok"] is True
    assert probe.self_test()["ok"] is True


def test_full_tinystories_optimizer_has_three_trainable_groups():
    import torch

    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.core = torch.nn.Linear(3, 3)
            self.tinystories_residual = torch.nn.Linear(3, 3)

    head = Head()
    tiny = torch.nn.Sequential(torch.nn.Linear(3, 4), torch.nn.Linear(4, 3))
    for parameter in tiny.parameters():
        parameter.requires_grad_(False)
    trainable = trainer.configure_tinystories_lm_trainability(tiny)
    assert trainable == sum(p.numel() for p in tiny.parameters())
    assert all(p.requires_grad for p in tiny.parameters())

    args = SimpleNamespace(
        clef_head_lr=1e-6,
        head_lr=1e-4,
        tinystories_lr=1e-5,
        weight_decay=0.01,
    )
    optimizer = trainer.build_joint_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=args
    )
    summary = trainer.optimizer_group_summary(optimizer)
    assert [row["group_name"] for row in summary] == [
        "clef_mature_head",
        "tinystories_residual_taps",
        "tinystories_full_model",
    ]
    assert [row["lr"] for row in summary] == [1e-6, 1e-4, 1e-5]
    assert summary[2]["parameters"] == trainable


def test_pre_unfreeze_optimizer_migrates_head_moments_and_starts_tiny_fresh():
    import torch

    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.core = torch.nn.Linear(2, 2)
            self.tinystories_residual = torch.nn.Linear(2, 2)

    head = Head()
    tiny = torch.nn.Linear(2, 2)
    args = SimpleNamespace(
        clef_head_lr=1e-6,
        head_lr=1e-4,
        tinystories_lr=1e-5,
        weight_decay=0.0,
    )
    mature_params, residual_params = trainer.mature._head_optimizer_parameters(head)
    legacy = torch.optim.AdamW([
        {
            "params": mature_params,
            "lr": args.clef_head_lr,
            "group_name": "clef_mature_head",
        },
        {
            "params": residual_params,
            "lr": args.head_lr,
            "group_name": "tinystories_residual_taps",
        },
    ], foreach=False)
    loss = sum(p.square().sum() for p in head.parameters())
    loss.backward()
    legacy.step()
    legacy_state = legacy.state_dict()
    assert legacy_state["state"]

    trainer.configure_tinystories_lm_trainability(tiny)
    joint = trainer.build_joint_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=args
    )
    migration = trainer.load_optimizer_state_compat(
        torch=torch, optimizer=joint, source_state=legacy_state
    )
    assert migration["mode"] == "expanded-from-frozen-backbone"
    assert migration["migrated_state_entries"] == len(legacy_state["state"])
    assert all(parameter not in joint.state for parameter in tiny.parameters())


def test_old_checkpoint_backfills_pristine_tinystories_at_unfreeze_boundary(tmp_path):
    import random
    import torch
    from safetensors.torch import save_file

    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.core = torch.nn.Linear(2, 2)
            self.tinystories_residual = torch.nn.Linear(2, 2)

    head = Head()
    tiny = torch.nn.Linear(2, 2)
    pristine = trainer._module_state_cpu(tiny)
    args = SimpleNamespace(
        clef_head_lr=1e-6,
        head_lr=1e-4,
        tinystories_lr=1e-5,
        weight_decay=0.0,
    )
    mature_params, residual_params = trainer.mature._head_optimizer_parameters(head)
    legacy = torch.optim.AdamW([
        {
            "params": mature_params,
            "lr": args.clef_head_lr,
            "group_name": "clef_mature_head",
        },
        {
            "params": residual_params,
            "lr": args.head_lr,
            "group_name": "tinystories_residual_taps",
        },
    ], foreach=False)

    checkpoint = tmp_path / "cycle-000038-reuse-004"
    checkpoint.mkdir()
    save_file(trainer._module_state_cpu(head), str(checkpoint / "head.safetensors"))
    torch.save(legacy.state_dict(), checkpoint / "optimizer.pt")
    torch.save({
        "python_random": random.getstate(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": [],
    }, checkpoint / "rng_state.pt")
    (checkpoint / "meta.json").write_text(json.dumps({
        "schema_version": trainer.SCHEMA,
        "geometry": "standard",
        "cycle": 38,
        "reuse_depth": 4,
        "global_step": 320,
        "metrics": {},
    }), encoding="utf-8")

    with torch.no_grad():
        for parameter in tiny.parameters():
            parameter.add_(100.0)
    trainer.configure_tinystories_lm_trainability(tiny)
    joint = trainer.build_joint_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=args
    )
    meta = trainer.load_checkpoint(
        torch=torch,
        checkpoint=checkpoint,
        head=head,
        tinystories_lm=tiny,
        optimizer=joint,
        pristine_tinystories_state=pristine,
    )
    assert meta["tinystories_load_mode"] == "pristine-backfill-from-frozen-era"
    for name, value in tiny.state_dict().items():
        assert torch.equal(value.detach().cpu(), pristine[name])
    assert all(parameter.requires_grad for parameter in tiny.parameters())


def test_unfreeze_phase_is_recorded_once_at_shared_commit_boundary(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    experiment_path = run / "experiment.json"
    experiment_path.write_text(json.dumps({"schema_version": trainer.SCHEMA}), encoding="utf-8")
    logger = trainer.LineageLog(run)
    args = SimpleNamespace(tinystories_lr=1e-5)
    shared = {"cycle": 38}
    phase = trainer.ensure_unfreeze_phase(
        experiment_path=experiment_path,
        shared_state=shared,
        args=args,
        logger=logger,
    )
    assert phase["started_after_committed_cycle"] == 38
    assert phase["first_trainable_cycle"] == 39
    again = trainer.ensure_unfreeze_phase(
        experiment_path=experiment_path,
        shared_state={"cycle": 45},
        args=args,
        logger=logger,
    )
    assert again == phase


def test_new_checkpoint_round_trips_lineage_specific_tinystories(tmp_path):
    import torch

    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.core = torch.nn.Linear(2, 2)
            self.tinystories_residual = torch.nn.Linear(2, 2)

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = torch.nn.Embedding(7, 2)
            self.lm_head = torch.nn.Linear(2, 7, bias=False)
            self.lm_head.weight = self.embed.weight

    head = Head()
    tiny = Tiny()
    trainer.configure_tinystories_lm_trainability(tiny)
    pristine = trainer._module_state_cpu(tiny)
    args = SimpleNamespace(
        clef_head_lr=1e-6,
        head_lr=1e-4,
        tinystories_lr=1e-5,
        weight_decay=0.0,
    )
    optimizer = trainer.build_joint_optimizer(
        torch=torch, head=head, tinystories_lm=tiny, args=args
    )
    checkpoint = trainer.save_checkpoint(
        torch=torch,
        lineage_dir=tmp_path,
        geometry="triangular",
        head=head,
        tinystories_lm=tiny,
        optimizer=optimizer,
        cycle=39,
        depth=1,
        global_step=324,
        metrics={},
    )
    assert (checkpoint / "tinystories.safetensors").is_file()

    saved = {name: value.detach().clone() for name, value in tiny.state_dict().items()}
    with torch.no_grad():
        for parameter in tiny.parameters():
            parameter.add_(5.0)
    meta = trainer.load_checkpoint(
        torch=torch,
        checkpoint=checkpoint,
        head=head,
        tinystories_lm=tiny,
        optimizer=optimizer,
        pristine_tinystories_state=pristine,
    )
    assert meta["tinystories_load_mode"] == "lineage-checkpoint"
    assert meta["optimizer_migration"]["mode"] == "exact-full-backbone"
    for name, value in tiny.state_dict().items():
        assert torch.equal(value.detach(), saved[name])



def test_prune_protects_last_shared_committed_champion(tmp_path):
    run = tmp_path / "run"
    lineage = run / "lineages" / "standard"
    checkpoints = lineage / "checkpoints"
    checkpoints.mkdir(parents=True)
    shared_champion = checkpoints / "cycle-000046-reuse-002"
    current_winner = checkpoints / "cycle-000047-reuse-003"
    last_attempt = checkpoints / "cycle-000047-reuse-004"
    for path in (
        checkpoints / "cycle-000046-reuse-001",
        shared_champion,
        checkpoints / "cycle-000046-reuse-003",
        current_winner,
        last_attempt,
    ):
        path.mkdir()
    cycle_dir = run / "cycles" / "cycle-000046"
    cycle_dir.mkdir(parents=True)
    committed = {
        "schema_version": trainer.CYCLE_RESULT_SCHEMA,
        "cycle": 46,
        "standard": {"champion_checkpoint": str(shared_champion)},
        "triangular": {"champion_checkpoint": "unused"},
    }
    committed_path = cycle_dir / "result.json"
    committed_path.write_text(json.dumps(committed), encoding="utf-8")
    (run / "training_state.json").write_text(json.dumps({
        "schema_version": trainer.SCHEMA,
        "cycle": 46,
        "latest_cycle_result": str(committed_path),
    }), encoding="utf-8")

    protected = trainer.protected_checkpoints_for_prune(
        output_dir=run,
        geometry="standard",
        winner_checkpoint=current_winner,
        last_attempt_checkpoint=last_attempt,
    )
    trainer.prune_checkpoints(lineage, keep=1, protected=protected)
    assert shared_champion.is_dir()
    assert current_winner.is_dir()
    assert last_attempt.is_dir()


def test_failed_unfreeze_recovery_restores_archived_promoted_cycle(tmp_path):
    run = tmp_path / "run"
    lineage = run / "lineages" / "standard"
    lineage.mkdir(parents=True)
    logger = trainer.LineageLog(run)

    missing_old = lineage / "checkpoints" / "cycle-000045-reuse-002"
    (lineage / "state.json").write_text(json.dumps({
        "schema_version": trainer.SCHEMA,
        "geometry": "standard",
        "cycle": 46,
        "champion_checkpoint": str(missing_old),
        "champion_global_step": 368,
        "champion_predev_accuracy": 0.5,
        "champion_predev_loss": 0.75,
        "promotions": 35,
        "attempted_optimizer_steps_total": 736,
        "train_seconds_total": 100.0,
        "eval_seconds_total": 200.0,
        "cache_seconds_total": 30.0,
    }), encoding="utf-8")

    interrupted_group = lineage / "checkpoints" / "interrupted" / "cycle-000047-123"
    winner = interrupted_group / "cycle-000047-reuse-003"
    other = interrupted_group / "cycle-000047-reuse-004"
    winner.mkdir(parents=True)
    other.mkdir(parents=True)
    (winner / "meta.json").write_text("{}", encoding="utf-8")
    (other / "meta.json").write_text("{}", encoding="utf-8")

    archive = lineage / "pre_unfreeze_interrupted" / "cycle-000047-456"
    archive.mkdir(parents=True)
    archived_result = {
        "schema_version": trainer.LINEAGE_RESULT_SCHEMA,
        "cycle": 47,
        "geometry": "standard",
        "incumbent_checkpoint": str(missing_old),
        "promoted": True,
        "winner_reuse_depth": 3,
        "champion_checkpoint": str(lineage / "checkpoints" / winner.name),
        "champion_global_step": 380,
        "champion_predev_accuracy": 0.5625,
        "champion_predev_loss": 0.72,
        "attempted_optimizer_steps_this_cycle": 16,
        "attempted_optimizer_steps_total": 752,
        "train_seconds_this_cycle": 4.0,
        "eval_seconds_this_cycle": 5.0,
        "cache_seconds_this_cycle": 1.0,
        "attempts": [
            {"checkpoint": str(lineage / "checkpoints" / winner.name)},
            {"checkpoint": str(lineage / "checkpoints" / other.name)},
        ],
    }
    (archive / "standard_result.json").write_text(json.dumps(archived_result), encoding="utf-8")

    committed_row = {
        "promotions": 35,
        "train_seconds_total": 100.0,
        "eval_seconds_total": 200.0,
        "cache_seconds_total": 30.0,
    }
    restored = trainer.restore_archived_frozen_cycle(
        output_dir=run,
        geometry="standard",
        cycle=47,
        committed_row=committed_row,
        logger=logger,
    )
    state = json.loads((lineage / "state.json").read_text(encoding="utf-8"))
    assert state["cycle"] == 47
    assert state["promotions"] == 36
    assert Path(state["champion_checkpoint"]).is_dir()
    assert Path(restored["champion_checkpoint"]).is_dir()
    assert (lineage / "checkpoints" / winner.name).is_dir()
    assert (lineage / "checkpoints" / other.name).is_dir()
    assert (run / "cycles" / "cycle-000047" / "standard_result.json").is_file()



def test_prepare_failed_unfreeze_recovery_detects_pruned_shared_champion(tmp_path):
    run = tmp_path / "run"
    cycle_dir = run / "cycles" / "cycle-000046"
    cycle_dir.mkdir(parents=True)
    committed = {
        "schema_version": trainer.CYCLE_RESULT_SCHEMA,
        "cycle": 46,
        "standard": {
            "champion_checkpoint": str(run / "missing-standard"),
            "champion_global_step": 368,
            "champion_predev_accuracy": 0.5,
            "champion_predev_loss": 0.75,
            "promotions": 35,
            "attempted_optimizer_steps_total": 736,
            "train_seconds_total": 100.0,
            "eval_seconds_total": 200.0,
            "cache_seconds_total": 30.0,
        },
        "triangular": {
            "champion_checkpoint": str(run / "lineages" / "triangular" / "checkpoints" / "cycle-000046-reuse-002"),
            "champion_global_step": 360,
            "champion_predev_accuracy": 0.5,
            "champion_predev_loss": 0.76,
            "promotions": 34,
            "attempted_optimizer_steps_total": 736,
            "train_seconds_total": 101.0,
            "eval_seconds_total": 201.0,
            "cache_seconds_total": 31.0,
        },
    }
    committed_path = cycle_dir / "result.json"
    committed_path.write_text(json.dumps(committed), encoding="utf-8")
    shared = {"cycle": 46, "latest_cycle_result": str(committed_path)}

    standard = run / "lineages" / "standard"
    standard.mkdir(parents=True)
    (standard / "state.json").write_text(json.dumps({
        **committed["standard"],
        "schema_version": trainer.SCHEMA,
        "geometry": "standard",
        "cycle": 46,
    }), encoding="utf-8")
    group = standard / "checkpoints" / "interrupted" / "cycle-000047-123"
    winner = group / "cycle-000047-reuse-002"
    winner.mkdir(parents=True)
    archive = standard / "pre_unfreeze_interrupted" / "cycle-000047-456"
    archive.mkdir(parents=True)
    (archive / "standard_result.json").write_text(json.dumps({
        "schema_version": trainer.LINEAGE_RESULT_SCHEMA,
        "cycle": 47,
        "geometry": "standard",
        "incumbent_checkpoint": str(run / "missing-standard"),
        "promoted": True,
        "winner_reuse_depth": 2,
        "champion_checkpoint": str(standard / "checkpoints" / winner.name),
        "champion_global_step": 376,
        "champion_predev_accuracy": 0.5625,
        "champion_predev_loss": 0.73,
        "attempted_optimizer_steps_this_cycle": 16,
        "attempted_optimizer_steps_total": 752,
        "train_seconds_this_cycle": 4.0,
        "eval_seconds_this_cycle": 5.0,
        "cache_seconds_this_cycle": 1.0,
        "attempts": [{"checkpoint": str(standard / "checkpoints" / winner.name)}],
    }), encoding="utf-8")

    triangular = run / "lineages" / "triangular"
    tri_checkpoint = Path(committed["triangular"]["champion_checkpoint"])
    tri_checkpoint.mkdir(parents=True)
    triangular.mkdir(parents=True, exist_ok=True)
    (triangular / "state.json").write_text(json.dumps({
        **committed["triangular"],
        "schema_version": trainer.SCHEMA,
        "geometry": "triangular",
        "cycle": 46,
    }), encoding="utf-8")

    plan = trainer.prepare_failed_unfreeze_recovery(
        output_dir=run,
        shared_state=shared,
        experiment={"backbone_unfreeze_phase": {
            "schema_version": trainer.UNFREEZE_PHASE_SCHEMA,
            "started_after_committed_cycle": 46,
        }},
        logger=trainer.LineageLog(run),
    )
    assert plan["cycle"] == 47
    assert plan["restored_geometries"] == ["standard"]
    restored_state = json.loads((standard / "state.json").read_text(encoding="utf-8"))
    assert restored_state["cycle"] == 47
    assert Path(restored_state["champion_checkpoint"]).is_dir()
