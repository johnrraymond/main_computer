#!/usr/bin/env python3
"""Greedy-failure-directed, oracle-free-inference Qwen glass experiment.

Harvest exact baseline greedy failures, then learn a hidden-state correction at
THE FIRST divergent autoregressive token.  Correct token IDs are used only as
training labels.  At inference a glass mode is recognized ONLY from frozen
Qwen multi-layer activations; no prompt ID, answer, or gold labels enter the
activation router.  Freezes the Qwen backbone and LM head.

Separate from earlier deformation_smoke/refine outputs and checkpoints.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools import nanojev_qwen_lm_deformation_refine as refine
from tools import nanojev_qwen_lm_deformation_legacy as base

SCHEMA = 'nanojev.qwen.failure-mode-glass.v1'


def _write_json(path, payload):
    base.ladder._atomic_json(path, payload)


def _lineage(args, lineage):
    return {'schema':SCHEMA, 'frozen_baseline':lineage, 'max_new_tokens':args.max_new_tokens,
            'max_tokens':args.max_tokens, 'layers':4, 'match_threshold':args.match_threshold}


def signature(hidden_states):
    """Use four widely spaced *live activation states*, not static weights.

    Static frozen Qwen weights do not identify an individual failure; their
    input-dependent activations do.  The LM head itself supplies gradients for
    the hidden-vector correction.
    """
    import torch
    import torch.nn.functional as F
    if not hidden_states:raise RuntimeError('GLASS_NO_HIDDEN_STATES')
    n=len(hidden_states)
    indices=tuple(dict.fromkeys((0,n//3,(2*n)//3,n-1)))
    samples=[]
    for i in indices:
        h=hidden_states[i][:,-1,:].detach().float()
        samples.append(F.normalize(h,dim=-1))
    result=torch.cat(samples,dim=-1)
    return F.normalize(result,dim=-1)


class FailureModeGlass:
    """Full multi-layer activation keys, zero outside matching modes.

    This is not a source-text lookup.  It sees signatures produced by Qwen
    while decoding and adds a trainable 1024-D hidden correction only for
    recognized modes.  Each new correction is initialized to exactly zero.
    """
    def __init__(self, hidden, *, threshold=0.99999, device='cpu'):
        import torch
        self.hidden=int(hidden);self.threshold=float(threshold);self.device=device
        if not 0. < self.threshold <= 1.:raise ValueError('INVALID_MODE_THRESHOLD')
        self.keys=None
        self.corrections=torch.nn.ParameterList()

    @property
    def n_modes(self):return len(self.corrections)

    def match(self, feature):
        import torch
        if self.keys is None or self.n_modes==0:return -1,0.
        q=feature.detach().float().reshape(1,-1).to(self.device)
        sim=q @ self.keys.T
        strength,index=sim.max(dim=-1)
        return (int(index.item()),float(strength.item())) if float(strength)>=self.threshold-1e-7 else (-1,float(strength.item()))

    def add(self, feature):
        import torch
        q=feature.detach().float().reshape(1,-1).to(self.device)
        q=torch.nn.functional.normalize(q,dim=-1)
        mode,strength=self.match(q)
        if mode>=0:return mode,False
        self.keys=q if self.keys is None else torch.cat([self.keys,q],dim=0)
        self.corrections.append(torch.nn.Parameter(torch.zeros(self.hidden,device=self.device)))
        return self.n_modes-1,True

    def apply(self, h, feature):
        mode,_=self.match(feature)
        if mode<0:return h,-1
        return h+self.corrections[mode].to(dtype=h.dtype).reshape(1,-1),mode

    def state(self):
        import torch
        return {'hidden':self.hidden,'threshold':self.threshold,
                'keys':None if self.keys is None else self.keys.detach().cpu(),
                'corrections':torch.stack([p.detach().cpu() for p in self.corrections])
                    if self.n_modes else torch.empty(0,self.hidden)}

    @classmethod
    def restore(cls, state, device='cpu'):
        obj=cls(state['hidden'],threshold=state['threshold'],device=device)
        if state['keys'] is not None:
            obj.keys=state['keys'].to(device).float()
            for vector in state['corrections']:
                obj.corrections.append(__import__('torch').nn.Parameter(vector.to(device).float().clone()))
            if len(obj.keys)!=obj.n_modes:raise RuntimeError('GLASS_MODE_STATE_MISMATCH')
        return obj


def decode(bb,tok,source,args,glass=None,expected_ids=None):
    """Full unassisted greedy generation, optional FIRST divergence capture.

    `expected_ids` is supplied only by the *trainer*, never by infer() or the
    deployment route.  Routing uses exclusively hidden activation signatures.
    """
    import torch
    decoder=bb.get_decoder();head=bb.get_output_embeddings()
    token_ids=tok(source+'\n',add_special_tokens=False)['input_ids']
    if not token_ids or len(token_ids)+args.max_new_tokens>args.max_tokens:
        raise ValueError('GLASS_CONTEXT_LIMIT')
    ids=torch.tensor([token_ids],device=args.device,dtype=torch.long)
    eos=tok.eos_token_id
    eos_set={int(eos)} if isinstance(eos,int) else set(eos or ())
    completion=[];first=None;mode_hits=[]
    with torch.no_grad():
        for step in range(args.max_new_tokens):
            out=decoder(input_ids=ids,attention_mask=torch.ones_like(ids),
                        use_cache=False,return_dict=True,output_hidden_states=True)
            h=out.last_hidden_state[:,-1,:].detach().clone()
            feat=signature(out.hidden_states).detach().clone()
            corrected,mode=(glass.apply(h,feat) if glass is not None else (h,-1))
            if mode>=0:mode_hits.append({'step':step,'mode':mode})
            logits=head(corrected.to(dtype=head.weight.dtype)).float()
            token=int(logits.argmax(dim=-1).item())
            if expected_ids is not None and first is None and (
                    step>=len(expected_ids) or token!=int(expected_ids[step])):
                first={'step':step,'gold':int(expected_ids[step]) if step<len(expected_ids) else None,
                       'predicted':token,'h':h.detach().cpu().float().reshape(-1),
                       'feature':feat.detach().cpu().float().reshape(-1)}
            if token in eos_set:break
            completion.append(token)
            text=tok.decode(completion,skip_special_tokens=True)
            if '\n' in text or '\r' in text:break
            ids=torch.cat((ids,torch.tensor([[token]],device=ids.device)),dim=1)
    return {'output':tok.decode(completion,skip_special_tokens=True),
            'token_ids':completion,'first':first,'mode_hits':mode_hits}


def train_one_token(head,glass,mode,h,expected,args):
    """Backprop only into a single glass vector until gold token wins margin."""
    import torch
    import torch.nn.functional as F
    if expected is None:return {'ok':False,'reason':'missing_target_token'}
    h=h.detach().float().reshape(1,-1).to(args.device)
    p=glass.corrections[mode]
    opt=torch.optim.Adam([p],lr=args.lr)
    with torch.no_grad():initial=p.detach().clone()
    for step in range(args.token_train_steps+1):
        logits=head((h+p.reshape(1,-1)).to(dtype=head.weight.dtype)).float()[0]
        best=int(logits.argmax().item())
        gold=logits[int(expected)]
        rivals=logits.detach().clone();rivals[int(expected)]=float('-inf')
        competitor=float(rivals.max().item())
        margin=float(gold.detach().item())-competitor
        if best==expected and margin>=args.logit_margin:
            return {'ok':True,'steps':step,'margin':margin,
                    'change_l2':float((p.detach()-initial).norm())}
        if step==args.token_train_steps:break
        opt.zero_grad(set_to_none=True)
        # Competing-token max must carry gradient into the correction vector.
        runner_up=torch.cat((logits[:expected],logits[expected+1:])).max()
        loss=F.softplus(runner_up-gold+args.logit_margin)
        if args.l2:loss=loss+args.l2*p.square().mean()
        loss.backward()
        if p.grad is None:raise RuntimeError('GLASS_NO_GRADIENT')
        torch.nn.utils.clip_grad_norm_([p],args.grad_clip)
        opt.step()
    return {'ok':False,'steps':args.token_train_steps,'margin':margin,
            'reason':'failed_logit_margin'}


def harvest(bb,tok,rows,args,work,lineage):
    """Persist ONLY frozen-Qwen failing prompts (with targets for training).

    A partial inventory resumes at a completed case; interruption cannot
    silently convert an incomplete scan into a 54-case certification.
    """
    path=work/'known_failures.json'
    partial=work/'harvest_partial.json'
    prompts_path=work/'known_failure_prompts.txt'
    if path.is_file():
        result=json.loads(path.read_text(encoding='utf8'))
        if result['lineage']!=lineage or result['cases']!=len(rows):
            raise RuntimeError('GLASS_FAILURE_LIST_DRIFT')
        if not prompts_path.is_file():
            _write_prompts(prompts_path,result['failures'])
        if args.expected_failures>=0 and result['failure_count']!=args.expected_failures:
            raise RuntimeError('GLASS_EXISTING_FAILURE_COUNT_MISMATCH')
        return result
    failures=[];start=0
    if partial.is_file():
        saved=json.loads(partial.read_text(encoding='utf8'))
        if saved['lineage']!=lineage or saved['cases']!=len(rows):
            raise RuntimeError('GLASS_HARVEST_PARTIAL_DRIFT')
        start=saved['next_index'];failures=saved['failures']
        if start<0 or start>len(rows):raise RuntimeError('GLASS_HARVEST_PARTIAL_CURSOR')
    for index in range(start,len(rows)):
        row=rows[index]
        output=decode(bb,tok,row['source'],args)['output']
        ok,_=base.ladder.correctness(row['source'],row['target'],output)
        if not ok:
            failures.append({'id':row['id'],'source':row['source'],
                             'target':row['target'],'baseline_output':output,
                             'rung':row['rung'],'residual':row['residual']})
        if (index+1)%args.harvest_checkpoint_every==0:
            _write_json(partial,{'schema':SCHEMA,'lineage':lineage,'cases':len(rows),
                                 'next_index':index+1,'failures':failures})
        if args.progress_every and ((index+1)%args.progress_every==0 or index+1==len(rows)):
            print('FAILURE_GLASS_HARVEST_PROGRESS='+json.dumps({'tested':index+1,
                  'total':len(rows),'failures':len(failures)},sort_keys=True),flush=True)
    result={'schema':SCHEMA,'lineage':lineage,'cases':len(rows),'failures':failures,
            'failure_count':len(failures)}
    _write_json(path,result)
    _write_prompts(prompts_path,failures)
    if partial.is_file():partial.unlink()
    print('FAILURE_GLASS_HARVEST='+json.dumps({'failures':len(failures),
          'path':str(path),'prompts':str(prompts_path),'cases':len(rows)},sort_keys=True),flush=True)
    if args.expected_failures>=0 and len(failures)!=args.expected_failures:
        raise RuntimeError(f'GLASS_EXPECTED_{args.expected_failures}_FAILURES_FOUND_{len(failures)}; '
                           f'inspect {str(path)} before training')
    return result


def _write_prompts(path,failures):
    tmp=path.with_suffix('.txt.tmp')
    tmp.write_text(''.join(row['source']+'\n' for row in failures),encoding='utf8')
    os.replace(tmp,path)


def _save_train(path,glass,lineage,history,*,pass_index=None,next_index=None,fixes=0,
                token_updates=0,unresolved=()):
    payload={'schema':SCHEMA,'lineage':lineage,'glass':glass.state(),
             'history':history}
    if pass_index is not None:
        payload['cursor']={'pass_index':pass_index,'next_index':next_index,
                           'fixes':fixes,'token_updates':token_updates,
                           'unresolved':list(unresolved)}
    base._store(path,payload)


def fit(bb,tok,head,glass,rows,args,work,lineage):
    """Reproduce and correct *each* real autoregressive failure, rerun, repeat.

    Correct labels enter ONLY this trainer and never the routing or inference
    logic.  Earlier successful corrections are retained across passes.
    """
    import torch
    harvest_obj=harvest(bb,tok,rows,args,work,lineage)
    failures=harvest_obj['failures']
    cp=work/'working.pt'
    partial=work/'partial.pt'
    history=[]
    resume=None
    if cp.is_file():
        obj=torch.load(cp,map_location='cpu',weights_only=True)
        if obj['lineage']!=lineage:raise RuntimeError('GLASS_WORKING_LINEAGE_DRIFT')
        glass=FailureModeGlass.restore(obj['glass'],device=args.device)
        history=obj['history']
    if partial.is_file():
        obj=torch.load(partial,map_location='cpu',weights_only=True)
        if obj['lineage']!=lineage:raise RuntimeError('GLASS_PARTIAL_LINEAGE_DRIFT')
        # A crash between committing working.pt and deleting partial.pt
        # leaves an obsolete partial. Prefer the newer completed checkpoint.
        if len(obj['history'])==len(history)-1 and history:
            partial.unlink()
        elif len(obj['history'])!=len(history):
            raise RuntimeError('GLASS_PARTIAL_HISTORY_DRIFT')
        else:
            resume=obj['cursor']
            if resume['pass_index']!=len(history):
                raise RuntimeError('GLASS_PARTIAL_PASS_DRIFT')
            glass=FailureModeGlass.restore(obj['glass'],device=args.device)
    if history and history[-1]['remaining']==0:
        return glass,history
    for pass_index in range(len(history),args.max_passes):
        cursor=(resume if resume is not None and resume['pass_index']==pass_index else None)
        fixes=cursor['fixes'] if cursor else 0
        unresolved=list(cursor['unresolved']) if cursor else []
        token_updates=cursor['token_updates'] if cursor else 0
        begin=cursor['next_index'] if cursor else 0
        for index in range(begin,len(failures)):
            row=failures[index]
            expected=tok(row['target']+'\n',add_special_tokens=False)['input_ids']
            # The gold sequence is used to supervise the FIRST divergent
            # greedy token. Every update is checked by regenerating the answer.
            for attempt in range(args.max_token_fixes_per_case+1):
                trace=decode(bb,tok,row['source'],args,glass,expected_ids=expected)
                ok,_=base.ladder.correctness(row['source'],row['target'],trace['output'])
                if ok:break
                fault=trace['first']
                if fault is None or fault['gold'] is None or attempt==args.max_token_fixes_per_case:
                    unresolved.append({'id':row['id'],'reason':'no_trainable_divergence_or_budget',
                                       'output':trace['output']})
                    break
                mode,created=glass.add(fault['feature'])
                result=train_one_token(head,glass,mode,fault['h'],fault['gold'],args)
                token_updates+=result['steps']
                if not result['ok']:
                    unresolved.append({'id':row['id'],'reason':result['reason'],
                                       'step':fault['step'],'mode':mode,'output':trace['output']})
                    break
            if ok:fixes+=1
            if args.progress_every and ((index+1)%args.progress_every==0 or index+1==len(failures)):
                print('FAILURE_GLASS_FIT_PROGRESS='+json.dumps({'pass':pass_index+1,
                    'tested':index+1,'total':len(failures),'fixed_in_pass':fixes,
                    'modes':glass.n_modes,'token_steps':token_updates},sort_keys=True),flush=True)
            if (index+1)%args.checkpoint_every==0:
                _save_train(partial,glass,lineage,history,pass_index=pass_index,
                            next_index=index+1,fixes=fixes,token_updates=token_updates,
                            unresolved=unresolved)
        # Independently regenerate all known failures; training on a token
        # is NEVER sufficient proof that the final answer was repaired.
        still=[]
        for row in failures:
            output=decode(bb,tok,row['source'],args,glass)['output']
            if not base.ladder.correctness(row['source'],row['target'],output)[0]:
                still.append({'id':row['id'],'output':output})
        event={'pass':pass_index+1,'known_failures':len(failures),
               'correct':len(failures)-len(still),'remaining':len(still),
               'modes':glass.n_modes,'token_optimizer_steps':token_updates,
               'unresolved':unresolved[:64],'remaining_examples':still[:64]}
        history.append(event)
        _save_train(cp,glass,lineage,history)
        if partial.is_file():partial.unlink()
        print('FAILURE_GLASS_PASS='+json.dumps(event,sort_keys=True),flush=True)
        if not still:break
        resume=None
    if not history or history[-1]['remaining']:
        print('FAILURE_GLASS_INCOMPLETE='+json.dumps({'remaining':history[-1]['remaining'] if history else len(failures),
               'max_passes':args.max_passes,'checkpoint':str(cp)},sort_keys=True),flush=True)
    return glass,history


def audit(bb,tok,rows,glass,args,work,lineage):
    errors=[];passed=0;new_errors=[]
    failure_ids={r['id'] for r in json.loads((work/'known_failures.json').read_text())['failures']}
    for i,row in enumerate(rows):
        output=decode(bb,tok,row['source'],args,glass)['output']
        ok,_=base.ladder.correctness(row['source'],row['target'],output)
        if ok:passed+=1
        else:
            entry={'id':row['id'],'source':row['source'],'expected':row['target'],
                   'output':output,'rung':row['rung'],'residual':row['residual']}
            errors.append(entry)
            if row['id'] not in failure_ids:new_errors.append(entry)
        if args.progress_every and ((i+1)%args.progress_every==0 or i+1==len(rows)):
            print('FAILURE_GLASS_AUDIT_PROGRESS='+json.dumps({'tested':i+1,
                  'total':len(rows),'wrong':len(errors),'new_errors':len(new_errors)},sort_keys=True),flush=True)
    result={'schema':SCHEMA,'lineage':lineage,'tested':len(rows),'passed':passed,
            'failed':len(errors),'known_failures_unfixed':sum(x['id'] in failure_ids for x in errors),
            'new_errors':len(new_errors),'failures':errors,'complete':len(errors)==0}
    _write_json(work/'full_audit.json',result)
    print('FAILURE_GLASS_AUDIT='+json.dumps({k:v for k,v in result.items() if k!='failures'},sort_keys=True),flush=True)
    return result


def run(args):
    import torch
    if args.stage=='status':
        work=Path(args.output)/'glass_failure_modes_v1'
        results={}
        for p in (work/'known_failures.json',work/'full_audit.json'):
            if p.is_file():results[p.name]=json.loads(p.read_text(encoding='utf8'))
        print('FAILURE_GLASS_STATUS='+json.dumps({k:{'cases':v.get('cases'),
            'failures':v.get('failure_count',v.get('failed'))} for k,v in results.items()},sort_keys=True),flush=True)
        return results
    if args.stage=='infer' and not args.prompt:raise ValueError('GLASS_INFER_PROMPT_REQUIRED')
    if args.max_passes<1 or args.max_token_fixes_per_case<1 or args.token_train_steps<1 or args.checkpoint_every<1 or args.harvest_checkpoint_every<1:
        raise ValueError('GLASS_INVALID_TRAINING_BUDGET')
    bb,tok,head,zero,rows,_old_glass,old_lineage,_work,qwen_sha,_report=refine._unpack(args)
    bb.eval();bb.requires_grad_(False);head.eval();head.requires_grad_(False)
    work=Path(args.output)/'glass_failure_modes_v1'
    work.mkdir(parents=True,exist_ok=True)
    lineage=_lineage(args,old_lineage)
    glass=FailureModeGlass(int(head.weight.shape[1]),threshold=args.match_threshold,device=args.device)
    if args.stage in ('audit','infer'):
        cp=work/'working.pt'
        if not cp.is_file():raise FileNotFoundError('GLASS_TRAIN_FIRST')
        old=torch.load(cp,map_location='cpu',weights_only=True)
        if old['lineage']!=lineage:raise RuntimeError('GLASS_CHECKPOINT_LINEAGE_DRIFT')
        glass=FailureModeGlass.restore(old['glass'],device=args.device)
    if args.stage=='harvest':result=harvest(bb,tok,rows,args,work,lineage)
    elif args.stage=='train':
        glass,history=fit(bb,tok,head,glass,rows,args,work,lineage)
        result=history[-1]
        if result['remaining']:
            raise RuntimeError('GLASS_KNOWN_FAILURES_UNCORRECTED_'+str(result['remaining'])+
                               '; checkpoint='+str(work/'working.pt'))
    elif args.stage=='audit':result=audit(bb,tok,rows,glass,args,work,lineage)
    elif args.stage=='infer':
        output=decode(bb,tok,args.prompt,args,glass)
        result={'output':output['output'],'mode_hits':output['mode_hits']}
        print('FAILURE_GLASS_INFER='+json.dumps(result,sort_keys=True),flush=True)
    else:raise ValueError('GLASS_BAD_STAGE')
    if base._lora_digest(bb,zero)!=qwen_sha:
        raise RuntimeError('GLASS_FROZEN_QWEN_MUTATED')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('harvest','train','audit','infer','status'),default='harvest')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint');p.add_argument('--text-checkpoint')
    p.add_argument('--ladder-working');p.add_argument('--output',required=True)
    p.add_argument('--radii',default='0,1,2,3,4');p.add_argument('--max-rung',type=int,default=8)
    p.add_argument('--operand-limit',type=int,default=1024);p.add_argument('--per-cell',type=int,default=8)
    p.add_argument('--t00-mode',choices=('exhaustive','sampled'),default='exhaustive')
    p.add_argument('--seed',type=int,default=42257);p.add_argument('--case-cap',type=int,default=0)
    p.add_argument('--batch-size',type=int,default=4);p.add_argument('--eval-batch',type=int,default=8)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256);p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--progress-every',type=int,default=100)
    p.add_argument('--expected-failures',type=int,default=54)
    p.add_argument('--harvest-checkpoint-every',type=int,default=100)
    p.add_argument('--match-threshold',type=float,default=0.99999)
    p.add_argument('--max-passes',type=int,default=100)
    p.add_argument('--max-token-fixes-per-case',type=int,default=20)
    p.add_argument('--token-train-steps',type=int,default=100)
    p.add_argument('--lr',type=float,default=.1)
    p.add_argument('--logit-margin',type=float,default=2.)
    p.add_argument('--l2',type=float,default=0.)
    p.add_argument('--grad-clip',type=float,default=10.)
    p.add_argument('--checkpoint-every',type=int,default=5)
    p.add_argument('--prompt')
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage not in ('status',) and (not args.stage1_checkpoint or not args.text_checkpoint or not args.ladder_working):
        p.error('the same three source checkpoint arguments as the original smoke test are required')
    return run(args)

if __name__=='__main__':main()
