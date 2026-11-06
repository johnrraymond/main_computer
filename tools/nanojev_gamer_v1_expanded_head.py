#!/usr/bin/env python3
"""GAMER V1: frozen-LoRA -> 100 deterministic contextual vectors -> enlarged C0 Wide.

No tokenizer-number heuristics, new attention, LoRA learning, table-oracle input,
legacy head mutation, or optional sparsification.  Every slot is populated.
The source certified run is strictly read-only; only new sibling runs are written.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from tools import nanojev_gamer_v1_handoff as gamer
from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs, _output_path

SCHEMA = 'nanojev.gamer.v1.expanded-head.v1'
DEFAULT_HIDDEN = (4096, 2048, 512, 128)


def positions_for_length(length, count=100, curvature=4.0):
    """Normalized exponential raster: broad beginning, dense tail.

    With >=count tokens every position is unique. With fewer tokens every
    position occurs at least once, remaining slots replicate existing tokens.
    Both endpoints are always present and the output is chronologically sorted.
    """
    if not isinstance(length,int) or length < 1 or count < 1:
        raise ValueError('GAMER_EXPANDED_INVALID_SEQUENCE_OR_SAMPLES')
    if not math.isfinite(curvature) or curvature < 0 or curvature > 20:
        raise ValueError('GAMER_EXPANDED_CURVATURE_OUT_OF_RANGE')
    if count == 1:
        return (length - 1,)
    def raster_fraction(t):
        fraction = (t if curvature == 0 else
                    1.0-math.expm1(curvature*(1.0-t))/math.expm1(curvature))
        return max(0,min(length-1,round((length-1)*fraction)))
    def raster(i):
        return raster_fraction(i/(count-1))
    if length >= count:
        chosen=[]
        for i in range(count):
            nominal=raster(i)
            # Keep room for all remaining selections, preventing duplicate
            # positions due to rounded exponential spacing near the tail.
            idx=max(chosen[-1]+1 if chosen else 0,
                    min(nominal,length-(count-i)))
            chosen.append(idx)
        if chosen[0] != 0 or chosen[-1] != length-1 or len(set(chosen))!=count:
            raise AssertionError('GAMER_EXPANDED_SAMPLING_NOT_DISTINCT')
        return tuple(chosen)
    # Every short-prompt token must be present; replicate surplus raster slots.
    extra=count-length
    chosen=sorted(list(range(length))+[raster_fraction((i+.5)/extra) for i in range(extra)])
    if len(chosen)!=count or set(chosen)!=set(range(length)):
        raise AssertionError('GAMER_EXPANDED_SHORT_PROMPT_COVERAGE')
    return tuple(chosen)


def configuration(*,samples=100,curvature=4.0,hidden=DEFAULT_HIDDEN,max_length=1024,
                  capture_batch=2,lr=.0002,weight_decay=.0001,grad_clip=1.,
                  batch_size=4,seed=47,zero_focus=.5,zero_radius=1.,
                  backend='offload-cuda',precision='float16',eval_every=100,
                  save_every=100):
    hidden=tuple(int(x) for x in hidden)
    if (samples!=100 or len(hidden)!=4 or any(x<1 for x in hidden)
        or max_length<1 or capture_batch<1 or batch_size<1
        or not (0<=zero_focus<=1) or zero_radius<=0
        or not (0<lr<=1) or weight_decay<0 or grad_clip<=0
        or eval_every<1 or save_every<1 or backend not in ('offload-cuda','cpu','full-cuda')
        or precision not in ('float16','bfloat16')
        or not math.isfinite(curvature) or curvature<0 or curvature>20):
        raise ValueError('GAMER_EXPANDED_INVALID_HYPERPARAMETERS')
    return {'samples':samples,'curvature':float(curvature),'hidden':list(hidden),
            'max_length':int(max_length),'capture_batch':capture_batch,
            'lr':float(lr),'weight_decay':float(weight_decay),'grad_clip':float(grad_clip),
            'batch_size':batch_size,'seed':seed,'zero_focus':float(zero_focus),
            'zero_radius':float(zero_radius),'backend':backend,'precision':precision,
            'eval_every':eval_every,'save_every':save_every,
            'input_width':samples*1024,'objective':'c0_wide_signed_log_smooth_l1_aux_0.25',
            'feature_policy':'100_NORMALIZED_EXPONENTIAL_FULL_TOKEN_HIDDEN_LATEST_LAYER',
            'short_prompt':'REPLICATE_EVERY_TOKEN_NO_MASK',
            'padding_policy':'RIGHT_PADDED_REAL_LENGTH_SAMPLE_ONLY'}


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _last_layer_norm(model):
    """Locate Qwen's final norm (also under the frozen PEFT wrapper)."""
    base=model.get_base_model() if hasattr(model,'get_base_model') else model
    backbone=getattr(base,'model',None)
    return getattr(backbone,'norm',None)


