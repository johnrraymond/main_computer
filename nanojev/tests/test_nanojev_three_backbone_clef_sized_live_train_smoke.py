from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_sized_live_train_smoke.py"
SPEC = importlib.util.spec_from_file_location("clef_sized_live_smoke_test_target", TOOL)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


def test_production_parameter_count_is_clef_sized():
    count = m.production_head_parameter_count()
    assert 118_000_000 <= count <= 124_000_000
    assert 0.96 <= count / m.CLEF_RELEASED_HEAD_PARAMS <= 1.04


def test_balanced_evidence_selection_keeps_both_ends():
    assert m.balanced_indices(0, 10, 4) == [0, 1, 8, 9]
    assert m.balanced_truncate(list(range(10)), 5) == [0, 1, 2, 8, 9]


def test_tiny_three_backbone_head_learns():
    Head = m.build_head_class()
    torch.manual_seed(13)
    head = Head(
        {"qwen": 16, "pythia": 12, "tinystories": 8},
        width=16,
        routing_layers=1,
        layers=1,
        heads=4,
        feedforward=32,
        fusion_feedforward=24,
    )
    evidence = {}
    for label, hidden in (("qwen", 16), ("pythia", 12), ("tinystories", 8)):
        evidence[label] = {
            "memory": torch.randn(9, hidden),
            "option_context": torch.randn(2, hidden),
            "option_question": torch.randn(2, hidden),
            "option_lexical": torch.randn(2, hidden),
            "global": torch.randn(hidden),
        }
    optimizer = torch.optim.AdamW(head.parameters(), lr=1e-2)
    before = m.sampled_parameter_signature(head.backbone_modules["qwen"].memory_projection.weight, 64)
    losses = []
    for _ in range(10):
        optimizer.zero_grad(set_to_none=True)
        logits = head(evidence)
        loss, _, _ = m.loss_parts(torch, logits, 0)
        losses.append(float(loss.item()))
        loss.backward()
        optimizer.step()
    after = m.sampled_parameter_signature(head.backbone_modules["qwen"].memory_projection.weight, 64)
    assert losses[-1] < losses[0]
    assert not torch.equal(before, after)


def test_freeze_module_blocks_gradient_updates():
    layer = torch.nn.Linear(5, 3)
    count = m.freeze_module(layer)
    assert count == sum(p.numel() for p in layer.parameters())
    assert not any(p.requires_grad for p in layer.parameters())
    assert not layer.training


def test_prepare_output_reuses_only_own_early_diagnostics(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    for name in ("events.jsonl", "progress.json", "error.json"):
        (out / name).write_text("x", encoding="utf-8")
    resolved = m.prepare_output(out)
    assert resolved == out.resolve()
    assert list(out.iterdir()) == []


def test_prepare_output_rejects_unknown_or_partial_artifacts(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "result.partial.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="prior artifacts"):
        m.prepare_output(out)


def test_error_report_preserves_traceback_and_artifacts(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "questions.json").write_text("{}", encoding="utf-8")
    logger = m.EventLog(out)
    logger.set_stage("forced_failure", completed=3)
    try:
        raise RuntimeError("forced smoke failure")
    except RuntimeError as exc:
        m.write_error_report(out, logger, exc)
    payload = m.read_json(out / "error.json")
    assert payload["stage"] == "forced_failure"
    assert payload["exception_type"] == "RuntimeError"
    assert "forced smoke failure" in payload["traceback"]
    assert "questions.json" in payload["artifacts"]


def test_self_test_passes():
    result = m.self_test()
    assert result["event"] == "clef_sized_smoke_self_test_passed"
    assert result["tiny_final_loss"] < result["tiny_initial_loss"]


def test_help_and_architecture_only_exit_zero(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        m.parse_args(["--help"])
    assert exc.value.code == 0
    assert m.main(["--architecture-only"]) == 0
    out = capsys.readouterr().out
    assert "clef_sized_architecture" in out

def test_live_evidence_recomputes_every_call(monkeypatch, tmp_path):
    calls = []
    def fake_extract_bundle_evidence(**kwargs):
        bundle = kwargs["bundle"]
        calls.append(bundle.label)
        hidden = bundle.hidden_size
        return {
            "memory": torch.zeros(3, hidden),
            "option_context": torch.zeros(2, hidden),
            "option_question": torch.zeros(2, hidden),
            "option_lexical": torch.zeros(2, hidden),
            "global": torch.zeros(hidden),
            "path_count": 1,
            "memory_tokens": 3,
        }
    monkeypatch.setattr(m, "extract_bundle_evidence", fake_extract_bundle_evidence)
    class B:
        def __init__(self, label, hidden):
            self.label = label
            self.hidden_size = hidden
    bundles = {"qwen": B("qwen", 16), "pythia": B("pythia", 12), "tinystories": B("tinystories", 8)}
    class Q:
        question_id = "q"
        task = "ast"
        candidates = (1, 2)
    class Args:
        path_batch = 1
        max_prompt_tokens = 8
        max_answer_tokens = 8
        prompt_evidence_tokens = 2
        answer_evidence_tokens = 2
    logger = m.EventLog(tmp_path)
    m.extract_live_evidence(torch=torch, bundles=bundles, question=Q(), args=Args(), logger=logger)
    m.extract_live_evidence(torch=torch, bundles=bundles, question=Q(), args=Args(), logger=logger)
    assert calls == ["qwen", "pythia", "tinystories", "qwen", "pythia", "tinystories"]


def test_large_parameter_signature_indices_use_exact_integer_arithmetic():
    # Regression for the real CUDA failure: float32 linspace cannot represent
    # every integer at Qwen-scale tensor sizes and can round numel-1 up to numel.
    numel = 596_049_920
    count = 2048
    old_float_indices = torch.linspace(0, numel - 1, steps=count, dtype=torch.float32).long()
    assert int(old_float_indices[-1]) == numel  # documents the former OOB bug

    indices = m.deterministic_sample_indices(numel, count)
    assert len(indices) == count
    assert indices[0] == 0
    assert indices[-1] == numel - 1
    assert min(indices) >= 0
    assert max(indices) < numel
    assert all(a < b for a, b in zip(indices, indices[1:]))


def test_parameter_signature_handles_large_logical_scale_without_float_indices():
    tensor = torch.arange(100_003, dtype=torch.float32)
    sample = m.sampled_parameter_signature(tensor, 2048)
    indices = m.deterministic_sample_indices(tensor.numel(), 2048)
    assert sample.shape == (2048,)
    assert float(sample[0]) == 0.0
    assert float(sample[-1]) == float(tensor[-1])
    assert torch.equal(sample, tensor[torch.tensor(indices, dtype=torch.long)])

def test_cli_failure_writes_error_json(tmp_path):
    out = tmp_path / "cli-failure"
    code = m.main([
        "--experiment-dir", str(tmp_path / "does-not-exist"),
        "--output-dir", str(out),
        "--no-clef-shared-init",
    ])
    assert code == 1
    payload = m.read_json(out / "error.json")
    assert payload["event"] == "clef_sized_smoke_failed"
    assert payload["stage"] == "question_generation"
    assert payload["traceback"]
    assert (out / "events.jsonl").is_file()
    assert (out / "progress.json").is_file()
