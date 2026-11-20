"""Read-only, post-hoc G2-0084 forensic attribution regression tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from tools import nanojev_gamer_v1_g2_binding_forensics_0084 as audit


def fixture_rows():
    objects=[{'id':'n0','kind':'actor','span':[0,3]},
             {'id':'n1','kind':'action','span':[4,8]},
             {'id':'n2','kind':'quantity','span':[9,10]}]
    graph={'schema':'nanojev.gamer.graph.v1','nodes':objects,
           'edges':[{'src':'n0','dst':'n1','type':'owns'},
                    {'src':'n1','dst':'n2','type':'measures'}], 'objective':None}
    gold_objects=[{'start':a['span'][0],'end':a['span'][1],'kind':a['kind']}
                  for a in objects]
    anchor=lambda i: [objects[i]['kind'],objects[i]['span']]
    owns=[anchor(0),anchor(1),'owns']
    measures=[anchor(1),anchor(2),'measures']
    wrong=[anchor(1),anchor(2),'owns']
    extra=[anchor(0),anchor(2),'refers_to']
    all_obj=[[a['span'][0],a['span'][1],a['kind']] for a in objects]
    short_obj=all_obj[:2]
    rows=[];pred=[]
    for split in ('dev','challenge'):
        for index in range(2):
            case=f'{split}-{index}'
            digest=hashlib.sha256(case.encode()).hexdigest()
            rows.append({'case_id':case,'split':split,'family':'encounter' if index==0 else 'allocation',
                         'prompt_sha256':digest,'graph':graph,'gold_objects':gold_objects})
            if index==0:
                nodes=all_obj
                modes={'trained':{'binding_edges':[owns,wrong,extra],'g1_objects':nodes},
                       'untrained':{'binding_edges':[owns,measures],'g1_objects':nodes},
                       'no_binding':{'binding_edges':[],'g1_objects':nodes},
                       'zero_hidden':{'binding_edges':[],'g1_objects':nodes}}
            else:
                nodes=short_obj
                modes={'trained':{'binding_edges':[owns],'g1_objects':nodes},
                       'untrained':{'binding_edges':[],'g1_objects':nodes},
                       'no_binding':{'binding_edges':[],'g1_objects':nodes},
                       'zero_hidden':{'binding_edges':[],'g1_objects':nodes}}
            pred.append({'case_id':case,'split':split,'family':rows[-1]['family'],
                         'prompt_sha256':digest,'modes':modes,'surface_count':len(nodes)})
    return rows,pred


def test_g2_0084_attribution_and_edge_sets():
    gold,pred=fixture_rows()
    result,cases=audit.forensic_analysis(gold,pred)
    for split in ('dev','challenge'):
        block=result['splits'][split]
        assert block['cases']==2
        assert block['gold_edges']==4
        assert block['gold_reachable']==3
        assert block['gold_unreachable']==1
        assert block['missed_reachable']==1
        assert block['false_edges']==2
        assert block['wrong_label_for_gold_endpoint_pair']==1
        assert block['extra_endpoint_pairs']==1
        assert block['g1_exact']==1
        assert block['cases_with_all_gold_reachable_and_g2_failure']==1
        assert block['modes']['trained']['tp']==2
        assert block['modes']['trained']['pred']==4
        assert block['modes']['trained']['gold']==4
        assert block['modes']['trained']['f1']==.5
        assert block['paired_vs_untrained']['different_edge_set']==2
        assert block['paired_vs_untrained']['tp_improved']==1
        assert block['paired_vs_untrained']['tp_worsened']==1
        assert block['binding_by_type']['trained']['owns']['tp']==2
    assert len(cases)==4
    assert result['status']=='POST_HOC_DIAGNOSIS_ONLY_NOT_CERTIFICATION'


def test_g2_0084_rejects_unanchored_edge():
    gold,pred=fixture_rows()
    pred[0]['modes']['trained']['binding_edges'].append([['actor',[45,50]], ['action',[4,8]], 'owns'])
    with pytest.raises(ValueError,match='EDGE_WITHOUT_G1_ENDPOINT'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_rejects_mode_g1_drift():
    gold,pred=fixture_rows()
    pred[0]['modes']['untrained']['g1_objects']=pred[0]['modes']['untrained']['g1_objects'][:1]
    with pytest.raises(ValueError,match='EDGE_WITHOUT_G1_ENDPOINT|G1_CHANGED_BETWEEN_BINDING_ARMS'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_rejects_duplicate_prediction_edges():
    gold,pred=fixture_rows()
    pred[0]['modes']['trained']['binding_edges'].append(pred[0]['modes']['trained']['binding_edges'][0])
    with pytest.raises(ValueError,match='DUPLICATE_PREDICTED_BINDING'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_rejects_duplicate_cases():
    gold,pred=fixture_rows()
    pred[1]['case_id']=pred[0]['case_id']
    with pytest.raises(ValueError,match='CASE_BIJECTION'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_rejects_wrong_prompt_commitment():
    gold,pred=fixture_rows()
    pred[0]['prompt_sha256']='mismatch'
    with pytest.raises(ValueError,match='GOLD_PRED_BINDING'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_rejects_bad_split():
    gold,pred=fixture_rows()
    pred[0]['split']='test'
    with pytest.raises(ValueError,match='GOLD_PRED_BINDING|PREDICTION_SCHEMA'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_rejects_duplicate_gold_objects():
    gold,pred=fixture_rows()
    gold[0]['gold_objects'].append(gold[0]['gold_objects'][0])
    with pytest.raises(ValueError,match='GOLD_OBJECT_DUPLICATE'):
        audit.forensic_analysis(gold,pred)


def test_g2_0084_no_prediction_is_not_exact_for_nonempty_gold():
    gold,pred=fixture_rows()
    for row in pred:
        row['modes']['trained']['binding_edges']=[]
    result,_=audit.forensic_analysis(gold,pred)
    assert result['splits']['dev']['modes']['trained']['exact']==0
    assert result['splits']['dev']['modes']['trained']['f1']==0
    assert result['splits']['dev']['cases_with_no_predicted_edges']==2


def test_g2_0084_inputs_not_mutated():
    gold,pred=fixture_rows()
    original=json.dumps((gold,pred),sort_keys=True)
    audit.forensic_analysis(gold,pred)
    assert json.dumps((gold,pred),sort_keys=True)==original