def _capture(torch,model,tok,rows,*,device,max_length,batch_size,samples,curvature,
             original_features=None):
    """Capture with exact C0 right-padded batch/last-nonpad indexing contract."""
    from tools.nanojev_qwen_c2_local_train import batched
    features=[]; spans=[]; max_delta=0.
    if tok.padding_side!='right':
        raise ValueError('GAMER_EXPANDED_REQUIRES_RIGHT_PADDING')
    final_norm=_last_layer_norm(model)
    start=0
    for group in batched(rows,batch_size):
        prompts=[gamer.original_prompt(r) for r in group]
        # Never silently truncate the user's numerical or symbolic prompt.
        for p in prompts:
            if len(tok.encode(p,add_special_tokens=True))>max_length:
                raise ValueError('GAMER_EXPANDED_PROMPT_TRUNCATION_REFUSED')
        encoded=tok(prompts,padding=True,truncation=True,max_length=max_length,
                    return_tensors='pt')
        encoded={k:v.to(device) for k,v in encoded.items()}
        if not {'input_ids','attention_mask'}<=set(encoded):
            raise ValueError('GAMER_EXPANDED_TOKENIZER_INPUTS_MISSING')
        with torch.inference_mode():
            if final_norm is not None:
                captured=[]
                def hook(_module,_args,out):
                    captured.append(out.detach())
                handle=final_norm.register_forward_hook(hook)
                try:
                    # Avoid N x vocabulary logits and all 29 layer activations.
                    # Final norm is precisely the hidden_states[-1] that the
                    # certified source capture previously selected.
                    model(**encoded,output_hidden_states=False,return_dict=True,
                          logits_to_keep=1)
                finally:
                    handle.remove()
                if len(captured)!=1:
                    raise ValueError('GAMER_EXPANDED_FINAL_NORM_HOOK_COUNT_MISMATCH')
                hidden=captured[0]
            else:
                # Explicitly bounded mock-model compatibility for unit tests;
                # production Qwen must expose the final normalization module.
                if int(max_length)>256 or int(model.config.hidden_size)==1024:
                    raise ValueError('GAMER_EXPANDED_FINAL_NORM_NOT_FOUND')
                result=model(**encoded,output_hidden_states=True,return_dict=True)
                hidden=result.hidden_states[-1]
            # Source C0 uses mask.sum(-1)-1, so preserve that precise convention.
            lengths=encoded['attention_mask'].sum(dim=-1).tolist()
            if len(lengths)!=len(group):
                raise ValueError('GAMER_EXPANDED_CAPTURE_BATCH_MISMATCH')
            for i,length in enumerate(lengths):
                length=int(length)
                if length<1 or length>hidden.shape[1]:
                    raise ValueError('GAMER_EXPANDED_CAPTURE_BAD_LENGTH')
                selected=positions_for_length(length,samples,curvature)
                x=hidden[i,list(selected)].to(dtype=torch.float32).cpu().numpy()
                if x.shape!=(samples,hidden.shape[-1]) or not np.isfinite(x).all():
                    raise ValueError('GAMER_EXPANDED_CAPTURE_NONFINITE')
                if original_features is not None:
                    old=original_features[start+i]
                    delta=float(np.max(np.abs(x[-1]-old)))
                    max_delta=max(max_delta,delta)
                    if delta>0.1:
                        raise ValueError('GAMER_EXPANDED_LEGACY_FEATURE_PARITY_FAILED: '
                                         +str(delta))
                features.append(x.reshape(-1))
                spans.append({'tokens':length,'first':selected[0],
                              'last':selected[-1], 'distinct':len(set(selected))})
        start+=len(group)
    result=np.asarray(features,dtype=np.float32)
    if result.ndim!=2 or result.shape[1]!=samples*int(model.config.hidden_size):
        raise ValueError('GAMER_EXPANDED_CAPTURE_WIDTH_MISMATCH')
    return result,{'cases':len(rows),'samples':samples,'input_width':result.shape[1],
                   'min_prompt_tokens':min(r['tokens'] for r in spans),
                   'max_prompt_tokens':max(r['tokens'] for r in spans),
                   'short_prompt_cases':sum(r['tokens']<samples for r in spans),
                   'capture_hook':'final_norm_only' if final_norm is not None else 'bounded_test_mock',
                   'min_distinct_samples':min(r['distinct'] for r in spans),
                   'max_legacy_last_token_delta':max_delta}


