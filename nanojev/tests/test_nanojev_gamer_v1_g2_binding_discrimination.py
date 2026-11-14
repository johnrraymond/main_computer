"""G2-0003 frozen-score discrimination, prior replay, and holdout isolation."""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_binding_discrimination as d
from tools import nanojev_gamer_v1_g2_binding_selection as s
from tools import nanojev_gamer_v1_g2_binding_bootstrap as b
from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, GamerGraphHead


def _rows():
    return [encounter(random.Random(18),'train',1), allocation(random.Random(40),'train',2)]


def _scores(n, winners):
    m=torch.full((n,n,len(BIND_CLASSES)),-8.0)
    m[:,:,0]=8.0
    for i,j,rel,strength in winners:
        m[i,j,0]=0
        m[i,j,BIND_CLASSES.index(rel)]=strength
    return m


def _case(index=1, family='encounter', prompt='x', extra=False):
    anchors=[(0,1,'actor'),(2,3,'action'),(4,5,'action'),(6,7,'quantity')]
    m=_scores(4,[(0,1,'owns',9.0),(0,2,'owns',8.5),
                 (3,1,'applies_to',9.0),(1,3,'owns',9.0)])
    gold={(anchors[0],anchors[1],'owns'),(anchors[3],anchors[1],'applies_to')}
    if extra:
        gold.add((anchors[0],anchors[2],'owns'))
    return {'index':index,'family':family,'prompt_sha256':b._sha(prompt),
            'anchors':anchors,'logits':m,'gold':gold}


def _allowed():
    return {('owns','actor','action'),('applies_to','quantity','action')}


def test_features_blind_to_gold_and_source_normalized_without_caps():
    a=_case()
    f=d.feature_edges(a,_allowed())
    assert len(f)==3
    own=[r for r in f if r[3]=='owns']
    assert len(own)==2
    assert sum(r[2] for r in own)==pytest.approx(1.0)
    a['gold']=set()
    assert d.feature_edges(a,_allowed())==f
    all_edges=d.edges_for_rule(f, {'mode':'source_share','thresholds':dict.fromkeys(d.RELATIONS,0.0)})
    assert len(all_edges)==3
    assert not any(e[0]==(2,3,'action') for e in all_edges)


def test_type_specific_fit_is_calibration_only_and_does_not_use_gold_for_selection():
    a=_case();a['features']=d.feature_edges(a,_allowed())
    for measure in ('confidence','source_share'):
        cuts,ev=d.fit_thresholds([a],measure=measure,beta=2)
        assert set(cuts)==set(d.RELATIONS)
        assert ev['owns']['gold']==1
        assert ev['owns']['candidates']==2
    rules,notes=d.fitted_rules([a])
    assert len(rules)==7
    assert 'dual_relation_source_f2' in rules
    first=d.edges_for_rule(a['features'],rules['source_competition_f2'])
    a['gold']=set()  # fitted inference must be completely blind
    assert d.edges_for_rule(a['features'],rules['source_competition_f2'])==first
    assert 'owns' in notes['relation_confidence_f2']


def test_score_distribution_audit_reports_auc_and_separation():
    a=_case();a['features']=d.feature_edges(a,_allowed())
    audit=d.score_diagnostics([a])
    assert audit['calibration_only'] is True
    assert audit['coverage']['compatible_true']==2
    assert audit['coverage']['compatible_false']==1
    assert audit['score_separation']['overall']['confidence_auc'] is not None
    assert audit['score_separation']['overall']['source_share_auc'] is not None
    assert audit['score_separation']['relation:owns']['false_confidence']['count']==1


def test_quantile_and_auc_small_known_cases():
    assert d._auc([.7,.8],[.2,.3]) == 1.0
    assert d._auc([.2],[.2]) == .5
    assert d._auc([.1],[.5]) == 0.0
    assert d._auc([],[]) is None
    assert d._quantiles([])['median'] is None


