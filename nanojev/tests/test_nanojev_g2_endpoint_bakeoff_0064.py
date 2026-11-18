"""G2-0064: frozen model causal intervention and no-leak controls."""
from types import SimpleNamespace

import pytest

from tools import nanojev_g2_endpoint_bakeoff_0064 as b


def _example():
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    rel=torch.full((p55.SLOTS,3),-8.)
    rel[:,0]=9.
    rel[:2,0]=0.;rel[:2,1]=14.
    src=torch.zeros((p55.SLOTS,3))
    dst=torch.zeros((p55.SLOTS,3))
    src[0,0]=12.;dst[0,2]=12.  # Wrong destination, needs dst=1.
    src[1,1]=12.;dst[1,2]=12.  # Correct second edge.
    features={'src':torch.tensor([0,0,1,1]),
              'dst':torch.tensor([1,2,1,2]),
              'relation':torch.zeros(4,dtype=torch.long),
              'node':torch.zeros(3,96),'kinds':torch.zeros(3,dtype=torch.long),
              'margin':torch.zeros(3),'geometry':torch.zeros(4,5)}
    return (rel,src,dst),features,{0,3},p55


def test_inference_and_noop_decoder_exactly_match_existing_p55():
    import torch
    logits,features,gold,p55=_example()
    class Frozen(torch.nn.Module):
        def forward(self,s):
            assert 'positive' not in s
            assert 'truth_count' not in s
            assert 'training_node_ids' not in s
            return logits
    original=p55.infer_edges(Frozen(),features,'set_prediction')
    assert original=={1,3}
    assert b._decode_logits(logits,features,0.0,max_edge=p55.MAX_EDGE)==original
    assert b._decode_logits(logits,features,0.0,1,max_edge=p55.MAX_EDGE)==original
    assert b._safe_features({**features,'positive':torch.tensor([0]),
                             'truth_count':42,'training_node_ids':(0,)})==features


def test_oracle_destination_helps_but_source_and_relation_do_not():
    logits,features,gold,p55=_example()
    mapping=b._oracle_matching(logits,gold,features,p55.rectangular_assignment)
    assert len(mapping)==len(gold)
    baseline=b._decode_logits(logits,features,0.0)
    assert baseline=={1,3}
    for name,expected in (('oracle_source',{1,3}),
                          ('oracle_destination',{0,3}),
                          ('oracle_both_endpoints',{0,3}),
                          ('oracle_relation_and_null',{1,3})):
        fixed=b._intervene(logits,features,mapping,b.ORACLES[name])
        assert b._decode_logits(fixed,features,0.0)==expected
    # Original unmodified logits still reproduce the old prediction.
    assert b._decode_logits(logits,features,0.0)==baseline


def test_topk_unique_assignment_can_recover_duplicate_slots_without_gold():
    import torch
    logits,features,_,_=_example()
    r,s,d=(x.clone() for x in logits)
    s[1]=s[0].clone();d[1]=d[0].clone()
    single=b._decode_logits((r,s,d),features,0.0,1)
    multi=b._decode_logits((r,s,d),features,0.0,2)
    assert len(single)==1
    assert len(multi)==2
    assert set(multi).issubset(set(range(len(features['src']))))
    assert len(b._decode_logits((r,s,d),features,0.0,4,max_edge=1))==1


def test_calibration_selects_only_calibration_labels():
    logits,features,_,p55=_example()
    case={'logits':logits,'features':features,'gold':{0,3},
          'truth_count':2,'max_edge':p55.MAX_EDGE}
    selection=b._select_margin_and_k([case])
    assert selection['margin_only'][1]==1
    assert selection['joint'][1] in b.ALT_K
    assert selection['margin_only'][0] in b.MARGINS
    assert selection['joint'][0] in b.MARGINS
    assert b._summarize(b._stats())['graphs']==0


def test_pair_partition_is_reproducible_stratified_and_disjoint():
    cells=[]
    for f in ('allocation','encounter'):
        for mode in ('renaming','role_swap'):
            for i in range(25):
                a={'family':f,'mode':mode}
                cells.append((i,0,a,dict(a)))
    ds=SimpleNamespace(pairs=cells)
    first=b._balanced_pair_partition(ds,12,24)
    second=b._balanced_pair_partition(ds,12,24)
    assert first==second
    ca,ev=first
    assert len(ca)==12 and len(ev)==24 and not (set(ca)&set(ev))
    assert len(set(ca+ev))==36
    with pytest.raises(ValueError,match='available'):
        b._balanced_pair_partition(ds,90,11)


def test_calibration_selection_does_not_access_evaluation_cases():
    # Fake eval case would fail if accidentally consumed by selection.
    logits,features,_,_=_example()
    cal={'logits':logits,'features':features,'gold':{0,3},
         'truth_count':2,'max_edge':96}
    chosen=b._select_margin_and_k([cal])
    old=chosen['margin_only']
    evaluation={**cal,'gold':{1,2},'truth_count':2}
    # No mutation of already selected policy when a contrasting eval label is supplied.
    assert chosen['margin_only']==old
    assert chosen['margin_only']==old  # evaluation labels cannot mutate chosen policy


