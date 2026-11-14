from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'nanojev_three_backbone_clef_tinystories_rom_mix_v2_train.py'


def load_script():
    spec = importlib.util.spec_from_file_location('nanojev_rom_mix_v2_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_champion(v2):
    return v2.load_module('nanojev_rom_mix_v2_champion_under_test',
        ROOT / 'tools' / 'nanojev_three_backbone_clef_tinystories_structured_supervision_train.py')


def synthetic_consensus():
    class PathRow:
        def __init__(self, prompt, answer):
            self.prompt, self.answer = prompt, answer
    class Candidate:
        def __init__(self, candidate_id, paths):
            self.candidate_id, self.paths = candidate_id, tuple(paths)
    class Question:
        def __init__(self, question_id, task, candidates, gold_index, stratum=''):
            self.question_id = question_id
            self.task = task
            self.candidates = tuple(candidates)
            self.gold_index = gold_index
            self.stratum = stratum
    relations = {
        'none': ('same', 'same', 'same'),
        'a': ('different', 'different', 'same'),
        'b': ('different', 'same', 'different'),
        'c': ('same', 'different', 'different'),
    }
    candidates = []
    for cid in ('a','b','c','none'):
        paths = []
        for pair, relation in zip(('ab','ac','bc'),relations[cid]):
            for orientation in ('forward','reverse'):
                paths.append(PathRow(f'Compare {pair} ({orientation}); same or different?', relation))
        candidates.append(Candidate(cid, paths))
    return Question('source-consensus', 'consensus', candidates, 2)


def test_requested_curriculum_and_native_units():
    v2=load_script()
    champ=load_champion(v2)
    plan = v2.allocation(480, units=champ.base.TASK_UNITS, tasks=champ.base.TASKS)
    assert sum(plan.values()) == 480
    assert v2.CURRICULUM == {
        'legacy':5.0,'mutation':10.0,'ast':5.0,'consensus':59.0,
        'triad':15.0,'dictionary_definition':5.0,'english_code':1.0,
    }
    champ.base.validate_plan(plan, expected_total=480)
    assert 272 <= plan['consensus'] <= 292
    assert plan['english_code'] in (4,8)
    assert 20 <= plan['dictionary_definition'] <= 30


def test_rom_pair_questions_use_actual_champion_relation_golds():
    v2=load_script()
    champ=load_champion(v2)
    natural=synthetic_consensus()
    source_pairs=champ.build_consensus_pairwise_questions(natural)
    actual=[]
    for pair, question in source_pairs:
        converted=v2.convert_pair_question(champ,pair,question,rom_parent_id='source-consensus::rom')
        assert converted.task == champ.CONSENSUS_PAIRWISE_TASK
        assert converted.question_id.endswith('::pair-'+pair)
        assert len(converted.candidates)==2
        assert all(len(c.paths)==2 for c in converted.candidates)
        for left,right in zip(converted.candidates[0].paths, converted.candidates[1].paths):
            assert left.prompt == right.prompt  # no candidate-label leakage
            assert 'RELATE '+pair[0].upper()+' '+pair[1].upper() in left.prompt
            assert 'BIND observation' in left.prompt
        actual.append(converted.candidates[converted.gold_index].candidate_id)
    assert actual == ['same', 'different', 'different']  # Gold topology = C
    assert champ.compose_consensus_relations(*actual) == 'c'


def test_installer_wires_both_consensus_encodings_and_lineage():
    v2=load_script()
    champ=load_champion(v2)
    natural=synthetic_consensus()
    def fake_romify(question):
        return champ._clone_question(
            question,
            question_id=question.question_id+'::rom',task='consensus',
            candidates=question.candidates,gold_index=question.gold_index,
        )
    fake=SimpleNamespace(
        champion=champ,rom=SimpleNamespace(),romify_for_mix=fake_romify,
        is_rom_question=lambda q:q.question_id.endswith('::rom'),
        _is_pairwise_natural_consensus=lambda q: q.task=='consensus' and not q.question_id.endswith('::rom'),
        build_mixed_frozen_training_cache=object(), evaluate_mixed_population=object(),
        train_mixed_population=object(), run=lambda *_:None, main=lambda _:None,
        TRAINER_VARIANT='old', DEFAULT_OUTPUT=Path('/tmp/old'),ROM_MIX_SCHEMA='old',
        DEFAULT_PARENT_RELEASE='clef-cycle-000136-reuse-002',
    )
    v2.install_cutover_adapter(fake)
    rom_question=fake.romify_for_mix(natural)
    assert fake._is_pairwise_natural_consensus(rom_question)
    rom_pairs=champ.build_consensus_pairwise_questions(rom_question)
    assert len(rom_pairs)==3
    assert all('ROM/RELATION/1' in pq.candidates[0].paths[0].prompt for _,pq in rom_pairs)
    natural_pairs=champ.build_consensus_pairwise_questions(natural)
    assert len(natural_pairs)==3
    assert all('ROM/RELATION/1' not in pq.candidates[0].paths[0].prompt for _,pq in natural_pairs)
    assert fake.ROM_MIX_SCHEMA == v2.SCHEME
    assert 'rom_mix_v2' in str(fake.DEFAULT_OUTPUT)
    assert champ.training_task_weights(1.0)==v2.CURRICULUM
    assert sum(champ.training_curriculum_plan(480,english_code_percent=1.0).values())==480
    assert v2.self_test(fake)['ok']


def test_missing_consensus_subdecisions_must_fail_loudly():
    v2=load_script()
    with pytest.raises(RuntimeError,match='do not fall back to direct-only'):
        v2.pair_registry_get('unknown-consensus::rom')
