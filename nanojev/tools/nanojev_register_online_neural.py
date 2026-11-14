#!/usr/bin/env python3
"""Independent tiny online neural decoder for two registered numbers in frozen Qwen.

Training: fresh (a,b,operation) -> frozen Qwen selected suffix activations ->
2-output MLP. No LASSO, no modification of Qwen or other NanoJev services.
Validation/test: immutable v3 activation records. Train on fresh pairs only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

import numpy as np

import nanojev_register_stability_v3_generate as v3

SCHEMA = 'nanojev.register-online-neural.v1'
DEFAULT_V3 = Path.home() / 'NanoJev' / 'runs' / 'register_stability_v3'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_online_neural_v1'
LAYER_IDS = (16, 28)
SUFFIX = 4
MAX_VALUE = v3.MAX_VALUE
TARGET_SCALE = math.asinh(MAX_VALUE)


def emit(event, **kw):
    print(json.dumps({'event': event, **kw}, sort_keys=True, default=str), flush=True)


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.partial')
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def read_benchmark(root):
    """Seal the existing benchmark by checking the generator's task hash."""
    root = Path(root).expanduser().resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != v3.SCHEMA or manifest.get('registration', {}).get('first') != v3.FIRST:
        raise RuntimeError('NEURAL_V3_MANIFEST_INCOMPATIBLE')
    task_bytes = (root / manifest['task_file']).read_bytes()
    # Original generator hashes its canonical LF serialization. Windows CRLF
    # conversion, if any, must not alter the decoded task content.
    text = task_bytes.decode('utf-8').replace('\r\n', '\n')
    if v3.digest(text) != manifest['tasks_sha256']:
        raise RuntimeError('NEURAL_V3_TASK_HASH_MISMATCH')
    tasks = [json.loads(line) for line in text.splitlines() if line]
    if len(tasks) != 23552 or len(tasks) != manifest['expected_unique_prompts']:
        raise RuntimeError('NEURAL_V3_TASK_COUNT_MISMATCH')
    if set(t['split'] for t in tasks) != {'train', 'val', 'test', 'control'}:
        raise RuntimeError('NEURAL_V3_SPLITS_INVALID')
    if manifest['capture']['stored_layers'] != list(LAYER_IDS):
        raise RuntimeError('NEURAL_V3_EXPECTS_CAPTURE_LAYERS_16_28')
    return manifest, tasks


def reserved_pairs(tasks):
    """Never train on any ordered pair from the fixed 23,552-prompt benchmark."""
    return {(int(t['a']), int(t['b'])) for t in tasks}


class FreshPairs:
    """New numeric problems; same exact XX register syntax + operation catalog."""
    def __init__(self, seed, reserved, holdout_ops=v3.HELD_OUT_OPERATIONS):
        self.rng = random.Random(seed)
        self.forbidden = set(reserved)
        self.seen = set()
        self.ops = [op for op in v3.OPERATIONS if op[0] not in holdout_ops]
        if not self.ops or not self.forbidden:
            raise ValueError('NEURAL_INVALID_PAIR_GENERATOR')
        self.grid = v3.numeric_grid()
        self.by_family = defaultdict(list)
        for op in self.ops:
            self.by_family[op[1]].append(op)
        self.families = sorted(self.by_family)
        self.drawn = 0

    def integer(self):
        r = self.rng.random()
        if r < .055:
            return 0
        sign = self.rng.choice((-1, 1))
        if r < .36:
            return self.rng.choice(self.grid)
        if r < .76:
            magnitude = int(round(math.exp(self.rng.uniform(0, math.log(MAX_VALUE)))))
            return sign * max(1, magnitude)
        return sign * self.rng.randint(1, MAX_VALUE)

    def batch(self, size):
        items=[]
        for _ in range(size):
            for _attempt in range(10000):
                a,b=self.integer(),self.integer()
                if (a,b) not in self.forbidden and (a,b) not in self.seen:
                    break
            else:
                raise RuntimeError('NEURAL_FRESH_PAIR_EXHAUSTED')
            self.seen.add((a,b))
            # Balanced operation families, then draw a variant in that family.
            family = self.families[self.drawn % len(self.families)]
            op = self.rng.choice(self.by_family[family])
            self.drawn += 1
            items.append({'a':a,'b':b,'operation':op[0], 'family':family,
                          'prompt':v3.render(a,b,op)})
        return items

    def state_dict(self):
        return {'rng_state':self.rng.getstate(), 'seen':sorted(self.seen), 'drawn':self.drawn}

    def load_state_dict(self, obj):
        self.rng.setstate(obj['rng_state'])
        self.seen=set(tuple(x) for x in obj['seen'])
        self.drawn=int(obj['drawn'])
        if self.seen & self.forbidden:
            raise RuntimeError('NEURAL_RESUME_BENCHMARK_LEAKAGE')


