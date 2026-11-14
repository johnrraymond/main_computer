"""G2-0001: sandbox semantic binding on frozen G1-1.25 *predicted* nodes.

This is a development handoff, not a G1 certificate, production G2 unlock, or
permission to execute graph objectives. Qwen, LoRA, G1 and the G1-0030 frozen
1.25 decoder are immutable. Only G2 pair_projection/binding learn from TRAIN.
No gold objects, edge labels, graph semantics or outcome are inference inputs.
The sealed challenge/test splits are not parsed or scored.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import random
import re

import torch
from torch.nn import functional as F

from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_component_diagnosis as diag
from tools import nanojev_gamer_v1_g1_component_reconstruction as recon
from tools.nanojev_gamer_v1_g1_layout_filter import CHALLENGER
from tools.nanojev_gamer_v1_graph_contract import BINDINGS, validate_graph
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, decode_graph, token_span_char_bounds
from tools import nanojev_gamer_v1_backbone_first as backbone

SCHEMA='nanojev.gamer.graph.g2.predicted-object-binding-bootstrap.v1'
REPORT='g2_binding_bootstrap_report.json'
CASES='g2_binding_bootstrap_per_case.jsonl'
HEAD='g2_binding_development.safetensors'
G1_ARM='survivor_dp_guard_125_0030'
G1_HEAD_SHA='1903928b340a65e27060ad07fd15dbcf6e3cad37a2dc29f0b1835682b8132e98'
SEED=320001
TRAIN_LIMIT=160
DEV_LIMIT=160
STEPS=160
BATCH=4
LR=3e-4


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _jsonl(rows):
    return ''.join(json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n'
                   for row in rows).encode('utf-8')


def original_training_rows(dataset,limit=TRAIN_LIMIT):
    """Read TRAIN only; never JSON-decode sealed challenge/test records."""
    path=Path(dataset)
    if not path.is_file():raise FileNotFoundError('GAMER_G2_0001_TRAINING_DATASET_MISSING')
    grouped={'encounter':[],'allocation':[]}
    seen=set()
    for line in path.open(encoding='utf-8'):
        match=re.search(r'"split"\s*:\s*"([^"]+)"',line)
        if match is None:raise ValueError('GAMER_G2_0001_DATASET_SPLIT_ABSENT')
        if match.group(1)!='train':continue
        row=json.loads(line)
        if row.get('annotation_provenance')!='AUTHOR_MARKED_IN_SOURCE_GENERATOR':
            raise ValueError('GAMER_G2_0001_UNVERIFIED_TRAIN_LABEL')
        validate_graph(row['graph'],complete=True)
        family=row.get('family')
        if family not in grouped:raise ValueError('GAMER_G2_0001_INVALID_FAMILY')
        digest=_sha(row['prompt'])
        if digest in seen:raise ValueError('GAMER_G2_0001_DUPLICATE_TRAIN')
        seen.add(digest)
        grouped[family].append(row)
    n=limit//2
    if limit<2 or limit%2 or any(len(v)<n for v in grouped.values()):
        raise ValueError('GAMER_G2_0001_INSUFFICIENT_BALANCED_TRAIN')
    return [grouped['encounter'][i//2] if i%2==0 else grouped['allocation'][i//2]
            for i in range(limit)]


def novel_dev_rows(train_rows,limit=DEV_LIMIT,seed=SEED):
    if limit<2 or limit%2:raise ValueError('GAMER_G2_0001_INVALID_DEV_SIZE')
    rng=random.Random(seed)
    seen={_sha(r['prompt']) for r in train_rows}
    rows=[]
    for i in range(limit):
        fn=corpus.encounter if i%2==0 else corpus.allocation
        for attempt in range(512):
            row=fn(rng,'dev',i)
            if _sha(row['prompt']) not in seen:break
        else:raise RuntimeError('GAMER_G2_0001_DEV_EXCLUSION_EXHAUSTED')
        if row.get('split')!='dev' or row.get('annotation_provenance')!='AUTHOR_MARKED_IN_SOURCE_GENERATOR':
            raise ValueError('GAMER_G2_0001_INVALID_DEV_ANNOTATION')
        validate_graph(row['graph'],complete=True)
        seen.add(_sha(row['prompt']))
        rows.append(row)
    return rows


def validate_parent(root,report_path):
    """Frozen G1 development evidence; explicitly NOT a gate certificate."""
    report_path=Path(report_path)
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={
        'frozen_125_all_development_checks_pass':True,
        'frozen_125_independent_validation_pass':True,
        'frozen_125_remaining_known_loss_count':1,
        'g2_unlocked':False,
        'certification_authorized':False,
        'challenge_or_test_split_used':False,
    }
    for key,expected in required.items():
        if type(report.get(key)) is not type(expected) or report[key]!=expected:
            raise ValueError('GAMER_G2_0001_PARENT_NOT_VALIDATED:'+key)
    if (report.get('checkpoint_file_sha256',{}).get('head_file_sha256')!=G1_HEAD_SHA or
            G1_ARM not in report.get('configurations_predeclared',[])):
        raise ValueError('GAMER_G2_0001_PARENT_HEAD_OR_ARM_DRIFT')
    checkpoint=Path(root)/'graph-g1-miss-null-focused-0008'
    head,manifest=cur._load_checkpoint(checkpoint,device='cpu')
    dataset=Path(root)/'graph-span-curriculum-0005.jsonl'
    if (manifest['stage']!='G1' or manifest['head_sha256']!=G1_HEAD_SHA or
            manifest['dataset_sha256']!=backbone.sha256(dataset) or
            manifest['train_limit'] or manifest['production_promotion'] is not False):
        raise ValueError('GAMER_G2_0001_G1_LINEAGE_CHANGED')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('GAMER_G2_0001_ADAPTER_LINEAGE_CHANGED')
    return head,manifest,dataset,backbone.sha256(report_path)


def predicted_spans(g1_output,prepared):
    """Exactly G1-0030's frozen 1.25 selection, without gold or future G2."""
    frozen=diag.frozen_stages(g1_output,prepared)
    chosen=frozen['selected']
    spans,_=recon.reconstruct_components(
        chosen['guard'],chosen['filtered_base'],chosen['filtered_wide'],chosen['filtered'],
        frozen['filtered_catalog'],guard_bonus=0.25)
    # Reject unmappable spans instead of inventing a string-level parser.
    if any(token_span_char_bounds(x,prepared) is None for x in spans):
        raise ValueError('GAMER_G2_0001_UNMAPPABLE_G1_OBJECT')
    return spans


