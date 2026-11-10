"""GAMER graph curriculum: ONE head, seven sequential learning gates.

All modules exist before G1. Gate certificates are checked before training G2+;
all inference/evaluation uses model predictions, not oracle graph fields. Frozen
Qwen/LoRA is a feature source, and no output tokens are generated.

A gate establishes bounded skill on a declared challenge, never universal game
competence. The test split is sealed until an independently certified G7 run.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
import torch
from torch.nn import functional as F

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_contract import (
    SCHEMA as GRAPH_SCHEMA, STAGES, STAGE_NAMES, SPAN_KINDS, NODE_KINDS, BINDINGS,
    RELATIONS, OPERATORS, OBJECTIVES, validate_graph, execute_graph, exact_number,
)
from tools.nanojev_gamer_v1_graph_head import (
    GamerGraphHead, decode_neural_spans, decode_greedy_spans, _optimal_span_indices, BIND_CLASSES, REL_CLASSES,
    OP_CLASSES, DEFAULT_MAX_SPAN_TOKENS, decode_graph, token_span_char_bounds,
)

SCHEMA = 'nanojev.gamer.graph.curriculum.v2.span'
HEAD_FILE = 'gamer_graph_head.safetensors'
MANIFEST = 'manifest.json'
# Predeclared developmental advancement gates, not claims of generality.
GATE_REQUIREMENTS = {
    'G1': {'metric': 'object_exact_rate', 'dev': .80, 'challenge': .70,
           'macro_f1': .90, 'kind_recall': .82, 'neural_gain': .08},
    'G2': {'metric': 'binding_exact_rate', 'dev': .80, 'challenge': .70,
           'neural_gain': .08},
    'G3': {'metric': 'relation_exact_rate', 'dev': .75, 'challenge': .65,
           'neural_gain': .08},
    'G4': {'metric': 'operation_exact_rate', 'dev': .75, 'challenge': .65,
           'neural_gain': .08},
    'G5': {'metric': 'objective_exact_rate', 'dev': .85, 'challenge': .75,
           'neural_gain': .08},
    'G6': {'metric': 'memory_conditioned_graph_exact_rate', 'dev': .85, 'challenge': .75,
           'neural_gain': .08},
    'G7': {'metric': 'decision_exact_rate', 'dev': .90, 'challenge': .85,
           'neural_gain': .15},
}

# Stage-specific trainability prevents accidental changes to earlier skills.
STAGE_MODULES = {
    'G1': ('project.', 'encoder.', 'span_start.', 'span_end.',
           'span_pool.', 'span_width_embed.', 'span_classifier.'),
    'G2': ('binding.', 'pair_projection.'),
    'G3': ('relationship.',),
    'G4': ('latent_query', 'latent_attention.', 'latent_presence.',
           'latent_kind.', 'operator.', 'operand_query.', 'operand_key.',
           'operand_stop', 'relationship.'),
    'G5': ('objective.', 'objective_candidate.'),
    'G6': ('memory_query.', 'memory_key.', 'memory_value.',
           'memory_write.', 'memory_write_gate.'),
    'G7': ('',),
}


def json_text(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2)


def load_rows(path):
    path=Path(path)
    if not path.is_file(): raise FileNotFoundError('GAMER_GRAPH_CORPUS_MISSING')
    rows=[];digests=set();splits=Counter()
    for line_number,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
        if not line.strip():continue
        row=json.loads(line)
        if not isinstance(row,dict) or not {'prompt','graph','split','family','object_spans'} <= row.keys():
            raise ValueError(f'GAMER_GRAPH_INVALID_DATA_ROW_{line_number}')
        if row['split'] not in ('train','dev','challenge','test'):
            raise ValueError('GAMER_GRAPH_INVALID_SPLIT')
        if not isinstance(row['prompt'],str) or not row['prompt'].strip():
            raise ValueError('GAMER_GRAPH_EMPTY_TRAINING_PROMPT')
        validate_graph(row['graph'], complete=True)
        if not isinstance(row['object_spans'],list):
            raise ValueError('GAMER_GRAPH_GOLD_SPANS_MISSING')
        if row.get('annotation_provenance') != 'AUTHOR_MARKED_IN_SOURCE_GENERATOR':
            raise ValueError('GAMER_GRAPH_UNVERIFIED_GOLD_ANNOTATIONS')
        spans=[]
        for item in row['object_spans']:
            if (not isinstance(item,dict) or set(item) != {'kind','start','end'} or
                item['kind'] not in SPAN_KINDS or type(item['start']) is not int or
                type(item['end']) is not int or
                not 0<=item['start']<item['end']<=len(row['prompt'])):
                raise ValueError('GAMER_GRAPH_GOLD_SPAN_MISMATCH')
            spans.append((item['start'],item['end'],item['kind']))
        if len(set(spans)) != len(spans) or any(spans[i][0]<spans[i-1][1]
                                               for i in range(1,len(spans))
                                               if spans[i][0] >= spans[i-1][0]):
            raise ValueError('GAMER_GRAPH_OVERLAPPING_GOLD_SPANS')
        expected={(n['span'][0],n['span'][1],n['kind']) for n in row['graph']['nodes'] if 'span' in n}
        if set(spans) != expected:
            raise ValueError('GAMER_GRAPH_GOLD_DOES_NOT_MATCH_GRAPH')
        digest=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()
        if digest in digests:raise ValueError('GAMER_GRAPH_DUPLICATE_PROMPT')
        digests.add(digest)
        rows.append(row);splits[row['split']]+=1
    if any(splits[s] < 1 for s in ('train','dev','challenge','test')):
        raise ValueError('GAMER_GRAPH_MISSING_REQUIRED_SPLIT')
    return rows


def aligned_labels(prepared,row):
    """Supervision only. No label/graph may enter Qwen or inference."""
    eligible=prepared['eligible'].tolist()
    offsets=prepared['offsets']; base=prepared['prompt_start']
    claimed=set()
    spans_by_id={};surface=[];latent=[]
    for node in row['graph']['nodes']:
        if 'span' not in node:
            latent.append(node);continue
        a,b=node['span']
        ids=[i for i,(x,y) in enumerate(offsets)
             if eligible[i] and x < base+b and y > base+a]
        if not ids or any(i in claimed for i in ids):
            raise ValueError('GAMER_GRAPH_GOLD_TOKEN_ALIGNMENT_FAILURE')
        claimed.update(ids)
        spans_by_id[node['id']] = len(surface)
        surface.append({'kind':node['kind'],'token_first':ids[0],'token_last':ids[-1]})
    return {'spans':surface,'surface_nodes':surface,
            'surface_ids':spans_by_id,'latent_nodes':latent,
            'eligible':prepared['eligible']}


def trainable_stage(head,stage):
    if stage not in STAGES:raise ValueError('GAMER_GRAPH_UNKNOWN_TRAINING_STAGE')
    prefix=STAGE_MODULES[stage]
    for name, param in head.named_parameters():
        param.requires_grad_(any(name.startswith(p) for p in prefix))
    count=sum(p.numel() for p in head.parameters() if p.requires_grad)
    if not count:raise ValueError('GAMER_GRAPH_NO_TRAINABLE_PARAMETERS')
    return count


def _pair_loss(matrix, ids, edges, types, *, weight=5.0):
    """Sparse positive + sampled negative pairs, not a teacher oracle at query."""
    reverse={name:idx for name,idx in ids.items()}
    positives=[]; negatives=[]
    if not types or types[0] != 'none' or matrix.shape[-1] != len(types):
        raise ValueError('GAMER_GRAPH_PAIR_CLASS_CONTRACT')
    gold={(e['src'],e['dst']):types.index(e['type'])
          for e in edges if e['type'] in types[1:] and e['src'] in reverse and e['dst'] in reverse}
    for src,i in ids.items():
        for dst,j in ids.items():
            if src==dst:continue
            target=gold.get((src,dst),0)
            (positives if target else negatives).append((i,j,target))
    # Stable deterministic negative sampling; never turn task answers into inputs.
    negatives=negatives[:max(32,8*len(positives))]
    samples=positives+negatives
    if not samples:return matrix.sum()*0
    a,b,y=zip(*samples)
    label=torch.tensor(y,dtype=torch.long,device=matrix.device)
    class_weights=torch.full((matrix.shape[-1],),weight,device=matrix.device)
    class_weights[0]=1.0
    return F.cross_entropy(matrix[list(a),list(b)],label,weight=class_weights)


def _align_graph_nodes(labels,row,output):
    idmap=dict(labels['surface_ids'])
    latent_nodes=labels['latent_nodes']
    if len(latent_nodes)>output['slot_presence_logits'].shape[0]:
        raise ValueError('GAMER_GRAPH_INSUFFICIENT_LATENT_SLOTS')
    for idx,node in enumerate(latent_nodes):
        idmap[node['id']]=output['surface_count']+idx
    return idmap



def g1_structured_object_set_loss(output, spans):
    """Loss-augmented exact interval decoding, optimized on *whole sets*.

    Maximize score(y)+[#wrong proposals+#missed gold] - score(gold).
    Decoder selection uses detached weights; gradients flow through the
    selected typed logits. The exact gold spans are training-only targets.
    """
    logits=output['span_logits']
    valid=output['span_valid']
    gold={(int(x['token_first']),int(x['token_last']-x['token_first']),
           SPAN_KINDS.index(x['kind'])+1) for x in spans}
    if not gold:
        raise ValueError('GAMER_GRAPH_STRUCTURED_LOSS_NEEDS_GOLD')
    picked=_optimal_span_indices(logits,valid,gold=gold,cost_augmented=True)
    def margin(first,width_index,label):
        return logits[first,width_index,label]-logits[first,width_index,0]
    gold_score=torch.stack([margin(a,w,k) for a,w,k in sorted(gold)]).sum()
    pred_score=(torch.stack([margin(a,w,k) for a,w,k in picked]).sum()
                if picked else gold_score*0)
    picked_set=set(picked)
    delta=len(picked_set-gold)+len(gold-picked_set)
    return F.relu(pred_score + delta - gold_score) / len(gold)


def g1_counterfactual_consistency(left_output,left_labels,right_output,right_labels):
    """Paired semantic-role consistency across two distinct prompt surfaces.

    The matched gold span ordering comes from the training-only author graph.
    No pairing, role label or source-string substitution enters inference.
    """
    left=left_labels['spans'];right=right_labels['spans']
    if len(left)!=len(right) or any(a['kind']!=b['kind'] for a,b in zip(left,right)):
        raise ValueError('GAMER_GRAPH_COUNTERFACTUAL_GRAPH_MISMATCH')
    losses=[]
    for a,b in zip(left,right):
        li=left_output['span_logits'][a['token_first'],a['token_last']-a['token_first']]
        ri=right_output['span_logits'][b['token_first'],b['token_last']-b['token_first']]
        # Bounded symmetric KL: both label distributions must track the same
        # semantic role even when the identity at that position is changed.
        lp=F.log_softmax(li,dim=-1)
        rp=F.log_softmax(ri,dim=-1)
        losses.append((F.kl_div(lp,rp.detach().exp(),reduction='sum')+
                       F.kl_div(rp,lp.detach().exp(),reduction='sum'))/2)
    # In addition to matching gold-span class distributions, train on actual
    # *decoded* set disagreements. The alignment is author-marked and only
    # available during training; inference still receives raw Qwen states.
    if 'span_valid' in left_output and 'span_valid' in right_output:
        chosen_left=set(_optimal_span_indices(left_output['span_logits'],
                                              left_output['span_valid']))
        chosen_right=set(_optimal_span_indices(right_output['span_logits'],
                                               right_output['span_valid']))
        for a,b in zip(left,right):
            def key(span):
                return (span['token_first'],span['token_last']-span['token_first'],
                        SPAN_KINDS.index(span['kind'])+1)
            ka,kb=key(a),key(b)
            if (ka in chosen_left)==(kb in chosen_right):
                continue
            loser, span=(left_output,a) if ka not in chosen_left else (right_output,b)
            logits=loser['span_logits'];valid=loser['span_valid']
            first,width,label=key(span);end=first+width
            gold_margin=logits[first,width,label]-logits[first,width,0]
            starts=torch.arange(len(valid),device=valid.device)[:,None]
            ends=starts+torch.arange(valid.shape[1],device=valid.device)[None,:]
            competing=valid & (starts<=end) & (ends>=first)
            competing=competing.clone();competing[first,width]=False
            rival_score=(logits[...,1:].amax(-1)-logits[...,0])[competing]
            rival=torch.maximum(rival_score.max(),gold_margin.new_zeros(())) if rival_score.numel() \
                  else gold_margin.new_zeros(())
            losses.append(F.softplus(1.0+rival-gold_margin))
    return torch.stack(losses).mean() if losses else \
           left_output['span_logits'].sum()*0

def g1_parent_retention_loss(student, teacher, labels, *, temperature=2.0):
    """Preserve gold-correct G1 decisions of the frozen parent on TRAIN rows.

    The teacher is the immutable parent head on the *same* contextual states.
    Only its already-correct complete gold spans are anchored. Incorrect
    teacher classifications receive no distillation pressure, so new learning
    can repair them. Gold labels and teacher scores never reach inference.
    """
    if not temperature>0:
        raise ValueError('GAMER_GRAPH_RETENTION_TEMPERATURE')
    if student['span_logits'].shape!=teacher['span_logits'].shape or not torch.equal(
            student['span_valid'],teacher['span_valid']):
        raise ValueError('GAMER_GRAPH_RETENTION_SPAN_CONTRACT_CHANGED')
    indices=[];types=[]
    with torch.no_grad():
        logits=teacher['span_logits']
        probs=logits.softmax(-1)
        for span in labels['spans']:
            first=span['token_first'];width=span['token_last']-first
            kind=SPAN_KINDS.index(span['kind'])+1
            if (teacher['span_valid'][first,width] and
                    int(logits[first,width].argmax())==kind and
                    float(probs[first,width,kind])>=.5):
                indices.append((first,width));types.append(kind)
    if not indices:
        return student['span_logits'].sum()*0,0
    start,width=zip(*indices)
    source=teacher['span_logits'][list(start),list(width)].detach()
    current=student['span_logits'][list(start),list(width)]
    target=F.softmax(source/temperature,dim=-1)
    distill=F.kl_div(F.log_softmax(current/temperature,dim=-1),
                    target,reduction='batchmean')*temperature**2
    classes=torch.tensor(types,device=current.device,dtype=torch.long)
    parent_margin=source.gather(1,classes[:,None]).squeeze(1)-source[:,0]
    current_margin=current.gather(1,classes[:,None]).squeeze(1)-current[:,0]
    # Unlike ordinary symmetric KL, this also prevents an already-correct
    # complete object from becoming a null object with a weaker margin.
    floor=F.relu(parent_margin-.25-current_margin).mean()
    return distill+floor,len(indices)



def g1_hard_null_positive_loss(output, spans):
    """Train-only focused margin for gold objects suppressed by the null logit.

    Unlike the default positive-null objective, this arm gives additional
    gradient ONLY to correctly annotated complete spans where null actually
    outranks the gold class. An incorrect *non-null* class is not silently
    reclassified as a null miss. No development set or inference-time label
    enters this calculation.
    """
    logits=output['span_logits']
    valid=output['span_valid']
    suppressed=[]
    for span in spans:
        first=int(span['token_first'])
        width=int(span['token_last'])-first
        label=SPAN_KINDS.index(span['kind'])+1
        if not bool(valid[first,width]):
            raise ValueError('GAMER_GRAPH_HARD_NULL_INVALID_GOLD')
        row=logits[first,width]
        # Isolate the null-vs-gold mechanism from other class confusions.
        if int(row.detach().argmax().item())==0:
            suppressed.append(F.softplus(1.5-(row[label]-row[0])))
    if not suppressed:
        return logits.sum()*0
    # Divide by ALL gold objects, not the number of failures, so a single
    # remaining hard example never receives a runaway per-example multiplier.
    return torch.stack(suppressed).sum()/len(spans)


def stage_loss(head, output, labels, row, stage, *, memory=None, structured_weight=.40, hard_null_weight=0.0):
    """All seven stage objectives implemented against one fixed head interface."""
    if stage=='G1':
        logits = output['span_logits']
        valid = output['span_valid']
        targets = torch.zeros(logits.shape[:2], dtype=torch.long, device=logits.device)
        if not valid.any():
            raise ValueError('GAMER_GRAPH_NO_ELIGIBLE_SPANS')
        for span in labels['spans']:
            start, end = span['token_first'], span['token_last']
            width_index = end-start
            if width_index >= logits.shape[1]:
                raise ValueError('GAMER_GRAPH_GOLD_SPAN_EXCEEDS_MAX_SPAN_TOKENS')
            if not valid[start, width_index]:
                raise ValueError('GAMER_GRAPH_GOLD_SPAN_INVALID')
            if targets[start,width_index]:
                raise ValueError('GAMER_GRAPH_GOLD_SPAN_AMBIGUOUS')
            targets[start,width_index] = SPAN_KINDS.index(span['kind']) + 1
        selected_logits=logits[valid]
        selected_targets=targets[valid]
        positive=selected_targets>0
        if not positive.any():
            raise ValueError('GAMER_GRAPH_G1_REQUIRES_POSITIVE_SPANS')
        # The original G1 objective classified gold spans and mined hard
        # negatives independently.  It did not explicitly optimize the score
        # used by the greedy non-overlap decoder: type logit minus null logit.
        # G1's dev attribution showed null winning 227/266 missed objects and
        # competing spans blocking 38/266.  Train both decisions directly.
        positive_loss=F.cross_entropy(selected_logits[positive],selected_targets[positive])
        negative_losses=F.cross_entropy(selected_logits[~positive],selected_targets[~positive],
                                         reduction='none')
        if not len(negative_losses):
            return positive_loss
        hard_count=min(len(negative_losses),max(32,8*int(positive.sum().item())))
        hard_loss=negative_losses.topk(hard_count).values.mean()

        # Positive-versus-null margin: do not let a valid gold object be
        # preferred as 'not an object'.  This uses gold labels only in training.
        pos_logits=selected_logits[positive]
        pos_types=selected_targets[positive]
        gold_logit=pos_logits.gather(1,pos_types[:,None]).squeeze(1)
        null_margin=gold_logit-pos_logits[:,0]
        positive_null_loss=F.softplus(1.5-null_margin).mean()

        # The exact object must outrank ALL overlapping invalid proposals, as
        # the real decoder ranks candidates by best-type minus null margin.
        # This directly supervises complete boundaries, not lexemes or a
        # deterministic post-hoc merge. Every gold object is non-overlapping.
        size,max_width=valid.shape
        starts=torch.arange(size,device=valid.device)[:,None]
        ends=starts+torch.arange(max_width,device=valid.device)[None,:]
        best_nonnull=logits[...,1:].amax(dim=-1)
        decoder_margin=best_nonnull-logits[...,0]
        rank_losses=[]
        for span in labels['spans']:
            first,last=span['token_first'],span['token_last']
            rivals=valid & (starts <= last) & (ends >= first)
            rivals=rivals.clone()
            rivals[first,last-first]=False
            if not rivals.any():
                continue
            own_type=SPAN_KINDS.index(span['kind'])+1
            own_margin=logits[first,last-first,own_type]-logits[first,last-first,0]
            rival_margins=decoder_margin[rivals]
            # Top competitors dominate the actual greedy decoder.  Using a
            # small top-k also prevents thousands of irrelevant null spans
            # from overwhelming gold supervision.
            hardest=rival_margins.topk(min(8,rival_margins.numel())).values
            rank_losses.append(F.softplus(0.75+hardest-own_margin).mean())
        ranking_loss=torch.stack(rank_losses).mean() if rank_losses else positive_loss*0
        return (positive_loss+hard_loss+0.05*negative_losses.mean()+
                0.75*positive_null_loss+0.75*ranking_loss+
                structured_weight*g1_structured_object_set_loss(output,labels['spans'])+
                hard_null_weight*g1_hard_null_positive_loss(output,labels['spans']))
    idmap=_align_graph_nodes(labels,row,output)
    edges=row['graph']['edges']
    if stage=='G2':
        return _pair_loss(output['binding_logits'], labels['surface_ids'], edges, BIND_CLASSES)
    if stage=='G3':
        # Only relations grounded in observed objects, before latent operations.
        return _pair_loss(output['relationship_logits'], labels['surface_ids'], edges, REL_CLASSES)
    if stage=='G4':
        present=output['slot_presence_logits']
        count=len(labels['latent_nodes'])
        target=torch.zeros(len(present),dtype=torch.long,device=present.device)
        target[:count]=1
        loss=F.cross_entropy(present,target)
        if count:
            k=torch.tensor([NODE_KINDS.index(n['kind']) for n in labels['latent_nodes']],device=present.device)
            loss=loss + F.cross_entropy(output['slot_kind_logits'][:count],k)
            operators=torch.tensor([OP_CLASSES.index(n['operator']) if n['kind']=='operation' else 0
                       for n in labels['latent_nodes']],device=present.device)
            loss=loss + F.cross_entropy(output['operator_logits'][output['surface_count']:output['surface_count']+count],operators)
            op_targets=[]
            for node in labels['latent_nodes']:
                if node['kind']!='operation':continue
                idx=idmap[node['id']]
                args=sorted((e for e in edges if e['src']==node['id'] and e['type']=='operand'),
                            key=lambda e:e['position'])
                if len(args)>head.max_operands:raise ValueError('GAMER_GRAPH_UNSUPPORTED_OPERAND_ARITY')
                for pos,e in enumerate(args):
                    op_targets.append((idx,pos,idmap[e['dst']]))
                if len(args)<head.max_operands:
                    op_targets.append((idx,len(args),output['operand_logits'].shape[-1]-1))
            if op_targets:
                i,j,k=zip(*op_targets)
                loss=loss+F.cross_entropy(output['operand_logits'][list(i),list(j)],
                                          torch.tensor(k,device=present.device))
        # Score edges to operation nodes are structural and must also be learned.
        loss=loss+_pair_loss(output['relationship_logits'],idmap,edges,REL_CLASSES)
        return loss
    if stage=='G5':
        kind=row['graph']['objective']['kind']
        target=torch.tensor([OBJECTIVES.index(kind)],device=output['objective_logits'].device)
        loss=F.cross_entropy(output['objective_logits'].unsqueeze(0),target)
        candidate_labels=torch.zeros(len(output['candidate_logits']),device=target.device)
        for ident in row['graph']['objective']['candidates']:
            candidate_labels[idmap[ident]]=1
        return loss + F.binary_cross_entropy_with_logits(output['candidate_logits'],candidate_labels)
    if stage=='G6':
        if memory is None or 'memory_target' not in row:
            raise ValueError('GAMER_GRAPH_G6_REQUIRES_ACQUISITION_EPISODES')
        target=row['memory_target']
        if type(target) is not int or not 0<=target<memory.shape[0]:
            raise ValueError('GAMER_GRAPH_MEMORY_TARGET_INVALID')
        logits=head.memory_key(memory) @ head.memory_query(output['global_state']) / head.width**.5
        retrieval=F.cross_entropy(logits.unsqueeze(0),torch.tensor([target],device=logits.device))
        written=head.memory_write(output['global_state'],memory[target])
        write_alignment=1-F.cosine_similarity(written.unsqueeze(0),memory[target].unsqueeze(0)).mean()
        write_gate=F.binary_cross_entropy(output['memory_write_probability'],
                                         torch.ones_like(output['memory_write_probability']))
        # Retrieval alone is NOT game-rule acquisition. The same G6 memory
        # parameters must help the frozen upstream graph heads reconstruct
        # a correct graph from a follow-up prompt lacking the old rule facts.
        graph_losses=[stage_loss(head,output,labels,row,sub) for sub in STAGES[:5]]
        return retrieval + .1*write_alignment + .02*write_gate + sum(graph_losses)/len(graph_losses)
    if stage=='G7':
        losses=[stage_loss(head,output,labels,row,step,memory=memory)
                for step in STAGES[:-1]]
        return sum(losses)/len(losses)
    raise ValueError('GAMER_GRAPH_UNKNOWN_TRAINING_STAGE')


def _episode_memory(head,model,tok,row,device,max_length):
    """Build a neural episodic memory from earlier example PROMPTS.

    Teacher target selects the correct prior episode for TRAINING ONLY;
    inference gets only its persisted neural memory, never that target.
    """
    if 'memory_episode' not in row:
        raise ValueError('GAMER_GRAPH_G6_MISSING_ACQUISITION_EPISODE')
    episode=row['memory_episode']
    if (not isinstance(episode,dict) or set(episode)!={'candidate_prompts','target'} or
        not isinstance(episode['candidate_prompts'],list) or
        not 2<=len(episode['candidate_prompts'])<=head.memory_slots_count or
        type(episode['target']) is not int or
        not 0<=episode['target']<len(episode['candidate_prompts'])):
        raise ValueError('GAMER_GRAPH_INVALID_ACQUISITION_EPISODE')
    vectors=[]
    with torch.no_grad():
        for prompt in episode['candidate_prompts']:
            if not isinstance(prompt,str) or not prompt.strip():
                raise ValueError('GAMER_GRAPH_INVALID_MEMORY_SOURCE_PROMPT')
            prepared=backbone.prepare_prompt(tok,prompt,max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            vectors.append(head.encode(hidden.float())[1].detach())
    bank=torch.zeros((head.memory_slots_count,head.width),device=device)
    bank[:len(vectors)]=torch.stack(vectors)
    return bank,episode['target'],len(vectors)


def _new_head(hidden_size,config):
    return GamerGraphHead(hidden_size=hidden_size,width=config['width'],
        layers=config['layers'],latent_slots=config['latent_slots'],
        memory_slots=config['memory_slots'],max_operands=config['max_operands'],
        max_span_tokens=config.get('max_span_tokens',DEFAULT_MAX_SPAN_TOKENS))


def _load_checkpoint(path, *, device='cpu'):
    from safetensors.torch import load_file
    path=Path(path)
    manifest=json.loads((path/MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('schema')!=SCHEMA or manifest.get('status')!='TRAINED_NOT_CERTIFIED' or \
            manifest.get('head',{}).get('object_extractor')!='joint-span-v1':
        raise ValueError('GAMER_GRAPH_BAD_CHECKPOINT_SCHEMA')
    file=path/HEAD_FILE
    if not file.exists() or backbone.sha256(file)!=manifest['head_sha256']:
        raise ValueError('GAMER_GRAPH_HEAD_HASH_MISMATCH')
    head=_new_head(manifest['head']['hidden_size'],manifest['head'])
    head.load_state_dict(load_file(str(file),device='cpu'),strict=True)
    return head.to(device),manifest


def _verify_certification(certificate,parent_path,parent_manifest,expected):
    """Reopen the source reports, not just a claimed PASS bit in a JSON file."""
    cert=json.loads(Path(certificate).read_text(encoding='utf-8'))
    if (cert.get('schema')!=SCHEMA or cert.get('status')!='DEVELOPMENT_GATE_PASSED' or
        cert.get('certified_stage')!=expected or
        cert.get('head_sha256')!=parent_manifest['head_sha256'] or
        cert.get('dataset_sha256')!=parent_manifest['dataset_sha256'] or
        cert.get('checkpoint_path')!=str(Path(parent_path).resolve()) or
        parent_manifest.get('train_limit') or cert.get('production_promotion') is not False):
        raise PermissionError('GAMER_GRAPH_GATE_CERTIFICATE_INVALID')
    reports=cert.get('source_reports',{})
    hashes=cert.get('report_hashes',{})
    if set(reports)!={'dev','challenge'} or set(hashes)!={'dev','challenge'}:
        raise PermissionError('GAMER_GRAPH_GATE_CERTIFICATE_MISSING_SOURCE_REPORTS')
    checks=GATE_REQUIREMENTS[expected]
    for split,path in reports.items():
        file=Path(path)
        if not file.is_file() or backbone.sha256(file)!=hashes[split]:
            raise PermissionError('GAMER_GRAPH_GATE_SOURCE_REPORT_TAMPERED')
        r=json.loads(file.read_text(encoding='utf-8'))
        m=r.get('metrics',{})
        if (r.get('schema')!=SCHEMA or r.get('stage')!=expected or
            r.get('split')!=split or r.get('limited') or r.get('test_split_used') or
            r.get('checkpoint_head_sha256')!=parent_manifest['head_sha256'] or
            r.get('dataset_sha256')!=parent_manifest['dataset_sha256'] or
            r.get('cases',0)<(20 if split=='dev' else 12) or
            m.get(checks['metric'],0)<checks[split] or
            len(m.get('by_family',{}))<2 or
            m.get('zero_hidden_ablation',{}).get('net_gain',0)<checks['neural_gain'] or
            m.get('stage_component_ablation',{}).get('net_gain',0)<checks['neural_gain']):
            raise PermissionError('GAMER_GRAPH_GATE_SOURCE_REPORT_NOT_PASSING')
        if expected=='G1':
            if m.get('object_macro_f1',0)<checks['macro_f1'] or any(
                m.get('by_kind',{}).get(kind,{}).get('recall',0)<checks['kind_recall']
                for kind in ('actor','action','quantity')):
                raise PermissionError('GAMER_GRAPH_GATE_OBJECT_REPORT_NOT_PASSING')
    for key,path in cert.get('retention_sources',{}).items():
        file=Path(path)
        if not file.is_file() or backbone.sha256(file)!=cert.get('retention_hashes',{}).get(key):
            raise PermissionError('GAMER_GRAPH_GATE_RETENTION_REPORT_TAMPERED')
    return cert


def capture(model,tok,row,device,max_length):
    prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
    hidden=backbone.frozen_context(model,prepared,device=device)
    return {'prepared':prepared,
            'hidden':hidden.detach().to(device='cpu',dtype=torch.float16).contiguous(),
            'labels':aligned_labels(prepared,row)}


def train(*,dataset,output,source_adapter,stage='G1',parent_checkpoint=None,
          prerequisite_cert=None,model_id='Qwen/Qwen3-0.6B',device='cuda',
          steps=400,accum=4,lr=3e-4,seed=47,max_length=2048,
          width=192,layers=1,latent_slots=16,memory_slots=32,max_operands=8,
          max_span_tokens=DEFAULT_MAX_SPAN_TOKENS,
          train_limit=0,refine_checkpoint=None,refine_parent_dataset=None,
          counterfactual_fraction=.5,consistency_weight=.15,
          structured_weight=.40,family_balanced=False,controlled_exposure=False,
          parent_retention_weight=0.0,hard_null_weight=0.0):
    from safetensors.torch import save_file
    dataset=Path(dataset); output=Path(output); source_adapter=Path(source_adapter)
    if stage not in STAGES:raise ValueError('GAMER_GRAPH_UNKNOWN_TRAINING_STAGE')
    if steps<1 or accum<1 or lr<=0:raise ValueError('GAMER_GRAPH_INVALID_TRAINING_SETTINGS')
    if (not 0<=counterfactual_fraction<=1 or consistency_weight<0 or
            structured_weight<0 or parent_retention_weight<0 or hard_null_weight<0):
        raise ValueError('GAMER_GRAPH_INVALID_COUNTERFACTUAL_TRAINING_SETTINGS')
    if stage!='G1' and hard_null_weight:
        raise ValueError('GAMER_GRAPH_HARD_NULL_G1_ONLY')
    if stage!='G1' and (family_balanced or structured_weight!=.40 or controlled_exposure):
        raise ValueError('GAMER_GRAPH_BALANCED_TRAINING_G1_ONLY')
    if controlled_exposure and (stage!='G1' or not family_balanced or
          consistency_weight!=0 or train_limit!=0):
        raise ValueError('GAMER_GRAPH_EXPOSURE_CONTROL_REQUIRES_PURE_G1_REFINEMENT')
    if parent_retention_weight and (stage!='G1' or not refine_checkpoint):
        raise ValueError('GAMER_GRAPH_RETENTION_REQUIRES_G1_PARENT')
    if output.exists():raise FileExistsError('GAMER_GRAPH_OUTPUT_IMMUTABLE')
    if source_adapter.resolve() in output.resolve().parents or output.resolve()==source_adapter.resolve():
        raise ValueError('GAMER_GRAPH_CANNOT_WRITE_IN_SOURCE_ADAPTER')
    if refine_checkpoint and (stage!='G1' or parent_checkpoint or prerequisite_cert):
        raise ValueError('GAMER_GRAPH_REFINE_G1_ONLY')
    if parent_checkpoint is None and stage!='G1':
        raise PermissionError('GAMER_GRAPH_STAGE_LOCKED_REQUIRES_PARENT_GATE')
    if stage=='G1' and (parent_checkpoint or prerequisite_cert):
        raise ValueError('GAMER_GRAPH_G1_MUST_START_CLEAN')
    rows=load_rows(dataset)
    training=[r for r in rows if r['split']=='train']
    if train_limit: training=training[:train_limit]
    if (controlled_exposure and counterfactual_fraction>0 and
            not any(r.get('counterfactual_pair') for r in training)):
        raise ValueError('GAMER_GRAPH_BALANCED_FAMILY_BOTH_SOURCES_REQUIRED')
    model,tok=backbone.model_components(model_id,source_adapter,device)
    if any(p.requires_grad for p in model.parameters()):
        raise RuntimeError('GAMER_GRAPH_SOURCE_NOT_FROZEN')
    if refine_checkpoint is not None:
        head,refine_manifest=_load_checkpoint(refine_checkpoint,device=device)
        if refine_manifest['stage']!='G1':
            raise ValueError('GAMER_GRAPH_REFINE_NOT_G1_CHECKPOINT')
        if refine_manifest['dataset_sha256']!=backbone.sha256(dataset):
            if refine_parent_dataset is None:
                raise ValueError('GAMER_GRAPH_REFINE_DATASET_LINEAGE_CHANGED')
            base=Path(refine_parent_dataset)
            if backbone.sha256(base)!=refine_manifest['dataset_sha256']:
                raise ValueError('GAMER_GRAPH_REFINE_PARENT_DATASET_HASH_CHANGED')
            base_rows=load_rows(base)
            if len(rows)<=len(base_rows) or rows[:len(base_rows)]!=base_rows or \
                    any(row['split']!='train' or 'counterfactual_pair' not in row
                        for row in rows[len(base_rows):]):
                raise ValueError('GAMER_GRAPH_REFINE_DATASET_MUST_BE_TRAIN_ONLY_APPEND')
            parent_pair_ids={}
            for row in rows[len(base_rows):]:
                key=row['counterfactual_pair']
                parent_pair_ids.setdefault(key,[]).append(row.get('counterfactual_side'))
            if any(sorted(v)!=['a','b'] for v in parent_pair_ids.values()):
                raise ValueError('GAMER_GRAPH_REFINE_COUNTERFACTUAL_PAIR_INCOMPLETE')
        if refine_manifest['source_adapter_sha256']!=backbone.adapter_fingerprint(source_adapter):
            raise ValueError('GAMER_GRAPH_REFINE_SOURCE_LINEAGE_CHANGED')
        if refine_manifest['model_id']!=model_id or refine_manifest['head']['hidden_size']!=int(model.config.hidden_size):
            raise ValueError('GAMER_GRAPH_REFINE_MODEL_LINEAGE_CHANGED')
        if refine_manifest.get('train_limit'):
            raise ValueError('GAMER_GRAPH_REFINE_UNBOUNDED_PARENT_REQUIRED')
    elif parent_checkpoint is not None:
        head,parent_manifest=_load_checkpoint(parent_checkpoint,device=device)
        if not prerequisite_cert:
            raise PermissionError('GAMER_GRAPH_PREREQUISITE_CERT_REQUIRED')
        previous=STAGES[STAGES.index(stage)-1]
        _verify_certification(prerequisite_cert,parent_checkpoint,parent_manifest,previous)
        if parent_manifest['dataset_sha256']!=backbone.sha256(dataset):
            raise ValueError('GAMER_GRAPH_DATASET_LINEAGE_CHANGED')
        if parent_manifest['source_adapter_sha256']!=backbone.adapter_fingerprint(source_adapter):
            raise ValueError('GAMER_GRAPH_SOURCE_LINEAGE_CHANGED')
    else:
        config={'width':width,'layers':layers,'latent_slots':latent_slots,
                'memory_slots':memory_slots,'max_operands':max_operands,
                'max_span_tokens':max_span_tokens}
        # Construct ALL heads before G1. Future objectives are initially locked.
        head=_new_head(int(model.config.hidden_size),config).to(device)
    # Separate frozen teacher, never touched by AdamW and never serialized.
    # A retention weight of zero preserves the original training path exactly.
    parent_teacher=None
    if parent_retention_weight:
        parent_teacher=copy.deepcopy(head).eval()
        parent_teacher.requires_grad_(False)
    count=trainable_stage(head,stage)
    memory=None
    if STAGES.index(stage)>=5 and any('memory_episode' not in row for row in training):
        raise ValueError('GAMER_GRAPH_G6_REQUIRES_SEQUENCED_ACQUISITION_CORPUS')
    cache=[]
    for i,row in enumerate(training):
        item=capture(model,tok,row,device,max_length)
        if STAGES.index(stage)>=5:
            bank,target,size=_episode_memory(head,model,tok,row,device,max_length)
            item['memory']=bank.detach().to('cpu',dtype=torch.float16)
            item['memory_target']=target
            item['memory_count']=size
        cache.append(item)
        if (i+1)%50==0 or i==0 or i+1==len(training):
            print(f'GAMER_GRAPH_CAPTURE {i+1}/{len(training)} frozen_qwen_once_per_prompt',flush=True)
    optimizer=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],lr=lr)
    rng=random.Random(seed);head.train()
    pairs={};replay_indices=[]
    for i,row in enumerate(training):
        pair=row.get('counterfactual_pair') if stage=='G1' else None
        if pair:
            pairs.setdefault(pair,[]).append(i)
        else:
            replay_indices.append(i)
    if any(len(v)!=2 or sorted(training[i].get('counterfactual_side') for i in v)!=['a','b']
           for v in pairs.values()):
        raise ValueError('GAMER_GRAPH_TRAINING_COUNTERFACTUAL_PAIRS_INVALID')
    pair_choices=list(pairs.values())
    # Every curriculum ablation must encounter the same sampling schedule.
    # Alternating the two families produces exact encounter/allocation balance
    # even when the appended corpus is ordered or one family has longer prompts.
    families=('encounter','allocation')
    pair_by_family={family:[] for family in families}
    replay_by_family={family:[] for family in families}
    for group in pair_choices:
        pair_families={training[i]['family'] for i in group}
        if len(pair_families)!=1 or next(iter(pair_families)) not in pair_by_family:
            raise ValueError('GAMER_GRAPH_COUNTERFACTUAL_PAIR_FAMILY_MISMATCH')
        pair_by_family[next(iter(pair_families))].append(group)
    for i in replay_indices:
        if training[i]['family'] in replay_by_family:
            replay_by_family[training[i]['family']].append(i)
    if family_balanced and (not all(replay_by_family.values()) or
            (counterfactual_fraction>0 and not all(pair_by_family.values()))):
        raise ValueError('GAMER_GRAPH_BALANCED_FAMILY_BOTH_SOURCES_REQUIRED')
    if family_balanced and pair_choices:
        by_mode={(family,mode):0 for family in families
                 for mode in ('role_swap','renaming')}
        for group in pair_choices:
            sample=training[group[0]]
            marker=(sample['family'],sample.get('counterfactual_mode'))
            if marker not in by_mode or any(training[i].get('counterfactual_mode')!=marker[1]
                                            for i in group):
                raise ValueError('GAMER_GRAPH_BALANCED_INVALID_PAIR_MODE')
            by_mode[marker]+=1
        if len(set(by_mode.values()))!=1 or 0 in by_mode.values():
            raise ValueError('GAMER_GRAPH_BALANCED_PAIR_FAMILY_MODE_IMBALANCE')
    # Controlled exposure: draw immutable per-family replay/pair streams *before*
    # choosing their mixture.  Therefore an exposure change cannot change the
    # identity/order of the replay examples at a given replay ordinal.  All
    # arms use the same seed, optimizer, examples-per-microbatch weighting and
    # family schedule; no future-stage or held-out data enters this sampler.
    replay_stream={};pair_stream={}
    replay_stream_sha=None;pair_stream_sha=None
    if controlled_exposure:
        slots={family:sum(families[(step*accum+micro)%len(families)]==family
                          for step in range(steps) for micro in range(accum))
               for family in families}
        for family_index,family in enumerate(families):
            rr=random.Random(seed+1001+family_index*107)
            replay_stream[family]=[rr.choice(replay_by_family[family])
                                   for _ in range(slots[family])]
            if pair_by_family[family]:
                pr=random.Random(seed+2003+family_index*109)
                pair_stream[family]=[pr.choice(pair_by_family[family])
                                     for _ in range(slots[family])]
        replay_stream_sha=hashlib.sha256(json.dumps(replay_stream,sort_keys=True).encode()).hexdigest()
        if pair_stream:
            pair_stream_sha=hashlib.sha256(json.dumps(pair_stream,sort_keys=True).encode()).hexdigest()
    microbatch_counts={family:{'counterfactual':0,'replay':0} for family in families}
    torch.manual_seed(seed)  # Same dropout sequence for each controlled arm.
    history=[]
    parent_anchor_total=0
    parent_replay_items=0
    for step in range(1,steps+1):
        optimizer.zero_grad(set_to_none=True)
        parts=[]
        for micro in range(accum):
            if family_balanced:
                family=families[((step-1)*accum+micro)%len(families)]
                group=pair_by_family[family]
                replay=replay_by_family[family]
                counts=microbatch_counts[family]
                seen=counts['counterfactual']+counts['replay']
                # Bounded rounding gives equal source exposure for each family,
                # not merely equal totals with an accidental pair/replay skew.
                target_pairs=int((seen+1)*counterfactual_fraction+0.5)
                is_pair=counts['counterfactual']<target_pairs
                if controlled_exposure:
                    indices=(pair_stream[family][counts['counterfactual']] if is_pair else
                             [replay_stream[family][counts['replay']]])
                else:
                    indices=(rng.choice(group) if is_pair else [rng.choice(replay)])
                counts['counterfactual' if is_pair else 'replay']+=1
            else:
                is_pair=bool(pair_choices) and rng.random()<counterfactual_fraction
                indices=(rng.choice(pair_choices) if is_pair else
                         [rng.choice(replay_indices if replay_indices else list(range(len(training))))])
            predictions=[];local_losses=[]
            for i in indices:
                item=cache[i];row=training[i]
                hidden=item['hidden'].to(device=device,dtype=torch.float32)
                prep=item['prepared'];lab=item['labels']
                bank=item['memory'].to(device=device,dtype=torch.float32) if 'memory' in item else None
                output_head=head(hidden,eligible=prep['eligible'].to(device),
                    training_spans=lab['spans'] if stage!='G1' else None,
                    memory=bank,active_stage=stage)
                episode_row={**row,'memory_target':item.get('memory_target')}
                stage_objective=stage_loss(head,output_head,lab,episode_row,stage,
                    memory=bank,structured_weight=structured_weight,hard_null_weight=hard_null_weight)
                if parent_teacher is not None and not row.get('counterfactual_pair'):
                    with torch.no_grad():
                        parent_output=parent_teacher(hidden,eligible=prep['eligible'].to(device),
                                                     active_stage='G1')
                    tether,anchors=g1_parent_retention_loss(output_head,parent_output,lab)
                    stage_objective=stage_objective+parent_retention_weight*tether
                    parent_anchor_total+=anchors
                    parent_replay_items+=1
                local_losses.append(stage_objective)
                predictions.append((output_head,lab))
            loss=torch.stack(local_losses).mean()
            if is_pair and consistency_weight:
                loss=loss+consistency_weight*g1_counterfactual_consistency(
                    predictions[0][0],predictions[0][1],predictions[1][0],predictions[1][1])
            if not torch.isfinite(loss):raise ValueError('GAMER_GRAPH_NONFINITE_LOSS')
            (loss/accum).backward()
            parts.append(float(loss.detach()))
        torch.nn.utils.clip_grad_norm_([p for p in head.parameters() if p.requires_grad],1.0)
        optimizer.step()
        value=sum(parts)/len(parts)
        history.append(value)
        if step==1 or step%50==0 or step==steps:
            print(f'GAMER_GRAPH_{stage}_TRAIN step={step}/{steps} loss={value:.6f}',flush=True)
    output.mkdir(parents=True)
    head_file=output/HEAD_FILE
    save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},str(head_file))
    manifest={'schema':SCHEMA,'status':'TRAINED_NOT_CERTIFIED','stage':stage,
         'all_stages_instantiated':list(STAGES),'stage_trainable_parameters':count,
         'head':{'hidden_size':head.hidden_size,'width':head.width,'layers':head.layers,
                 'latent_slots':head.latent_slots_count,'memory_slots':head.memory_slots_count,
                 'max_operands':head.max_operands,
                 'max_span_tokens':head.max_span_tokens,
                 'object_extractor':'joint-span-v1'},
         'head_sha256':backbone.sha256(head_file),'dataset_sha256':backbone.sha256(dataset),
         'source_adapter_sha256':backbone.adapter_fingerprint(source_adapter),
         'source_adapter':str(source_adapter.resolve()),'model_id':model_id,
         'parent_checkpoint':str(Path(parent_checkpoint).resolve()) if parent_checkpoint else None,
         'parent_cert':str(Path(prerequisite_cert).resolve()) if prerequisite_cert else None,
         'backbone_frozen':True,'lora_frozen':True,'generated_tokens':0,
         'train_examples':len(training),'train_limit':train_limit,
         'steps':steps,'accum':accum,'lr':lr,'seed':seed,'last_loss':history[-1],
         'test_split_used':False,'production_promotion':False,
         'next_training_gate':'LOCKED_PENDING_CERTIFICATION',
         'g1_training_objective':('positive-null-overlap-exposure-controlled-v1'
             if controlled_exposure else
             'positive-null-overlap-structured-set-counterfactual-v2-balanced'
             if family_balanced else 'positive-null-overlap-structured-set-counterfactual-v1'),
         'counterfactual_pair_count':len(pair_choices),
         'counterfactual_fraction':counterfactual_fraction,
         'consistency_weight':consistency_weight,
         'parent_retention_weight':parent_retention_weight,
         'g1_parent_retention_anchor_count':parent_anchor_total,
         'g1_parent_retention_replay_items':parent_replay_items,
         'structured_weight':structured_weight,
         'hard_null_weight':hard_null_weight,
         'g1_family_balanced':bool(family_balanced),
         'g1_controlled_exposure':bool(controlled_exposure),
         'g1_replay_stream_sha256':replay_stream_sha,
         'g1_pair_stream_sha256':pair_stream_sha,
         'g1_microbatch_family_counts':microbatch_counts if family_balanced else None,
         'g1_parent_dataset_sha256':backbone.sha256(refine_parent_dataset) if refine_parent_dataset else None,
         'g1_refined_from':str(Path(refine_checkpoint).resolve()) if refine_checkpoint else None,
         'g1_refined_head_sha256':refine_manifest['head_sha256'] if refine_checkpoint else None}
    (output/MANIFEST).write_text(json_text(manifest)+'\n',encoding='utf-8')
    return manifest


def _ablate_stage_head(output,stage):
    """Remove the proposed newly learned operation without changing prompts."""
    result=dict(output)
    fields={
        'G1':('span_logits',),
        'G2':('binding_logits',),
        'G3':('relationship_logits',),
        'G4':('slot_presence_logits','slot_kind_logits','operator_logits',
              'operand_logits','relationship_logits'),
        'G5':('objective_logits','candidate_logits'),
        'G6':('global_state',),
        'G7':('span_logits','binding_logits','relationship_logits','operator_logits',
              'operand_logits','objective_logits','candidate_logits'),
    }[stage]
    for field in fields:
        if field in result:
            result[field]=torch.zeros_like(result[field])
    if 'span_logits' in fields and 'spans' in result:
        result['spans']=[]
    return result


def _char_objects(output,prepared,*,normalize_whitespace=True):
    """Neural object spans in author-space, not raw tokenizer-space.

    Whitespace normalization is game-independent and shared with the graph
    decoder. The optional unnormalized form is reported for audit only.
    """
    spans=decode_neural_spans(output['span_logits'], output['span_valid'])
    result=set()
    for span in spans:
        bounds=token_span_char_bounds(span,prepared,
                                     normalize_whitespace=normalize_whitespace)
        if bounds is not None:result.add((*bounds,span['kind']))
    return result


def _normalized_edges(graph,types):
    ids={n['id']:n for n in graph['nodes']}
    def anchor(ident):
        node=ids[ident]
        return (node['kind'],tuple(node['span'])) if 'span' in node else None
    return {(anchor(e['src']),anchor(e['dst']),e['type']) for e in graph['edges']
            if e['type'] in types and anchor(e['src']) and anchor(e['dst'])}


def _operation_signatures(graph):
    """Compare executable mathematical subgraphs without relying on node IDs."""
    nodes={n['id']:n for n in graph['nodes']}
    args={}
    for e in graph['edges']:
        if e['type']=='operand':args.setdefault(e['src'],[]).append(e)
    def signature(ident,visiting=None):
        visiting=set() if visiting is None else visiting
        if ident in visiting:return ('cycle',)
        node=nodes[ident]
        if node['kind']!='operation':
            return (node['kind'],tuple(node['span'])) if 'span' in node else (node['kind'],'ungrounded')
        if 'operator' not in node:return ('missing_operator',)
        return (node['operator'],tuple(signature(e['dst'],visiting|{ident})
               for e in sorted(args.get(ident,[]),key=lambda e:e['position'])))
    return sorted(str(signature(n['id'])) for n in graph['nodes'] if n['kind']=='operation')


def _objective_signature(graph):
    objective=graph['objective']
    if not objective:return None
    nodes={n['id']:n for n in graph['nodes']}
    options=[]
    for ident in objective['candidates']:
        node=nodes[ident]
        if 'span' not in node:return None
        options.append(tuple(node['span']))
    return objective['kind'],tuple(sorted(options))


def _compare(pred, gold, stage):
    if stage=='G2':
        a,b=_normalized_edges(pred,BINDINGS),_normalized_edges(gold,BINDINGS)
        return a==b, a,b
    if stage=='G3':
        a,b=_normalized_edges(pred,RELATIONS),_normalized_edges(gold,RELATIONS)
        return a==b,a,b
    if stage=='G4':
        a,b=_operation_signatures(pred),_operation_signatures(gold)
        return a==b,a,b
    if stage=='G5':
        a,b=_objective_signature(pred),_objective_signature(gold)
        return a==b,a,b
    if stage=='G6':
        # Acquisition must reconstruct usable mathematical rules, not just
        # identify which stored prompt looks similar.
        return _compare(pred,gold,'G7')
    if stage=='G7':
        try:
            observed=execute_graph(pred)
            truth=execute_graph(gold)
            pred_nodes={n['id']:n for n in pred['nodes']}
            gold_nodes={n['id']:n for n in gold['nodes']}
            def scored(result,nodes):
                return {tuple(nodes[ident]['span']):value for ident,value in result['scores'].items()}
            pred_scores=scored(observed,pred_nodes)
            gold_scores=scored(truth,gold_nodes)
            # A correct winner with wrong quantities or a fabricated graph is
            # NOT accepted as successful integrated generalization.
            pred_quantities={(tuple(n['span']),str(exact_number(n['value'])) if 'value' in n else None) for n in pred['nodes']
                            if n['kind']=='quantity' and 'span' in n}
            gold_quantities={(tuple(n['span']),str(exact_number(n['value'])) if 'value' in n else None) for n in gold['nodes']
                            if n['kind']=='quantity' and 'span' in n}
            complete=(pred_scores==gold_scores and
                      pred_quantities==gold_quantities and
                      _operation_signatures(pred)==_operation_signatures(gold) and
                      _objective_signature(pred)==_objective_signature(gold) and
                      _normalized_edges(pred,BINDINGS)==_normalized_edges(gold,BINDINGS) and
                      _normalized_edges(pred,RELATIONS)==_normalized_edges(gold,RELATIONS))
            return complete,pred_scores,gold_scores
        except (ValueError,KeyError,TypeError):
            return False,set(),{'INCOMPLETE_OR_INVALID_NEURAL_GRAPH'}
    raise ValueError('GAMER_GRAPH_UNKNOWN_COMPARISON_STAGE')


def evaluate(*,dataset,checkpoint,output,split='dev',limit=0,device='cuda',
             ablation_limit=8,max_length=2048,final_test=False,
             final_test_certificate=None,target_stage=None):
    dataset=Path(dataset);checkpoint=Path(checkpoint);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_GRAPH_EVAL_IMMUTABLE')
    if split not in ('train','dev','challenge','test'):
        raise ValueError('GAMER_GRAPH_UNKNOWN_EVAL_SPLIT')
    if split=='test' and not final_test:
        raise PermissionError('GAMER_GRAPH_BLIND_TEST_SEALED')
    head,manifest=_load_checkpoint(checkpoint,device=device)
    if split=='test':
        if manifest['stage']!='G7' or not final_test_certificate or limit:
            raise PermissionError('GAMER_GRAPH_BLIND_TEST_REQUIRES_CERTIFIED_G7_FULL_RUN')
        _verify_certification(final_test_certificate,checkpoint,manifest,'G7')
        # Reserve one-shot use before exposing labels; repeated tuning on the
        # blind split is prohibited. An interrupted run requires a NEW blind
        # evaluation dataset, not reuse of the same supposedly blind split.
        with (checkpoint/'blind_test_consumed.json').open('x',encoding='utf-8') as f:
            f.write(json_text({'head_sha256':manifest['head_sha256'],
                               'destination':str(output.resolve()),
                               'status':'BLIND_TEST_CONSUMED'})+'\n')
    if manifest['dataset_sha256']!=backbone.sha256(dataset):
        raise ValueError('GAMER_GRAPH_EVAL_DATASET_LINEAGE')
    if manifest['source_adapter_sha256']!=backbone.adapter_fingerprint(manifest['source_adapter']):
        raise ValueError('GAMER_GRAPH_EVAL_SOURCE_LINEAGE')
    rows=[r for r in load_rows(dataset) if r['split']==split]
    if limit:rows=rows[:limit]
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    head.eval()
    stage=target_stage or manifest['stage']
    if stage not in STAGES or STAGES.index(stage)>STAGES.index(manifest['stage']):
        raise PermissionError('GAMER_GRAPH_CANNOT_EVALUATE_UNTRAINED_FUTURE_STAGE')
    total=correct=gold_count=pred_count=tp=0
    raw_object_exact=raw_object_tp=raw_object_pred_count=0
    by_family=Counter();family_ok=Counter()
    kind_gold=Counter();kind_tp=Counter();kind_pred=Counter()
    zero_correct=0; component_correct=0; abl_cases=0
    gold_bindings=gold_relations=gold_operations=0
    details=[]
    for i,row in enumerate(rows):
        item=capture(model,tok,row,device,max_length)
        h=item['hidden'].to(device=device,dtype=torch.float32)
        p=item['prepared'];elig=p['eligible'].to(device)
        gold_bindings+=sum(e['type'] in BINDINGS for e in row['graph']['edges'])
        gold_relations+=sum(e['type'] in RELATIONS and 'span' in next(n for n in row['graph']['nodes'] if n['id']==e['src']) and 'span' in next(n for n in row['graph']['nodes'] if n['id']==e['dst']) for e in row['graph']['edges'])
        gold_operations+=sum(n['kind']=='operation' for n in row['graph']['nodes'])
        bank=None;memory_target=None;memory_count=None
        if STAGES.index(stage)>=5:
            if 'memory_episode' not in row:
                raise ValueError('GAMER_GRAPH_MEMORY_EVALUATION_REQUIRES_EPISODE')
            bank,memory_target,memory_count=_episode_memory(head,model,tok,row,device,max_length)
        with torch.inference_mode():
            output_head=head(h,eligible=elig,memory=bank,active_stage=stage)
        found=_char_objects(output_head,p)
        raw_found=_char_objects(output_head,p,normalize_whitespace=False)
        gold={(x['start'],x['end'],x['kind']) for x in row['object_spans']}
        raw_object_tp+=len(raw_found&gold)
        raw_object_pred_count+=len(raw_found)
        raw_object_exact+=int(raw_found==gold)
        tp+=len(found&gold);pred_count+=len(found);gold_count+=len(gold)
        for kind in SPAN_KINDS:
            kind_gold[kind]+=sum(g[2]==kind for g in gold)
            kind_pred[kind]+=sum(g[2]==kind for g in found)
            kind_tp[kind]+=sum(g[2]==kind for g in gold&found)
        metric=GATE_REQUIREMENTS[stage]['metric']
        if stage=='G1':
            passed=found==gold
            expected=gold;observed=found
        else:
            graph,surfaces=decode_graph(output_head,p,unlocked=stage)
            passed,observed,expected=_compare(graph,row['graph'],stage)
        total+=1;correct+=int(passed)
        by_family[row['family']]+=1
        family_ok[row['family']]+=int(passed)
        if i<ablation_limit:
            # A real causal control, same head with content-free backbone vectors.
            with torch.inference_mode():
                zero=head(torch.zeros_like(h),eligible=elig,memory=bank,active_stage=stage)
            if stage=='G1':
                zero_passed=_char_objects(zero,p)==gold
            else:
                zg,_=decode_graph(zero,p,unlocked=stage)
                zero_passed,_,_=_compare(zg,row['graph'],stage)
            zero_correct+=int(zero_passed)
            ablated=_ablate_stage_head(output_head,stage)
            if stage=='G1':
                component_passed=_char_objects(ablated,p)==gold
            elif stage=='G6':
                # Remove the acquired memory itself and recompute the graph.
                with torch.inference_mode():
                    no_memory=head(h,eligible=elig,memory=torch.zeros_like(bank),active_stage=stage)
                ab_graph,_=decode_graph(no_memory,p,unlocked=stage)
                component_passed,_,_=_compare(ab_graph,row['graph'],stage)
            else:
                ab_graph,_=decode_graph(ablated,p,unlocked=stage)
                component_passed,_,_=_compare(ab_graph,row['graph'],stage)
            component_correct+=int(component_passed)
            abl_cases+=1
        details.append({'index':i+1,'family':row['family'],'passed':bool(passed),
                        'object_expected':len(gold),'object_predicted':len(found),
                        'object_correct':len(gold&found),
                        'raw_token_boundary_correct':len(raw_found&gold),
                        'boundary_normalization_new_matches':len(found&gold)-len(raw_found&gold),
                        'missing_objects':sorted(map(str,gold-found))[:12],
                        'extra_objects':sorted(map(str,found-gold))[:12]})
        if i==0 or (i+1)%10==0 or i+1==len(rows):
            print(f'GAMER_GRAPH_{stage}_EVAL {i+1}/{len(rows)} {metric}={correct}',flush=True)
    precision=tp/max(pred_count,1);recall=tp/max(gold_count,1)
    micro_f1=2*precision*recall/max(precision+recall,1e-12)
    class_f1=[]
    for kind in SPAN_KINDS:
        if kind_gold[kind]:
            cp=kind_tp[kind]/max(kind_pred[kind],1)
            cr=kind_tp[kind]/kind_gold[kind]
            class_f1.append(2*cp*cr/max(cp+cr,1e-12))
    macro_f1=sum(class_f1)/max(len(class_f1),1)
    raw_precision=raw_object_tp/max(raw_object_pred_count,1)
    raw_recall=raw_object_tp/max(gold_count,1)
    raw_f1=2*raw_precision*raw_recall/max(raw_precision+raw_recall,1e-12)
    metrics={metric:correct/max(total,1),
             'token_boundary_whitespace_normalized': True,
             'raw_token_boundary_object_exact_rate':raw_object_exact/max(total,1),
             'raw_token_boundary_object_micro_f1':raw_f1,
             'gold_binding_edges':gold_bindings,'gold_surface_relation_edges':gold_relations,
             'gold_operation_nodes':gold_operations,
             'object_exact_rate':sum(d['object_correct']==d['object_expected']==d['object_predicted'] for d in details)/max(total,1),
             'object_micro_precision':precision,'object_micro_recall':recall,
             'object_macro_f1':macro_f1,'object_micro_f1':micro_f1,
             'by_family':{k:{'correct':family_ok[k],'total':v,'rate':family_ok[k]/v} for k,v in sorted(by_family.items())},
             'by_kind':{k:{'correct':kind_tp[k],'total':v,'recall':kind_tp[k]/v}
                         for k,v in kind_gold.items() if v},
             'zero_hidden_ablation':{'cases':abl_cases,'correct':zero_correct,
                 'head_first_n_correct':sum(x['passed'] for x in details[:abl_cases]),
                 'net_gain': (sum(x['passed'] for x in details[:abl_cases])-zero_correct)/max(abl_cases,1)},
             'stage_component_ablation':{'cases':abl_cases,'correct':component_correct,
                'head_first_n_correct':sum(x['passed'] for x in details[:abl_cases]),
                'net_gain':(sum(x['passed'] for x in details[:abl_cases])-component_correct)/max(abl_cases,1)}}
    report={'schema':SCHEMA,'status':'MEASURED_NOT_CERTIFIED','stage':stage,
          'checkpoint_stage':manifest['stage'],
          'split':split,'cases':total,'limited':bool(limit), 'metrics':metrics,
          'checkpoint_head_sha256':manifest['head_sha256'],
          'dataset_sha256':manifest['dataset_sha256'],
          'source_adapter_sha256':manifest['source_adapter_sha256'],
          'generated_tokens':0,'teacher_graph_at_inference':False,
          'game_specific_parser_at_inference':False,
          'test_split_used':split=='test','cases_detail':details,
          'scope':'The score measures the declared stage; later stages remain locked.'}
    output.mkdir(parents=True)
    (output/'report.json').write_text(json_text(report)+'\n',encoding='utf-8')
    return report


def certify(*,checkpoint,dev_report,challenge_report,output,
            retention_dev=None,retention_challenge=None):
    """Developmental gate (not publication certification); no training here."""
    checkpoint=Path(checkpoint);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_GRAPH_GATE_OUTPUT_IMMUTABLE')
    _,manifest=_load_checkpoint(checkpoint,device='cpu')
    stage=manifest['stage']; gate=GATE_REQUIREMENTS[stage]
    reports=[];failures=[]
    if manifest.get('train_limit'):
        failures.append('subset_smoke_training_not_certifiable')
    for path,split in ((dev_report,'dev'),(challenge_report,'challenge')):
        path=Path(path)
        r=json.loads(path.read_text(encoding='utf-8'))
        if (r.get('schema')!=SCHEMA or r.get('stage')!=stage or r.get('split')!=split or
            r.get('checkpoint_head_sha256')!=manifest['head_sha256'] or
            r.get('dataset_sha256')!=manifest['dataset_sha256'] or
            r.get('limited') or r.get('test_split_used') or r.get('generated_tokens') !=0):
            raise ValueError('GAMER_GRAPH_GATE_REPORT_LINEAGE_OR_SCOPE_INVALID')
        reports.append((r,path,split))
        if r['cases'] < (20 if split=='dev' else 12):failures.append(f'{split}:not_enough_cases')
        score=r['metrics'].get(gate['metric'])
        if not isinstance(score,(int,float)) or score < gate[split]:
            failures.append(f'{split}:{gate["metric"]}_below_threshold')
        if len(r['metrics']['by_family'])<2:failures.append(f'{split}:family_diversity')
        if r['metrics']['zero_hidden_ablation']['cases']<8:
            failures.append(f'{split}:missing_causal_ablation')
        if r['metrics']['zero_hidden_ablation']['net_gain']<gate['neural_gain']:
            failures.append(f'{split}:neural_causality_not_demonstrated')
        if r['metrics'].get('stage_component_ablation',{}).get('net_gain',0)<gate['neural_gain']:
            failures.append(f'{split}:stage_component_causality_not_demonstrated')
        if stage=='G2' and r['metrics'].get('gold_binding_edges',0)<r['cases']:
            failures.append(f'{split}:no_substantive_binding_supervision')
        if stage=='G3' and r['metrics'].get('gold_surface_relation_edges',0)<r['cases']:
            failures.append(f'{split}:no_substantive_relation_supervision')
        if stage=='G4' and r['metrics'].get('gold_operation_nodes',0)<r['cases']:
            failures.append(f'{split}:no_substantive_operation_supervision')
        if stage=='G1':
            if r['metrics']['object_macro_f1']<gate['macro_f1']:
                failures.append(f'{split}:object_f1')
            by_kind=r['metrics']['by_kind']
            for k in ('actor','action','quantity'):
                if k not in by_kind or by_kind[k]['recall']<gate['kind_recall']:
                    failures.append(f'{split}:missing_or_inaccurate_{k}')
    # Earlier skills must be re-evaluated on CURRENT weights, not inherited
    # from an old checkpoint. This protects certified competencies.
    retention_dev=retention_dev or {}
    retention_challenge=retention_challenge or {}
    for prior in STAGES[:STAGES.index(stage)]:
        for label,mapping in (('dev',retention_dev),('challenge',retention_challenge)):
            if prior not in mapping:
                failures.append(f'{label}:{prior}:retention_report_missing')
                continue
            r=json.loads(Path(mapping[prior]).read_text(encoding='utf-8'))
            requirement=GATE_REQUIREMENTS[prior]
            if (r.get('schema')!=SCHEMA or r.get('stage')!=prior or
                r.get('checkpoint_stage')!=stage or r.get('split')!=label or
                r.get('checkpoint_head_sha256')!=manifest['head_sha256'] or
                r.get('dataset_sha256')!=manifest['dataset_sha256'] or r.get('limited')):
                failures.append(f'{label}:{prior}:retention_lineage_invalid')
                continue
            score=r['metrics'].get(requirement['metric'])
            if score is None or score<requirement[label]:
                failures.append(f'{label}:{prior}:retention_regression')
    passed=not failures
    cert={'schema':SCHEMA,'status':'DEVELOPMENT_GATE_PASSED' if passed else 'DEVELOPMENT_GATE_BLOCKED',
          'certified_stage':stage,'next_stage':STAGES[STAGES.index(stage)+1] if passed and stage!='G7' else None,
          'head_sha256':manifest['head_sha256'],'dataset_sha256':manifest['dataset_sha256'],
          'checkpoint_path':str(checkpoint.resolve()),
          'report_hashes':{split:backbone.sha256(path) for _,path,split in reports},
          'source_reports':{split:str(path.resolve()) for _,path,split in reports},
          'retention_hashes':{f'{split}:{prior}':backbone.sha256(path)
               for split,mapping in (('dev',retention_dev),('challenge',retention_challenge))
               for prior,path in mapping.items()},
          'retention_sources':{f'{split}:{prior}':str(Path(path).resolve())
               for split,mapping in (('dev',retention_dev),('challenge',retention_challenge))
               for prior,path in mapping.items()},
          'requirements':gate,'failures':failures,
          'scope':'Developmental advancement only, NOT blind test or production promotion',
          'production_promotion':False}
    output.mkdir(parents=True)
    (output/'certification.json').write_text(json_text(cert)+'\n',encoding='utf-8')
    return cert


def plan():
    return {'architecture':'ONE_NEURAL_TYPED_GRAPH_CONSTRUCTOR',
            'available_components':['joint_span_objects','binding','relationship','latent_operations',
                                    'operand_pointers','objective','neural_memory'],
            'stages':[{ 'stage':s,'goal':STAGE_NAMES[s],'thresholds':GATE_REQUIREMENTS[s]}
                      for s in STAGES],
            'initially_unlocked':'G1','next_stages':'CERTIFICATE_GATED',
            'no_generation':True,'no_game_specific_inference_parser':True,
            'numeric_executor':'EXACT_FRACTION_GENERIC_DAG',
            'blind_test':'SEALED_UNTIL_INDEPENDENT_G7_CERTIFICATION',
            'production_promotion':False}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    sub.add_parser('plan')
    exposure=sub.add_parser('audit-g1-exposure')
    exposure.add_argument('--replay',required=True)
    exposure.add_argument('--fraction-10',required=True)
    exposure.add_argument('--fraction-25',required=True)
    exposure.add_argument('--fraction-50',required=True)
    exposure.add_argument('--output',required=True)
    miss=sub.add_parser('audit-g1-missed-spans')
    miss.add_argument('--control',required=True)
    miss.add_argument('--structured',required=True)
    miss.add_argument('--null-focused',required=True)
    miss.add_argument('--output',required=True)
    miss.add_argument('--baseline-diagnosis')
    miss.add_argument('--control-diagnosis')
    miss.add_argument('--structured-diagnosis')
    miss.add_argument('--null-focused-diagnosis')
    retain=sub.add_parser('audit-g1-retention')
    retain.add_argument('--control',required=True)
    retain.add_argument('--retained',required=True)
    retain.add_argument('--output',required=True)
    audit=sub.add_parser('audit-g1-ablations')
    audit.add_argument('--none',required=True)
    audit.add_argument('--structured',required=True)
    audit.add_argument('--consistency',required=True)
    audit.add_argument('--combined',required=True)
    audit.add_argument('--output',required=True)
    cmp=sub.add_parser('compare-g1')
    cmp.add_argument('--baseline-dataset',required=True)
    cmp.add_argument('--candidate-dataset')
    cmp.add_argument('--baseline-checkpoint',required=True)
    cmp.add_argument('--candidate-checkpoint')
    cmp.add_argument('--output',required=True)
    cmp.add_argument('--device',default='cuda')
    cmp.add_argument('--max-length',type=int,default=2048)
    frozen=sub.add_parser('frob-g1-frozen-decoder',
        help='Development-only fixed-score whole-span decoder comparison; no training or promotion')
    frozen.add_argument('--dataset',required=True)
    frozen.add_argument('--checkpoint',required=True)
    frozen.add_argument('--baseline-diagnosis',required=True)
    frozen.add_argument('--output',required=True)
    frozen.add_argument('--device',default='cuda')
    frozen.add_argument('--max-length',type=int,default=2048)
    frozen.add_argument('--expected-head-sha256',
        default='114150ae0ef351a2bd365b63c6a7fd70ac53d973d673577c5a910f6b8e1a7c90')
    transfer=sub.add_parser('frob-g1-decoder-transfer',
        help='Frozen linear decoder across G1-0007 and three G1-0008 checkpoints; development only')
    transfer.add_argument('--root',required=True)
    transfer.add_argument('--output',required=True)
    transfer.add_argument('--reference-report')
    transfer.add_argument('--device',default='cuda')
    transfer.add_argument('--max-length',type=int,default=2048)
    fresh=sub.add_parser('frob-g1-fresh-dev',
        help='Frozen top-two checkpoints on newly seeded dev examples; no G2 or challenge')
    fresh.add_argument('--root',required=True)
    fresh.add_argument('--transfer-report',required=True)
    fresh.add_argument('--output',required=True)
    fresh.add_argument('--device',default='cuda')
    fresh.add_argument('--max-length',type=int,default=2048)
    null_boundary=sub.add_parser('frob-g1-null-boundary',
        help='Read-only null-vs-object margin and decoder-conflict probe; dev only, no G2')
    null_boundary.add_argument('--root',required=True)
    null_boundary.add_argument('--transfer-report',required=True)
    null_boundary.add_argument('--fresh-run',required=True)
    null_boundary.add_argument('--output',required=True)
    null_boundary.add_argument('--device',default='cuda')
    null_boundary.add_argument('--max-length',type=int,default=2048)
    validation=sub.add_parser('frob-g1-calibration-validation',
        help='Read-only four-way frozen null calibration on 320 new dev prompts; no G2')
    validation.add_argument('--root',required=True)
    validation.add_argument('--transfer-report',required=True)
    validation.add_argument('--fresh-run',required=True)
    validation.add_argument('--null-run',required=True)
    validation.add_argument('--output',required=True)
    validation.add_argument('--device',default='cuda')
    validation.add_argument('--max-length',type=int,default=2048)
    action_errors=sub.add_parser('frob-g1-action-errors',
        help='Frozen G1-0014 winner action-error attribution; read-only dev, G2 locked')
    action_errors.add_argument('--root',required=True)
    action_errors.add_argument('--transfer-report',required=True)
    action_errors.add_argument('--fresh-run',required=True)
    action_errors.add_argument('--null-run',required=True)
    action_errors.add_argument('--validation-run',required=True)
    action_errors.add_argument('--output',required=True)
    action_errors.add_argument('--device',default='cuda')
    action_errors.add_argument('--max-length',type=int,default=2048)
    action_ranking=sub.add_parser('frob-g1-action-ranking',
        help='G1-0016 frozen action-span ranking ablation on G1-0014 dev; G2 locked')
    action_ranking.add_argument('--root',required=True)
    action_ranking.add_argument('--transfer-report',required=True)
    action_ranking.add_argument('--fresh-run',required=True)
    action_ranking.add_argument('--null-run',required=True)
    action_ranking.add_argument('--validation-run',required=True)
    action_ranking.add_argument('--action-run',required=True)
    action_ranking.add_argument('--output',required=True)
    action_ranking.add_argument('--device',default='cuda')
    action_ranking.add_argument('--max-length',type=int,default=2048)
    action_width_validation=sub.add_parser('frob-g1-action-width-validation',
        help='G1-0018 frozen two-way action-width comparison on 320 unseen dev prompts; G2 locked')
    action_width_validation.add_argument('--root',required=True)
    action_width_validation.add_argument('--transfer-report',required=True)
    action_width_validation.add_argument('--fresh-run',required=True)
    action_width_validation.add_argument('--null-run',required=True)
    action_width_validation.add_argument('--validation-run',required=True)
    action_width_validation.add_argument('--action-run',required=True)
    action_width_validation.add_argument('--ranking-run',required=True)
    action_width_validation.add_argument('--output',required=True)
    action_width_validation.add_argument('--device',default='cuda')
    action_width_validation.add_argument('--max-length',type=int,default=2048)
    d=sub.add_parser('diagnose-g1')
    d.add_argument('--dataset',required=True);d.add_argument('--checkpoint',required=True)
    d.add_argument('--output',required=True);d.add_argument('--device',default='cuda')
    d.add_argument('--limit',type=int,default=0);d.add_argument('--max-length',type=int,default=2048)
    t=sub.add_parser('train')
    t.add_argument('--dataset',required=True);t.add_argument('--output',required=True)
    t.add_argument('--source-adapter',required=True)
    t.add_argument('--stage',default='G1',choices=STAGES)
    t.add_argument('--parent-checkpoint');t.add_argument('--prerequisite-cert')
    t.add_argument('--refine-checkpoint',help='Continue G1 from an existing immutable G1 checkpoint; produces a new checkpoint')
    t.add_argument('--refine-parent-dataset',help='Required for training-only extension of existing immutable dataset')
    t.add_argument('--counterfactual-fraction',type=float,default=.5)
    t.add_argument('--consistency-weight',type=float,default=.15)
    t.add_argument('--structured-weight',type=float,default=.40)
    t.add_argument('--family-balanced',action='store_true',
                   help='Exactly balance encounter and allocation training microbatches')
    t.add_argument('--hard-null-weight',type=float,default=0.0,
                   help='Additional training-only margin on gold complete spans classified as null')
    t.add_argument('--parent-retention-weight',type=float,default=0.0,
                   help='Distill only correct parent G1 spans on original replay examples')
    t.add_argument('--controlled-exposure',action='store_true',
                   help='Deterministic matched replay/pair streams; G1 only, consistency loss off')
    t.add_argument('--device',default='cuda');t.add_argument('--steps',type=int,default=400)
    t.add_argument('--accum',type=int,default=4);t.add_argument('--lr',type=float,default=3e-4)
    t.add_argument('--max-length',type=int,default=2048)
    t.add_argument('--max-span-tokens',type=int,default=DEFAULT_MAX_SPAN_TOKENS)
    t.add_argument('--train-limit',type=int,default=0)
    e=sub.add_parser('evaluate')
    e.add_argument('--dataset',required=True);e.add_argument('--checkpoint',required=True)
    e.add_argument('--output',required=True);e.add_argument('--split',default='dev',choices=('train','dev','challenge'))
    e.add_argument('--limit',type=int,default=0);e.add_argument('--device',default='cuda')
    e.add_argument('--ablation-limit',type=int,default=8)
    e.add_argument('--max-length',type=int,default=2048)
    e.add_argument('--target-stage',choices=STAGES)
    e.add_argument('--final-test',action='store_true')
    e.add_argument('--final-test-certificate')
    g=sub.add_parser('certify')
    g.add_argument('--checkpoint',required=True);g.add_argument('--dev-report',required=True)
    g.add_argument('--challenge-report',required=True);g.add_argument('--output',required=True)
    g.add_argument('--retention-dev',action='append',default=[],metavar='G1=REPORT_PATH')
    g.add_argument('--retention-challenge',action='append',default=[],metavar='G1=REPORT_PATH')
    a=ap.parse_args(argv)
    if a.command=='plan':result=plan()
    elif a.command=='audit-g1-exposure':
        from tools.nanojev_gamer_v1_graph_comparison import audit_exposure
        result=audit_exposure(replay=a.replay,fraction_10=a.fraction_10,
             fraction_25=a.fraction_25,fraction_50=a.fraction_50,output=a.output)
    elif a.command=='audit-g1-missed-spans':
        from tools.nanojev_gamer_v1_graph_comparison import audit_g1_missed_span_objectives
        result=audit_g1_missed_span_objectives(control=a.control,structured=a.structured,
               null_focused=a.null_focused,output=a.output,
               baseline_diagnosis=a.baseline_diagnosis,
               control_diagnosis=a.control_diagnosis,
               structured_diagnosis=a.structured_diagnosis,
               null_focused_diagnosis=a.null_focused_diagnosis)
    elif a.command=='audit-g1-retention':
        from tools.nanojev_gamer_v1_graph_comparison import audit_retention
        result=audit_retention(control=a.control,retained=a.retained,output=a.output)
    elif a.command=='audit-g1-ablations':
        from tools.nanojev_gamer_v1_graph_comparison import audit_ablations
        result=audit_ablations(none=a.none,structured=a.structured,
            consistency=a.consistency,combined=a.combined,output=a.output)
    elif a.command=='compare-g1':
        from tools.nanojev_gamer_v1_graph_comparison import compare
        result=compare(baseline_dataset=a.baseline_dataset,
           candidate_dataset=a.candidate_dataset,
           baseline_checkpoint=a.baseline_checkpoint,
           candidate_checkpoint=a.candidate_checkpoint,output=a.output,
           device=a.device,max_length=a.max_length)
    elif a.command=='frob-g1-frozen-decoder':
        from tools.nanojev_gamer_v1_g1_decoder_frob import run
        result=run(dataset=a.dataset,checkpoint=a.checkpoint,
           baseline_diagnosis=a.baseline_diagnosis,output=a.output,
           device=a.device,max_length=a.max_length,
           expected_head_sha256=a.expected_head_sha256)
    elif a.command=='frob-g1-decoder-transfer':
        from tools.nanojev_gamer_v1_g1_decoder_transfer import run
        result=run(root=a.root,output=a.output,device=a.device,
                   reference_report=a.reference_report,max_length=a.max_length)
    elif a.command=='frob-g1-fresh-dev':
        from tools.nanojev_gamer_v1_g1_fresh_dev import run
        result=run(root=a.root,transfer_report=a.transfer_report,output=a.output,
                   device=a.device,max_length=a.max_length)
    elif a.command=='frob-g1-null-boundary':
        from tools.nanojev_gamer_v1_g1_null_boundary import run
        result=run(root=a.root,transfer_report=a.transfer_report,
                   fresh_run=a.fresh_run,output=a.output,device=a.device,
                   max_length=a.max_length)
    elif a.command=='frob-g1-calibration-validation':
        from tools.nanojev_gamer_v1_g1_calibration_validation import run
        result=run(root=a.root,transfer_report=a.transfer_report,
                   fresh_run=a.fresh_run,null_run=a.null_run,
                   output=a.output,device=a.device,max_length=a.max_length)
    elif a.command=='frob-g1-action-errors':
        from tools.nanojev_gamer_v1_g1_action_errors import run
        result=run(root=a.root,transfer_report=a.transfer_report,
                   fresh_run=a.fresh_run,null_run=a.null_run,
                   validation_run=a.validation_run,output=a.output,
                   device=a.device,max_length=a.max_length)
    elif a.command=='frob-g1-action-ranking':
        from tools.nanojev_gamer_v1_g1_action_ranking import run
        result=run(root=a.root,transfer_report=a.transfer_report,
                   fresh_run=a.fresh_run,null_run=a.null_run,
                   validation_run=a.validation_run,action_run=a.action_run,
                   output=a.output,device=a.device,max_length=a.max_length)
    elif a.command=='frob-g1-action-width-validation':
        from tools.nanojev_gamer_v1_g1_action_width_validation import run
        result=run(root=a.root,transfer_report=a.transfer_report,
                   fresh_run=a.fresh_run,null_run=a.null_run,
                   validation_run=a.validation_run,action_run=a.action_run,
                   ranking_run=a.ranking_run,output=a.output,
                   device=a.device,max_length=a.max_length)
    elif a.command=='diagnose-g1':
        from tools.nanojev_gamer_v1_graph_diagnostic import diagnose
        result=diagnose(dataset=a.dataset,checkpoint=a.checkpoint,output=a.output,
                        device=a.device,limit=a.limit,max_length=a.max_length)
    elif a.command=='train':result=train(dataset=a.dataset,output=a.output,
       source_adapter=a.source_adapter,stage=a.stage,parent_checkpoint=a.parent_checkpoint,
       prerequisite_cert=a.prerequisite_cert,device=a.device,steps=a.steps,accum=a.accum,
       lr=a.lr,max_length=a.max_length,train_limit=a.train_limit,
       max_span_tokens=a.max_span_tokens,refine_checkpoint=a.refine_checkpoint,
       refine_parent_dataset=a.refine_parent_dataset,
       counterfactual_fraction=a.counterfactual_fraction,consistency_weight=a.consistency_weight,
       structured_weight=a.structured_weight,family_balanced=a.family_balanced,
       controlled_exposure=a.controlled_exposure,
       parent_retention_weight=a.parent_retention_weight,hard_null_weight=a.hard_null_weight)
    elif a.command=='evaluate':result=evaluate(dataset=a.dataset,checkpoint=a.checkpoint,
       output=a.output,split=a.split,limit=a.limit,device=a.device,
       ablation_limit=a.ablation_limit,max_length=a.max_length,target_stage=a.target_stage,
       final_test=a.final_test,final_test_certificate=a.final_test_certificate)
    else:
        def keyed(items):
            out={}
            for item in items:
                if '=' not in item:raise ValueError('GAMER_GRAPH_RETENTION_ARGUMENT_FORMAT')
                key,val=item.split('=',1)
                if key not in STAGES or not val:raise ValueError('GAMER_GRAPH_RETENTION_ARGUMENT_FORMAT')
                out[key]=val
            return out
        result=certify(checkpoint=a.checkpoint,dev_report=a.dev_report,
           challenge_report=a.challenge_report,output=a.output,
           retention_dev=keyed(a.retention_dev),
           retention_challenge=keyed(a.retention_challenge))
    print(json_text(result))
    return result

if __name__=='__main__':main()
