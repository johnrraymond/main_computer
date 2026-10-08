from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


common = load("center_expand_common_test", "nanojev_tinystories_center_expand_common.py")
cutover = load("center_expand_cutover_test", "nanojev_tinystories_center_expand_cutover.py")
trainer = load("center_expand_train_test", "nanojev_tinystories_center_expand_train.py")


def test_triangular_examples_are_exact():
    assert common.triangular_replication_counts(4, 6) == [1, 2, 2, 1]
    assert common.triangular_replication_counts(7, 16) == [1, 2, 3, 4, 3, 2, 1]


def test_replication_fills_arbitrary_budget_exactly_and_preserves_every_source():
    for source in range(1, 50):
        for target in range(source, source + 97):
            counts = common.triangular_replication_counts(source, target)
            assert len(counts) == source
            assert min(counts) >= 1
            assert sum(counts) == target
            assert counts == list(reversed(counts)) or abs(counts[0] - counts[-1]) <= 1


def test_embedding_expansion_is_only_copying_rows():
    embeddings = torch.tensor([[1.0], [2.0], [3.0], [4.0]])
    expanded, counts, stats = common.expand_embeddings_exact(torch, embeddings, 6)
    assert counts == [1, 2, 2, 1]
    assert expanded[:, 0].tolist() == [1.0, 2.0, 2.0, 3.0, 3.0, 4.0]
    assert stats.source_tokens == 4
    assert stats.expanded_tokens == 6


def test_expansion_refuses_to_compress():
    with pytest.raises(ValueError, match="cannot shrink"):
        common.triangular_replication_counts(8, 7)


def test_micro_head_contains_only_tinystories_backbone_projection():
    spec = common.micro_head_state_spec(torch=torch)
    assert any(name.startswith("backbone_modules.tinystories.") for name in spec)
    assert not any(name.startswith("backbone_modules.qwen.") for name in spec)
    assert not any(name.startswith("backbone_modules.pythia.") for name in spec)
    assert "tinystories_residual.memory.weight" in spec
    assert "evidence_layers.0.attention.in_proj_weight" in spec
    assert "layers.0.self_attn.in_proj_weight" in spec


def test_cutover_uses_training_state_best_checkpoint_as_authority(tmp_path):
    run = tmp_path / "run"
    champion = run / "checkpoints" / "champion"
    champion.mkdir(parents=True)
    (run / "training_state.json").write_text(
        '{"best_checkpoint": %r, "cycle": 99}' % str(champion), encoding="utf-8"
    )
    # JSON requires double quotes after repr on Windows-like paths; normalize fixture.
    text = (run / "training_state.json").read_text(encoding="utf-8").replace("'", '"')
    (run / "training_state.json").write_text(text, encoding="utf-8")
    (run / "experiment.json").write_text('{"train_plan": {}}', encoding="utf-8")
    (champion / "meta.json").write_text('{"cycle": 98, "cycle_complete": true}', encoding="utf-8")
    for name in ("head.safetensors", "tinystories.safetensors", "rng_state.pt"):
        (champion / name).write_bytes(b"x")
    _source, _state, _experiment, observed, meta = cutover.resolve_current_champion(run)
    assert observed == champion.resolve()
    assert meta["cycle"] == 98


def test_trainer_reuses_same_objective_library_but_ultracuts_population():
    source = (TOOLS / "nanojev_tinystories_center_expand_train.py").read_text(encoding="utf-8")
    assert "EfficientQuestionFactory" in source
    assert 'train_plan = objective_unit_plan(int(args.train_units), label="train_units")' in source
    assert 'predev_plan = objective_unit_plan(int(args.predev_units), label="predev_units")' in source
    assert "build_consensus_pairwise_questions" not in source  # inherited mature implementation
    assert trainer.self_test()["same_task_library"] == list(common.base.TASKS)


