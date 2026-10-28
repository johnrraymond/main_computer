#!/usr/bin/env python3
"""Stage 1: numerically adapt Qwen for PROBLEM->PROBLEM/ANSWER vector transitions.

Qwen q/v LoRA + a disposable control/payload probe are trained. HybridLink,
LM head and final math head are NOT trained or invoked. Candidate retrieval is a
teacher-vector diagnostic, not autonomous latent recursion or symbol generation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random

from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA = 'nanojev.qwen.problem-answer-vector-stage1.v1'
SCALES = (8, 64, 1024, 32768, 1000000)
OOD_SCALES = (127, 977, 99991, 1999999)
DEPTHS = (1, 2, 3, 4, 6, 8, 12)
ACTIONS = ('PROBLEM', 'ANSWER')


def _literal(n):
    return str(n) if n >= 0 else f'({n})'


def _expr(nums):
    """Canonical left-deep tree with signed leaves and explicit parentheses."""
    assert len(nums) >= 2
    e = f'{_literal(nums[0])}+{_literal(nums[1])}'
    for v in nums[2:]:
        e = f'({e})+{_literal(v)}'
    return e


def _rows_for(nums, split, i):
    remaining = list(nums)
    rows = []
    original = sum(nums)
    while len(remaining) > 1:
        source = 'PROBLEM: ' + _expr(remaining)
        following = [remaining[0] + remaining[1]] + remaining[2:]
        kind = 'ANSWER' if len(following) == 1 else 'PROBLEM'
        dest = (f'ANSWER: {following[0]}' if kind == 'ANSWER'
                else 'PROBLEM: ' + _expr(following))
        rows.append({'id':f'{split}-{i:05d}-hop-{len(nums)-len(remaining):02d}',
                     'case_id':f'{split}-{i:05d}', 'source':source,'target':dest,
                     'action':kind, 'depth_left':len(remaining)-1,
                     'answer':original,'signed':(original > 0)-(original < 0),
                     'initial_depth':len(nums)-1})
        remaining = following
    assert rows[-1]['target']==f'ANSWER: {original}'
    assert len(rows)==len(nums)-1
    return rows


def _retry_scale(scale, attempt):
    """Grow the *source* support on collisions rather than weaken split isolation.

    With depth=1, i % len(DEPTHS)==0 and the cancellation template uses
    only scale//2..scale for its first operand.  Repeating that tiny pool
    across the default 448/96/96/96 cases exhausts it.  Keep initial draws
    unchanged, then progressively widen the operand pool on retries.  This
    keeps zero-line / near-zero cases while retaining exact source
    disjointness; the nominal scale is a starting band, not a hard cap.
    """
    if attempt < 8:
        return scale
    return scale + (1 + (attempt - 8)//4) * max(4, scale//4)


def make_deck(seed=62421, train_count=448, dev_count=96,
              ood_count=96, test_count=96):
    counts={'train':train_count,'dev':dev_count,'ood':ood_count,'test':test_count}
    if any(v < 14 for v in counts.values()): raise ValueError('STAGE1_MIN_CASE_COUNT_14')
    rng=random.Random(seed)
    deck={}; known=set()
    for split,count in counts.items():
        rows=[]
        for i in range(count):
            depth = DEPTHS[i%len(DEPTHS)]
            scale = (OOD_SCALES if split=='ood' else SCALES)[(i//len(DEPTHS))%(len(OOD_SCALES) if split=='ood' else len(SCALES))]
            for attempt in range(200):
                spread = _retry_scale(scale, attempt)
                nums = [rng.randint(-spread, spread) for _ in range(depth+1)]
                if i%7==0:
                    nums[0]=rng.randint(max(1,spread//2),spread)
                    nums[1]=-nums[0] + (0,1,-1,4,-4)[(i//7)%5]
                if i%11==0 and depth>=2:
                    nums[-1] = -sum(nums[:-1]) + (0,1,-1)[(i//11)%3]
                if i%14==7:nums[-1]=-sum(nums[:-1])
                if split=='train' and i==0:nums=[6,6]
                candidate=_rows_for(nums,split,i)
                sources={r['source'] for r in candidate}
                if len(sources)==len(candidate) and not sources & known:
                    known.update(sources);rows.extend(candidate);break
            else:raise RuntimeError(f'STAGE1_CANNOT_GENERATE_DISJOINT_DECK split={split} case={i} depth={depth} scale={scale} retries=200')
        deck[split]=rows
    return deck


def deck_hash(d):return base.digest(d)


def _text_parent(path):
    ck=Path(path)
    if not ck.is_file():raise ValueError('STAGE1_TEXT_CHECKPOINT_MISSING')
    cfg=json.loads((ck.parent/'run_config.json').read_text(encoding='utf8'))
    if cfg.get('schema')!=micro.SCHEMA or cfg.get('stage')!='train-text':
        raise RuntimeError('STAGE1_WRONG_PARENT_FAMILY')
    return cfg


def _encode(backbone,tok,strings,device,batch_size,max_tokens,grad=False):
    """Qwen transformer ONLY; never calls backbone() or output embeddings."""
    import torch
    from contextlib import nullcontext
    if not strings:raise ValueError('STAGE1_EMPTY_ENCODING')
    dec=backbone.get_decoder()
    pad=tok.pad_token_id
    if pad is None:pad=tok.eos_token_id
    if pad is None:raise RuntimeError('STAGE1_NO_PAD_ID')
    results=[]
    for start in range(0,len(strings),batch_size):
        seq=[tok(s,add_special_tokens=False)['input_ids'] for s in strings[start:start+batch_size]]
        if any(not ids or len(ids)>max_tokens for ids in seq):
            raise ValueError('STAGE1_TOKEN_LIMIT_EXCEEDED')
        lengths=[len(ids) for ids in seq]
        tensor=torch.full((len(seq),max(lengths)),int(pad),dtype=torch.long,device=device)
        mask=torch.zeros_like(tensor)
        for j,ids in enumerate(seq):
            tensor[j,:len(ids)]=torch.tensor(ids,device=device)
            mask[j,:len(ids)]=1
        with nullcontext() if grad else torch.no_grad():
            out=dec(input_ids=tensor,attention_mask=mask,use_cache=False,return_dict=True)
            h=out.last_hidden_state[torch.arange(len(seq),device=device),torch.tensor(lengths,device=device)-1]
            results.append(h.float())
    return torch.cat(results,dim=0)


def _heads(hidden, device):
    from torch import nn
    class Probe(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=nn.LayerNorm(hidden)
            self.control=nn.Linear(hidden,2)
            self.payload=nn.Sequential(nn.Linear(hidden, max(16,hidden//4)),nn.GELU(),
                                       nn.Linear(max(16,hidden//4),hidden))
            # Residual map anchors the payload to Qwen's existing hidden space.
            nn.init.zeros_(self.payload[-1].weight)
            nn.init.zeros_(self.payload[-1].bias)
        def forward(self,h):
            import torch.nn.functional as F
            z=self.norm(h.float())
            return self.control(z),F.normalize(z+self.payload(z),dim=-1)
    return Probe().to(device)


def _loss(probe,features,targets,actions,keys,temperature=.12):
    """Same-action contrastive pairs; duplicate numeric answers are positives."""
    import torch
    import torch.nn.functional as F
    logits,vec=probe(features)
    target=F.normalize(targets.float(),dim=-1)
    scores=vec @ target.T / temperature
    same_action=actions[:,None]==actions[None,:]
    same_target=torch.tensor([[a==b for b in keys] for a in keys],device=scores.device,dtype=torch.bool)
    positives=same_target & same_action
    assert positives.diag().all()
    available=scores.masked_fill(~same_action,-torch.inf)
    pos=available.masked_fill(~positives,-torch.inf)
    contrastive=(torch.logsumexp(available,1)-torch.logsumexp(pos,1)).mean()
    alignment=(1-(vec*target).sum(-1)).mean()
    classification=F.cross_entropy(logits,actions)
    return classification+contrastive+.25*alignment, {
        'control':float(classification.detach()),'contrastive':float(contrastive.detach()),
        'alignment':float(alignment.detach())}


def _evaluation(backbone,tok,probe,rows,teacher,device,encode_batch,max_tokens):
    """Within-type retrieval: teacher candidates only for diagnostic scoring."""
    import torch
    import torch.nn.functional as F
    probe.eval();backbone.eval()
    targets=[x['target'] for x in rows]
    truth=[x['action'] for x in rows]
    with torch.no_grad():
        features=_encode(backbone,tok,[x['source'] for x in rows],device,encode_batch,max_tokens)
        ctrl,pred=probe(features)
        kinds=ctrl.argmax(-1).tolist()
        prototypes=F.normalize(teacher.float(),dim=-1)
        sim=pred @ prototypes.T
        counters=Counter()
        errors=[]
        for i,(row,k) in enumerate(zip(rows,kinds)):
            counters['total']+=1
            correct=k==ACTIONS.index(row['action'])
            counters['action_correct']+=int(correct)
            counters['answer_count' if row['action']=='ANSWER' else 'problem_count']+=1
            if not correct:
                counters['gate_wrong']+=1
                if len(errors)<24:errors.append({'source':row['source'],'expected':row['action'],
                                                   'predicted':ACTIONS[k],'expected_target':row['target']})
                continue
            # Retrieval is restricted to predicted type to prevent the gate alone
            # from making target alignment appear more accurate than it is.
            valid=torch.tensor([x==ACTIONS[k] for x in truth],device=device,dtype=torch.bool)
            best=int(sim[i].masked_fill(~valid,-torch.inf).argmax())
            right=targets[best]==row['target']
            counters['payload_correct']+=int(right)
            counters['answer_correct' if row['action']=='ANSWER' else 'problem_correct']+=int(right)
            if not right and len(errors)<24:
                errors.append({'source':row['source'],'expected':row['action'],
                               'expected_target':row['target'],'retrieved':targets[best]})
    def rate(a,b):return round(counters[a]/counters[b],6) if counters[b] else None
    return {'transitions':len(rows),'control_accuracy':rate('action_correct','total'),
            'payload_accuracy':rate('payload_correct','total'),
            'answer_payload_accuracy':rate('answer_correct','answer_count'),
            'next_problem_payload_accuracy':rate('problem_correct','problem_count'),
            'candidate_pool':len(rows),'candidate_protocol':'teacher-vector within-kind retrieval, NOT generation',
            'errors':errors}


def _load_model(args):
    from tools import nanojev_qwen_zero_line_backbone as zero
    parent=_text_parent(args.text_checkpoint)
    model,tok,sources,frozen,dirs=micro._load(args,checkpoint=Path(args.text_checkpoint))
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(False)
    return model,tok,sources,frozen,dirs,parent


def preview(args):
    _text_parent(args.text_checkpoint)
    deck=make_deck(args.seed,args.train_count,args.dev_count,args.ood_count,args.test_count)
    result={'schema':SCHEMA,'stage':'preview','model_loaded':False,'deck_sha256':deck_hash(deck),
            'counts':{k:{'transitions':len(v),'answers':sum(r['action']=='ANSWER' for r in v),
                          'next_problems':sum(r['action']=='PROBLEM' for r in v)} for k,v in deck.items()},
            'sample':[{k:r[k] for k in ('source','target','action')} for r in deck['train'][:4]],
            'no_lm_head':True,'no_hybridlink_training':True,'no_math_head':True}
    print('QWEN_STAGE1_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)
    return result


def _prepare(args,split,include_teacher=True):
    deck=make_deck(args.seed,args.train_count,args.dev_count,args.ood_count,args.test_count)
    model,tok,sources,frozen,dirs,parent=_load_model(args)
    return deck,model,tok,sources,frozen,dirs


def train(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    if not (args.steps>0 and args.eval_every>0 and args.batch_size>1 and args.max_tokens>8 and args.lr>0):
        raise ValueError('STAGE1_BAD_TRAIN_PARAMETERS')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('STAGE1_NONEMPTY_OUTPUT')
    deck,model,tok,sources,frozen,dirs=_prepare(args,'dev')
    for protected in (*dirs,Path(args.text_checkpoint).parent):
        if out.resolve()==protected.resolve() or out.resolve() in protected.resolve().parents or protected.resolve() in out.resolve().parents:
            raise RuntimeError('STAGE1_OUTPUT_OVERLAPS_PARENT')
    backbone=model.backbone
    backbone.eval()
    # Teacher Qwen vectors are captured ONCE from the unmodified text-LoRA parent.
    targets={}
    for split in ('train','dev'):
        targets[split]=_encode(backbone,tok,[r['target'] for r in deck[split]],args.device,args.encoder_batch,args.max_tokens).detach().cpu()
    original=zero.lora_state(backbone)
    params=[p for _,p in zero.lora_named_parameters(backbone)]
    if not params:raise RuntimeError('STAGE1_NO_LORA_WEIGHTS_TO_FINE_TUNE')
    for p in params:p.requires_grad_(True)
    backbone.eval();model.head.requires_grad_(False)
    torch.manual_seed(args.seed)
    probe=_heads(int(backbone.config.hidden_size),args.device)
    optimizer=torch.optim.AdamW([{'params':params,'lr':args.lr},
                                  {'params':list(probe.parameters()),'lr':args.probe_lr}],weight_decay=0)
    train_rows=deck['train'];dev_rows=deck['dev']
    train_target=targets['train'];dev_target=targets['dev']
    meta={'schema':SCHEMA,'stage':'train-qwen','source_sha256':sources,'frozen_sha256':frozen,
          'text_parent_sha256':activation.file_hash(Path(args.text_checkpoint)),
          'deck_sha256':deck_hash(deck),'seed':args.seed,'train_count':args.train_count,
          'dev_count':args.dev_count,'ood_count':args.ood_count,'test_count':args.test_count,
          'lr':args.lr,'probe_lr':args.probe_lr,'steps':args.steps,'batch_size':args.batch_size,
          'max_tokens':args.max_tokens,'encoder_batch':args.encoder_batch,'eval_every':args.eval_every,
          'selection':'dev control_accuracy and candidate payload_accuracy, not a generation certificate',
          'trainable':'Qwen q/v LoRA + provisional control and teacher-vector mapping heads',
          'hybridlink_training':False,'lm_head_used':False,'math_head_training':False,'test_evaluated':False}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'run_config.json',meta)
    rng=random.Random(args.seed+11)
    best_score=(-1,-1);best_step=None
    for step in range(1,args.steps+1):
        selected=rng.sample(range(len(train_rows)),min(args.batch_size,len(train_rows)))
        rows=[train_rows[i] for i in selected]
        target=train_target[selected].to(args.device)
        actions=torch.tensor([ACTIONS.index(r['action']) for r in rows],device=args.device)
        optimizer.zero_grad(set_to_none=True)
        features=_encode(backbone,tok,[r['source'] for r in rows],args.device,args.encoder_batch,args.max_tokens,grad=True)
        loss,detail=_loss(probe,features,target,actions,[r['target'] for r in rows])
        if not torch.isfinite(loss):raise RuntimeError('STAGE1_NONFINITE_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_([*params,*probe.parameters()],1.0)
        optimizer.step()
        if step%args.eval_every and step!=args.steps:continue
        metrics=_evaluation(backbone,tok,probe,dev_rows,dev_target.to(args.device),args.device,args.encoder_batch,args.max_tokens)
        score=(metrics['payload_accuracy'],metrics['control_accuracy'])
        if score>best_score:
            best_score=score;best_step=step
            base._atomic_torch(out/'best.pt',{'schema':SCHEMA,'status':'STAGE1_TRAINED_UNCERTIFIED',
                 'config_hash':base.digest(meta),'step':step,'source_sha256':sources,
                 'frozen_sha256':frozen,'text_parent_sha256':meta['text_parent_sha256'],
                 'lora':zero.lora_state(backbone),
                 'probe':{k:v.detach().cpu().clone() for k,v in probe.state_dict().items()}})
        print('QWEN_STAGE1_PROGRESS='+json.dumps({'step':step,'train_loss':round(float(loss.detach()),5),
               'train_loss_parts':detail,'dev_control_accuracy':metrics['control_accuracy'],
               'dev_payload_accuracy':metrics['payload_accuracy'],
               'dev_answer_payload_accuracy':metrics['answer_payload_accuracy'],
               'dev_next_problem_payload_accuracy':metrics['next_problem_payload_accuracy'],
               'best_step':best_step,'backbone_lora_trainable':True,
               'headless':True},sort_keys=True),flush=True)
    final=zero.lora_state(backbone)
    if not any(not torch.equal(original[k],final[k]) for k in original):
        raise RuntimeError('STAGE1_LORA_DID_NOT_UPDATE')
    if zero.frozen_backbone_sha256(backbone)!=frozen:
        raise RuntimeError('STAGE1_FROZEN_QWEN_WEIGHTS_CHANGED')
    # Measure how much trainable Qwen adaptation matters under the SAME probe.
    saved=zero.lora_state(backbone)
    zero.restore_lora(backbone,original)
    parent_scores=_evaluation(backbone,tok,probe,dev_rows,dev_target.to(args.device),args.device,args.encoder_batch,args.max_tokens)
    zero.restore_lora(backbone,saved)
    outcome={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED','best_step':best_step,
             'last_step_dev':metrics,'warm_start_backbone_under_last_step_probe':parent_scores,
             'backbone_lora_updated':True,'final_checkpoint':str(out/'best.pt'),
             'test_evaluated':False,'hybridlink_trained':False,'lm_head_used':False,
             'recursion_running':False}
    base._atomic_json(out/'result.json',outcome)
    print('QWEN_STAGE1_TRAIN_RESULT='+json.dumps(outcome,sort_keys=True),flush=True)
    return outcome


def probe_run(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    ck=Path(args.checkpoint)
    if not ck.is_file():raise ValueError('STAGE1_CHECKPOINT_MISSING')
    if args.split=='test' and (ck.parent/'test_consumed.json').exists():
        raise RuntimeError('STAGE1_TEST_ALREADY_CONSUMED')
    saved=json.loads((ck.parent/'run_config.json').read_text(encoding='utf8'))
    if saved.get('schema')!=SCHEMA or saved.get('stage')!='train-qwen':
        raise RuntimeError('STAGE1_CONFIG_WRONG_FAMILY')
    if activation.file_hash(Path(args.text_checkpoint))!=saved['text_parent_sha256']:
        raise RuntimeError('STAGE1_TEXT_PARENT_DRIFT')
    deck=make_deck(saved['seed'],saved['train_count'],saved['dev_count'],saved['ood_count'],saved['test_count'])
    if deck_hash(deck)!=saved['deck_sha256']:raise RuntimeError('STAGE1_DECK_DRIFT')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('STAGE1_PROBE_NONEMPTY_OUTPUT')
    deck,model,tok,sources,frozen,dirs=_prepare(args,args.split)
    for protected in (*dirs,Path(args.text_checkpoint).parent,ck.parent):
        if out.resolve()==protected.resolve() or out.resolve() in protected.resolve().parents or protected.resolve() in out.resolve().parents:
            raise RuntimeError('STAGE1_PROBE_OUTPUT_OVERLAPS_SOURCE')
    if sources!=saved['source_sha256'] or frozen!=saved['frozen_sha256']:
        raise RuntimeError('STAGE1_SOURCE_DRIFT')
    backbone=model.backbone
    rows=deck[args.split]
    # Teacher targets are encoded from original parent before installing the learned LoRA.
    target=_encode(backbone,tok,[r['target'] for r in rows],args.device,args.encoder_batch,args.max_tokens).detach()
    state=torch.load(ck,weights_only=True,map_location='cpu')
    if state['schema']!=SCHEMA or state['config_hash']!=base.digest(saved):
        raise RuntimeError('STAGE1_CHECKPOINT_DRIFT')
    probe=_heads(int(backbone.config.hidden_size),args.device)
    probe.load_state_dict(state['probe'],strict=True);probe.eval()
    original=zero.lora_state(backbone)
    zero.restore_lora(backbone,state['lora'])
    metrics=_evaluation(backbone,tok,probe,rows,target,args.device,args.encoder_batch,args.max_tokens)
    zero.restore_lora(backbone,original)
    before=_evaluation(backbone,tok,probe,rows,target,args.device,args.encoder_batch,args.max_tokens)
    result={'schema':SCHEMA,'status':'PROBE_UNCERTIFIED','split':args.split,
            'trained':metrics,'original_qwen_same_probe':before,
            'checkpoint_sha256':activation.file_hash(ck),'lm_head_used':False,
            'hybridlink_used':False,'math_head_used':False,'recursion_running':False,
            'test_evaluated':args.split=='test'}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'result.json',result)
    if args.split=='test':
        base._atomic_json(ck.parent/'test_consumed.json',{'checkpoint_sha256':result['checkpoint_sha256']})
    print('QWEN_STAGE1_PROBE_RESULT='+json.dumps({k:v for k,v in result.items() if k not in ('trained','original_qwen_same_probe')}
                                      |{'trained':{k:v for k,v in metrics.items() if k!='errors'},
                                        'original_qwen_same_probe':{k:v for k,v in before.items() if k!='errors'}},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','probe'),default='preview')
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--checkpoint')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_problem_answer_stage1_v1'))
    p.add_argument('--seed',type=int,default=62421)
    p.add_argument('--train-count',type=int,default=448)
    p.add_argument('--dev-count',type=int,default=96)
    p.add_argument('--ood-count',type=int,default=96)
    p.add_argument('--test-count',type=int,default=96)
    p.add_argument('--batch-size',type=int,default=12)
    p.add_argument('--encoder-batch',type=int,default=12)
    p.add_argument('--max-tokens',type=int,default=128)
    p.add_argument('--steps',type=int,default=1200)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--lr',type=float,default=0.00005)
    p.add_argument('--probe-lr',type=float,default=0.0002)
    p.add_argument('--split',choices=('dev','ood','test'),default='dev')
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage=='probe' and not args.checkpoint:p.error('STAGE1_PROBE_NEEDS_CHECKPOINT')
    if args.stage=='preview':preview(args)
    elif args.stage=='train':train(args)
    else:probe_run(args)

if __name__=='__main__':main()
