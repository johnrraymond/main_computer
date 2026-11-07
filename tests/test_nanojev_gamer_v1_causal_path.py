"""Frozen C0 causal-path experiments: no training and strict zero oracle."""
import json
import hashlib
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_causal_path as causal
from tools import nanojev_gamer_v1_number_distance as distance
from tools.nanojev_qwen_c0_compressive_train import make_network


def test_train_only_fold_selection_and_global_consensus():
    games=[f'g{i}' for i in range(8)]
    folds=distance._group_folds(games,folds=2,seed=47)
    qq=[];hh=[]
    for tr,te in folds:
        qq.append({'test_games':len(te),'train_games':len(tr),'top_blocks':[65,71,72,87],
                   'nested_top1_tile':3})
        hh.append({'top_blocks':[2,1,3,0],'nested_top1_tile':5})
    report={'folds':2,'seed':47,'by_stage':{
      'qwen_final_100':{'arms':{'unseen_games_values_words':{
          'fold_details':qq,'top_block_selection_frequency':{'65':2,'71':2,'72':2,'87':2}}}},
      'c0_hidden_1':{'arms':{'unseen_games_values_words':{'fold_details':hh}}}}}
    assignments,global_blocks=causal._fold_positions(report,games,top=2)
    assert len(assignments)==8
    assert all(v['blocks']==[65,71] for v in assignments.values())
    assert all(v['hidden_block']==2 and v['hidden_tile']==5 for v in assignments.values())
    assert global_blocks==[65,71]
    with pytest.raises(ValueError,match='FOLDS_CHANGED'):
        report['by_stage']['qwen_final_100']['arms']['unseen_games_values_words']['fold_details'][0]['test_games']+=1
        causal._fold_positions(report,games,top=2)


def test_block_and_tile_transplant_are_reversible_and_do_not_touch_source():
    a=np.arange(3*1024,dtype=np.float32);b=-a
    orig=a.copy();don=b.copy()
    p=causal._swap_blocks(a,b,[1],1024)
    assert np.array_equal(p[:1024],a[:1024])
    assert np.array_equal(p[1024:2048],b[1024:2048])
    assert np.array_equal(p[2048:],a[2048:])
    q=causal._swap_tile(a,b,1,3)
    assert np.array_equal(q[1024+3*64:1024+4*64],b[1024+3*64:1024+4*64])
    assert np.array_equal(a,orig) and np.array_equal(b,don)
    assert np.array_equal(causal._swap_blocks(a,b,[]),a)
    with pytest.raises(ValueError,match='BAD_BLOCK'):
        causal._swap_blocks(a,b,[4])
    with pytest.raises(ValueError,match='OUT_OF_RANGE'):
        causal._swap_tile(a,b,2,16)


def test_pair_sampling_balances_zero_and_excludes_token_count_mismatch():
    v=np.arange(-8,9)
    counts=np.full((3,3,17),28,dtype=np.int32)
    cases,stats=causal._chosen_pairs(v,counts,games=3,limit=30)
    assert len(cases)==30
    assert stats['by_kind']['touch_zero']>=8
    counts[:,2,np.where(v==0)[0][0]]=29
    cases,stats=causal._chosen_pairs(v,counts,games=3,limit=30)
    assert stats['token_count_mismatch_excluded']>0
    assert all(counts[c['game'],c['form'],c['from']]==counts[c['game'],c['form'],c['to']] for c in cases)


def test_paired_gain_signflip_and_zero_gate_never_equate_effect_to_correctness():
    rows=[{'receiver_logit':0,'donor_logit':2,'candidate_logit':1.5,
           'control_logits':[.02,.05]} for _ in range(12)]
    result=causal._measure(rows,permutations=99)
    assert result['candidate_minus_control_gain']>1.0
    assert result['paired_signflip_p']==.01
    # Predictive effects alone cannot certify zero without original truth.
    synth={'input_position_swap':{'touch_zero':result}}
    assert not causal._zero_gate(None,synth)['passed']
    bad={'baseline':{'zero':{'cases':12,'within_1e_6':0,'within_0_1':0},
          'small_negative':{'cases':12,'sign_accuracy':.98},
          'small_positive':{'cases':12,'sign_accuracy':.98},
          'nonzero_sign_accuracy':.98}}
    gate=causal._zero_gate(bad,synth)
    assert not gate['passed'] and 'BLOCKED_ZERO_LINE' in gate['status']
    good={'baseline':{'zero':{'cases':12,'within_1e_6':1,'within_0_1':1},
          'small_negative':{'cases':12,'sign_accuracy':1.0},
          'small_positive':{'cases':12,'sign_accuracy':1.0},
          'nonzero_sign_accuracy':.99}}
    spelling={'forms':{k:{'median_abs_signed_logit_gap':0.0} for k in
                        ('plus_zero','minus_zero','word_zero')}}
    assert causal._zero_gate(good,synth,spelling)['passed']
    spelling['forms']['word_zero']['median_abs_signed_logit_gap']=0.2
    assert not causal._zero_gate(good,synth,spelling)['passed']


