"""Acceptance checks for G2-0051 label-blind high-recall candidate construction."""
import hashlib
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g2_candidate_construction_repair as c


A=(0,3,'actor')
B=(4,7,'action')
C=(8,11,'action')
D=(12,15,'quantity')
E=(16,19,'outcome')
ALLOW={('owns','actor','action'),('refers_to','action','action'),
       ('applies_to','quantity','action')}


def evidence():
    return {'baseline_anchors':[A],
            'anchors':[B,C,D,E],
            'spans':[{'token_first':i,'token_last':i,'kind':x[2]}
                      for i,x in enumerate((B,C,D,E))],
            'gold':{(A,E,'measures')},'prompt':'MUST_NOT_BE_READ'}


def record():
    return {'recovered':{'anchors':[A,B]},
            'shortlisted':{'edges':[(A,B,'owns')]},
            'row':{'family':'allocation','prompt':'alpha',
                   'graph':{'nodes':[], 'edges':[]},'split':'train'}}


def test_expansion_preserves_parent_and_uses_g1_catalog_not_gold():
    e=evidence()
    extra=c.expand_occurrences(e,[A,B],max_anchors=6,max_additional=4)
    assert extra[:2]==[A,B]
    assert set(extra)=={A,B,C,D,E}
    e['gold']={(D,E,'measures')};e['prompt']='UNRELATED CHANGED TEXT'
    assert extra==c.expand_occurrences(e,[A,B],max_anchors=6,max_additional=4)


def test_occurrence_budgets_and_duplicate_validation():
    assert len(c.expand_occurrences(evidence(),[A,B],max_anchors=3,max_additional=1))==3
    with pytest.raises(ValueError,match='BAD_OCCURRENCE_BUDGET'):
        c.expand_occurrences(evidence(),[A],max_anchors=0)
    with pytest.raises(ValueError,match='INVALID_ORIGINAL_OCCURRENCES'):
        c.expand_occurrences(evidence(),[A,A])
    with pytest.raises(ValueError,match='G1_CATALOG_MISALIGNMENT'):
        c.expand_occurrences({'anchors':[B],'spans':[]},[A])


def test_kind_balanced_expansion_will_not_exclude_entire_rare_kind():
    actions=[(i*4,i*4+3,'action') for i in range(1,30)]
    rare=(202,205,'outcome')
    e={'baseline_anchors':[A],'anchors':actions+[rare],
       'spans':[{'token_first':i,'token_last':i,'kind':x[2]}
                for i,x in enumerate(actions+[rare])]}
    selection=c.expand_occurrences(e,[A],max_anchors=6,max_additional=5)
    assert rare in selection
    assert len(selection)==len(set(selection))==6


def test_all_typed_directed_pairs_not_top_k_or_one_destination():
    out=c.inventory([A,B,C,D,E],ALLOW)
    assert (A,B,'owns') in out and (A,C,'owns') in out
    assert (B,C,'refers_to') in out and (C,B,'refers_to') in out
    assert (D,B,'applies_to') in out and (D,C,'applies_to') in out
    assert (A,E,'owns') not in out
    assert all(s!=t for s,t,_ in out)
    assert len(out)==len(set(out))
    assert (A,E,'owns') in c.inventory([A,B,C,D,E],ALLOW,open_types=True)


def test_record_repair_monotonic_and_no_author_label_read():
    r=record();e=evidence()
    p=c.candidates_for_record(r,e,ALLOW)
    assert p['legacy']<=p['unbounded_typed']<=p['recovered_typed']<=p['recovered_open']
    assert (A,C,'owns') in p['recovered_typed']
    assert (A,E,'owns') not in p['recovered_typed']
    assert (A,E,'owns') in p['recovered_open']
    r['row']['graph']={'deliberate':'malformed'}
    e['gold']='malformed'
    assert c.candidates_for_record(r,e,ALLOW)['recovered_typed']==p['recovered_typed']


