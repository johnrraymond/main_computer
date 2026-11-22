"""Offline falsifiable checks of 0097 direct weight edit and scoring protocol."""
import hashlib
import json
from pathlib import Path
import torch
from safetensors.torch import save_file, load_file
import pytest
from tools import nanojev_gamer_v1_g2_mit_weight_intervention_0097 as m
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus


def fake_parent(tmp_path, sub='oracle-fabric-g2-extended-learning-0096/warm_supervised/milestone-01600'):
    p=tmp_path/sub
    p.mkdir(parents=True)
    tensor={'binding.bias':torch.tensor([0., .1, .2, .3, .4]),
            'binding.weight':torch.arange(10.,dtype=torch.float32).reshape(2,5),
            'span_classifier.bias':torch.tensor([3.,4.])}
    save_file(tensor,str(p/cur.HEAD_FILE))
    m.write(p/cur.MANIFEST,{'schema':cur.SCHEMA,
         'status':'TRAINED_NOT_CERTIFIED','head_sha256':m.sha(p/cur.HEAD_FILE),
         'head':{'object_extractor':'joint-span-v1'},
         'g3_training_unlocked':False,'production_promotion':False})
    return p,tensor


def test_exactly_one_nontrainable_coefficient_changes():
    initial={'binding.bias':torch.tensor([0.125,-0.3,0.8,-1.2,0.5]),
             'binding.weight':torch.randn(5,192),
             'span_classifier.weight':torch.randn(4,16)}
    new, receipt=m.edit_state(initial)
    assert receipt['changed_coefficients']==1 and receipt['gradient_steps']==0
    assert receipt['index']==m.BIND_CLASSES.index('none')==0
    assert torch.isclose(new['binding.bias'][0]-initial['binding.bias'][0],
                         torch.tensor(0.5))
    assert all(torch.equal(new[k],old) for k,old in initial.items() if k!='binding.bias')
    assert torch.equal(new['binding.bias'][1:],initial['binding.bias'][1:])
    with pytest.raises(ValueError,match='PRECOMMITTED_EDIT_DELTA_DRIFT'):
        m.edit_state(initial,0.4)


def test_edited_none_bias_is_exact_negative_margin_shift():
    # If binding logits are linear in binding.bias and score margins are
    # positive minus NONE, the +0.5 edit reduces every positive margin by 0.5.
    positive=torch.tensor([[0.7,-0.2,0.3,1.1]],dtype=torch.float32)
    before=torch.cat([torch.tensor([[0.4]]),positive],dim=1)
    after=before.clone()
    after[:,0]+=0.5
    assert torch.allclose(after[:,1:]-after[:,:1],
                          before[:,1:]-before[:,:1]-0.5)


def test_auto_discovery_is_unambiguous_and_explicit(tmp_path):
    path,orig=fake_parent(tmp_path)
    matches=m.discover(tmp_path)
    assert len(matches)==1 and matches[0]['checkpoint']==str(path.resolve())
    assert m.select_checkpoint(tmp_path,None,'warm_supervised')==matches[0]
    # Other campaigns no longer create ambiguity or trigger recursive scans.
    other,_=fake_parent(tmp_path,'another-0096/warm_supervised/checkpoint')
    assert m.select_checkpoint(tmp_path,None,'warm_supervised')==matches[0]
    assert m.select_checkpoint(tmp_path,str(path),'warm_supervised')==matches[0]
    # 0096's highest selected milestone, not the final (potentially weaker) head.
    earlier,_=fake_parent(tmp_path,
        'oracle-fabric-g2-extended-learning-0096/warm_supervised/milestone-01200')
    final,_=fake_parent(tmp_path,
        'oracle-fabric-g2-extended-learning-0096/warm_supervised/final')
    assert m.discover(tmp_path)[0]['checkpoint']==str(path.resolve())
    # Custom 0096 manifest does not share curriculum-v2 status/schema.
    manifest_file=path/cur.MANIFEST
    custom=m.load(manifest_file)
    custom.update(schema='nanojev.gamer.graph.g2.extended-learning-0096.v1',
                  status='G2_0096_CHECKPOINT_FROZEN_NOT_CERTIFIED')
    m.write(manifest_file,custom)
    assert m.discover(tmp_path)[0]['source_schema']==custom['schema']
    # Unknown external locations are never silently accepted.
    with pytest.raises(ValueError,match='SOURCE_NOT_0096_CAMPAIGN'):
        m.select_checkpoint(tmp_path,str(other),'warm_supervised')



