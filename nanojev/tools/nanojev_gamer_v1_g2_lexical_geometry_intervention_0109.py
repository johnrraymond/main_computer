"""G2-0109: frozen G2-0103 post-decoder intervention and independent challenge.

    python -m tools.nanojev_gamer_v1_g2_lexical_geometry_intervention_0109 audit
    python -m tools.nanojev_gamer_v1_g2_lexical_geometry_intervention_0109 selftest
    python -u -m tools.nanojev_gamer_v1_g2_lexical_geometry_intervention_0109 run --device cuda

Read-only model evaluation. Original 275 are reused-fit and NOT independent
certification. A *new*, fixed-seed challenge uses author-annotated graphs with
aliases, pronouns and quantities appearing before their action. It reports
intrinsic rule counterexamples separately from frozen-model, G1-dependent
inference. No training, no label-dependent inference, no production cutover.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter,defaultdict
import hashlib
import json
import random
from pathlib import Path

import torch
from tools import nanojev_gamer_v1_g2_semantic_evidence_audit_0108 as prior
from tools import nanojev_gamer_v1_g2_checkpoint_forensics_0106 as forensics
from tools import nanojev_gamer_v1_g2_raw_graph_reranking_0103 as rerank
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as graph
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as enc
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as cv
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as competence
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_contract as contract
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_router_continuation_0099 as continuation
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_g2_error_atlas_0093 as atlas

TAG='G2_0109_LEXICAL_GEOMETRY'
SCHEMA='nanojev.g2.lexical-geometry-intervention-0109.v1'
SEED=109091
DEFAULT_BASES=12
REL=tuple(enc.BIND_CLASSES)
REF=REL.index('refers_to')
APPLY=REL.index('applies_to')
MODES=('control','alias','pronoun','forward')


def require(ok,reason):
    if not ok: raise ValueError(TAG+'_'+reason)


def emit(name,datum):
    print(TAG+'_'+name+' '+json.dumps(datum,sort_keys=True,allow_nan=False,ensure_ascii=False),flush=True)


def sha_text(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _span_text(prompt,node):
    a,z=node['span'];return prompt[a:z]


def _rewrite_node_text(row, node_id, replacement):
    """Rewrite one annotated mention, remapping all subsequent spans exactly."""
    row=copy.deepcopy(row);text=row['prompt']; nodes=row['graph']['nodes']
    node=next(n for n in nodes if n['id']==node_id)
    a,z=node['span']; require(0<=a<z<=len(text),'EDIT_SPAN_INVALID')
    require(replacement and replacement.strip()==replacement,'BAD_REPLACEMENT')
    new=text[:a]+replacement+text[z:]; delta=len(replacement)-(z-a)
    for n in nodes:
        if 'span' not in n:continue
        x,y=n['span']
        if n['id']==node_id:n['span']=[a,a+len(replacement)]
        elif x>=z:n['span']=[x+delta,y+delta]
        else:require(y<=a,'OVERLAPPING_AUTHOR_SPANS')
    row['prompt']=new
    _sync_spans(row)
    return row


def _sync_spans(row):
    prompt=row['prompt']
    spans=[]
    for n in row['graph']['nodes']:
        if 'span' not in n:continue
        a,z=n['span'];require(0<=a<z<=len(prompt),'MAPPED_SPAN_INVALID')
        require(prompt[a:z].strip()==prompt[a:z],'SPAN_WITH_WHITESPACE')
        spans.append({'kind':n['kind'],'start':a,'end':z})
    row['object_spans']=spans
    contract.validate_graph(row['graph'],complete=True)
    require(contract.execute_graph(row['graph'])['decisions']==row['expected_decisions'],
            'ANNOTATED_GRAPH_SEMANTICS_CHANGED')


def _ref_node(row):
    links=[edge for edge in row['graph']['edges'] if edge['type']=='refers_to']
    require(bool(links),'NO_REFERRING_MENTION')
    nodes={n['id']:n for n in row['graph']['nodes']}
    for edge in links:
        src,dst=nodes[edge['src']],nodes[edge['dst']]
        if src['kind']=='action' and dst['kind']=='action' and _span_text(row['prompt'],src)==_span_text(row['prompt'],dst):
            return src['id'],_span_text(row['prompt'],dst)
    raise ValueError(TAG+'_REF_SOURCE_TARGET_ALIGNMENT_UNAVAILABLE')


def _forward_quantity(row):
    """Reorder an existing encounter payoff, or allocation benefit/cost clause.

    Preserves all annotated text verbatim, node IDs, gold edges and objective;
    only text order changes. Creates an actual gold applies_to edge where
    quantity precedes the action it belongs to.
    """
    row=copy.deepcopy(row);oldtext=row['prompt']
    nodes={n['id']:n for n in row['graph']['nodes'] if 'span' in n}
    candidates=[]
    for edge in row['graph']['edges']:
        if edge['type']!='applies_to':continue
        src=nodes.get(edge['src']);dst=nodes.get(edge['dst'])
        if not src or not dst or src['kind']!='quantity' or dst['kind']!='action':continue
        if src['span'][0]<=dst['span'][1]:continue
        candidates.append((src,dst))
    require(bool(candidates),'FORWARD_GOLD_CANDIDATE_MISSING')
    src,dst=candidates[0]
    original_node_text={node_id:oldtext[node['span'][0]:node['span'][1]]
                        for node_id,node in nodes.items()}
    if row['family']=='allocation':
        # NAME benefit B cost C -> benefit B cost C for NAME
        # Both original quantities must precede the same gold action.
        a=dst['span'][0]
        # Find the current clause's right boundary, without crossing the next plan.
        semi=oldtext.find(';',a)
        if semi<0:semi=oldtext.find('.',a)
        require(semi>a,'ALLOCATION_CLAUSE_BOUNDARY_MISSING')
        z=max(node['span'][1] for node in nodes.values()
              if node['kind']=='quantity' and dst['span'][1]<node['span'][0]<semi)
        require(oldtext[dst['span'][1]:src['span'][0]].startswith(' benefit '),
                'ALLOCATION_CLAUSE_FORMAT_DRIFT')
        name=oldtext[dst['span'][0]:dst['span'][1]]
        rest=oldtext[dst['span'][1]:z]
        require(rest.startswith(' benefit ') and ' cost ' in rest,
                'ALLOCATION_BENEFIT_COST_PATTERN_DRIFT')
        newseg=rest.lstrip()+' for '+name
        where={dst['id']:(a+len(newseg)-len(name),a+len(newseg))}
        shift=-(len(name)+1)
        for node in nodes.values():
            x,y=node['span']
            if dst['span'][1]<=x and y<=z:
                where[node['id']]=(x+shift,y+shift)
    else:
        # choosing X against Y yields Q -> Q is the payoff that choosing X against Y yields
        z=src['span'][1];number=oldtext[src['span'][0]:z]
        a=oldtext.rfind('choosing ',0,dst['span'][0]+1)
        require(a>=0 and a<=dst['span'][0] and z-a<150,
                'ENCOUNTER_CLAUSE_FORMAT_DRIFT')
        stem=oldtext[a:src['span'][0]].rstrip()
        require(' yields' in stem and 'choosing ' in stem,
                'ENCOUNTER_PAYOFF_FORMAT_DRIFT')
        prefix=number+' is the payoff that '
        newseg=prefix+stem
        where={src['id']:(a,a+len(number))}
        shift=len(prefix)
        for node in nodes.values():
            x,y=node['span']
            if a<=x and y<=src['span'][0]:
                where[node['id']]=(x+shift,y+shift)
    newtext=oldtext[:a]+newseg+oldtext[z:]
    delta=len(newseg)-(z-a)
    for node in nodes.values():
        x,y=node['span']
        if node['id'] in where:node['span']=list(where[node['id']])
        elif x>=z:node['span']=[x+delta,y+delta]
        else:require(y<=a,'FORWARD_OVERLAPPING_NODE_SPANS')
    row['prompt']=newtext
    _sync_spans(row)
    # Catch corruption of *any* moved or untouched author annotation.
    texts={n['id']:row['prompt'][n['span'][0]:n['span'][1]]
           for n in row['graph']['nodes'] if 'span' in n}
    require(texts==original_node_text,'TEXT_MOVEMENT_OR_AUTHOR_SPAN_CORRUPTION')
    return row


def _challenge_suite(bases=DEFAULT_BASES):
    require(type(bases) is int and bases>=4 and bases%2==0 and bases<=40,
            'INVALID_CHALLENGE_BASE_COUNT')
    allrows=[];seen=set()
    for i in range(bases):
        family=('allocation' if i%2==0 else 'encounter')
        rng=random.Random(SEED+1009*i)
        base=(corpus.allocation(rng,'challenge',1_090_000+4*i) if family=='allocation'
              else corpus.encounter(rng,'challenge',1_090_000+4*i))
        ident=f'g2-0109-{i:05d}'
        source_id,target=_ref_node(base)
        alias='AliasK'+str(i).zfill(3)
        a=_rewrite_node_text(base,source_id,alias)
        a['prompt']+=f' Alias note: {alias} is another name for {target}.'
        p=_rewrite_node_text(base,source_id,'it')
        p['prompt']+=f" Pronoun note: the single word 'it' in the numerical clause denotes {target}."
        f=_forward_quantity(base)
        for mode,row in zip(MODES,(base,a,p,f)):
            row=copy.deepcopy(row)
            # Appended explanatory text has no new annotated graph nodes.
            _sync_spans(row)
            row.update({'case_id':ident+'-'+mode,'base_id':ident,
                        'mode':mode,'family':family,'original_split':'0109-independent-challenge',
                        'split':'challenge','annotation_provenance':'AUTHOR_MARKED_IN_SOURCE_GENERATOR'})
            sig=sha_text(row['prompt']);require(sig not in seen,'DUPLICATE_CHALLENGE_PROMPT');seen.add(sig)
            allrows.append(row)
    require(len(allrows)==4*bases,'CHALLENGE_CASE_COUNT_DRIFT')
    return allrows


def _rule_reasons(prompt,objects,i,j,t):
    """Deployment-shaped rule: NO labels, IDs, mode, family or model internals."""
    if i==j or not (0<=i<len(objects) and 0<=j<len(objects)):
        raise ValueError(TAG+'_INVALID_FILTER_PAIR')
    s=objects[i];dest=objects[j]
    x,y=int(s[0]),int(s[1]);a,z=int(dest[0]),int(dest[1])
    require(0<=x<y<=len(prompt) and 0<=a<z<=len(prompt),'FILTER_SPAN_INVALID')
    if int(t)==REF and prompt[x:y].strip().casefold()!=prompt[a:z].strip().casefold():
        return 'refers_to_nonidentical_names'
    if int(t)==APPLY and x<a:
        return 'applies_to_forward_quantity'
    return None


def apply_filter(prompt,objects,graph_matrix):
    """A pure post-decoder veto. Existing graph labels only; never adds edges."""
    require(graph_matrix.shape==(len(objects),len(objects)),'GRAPH_SHAPE_INVALID')
    output=graph_matrix.clone();reasons=Counter();sample=[]
    for i,j in (graph_matrix>0).nonzero(as_tuple=False).tolist():
        t=int(graph_matrix[i,j]);reason=_rule_reasons(prompt,objects,i,j,t)
        if reason:
            output[i,j]=0;reasons[reason]+=1
            sample.append({'i':i,'j':j,'relation':REL[t],'source':prompt[objects[i][0]:objects[i][1]],
                           'destination':prompt[objects[j][0]:objects[j][1]],'reason':reason})
    require(not bool(((graph_matrix==0)&(output!=0)).any()),'FILTER_ADDED_EDGE')
    return output,reasons,sample


def _truth_matrix(row):
    """Author's exact span-level gold; used ONLY to audit rule validity."""
    nodes=[n for n in row['graph']['nodes'] if 'span' in n];index={n['id']:i for i,n in enumerate(nodes)}
    out=torch.zeros((len(nodes),len(nodes)),dtype=torch.long)
    for edge in row['graph']['edges']:
        if edge['type'] in REL and edge['src'] in index and edge['dst'] in index:
            i,j=index[edge['src']],index[edge['dst']]
            require(i!=j and out[i,j]==0,'DUPLICATE_GOLD_BINDING')
            out[i,j]=REL.index(edge['type'])
    objects=[[n['span'][0],n['span'][1],n['kind']] for n in nodes]
    return objects,out


