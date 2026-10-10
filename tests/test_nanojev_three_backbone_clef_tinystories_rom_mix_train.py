from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SCRIPT = TOOLS / "nanojev_three_backbone_clef_tinystories_rom_mix_train.py"


def load_module():
    name = "test_nanojev_three_backbone_clef_tinystories_rom_mix_train_target"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_self_test_contract():
    module = load_module()
    result = module.self_test()
    assert result["ok"] is True
    assert all(result["checks"].values())
    assert result["default_parent_release"] == "clef-cycle-000136-reuse-002"


def test_dry_run_preserves_natural_population_and_adds_rom():
    module = load_module()
    args = module.parse_args(["--dry-run"])
    result = module.dry_run_contract(args)
    assert result["natural_train_questions"] == 480
    assert result["additive_rom_train_questions"] == 120
    assert result["mixed_train_questions_per_reuse"] == 600
    assert result["maximum_presentations_per_cycle"] == 2400
    assert result["max_reuse_depth"] == 4
    assert result["selection_policy"] == module.champion.CHAMPION_SELECTION_LOSS_FIRST
    assert result["mature_head_lr"] == 1e-6
    assert result["residual_head_lr"] == 1e-4


def test_rom_is_additive_and_deterministic():
    module = load_module()

    class PathRow:
        def __init__(self, prompt, answer):
            self.prompt = prompt
            self.answer = answer

    class Candidate:
        def __init__(self, candidate_id, paths):
            self.candidate_id = candidate_id
            self.paths = tuple(paths)

    class Question:
        def __init__(self, question_id, task, candidates, gold_index, stratum=""):
            self.question_id = question_id
            self.task = task
            self.candidates = tuple(candidates)
            self.gold_index = gold_index
            self.stratum = stratum

    questions = [
        Question(
            f"q-{i}",
            "english_code",
            (
                Candidate("false", (PathRow("Is this English?\n\nhello world\n\nAnswer:", " false"),)),
                Candidate("true", (PathRow("Is this English?\n\nhello world\n\nAnswer:", " true"),)),
            ),
            1,
        )
        for i in range(20)
    ]
    a, ma = module.build_additive_rom_mix(
        questions, percent=25.0, seed=11, cycle=137, role="test"
    )
    b, mb = module.build_additive_rom_mix(
        questions, percent=25.0, seed=11, cycle=137, role="test"
    )
    assert ma == mb
    assert [q.question_id for q in a] == [q.question_id for q in b]
    assert sum(not module.is_rom_question(q) for q in a) == 20
    assert sum(module.is_rom_question(q) for q in a) == 5


def test_rom_consensus_never_enters_pairwise_path():
    module = load_module()

    class Question:
        def __init__(self, question_id):
            self.question_id = question_id
            self.task = "consensus"

    assert module._is_pairwise_natural_consensus(Question("natural")) is True
    assert module._is_pairwise_natural_consensus(Question("rom::rom")) is False


def test_cutover_records_backbone_source_from_question_factory(tmp_path):
    module = load_module()

    class Factory:
        source = {
            "qwen": {"model": "Qwen/Qwen3-0.6B", "revision": "qwen-pin"},
            "pythia": {"model": "EleutherAI/pythia-70m", "revision": "pythia-pin"},
            "tinystories": {"model": "roneneldan/TinyStories-33M", "revision": "tiny-pin"},
        }

    experiment = {"parent_hf": {"release_name": module.DEFAULT_PARENT_RELEASE}}
    path = tmp_path / "experiment.json"
    resolved = module._record_or_validate_backbone_source(
        factory=Factory(), experiment=experiment, experiment_path=path, resume=False
    )
    assert resolved == Factory.source
    assert experiment["source"] == Factory.source
    persisted = module.smoke.read_json(path)
    assert persisted["source"] == Factory.source


def test_resume_rejects_backbone_source_drift(tmp_path):
    module = load_module()

    class Factory:
        source = {"qwen": {"model": "Qwen/Qwen3-0.6B", "revision": "new"}}

    experiment = {"source": {"qwen": {"model": "Qwen/Qwen3-0.6B", "revision": "old"}}}
    import pytest
    with pytest.raises(RuntimeError, match="resume backbone source provenance mismatch"):
        module._record_or_validate_backbone_source(
            factory=Factory(), experiment=experiment,
            experiment_path=tmp_path / "experiment.json", resume=True,
        )


def test_parent_resolve_failure_skeleton_is_safe_to_retry(tmp_path):
    module = load_module()
    out = tmp_path / "run"
    for dirname in ("cycles", "checkpoints", "predev_champ"):
        (out / dirname).mkdir(parents=True, exist_ok=True)
    (out / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "progress.json").write_text("{}", encoding="utf-8")
    (out / "error.json").write_text("{}", encoding="utf-8")
    assert module._recover_parent_resolve_bootstrap(out) is True
    assert not out.exists()