def test_replay_0002_detects_tampered_case_and_stats():
    c=_case();allowed=_allowed()
    controls=s.configurations()[0]
    saved=[{'index':1,'family':'encounter','prompt_sha256':c['prompt_sha256'],
            'g1_predicted_nodes':len(c['anchors']),'gold_binding_edges':len(c['gold']),
            'configurations':s._case_scores(c,controls,allowed)}]
    report={'selected_rule':None,'independent_validation_metrics':s._summarize([c],controls,allowed)}
    d.replay_0002([c],saved,report,allowed)
    saved[0]['configurations']['argmax_050']['correct']=77
    with pytest.raises(ValueError,match='REPLAY_PREDICTIONS'):
        d.replay_0002([c],saved,report,allowed)
    saved[0]['configurations']=s._case_scores(c,controls,allowed)
    report['independent_validation_metrics']={}
    with pytest.raises(ValueError,match='REPLAY_METRICS'):
        d.replay_0002([c],saved,report,allowed)


def test_invalid_inputs_unsupported_modes_and_no_trainable_leakage():
    c=_case()
    assert len(d.feature_edges(c,_allowed()))==3
    c['logits'][0,1,0]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE'):
        d.feature_edges(c,_allowed())
    with pytest.raises(ValueError,match='UNKNOWN_RULE'):
        d.edges_for_rule([],{'mode':'wrong'})
    with pytest.raises(ValueError,match='BAD_FIT_SPEC'):
        d.fit_thresholds([],measure='unknown',beta=2)


def test_new_holdout_excludes_all_prior_generations():
    train=_rows()
    first=b.novel_dev_rows(train,limit=4,seed=b.SEED)
    second=s.new_validation_rows(train,first,limit=4)
    third=d.new_validation_rows(train,first,second,limit=6)
    assert len(third)==6
    assert len({b._sha(x['prompt']) for x in train+first+second+third})==len(train+first+second+third)
    assert Counter_(x['family'] for x in third)=={'encounter':3,'allocation':3}


def Counter_(items):
    from collections import Counter
    return dict(Counter(items))


def test_selection_recall_floor_is_strict():
    metrics={'argmax_050':{'recall':.79}}
    for name in ('a','b'):
        metrics[name]={'recall':.55,'f1':.9,'precision':.9,'false_positive':0}
    assert d.choose_calibration_winner(metrics,{'a':{},'b':{}})[0] is None
    metrics['a']['recall']=.68
    assert d.choose_calibration_winner(metrics,{'a':{},'b':{}})[0]=='a'


def test_cli_command_is_direct():
    cwd=Path(__file__).resolve().parents[1]
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-binding-discrimination','--help'],cwd=cwd,capture_output=True,text=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    assert '--device' in proc.stdout and '--selection-run' in proc.stdout


