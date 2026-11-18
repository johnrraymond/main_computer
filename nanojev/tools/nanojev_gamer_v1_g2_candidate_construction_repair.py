"""G2-0051: label-blind high-recall candidate construction and stage-localized audit.

This is an executable candidate *constructor*, not a gold-edge rescue.  It
reuses frozen G1's neural alternative occurrence catalog, retains all admitted
parent occurrences, and removes the destination top-k cutoff.  Type-open
candidates are an exploratory fallback, never a certified inference policy.

The immutable TRAIN-domain counterfactual pairs from G2-0050 supply only
post-construction audit labels. No DEV or sealed TEST labels are read.
"""
from __future__ import annotations

from collections import Counter
import gzip
import json
import shutil
import tempfile
from pathlib import Path

from tools import nanojev_gamer_v1_g2_counterfactual_learning as p50
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SOURCE=p50.g40
SCHEMA='nanojev.gamer.graph.g2.candidate-construction-repair.v1'
REPORT='g2_candidate_construction_repair_report.json'
LEDGER='g2_candidate_construction_repair_per_case.jsonl.gz'
STATUS='LABEL_BLIND_CANDIDATE_REPAIR_AUDITED_NOT_CERTIFIED'
MAX_ANCHORS=128
MAX_ADDITIONAL=64
# The graph contract permits these types, but does not give an oracle relation
# label to the inference candidate generator.
RELATIONS=tuple(r for r in BIND_CLASSES if r!='none')
KINDS=set(SPAN_KINDS)


def refuse(message):
    raise ValueError('GAMER_G2_0051_'+message)


def expand_occurrences(evidence, admitted, *, max_anchors=MAX_ANCHORS,
                       max_additional=MAX_ADDITIONAL):
    """Preserve all previously admitted occurrences and widen using G1 proposals.

    Uses ONLY the frozen G1 predicted char spans/kinds, never prompt strings,
    graph targets, or answer-dependent spans.  A kind-balanced round-robin
    prevents high-density action proposals from excluding all other kinds.
    """
    if not 1<=max_anchors<=128 or not 0<=max_additional<=max_anchors:
        refuse('BAD_OCCURRENCE_BUDGET')
    original=list(admitted)
    if len(original)>max_anchors or len(original)!=len(set(original)) or not original:
        refuse('INVALID_ORIGINAL_OCCURRENCES')
    if len(evidence.get('anchors',()))!=len(evidence.get('spans',())):
        refuse('G1_CATALOG_MISALIGNMENT')
    seen=set(original);groups={kind:[] for kind in SPAN_KINDS}
    for anchor,span in zip(evidence['anchors'],evidence['spans']):
        if (not isinstance(anchor,tuple) or len(anchor)!=3 or
                anchor[2] not in KINDS or not isinstance(span,dict) or
                anchor[0]>=anchor[1]):
            refuse('BAD_G1_PROPOSAL')
        if anchor not in seen:
            groups[anchor[2]].append(anchor);seen.add(anchor)
    chosen=list(original);remaining=min(max_additional,max_anchors-len(chosen))
    # Duplicate prevention is independent of oracle annotation.
    while remaining and any(groups.values()):
        advanced=False
        for kind in SPAN_KINDS:
            if groups[kind] and remaining:
                chosen.append(groups[kind].pop(0));remaining-=1;advanced=True
        if not advanced:break
    if not set(original)<=set(chosen) or len(chosen)!=len(set(chosen)):
        refuse('OCCURRENCE_MONOTONICITY')
    return chosen


def inventory(anchors, allowed, *, open_types=False):
    """Enumerate every directed destination for every available relation.

    Unlike the legacy top-24 shortlist, no gold or frozen-score cutoff can
    exclude an otherwise type-compatible candidate.  The output is a concrete
    ordered edge inventory that a future scorer can consume.
    """
    if not anchors or len(anchors)>128 or len(anchors)!=len(set(anchors)):
        refuse('BAD_ANCHOR_SET')
    if any(len(a)!=3 or a[2] not in KINDS or a[0]>=a[1] for a in anchors):
        refuse('BAD_TYPED_ANCHORS')
    permissible=set(allowed)
    if any(not isinstance(x,tuple) or len(x)!=3 for x in permissible):
        refuse('BAD_TYPE_SIGNATURE')
    edges=[]
    for src in anchors:
        for relation in RELATIONS:
            for dst in anchors:
                if src==dst:continue
                if open_types or (relation,src[2],dst[2]) in permissible:
                    edges.append((src,dst,relation))
    if len(set(edges))!=len(edges):refuse('DUPLICATE_INVENTORY')
    return edges


