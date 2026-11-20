"""G2-0089: gated exact-graph trainability, structural continuation, causal repair eligibility.

Run: python -m tools.nanojev_gamer_v1_g2_structural_learning_0089 run --device cuda

All training uses predicted G1 objects. Frozen G1/G3+ weights, original G2
thresholds, unmodified upstream checkpoints and NO automatic G3 promotion.
The development (trainability/arm selection) and sealed prospective phases are
separate processes. Oracle annotations are NEVER an input to blind inference.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import torch
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_g2_native_training_0083 as native
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as parent
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as previous
from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as fuzz
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, decode_graph

SCHEMA='nanojev.gamer.graph.g2.structural-learning-0089.v1'
MODULE='tools.nanojev_gamer_v1_g2_structural_learning_0089'
SEED=890089
TRAIN_ONE_STEPS=300
TRAIN_EIGHT_STEPS=800
CONTINUATION_STEPS=400
LR=5e-5
ACCUM=4
DEV=96
CHALLENGE=64
FUZZ_PAIRS=8
STRUCTURED_WEIGHT=2.0
CONSISTENCY_WEIGHT=0.1
ROOT_DEFAULT='../runtime/reports/gamer-v1'


def require(ok,code):
    if not ok:raise ValueError('G2_0089_'+code)


def sha(path):return parent.sha(path)
def js(path):return parent.js(path)
def write_json(path,obj):return parent.write_json(path,obj)
def read_jsonl(path):return parent.read_jsonl(path)
def write_jsonl(path,rows):return parent.write_jsonl(path,rows)


def paths_for(root):
    paths=previous.paths_for(root)
    paths['parent_0088']=Path(root)/'oracle-fabric-g2-posttrain-graph-bakeoff-0088'
    paths['causal_0086']=Path(root)/'oracle-fabric-g2-within-type-causal-discrimination-0086'
    return paths


def preflight(paths):
    # Both earlier experiments have independently audited, hash-bound records.
    parent_manifest,_,_=previous.ensure_parent(paths)
    old=paths['parent_0088'];commit=previous.check_seal(paths,old)
    report=js(old/'scored'/'report.json')
    sel=js(old/'selection.json')
    require(report.get('status')=='G2_0088_GATE_BLOCKED' and
            report.get('g3_training_unlocked') is False and
            sel.get('status')=='DECODER_FROZEN_BEFORE_NEW_INFERENCE' and
            sel.get('winner_method')=='joint' and
            sha(old/'selection.json')==commit['selection_sha256'] and
            sha(old/'blind'/'predictions.jsonl')==js(old/'blind'/'lock.json')['predictions_sha256'],
            '0088_EVIDENCE_DRIFT')
    require(parent_manifest['head_sha256']==commit['parent_head_sha256'],
            'PARENT_WEIGHT_MISMATCH')
    require(cur.GATE_REQUIREMENTS['G2']['dev']==.80 and
            cur.GATE_REQUIREMENTS['G2']['challenge']==.70 and
            cur.GATE_REQUIREMENTS['G2']['neural_gain']==.08,
            'ORIGINAL_GATE_CHANGED')
    return parent_manifest,sel['winner']


def causal_target(paths):
    """C is forbidden without causally localized evidence ON THE CURRENT head.

    Historical G2-0086 targeted G2-0083, not the post-0087 checkpoint;
    transplant changes from that older state cannot license new weight edits.
    """
    source=paths['causal_0086']/'report.json'
    if not source.is_file():
        return {'eligible':False,'reason':'NO_LOCALIZED_REPAIR_EVIDENCE'}
    report=js(source)
    require(report.get('checkpoint_mutation') is False and
            report.get('independent_certification') is False,
            'CAUSAL_REPORT_LINEAGE_INVALID')
    current=sha(paths['parent_0087']/'training'/cur.HEAD_FILE)
    if report.get('checkpoint_sha256') != current:
        return {'eligible':False,'reason':'CAUSAL_EVIDENCE_ON_OLD_CHECKPOINT',
                'evidence_head_sha256':report.get('checkpoint_sha256'),
                'current_head_sha256':current,'source_sha256':sha(source)}
    data=report['summary']['interventions']['by_intervention']
    sham=data.get('sham_false:self_pair_input_control',{})
    enc=data.get('false_to_true:encoder',{})
    coupler=data.get('false_to_true:g1_g2_coupler',{})
    # Requires specificity against sham, a distinctive effect attributable to
    # the trainable coupler (NOT the frozen G1 encoder), and independent
    # selective-rescue evidence absent in historical 0086.
    selective=report.get('local_repair_validation',{})
    eligible=(sham.get('n',0)>=32 and abs(sham.get('mean_delta_typed_margin',999))<1e-4
              and enc.get('n',0)>=32 and coupler.get('n',0)>=32
              and coupler.get('mean_graph_decision_changes',0) >=
                  enc.get('mean_graph_decision_changes',0)+.25
              and coupler.get('mean_graph_decision_changes',0)>=.75
              and selective.get('heldout_tests',0)>=32
              and selective.get('false_positive_rescue_rate',0)>=.75
              and selective.get('true_positive_preservation_rate',0)>=.95)
    return {'eligible':bool(eligible),
            'reason':'LOCALIZED_CAUSAL_DIFFERENCE_PROVEN' if eligible
                 else 'NO_LOCALIZED_REPAIR_TARGET',
            'source_sha256':sha(source),
            'proposed_prefix':'interface_couplers.G1_G2.delta.' if eligible else None}


def _edges_to_targets(item,device):
    return parent.training_targets(item['spans'],item['ids'],item['row']['graph']['edges'],device=device)


def _oracle_surface(record,targets):
    """Gold-derived surface for trainability *diagnosis only*; never inference."""
    objs,_=previous.validate_surface(record)
    n=len(objs)
    require(targets.shape==(n,n),'ORACLE_TARGET_SHAPE')
    return {'g1_objects':objs,'binding_edges':[],
            'pair_margins':[[i,j,[20. if int(targets[i,j])==k+1 else -20.
                                   for k in range(len(BIND_CLASSES)-1)]]
                            for i in range(n) for j in range(n) if i!=j]}


def _gold_keys(record,targets):
    objs,_=previous.validate_surface(record)
    require(targets.shape==(len(objs),len(objs)),'GOLD_TARGET_SHAPE')
    return {previous.format_edge(objs,i,j,int(targets[i,j]))
            for i in range(len(objs)) for j in range(len(objs))
            if i!=j and int(targets[i,j])>0}


def _surface_from_logits(objs,logits):
    n=len(objs)
    require(logits.ndim==3 and logits.shape[:2]==(n,n) and
            logits.shape[-1]==len(BIND_CLASSES),'GRAPH_LOGIT_SHAPE')
    margins=(logits[... ,1:]-logits[...,0:1]).detach().float().cpu()
    return {'g1_objects':objs,'binding_edges':[],
            'pair_margins':[[i,j,margins[i,j].tolist()]
                            for i in range(n) for j in range(n) if i!=j]}


def graph_set_hinge(logits,targets,objs,spec):
    """Loss-augmented structured hinge through the SAME frozen graph decoder.

    Gold is used here only to construct training-time cost augmentation and
    the gold score. All decoder choices are detached; selected model logits
    remain differentiable. No fixed top-k truncation is introduced.
    """
    surface=_surface_from_logits(objs,logits)
    gold=_gold_keys(surface,targets)
    predicted=previous.decode(surface,spec)
    if predicted==gold:
        # Cost-augmented inference may still impose a margin when exact is won.
        exact=True
    else:exact=False
    aug={'g1_objects':surface['g1_objects'],'binding_edges':[], 'pair_margins':[]}
    for i,j,m in surface['pair_margins']:
        t=int(targets[i,j])
        aug['pair_margins'].append([i,j,[float(v)+(1. if (k+1)!=t else -1.)
                                  for k,v in enumerate(m)]])
    rival=previous.decode(aug,spec)
    lookup={(r[2],(r[0],r[1])):i for i,r in enumerate(objs)}
    margins=logits[...,1:]-logits[...,0:1]
    def weight(edges):
        items=[]
        for a,sa,b,sb,t in sorted(edges):
            i=lookup[(a,sa)];j=lookup[(b,sb)]
            items.append(margins[i,j,BIND_CLASSES.index(t)-1])
        return torch.stack(items).sum() if items else logits.sum()*0
    cost=len(rival^gold)
    hinge=F.relu(weight(rival)-weight(gold)+float(cost))/max(1,len(gold))
    require(torch.isfinite(hinge).item(),'NONFINITE_GRAPH_HINGE')
    return hinge,{'exact':exact,'predicted_edges':len(predicted),
                  'gold_edges':len(gold),'rival_hamming':cost}


def objective(logits,targets,spans,objs,spec):
    base,_,stats=parent.loss_components(logits,targets,spans)
    hinge,metric=graph_set_hinge(logits,targets,objs,spec)
    return base+STRUCTURED_WEIGHT*hinge,{'base_loss':float(base.detach()),
             'structured_loss':float(hinge.detach()),**stats,**metric}


def _item_capture(network,tok,composite,row,device):
    raw=cur.capture(network,tok,row,device,2048)
    hidden=raw['hidden'].detach().cpu().float()
    predicted=composite.predict(raw['hidden'].to(device),raw['prepared'],
                                family=row['family'],native_stage='G1')
    spans=predicted['token_spans']
    ids=cur.composite_binding_labels(raw['prepared'],row,spans)
    gold=parent.training_targets(spans,ids,row['graph']['edges'],device='cpu')
    return {'row':row,'hidden':hidden,'eligible':raw['prepared']['eligible'].cpu(),
            'spans':spans,'ids':ids,'targets':gold,
            'prepared':raw['prepared'],'reachable':int((gold>0).sum())}


def _forward(head,item,device):
    n=len(item['spans'])
    return head(item['hidden'].to(device),eligible=item['eligible'].to(device),
                inference_spans=item['spans'],active_stage='G2')['binding_logits'][:n,:n]


def _serialize_edges(edges):
    return native._safe_edges({((a,sa),(b,sb),t) for a,sa,b,sb,t in edges})


def _surface(head,item,device):
    with torch.no_grad():
        return previous._infer(head,item['hidden'].to(device),item['eligible'].to(device),
                               item['spans'],item['prepared'],{},with_margins=True)


def _train_case(head,item,device,spec):
    logits=_forward(head,item,device)
    surface=_surface_from_logits(item['objects'],logits)
    return objective(logits,item['targets'].to(device),item['spans'],surface['g1_objects'],spec)


def _candidate_training_cases(network,tok,composite,head,paths,spec,device,needed=8):
    rows=[r for r in cur.load_rows(paths['dataset']) if r['split']=='train']
    require(rows,'NO_TRAIN_DATA')
    chosen=[];excluded=Counter();seen=set()
    # Interleave source families; use only original training split.
    streams={f:iter(r for r in rows if r['family']==f)
             for f in ('encounter','allocation')}
    for turn in range(min(len(rows),600)):
        fam=('encounter','allocation')[turn%2]
        try:row=next(streams[fam])
        except StopIteration:continue
        if hashlib.sha256(row['prompt'].encode()).hexdigest() in seen:continue
        seen.add(hashlib.sha256(row['prompt'].encode()).hexdigest())
        item=_item_capture(network,tok,composite,row,device)
        full=parent.previous._gold_edges(row['graph'])
        if len(full)<1 or item['reachable']!=len(full):
            excluded['g1_unreachable']+=1;continue
        if len(item['spans'])<2:
            excluded['too_few_nodes']+=1;continue
        raw=_surface(head,item,device)
        item['objects']=raw['g1_objects']
        expected=_gold_keys(raw,item['targets'])
        require(expected==full,'TARGET_TO_AUTHOR_ALIGNMENT_DRIFT')
        if previous.decode(_oracle_surface(raw,item['targets']),spec)!=expected:
            excluded['decoder_capacity_ceiling']+=1;continue
        chosen.append(item)
        if len(chosen)==needed:break
    require(len(chosen)==needed,'NOT_ENOUGH_FULLY_REACHABLE_DECODABLE_TRAINING_CASES')
    return chosen,dict(excluded)


def _mutable(head,*,prefix=None):
    cur.trainable_stage(head,'G2')
    if prefix is not None:
        require(prefix in ('binding.','pair_projection.',
                'interface_couplers.G1_G2.delta.'),'UNAUTHORIZED_LOCAL_REPAIR')
        for name,p in head.named_parameters():p.requires_grad_(p.requires_grad and name.startswith(prefix))
    params=[p for p in head.parameters() if p.requires_grad]
    require(params,'NO_G2_TRAINABLE_WEIGHTS')
    return params


def overfit(head,items,spec,device,*,limit,lr=LR):
    require(len(items) in (1,8),'OVERFIT_CASE_COUNT')
    _mutable(head);params=[p for p in head.parameters() if p.requires_grad]
    optimizer=torch.optim.AdamW(params,lr=lr,weight_decay=0.)
    torch.manual_seed(SEED+len(items))
    history=[];last=0
    for step in range(1,limit+1):
        optimizer.zero_grad(set_to_none=True)
        # Exhaust all examples every update for a clean memorization diagnosis.
        totals=[]
        for item in items:
            loss,_=_train_case(head,item,device,spec)
            (loss/len(items)).backward()
            totals.append(float(loss.detach()))
        torch.nn.utils.clip_grad_norm_(params,1.)
        optimizer.step()
        if step==1 or step%10==0 or step==limit:
            with torch.no_grad():
                correct=0
                for r in items:
                    surface=_surface(head,r,device)
                    correct+=int(previous.decode(surface,spec)==
                                 _gold_keys(surface,r['targets']))
            history.append({'step':step,'exact':correct,'of':len(items),
                            'mean_loss':sum(totals)/len(totals)})
            if step==1 or step%50==0 or correct==len(items) or step==limit:
                print('G2_0089_OVERFIT '+json.dumps({'cases':len(items),'step':step,
                   'max_steps':limit,'exact':correct,'last_loss':history[-1]['mean_loss']},
                   sort_keys=True),flush=True)
            last=correct
            if correct==len(items):break
    return {'cases':len(items),'exact':last,'passed':last==len(items),
            'steps':step,'last_loss':history[-1]['mean_loss'],
            'history':history}


def _save_head(head,source_manifest,path,paths,*,arm,detail):
    path.mkdir(exist_ok=False)
    save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},
              str(path/cur.HEAD_FILE))
    manifest=dict(source_manifest)
    manifest.update({'head_sha256':sha(path/cur.HEAD_FILE),'stage':'G2',
          'status':'TRAINED_NOT_CERTIFIED','next_training_gate':'LOCKED_PENDING_CERTIFICATION',
          'g3_training_unlocked':False,'production_promotion':False,
          'g2_0089':{'schema':SCHEMA,'arm':arm,'frozen_g1':True,'detail':detail,
                     'parent_head_sha256':source_manifest['head_sha256']}})
    write_json(path/'manifest.json',manifest)
    diff=native.compare_frozen_stage(paths['parent_0087']/'training',path)
    require(diff['non_g2_weights_mutated'] is False,'NON_G2_WEIGHTS_CHANGED')
    return manifest


def _load_runtime(paths,device):
    parent_record,_=parent._verify_trained(paths,paths['parent_0087'])
    require(parent_record['head_sha256']==sha(paths['parent_0087']/'training'/cur.HEAD_FILE),
            'PARENT_DRIFT')
    network,tok=backbone.model_components(parent_record['model_id'],paths['adapter'],device)
    require(not any(p.requires_grad for p in network.parameters()),'BACKBONE_NOT_FROZEN')
    composite=g1native.load_composite(paths['composite'],device=device)
    head,manifest=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
    require(manifest['head_sha256']==parent_record['head_sha256'],'HEAD_DRIFT')
    return network,tok,composite,head,manifest


def _training_provenance(paths,selection):
    return {'schema':SCHEMA,'source_sha256':sha(__file__),
            'dataset_sha256':sha(paths['dataset']),
            'g1_certificate_sha256':sha(paths['certificate']),
            'g1_composite_sha256':sha(paths['composite']/'composite-interface.json'),
            'parent_head_sha256':sha(paths['parent_0087']/'training'/cur.HEAD_FILE),
            '0088_selection_sha256':sha(paths['parent_0088']/'selection.json'),
            'decoder':selection,'seed':SEED,'one_steps':TRAIN_ONE_STEPS,
            'eight_steps':TRAIN_EIGHT_STEPS,'continuation_steps':CONTINUATION_STEPS,
            'structural_weight':STRUCTURED_WEIGHT,'consistency_weight':CONSISTENCY_WEIGHT,
            'g3_training_unlocked':False}


def _check_provenance(paths,dest):
    saved=js(dest/'provenance.json')
    require(saved['schema']==SCHEMA and saved['source_sha256']==sha(__file__) and
            saved['dataset_sha256']==sha(paths['dataset']) and
            saved['parent_head_sha256']==sha(paths['parent_0087']/'training'/cur.HEAD_FILE) and
            saved['0088_selection_sha256']==sha(paths['parent_0088']/'selection.json') and
            saved['g1_certificate_sha256']==sha(paths['certificate']) and
            saved['g1_composite_sha256']==sha(paths['composite']/'composite-interface.json') and
            saved['g3_training_unlocked'] is False,'PROVENANCE_DRIFT')
    return saved


def trainability(*,root,output,device='cuda'):
    paths=paths_for(root);dest=Path(output).resolve()
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_ALREADY_EXISTS')
    _,spec=preflight(paths)
    network,tok,composite,_,manifest=_load_runtime(paths,device)
    parent_head,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
    chosen,rejections=_candidate_training_cases(network,tok,composite,parent_head,
                                                paths,spec,device)
    # Seal the training-case identities before optimization.
    dest.mkdir(exist_ok=False)
    write_json(dest/'provenance.json',_training_provenance(paths,spec))
    training_id=[{'prompt_sha256':hashlib.sha256(it['row']['prompt'].encode()).hexdigest(),
                  'family':it['row']['family'],'reachable_edges':it['reachable']}
                 for it in chosen]
    write_json(dest/'trainability-selection.json',{'cases':training_id,
                                                   'rejections':rejections,
                                                   'oracle_decode_feasible':True})
    results=[]
    for count,limit in ((1,TRAIN_ONE_STEPS),(8,TRAIN_EIGHT_STEPS)):
        head,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
        record=overfit(head,chosen[:count],spec,device,limit=limit)
        results.append(record)
        print('G2_0089_TRAINABILITY '+json.dumps({k:record[k]
             for k in ('cases','exact','passed','steps','last_loss')},sort_keys=True),flush=True)
        if not record['passed']:break
        _save_head(head,manifest,dest/f'overfit-{count}',paths,
                   arm='MEMORIZATION_ONLY_NOT_DEPLOYABLE',detail=record)
    result={'schema':SCHEMA,'one_pass':bool(results and results[0]['passed']),
            'eight_pass':bool(len(results)==2 and results[1]['passed']),
            'results':results,'status':'EXACT_GRAPH_TRAINABILITY_PASSED'
              if len(results)==2 and results[1]['passed'] else 'EXACT_GRAPH_TRAINABILITY_BLOCKED',
            'g3_unlocked':False,'no_production_promotion':True}
    write_json(dest/'trainability.json',result)
    return result


def _capture_training_data(network,tok,composite,paths,device):
    rows=[r for r in cur.load_rows(paths['dataset']) if r['split']=='train']
    cache=[];counts=Counter()
    for i,r in enumerate(rows,1):
        item=_item_capture(network,tok,composite,r,device)
        if len(item['spans'])>=2 and item['reachable']:
            cache.append(item);counts[r['family']]+=1
        if i==1 or i%100==0 or i==len(rows):
            print(f'G2_0089_CAPTURE {i}/{len(rows)} predicted_G1',flush=True)
    require(all(counts[f]>0 for f in ('encounter','allocation')),'MISSING_TRAIN_FAMILY')
    return cache


def _capture_counterfactuals(network,tok,composite,device):
    pairs=fuzz.make_pairs(seed=SEED+1000,pairs=8)
    out=[]
    for group in pairs:
        base=_item_capture(network,tok,composite,group['base'],device)
        changed=_item_capture(network,tok,composite,group['fuzz'],device)
        out.append((group['mode'],base,changed))
    return out


def _consistency(head,left,right,mode,device):
    """Source-ID-aligned equivariance; unchanged subgraph for distractors.

    In action removal the gold edge set changes. Never penalize it for failing
    invariance; instead use the ordinary structure-supervision on both sides.
    """
    if mode=='action_removal':return None
    assert mode in ('renaming','role_swap','added_distractor')
    common=sorted(set(left['ids'])&set(right['ids']))
    if len(common)<2:return None
    a=_forward(head,left,device);b=_forward(head,right,device)
    ma=a[...,1:]-a[...,0:1];mb=b[...,1:]-b[...,0:1]
    pair_a=[];pair_b=[]
    for src in common:
        for dst in common:
            if src==dst:continue
            pair_a.append(ma[left['ids'][src],left['ids'][dst]])
            pair_b.append(mb[right['ids'][src],right['ids'][dst]])
    if not pair_a:return None
    return F.smooth_l1_loss(torch.stack(pair_a),torch.stack(pair_b))


def _fit_arm(paths,dest,device,network,tok,composite,spec,manifest,*,
             arm,prefix=None):
    head,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
    params=_mutable(head,prefix=prefix)
    cache=_capture_training_data(network,tok,composite,paths,device)
    counterfactual=_capture_counterfactuals(network,tok,composite,device)
    sampler={f:[r for r in cache if r['row']['family']==f]
             for f in ('encounter','allocation')}
    randomizer=random.Random(SEED);torch.manual_seed(SEED)
    optimizer=torch.optim.AdamW(params,lr=LR)
    log=[];stats=Counter()
    for step in range(1,CONTINUATION_STEPS+1):
        head.train();optimizer.zero_grad(set_to_none=True)
        losses=[]
        for micro in range(ACCUM):
            fam=('encounter','allocation')[((step-1)*ACCUM+micro)%2]
            item=randomizer.choice(sampler[fam]);n=len(item['spans'])
            # Author space objects are recovered from predicted spans; no gold
            # coordinates become neural inputs or candidate nodes.
            if 'objects' not in item:
                item['objects']=_surface(head,item,device)['g1_objects']
            loss,diag=_train_case(head,item,device,spec)
            (loss/ACCUM).backward();losses.append(float(loss.detach()))
            stats.update({'training_exact':int(diag['exact']),'training_cases':1})
        if step%8==0:
            mode,a,b=counterfactual[(step//8-1)%len(counterfactual)]
            for entry in (a,b):
                if len(entry['spans'])<2:continue
                if 'objects' not in entry:
                    entry['objects']=_surface(head,entry,device)['g1_objects']
                l,_=_train_case(head,entry,device,spec)
                (0.25*l).backward()
            invariant=_consistency(head,a,b,mode,device) if (
                len(a['spans'])>=2 and len(b['spans'])>=2) else None
            if invariant is not None:
                (CONSISTENCY_WEIGHT*invariant).backward();stats['consistency_updates']+=1
            stats['counterfactual_pairs_seen']+=1
        torch.nn.utils.clip_grad_norm_(params,1.)
        optimizer.step()
        if step==1 or step%50==0 or step==CONTINUATION_STEPS:
            report={'step':step,'of':CONTINUATION_STEPS,
                    'loss':sum(losses)/len(losses),
                    'training_exact_observed':stats['training_exact'],
                    'training_cases_observed':stats['training_cases']}
            log.append(report)
            print('G2_0089_STRUCTURED '+json.dumps(report,sort_keys=True),flush=True)
    saved=_save_head(head,manifest,dest/'arm-'+arm,paths,arm=arm,
        detail={'steps':CONTINUATION_STEPS,'lr':LR,'accum':ACCUM,
                'counterfactual_policy':'source-id-aligned consistency; action-removal supervised',
                'statistics':dict(stats),'train_log':log,'prefix':prefix})
    return saved


def _score_arm(head,item,device,spec):
    surface=_surface(head,item,device)
    gold=parent.previous._gold_edges(item['row']['graph'])
    predicted=previous.decode(surface,spec)
    return {'exact':int(predicted==gold),'tp':len(predicted&gold),
            'pred':len(predicted),'gold':len(gold),
            'g1_reachable':int(item['reachable']==len(gold))}


def _selection_eval(paths,device,network,tok,composite,spec,arms):
    corpus_rows=[r for r in cur.load_rows(paths['dataset']) if r['split'] in ('dev','challenge')]
    totals={name:{s:Counter() for s in ('dev','challenge')} for name in arms}
    for ix,row in enumerate(corpus_rows,1):
        item=_item_capture(network,tok,composite,row,device)
        if len(item['spans'])<2:continue
        for name,head in arms.items():totals[name][row['split']].update(_score_arm(head,item,device,spec))
        if ix==1 or ix%20==0 or ix==len(corpus_rows):
            print(f'G2_0089_SELECTION {ix}/{len(corpus_rows)} exposed_historical',flush=True)
    result={}
    for name,by in totals.items():
        result[name]={}
        for split,c in by.items():
            p=c['tp']/max(c['pred'],1);r=c['tp']/max(c['gold'],1)
            result[name][split]={'exact':c['exact'],'cases':80 if split=='dev' else 32,
                 'edge_f1':2*p*r/max(p+r,1e-12),'reachable_cases':c['g1_reachable']}
    # Genuine lexicographic primary objective: total exact graphs, then F1.
    ranking=sorted(arms,key=lambda name:(-(result[name]['dev']['exact']+
                  result[name]['challenge']['exact']),
                 -(result[name]['dev']['edge_f1']+result[name]['challenge']['edge_f1']),
                  name))
    return result,ranking[0]



def historical_replay(paths,dest,checkpoint,device):
    """Read-only original-cohort checks on the selected CURRENT G2 checkpoint.

    Do not issue a G2 certificate from repeatedly exposed historical examples.
    The original native evaluator performs the genuine ablations and current-
    weight G1 retention. All failures are recorded, never waived.
    """
    findings={};failures=[]
    for stage in ('G2','G1'):
        for split in ('dev','challenge'):
            path=dest/'evaluation'/(stage+'_'+split)
            report=cur.evaluate(dataset=paths['dataset'],checkpoint=checkpoint,
                output=path,split=split,device=device,target_stage=stage,
                ablation_limit=32 if stage=='G2' else 0)
            require(report['checkpoint_head_sha256']==sha(checkpoint/cur.HEAD_FILE)
                and report['stage']==stage and report['split']==split
                and not report['test_split_used'] and not report['limited'],
                'HISTORICAL_NATIVE_REPORT_SCOPE')
            findings[stage+'_'+split]={
                 'report_sha256':sha(path/'report.json'),
                 'cases':report['cases'],'metrics':report['metrics']}
            if stage=='G2':
                metric=report['metrics'];gate=cur.GATE_REQUIREMENTS['G2']
                if metric['binding_exact_rate']<gate[split]:
                    failures.append(split+':original_exact_rate')
                for key in ('zero_hidden_ablation','stage_component_ablation'):
                    if metric.get(key,{}).get('net_gain',0)<gate['neural_gain']:
                        failures.append(split+':'+key)
    # G1 is frozen byte-for-byte, but also report its actual current-weight
    # functional reconstruction results (not merely inherited assertions).
    for split in ('dev','challenge'):
        m=findings['G1_'+split]['metrics'];gate=cur.GATE_REQUIREMENTS['G1']
        if m['object_exact_rate']<gate[split]:
            failures.append(split+':G1_retention')
    output={'status':'HISTORICAL_NATIVE_EVALUATION_COMPLETED',
            'checkpoint_sha256':sha(checkpoint/cur.HEAD_FILE),
            'reports':findings,'failures':failures,
            'certified':False,'g3_training_unlocked':False}
    write_json(dest/'historical.json',output)
    return output


def bakeoff(*,root,output,device='cuda'):
    paths=paths_for(root);dest=Path(output).resolve();design=_check_provenance(paths,dest)
    gate=js(dest/'trainability.json')
    require(gate['one_pass'] and gate['eight_pass'],'TRAINABILITY_GATE_NOT_PASSED')
    require(not (dest/'selection.json').exists() and not (dest/'seal').exists(),
            'IMMUTABLE_BAKEOFF_ALREADY_DONE')
    _,spec=preflight(paths)
    network,tok,composite,_,record=_load_runtime(paths,device)
    repair=causal_target(paths)
    _fit_arm(paths,dest,device,network,tok,composite,spec,record,arm='B')
    arms={'A':cur._load_checkpoint(paths['parent_0087']/'training',device=device)[0],
          'B':cur._load_checkpoint(dest/'arm-B',device=device)[0]}
    if repair['eligible']:
        _fit_arm(paths,dest,device,network,tok,composite,spec,record,
                 arm='C',prefix=repair['proposed_prefix'])
        arms['C']=cur._load_checkpoint(dest/'arm-C',device=device)[0]
    for head in arms.values():head.eval().requires_grad_(False)
    metrics,winner=_selection_eval(paths,device,network,tok,composite,spec,arms)
    record={'schema':SCHEMA,'status':'SELECTED_FROM_EXPOSED_HISTORICAL_ONLY',
        'winner':winner,'arm_C':repair,'historical':metrics,
        'selected_checkpoint_sha256':sha(paths['parent_0087']/'training'/cur.HEAD_FILE)
              if winner=='A' else sha(dest/('arm-'+winner)/cur.HEAD_FILE),
        'frozen_decoder':spec,'g3_unlocked':False}
    write_json(dest/'selection.json',record)
    winner_path=(paths['parent_0087']/'training' if winner=='A' else
                 dest/('arm-'+winner))
    historical=historical_replay(paths,dest,winner_path,device)
    _make_seal(paths,dest,record)
    print('G2_0089_SELECTED '+json.dumps({'winner':winner,'arm_C':repair['reason'],
          'development':{k:v['dev'] for k,v in metrics.items()}},sort_keys=True),flush=True)
    return record


def _fresh_rows(paths):
    excluded=parent.excluded_prompts(paths)
    for r in read_jsonl(paths['parent_0088']/'seal'/'prompts.jsonl'):
        excluded.add(r['prompt_sha256'])
    for pair in fuzz.make_pairs(seed=SEED+1000,pairs=8):
        for side in ('base','fuzz'):
            excluded.add(hashlib.sha256(pair[side]['prompt'].encode()).hexdigest())
    rng=random.Random(SEED);serial=89_000_000;rows=[]
    for split,size in (('dev',DEV),('challenge',CHALLENGE)):
        for index in range(size):
            factory=corpus.encounter if index%2==0 else corpus.allocation
            for _ in range(4096):
                row=factory(rng,'test',serial);serial+=1
                digest=hashlib.sha256(row['prompt'].encode()).hexdigest()
                if digest not in excluded:break
            else:raise ValueError('G2_0089_FRESH_EXHAUSTED')
            excluded.add(digest)
            rows.append({'case_id':f'g2-0089-{split}-{index+1:04}',
                'split':split,'family':row['family'],'prompt':row['prompt'],
                'prompt_sha256':digest,'graph':row['graph'],
                'gold_objects':row['object_spans']})
    return rows


def _make_seal(paths,dest,selection):
    rows=_fresh_rows(paths)
    seen={r['prompt_sha256'] for r in rows}
    require(len(seen)==len(rows),'FRESH_PROMPT_DUPLICATION')
    for pair in fuzz.make_pairs(seed=SEED+2000,pairs=FUZZ_PAIRS):
        for side in ('base','fuzz'):
            r=pair[side];digest=hashlib.sha256(r['prompt'].encode()).hexdigest()
            require(digest not in seen,'FUZZ_PROMPT_OVERLAP')
            seen.add(digest)
            rows.append({'case_id':f'g2-0089-fuzz-{pair["id"]}-{side}',
                 'split':'fuzz','family':r['family'],'prompt':r['prompt'],
                 'prompt_sha256':digest,'graph':r['graph'],'gold_objects':r['object_spans'],
                 'fuzz_pair':pair['id'],'fuzz_side':side,'fuzz_mode':pair['mode']})
    seal=dest/'seal';seal.mkdir(exist_ok=False)
    public=('case_id','split','family','prompt','prompt_sha256','fuzz_pair','fuzz_side','fuzz_mode')
    secret=('case_id','split','family','prompt_sha256','graph','gold_objects',
            'fuzz_pair','fuzz_side','fuzz_mode')
    write_jsonl(seal/'prompts.jsonl',[{k:r[k] for k in public if k in r} for r in rows])
    write_jsonl(seal/'answers.SECRET.jsonl',[{k:r[k] for k in secret if k in r} for r in rows])
    write_json(seal/'commit.json',{'schema':SCHEMA,'status':'SELECTION_FROZEN_GOLD_COMMITTED',
        'seed':SEED,'dev':DEV,'challenge':CHALLENGE,'fuzz':FUZZ_PAIRS*2,
        'selection_sha256':sha(dest/'selection.json'),
        'provenance_sha256':sha(dest/'provenance.json'),
        'historical_sha256':sha(dest/'historical.json'),
        'gold_sha256':sha(seal/'answers.SECRET.jsonl'),
        'prompts_sha256':sha(seal/'prompts.jsonl'),
        'checkpoint_sha256':selection['selected_checkpoint_sha256'],
        'runner_sha256':sha(__file__),'g3_unlocked':False,
        'oracle_used_in_blind':False,'independent_distribution':False})


def _check_seal(paths,dest,*,verify_gold):
    _check_provenance(paths,dest)
    c=js(dest/'seal'/'commit.json')
    require(c['schema']==SCHEMA and c['status']=='SELECTION_FROZEN_GOLD_COMMITTED'
        and c['g3_unlocked'] is False and c['oracle_used_in_blind'] is False
        and c['runner_sha256']==sha(__file__)
        and c['selection_sha256']==sha(dest/'selection.json')
        and c['provenance_sha256']==sha(dest/'provenance.json')
        and c.get('historical_sha256')==sha(dest/'historical.json')
        and c['prompts_sha256']==sha(dest/'seal'/'prompts.jsonl'),
        'SEAL_INTEGRITY')
    if verify_gold:require(c['gold_sha256']==sha(dest/'seal'/'answers.SECRET.jsonl'),
                           'GOLD_CHANGED')
    return c


def _load_arm_checkpoint(paths,dest,selection,device):
    winner=selection['winner'];checkpoint=(paths['parent_0087']/'training'
         if winner=='A' else dest/('arm-'+winner))
    require(sha(checkpoint/cur.HEAD_FILE)==selection['selected_checkpoint_sha256'],
            'WINNER_HEAD_CHANGED')
    head,record=cur._load_checkpoint(checkpoint,device=device)
    if winner!='A':
        require(record.get('g2_0089',{}).get('arm')==winner and
                record.get('g2_0089',{}).get('frozen_g1') is True and
                record.get('g2_0089',{}).get('parent_head_sha256')==
                sha(paths['parent_0087']/'training'/cur.HEAD_FILE),
                'ARM_CHECKPOINT_LINEAGE')
        native.compare_frozen_stage(paths['parent_0087']/'training',checkpoint)
    head.eval().requires_grad_(False)
    return head


def predict(*,root,output,device='cuda'):
    paths=paths_for(root);dest=Path(output).resolve()
    commit=_check_seal(paths,dest,verify_gold=False)
    require(not (dest/'blind').exists(),'BLIND_EXISTS')
    selection=js(dest/'selection.json')
    network,tok,composite,_,_= _load_runtime(paths,device)
    head=_load_arm_checkpoint(paths,dest,selection,device)
    baseline,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
    baseline.eval().requires_grad_(False)
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE+2*FUZZ_PAIRS and
            len({r['case_id'] for r in prompts})==len(prompts),'PROMPT_CASES')
    results=[]
    for ix,row in enumerate(prompts,1):
        require(hashlib.sha256(row['prompt'].encode()).hexdigest()==row['prompt_sha256'],
                'PROMPT_HASH_DRIFT')
        prepared,h=native._capture_inference(network,tok,row['prompt'],device,2048)
        g1=composite.predict(h,prepared,family=row['family'],native_stage='G1')
        span=g1['token_spans'];elig=prepared['eligible'].to(device)
        trained=previous._infer(head,h,elig,span,prepared,selection['frozen_decoder'])
        control=previous._infer(baseline,h,elig,span,prepared,selection['frozen_decoder'])
        spec=selection['frozen_decoder']
        require(trained['g1_objects']==control['g1_objects'],'G1_ARM_DRIFT')
        zero_g1=composite.predict(h,prepared,family=row['family'],mode='zero_hidden',
                                  native_stage='G1')
        zero=previous._infer(head,torch.zeros_like(h),elig,
                             zero_g1['token_spans'],prepared,spec)
        results.append({k:v for k,v in row.items() if k!='prompt'}|{
            'modes':{
                'selected':{'g1_objects':trained['g1_objects'],
                            'binding_edges':_serialize_edges(previous.decode(trained,spec))},
                'parent':{'g1_objects':control['g1_objects'],
                          'binding_edges':_serialize_edges(previous.decode(control,spec))},
                'zero_hidden':{'g1_objects':zero['g1_objects'],
                               'binding_edges':_serialize_edges(previous.decode(zero,spec))},
                'no_binding':{'g1_objects':trained['g1_objects'],'binding_edges':[]}},
            'raw_surface':trained})
        if ix==1 or ix%20==0 or ix==len(prompts):
            print(f'G2_0089_BLIND {ix}/{len(prompts)}',flush=True)
    blind=dest/'blind';blind.mkdir(exist_ok=False)
    write_jsonl(blind/'predictions.jsonl',results)
    lock={'schema':SCHEMA,'status':'LOCKED_UNSCORED',
          'cases':len(results),'predictions_sha256':sha(blind/'predictions.jsonl'),
          'prompts_sha256':commit['prompts_sha256'],
          'selection_sha256':commit['selection_sha256'],
          'checkpoint_sha256':commit['checkpoint_sha256'],
          'gold_opened_during_inference':False,'g3_unlocked':False}
    write_json(blind/'lock.json',lock)
    print('G2_0089_BLIND_LOCKED '+json.dumps({'cases':len(results),
          'sha256':lock['predictions_sha256']},sort_keys=True),flush=True)
    return lock


def _metric(counter,size):
    p=counter['tp']/max(counter['pred'],1)
    r=counter['tp']/max(counter['gold'],1)
    return {'exact':counter['exact'],'rate':counter['exact']/size,
            'precision':p,'recall':r,'f1':2*p*r/max(p+r,1e-12),
            'tp':counter['tp'],'fp':counter['pred']-counter['tp'],
            'fn':counter['gold']-counter['tp']}


def score(*,root,output):
    paths=paths_for(root);dest=Path(output).resolve()
    commit=_check_seal(paths,dest,verify_gold=True)
    require(not (dest/'scored').exists(),'SCORE_ALREADY_EXISTS')
    lock=js(dest/'blind'/'lock.json')
    require(lock.get('schema')==SCHEMA and lock.get('status')=='LOCKED_UNSCORED'
        and lock.get('cases')==DEV+CHALLENGE+2*FUZZ_PAIRS
        and lock.get('predictions_sha256')==sha(dest/'blind'/'predictions.jsonl')
        and lock.get('prompts_sha256')==commit['prompts_sha256']
        and lock.get('selection_sha256')==commit['selection_sha256']
        and lock.get('checkpoint_sha256')==commit['checkpoint_sha256']
        and lock.get('gold_opened_during_inference') is False,'BLIND_LOCK_DRIFT')
    predictions=read_jsonl(dest/'blind'/'predictions.jsonl')
    answers=read_jsonl(dest/'seal'/'answers.SECRET.jsonl')
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    indexed={r['case_id']:r for r in answers}
    seen={r['case_id']:r for r in prompts}
    require(len(indexed)==len(seen)==len(predictions)==DEV+CHALLENGE+2*FUZZ_PAIRS
            and len({r['case_id'] for r in predictions})==len(predictions)
            and {r['case_id'] for r in predictions}==set(indexed)==set(seen),
            'CASE_BIJECTION')
    spec=js(dest/'selection.json')['frozen_decoder']
    totals={s:{m:Counter() for m in ('selected','parent','zero_hidden','no_binding')}
            for s in ('dev','challenge')}
    reachable={s:Counter() for s in ('dev','challenge')};fuzz_records={}
    case_log=[]
    for row in predictions:
        ident=row['case_id'];answer=indexed[ident]
        require(all(row[k]==answer[k]==seen[ident][k]
                    for k in ('split','family','prompt_sha256'))
                and hashlib.sha256(seen[ident]['prompt'].encode()).hexdigest()==
                     row['prompt_sha256'],'CASE_PROMPT_DRIFT')
        gold=parent.previous._gold_edges(answer['graph'])
        selected=native._keyed_edges(row['modes']['selected']['binding_edges'])
        require(selected==previous.decode(row['raw_surface'],spec),
                'SELECTED_EDGE_REPLAY_CHANGED')
        if row['split']=='fuzz':
            fuzz_records[ident]={'row':row,'gold':answer['graph']}
            continue
        split=row['split'];require(split in totals,'UNKNOWN_SPLIT')
        anchors={(x[2],(x[0],x[1])) for x in row['modes']['selected']['g1_objects']}
        full={e for e in gold if (e[0],e[1]) in anchors and (e[2],e[3]) in anchors}
        reachable[split].update({'fully_reachable':int(len(full)==len(gold)),
                                 'total':1,'unreachable_edges':len(gold)-len(full)})
        case={'case_id':ident,'split':split,'reachable':len(full)==len(gold)}
        for mode,record in row['modes'].items():
            pred=native._keyed_edges(record['binding_edges'])
            current={(x[2],(x[0],x[1])) for x in record['g1_objects']}
            require(all((e[0],e[1]) in current and (e[2],e[3]) in current
                        for e in pred),'UNANCHORED_BINDING')
            totals[split][mode].update({'exact':int(pred==gold),
                'tp':len(pred&gold),'gold':len(gold),'pred':len(pred)})
            case[mode+'_exact']=pred==gold
        case_log.append(case)
    metrics={};failures=list(js(dest/'historical.json')['failures'])
    for split,size in (('dev',DEV),('challenge',CHALLENGE)):
        require(reachable[split]['total']==size,'SPLIT_COUNT')
        row={m:_metric(c,size) for m,c in totals[split].items()}
        original=cur.GATE_REQUIREMENTS['G2']
        if row['selected']['rate']<original[split]:failures.append(split+':binding_exact_rate')
        for control in ('zero_hidden','no_binding'):
            if (row['selected']['exact']-row[control]['exact'])/size<original['neural_gain']:
                failures.append(split+':'+control+'_causal_gain')
        if row['selected']['exact']<=row['parent']['exact']:
            failures.append(split+':no_exact_gain_over_parent')
        metrics[split]={'arms':row,'reachable':dict(reachable[split])}
    fuzz_pairs={}
    for value in fuzz_records.values():
        row=value['row'];fuzz_pairs.setdefault(row['fuzz_pair'],{})[row['fuzz_side']]=value
    require(len(fuzz_pairs)==FUZZ_PAIRS and all(set(p)=={'base','fuzz'} for p in fuzz_pairs.values()),
            'FUZZ_COMPLETENESS')
    fuzz_total=Counter();fuzz_details=[]
    for ident,sides in sorted(fuzz_pairs.items()):
        base=sides['base']['row'];changed=sides['fuzz']['row']
        require(base['fuzz_mode']==changed['fuzz_mode'],'FUZZ_MODE_DRIFT')
        norm=[];exact=[]
        for side in (sides['base'],sides['fuzz']):
            row=side['row'];graph=side['gold']
            nodes={n['id']:(n['kind'],tuple(n['span'])) for n in graph['nodes'] if 'span' in n}
            inverse={v:k for k,v in nodes.items()}
            obj={(x[2],(x[0],x[1])) for x in row['modes']['selected']['g1_objects']}
            exact.append(obj==set(inverse))
            pred=native._keyed_edges(row['modes']['selected']['binding_edges'])
            norm.append({(inverse.get((a,sa),'UNMATCHED'),inverse.get((b,sb),'UNMATCHED'),t)
                         for a,sa,b,sb,t in pred})
        mode=base['fuzz_mode'];equivalent=norm[0]==norm[1]
        expected_invariant=mode!='action_removal'
        fuzz_total['pairs']+=1
        if expected_invariant and all(exact):
            fuzz_total['eligible_invariant']+=1
            fuzz_total['preserved']+=int(equivalent)
            if not equivalent:failures.append('fuzz:'+ident+':noninvariant')
        if not expected_invariant:
            fuzz_total['action_removal_pairs']+=1
            if equivalent:failures.append('fuzz:'+ident+':action_removal_unchanged')
        fuzz_details.append({'id':ident,'mode':mode,'g1_exact':exact,
                             'predicted_equal':equivalent})
    output={'schema':SCHEMA,'status':'DEVELOPMENT_GATES_PASSED_NOT_CERTIFIED'
          if not failures else 'G2_0089_GATE_BLOCKED',
          'metrics':metrics,'historical':js(dest/'historical.json'),
          'fuzz':{'totals':dict(fuzz_total),'pairs':fuzz_details},
          'failures':failures,'original_g2_gates':cur.GATE_REQUIREMENTS['G2'],
          'native_g1_unchanged':True,'independent_distribution':False,
          'g2_certified':False,'g3_training_unlocked':False,'production_promotion':False,
          'selected_arm':js(dest/'selection.json')['winner'],
          'integrity':{'gold_sha256':commit['gold_sha256'],
             'blind_sha256':lock['predictions_sha256'],
             'selection_sha256':commit['selection_sha256']}}
    scored=dest/'scored';scored.mkdir(exist_ok=False)
    write_json(scored/'report.json',output)
    write_jsonl(scored/'cases.jsonl',case_log)
    print('G2_0089_RESULT '+json.dumps({'status':output['status'],
       'selected_arm':output['selected_arm'],
       'dev_exact':metrics['dev']['arms']['selected']['exact'],
       'challenge_exact':metrics['challenge']['arms']['selected']['exact'],
       'failures':failures,'g3_unlocked':False},sort_keys=True),flush=True)
    return output


def run(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=Path(output).resolve() if output else (
         Path(root)/'oracle-fabric-g2-structural-learning-0089').resolve()
    require(not dest.exists(),'OUTPUT_ALREADY_EXISTS')
    first=trainability(root=root,output=dest,device=device)
    if not first['eight_pass']:
        print('G2_0089_BLOCKED '+json.dumps({'status':first['status'],
                     'g3_unlocked':False},sort_keys=True),flush=True)
        return first
    bakeoff(root=root,output=dest,device=device)
    for command in ('predict','score'):
        cmd=[sys.executable,'-u','-m',MODULE,command,
             '--root',str(Path(root).resolve()),'--output',str(dest)]
        if command=='predict':cmd+=['--device',device]
        subprocess.run(cmd,check=True)
    return js(dest/'scored'/'report.json')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    choices=('run','trainability','bakeoff','predict','score')
    sub=parser.add_subparsers(dest='command',required=True)
    for command in choices:
        p=sub.add_parser(command)
        p.add_argument('--root',default=ROOT_DEFAULT)
        p.add_argument('--output')
        if command in ('run','trainability','bakeoff','predict'):
            p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    args=vars(parser.parse_args(argv));cmd=args.pop('command')
    if cmd!='run' and args['output'] is None:
        args['output']=str(Path(args['root'])/'oracle-fabric-g2-structural-learning-0089')
    return {'run':run,'trainability':trainability,'bakeoff':bakeoff,
            'predict':predict,'score':score}[cmd](**args)

if __name__=='__main__':main()
