from pathlib import Path
import json
import pytest
from tools import nanojev_contract_v3_generalization_eval as ge
from tools import nanojev_contract_v3_make_fresh_challenge as fresh
from tools import nanojev_contract_v3_semantic_bakeoff as bo
from tools import nanojev_contract_v3_semantic_guard as guard
from tools import nanojev_contract_v3_live_head as bridge


def test_fresh_corpus_is_fixed_complete_and_distinct():
    obj,cases,digest=ge.validate_corpus(fresh.OUT)
    assert digest==bo.PINNED_FRESH_SHA256
    assert len(cases)==24
    assert sum(c['expected']=='contract' for c in cases)==14
    assert sum(c['expected']=='needs_clarification' for c in cases)==10
    assert {c['request'] for c in cases}.isdisjoint(
           {c['request'] for c in ge.validate_corpus(ge.DEFAULT_CORPUS)[1]})
    assert fresh.build()==obj


def test_all_fresh_positive_replays_have_complete_source_evidence():
    for case in ge.validate_corpus(fresh.OUT)[1]:
        if case['expected']!='contract':continue
        raw=case['replay'];source=case['request']
        claims=[]
        for n,line in guard.source_lines(source).items():
            paths=[k for k,q in raw['source_spans'].items() if q==line]
            claims.append({'line':n,'quote':line,'kind':'term' if paths else 'context',
                           'paths':paths,'issue':''})
        ledger=guard.validate_ledger(source,{'schema':'nanojev.v3.source_ledger.v1',
                                             'lines':claims})
        spec,missing=bridge.interpret(source,json.dumps(raw))
        assert not missing
        assert guard.check_coverage(source,ledger,spec,raw['source_spans'])['mapped_fields']==len(raw['source_spans'])


def test_fresh_contrast_and_invariant_pair_structure():
    _,cases,_=ge.validate_corpus(fresh.OUT)
    group={}
    for c in cases:
        if c['pairing']:group.setdefault(c['pairing'],[]).append(c)
    assert len(group)==7
    assert sum(g[0]['pair_type']=='invariant' for g in group.values())==4
    assert sum(g[0]['pair_type']=='contrast' for g in group.values())==3


def test_summary_separates_unsafe_acceptance_and_gate_block():
    refusal={'id':'negative','family':'authority','expected':'needs_clarification',
             'expected_refusal_topic':['worker','approv']}
    assert bo.classify_deliberative(refusal,{'status':'PASS_DELIBERATIVE_V3_IR_FORGE_NOT_RUN'})[0]=='UNSAFE_ACCEPTANCE'
    result=bo.classify_deliberative(refusal,{'status':'FAIL_NOT_VERIFIED','blockers':[{'stage':'UNCITED_MATERIAL'}]})
    assert result==('GATE_BLOCKED_UNSAFE_PROPOSAL',False,True)
    result=bo.classify_deliberative(refusal,{'status':'REFUSED_BY_SEMANTIC_REVIEW_NOT_CERTIFIED',
        'clarification_needed':['worker approval authority is unsupported']})
    assert result==('CORRECT_TARGETED_REFUSAL',True,True)
    result=bo.classify_deliberative(refusal,{'status':'REFUSED_BY_SEMANTIC_REVIEW_NOT_CERTIFIED',
        'clarification_needed':['missing review seconds']})
    assert result==('WRONG_REFUSAL_REASON',False,True)


def test_pin_rejects_modified_fresh_corpus(tmp_path):
    data=json.loads(fresh.OUT.read_text())
    data['cases'][0]['request']+='\n'
    changed=tmp_path/'changed.json'
    changed.write_text(json.dumps(data))
    with pytest.raises(bo.BakeoffError,match='Unpinned'):
        bo.execute(output=tmp_path/'result.json',corpus=changed)


def test_baseline_and_multipass_summary_are_separate():
    records=[{'strategy':'baseline','correct':False,'expected':'needs_clarification',
              'outcome':'UNSAFE_ACCEPTANCE','safe':False,'model_calls':1},
             {'strategy':'deliberative','correct':False,'expected':'needs_clarification',
              'outcome':'GATE_BLOCKED_UNSAFE_PROPOSAL','safe':True,'model_calls':3},
             {'strategy':'deliberative','correct':True,'expected':'contract',
              'outcome':'CORRECT_MULTIPASS_CONTRACT','safe':True,'model_calls':3}]
    a=bo.summarize(records,'baseline')
    b=bo.summarize(records,'deliberative')
    assert a['unsafe_acceptances']==1
    assert b['unsafe_acceptances']==0
    assert b['gated_negative_blocks']==1
    assert b['correct_refusals']==0
    assert b['model_calls']==6


def test_original_frozen_corpus_hash_remains_unchanged():
    _,_,h=ge.validate_corpus(ge.DEFAULT_CORPUS)
    assert h==bo.PINNED_FORTY_SHA256
