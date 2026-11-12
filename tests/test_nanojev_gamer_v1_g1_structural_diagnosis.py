"""Tests for the source-vs-OOD G1-0023 forensic replay."""
import hashlib
import json
import random
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_structural_diagnosis as diag
from tools import nanojev_gamer_v1_g1_structural_ood as ood
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


@pytest.mark.parametrize('generator',[encounter,allocation])
@pytest.mark.parametrize('layout',['evidence_first','ledger_grid'])
def test_restore_is_exact_and_gold_preserving(generator,layout):
    for index in range(12):
        source=generator(random.Random(9001+index),'dev',index)
        changed=ood.structural_ood_row(source,layout=layout)
        restored,regions=diag.restore_source(changed)
        assert restored['prompt']==source['prompt']
        assert restored['graph']==source['graph']
        assert restored['object_spans']==source['object_spans']
        assert len(regions)==len(ood.SECTION_NAMES[source['family']])
        assert restored['expected_decisions']==source['expected_decisions']


def test_restore_fails_closed_on_gold_or_source_mutation():
    source=allocation(random.Random(39),'dev',1)
    changed=ood.structural_ood_row(source,layout='ledger_grid')
    damaged=json.loads(json.dumps(changed))
    damaged['graph']['nodes'][0]['span'][0]+=1
    with pytest.raises(ValueError,match='GOLD_SPAN_REMAP|ROUNDTRIP'):
        diag.restore_source(damaged)
    damaged=json.loads(json.dumps(changed))
    damaged['source_prompt_sha256']='0'*64
    with pytest.raises(ValueError,match='ORIGINAL_SHA_MISMATCH'):
        diag.restore_source(damaged)
    damaged=json.loads(json.dumps(changed))
    damaged['prompt']=damaged['prompt'].replace('DATA LEDGER','RAW LEDGER',1)
    with pytest.raises(ValueError,match='HEADER_MALFORMED'):
        diag.restore_source(damaged)


def test_overlap_taxonomy_and_stage_count():
    gold={(2,4,'outcome'),(8,10,'quantity')}
    spans={(2,5,'outcome'),(8,10,'quantity'),(12,14,'quantity'),(20,24,'actor')}
    regions={0:(2,16)}
    attr=diag._attribution(spans,spans,gold,regions)
    assert attr['selected_false_positive.outcome.same_kind_boundary_overlap']==1
    assert attr['selected_false_positive.quantity.unannotated_surface']==1
    assert attr['selected_false_positive.actor.heading_or_layout_separator']==1
    assert diag._overlap_type((8,10,'outcome'),gold)=='other_kind_overlap'


def test_load_0022_rejects_altered_report_counts(tmp_path):
    path=tmp_path/'ref';path.mkdir()
    for name in diag.OOD_FILES:
        (path/name).write_text('{}\n' if name.endswith('.json') else '',encoding='utf-8')
    info={'adapter_sha':'adapter','checkpoint_files':{diag.WINNER_HEAD:{'manifest_sha256':'m','head_file_sha256':'h'}}}
    with pytest.raises(ValueError,match='INVALID_0022_REFERENCE'):
        diag._load_0022(path,info)