def gold_binding_edges(row):
    nodes={n['id']:n for n in row['graph']['nodes'] if 'span' in n}
    def anchor(node):return (node['span'][0],node['span'][1],node['kind'])
    return {(anchor(nodes[e['src']]),anchor(nodes[e['dst']]),e['type'])
            for e in row['graph']['edges']
            if e['type'] in BINDINGS and e['src'] in nodes and e['dst'] in nodes}


def predicted_anchors(spans,prepared):
    return [(a,b,span['kind']) for span in spans
            for bounds in (token_span_char_bounds(span,prepared),)
            if bounds is not None for a,b in (bounds,)]


def supervision(anchors,row):
    """Ground only pairs whose two predicted nodes exactly match author spans."""
    gold_nodes={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
    bindings=gold_binding_edges(row)
    index={anchor:i for i,anchor in enumerate(anchors) if anchor in gold_nodes}
    positives=[];negatives=[]
    for left,i in index.items():
        for right,j in index.items():
            if i==j:continue
            labels=[kind for a,b,kind in bindings if a==left and b==right]
            if len(labels)>1:raise ValueError('GAMER_G2_0001_MULTIPLE_BINDINGS_PER_PAIR')
            target=BIND_CLASSES.index(labels[0]) if labels else 0
            (positives if target else negatives).append((i,j,target))
    # Bound negative class imbalance; labels used ONLY in G2 TRAIN loss.
    negatives=sorted(negatives)[:max(32,8*len(positives))]
    return positives+negatives,{'matched_nodes':len(index),
        'gold_nodes':len(gold_nodes),'reachable_gold_edges':len(positives),
        'gold_edges':len(bindings)}


def pair_logits(head,vectors,indices):
    if not indices:raise ValueError('GAMER_G2_0001_NO_PAIR_SAMPLES')
    left=torch.stack([vectors[i] for i,j,_ in indices]);right=torch.stack([vectors[j] for i,j,_ in indices])
    pair=torch.cat((left,right,left*right,left-right),dim=-1)
    return head.binding(F.gelu(head.pair_projection(pair)))


def edge_set(graph):
    nodes={n['id']:n for n in graph['nodes'] if 'span' in n}
    anchors={i:(n['span'][0],n['span'][1],n['kind']) for i,n in nodes.items()}
    return {(anchors[e['src']],anchors[e['dst']],e['type']) for e in graph['edges']
            if e['type'] in BINDINGS and e['src'] in anchors and e['dst'] in anchors}


def metrics(case_results):
    counts=Counter()
    by_family={}
    for result in case_results:
        counts.update({k:result[k] for k in ('correct','missing','false_positive')})
        counts['cases']+=1
        counts['exact_cases']+=result['exact']
        by_family.setdefault(result['family'],[]).append(result)
    correct=counts['correct'];missing=counts['missing'];extra=counts['false_positive']
    return {'cases':counts['cases'],'exact_cases':counts['exact_cases'],
            'correct':correct,'missing':missing,'false_positive':extra,
            'precision':correct/max(1,correct+extra),
            'recall':correct/max(1,correct+missing),
            'f1':2*correct/max(1,2*correct+missing+extra),
            'by_family':{name:metrics_for_family(values) for name,values in by_family.items()}}


def metrics_for_family(rows):
    correct=sum(r['correct'] for r in rows);missing=sum(r['missing'] for r in rows)
    extra=sum(r['false_positive'] for r in rows)
    return {'cases':len(rows),'correct':correct,'missing':missing,
            'false_positive':extra,'f1':2*correct/max(1,2*correct+missing+extra)}


def score_output(output,prepared,row):
    graph,_=decode_graph(output,prepared,unlocked='G2')
    validate_graph(graph,complete=False)
    predicted=edge_set(graph);gold=gold_binding_edges(row)
    return {'correct':len(predicted&gold),'missing':len(gold-predicted),
            'false_positive':len(predicted-gold),'exact':predicted==gold}


def train_g2(head,train_cache,*,steps=STEPS,batch=BATCH,lr=LR,seed=SEED,device='cpu'):
    if steps<1 or batch<1 or lr<=0:raise ValueError('GAMER_G2_0001_INVALID_OPTIMIZER')
    count=cur.trainable_stage(head,'G2')
    active={n for n,p in head.named_parameters() if p.requires_grad}
    if not active or not all(n.startswith(('binding.','pair_projection.')) for n in active):
        raise ValueError('GAMER_G2_0001_UPSTREAM_UNFROZEN')
    optimizer=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],lr=lr)
    rng=random.Random(seed)
    history=[]
    groups={f:[i for i,x in enumerate(train_cache) if x['family']==f and x['pairs']]
            for f in ('encounter','allocation')}
    if not all(groups.values()):raise ValueError('GAMER_G2_0001_NO_TRAINABLE_FAMILY')
    for step in range(steps):
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for j in range(batch):
            family=('encounter','allocation')[(step*batch+j)%2]
            case=train_cache[rng.choice(groups[family])]
            sample=case['pairs']
            logits=pair_logits(head,case['vectors'].to(device),sample)
            labels=torch.tensor([target for i,k,target in sample],device=device)
            loss=F.cross_entropy(logits,labels)
            (loss/batch).backward()
            losses.append(float(loss.detach()))
        torch.nn.utils.clip_grad_norm_([p for p in head.parameters() if p.requires_grad],1.0)
        optimizer.step()
        history.append(sum(losses)/len(losses))
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_BOOTSTRAP_TRAIN {step+1}/{steps} loss={history[-1]:.6f}',flush=True)
    if not all(math.isfinite(x) for x in history):raise ValueError('GAMER_G2_0001_BAD_LOSS')
    return count,history