def test_audit_identifies_endpoint_signature_and_shortlist_defects(monkeypatch):
    r=record();p=c.candidates_for_record(r,evidence(),ALLOW)
    monkeypatch.setattr(c.SOURCE.base,'gold_binding_edges',lambda row: {
        (A,B,'owns'),(A,C,'owns'),(A,E,'owns')})
    got=c.audit_candidate_case(r,p,'counterfactual_train','renaming','pair1')
    assert got['missing_gold_edges']=={
        'legacy':2,'unbounded_typed':2,'recovered_typed':1,'recovered_open':0}
    assert got['reachable']['recovered_open']
    assert got['original_endpoints_present']==2
    assert got['recovered_endpoints_present']==4


def test_pair_summary_requires_both_graphs():
    rows=[];paired={}
    for section in ('counterfactual_train','counterfactual_probe'):
        ids=[]
        for i in range(4):
            sha=str(section)+str(i);ids.append(sha)
            rows.append({'section':section,'family':('allocation' if i<2 else 'encounter'),
                'prompt_sha256':sha,'required_endpoints':3,'original_endpoints_present':2,
                'recovered_endpoints_present':3,'g1_catalog_endpoints_present':3,
                'reachable':{'legacy':False,'unbounded_typed':True,
                    'recovered_typed':True,'recovered_open':True},
                'missing_gold_edges':{'legacy':1,'unbounded_typed':0,
                    'recovered_typed':0,'recovered_open':0}})
        paired[section]=[{'sha256':ids[:2]},{'sha256':ids[2:]}]
    out=c.summaries(rows,paired)
    assert out['counterfactual_probe']['both_reachable_pairs']['legacy']==0
    assert out['counterfactual_probe']['both_reachable_pairs']['recovered_typed']==2
    assert out['counterfactual_train']['families']['encounter']['reachable_open']==2


def test_parent50_artifact_validation_catches_mutation(tmp_path,monkeypatch):
    folder=tmp_path/'parent50';folder.mkdir()
    r={'schema':c.p50.SCHEMA,'status':c.p50.STATUS,
       'source_parent_schema':c.p50.d48.SCHEMA,
       'parent_hashes':{'hash':'value'},'production_promotion':False,
       'sealed_test_used':False,'dev_labels_used_for_training':False,
       'dev_labels_used_for_calibration':False,'g2_certified':False,
       'frozen_g1':True,'frozen_g2_0033_parent':True,
       'train_policy':c.p50.POLICY,'decision':{'promotion_authorized':False}}
    (folder/c.p50.LEDGER).write_bytes(b'ledger')
    (folder/c.p50.WEIGHTS).write_bytes(b'weights')
    (folder/c.p50.POLICIES).write_text(json.dumps(c.p50.POLICY))
    for filename,key in ((c.p50.LEDGER,'ledger'),(c.p50.WEIGHTS,'weights'),
                         (c.p50.POLICIES,'policies')):
        r[key+'_sha256']=hashlib.sha256((folder/filename).read_bytes()).hexdigest()
    (folder/c.p50.REPORT).write_text(json.dumps(r))
    assert c.verify_parent50(folder,{'hash':'value'})[0]['schema']==c.p50.SCHEMA
    r['ledger_sha256']='corrupt';(folder/c.p50.REPORT).write_text(json.dumps(r))
    with pytest.raises(ValueError,match='PARENT50_HASH:ledger'):
        c.verify_parent50(folder,{'hash':'value'})


def test_cli_contract_present():
    from tools.nanojev_gamer_v1_graph_curriculum import main
    import contextlib,io
    with contextlib.redirect_stdout(io.StringIO()) as output:
        with pytest.raises(SystemExit) as error:main(['frob-g2-candidate-construction-repair','--help'])
    assert error.value.code==0
    assert '--counterfactual50-run' in output.getvalue()
    assert '--device' in output.getvalue()


