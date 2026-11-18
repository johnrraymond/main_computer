"""G1/G2-0052: label-blind raw-G1 endpoint recovery plus exact-span loss tracing.

Research-only replacement for the G2-0051 G1-catalog bottleneck, not an
oracle edge injector or a trained graph scorer. The original candidate graph
and frozen model remain untouched. Only a post-capture audit sees author gold.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import unicodedata

import torch
from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA='nanojev.gamer.graph.g1-g2.endpoint-interface-repair.v1'
STATUS='G1_G2_ENDPOINT_INTERFACE_REPAIR_AUDITED_NOT_CERTIFIED'
MAX_ANCHORS=192
PER_KIND_TOP=384
REPORT=p51.REPORT
LEDGER=p51.LEDGER


def refuse(reason):
    raise ValueError('GAMER_G1_G2_0052_'+reason)


def _punct_trim(anchor,prompt):
    """Generic Unicode boundary normalization; internal punctuation is kept."""
    first,last,kind=anchor
    while first<last and (prompt[first].isspace() or unicodedata.category(prompt[first]).startswith('P')):
        first+=1
    while last>first and (prompt[last-1].isspace() or unicodedata.category(prompt[last-1]).startswith('P')):
        last-=1
    return (first,last,kind) if first<last else None


def _validate(raw,constructed):
    logits=raw['span_logits'];valid=raw['span_valid'];prepared=raw['prepared']
    if (not isinstance(logits,torch.Tensor) or not isinstance(valid,torch.Tensor)
            or logits.ndim!=3 or logits.shape[:2]!=valid.shape or
            logits.shape[-1]!=len(SPAN_KINDS)+1 or not bool(torch.isfinite(logits).all())
            or not isinstance(prepared.get('prompt'),str)):
        refuse('RAW_G1_SHAPE')
    if len(constructed['expanded_anchors'])>p51.MAX_ANCHORS:
        refuse('PARENT_ANCHOR_BUDGET')
    if len(constructed['expanded_anchors'])!=len(set(constructed['expanded_anchors'])):
        refuse('DUPLICATE_PARENT')
    return logits.detach().cpu(),valid.detach().cpu().bool(),prepared


def _raw_lattice(logits,valid,prepared):
    """All valid span boundaries, before G1 confidence/type/selection filters.

    The lattice is diagnostic only. No gold spans enter the candidate builder.
    """
    positions=valid.nonzero(as_tuple=False).tolist()
    bounds={}
    for first,width in positions:
        char=token_span_char_bounds({'token_first':int(first),
                                     'token_last':int(first+width)},prepared)
        if char is not None:
            bounds.setdefault(char,[]).append((int(first),int(width)))
    return bounds


def _neural_options(logits,valid,prepared):
    """Permit subthreshold class alternatives using frozen G1 scores only."""
    margins=logits[...,1:]-logits[...,0:1]
    chosen_by_kind={}
    flat_valid=valid.flatten()
    for k,kind in enumerate(SPAN_KINDS):
        flat=margins[:,:,k].reshape(-1).clone()
        flat[~flat_valid]=-float('inf')
        count=min(PER_KIND_TOP,int(flat_valid.sum().item()))
        if not count:continue
        values,indices=torch.topk(flat,k=count,sorted=True)
        kind_rows=[];seen=set()
        for value,index in zip(values.tolist(),indices.tolist()):
            if not math.isfinite(value):continue
            first=index//valid.shape[1];width=index%valid.shape[1]
            bounds=token_span_char_bounds({'token_first':int(first),
                                'token_last':int(first+width)},prepared)
            if bounds is None:continue
            anchor=(*bounds,kind)
            if anchor not in seen:
                kind_rows.append((float(value),anchor,(int(first),int(first+width))));seen.add(anchor)
            trimmed=_punct_trim(anchor,prepared['prompt'])
            if trimmed is not None and trimmed not in seen:
                kind_rows.append((float(value)-0.001,trimmed,(int(first),int(first+width))));seen.add(trimmed)
        chosen_by_kind[kind]=kind_rows
    return chosen_by_kind


def inventory(anchors,allowed):
    """Bounded typed, directed edge inventory, without top-k or gold labels."""
    if not 1<=len(anchors)<=MAX_ANCHORS or len(set(anchors))!=len(anchors):
        refuse('ANCHOR_BUDGET')
    allowed=set(allowed)
    if any(len(x)!=3 for x in allowed):refuse('TYPE_SIGNATURE')
    groups=defaultdict(list)
    for anchor in anchors:
        if anchor[2] not in SPAN_KINDS or anchor[0]>=anchor[1]:refuse('BAD_ANCHOR')
        groups[anchor[2]].append(anchor)
    edges=set()
    for relation,source_kind,dest_kind in allowed:
        for src in groups.get(source_kind,()):
            for dst in groups.get(dest_kind,()):
                if src!=dst:edges.add((src,dst,relation))
    return edges


def construct(raw,parent):
    """G1-to-G2 repair; every extra endpoint derives from frozen G1 logits.

    Preserve G2-0051's admitted occurrences; score-budget remaining slots from
    top raw neural alternatives in a kind-balanced round-robin. No author graph
    or family labels are available to this function.
    """
    logits,valid,prepared=_validate(raw,parent)
    base=list(parent['expanded_anchors'])
    seen=set(base);neural=_neural_options(logits,valid,prepared)
    pointers={kind:0 for kind in SPAN_KINDS}
    added=[];origin={}
    while len(base)+len(added)<MAX_ANCHORS:
        advanced=False
        for kind in SPAN_KINDS:
            entries=neural.get(kind,())
            while pointers[kind]<len(entries):
                score,anchor,token_span=entries[pointers[kind]];pointers[kind]+=1
                if anchor in seen:continue
                seen.add(anchor);added.append(anchor);advanced=True
                origin[anchor]={'token_first':token_span[0],
                    'token_last':token_span[1], 'kind':kind,
                    'char_span':[anchor[0],anchor[1]],
                    'neural_margin':round(score,6),'source':'frozen_g1_raw'}
                break
            if len(base)+len(added)==MAX_ANCHORS:break
        if not advanced:break
    all_anchors=base+added
    edges=inventory(all_anchors,raw['allowed'])
    if not parent['recovered_typed']<=edges or not set(base)<=set(all_anchors):
        refuse('G1_G2_MONOTONICITY')
    if set(origin)!=set(added):refuse('PROVENANCE_MISSING')
    return {'anchors':all_anchors,'new_anchors':added,'typed_edges':edges,
            'new_endpoint_provenance':origin,
            'raw_lattice':_raw_lattice(logits,valid,prepared),
            'logits':logits,'prepared':prepared,
            'selected_neural':neural}


def audit(record,parent,recovered,prior):
    """The FIRST lookup of gold in this repair; all inventories are frozen."""
    if recovered is None:refuse('MISSING_EXTENSION')
    gold=p51.SOURCE.base.gold_binding_edges(record['row'])
    endpoints={n for s,d,_ in gold for n in (s,d)}
    before=set(parent['expanded_anchors']);after=set(recovered['anchors'])
    missing_edges=sum(e not in recovered['typed_edges'] for e in gold)
    if missing_edges>prior['missing_gold_edges']['recovered_typed']:
        refuse('EDGE_MONOTONICITY')
    raw_lattice=recovered.pop('raw_lattice')
    logits=recovered.pop('logits')
    prepared=recovered.pop('prepared')
    recovered.pop('selected_neural')
    new_endpoint_provenance=recovered.pop('new_endpoint_provenance')
    missing_details=[];prior_missing=[];reasons=Counter()
    for endpoint in sorted(endpoints-before):
        start,end,kind=endpoint
        eligible=raw_lattice.get((start,end),())
        if eligible:
            k=SPAN_KINDS.index(kind)
            score=max(float(logits[t,w,k+1]-logits[t,w,0]) for t,w in eligible)
            cause=('raw_neural_favors_null' if score<=0 else
                   'positive_raw_span_excluded_by_budget_or_rank')
        else:
            score=None
            cause='no_exact_token_span_boundary'
        recovered_now=endpoint in after
        previous={'kind':kind,'span':[start,end],
            'surface':record['row']['prompt'][start:end],
            'recovered_by_raw_g1':recovered_now,
            'diagnosis':('recovered_from_raw_g1' if recovered_now else cause),
            'raw_neural_margin':round(score,6) if score is not None else None}
        prior_missing.append(previous)
        if not recovered_now:
            reasons[cause]+=1
            missing_details.append(previous)
    newly_found=endpoints & (after-before)
    return {'stage':'raw_neural_endpoint_recovery',
            'missing_gold_edges':missing_edges,'reachable':missing_edges==0,
            'required_endpoints':len(endpoints),
            'previous_endpoints_present':len(endpoints&before),
            'repaired_endpoints_present':len(endpoints&after),
            'newly_found':len(newly_found),
            'prior_missing_endpoints':prior_missing,
            'originally_missing_by_kind':dict(Counter(x['kind'] for x in prior_missing)),
            'newly_found_by_kind':dict(Counter(x[2] for x in newly_found)),
            'missing_by_reason':dict(reasons),'still_missing':missing_details,
            'new_endpoint_candidates':len(recovered['new_anchors']),
            'new_endpoint_provenance':list(new_endpoint_provenance.values()),
            'total_endpoint_candidates':len(after),
            'typed_candidate_count':len(recovered['typed_edges'])}


def summarize(rows,pairs,replay):
    """Paired coverage and cause breakdown for a single frozen input policy."""
    result={}
    for section,ps in pairs.items():
        batch=[r for r in rows if r['section']==section]
        lookup={r['prompt_sha256']:r['interface_repair'] for r in batch}
        if len(batch)!=2*len(ps) or len(lookup)!=len(batch):refuse('CASE_IDENTITY')
        old=p51.summaries(rows,pairs)[section]
        out={'cases':len(batch),'pairs':len(ps),
            'baseline_reachable':old['reachable_graphs']['recovered_typed'],
            'baseline_both_pairs':old['both_reachable_pairs']['recovered_typed'],
            'repaired_reachable':sum(r['interface_repair']['reachable'] for r in batch),
            'repaired_both_pairs':sum(all(lookup[sha]['reachable'] for sha in p['sha256']) for p in ps),
            'missing_gold_edges':sum(r['interface_repair']['missing_gold_edges'] for r in batch),
            'baseline_missing_gold_edges':old['missing_gold_edges']['recovered_typed'],
            'required_endpoints':sum(r['interface_repair']['required_endpoints'] for r in batch),
            'baseline_endpoints_present':sum(r['interface_repair']['previous_endpoints_present'] for r in batch),
            'repaired_endpoints_present':sum(r['interface_repair']['repaired_endpoints_present'] for r in batch),
            'newly_found_by_kind':dict(sum((Counter(r['interface_repair']['newly_found_by_kind']) for r in batch), Counter())),
            'originally_missing_by_kind':dict(sum((Counter(r['interface_repair']['originally_missing_by_kind']) for r in batch),Counter())),
            'remaining_causes':dict(sum((Counter(r['interface_repair']['missing_by_reason']) for r in batch),Counter())),
            'families':{fam:{'cases':sum(r['family']==fam for r in batch),
               'repaired_reachable':sum(r['family']==fam and r['interface_repair']['reachable'] for r in batch)}
               for fam in ('allocation','encounter')}}
        if out['repaired_reachable']<out['baseline_reachable'] or out['repaired_both_pairs']<out['baseline_both_pairs']:
            refuse('SUMMARY_REGRESSION')
        result[section]=out
    p=result['counterfactual_probe']
    coverage=p['repaired_reachable']>=31 and p['repaired_both_pairs']>=15
    return {'sections':result,'decision':{
        'parent50_exact_replay':bool(replay['exact_parent50_legacy_replay']),
        'coverage_targets_met':bool(coverage),
        'practical_candidate_signal':bool(coverage and replay['exact_parent50_legacy_replay']),
        'minimum_probe_graphs':31,'minimum_probe_pairs':15,
        'candidate_construction_uses_gold':False,
        'scorer_retraining_performed':False,'production_promotion':False,
        'next_step':('INVESTIGATE_TOKEN_ALIGNMENT' if p['remaining_causes'].get('no_exact_token_span_boundary',0)
            else 'INVESTIGATE_NEURAL_G1_RANK_BUDGET_OR_TRAINING' if not coverage
            else 'VALIDATE_G2_SCORING_WITH_REPAIRED_INPUTS')}}


def verify_parent51(directory):
    path=Path(directory);report=json.loads((path/p51.REPORT).read_text(encoding='utf-8'))
    if (report.get('schema')!=p51.SCHEMA or report.get('status')!=p51.STATUS or
        report.get('candidate_generation_uses_gold') is not False or
        report.get('production_promotion') is not False or
        report.get('parent50_replay_diagnostic',{}).get('exact_parent50_legacy_replay') is not True or
        report.get('results',{}).get('counterfactual_probe',{}).get('reachable_graphs',{}).get('recovered_typed')!=8 or
        report['results']['counterfactual_probe']['both_reachable_pairs']['recovered_typed']!=1 or
        p51.SOURCE.backbone.sha256(path/p51.LEDGER)!=report.get('ledger_sha256')):
        refuse('PARENT51_CONTRACT_OR_LEDGER')
    return report


def run(*,candidate51_run,output,scoring_case_features=None,
        scoring_finalize=None,scoring_schema=None,scoring_status=None,
        scoring_report_name=None,scoring_ledger_name=None,**kwargs):
    verify_parent51(candidate51_run)
    parent51_path=Path(candidate51_run)
    hashes={
        'g2_0051_report_sha256':p51.SOURCE.backbone.sha256(parent51_path/p51.REPORT),
        'g2_0051_ledger_sha256':p51.SOURCE.backbone.sha256(parent51_path/p51.LEDGER)}
    result=p51.run(output=output,additional_parent_hashes=hashes,interface_extension=construct,
                   interface_audit=audit,interface_summary=summarize,
                   alternate_schema=scoring_schema or SCHEMA,
                   alternate_status=scoring_status or STATUS,
                   interface_case_features=scoring_case_features,
                   interface_finalize=scoring_finalize,
                   alternate_report_filename=scoring_report_name,
                   alternate_ledger_filename=scoring_ledger_name,**kwargs)
    if result['results']['counterfactual_probe']['reachable_graphs']['recovered_typed']!=8 or \
       result['results']['counterfactual_probe']['both_reachable_pairs']['recovered_typed']!=1:
        refuse('PARENT51_COVERAGE_REPLAY_DRIFT')
    return result
