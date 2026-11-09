#!/usr/bin/env python3
"""Train/evaluate a direct GAMER decision head on frozen Qwen/LoRA activations.

There is NO language-completion target, vocabulary head, token generation,
JSON-output training, lexical inference, or predetermined game identity.
Training-only labels annotate player-action mentions and numerical winners.
This is a bounded direct action-selection learner; not yet certified for
arbitrary game discovery, missing rules, or unmentioned actions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from tools import nanojev_gamer_v1_backbone_first as core
from tools.nanojev_gamer_v1_backbone_first_head import GamerDirectHead, direct_loss


def load_dataset(path):
    path=Path(path)
    if not path.is_file():raise FileNotFoundError('GAMER_DATASET_MISSING')
    rows=[];seen=set()
    for number,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
        if not line.strip():continue
        try:row=json.loads(line)
        except ValueError as exc:raise ValueError(f'GAMER_DATASET_INVALID_JSON_LINE_{number}') from exc
        if not isinstance(row,dict) or not {'prompt','state','split'}<=set(row):
            raise ValueError('GAMER_DATASET_MISSING_FIELDS')
        if row['split'] not in ('train','dev','test'):
            raise ValueError('GAMER_DATASET_INVALID_SPLIT')
        core.prompt_messages(row['prompt'])
        core.validate_state(row['state'])
        if not isinstance(row.get('role_spans'),list) or not row['role_spans']:
            raise ValueError('GAMER_DIRECT_ROLE_ANNOTATIONS_REQUIRED_REGENERATE_CORPUS')
        digest=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()
        if digest in seen:raise ValueError('GAMER_DATASET_DUPLICATE_PROMPT')
        seen.add(digest)
        rows.append(row)
    if any(not any(r['split']==split for r in rows) for split in ('train','dev','test')):
        raise ValueError('GAMER_DATASET_REQUIRES_THREE_SPLITS')
    return rows


def _span_token_ids(prepared, start, end, eligible):
    """Training-label alignment, not a runtime classifier or tokenizer oracle."""
    base=prepared['prompt_start']
    return [i for i,(a,b) in enumerate(prepared['offsets'])
            if eligible[i] and a < base+end and b > base+start]


def supervision(row,prepared):
    """Supervised token-level role labels from annotated TRAINING corpus.

    No lexical or regex role guesses. The corpus has exact authoring offsets;
    these are never passed to inference. The same strategy name may be tagged
    player in one clause and opponent in another.
    """
    import torch
    from tools.nanojev_gamer_v1_backbone_first_head import (
        PLAYER_BEGIN, PLAYER_INSIDE, OPP_BEGIN, OPP_INSIDE)
    oracle=core.execute_state(row['state'])
    if oracle['status']!='evaluated':
        raise ValueError('GAMER_DIRECT_UNSUPPORTED_ABSTENTION_TRAINING_ROW')
    names={a['name'] for a in row['state']['actions']}
    tags=[0]*len(prepared['offsets'])
    eligible=prepared['eligible'].tolist()
    starts={name:[] for name in names}
    opponents=0
    for span in row['role_spans']:
        if not isinstance(span,dict) or set(span)!= {'role','name','start','end'}:
            raise ValueError('GAMER_DIRECT_INVALID_ROLE_ANNOTATION')
        role,name,start,end=(span[k] for k in ('role','name','start','end'))
        if (role not in ('player','opponent') or not isinstance(name,str) or not name or
            not isinstance(start,int) or isinstance(start,bool) or
            not isinstance(end,int) or isinstance(end,bool) or
            start<0 or end>len(row['prompt']) or start>=end or
            row['prompt'][start:end]!=name):
            raise ValueError('GAMER_DIRECT_ROLE_OFFSET_MISMATCH')
        if role=='player' and name not in names:
            raise ValueError('GAMER_DIRECT_PLAYER_ANNOTATION_NOT_IN_STATE')
        ids=_span_token_ids(prepared,start,end,eligible)
        if not ids or any(tags[i]!=0 for i in ids):
            # A tokenizer may merge two role-bearing characters into the same
            # token; never fabricate labels for an ambiguous token.
            continue
        begin,inside=(PLAYER_BEGIN,PLAYER_INSIDE) if role=='player' else (OPP_BEGIN,OPP_INSIDE)
        tags[ids[0]]=begin
        for i in ids[1:]:tags[i]=inside
        if role=='player':starts[name].append(ids[0])
        else:opponents+=1
    if not all(starts.values()):
        missing=sorted(name for name,indices in starts.items() if not indices)
        raise ValueError('GAMER_DIRECT_PLAYER_ACTION_NOT_TOKEN_ALIGNED:'+repr(missing))
    if opponents==0:
        raise ValueError('GAMER_DIRECT_NO_OPPONENT_ROLE_SUPERVISION')
    return {'tags':torch.tensor(tags,dtype=torch.long),
            'action_starts':{name:torch.tensor(v,dtype=torch.long) for name,v in starts.items()},
            'winners':set(oracle['decisions']),
            'opponent_mentions':opponents}


def _frozen(model_id,adapter,device):
    model,tok=core.model_components(model_id,adapter,device)
    if any(p.requires_grad for p in model.parameters()):
        raise RuntimeError('GAMER_SOURCE_WEIGHTS_NOT_FROZEN')
    return model,tok


def _capture(model,tok,row,device,max_length):
    prepared=core.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
    hidden=core.frozen_context(model,prepared,device=device)
    labels=supervision(row,prepared)
    return {'hidden':hidden.detach().to(device='cpu',dtype=__import__('torch').float16).contiguous(),
            'prepared':prepared,'labels':labels}


def _head_loss(head,item,device):
    hidden=item['hidden'].to(device=device,dtype=__import__('torch').float32)
    labels=item['labels']
    tags,preferences=head(hidden)
    return direct_loss(tags,preferences,labels['tags'].to(device),
            item['prepared']['eligible'].to(device),
            {k:v.to(device) for k,v in labels['action_starts'].items()},
            labels['winners'])


def train(*,input,output,source_adapter,model_id='Qwen/Qwen3-0.6B',
          device='cuda',steps=2000,lr=3e-4,accum=8,max_length=2048,
          width=192,layers=1,seed=47,capture_limit_mib=3072,train_limit=0):
    import torch
    from safetensors.torch import save_file
    input=Path(input);output=Path(output);source_adapter=Path(source_adapter)
    if output.exists():raise FileExistsError('GAMER_DIRECT_OUTPUT_EXISTS')
    if output.resolve()==source_adapter.resolve() or source_adapter.resolve() in output.resolve().parents:
        raise ValueError('GAMER_DIRECT_SOURCE_ADAPTER_OUTPUT_FORBIDDEN')
    if min(steps,accum,max_length,width,layers,capture_limit_mib)<=0:
        raise ValueError('GAMER_DIRECT_INVALID_SETTINGS')
    random.seed(seed);torch.manual_seed(seed)
    rows=load_dataset(input)
    train_rows=[r for r in rows if r['split']=='train']
    full_train_samples=len(train_rows)
    if train_limit>0 and train_limit<len(train_rows):
        # Balanced-over-order sample rather than taking only the first corpus
        # family. Used for cheap architectural smoke tests, never promotion.
        selected=sorted(random.Random(seed).sample(range(len(train_rows)),train_limit))
        train_rows=[train_rows[i] for i in selected]
    before=core.adapter_fingerprint(source_adapter)
    model,tok=_frozen(model_id,source_adapter,device)
    frozen=[];total_bytes=0
    for i,row in enumerate(train_rows,1):
        item=_capture(model,tok,row,device,max_length)
        total_bytes+=item['hidden'].numel()*item['hidden'].element_size()
        if total_bytes>capture_limit_mib*1024*1024:
            raise MemoryError('GAMER_CAPTURE_LIMIT_EXCEEDED_USE_SMALLER_TRAIN_SET')
        frozen.append(item)
        if i==1 or i%50==0 or i==len(train_rows):
            print(f'GAMER_DIRECT_CAPTURE {i}/{len(train_rows)} frozen_qwen_once_per_prompt '
                  f'cache_mib={total_bytes/(1024*1024):.1f}',flush=True)
    hidden_size=frozen[0]['hidden'].shape[-1]
    head=GamerDirectHead(hidden_size,width=width,layers=layers).to(device)
    optimizer=torch.optim.AdamW(head.parameters(),lr=lr,weight_decay=0.01)
    losses=[];head.train()
    for step in range(1,steps+1):
        optimizer.zero_grad(set_to_none=True)
        rolling=Counter()
        for _ in range(accum):
            item=frozen[random.randrange(len(frozen))]
            loss,components=_head_loss(head,item,device)
            (loss/accum).backward()
            rolling['total']+=float(loss.detach())
            for key,value in components.items():rolling[key]+=value
        torch.nn.utils.clip_grad_norm_(head.parameters(),1.0)
        optimizer.step()
        loss_value=rolling['total']/accum
        losses.append(loss_value)
        if step==1 or step%100==0 or step==steps:
            print(f'GAMER_DIRECT_HEAD_TRAIN step={step}/{steps} loss={loss_value:.6f} '
                  f'choice={rolling["choice_loss"]/accum:.5f} '
                  f'span={rolling["tag_loss"]/accum:.5f}',flush=True)
    if core.adapter_fingerprint(source_adapter)!=before:
        raise RuntimeError('GAMER_SOURCE_ADAPTER_CHANGED')
    if any(p.grad is not None for p in model.parameters()):
        raise RuntimeError('GAMER_FROZEN_BACKBONE_RECEIVED_GRADIENTS')
    output.mkdir(parents=True,exist_ok=False)
    path=output/'gamer_head.safetensors'
    save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},str(path))
    manifest={
        'schema':core.SCHEMA,'status':'HEAD_TRAINED_NOT_CERTIFIED',
        'model_id':model_id,'source_adapter':str(source_adapter.resolve()),
        'source_adapter_sha256':before,'head_sha256':core.sha256(path),
        'head':{'type':'contextual-player-opponent-role-and-coupled-preference',
                'hidden_size':hidden_size,'width':width,'layers':layers},
        'backbone_frozen':True,'lora_frozen':True,
        'trainable_parameters':'DIRECT_GAMER_HEAD_ONLY',
        'training_target':'PLAYER_OPPONENT_ROLE_SPANS_AND_PREDICTED_START_COUPLED_DECISION',
        'train_samples':len(train_rows),'full_train_samples':full_train_samples,
        'train_subset_smoke':bool(train_limit and train_limit<full_train_samples),
        'dev_samples':sum(r['split']=='dev' for r in rows),
        'test_samples':sum(r['split']=='test' for r in rows),
        'dataset_sha256':core.sha256(input),'steps':steps,'accum':accum,
        'lr':lr,'seed':seed,'max_length':max_length,
        'final_train_loss':losses[-1], 'generated_tokens':0,
        'no_inference_game_oracle':True,'no_inference_lexical_parser':True,
        'supervision':'TRAINING_ONLY_EXPLICIT_CHARACTER_ROLE_SPANS',
        'production_promotion':False,'evaluation_status':'UNTESTED',
        'scope':'Named player actions present in the prompt; no general-game certificate',
    }
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return manifest


def evaluate(*,input,checkpoint,output,split='dev',device='cuda',limit=0,
             max_input_tokens=2048,ablation_limit=8):
    import torch
    input=Path(input);checkpoint=Path(checkpoint);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_IMMUTABLE_EVALUATION_EXISTS')
    if split not in ('dev','test'):raise ValueError('GAMER_DIRECT_INVALID_EVALUATION_SPLIT')
    rows=load_dataset(input)
    manifest=json.loads((checkpoint/'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema')!=core.SCHEMA or
        manifest.get('status')!='HEAD_TRAINED_NOT_CERTIFIED' or
        manifest.get('dataset_sha256')!=core.sha256(input) or
        manifest.get('backbone_frozen') is not True or
        manifest.get('lora_frozen') is not True):
        raise ValueError('GAMER_DIRECT_EVAL_CHECKPOINT_OR_DATASET_LINEAGE_MISMATCH')
    if core.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('GAMER_SOURCE_ADAPTER_LINEAGE_MISMATCH')
    model,tok=_frozen(manifest['model_id'],manifest['source_adapter'],device)
    head=core.load_head_from_checkpoint(checkpoint,manifest,device=device)
    deck=[r for r in rows if r['split']==split]
    if limit>0:deck=deck[:limit]
    if not deck:raise ValueError('GAMER_DIRECT_EVAL_EMPTY')
    counts=Counter();by_family={};details=[];ablation=Counter()
    for index,row in enumerate(deck,1):
        oracle=core.execute_state(row['state'])
        expected={a['name'] for a in row['state']['actions']}
        winners=set(oracle['decisions']) if oracle['status']=='evaluated' else set()
        prepared=core.prepare_prompt(tok,row['prompt'],max_input_tokens=max_input_tokens)
        hidden=core.frozen_context(model,prepared,device=device)
        decision=core.direct_decide(head,hidden,prepared)
        actual={a['name'] for a in decision['neural_actions']}
        chosen=decision['decisions']
        exact=(chosen[0] in winners) if chosen else False
        names_exact=(actual==expected)
        counts['cases']+=1
        counts['decision_correct']+=int(exact)
        counts['action_set_exact']+=int(names_exact)
        counts['no_neural_action_spans']+=int(not chosen)
        counts['tie_cases']+=int(len(winners)!=1)
        fam=str(row.get('family','unspecified'))
        group=by_family.setdefault(fam,{'cases':0,'decision_correct':0,'action_set_exact':0})
        group['cases']+=1;group['decision_correct']+=int(exact)
        group['action_set_exact']+=int(names_exact)
        case={'index':index,'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
              'expected_actions':sorted(expected),'predicted_actions':sorted(actual),
              'expected_decisions':sorted(winners),'predicted_decisions':chosen,
              'missing_player_actions':sorted(expected-actual),
              'extra_neural_actions':sorted(actual-expected),
              'decision_correct':exact,'action_set_exact':names_exact,
              'generated_tokens':0,'head_status':decision['status']}
        if index<=ablation_limit:
            # This is merely a representation dependence test, NOT a language
            # comprehension certificate. Do not treat trivial ablation as proof.
            null_hidden=torch.zeros_like(hidden)
            neutral=core.direct_decide(head,null_hidden,prepared)
            null_correct=bool(neutral['decisions'] and neutral['decisions'][0] in winners)
            ablation['cases']+=1
            ablation['zero_hidden_correct']+=int(null_correct)
            ablation['head_correct']+=int(exact)
            case['zero_hidden_decisions']=neutral['decisions']
        details.append(case)
        print(f'GAMER_DIRECT_HEAD_EVAL {index}/{len(deck)} '
              f'actions={counts["action_set_exact"]} decisions={counts["decision_correct"]} '
              f'generated_tokens=0',flush=True)
    report={'schema':core.SCHEMA,'status':'MEASURED_NOT_CERTIFIED',
            'split':split,'cases':len(deck),'counts':dict(counts),
            'decision_accuracy':counts['decision_correct']/len(deck),
            'action_set_accuracy':counts['action_set_exact']/len(deck),
            'by_family':by_family,'zero_hidden_ablation':{**dict(ablation),
                'caution':'Dependence on Qwen vectors does not establish semantic generality'},
            'game_state_exact_not_measured':True,
            'direct_neural_decision_not_math_state':True,
            'no_generated_tokens':True,'no_production_promotion':True,
            'blind_eval_reuse_warning':'Repeated evaluations are diagnostics, not independent test evidence.',
            'checkpoint_head_sha256':manifest['head_sha256'],
            'cases_detail':details}
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('train','evaluate'))
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--source-adapter',type=Path)
    ap.add_argument('--checkpoint',type=Path)
    ap.add_argument('--model-id',default='Qwen/Qwen3-0.6B')
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--steps',type=int,default=2000)
    ap.add_argument('--lr',type=float,default=3e-4)
    ap.add_argument('--accum',type=int,default=8)
    ap.add_argument('--max-length',type=int,default=2048)
    ap.add_argument('--width',type=int,default=192)
    ap.add_argument('--layers',type=int,default=1)
    ap.add_argument('--capture-limit-mib',type=int,default=3072)
    ap.add_argument('--train-limit',type=int,default=0)
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--split',choices=('dev','test'),default='dev')
    ap.add_argument('--limit',type=int,default=0)
    ap.add_argument('--max-input-tokens',type=int,default=2048)
    ap.add_argument('--ablation-limit',type=int,default=8)
    args=ap.parse_args(argv)
    if args.command=='train':
        if args.source_adapter is None:ap.error('train requires --source-adapter')
        result=train(input=args.input,output=args.output,source_adapter=args.source_adapter,
              model_id=args.model_id,device=args.device,steps=args.steps,lr=args.lr,
              accum=args.accum,max_length=args.max_length,width=args.width,
              layers=args.layers,seed=args.seed,capture_limit_mib=args.capture_limit_mib,
              train_limit=args.train_limit)
    else:
        if args.checkpoint is None:ap.error('evaluate requires --checkpoint')
        result=evaluate(input=args.input,checkpoint=args.checkpoint,output=args.output,
              split=args.split,device=args.device,limit=args.limit,
              max_input_tokens=args.max_input_tokens,ablation_limit=args.ablation_limit)
    print(json.dumps({k:v for k,v in result.items() if k!='cases_detail'},indent=2,sort_keys=True))
    return result

if __name__=='__main__':main()