def _rule_gold_audit(rows):
    counts=defaultdict(Counter);examples={k:[] for k in MODES}
    for row in rows:
        objs,gold=_truth_matrix(row)
        filtered,reasons,removed=apply_filter(row['prompt'],objs,gold)
        bucket=counts[row['mode']]
        bucket.update({'cases':1,'gold_edges':int((gold>0).sum()),
                       'gold_edges_destroyed':sum(reasons.values())})
        bucket.update(reasons)
        if row['mode']=='forward':
            require(reasons['applies_to_forward_quantity']>=1,
                    'FORWARD_COUNTEREXAMPLE_MISSING_IN_CASE_'+row['case_id'])
        if removed and len(examples[row['mode']])<3:
            examples[row['mode']].append({'case_id':row['case_id'],'family':row['family'],
                 'deleted_true_edges':removed[:3],'prompt':row['prompt']})
        require(int((filtered!=gold).sum())==sum(reasons.values()),'RULE_GOLD_ACCOUNTING_DRIFT')
    for name in MODES:
        require(counts[name]['cases']==len(rows)//4,'CHALLENGE_MODE_BALANCE_DRIFT')
    require(counts['control']['gold_edges_destroyed']==0,'CLEAN_CONTROL_RULE_FAILED')
    require(counts['alias']['refers_to_nonidentical_names']>=len(rows)//4,
            'ALIAS_COUNTEREXAMPLE_ABSENT')
    require(counts['pronoun']['refers_to_nonidentical_names']>=len(rows)//4,
            'PRONOUN_COUNTEREXAMPLE_ABSENT')
    require(counts['forward']['applies_to_forward_quantity']>=len(rows)//4,
            'FORWARD_COUNTEREXAMPLE_ABSENT')
    return {k:dict(v) for k,v in sorted(counts.items())},examples


def _case_result(rec,raw,filtered,reasons):
    oldc=competence._count(rec,raw);newc=competence._count(rec,filtered)
    require(newc['tp']+newc['fp']<=oldc['tp']+oldc['fp'],'POSTFILTER_ADDED_PREDICTIONS')
    return oldc,newc,{'removed_tp':oldc['tp']-newc['tp'],
                      'removed_fp':oldc['fp']-newc['fp'],
                      'removed_edges':int((raw!=filtered).sum()),
                      **{f'reason_{k}':v for k,v in reasons.items()}}


def _eval_one(rec,prompt,stage):
    original=stage['graph'].cpu().long();actual=rec['item']['objects']
    filtered,reasons,vetoes=apply_filter(prompt,actual,original)
    a,b,impact=_case_result(rec,original,filtered,reasons)
    require(impact['removed_edges']==sum(reasons.values()),'VETO_COUNT_DRIFT')
    return a,b,impact,vetoes


def _public_source_audit(rows, source):
    original={r['prompt_sha256'] for r in atlas.jsonl(source[1]/'seal'/'prompts.jsonl')}
    require(all(sha_text(r['prompt']) not in original for r in rows),'PROMPT_LEAK_IN_0093')


def _manifest_path(source):return source[8]/'lexical-geometry-intervention-0109'/'challenge.json'


def _prepare_challenge(source,bases):
    rows=_challenge_suite(bases);_public_source_audit(rows,source)
    audit,examples=_rule_gold_audit(rows)
    path=_manifest_path(source)
    payload={'schema':SCHEMA,'seed':SEED,'bases':bases,'count':len(rows),
             'rows':rows,'prompt_sha256s':[sha_text(x['prompt']) for x in rows],
             'author_gold_rule_audit':audit,'author_gold_counterexamples':examples,
             'generation':'fresh_corpus_challenge_pool_author_annotated',
             'before_model_inference':True,'no_training':True}
    data=json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2)+'\n'
    if path.exists():
        require(path.read_text(encoding='utf-8')==data,
                'IMMUTABLE_CHALLENGE_CHANGED_REFUSE_EXISTING_MANIFEST')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as stream:stream.write(data)
    return path,payload


