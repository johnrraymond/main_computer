"""G1/G2-0052 source-level acceptance tests for raw neural endpoint repair."""
import json
import hashlib
import gzip

import pytest
import torch

from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as e
from tools import nanojev_gamer_v1_g2_candidate_construction_repair as c
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

A=(0,5,'actor')
B=(6,10,'action')
C=(11,17,'action')
E=(18,22,'outcome')
ALLOW={('owns','actor','action'),('refers_to','action','action'),
       ('measures','action','outcome')}


def fixture():
    # Exactly one token per word, including a low-margin action candidate.
    prompt='alpha beta gamma delta'
    offsets=[(0,5),(6,10),(11,16),(17,22)]
    prepared={'offsets':offsets,'prompt_start':0,'prompt':prompt}
    logits=torch.full((4,2,len(SPAN_KINDS)+1),-10.,dtype=torch.float32)
    logits[...,0]=0.0
    valid=torch.zeros((4,2),dtype=torch.bool)
    valid[:,0]=True
    logits[0,0,SPAN_KINDS.index('actor')+1]=4.0
    logits[1,0,SPAN_KINDS.index('action')+1]=3.0
    logits[2,0,SPAN_KINDS.index('action')+1]=-0.2  # invisible to prior positive catalog
    logits[3,0,SPAN_KINDS.index('outcome')+1]=2.0
    raw={'span_logits':logits,'span_valid':valid,'prepared':prepared,'allowed':ALLOW,
         'author_graph':'MUST_NEVER_BE_READ'}
    # Recovered G2-0051 catalog includes only A, B (not the low-margin gamma).
    parent={'expanded_anchors':[A,B], 'recovered_typed':{(A,B,'owns')}}
    return raw,parent


def test_low_margin_neural_class_can_reach_g2():
    raw,parent=fixture()
    got=e.construct(raw,parent)
    assert (11,16,'action') in got['anchors']
    assert (A,(11,16,'action'),'owns') in got['typed_edges']
    assert got['typed_edges']>=parent['recovered_typed']
    raw['author_graph']={'gold':[('wrong',)]}
    got2=e.construct(raw,parent)
    assert got2['anchors']==got['anchors']


def test_directed_relations_not_injected_from_gold():
    raw,parent=fixture();got=e.construct(raw,parent)
    assert ((11,16,'action'),A,'owns') not in got['typed_edges']
    assert (A,(11,16,'action'),'owns') in got['typed_edges']
    assert not any(edge[0]==edge[1] for edge in got['typed_edges'])


def test_unicode_punctuation_normalization_never_hardcodes_actor_names():
    assert e._punct_trim((0,8,'action'),' (A-B)! ') == (2,5,'action')
    assert e._punct_trim((0,5,'action'),' (A) ') == (2,3,'action')


def test_raw_neural_logits_schema_fails_closed():
    raw,parent=fixture()
    raw['span_logits']=torch.zeros((4,2,3))
    with pytest.raises(ValueError,match='RAW_G1_SHAPE'):e.construct(raw,parent)


def test_audit_distinguishes_neural_rank_from_unrepresentable_boundary(monkeypatch):
    raw,parent=fixture();got=e.construct(raw,parent)
    gold={(A,(11,16,'action'),'owns'),(B,(11,16,'action'),'refers_to')}
    rec={'row':{'prompt':'alpha beta gamma delta'}}
    monkeypatch.setattr(c.SOURCE.base,'gold_binding_edges',lambda row:gold)
    prior={'missing_gold_edges':{'recovered_typed':2}}
    a=e.audit(rec,parent,got,prior)
    assert a['reachable'] and a['newly_found']==1
    assert a['newly_found_by_kind']=={'action':1}
    assert a['still_missing']==[]
    assert a['prior_missing_endpoints'][0]['surface']=='gamma'
    assert a['prior_missing_endpoints'][0]['recovered_by_raw_g1']


def test_audit_labels_do_not_change_generated_candidates(monkeypatch):
    raw,parent=fixture();got=e.construct(raw,parent)
    gold={(A,(99,100,'action'),'owns')}
    monkeypatch.setattr(c.SOURCE.base,'gold_binding_edges',lambda row:gold)
    audit=e.audit({'row':{'prompt':'alpha beta gamma delta'}},parent,got,
        {'missing_gold_edges':{'recovered_typed':1}})
    assert audit['missing_by_reason']=={'no_exact_token_span_boundary':1}
    assert audit['missing_gold_edges']==1
    assert audit['prior_missing_endpoints'][0]['diagnosis']=='no_exact_token_span_boundary'


