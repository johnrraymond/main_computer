from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRAIN_TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_train.py"
CUTOVER_TOOL = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_cutover.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load("clef_tinystories_train_test_target", TRAIN_TOOL)
c = load("clef_tinystories_cutover_test_target", CUTOVER_TOOL)


def test_defaults_keep_32_reuse_and_lower_tinystories_lr():
    assert m.DEFAULT_EPOCHS_PER_CYCLE == 32
    assert m.DEFAULT_HEAD_LR == pytest.approx(1e-4)
    assert m.DEFAULT_TINYSTORIES_LR == pytest.approx(1e-5)
    assert m.DEFAULT_TINYSTORIES_PATH_BATCH == 1
    assert m.FROZEN_LABELS == ("qwen", "pythia")
    assert m.TRAINABLE_LABEL == "tinystories"


def test_configure_backbone_trainability_unfreezes_only_tinystories():
    import torch

    bundles = {
        label: SimpleNamespace(lm=torch.nn.Sequential(torch.nn.Linear(3, 3), torch.nn.Dropout(0.5)))
        for label in ("qwen", "pythia", "tinystories")
    }
    counts = m.configure_backbone_trainability(bundles)
    assert counts["qwen"] == 0
    assert counts["pythia"] == 0
    assert counts["tinystories"] > 0
    assert not any(p.requires_grad for p in bundles["qwen"].lm.parameters())
    assert not any(p.requires_grad for p in bundles["pythia"].lm.parameters())
    assert all(p.requires_grad for p in bundles["tinystories"].lm.parameters())
    assert bundles["tinystories"].lm.training is False


def test_joint_optimizer_has_separate_head_and_tinystories_learning_rates():
    import torch

    head = torch.nn.Linear(4, 2)
    tiny = torch.nn.Linear(4, 4)
    args = SimpleNamespace(head_lr=1e-4, tinystories_lr=1e-5, weight_decay=0.01)
    optimizer = m.build_optimizer(torch=torch, head=head, tinystories_lm=tiny, args=args)
    summary = m.optimizer_group_summary(optimizer)
    assert [row["group_name"] for row in summary] == ["clef_head", "tinystories"]
    assert summary[0]["lr"] == pytest.approx(1e-4)
    assert summary[1]["lr"] == pytest.approx(1e-5)
    assert summary[0]["parameters"] == sum(p.numel() for p in head.parameters())
    assert summary[1]["parameters"] == sum(p.numel() for p in tiny.parameters())


