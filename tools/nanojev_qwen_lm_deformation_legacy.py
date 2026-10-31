#!/usr/bin/env python3
"""Two-pass Qwen zero-line deformation-glass smoke experiment.

Pass 1: train only the existing Qwen q/v LoRA on the complete selected
          arithmetic corpus; freeze a copy of that trained model.
Pass 2: train only a zero-default, activation-localized latent correction
          immediately before the existing frozen LM output head.

This is a Qwen/LM-head interface smoke test, NOT a claim that the entire
HybridLink apparatus has been trained or that autoregressive errors can be
identified perfectly from teacher-forced token predictions.

The input ladder working.pt is never modified; this experiment uses its own
output directory and immutable baseline.pt/glass.pt checkpoints.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import math
import random
import shutil
import time
from pathlib import Path

from tools import nanojev_qwen_lm_radius_ladder as ladder

SCHEMA = 'nanojev.qwen.relative-deformation-smoke.v1'


def corpus_for_schedule(radii, max_rung, operand_limit, per_cell, seed, t00_mode):
    """Nested radii: maximum radius includes all smaller deltas.

    Return each exact case once, not once per overlapping radius.  This is the
    entire finite acquisition corpus selected by the requested schedule.
    """
    specs = ladder.parse_radii(radii, operand_limit)
    rows = []
    scopes = []
    for rung in range(max_rung + 1):
        cases, scope = ladder.deck(rung, specs[-1], operand_limit,
                                   per_cell=per_cell, seed=seed, t00_mode=t00_mode)
        if scope['undersampled_cells']:
            raise RuntimeError('GLASS_CORPUS_UNDERSAMPLED:'+str(rung))
        rows.extend(cases)
        scopes.append({'rung': rung, 'radius': specs[-1], 'count': len(cases), 'scope': scope['kind']})
    return rows, scopes


def _cases_hash(rows):
    return ladder.deck_fingerprint(rows)


def _file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def _lora_digest(bb,zero):
    h=hashlib.sha256()
    for name,tensor in sorted(zero.lora_state(bb).items()):
        h.update(name.encode('utf8'))
        h.update(tensor.detach().contiguous().cpu().float().numpy().tobytes())
    return h.hexdigest()


def _token_batch(tok, rows, device, max_seq, error_positions=None):
    import torch
    seq=[];target=[]
    for row in rows:
        src=tok(row['source']+'\n',add_special_tokens=False)['input_ids']
        suf=tok(row['target']+'\n',add_special_tokens=False)['input_ids']
        if not src or not suf or len(src)+len(suf)>max_seq:
            raise ValueError('GLASS_TOKEN_LIMIT:'+row['id'])
        a=src+suf;y=[-100]*len(src)+suf
        if error_positions is not None:
            allowed=set(error_positions.get(row['id'],()))
            y=[v if i in allowed else -100 for i,v in enumerate(y)]
        seq.append(a);target.append(y)
    pad=tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    if pad is None:raise RuntimeError('GLASS_PAD_UNAVAILABLE')
    width=max(map(len,seq))
    x=torch.full((len(rows),width),int(pad),device=device,dtype=torch.long)
    y=torch.full_like(x,-100); mask=torch.zeros_like(x)
    for i,(a,b) in enumerate(zip(seq,target)):
        x[i,:len(a)]=torch.tensor(a,device=device)
        y[i,:len(b)]=torch.tensor(b,device=device)
        mask[i,:len(a)]=1
    return x,y,mask


def _groups(rows):
    groups=collections.defaultdict(list)
    for r in rows:groups[(r['rung'],r['residual'])].append(r)
    return groups


def _cpu_tensor(t):
    return t.detach().float().cpu()


class RelativeDeformationGlass:
    """Exact-zero outside a learned activation-mode neighborhood.

    Low-dimensional frozen random projection is used ONLY to look up modes.
    The trainable parameter is a small bank of additive hidden-vector deltas.
    New entries and all initially trained vectors start at zero. No learned
    answer numbers or text strings enter the inference path.
    """
    def __init__(self, module, *, hidden, keys, projection_dim=48, threshold=0.995, seed=42257):
        import torch
        import torch.nn.functional as F
        self.module=module
        self.hidden=int(hidden)
        self.threshold=float(threshold)
        if not (0.0 < threshold <= 1.0):raise ValueError('GLASS_BAD_THRESHOLD')
        if projection_dim<1:raise ValueError('GLASS_BAD_PROJECTION')
        g=torch.Generator(device='cpu').manual_seed(int(seed))
        projection=torch.randn(hidden,projection_dim,generator=g,dtype=torch.float32)/math.sqrt(hidden)
        if keys is None:keys=torch.empty(0,hidden)
        if keys.ndim!=2 or keys.shape[1]!=hidden:raise ValueError('GLASS_BAD_KEYS')
        with torch.no_grad():
            projected=F.normalize(keys.float()@projection,dim=-1) if len(keys) else torch.empty(0,projection_dim)
        # Create a real nn.Module so .parameters, .state_dict, optimizer and
        # registered module hooks follow normal torch semantics.
        class Impl(torch.nn.Module):
            def __init__(self, owner):
                super().__init__()
                self.register_buffer('projection',projection)
                self.register_buffer('keys',projected)
                self.corrections=torch.nn.Parameter(torch.zeros(len(keys),hidden,dtype=torch.float32))
                self.last_delta=None
                self.threshold=owner.threshold
            def forward(self,h):
                if self.keys.shape[0]==0:return h
                shape=h.shape
                import torch.nn.functional as F
                q=F.normalize(h.detach().float().reshape(-1,shape[-1]) @ self.projection,dim=-1)
                similarity=q @ self.keys.T
                strength,index=similarity.max(dim=-1)
                active=(strength>=self.threshold-1e-6).to(dtype=self.corrections.dtype)
                delta=(self.corrections[index] * active.unsqueeze(-1)).reshape(shape)
                self.last_delta=delta
                return h+delta.to(dtype=h.dtype)
        self.net=Impl(self)

    def hook(self, lm_head):
        def intervene(_module, inputs):
            return (self.net(inputs[0]),*inputs[1:])
        return lm_head.register_forward_pre_hook(intervene)


def _scan(bb,tok,rows,args, *, collect=False, phase='baseline'):
    """Full teacher-forced CE and error-token scan; never uses oracle output at inference.

    Collects a bounded number of candidate modes per (rung, signed delta)
    and records EVERY teacher-forced wrong-token position for the second pass.
    """
    import torch
    import torch.nn.functional as F
    bb.eval()
    by_group=collections.defaultdict(lambda: {'nll':0.,'tokens':0,'errors':0,'cases':0})
    errors={}; candidates=collections.defaultdict(list)
    candidate_seen=collections.Counter()
    selection_rng=random.Random(args.seed+811)
    capture=[]
    def capture_hook(_head,inputs):
        capture.append(inputs[0].detach())
    context=bb.get_output_embeddings().register_forward_pre_hook(capture_hook) if collect else None
    try:
        for index in range(0,len(rows),args.batch_size):
            batch=rows[index:index+args.batch_size]
            x,y,mask=_token_batch(tok,batch,args.device,args.max_seq)
            with torch.no_grad():
                out=bb(input_ids=x,attention_mask=mask,return_dict=True,use_cache=False)
                logits=out.logits[:,:-1].float()
                gold=y[:,1:]
                loss=F.cross_entropy(logits.reshape(-1,logits.shape[-1]),gold.reshape(-1),
                                     ignore_index=-100,reduction='none').reshape(gold.shape)
                valid=gold!=-100
                wrong=(logits.argmax(-1)!=gold)&valid
                for i,row in enumerate(batch):
                    g=by_group[(row['rung'],row['residual'])]
                    g['nll']+=float((loss[i]*valid[i]).sum())
                    g['tokens']+=int(valid[i].sum())
                    g['errors']+=int(wrong[i].sum());g['cases']+=1
                    if collect and bool(wrong[i].any()):
                        locations=(torch.nonzero(wrong[i]).flatten()+1).tolist()
                        errors[row['id']]=locations
                        # One mode per failing problem, selected deterministically
                        # within each signed-residual/rung group.
                        group=(row['rung'],row['residual'])
                        candidate_seen[group]+=1
                        seen=candidate_seen[group]
                        slot=(seen-1 if seen<=args.candidates_per_group else
                              selection_rng.randrange(seen))
                        if slot<args.candidates_per_group:
                            first=int(locations[0])-1
                            example=(row['id'],_cpu_tensor(capture[-1][i,first,:]))
                            if slot==len(candidates[group]):candidates[group].append(example)
                            else:candidates[group][slot]=example
            capture.clear()
            if args.progress_every and ((index//args.batch_size+1)%args.progress_every==0):
                print('GLASS_SCAN_PROGRESS='+json.dumps({'phase':phase,'cases':index+len(batch),
                     'total':len(rows)},sort_keys=True),flush=True)
    finally:
        if context is not None:context.remove()
    summary={f'T{r:02d}/delta{d:+d}':{'cases':v['cases'],'tokens':v['tokens'],
             'mean_loss':round(v['nll']/max(1,v['tokens']),7),
             'wrong_tokens':v['errors']} for (r,d),v in sorted(by_group.items())}
    totals={'cases':len(rows),'tokens':sum(v['tokens'] for v in by_group.values()),
            'wrong_tokens':sum(v['errors'] for v in by_group.values()),
            'mean_loss':round(sum(v['nll'] for v in by_group.values())/
                               max(1,sum(v['tokens'] for v in by_group.values())),7)}
    return {'summary':summary,'totals':totals,'error_positions':errors,'candidates':candidates}


def _choose_keys(candidates,budget):
    """Round-robin across depths and signed residuals; never favor huge T00 decks."""
    import torch
    if budget<1:raise ValueError('GLASS_MODE_BUDGET_MUST_BE_POSITIVE')
    groups=[list(v) for k,v in sorted(candidates.items()) if v]
    chosen=[]; ids=[]
    while groups and len(chosen)<budget:
        next_groups=[]
        for group in groups:
            if len(chosen)>=budget:break
            tag,key=group.pop(0);chosen.append(key);ids.append(tag)
            if group:next_groups.append(group)
        groups=next_groups
    if not chosen:return torch.empty(0,0),[]
    return torch.stack(chosen),ids


def _glossary_for_checkpoint(args,rows,scopes,source,snapshot):
    return {'schema':SCHEMA,'corpus_sha256':_cases_hash(rows),'scope':scopes,
            'parent_working':str(Path(source).resolve()),
            'source_snapshot_sha256':_file_hash(snapshot),'schedule':{
                'radii':args.radii,'max_rung':args.max_rung,'operand_limit':args.operand_limit,
                'per_cell':args.per_cell,'t00_mode':args.t00_mode,'seed':args.seed}}


def _load_source(bb,zero,source,sources,frozen):
    import torch
    item=torch.load(source,map_location='cpu',weights_only=True)
    if not isinstance(item.get('lora'),dict):raise RuntimeError('GLASS_SOURCE_MISSING_LORA')
    state=item.get('state',{})
    if state.get('source_sha256')!=sources or state.get('frozen_sha256')!=frozen:
        raise RuntimeError('GLASS_PARENT_MODEL_LINEAGE_MISMATCH')
    zero.restore_lora(bb,item['lora'])
    return item


def _store(path,payload):
    ladder._atomic_torch(path,payload)


def _backbone_train(bb,tok,zero,rows,args,*,partial_path,meta):
    import torch
    bb.eval();bb.requires_grad_(False)
    ps=[p for _,p in zero.lora_named_parameters(bb)]
    if not ps:raise RuntimeError('GLASS_BACKBONE_LORA_MISSING')
    for p in ps:p.requires_grad_(True)
    opt=torch.optim.AdamW(ps,lr=args.backbone_lr,weight_decay=0)
    policy={'epochs':args.backbone_epochs,'batch_size':args.batch_size,
            'lr':args.backbone_lr,'seed':args.seed}
    epoch_start=0;offset_start=0;steps=0
    if partial_path.exists():
        snap=torch.load(partial_path,map_location='cpu',weights_only=True)
        if snap['meta']!=meta or snap['policy']!=policy:
            raise RuntimeError('GLASS_BACKBONE_PARTIAL_DRIFT')
        zero.restore_lora(bb,snap['lora'])
        opt.load_state_dict(snap['optimizer'])
        epoch_start=snap['epoch'];offset_start=snap['next_offset'];steps=snap['steps']
    for epoch in range(epoch_start,args.backbone_epochs):
        order=list(range(len(rows)))
        random.Random(args.seed+epoch).shuffle(order)
        for offset in range(offset_start if epoch==epoch_start else 0,len(order),args.batch_size):
            batch=[rows[i] for i in order[offset:offset+args.batch_size]]
            opt.zero_grad(set_to_none=True);bb.train()
            loss=ladder._batch_loss(bb,tok,batch,args.device,args.max_seq)
            if not bool(torch.isfinite(loss)):raise RuntimeError('GLASS_BAD_BACKBONE_LOSS')
            loss.backward();torch.nn.utils.clip_grad_norm_(ps,1.)
            opt.step();steps+=1
            if steps%args.checkpoint_every==0 or offset+args.batch_size>=len(order):
                _store(partial_path,{'schema':SCHEMA,'meta':meta,'policy':policy,
                     'lora':zero.lora_state(bb),'optimizer':opt.state_dict(),
                     'epoch':epoch,'next_offset':offset+args.batch_size,'steps':steps})
            if args.progress_every and steps%args.progress_every==0:
                print('GLASS_BACKBONE_PROGRESS='+json.dumps({'epoch':epoch+1,
                     'steps':steps,'loss':round(float(loss.detach()),7)},sort_keys=True),flush=True)
    bb.eval();bb.requires_grad_(False)
    return steps


def _train_glass(bb,tok,glass,rows,error_positions,args,*,partial_path,meta):
    import torch
    bb.eval();bb.requires_grad_(False)
    net=glass.net.to(args.device)
    if not len(net.keys):return {'steps':0,'reason':'no_teacher_forced_error_modes'}
    opt=torch.optim.AdamW([net.corrections],lr=args.glass_lr,weight_decay=0)
    policy={'epochs':args.glass_epochs,'batch_size':args.batch_size,'lr':args.glass_lr,
            'l2':args.glass_l2,'seed':args.seed,'threshold':args.glass_threshold,
            'modes':len(net.keys),'projection_dim':args.glass_projection_dim}
    epoch_start=0;offset_start=0;steps=0
    if partial_path.exists():
        snap=torch.load(partial_path,map_location='cpu',weights_only=True)
        if snap['meta']!=meta or snap['policy']!=policy:
            raise RuntimeError('GLASS_CORRECTION_PARTIAL_DRIFT')
        net.load_state_dict(snap['glass']);opt.load_state_dict(snap['optimizer'])
        epoch_start=snap['epoch'];offset_start=snap['next_offset'];steps=snap['steps']
    head=bb.get_output_embeddings()
    with glass.hook(head):
        for epoch in range(epoch_start,args.glass_epochs):
            order=list(range(len(rows)))
            random.Random(args.seed+1001+epoch).shuffle(order)
            for offset in range(offset_start if epoch==epoch_start else 0,len(order),args.batch_size):
                batch=[rows[i] for i in order[offset:offset+args.batch_size]]
                # Visit the complete corpus, optimize only the tokens the
                # frozen baseline actually computed incorrectly.
                x,y,mask=_token_batch(tok,batch,args.device,args.max_seq,error_positions)
                if bool((y!=-100).any()):
                    opt.zero_grad(set_to_none=True)
                    out=bb(input_ids=x,attention_mask=mask,labels=y,return_dict=True,use_cache=False)
                    loss=out.loss
                    if net.last_delta is not None:
                        loss=loss+args.glass_l2*net.last_delta.float().square().mean()
                    if not bool(torch.isfinite(loss)):raise RuntimeError('GLASS_BAD_CORRECTION_LOSS')
                    loss.backward()
                    if net.corrections.grad is None:raise RuntimeError('GLASS_NO_CORRECTION_GRADIENT')
                    torch.nn.utils.clip_grad_norm_([net.corrections],1.)
                    opt.step();steps+=1
                    if args.progress_every and steps%args.progress_every==0:
                        print('GLASS_CORRECTION_PROGRESS='+json.dumps({'epoch':epoch+1,
                             'steps':steps,'loss':round(float(loss.detach()),7)},sort_keys=True),flush=True)
                if ((offset//args.batch_size+1)%args.checkpoint_every==0 or
                        offset+args.batch_size>=len(order)):
                    _store(partial_path,{'schema':SCHEMA,'meta':meta,'policy':policy,
                         'glass':net.state_dict(),'optimizer':opt.state_dict(),
                         'epoch':epoch,'next_offset':offset+args.batch_size,'steps':steps})
    return {'steps':steps}


def _sample_rollouts(bb,tok,rows,args,glass=None):
    """Independent unassisted greedy output evidence, bounded per cell."""
    selected=[]
    for _,group in sorted(_groups(rows).items()):
        k=min(args.greedy_cases_per_cell,len(group))
        if not k:continue
        # Spread sparse greedy checks over each signed-delta cell instead of
        # always checking the first/smallest operands.
        positions=[min(len(group)-1,int((j+0.5)*len(group)/k)) for j in range(k)]
        selected.extend(group[i] for i in positions)
    results={};head=bb.get_output_embeddings()
    import contextlib
    with glass.hook(head) if glass is not None else contextlib.nullcontext():
        for i in range(0,len(selected),args.eval_batch):
            batch=selected[i:i+args.eval_batch]
            outputs=ladder._greedy(bb,tok,[r['source'] for r in batch],
                                 args.device,args.max_tokens,args.max_new_tokens)
            for row,output in zip(batch,outputs):
                key=f'T{row["rung"]:02d}/delta{row["residual"]:+d}'
                z=results.setdefault(key,{'tested':0,'correct':0,'failures':[]})
                ok,_=ladder.correctness(row['source'],row['target'],output)
                z['tested']+=1;z['correct']+=int(ok)
                z.setdefault('passed_ids',[])
                z.setdefault('failed_ids',[])
                z['passed_ids' if ok else 'failed_ids'].append(row['id'])
                if not ok and len(z['failures'])<3:z['failures'].append({'id':row['id'],'output':output})
    return results


def _compare(pre,back,glass):
    comparison={}
    for key in sorted(set(pre)|set(back)|set(glass)):
        a=pre.get(key,{});b=back.get(key,{});c=glass.get(key,{})
        comparison[key]={'initial_loss':a.get('mean_loss'),
            'backbone_loss':b.get('mean_loss'),'glass_loss':c.get('mean_loss'),
            'backbone_wrong_tokens':b.get('wrong_tokens'),
            'glass_wrong_tokens':c.get('wrong_tokens')}
    return comparison


def run(args):
    import torch
    if args.stage=='status':
        path=Path(args.output)/'report.json'
        if not path.exists():raise FileNotFoundError('GLASS_REPORT_NOT_FOUND')
        result=json.loads(path.read_text(encoding='utf8'))
        print('GLASS_STATUS='+json.dumps(result,sort_keys=True),flush=True)
        return result
    if not args.stage1_checkpoint or not args.text_checkpoint or not args.ladder_working:
        raise ValueError('GLASS_REQUIRES_STAGE1_TEXT_AND_LADDER_WORKING')
    if args.backbone_epochs<0 or args.glass_epochs<1 or args.batch_size<1 or args.checkpoint_every<1:
        raise ValueError('GLASS_INVALID_EPOCHS_OR_BATCH_SIZE')
    if args.greedy_cases_per_cell<0 or args.case_cap<0 or args.glass_modes<1 or not 0<=args.max_rung<=8:
        raise ValueError('GLASS_BAD_EVALUATION_OR_CORPUS_SCOPE')
    out=Path(args.output)
    src=Path(args.ladder_working).resolve()
    ladder._outputs_safe(out,[src.parent,Path(args.stage1_checkpoint).parent,
                              Path(args.text_checkpoint).parent])
    snapshot=out/'source_snapshot.pt'
    if not snapshot.is_file():
        if not src.is_file():raise FileNotFoundError('GLASS_LADDER_WORKING_MISSING')
        if out.is_dir() and any(out.iterdir()):
            raise RuntimeError('GLASS_REFUSES_NONEMPTY_OUTPUT_WITHOUT_SOURCE_SNAPSHOT')
        out.mkdir(parents=True,exist_ok=True)
        temp=snapshot.with_name('source_snapshot.pt.tmp')
        shutil.copy2(src,temp)
        temp.replace(snapshot)
    # All later stages use the same IMMUTABLE source snapshot even if the
    # original ladder continues training in its own directory.
    rows,scopes=corpus_for_schedule(args.radii,args.max_rung,args.operand_limit,
                                   args.per_cell,args.seed,args.t00_mode)
    if args.case_cap:
        # Optional smoke-only speed limit; NEVER label it the full corpus.
        groups=_groups(rows);selected=[]
        for key in sorted(groups):selected.extend(groups[key][:args.case_cap])
        rows=selected
    if not rows:raise RuntimeError('GLASS_CORPUS_EMPTY')
    meta=_glossary_for_checkpoint(args,rows,scopes,src,snapshot)
    out.mkdir(parents=True,exist_ok=True)
    bb,tok,sources,frozen,dirs,zero=ladder._fresh_parent(args)
    ladder._outputs_safe(out,dirs)
    original=_load_source(bb,zero,snapshot,sources,frozen)
    report_path=out/'report.json'
    report=json.loads(report_path.read_text()) if report_path.is_file() else {
        'schema':SCHEMA,'meta':meta,'corpus_cases':len(rows),'full_corpus':not bool(args.case_cap)}
    if report['meta']!=meta or report['corpus_cases']!=len(rows):
        raise RuntimeError('GLASS_RESUME_CORPUS_OR_SOURCE_CHANGED')
    baseline_path=out/'baseline.pt';glass_path=out/'glass.pt'
    if args.stage in ('backbone','both') and not baseline_path.exists():
        initial=_scan(bb,tok,rows,args,phase='initial')
        report['initial']=initial['totals'];report['initial_per_cell']=initial['summary']
        phase_start=time.perf_counter()
        steps=_backbone_train(bb,tok,zero,rows,args,
                   partial_path=out/'backbone_partial.pt',meta=meta)
        back=_scan(bb,tok,rows,args,collect=True,phase='backbone')
        report['backbone']=back['totals'];report['backbone_per_cell']=back['summary']
        report['backbone_steps']=steps
        report['backbone_training_seconds']=round(time.perf_counter()-phase_start,3)
        report['backbone_greedy']=_sample_rollouts(bb,tok,rows,args)
        # Never write to the running ladder source. The baseline checkpoint is
        # written only once; no glass trainable parameters are present yet.
        _store(baseline_path,{'schema':SCHEMA,'meta':meta,'lora':zero.lora_state(bb),
                              'error_positions':back['error_positions'],
                              'mode_candidates':{str(k):[(i,x) for i,x in v]
                                                 for k,v in back['candidates'].items()},
                              'source_sha256':sources,'frozen_sha256':frozen})
        ladder._atomic_json(report_path,report)
        (out/'backbone_partial.pt').unlink(missing_ok=True)
        print('GLASS_BACKBONE_FROZEN='+json.dumps({'cases':len(rows),'steps':steps,
            'mean_loss':back['totals']['mean_loss'],'wrong_tokens':back['totals']['wrong_tokens'],
            'path':str(baseline_path)},sort_keys=True),flush=True)
    if args.stage=='backbone':return report
    if not baseline_path.exists():raise RuntimeError('GLASS_RUN_BACKBONE_PHASE_FIRST')
    baseline=torch.load(baseline_path,map_location='cpu',weights_only=True)
    if baseline.get('meta')!=meta or baseline.get('source_sha256')!=sources or baseline.get('frozen_sha256')!=frozen:
        raise RuntimeError('GLASS_BASELINE_DRIFT')
    zero.restore_lora(bb,baseline['lora'])
    bb.eval();bb.requires_grad_(False)
    frozen_before=_lora_digest(bb,zero)
    # The native LM head remains frozen. We learn deformation of its INPUT.
    head=bb.get_output_embeddings()
    if head is None:raise RuntimeError('GLASS_HEAD_UNAVAILABLE')
    weight=getattr(head,'weight',None)
    if weight is None:raise RuntimeError('GLASS_HEAD_WEIGHT_UNAVAILABLE')
    hidden=int(weight.shape[1])
    if glass_path.exists():
        saved=torch.load(glass_path,map_location='cpu',weights_only=True)
        if saved.get('meta')!=meta:raise RuntimeError('GLASS_SAVED_BANK_DRIFT')
        keys=saved['keys']
    else:
        candidates={}
        for key,value in baseline['mode_candidates'].items():
            # Only metadata parsing, never reinterpret arbitrary source text.
            k=key.strip('()').split(', ')
            pair=(int(k[0]),int(k[1]))
            candidates[pair]=value
        keys,ids=_choose_keys(candidates,args.glass_modes)
        if keys.numel()==0:keys=torch.empty(0,hidden)
    glass=RelativeDeformationGlass(None,hidden=hidden,keys=keys,
        projection_dim=args.glass_projection_dim,threshold=args.glass_threshold,seed=args.seed)
    net=glass.net.to(args.device)
    if glass_path.exists():
        if saved['threshold']!=args.glass_threshold or saved['projection_dim']!=args.glass_projection_dim:
            raise RuntimeError('GLASS_BANK_POLICY_DRIFT')
        net.load_state_dict(saved['glass'])
    else:
        assert torch.count_nonzero(net.corrections)==0
        if keys.numel():
            # A pre-training test ensures *exact* preservation of the backbone.
            x,y,mask=_token_batch(tok,rows[:1],args.device,args.max_seq)
            with torch.no_grad():
                ordinary=bb(input_ids=x,attention_mask=mask,return_dict=True,use_cache=False).logits
                with glass.hook(head):
                    untouched=bb(input_ids=x,attention_mask=mask,return_dict=True,use_cache=False).logits
            if not torch.equal(ordinary,untouched):
                raise RuntimeError('GLASS_INITIAL_ZERO_INVARIANT_FAILED')
        phase_start=time.perf_counter()
        trained=_train_glass(bb,tok,glass,rows,baseline['error_positions'],args,
                   partial_path=out/'glass_partial.pt',meta=meta)
        _store(glass_path,{'schema':SCHEMA,'meta':meta,'glass':net.state_dict(),
             'keys':keys,'threshold':args.glass_threshold,
             'projection_dim':args.glass_projection_dim,'glass_steps':trained['steps'],
             'backbone_lora_sha256':frozen_before})
        (out/'glass_partial.pt').unlink(missing_ok=True)
        report['glass_steps']=trained['steps'];report['glass_modes']=len(keys)
        report['glass_training_seconds']=round(time.perf_counter()-phase_start,3)
    if _lora_digest(bb,zero)!=frozen_before:
        raise RuntimeError('GLASS_SECOND_PASS_CHANGED_FROZEN_QWEN')
    # Full-corpus teacher-forced metrics on the exact same Qwen checkpoint.
    back=_scan(bb,tok,rows,args,phase='backbone_recheck')
    with glass.hook(head):
        corrected=_scan(bb,tok,rows,args,phase='glass')
    report['backbone']=back['totals'];report['backbone_per_cell']=back['summary']
    report['glass']=corrected['totals'];report['glass_per_cell']=corrected['summary']
    report['comparison']=_compare(report.get('initial_per_cell',{}),back['summary'],corrected['summary'])
    report['glass_greedy']=_sample_rollouts(bb,tok,rows,args,glass=glass)
    report['backbone_greedy']=_sample_rollouts(bb,tok,rows,args)
    baseline_ok={id for r in report['backbone_greedy'].values() for id in r['passed_ids']}
    glass_ok={id for r in report['glass_greedy'].values() for id in r['passed_ids']}
    report['greedy_effect']={'corrected':len(glass_ok-baseline_ok),
          'new_errors':len(baseline_ok-glass_ok),
          'net_corrections':len(glass_ok-baseline_ok)-len(baseline_ok-glass_ok),
          'tested':sum(x['tested'] for x in report['glass_greedy'].values())}
    report['glass_trainable_parameters']=net.corrections.numel()
    report['glass_stored_floats']=net.corrections.numel()+net.keys.numel()+net.projection.numel()
    report['qwen_trainable_lora_parameters_frozen']=sum(v.numel() for v in baseline['lora'].values())
    report['zero_default_enforced']=True
    report['all_glass_training_backbone_frozen']=True
    report['note']='Teacher-forced CE across selected corpus; unassisted greedy is sampled by rung/delta.'
    ladder._atomic_json(report_path,report)
    print('GLASS_SMOKE_RESULT='+json.dumps({'cases':len(rows),'full_corpus':not bool(args.case_cap),
          'baseline_loss':back['totals']['mean_loss'],'glass_loss':corrected['totals']['mean_loss'],
          'baseline_wrong_tokens':back['totals']['wrong_tokens'],
          'glass_wrong_tokens':corrected['totals']['wrong_tokens'],
          'glass_modes':report.get('glass_modes',len(keys)),'glass_steps':report.get('glass_steps',0),
          'greedy_effect':report['greedy_effect'],
          'glass_trainable_parameters':report['glass_trainable_parameters'],
          'qwen_trainable_lora_parameters_frozen':report['qwen_trainable_lora_parameters_frozen'],
          'path':str(report_path)},sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('both','backbone','glass','status'),default='backbone',
                   help='backbone first (default), then glass; both is an explicit auto-cutover')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint')
    p.add_argument('--text-checkpoint')
    p.add_argument('--ladder-working',help='Existing ladder working.pt; never modified')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_deformation_glass_smoke_v1'))
    p.add_argument('--radii',default='0,1,2,3,4')
    p.add_argument('--max-rung',type=int,default=8)
    p.add_argument('--operand-limit',type=int,default=1024)
    p.add_argument('--per-cell',type=int,default=8)
    p.add_argument('--t00-mode',choices=('exhaustive','sampled'),default='exhaustive')
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--eval-batch',type=int,default=8)
    p.add_argument('--case-cap',type=int,default=0,help='Optional per-(rung,signed-delta) cap for fast smoke; 0 = full corpus')
    p.add_argument('--backbone-epochs',type=int,default=1)
    p.add_argument('--glass-epochs',type=int,default=1)
    p.add_argument('--backbone-lr',type=float,default=5e-5)
    p.add_argument('--glass-lr',type=float,default=0.005)
    p.add_argument('--glass-l2',type=float,default=0.001)
    p.add_argument('--glass-modes',type=int,default=128)
    p.add_argument('--candidates-per-group',type=int,default=4)
    p.add_argument('--glass-projection-dim',type=int,default=48)
    p.add_argument('--glass-threshold',type=float,default=0.995)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--greedy-cases-per-cell',type=int,default=2)
    p.add_argument('--progress-every',type=int,default=100)
    p.add_argument('--checkpoint-every',type=int,default=100,
                   help='Save restartable partial phase state every N batches/steps')
    # Pass through the same original-model loading options as the ladder.
    p.add_argument('--baseline');p.add_argument('--backbone')
    p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    return run(args)


if __name__=='__main__':main()