def _challenge_capture(source,experts,spec,device,rows):
    """Genuine frozen G1 and seven frozen G2 experts on *new* prompt texts.

    Any G1 misses remain part of end-to-end metrics, not excluded or repaired
    using gold spans. G1 exact is reported separately for conditional analysis.
    """
    root,atlas_dir,commit,parent_dir,*_=source
    paths=competence.atlas._roots(root)
    parent_record,_=hard._verify_trained(paths,paths['parent_0087'])
    net,tok=backbone.model_components(parent_record['model_id'],paths['adapter'],device)
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(parent_dir,device=device)
    old._mutable(head)
    def unavailable(row,reason):
        # Failed G1 char-span capture is an end-to-end failure, not a reason
        # to remove that hard case from the challenge's denominator.
        return {'row':row,'objects':[],'targets':torch.zeros((0,0),dtype=torch.long),
                'full_gold':atlas._gold_edges(row['graph']),
                'g1_exact':False,'capture_error':reason}
    try:
        items=old._capture(net,tok,composite,rows,device,must_exact=False)
    except ValueError as exc:
        # Only the precise upstream char mapping error admits a fallback.
        # All other failures (weights, bad graph/gold, decoder, authentication)
        # remain hard errors. No gold objects are substituted into the model.
        if 'UNMAPPED_G1_SPAN' not in str(exc):raise
        emit('G1_CAPTURE_FALLBACK',{'error':'UNMAPPED_G1_SPAN',
                 'action':'isolate_cases_and_count_as_failed_G1'})
        items=[]
        for row in rows:
            try:items.extend(old._capture(net,tok,composite,[row],device,must_exact=False))
            except ValueError as one:
                if 'UNMAPPED_G1_SPAN' not in str(one):raise
                items.append(unavailable(row,'UNMAPPED_G1_SPAN'))
    viable=[item for item in items if len(item['objects'])>=2]
    records=(competence._capture_experts(head,viable,device,spec,experts,
                                        '0109-independent-challenge',training=False)
             if viable else [])
    for item in items:
        if len(item['objects'])>=2:continue
        record={'item':{k:item[k] for k in ('row','objects','targets','full_gold','g1_exact')},
                'identity':{},'capture_error':item.get('capture_error','INSUFFICIENT_G1_NODES')}
        record['item']['row']={k:item['row'][k] for k in ('case_id','base_id','mode','family')}
        records.append(record)
    del net,tok,composite,head,items
    require(len(records)==len(rows),'CHALLENGE_CAPTURE_COUNT_DRIFT')
    require({rec['item']['row']['case_id'] for rec in records}=={r['case_id'] for r in rows},
            'CHALLENGE_CAPTURE_ID_DRIFT')
    return records