def test_run_initializes_parent_module_before_parent_verification(monkeypatch,tmp_path):
    """Guard against a report variable shadowing the imported G2-0050 module."""
    sentinel=RuntimeError('parent verifier reached')
    def stop_at_first_parent(*args,**kwargs):
        raise sentinel
    monkeypatch.setattr(c.p50.d49.g41,'verify_parent40',stop_at_first_parent)
    with pytest.raises(RuntimeError,match='parent verifier reached'):
        c.run(bakeoff40_run='unused',oracle41_run='unused',rank42_run='unused',
              absolute43_run='unused',memorization44_run='unused',
              separability45_run='unused',multiprompt46_run='unused',
              crossover47_run='unused',occurrence48_run='unused',
              integrated49_run='unused',counterfactual50_run='unused',
              output=str(tmp_path/'output'))



def _probe_fixture(tmp_path):
    import gzip
    folder=tmp_path/'parent50';folder.mkdir()
    pairs=[{'sha256':('a','b')},{'sha256':('c','d')}]
    expected={'a':False,'b':True,'c':True,'d':True}
    with gzip.open(folder/c.p50.LEDGER,'wt',encoding='utf-8') as stream:
        for sha,reached in expected.items():
            row={'arm':'replay_bce','section':'counterfactual_probe',
                 'prompt_sha256':sha,'candidate_reachable':reached,
                 'missing_candidate_gold_edges':0 if reached else 1}
            stream.write(json.dumps(row)+'\n')
    # Match the persisted G2-0050 report, not its flattened CLI printout.
    report={'results':{'replay_bce':{'counterfactual_probe':{
                'all':{'cases':4},'candidate_reachable':{'cases':3}}}},
            'training':{'candidate_reachability':{'counterfactual_probe':{
                'pairs':2,'both':1}}}}
    return folder,report,pairs,expected


def test_parent50_probe_replay_uses_original_ledger_not_aggregate(tmp_path):
    folder,report,pairs,expected=_probe_fixture(tmp_path)
    assert c.parent50_probe_ledger(folder,report,pairs)=={sha:(0 if reach else 1) for sha,reach in expected.items()}
    report['results']['replay_bce']['counterfactual_probe']['candidate_reachable']['cases']=2
    with pytest.raises(ValueError,match='PARENT50_PROBE_LEDGER_SUMMARY_DRIFT'):
        c.parent50_probe_ledger(folder,report,pairs)
    report['results']['replay_bce']['counterfactual_probe']['candidate_reachable']['cases']=3
    with pytest.raises(ValueError,match='PARENT50_PROBE_COHORT_DRIFT'):
        c.parent50_probe_ledger(folder,report,[{'sha256':('a','b')},
                                               {'sha256':('c','WRONG')}])


def test_parent50_saved_report_summary_schema_and_pair_invariants(tmp_path):
    folder,report,pairs,expected=_probe_fixture(tmp_path)
    assert c.parent50_probe_ledger(folder,report,pairs)['a']==1
    # The condensed CLI schema must be rejected rather than silently used
    # in place of the persisted G2-0050 report's nested metrics.
    report['results']['replay_bce']['counterfactual_probe']['candidate_reachable']=3
    with pytest.raises(ValueError,match='PARENT50_PROBE_REPORT_SCHEMA'):
        c.parent50_probe_ledger(folder,report,pairs)
    report['results']['replay_bce']['counterfactual_probe']['candidate_reachable']={'cases':3}
    report['training']['candidate_reachability']['counterfactual_probe']['both']=0
    with pytest.raises(ValueError,match='PARENT50_PROBE_LEDGER_SUMMARY_DRIFT'):
        c.parent50_probe_ledger(folder,report,pairs)
    report['training']['candidate_reachability']['counterfactual_probe']['both']=1
    report['training']['candidate_reachability']['counterfactual_probe']['pairs']=1
    with pytest.raises(ValueError,match='PARENT50_PROBE_LEDGER_SUMMARY_DRIFT'):
        c.parent50_probe_ledger(folder,report,pairs)
    report['training']['candidate_reachability']['counterfactual_probe']['pairs']=2
    report['results']['replay_bce']['counterfactual_probe']['all']['cases']=3
    with pytest.raises(ValueError,match='PARENT50_PROBE_LEDGER_SUMMARY_DRIFT'):
        c.parent50_probe_ledger(folder,report,pairs)