def test_trainable_bundle_evidence_preserves_backprop_through_hidden_and_logp():
    import torch

    class Tokenizer:
        pad_token_id = 0

        def encode(self, text, add_special_tokens=False):
            stripped = text.strip()
            if stripped == "SAME":
                return [3]
            if stripped == "DIFFERENT":
                return [4]
            return [1, 2]

    class Backbone(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = torch.nn.Embedding(8, 4)

        def forward(self, *, input_ids, attention_mask, use_cache, return_dict):
            return SimpleNamespace(last_hidden_state=self.embedding(input_ids))

    @dataclass(frozen=True)
    class PathRow:
        prompt: str
        answer: str

    @dataclass(frozen=True)
    class Candidate:
        candidate_id: str
        paths: tuple[PathRow, ...]

    @dataclass(frozen=True)
    class Question:
        question_id: str
        task: str
        candidates: tuple[Candidate, ...]
        gold_index: int

    backbone = Backbone()
    output_weight = torch.nn.Parameter(torch.randn(8, 4))
    bundle = m.smoke.BackboneBundle(
        label="tinystories",
        model_name="dummy",
        lm=backbone,
        backbone=backbone,
        tokenizer=Tokenizer(),
        output_weight=output_weight,
        hidden_size=4,
        max_positions=32,
    )
    question = Question(
        "q1",
        "ast",
        (
            Candidate("same", (PathRow("Prompt", " SAME"),)),
            Candidate("different", (PathRow("Prompt", " DIFFERENT"),)),
        ),
        0,
    )

    evidence = m.smoke.extract_bundle_evidence(
        torch=torch,
        bundle=bundle,
        question=question,
        path_batch=2,
        max_prompt_tokens=16,
        max_answer_tokens=8,
        prompt_evidence_tokens=2,
        answer_evidence_tokens=1,
        track_grad=True,
    )
    loss = (
        evidence["memory"].sum()
        + evidence["option_predictor"].sum()
        + evidence["option_logp"].sum()
        + evidence["option_lexical"].sum()
    )
    loss.backward()
    assert backbone.embedding.weight.grad is not None
    assert float(backbone.embedding.weight.grad.abs().sum()) > 0.0
    assert output_weight.grad is not None
    assert float(output_weight.grad.abs().sum()) > 0.0

    backbone.zero_grad(set_to_none=True)
    output_weight.grad = None
    frozen = m.smoke.extract_bundle_evidence(
        torch=torch,
        bundle=bundle,
        question=question,
        path_batch=2,
        max_prompt_tokens=16,
        max_answer_tokens=8,
        prompt_evidence_tokens=2,
        answer_evidence_tokens=1,
        track_grad=False,
    )
    assert not frozen["memory"].requires_grad
    assert not frozen["option_logp"].requires_grad
    assert not frozen["option_lexical"].requires_grad


def test_cutover_manifest_explicitly_resets_optimizer_and_unfreezes_tinystories(tmp_path: Path):
    source_experiment = tmp_path / "source"
    source_checkpoint = source_experiment / "checkpoints" / "cycle-000007"
    source_checkpoint.mkdir(parents=True)
    manifest = c.build_manifest(
        source_experiment=source_experiment,
        source_checkpoint=source_checkpoint,
        source_experiment_meta={
            "schema_version": c.base.SCHEMA,
            "source_experiment": str(tmp_path / "question-source"),
        },
        checkpoint_meta={
            "cycle": 7,
            "global_step": 8960,
            "head_parameters": c.smoke.production_head_parameter_count(),
            "metrics": {"selection": {"overall": {"mean_loss": 0.5, "accuracy": 0.75}}},
        },
        head_sha256="abc",
    )
    assert manifest["optimizer_reset"] is True
    assert manifest["rng_reset"] is True
    assert manifest["model_transition"]["qwen"] == "frozen"
    assert manifest["model_transition"]["pythia"] == "frozen"
    assert manifest["model_transition"]["tinystories"] == "frozen->trainable-from-pristine-base"
    assert manifest["source_selection"] == {"loss": 0.5, "accuracy": 0.75}


def test_joint_checkpoint_roundtrip_saves_head_and_tinystories(tmp_path: Path):
    import torch

    head = torch.nn.Linear(3, 2)
    tiny = torch.nn.Linear(3, 3)
    args = SimpleNamespace(head_lr=1e-4, tinystories_lr=1e-5, weight_decay=0.01)
    optimizer = m.build_optimizer(torch=torch, head=head, tinystories_lm=tiny, args=args)

    class Logger:
        def emit(self, event, **fields):
            pass

    original_head = {k: v.detach().clone() for k, v in head.state_dict().items()}
    original_tiny = {k: v.detach().clone() for k, v in tiny.state_dict().items()}
    checkpoint = m.save_checkpoint(
        torch=torch,
        output_dir=tmp_path,
        head=head,
        tinystories_lm=tiny,
        optimizer=optimizer,
        cycle=1,
        global_step=10,
        experiment_meta={
            "head_parameters": sum(p.numel() for p in head.parameters()),
            "tinystories_parameters": sum(p.numel() for p in tiny.parameters()),
            "trainable_parameters": sum(p.numel() for p in head.parameters()) + sum(p.numel() for p in tiny.parameters()),
            "cutover_dir": "cutover",
            "source_experiment": "source",
            "train_plan": {"legacy": 1},
            "dev_plan": {"legacy": 1},
        },
        cycle_metrics={},
        logger=Logger(),
    )
    assert (checkpoint / "head.safetensors").is_file()
    assert (checkpoint / "tinystories.safetensors").is_file()
    with torch.no_grad():
        for p in head.parameters():
            p.add_(10)
        for p in tiny.parameters():
            p.sub_(10)
    m.load_checkpoint(
        torch=torch,
        head=head,
        tinystories_lm=tiny,
        optimizer=optimizer,
        checkpoint=checkpoint,
    )
    for key, value in head.state_dict().items():
        assert torch.equal(value, original_head[key])
    for key, value in tiny.state_dict().items():
        assert torch.equal(value, original_tiny[key])


def test_self_test_describes_intended_stage():
    result = m.self_test()
    assert result["schema_version"] == m.SCHEMA
    assert result["reuse_epochs"] == 32
    assert result["frozen_backbones"] == ["qwen", "pythia"]
    assert result["trainable_backbone"] == "tinystories"


def test_trainable_tinystories_uses_memory_safe_path_batch(monkeypatch):
    captured = []

    def fake_extract_bundle_evidence(**kwargs):
        captured.append((kwargs["bundle"].label, kwargs["path_batch"], kwargs["track_grad"]))
        return {
            "memory": "m",
            "option_context": "c",
            "option_predictor": "p",
            "option_terminal": "t",
            "option_question": "q",
            "option_lexical": "l",
            "option_logp": "lp",
            "global": "g",
            "path_count": 2,
            "unique_path_count": 2,
            "memory_tokens": 4,
        }

    monkeypatch.setattr(m.smoke, "extract_bundle_evidence", fake_extract_bundle_evidence)
    args = SimpleNamespace(
        path_batch=4,
        tinystories_path_batch=1,
        max_prompt_tokens=768,
        max_answer_tokens=128,
        prompt_evidence_tokens=8,
        answer_evidence_tokens=8,
    )
    m.extract_one_bundle(
        torch=object(), bundle=SimpleNamespace(label="tinystories"), question=object(),
        args=args, track_grad=True,
    )
    m.extract_one_bundle(
        torch=object(), bundle=SimpleNamespace(label="qwen"), question=object(),
        args=args, track_grad=False,
    )
    assert captured == [("tinystories", 1, True), ("qwen", 4, False)]


def test_event_log_suppresses_spam_on_console_but_keeps_jsonl(tmp_path: Path, capsys):
    logger = m.EventLog(tmp_path)
    logger.emit("clef_tinystories_live_trainable_evidence", question_id="q1")
    logger.emit("clef_tinystories_train_question_complete", question_id="q1")
    logger.emit("clef_tinystories_optimizer_step", global_step=1)
    logger.emit("clef_tinystories_train_epoch_complete", cycle=1, epoch=1)
    console = capsys.readouterr().out
    assert "live_trainable_evidence" not in console
    assert "train_question_complete" not in console
    assert "optimizer_step" not in console
    assert "train_epoch_complete" in console
    rows = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 4
    assert "live_trainable_evidence" in rows[0]
    assert "train_question_complete" in rows[1]
    assert "optimizer_step" in rows[2]
    assert "train_epoch_complete" in rows[3]


def test_event_log_verbose_console_restores_detailed_events(tmp_path: Path, capsys):
    logger = m.EventLog(tmp_path, verbose_console=True)
    logger.emit("clef_tinystories_live_trainable_evidence", question_id="q1")
    assert "live_trainable_evidence" in capsys.readouterr().out


def test_parse_args_defaults_to_quiet_console_and_supports_verbose_flag():
    quiet = m.parse_args(["--self-test"])
    assert quiet.verbose_events is False
    verbose = m.parse_args(["--self-test", "--verbose-events"])
    assert verbose.verbose_events is True


def test_reuse_checkpoints_are_only_every_16_epochs():
    assert m.DEFAULT_REUSE_CHECKPOINT_INTERVAL == 16
    assert not m.reuse_checkpoint_due(1, 32)
    assert not m.reuse_checkpoint_due(8, 32)
    assert m.reuse_checkpoint_due(16, 32)
    assert not m.reuse_checkpoint_due(24, 32)
    assert m.reuse_checkpoint_due(32, 32)


def test_reuse_checkpoint_path_names_cycle_and_reuse_epoch(tmp_path: Path):
    assert m._checkpoint_dir(tmp_path, 3, 16).name == "cycle-000003-reuse-016"
    assert m._checkpoint_dir(tmp_path, 3, 32).name == "cycle-000003-reuse-032"
    assert m._checkpoint_dir(tmp_path, 0).name == "cycle-000000"


def test_epochs_per_cycle_must_end_on_16_epoch_checkpoint():
    args = m.parse_args(["--self-test", "--epochs-per-cycle", "32"])
    assert args.epochs_per_cycle == 32
    with pytest.raises(SystemExit):
        m.parse_args(["--self-test", "--epochs-per-cycle", "24"])


def test_self_test_reports_only_16_and_32_reuse_checkpoints():
    result = m.self_test()
    assert result["reuse_checkpoint_interval"] == 16
    assert result["reuse_checkpoint_epochs"] == [16, 32]
