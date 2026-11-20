"""G2-0087 adversarial tests: hardest same-kind negatives, predicted G1, seals."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as target
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def spans():
    return [{'kind':'quantity','token_first':0,'token_last':0},
            {'kind':'outcome','token_first':1,'token_last':1},
            {'kind':'outcome','token_first':2,'token_last':2},
            {'kind':'actor','token_first':3,'token_last':3}]


def test_training_targets_use_only_observed_g1_nodes():
    labels=target.training_targets(spans(),{'q':0,'true':1,'false':2},[
        {'src':'q','dst':'true','type':BIND_CLASSES[1]},
        {'src':'q','dst':'missing','type':BIND_CLASSES[1]},
        {'src':'absent','dst':'true','type':BIND_CLASSES[1]}],device='cpu')
    assert labels.shape==(4,4)
    assert labels[0,1]==1
    assert int((labels>0).sum())==1
    assert labels[0,2]==0


def test_training_targets_reject_conflicting_labels_and_bad_id_map():
    with pytest.raises(ValueError,match='MULTILABEL_PAIR_UNSUPPORTED'):
        target.training_targets(spans(),{'q':0,'true':1},[
            {'src':'q','dst':'true','type':BIND_CLASSES[1]},
            {'src':'q','dst':'true','type':BIND_CLASSES[2]}],device='cpu')
    with pytest.raises(ValueError,match='OBSERVED_ID_MAP'):
        target.training_targets(spans(),{'q':0,'true':0},[],device='cpu')


def test_target_loss_rejects_self_edges():
    with pytest.raises(ValueError,match='GOLD_SELF_EDGE'):
        target.training_targets(spans(),{'q':0},[
            {'src':'q','dst':'q','type':BIND_CLASSES[1]}],device='cpu')


def sample_logits():
    # The hardest false outcome is deliberately the *last* destination.
    logits=torch.full((4,4,len(BIND_CLASSES)),-2.0,dtype=torch.float64,
                      requires_grad=True)
    return logits


def test_hard_negative_loss_punishes_wrong_same_kind_destination():
    base=torch.zeros((4,4,len(BIND_CLASSES)),dtype=torch.float64)
    base[0,1,1]=2.0
    base[0,2,1]=7.0
    logits=base.detach().requires_grad_()
    gold=target.training_targets(spans(),{'q':0,'true':1},
        [{'src':'q','dst':'true','type':BIND_CLASSES[1]}],device='cpu')
    loss,terms,info=target.loss_components(logits,gold,spans())
    assert info['hard_rank_pairs']==1 and info['hard_rank_inverted']==1
    assert info['negative_directed_pairs']==11
    assert terms['same_kind_rank'].item()>5
    loss.backward()
    # Under gradient descent the true margin rises and the hard false falls.
    assert logits.grad[0,1,1].item()<0
    assert logits.grad[0,2,1].item()>0


def test_ranking_correct_and_missing_competitor():
    base=torch.zeros((4,4,len(BIND_CLASSES)),dtype=torch.float32)
    base[0,1,1]=6.0; base[0,2,1]=-2.0
    gold=target.training_targets(spans(),{'q':0,'true':1},
        [{'src':'q','dst':'true','type':BIND_CLASSES[1]}],device='cpu')
    _,terms,info=target.loss_components(base,gold,spans())
    assert info['hard_rank_inverted']==0
    assert terms['same_kind_rank'].item()<0.01
    fewer=[spans()[0],spans()[1]]
    _,terms,info=target.loss_components(base[:2,:2],gold[:2,:2],fewer)
    assert info['hard_rank_pairs']==0
    assert terms['same_kind_rank'].item()==0


def test_dense_admission_includes_late_negative_pairs():
    n=35
    s=[{'kind':'outcome','token_first':i,'token_last':i} for i in range(n)]
    s[0]['kind']='quantity'
    log=torch.zeros((n,n,len(BIND_CLASSES)),dtype=torch.float64,requires_grad=True)
    gold=target.training_targets(s,{'a':0,'b':1},
        [{'src':'a','dst':'b','type':BIND_CLASSES[1]}],device='cpu')
    loss,parts,info=target.loss_components(log,gold,s)
    assert info['negative_directed_pairs']==n*(n-1)-1
    loss.backward()
    # A pair far beyond the original 0083 negatives[:32] still contributes.
    assert log.grad[33,34,0].item()!=0
    assert info['hard_rank_pairs']==1


def test_no_legal_pair_rejected():
    with pytest.raises(ValueError,match='INSUFFICIENT_G1_NODES'):
        target.loss_components(torch.zeros((1,1,len(BIND_CLASSES))),
                               torch.zeros((1,1),dtype=torch.long),spans()[:1])


def test_seal_commit_and_drift(tmp_path,monkeypatch):
    root=tmp_path/'run';root.mkdir()
    paths={'dataset':tmp_path/'dataset','certificate':tmp_path/'cert',
           'composite':tmp_path/'composite'}
    paths['dataset'].write_text('dataset')
    paths['certificate'].write_text('signed')
    paths['composite'].mkdir()
    (paths['composite']/'composite-interface.json').write_text('composite')
    records=[{'case_id':f'g2-0087-dev-{i+1:04}', 'split':'dev','family':'encounter',
              'prompt':'prompt'+str(i),'prompt_sha256':hashlib.sha256(('prompt'+str(i)).encode()).hexdigest(),
              'gold_objects':[],'graph':{'nodes':[],'edges':[]}} for i in range(3)]
    monkeypatch.setattr(target,'prospective_rows',lambda _:records)
    monkeypatch.setattr(target,'DEV',2);monkeypatch.setattr(target,'CHALLENGE',1)
    target.commit_seal(paths,root,{'head_sha256':target.PARENT_SHA})
    # The original corpus/answer files are committed before any training.
    assert target.check_seal(paths,root)['answers_sha256']==target.sha(root/'seal'/'answers.SECRET.jsonl')
    with (root/'seal'/'answers.SECRET.jsonl').open('a') as f:f.write('{}\n')
    with pytest.raises(ValueError,match='SEAL_answers_sha256_DRIFT'):
        target.check_seal(paths,root)


def _graph():
    return {'nodes':[{'id':'q','kind':'quantity','span':[0,1]},
                     {'id':'t','kind':'outcome','span':[2,3]},
                     {'id':'f','kind':'outcome','span':[4,5]}],
            'edges':[{'src':'q','dst':'t','type':BIND_CLASSES[1]}]}


def test_rank_audit_same_source_destination_kind():
    graph=_graph();objects=[[0,1,'quantity'],[2,3,'outcome'],[4,5,'outcome']]
    mat=[[i,j,[0.0]*len(BIND_CLASSES[1:])] for i in range(3) for j in range(3) if i!=j]
    mat[0][2][0]=4.0;mat[1][2][0]=1.0  # pairs 0->1, 0->2
    arm={'g1_objects':objects,'pair_margins':mat}
    result=target._paired_rank_statistics(arm,{'graph':graph},arm)
    assert result['groups']==1 and result['all_true_above_false']==1
    mat[1][2][0]=5
    result=target._paired_rank_statistics(arm,{'graph':graph},arm)
    assert result['groups']==1 and result['inverted']==1


def test_rank_audit_detects_duplicate_and_truncated_scores():
    graph=_graph();objects=[[0,1,'quantity'],[2,3,'outcome'],[4,5,'outcome']]
    mat=[[i,j,[0.0]*len(BIND_CLASSES[1:])] for i in range(3) for j in range(3) if i!=j]
    with pytest.raises(ValueError,match='PAIR_MARGINS_INCOMPLETE'):
        target._paired_rank_statistics({'g1_objects':objects,'pair_margins':mat[:-1]},{'graph':graph},{'pair_margins':mat[:-1]})
    mat[-1]=mat[0]
    with pytest.raises(ValueError,match='PAIR_SCORE_FORMAT'):
        target._paired_rank_statistics({'g1_objects':objects,'pair_margins':mat},{'graph':graph},{'pair_margins':mat})


def test_lock_required_before_score(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir()
    out=root/'trial';out.mkdir()
    monkeypatch.setattr(target,'check_seal',lambda paths,dest:{'prompts_sha256':'p'})
    monkeypatch.setattr(target,'_verify_trained',lambda paths,dest:({'head_sha256':'h'},{}))
    with pytest.raises(ValueError,match='HISTORICAL_EVALUATION_MISSING'):
        target.score(root=root,output=out)


def test_no_source_mutation_in_0087_code():
    # The opt-in runner does not monkeypatch the native certified head or decoder.
    source=Path(target.__file__).read_text()
    assert 'cur.trainable_stage(head,\'G2\')' in source
    assert 'inference_spans=spans' in source
    assert 'g3_training_unlocked\':False' in source


def test_module_command_is_importable():
    import importlib
    assert importlib.import_module(target.MODULE) is target


def test_frozen_checkpoint_still_g3_locked_in_report(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir()
    out=root/'trial';out.mkdir()
    monkeypatch.setattr(target,'DEV',2)
    monkeypatch.setattr(target,'CHALLENGE',2)
    gold=_graph()
    objects=[[0,1,'quantity'],[2,3,'outcome'],[4,5,'outcome']]
    def arm(edges,train=False,score_true=3.0,score_false=1.0):
        record={'g1_objects':objects,'binding_edges':edges}
        if train:
            scores=[[i,j,[0.0]*len(BIND_CLASSES[1:])]
                    for i in range(3) for j in range(3) if i!=j]
            scores[0][2][0]=score_true
            scores[1][2][0]=score_false
            record['pair_margins']=scores
        return record
    edge=[[['quantity',[0,1]],['outcome',[2,3]],BIND_CLASSES[1]]]
    wrong=[[['quantity',[0,1]],['outcome',[4,5]],BIND_CLASSES[1]]]
    prompts=[];answers=[];preds=[]
    for split in ('dev','challenge'):
        for n in (1,2):
            case_id=f'g2-0087-{split}-{n:04}'
            fam='encounter' if n==1 else 'allocation'
            text='unseen-'+case_id
            digest=hashlib.sha256(text.encode()).hexdigest()
            prompts.append({'case_id':case_id,'split':split,'family':fam,
                            'prompt':text,'prompt_sha256':digest})
            answers.append({'case_id':case_id,'split':split,'family':fam,
                            'prompt_sha256':digest,
                            'gold_objects':[{'start':a,'end':b,'kind':k} for a,b,k in objects],
                            'graph':gold})
            preds.append({'case_id':case_id,'split':split,'family':fam,
                          'prompt_sha256':digest,'surface_count':3,'modes':{
                        'trained':arm(edge,True,4.0,0.2),
                        'control':arm(wrong,True,0.3,5.0),
                        'untrained':arm([]), 'no_binding':arm([]),
                        'zero_hidden':arm([])}})
    (out/'seal').mkdir();(out/'blind').mkdir()
    target.write_jsonl(out/'seal'/'prompts.jsonl',prompts)
    target.write_jsonl(out/'seal'/'answers.SECRET.jsonl',answers)
    target.write_jsonl(out/'blind'/'predictions.jsonl',preds)
    target.write_json(out/'blind'/'lock.json',{'schema':target.SCHEMA,
            'status':'BLIND_PREDICTIONS_LOCKED_UNSCORED','cases':4,
            'prompts_sha256':target.sha(out/'seal'/'prompts.jsonl'),
            'predictions_sha256':target.sha(out/'blind'/'predictions.jsonl'),
            'trained_head_sha256':'t','parent_head_sha256':target.PARENT_SHA,
            'gold_read_during_inference':False,'teacher_forced_g1':False})
    monkeypatch.setattr(target,'check_seal',lambda paths,dest:{
             'prompts_sha256':target.sha(out/'seal'/'prompts.jsonl')})
    monkeypatch.setattr(target,'_verify_trained',lambda paths,dest:({'head_sha256':'t'},{}))
    monkeypatch.setattr(target,'verify_history',lambda *args:{'status':'DEVELOPMENT_GATE_PASSED', 'failures':[], 'independent_failures':[]})
    result=target.score(root=root,output=out)
    assert result['status']=='G2_0087_NUMERIC_GATES_PASSED_NOT_CERTIFIED'
    assert result['splits']['dev']['metrics']['trained']['exact']==2
    assert result['splits']['challenge']['metrics']['control']['exact']==0
    assert result['splits']['dev']['ranking']['trained']['rate']==1.0
    assert result['splits']['dev']['ranking']['control']['rate']==0.0
    assert result['g3_training_unlocked'] is False
    with pytest.raises(ValueError,match='ALREADY_SCORED'):
        target.score(root=root,output=out)


def test_native_graph_head_training_updates_only_g2_weights():
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    torch.manual_seed(87)
    head=GamerGraphHead(hidden_size=8,width=16,layers=1,latent_slots=4,
                        memory_slots=4,max_operands=2,max_span_tokens=2)
    cur.trainable_stage(head,'G2')
    old={k:v.clone() for k,v in head.state_dict().items()}
    sample=spans()[:3]
    # Source vectors and spans are exogenous G1 predictions, not oracle objects.
    h=torch.randn(7,8)
    out=head(h,eligible=torch.ones(7,dtype=torch.bool),
             inference_spans=sample,active_stage='G2')
    labels=target.training_targets(sample,{'q':0,'true':1},
            [{'src':'q','dst':'true','type':BIND_CLASSES[1]}],device='cpu')
    loss,components,_=target.loss_components(out['binding_logits'][:3,:3],labels,sample)
    assert float(components['same_kind_rank'].detach())>=0
    optimizer=torch.optim.SGD([v for v in head.parameters() if v.requires_grad],lr=.01)
    optimizer.zero_grad();loss.backward();optimizer.step()
    changed=[k for k,v in head.state_dict().items() if not torch.equal(v,old[k])]
    assert changed
    assert all(any(k.startswith(prefix) for prefix in cur.STAGE_MODULES['G2']) for k in changed)
    assert torch.equal(old['project.0.weight'],head.project[0].weight)