def model_factory(torch, width=32, dropout=0.0):
    nn = torch.nn
    return nn.Sequential(
        nn.LayerNorm(len(LAYER_IDS) * SUFFIX * 1024, elementwise_affine=False),
        nn.Linear(len(LAYER_IDS)*SUFFIX*1024, width),
        nn.GELU(),
        nn.Linear(width, width),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(width, 2),
    )


def targets(torch, pair, device):
    x = torch.as_tensor(pair, dtype=torch.float32, device=device)
    return torch.asinh(x) / TARGET_SCALE


def recover(torch, predictions):
    """Return estimated INTEGER numeric registers, preserving exact-zero rule."""
    y = predictions.detach().float().clamp(-1.02, 1.02)
    return torch.round(torch.sinh(y * TARGET_SCALE)).to(torch.int64)


def correctness(pred, truth):
    """Exact zero and 5% error for each nonzero operand, then joint pass."""
    pred = np.asarray(pred, dtype=np.int64)
    truth = np.asarray(truth, dtype=np.int64)
    exact_zero = (truth == 0) & (pred == 0)
    normal = (truth != 0) & (np.abs(pred.astype(np.float64) - truth) <= .05 * np.abs(truth))
    return (exact_zero | normal)


def summarize_predictions(pred, truth, operations=None, families=None):
    hits=correctness(pred, truth)
    scores={'rows':int(len(hits)), 'x_pass_rate':float(hits[:,0].mean()),
            'y_pass_rate':float(hits[:,1].mean()),
            'joint_pass_rate':float(hits.all(axis=1).mean())}
    for i,label in enumerate(('x','y')):
        mask=truth[:,i]==0
        scores[label+'_zero_count']=int(mask.sum())
        scores[label+'_zero_recall']=float(hits[mask,i].mean()) if mask.any() else None
    if operations is not None:
        for group_name,keys in (('operations', operations),('families',families)):
            if keys is None: continue
            grouped={}
            for name in sorted(set(keys)):
                at=np.asarray([x==name for x in keys])
                grouped[name]={'rows':int(at.sum()),
                               'joint_pass_rate':float(hits[at].all(axis=1).mean())}
            scores[group_name]=grouped
    return scores


def extract_batched(torch, frozen, tokenizer, prompts, device, max_tokens):
    """Qwen forward-only. Right pad, then gather real suffix token positions.

    Existing v3 captures use unpadded prompts. Right padding leaves real-token
    position indices unchanged; explicit mask prevents attention to padding.
    """
    token_lists=[]
    for prompt in prompts:
        ids=tokenizer(prompt, add_special_tokens=False)['input_ids']
        if ids and isinstance(ids[0],list): ids=ids[0]
        if not ids or len(ids)>max_tokens or len(ids)<SUFFIX:
            raise RuntimeError(f'NEURAL_PROMPT_LENGTH_INVALID:{len(ids)}:{max_tokens}')
        token_lists.append(ids)
    seq=max(map(len,token_lists))
    pad=tokenizer.pad_token_id
    if pad is None: pad=tokenizer.eos_token_id
    if pad is None: raise RuntimeError('NEURAL_TOKENIZER_HAS_NO_PAD_OR_EOS')
    input_ids=torch.full((len(prompts),seq),int(pad),dtype=torch.long,device=device)
    mask=torch.zeros((len(prompts),seq),dtype=torch.long,device=device)
    lengths=[]
    for k,ids in enumerate(token_lists):
        input_ids[k,:len(ids)]=torch.as_tensor(ids,dtype=torch.long,device=device)
        mask[k,:len(ids)]=1
        lengths.append(len(ids))
    position_ids=(mask.cumsum(-1)-1).clamp_min(0)
    with torch.inference_mode():
        h=frozen(input_ids=input_ids,attention_mask=mask,position_ids=position_ids,
                 output_hidden_states=True,use_cache=False).hidden_states
        if h is None or len(h)<=max(LAYER_IDS):
            raise RuntimeError('NEURAL_QWEN_MISSING_HIDDEN_LAYERS')
        offsets=torch.arange(-SUFFIX,0,dtype=torch.long,device=device)
        indices=torch.as_tensor(lengths,dtype=torch.long,device=device)[:,None]+offsets[None,:]
        bi=torch.arange(len(prompts),device=device)[:,None]
        data=torch.stack([h[l][bi,indices,:] for l in LAYER_IDS],dim=1)
        if data.shape != (len(prompts),len(LAYER_IDS),SUFFIX,1024):
            raise RuntimeError(f'NEURAL_CAPTURE_LAYOUT_MISMATCH:{tuple(data.shape)}')
        flattened=data.reshape(len(prompts),-1).float()
    # PyTorch forbids using inference tensors as saved activations for the
    # trainable Linear backward. Clone OUTSIDE inference_mode to obtain a
    # regular detached tensor (without a frozen-backbone autograd graph).
    return flattened.clone()