def test_parent50_report_nested_reachability_is_written_by_real_upstream_summary():
    # G2-0050 persists this real summarizer's output, not the flat CLI view.
    from tools import nanojev_gamer_v1_g2_multiprompt_discrimination as d46
    assert c.p50.d46.summarize is d46.summarize
    rows=[{'exact':0,'missing':1,'extra':0,'inversions':1,
           'gold_count':2,'selected_count':1,'missing_candidate_gold_edges':1,
           'candidate_reachable':False,'family':'encounter'},
          {'exact':1,'missing':0,'extra':0,'inversions':0,
           'gold_count':1,'selected_count':1,'missing_candidate_gold_edges':0,
           'candidate_reachable':True,'family':'allocation'}]
    persisted=d46.summarize(rows)
    assert persisted['all']['cases']==2
    assert persisted['candidate_reachable']['cases']==1
    assert isinstance(persisted['candidate_reachable'],dict)


def test_replay_detects_per_prompt_drift_even_when_aggregate_matches():
    expected={'a':0,'b':1,'c':1,'d':0}
    pairs=[{'sha256':('a','b')},{'sha256':('c','d')}]
    # Both old and new have 2/4 reachable, but graph identities have changed.
    rows=[{'section':'counterfactual_probe','prompt_sha256':sha,
           'reachable':{'legacy':gap==0},'missing_gold_edges':{'legacy':gap}}
          for sha,gap in {'a':1,'b':0,'c':1,'d':0}.items()]
    actual=c.replay_diagnostic(rows,expected,pairs)
    assert not actual['exact_parent50_legacy_replay']
    assert actual['parent50_reachable_graphs']==actual['current_reachable_graphs']==2
    assert actual['per_prompt_reachability_changed']==2
    assert [v['prompt_sha256'] for v in actual['changed_prompt_details']]==['a','b']
    with pytest.raises(ValueError,match='REPLAY_PROBE_PROMPT_DRIFT'):
        c.replay_diagnostic(rows[:-1],expected,pairs)
    # Even if all graph-level reachability bits agree, gold-edge count drift
    # is not a faithful replay and must block a claimed recovery signal.
    rows[0]['missing_gold_edges']['legacy']=3
    rows[0]['reachable']['legacy']=False
    expected_changed={'a':2,'b':0,'c':1,'d':0}
    counts=c.replay_diagnostic(rows,expected_changed,pairs)
    assert counts['per_prompt_reachability_changed']==0
    assert counts['per_prompt_missing_edge_count_changed']==1
    assert not counts['exact_parent50_legacy_replay']


def test_replay_match_is_required_for_candidate_coverage_signal():
    probe={'reachable_graphs':{'recovered_typed':32},
           'both_reachable_pairs':{'recovered_typed':16}}
    drift={'exact_parent50_legacy_replay':False}
    d=c.candidate_decision(probe,drift)
    assert not d['repaired_typed_candidate_coverage_signal']
    assert d['observed_coverage_threshold_met']
    assert d['next_step']=='INVESTIGATE_LEGACY_REPLAY_DRIFT'
    assert not d['promotion_authorized']
    match={'exact_parent50_legacy_replay':True}
    assert c.candidate_decision(probe,match)['repaired_typed_candidate_coverage_signal']
    probe['reachable_graphs']['recovered_typed']=30
    assert not c.candidate_decision(probe,match)['repaired_typed_candidate_coverage_signal']