def candidates_for_record(record, frozen_g1_evidence, allowed):
    """Candidate construction occurs before any audit of author graph edges."""
    parent=list(record['recovered']['anchors'])
    existing=list(record['shortlisted']['edges'])
    expanded=expand_occurrences(frozen_g1_evidence,parent)
    typed=inventory(parent,allowed)
    widened=inventory(expanded,allowed)
    open_inventory=inventory(expanded,allowed,open_types=True)
    if not set(existing)<=set(typed) or not set(typed)<=set(widened) or not set(widened)<=set(open_inventory):
        refuse('CANDIDATE_MONOTONICITY')
    if not set(parent)<=set(expanded):refuse('ENDPOINT_MONOTONICITY')
    return {'original_anchors':parent,'expanded_anchors':expanded,
            'g1_catalog_anchors':set(frozen_g1_evidence['anchors']) | set(frozen_g1_evidence['baseline_anchors']),
            'legacy':set(existing),'unbounded_typed':set(typed),
            'recovered_typed':set(widened),'recovered_open':set(open_inventory),
            'candidate_counts':{'legacy':len(existing),'unbounded_typed':len(typed),
                'recovered_typed':len(widened),'recovered_open':len(open_inventory)}}


def audit_candidate_case(record, constructed, section, mode, pair):
    """Post-inference audit: gold is read only after candidate sets are fixed."""
    gold=SOURCE.base.gold_binding_edges(record['row'])
    endpoints={node for src,dst,_ in gold for node in (src,dst)}
    stages=('legacy','unbounded_typed','recovered_typed','recovered_open')
    missing={s:len(gold-constructed[s]) for s in stages}
    if any(missing[a]<missing[b] for a,b in zip(stages,stages[1:])):
        refuse('GOLD_AUDIT_MONOTONICITY')
    orig=set(constructed['original_anchors']);recovered=set(constructed['expanded_anchors'])
    catalog=constructed['g1_catalog_anchors']
    original_missing=sum(src not in orig or dst not in orig for src,dst,_ in gold)
    recovered_missing=sum(src not in recovered or dst not in recovered for src,dst,_ in gold)
    catalog_missing=sum(src not in catalog or dst not in catalog for src,dst,_ in gold)
    typed_missing=sum(edge not in constructed['recovered_typed'] and
                      edge in constructed['recovered_open'] for edge in gold)
    shortlist_missing=sum(edge not in constructed['legacy'] and
                          edge in constructed['unbounded_typed'] for edge in gold)
    return {'section':section,'mode':mode,'pair':pair,'family':record['row']['family'],
            'failure_attribution':{'original_missing_endpoint_edges':original_missing,
                'recovered_missing_endpoint_edges':recovered_missing,
                'not_in_frozen_g1_catalog_edges':catalog_missing,
                'type_signature_excluded_edges':typed_missing,
                'shortlist_excluded_edges':shortlist_missing},
            'prompt_sha256':SOURCE.base._sha(record['row']['prompt']),
            'gold_edges':len(gold),'required_endpoints':len(endpoints),
            'original_endpoints_present':len(endpoints&orig),
            'recovered_endpoints_present':len(endpoints&recovered),
            'g1_catalog_endpoints_present':len(endpoints&catalog),
            'missing_gold_edges':missing,
            'reachable':{k:v==0 for k,v in missing.items()},
            'candidate_counts':constructed['candidate_counts'],
            'new_endpoints':len(recovered-orig)}


