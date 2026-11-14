from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_initializer_helpers_are_deterministic():
    mod = load("nanojev_code_initialize_test", TOOLS / "nanojev_code_initialize.py")
    assert mod.sha256_json({"b": 2, "a": 1}) == mod.sha256_json({"a": 1, "b": 2})
    assert mod.SCHEMA == "main-computer-nanojev-code-experiment-v2"


def test_trainer_cycle_slice_wraps():
    mod = load("nanojev_code_train_test", TOOLS / "nanojev_code_train.py")
    assert mod.cyclic_slice([1, 2, 3], 2, 3) == [3, 1, 2]


def test_checkpoint_config_declares_frozen_backbone():
    mod = load("nanojev_code_train_config_test", TOOLS / "nanojev_code_train.py")
    experiment = {
        "model": "example/model",
        "requested_revision": "main",
        "resolved_model_revision": "abc123",
        "set_head": "attention",
        "max_length": 384,
        "initialization": "clean pretrained frozen backbone with new NanoJev decision head; no init checkpoint",
        "experiment_sha256": "deadbeef",
        "backbone_frozen": True,
    }
    train = {"head_lr": 2e-4}
    cfg = mod.make_checkpoint_config(experiment, train, cycle=4, global_step=87)
    assert cfg["schema_version"] == "openjev-decision-pipeline-v1"
    assert cfg["set_head"] == "attention"
    assert cfg["resolved_model_revision"] == "abc123"
    assert cfg["main_computer_global_step"] == 87
    assert cfg["backbone_frozen"] is True
    assert "backbone_lr" not in cfg


def test_trainer_never_allows_seen_to_exceed_available():
    mod = load("nanojev_code_train_seen_guard_test", TOOLS / "nanojev_code_train.py")
    assert mod.batch_fits_seen_budget(0, 256, 8) is True
    assert mod.batch_fits_seen_budget(248, 256, 8) is True
    assert mod.batch_fits_seen_budget(256, 256, 8) is False
    assert mod.batch_fits_seen_budget(250, 256, 8) is False