def _metric_counter(c):return competence._metric(c)


def _score_records(records,prompts,fold,spec,bundle,models,choice,device,verify_original=None):
    result={'baseline':Counter(),'filtered':Counter(),'impact':Counter(),'by_mode':defaultdict(lambda:{'baseline':Counter(),'filtered':Counter(),'impact':Counter()}),
            'by_family':defaultdict(lambda:{'baseline':Counter(),'filtered':Counter()}),
            'case_level':[]}
    for rec in records:
        case_id=rec['item']['row']['case_id']
        require(case_id in prompts,'MISSING_VERIFIED_PROMPT_'+case_id)
        if len(rec['item']['objects'])<2:
            # New challenge can fail G1. Keep those cases in *end-to-end*
            # counts rather than selecting only cases easy for G1.
            empty=torch.zeros((len(rec['item']['objects']),)*2,dtype=torch.long)
            stage={'graph':empty}
        else:
            stage=forensics._stage(rec,fold['experts'],spec,device,bundle,models,choice,probe=False)
            if verify_original:
                actual=rerank._infer(rec,fold['experts'],spec,device,bundle,choice['arm'],
                    models.get(choice['arm']),**choice['params'])
                require(torch.equal(stage['graph'],actual),'FROZEN_0103_INFERENCE_MISMATCH')
        a,b,impact,vetoes=_eval_one(rec,prompts[case_id],stage)
        result['baseline'].update(a);result['filtered'].update(b);result['impact'].update(impact)
        mode=rec['item']['row']['mode'];family=rec['item']['row']['family']
        group=result['by_mode'][mode];group['baseline'].update(a);group['filtered'].update(b);group['impact'].update(impact)
        fam=result['by_family'][family];fam['baseline'].update(a);fam['filtered'].update(b)
        result['case_level'].append({'case_id':case_id,'family':family,'mode':mode,
            'g1_exact':bool(rec['item']['g1_exact']),
            'inference_skipped_insufficient_g1_nodes':len(rec['item']['objects'])<2,
            'original':dict(a),'filtered':dict(b),'removed':impact,'vetoes':vetoes[:8]})
    return result