def summaries(rows,pairs):
    stages=('legacy','unbounded_typed','recovered_typed','recovered_open')
    result={};cases={x['prompt_sha256']:x for x in rows}
    for section in ('counterfactual_train','counterfactual_probe'):
        batch=[r for r in rows if r['section']==section]
        section_pairs=pairs[section]
        block={'cases':len(batch),'pairs':len(section_pairs),
               'reachable_graphs':{s:sum(r['reachable'][s] for r in batch) for s in stages},
               'missing_gold_edges':{s:sum(r['missing_gold_edges'][s] for r in batch) for s in stages},
               'both_reachable_pairs':{s:sum(all(cases[h]['reachable'][s] for h in p['sha256'])
                   for p in section_pairs) for s in stages},
               'required_endpoints':sum(r['required_endpoints'] for r in batch),
               'original_endpoints_present':sum(r['original_endpoints_present'] for r in batch),
               'recovered_endpoints_present':sum(r['recovered_endpoints_present'] for r in batch),
               'g1_catalog_endpoints_present':sum(r['g1_catalog_endpoints_present'] for r in batch)}
        block['families']={f:{'cases':sum(r['family']==f for r in batch),
                'reachable_open':sum(r['family']==f and r['reachable']['recovered_open'] for r in batch),
                'reachable_typed':sum(r['family']==f and r['reachable']['recovered_typed'] for r in batch)}
                for f in ('allocation','encounter')}
        result[section]=block
    return result


def verify_parent50(directory,previous_hashes):
    folder=Path(directory)
    report=json.loads((folder/p50.REPORT).read_text(encoding='utf-8'))
    for key,value in {'schema':p50.SCHEMA,'status':p50.STATUS,
             'source_parent_schema':p50.d48.SCHEMA,'parent_hashes':previous_hashes,
             'production_promotion':False,'sealed_test_used':False,
             'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
             'g2_certified':False,'frozen_g1':True,'frozen_g2_0033_parent':True,
             'train_policy':p50.POLICY}.items():
        if report.get(key)!=value:refuse('PARENT50_CONTRACT:'+key)
    if report.get('decision',{}).get('promotion_authorized') is not False:
        refuse('PARENT50_PROMOTION')
    for filename,label in ((p50.LEDGER,'ledger'),(p50.WEIGHTS,'weights'),(p50.POLICIES,'policies')):
        if SOURCE.backbone.sha256(folder/filename)!=report.get(label+'_sha256'):
            refuse('PARENT50_HASH:'+label)
    if json.loads((folder/p50.POLICIES).read_text(encoding='utf-8'))!=p50.POLICY:
        refuse('PARENT50_POLICY')
    return report,{'g2_0050_'+name+'_sha256':SOURCE.backbone.sha256(folder/file)
          for name,file in (('report',p50.REPORT),('ledger',p50.LEDGER),
                            ('weights',p50.WEIGHTS),('policies',p50.POLICIES))}


def parent50_probe_ledger(directory, report, pairs):
    """Replay oracle: parent G2-0050's saved per-prompt candidate availability.

    Fail closed if the regenerated author pairs do not match the immutable
    parent ledger. A drift in the *candidate computation* is different: report
    it by prompt, but do not silently equate two nonidentical baselines.
    """
    expected={sha for pair in pairs for sha in pair['sha256']}
    if len(expected)!=2*len(pairs):refuse('PARENT50_PROBE_DUPLICATE_PROMPT')
    found={}
    with gzip.open(Path(directory)/p50.LEDGER,'rt',encoding='utf-8') as stream:
        for line in stream:
            item=json.loads(line)
            if (item.get('arm'),item.get('section'))!=('replay_bce','counterfactual_probe'):
                continue
            sha=item.get('prompt_sha256')
            reachable=item.get('candidate_reachable')
            gap=item.get('missing_candidate_gold_edges')
            if (not isinstance(sha,str) or sha in found or
                    type(reachable) is not bool or type(gap) is not int or
                    gap<0 or reachable is not (gap==0)):
                refuse('PARENT50_PROBE_LEDGER_INVALID')
            found[sha]=gap
    if set(found)!=expected:
        refuse('PARENT50_PROBE_COHORT_DRIFT')
    # G2-0050 persists the full d46.summarize() structure. Its concise CLI
    # output flattens candidate_reachable to a count, but the saved report has
    # candidate_reachable={cases, exact, ...}. Validate against that stored
    # schema, not against the CLI projection.
    parent=report['results']['replay_bce']['counterfactual_probe']
    reach=parent.get('candidate_reachable')
    all_cases=parent.get('all')
    pair_summary=report['training']['candidate_reachability']['counterfactual_probe']
    if (not isinstance(reach,dict) or not isinstance(all_cases,dict) or
            not isinstance(pair_summary,dict) or
            type(reach.get('cases')) is not int or
            type(all_cases.get('cases')) is not int or
            type(pair_summary.get('pairs')) is not int or
            type(pair_summary.get('both')) is not int):
        refuse('PARENT50_PROBE_REPORT_SCHEMA')
    if (all_cases['cases']!=len(found) or
            reach['cases']!=sum(gap==0 for gap in found.values()) or
            pair_summary['pairs']!=len(pairs) or
            pair_summary['both']!=sum(all(found[sha]==0 for sha in pair['sha256'])
                                      for pair in pairs)):
        refuse('PARENT50_PROBE_LEDGER_SUMMARY_DRIFT')
    return found


