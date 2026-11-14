"""Regression tests for the champion -> natural-language ROM consensus-only fork."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_natural_rom_consensus_train.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nlrom_consensus_isolated_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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


def consensus_source(gold_name="c", question_id="source"):
    codes = {"a": "result=2+3", "b": "result = 2 + 3", "c": "result=2+4"}
    labels = {
        "a": {"ab": "different", "ac": "different", "bc": "same"},
        "b": {"ab": "different", "ac": "same", "bc": "different"},
        "c": {"ab": "same", "ac": "different", "bc": "different"},
        "none": {"ab": "same", "ac": "same", "bc": "same"},
    }
    candidates = []
    for cid in ("a", "b", "c", "none"):
        paths = []
        for pair in ("ab", "ac", "bc"):
            x,y=pair
            for left,right in ((x,y),(y,x)):
                prompt = (
                    "Language: Python\nProgram A:\n```python\n" + codes[left] +
                    "\n```\nProgram B:\n```python\n" + codes[right] +
                    "\n```\nTask: Determine whether both programs have exactly the same normalized Python AST.\nAnswer:"
                )
                paths.append(PathRow(prompt, " " + labels[cid][pair].upper()))
        candidates.append(Candidate(cid, paths))
    return Question(question_id, "consensus", candidates, {"a":0,"b":1,"c":2,"none":3}[gold_name])


def test_selftest_and_cutover_contract():
    m = load_module()
    assert m.self_test()["ok"]
    assert m.DEFAULT_PARENT_RELEASE == "clef-cycle-000136-reuse-002"
    assert m.DEFAULT_PARENT_REVISION == "222a8e361121a5aa35e0ecd0e073d5f645ef54f9"
    assert m.TRAINER_VARIANT != "three-backbone-clef-tinystories-additive-rom-mix-v1"
    assert m.champion.FROZEN_LABELS == ("qwen", "pythia", "tinystories")
    assert not m._is_pairwise_natural_consensus(consensus_source())


@pytest.mark.parametrize("count", [4, 48, 160, 480, 512])
def test_only_consensus_and_no_triad(count):
    m=load_module()
    plan=m.consensus_only_plan(count)
    assert list(plan) == list(m.base.TASKS)
    assert plan["consensus"] == count
    assert sum(plan.values()) == count
    assert all(v==0 for k,v in plan.items() if k!="consensus")


@pytest.mark.parametrize("count", [0,1,2,3,5,6,479,-4])
def test_native_unit_guard(count):
    m=load_module()
    with pytest.raises(ValueError,match="positive multiple"):
        m.consensus_only_plan(count)


@pytest.mark.parametrize("topology,expected", [
    ("a",("different","different","same")),
    ("b",("different","same","different")),
    ("c",("same","different","different")),
    ("none",("same","same","same")),
])
def test_natural_rom_all_golds(topology,expected):
    m=load_module()
    q=consensus_source(topology, question_id="topology-"+topology)
    converted=m.build_natural_rom_consensus(q)
    pairs=m.pair_builder(converted)
    assert converted.task=="consensus"
    assert converted.gold_index==q.gold_index
    assert len(converted.candidates)==4
    assert all(len(c.paths)==1 for c in converted.candidates)
    direct=converted.candidates[0].paths[0].prompt
    assert "Begin a consensus comparison." in direct
    assert "record SAME or DIFFERENT as ab_relation" in direct
    assert "ROM/CONSENSUS/1" not in direct
    assert "result=2+4" in direct
    assert [name for name,_ in pairs] == ["ab","ac","bc"]
    assert tuple(p.candidates[p.gold_index].candidate_id for _,p in pairs)==expected
    assert m.champion.compose_consensus_relations(*expected) == topology
    for pair_name,p in pairs:
        assert p.gold_index in (0,1)
        assert len(p.candidates)==2
        assert all(len(candidate.paths)==2 for candidate in p.candidates)
        for ca,cb in zip(p.candidates[0].paths,p.candidates[1].paths):
            assert ca.prompt==cb.prompt
            assert "Begin a consensus relationship comparison." in ca.prompt
            assert "ROM/RELATION/1" not in ca.prompt
            assert "Return exactly SAME or DIFFERENT" in ca.prompt
            assert f"{pair_name}_relation" in ca.prompt
        assert p.candidates[0].paths[0].prompt != p.candidates[0].paths[1].prompt


def test_rendering_excludes_gold_and_preserves_candidate_labels():
    m=load_module()
    q=consensus_source("c")
    converted=m.build_natural_rom_consensus(q)
    assert [c.candidate_id for c in converted.candidates] == [c.candidate_id for c in q.candidates]
    assert [c.paths[0].answer.strip() for c in converted.candidates] == ["A","B","C","NONE"]
    assert len(set(c.paths[0].prompt for c in converted.candidates))==1
    pairs=m.pair_builder(converted)
    for _,p in pairs:
        assert set(c.candidate_id for c in p.candidates)=={"same","different"}
        assert not any("gold" in r.prompt.lower() for c in p.candidates for r in c.paths)


def test_conversion_all_task_splits_and_no_additive_rom():
    m=load_module()
    sources=[consensus_source("c",f"source-{idx}") for idx in range(4)]
    for role in ("predev","train","dev"):
        questions,manifest=m.build_additive_rom_mix(sources,percent=0.0,seed=34,cycle=137,role=role)
        assert len(questions)==len(sources)
        assert manifest["natural_count"]==0
        assert manifest["raw_rom_count"]==0
        assert manifest["rom_by_task"]=={"consensus":4}
        assert all(m.is_rom_question(q) and q.task=="consensus" for q in questions)
        assert all(len(m.pair_builder(q))==3 for q in questions)
    with pytest.raises(RuntimeError,match="forbids additive"):
        m.build_additive_rom_mix(sources,percent=25.0,seed=1,cycle=137,role="train")
    with pytest.raises(RuntimeError,match="non-consensus"):
        m.build_additive_rom_mix([SimpleNamespace(task="triad")],percent=0.0,seed=1,cycle=137,role="train")


def test_fail_closed_missing_pair_cache():
    m=load_module()
    q=Question("never-seen::natural-rom", "consensus", (), 0)
    with pytest.raises(RuntimeError,match="missing supervised"):
        m.pair_builder(q)


def test_cli_dry_run_no_other_training_and_parent_pinned():
    m=load_module()
    args=m.parse_args(["--max-cycles","1"])
    summary=m.dry_run_contract(args)
    assert summary["train_questions_per_reuse"]==480
    assert summary["predev_questions"]==512
    assert summary["separate_triad_training"] is False
    assert summary["other_task_training"] is False
    assert summary["raw_rom_training"] is False
    assert summary["start_cycle_after_parent"]==137
    assert args.routing_supervision_weight==0.0
    assert args.field_supervision_weight==0.25
    assert args.rom_additive_percent==0.0
    assert args.train_english_code_percent==0.0
    with pytest.raises(SystemExit):
        m.parse_args(["--rom-additive-percent","25"])