def test_mocked_full_320_pair_replay(tmp_path,monkeypatch):
    checkpoint=tmp_path/'checkpoint';checkpoint.mkdir()
    (checkpoint/'manifest.json').write_bytes(b'manifest')
    (checkpoint/cur.HEAD_FILE).write_bytes(b'head')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    info={'entries':{'null_focused':{'checkpoint':checkpoint,'manifest':{
        'source_adapter':'mock_adapter','model_id':'mock_model','head_sha256':'head-sha'}}},
        'adapter_sha':'adapter-sha','checkpoint_files':{'null_focused':{
            'manifest_sha256':sha(checkpoint/'manifest.json'),
            'head_file_sha256':sha(checkpoint/cur.HEAD_FILE)}}}
    rows=[{'prompt':'ab'+str(i),'split':'ood_dev','family':('encounter' if i%2==0 else 'allocation'),
           'surface_shift':diag.LAYOUTS[(i//2)%2],
           'object_spans':[{'start':0,'end':2,'kind':'action'}],
           'graph':{'nodes':[{'id':'n0','kind':'action','span':[0,2]}]},
           'source_prompt_sha256':hashlib.sha256(('ab'+str(i)).encode()).hexdigest()}
           for i in range(diag.CASES)]
    cases=[{'configurations':{key:{'correct':1,'missing':0,'false_positives':0,'exact':True}
          for key in (diag.CONTROL,diag.GUARD)}} for _ in rows]
    metric=diag._summarize([({(0,2,'action')},{(0,2,'action')}) for _ in rows],rows)
    report={'metrics':{diag.CONTROL:metric,diag.GUARD:metric}}
    monkeypatch.setattr(diag,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(diag,'_load_0022',lambda *args:(report,rows,cases,{}, {
        diag.OOD_FILES[0]:'mock-report-sha', diag.OOD_FILES[1]:'mock-dataset-sha'}))
    monkeypatch.setattr(diag,'restore_source',lambda x:({**x,'split':'dev'}, {0:(0,len(x['prompt']))}))
    monkeypatch.setattr(diag,'verify_author_gold',lambda *args:320)
    monkeypatch.setattr(diag,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-sha')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    action=SPAN_KINDS.index('action')+1
    class MockHead:
        def eval(self):return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((2,2,len(SPAN_KINDS)+1),-35.,dtype=torch.float32)
            logits[:,:,0]=6.
            logits[0,1,action]=12.
            valid=torch.zeros(2,2,dtype=torch.bool)
            valid[0,1]=True
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *args,**kwargs:(MockHead(),{'stage':'G1','head_sha256':'head-sha'}))
    monkeypatch.setattr(cur,'capture',lambda m,t,row,device,max_length:{
        'prepared':{'eligible':torch.ones(2,dtype=torch.bool),
                    'offsets':[(0,1),(1,2)],'prompt_start':0,
                    'prompt':row['prompt']},
        'hidden':torch.zeros(2,4)})
    # The mock's null-logit shift makes the action win with probability >.5.
    out=tmp_path/'result'
    result=diag.run(root='r',transfer_report='t',fresh_run='f',null_run='n',
                    validation_run='v',ood_run='ood',output=out,device='cpu')
    assert result['original_gold_reconstruction']=='EXACT_ROUNDTRIP_AND_INDEPENDENT_AUTHOR_REPLAY_ALL_320'
    assert result['ood_control_metrics']['exact_prompts']==320
    assert result['ood_guard_metrics']['exact_prompts']==320
    assert result['stage_attribution']['ood']['selected_gold.action']==320
    assert len(diag._read_jsonl(out/'structural_failure_pairs.jsonl'))==320
    assert not result['g2_unlocked'] and not result['production_promotion']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        diag.run(root='r',transfer_report='t',fresh_run='f',null_run='n',
                 validation_run='v',ood_run='ood',output=out,device='cpu')


def test_independent_author_replay_rejects_mutated_graph():
    rng=random.Random(ood.SEED)
    rows=[];sources=[]
    for i in range(16):
        gen=encounter if i%2==0 else allocation
        source=gen(rng,'dev',i)
        shifted=ood.structural_ood_row(source,layout=ood.LAYOUTS[(i//2)%2])
        restored,_=diag.restore_source(shifted)
        sources.append(restored);rows.append(shifted)
    assert diag.verify_author_gold(rows,sources)==16
    sources[7]['graph']['nodes'][0]['span'][0]+=1
    with pytest.raises(ValueError,match='AUTHORED_GOLD_MISMATCH'):
        diag.verify_author_gold(rows,sources)