def replay_diagnostic(rows, expected, pairs):
    """Attribute legacy capture drift at the original gold-edge gap granularity."""
    actual={}
    for row in rows:
        if row['section']!='counterfactual_probe':continue
        sha=row['prompt_sha256']
        gap=row['missing_gold_edges']['legacy']
        if sha in actual or type(gap) is not int or gap<0 or \
                row['reachable']['legacy'] is not (gap==0):
            refuse('REPLAY_DUPLICATE_OR_INVALID')
        actual[sha]=gap
    if set(actual)!=set(expected):refuse('REPLAY_PROBE_PROMPT_DRIFT')
    changes=[{'prompt_sha256':sha,'parent_missing_gold_edges':expected[sha],
              'current_missing_gold_edges':actual[sha]}
             for sha in sorted(expected) if actual[sha]!=expected[sha]]
    ref_graphs=sum(v==0 for v in expected.values())
    obs_graphs=sum(v==0 for v in actual.values())
    ref_pairs=sum(all(expected[sha]==0 for sha in p['sha256']) for p in pairs)
    obs_pairs=sum(all(actual[sha]==0 for sha in p['sha256']) for p in pairs)
    matched=not changes
    return {'exact_parent50_legacy_replay':matched,
            'parent50_reachable_graphs':ref_graphs,
            'current_reachable_graphs':obs_graphs,
            'parent50_both_reachable_pairs':ref_pairs,
            'current_both_reachable_pairs':obs_pairs,
            'per_prompt_reachability_changed':sum((actual[sha]==0)!=(expected[sha]==0)
                for sha in expected),
            'per_prompt_missing_edge_count_changed':len(changes),
            'changed_prompt_details':changes,
            'comparison_interpretation':('EXACT_PER_PROMPT_REPLAY' if matched else
                                         'BASELINE_DRIFT_WITH_IDENTICAL_PROMPTS')}