def test_ultracut_defaults_keep_every_objective_in_one_sixteen_question_unit():
    args = trainer.build_parser().parse_args([])
    assert args.train_units == 1
    assert args.predev_units == 1
    train_plan = trainer.objective_unit_plan(args.train_units, label="train_units")
    predev_plan = trainer.objective_unit_plan(args.predev_units, label="predev_units")
    assert train_plan == {task: int(common.base.TASK_UNITS[task]) for task in common.base.TASKS}
    assert predev_plan == train_plan
    assert sum(train_plan.values()) == 16


def test_ultracut_units_must_be_positive():
    with pytest.raises(ValueError, match="train-units must be positive"):
        trainer.objective_unit_plan(0, label="train_units")


def test_evidence_transform_happens_after_embedding_lookup():
    source = (TOOLS / "nanojev_tinystories_center_expand_common.py").read_text(encoding="utf-8")
    assert "prompt_vectors = input_embeddings(prompt_ids)" in source
    assert "expand_embeddings_exact" in source
    assert "inputs_embeds=embeds" in source
    assert "input_ids=tokens" not in source


def test_mature_reuse_is_constrained_to_tinystories_only():
    common.patch_mature_for_tinystories_only(expanded_prompt_tokens=23)
    assert common.mature.FROZEN_LABELS == ("tinystories",)


def test_cutover_json_task_order_is_canonicalized_before_validation():
    alphabetical = {
        "ast": 112,
        "consensus": 104,
        "dictionary_definition": 71,
        "english_code": 24,
        "legacy": 29,
        "mutation": 70,
        "triad": 70,
    }
    observed = trainer.canonicalize_task_plan(alphabetical, label="train_plan")
    assert tuple(observed) == tuple(common.base.TASKS)
    assert observed == {task: alphabetical[task] for task in common.base.TASKS}
    common.base.validate_plan(observed, expected_total=sum(alphabetical.values()))


def test_cutover_json_task_order_canonicalizer_rejects_key_drift():
    with pytest.raises(ValueError, match="task keys changed"):
        trainer.canonicalize_task_plan({"ast": 1}, label="train_plan")


def test_center_expand_trainer_defaults_reserve_real_replication_budget():
    args = trainer.build_parser().parse_args([])
    assert args.max_prompt_tokens == 960
    assert args.expanded_prompt_tokens == 1920
    assert trainer.validate_expansion_budgets(
        max_prompt_tokens=args.max_prompt_tokens,
        expanded_prompt_tokens=args.expanded_prompt_tokens,
    ) == (960, 1920)


def test_center_expand_trainer_rejects_noop_equal_source_and_target_budget():
    with pytest.raises(ValueError, match="non-zero vector budget"):
        trainer.validate_expansion_budgets(
            max_prompt_tokens=1920,
            expanded_prompt_tokens=1920,
        )


def test_source_budget_is_persisted_in_resume_contract_and_checkpoints():
    source = (TOOLS / "nanojev_tinystories_center_expand_train.py").read_text(encoding="utf-8")
    assert '"max_prompt_tokens": max_prompt_tokens' in source
    assert '"max_prompt_tokens": int(experiment["max_prompt_tokens"])' in source
    assert 'nominal_expansion_ratio' in source


def test_real_expansion_budget_produces_replication_greater_than_one():
    embeddings = torch.arange(960, dtype=torch.float32).unsqueeze(1)
    expanded, counts, stats = common.expand_embeddings_exact(torch, embeddings, 1920)
    assert expanded.shape[0] == 1920
    assert sum(counts) == 1920
    assert stats.source_tokens == 960
    assert stats.expanded_tokens == 1920
    assert stats.max_copies > 1


