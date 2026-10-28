"""T01 actual two-hop feedback, frozen lineage, per-case T00-retention gate."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from tools import nanojev_hybridlink_t01_retention_cutover as cut
from tools import nanojev_hybridlink_t00_curriculum_repair as t00
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
from tools import nanojev_qwen_numeric_dual_cutover as parent
from test_nanojev_hybridlink_latent_loop_cutover import StubBB, StubTokenizer, _allow_latent_embeddings
from test_nanojev_hybridlink_latent_diagnostics import ContractNumeric
from test_nanojev_qwen_numeric_dual_cutover import fake_stage1, make_parent, args_for


def test_t01_windows_requires_two_real_hops_and_ends_in_answer():
    ch=[['PROBLEM: 2+3','ANSWER: 5'],
        ['PROBLEM: (2+3)+4','PROBLEM: 5+4','ANSWER: 9'],
        ['PROBLEM: ((2+3)+4)+5','PROBLEM: (5+4)+5','PROBLEM: 9+5','ANSWER: 14']]
    w=cut.t01_windows(ch)
    assert len(w)==2
    assert w[0]==ch[1]
    assert w[1]==ch[2][-3:]
    assert not cut.t01_windows([ch[0]])


def _gate_args():
    return SimpleNamespace(min_t01_steps=100,min_gate_cases=12,min_t01_exit_rate=.75,
       min_intermediate_cosine=.5,min_final_cosine=.7,min_zero_gain=.01,
       max_t00_cosine_drop=.025,min_t00_retention_rate=1.)


def _success():
    return {'cases':12,'correct_stop_depth':12,'early_false_stops':0,
       'first_step_continued':12,'mean_first_step_gold_cosine':.7,
       'mean_final_gold_cosine':.85,'mean_intact_minus_zero_cosine':.2}


def test_gate_requires_early_continue_and_late_answer_and_retention():
    a=_gate_args();m=_success();ret={'retention_pass':True}
    assert cut.gate_t01(m,ret,100,a)==(True,[])
    assert 'MIN_T01_STEPS_NOT_MET' in cut.gate_t01(m,ret,50,a)[1]
    assert 'T00_RETENTION_REGRESSED' in cut.gate_t01(m,{'retention_pass':False},100,a)[1]
    assert 'T01_EARLY_FALSE_STOPS' in cut.gate_t01(dict(m,early_false_stops=1),ret,100,a)[1]
    assert 'INSUFFICIENT_TWO_HOP_CORRECT_EXITS' in cut.gate_t01(dict(m,correct_stop_depth=3),ret,100,a)[1]
    assert 'INTERMEDIATE_FALSE_COMPLETION' in cut.gate_t01(dict(m,first_step_continued=2),ret,100,a)[1]
    assert 'NO_TWO_HOP_ADVANTAGE_OVER_ZERO' in cut.gate_t01(dict(m,mean_intact_minus_zero_cosine=0.),ret,100,a)[1]


def test_retention_checks_each_baseline_case_not_only_aggregate():
    a=_gate_args()
    old=[{'source':'a','generated_exit':True,'gold_exit':True},
         {'source':'b','generated_exit':True,'gold_exit':True}]
    cur=[{'source':'a','generated_exit':False,'gold_exit':True},
         {'source':'b','generated_exit':True,'gold_exit':True}]
    b={'mean_generated_to_gold_cosine':.83};c={'mean_generated_to_gold_cosine':.84,'generated_exit_rate':.5}
    r=cut.t00_regressions(old,cur,b,c,a)
    assert not r['retention_pass'] and r['lost_sources']==['a']
    restored=[dict(x) for x in old]
    assert cut.t00_regressions(old,restored,b,dict(c,generated_exit_rate=1.),a)['retention_pass']
    assert not cut.t00_regressions(old,restored,b,dict(c,mean_generated_to_gold_cosine=.7,generated_exit_rate=1.),a)['retention_pass']
    with pytest.raises(RuntimeError,match='CASE_DRIFT'):
        cut.t00_regressions(old,old[:-1],b,c,a)


def test_generator_receives_only_start_and_runs_two_real_decoder_steps(monkeypatch):
    import tools.nanojev_hybridlink_latent_diagnostics as diagnostic
    bb=StubBB();tok=StubTokenizer();link=loop.make_link(8,8)
    class Never(torch.nn.Module):
        def forward(self,x):return x[:,0:1]*0.-10
    det=Never();num=ContractNumeric()
    observed=[]
    original=diagnostic.build_trace
    def spy(bb,tok,link,det,numeric,chain,*args):
        observed.append(chain)
        return original(bb,tok,link,det,numeric,chain,*args)
    monkeypatch.setattr(diagnostic,'build_trace',spy)
    trace=cut._run_one(bb,tok,link,det,num,'PROBLEM: (2+3)+4','cpu',128,64.,.9,1e-8,2)
    assert trace['steps']==2 and trace['status']=='BUDGET_EXHAUSTED'
    assert observed==[['PROBLEM: (2+3)+4']]
    assert bb.lm_head.weight.grad is None


def _fake_two_hop(monkeypatch,tmp_path):
    fake=fake_stage1(monkeypatch)
    def deck(seed,train_count,dev_count,ood_count,test_count):
        sets={}
        for label,count,offset in (('train',train_count,0),('dev',dev_count,1000),
                                   ('ood',ood_count,2000),('test',test_count,3000)):
            if count%2:raise ValueError('need full pairs')
            rows=[]
            for k in range(count//2):
                a=offset+5*k+3;b=2*k+6;c=7+k
                rows.append({'source':f'PROBLEM: ({a}+{b})+{c}',
                             'target':f'PROBLEM: {a+b}+{c}','action':'PROBLEM'})
                rows.append({'source':f'PROBLEM: {a+b}+{c}',
                             'target':f'ANSWER: {a+b+c}','action':'ANSWER'})
            sets[label]=rows
        return sets
    fake.make_deck=deck
    text,stage1,_=make_parent(tmp_path,fake)
    a=args_for(tmp_path,text,stage1)
    a.output=str(tmp_path/'detector');a.cache_dir=str(tmp_path/'cache')
    a.steps=2;a.eval_every=1;a.batch_size=4;a.numeric_weight=.3;a.numeric_lr=.002
    a.answer_threshold=.75
    return a


def test_cpu_training_no_false_promotion_and_parent_immutability(monkeypatch,tmp_path):
    _allow_latent_embeddings(monkeypatch)
    a=_fake_two_hop(monkeypatch,tmp_path)
    frozen.train(a)
    a.detector_checkpoint=str(Path(a.output)/'best.pt')
    a.output=str(tmp_path/'t00')
    a.steps=1;a.eval_every=1;a.eval_cases=2;a.min_gate_cases=1
    a.min_steps_per_rung=1;a.max_unroll=2;a.max_depth=3;a.max_rung=0
    a.lr=.001;a.completion_weight=.2;a.seed=17;a.drift_threshold=1e-8;a.stall_patience=2
    a.min_exit_rate=.5;a.min_gold_exit_rate=.5;a.min_gold_cosine=0.
    a.min_source_gain=0.;a.min_zero_gain=0.;a.max_early_stops=0;a.warm_start_loop=None
    # Produce the same trusted checkpoint schema as a successful T00 run.
    synthetic={'cases':2,'correct_stop_depth':2,'answer_exits':2,'early_false_stops':0,
         'generated_exit_rate':1.,'gold_exit_rate':1.,'mean_generated_to_gold_cosine':.9,
         'mean_gain_over_source_cosine':.2,'mean_intact_minus_zero_cosine':.15}
    with monkeypatch.context() as m:
        m.setattr(t00,'t00_eval',lambda *xs:(synthetic,[]))
        result=t00.train(a)
    assert result['accepted_rung']=='T00'
    a.warm_start_t00=result['checkpoint']
    a.output=str(tmp_path/'t01');a.steps=2;a.eval_every=1
    a.eval_cases=2;a.t00_cases=2;a.min_gate_cases=1;a.min_t01_steps=100
    a.min_t01_exit_rate=.5;a.min_t00_retention_rate=.01
    a.max_t00_cosine_drop=1.;a.min_intermediate_cosine=-1.;a.min_final_cosine=-1.
    a.min_zero_gain=-1.;a.t00_replay_fraction=.25
    a.checkpoint=None
    before=(parent.file_hash(a.stage1_checkpoint),parent.file_hash(a.detector_checkpoint))
    # Toy mock Qwen has a random detector; isolate real two-hop execution from
    # the T00 retention policy (which has separate per-case tests above).
    def toy_baseline(*xs):
        return (dict(synthetic),[{'source':'toy-A','generated_exit':True,'gold_exit':True},
                                  {'source':'toy-B','generated_exit':True,'gold_exit':True}])
    monkeypatch.setattr(t00,'t00_eval',toy_baseline)
    outcome=cut.train(a)
    assert outcome['status']=='T01_PROVISIONAL_NOT_PROMOTED'
    assert not (Path(a.output)/'best.pt').exists()
    assert (Path(a.output)/'candidate.pt').exists()
    ck=torch.load(Path(a.output)/'candidate.pt',weights_only=True)
    assert ck['accepted_rung'] is None and ck['trained_generator_only']
    assert 'numeric' not in ck and 'lora' not in ck
    assert before==(parent.file_hash(a.stage1_checkpoint),parent.file_hash(a.detector_checkpoint))
    rows=json.loads((Path(a.output)/'history.json').read_text())
    assert rows and all(not x['gate_pass'] for x in rows)
    assert all(x['t01']['reference_used_only_after_generation'] for x in rows)
    assert all(x['t01']['geometry_feedback_causal'] is False for x in rows)
    # Refuse an unaccepted candidate as the T00 parent.
    a.warm_start_t00=str(Path(a.output)/'candidate.pt')
    a.output=str(tmp_path/'unsafe')
    with pytest.raises(RuntimeError,match='MUST_BE_ACCEPTED_T00'):
        cut.train(a)
    # Positive acceptance path, with a fully simulated DEV gate but actual
    # checkpoint serialization and original Patch3 runtime compatibility.
    a.warm_start_t00=result['checkpoint']
    a.output=str(tmp_path/'t01-accepted')
    a.steps=1;a.min_t01_steps=1
    synthetic_t01=dict(_success(),cases=2,correct_stop_depth=2,first_step_continued=2)
    synthetic_t01.update({'answer_exits':2,'first_step_gold_cosine':.8,
                          'early_false_stops':0,'reference_used_only_after_generation':True,
                          'geometry_feedback_causal':False})
    monkeypatch.setattr(cut,'evaluate_t01',lambda *xs:(synthetic_t01,[]))
    approved=cut.train(a)
    assert approved['accepted_rung']=='T01'
    assert (Path(a.output)/'accepted_T01.pt').exists()
    a.checkpoint=approved['checkpoint']
    cfg,ck=loop._load_runtime_checkpoint(a)
    assert cfg['t01_schema']==cut.SCHEMA and ck['accepted_rung']==1
    assert ck['status']=='T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED'
    assert ck['trained_generator_only'] and 'lora' not in ck and 'numeric' not in ck
    a.stage='inspect';a.control='intact';a.state='PROBLEM: (3+6)+7'
    runtime=loop.evaluate(a)
    assert runtime['headless'] and not runtime['inference_oracle_used']


def test_t01_probe_refuses_arbitrary_checkpoint():
    assert cut.SCHEMA!=t00.SCHEMA