def test_0096_milestone_manifest_without_run_status_is_accepted(tmp_path):
    # A saved 0096 head is not itself an experiment report, so it may contain
    # no status field. The manifest's head hash remains mandatory.
    path,_=fake_parent(tmp_path)
    manifest_path=path/cur.MANIFEST
    original=m.load(manifest_path)
    original.pop('status')
    m.write(manifest_path,original)
    found=m.discover(tmp_path)
    assert len(found)==1
    assert found[0]['status']=='0096-SELECTED_CHECKPOINT'
    assert found[0]['head_sha256']==m.sha(path/cur.HEAD_FILE)
    assert found[0]['manifest_sha256']==m.sha(manifest_path)
    assert m.select_checkpoint(tmp_path,None,'warm_supervised')==found[0]
    # A v1 checkpoint can also legitimately omit a schema; neither omission
    # grants any ability to bypass the manifest-bound weight hash.
    original.pop('schema')
    m.write(manifest_path, original)
    assert m.discover(tmp_path)[0]['source_schema']=='0096-checkpoint-manifest-unversioned'
    original['head_sha256']='0'*64
    m.write(manifest_path,original)
    with pytest.raises(ValueError,match='SOURCE_CHECKPOINT_HASH_DRIFT'):
        m.discover(tmp_path)
    original['head_sha256']=m.sha(path/cur.HEAD_FILE)
    original['g3_unlocked']=True
    m.write(manifest_path,original)
    with pytest.raises(ValueError,match='SOURCE_MANIFEST_INVALID'):
        m.discover(tmp_path)

