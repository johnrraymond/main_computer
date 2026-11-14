#!/usr/bin/env python3
"""HybridLink Head-1: UTF-8 byte -> Qwen input-embedding edit localization.

This is NOT an arithmetic engine. A learned whole-span classifier finds signed-integer digit runs in raw UTF-8
bytes; tokenizer char offsets are converted to byte offsets deterministically.
A diagnostic lexical probe selects candidate Qwen input embedding dimensions
using signed bits of individual literals, without certifying those dimensions. No A+B label or delta enters training.

The edit map is exported but applied only when a separate, future correction
producer is supplied. This experiment never mutates pretrained Qwen weights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

SCHEMA = 'nanojev.hybrid-utf8-lexical-edit-alignment.v3'
ROOT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_hybrid_utf8_alignment_v3'
LITERAL = re.compile(r'(?<![A-Za-z0-9_])[-+]?[0-9]+(?![A-Za-z0-9_])', re.ASCII)
DIGIT_RUN = re.compile(rb'[0-9]+')
BITS = 6  # one sign bit, five magnitude bits, valid for signed radius 16


def spans_from_text(text, radius=16):
    """Oracle annotation ONLY; never used to generate inference edit locations."""
    spans = []
    for match in LITERAL.finditer(text):
        raw = match.group(0)
        value = int(raw)
        if abs(value) > radius:
            raise ValueError('LEX_VALUE_OUTSIDE_RADIUS')
        start = len(text[:match.start()].encode('utf-8'))
        end = len(text[:match.end()].encode('utf-8'))
        spans.append({'begin':start, 'end':end, 'value':value, 'literal':raw})
    if not spans:
        raise ValueError('LEX_NO_LITERALS')
    return spans


def signed_bits(value):
    assert -16 <= value <= 16, 'LEX_BIT_RADIUS'
    magnitude = abs(value)
    return [int(value < 0)] + [(magnitude >> i) & 1 for i in range(4, -1, -1)]


def tokenize_offsets(tokenizer, text):
    """HF fast tokenizer uses character offsets; translate to UTF-8 BYTE offsets.

    Fail closed on slow tokenizers, unaligned special tokens, truncated spans,
    and tokenizer normalization that cannot be mapped to input characters.
    """
    if not getattr(tokenizer, 'is_fast', False):
        raise RuntimeError('LEX_REQUIRES_FAST_TOKENIZER_WITH_OFFSETS')
    record = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids = record['input_ids']
    char_offsets = record['offset_mapping']
    if len(ids) != len(char_offsets) or not ids:
        raise ValueError('LEX_BAD_TOKENIZER_OFFSETS')
    char_to_byte = [0]
    for char in text:
        char_to_byte.append(char_to_byte[-1] + len(char.encode('utf-8')))
    intervals = []
    for start, end in char_offsets:
        if not (0 <= start < end <= len(text)):
            raise ValueError('LEX_TOKEN_OFFSET_AMBIGUOUS')
        intervals.append((char_to_byte[start],char_to_byte[end]))
    return [int(i) for i in ids], intervals


def byte_token_map(text, tokenizer):
    ids, intervals = tokenize_offsets(tokenizer,text)
    data = text.encode('utf-8')
    memberships = []
    for start, end in intervals:
        touches = tuple(range(start,end))
        if not touches or end > len(data):
            raise ValueError('LEX_TOKEN_BYTE_MAP_BROKEN')
        memberships.append(touches)
    return ids, intervals, memberships


def digit_runs(data, spans=None):
    """Propose whole ASCII digit runs; learn reject/bare/signed decisions.

    Candidate generation is deliberately broader than the oracle. For example,
    'v8x' is proposed but must be rejected; in '0+0' the second + is proposed
    as an optional sign but the learned decision must choose BARE, not SIGNED.
    No oracle spans or lexical boundary regex are used at inference.
    """
    runs=[]
    oracle={(sp['begin'],sp['end']) for sp in spans} if spans is not None else None
    for match in DIGIT_RUN.finditer(data):
        begin,end=match.span()
        sign=begin>0 and data[begin-1] in (43,45)
        # A sign following an operand or a closing parenthesis is a binary
        # operator, never part of the next literal (e.g. 0+0, (1)-2).
        unary=bool(sign and (begin<2 or data[begin-2] not in b'0123456789)]}'))
        target=None
        if oracle is not None:
            target=2 if sign and (begin-1,end) in oracle else (1 if (begin,end) in oracle else 0)
        runs.append({'begin':begin,'end':end,'has_sign':sign,'unary_possible':unary,'target':target})
    return runs


def sample(text, tokenizer, radius=16):
    spans = spans_from_text(text, radius)
    data = text.encode('utf-8')
    ids, intervals, memberships = byte_token_map(text,tokenizer)
    labels = [0.] * len(data)
    for s in spans:
        for pos in range(s['begin'],s['end']):
            labels[pos] = 1.
    token_labels = [float(any(labels[b] for b in members)) for members in memberships]
    lexical_tokens = []
    for s in spans:
        touched = [i for i,(a,b) in enumerate(intervals)
                   if b > s['begin'] and a < s['end']]
        if not touched:
            raise ValueError('LEX_SOURCE_SPAN_NOT_TOKENIZED')
        lexical_tokens.append(touched)
    # One token cannot be assigned to TWO separate numerical literals; do not
    # silently pretend token-level editing is more precise than its offsets.
    if len({i for token_indices in lexical_tokens for i in token_indices}) != sum(map(len,lexical_tokens)):
        raise ValueError('LEX_MULTIPLE_LITERALS_IN_SINGLE_TOKEN')
    return {'text':text,'bytes':list(data),'ids':ids,'offsets':intervals,
            'memberships':memberships,'spans':spans,'byte_labels':labels,
            'token_labels':token_labels,'lexical_tokens':lexical_tokens,'runs':digit_runs(data,spans)}


class LexicalAlignmentHead(nn.Module):
    """Locates whole signed UTF-8 spans and selects proposed sparse dimensions.

    A digit-run proposal scanner supplies candidates, not their truth labels.
    The learned span classifier decides reject/bare/signed. The
    trained dimension selector is calibrated by an auxiliary frozen-embedding
    lexical-bit probe. Its coordinates are candidate edit channels, NOT known
    intrinsic 'numeric coordinates' of Qwen.
    """
    def __init__(self, hidden=1024, width=48, sparse_dims=64):
        super().__init__()
        assert hidden >= 8 and 1 <= sparse_dims < hidden
        self.hidden = int(hidden)
        self.sparse_dims = int(sparse_dims)
        self.byte_embedding = nn.Embedding(256,width)
        self.byte_network = nn.Sequential(
            nn.Conv1d(width,width,5,padding=2),nn.GELU(),
            nn.Conv1d(width,width,5,padding=2),nn.GELU(),
            nn.Conv1d(width,1,1))
        # Small random initial differences ensure a stable top-k tie break.
        self.dim_logits = nn.Parameter(-2.6 + torch.linspace(-0.1,0.1,hidden))
        self.probe = nn.Sequential(nn.Linear(hidden,96),nn.GELU(),nn.Linear(96,BITS))
        # Whole-span classifier. Context includes optional sign, previous
        # byte, first/last digit and byte immediately following the run.
        self.span_classifier = nn.Sequential(nn.Linear(width*6+2,width),nn.GELU(),
                                             nn.Linear(width,3))

    def byte_logits(self, source_bytes):
        assert source_bytes.ndim==1 and source_bytes.numel() > 0
        features = self.byte_embedding(source_bytes)[None].transpose(1,2)
        return self.byte_network(features)[0,0]

    def span_logits(self, source_bytes, runs):
        """Predict REJECT, BARE, SIGNED for each complete digit run."""
        assert source_bytes.ndim==1 and source_bytes.numel()>0
        if not runs: return torch.empty(0,3,device=source_bytes.device)
        features=self.byte_network[:-1](self.byte_embedding(source_bytes)[None].transpose(1,2))[0].T
        zero=features.new_zeros(self.byte_embedding.embedding_dim)
        rows=[]
        for run in runs:
            begin,end=run['begin'],run['end']
            previous=features[begin-1] if begin>=1 else zero
            before=features[begin-2] if run['has_sign'] and begin>=2 else (
                features[begin-1] if not run['has_sign'] and begin>=1 else zero)
            after=features[end] if end<features.shape[0] else zero
            # The last 2 explicit indicators disambiguate signed candidates
            # and source boundaries without hard-coding the target class.
            flags=features.new_tensor([float(run['has_sign']),float(begin==0)])
            feat=torch.cat((features[begin],features[end-1],features[begin:end].mean(0),
                            previous,before,after,flags))
            logits=self.span_classifier(feat)
            if not run['unary_possible']:
                logits=torch.cat((logits[:2],logits[2:]*0-1e4))
            rows.append(logits)
        return torch.stack(rows)

    def structured_byte_logits(self, source_bytes, runs, return_decisions=False):
        """Atomic span decoding: cannot emit partial digits or detached signs.

        Candidate proposals depend only on raw bytes, never oracle annotations.
        The neural scores decide whether a candidate is a number and whether
        an adjacent unary sign belongs to it. Return calibrated-like logits
        for the existing byte-to-token map, not raw independent-byte logits.
        """
        logits=self.span_logits(source_bytes,runs)
        decisions=logits.argmax(dim=-1).tolist()
        probs=logits.softmax(-1)
        out=source_bytes.new_full((len(source_bytes),),-15,dtype=torch.float32)
        predictions=[]
        for idx,(run,which) in enumerate(zip(runs,decisions)):
            if which==0:continue
            begin=run['begin']-(1 if which==2 else 0)
            end=run['end']
            # All bits in a selected run share ONE decision and confidence.
            confidence=0.5+0.5*probs[idx,which]
            out[begin:end]=torch.logit(confidence.clamp(0.50001,0.999999))
            predictions.append((begin,end))
        if return_decisions:return out,predictions,decisions
        return out

    @staticmethod
    def token_probabilities(byte_logits, memberships):
        # Aggregation to token offsets is differentiable, including if tokens
        # cover multiple UTF-8 bytes. No oracle span positions are consulted.
        probabilities = byte_logits.sigmoid()
        return torch.stack([probabilities[list(m)].amax() for m in memberships])

    def selected_dimensions(self):
        return torch.topk(self.dim_logits.detach(),self.sparse_dims).indices.sort().values

    def edit_map(self, byte_logits, memberships):
        token_prob = self.token_probabilities(byte_logits,memberships)
        mask = torch.zeros(self.hidden,device=byte_logits.device,dtype=token_prob.dtype)
        mask[self.selected_dimensions()] = 1.
        # No edit at positions that the locator did not classify as numeric.
        token_prob = token_prob * (token_prob >= 0.5).to(token_prob.dtype)
        return token_prob[:,None]*mask[None,:]

    def lexical_logits(self, token_embeddings, spans_as_token_indices):
        assert token_embeddings.ndim==2 and token_embeddings.size(-1)==self.hidden
        soft = self.dim_logits.sigmoid()
        hard = torch.zeros_like(soft)
        hard[self.selected_dimensions()] = 1.
        # Straight-through top-k: the actual probe sees ONLY selected coords,
        # while the selector learns from its decoder's error.
        selected = hard + soft - soft.detach() if self.training else hard
        # Gold spans are only TRAINING/diagnostic labels, never inference edits.
        features = torch.stack([token_embeddings[tokens].mean(0)*selected
                                for tokens in spans_as_token_indices])
        return self.probe(features)


def apply_edit(input_embeddings, edit_map, corrections):
    """Non-mutating future HybridLink edit boundary. No corrections in stage 1."""
    if input_embeddings.shape!=edit_map.shape or input_embeddings.shape!=corrections.shape:
        raise ValueError('LEX_EDIT_SHAPE_MISMATCH')
    if any(x.device != input_embeddings.device for x in (edit_map,corrections)):
        raise ValueError('LEX_EDIT_DEVICE_MISMATCH')
    if not torch.isfinite(corrections).all() or not torch.isfinite(edit_map).all():
        raise ValueError('LEX_EDIT_NONFINITE')
    if (edit_map < 0).any() or (edit_map > 1).any():
        raise ValueError('LEX_EDIT_GATE_RANGE')
    return input_embeddings + (edit_map*corrections).to(input_embeddings.dtype)


def build_data(seed=2311,train_count=480,dev_count=96,test_count=96,radius=16):
    """Disjoint grammatical forms; none has an answer/delta label or sum target.

    Every literal remains in [-16,16]. Multi-literal expressions are used
    ONLY as location problems; no expression is evaluated.
    """
    rng=random.Random(seed)
    forms={
      'train':[
        lambda a,b:f'Compute {a}.',lambda a,b:f'Value: ({a})',
        lambda a,b:f'Question: (({a})).',lambda a,b:f'Compute {a}+({b}).',
        lambda a,b:f'Input ({a}) + ({b})',lambda a,b:f'Compute ({a}+({b}))',
        lambda a,b:f'Read {a} and ({b}).',lambda a,b:f'Pair {a}, {b}.',
        # Train spacing and punctuation independently of the literal value.
        lambda a,b:f'Compute {a} + ({b})',
        lambda a,b:f'Compute {a} + ({b}).',
        lambda a,b:f'Compute ({a}) + ({b})',
        lambda a,b:f'{a} + ({b})',
        lambda a,b:f'Compute {a}+ ({b})',
        lambda a,b:f'Compute {a} +({b})',
        lambda a,b:f'Observe {a} + ({b})',
        lambda a,b:f'Input: {a} + ({b})',
        lambda a,b:f'((({a}))) and ((({b})))',
        lambda a,b:f'Résumé: (({a})) and ((({b})))',
        lambda a,b:f'Compute {a}+{b}',
        lambda a,b:f'Compute {a} + +{abs(b)}',
        lambda a,b:f'Compute {a} + (-{abs(b)})'],
      'dev':[
        lambda a,b:f'Calculate ((({a}))).',
        lambda a,b:f'Café asks: (({a})) + (({b}))',
        lambda a,b:f'What is the literal ({a})?',
        lambda a,b:f'Pairing: ({a}) plus ({b}).',
        lambda a,b:f'Result ({a}) and ((({b})))',
        lambda a,b:f'Read ({a})+({b})'],
      'test':[
        lambda a,b:f'Observe (((( {a} )))).',
        lambda a,b:f'Résumé values: (({a})) and ((({b})))',
        lambda a,b:f'The numerical word is ({a}).',
        lambda a,b:f'Evaluate ((({a}))+(({b}))).',
        lambda a,b:f'Résumé: ((({a}))) and (({b}))',
        lambda a,b:f'Input {a}+({b})'] }
    result={}
    used=set()
    anchors = ['Compute 0.','Value: (0)','Question: ((0)).','Compute 0+(0).',
               'Input (0) + (0)','Compute (0+(0))',
               'Compute 16+(-16).', 'Compute -16+(16).']
    anchors.extend(f'Value: ({n})' for n in range(-radius,radius+1))
    for split,count in [('train',train_count),('dev',dev_count),('test',test_count)]:
        items=[]
        if split=='train':
            for value in anchors[:count]:
                if value not in used:
                    used.add(value);items.append(value)
        attempts=0
        while len(items)<count:
            attempts+=1
            if attempts>count*200:raise RuntimeError('LEX_DATA_EXHAUSTED')
            a=rng.randint(-radius,radius); b=rng.randint(-radius,radius)
            template=rng.choice(forms[split]); text=template(a,b)
            if attempts%3==0:
                decoy=rng.randint(0,radius)
                text+=rng.choice([f' tag_{decoy}x',f' v{decoy}x',f' label_{decoy}',f' code{decoy}x'])
            if text not in used:
                used.add(text);items.append(text)
        result[split]=items
    assert len(used)==sum(len(v) for v in result.values()),'LEX_LEAKED_EXPRESSION'
    return result


def losses(model,row,embeddings,device,bit_weight=0.10,sparsity_weight=0.10):
    raw=torch.tensor(row['bytes'],device=device,dtype=torch.long)
    output=model.byte_logits(raw)
    byte_target=torch.tensor(row['byte_labels'],device=device)
    token_prob=model.token_probabilities(output,row['memberships'])
    token_target=torch.tensor(row['token_labels'],device=device)
    # Numeric source bytes are sparse. Weight positives so an omitted literal
    # cannot be hidden by correctly classifying hundreds of prose bytes.
    positive=byte_target.sum().clamp_min(1)
    negative=(byte_target.numel()-byte_target.sum()).clamp_min(1)
    b=F.binary_cross_entropy_with_logits(output,byte_target,
          pos_weight=(negative/positive).clamp(1.0,8.0))
    weights=1.0+3.0*token_target
    t=(F.binary_cross_entropy(token_prob.clamp(1e-6,1-1e-6),token_target,
          reduction='none')*weights).sum()/weights.sum()
    span_logits=model.span_logits(raw,row['runs'])
    span_targets=torch.tensor([r['target'] for r in row['runs']],device=device,dtype=torch.long)
    span_loss=F.cross_entropy(span_logits,span_targets)
    bit_target=torch.tensor([signed_bits(s['value']) for s in row['spans']],device=device,dtype=torch.float32)
    bit=F.binary_cross_entropy_with_logits(model.lexical_logits(embeddings,row['lexical_tokens']),bit_target)
    excess=F.relu(model.dim_logits.sigmoid().sum()/model.hidden - model.sparse_dims/model.hidden)
    total=span_loss + 0.10*b + 0.10*t + bit_weight*bit + sparsity_weight*excess
    return total,{'span_ce':span_loss.detach(),'byte_ce':b.detach(),'token_ce':t.detach(), 'bit_ce':bit.detach(),
                  'soft_dimension_count':model.dim_logits.sigmoid().sum().detach()}


def judge(model,rows,tokenizer,embedding,device):
    model.eval()
    correct_bytes=total_bytes=correct_tokens=total_tokens=correct_bits=total_bits=0
    positive_bytes=found_positive_bytes=0
    literal_count=literal_complete=0
    exact_examples=0
    overlap=union=0
    edit_outside=0.
    span_ce_sum=0.
    with torch.no_grad():
        for raw in rows:
            row=sample(raw,tokenizer)
            source=torch.tensor(row['bytes'],device=device)
            logits=model.structured_byte_logits(source,row['runs'])
            span_ce_sum+=float(F.cross_entropy(model.span_logits(source,row['runs']),
                torch.tensor([r['target'] for r in row['runs']],device=device,dtype=torch.long)).item())
            pb=(logits.sigmoid()>=0.5).int().tolist()
            truth=[int(n) for n in row['byte_labels']]
            correct_bytes+=sum(a==b for a,b in zip(pb,truth));total_bytes+=len(truth)
            positive_bytes+=sum(truth)
            found_positive_bytes+=sum(a==b==1 for a,b in zip(pb,truth))
            literal_count+=len(row['spans'])
            literal_complete+=sum(all(pb[i]==1 for i in range(s['begin'],s['end']))
                                  for s in row['spans'])
            exact_examples+=int(pb==truth)
            tp=(model.token_probabilities(logits,row['memberships'])>=0.5).int().tolist()
            gold=[int(n) for n in row['token_labels']]
            correct_tokens+=sum(a==b for a,b in zip(tp,gold));total_tokens+=len(gold)
            overlap+=sum(a==1 and b==1 for a,b in zip(tp,gold))
            union+=sum(a==1 or b==1 for a,b in zip(tp,gold))
            e=embedding(torch.tensor(row['ids'],device=device)).detach().float()
            bit_logits=model.lexical_logits(e,row['lexical_tokens'])
            guessed=(bit_logits.sigmoid()>=0.5).int().tolist()
            targets=[signed_bits(s['value']) for s in row['spans']]
            correct_bits+=sum(a==b for a,b in zip(guessed,targets));total_bits+=len(targets)
            edit_map=model.edit_map(logits,row['memberships'])
            # Predicted edit activation outside oracle numeric TOKEN positions.
            non=[i for i,good in enumerate(gold) if good==0]
            if non:edit_outside+=float(edit_map[non].amax().detach().cpu())
    return {'span_ce':round(span_ce_sum/max(1,len(rows)),7),
       'byte_accuracy':correct_bytes/max(1,total_bytes),
       'numeric_byte_recall':found_positive_bytes/max(1,positive_bytes),
       'literal_complete_recall':literal_complete/max(1,literal_count),
       'example_exact_fraction':exact_examples/max(1,len(rows)),
       'token_accuracy':correct_tokens/max(1,total_tokens),
       'token_iou':overlap/max(1,union),
       'lexical_bitfield_exact':correct_bits/max(1,total_bits),
       'false_edit_peak_sum':round(edit_outside,5),
       'bytes':total_bytes,'tokens':total_tokens,'literals':total_bits}


def load_embeddings(device='cuda',model_id=None,allow_download=False,loader=None):
    if loader is None:
        from tools.nanojev_qwen_hybridagent import load_qwen,MODEL_ID,MODEL_REVISION
        base=MODEL_ID if model_id is None else model_id
        model,tokenizer,details=load_qwen(model_id=base,device=device,
            precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'float32',
            allow_download=allow_download)
        revision=details.get('revision',MODEL_REVISION)
    else:
        model,tokenizer,details=loader(device=device)
        revision=details.get('revision','toy-only')
    if not getattr(tokenizer,'is_fast',False):
        raise RuntimeError('LEX_QWEN_TOKENIZER_NOT_FAST')
    embedding=model.backbone.get_input_embeddings() if hasattr(model,'backbone') else model.get_input_embeddings()
    assert embedding.weight.device.type==device,'LEX_MODEL_DEVICE_MISMATCH'
    embedding.requires_grad_(False)
    assert not any(p.requires_grad for p in embedding.parameters()),'LEX_BACKBONE_NOT_FROZEN'
    return tokenizer,embedding, {'revision':revision,'hidden':embedding.weight.shape[1],
                                  'tokenizer_size':len(tokenizer)}


def _config(*,device,seed,train_count,dev_count,test_count,steps,eval_every,width,sparse_dims,
            head_lr,model_id,identity):
    return {'schema':SCHEMA,'seed':seed,'counts':{'train':train_count,'dev':dev_count,'test':test_count},
            'steps':steps,'eval_every':eval_every,'width':width,'sparse_dims':sparse_dims,
            'head_lr':head_lr,'device':device,'model_id':model_id or 'Qwen/Qwen3-0.6B-Base',
            'identity':identity}


def _state(model):
    return {k:v.detach().cpu().clone() for k,v in model.state_dict().items()}


def _atomic_json(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf8')
    temp.replace(path)


def train(*,device='cuda',output=ROOT,model_id=None,allow_download=False,
          seed=2311,train_count=480,dev_count=96,test_count=96,steps=600,eval_every=50,
          width=48,sparse_dims=64,head_lr=0.002,resume=False,stop_after=None,loader=None):
    assert device in ('cuda','cpu') and steps>0 and eval_every>0,'LEX_BAD_TRAIN_CONFIG'
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('LEX_CUDA_UNAVAILABLE')
    output=Path(output)
    if output.exists() and not resume and any(output.iterdir()):
        raise RuntimeError('LEX_OUTPUT_EXISTS_USE_RESUME_OR_NEW_OUTPUT')
    tokenizer,embedding,identity=load_embeddings(device,model_id,allow_download,loader)
    if sparse_dims>=identity['hidden']:raise ValueError('LEX_SPARSITY_MUST_BE_LESS_THAN_HIDDEN')
    if train_count<6 or dev_count<6 or test_count<6:raise ValueError('LEX_MINIMUM_SPLIT_SIZE')
    cfg=_config(device=device,seed=seed,train_count=train_count,dev_count=dev_count,
      test_count=test_count,steps=steps,eval_every=eval_every,width=width,sparse_dims=sparse_dims,
      head_lr=head_lr,model_id=model_id,identity=identity)
    data=build_data(seed,train_count,dev_count,test_count)
    prepared={part:[sample(text,tokenizer) for text in data[part]] for part in ('train','dev')}
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed)
    model=LexicalAlignmentHead(identity['hidden'],width,sparse_dims).to(device)
    optim=torch.optim.AdamW(model.parameters(),lr=head_lr,weight_decay=0.)
    output.mkdir(parents=True,exist_ok=True)
    cfg_file=output/'config.json'
    if resume:
        if (output/'gate_result.json').exists():raise RuntimeError('LEX_RUN_ALREADY_GATED')
        if json.loads(cfg_file.read_text(encoding='utf8'))!=cfg:
            raise RuntimeError('LEX_RESUME_CONFIG_DRIFT')
        cp=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
        if cp['schema']!=SCHEMA or cp['step']>=steps:
            raise RuntimeError('LEX_BAD_RESUME_CHECKPOINT')
        model.load_state_dict(cp['head']);optim.load_state_dict(cp['optimizer'])
        start=cp['step']+1;best=cp['best'];
        torch.set_rng_state(cp['rng'])
        if device=='cuda' and cp['cuda_rng'] is not None:torch.cuda.set_rng_state_all(cp['cuda_rng'])
    else:
        _atomic_json(cfg_file,cfg)
        torch.manual_seed(seed)
        if device=='cuda':torch.cuda.manual_seed_all(seed)
        start=1;best=-1.
    # Frozen real Qwen INPUT embedding lookup; no decoder forward or LoRA.
    trace=[]
    for step in range(start,steps+1):
        model.train(); row=prepared['train'][(step-1)%len(prepared['train'])]
        optim.zero_grad(set_to_none=True)
        with torch.no_grad():
            e=embedding(torch.tensor(row['ids'],device=device)).detach().float()
        total,parts=losses(model,row,e,device)
        total.backward()
        if step==start:
            assert model.span_classifier[0].weight.grad is not None and model.span_classifier[0].weight.grad.abs().sum()>0,'LEX_NO_SPAN_GRADIENT'
            assert model.dim_logits.grad is not None and model.dim_logits.grad.abs().sum()>0,'LEX_NO_DIM_GRADIENT'
            assert all(p.grad is None for p in embedding.parameters()),'LEX_FROZEN_EMBEDDING_UPDATED'
        torch.nn.utils.clip_grad_norm_(model.parameters(),5.0)
        optim.step()
        if step==start or step%eval_every==0 or step==steps:
            report=judge(model,data['dev'],tokenizer,embedding,device)
            # Stage 1 is ONLY source-to-input alignment. The weak frozen-Qwen
            # bitfield probe is a diagnostic, never a gate or checkpoint target.
            score=(10*report['example_exact_fraction']+2*report['token_iou']
                   +report['literal_complete_recall']+report['numeric_byte_recall']
                   +0.001/(1+report['span_ce']))
            if score>best:
                best=score
                torch.save({'schema':SCHEMA,'head':_state(model),'step':step,'objective':score},output/'best.pt')
            event={'step':step,'total':steps,'loss':round(float(total.detach()),5),
              'span_ce':round(float(parts['span_ce']),5),
              'byte_ce':round(float(parts['byte_ce']),5),'token_ce':round(float(parts['token_ce']),5),
              'bit_ce':round(float(parts['bit_ce']),5),'selected_dimensions':sparse_dims,
              'dev':report,'best_objective':best}
            if device=='cuda':event['cuda_peak_mib']=round(torch.cuda.max_memory_allocated()/1048576,1)
            print('LEX_ALIGN_STEP='+json.dumps(event,sort_keys=True),flush=True)
            trace.append(event)
        torch.save({'schema':SCHEMA,'step':step,'head':_state(model),'optimizer':optim.state_dict(),
                    'best':best,'rng':torch.get_rng_state(),
                    'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None},output/'last.pt')
        if stop_after and step>=stop_after:break
    complete=step==steps
    result={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED' if complete else 'PAUSED',
            'step':step,'steps':steps,'best_objective':best,'output':str(output),
            'base_embeddings_frozen':True,'arithmetic_targets_used':False,
            'final_test_read':False}
    _atomic_json(output/'training_result.json',result)
    print('LEX_ALIGN_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def gate(*,device='cuda',output=ROOT,model_id=None,allow_download=False,loader=None):
    output=Path(output)
    cfg=json.loads((output/'config.json').read_text(encoding='utf8'))
    result=json.loads((output/'training_result.json').read_text(encoding='utf8'))
    if cfg['schema']!=SCHEMA or result['status']!='TRAINED_UNCERTIFIED':
        raise RuntimeError('LEX_GATE_REQUIRES_COMPLETE_TRAINING')
    if (output/'gate_result.json').exists():
        raise RuntimeError('LEX_GATE_ALREADY_USED_USE_NEW_LINEAGE')
    tokenizer,embedding,identity=load_embeddings(device,model_id,allow_download,loader)
    if cfg['identity']!=identity or cfg['device']!=device:
        raise RuntimeError('LEX_GATE_MODEL_IDENTITY_DRIFT')
    head=LexicalAlignmentHead(identity['hidden'],cfg['width'],cfg['sparse_dims']).to(device)
    state=torch.load(output/'best.pt',map_location='cpu',weights_only=True)
    assert state['schema']==SCHEMA,'LEX_BEST_BAD_SCHEMA'
    head.load_state_dict(state['head'])
    items=build_data(
       cfg['seed'],cfg['counts']['train'],cfg['counts']['dev'],cfg['counts']['test'])
    report=judge(head,items['test'],tokenizer,embedding,device)
    # Independent inspection canaries test whitespace/parenthesis variants
    # absent from the narrow v1 grammar. They are NOT arithmetic targets.
    canaries=['Compute 8 + (-8)', 'Compute 7 + (-7)', 'Compute -8 + (8)',
              'Value: (0)', '((0+0))', 'Compute (16) + (-16)',
              'Résumé: ((-16)) and (((+15)))']
    canary_results=[]
    head.eval()
    with torch.no_grad():
        for text in canaries:
            row=sample(text,tokenizer)
            flags=(head.structured_byte_logits(torch.tensor(row['bytes'],device=device),row['runs']).sigmoid()>=0.5).tolist()
            expected=[bool(x) for x in row['byte_labels']]
            canary_results.append({'text':text,'exact':flags==expected,
                      'missed_bytes':[i for i,(a,b) in enumerate(zip(flags,expected)) if b and not a],
                      'false_bytes':[i for i,(a,b) in enumerate(zip(flags,expected)) if a and not b]})
    allowed=bool(report['byte_accuracy']>=0.98 and report['token_iou']>=0.95
                 and report['numeric_byte_recall']>=0.99
                 and report['literal_complete_recall']>=0.98
                 and report['example_exact_fraction']>=0.98
                 and all(row['exact'] for row in canary_results))
    out={'schema':SCHEMA,'status':'ALIGNMENT_PASS' if allowed else 'ALIGNMENT_FAIL',
         'gate_pass':allowed,'criterion':{'byte_accuracy':0.98,'token_iou':0.95,
              'numeric_byte_recall':0.99,'literal_complete_recall':0.98,
              'example_exact_fraction':0.98,'canaries_exact':True},
         'canaries':canary_results,
         'metrics':report,'trained_checkpoint_step':state['step'],
         'selected_dimensions':head.selected_dimensions().detach().cpu().tolist(),
         'numerical_correction_trained':False,'arithmetic_trained':False,
         'bitfield_decoder_certified':False,'dimension_subspace_certified':False,
         'backbone_base_mutations':0}
    _atomic_json(output/'gate_result.json',out)
    print('LEX_ALIGN_GATE='+json.dumps(out,sort_keys=True),flush=True)
    return out


def inspect(*,device='cuda',output=ROOT,model_id=None,allow_download=False,
            text='Compute 8 + (-8)',loader=None):
    tok,embedding,identity=load_embeddings(device,model_id,allow_download,loader)
    cp=torch.load(Path(output)/'best.pt',map_location='cpu',weights_only=True)
    cfg=json.loads((Path(output)/'config.json').read_text(encoding='utf8'))
    head=LexicalAlignmentHead(identity['hidden'],cfg['width'],cfg['sparse_dims']).to(device)
    head.load_state_dict(cp['head']);head.eval()
    # No oracle spans used for the predicted edit map or positions.
    ids,intervals,membership=byte_token_map(text,tok)
    with torch.no_grad():
        logits,spans,classes=head.structured_byte_logits(
            torch.tensor(list(text.encode('utf-8')),device=device),digit_runs(text.encode('utf-8')),True)
        edit=head.edit_map(logits,membership)
        byte_flags=(logits.sigmoid()>=0.5).int().tolist()
    # Oracle labels appear ONLY in inspection diagnostics, not the
    # model-produced edit map. A missed operand is explicitly visible.
    diagnostic=sample(text,tok)
    oracle=[bool(x) for x in diagnostic['byte_labels']]
    answer={'schema':SCHEMA,'text':text,'utf8_hex':text.encode('utf-8').hex(),
            'byte_probabilities':[round(float(x),4) for x in logits.sigmoid().tolist()],
            'predicted_numeric_bytes':[i for i,x in enumerate(byte_flags) if x],
            'predicted_spans':spans,'candidate_classes':classes,
            'expected_numeric_bytes':[i for i,x in enumerate(oracle) if x],
            'missed_numeric_bytes':[i for i,x in enumerate(oracle) if x and not byte_flags[i]],
            'false_numeric_bytes':[i for i,x in enumerate(oracle) if not x and byte_flags[i]],
            'literal_spans_complete':[all(byte_flags[i] for i in range(sp['begin'],sp['end']))
                                     for sp in diagnostic['spans']],
            'alignment_exact':byte_flags==oracle,
            'token_byte_offsets':intervals,'edit_token_positions':[
                i for i,x in enumerate(edit.amax(dim=-1).tolist()) if x>=0.5],
            'dimension_indices':head.selected_dimensions().detach().cpu().tolist(),
            'edit_map_shape':list(edit.shape),'edits_applied':False}
    print('LEX_ALIGN_INSPECT='+json.dumps(answer,ensure_ascii=False,sort_keys=True),flush=True)
    return answer


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('train','gate','inspect','all'),default='all')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--model',default=None)
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--steps',type=int,default=600)
    p.add_argument('--eval-every',type=int,default=50)
    p.add_argument('--seed',type=int,default=2311)
    p.add_argument('--train-count',type=int,default=480)
    p.add_argument('--dev-count',type=int,default=96)
    p.add_argument('--test-count',type=int,default=96)
    p.add_argument('--sparse-dims',type=int,default=64)
    p.add_argument('--width',type=int,default=48)
    p.add_argument('--head-lr',type=float,default=0.002)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int,default=None)
    p.add_argument('--text',default='Compute 8 + (-8)')
    args=p.parse_args(argv)
    common={'device':args.device,'output':args.output,'model_id':args.model,
            'allow_download':args.allow_model_download}
    if args.stage in ('train','all'):
        result=train(**common,seed=args.seed,train_count=args.train_count,
           dev_count=args.dev_count,test_count=args.test_count,steps=args.steps,
           eval_every=args.eval_every,sparse_dims=args.sparse_dims,width=args.width,
           head_lr=args.head_lr,resume=args.resume,stop_after=args.stop_after)
        if result['status']!='TRAINED_UNCERTIFIED':return 0
    if args.stage in ('gate','all'):gate(**common)
    if args.stage=='inspect':inspect(**common,text=args.text)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