def _toy_head(width):
    net=make_network(torch,width,hidden=(8,6,4,2)).eval()
    with torch.no_grad():
        for p in net.parameters():p.zero_()
        net.norm.weight.fill_(1.0)
        pos=65*1024+12
        net.enc[0].weight[0,pos]=1.0
        net.enc[2].weight[0,0]=1.0
        net.enc[4].weight[0,0]=1.0
        net.mid[0].weight[0,0]=1.0
        net.corrected.weight[0,0]=.05
    return net


def test_real_torch_forward_and_counterfactual_influence_from_selected_qwen_position():
    w=102400
    h=_toy_head(w)
    base=np.zeros(w,dtype=np.float32)
    rng=np.random.default_rng(5)
    base[:256]=rng.normal(0,.2,size=256)
    before=base.copy()
    after=base.copy();after[65*1024+12]=2.
    rec,hidden=causal._infer(torch,h,before,device='cpu',trace=True)
    donor=causal._infer(torch,h,after,device='cpu')
    patched,patched_hidden=causal._infer(torch,h,causal._swap_blocks(before,after,[65]),device='cpu',trace=True)
    placebo=causal._infer(torch,h,causal._swap_blocks(before,after,[33]),device='cpu')
    assert abs(donor-patched)<1e-6
    assert abs(rec-placebo)<1e-6
    assert abs(patched-rec)>.02
    assert np.linalg.norm(patched_hidden-hidden)>.02
    assert abs(causal._infer_h1(torch,h,hidden,device='cpu')-rec)<1e-6
    assert np.array_equal(before,base)
    assert abs(causal._parity(float(causal._inverse_signed_log(rec)),rec))<1e-8
    with pytest.raises(ValueError,match='PARITY_FAILED'):
        causal._parity(float(causal._inverse_signed_log(rec+1)),rec)


def test_labeled_certified_metrics_need_true_zero_predictions():
    target=[0,0,-1,2];pred=[0.03,-0.03,-1.1,2.2]
    s=causal._certified_metrics(target,pred)
    assert s['zero']['cases']==2
    assert s['zero']['within_1e_6']==0
    assert s['zero']['within_0_1']==1.0
    assert s['nonzero_sign_accuracy']==1.0
    assert s['small_negative']['cases']==1 and s['small_negative']['sign_accuracy']==1.0


def test_cli_registration_read_only_flags():
    from tools.nanojev_gamer_v1_staircase import parser,main
    p=parser()
    args=p.parse_args(['head-100-causal-path','--run','report','--causal-pairs','24'])
    assert args.causal_pairs==24
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-causal-path','--run','report','--steps','1'])
    with pytest.raises(ValueError,match='ONLY_CAUSAL_PATH'):
        main(['head-100-number-distance','--run','report','--causal-pairs','24'])


def test_read_source_enforces_exact_capture_fingerprints(tmp_path):
    src=tmp_path/'capture';src.mkdir()
    for name in ('activations.npz','interventions.jsonl'):(src/name).write_bytes(b'content')
    cr={'schema':distance.SCHEMA,'status':'COMPLETE_READ_ONLY','checkpoint':'best',
       'checkpoint_step':500,'contexts':[{'game_id':'game'}],
       'expanded_run':str(tmp_path/'expanded'),'source_run':str(tmp_path/'source')}
    (src/'final_report.json').write_text(json.dumps(cr))
    loc_folder=tmp_path/'localization';loc_folder.mkdir()
    hashes={p.name:causal._hash(p) for p in src.iterdir()}
    from tools import nanojev_gamer_v1_information_localization as localize
    r={'schema':localize.SCHEMA,'status':'COMPLETE_READ_ONLY','source_capture':str(src),
       'source_sha256':hashes,'by_stage':{'qwen_final_100':{'primary_candidate':True}}}
    (loc_folder/'final_report.json').write_text(json.dumps(r))
    _,_,_,files=causal._read_source(loc_folder)
    assert len(files)==3
    (src/'interventions.jsonl').write_bytes(b'drift')
    with pytest.raises(ValueError,match='FINGERPRINT_MISMATCH'):
        causal._read_source(loc_folder)