def load_eval(root, manifest, tasks, split, count=None, seed=19):
    """Load only immutable v3 benchmark records, without recomputing Qwen."""
    choices=[t for t in tasks if t['split']==split]
    if count is not None and count<len(choices):
        # Fixed subset for comparable validation across all training steps.
        indices=sorted(random.Random(seed).sample(range(len(choices)),count))
        choices=[choices[i] for i in indices]
    xs=[];pairs=[];ops=[];families=[]
    for t in choices:
        file=Path(root)/'records'/(t['sha256']+'.npz')
        with np.load(file,allow_pickle=False) as record:
            md=json.loads(str(record['metadata'].item()))
            arr=np.asarray(record['hidden_states'])
        if md.get('sha256')!=t['sha256'] or md.get('prompt')!=t['prompt'] or arr.ndim!=3:
            raise RuntimeError(f'NEURAL_V3_EVAL_RECORD_INVALID:{file}')
        if md.get('stored_layers')!=list(LAYER_IDS) or arr.shape[0]!=2 or arr.shape[2]!=1024:
            raise RuntimeError(f'NEURAL_V3_EVAL_LAYER_MISMATCH:{file}')
        if arr.shape[1]<SUFFIX:
            raise RuntimeError(f'NEURAL_V3_EVAL_SHORT_PROMPT:{file}')
        xs.append(arr[:,-SUFFIX:,:].reshape(-1).astype(np.float32))
        pairs.append([t['a'],t['b']]);ops.append(t['operation']);families.append(t['family'])
    if not xs: raise RuntimeError(f'NEURAL_V3_EVAL_EMPTY_SPLIT:{split}')
    return np.asarray(xs,dtype=np.float32),np.asarray(pairs,dtype=np.int64),ops,families


def score(torch, net, eval_data, device, batch_size=256):
    x,y,ops,fam=eval_data
    old=net.training;net.eval(); predictions=[]
    with torch.inference_mode():
        for i in range(0,len(x),batch_size):
            batch=torch.as_tensor(x[i:i+batch_size],dtype=torch.float32,device=device)
            predictions.append(recover(torch,net(batch)).cpu().numpy())
    net.train(old)
    return summarize_predictions(np.concatenate(predictions),y,ops,fam)