def test_prepared_gold_is_excluded_from_edit_and_lineage_is_unchanged(tmp_path,monkeypatch):
    root=tmp_path/'reports'
    root.mkdir()
    src,raw=fake_parent(root)
    monkeypatch.setattr(m.structural,'paths_for',lambda root:{'dataset':root/'nonexistent.jsonl'})
    monkeypatch.setattr(m.structural,'preflight',lambda paths: ({'status':'TEST'},
                          {'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}))
    source_hash=m.sha(src/cur.HEAD_FILE)
    commit=m.prepare(root=root,cases=40)
    dest=m.location(root,None)
    assert (dest/'seal'/'answers.SECRET.jsonl').exists()
    gold=(dest/'seal'/'answers.SECRET.jsonl').read_bytes()
    (dest/'seal'/'answers.SECRET.jsonl').unlink()
    # Edit deliberately cannot open the gold file: runs successfully without it.
    m.edit(root=root)
    assert m.sha(src/cur.HEAD_FILE)==source_hash
    assert m.check(dest)['source']['head_sha256']==source_hash
    edit=load_file(str(dest/'edit'/'checkpoint'/cur.HEAD_FILE))
    assert torch.allclose(edit['binding.bias'][0],raw['binding.bias'][0]+0.5)
    assert torch.equal(edit['span_classifier.bias'],raw['span_classifier.bias'])
    (dest/'seal'/'answers.SECRET.jsonl').write_bytes(gold)
    assert m.check(dest,with_gold=True)==commit


def test_score_requires_blind_lock_and_uses_genuine_graph_gold(tmp_path,monkeypatch):
    root=tmp_path/'reports'; root.mkdir()
    src,raw=fake_parent(root)
    monkeypatch.setattr(m.structural,'paths_for',lambda root:{'dataset':root/'none.jsonl'})
    monkeypatch.setattr(m.structural,'preflight',lambda paths: ({},
        {'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}))
    m.prepare(root=root,cases=40)
    m.edit(root=root)
    dest=m.location(root,None)
    predictions=[]
    for case in m.rows(dest/'seal'/'prompts.jsonl'):
        gold=next(a for a in m.rows(dest/'seal'/'answers.SECRET.jsonl')
                  if a['case_id']==case['case_id'])
        edges=sorted(m.bakeoff.truth(gold['graph']))
        preds=[m.json_edge(e) for e in edges]
        # Exact equality in both arms is expected in this synthetic scorer
        # acceptance, NOT a model performance result.
        predictions.append({'case_id':case['case_id'],
              'prompt_sha256':case['prompt_sha256'],'family':case['family'],
              'baseline':preds,'edited':preds,'g1_objects':[]})
    (dest/'blind').mkdir()
    m.write_rows(dest/'blind'/'predictions.jsonl',predictions)
    lock={'schema':m.SCHEMA,'status':'BLIND_PREDICTIONS_LOCKED',
          'prompts_sha256':m.load(dest/'seal'/'commit.json')['prompts_sha256'],
          'predictions_sha256':m.sha(dest/'blind'/'predictions.jsonl'),
          'parent_head_sha256':m.load(dest/'seal'/'commit.json')['source']['head_sha256'],
          'edited_head_sha256':m.verify_edit(dest)['edited_head_sha256'],
          'gold_accessed':False}
    m.write(dest/'blind'/'lock.json',lock)
    report=m.score(root=root)
    assert report['metrics']['overall']['baseline']['exact']==40
    assert report['metrics']['overall']['edited']['exact']==40
    assert report['precommitted_gate_passed'] is False
    assert report['status']=='MIT_EDIT_NO_SAFE_GAIN_ON_FRESH_CHALLENGE'
    assert report['parent_unchanged'] is True


def test_precommitted_static_conditions():
    assert m.EDIT_DELTA==0.5
    assert m.EDIT_LABEL=='none'
    assert m.PREFERRED_ARM=='warm_supervised'
    assert m.OUTPUT_NAME.endswith('0097')


def test_predict_is_gold_blind_and_resumes_without_reloading_gpu(tmp_path,monkeypatch):
    root=tmp_path/'reports';root.mkdir()
    fake_parent(root)
    monkeypatch.setattr(m.structural,'paths_for',lambda root:{'dataset':root/'none.jsonl'})
    monkeypatch.setattr(m.structural,'preflight',lambda paths: ({},
        {'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}))
    m.prepare(root=root,cases=40)
    m.edit(root=root)
    dest=m.location(root,None)
    gold_file=dest/'seal'/'answers.SECRET.jsonl'
    secret=gold_file.read_bytes()
    gold_file.unlink()
    calls=[]
    class FakeHead:
        def eval(self):return self
        def requires_grad_(self,_):return self
    class FakeComposite:
        def predict(self,*args,**kwargs):
            return {'token_spans':[]}
    monkeypatch.setattr(m.structural,'_load_runtime',lambda *args:
                        (None,None,FakeComposite(),None,None))
    monkeypatch.setattr(m,'_load_scientific_head',lambda *args,**kwargs:FakeHead())
    monkeypatch.setattr(m.bakeoff.previous,'_capture_inference',lambda *args:
                        ({'eligible':torch.ones(2)},torch.ones(2)))
    def infer(*args):
        calls.append(1)
        return {'g1_objects':[],'pair_margins':[],'binding_edges':[]}
    monkeypatch.setattr(m.frozen,'_infer',infer)
    m.predict(root=root,device='cpu')
    assert len(list((dest/'blind'/'shards').glob('*.json')))==40
    assert len(calls)==80
    assert len(list(m.rows(dest/'blind'/'predictions.jsonl')))==40
    assert m.load(dest/'blind'/'lock.json')['gold_accessed'] is False
    (dest/'blind'/'lock.json').unlink()
    # A simulated interruption after inference but before locking must
    # reconstruct the aggregate from identical validated shards without GPU.
    monkeypatch.setattr(m.structural,'_load_runtime',lambda *args:
                        (_ for _ in ()).throw(AssertionError('GPU SHOULD NOT RELOAD')))
    m.predict(root=root,device='cpu')
    assert len(calls)==80
    gold_file.write_bytes(secret)
    report=m.score(root=root)
    assert report['status']=='MIT_EDIT_NO_SAFE_GAIN_ON_FRESH_CHALLENGE'


def test_0096_raw_checkpoint_loads_strictly_without_curriculum_manifest(tmp_path):
    # Actual 0096 milestone manifests need not be curriculum-v2; use model
    # architecture from the verified pre-existing G2 head, not from that file.
    path,_=fake_parent(tmp_path)
    class Mini(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.binding = torch.nn.Linear(5,2)
            self.span_classifier = torch.nn.Linear(1,2)
    model = Mini()
    weights = {k: v.clone() for k,v in model.state_dict().items()}
    m.save_file(weights,str(path/cur.HEAD_FILE))
    custom=m.load(path/cur.MANIFEST)
    custom.update(schema='nanojev.gamer.graph.g2.extended-learning-0096.v1',
                  status='G2_0096_CHECKPOINT_FROZEN_NOT_CERTIFIED',
                  head_sha256=m.sha(path/cur.HEAD_FILE))
    m.write(path/cur.MANIFEST,custom)
    head = m._load_scientific_head(path,model,device='cpu')
    assert all(torch.equal(head.state_dict()[k],v) for k,v in weights.items())
    assert head is not model
    with torch.no_grad():
        head.binding.bias[0] += 7.0
    assert not torch.equal(head.binding.bias,model.binding.bias)
    invalid=dict(weights)
    invalid.pop('binding.weight')
    m.save_file(invalid,str(path/cur.HEAD_FILE))
    with pytest.raises(RuntimeError,match='Missing key'):
        m._load_scientific_head(path,model,device='cpu')
