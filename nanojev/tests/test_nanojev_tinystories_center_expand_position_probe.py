from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nanojev_tinystories_center_expand_position_probe.py"


def load_module():
    spec = importlib.util.spec_from_file_location("position_probe_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = load_module()


class FakeTokenizer:
    def __init__(self):
        self.vocab = {}

    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        words = text.replace(".", " . ").replace("?", " ? ").replace(":", " : ").split()
        out = []
        for word in words:
            key = word.lower()
            if key not in self.vocab:
                self.vocab[key] = len(self.vocab) + 1
            out.append(self.vocab[key])
        return out


def test_allocator_exact_budget_and_expected_shapes():
    assert m.replication_counts("uniform", 7, 14) == [2] * 7
    assert m.replication_counts("triangular", 7, 16) == [1, 2, 3, 4, 3, 2, 1]
    edge = m.replication_counts("edge", 7, 16)
    assert sum(edge) == 16
    assert edge[0] > edge[3]
    assert edge[-1] > edge[3]
    for source in range(1, 60):
        for target in range(source, source + 61):
            for mode in ("uniform", "triangular", "edge"):
                counts = m.replication_counts(mode, source, target)
                assert len(counts) == source
                assert sum(counts) == target
                assert min(counts) >= 1


def test_position_variants_keep_identical_token_multiset_and_length():
    tokenizer = FakeTokenizer()
    variants = [
        m.build_prompt_variant(
            tokenizer=tokenizer,
            gold_word="blue",
            position=position,
            source_tokens=960,
        )
        for position in m.DEFAULT_POSITIONS
    ]
    assert all(len(variant.prompt_ids) == 960 for variant in variants)
    canonical_multiset = sorted(variants[0].prompt_ids)
    assert all(sorted(variant.prompt_ids) == canonical_multiset for variant in variants[1:])
    for expected, variant in zip(m.DEFAULT_POSITIONS, variants):
        assert variant.actual_position == pytest.approx(expected, abs=m.POSITION_EPSILON)


def test_triangular_changes_fact_allocation_by_position_while_uniform_does_not():
    tokenizer = FakeTokenizer()
    edge_variant = m.build_prompt_variant(
        tokenizer=tokenizer, gold_word="blue", position=0.05, source_tokens=960
    )
    middle_variant = m.build_prompt_variant(
        tokenizer=tokenizer, gold_word="blue", position=0.50, source_tokens=960
    )
    uniform = m.replication_counts("uniform", 960, 1920)
    triangular = m.replication_counts("triangular", 960, 1920)
    edge_uniform = uniform[edge_variant.fact_start:edge_variant.fact_end]
    middle_uniform = uniform[middle_variant.fact_start:middle_variant.fact_end]
    edge_tri = triangular[edge_variant.fact_start:edge_variant.fact_end]
    middle_tri = triangular[middle_variant.fact_start:middle_variant.fact_end]
    assert sum(edge_uniform) / len(edge_uniform) == pytest.approx(
        sum(middle_uniform) / len(middle_uniform), abs=1e-12
    )
    assert sum(middle_tri) / len(middle_tri) > sum(edge_tri) / len(edge_tri)


def _write_checkpoint(path: Path, *, cycle: int, depth: int, loss: float, accuracy: float):
    path.mkdir(parents=True)
    (path / "head.safetensors").write_bytes(f"head-{cycle}-{depth}".encode())
    (path / "tinystories.safetensors").write_bytes(f"tiny-{cycle}-{depth}".encode())
    (path / "meta.json").write_text(
        json.dumps({
            "cycle": cycle,
            "reuse_depth": depth,
            "cycle_complete": True,
            "metrics": {"predev": {"overall": {"mean_loss": loss, "accuracy": accuracy}}},
        }),
        encoding="utf-8",
    )


def test_checkpoint_is_physically_frozen_across_later_training(tmp_path: Path):
    run = tmp_path / "run"
    first = run / "checkpoints" / "cycle-000001-reuse-001"
    _write_checkpoint(first, cycle=1, depth=1, loss=0.8, accuracy=0.5)
    frozen1, lock1 = m.freeze_checkpoint(run_dir=run, explicit_checkpoint=None, reset=False)
    assert Path(lock1["source_checkpoint"]) == first.resolve()
    assert frozen1 != first.resolve()
    first_head = (frozen1 / "head.safetensors").read_bytes()

    second = run / "checkpoints" / "cycle-000002-reuse-001"
    _write_checkpoint(second, cycle=2, depth=1, loss=0.4, accuracy=0.7)
    frozen2, lock2 = m.freeze_checkpoint(run_dir=run, explicit_checkpoint=None, reset=False)
    assert frozen2 == frozen1
    assert lock2["source_checkpoint"] == lock1["source_checkpoint"]
    assert (frozen2 / "head.safetensors").read_bytes() == first_head

    frozen3, lock3 = m.freeze_checkpoint(run_dir=run, explicit_checkpoint=None, reset=True)
    assert Path(lock3["source_checkpoint"]) == second.resolve()
    assert (frozen3 / "head.safetensors").read_bytes() != first_head


def test_curve_diagnostics_reports_middle_penalty():
    matrix = {
        "uniform": {
            "0.05": {"accuracy": 0.8, "mean_loss": 0.2, "mean_gold_probability": 0.8},
            "0.25": {"accuracy": 0.7, "mean_loss": 0.3, "mean_gold_probability": 0.7},
            "0.50": {"accuracy": 0.5, "mean_loss": 0.8, "mean_gold_probability": 0.5},
            "0.75": {"accuracy": 0.7, "mean_loss": 0.3, "mean_gold_probability": 0.7},
            "0.95": {"accuracy": 0.8, "mean_loss": 0.2, "mean_gold_probability": 0.8},
        }
    }
    result = m.curve_diagnostics(matrix, loss_key="mean_loss")["uniform"]
    assert result["middle_minus_edge_loss"] == pytest.approx(0.6)
    assert result["middle_minus_edge_accuracy"] == pytest.approx(-0.3)
    assert result["middle_minus_edge_gold_probability"] == pytest.approx(-0.3)


def test_self_test_and_defaults_define_decisive_probe_contract():
    result = m.self_test()
    assert result["ok"] is True
    assert result["schema_version"] == m.SCHEMA
    assert result["default_items"] == 256
    assert result["default_positions"] == [0.05, 0.25, 0.5, 0.75, 0.95]
    assert result["default_modes"] == ["none", "uniform", "triangular", "edge"]
