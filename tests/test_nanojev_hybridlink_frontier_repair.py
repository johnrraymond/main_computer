"""Offline active-frontier proofs: case selection, durable hard bank, strict rollback."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from tools import nanojev_hybridlink_frontier_repair as f
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from test_nanojev_hybridlink_latent_loop_cutover import StubBB, StubTokenizer
from test_nanojev_hybridlink_latent_diagnostics import ContractNumeric


def _chains():
    return [['PROBLEM: (-132300)+(-28589)','ANSWER: -160889'],
            ['PROBLEM: (2304+632)+5','PROBLEM: 2936+5','ANSWER: 2941'],
            ['PROBLEM: ((5+6)+7)+8','PROBLEM: (11+7)+8','PROBLEM: 18+8','ANSWER: 26']]


def _case(rung,chain,correct=True,cos=.9,margin=.2):
    return {'source':chain[0], 'chain':chain,'rung':rung,'correct':correct,
            'final_cosine':cos,'min_detector_margin':margin}


def test_windows_only_real_suffixes_and_seeded_stable_selection():
    chains=_chains()
    all_={'dev':chains,'train':chains}
    first=f.discover_windows(all_,4,8,222,hard_sources=[chains[0][0]])
    assert first==f.discover_windows(all_,4,8,222,hard_sources=[chains[0][0]])
    assert all(ch[-1].startswith('ANSWER: ') and len(ch)==r+2 for r,rows in first.items() for ch in rows)
    assert first[0]
    assert first[2]==[chains[-1]]
    assert first[3]==[] and first[4]==[]
    assert any(ch[0]==chains[0][0] for ch in first[0])
    assert f._number_features(chains[0][0])['max_operand_abs']==132300


def test_hardbank_persists_failures_threats_fragile_repair_and_csv(tmp_path):
    p=tmp_path/'bank.sqlite3';ch=_chains()[0];r=_case(0,ch,correct=False,cos=.2,margin=-.1)
    b=f.EvidenceBank(p)
    for kind in ('failure','threatened','fragile','repaired'):
        b.record(0,ch,kind,0,r,detail={'kind':kind})
    assert b.summary()=={'cases':1,'failures':1,'threatened':1,'repaired':1,'fragile':1,'historical':0}
    assert b.risk(0,ch)>6
    b.export(tmp_path);b.close()
    assert '132300' in (tmp_path/'hard_numbers.csv').read_text()
    assert 'threatened' in (tmp_path/'hard_cases.csv').read_text()
    reopened=f.EvidenceBank(p)
    assert reopened.summary()['cases']==1
    reopened.close()


def test_safety_no_regressions_and_no_quality_erosion():
    a,b=_chains()[:2]
    k1=f._key(0,a);k2=f._key(1,b)
    old={k1:_case(0,a,True,.92),k2:_case(1,b,False,.25)}
    bad={k1:_case(0,a,False,.95),k2:_case(1,b,True,.8)}
    result=f.evaluate_safety(old,bad,.6,.025)
    assert k1 in result['regressions'] and not result['safe']
    subtle={k1:_case(0,a,True,.89),k2:_case(1,b,True,.8)}
    result=f.evaluate_safety(old,subtle,.6,.025)
    assert k1 in result['quality_drops'] and not result['safe']
    good={k1:_case(0,a,True,.91),k2:_case(1,b,True,.8)}
    result=f.evaluate_safety(old,good,.6,.025)
    assert result['safe'] and result['new_repairs']==[k2]
    with pytest.raises(RuntimeError,match='CASE_SET_DRIFT'):
        f.evaluate_safety(old,{k1:good[k1]},.6,.025)


def test_rehearsal_uses_repeated_threats_not_just_newest(tmp_path):
    bank=f.EvidenceBank(tmp_path/'b.sqlite3')
    ch1,ch2=_chains()[:2]
    bank.record(0,ch1,'threatened',0,_case(0,ch1,True,.9,.01))
    all_rows={f._key(0,ch1):_case(0,ch1,True,.9,.01),
              f._key(0,ch2):_case(0,ch2,True,.9,.4)}
    result=f.choose_rehearsal(all_rows,bank,1,.6)
    assert result[0][1]['source']==ch1[0]
    bank.close()


def test_historical_retention_examples_are_seeded(tmp_path):
    hist=[{'retention':{'lost_sources':['PROBLEM: 2304+632']}},
          {'retention':{'lost_sources':['PROBLEM: 2304+632','PROBLEM: 2+3']}}]
    (tmp_path/'history.json').write_text(json.dumps(hist))
    assert f.historical_t00_sources(tmp_path)==['PROBLEM: 2+3','PROBLEM: 2304+632']


def test_actual_generated_rollout_with_posthoc_grading(monkeypatch):
    bb=StubBB();tok=StubTokenizer();link=loop.make_link(8,8);num=ContractNumeric()
    class NoAnswer(torch.nn.Module):
        def forward(self,h):return h[:,:1]*0-10
    chain=['PROBLEM: (2+3)+4','PROBLEM: 5+4','ANSWER: 9']
    refs={'PROBLEM: 5+4':torch.randn(8),'ANSWER: 9':torch.randn(8)}
    args=SimpleNamespace(device='cpu',max_tokens=128,answer_threshold=.9,
                         drift_threshold=1e-8,stall_patience=2,overrun_steps=1)
    observed=[]
    original=f.diag.build_trace
    def spy(bb,tok,link,det,numeric,chain,*extra):
        observed.append(chain)
        return original(bb,tok,link,det,numeric,chain,*extra)
    monkeypatch.setattr(f.diag,'build_trace',spy)
    row=f.evaluate_one(bb,tok,link,NoAnswer(),num,chain,refs,args,64.)
    assert observed==[['PROBLEM: (2+3)+4']]
    assert row['expected_steps']==2 and not row['correct']
    assert row['history'][0]['gold_target']=='PROBLEM: 5+4'
    assert bb.lm_head.weight.grad is None


def test_checkpoint_input_rejects_provisional_and_wrong_schema(monkeypatch):
    import tools.nanojev_hybridlink_t00_curriculum_repair as t00
    monkeypatch.setattr(t00,'_verified_link_checkpoint',lambda *a:{'training_schema':f.t01.SCHEMA,
                                                                   'accepted_rung':None,'status':'PROVISIONAL_T01'})
    with pytest.raises(RuntimeError,match='ACCEPTED_T01'):
        f.verify_input('fake.pt',SimpleNamespace(),{})


def test_preview_does_not_load_qwen_and_reports_missing_depth(monkeypatch):
    monkeypatch.setattr(f,'_get_chains',lambda args:{'train':_chains(),'dev':_chains()})
    args=SimpleNamespace(max_rung=4,cases_per_rung=5,seed=42)
    result=f.preview(args)
    assert result['missing_rungs']==[3,4]
    assert result['checkpoint_loaded'] is False


def test_cpu_end_to_end_accepted_t01_provenance_and_frontier_gate(monkeypatch,tmp_path):
    from test_nanojev_hybridlink_t01_retention_cutover import _fake_two_hop,_success
    from test_nanojev_hybridlink_latent_loop_cutover import _allow_latent_embeddings
    from tools import nanojev_hybridlink_t00_curriculum_repair as t00
    from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
    from tools import nanojev_hybridlink_t01_retention_cutover as t01
    _allow_latent_embeddings(monkeypatch)
    a=_fake_two_hop(monkeypatch,tmp_path)
    frozen.train(a)
    a.detector_checkpoint=str(Path(a.output)/'best.pt')
    a.output=str(tmp_path/'t00');a.steps=1;a.eval_every=1;a.eval_cases=2
    a.min_gate_cases=1;a.min_steps_per_rung=1;a.max_unroll=2;a.max_rung=0
    a.lr=.001;a.completion_weight=.2;a.seed=17;a.max_depth=3
    a.drift_threshold=1e-8;a.stall_patience=2
    a.min_exit_rate=.5;a.min_gold_exit_rate=.5;a.min_gold_cosine=0.
    a.min_source_gain=0.;a.min_zero_gain=0.;a.max_early_stops=0;a.warm_start_loop=None
    baseline={'cases':2,'correct_stop_depth':2,'answer_exits':2,'early_false_stops':0,
              'generated_exit_rate':1.,'gold_exit_rate':1.,'mean_generated_to_gold_cosine':.9,
              'mean_gain_over_source_cosine':.2,'mean_intact_minus_zero_cosine':.15}
    with monkeypatch.context() as m:
        m.setattr(t00,'t00_eval',lambda *xs:(baseline,[]))
        start=t00.train(a)
    a.warm_start_t00=start['checkpoint'];a.output=str(tmp_path/'t01');a.steps=1
    a.min_t01_steps=1;a.t00_cases=2;a.max_t00_cosine_drop=1.
    a.min_t01_exit_rate=.5;a.min_t00_retention_rate=.01
    a.min_intermediate_cosine=-1.;a.min_final_cosine=-1.;a.min_zero_gain=-1.
    a.t00_replay_fraction=.25;a.checkpoint=None
    accepted=dict(_success(),cases=2,correct_stop_depth=2,first_step_continued=2)
    accepted.update({'answer_exits':2,'early_false_stops':0,'mean_first_step_gold_cosine':.8,
                     'reference_used_only_after_generation':True,'geometry_feedback_causal':False})
    with monkeypatch.context() as m:
        m.setattr(t00,'t00_eval',lambda *xs:(baseline,[{'source':'a','generated_exit':True,'gold_exit':True},
                                                  {'source':'b','generated_exit':True,'gold_exit':True}]))
        m.setattr(t01,'evaluate_t01',lambda *xs:(accepted,[]))
        won=t01.train(a)
    assert won['accepted_rung']=='T01'
    a.start_t01=won['checkpoint'];a.output=str(tmp_path/'frontier');a.stage='run'
    a.max_rung=2;a.cases_per_rung=24;a.repair_cycles=0;a.repair_steps=1
    a.check_every=1;a.quick_guards=1;a.replay_limit=2;a.overrun_steps=0
    a.lr=.00005;a.l2=.01;a.preserve_weight=2.;a.scales=[.125]
    a.clip=1.;a.min_gold_cosine=-1.;a.max_cosine_drop=.025
    a.fragile_margin=.05
    outcome=f.run(a)
    assert outcome['source_accepted_rung']==1
    assert outcome['status'] in ('stalled','target_reached','no_recorded_chains')
    assert (Path(a.output)/'hard_cases.sqlite3').exists()
    assert (Path(a.output)/'hard_numbers.csv').exists()
    assert Path(won['checkpoint']).exists()


def test_live_cpu_repair_rejects_all_candidates_when_frozen_detector_cannot_exit(tmp_path):
    # Real differentiable Qwen-stub feedback, frozen detector always says CONTINUE.
    # The repair optimizer is not allowed to fake a successful promotion.
    from test_nanojev_hybridlink_latent_loop_cutover import AlwaysDetector
    torch.manual_seed(101)
    bb=StubBB();tok=StubTokenizer();link=loop.make_link(8,8)
    det=AlwaysDetector(-10);num=ContractNumeric()
    for mod in (bb,det,num):mod.requires_grad_(False)
    ch=['PROBLEM: 5+1','ANSWER: 6']
    refs={ch[0]:torch.randn(8),ch[1]:torch.randn(8)}
    a=SimpleNamespace(device='cpu',max_tokens=128,completion_weight=.2,
        answer_threshold=.9,drift_threshold=1e-8,stall_patience=2,overrun_steps=0,
        replay_limit=4,min_gold_cosine=-1.,seed=21,lr=.0005,
        repair_steps=1,check_every=1,quick_guards=1,scales=[.125],
        preserve_weight=1.,l2=.01,clip=1.,max_cosine_drop=.025)
    cases=[(0,ch)];bank=f.EvidenceBank(tmp_path/'hard.sqlite3')
    current=f._audit_current(bb,tok,link,det,num,cases,refs,a,64.)
    assert f._counts(current,-1.)['incorrect']==1
    before={k:v.detach().clone() for k,v in link.state_dict().items()}
    trial,after,accepted=f.attempt_repair(bb,tok,link,det,num,cases,current,{},refs,a,64.,bank,0,0)
    assert trial is None and accepted is None and after is current
    assert all(torch.equal(before[k],v) for k,v in link.state_dict().items())
    assert not any(p.grad is not None for p in bb.parameters())
    bank.close()


def test_resume_restores_exact_prior_cases_and_first_success_baseline(tmp_path):
    p=tmp_path/'prior';p.mkdir()
    ch=_chains()[0]
    (p/'selected_cases.json').write_text(json.dumps({'0':[ch],'1':[]}),encoding='utf8')
    row=_case(0,ch,True,.92)
    old_audit={'schema':f.SCHEMA,'rung':0,'cycle':0,'cases':[row]}
    later={**row,'final_cosine':.90}
    (p/'audit_history.jsonl').write_text(json.dumps(old_audit)+'\n'+json.dumps(
       dict(old_audit,cycle=1,cases=[later]))+'\n',encoding='utf8')
    (p/'latest.pt').write_bytes(b'checkpoint-key-only')
    chosen={0:[],1:[],2:[]}
    selected,restored=f._load_prior_selected(p/'latest.pt',chosen,2)
    assert restored and selected[0]==[ch]
    anchors=f._load_prior_successes(p/'latest.pt',.6)
    assert anchors[f._key(0,ch)]['final_cosine']==.92


def test_prior_hard_bank_backups_without_modifying_source(tmp_path):
    old=tmp_path/'old';old.mkdir();new=tmp_path/'new'
    b=f.EvidenceBank(old/'hard_cases.sqlite3')
    ch=_chains()[0];b.record(0,ch,'historical_loss',0)
    b.close()
    first=(old/'hard_cases.sqlite3').read_bytes()
    assert f._import_previous_bank(old/'latest.pt',new)
    assert first==(old/'hard_cases.sqlite3').read_bytes()
    newer=f.EvidenceBank(new/'hard_cases.sqlite3')
    assert newer.summary()['historical']==1
    newer.close()