def _public_result(part,include_cases=False):
    document={'baseline':_metric_counter(part['baseline']),
              'filtered':_metric_counter(part['filtered']),
              'impact':dict(part['impact']),
              'by_mode':{name:{k:_metric_counter(v) if k in ('baseline','filtered') else dict(v)
                          for k,v in value.items()} for name,value in sorted(part['by_mode'].items())},
              'by_family':{name:{k:_metric_counter(v) for k,v in value.items()}
                           for name,value in sorted(part['by_family'].items())}}
    if include_cases:document['case_level']=part['case_level']
    return document


def audit(root=old.ROOT_DEFAULT,output=None,bases=DEFAULT_BASES):
    source,experts,r99,r100,r101,r102,p102,report,origin,p107=prior._load(root,output)
    p108=source[8]/'semantic-evidence-audit-0108'/'result.json'
    require(p108.is_file(),'RUN_G2_0108_FIRST')
    prior_doc=old.read(p108)
    require(prior_doc.get('schema')==prior.SCHEMA and prior_doc.get('source_0103_sha256')==old.digest(origin)
            and prior_doc.get('source_0107_sha256')==old.digest(p107)
            and prior_doc.get('replay',{}).get('exact')==61 and prior_doc.get('new_training') is False,
            '0108_LINEAGE_DRIFT')
    rows=_challenge_suite(bases);_public_source_audit(rows,source)
    gold,examples=_rule_gold_audit(rows)
    emit('AUDIT',{'ready':True,'original_heldout_cases':275,'new_challenge_cases':len(rows),
                  'new_base_ids':bases,'control_alias_pronoun_forward':gold,
                  'frozen_replay_exact':report['candidate']['exact'],
                  'g3_unlocked':False,'new_training':False,'checkpoint_writes':False,
                  'challenge_gold_only_for_evaluation':True,
                  'reused_fit_heldout_not_independent':True})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',bases=DEFAULT_BASES):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),'CUDA_UNAVAILABLE')
    source,experts,r99,r100,r101,r102,p102,report,origin,p107=prior._load(root,output)
    p108=source[8]/'semantic-evidence-audit-0108'/'result.json'
    require(p108.is_file(),'RUN_G2_0108_FIRST')
    doc108=old.read(p108)
    require(doc108.get('schema')==prior.SCHEMA and doc108.get('replay') and
            all(doc108['replay'].get(k)==report['candidate'].get(k)
                for k in ('tp','fp','fn','exact','cases')) and
            doc108.get('source_0103_sha256')==old.digest(origin) and
            doc108.get('source_0107_sha256')==old.digest(p107) and
            doc108.get('new_training') is False and doc108.get('production_promoted') is False,
            '0108_LINEAGE_OR_REPLAY_DRIFT')
    result_path=source[8]/'lexical-geometry-intervention-0109'/'result.json'
    require(not result_path.exists(),'RESULT_EXISTS_READ_ONLY_NO_OVERWRITE')
    challenge_file,seal=_prepare_challenge(source,bases)
    require(seal['before_model_inference'] is True,'CHALLENGE_NOT_SEALED')
    emit('START',{'device':device,'challenge_cases':len(seal['rows']),
                   'new_training':False,'reused_fit_cases':275,
                   'gold_used_for_filters':False,'frozen_checkpoint_fold_models':3,
                   'challenge_sealed_sha256':old.digest(challenge_file)})
    records,spec=continuation._records(source,experts,device)
    prompts=prior._public_prompts(source,[rec['item']['row'] for rec in records])
    out=Counter();flt=Counter();impact=Counter();families=defaultdict(lambda:{'baseline':Counter(),'filtered':Counter()})
    result_by_fold=[]
    resources=[]
    with torch.inference_mode():
        for fold,(index,(train,val,test),base_counts) in enumerate(cv._split_by_base(records,3,rerank.SEED)):
            require(index==fold and base_counts==report['folds'][fold]['base_counts'],
                    'FROZEN_SPLIT_CHANGED')
            bundle=graph._bundle_from_0100(fold,r100['folds'][fold],r99,experts,device)
            models,manifest=rerank.load_candidate(Path(report['folds'][fold]['manifest']).parent,
                experts,device,old.digest(p102))
            choice=report['folds'][fold]['chosen']
            require(manifest['selected']==choice,'FROZEN_ARM_CHANGED')
            choice_resources={'experts':experts,'bundle':bundle,'models':models,'choice':choice}
            part=_score_records(test,{k:v['text'] for k,v in prompts.items()},choice_resources,
                                spec,bundle,models,choice,device,verify_original=True)
            expected=report['folds'][fold]['selected']['overall']
            require(all(part['baseline'][k]==expected[k] for k in ('tp','fp','fn','exact','cases')),
                    'FROZEN_CHECKPOINT_REPLAY_CHANGED_FOLD_'+str(fold))
            out.update(part['baseline']);flt.update(part['filtered']);impact.update(part['impact'])
            for family,value in part['by_family'].items():
                families[family]['baseline'].update(value['baseline'])
                families[family]['filtered'].update(value['filtered'])
            result_by_fold.append({'fold':fold,'selected':choice,'metrics':_public_result(part)})
            resources.append(choice_resources)
            emit('FROZEN_FOLD',{'fold':fold,'before':_metric_counter(part['baseline']),
                                 'after':_metric_counter(part['filtered']),
                                 'removed':part['impact']['removed_edges']})
        require(all(out[k]==report['candidate'][k] for k in ('tp','fp','fn','exact','cases')),
                'FROZEN_GLOBAL_REPLAY_CHANGED')
        challenging=_challenge_capture(source,experts,spec,device,seal['rows'])
        by_id={x['case_id']:x for x in seal['rows']}
        require(len({x['prompt'] for x in seal['rows']})==len(seal['rows']),
                'DUPLICATE_INDEPENDENT_PROMPTS')
        independent=[]
        for fold,resource in enumerate(resources):
            part=_score_records(challenging,{k:v['prompt'] for k,v in by_id.items()},resource,
                  spec,resource['bundle'],resource['models'],resource['choice'],device,
                  verify_original=True)
            independent.append({'fold':fold,'selected':resource['choice'],
                                'metrics':_public_result(part,include_cases=True)})
            emit('CHALLENGE_FOLD',{'fold':fold,'before':_metric_counter(part['baseline']),
                  'after':_metric_counter(part['filtered']),
                  'g1_exact_cases':part['baseline']['g1_exact'],
                  'removed_true':part['impact']['removed_tp'],
                  'removed_false':part['impact']['removed_fp']})
    theoretical={'precision':0.9589,'exact':92,'removed_fp':438,'removed_tp':0}
    emit('THEORY_CHECK',{'original_calculated_projection':theoretical,
         'measured':{'precision':_metric_counter(flt)['precision'],
                     'exact':flt['exact'],'removed_fp':impact['removed_fp'],
                     'removed_tp':impact['removed_tp']},
         'projection_not_assumed_true':True})
    orig_result={'baseline':_metric_counter(out),'filtered':_metric_counter(flt),
                 'impact':dict(impact),
                 'by_family':{k:{s:_metric_counter(val[s]) for s in ('baseline','filtered')}
                              for k,val in families.items()},
                 'folds':result_by_fold,'earlier_theoretical_projection':theoretical}
    report_out={'schema':SCHEMA,'original_reused_fit':orig_result,
                'independent_challenge':{'sealed_sha256':old.digest(challenge_file),
                  'count':len(seal['rows']),'base_ids':bases,'seed':SEED,
                  'author_gold_counterexamples':seal['author_gold_rule_audit'],
                  'examples':seal['author_gold_counterexamples'],
                  'folds':independent,
                  'interpretation':'Each fold model independently evaluated on the SAME new cases. G1 failures included in end-to-end numbers; author gold audits do not depend on G1.'},
                'source_0103_sha256':old.digest(origin),'source_0108_sha256':old.digest(p108),
                'checkpoint_writes':False,'new_training':False,'production_promoted':False,
                'confirmation_used':False,'g3_unlocked':False,
                'research_only':True,
                'original_not_independent_certification':True,
                'independent_challenge_not_general_language_certification':True}
    result_path.parent.mkdir(parents=True,exist_ok=True)
    old.write(result_path,report_out)
    emit('DIAGNOSIS',{'original_reused_fit':{k:orig_result[k] for k in ('baseline','filtered','impact','by_family')},
                       'independent_gold_counterexamples':seal['author_gold_rule_audit'],
                       'challenge_folds':[{'fold':x['fold'],
                             'baseline':x['metrics']['baseline'],'filtered':x['metrics']['filtered'],
                             'impact':x['metrics']['impact']} for x in independent]})
    emit('SAVED',{'report':str(result_path),'challenge_seal':str(challenge_file),
                   'sha256':old.digest(result_path), 'checkpoints_unchanged':True,
                   'production_promoted':False})