def verify_capture_alignment(torch, frozen, tokenizer, device, root, tasks, max_tokens,
                             count=2, abs_tolerance=0.06):
    """Verify padded microbatch last-four states agree with saved unpadded v3.

    A mismatch fails closed rather than training on a different observation rule.
    Floating-point execution can differ slightly with batching and kernels.
    """
    selected=[t for t in tasks if t['split']=='val'][:count]
    if len(selected)!=count:raise RuntimeError('NEURAL_ALIGNMENT_VALIDATION_MISSING')
    got=extract_batched(torch,frozen,tokenizer,[t['prompt'] for t in selected],
                        device,max_tokens).detach().cpu().numpy()
    refs=[]
    for task in selected:
        with np.load(Path(root)/'records'/(task['sha256']+'.npz'),allow_pickle=False) as f:
            md=json.loads(str(f['metadata'].item()))
            a=np.asarray(f['hidden_states'],dtype=np.float32)
        if md.get('sha256')!=task['sha256'] or md.get('prompt')!=task['prompt']:
            raise RuntimeError('NEURAL_ALIGNMENT_CAPTURE_IDENTITY_ERROR')
        refs.append(a[:,-SUFFIX:,:].reshape(-1))
    ref=np.stack(refs)
    err=np.abs(got-ref)
    max_abs=float(err.max())
    # Coordinate-level scale-relative check is robust to near-zero features.
    rms=float(np.sqrt(np.square(err).mean()))
    relative=rms/max(float(np.sqrt(np.square(ref).mean())),1.e-6)
    emit('neural_capture_alignment',max_absolute_error=max_abs,relative_rms=relative)
    if not np.isfinite(relative) or relative > abs_tolerance:
        raise RuntimeError(f'NEURAL_LIVE_CAPTURE_MISMATCH:{relative}')


def save_checkpoint(torch, path, net, optim, step, config, pair_stream, best, model_identity):
    path.parent.mkdir(parents=True,exist_ok=True)
    payload={'schema':SCHEMA,'config':config,'step':step,'model':net.state_dict(),
             'optimizer':optim.state_dict(),'random_stream':pair_stream.state_dict(),
             'torch_rng':torch.get_rng_state(),'best_validation':float(best),
             'backbone_identity':model_identity}
    if torch.cuda.is_available():payload['cuda_rng']=torch.cuda.get_rng_state_all()
    tmp=path.with_name(path.name+'.partial')
    torch.save(payload,tmp)
    os.replace(tmp,path)


def load_checkpoint(torch,path,net,optim,stream,config,identity,device):
    # A checkpoint includes CPU and CUDA RNG states.  Loading with
    # map_location='cuda' silently relocates these ByteTensors to the GPU;
    # torch.cuda.set_rng_state_all() rejects CUDA-resident RNG tensors.
    # Keep the serialized checkpoint on CPU. load_state_dict() copies model
    # tensors into the existing module device, and optimizer.load_state_dict()
    # restores its parameter-associated state on the parameter device.
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=SCHEMA or ck.get('config')!=config or ck.get('backbone_identity')!=identity:
        raise RuntimeError('NEURAL_CHECKPOINT_CONFIG_OR_BACKBONE_DRIFT')
    net.load_state_dict(ck['model']);optim.load_state_dict(ck['optimizer'])
    stream.load_state_dict(ck['random_stream'])
    torch.set_rng_state(ck['torch_rng'].to(device='cpu',dtype=torch.uint8))
    if 'cuda_rng' in ck and torch.cuda.is_available():
        cuda_states=ck['cuda_rng']
        if not isinstance(cuda_states,(tuple,list)):
            raise RuntimeError('NEURAL_CHECKPOINT_CUDA_RNG_INVALID')
        torch.cuda.set_rng_state_all([
            state.to(device='cpu',dtype=torch.uint8) for state in cuda_states
        ])
    return int(ck['step']),float(ck['best_validation'])


