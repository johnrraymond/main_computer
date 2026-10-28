"""CPU integration: immutable Stage1 parent, cached vectors, detector semantics."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import torch
from tools import nanojev_hybridlink_frozen_detector_cutover as fc
from tools import nanojev_qwen_numeric_dual_cutover as ct
from test_nanojev_qwen_numeric_dual_cutover import fake_stage1, make_parent, args_for


def make_args(monkeypatch,tmp_path):
    fake=fake_stage1(monkeypatch)
    # Test fixture's original tiny deck repeats identical DEV states. Give DEV
    # disjoint values, as the real Stage1 deck does for source examples.
    old_deck=fake.make_deck
    def disjoint_deck(seed,train_count,dev_count,ood_count,test_count):
        d=old_deck(seed,train_count,dev_count,ood_count,test_count)
        d['dev']=[{'source':f'PROBLEM: {10000+i}+{500+i}',
                   'target':f'ANSWER: {10500+2*i}', 'action':'ANSWER'} if i%2==0 else
                  {'source':f'PROBLEM: ({10000+i}+{500+i})+7',
                   'target':f'PROBLEM: {10500+2*i}+7', 'action':'PROBLEM'}
                  for i in range(dev_count)]
        return d
    fake.make_deck=disjoint_deck
    text,parent,_=make_parent(tmp_path,fake)
    a=args_for(tmp_path,text,parent)
    a.output=str(tmp_path/'frozen')
    a.cache_dir=str(tmp_path/'shared-cache')
    a.stage='train'
    a.numeric_lr=.002;a.numeric_weight=.25;a.answer_threshold=.75
    a.steps=3;a.eval_every=1;a.batch_size=8
    return a


def test_completion_not_identical_to_numerical_value():
    assert fc.is_complete_state('ANSWER: 1')
    assert not fc.is_complete_state('PROBLEM: (2+(-1))+5')
    assert not fc.is_complete_state('PROBLEM: 1+5')
    rows=[{'source':'PROBLEM: (2+(-1))+5','target':'PROBLEM: 1+5'},
          {'source':'PROBLEM: 1+5','target':'ANSWER: 6'},
          {'source':'PROBLEM: 0+1','target':'ANSWER: 1'}]
    x=fc.states_from_rows(rows)
    assert len(x)==5
    assert sum(r['done'] for r in x)==2
    assert any(not r['done'] and r['value']==1 for r in x)


def test_fixed_point_requires_completion():
    a=torch.tensor([1.,2.,3.]);b=a.clone()
    assert fc.classify_latent_step(a,b,.2)['action']=='STALLED'
    assert fc.classify_latent_step(a,b,.99)['action']=='ANSWER'
    assert fc.classify_latent_step(a,a+1.,.2)['action']=='CONTINUE'
    with pytest.raises(ValueError,match='SHAPE'):fc.vector_drift(a,torch.zeros(4))
    with pytest.raises(ValueError,match='NONFINITE'):fc.vector_drift(a,torch.tensor([1.,2.,float('nan')]))
    with pytest.raises(ValueError,match='ANSWER_PROBABILITY'):fc.classify_latent_step(a,b,float('nan'))


def test_immutable_parent_and_cache_reuse_and_handoff(monkeypatch,tmp_path):
    a=make_args(monkeypatch,tmp_path)
    psha=ct.file_hash(a.stage1_checkpoint);tsha=ct.file_hash(a.text_checkpoint)
    preview=fc.preview(a)
    assert preview['model_loaded'] is False and preview['counts']['dev']['answers']>0
    result=fc.train(a)
    assert result['qwen_frozen_verified'] and result['stage1_probe_frozen_verified']
    assert result['parent_unchanged'] and result['latent_feedback_untested']
    assert ct.file_hash(a.stage1_checkpoint)==psha and ct.file_hash(a.text_checkpoint)==tsha
    weights=torch.load(Path(a.output)/'best.pt',map_location='cpu',weights_only=True)
    assert set(weights.keys())=={'schema','config_hash','parent_sha256','step','numeric','detector','dev_metrics','status'}
    assert weights['status']=='UNVALIDATED_FOR_LATENT_FEEDBACK'
    assert 'lora' not in weights and 'probe' not in weights
    assert weights['dev_metrics']['detector']['prefix_supervision_only']
    assert result['dev_overlap_removed']>=0
    # Reuse exactly the same frozen Qwen and teacher vectors across new outputs.
    a.output=str(tmp_path/'run2')
    result2=fc.train(a)
    assert result2['cache_hits']=={'train':True,'dev':True} and result2['teacher_cache_hit']
    a.checkpoint=str(Path(a.output)/'best.pt');a.stage='probe'
    report=fc.probe(a)
    assert report['dev']['detector']['negatives']>0
    assert report['dev']['numeric']['count']==report['dev']['detector']['count']
    a.state='PROBLEM: (2+(-1))+5';a.stage='inspect'
    # Inference must not call the gold arithmetic parser.
    monkeypatch.setattr(ct,'numeric_label',lambda _:(_ for _ in ()).throw(AssertionError('ORACLE_IN_INFERENCE')))
    peek=fc.inspect(a)
    assert 'detector_probability' in peek and not peek['inference_oracle_used']
    a.stage='handoff';a.output=str(tmp_path/'handoff')
    handoff=fc.handoff(a)
    assert handoff['status']=='PREPARED_NOT_ACTIVATED' and handoff['stalled_is_not_answer']
    assert Path(a.output,'handoff.json').is_file()


def test_cached_vectors_provenance_and_corruption_fail_closed(monkeypatch,tmp_path):
    a=make_args(monkeypatch,tmp_path)
    fc.train(a)
    cache=next(Path(a.cache_dir).glob('parent-train-*.pt'))
    x=torch.load(cache,weights_only=True)
    x['key']='WRONG';torch.save(x,cache)
    a.output=str(tmp_path/'rerun')
    with pytest.raises(RuntimeError,match='PROVENANCE_MISMATCH'):fc.train(a)
