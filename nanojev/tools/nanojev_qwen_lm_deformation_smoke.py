#!/usr/bin/env python3
"""Edge-first paired A/B *continuous* deformation glass on a frozen numeric-terminal Qwen ladder.

A: h' = h + f(H), B: h' = h + sigmoid(g(H))*f(H).
Both have exactly zero-initialized residual output; neither contains a mode bank.
The glass sees ONLY frozen Qwen activations at inference (no gold, IDs, logits).

Stages: probe (immutable snapshot + frozen greedy frontier census), train (paired
restartable bounded rounds), run (probe + train), audit (actual greedy comparison),
status (read-only). No changes to the source working.pt or its LoRA weights.

Legacy mode-bank code is archived in nanojev_qwen_lm_deformation_legacy.py;
legacy refine imports it explicitly. Do not load mode-bank glass.pt here.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import random
import shutil
import time
from pathlib import Path

from tools import nanojev_qwen_lm_radius_ladder as ladder

SCHEMA = 'nanojev.qwen.glass-edge-ab.v1'


def _sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def _emit(tag, payload):
    print(tag+'='+json.dumps(payload,sort_keys=True),flush=True)


def _atomic_json(path,payload):
    ladder._atomic_json(path,payload)


def _read_checkpoint_metadata(path):
    import torch
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=ladder.SCHEMA or not isinstance(ck.get('state'),dict):
        raise RuntimeError('GLASS_AB_SOURCE_NOT_LADDER_CHECKPOINT')
    state=ck['state'];cfg=state.get('config')
    if not isinstance(cfg,dict) or cfg.get('terminal_mode')!='numeric':
        raise RuntimeError('GLASS_AB_REQUIRES_NUMERIC_TERMINAL_CHECKPOINT')
    if state.get('config_hash')!=ladder._hash(cfg):
        raise RuntimeError('GLASS_AB_SOURCE_CONFIG_HASH_MISMATCH')
    if not isinstance(ck.get('lora'),dict) or not ck['lora']:
        raise RuntimeError('GLASS_AB_SOURCE_NO_LORA')
    return state


def _snapshot(args):
    out=Path(args.output).resolve()
    source=Path(args.ladder_working).resolve()
    parents=[source.parent]
    if args.stage1_checkpoint:parents.append(Path(args.stage1_checkpoint).resolve().parent)
    if args.text_checkpoint:parents.append(Path(args.text_checkpoint).resolve().parent)
    ladder._outputs_safe(out,parents)
    path=out/'source_snapshot.pt';meta_path=out/'source_meta.json'
    if path.exists()!=meta_path.exists():raise RuntimeError('GLASS_AB_PARTIAL_SOURCE_SNAPSHOT')
    if not path.exists():
        if not source.is_file():raise FileNotFoundError('GLASS_AB_LADDER_WORKING_MISSING')
        if out.exists() and any(out.iterdir()):raise RuntimeError('GLASS_AB_OUTPUT_NOT_EMPTY')
        out.mkdir(parents=True,exist_ok=True)
        temp=out/'source_snapshot.pt.tmp'
        try:
            shutil.copy2(source,temp)  # source ladder writes atomic checkpoint replacements
            state=_read_checkpoint_metadata(temp)
            digest=_sha(temp)
            os.replace(temp,path)
            _atomic_json(meta_path,{'schema':SCHEMA,'source':str(source),
                        'snapshot_sha256':digest,'source_state':state})
        finally:
            if temp.exists():temp.unlink()
    meta=json.loads(meta_path.read_text(encoding='utf8'))
    if meta.get('schema')!=SCHEMA or meta.get('source')!=str(source):
        raise RuntimeError('GLASS_AB_SOURCE_LINEAGE_MISMATCH')
    if _sha(path)!=meta['snapshot_sha256']:
        raise RuntimeError('GLASS_AB_SOURCE_SNAPSHOT_DRIFT')
    return path,meta


def _frontier_spec(state,args):
    cfg=state['config']
    current_t=max(0,min(8,int(state.get('rung',0))))
    radii=cfg['radii']; index=max(0,min(len(radii)-1,int(state.get('radius_index',0))))
    radius=int(radii[index]);limit=int(cfg['operand_limit'])
    signed_values={0}
    for d in range(max(0,radius-args.inner_shells),min(limit,radius+args.outer_shells)+1):
        signed_values.add(d);signed_values.add(-d)
    depths={current_t}
    for d in range(1,args.depth_shells+1):
        if current_t-d>=0:depths.add(current_t-d)
        if current_t+d<=8:depths.add(current_t+d)
    # Include T00 near the magnitude boundary; NOT an exhaustive origin-first sweep.
    depths.add(0)
    return {'rungs':sorted(depths),'residuals':sorted(signed_values),
            'current_rung':current_t,'current_radius':radius,
            'operand_limit':limit,'numeric_contract':True}


def _sample_candidate_rows(state,args):
    cfg=state['config'];frontier=_frontier_spec(state,args)
    values=set(frontier['residuals']);max_rad=max(map(abs,values))
    grouped=collections.defaultdict(list)
    # Deterministically draw bounded cells from all nearby rungs and magnitudes.
    for rung in frontier['rungs']:
        rows,scope=ladder.deck(rung,max_rad,frontier['operand_limit'],
                    per_cell=args.probe_per_cell,seed=int(cfg['seed'])+args.probe_seed,
                    t00_mode='sampled',terminal_mode='numeric')
        for row in rows:
            if row['residual'] not in values:continue
            # Split by rung, signed result, and magnitude band.
            magnitude=max(abs(n) for n in row['leaves'])
            band=next((name for lo,hi,name in ladder.bands(frontier['operand_limit']) if lo<=magnitude<=hi),'other')
            grouped[(rung,row['residual'],band)].append(row)
    rng=random.Random(args.probe_seed)
    for group in grouped.values():rng.shuffle(group)
    selected=[]
    groups=sorted(grouped,key=lambda k:(abs(k[0]-frontier['current_rung']),
        abs(abs(k[1])-frontier['current_radius']),k[2],k[1]))
    while groups and len(selected)<args.probe_limit:
        pending=[]
        for k in groups:
            if len(selected)>=args.probe_limit:break
            selected.append(grouped[k].pop())
            if grouped[k]:pending.append(k)
        groups=pending
    # Never duplicate a source across sampled cells; the emitted deck is immutable.
    unique={r['source']:r for r in selected}
    selected=list(unique.values())
    frontier['sampled_cases']=len(selected)
    frontier['candidate_cells']=len(grouped)
    frontier['deck_hash']=ladder.deck_fingerprint(selected)
    return selected,frontier


class ContinuousGlass:
    """Single residual function, optional *soft* learned gate; no discrete modes.

    Last deformation projection is zero-initialized. The gate version starts
    with zero residual too, despite its sigmoid being nonzero. Input features
    come from frozen decoder hidden states at selected depths only.
    """
    def __init__(self,hidden=1024,feature_layers=4,width=192,gated=False):
        import torch
        self.hidden=int(hidden);self.feature_layers=int(feature_layers)
        self.width=int(width);self.gated=bool(gated)
        h=self.hidden;n=self.feature_layers
        class Net(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.norm=torch.nn.LayerNorm(h*n)
                self.shared=torch.nn.Linear(h*n,width)
                self.delta=torch.nn.Linear(width,h)
                torch.nn.init.zeros_(self.delta.weight)
                torch.nn.init.zeros_(self.delta.bias)
                if gated:
                    self.gate=torch.nn.Linear(width,1)
                    torch.nn.init.zeros_(self.gate.weight)
                    torch.nn.init.constant_(self.gate.bias,-2.0)
            def forward(self,features):
                import torch.nn.functional as F
                x=F.gelu(self.shared(self.norm(features.float())))
                delta=self.delta(x)
                probability=torch.sigmoid(self.gate(x)) if gated else None
                effective=delta*probability if gated else delta
                return effective,probability
        self.net=Net()

    def apply(self,h,features):
        import torch
        effective,prob=self.net(features)
        return h+effective.to(dtype=h.dtype),effective,prob


def _features(output,count):
    """Capture live multi-depth Qwen activations; never logits or target IDs."""
    import torch
    states=output.hidden_states
    if states is None or len(states)<2:raise RuntimeError('GLASS_AB_DECODER_NO_HIDDEN_STATES')
    ids=[round(i*(len(states)-1)/(count-1)) for i in range(count)] if count>1 else [len(states)-1]
    return torch.cat([states[i][:,-1,:].detach().float() for i in ids],dim=-1)


def _token_ids(tok,text):
    return list(tok(text,add_special_tokens=False)['input_ids'])


def _rollout(bb,tok,row,args,glass=None,*,capture_first_wrong=False):
    """Actual unassisted greedy tokens, one case, identical A/B protocol.

    If requested, return a pre-error feature tuple for training. The target
    is used ONLY by this offline trainer/scorekeeper, never glass inference.
    """
    import torch
    dec=bb.get_decoder();head=bb.get_output_embeddings()
    prefix=_token_ids(tok,row['source']+'\n')
    gold=_token_ids(tok,row['target']+'\n')
    if not prefix or not gold:raise RuntimeError('GLASS_AB_EMPTY_TOKENIZATION')
    if len(prefix)+args.max_new_tokens>args.max_tokens:
        raise RuntimeError('GLASS_AB_CONTEXT_TOO_LONG:'+row['id'])
    ids=list(prefix);generated=[];wrong_state=None;stopped=False
    norms=[];gate_values=[]
    eos=tok.eos_token_id
    eos_set={int(eos)} if isinstance(eos,int) else set(eos or ())
    with torch.no_grad():
        for step in range(args.max_new_tokens):
            x=torch.tensor([ids],device=args.device,dtype=torch.long)
            o=dec(input_ids=x,attention_mask=torch.ones_like(x),
                  return_dict=True,use_cache=False,output_hidden_states=bool(glass or capture_first_wrong))
            h=o.last_hidden_state[:,-1,:]
            feats=_features(o,args.feature_layers) if (glass or capture_first_wrong) else None
            if glass is not None:
                h,delta,gate_probability=glass.apply(h,feats)
                norms.append(float(delta.float().norm(dim=-1).mean().item()))
                if gate_probability is not None:
                    gate_values.append(float(gate_probability.mean().item()))
            weight=getattr(head,'weight',None)
            scores=head(h.to(dtype=weight.dtype if weight is not None else h.dtype))
            token=int(scores.argmax(dim=-1).item())
            if capture_first_wrong and wrong_state is None and (step>=len(gold) or token!=gold[step]):
                wrong_state={'features':feats.cpu().squeeze(0),
                             'hidden':o.last_hidden_state[:,-1,:].float().cpu().squeeze(0),
                             'gold':int(gold[step]) if step<len(gold) else None,
                             'prefix':list(ids),'step':step}
            if token in eos_set:
                stopped=True;break
            generated.append(token);ids.append(token)
            if '\n' in tok.decode(generated,skip_special_tokens=True) or '\r' in tok.decode(generated,skip_special_tokens=True):
                stopped=True;break
    output=tok.decode(generated,skip_special_tokens=True)
    ok,_=ladder.correctness(row['source'],row['target'],output)
    # Tokenization differences should not allow a false claim of exact correctness.
    if not ok and capture_first_wrong and wrong_state is None:
        wrong_state={'features':feats.cpu().squeeze(0),'hidden':o.last_hidden_state[:,-1,:].float().cpu().squeeze(0),
                     'gold':None,'prefix':list(ids),'step':len(generated)}
    return {'id':row['id'],'correct':bool(ok),'output':output,
            'wrong_state':wrong_state,'steps':len(generated),
            'mean_delta_norm':sum(norms)/max(1,len(norms)),
            'max_delta_norm':max(norms,default=0.),
            'mean_gate_probability':sum(gate_values)/len(gate_values) if gate_values else None}


def _capture_gold_position(bb,tok,wrong,args):
    """Recompute exact frozen features for the chosen pre-divergence prefix."""
    import torch
    with torch.no_grad():
        ids=torch.tensor([wrong['prefix']],device=args.device,dtype=torch.long)
        out=bb.get_decoder()(input_ids=ids,attention_mask=torch.ones_like(ids),
                              use_cache=False,return_dict=True,output_hidden_states=True)
    return out.last_hidden_state[:,-1,:].float().detach(),_features(out,args.feature_layers).detach()


def _load_model(args,snapshot,meta):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    # The original parent path and frozen hashes are checked just like ladder resume.
    bb,tok,sources,frozen,dirs,_=ladder._fresh_parent(args)
    ladder._outputs_safe(Path(args.output),dirs)
    state=meta['source_state']
    if state.get('source_sha256')!=sources or state.get('frozen_sha256')!=frozen:
        raise RuntimeError('GLASS_AB_PARENT_LINEAGE_MISMATCH')
    ck=torch.load(snapshot,map_location='cpu',weights_only=True)
    zero.restore_lora(bb,ck['lora']);bb.eval();bb.requires_grad_(False)
    return bb,tok,zero


def _policy(args,frontier,meta):
    return {'schema':SCHEMA,'source_sha256':meta['snapshot_sha256'],
            'deck_sha256':frontier['deck_hash'],
            'architecture':{'width':args.glass_width,'feature_layers':args.feature_layers},
            'training':{'seed':args.seed,'learning_rate':args.lr,'preserve_weight':args.preserve_weight,
                        'delta_l2':args.delta_l2,'gate_loss':args.gate_loss,
                        'steps_per_round':args.steps_per_round,
                        'round_eval_limit':args.round_eval_limit,'full_eval_every':args.full_eval_every},
            'inference':{'max_tokens':args.max_tokens,'max_new_tokens':args.max_new_tokens}}


def _last_committed_pair(out):
    """Use the latest completed TWO-branch checkpoint; ignore torn writes."""
    roots=sorted((out/'rounds').glob('round_*')) if (out/'rounds').exists() else []
    complete=[r for r in roots if (r/'complete.json').is_file() and
              (r/'glass_A.pt').is_file() and (r/'glass_B.pt').is_file()]
    return complete[-1] if complete else out


def _load_glasses(args,model_hidden,out,policy,*,require=False):
    import torch
    glasses={};opts={};history={}
    src=_last_committed_pair(out)
    for name,gated in [('A',False),('B',True)]:
        obj=ContinuousGlass(model_hidden,args.feature_layers,args.glass_width,gated)
        obj.net.to(args.device)
        path=src/f'glass_{name}.pt'
        saved=None
        if path.exists():
            saved=torch.load(path,map_location='cpu',weights_only=True)
            if saved.get('policy')!=policy:raise RuntimeError('GLASS_AB_POLICY_DRIFT:'+name)
            obj.net.load_state_dict(saved['weights'])
            history[name]=saved.get('history',[])
        elif require:raise FileNotFoundError('GLASS_AB_MISSING_'+name)
        opts[name]=torch.optim.AdamW(obj.net.parameters(),lr=args.lr,weight_decay=0.)
        if saved is not None and saved.get('optimizer') is not None:
            opts[name].load_state_dict(saved['optimizer'])
            for state in opts[name].state.values():
                for k,v in state.items():
                    if torch.is_tensor(v):state[k]=v.to(args.device)
        glasses[name]=obj
    if len(history.get('A',[]))!=len(history.get('B',[])):
        raise RuntimeError('GLASS_AB_TORN_PAIRED_CHECKPOINT')
    return glasses,opts,history


def _save_glasses(out,glasses,opts,history,policy):
    import torch
    number=len(history['A'])
    if number!=len(history['B']):raise RuntimeError('GLASS_AB_UNPAIRED_ROUND')
    round_dir=out/'rounds'/f'round_{number:05d}'
    round_dir.mkdir(parents=True,exist_ok=True)
    for name in ('A','B'):
        ladder._atomic_torch(round_dir/f'glass_{name}.pt',{'schema':SCHEMA,'policy':policy,
                  'weights':{k:v.detach().cpu() for k,v in glasses[name].net.state_dict().items()},
                  'optimizer':opts[name].state_dict(),'history':history[name]})
    # Round becomes visible for resume only AFTER both artifacts exist.
    _atomic_json(round_dir/'complete.json',{'schema':SCHEMA,'round':number,
                 'policy':policy,'branches':['A','B']})
    # Convenience names are not authoritative for recovery.
    for name in ('A','B'):
        shutil.copy2(round_dir/f'glass_{name}.pt',out/f'glass_{name}.pt')


def _probe(bb,tok,rows,args,*,glass=None,limit=0,show_progress=False):
    results=[]
    for i,row in enumerate(rows[:limit or None]):
        answer=_rollout(bb,tok,row,args,glass=glass)
        results.append({k:answer[k] for k in ('id','correct','output','mean_delta_norm',
                       'max_delta_norm','mean_gate_probability')})
        if show_progress and (i+1)%args.progress_every==0:
            _emit('GLASS_AB_PROBE_PROGRESS',{'tested':i+1,'total':len(rows[:limit or None])})
    return results


def _compare(rows,baseline,a,b):
    cases={r['id']:r for r in rows}
    pre={r['id']:r for r in baseline};ra={r['id']:r for r in a};rb={r['id']:r for r in b}
    def result(group):
        fixed=[k for k in cases if not pre[k]['correct'] and group[k]['correct']]
        regressed=[k for k in cases if pre[k]['correct'] and not group[k]['correct']]
        changed=[k for k in cases if pre[k]['output']!=group[k]['output']]
        guarded=[group[k] for k in cases if pre[k]['correct']]
        return {'correct':sum(v['correct'] for v in group.values()),'tested':len(cases),
                'fixed':len(fixed),'regressed':len(regressed),
                'changed_outputs':len(changed),'fixed_ids':fixed,'regressed_ids':regressed,
                'baseline_correct_interventions':sum(v.get('max_delta_norm',0)>1e-6 for v in guarded),
                'baseline_correct_mean_delta_norm':round(sum(v.get('mean_delta_norm',0) for v in guarded)/max(1,len(guarded)),8),
                'baseline_correct_max_delta_norm':round(max((v.get('max_delta_norm',0) for v in guarded),default=0.),8),
                'mean_gate_probability':round(sum(v.get('mean_gate_probability') or 0 for v in group.values())/
                           max(1,sum(v.get('mean_gate_probability') is not None for v in group.values())),6)
                     if any(v.get('mean_gate_probability') is not None for v in group.values()) else None}
    return {'baseline':{'correct':sum(v['correct'] for v in baseline),'tested':len(cases)},
            'A':result(ra),'B':result(rb)}


def _train_paired_step(glass,opt,bb,tok,row,good_row,args,rng):
    """One same-case A/B step; math targets enter loss ONLY, never glass inputs.

    Corrected cases constrain the residual toward zero. Failed cases learn the
    first truly incorrect greedy token; baseline-correct cases provide replay.
    """
    import torch
    import torch.nn.functional as F
    actual=_rollout(bb,tok,row,args,glass=glass,capture_first_wrong=True)
    opt.zero_grad(set_to_none=True)
    terms=[]
    if not actual['correct'] and actual['wrong_state'] is not None and actual['wrong_state']['gold'] is not None:
        h,feats=_capture_gold_position(bb,tok,actual['wrong_state'],args)
        changed,delta,prob=glass.apply(h,feats)
        head=bb.get_output_embeddings()
        weight=getattr(head,'weight',None)
        logits=head(changed.to(dtype=weight.dtype if weight is not None else changed.dtype)).float()
        target=torch.tensor([actual['wrong_state']['gold']],device=args.device,dtype=torch.long)
        terms.append(F.cross_entropy(logits,target))
        terms.append(args.delta_l2*delta.square().mean())
        if glass.gated:terms.append(args.gate_loss*F.binary_cross_entropy(prob,torch.ones_like(prob)))
    else:
        # A correct answer is itself acquired competency, even if it was once wrong.
        src=_token_ids(tok,row['source']+'\n');expected=_token_ids(tok,row['target']+'\n')
        prefix=src+expected[:rng.randrange(len(expected))]
        h,feats=_capture_gold_position(bb,tok,{'prefix':prefix},args)
        _,delta,prob=glass.apply(h,feats)
        terms.append(args.preserve_weight*delta.square().mean())
        if glass.gated:terms.append(args.gate_loss*F.binary_cross_entropy(prob,torch.zeros_like(prob)))
    if good_row is not None:
        # Replay a uniformly chosen *token position* of an originally correct
        # sequence, not just the first token; preserve the entire output path.
        src=_token_ids(tok,good_row['source']+'\n');expected=_token_ids(tok,good_row['target']+'\n')
        prefix=src+expected[:rng.randrange(len(expected))]
        h,feats=_capture_gold_position(bb,tok,{'prefix':prefix},args)
        _,delta,prob=glass.apply(h,feats)
        terms.append(args.preserve_weight*delta.square().mean())
        if glass.gated:terms.append(args.gate_loss*F.binary_cross_entropy(prob,torch.zeros_like(prob)))
    loss=sum(terms)
    if not bool(torch.isfinite(loss)):raise RuntimeError('GLASS_AB_BAD_LOSS')
    loss.backward()
    torch.nn.utils.clip_grad_norm_(glass.net.parameters(),1.)
    opt.step()
    return {'wrong_before':not actual['correct'],'loss':round(float(loss.detach()),6)}


def _round_subset(rows,baseline,args,round_id):
    """Fast matched A/B evaluation, with both sides of the edge represented."""
    if args.round_eval_limit==0 or len(rows)<=args.round_eval_limit:return rows
    rng=random.Random(args.seed+round_id*131)
    failed=[r for r in rows if not baseline[r['id']]['correct']]
    good=[r for r in rows if baseline[r['id']]['correct']]
    rng.shuffle(failed);rng.shuffle(good)
    # Split roughly 70/30 toward actual errors, preserving correct neighbors.
    n_fail=min(len(failed),max(1,round(args.round_eval_limit*.7)))
    chosen=failed[:n_fail]
    rest=args.round_eval_limit-len(chosen)
    chosen+=good[:rest]
    if len(chosen)<args.round_eval_limit:chosen+=failed[n_fail:n_fail+args.round_eval_limit-len(chosen)]
    rng.shuffle(chosen)
    return chosen


def _run(args):
    import torch
    args.device=str(args.device)
    if args.progress_every<1 or args.probe_limit<1 or args.probe_per_cell<1 or args.max_new_tokens<1 or args.round_eval_limit<0 or args.audit_limit<0 or args.inner_shells<0 or args.outer_shells<1 or args.depth_shells<0:
        raise ValueError('GLASS_AB_BAD_PROBE_ARGUMENTS')
    if args.stage not in ('status',) and (not args.stage1_checkpoint or not args.text_checkpoint):
        raise ValueError('GLASS_AB_NEEDS_PARENT_CHECKPOINTS')
    out=Path(args.output).resolve()
    if args.stage=='status':
        for name in ('frontier.json','comparison.json'):
            path=out/name
            if path.exists():_emit('GLASS_AB_STATUS_'+name.split('.')[0].upper(),json.loads(path.read_text()))
        return
    snapshot,meta=_snapshot(args)
    rows,frontier=_sample_candidate_rows(meta['source_state'],args)
    if not rows:raise RuntimeError('GLASS_AB_NO_PROBE_CASES')
    out.mkdir(parents=True,exist_ok=True)
    policy=_policy(args,frontier,meta)
    deck_path=out/'frontier.json'
    if deck_path.exists():
        previous=json.loads(deck_path.read_text())
        if previous['policy']!=policy:raise RuntimeError('GLASS_AB_FRONTIER_DRIFT')
    else:
        _atomic_json(deck_path,{'policy':policy,'frontier':frontier,'rows':rows})
    bb,tok,zero=_load_model(args,snapshot,meta)
    args._tok=tok
    hidden=int(bb.get_output_embeddings().weight.shape[1])
    glasses,opts,history=_load_glasses(args,hidden,out,policy,require=args.stage=='audit')
    original_lora_digest=hashlib.sha256()
    for name,t in sorted(zero.lora_state(bb).items()):
        original_lora_digest.update(name.encode())
        original_lora_digest.update(t.detach().cpu().contiguous().float().numpy().tobytes())
    original_lora_digest=original_lora_digest.hexdigest()
    # Prove that both zero-initialized glasses change no hidden outputs.
    for glass in glasses.values():
        if not (out/'glass_A.pt').exists() or not (out/'glass_B.pt').exists():
            a=torch.zeros(1,hidden,device=args.device)
            b=torch.zeros(1,hidden*args.feature_layers,device=args.device)
            actual,delta,_=glass.apply(a,b)
            if not torch.equal(actual,a) or bool(delta.count_nonzero()):
                raise RuntimeError('GLASS_AB_NOT_EXACT_ZERO_AT_INIT')
    baseline_path=out/'baseline.json'
    if baseline_path.exists():baseline=json.loads(baseline_path.read_text())
    else:
        initial=_probe(bb,tok,rows,args,show_progress=True)
        baseline={r['id']:r for r in initial}
        _atomic_json(baseline_path,baseline)
        defects=[];shells=collections.defaultdict(lambda:{'tested':0,'failed':0})
        for case in rows:
            item=baseline[case['id']]
            edge=frontier['current_radius']
            shell='inside' if abs(case['residual'])<edge else ('edge' if abs(case['residual'])==edge else 'outside')
            name=f'T{case["rung"]:02d}/{shell}'
            shells[name]['tested']+=1
            if not item['correct']:
                shells[name]['failed']+=1
                defects.append({'id':case['id'],'source':case['source'],
                    'expected':case['target'],'actual':item['output'],'shell':shell})
        _atomic_json(out/'baseline_defects.json',{'cases':defects,'by_shell':dict(shells)})
        _emit('GLASS_AB_BASELINE',{'correct':sum(x['correct'] for x in initial),
              'failed':sum(not x['correct'] for x in initial),'total':len(initial),
              'frontier':{k:frontier[k] for k in ('current_rung','current_radius')}})
    # On an empty new glass, check inference identity over *actual* frontier.
    if not any(history.values()):
        for name in ('A','B'):
            if not (out/f'glass_{name}.pt').exists():
                check=_probe(bb,tok,rows[:min(8,len(rows))],args,glass=glasses[name])
                for ref,got in zip(rows,check):
                    if baseline[got['id']]['output']!=got['output']:
                        raise RuntimeError('GLASS_AB_INITIAL_OUTPUT_CHANGED:'+name)
    if args.stage=='probe':
        _emit('GLASS_AB_PROBE_DONE',{'total':len(rows),'failures':sum(not v['correct'] for v in baseline.values()),'output':str(out)})
        return
    if args.stage=='audit':
        audit_rows=rows
        if args.audit_scope=='certified':
            audit_rows=[];seen=set()
            for certificate,deck in ladder._certified_bank(meta['source_state']['config'],
                                                  meta['source_state'].get('certificates',[])):
                for r in deck:
                    if r['source'] not in seen:
                        audit_rows.append(r);seen.add(r['source'])
            if not audit_rows:raise RuntimeError('GLASS_AB_NO_CERTIFIED_DECKS')
            if args.audit_limit:
                audit_rows=audit_rows[:args.audit_limit]
        if args.audit_scope=='certified':
            baseline_audit=_probe(bb,tok,audit_rows,args,show_progress=True)
        else:
            baseline_audit=list(baseline.values())
        a=_probe(bb,tok,audit_rows,args,glass=glasses['A'],show_progress=True)
        b=_probe(bb,tok,audit_rows,args,glass=glasses['B'],show_progress=True)
        summary=_compare(audit_rows,baseline_audit,a,b)
        summary['scope']=args.audit_scope
        summary['complete_audit']=bool(args.audit_scope=='certified' and not args.audit_limit)
        audit_path=out/('audit_certified.json' if args.audit_scope=='certified' else 'audit_frontier.json')
        _atomic_json(audit_path,summary)
        _emit('GLASS_AB_AUDIT',{'scope':summary['scope'],'complete_audit':summary['complete_audit'],
                'baseline':summary['baseline'],
                'A':{k:summary['A'][k] for k in ('correct','tested','fixed','regressed')},
                'B':{k:summary['B'][k] for k in ('correct','tested','fixed','regressed')},
                'report':str(audit_path)})
        return
    for round_id in range(max((len(v) for v in history.values()),default=0),
                          max((len(v) for v in history.values()),default=0)+args.rounds):
        started=time.monotonic()
        # Interleave A/B optimizer steps on the SAME selected problem and
        # SAME originally-correct replay problem. Neither trains Qwen.
        subset=_round_subset(rows,baseline,args,round_id)
        if args.full_eval_every and (round_id+1)%args.full_eval_every==0:
            subset=rows
        pre={r['id']:baseline[r['id']] for r in subset}
        before={name:{r['id']:v for r,v in zip(subset,_probe(bb,tok,subset,args,glass=glasses[name]))}
                for name in ('A','B')}
        failures=[r for r in subset if any(not before[n][r['id']]['correct'] for n in ('A','B'))]
        protect=[r for r in rows if baseline[r['id']]['correct']]
        rng=random.Random(args.seed+round_id*1000)
        scores={'A':{'steps':0,'wrong_before':0},'B':{'steps':0,'wrong_before':0}}
        for step in range(args.steps_per_round if failures else 0):
            row=rng.choice(failures)
            good=rng.choice(protect) if protect else None
            for name in ('A','B'):
                stats=_train_paired_step(glasses[name],opts[name],bb,tok,row,good,args,rng)
                scores[name]['steps']+=1
                scores[name]['wrong_before']+=int(stats['wrong_before'])
            if (step+1)%max(1,args.progress_every)==0:
                _emit('GLASS_AB_TRAIN_PROGRESS',{'round':round_id+1,'step':step+1,
                      'total':args.steps_per_round,'A_wrong_seen':scores['A']['wrong_before'],
                      'B_wrong_seen':scores['B']['wrong_before']})
        a=_probe(bb,tok,subset,args,glass=glasses['A'])
        b=_probe(bb,tok,subset,args,glass=glasses['B'])
        summary=_compare(subset,list(pre.values()),a,b)
        summary['audit_scope']='full_frontier' if len(subset)==len(rows) else 'matched_frontier_sample'
        for name,after in (('A',a),('B',b)):
            later={r['id']:r for r in after}
            new_fixes=[key for key in later if not before[name][key]['correct'] and later[key]['correct']]
            new_losses=[key for key in later if before[name][key]['correct'] and not later[key]['correct']]
            record={'round':round_id+1,
                    'result':{k:summary[name][k] for k in ('correct','fixed','regressed','changed_outputs','baseline_correct_interventions','baseline_correct_mean_delta_norm','baseline_correct_max_delta_norm','mean_gate_probability')},
                    'new_fixes':new_fixes,'new_losses':new_losses,
                    'coincident_gain_and_loss':bool(new_fixes and new_losses),
                    'optimizer':scores[name]}
            history.setdefault(name,[]).append(record)
        new_lora_digest=hashlib.sha256()
        for name,t in sorted(zero.lora_state(bb).items()):
            new_lora_digest.update(name.encode())
            new_lora_digest.update(t.detach().cpu().contiguous().float().numpy().tobytes())
        if new_lora_digest.hexdigest()!=original_lora_digest:
            raise RuntimeError('GLASS_AB_FROZEN_QWEN_CHANGED')
        _save_glasses(out,glasses,opts,history,policy)
        _atomic_json(out/'comparison.json',{'round':round_id+1,'summary':summary,
                     'seconds':round(time.monotonic()-started,3),'history':history})
        _emit('GLASS_AB_ROUND',{'round':round_id+1,
                            'A':{**history['A'][-1]['result'],'new_fixes':len(history['A'][-1]['new_fixes']),
                                 'new_losses':len(history['A'][-1]['new_losses'])},
                            'B':{**history['B'][-1]['result'],'new_fixes':len(history['B'][-1]['new_fixes']),
                                 'new_losses':len(history['B'][-1]['new_losses'])},
                            'baseline':summary['baseline'],'audit_scope':summary['audit_scope'],
                            'seconds':round(time.monotonic()-started,3)})
    # Intentionally no stacking. Persistent interference needs later diagnostics.


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('probe','train','run','audit','status'),default='run')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint');p.add_argument('--text-checkpoint')
    p.add_argument('--ladder-working',default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_numeric_terminal_v1'/'working.pt'))
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_numeric_glass_edge_ab_v1'))
    p.add_argument('--inner-shells',type=int,default=2)
    p.add_argument('--outer-shells',type=int,default=3)
    p.add_argument('--depth-shells',type=int,default=2)
    p.add_argument('--probe-per-cell',type=int,default=3)
    p.add_argument('--probe-limit',type=int,default=240)
    p.add_argument('--probe-seed',type=int,default=81)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--rounds',type=int,default=6)
    p.add_argument('--steps-per-round',type=int,default=12)
    p.add_argument('--round-eval-limit',type=int,default=96,help='Matched A/B assessment cases per round; 0 = entire probe')
    p.add_argument('--audit-scope',choices=('frontier','certified'),default='frontier')
    p.add_argument('--audit-limit',type=int,default=0,help='Optional noncertifying cap for large certified audits')
    p.add_argument('--full-eval-every',type=int,default=3,help='Assess entire frontier every N rounds')
    p.add_argument('--lr',type=float,default=0.001)
    p.add_argument('--glass-width',type=int,default=192)
    p.add_argument('--feature-layers',type=int,default=4)
    p.add_argument('--preserve-weight',type=float,default=5.0)
    p.add_argument('--delta-l2',type=float,default=0.0001)
    p.add_argument('--gate-loss',type=float,default=0.1)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--progress-every',type=int,default=20)
    p.add_argument('--baseline');p.add_argument('--backbone')
    p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    return _run(args)

if __name__=='__main__':main()