def config_for(args, manifest):
    return {'width':args.width,'dropout':args.dropout,'lr':args.lr,
            'weight_decay':args.weight_decay,'batch_size':args.batch_size,
            'replay_updates':args.replay_updates,'seed':args.seed,
            'val_examples':args.val_examples,'max_prompt_tokens':args.max_prompt_tokens,
            'benchmark_sha256':manifest['tasks_sha256'],'layers':list(LAYER_IDS),
            'suffix':SUFFIX,'objective':'smooth_l1_asinh_2outputs_v1',
            'train_operations':[op[0] for op in v3.OPERATIONS if op[0] not in v3.HELD_OUT_OPERATIONS]}


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        self_test();return 0
    if args.batch_size<1 or args.width<2 or args.steps<1 or args.eval_every<1 or args.checkpoint_every<1:
        raise ValueError('NEURAL_INVALID_TRAIN_SETTINGS')
    if args.replay_updates<1 or args.max_prompt_tokens<SUFFIX:
        raise ValueError('NEURAL_INVALID_REPLAY_OR_TOKENS')
    import torch
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('NEURAL_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    manifest,tasks=read_benchmark(args.v3_root)
    config=config_for(args,manifest)
    results=args.results_root.expanduser().resolve()
    results.mkdir(parents=True,exist_ok=True)
    cfg_path=results/'run_config.json'
    if cfg_path.is_file():
        if json.loads(cfg_path.read_text(encoding='utf-8'))!=config:
            raise RuntimeError('NEURAL_EXISTING_RESULTS_CONFIG_DRIFT')
    else:
        if any(results.iterdir()):raise RuntimeError('NEURAL_RESULTS_ROOT_NOT_EMPTY')
        atomic_json(cfg_path,config)
    identity=json.loads((args.v3_root/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':raise RuntimeError('NEURAL_BACKBONE_NOT_QWEN')
    torch.manual_seed(args.seed)
    net=model_factory(torch,args.width,args.dropout).to(device)
    optim=torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=FreshPairs(args.seed,reserved_pairs(tasks))
    parameters=sum(p.numel() for p in net.parameters() if p.requires_grad)
    emit('neural_decoder_ready',parameters=parameters,width=args.width,
         backbone='frozen Qwen3-0.6B',trainable_backbone_parameters=0,
         heldout_operations=list(v3.HELD_OUT_OPERATIONS),benchmark_prompts=len(tasks))
    last=results/'latest.pt'; best_ck=results/'best.pt'
    if args.eval_only:
        if not best_ck.is_file():raise FileNotFoundError(f'NEURAL_BEST_CHECKPOINT_MISSING:{best_ck}')
        step,best=load_checkpoint(torch,best_ck,net,optim,stream,config,identity,device)
        for split in ('val','test','control'):
            data=load_eval(args.v3_root,manifest,tasks,split,None)
            emit('neural_final_evaluation',split=split,step=step,**score(torch,net,data,device))
        return 0
    if last.exists() and not args.resume:
        raise RuntimeError('NEURAL_EXISTING_CHECKPOINT_REQUIRES_--resume')
    step=0;best=-1.0
    if args.resume:
        if not last.is_file():raise RuntimeError('NEURAL_RESUME_MISSING_CHECKPOINT')
        step,best=load_checkpoint(torch,last,net,optim,stream,config,identity,device)
        emit('neural_resumed',step=step,best_validation=best)
    val=load_eval(args.v3_root,manifest,tasks,'val',args.val_examples)
    # Important: evaluation does NOT create Qwen generations or gradients.
    import nanojev_numerate_spine_cassette_smoke as cassette
    # Device and dtype settings match the existing v3 Qwen capture identity.
    torch_source,frozen,tokenizer,source_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit')
        or model_info.get('source')!=identity['model_info'].get('source')):
        raise RuntimeError('NEURAL_QWEN_BACKBONE_REVISION_DRIFT')
    if str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype')):
        raise RuntimeError('NEURAL_QWEN_BACKBONE_DTYPE_DRIFT')
    frozen.eval()
    verify_capture_alignment(torch,frozen,tokenizer,source_device,args.v3_root,tasks,
                             args.max_prompt_tokens)
    start=time.monotonic();reported_loss=0.0; reported_steps=0
    cumulative_capture_seconds=0.0; cumulative_decoder_seconds=0.0
    for k in range(step+1,args.steps+1):
        t_capture=time.monotonic()
        rows=stream.batch(args.batch_size)
        feats=extract_batched(torch,frozen,tokenizer,[r['prompt'] for r in rows],
                              source_device,args.max_prompt_tokens)
        truth=targets(torch,[[r['a'],r['b']] for r in rows],device)
        # The frozen Qwen activation tensor has no autograd graph.
        assert not feats.requires_grad
        if device.type=='cuda':torch.cuda.synchronize()
        cumulative_capture_seconds += time.monotonic()-t_capture
        t_decoder=time.monotonic()
        for _ in range(args.replay_updates):
            net.train()
            predicted=net(feats)
            loss=torch.nn.functional.smooth_l1_loss(predicted,truth,beta=.025)
            optim.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
            optim.step()
        reported_loss += float(loss.detach().cpu()); reported_steps+=1
        if device.type=='cuda':torch.cuda.synchronize()
        cumulative_decoder_seconds += time.monotonic()-t_decoder
        if k%args.report_every==0 or k==args.steps:
            elapsed=time.monotonic()-start
            emit('neural_train_progress',step=k,unique_generated_pairs=len(stream.seen),
                 mean_loss=round(reported_loss/reported_steps,6),
                 elapsed_seconds=round(elapsed,2),
                 new_pairs_per_second=round((k-step)*args.batch_size/max(elapsed,1.e-9),2),
                 frozen_qwen_seconds=round(cumulative_capture_seconds,2),
                 decoder_update_seconds=round(cumulative_decoder_seconds,2))
            reported_loss=0.0; reported_steps=0
        if k%args.eval_every==0 or k==args.steps:
            scores=score(torch,net,val,device)
            emit('neural_validation',step=k,**scores)
            if scores['joint_pass_rate']>best:
                best=scores['joint_pass_rate']
                save_checkpoint(torch,best_ck,net,optim,k,config,stream,best,identity)
                emit('neural_best_checkpoint',step=k,validation_joint=best,path=str(best_ck))
        if k%args.checkpoint_every==0 or k==args.steps:
            save_checkpoint(torch,last,net,optim,k,config,stream,best,identity)
            emit('neural_checkpoint',step=k,path=str(last))
    emit('neural_training_complete',steps=args.steps,best_validation_joint=best,
         generated_pairs=len(stream.seen),results_root=str(results))
    return 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--eval-only',action='store_true',help='Score saved best model; never load Qwen.')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--v3-root',type=Path,default=DEFAULT_V3)
    p.add_argument('--results-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--steps',type=int,default=1000)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--width',type=int,default=32)
    p.add_argument('--dropout',type=float,default=0.)
    p.add_argument('--lr',type=float,default=0.0003)
    p.add_argument('--weight-decay',type=float,default=0.01)
    p.add_argument('--replay-updates',type=int,default=2)
    p.add_argument('--seed',type=int,default=20261014)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--checkpoint-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=20)
    p.add_argument('--val-examples',type=int,default=512)
    p.add_argument('--max-prompt-tokens',type=int,default=256)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','float32','bfloat16'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--threads',type=int,default=2)
    return p


