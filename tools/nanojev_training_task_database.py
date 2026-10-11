#!/usr/bin/env python3
"""Precompute a sealed NanoJev training-task corpus without training any model.

Seven existing CLEF objective factories, actual three-way consensus relation
supervision, canonical ROM, controlled-English ROM, existing CJM utility corpus,
and optional captured captain smoke traces share one SQLite reserve/export layer.
No GPU, model evaluation, optimizer, or trainer modifications.
The --binary-consensus-only mode generates independent balanced SAME/DIFFERENT
questions from the verified consensus oracle, without any other task families.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Mapping

from nanojev_training_task_store import TrainingTaskStore, canonical, sha, SPLITS, SCHEMA as STORE_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / 'runtime' / 'nanojev_training_task_bank' / 'v1'
SCHEMA = 'nanojev.training-task-generator.v1'
DEFAULT_SOURCE_EXPERIMENT = Path(
    r'C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_broad_curriculum_v1')


def load_local(name: str, filename: str):
    path = TOOLS / filename
    if not path.is_file():
        raise RuntimeError(f'TASK_BANK_DEPENDENCY_MISSING: {path}')
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('TASK_BANK_DEPENDENCY_UNLOADABLE: ' + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def clone_question(question, *, question_id: str, candidates):
    kw = {'question_id':question_id,'task':str(question.task),
          'candidates':tuple(candidates),'gold_index':int(question.gold_index),
          'stratum':str(getattr(question,'stratum','') or '')}
    try:
        return question.__class__(**kw)
    except TypeError:
        kw.pop('stratum')
        return question.__class__(**kw)


def rename_question(question, suffix: str):
    return clone_question(question, question_id=str(question.question_id)+suffix,
                          candidates=question.candidates)


def _unambiguous(value: Any) -> str:
    if isinstance(value,str):
        return json.dumps(value, ensure_ascii=False)
    return canonical(value)


RELATIONS = {
    'NORMALIZED_AST_EQUIVALENCE':'whether their normalized Python syntax trees are equivalent',
    'BEHAVIORAL_EQUIVALENCE':'whether their program behaviors are equivalent',
    'EXACT_CONTINUATION':'which text is the exact continuation',
    'DICTIONARY_FORWARD_REVERSE_CONSISTENCY':'whether this definition matches the headword in both directions',
    'PART_OF_SPEECH_COMPATIBILITY':'whether this definition matches the indicated part of speech',
    'CLASSIFICATION_MATCH':'whether the text matches the requested classification',
}
VERIFICATIONS = {
    'UNIQUE_OUTLIER_OR_NONE': 'Check that exactly one candidate differs from the matching pair; otherwise select NONE.',
    'SAME_OR_DIFFERENT':'Check whether the answer is SAME or DIFFERENT.',
    'PRESERVING_OR_CHANGING':'Check whether the modification preserves or changes behavior.',
}


def natural_rom_text(payload: Mapping[str,Any]) -> str:
    """Deterministic controlled English for the existing *structured* ROM AST.

    Emit no oracle answer; never infer or fabricate semantics for unknown ops.
    The source ROM JSON remains separately stored for machine fidelity.
    """
    task = str(payload['task'])
    bindings = dict(payload['inputs'])
    lines = [f'Begin a {task.replace("_"," ")} reasoning task.']
    for step in payload['program']:
        op = step['op']
        if op == 'BEGIN' or op == 'END':
            continue
        if op == 'OPEN':
            lines.append(f'Open evidence object {step["object"]}.')
        elif op == 'BIND':
            field = str(step['input'])
            if field not in bindings:
                raise RuntimeError('NATURAL_ROM_BINDING_MISSING: '+field)
            lines.append(f'Bind evidence object {step["object"]} to {field}: {_unambiguous(bindings[field])}.')
        elif op == 'SEAL':
            lines.append(f'Finish recording evidence object {step["object"]}.')
        elif op == 'RELATE':
            rule = str(step['using'])
            if rule not in RELATIONS:
                # A generic mapping would silently change the semantics.
                raise RuntimeError('NATURAL_ROM_UNKNOWN_RELATION: '+rule)
            lines.append(f'Compare {step["left"]} with {step["right"]}; determine {RELATIONS[rule]}. '
                         f'Record the relationship as {step["into"]}.')
        elif op == 'OBJECTIVE':
            lines.append(f'The objective is to determine {step["target"]}.')
        elif op == 'RESOLVE':
            sources = step.get('from',[])
            lines.append(f'Resolve {step["output"]} using '+', '.join(map(str,sources))+'.')
        elif op == 'HOLD':
            lines.append(f'Retain the provisional value of {step["object"]}.')
        elif op == 'VERIFY':
            constraint = str(step['constraint'])
            lines.append(VERIFICATIONS.get(constraint, f'Verify the defined {constraint} constraint.'))
        elif op == 'COMMIT':
            lines.append(f'Commit the resolved value of {step["object"]}.')
        else:
            raise RuntimeError('NATURAL_ROM_UNKNOWN_OP: '+str(op))
    lines.append(f'Return only the value of {payload["output"]}.')
    lines.append('End the reasoning task.')
    return '\n'.join(lines) + '\nAnswer:'


def build_natural_rom_question(question, rom_module):
    fixed = rom_module.romify_question(question)
    prompt = fixed.candidates[0].paths[0].prompt
    if not prompt.endswith('\nROM_OUTPUT:'):
        raise RuntimeError('TASK_BANK_RAW_ROM_CONTRACT_CHANGED')
    payload = json.loads(prompt[:-len('\nROM_OUTPUT:')])
    natural_prompt = natural_rom_text(payload)
    _bindings, candidate_values = rom_module.rom_bindings_and_candidate_values(question)
    if len(candidate_values) != len(fixed.candidates):
        raise RuntimeError('TASK_BANK_NATURAL_ROM_VALUES_DRIFT')
    candidates = []
    for candidate, value in zip(fixed.candidates, candidate_values):
        # Natural-ROM uses human-readable target text. The raw-ROM JSON output
        # remains intact as a distinct, independently stored variant.
        paths = tuple(type(p)(natural_prompt,' '+str(value)) for p in candidate.paths)
        candidates.append(type(candidate)(candidate.candidate_id,paths))
    return clone_question(question,question_id=str(question.question_id)+'::natural-rom',
                          candidates=candidates)


def pairwise_rom_question(pair_question, pair: str, v2):
    candidates = []
    for candidate in pair_question.candidates:
        paths = tuple(type(p)(v2.rom_pair_prompt(p.prompt,pair),p.answer)
                      for p in candidate.paths)
        candidates.append(type(candidate)(candidate.candidate_id,paths))
    return clone_question(pair_question, question_id=str(pair_question.question_id)+'::rom',
                          candidates=candidates)


def pairwise_natural_rom_question(pair_question, pair: str):
    left, right = pair.upper()
    candidates = []
    for candidate in pair_question.candidates:
        paths = []
        for p in candidate.paths:
            prompt = (f'Begin a consensus relationship comparison.\n'
                      f'Open the following two-program observation:\n{p.prompt}\n'
                      f'This is the global {pair.upper()} relationship. In this observation, Program A is global {left} '
                      f'and Program B is global {right}. Compare these two programs using normalized Python '
                      f'abstract syntax trees; ignore comments and formatting.\n'
                      f'Record their relationship as {pair.lower()}_relation.\n'
                      'Decide whether this relationship is SAME or DIFFERENT.\n'
                      'Verify the result against the two programs.\n'
                      'Return exactly SAME or DIFFERENT.\nEnd the relationship comparison.\nAnswer:')
            paths.append(type(p)(prompt,p.answer))
        candidates.append(type(candidate)(candidate.candidate_id,tuple(paths)))
    return clone_question(pair_question,
                          question_id=str(pair_question.question_id)+'::natural-rom',
                          candidates=candidates)


def assert_question(q):
    if len(q.candidates)<2 or not 0<=int(q.gold_index)<len(q.candidates):
        raise RuntimeError('TASK_BANK_QUESTION_INVALID')
    ids = [str(c.candidate_id) for c in q.candidates]
    if len(ids)!=len(set(ids)) or not all(c.paths for c in q.candidates):
        raise RuntimeError('TASK_BANK_QUESTION_CANDIDATE_INVALID')
    if any(not isinstance(p.prompt,str) or not isinstance(p.answer,str)
           for c in q.candidates for p in c.paths):
        raise RuntimeError('TASK_BANK_PATH_INVALID')


def clef_variants(question, *, champion, rom, v2) -> dict:
    base = champion.base
    assert_question(question)
    raw = rename_question(rom.romify_question(question),'::rom')
    readable = build_natural_rom_question(question,rom)
    variants = {'natural':base.serialize_question(question),
                'rom':base.serialize_question(raw),
                'natural_rom':base.serialize_question(readable)}
    for variant in (raw,readable):
        assert_question(variant)
        if ([c.candidate_id for c in variant.candidates] !=
            [c.candidate_id for c in question.candidates] or
            int(variant.gold_index)!=int(question.gold_index)):
            raise RuntimeError('TASK_BANK_ROM_GOLD_DRIFT')
    if str(question.task)=='consensus':
        pairs = champion.build_consensus_pairwise_questions(question)
        if tuple(name for name,_ in pairs)!=('ab','ac','bc'):
            raise RuntimeError('TASK_BANK_CONSENSUS_PAIR_SET_INVALID')
        for pair,source in pairs:
            for variant, obj in (
                ('natural',source),
                ('rom',pairwise_rom_question(source,pair,v2)),
                ('natural_rom',pairwise_natural_rom_question(source,pair))):
                assert_question(obj)
                if int(obj.gold_index)!=int(source.gold_index):
                    raise RuntimeError('TASK_BANK_PAIRWISE_GOLD_DRIFT')
                variants[f'{variant}_pair_{pair}']=base.serialize_question(obj)
    return variants


def insert_clef(store, questions, *, cohort: str, split: str, cycle: int,
                champion, rom, v2, seen: set[str], factory, provenance: dict) -> dict:
    n=0
    by_task=Counter()
    for q in questions:
        fp=factory.question_fingerprint(q)
        if fp in seen:
            raise RuntimeError('TASK_BANK_CROSS_CYCLE_OR_SPLIT_LEAK: '+fp)
        seen.add(fp)
        variants=clef_variants(q,champion=champion,rom=rom,v2=v2)
        store.put(cohort_id=cohort,family='clef',task=str(q.task),split=split,
                  fingerprint=fp,oracle='existing-objective-gold-v1',label_status='verified',
                  target={'goldIndex':int(q.gold_index),
                          'goldCandidateId':str(q.candidates[q.gold_index].candidate_id)},
                  provenance={**provenance,'originalQuestionId':str(q.question_id),
                              'fingerprint':fp,'cycle':cycle},variants=variants)
        by_task[str(q.task)]+=1
        n+=1
    return {'groups':n,'tasks':dict(by_task)}


def generate_clef(store, *, source_experiment: Path, db_path: Path,
                  seed: int, cycles: int, train_count: int, dev_count: int,
                  predev_count: int, cycle_base: int) -> dict:
    champion=load_local('task_bank_champion','nanojev_three_backbone_clef_tinystories_structured_supervision_train.py')
    rom=load_local('task_bank_rom','nanojev_three_backbone_clef_rom_structured_supervision_train.py')
    v2=load_local('task_bank_v2','nanojev_three_backbone_clef_tinystories_rom_mix_v2_train.py')
    base=champion.base
    rom.validate_rom_templates()
    train_plan=v2.allocation(train_count,units=base.TASK_UNITS,tasks=base.TASKS)
    base.validate_plan(train_plan,expected_total=train_count)
    dev_plan=base.curriculum_plan(dev_count)
    predev_plan=base.curriculum_plan(predev_count)
    if cycles<1:
        raise ValueError('at least one cycle required')
    def logger_factory():
        class Logger:
            def emit(self,event,**fields):
                if event.endswith('retry') or event.endswith('repaired'):
                    print(canonical({'event':event,**fields}),flush=True)
        return Logger()
    logger=logger_factory()
    seen:set[str]=set()
    summary=[]
    with champion.EfficientQuestionFactory(source_experiment=source_experiment,
                     training_db=db_path,seed=seed,create_db=True,logger=logger) as factory:
        meta={'sourceExperiment':str(source_experiment),'seed':seed,
              'sourceModel':factory.source.get('model'),
              'sourceRevision':factory.source.get('revision'),
              'factory':'EfficientQuestionFactory',
              'romSchema':rom.ROM_SCHEMA,
              'romTemplateIds':{task:rom.rom_template_id(task) for task in rom.ROM_TEMPLATES}}
        for offset in range(cycles):
            cycle=cycle_base+offset
            predev,pre_fps,_,_=champion.select_fresh_selection_bank(
                factory=factory,plan=predev_plan,seed=seed,
                data_cycle_base=cycle+1_000_000,blocked_fingerprints=seen,logger=logger)
            train,dev,_=factory.generate(data_cycle=cycle+100,train_plan=train_plan,
                          dev_plan=dev_plan,seed=seed,
                          blocked_fingerprints=seen | pre_fps)
            # All populations generated before any inserts; the DB is never partially sealed.
            with store.conn:
                for split,questions in [('predev',predev),('train',train),('dev',dev)]:
                    cohort=f'clef:{cycle}:{split}'
                    store.add_cohort(cohort,family='clef',split=split,cycle=cycle,
                                     source={**meta,'population':split,'cycle':cycle})
                    result=insert_clef(store,questions,cohort=cohort,split=split,
                                       cycle=cycle,champion=champion,rom=rom,v2=v2,
                                       seen=seen,factory=factory,provenance=meta)
                    summary.append({'cohort':cohort,**result})
            print(canonical({'event':'task_bank_clef_cycle_generated','cycle':cycle,
                             'populations':summary[-3:]}),flush=True)
    return {'cycleCount':cycles,'populations':summary,
            'trainPlan':train_plan,'devPlan':dev_plan,'predevPlan':predev_plan}



def binary_consensus_natural_rom_prompt(prompt: str, pair: str, rom) -> str:
    """Use the exact controlled-English pair prompt from the NL-ROM trainer.

    This is intentionally an isolated, dependency-light renderer: importing the
    trainer here would install its global training hooks in the database builder.
    """
    if pair not in ('ab', 'ac', 'bc'):
        raise ValueError('TASK_BANK_UNKNOWN_CONSENSUS_PAIR')
    first, second = rom._program_pair(str(prompt))
    return (
        'Begin a consensus relationship comparison.\n'
        f'This comparison records the global {pair.upper()} relationship.\n'
        'The first program is:\n```python\n' + first + '\n```\n'
        'The second program is:\n```python\n' + second + '\n```\n'
        'Compare the two programs using normalized Python abstract syntax trees.\n'
        'Ignore comments and formatting-only differences.\n'
        f'Record whether the two programs are SAME or DIFFERENT as {pair}_relation.\n'
        'Verify the relationship against the two programs.\n'
        'Return exactly SAME or DIFFERENT.\n'
        'End the relationship comparison.\nAnswer:'
    )


def single_orientation_binary_question(source, *, pair: str, orientation: int,
                                       presentation: str, rom):
    """One labeled binary question, not a 4-way problem or 2-path aggregate."""
    if orientation not in (0, 1) or presentation not in ('natural', 'natural_rom'):
        raise ValueError('TASK_BANK_INVALID_BINARY_VARIANT')
    candidates = []
    for candidate in source.candidates:
        if len(candidate.paths) != 2:
            raise RuntimeError('TASK_BANK_BINARY_PAIR_ORIENTATIONS_MISSING')
        path = candidate.paths[orientation]
        prompt = (binary_consensus_natural_rom_prompt(path.prompt, pair, rom)
                  if presentation == 'natural_rom' else path.prompt)
        paths = (type(path)(prompt, path.answer),)
        candidates.append(type(candidate)(candidate.candidate_id, paths))
    suffix = 'reverse' if orientation else 'forward'
    result = clone_question(
        source, question_id=f'{source.question_id}::{presentation}-{suffix}',
        candidates=candidates)
    assert_question(result)
    if str(result.task) != 'consensus_pairwise' or len(result.candidates) != 2:
        raise RuntimeError('TASK_BANK_BINARY_QUESTION_INVALID')
    if [str(c.candidate_id).lower() for c in result.candidates] != ['same', 'different']:
        raise RuntimeError('TASK_BANK_BINARY_CANDIDATES_INVALID')
    if result.candidates[0].paths[0].prompt != result.candidates[1].paths[0].prompt:
        raise RuntimeError('TASK_BANK_BINARY_PAIR_PROMPT_DRIFT')
    if any(len(c.paths) != 1 for c in result.candidates):
        raise RuntimeError('TASK_BANK_BINARY_NOT_INDEPENDENT')
    return result


def binary_consensus_variants(source, *, pair: str, rom, base) -> dict[str, Any]:
    variants = {}
    for presentation in ('natural_rom', 'natural'):
        for orientation in (0, 1):
            name = presentation + ('_reverse' if orientation else '')
            q = single_orientation_binary_question(
                source, pair=pair, orientation=orientation,
                presentation=presentation, rom=rom)
            if int(q.gold_index) != int(source.gold_index):
                raise RuntimeError('TASK_BANK_BINARY_GOLD_DRIFT')
            variants[name] = base.serialize_question(q)
    return variants


def insert_binary_consensus(store, questions, *, cohort: str, split: str, cycle: int,
                            champion, rom, seen: set[str], factory, provenance: dict) -> dict:
    """Three independently trainable binary examples per source consensus group.

    Every AB/AC/BC pair and both orientations inherit their source split.
    """
    by_label = Counter()
    by_gold = Counter()
    source_count = 0
    for q in questions:
        if str(q.task) != 'consensus':
            raise RuntimeError('TASK_BANK_BINARY_NON_CONSENSUS_SOURCE')
        fp = factory.question_fingerprint(q)
        if fp in seen:
            raise RuntimeError('TASK_BANK_BINARY_SOURCE_SPLIT_LEAK: ' + fp)
        seen.add(fp)
        source_count += 1
        gold = str(q.candidates[int(q.gold_index)].candidate_id).lower()
        if gold not in champion.CONSENSUS_EXPECTED:
            raise RuntimeError('TASK_BANK_BINARY_UNKNOWN_TOPOLOGY')
        by_gold[gold] += 1
        pairs = champion.build_consensus_pairwise_questions(q)
        if tuple(name for name, _ in pairs) != ('ab', 'ac', 'bc'):
            raise RuntimeError('TASK_BANK_BINARY_PAIR_SET_INVALID')
        for pair, source in pairs:
            label = str(source.candidates[int(source.gold_index)].candidate_id).upper()
            expected = champion.CONSENSUS_EXPECTED[gold][pair].upper()
            if label != expected or label not in ('SAME', 'DIFFERENT'):
                raise RuntimeError('TASK_BANK_BINARY_GOLD_ORACLE_DRIFT')
            variants = binary_consensus_variants(source, pair=pair,
                                                 rom=rom, base=champion.base)
            store.put(cohort_id=cohort, family='clef_binary_consensus',
                      task='consensus_pairwise', split=split,
                      fingerprint=sha(['binary-consensus', fp, pair]),
                      oracle='normalized-python-ast-equivalence-v1', label_status='verified',
                      target={'label':label, 'goldIndex':int(source.gold_index),
                              'pair':pair, 'sourceTopology':gold},
                      provenance={**provenance, 'parentFingerprint':fp,
                                  'parentQuestionId':str(q.question_id),
                                  'pair':pair, 'cycle':cycle,
                                  'orientationPolicy':'forward-and-reverse-are-same-group'},
                      variants=variants)
            by_label[label] += 1
    # The source generator guarantees exactly one of each four-way label in
    # every four-question unit. Consequently the 3 binary pairs are 50/50.
    # Fail closed if that contract changes; never silently train collapsed labels.
    if set(by_gold) != {'a', 'b', 'c', 'none'} or len(set(by_gold.values())) != 1:
        raise RuntimeError(f'TASK_BANK_BINARY_TOPOLOGY_IMBALANCE: {dict(by_gold)}')
    if by_label['SAME'] != by_label['DIFFERENT']:
        raise RuntimeError(f'TASK_BANK_BINARY_LABEL_IMBALANCE: {dict(by_label)}')
    return {'sourceConsensusQuestions':source_count,
            'binaryQuestions':sum(by_label.values()),
            'presentations':4*sum(by_label.values()),
            'labels':dict(sorted(by_label.items())),
            'sourceTopologies':dict(sorted(by_gold.items()))}



def balanced_consensus_questions(*, factory, count: int, kind: str, cycle: int,
                                 seed: int, blocked: set[str], role: str) -> tuple[list, set[str]]:
    """Fill exactly one source example per A/B/C/NONE bucket, even after retries.

    EfficientQuestionFactory guarantees that each *requested* four-question unit
    is balanced, but filtering duplicate fingerprints can skew the retained
    labels. Never allow an apparently balanced binary target to conceal that.
    """
    if count < 4 or count % 4 or kind not in ('train', 'eval'):
        raise ValueError('TASK_BANK_BINARY_INVALID_BALANCED_REQUEST')
    wanted = count // 4
    buckets: dict[str,list] = {name:[] for name in ('a','b','c','none')}
    encountered = set(blocked)
    for attempt in range(8):
        missing = {name: wanted - len(rows) for name, rows in buckets.items()}
        if not any(missing.values()):
            result = [item for name in ('a','b','c','none') for item in buckets[name]]
            return result, {factory.question_fingerprint(q) for q in result}
        # Request one four-label source unit per maximum label deficit.
        # Refill attempts start at a new namespace and never re-use a fingerprint
        # that was previously encountered, even if its bucket was already full.
        request = 4 * max(missing.values())
        fresh, _, _ = factory._generate_filtered(
            kind=kind, plan={'consensus':request},
            data_cycle=int(cycle), seed=int(seed),
            blocked_fingerprints=encountered,
            event_prefix=f'binary-{role}', generation_namespace=attempt)
        for question in fresh:
            fp = factory.question_fingerprint(question)
            if fp in encountered:
                raise RuntimeError('TASK_BANK_BINARY_GENERATOR_REPEATED_FINGERPRINT')
            encountered.add(fp)
            name = str(question.candidates[int(question.gold_index)].candidate_id).lower()
            if name not in buckets:
                raise RuntimeError('TASK_BANK_BINARY_SOURCE_TOPOLOGY_UNKNOWN')
            if len(buckets[name]) < wanted:
                buckets[name].append(question)
    raise RuntimeError('TASK_BANK_BINARY_BALANCE_EXHAUSTED: '+str({k:len(v) for k,v in buckets.items()}))

def generate_binary_consensus(store, *, source_experiment: Path, db_path: Path,
                              seed: int, cycles: int, train_count: int,
                              dev_count: int, predev_count: int,
                              cycle_base: int) -> dict:
    """Generate offline gold binary consensus only; no LM forward or evaluations."""
    champion = load_local('task_bank_binary_champion',
                         'nanojev_three_backbone_clef_tinystories_structured_supervision_train.py')
    rom = load_local('task_bank_binary_rom',
                     'nanojev_three_backbone_clef_rom_structured_supervision_train.py')
    unit = int(champion.base.TASK_UNITS['consensus'])
    if cycles < 1 or min(train_count, dev_count, predev_count) <= 0 or any(
        n % unit for n in (train_count, dev_count, predev_count)
    ):
        raise ValueError('TASK_BANK_BINARY_COUNTS_MUST_BE_POSITIVE_MULTIPLES_OF_FOUR')

    class Logger:
        def emit(self, event, **fields):
            if event.endswith(('retry', 'repaired')):
                print(canonical({'event':event, **fields}), flush=True)

    seen: set[str] = set()
    summary = []
    with champion.EfficientQuestionFactory(source_experiment=source_experiment,
                   training_db=db_path, seed=seed, create_db=True,
                   logger=Logger()) as factory:
        meta = {'sourceExperiment':str(source_experiment),
                'sourceModel':factory.source.get('model'),
                'sourceRevision':factory.source.get('revision'),
                'sourceGenerator':'EfficientQuestionFactory',
                'renderer':'clef-controlled-english-rom-consensus-only-pairwise-v1',
                'seed':seed}
        for offset in range(cycles):
            cycle = cycle_base + offset
            predev, pre_fps = balanced_consensus_questions(
                factory=factory, count=predev_count, kind='eval',
                cycle=cycle+1_000_000, seed=seed, blocked=seen,
                role='predev')
            train, train_fps = balanced_consensus_questions(
                factory=factory, count=train_count, kind='train',
                cycle=cycle+100, seed=seed, blocked=seen|pre_fps,
                role='train')
            dev, _ = balanced_consensus_questions(
                factory=factory, count=dev_count, kind='eval',
                cycle=cycle+200, seed=seed, blocked=seen|pre_fps|train_fps,
                role='dev')
            with store.conn:
                for split, questions in (('predev', predev), ('train', train), ('dev', dev)):
                    cohort = f'binary-consensus:{cycle}:{split}'
                    store.add_cohort(cohort, family='clef_binary_consensus',
                                     split=split, cycle=cycle,
                                     source={**meta,'population':split,'cycle':cycle})
                    stats = insert_binary_consensus(
                        store, questions, cohort=cohort, split=split, cycle=cycle,
                        champion=champion, rom=rom, seen=seen,
                        factory=factory, provenance=meta)
                    summary.append({'cohort':cohort, **stats})
            print(canonical({'event':'task_bank_binary_consensus_cycle_generated',
                             'cycle':cycle, 'populations':summary[-3:]}), flush=True)
    return {'cycleCount':cycles, 'populations':summary,
            'splitSourceQuestionCounts':{'train':train_count,
                                         'dev':dev_count,'predev':predev_count},
            'sourceQuestionsPerBinaryQuestion':3,
            'presentationsPerBinaryQuestion':4,
            'supervisedTask':'consensus_pairwise',
            'trainingObjective':'single-binary-SAME-or-DIFFERENT',
            'rawRomIncluded':False, 'directConsensusIncluded':False,
            'otherTasksIncluded':False}

def cjm_natural_rom(row: dict) -> dict:
    p=row['input']; o=p['options']
    return {'schema':'task-bank.cjm-natural-rom.v1','sampleId':row['sampleId'],
            'groupId':row.get('groupId'), 'split':row['split'],
            'prompt':('Begin a captain decision comparison.\n'
                      f'The captain jacket is {_unambiguous(p["jacket"])}.\n'
                      f'The mission is {_unambiguous(p["mission"])}.\n'
                      f'The observed game state is {_unambiguous(p["observation"])}.\n'
                      f'Option A is {_unambiguous(o["A"])}.\n'
                      f'Option B is {_unambiguous(o["B"])}.\n'
                      'Compare both legal actions according to the jacket and mission.\n'
                      'Select the preferred action using the provisional CJM utility teacher.\n'
                      'Return only A or B.\nEnd the captain comparison.\nAnswer:'),
            'completion':row['output']['choice'],
            'teacher':row['output']['teacherEvidence']['evidenceKind']}


def generate_cjm(store, *, output_dir: Path, seed: int, jackets: int,
                 variants: int, minimum_margin: float) -> dict:
    cjm=load_local('task_bank_cjm','nanojev_captain_jacket_database_smoke.py')
    from nanojev_captain_jacket_store import CaptainJacketStore
    result=cjm.generate(output_dir=output_dir,jacket_count=jackets,
                        variants=variants,seed=seed,min_margin=minimum_margin)
    counters=Counter()
    with CaptainJacketStore(output_dir/'captain-jackets.sqlite') as source:
        for split in ('train','dev','test'):
            cohort=f'cjm:{split}'
            with store.conn:
                store.add_cohort(cohort,family='cjm',split=split,
                                 source={'cjmSchema':cjm.DATA_SCHEMA,
                                         'teacher':cjm.TEACHER,
                                         'manifestSha256':sha(json.loads((output_dir/'manifest.json').read_text(encoding='utf-8'))),
                                         'source':'existing-CJM-generator'})
                grouped:dict[str,dict]={}
                for pref,rom,plain in source.export_records(split):
                    group=str(plain['groupId'])
                    key=grouped.setdefault(group,{'rom':{},'raw':{},'natural_rom':{},
                                                  'teacher':plain['teacher'],
                                                  'observation':pref['observationSha256'],
                                                  'action':plain['targetActionId']})
                    orientation=str(pref['orientation'])
                    if orientation not in ('AB','BA'):
                        raise RuntimeError('TASK_BANK_CJM_ORIENTATION_UNKNOWN')
                    key['rom'][orientation]=rom
                    key['raw'][orientation]=plain
                    key['natural_rom'][orientation]=cjm_natural_rom(rom)
                for group,item in grouped.items():
                    if any(set(item[v])!={'AB','BA'} for v in ('raw','rom','natural_rom')):
                        raise RuntimeError('TASK_BANK_CJM_ORIENTATION_MISSING')
                    for variant, per in (('raw',item['raw']),('rom',item['rom']),
                                         ('natural_rom',item['natural_rom'])):
                        if per['AB'].get('completion',per['AB'].get('output',{}).get('choice')) == per['BA'].get('completion',per['BA'].get('output',{}).get('choice')):
                            raise RuntimeError('TASK_BANK_CJM_POSITION_NOT_BALANCED')
                    variants_by_name={f'{variant}_{orientation}':sample
                        for variant in ('raw','rom','natural_rom')
                        for orientation,sample in item[variant].items()}
                    store.put(cohort_id=cohort,family='cjm',task='captain_jacket_preference',
                              split=split,fingerprint=sha(['cjm',group]),
                              oracle=item['teacher'],label_status='provisional',
                              target={'preferredActionId':item['action']},
                              provenance={'groupId':group,'observationSha256':item['observation'],
                                          'teacher':item['teacher'],
                                          'labelInterpretation':'provisional utility, not measured game outcome'},
                              variants=variants_by_name)
                    counters[split]+=1
    return {'groupsBySplit':dict(counters),'sourceCjm':result['counts'],
            'labelStatus':'provisional','includesDeterministicBoardingSmoke':True}


def import_captain_trace(store, run_dir: Path, seed: int) -> dict:
    """Import exact recorded captain smoke questions with NO fabricated gold.

    A model-produced choice is observational telemetry, never a supervision label.
    Splitting by simulation ID keeps related time steps on one side of the wall.
    """
    manifest_path=run_dir/'manifest.json'
    if not manifest_path.is_file():
        raise RuntimeError('TASK_BANK_CAPTAIN_TRACE_MANIFEST_MISSING')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest_paths=dict(manifest.get('paths') or {})
    def resolve_trace_file(name: str) -> Path:
        supplied=manifest_paths.get(name)
        candidates=[Path(str(supplied))] if supplied else []
        candidates.append(run_dir/(name+'.jsonl'))
        for path in candidates:
            if path.is_file():
                return path
        raise RuntimeError('TASK_BANK_CAPTAIN_TRACE_FILES_MISSING: '+name)
    training_path=resolve_trace_file('training')
    state_path=resolve_trace_file('states')
    states={}
    for text in state_path.read_text(encoding='utf-8').splitlines():
        if text.strip():
            state=json.loads(text)
            sid=str(state.get('stateId') or '')
            if sid:
                states[sid]=state
    grouped:dict[str,list[dict]]={}
    for text in training_path.read_text(encoding='utf-8').splitlines():
        if not text.strip():
            continue
        item=json.loads(text)
        if dict(item.get('measurement') or {}).get('status')!='completed':
            continue
        simulation=str(item.get('simulationId') or '')
        if not simulation:
            raise RuntimeError('TASK_BANK_CAPTAIN_SIMULATION_ID_MISSING')
        grouped.setdefault(simulation,[]).append(item)
    counts=Counter()
    for simulation,rows in sorted(grouped.items()):
        sample_hash=int(sha([seed,simulation])[:8],16)%100
        split='train' if sample_hash<80 else ('dev' if sample_hash<90 else 'test')
        cohort=f'captain-trace:{sha(simulation)[:16]}'
        with store.conn:
            store.add_cohort(cohort,family='captain_smoke',split=split,
                             source={'run':str(run_dir),'manifestSha256':sha(manifest),
                                     'simulationId':simulation})
            for row in rows:
                state_id=str(row.get('stateId') or '')
                state=states.get(state_id,{})
                question_id=str(row.get('questionId') or '')
                question = next((q for q in dict(state.get('executedModelRequest') or {}).get('questions',[])
                                 if str(q.get('id') or '')==question_id),None)
                ident=sha([simulation,state_id,question_id,row.get('sampleId')])
                if not isinstance(question,dict):
                    counts['uncaptured']+=1
                store.put(cohort_id=cohort,family='captain_smoke',task='captain_smoke_trace',
                          split=split,fingerprint=ident,oracle='none-observational-only',
                          label_status='unlabeled',target=None,
                          provenance={'simulationId':simulation,'stateId':state_id,
                                      'questionId':question_id,'captureAvailable':bool(question),
                                      'teacher':'none','sourceKind':'executed-captain-smoke'},
                          variants={'trace':{'schema':'task-bank.captain-smoke-trace.v1',
                                             'simulationId':simulation,'stateId':state_id,
                                             'questionId':question_id,
                                             'question':question,'observation':row.get('canonical'),
                                             'jacket':state.get('jacket'),
                                             'executedModelRequestSha256':state.get('executedModelRequestSha256'),
                                             'measurement':row.get('measurement'),
                                             'supervisedGold':None}})
                counts['imported']+=1
    return dict(counts)


def export_bank(store: TrainingTaskStore, output: Path) -> dict:
    paths={}
    variants=[r[0] for r in store.conn.execute('SELECT DISTINCT variant FROM presentations ORDER BY variant')]
    for split in SPLITS:
        for variant in variants:
            path=output / f'{split}-{variant}.jsonl'
            n=0
            with path.open('w',encoding='utf-8',newline='\n') as f:
                for row in store.iter_examples(split,variant):
                    f.write(canonical(row)+'\n')
                    n+=1
            if n:
                paths[path.name]={'rows':n,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            else:
                path.unlink()
    return paths


def generate(*,output_dir:Path,source_experiment:Path=DEFAULT_SOURCE_EXPERIMENT,
             cycles:int=1,train_questions:int=480,dev_questions:int=48,
             predev_questions:int=512,seed:int=20261010,
             data_cycle_base:int=3_100_000,include_clef:bool=True,
             include_cjm:bool=True,cjm_jackets:int=24,cjm_variants:int=3,
             cjm_minimum_margin:float=.15,captain_run_dirs=(),
             binary_consensus_only:bool=False) -> dict:
    if binary_consensus_only and captain_run_dirs:
        raise ValueError('TASK_BANK_BINARY_MODE_FORBIDS_CAPTAIN_TRACE')
    output_dir=Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError('TASK_BANK_OUTPUT_EXISTS: '+str(output_dir))
    output_dir.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=f'.{output_dir.name}-generating-',dir=output_dir.parent))
    try:
        with TrainingTaskStore(temp/'training-tasks.sqlite') as store:
            sources={}
            if binary_consensus_only:
                sources['binary_consensus']=generate_binary_consensus(
                    store, source_experiment=Path(source_experiment),
                    db_path=temp/'training_lexical.db', seed=seed, cycles=cycles,
                    train_count=train_questions,dev_count=dev_questions,
                    predev_count=predev_questions,cycle_base=data_cycle_base)
            if include_clef and not binary_consensus_only:
                sources['clef']=generate_clef(store,source_experiment=Path(source_experiment),
                    db_path=temp/'training_lexical.db',seed=seed,cycles=cycles,
                    train_count=train_questions,dev_count=dev_questions,
                    predev_count=predev_questions,cycle_base=data_cycle_base)
            if include_cjm and not binary_consensus_only:
                sources['cjm']=generate_cjm(store,output_dir=temp/'cjm-source',seed=seed,
                   jackets=cjm_jackets,variants=cjm_variants,minimum_margin=cjm_minimum_margin)
            for run_dir in captain_run_dirs:
                key='captain_smoke:'+str(Path(run_dir).resolve())
                sources[key]=import_captain_trace(store,Path(run_dir).resolve(strict=True),seed)
            with store.conn:
                store.conn.execute('INSERT INTO metadata VALUES (?,?)', ('generator_schema',SCHEMA))
            checks=store.verify()
            stats=store.stats()
            if not stats['groups']:
                raise RuntimeError('TASK_BANK_EMPTY')
            exports=export_bank(store,temp)
        manifest={'schema':SCHEMA,'storeSchema':STORE_SCHEMA,'ok':True,
                  'seed':seed,'sources':sources,'checks':checks,'counts':stats,
                  'exports':exports,
                  'splitPolicy':'source fingerprint unique across cycles/splits; derived renderings, AB/BA preferences and pairwise relationships stay in their parent group',
                  'claimPolicy':'train groups only for training; held-out groups only for evaluation; unlabeled smoke traces never trainable',
                  'trainerIntegrated':False,'noTrainingPerformed':True,
                  'binaryConsensusOnly':bool(binary_consensus_only),
                  'representationNote':'natural_rom is a deterministic controlled-English rendering of the existing structured ROM program; separately versioned experimental representation'}
        (temp/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        os.replace(temp,output_dir)
        return {'outputDir':str(output_dir),**manifest}
    finally:
        if temp.exists():
            shutil.rmtree(temp)



def verify_existing_bank(output_dir: Path) -> dict:
    output_dir=Path(output_dir).expanduser().resolve(strict=True)
    manifest=json.loads((output_dir/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema')!=SCHEMA or manifest.get('storeSchema')!=STORE_SCHEMA:
        raise RuntimeError('TASK_BANK_MANIFEST_SCHEMA_MISMATCH')
    with TrainingTaskStore(output_dir/'training-tasks.sqlite') as store:
        checks=store.verify()
        if manifest['counts']!=store.stats():
            raise RuntimeError('TASK_BANK_MANIFEST_COUNTS_DRIFT')
    checked=0
    for filename, info in manifest['exports'].items():
        if Path(filename).name!=filename:
            raise RuntimeError('TASK_BANK_EXPORT_PATH_INVALID')
        path=output_dir/filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=info['sha256']:
            raise RuntimeError('TASK_BANK_EXPORT_HASH_MISMATCH: '+filename)
        if sum(1 for line in path.open(encoding='utf-8') if line.strip())!=info['rows']:
            raise RuntimeError('TASK_BANK_EXPORT_ROW_COUNT_MISMATCH: '+filename)
        checked+=1
    cjm_manifest=output_dir/'cjm-source'/'manifest.json'
    if cjm_manifest.is_file():
        source=json.loads(cjm_manifest.read_text(encoding='utf-8'))
        for filename,digest in source.get('jsonlSha256',{}).items():
            if Path(filename).name!=filename:
                raise RuntimeError('TASK_BANK_CJM_EXPORT_PATH_INVALID')
            path=cjm_manifest.parent/filename
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise RuntimeError('TASK_BANK_CJM_EXPORT_HASH_MISMATCH: '+filename)
            checked+=1
    return {'ok':True,'schema':SCHEMA,'bank':str(output_dir),
            'verifiedFiles':checked,'checks':checks}


def self_test() -> dict:
    from nanojev_training_task_store import TrainingTaskStore
    with tempfile.TemporaryDirectory(prefix='task-bank-self-test-') as d:
        path=Path(d)/'task.sqlite'
        with TrainingTaskStore(path) as store:
            with store.conn:
                store.add_cohort('train1',family='clef',split='train',cycle=1)
                g=store.put(cohort_id='train1',family='clef',task='consensus',split='train',
                            fingerprint='f1',oracle='verified-synthetic-test',
                            label_status='verified',target={'goldIndex':2},
                            provenance={'source':'test'},variants={'natural':{'target':2},'rom':{'target':2}})
                store.add_cohort('dev1',family='clef',split='dev',cycle=1)
                h=store.put(cohort_id='dev1',family='clef',task='consensus',split='dev',
                            fingerprint='f2',oracle='verified-synthetic-test',
                            label_status='verified',target={'goldIndex':0},
                            provenance={'source':'test'},variants={'natural':{'target':0}})
            try:
                store.claim(h,'train')
            except ValueError:
                pass
            else:
                raise RuntimeError('TASK_BANK_CLAIM_LEAK')
            store.claim(g,'train');store.claim(h,'eval')
            if len(list(store.iter_examples('train','natural')))!=1:
                raise RuntimeError('TASK_BANK_EXPORT_WRONG')
            return {'ok':True,'schema':SCHEMA,'checks':store.verify(),
                    'stats':store.stats()}


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--source-experiment',type=Path,default=DEFAULT_SOURCE_EXPERIMENT)
    p.add_argument('--cycles',type=int,default=1)
    p.add_argument('--train-questions',type=int,default=480)
    p.add_argument('--dev-questions',type=int,default=48)
    p.add_argument('--predev-questions',type=int,default=512)
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--data-cycle-base',type=int,default=3_100_000)
    p.add_argument('--no-clef',action='store_true')
    p.add_argument('--binary-consensus-only',action='store_true',
                   help='generate only independent verified SAME/DIFFERENT consensus pairs, all representations and splits precomputed; skips CJM and other CLEF tasks')
    p.add_argument('--no-cjm',action='store_true')
    p.add_argument('--cjm-jackets',type=int,default=24)
    p.add_argument('--cjm-variants',type=int,default=3)
    p.add_argument('--cjm-minimum-margin',type=float,default=.15)
    p.add_argument('--captain-run-dir',action='append',type=Path,default=[])
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--verify-bank',type=Path,help='verify an existing sealed bank and exported hashes')
    args=p.parse_args(argv)
    try:
        result=verify_existing_bank(args.verify_bank) if args.verify_bank is not None else (self_test() if args.self_test else generate(
            output_dir=args.output_dir,source_experiment=args.source_experiment,
            cycles=args.cycles,train_questions=args.train_questions,
            dev_questions=args.dev_questions,predev_questions=args.predev_questions,
            seed=args.seed,data_cycle_base=args.data_cycle_base,
            include_clef=not args.no_clef,include_cjm=not args.no_cjm,
            cjm_jackets=args.cjm_jackets,cjm_variants=args.cjm_variants,
            cjm_minimum_margin=args.cjm_minimum_margin,captain_run_dirs=args.captain_run_dir,
            binary_consensus_only=args.binary_consensus_only))
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'ok':False,'schema':SCHEMA,
                          'error':f'{type(exc).__name__}: {exc}'},indent=2))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