def test_mocked_full_runner_calibration_and_fresh_validation(monkeypatch,tmp_path):
    train=_rows()
    first=b.novel_dev_rows(train,limit=4,seed=b.SEED)
    second=b.novel_dev_rows(train+first,limit=4,seed=s.SEED)
    fresh=b.novel_dev_rows(train+first+second,limit=4,seed=d.SEED)
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4)
    dataset=tmp_path/'dataset.jsonl';dataset.write_text('train',encoding='utf-8')
    g1_report=tmp_path/'g1-report.json';g1_report.write_text('{}')
    g2_run=tmp_path/'g2';g2_run.mkdir()
    selection_run=tmp_path/'g2-0002';selection_run.mkdir()
    manifest={'head_sha256':'mock-g1','model_id':'dummy','source_adapter':'dummy'}
    bootstrap={'train_prompt_hashes_sha256':b._sha('\n'.join(b._sha(r['prompt']) for r in train)),
               'dev_dataset_sha256':d._sha_rows(first)}
    def row_case(row,index):
        return _case(index,row['family'],row['prompt'])
    second_cases=[row_case(r,i) for i,r in enumerate(second,1)]
    controls=s.configurations()[0]
    compat=s.compatibility_from_train(train)
    # Mocked parent reports have the full 0002 replay data.
    saved=[]
    for c in second_cases:
        saved.append({'group':'independent_validation','index':c['index'],
            'family':c['family'],'prompt_sha256':c['prompt_sha256'],
            'g1_predicted_nodes':len(c['anchors']),'gold_binding_edges':len(c['gold']),
            'configurations':s._case_scores(c,controls,compat)})
    report={'schema':s.SCHEMA,'g1_frozen':True,'g2_development_weights_frozen':True,
            'trainable_parameters':0,'g2_certified':False,'g2_production_unlocked':False,
            'production_promotion':False,'certification_authorized':False,
            'graph_execution_authorized':False,'memory_writes':False,'sealed_test_used':False,
            'challenge_or_test_split_used':False,'oracle_input_to_decoder':False,
            'compatibility_source':'author_marked_train_only',
            'parent_bootstrap_replay_pass':True,'calibration_only_rule_selection':True,
            'g1_development_candidate':b.G1_ARM,'validation_seed':s.SEED,
            'validation_cases':len(second),'validation_dataset_sha256':d._sha_rows(second),
            'parent_bootstrap_dev_cases':len(first),'g2_parent_hashes':{'boot':'mock'},
            'decoder_rules_predeclared':{**s.configurations()[0],**s.configurations()[1]},
            'selected_rule':None,'independent_development_screen_pass':False,
            'compatibility_sha256':s._hash_json([list(x) for x in sorted(compat)]),
            'independent_validation_metrics':s._summarize(second_cases,controls,compat)}
    (selection_run/s.REPORT).write_text(json.dumps(report))
    (selection_run/s.CASES).write_text(''.join(json.dumps(r)+'\n' for r in
         ([{'group':'calibration'} for _ in first]+saved)))
    monkeypatch.setattr(d.parent,'load_parents',lambda *a:(head,manifest,dataset,bootstrap,[],{}, {'boot':'mock'}))
    monkeypatch.setattr(d.parent,'TRAIN_LIMIT',len(train))
    monkeypatch.setattr(d.parent,'CALIBRATION_LIMIT',len(first))
    monkeypatch.setattr(d.parent,'VALIDATION_LIMIT',len(second))
    monkeypatch.setattr(d.base,'original_training_rows',lambda *_:train)
    monkeypatch.setattr(d.base,'novel_dev_rows',lambda rows,limit,seed:
        first if seed==b.SEED else (second if seed==s.SEED else fresh))
    monkeypatch.setattr(d.parent,'new_validation_rows',lambda *_a,**_kw:second)
    monkeypatch.setattr(d,'new_validation_rows',lambda *a,**kw:fresh)
    class Frozen:
        def parameters(self):return iter([])
    monkeypatch.setattr(d.backbone,'model_components',lambda *a:(Frozen(),None))
    calls=[]
    def capture(_head,_manifest,_model,_tok,rows,**kw):
        calls.append(kw['group'])
        return [row_case(row,i) for i,row in enumerate(rows,1)]
    monkeypatch.setattr(d.parent,'capture',capture)
    output=tmp_path/'out'
    result=d.run(root=tmp_path,output=output,g1_validation_report=g1_report,
                 g2_run=g2_run,selection_run=selection_run,device='cpu',validation_limit=4)
    assert result['trainable_parameters']==0
    assert result['g2_0002_replay_pass'] is True
    assert result['validation_split']=='new_prompt_disjoint_dev'
    assert len(json.loads((output/d.REPORT).read_text())['decoder_rules_calibration_fitted'])==7
    assert json.loads((output/d.AUDIT).read_text())['calibration_only'] is True
    assert len((output/d.CASES).read_text().splitlines())==4
    assert calls==['g2_0002_replay_calibration','new_independent_validation']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.run(root=tmp_path,output=output,g1_validation_report=g1_report,
              g2_run=g2_run,selection_run=selection_run,device='cpu',validation_limit=4)
    # Change parent report after output has been written; fresh run must fail closed.
    result_path=selection_run/s.REPORT
    changed=dict(report,production_promotion=True)
    result_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='PARENT_CONTRACT:production_promotion'):
        d.run(root=tmp_path,output=tmp_path/'new',g1_validation_report=g1_report,
              g2_run=g2_run,selection_run=selection_run,device='cpu',validation_limit=4)