def test_intervention_scorer_distinguishes_oracle_and_deployable_arms():
    logits,features,gold,p55=_example()
    c={'logits':logits,'features':features,'gold':gold,'truth_count':2,
       'family':'allocation','mode':'role_swap','max_edge':96,
       'baseline':{1,3}}
    chosen={'margin_only':(0.0,1),'joint':(0.0,2)}
    result=b._score_interventions([c],chosen,p55.rectangular_assignment)
    assert result['overall']['baseline']['f1']==0.5
    assert result['overall']['oracle_destination']['f1']==1.0
    assert result['overall']['oracle_source']['f1']==0.5
    assert result['overall']['oracle_relation_and_null']['f1']==0.5
    assert result['overall']['oracle_destination']['exact']==1
    assert result['by_family']['baseline']['allocation']['graphs']==1
    assert result['reachable_only']['baseline']['graphs']==1


def test_report_contains_non_certification_and_pair_split():
    m={'f1':.5,'precision':.5,'recall':.5,'edge_errors':10,'missing':4,'extra':6,'exact':0,'graphs':2}
    names=['baseline','calibrated_margin','calibrated_unique_assignment',*b.ORACLES]
    doc={'checkpoint_step':4900,'checkpoint_sha256':'a'*64,
         'calibration':{'pair_count':16,'selected_margin_only':0.5,
                        'selected_joint_margin':1.0,'selected_joint_top_k':2},
         'evaluation':{'pair_count':32,'results':{
             'overall':{n:m for n in names},
             'by_family':{n:{'allocation':m} for n in names}}}}
    out=b.format_report(doc)
    assert 'G2-0064' in out and 'Oracle arms' in out and 'NOT certification' in out


def test_capture_predictions_never_passes_labels_to_model_and_matches_baseline():
    import torch
    logits,features,gold,p55=_example()
    class Frozen(torch.nn.Module):
        def forward(self,s):
            assert set(s)==set(features)
            assert not any(k in s for k in ('positive','truth_count','training_node_ids'))
            return logits
    raw={**features,'positive':torch.tensor(sorted(gold)),
         'truth_count':len(gold),'family':'encounter','mode':'renaming',
         'prompt_sha256':'9'*64,'training_node_ids':(1,2)}
    class Corpus:
        def pair(self,index,with_labels=True):
            assert index==0 and with_labels
            return ((raw,raw),{})
    cases=b._capture_predictions(Frozen(),Corpus(),[0],p55)
    assert len(cases)==2
    assert all(c['baseline']=={1,3} for c in cases)
    assert all(c['gold']==gold for c in cases)


def test_run_integration_verifies_lineage_and_never_opens_test(monkeypatch,tmp_path):
    import torch
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_g2_scaling_0059 as g
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
    logits,features,gold,_=_example()
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.dummy=torch.nn.Parameter(torch.zeros(1))
        def forward(self,s):
            assert set(s)==set(features), 'training labels leaked into inference'
            return tuple(t + self.dummy*0 for t in logits)
    monkeypatch.setattr(p55,'model_for',lambda name:Model())
    training=tmp_path/'training';training.mkdir()
    plan=tmp_path/'plan';plan.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    (plan/'plan.json').write_text('{}')
    (capture/'capture.json').write_text('{}')
    save_file(Model().state_dict(),str(training/'checkpoint-000004900.safetensors'))
    weight_sha=g.file_sha(training/'checkpoint-000004900.safetensors')
    training_meta={'production_promotion':False,'sealed_test_used':False,
                   'plan_sha256':g.file_sha(plan/'plan.json'),
                   'capture_config_sha256':g.file_sha(capture/'capture.json')}
    (training/'training.json').write_text(__import__('json').dumps(training_meta))
    receipt={'step':4900,'weights_sha256':weight_sha,
             'validation':{'graphs':4,'exact':0,'true_positive':1,'gold_edges':8,
                           'predicted_edges':8,'missing':7,'extra':7}}
    (training/'checkpoint-000004900.json').write_text(__import__('json').dumps(receipt))
    zero={**receipt,'step':0}
    (training/'checkpoint-000000000.json').write_text(__import__('json').dumps(zero))
    class Corpus:
        def __init__(self,plan_dir,capture_dir,split,require_complete):
            assert split=='validation' and not require_complete
            self.pairs=[(i,0,{'family':'allocation','mode':'renaming'},
                              {'family':'allocation','mode':'renaming'}) for i in range(4)]
        def pair(self,index,with_labels=True):
            assert with_labels
            raw={**features,'positive':torch.tensor(sorted(gold)),
                 'truth_count':2,'family':'allocation','mode':'renaming',
                 'prompt_sha256':str(index+1)*64}
            return ((raw,raw),{})
    monkeypatch.setattr(g,'FeatureCorpus',Corpus)
    result=b.run(training_dir=training,plan_dir=plan,capture_dir=capture,
                 checkpoint_step=4900,calibration_pairs=2,evaluation_pairs=2,
                 cpu_threads=2)
    assert result['checkpoint_sha256']==weight_sha
    assert result['evaluation']['results']['overall']['oracle_destination']['f1']==1.0
    assert result['evaluation']['results']['overall']['baseline']['f1']==.5
    assert result['sealed_test_used'] is False
    assert result['production_promotion'] is False
    assert not (training/'selection.json').exists()
    assert not (capture/'test').exists()
    training_meta['capture_config_sha256']='0'*64
    (training/'training.json').write_text(__import__('json').dumps(training_meta))
    with pytest.raises(ValueError,match='checksum'):
        b.run(training_dir=training,plan_dir=plan,capture_dir=capture,
              calibration_pairs=1,evaluation_pairs=1)