def self_test():
    import torch
    # No Qwen or HF download. Exercise fresh pairing, holdout, tiny net, backward.
    pairs,tasks,_,_=v3.build_plan()
    seen=reserved_pairs(tasks)
    gen=FreshPairs(22,seen)
    b=gen.batch(100)
    assert len(b)==100 and len({(r['a'],r['b']) for r in b})==100
    assert all((r['a'],r['b']) not in seen for r in b)
    assert all(r['operation'] not in v3.HELD_OUT_OPERATIONS for r in b)
    assert all(r['prompt'].startswith('XXXXXX = ') for r in b)
    old=gen.state_dict();after=gen.batch(12)
    gen2=FreshPairs(22,seen);gen2.load_state_dict(old)
    assert gen2.batch(12)==after
    model=model_factory(torch,16)
    x=torch.randn(8,8192); y=targets(torch,[[i+1,-i] for i in range(8)],'cpu')
    opt=torch.optim.AdamW(model.parameters(),lr=.001)
    pre=model[1].weight.detach().clone()
    out=model(x);loss=torch.nn.functional.smooth_l1_loss(out,y)
    loss.backward();opt.step()
    assert out.shape==(8,2) and not torch.equal(pre,model[1].weight)
    assert correctness([[0,100],[-10,95]],[[0,100],[-10,100]]).all()
    assert not correctness([[1,100]],[[0,100]])[0,0]
    metrics=summarize_predictions(np.array([[0,100],[1,99]]),np.array([[0,100],[0,100]]),['a','b'],['symbolic','written'])
    assert metrics['x_zero_recall']==.5 and metrics['joint_pass_rate']==.5
    emit('neural_self_test_pass',parameter_count=sum(p.numel() for p in model.parameters()),
         holdout_pairs=len(seen),new_problems=100)


if __name__=='__main__':
    raise SystemExit(main())