def _binding_snapshot(head):
    return {k:v.detach().clone() for k,v in head.state_dict().items()
            if k.startswith(('binding.','pair_projection.'))}


def _assert_g1_frozen(head,old):
    new=head.state_dict()
    for key,val in old.items():
        if key not in new or not torch.equal(val,new[key]):
            raise ValueError('GAMER_G2_0001_G1_PARAMETER_DRIFT:'+key)


def run(*,root,output,validation_report,device='cuda',max_length=2048,
        train_limit=TRAIN_LIMIT,dev_limit=DEV_LIMIT,steps=STEPS):
    from safetensors.torch import save_file
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0001_IMMUTABLE_OUTPUT')
    head,manifest,dataset,report_sha=validate_parent(root,validation_report)
    training=original_training_rows(dataset,train_limit)
    dev=novel_dev_rows(training,dev_limit)
    overlap={_sha(x['prompt']) for x in training}&{_sha(x['prompt']) for x in dev}
    if overlap:raise ValueError('GAMER_G2_0001_TRAIN_DEV_OVERLAP')
    head=head.to(device).eval()
    g1_state={k:v.detach().clone() for k,v in head.state_dict().items()
              if not k.startswith(('binding.','pair_projection.'))}
    baseline=deepcopy(head.binding).to(device).eval()
    baseline_projection=deepcopy(head.pair_projection).to(device).eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):raise ValueError('GAMER_G2_0001_BACKBONE_UNFROZEN')
    train_cache=[];coverage=Counter();all_train_sha=[]
    with torch.no_grad():
        for index,row in enumerate(training,1):
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            g1=head(hidden,eligible=prepared['eligible'].to(device),active_stage='G1')
            spans=predicted_spans(g1,prepared)
            g2=head(hidden,eligible=prepared['eligible'].to(device),training_spans=spans,active_stage='G2')
            anchors=predicted_anchors(spans,prepared)
            pairs,numbers=supervision(anchors,row)
            train_cache.append({'family':row['family'],'vectors':g2['node_vectors'][:len(spans)].detach().cpu(),
                                'pairs':pairs,'coverage':numbers})
            coverage.update(numbers);all_train_sha.append(_sha(row['prompt']))
            if index==1 or index%40==0 or index==len(training):
                print(f'GAMER_G2_BOOTSTRAP_CAPTURE train {index}/{len(training)}',flush=True)
    # Cache untrained pair head readouts and frozen G1 predictions on fresh dev.
    dev_cache=[]
    with torch.no_grad():
        for index,row in enumerate(dev,1):
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            g1=head(hidden,eligible=prepared['eligible'].to(device),active_stage='G1')
            spans=predicted_spans(g1,prepared)
            result=head(hidden,eligible=prepared['eligible'].to(device),training_spans=spans,active_stage='G2')
            base=score_output(result,prepared,row)
            # Drop giant span/attention tensors; retain only G2 readout inputs.
            compact={'spans':spans,'surface_count':len(spans),
                     'node_vectors':result['node_vectors'].detach().cpu().contiguous(),
                     'binding_logits':result['binding_logits'].detach().cpu().contiguous()}
            dev_cache.append((compact,prepared,row,base))
            if index==1 or index%40==0 or index==len(dev):
                print(f'GAMER_G2_BOOTSTRAP_CAPTURE dev {index}/{len(dev)}',flush=True)
    # No backbone or G1 optimization is allowed; only G2 weights update.
    params,history=train_g2(head,train_cache,steps=steps,device=device)
    _assert_g1_frozen(head,g1_state)
    per_case=[];coverage_dev=Counter();scores={'untrained':[],'trained':[],'zero_node_features':[]}
    with torch.inference_mode():
        for index,(original,prepared,row,untrained) in enumerate(dev_cache,1):
            vectors=original['node_vectors'].to(device)
            def new_logits(v):
                n=len(v);l=v[:,None,:].expand(n,n,-1);r=v[None,:,:].expand(n,n,-1)
                return head.binding(F.gelu(head.pair_projection(torch.cat((l,r,l*r,l-r),-1))))
            trained=dict(original);trained['binding_logits']=new_logits(vectors)
            ablated=dict(original);ablated['binding_logits']=new_logits(torch.zeros_like(vectors))
            results={'untrained':untrained,'trained':score_output(trained,prepared,row),
                     'zero_node_features':score_output(ablated,prepared,row)}
            # Ensure frozen pretraining model reproduced cached baseline exactly.
            lv=vectors[:,None,:].expand(len(vectors),len(vectors),-1)
            rv=vectors[None,:,:].expand(len(vectors),len(vectors),-1)
            frozen_out=dict(original)
            frozen_out['binding_logits']=baseline(F.gelu(baseline_projection(torch.cat((lv,rv,lv*rv,lv-rv),-1))))
            if score_output(frozen_out,prepared,row)!=untrained:
                raise ValueError('GAMER_G2_0001_FROZEN_BASELINE_DRIFT')
            for name,value in results.items():scores[name].append({**value,'family':row['family']})
            anchors=predicted_anchors(original['spans'],prepared)
            _,nums=supervision(anchors,row);coverage_dev.update(nums)
            per_case.append({'index':index,'family':row['family'],'prompt_sha256':_sha(row['prompt']),
                             'g1_predicted_nodes':len(anchors),'g1_exact_matched_nodes':nums['matched_nodes'],
                             'gold_nodes':nums['gold_nodes'],'reachable_gold_binding_edges':nums['reachable_gold_edges'],
                             'gold_binding_edges':nums['gold_edges'],'configurations':results})
    results={name:metrics(rows) for name,rows in scores.items()}
    # Recording a failure does not abort development, conceal errors, or unlock stages.
    candidate=results['trained'];reference=results['untrained'];null=results['zero_node_features']
    report={'schema':SCHEMA,'status':'G2_DEVELOPMENT_BOOTSTRAP_NOT_CERTIFIED',
        'g2_development_started':True,'g2_certified':False,'g2_production_unlocked':False,
        'g1_development_candidate':G1_ARM,'g1_frozen':True,
        'g1_known_residual_losses':1,'g1_independent_validation_report_sha256':report_sha,
        'g1_parent_checkpoint':str(root/'graph-g1-miss-null-focused-0008'),
        'g1_head_sha256':manifest['head_sha256'],'g1_dataset_sha256':manifest['dataset_sha256'],
        'g1_source_adapter_sha256':manifest['source_adapter_sha256'],
        'source_adapter_sha256':backbone.adapter_fingerprint(manifest['source_adapter']),
        'g2_trainable_parameters':params,'g2_trainable_modules':['binding','pair_projection'],
        'g2_training_only_author_gold':True,'g2_inference_uses_predicted_g1_only':True,
        'train_cases':len(training),'dev_cases':len(dev),
        'train_prompt_hashes_sha256':_sha('\n'.join(all_train_sha)),
        'dev_dataset_sha256':hashlib.sha256(_jsonl(dev)).hexdigest(),
        'train_binding_coverage':dict(coverage),'dev_binding_coverage':dict(coverage_dev),
        'train_steps':steps,'train_batch':BATCH,'train_lr':LR,'train_last_loss':history[-1],
        'metrics':results,'train_improves_end_to_end_f1':candidate['f1']>reference['f1'],
        'learned_signal_ablation_gain':candidate['f1']-null['f1'],
        'challenge_or_test_split_used':False,'sealed_test_used':False,
        'production_promotion':False,'certification_authorized':False,
        'memory_writes':False,'graph_execution_authorized':False}
    output.mkdir(parents=True)
    files=_binding_snapshot(head)
    save_file({k:v.detach().cpu().contiguous() for k,v in files.items()},str(output/HEAD))
    report['g2_development_weights_sha256']=backbone.sha256(output/HEAD)
    (output/REPORT).write_text(cur.json_text(report)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(_jsonl(per_case))
    return report