def test_zero_spelling_invariance_not_a_numeric_answer_test():
    values=np.arange(-8,9)
    ans=np.zeros((2,3,17,1),dtype=np.float32)
    iz=np.where(values==0)[0][0]
    ans[:,0,iz,0]=0.0
    ans[:,1,iz,0]=0.01
    ans[:,2,iz,0]=0.5
    counts=np.full((2,3,17),10,dtype=int)
    counts[:,2,iz]=11
    report=causal.zero_spelling_invariance(ans,values,counts,
              minus_zero=np.zeros((2,1)),minus_zero_counts=np.full(2,11))
    assert report['forms']['plus_zero']['changed_token_count']==0
    assert report['forms']['word_zero']['changed_token_count']==2
    assert report['forms']['word_zero']['median_abs_signed_logit_gap']>.3
    assert report['forms']['minus_zero']['changed_token_count']==2
    assert report['forms']['minus_zero']['median_abs_signed_logit_gap']==0.


def test_synthetic_full_width_head_replay_and_matched_causal_interventions():
    rng=np.random.default_rng(17)
    head=_toy_head(102400)
    values=np.arange(-8,9,dtype=np.int32)
    n=17
    data=np.zeros((1,3,n,102400),dtype=np.float32)
    hidden=np.zeros((1,3,n,8),dtype=np.float32)
    answer=np.zeros((1,3,n,1),dtype=np.float32)
    base=np.zeros(102400,dtype=np.float32)
    base[:256]=rng.normal(0,.3,size=256)
    for f in range(3):
        for vi,v in enumerate(values):
            x=base.copy();x[65*1024+12]=float(v)*.2
            data[0,f,vi]=x
            logit,h1=causal._infer(torch,head,x,device='cpu',trace=True)
            hidden[0,f,vi]=h1
            answer[0,f,vi,0]=float(causal._inverse_signed_log(logit))
    counts=np.full((1,3,17),44,dtype=np.int32)
    choice={0:{'fold':0,'blocks':[65], 'tile':0,'hidden_block':0,'hidden_tile':0}}
    result=causal.evaluate_transplants(torch,head,data,hidden,answer,counts,values,['g'],
                        choice,device='cpu',max_pairs=12,controls=2,permutations=19)
    assert result['selection']['selected']==12
    assert result['baseline_replay_max_abs_signed_log_error']<1e-5
    assert result['identity_swap_max_abs_signed_log_error']==0
    assert result['input_position_swap']['all']['candidate_minus_control_gain']>0
    assert result['input_position_swap']['touch_zero']['cases']>=3