def selftest():
    # First test independently-authored counterexamples and source span remapping.
    cases=_challenge_suite(6)
    audit,examples=_rule_gold_audit(cases)
    assert audit['control']['gold_edges_destroyed']==0
    assert audit['alias']['refers_to_nonidentical_names']>=6
    assert audit['pronoun']['refers_to_nonidentical_names']>=6
    assert audit['forward']['applies_to_forward_quantity']>=6
    # Model-facing policy does not accept target, IDs, mode, or family.
    n=len(REL)
    prompt='Alpha pays 17 for Beta. Alpha moves.'
    objects=[[0,5,'action'],[11,13,'quantity'],[18,22,'action'],[24,29,'action']]
    mat=torch.zeros((4,4),dtype=torch.long);mat[0,2]=REF;mat[1,2]=APPLY;mat[0,3]=REF
    flt,counts,vetoes=apply_filter(prompt,objects,mat)
    assert int(flt[0,2])==0 and int(flt[1,2])==0 and int(flt[0,3])==REF
    assert sum(counts.values())==2
    assert int((flt>0).sum())==int((mat>0).sum())-2
    # Correct edges with same-name references and backward applies_to survive.
    prompt='Alpha pays 17 for Alpha';o=[[0,5,'action'],[11,13,'quantity'],[18,23,'action']]
    mat=torch.zeros((3,3),dtype=torch.long);mat[2,0]=REF;mat[1,0]=APPLY
    v,_,_=apply_filter(prompt,o,mat)
    assert torch.equal(v,mat)
    # An unparseable new prompt must stay in the end-to-end denominator.
    hardrow=cases[1]
    zero={'item':{'objects':[],'targets':torch.zeros((0,0),dtype=torch.long),
          'full_gold':atlas._gold_edges(hardrow['graph']),'g1_exact':False,
          'row':{k:hardrow[k] for k in ('case_id','base_id','mode','family')}}}
    unavailable=_score_records([zero],{hardrow['case_id']:hardrow['prompt']},
             {'experts':{}}, {}, {}, {}, {}, 'cpu',verify_original=True)
    assert unavailable['baseline']['cases']==1 and unavailable['baseline']['g1_exact']==0
    assert unavailable['baseline']['fn']>0 and unavailable['baseline']['exact']==0
    assert unavailable['case_level'][0]['inference_skipped_insufficient_g1_nodes']
    # Explicitly demonstrate the intervention's failure mode on new gold.
    assert all(audit[x]['gold_edges_destroyed']>0 for x in ('alias','pronoun','forward'))
    emit('SELFTEST',{'fresh_author_annotated_cases':len(cases),
                    'independent_lexical_counterexamples':True,
                    'forward_gold_false_veto_detected':True,
                    'alias_and_pronoun_false_veto_detected':True,
                    'g1_failures_kept_in_denominator':True,
                    'original_policy_filter_pure_no_gold':True,
                    'original_correct_relations_preserved_in_fixture':True,
                    'no_training':True,'checkpoint_writes':False})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='audit',choices=('audit','selftest','run'))
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--bases',type=int,default=DEFAULT_BASES)
    arg=parser.parse_args(argv)
    if arg.command=='selftest':selftest()
    elif arg.command=='audit':audit(arg.root,arg.output,arg.bases)
    else:run(arg.root,arg.output,arg.device,arg.bases)

if __name__=='__main__':main()
