"""Offline bank invariants; no model weights or GPU needed."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from nanojev_training_task_store import TrainingTaskStore
import nanojev_training_task_database as gen


@dataclass(frozen=True)
class PathRow:
    prompt: str
    answer: str


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    paths: tuple[PathRow,...]


@dataclass(frozen=True)
class Question:
    question_id: str
    task: str
    candidates: tuple[Candidate,...]
    gold_index: int
    stratum: str = ''


def make_consensus(champion):
    programs={'a':'answer = 2 + 3','b':'answer=2+3\n# formatting only',
              'c':'answer = 2 + 4'}
    prompts={}
    for pair in ('ab','ac','bc'):
        a,b=pair
        def prompt(left,right):
            return (f'Program A:\n```python\n{left}\n```\n'
                    f'Program B:\n```python\n{right}\n```\n'
                    'Determine the normalized Python AST relation. Answer:')
        prompts[pair]=[prompt(programs[a],programs[b]),prompt(programs[b],programs[a])]
    candidates=[]
    for name in ('a','b','c','none'):
        paths=[]
        for pair in ('ab','ac','bc'):
            label=' SAME' if champion.CONSENSUS_EXPECTED[name][pair]=='same' else ' DIFFERENT'
            paths.extend(PathRow(p,label) for p in prompts[pair])
        candidates.append(Candidate(name,tuple(paths)))
    return Question('fixture','consensus',tuple(candidates),2)


@pytest.fixture(scope='module')
def codecs():
    champ=gen.load_local('task_test_champ','nanojev_three_backbone_clef_tinystories_structured_supervision_train.py')
    rom=gen.load_local('task_test_rom','nanojev_three_backbone_clef_rom_structured_supervision_train.py')
    v2=gen.load_local('task_test_v2','nanojev_three_backbone_clef_tinystories_rom_mix_v2_train.py')
    return champ,rom,v2


def test_every_actual_rom_template_has_a_readable_complete_renderer(codecs):
    _,rom,_=codecs
    for task,t in rom.ROM_TEMPLATES.items():
        payload={'task':task,'inputs':{k:'sample' for k in t['inputs']},
                 'program':t['program'],'output':t['output']}
        text=gen.natural_rom_text(payload)
        assert text.startswith('Begin a ')
        assert text.endswith('Answer:')
        assert f'Return only the value of {t["output"]}' in text
    with pytest.raises(RuntimeError,match='UNKNOWN_OP'):
        gen.natural_rom_text({'task':'ast','inputs':{},'output':'x',
                              'program':[{'op':'MAGIC'}]})


def test_consensus_has_three_consistent_pairwise_targets_per_rendering(codecs):
    champ,rom,v2=codecs
    row=gen.clef_variants(make_consensus(champ),champion=champ,rom=rom,v2=v2)
    assert len(row)==12
    assert row['natural_rom']['candidates'][2]['paths'][0]['answer']==' C'
    assert row['rom']['candidates'][2]['paths'][0]['answer'].lstrip().startswith('{')
    for variant in ('natural','rom','natural_rom'):
        direct=row[variant]
        assert len(direct['candidates'])==4
        assert direct['gold_index']==2
        assert [x['candidate_id'] for x in direct['candidates']]==['a','b','c','none']
        for pair,wanted in (('ab','same'),('ac','different'),('bc','different')):
            q=row[f'{variant}_pair_{pair}']
            assert q['task']==champ.CONSENSUS_PAIRWISE_TASK
            assert q['candidates'][q['gold_index']]['candidate_id']==wanted
            assert len(q['candidates'])==2
            assert all(len(c['paths'])==2 for c in q['candidates'])
            assert [p['prompt'] for p in q['candidates'][0]['paths']]==[
                p['prompt'] for p in q['candidates'][1]['paths']]
    # BC observation uses local Program A/B; natural ROM must explicitly map back to global B/C.
    bc=row['natural_rom_pair_bc']['candidates'][0]['paths'][0]['prompt']
    assert 'Program A is global B' in bc
    assert 'Program B is global C' in bc
    assert 'answer = 2 + 4' in row['natural_rom']['candidates'][0]['paths'][0]['prompt']


def test_reserve_and_cross_split_duplicate_are_fail_closed(tmp_path):
    with TrainingTaskStore(tmp_path/'bank.sqlite') as db:
        with db.conn:
            db.add_cohort('a',family='clef',split='train')
            db.add_cohort('b',family='clef',split='predev')
            train=db.put(cohort_id='a',family='clef',task='triad',split='train',
                         fingerprint='same',oracle='formal',label_status='verified',
                         target={'gold':0},provenance={},variants={'natural':{'x':1}})
        with pytest.raises(sqlite3.IntegrityError):
            with db.conn:
                db.put(cohort_id='b',family='clef',task='triad',split='predev',
                       fingerprint='same',oracle='formal',label_status='verified',
                       target={'gold':0},provenance={},variants={'rom':{'x':1}})
        with pytest.raises(ValueError,match='SPLIT_RESERVE'):
            db.claim(train,'eval')
        db.claim(train,'train')
        assert db.verify()['db_integrity']


def test_unlabeled_smoke_not_trainable(tmp_path):
    run=tmp_path/'run';run.mkdir()
    (run/'manifest.json').write_text(json.dumps({'schema':'game.captain-generation.v1'}))
    (run/'states.jsonl').write_text(json.dumps({
        'stateId':'s','jacket':{'mission_commitment':.8},'executedModelRequest':{
            'questions':[{'id':'q','options':['hold','approach']}]}})+'\n')
    (run/'training.jsonl').write_text(json.dumps({
        'simulationId':'battle-01','stateId':'s','questionId':'q','sampleId':'r1',
        'canonical':{'rangeM':300},'measurement':{'status':'completed','answer':'hold'}})+'\n')
    with TrainingTaskStore(tmp_path/'bank.sqlite') as db:
        result=gen.import_captain_trace(db,run,1701)
        assert result['imported']==1
        row=db.conn.execute('SELECT group_id,split FROM task_groups').fetchone()
        assert list(db.iter_examples(row['split'],'trace'))[0]['target'] is None
        if row['split']=='train':
            with pytest.raises(ValueError,match='UNLABELED'):
                db.claim(row['group_id'],'train')
        else:
            with pytest.raises(ValueError,match='SPLIT_RESERVE'):
                db.claim(row['group_id'],'train')
        assert db.verify()['db_integrity']


def test_empty_run_does_not_persist_output(tmp_path):
    out=tmp_path/'set1'
    with pytest.raises(RuntimeError,match='TASK_BANK_EMPTY'):
        gen.generate(output_dir=out,include_clef=False,include_cjm=False)
    assert not out.exists()
    assert list(tmp_path.glob('.*-generating-*'))==[]


def test_cjm_existing_authoritative_generator_integration(tmp_path):
    pytest.importorskip('subprocess')
    with TrainingTaskStore(tmp_path/'tasks.sqlite') as db:
        result=gen.generate_cjm(db,output_dir=tmp_path/'cjm',seed=20261009,
                                jackets=24,variants=3,minimum_margin=.15)
        assert result['groupsBySplit']['train']>0
        assert result['groupsBySplit']['dev']>0
        assert result['groupsBySplit']['test']>0
        assert result['labelStatus']=='provisional'
        samples=list(db.iter_examples('train','raw_AB',family='cjm'))
        assert samples and all(x['labelStatus']=='provisional' for x in samples)
        assert all(x['oracle'] for x in samples)
        for sample in samples[:15]:
            group=sample['groupId']
            other=db.conn.execute('''SELECT sample_json FROM presentations WHERE group_id=?
                AND variant='raw_BA' ''',(group,)).fetchone()
            assert other is not None
            assert sample['example']['completion']!=json.loads(other[0])['completion']
        assert db.verify()['db_integrity']
        assert db.conn.execute('SELECT COUNT(*) FROM task_groups WHERE label_status="unlabeled"').fetchone()[0]==0


def test_publish_and_verify_cjm_bank_with_hash_failure_detection(tmp_path):
    output=tmp_path/'sealed'
    result=gen.generate(output_dir=output,include_clef=False,include_cjm=True,
                        cjm_jackets=24,cjm_variants=3)
    assert result['ok']
    assert gen.verify_existing_bank(output)['verifiedFiles']>0
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        gen.generate(output_dir=output,include_clef=False,include_cjm=True)
    filename=next(iter(result['exports']))
    path=output/filename
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(RuntimeError,match='EXPORT_HASH_MISMATCH'):
        gen.verify_existing_bank(output)


def make_binary_source_consensus(champion, gold: str, serial: int = 0):
    """Four distinct, AST-consistent source topologies with reversible prompts."""
    assert gold in ('a', 'b', 'c', 'none')
    matched = f'answer = {serial + 5} + 3'
    alternate = f'answer = {serial + 5} + 4'
    programs = {name: (alternate if name == gold else matched) for name in 'abc'}
    candidates = []
    for label in ('a', 'b', 'c', 'none'):
        paths = []
        for pair in ('ab', 'ac', 'bc'):
            left, right = pair
            expected = champion.CONSENSUS_EXPECTED[label][pair].upper()
            for x,y in ((left,right),(right,left)):
                prompt = (f'Language: Python\nProgram A:\n```python\n{programs[x]}\n```\n'
                          f'Program B:\n```python\n{programs[y]}\n```\n'
                          'Task: Determine whether Program A and Program B have exactly '
                          'the same normalized Python AST. Ignore formatting-only '
                          'differences. Answer with exactly SAME or DIFFERENT.\nAnswer:')
                paths.append(PathRow(prompt,' '+expected))
        candidates.append(Candidate(label,tuple(paths)))
    return Question(f'synthetic-source-{gold}-{serial}', 'consensus', tuple(candidates),
                    ('a','b','c','none').index(gold))


def test_binary_consensus_renders_single_orientation_exact_trainer_prompt(codecs):
    champion,rom,_ = codecs
    q=make_binary_source_consensus(champion,'c')
    from ast import parse, dump
    pairs=champion.build_consensus_pairwise_questions(q)
    observed={}
    for pair,source in pairs:
        variants=gen.binary_consensus_variants(source,pair=pair,rom=rom,base=champion.base)
        assert set(variants)=={'natural','natural_reverse','natural_rom','natural_rom_reverse'}
        for presentation in ('natural','natural_rom'):
            fwd=variants[presentation]
            rev=variants[presentation+'_reverse']
            assert fwd['task']==rev['task']=='consensus_pairwise'
            assert fwd['gold_index']==rev['gold_index']==int(source.gold_index)
            for obj in (fwd,rev):
                assert len(obj['candidates'])==2
                assert [r['candidate_id'] for r in obj['candidates']]==['same','different']
                assert all(len(r['paths'])==1 for r in obj['candidates'])
                assert obj['candidates'][0]['paths'][0]['prompt']==obj['candidates'][1]['paths'][0]['prompt']
        first,second=rom._program_pair(variants['natural']['candidates'][0]['paths'][0]['prompt'])
        reverse_second,reverse_first=rom._program_pair(variants['natural_reverse']['candidates'][0]['paths'][0]['prompt'])
        assert dump(parse(first))==dump(parse(reverse_first))
        assert dump(parse(second))==dump(parse(reverse_second))
        nl=variants['natural_rom']['candidates'][0]['paths'][0]['prompt']
        assert f'global {pair.upper()} relationship' in nl
        assert 'Return exactly SAME or DIFFERENT.' in nl
        assert 'Answer:' in nl
        assert '\n```python\n'+first+'\n```' in nl
        observed[pair]=source.candidates[source.gold_index].candidate_id
    assert observed=={'ab':'same','ac':'different','bc':'different'}


def test_binary_consensus_balanced_groups_and_labels_with_source_split_boundary(tmp_path,codecs):
    champion,rom,_=codecs
    class Fingerprints:
        @staticmethod
        def question_fingerprint(question):
            return question.question_id
    factory=Fingerprints()
    with TrainingTaskStore(tmp_path/'bank.sqlite') as db:
        with db.conn:
            db.add_cohort('train-1',family='clef_binary_consensus',split='train',cycle=1)
            train=gen.insert_binary_consensus(
                db,[make_binary_source_consensus(champion,label,0)
                    for label in ('a','b','c','none')],cohort='train-1',split='train',
                cycle=1,champion=champion,rom=rom,seen=set(),factory=factory,provenance={})
        assert train['sourceConsensusQuestions']==4
        assert train['binaryQuestions']==12
        assert train['presentations']==48
        assert train['labels']=={'DIFFERENT':6,'SAME':6}
        assert all(n==1 for n in train['sourceTopologies'].values())
        fwd=list(db.iter_examples('train','natural_rom',family='clef_binary_consensus'))
        rev=list(db.iter_examples('train','natural_rom_reverse',family='clef_binary_consensus'))
        assert len(fwd)==len(rev)==12
        assert set(x['groupId'] for x in fwd)==set(x['groupId'] for x in rev)
        assert all(x['task']=='consensus_pairwise' and x['labelStatus']=='verified' for x in fwd)
        assert all(x['target']['goldIndex'] == x['example']['gold_index'] for x in fwd)
        assert len({x['provenance']['parentFingerprint'] for x in fwd})==4
        assert db.verify()['db_integrity']


def test_binary_consensus_balancing_survives_source_fingerprint_repair():
    class FakeQuestion:
        def __init__(self, name, gold):
            self.question_id=name
            self.candidates=[type('Candidate',(),{'candidate_id':g})()
                             for g in ('a','b','c','none')]
            self.gold_index=('a','b','c','none').index(gold)
    class Source:
        calls=0
        @staticmethod
        def question_fingerprint(q):return q.question_id
        def _generate_filtered(self, **kwargs):
            self.calls+=1
            if self.calls==1:
                golds=['a','b','b','b','c','c','none','none']
            else:
                assert kwargs['plan']=={'consensus':4}
                golds=['a','b','c','none']
            questions=[FakeQuestion(f'q{self.calls}-{i}',g) for i,g in enumerate(golds)]
            return questions,0,[]
    source=Source()
    questions,fps=gen.balanced_consensus_questions(factory=source,count=8,kind='train',
                                                  cycle=170,seed=1,blocked=set(),role='train')
    assert source.calls==2
    assert len(questions)==len(fps)==8
    assert {g:sum(q.candidates[q.gold_index].candidate_id==g for q in questions)
            for g in ('a','b','c','none')}=={'a':2,'b':2,'c':2,'none':2}


def test_binary_consensus_rejects_unbalanced_data_and_cross_split_parent(tmp_path,codecs):
    champion,rom,_=codecs
    class F:
        question_fingerprint=staticmethod(lambda q:q.question_id)
    seen=set()
    with TrainingTaskStore(tmp_path/'bank.sqlite') as db:
        with db.conn:
            db.add_cohort('train',family='clef_binary_consensus',split='train')
            db.add_cohort('predev',family='clef_binary_consensus',split='predev')
        with pytest.raises(RuntimeError,match='TOPOLOGY_IMBALANCE'):
            with db.conn:
                gen.insert_binary_consensus(db,[make_binary_source_consensus(champion,'c')],
                    cohort='train',split='train',cycle=1,champion=champion,rom=rom,
                    seen=set(),factory=F(),provenance={})
        assert db.stats()['groups']==0
        questions=[make_binary_source_consensus(champion,g) for g in ('a','b','c','none')]
        with db.conn:
            gen.insert_binary_consensus(db,questions,cohort='train',split='train',
                   cycle=1,champion=champion,rom=rom,seen=seen,factory=F(),provenance={})
        with pytest.raises(RuntimeError,match='SOURCE_SPLIT_LEAK'):
            with db.conn:
                gen.insert_binary_consensus(db,questions,cohort='predev',split='predev',
                       cycle=2,champion=champion,rom=rom,seen=seen,factory=F(),provenance={})
        assert db.stats()['groups']==12


def test_binary_consensus_only_mode_does_not_invoke_old_clef_or_cjm(tmp_path,monkeypatch):
    def no_old(*args,**kwargs):
        raise AssertionError('old task families invoked in binary-only mode')
    monkeypatch.setattr(gen,'generate_clef',no_old)
    monkeypatch.setattr(gen,'generate_cjm',no_old)
    def minimal_binary(store,**kwargs):
        with store.conn:
            store.add_cohort('binary',family='clef_binary_consensus',split='train',cycle=1)
            store.put(cohort_id='binary',family='clef_binary_consensus',
                      task='consensus_pairwise',split='train',fingerprint='binary-only-fixture',
                      oracle='normalized-python-ast-equivalence-v1',label_status='verified',
                      target={'label':'SAME','goldIndex':0},provenance={},
                      variants={'natural_rom':{'gold_index':0,'task':'consensus_pairwise'}})
        return {'cycleCount':1}
    monkeypatch.setattr(gen,'generate_binary_consensus',minimal_binary)
    path=tmp_path/'binary'
    result=gen.generate(output_dir=path,binary_consensus_only=True)
    assert result['binaryConsensusOnly'] is True
    assert set(result['sources'])=={'binary_consensus'}
    assert result['exports']['train-natural_rom.jsonl']['rows']==1
    assert gen.verify_existing_bank(path)['ok']


def test_binary_controlled_english_matches_active_trainer_exactly(codecs):
    """Catch any prompt drift between pre-generated bank and active NL-ROM trainer."""
    import ast
    champion,rom,_=codecs
    trainer_path=ROOT/'tools'/'nanojev_three_backbone_clef_tinystories_natural_rom_consensus_train.py'
    source=ast.parse(trainer_path.read_text(encoding='utf-8'))
    method=next(node for node in source.body
                if isinstance(node,ast.FunctionDef) and node.name=='pairwise_natural_rom_prompt')
    namespace={'rom':rom}
    exec(compile(ast.Module(body=[method], type_ignores=[]),str(trainer_path),'exec'),namespace)
    golden=namespace['pairwise_natural_rom_prompt']
    for gold in ('a','b','c','none'):
        q=make_binary_source_consensus(champion,gold)
        for pair,source_pair in champion.build_consensus_pairwise_questions(q):
            for orientation in (0,1):
                prompt=source_pair.candidates[0].paths[orientation].prompt
                assert gen.binary_consensus_natural_rom_prompt(prompt,pair,rom)==golden(prompt,pair)


def test_binary_full_two_cycle_bank_generation_and_export(tmp_path,codecs,monkeypatch):
    champion,rom,_=codecs
    class FakeFactory:
        def __init__(self,**kw):
            self.source={'model':'test-model','revision':'test-revision'}
        def __enter__(self):return self
        def __exit__(self,*args):return False
        @staticmethod
        def question_fingerprint(q):return q.question_id
        def _generate_filtered(self,*,kind,plan,data_cycle,seed,blocked_fingerprints,
                               event_prefix,generation_namespace):
            assert kind in ('eval','train')
            assert plan.keys()=={'consensus'}
            n=plan['consensus']
            assert n%4==0
            rows=[]
            for i in range(n//4):
                for label in ('a','b','c','none'):
                    serial=data_cycle*1000 + generation_namespace*100 + i
                    q=make_binary_source_consensus(champion,label,serial)
                    assert q.question_id not in blocked_fingerprints
                    rows.append(q)
            return rows,0,[]
    monkeypatch.setattr(champion,'EfficientQuestionFactory',FakeFactory)
    real_load=gen.load_local
    def load_mock(name,filename):
        if filename=='nanojev_three_backbone_clef_tinystories_structured_supervision_train.py':
            return champion
        if filename=='nanojev_three_backbone_clef_rom_structured_supervision_train.py':
            return rom
        return real_load(name,filename)
    monkeypatch.setattr(gen,'load_local',load_mock)
    path=tmp_path/'two-cycles'
    result=gen.generate(output_dir=path,binary_consensus_only=True,
                        cycles=2,train_questions=8,predev_questions=8,
                        dev_questions=4)
    assert result['counts']['groups']==2*(8+8+4)*3
    assert result['counts']['presentations']==4*result['counts']['groups']
    assert set(x['family'] for x in result['counts']['by_family_split'])=={'clef_binary_consensus'}
    assert result['sources']['binary_consensus']['otherTasksIncluded'] is False
    assert result['exports']['train-natural_rom.jsonl']['rows']==2*8*3
    assert result['exports']['predev-natural_rom.jsonl']['rows']==2*8*3
    assert gen.verify_existing_bank(path)['ok']
    with TrainingTaskStore(path/'training-tasks.sqlite') as store:
        for split in ('train','dev','predev'):
            samples=list(store.iter_examples(split,'natural_rom',family='clef_binary_consensus'))
            assert sum(x['target']['label']=='SAME' for x in samples)==len(samples)//2
            assert all(x['task']=='consensus_pairwise' for x in samples)
            for x in samples[:2]:
                assert x['provenance']['parentQuestionId']
                assert x['target']['goldIndex']==x['example']['gold_index']