def test_certified_source_keeps_original_labels_and_cache_order(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_expanded_head as exp
    expanded=tmp_path/'expanded';source=tmp_path/'source'
    (expanded/'feature_cache').mkdir(parents=True);source.mkdir()
    (source/'handoff_manifest.json').write_text(json.dumps({}))
    (source/'certified_pairs.jsonl').write_text('dummy')
    (expanded/'feature_cache'/'dev.npz').write_text('mock')
    dev=[]
    for g in range(4):
        for i,answer in enumerate((0,-1,1,2)):
            dev.append({'game_id':f'g{g}','answer':answer,'split':'dev','prompt_08':f'g{g}-{i}'})
    monkeypatch.setattr(causal.gamer,'load_cases',lambda *a,**k:(dev,{}))
    monkeypatch.setattr(causal.gamer,'by_split',lambda rows,part:[r for r in rows if r['split']==part])
    monkeypatch.setattr(causal.gamer,'original_prompt',lambda r:r['prompt_08'])
    full=np.random.default_rng(3).normal(size=(len(dev),8)).astype(np.float32)
    def fake_read(path,identity,n,dim):
        assert identity['split']=='dev' and n==len(dev) and dim==8
        assert identity['prompt_sha256']==hashlib.sha256(causal.gamer.canonical(
                    [r['prompt_08'] for r in dev]).encode()).hexdigest()
        return full,{}
    monkeypatch.setattr(exp,'_read_cache',fake_read)
    identity={'configuration':{'input_width':8},'schema':'mock'}
    rows,features,cache,summary=causal._certified_source(expanded,source,identity,limit=12)
    assert cache.is_file() and features.shape==(12,8)
    assert summary['zero_cases']>=2
    games={r['game_id'] for r in rows}
    assert all(sum(r['game_id']==g for r in rows)>=2 for g in games)
    assert all(any(np.array_equal(features[i],item) for item in full) for i in range(len(features)))


def test_complete_capture_to_causal_report_smoke_readonly(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_information_localization as info
    head=_toy_head(102400)
    source=tmp_path/'source';expanded=tmp_path/'expanded'
    source.mkdir();expanded.mkdir()
    (expanded/'best_model.pt').write_bytes(b'fake frozen best checkpoint')
    (expanded/'provenance.json').write_text(json.dumps({'configuration':{'input_width':102400}}))
    capture=tmp_path/'capture';capture.mkdir()
    context_names=[f'g{i}' for i in range(8)]
    values=np.arange(-8,9,dtype=np.int32)
    rng=np.random.default_rng(123)
    x=np.zeros((8,3,17,102400),dtype=np.float32)
    h1=np.zeros((8,3,17,8),dtype=np.float32)
    y=np.zeros((8,3,17,1),dtype=np.float32)
    neg=np.zeros((8,1),dtype=np.float32)
    for g in range(8):
        base=np.zeros(102400,dtype=np.float32)
        base[:128]=rng.normal(size=128)*.05
        for f in range(3):
            for vi,val in enumerate(values):
                row=base.copy()
                row[65*1024+12]=float(val)*.15
                x[g,f,vi]=row
                logit,hidden=causal._infer(torch,head,row,device='cpu',trace=True)
                h1[g,f,vi]=hidden
                y[g,f,vi,0]=causal._inverse_signed_log(logit)
        neg[g,0]=y[g,0,8,0]
    counts=np.full((8,3,17),27,dtype=np.int32)
    np.savez_compressed(capture/'activations.npz',qwen_final_100=x,
                        c0_hidden_1=h1,c0_answer=y,token_counts=counts,values=values,
                        minus_zero__c0_answer=neg)
    (capture/'interventions.jsonl').write_text(''.join(json.dumps({
        'context':i,'form':'minus_zero','token_count':27})+'\n' for i in range(8)))
    (capture/'final_report.json').write_text(json.dumps({
       'schema':distance.SCHEMA,'status':'COMPLETE_READ_ONLY',
       'source_run':str(source),'expanded_run':str(expanded),
       'checkpoint':'best','checkpoint_step':500,
       'contexts':[{'game_id':g} for g in context_names]}))
    saved={p.name:causal._hash(p) for p in capture.iterdir()}
    localization=tmp_path/'localization';localization.mkdir()
    groups=distance._group_folds(context_names,folds=4,seed=47)
    qfold=[];hfold=[]
    for tr,te in groups:
        qfold.append({'train_games':len(tr),'test_games':len(te),
                      'top_blocks':[65,71,72,73],'nested_top1_tile':0})
        hfold.append({'top_blocks':[0,1,2,3],'nested_top1_tile':0})
    (localization/'final_report.json').write_text(json.dumps({
       'schema':info.SCHEMA,'status':'COMPLETE_READ_ONLY','source_capture':str(capture),
       'source_sha256':saved,'seed':47,'folds':4,
       'by_stage':{'qwen_final_100':{'primary_candidate':True,'arms':{
                   'unseen_games_values_words':{'fold_details':qfold,
                      'top_block_selection_frequency':{'65':4,'71':2,'72':2,'73':1}}}},
                   'c0_hidden_1':{'arms':{'unseen_games_values_words':{'fold_details':hfold}}}}}))
    monkeypatch.setattr(causal,'_load_head',lambda e,c,device:(torch,head,{},e/'best_model.pt'))
    result=causal.run_causal(localization,device='cpu',max_pairs=12,top=1,
                             controls=1,permutations=0,skip_certified=True)
    assert result['status']=='COMPLETE_READ_ONLY'
    assert result['zero_line_gate']['status']=='BLOCKED_NO_CERTIFIED_DEV'
    assert result['synthetic_counterfactual_influence']['selection']['selected']==12
    assert result['zero_spelling_invariance']['forms']['word_zero']['cases']==8
    assert all(causal._hash(p)==saved[p.name] for p in capture.iterdir())
    assert (tmp_path/'localization-causal-path-0001'/'final_report.json').is_file()