def _save_cache(path, *,identity,features,summary):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,suffix='.npz',delete=False) as stream:
        tmp=Path(stream.name)
    try:
        with tmp.open('wb') as out:
            np.savez_compressed(out,identity=gamer.canonical(identity),
                                features=features.astype(np.float16),
                                summary=gamer.canonical(summary))
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


def _read_cache(path,identity,n,dim):
    with np.load(path,allow_pickle=False) as data:
        if str(data['identity'].item())!=gamer.canonical(identity):
            raise ValueError('GAMER_EXPANDED_CACHE_LINEAGE_MISMATCH: '+str(path))
        x=data['features'].astype(np.float32)
        summary=json.loads(str(data['summary'].item()))
    if x.shape!=(n,dim) or not np.isfinite(x).all():
        raise ValueError('GAMER_EXPANDED_CACHE_SHAPE_OR_VALUE_INVALID')
    return x,summary


def make_expanded_head(torch,input_width,hidden=DEFAULT_HIDDEN):
    from tools.nanojev_qwen_c0_compressive_train import make_network
    return make_network(torch,input_width,tuple(hidden))


class HeadTrainer:
    """FP32 CPU AdamW master + FP16 GPU mirror; or ordinary CPU/GPU AdamW.

    CUDA-offloaded mode keeps the large FP32 AdamW moments off GPU while
    preserving the actual wide Linear layer; no low-rank approximation.
    """
    def __init__(self,torch,model,*,backend,precision,lr,weight_decay,clip):
        self.torch=torch;self.backend=backend;self.clip=clip;self.scale=512.
        self.master=model.to('cpu',dtype=torch.float32)
        if backend=='offload-cuda':
            if not torch.cuda.is_available():
                raise RuntimeError('GAMER_EXPANDED_CUDA_REQUIRED_FOR_OFFLOAD')
            self.work=make_expanded_head(torch,model.norm.normalized_shape[0],
                    tuple(m.out_features for m in list(model.enc) if isinstance(m,torch.nn.Linear))+
                    (model.mid[0].out_features,)).to('cuda',dtype=(torch.float16 if precision=='float16' else torch.bfloat16))
            self.work.load_state_dict(self.master.state_dict())
            self.device='cuda'
            self.optimizer=torch.optim.AdamW(self.master.parameters(),lr=lr,
                              weight_decay=weight_decay,foreach=False)
        else:
            if backend=='full-cuda' and not torch.cuda.is_available():
                raise RuntimeError('GAMER_EXPANDED_CUDA_UNAVAILABLE')
            self.device='cuda' if backend=='full-cuda' else 'cpu'
            self.master.to(self.device)
            self.work=self.master
            self.optimizer=torch.optim.AdamW(self.master.parameters(),lr=lr,
                              weight_decay=weight_decay,foreach=False)

    def forward(self,x):
        x=x.to(self.device,dtype=next(self.work.parameters()).dtype)
        return self.work(x)

    def update(self,x,y,*,beta=.1,aux=.25):
        from tools.nanojev_qwen_c0_compressive_train import neural_loss
        torch=self.torch
        if self.backend=='offload-cuda':
            # Reuse one mini-batch when dynamic scaling detects overflow.
            for attempt in range(8):
                self.work.train();self.work.zero_grad(set_to_none=True)
                a,b=self.forward(x)
                loss=neural_loss(torch,a.float(),b.float(),y.to('cuda'),
                                 beta=beta,aux_weight=aux)
                if not torch.isfinite(loss).item():
                    raise ValueError('GAMER_EXPANDED_NONFINITE_FORWARD_LOSS')
                (loss*self.scale).backward()
                finite=True
                for src,dst in zip(self.work.parameters(),self.master.parameters()):
                    if src.grad is None:dst.grad=None;continue
                    grad=src.grad.detach().to('cpu',dtype=torch.float32)/self.scale
                    if not bool(torch.isfinite(grad).all()):
                        finite=False;break
                    dst.grad=grad
                if finite:break
                self.scale/=2.
                if self.scale < 1.0 or attempt==7:
                    raise ValueError('GAMER_EXPANDED_FP16_GRADIENTS_NONFINITE')
            torch.nn.utils.clip_grad_norm_(self.master.parameters(),self.clip)
            self.optimizer.step();self.optimizer.zero_grad(set_to_none=True)
            with torch.no_grad():
                for src,dst in zip(self.master.parameters(),self.work.parameters()):
                    dst.copy_(src.to(device='cuda',dtype=dst.dtype))
            return float(loss.detach().cpu())
        self.work.train();self.optimizer.zero_grad(set_to_none=True)
        a,b=self.forward(x)
        loss=neural_loss(torch,a.float(),b.float(),y.to(self.device),beta=beta,aux_weight=aux)
        if not torch.isfinite(loss).item():
            raise ValueError('GAMER_EXPANDED_NONFINITE_FORWARD_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.work.parameters(),self.clip)
        self.optimizer.step()
        return float(loss.detach().cpu())

    def predict(self,x,batch=8):
        from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
        self.work.eval();results=[]
        with self.torch.inference_mode():
            for start in range(0,len(x),batch):
                inp=self.torch.as_tensor(x[start:start+batch],dtype=self.torch.float32)
                p,_=self.forward(inp)
                results.append(inverse_signed_log(p.float().cpu().numpy()))
        return np.concatenate(results).astype('float64')

    def state(self):
        return {'model':{k:v.detach().cpu() for k,v in self.master.state_dict().items()},
                'optimizer':self.optimizer.state_dict(),'grad_scale':self.scale}

    def restore(self,state):
        self.master.load_state_dict(state['model'])
        self.optimizer.load_state_dict(state['optimizer'])
        self.scale=float(state.get('grad_scale',512.))
        if self.backend=='offload-cuda':
            self.work.load_state_dict(self.master.state_dict())
        else:
            for s in self.optimizer.state.values():
                for k,v in s.items():
                    if isinstance(v,self.torch.Tensor):s[k]=v.to(self.device)


def _identity(source,config):
    return {'schema':SCHEMA,'dataset_sha256':source['manifest']['dataset_sha256'],
            'original_prompt_corpus_sha256':source['manifest']['original_prompt_corpus_sha256'],
            'adapter_sha256':gamer.file_tree_sha256(source['source_adapter']),
            'source_head_sha256':source['head_sha256'],
            'source_lora_sha256':source['lora_sha256'],
            'source_step':source['lora_state']['step'],
            'model':source['manifest']['model'],
            'configuration':config}


def _result_metrics(rows,pred,*,radius):
    from tools.nanojev_gamer_v1_zero_line import target_zero_line_metrics
    return {'numeric':gamer.numeric_metrics(rows,pred),
            'zero_line':target_zero_line_metrics(rows,pred,radius=radius)}


def _write_predictions(path, rows, predictions, *, step):
    """Exact held-out example evidence; no targets are passed into a model."""
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=(
            'step','case_index','game_id','prompt_sha256','target','prediction',
            'absolute_error','correct_sign','target_zero','predicted_zero_within_1e_6'))
        writer.writeheader()
        for i,(row,pred) in enumerate(zip(rows,predictions)):
            target=float(row['answer']);value=float(pred)
            writer.writerow({'step':step,'case_index':i,'game_id':row['game_id'],
                'prompt_sha256':hashlib.sha256(gamer.original_prompt(row).encode('utf-8')).hexdigest(),
                'target':target,'prediction':value,'absolute_error':abs(value-target),
                'correct_sign':int(np.sign(target)==np.sign(value)),
                'target_zero':int(target==0.),
                'predicted_zero_within_1e_6':int(abs(value)<=1e-6)})