def test_resume_quarantines_uncommitted_future_cycle_checkpoints(tmp_path):
    run = tmp_path / "run"
    root = run / "checkpoints"
    committed = root / "cycle-000002-reuse-004"
    stale1 = root / "cycle-000003-reuse-001"
    stale2 = root / "cycle-000003-reuse-002.tmp"
    future = root / "cycle-000004-reuse-001"
    for path, marker in (
        (committed, "committed"),
        (stale1, "stale-1"),
        (stale2, "stale-2"),
        (future, "future"),
    ):
        path.mkdir(parents=True, exist_ok=True)
        (path / "marker.txt").write_text(marker, encoding="utf-8")

    archived = trainer.quarantine_uncommitted_checkpoints(
        output_dir=run,
        committed_cycle=2,
        protected_checkpoint=committed,
    )

    assert committed.is_dir()
    assert not stale1.exists()
    assert not stale2.exists()
    assert not future.exists()
    assert [(row["cycle"], row["reuse_depth"], row["temporary"]) for row in archived] == [
        (3, 1, False),
        (3, 2, True),
        (4, 1, False),
    ]
    archive_roots = {Path(row["archived"]).parent for row in archived}
    assert len(archive_roots) == 1
    archive_root = next(iter(archive_roots))
    assert (archive_root / "quarantine.json").is_file()
    assert (archive_root / stale1.name / "marker.txt").read_text(encoding="utf-8") == "stale-1"
    assert (archive_root / stale2.name / "marker.txt").read_text(encoding="utf-8") == "stale-2"
    assert (archive_root / future.name / "marker.txt").read_text(encoding="utf-8") == "future"

    # A second resume sees a clean replay boundary and does nothing.
    assert trainer.quarantine_uncommitted_checkpoints(
        output_dir=run,
        committed_cycle=2,
        protected_checkpoint=committed,
    ) == []


def test_resume_never_quarantines_protected_best_checkpoint(tmp_path):
    run = tmp_path / "run"
    protected = run / "checkpoints" / "cycle-000003-reuse-001"
    other = run / "checkpoints" / "cycle-000003-reuse-002"
    protected.mkdir(parents=True)
    other.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="refusing to quarantine the committed best checkpoint"):
        trainer.quarantine_uncommitted_checkpoints(
            output_dir=run,
            committed_cycle=2,
            protected_checkpoint=protected,
        )

    assert protected.is_dir()
    assert other.is_dir()
    assert not (run / "checkpoints" / "interrupted").exists()


def test_reuse_trajectory_is_post_depth_and_resume_safe(tmp_path):
    source = (TOOLS / "nanojev_tinystories_center_expand_train.py").read_text(encoding="utf-8")
    assert 'logger.set_stage("train_post_eval", cycle=cycle, reuse_depth=depth)' in source
    assert '"train_post": train_post_eval["summary"]' in source

    rows = []
    first = {
        "cycle": 1,
        "reuse_depth": 1,
        "global_step": 4,
        "train_post": {"accuracy": 0.5, "mean_loss": 0.8},
        "predev": {"accuracy": 0.4, "mean_loss": 0.9},
    }
    future = {
        "cycle": 2,
        "reuse_depth": 1,
        "global_step": 8,
        "train_post": {"accuracy": 0.75, "mean_loss": 0.5},
        "predev": {"accuracy": 0.45, "mean_loss": 0.85},
    }
    rows = trainer.upsert_reuse_trajectory_row(rows, future)
    rows = trainer.upsert_reuse_trajectory_row(rows, first)
    assert [(row["cycle"], row["reuse_depth"]) for row in rows] == [(1, 1), (2, 1)]

    replacement = dict(first)
    replacement["train_post"] = {"accuracy": 0.625, "mean_loss": 0.7}
    rows = trainer.upsert_reuse_trajectory_row(rows, replacement)
    assert len(rows) == 2
    assert rows[0]["train_post"]["accuracy"] == 0.625

    path = tmp_path / trainer.TRAJECTORY_FILENAME
    trainer.persist_reuse_trajectory(path, rows)
    loaded = trainer.load_reuse_trajectory(path)
    assert loaded == rows

    kept, dropped = trainer.trim_reuse_trajectory_after_cycle(loaded, 1)
    assert [(row["cycle"], row["reuse_depth"]) for row in kept] == [(1, 1)]
    assert [(row["cycle"], row["reuse_depth"]) for row in dropped] == [(2, 1)]