def test_immutability_parent51_must_reproduce(tmp_path):
    folder=tmp_path/'parent51';folder.mkdir()
    with gzip.open(folder/c.LEDGER,'wt') as stream:stream.write('{}\n')
    r={'schema':c.SCHEMA,'status':c.STATUS,
        'candidate_generation_uses_gold':False,'production_promotion':False,
        'parent50_replay_diagnostic':{'exact_parent50_legacy_replay':True},
        'results':{'counterfactual_probe':{
            'reachable_graphs':{'recovered_typed':8},
            'both_reachable_pairs':{'recovered_typed':1}}}}
    r['ledger_sha256']=hashlib.sha256((folder/c.LEDGER).read_bytes()).hexdigest()
    (folder/c.REPORT).write_text(json.dumps(r))
    assert e.verify_parent51(folder)['status']==c.STATUS
    r['results']['counterfactual_probe']['reachable_graphs']['recovered_typed']=9
    (folder/c.REPORT).write_text(json.dumps(r))
    with pytest.raises(ValueError,match='PARENT51_CONTRACT_OR_LEDGER'):
        e.verify_parent51(folder)


def test_summary_group_and_pair_metrics():
    rows=[];pairs={}
    for section in ('counterfactual_train','counterfactual_probe'):
        ps=[]
        for n in range(4):
            sha=section+str(n)
            rows.append({'section':section,'family':'allocation' if n%2==0 else 'encounter',
                'prompt_sha256':sha,'required_endpoints':4,'original_endpoints_present':3,
                'recovered_endpoints_present':3,'g1_catalog_endpoints_present':3,
                'reachable':{x:False for x in ('legacy','unbounded_typed','recovered_typed','recovered_open')},
                'missing_gold_edges':{x:1 for x in ('legacy','unbounded_typed','recovered_typed','recovered_open')},
                'interface_repair':{'reachable':n!=3,'required_endpoints':4,
                    'previous_endpoints_present':3,'repaired_endpoints_present':4,
                    'newly_found_by_kind':{'action':1},'originally_missing_by_kind':{'action':1},
                    'missing_gold_edges':0 if n!=3 else 1,
                    'missing_by_reason':{'no_exact_token_span_boundary':1} if n==3 else {}}})
        ps=[{'sha256':[section+'0',section+'1']},{'sha256':[section+'2',section+'3']}]
        pairs[section]=ps
    got=e.summarize(rows,pairs,{'exact_parent50_legacy_replay':True})
    assert got['sections']['counterfactual_probe']['repaired_reachable']==3
    assert got['sections']['counterfactual_probe']['repaired_both_pairs']==1
    assert got['decision']['coverage_targets_met'] is False


def test_command_is_registered():
    import contextlib,io
    from tools.nanojev_gamer_v1_graph_curriculum import main
    with contextlib.redirect_stdout(io.StringIO()) as out:
        with pytest.raises(SystemExit) as ex:
            main(['frob-g1-g2-endpoint-repair','--help'])
    assert ex.value.code==0
    assert '--candidate51-run' in out.getvalue()
    assert '--device' in out.getvalue()


def test_run_checks_actual_0051_parent_before_launching_expensive_capture(tmp_path,monkeypatch):
    folder=tmp_path/'candidate51';folder.mkdir()
    with gzip.open(folder/c.LEDGER,'wt') as out:out.write('{}\n')
    report={'schema':c.SCHEMA,'status':c.STATUS,
        'candidate_generation_uses_gold':False,'production_promotion':False,
        'parent50_replay_diagnostic':{'exact_parent50_legacy_replay':True},
        'results':{'counterfactual_probe':{'reachable_graphs':{'recovered_typed':8},
            'both_reachable_pairs':{'recovered_typed':1}}}}
    report['ledger_sha256']=hashlib.sha256((folder/c.LEDGER).read_bytes()).hexdigest()
    (folder/c.REPORT).write_text(json.dumps(report))
    seen={}
    def mocked_run(**kwargs):
        seen.update(kwargs)
        return {'results':{'counterfactual_probe':{'reachable_graphs':{'recovered_typed':8},
            'both_reachable_pairs':{'recovered_typed':1}}}}
    monkeypatch.setattr(e.p51,'run',mocked_run)
    e.run(candidate51_run=folder,output=str(tmp_path/'new'),device='cpu')
    assert seen['interface_extension'] is e.construct
    assert seen['interface_audit'] is e.audit
    assert seen['interface_summary'] is e.summarize
    assert seen['alternate_schema']==e.SCHEMA
    assert seen['additional_parent_hashes']['g2_0051_ledger_sha256']==report['ledger_sha256']
    assert 'candidate51_run' not in seen


def test_full_g2_0051_interface_hook_is_optional():
    import inspect
    from tools import nanojev_gamer_v1_g2_completion_bakeoff as source
    assert 'interface_extension' in inspect.signature(c.run).parameters
    hook=inspect.getsource(source.run)
    assert 'include_raw_g1=False' in hook
    # The optional raw-G1 payload is assembled in the capture helper; the
    # runner only installs the helper as its callback.
    capture_hook=inspect.getsource(source._capture_candidate_repair_batch)
    assert "record['g1_raw_interface']" in capture_hook
    assert "pred=head(hidden" not in hook  # no free-floating separate G1 inference