def run_expanded(run,*,device='cuda',output=None,resume=False,steps=2000,
                 samples=100,curvature=4.,hidden=DEFAULT_HIDDEN,max_length=None,
                 batch_size=4,capture_batch=2,backend='offload-cuda',precision='float16',
                 seed=47,lr=.0002,weight_decay=.0001,grad_clip=1.,
                 zero_focus=.5,zero_radius=1.,eval_every=100,save_every=100):
    import torch
    from tools.nanojev_qwen_c0_compressive_train import signed_log
    from tools.nanojev_gamer_v1_zero_line import ZeroLineSampler
    if steps<1:raise ValueError('GAMER_EXPANDED_POSITIVE_STEPS_REQUIRED')
    source=_source_inputs(run)
    hp=source['head_hp']
    config=configuration(samples=samples,curvature=curvature,hidden=hidden,
                  max_length=int(max_length or hp.get('max_length',1024)),
                  batch_size=batch_size,capture_batch=capture_batch,
                  backend=backend,precision=precision,seed=seed,lr=lr,
                  weight_decay=weight_decay,grad_clip=grad_clip,zero_focus=zero_focus,
                  zero_radius=zero_radius,eval_every=eval_every,save_every=save_every)
    if steps>0 and config['max_length']<100:
        raise ValueError('GAMER_EXPANDED_MAX_LENGTH_TOO_SMALL')
    if backend!='cpu' and not torch.cuda.is_available():
        raise RuntimeError('GAMER_EXPANDED_CUDA_UNAVAILABLE')
    if resume and output is None:
        raise ValueError('GAMER_EXPANDED_RESUME_OUTPUT_REQUIRED')
    out=(Path(output).resolve() if resume else
         _output_path(run,output,suffix='expanded-head-100'))
    source_path=Path(run).resolve()
    if out==source_path or source_path in out.parents or out in source_path.parents:
        raise ValueError('GAMER_EXPANDED_OUTPUT_OVERLAPS_SOURCE_RUN')
    if resume:
        if not out.is_dir() or not (out/'latest.pt').is_file():
            raise FileNotFoundError('GAMER_EXPANDED_RESUME_CHECKPOINT_MISSING')
    else:
        out.mkdir(parents=True,exist_ok=False)
    cache=out/'feature_cache'
    identity=_identity(source,config)
    existing=out/'provenance.json'
    if existing.is_file():
        if _json(existing)!=identity:raise ValueError('GAMER_EXPANDED_PROVENANCE_CHANGED')
    elif resume:
        raise ValueError('GAMER_EXPANDED_PROVENANCE_NOT_FOUND')
    else:
        gamer.atomic_json(existing,identity)
    full={p:source['full_split_rows'][p] for p in ('train','dev')}
    certified_cases,_=gamer.load_cases(Path(run)/'certified_pairs.jsonl',
         allow_partial=bool(source['manifest'].get('allow_partial_smoke')),
         include_catalog_extensions=bool(source['manifest'].get('bounded_catalog_extensions')),
         include_transition_games=bool(source['manifest'].get('bounded_transition_games')))
    full['test']=gamer.by_split(certified_cases,'test')
    if not all(full.values()):raise ValueError('GAMER_EXPANDED_MISSING_SOURCE_SPLIT')
    original={'train':source['selected']['train']['features'],
              'dev':source['selected']['dev']['features']}
    captured_model=None
    arrays={};capture_reports={}
    print(f'GAMER EXPANDED C0: {out}',flush=True)
    print(f'  LoRA step {identity["source_step"]} FROZEN; {samples} vectors x 1024; '
          f'head {tuple(hidden)}; training backend {backend}',flush=True)
    for part,rows in full.items():
        cache_path=cache/f'{part}.npz'
        cache_id={**identity,'split':part,
                  'prompt_sha256':hashlib.sha256(gamer.canonical(
                      [gamer.original_prompt(r) for r in rows]).encode('utf-8')).hexdigest()}
        if cache_path.is_file():
            x,summary=_read_cache(cache_path,cache_id,len(rows),config['input_width'])
            print(f'  reused {part} capture: {len(rows)} cases',flush=True)
        else:
            if captured_model is None:
                _,tok,model=gamer.model_components(identity['model'],device,
                                                     adapter_dir=source['source_adapter'])
                model.eval()
                captured_model=(tok,model)
            tok,model=captured_model
            x,summary=_capture(torch,model,tok,rows,device=device,
                      max_length=config['max_length'],batch_size=capture_batch,
                      samples=samples,curvature=curvature,
                      original_features=original.get(part))
            if gamer.file_tree_sha256(source['source_adapter'])!=identity['adapter_sha256']:
                raise ValueError('GAMER_EXPANDED_SOURCE_LORA_CHANGED_DURING_CAPTURE')
            _save_cache(cache_path,identity=cache_id,features=x,summary=summary)
            print(f'  captured {part}: {len(rows)} cases, '
                  f'cached_last_token_delta={summary["max_legacy_last_token_delta"]:.6g}',flush=True)
        arrays[part]=x;capture_reports[part]=summary
    if captured_model is not None:
        del captured_model,model,tok
        if torch.cuda.is_available():torch.cuda.empty_cache()
    if gamer.file_tree_sha256(source['source_adapter'])!=identity['adapter_sha256']:
        raise ValueError('GAMER_EXPANDED_FROZEN_LORA_CHANGED')
    gamer.atomic_json(out/'capture_report.json',capture_reports)
    # Allocate the 419M-weight encoder only after Qwen and all capture tensors
    # are off CUDA, so one 6GB GPU can run capture and training separately.
    gamer.set_seeds(seed)
    trainer=HeadTrainer(torch,make_expanded_head(torch,config['input_width'],hidden),
                    backend=backend,precision=precision,lr=lr,
                    weight_decay=weight_decay,clip=grad_clip)
    rng=np.random.default_rng(seed+9981)
    sampler=ZeroLineSampler(full['train'],focus=zero_focus,radius=zero_radius)
    expected=[float(r['answer']) for r in full['dev']]
    state_path=out/'latest.pt';best_path=out/'best_model.pt'
    start=0;best=float('inf');best_step=None;checks=[]
    if resume:
        state=torch.load(state_path,map_location='cpu',weights_only=True)
        if state.get('identity')!=identity or state.get('schema')!=SCHEMA:
            raise ValueError('GAMER_EXPANDED_CHECKPOINT_LINEAGE_MISMATCH')
        trainer.restore(state['trainer'])
        start=int(state['step']);best=float(state['best'])
        best_step=state.get('best_step');checks=list(state.get('checks',[]))
        gamer.restore_rng(torch,state['rng'],numpy_rng=rng)
        if steps<=start:raise ValueError('GAMER_EXPANDED_TARGET_NOT_GREATER_THAN_CHECKPOINT')
    def save(step):
        gamer.save_training_state(torch,state_path,{
            'schema':SCHEMA,'identity':identity,'step':step,
            'trainer':trainer.state(),'best':best,'best_step':best_step,
            'checks':checks,'rng':gamer.training_rng(torch,numpy_rng=rng)})
    if not resume:save(0)
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    for step in range(start+1,steps+1):
        ix,strata=sampler.draw(rng,batch_size)
        x=torch.as_tensor(arrays['train'][ix].copy(),dtype=torch.float32)
        y=torch.as_tensor(signed_log(np.array([float(full['train'][i]['answer']) for i in ix],
                                                dtype=np.float64)).astype('float32'))
        loss=trainer.update(x,y)
        eval_due=(step%eval_every==0 or step==steps)
        if eval_due:
            pred=trainer.predict(arrays['dev'],batch=batch_size)
            score=float(np.mean(np.abs(pred-np.asarray(expected))))
            metrics=_result_metrics(full['dev'],pred,radius=zero_radius)
            _write_predictions(out/'dev_evaluations'/f'step_{step:07d}.csv',full['dev'],pred,step=step)
            checks.append({'step':step,'dev_mae':score,'loss':loss,
                           'zero_line':metrics['zero_line']})
            if score<best:
                best=score;best_step=step
                gamer.save_training_state(torch,best_path,{
                    'schema':SCHEMA,'identity':identity,'step':step,
                    'model':{k:v.detach().cpu() for k,v in trainer.master.state_dict().items()},
                    'dev':metrics})
            print(f'GAMER EXPANDED step {step}/{steps}: '
                  f'loss={loss:.6g} dev_mae={score:.6g} '
                  f'best={best:.6g} at {best_step}',flush=True)
        elif step==start+1 or step%25==0:
            print(f'GAMER EXPANDED step {step}/{steps}: loss={loss:.6g}',flush=True)
        if step%save_every==0 or step==steps:save(step)
    checkpoint=torch.load(best_path,map_location='cpu',weights_only=True)
    trainer.master.load_state_dict(checkpoint['model'])
    if trainer.backend=='offload-cuda':trainer.work.load_state_dict(trainer.master.state_dict())
    elif trainer.backend=='full-cuda':trainer.master.to('cuda')
    dev_pred=trainer.predict(arrays['dev'],batch=batch_size)
    test_pred=trainer.predict(arrays['test'],batch=batch_size)
    _write_predictions(out/'dev_best.csv',full['dev'],dev_pred,step=best_step)
    _write_predictions(out/'test_best.csv',full['test'],test_pred,step=best_step)
    report={'schema':SCHEMA,'status':'EXPERIMENTAL_NOT_PROMOTED',
            'identity':identity,'steps':steps,'best_step':best_step,
            'best_dev_mae':best,'capture':capture_reports,
            'dev':_result_metrics(full['dev'],dev_pred,radius=zero_radius),
            'test':_result_metrics(full['test'],test_pred,radius=zero_radius),
            'checks':checks,'source_run_read_only':True,
            'no_lora_updates':True,'no_legacy_head_mutation':True}
    source_report=source['cycle']/'to_numerical'/'report.json'
    if source_report.is_file():
        prior=_json(source_report)
        report['legacy_c0_best_dev_mae']=prior.get('dev',{}).get('mae')
    if gamer.file_tree_sha256(source['source_adapter'])!=identity['adapter_sha256']:
        raise ValueError('GAMER_EXPANDED_SOURCE_ADAPTER_CHANGED_BEFORE_REPORT')
    if _sha(source['cycle']/'to_numerical'/'latest.pt')!=identity['source_head_sha256']:
        raise ValueError('GAMER_EXPANDED_SOURCE_HEAD_CHANGED_BEFORE_REPORT')
    if _sha(Path(run)/'certified_pairs.jsonl')!=identity['dataset_sha256']:
        raise ValueError('GAMER_EXPANDED_SOURCE_DATASET_CHANGED_BEFORE_REPORT')
    gamer.atomic_json(out/'final_report.json',report)
    print(f'GAMER EXPANDED COMPLETE: {out}/final_report.json; '
          f'best dev MAE={best:.6g}; source LoRA/C0 unchanged.',flush=True)
    return report