def candidate_decision(probe,replay):
    """Never attribute improvements to a repaired inventory on a drifted replay."""
    # Thresholds were predeclared in G2-0051; don't recalibrate to this run.
    observed=(probe['reachable_graphs']['recovered_typed']>=31 and
              probe['both_reachable_pairs']['recovered_typed']>=15)
    match=replay['exact_parent50_legacy_replay']
    signal=bool(match and observed)
    return {'repaired_typed_candidate_coverage_signal':signal,
            'minimum_probe_graphs':31,'minimum_probe_pairs':15,
            'observed_coverage_threshold_met':bool(observed),
            'parent50_exact_replay_required_for_signal':True,
            'unscored_inventory_not_model_promotion':True,
            'next_step':('INVESTIGATE_LEGACY_REPLAY_DRIFT' if not match else
                         'TRAIN_AND_VALIDATE_CANDIDATE_SCORER' if signal else
                         'REPAIR_FROZEN_G1_CATALOG_OR_TYPE_SIGNATURES'),
            'promotion_authorized':False}


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,
        memorization44_run,separability45_run,multiprompt46_run,crossover47_run,
        occurrence48_run,integrated49_run,counterfactual50_run,output,
        train_pairs=48,probe_pairs=16,device='cuda',
        interface_extension=None,interface_audit=None,interface_summary=None,
        alternate_schema=None,alternate_status=None,
        additional_parent_hashes=None,interface_case_features=None,
        interface_finalize=None,alternate_report_filename=None,
        alternate_ledger_filename=None,alternate_weights_filename=None,
        alternate_policy_filename=None,fresh_probe_pairs=0,
        final_test_pairs=0,interface_inference_only=False,
        interface_training_alignment=None,**kwargs):
    g=p50
    p40,h40=g.d49.g41.verify_parent40(bakeoff40_run)
    p41,_,h41=g.d49.g42.verify_parent41(oracle41_run,h40)
    p42,h42=g.d49.b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=g.d48.d45.d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=g.d48.d45.verify_parent44(memorization44_run,h40,h41,h42,h43)
    p45,h45=g.d46.verify_parent45(separability45_run,{**h40,**h41,**h42,**h43,**h44})
    p46,h46=g.d47.verify_parent46(multiprompt46_run,{**h40,**h41,**h42,**h43,**h44,**h45})
    p47,h47=g.d48.verify_parent47(crossover47_run,{**h40,**h41,**h42,**h43,**h44,**h45,**h46},p46)
    p48,h48,rows48=g.d49.verify_parent48(occurrence48_run,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47},p46,p47)
    p49,h49=g.verify_parent49(integrated49_run,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47,**h48},p48)
    parent50_report,h50=verify_parent50(counterfactual50_run,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47,**h48,**h49})
    if (parent50_report['cohorts']!=p46['cohorts'] or
         parent50_report['training']['author_pairs_requested']!=train_pairs or
         parent50_report['training']['probe_pairs_requested']!=probe_pairs):
        refuse('COUNTERFACTUAL_COHORT_DRIFT')
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0051_IMMUTABLE_OUTPUT')
    # The 0050 run excluded the original 0048 train/audit/transfer prompts;
    # enforce exactly the same exclusion here before regenerating the pairs.
    original_hashes={sha for name,ids in p48['cohorts'].items()
                     if name.endswith('_prompts') for sha in ids}
    paired=g.author_pairs(train_pairs,probe_pairs,forbidden=original_hashes)
    fresh=[]
    if fresh_probe_pairs:
        if (interface_case_features is None or interface_finalize is None or
                fresh_probe_pairs%4 or fresh_probe_pairs<4 or
                fresh_probe_pairs+probe_pairs>128):
            refuse('FRESH_PROBE_REQUEST')
        extended=g.author_pairs(train_pairs,probe_pairs+fresh_probe_pairs,
                               forbidden=original_hashes)
        for section in paired:
            old_ids=[p['id'] for p in paired[section]]
            if [p['id'] for p in extended[section][:len(old_ids)]]!=old_ids:
                refuse('FRESH_COHORT_CHANGED_EXISTING_PROMPTS')
        fresh=extended['counterfactual_probe'][probe_pairs:]
        if len(fresh)!=fresh_probe_pairs or any(
                sha in {q for ps in paired.values() for x in ps for q in x['sha256']}
                for p in fresh for sha in p['sha256']):
            refuse('FRESH_COHORT_COLLISION')
    final_test=[]
    if final_test_pairs:
        # G2-0056: a newly generated, never-before-scored final cohort beyond
        # both the historical and 0053 fresh probes. Preserve their exact prefix.
        if (interface_case_features is None or interface_finalize is None or
                final_test_pairs%4 or final_test_pairs<4 or not fresh or
                probe_pairs+fresh_probe_pairs+final_test_pairs>128):
            refuse('FINAL_TEST_REQUEST')
        extended_final=g.author_pairs(train_pairs,
            probe_pairs+fresh_probe_pairs+final_test_pairs,forbidden=original_hashes)
        known=paired['counterfactual_probe']+fresh
        if [(p['id'],p['sha256']) for p in extended_final['counterfactual_probe'][:len(known)]] != [
                (p['id'],p['sha256']) for p in known]:
            refuse('FINAL_TEST_PREVIOUS_COHORT_DRIFT')
        final_test=extended_final['counterfactual_probe'][len(known):]
        existing={sha for ps in paired.values() for pair in ps for sha in pair['sha256']}
        existing.update(sha for pair in fresh for sha in pair['sha256'])
        if (len(final_test)!=final_test_pairs or
            any(sha in existing for pair in final_test for sha in pair['sha256']) or
            len({sha for pair in final_test for sha in pair['sha256']})!=2*final_test_pairs):
            refuse('FINAL_TEST_COLLISION')
    historical_probe=parent50_probe_ledger(counterfactual50_run,parent50_report,
                                           paired['counterfactual_probe'])
    results={};rows=[];scoring_cases=[]
    def callback(*,training,fitting,auditing,assert_all_frozen,
                 capture_candidate_repair_rows=None,**ignored):
        if capture_candidate_repair_rows is None:refuse('MISSING_REPAIR_CAPTURE_HOOK')
        # Reuse only TRAIN-domain author pairs. DEV and TEST remain sealed.
        for section,pairs in paired.items():
            # Process at most four pairs at a time. A type-open high-recall
            # inventory can contain tens of thousands of edges per prompt;
            # never retain all 128 inventories simultaneously in memory.
            for start in range(0,len(pairs),4):
                batch=pairs[start:start+4]
                material=[r for p in batch for r in p['rows']]
                records=capture_candidate_repair_rows(material,'g2_0051_'+section,
                    include_raw_g1=interface_extension is not None)
                if len(records)!=len(material):refuse('CAPTURE_COUNT')
                for rec,expected,pair in zip(records,material,
                                             [p for p in batch for _ in p['rows']]):
                    if rec['row']['prompt']!=expected['prompt'] or rec['row']['split']!='train':
                        refuse('CAPTURE_IDENTITY')
                    proposal=rec.pop('candidate_repair')
                    extra=None;score_case=None
                    if interface_extension is not None:
                        raw=rec.pop('g1_raw_interface')
                        # The constructor does not receive rec, row, gold or annotations.
                        extra=interface_extension(raw,proposal)
                        if interface_case_features is not None:
                            # Before reading any gold, construct label-blind scorer inputs.
                            score_case=interface_case_features(raw,extra,section)
                        del raw
                    elif interface_case_features is not None:
                        refuse('SCORING_REQUIRES_REPAIRED_INTERFACE')
                    audited=audit_candidate_case(rec,proposal,section,pair['mode'],pair['id'])
                    if interface_audit is not None:
                        audited['interface_repair']=interface_audit(rec,proposal,extra,audited)
                    if score_case is not None:
                        # Gold becomes available only AFTER candidate construction.
                        # Optional training-only canonical node IDs. A held-out
                        # or probe graph must never receive this annotation.
                        if section=='counterfactual_train' and interface_training_alignment is not None:
                            score_case.update(interface_training_alignment(rec['row'],extra,score_case))
                        score_case['labels']=SOURCE.base.gold_binding_edges(rec['row'])
                        score_case['prompt_sha256']=audited['prompt_sha256']
                        score_case['family']=audited['family']
                        score_case['mode']=pair['mode']
                        score_case['pair']=pair['id']
                        scoring_cases.append(score_case)
                    rows.append(audited)
        # A fresh, counterfactual TRAIN-domain holdout generated only for 0053.
        # The original 0050/0051/0052 case identities remain unchanged.
        if fresh:
            for start in range(0,len(fresh),4):
                batch=fresh[start:start+4]
                material=[r for pair in batch for r in pair['rows']]
                records=capture_candidate_repair_rows(material,'g2_0053_fresh_holdout',
                    include_raw_g1=True)
                if len(records)!=len(material):refuse('FRESH_CAPTURE_COUNT')
                for rec,expected,pair in zip(records,material,
                         [p for p in batch for _ in p['rows']]):
                    if rec['row']['prompt']!=expected['prompt'] or rec['row']['split']!='train':
                        refuse('FRESH_CAPTURE_IDENTITY')
                    proposal=rec.pop('candidate_repair')
                    raw=rec.pop('g1_raw_interface')
                    extra=interface_extension(raw,proposal)
                    score_case=interface_case_features(raw,extra,'fresh_counterfactual_probe')
                    del raw
                    audited=audit_candidate_case(rec,proposal,'fresh_counterfactual_probe',
                                                  pair['mode'],pair['id'])
                    audited['interface_repair']=interface_audit(rec,proposal,extra,audited)
                    score_case.update(labels=SOURCE.base.gold_binding_edges(rec['row']),
                        prompt_sha256=audited['prompt_sha256'],
                        family=audited['family'],mode=pair['mode'],pair=pair['id'])
                    scoring_cases.append(score_case)
                    rows.append(audited)
        # Final-test features are captured label-blind. The scorer's finalizer
        # must not inspect these cases until *after* validation selects a model.
        if final_test:
            for start in range(0,len(final_test),4):
                batch=final_test[start:start+4]
                material=[row for pair in batch for row in pair['rows']]
                records=capture_candidate_repair_rows(material,
                    'g2_0056_final_holdout',include_raw_g1=True)
                if len(records)!=len(material):refuse('FINAL_TEST_CAPTURE_COUNT')
                for rec,expected,pair in zip(records,material,
                                     [pair for pair in batch for _ in pair['rows']]):
                    if rec['row']['prompt']!=expected['prompt'] or rec['row']['split']!='train':
                        refuse('FINAL_TEST_CAPTURE_IDENTITY')
                    proposal=rec.pop('candidate_repair')
                    raw=rec.pop('g1_raw_interface')
                    extra=interface_extension(raw,proposal)
                    score_case=interface_case_features(raw,extra,'final_counterfactual_test')
                    del raw
                    audited=audit_candidate_case(rec,proposal,'final_counterfactual_test',
                                                  pair['mode'],pair['id'])
                    audited['interface_repair']=interface_audit(rec,proposal,extra,audited)
                    score_case.update(labels=SOURCE.base.gold_binding_edges(rec['row']),
                        prompt_sha256=audited['prompt_sha256'],family=audited['family'],
                        mode=pair['mode'],pair=pair['id'])
                    scoring_cases.append(score_case)
                    rows.append(audited)
        assert_all_frozen()
        results.update(summaries(rows,paired))
        replay=replay_diagnostic(rows,historical_probe,paired['counterfactual_probe'])
        print('GAMER_G2_0051_PARENT50_REPLAY '+json.dumps(replay,sort_keys=True),
              flush=True)
        probe=results['counterfactual_probe']
        decision=candidate_decision(probe,replay)
        report={'schema':alternate_schema or SCHEMA,
            'status':alternate_status or STATUS,'parent_hashes':h50,
            'decision':decision,'parent50_replay_diagnostic':replay,
            'source_parent_schema':parent50_report['schema'],
            'cohorts':{'train_pairs':train_pairs,'probe_pairs':probe_pairs},
            'budgets':{'max_anchors':MAX_ANCHORS,'max_additional':MAX_ADDITIONAL},
            'results':results,'candidate_generation_uses_gold':False,
            'gold_used_only_for_post_capture_audit':True,
            'type_open_inventory_is_exploratory_not_certified':True,
            'frozen_qwen_g1_and_model_parents':True,'new_optimizer_steps':0,
            'development_evaluation_skipped':True,'sealed_test_used':False,
            'production_promotion':False,'g2_certified':False}
        if interface_summary is not None:
            report['interface_repair']=interface_summary(rows,paired,replay)
        extra_weights=None;extra_policy=None
        auxiliary_ledger=None
        if fresh:
            fresh_rows=[row for row in rows if row['section']=='fresh_counterfactual_probe']
            report['fresh_probe_candidate_audit']={
                'cases':len(fresh_rows),'pairs':len(fresh),
                'reachable':sum(r['interface_repair']['reachable'] for r in fresh_rows),
                'missing_gold_edges':sum(r['interface_repair']['missing_gold_edges'] for r in fresh_rows),
                'never_in_parent50_or_parent52':True,
                'train_domain_author_generated_not_sealed_test':True}
        if interface_finalize is not None:
            if interface_case_features is None or interface_summary is None:
                refuse('SCORING_EXTENSION_CONTRACT')
            experiment,extra_weights,extra_policy=interface_finalize(
                scoring_cases,{**paired,**({'fresh_counterfactual_probe':fresh} if fresh else {}),
                    **({'final_counterfactual_test':final_test} if final_test else {})},
                replay,report)
            if isinstance(experiment,dict):
                auxiliary_ledger=experiment.pop('_auxiliary_ledger',None)
                if auxiliary_ledger is not None:
                    if (not isinstance(auxiliary_ledger,dict)
                        or set(auxiliary_ledger)!={'filename','rows'}
                        or not isinstance(auxiliary_ledger['filename'],str)
                        or Path(auxiliary_ledger['filename']).name!=auxiliary_ledger['filename']
                        or not auxiliary_ledger['filename'].endswith('.jsonl.gz')
                        or not isinstance(auxiliary_ledger['rows'],list)
                        or not 1<=len(auxiliary_ledger['rows'])<=4096
                        or any(not isinstance(item,dict) for item in auxiliary_ledger['rows'])):
                        refuse('AUXILIARY_LEDGER_INVALID')
            optimizer_steps=(experiment.get('training',{}).get('optimizer_steps_total',
                             experiment.get('training',{}).get('steps'))
                if isinstance(experiment,dict) else None)
            if (type(optimizer_steps) is not int or
                    (optimizer_steps!=0 if interface_inference_only else optimizer_steps<=0) or
                    not isinstance(extra_weights,dict) or not extra_weights or
                    not isinstance(extra_policy,dict)):
                refuse('SCORING_TRAINING_RECEIPT_INVALID')
            if interface_inference_only and (
                    experiment.get('training',{}).get('optimizer_steps_total')!=0 or
                    experiment.get('gates',{}).get('model_frozen') is not True or
                    extra_policy.get('enabled') is not False):
                refuse('SCORING_INFERENCE_ONLY_CONTRACT')
            report['new_optimizer_steps']=optimizer_steps
            report['scorer_retraining_performed']=not interface_inference_only
            report['scoring_experiment']=experiment
        if additional_parent_hashes:
            if not isinstance(additional_parent_hashes,dict) or \
               any(not k.startswith('g2_0051_') or not isinstance(v,str) or len(v)!=64
                   for k,v in additional_parent_hashes.items()):
                refuse('ADDITIONAL_PARENT_HASH_FORMAT')
            report['parent_hashes']={**h50,**additional_parent_hashes}
        folder.parent.mkdir(parents=True,exist_ok=True)
        temp=Path(tempfile.mkdtemp(prefix=folder.name+'.staging-',dir=folder.parent))
        try:
            ledger_name=alternate_ledger_filename or LEDGER
            report_name=alternate_report_filename or REPORT
            if Path(ledger_name).name!=ledger_name or Path(report_name).name!=report_name:
                refuse('BAD_OUTPUT_FILENAME')
            with gzip.open(temp/ledger_name,'wt',encoding='utf-8') as file:
                for row in rows:file.write(json.dumps(row,sort_keys=True)+'\n')
            report['ledger_sha256']=SOURCE.backbone.sha256(temp/ledger_name)
            if auxiliary_ledger is not None:
                auxiliary_name=auxiliary_ledger['filename']
                if auxiliary_name in (ledger_name,report_name):
                    refuse('AUXILIARY_LEDGER_NAME_COLLISION')
                with gzip.open(temp/auxiliary_name,'wt',encoding='utf-8') as file:
                    for item in auxiliary_ledger['rows']:
                        file.write(json.dumps(item,sort_keys=True)+'\n')
                report['scoring_experiment']['auxiliary_prediction_ledger']={
                    'filename':auxiliary_name,
                    'sha256':SOURCE.backbone.sha256(temp/auxiliary_name),
                    'rows':len(auxiliary_ledger['rows'])}
            if extra_weights is not None:
                from safetensors.torch import save_file
                filename=alternate_weights_filename or 'g2_0053_scoring_development.safetensors'
                if Path(filename).name!=filename or not filename.endswith('.safetensors'):
                    refuse('BAD_WEIGHTS_FILENAME')
                save_file({k:v.detach().cpu().contiguous() for k,v in extra_weights.items()},
                          str(temp/filename))
                report['scoring_weights_sha256']=SOURCE.backbone.sha256(temp/filename)
                if extra_policy is None:refuse('SCORING_POLICY_MISSING')
                policy_file=alternate_policy_filename or 'g2_0053_frozen_policy.json'
                if Path(policy_file).name!=policy_file or not policy_file.endswith('.json'):
                    refuse('BAD_POLICY_FILENAME')
                (temp/policy_file).write_text(json.dumps(extra_policy,indent=2,sort_keys=True)+'\n',encoding='utf-8')
                report['scoring_policy_sha256']=SOURCE.backbone.sha256(temp/policy_file)
            (temp/report_name).write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            if folder.exists():raise FileExistsError('GAMER_G2_0051_OUTPUT_RACE')
            temp.rename(folder)
        finally:
            if temp.exists():shutil.rmtree(temp)
        return report
    return SOURCE.run(output=output,diagnostic_callback=callback,device=device,**kwargs)
