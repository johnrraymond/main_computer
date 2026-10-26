#!/usr/bin/env python3
"""Read-only HybridLink embedding-geometry and one-step intervention diagnostic.

Uses the *already trained* interface-repair checkpoints, never trains or writes
any model source. Teacher-forced pairs are used ONLY for geometry reference;
free-running inference never receives a gold answer or teacher latent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_qwen_hybrid_interface_repair as repair
from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
from tools import nanojev_qwen_zero_line_backbone as zero

SCHEMA = 'nanojev.qwen3-06b.hybrid-latent-geometry.v1'
OUTPUT = Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_latent_geometry_v1'
CONDITIONS = ('text','marker_only','intact','zero','scale_0p1','norm_match','native_argmax')


def _safe_float(value):
    result=float(value)
    assert torch.isfinite(torch.tensor(result)), 'GEOMETRY_NONFINITE_STATISTIC'
    return result


def describe(values):
    vals=torch.as_tensor(values).detach().float().reshape(-1).cpu()
    assert vals.numel()>0 and bool(torch.isfinite(vals).all()),'GEOMETRY_INVALID_VECTOR_STATISTICS'
    return {'count':int(vals.numel()),'mean':_safe_float(vals.mean()),
            'median':_safe_float(vals.median()),
            'p05':_safe_float(torch.quantile(vals,0.05)),
            'p95':_safe_float(torch.quantile(vals,0.95)),
            'min':_safe_float(vals.min()),'max':_safe_float(vals.max())}


def measure_geometry(head, pairs, *, device='cpu', batch=64):
    x,y,is_exit=pairs
    assert x.ndim==2 and y.shape==x.shape and x.shape[0]>0,'GEOMETRY_PAIR_SHAPE'
    assert is_exit.shape==(x.shape[0],) and is_exit.dtype==torch.bool,'GEOMETRY_EXIT_MASK'
    assert bool(torch.isfinite(x).all()) and bool(torch.isfinite(y).all()),'GEOMETRY_NONFINITE_PAIRS'
    head.eval()
    guesses=[];pre_post=[]
    with torch.inference_mode():
        for a in range(0,len(x),batch):
            z=x[a:a+batch].to(device=device,dtype=next(head.parameters()).dtype)
            residual=head.proj2(head.act(head.proj1(head.pre_ln(z))))
            pre_post.append((z+residual).float().cpu())
            guesses.append(head(z).float().cpu())
    p=torch.cat(guesses)
    raw=torch.cat(pre_post)
    x=x.float().cpu();y=y.float().cpu()
    pnorm=p.norm(dim=-1);ynorm=y.norm(dim=-1)
    cosine=F.cosine_similarity(p,y,dim=-1,eps=1e-12)
    ratio=pnorm/ynorm.clamp_min(1e-12)
    assert bool(torch.isfinite(ratio).all()),'GEOMETRY_BAD_NORM_RATIO'
    def group(mask):
        assert bool(mask.any()),'GEOMETRY_EMPTY_GROUP'
        indices=torch.nonzero(mask).flatten()
        t=pnorm[indices];u=ynorm[indices];c=cosine[indices]
        return {'count':len(indices),'head_norm':describe(t),'target_norm':describe(u),
                'norm_ratio':describe(ratio[indices]),
                'fraction_norm_within_half_to_double':_safe_float(((ratio[indices]>=0.5)&(ratio[indices]<=2.)).float().mean()),
                'cosine_to_target':describe(c),
                'head_target_mse':_safe_float(F.mse_loss(p[indices],y[indices])),
                'direction_only_mse':_safe_float(F.mse_loss(F.normalize(p[indices],dim=-1),F.normalize(y[indices],dim=-1))),
                'source_hidden_norm':describe(x[indices].norm(dim=-1)),
                'before_post_layernorm_norm':describe(raw[indices].norm(dim=-1))}
    result={'all':group(torch.ones(len(x),dtype=torch.bool)),
            'non_exit':group(~is_exit), 'exit':group(is_exit)} if bool((~is_exit).any()) and bool(is_exit.any()) else {'all':group(torch.ones(len(x),dtype=torch.bool))}
    # Dev-only scalar calibration: NEVER compute a scale using evaluation/test pairs.
    scale=_safe_float(ynorm.median()/pnorm.median().clamp_min(1e-12))
    least_squares=_safe_float((p*y).sum()/p.square().sum().clamp_min(1e-12))
    result['calibration']={'median_norm_scale':scale,
                           'least_squares_scale_signed':least_squares,
                           'scaled_mse':_safe_float(F.mse_loss(p*scale,y)),
                           'raw_mse':_safe_float(F.mse_loss(p,y))}
    return result


def sample_vocabulary_norms(model, *, sample_size=4096, seed=713):
    table=model.backbone.get_input_embeddings().weight.detach()
    total=table.shape[0]
    assert total>2 and sample_size>=1,'GEOMETRY_VOCAB_SIZE'
    assert 0<=model.start_id<total and 0<=model.end_id<total and model.start_id!=model.end_id,'GEOMETRY_MARKER_IDS_INVALID'
    valid=torch.arange(total,dtype=torch.long)
    valid=valid[(valid!=model.start_id)&(valid!=model.end_id)]
    rng=torch.Generator(device='cpu').manual_seed(seed)
    inds=valid[torch.randperm(len(valid),generator=rng)[:min(len(valid),sample_size)]].to(table.device)
    sampled=table.index_select(0,inds).float().norm(dim=-1)
    markers=table[torch.tensor([model.start_id,model.end_id],device=table.device)].float().norm(dim=-1)
    return {'sampled_original_token_norm':describe(sampled),
            'marker_input_norms':{'entry':_safe_float(markers[0]),'exit':_safe_float(markers[1])},
            'sample_size':int(len(inds))}


class SubstituteHead(nn.Module):
    """Temporary inference-only substitution; original module is always restored."""
    def __init__(self, original, mode, *, native=None, calibration=None):
        super().__init__()
        assert mode in ('zero','scale_0p1','norm_match','native_argmax'),'GEOMETRY_BAD_MODE'
        if mode=='norm_match':
            assert calibration is not None and 0<float(calibration)<1e6,'GEOMETRY_BAD_CALIBRATION'
        if mode=='native_argmax':
            assert native is not None and native.ndim==2,'GEOMETRY_BAD_NATIVE_REFERENCE'
        self.original=original
        self.mode=mode
        self.native=native
        self.calibration=calibration

    def forward(self,hidden):
        pred=self.original(hidden)
        if self.mode=='zero':return torch.zeros_like(pred)
        if self.mode=='scale_0p1':return pred*0.1
        if self.mode=='norm_match':return pred*float(self.calibration)
        return self.native.to(device=pred.device,dtype=pred.dtype).expand_as(pred)


def native_next_embedding(model,ids):
    """Use Qwen's OWN argmax next-token embedding; no oracle/teacher input."""
    with torch.inference_mode():
        marked=torch.cat((ids,ids.new_full((1,1),model.start_id)),dim=1)
        _,logits,_=model._step(input_ids=marked,mask=torch.ones_like(marked))
        token=logits.argmax(dim=-1)
        return model.backbone.get_input_embeddings()(token).detach().float(),int(token.item())


def decode_marker_only(model,tokenizer,ids,budget):
    """Enter and exit with NO latent embedding, preserving ordinary KV history."""
    assert 1<=budget<=256,'GEOMETRY_BAD_BUDGET'
    markers=ids.new_tensor([[model.start_id,model.end_id]])
    prompt=torch.cat((ids,markers),dim=1)
    mask=torch.ones_like(prompt)
    if ids.device.type=='cuda':torch.cuda.synchronize(ids.device)
    t0=time.perf_counter()
    with torch.inference_mode():
        _,logits,cache=model._step(input_ids=prompt,mask=mask)
        tokens=[]
        for _ in range(budget):
            nextid=logits.argmax(-1,keepdim=True)
            token=int(nextid.item())
            if tokenizer.eos_token_id is not None and token==tokenizer.eos_token_id:break
            tokens.append(token)
            mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
            _,logits,cache=model._step(input_ids=nextid,cache=cache,mask=mask)
    if ids.device.type=='cuda':torch.cuda.synchronize(ids.device)
    completion=tokenizer.decode(tokens,skip_special_tokens=True)
    return {'completion':completion[:300],'tokens':len(tokens),'latents':0,
            'seconds':time.perf_counter()-t0,**paired.parse_answer(completion)}


def decode_intervention(model,tokenizer,ids,mode,*,budget,calibration,native=None):
    assert mode in CONDITIONS,'GEOMETRY_UNKNOWN_CONDITION'
    if mode=='text':return paired.decode_once(model,tokenizer,ids,0,budget)
    if mode=='marker_only':return decode_marker_only(model,tokenizer,ids,budget)
    if mode=='intact':return paired.decode_once(model,tokenizer,ids,1,budget)
    original=model.head
    model.head=SubstituteHead(original,mode,native=native,calibration=calibration)
    try:
        return paired.decode_once(model,tokenizer,ids,1,budget)
    finally:
        model.head=original


def _preflight(root):
    root=Path(root)
    gate=json.loads((root/'gate_result.json').read_text(encoding='utf8'))
    aligncfg=json.loads((root/'align'/'config.json').read_text(encoding='utf8'))
    alignresult=json.loads((root/'align'/'result.json').read_text(encoding='utf8'))
    assert gate['schema']==repair.SCHEMA and aligncfg['schema']==repair.SCHEMA,'GEOMETRY_SCHEMA_DRIFT'
    assert gate.get('marker_checkpoint_roundtrip') is True and gate.get('tied_output_marker_override') is True and gate.get('markers_pass') is True,'GEOMETRY_REQUIRES_CORRECTED_MARKER_GATE'
    assert alignresult['status']=='HEAD_REALIGNED_UNCERTIFIED','GEOMETRY_HEAD_NOT_COMPLETE'
    ff=repair.fingerprint(root/'format'/'best.pt');hf=repair.fingerprint(root/'align'/'best.pt')
    assert gate['format_checkpoint_sha256']==ff and gate['aligned_head_sha256']==hf,'GEOMETRY_GATE_CHECKPOINT_MISMATCH'
    assert aligncfg['format_checkpoint_sha256']==ff,'GEOMETRY_ALIGNMENT_CHECKPOINT_MISMATCH'
    return gate,{'format':ff,'head':hf}


def run(*,device='cuda',base=repair.hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
        interface=repair.ROOT,output=OUTPUT,cases=6,budget=48,skip_decode=False,
        loader=None,allow_download=False):
    interface,base,numerical,output=map(Path,(interface,base,numerical,output))
    assert cases>=6 and cases%6==0 and cases<=18,'GEOMETRY_BALANCED_CASE_COUNT'
    assert 1<=budget<=256,'GEOMETRY_INVALID_TOKEN_BUDGET'
    for path in (interface,base,numerical):
        assert path.resolve()!=output.resolve() and path.resolve() not in output.resolve().parents and output.resolve() not in path.resolve().parents,'GEOMETRY_OUTPUT_SOURCE_OVERLAP'
    assert not output.exists() or not any(output.iterdir()),'GEOMETRY_OUTPUT_EXISTS_REFUSED'
    _, hashes=_preflight(interface)
    model,tok,_,fc,_=repair._load_format(device=device,base=base,numerical=numerical,
                          output=interface,loader=loader,allow_download=allow_download)
    aligncfg=json.loads((interface/'align'/'config.json').read_text(encoding='utf8'))
    cp=torch.load(interface/'align'/'best.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==repair.SCHEMA and cp['config_hash']==repair._hash_config(aligncfg),'GEOMETRY_HEAD_STATE_DRIFT'
    model.head.to(device=device,dtype=torch.float32)
    model.head.load_state_dict(cp['head'],strict=True)
    model.head.requires_grad_(False);model.backbone.requires_grad_(False)
    model.head.eval();model.backbone.eval()
    data=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])
    heldout=data['test'][:cases]
    assert len(heldout)==cases,'GEOMETRY_INSUFFICIENT_CASES'
    # Teacher-forced dev/test pairs are reference measurements, not inference inputs.
    dev_pairs=repair._pairs(model,tok,data['dev'][:6],device,fc['max_seq'])
    test_pairs=repair._pairs(model,tok,heldout,device,fc['max_seq'])
    dev=measure_geometry(model.head,dev_pairs,device=device)
    test=measure_geometry(model.head,test_pairs,device=device)
    calibration=dev['calibration']['median_norm_scale']
    assert 1e-6<calibration<1e6,'GEOMETRY_UNSAFE_CALIBRATION'
    vocabulary=sample_vocabulary_norms(model)
    print('HYBRID_GEOMETRY_MEASURE='+json.dumps({'dev':dev,'test':test,'vocabulary':vocabulary,
          'calibration_used_for_inference':calibration},sort_keys=True),flush=True)
    records=[]
    if not skip_decode:
        with torch.inference_mode():
            for item in heldout:
                ids=torch.tensor([tok(f"Question: Compute {item['expression']}.\nReasoning: ",
                                   add_special_tokens=False)['input_ids']],
                                  device=device,dtype=torch.long)
                assert ids.shape[1]>0,'GEOMETRY_EMPTY_PROMPT'
                native,tid=native_next_embedding(model,ids)
                modes={}
                for cond in CONDITIONS:
                    result=decode_intervention(model,tok,ids,cond,budget=budget,
                           calibration=calibration,native=native)
                    modes[cond]={'answer':result['answer_label'],'parseable':result['answer_label'] is not None,
                         'completion':result['completion'],'tokens':result['tokens'],'seconds':result['seconds']}
                row={'id':item['id'],'expected':item['answer'],'family':item['family'],
                     'sign':item['sign'],'native_argmax_token_id':tid,'variants':modes}
                records.append(row)
                print('HYBRID_GEOMETRY_CASE='+json.dumps(row,sort_keys=True,ensure_ascii=False),flush=True)
    scores={c:{'correct':sum(r['variants'][c]['answer']==r['expected'] for r in records),
               'parseable':sum(r['variants'][c]['parseable'] for r in records),
               'cases':len(records)} for c in CONDITIONS} if records else {}
    # Cross-check all external checkpoint bytes *after* inference.
    _,check=_preflight(interface)
    assert check==hashes,'GEOMETRY_CHECKPOINT_MUTATED'
    assert all(not p.requires_grad for p in model.parameters()),'GEOMETRY_PARAMETERS_UNFROZEN'
    result={'schema':SCHEMA,'status':'READ_ONLY_DIAGNOSTIC_COMPLETE_UNCERTIFIED',
            'format_checkpoint_sha256':hashes['format'],'head_checkpoint_sha256':hashes['head'],
            'source_mutations':0,'optimizer_steps':0,'oracle_latents_supplied_at_inference':0,
            'teacher_forcing_geometry_only':True,'calibration_source':'dev_only',
            'dataset_fingerprint':zero.fingerprint(data),
            'deeper_training_permitted':False,'cases':cases,'score':scores,
            'geometry':{'dev':dev,'heldout':test,'vocabulary':vocabulary},
            'calibration_used_for_inference':calibration,
            'interpretation':'Measured geometry and matched one-step controls only; not a training or reasoning certification.',
            'records':records}
    output.mkdir(parents=True,exist_ok=True)
    zero.atomic_json(output/'result.json',result)
    print('HYBRID_GEOMETRY_RESULT='+json.dumps({k:v for k,v in result.items() if k not in ('records','geometry')},sort_keys=True),flush=True)
    print('HYBRID_GEOMETRY_REPORT='+str((output/'result.json').resolve()),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--baseline',type=Path,default=repair.hybrid.DEFAULT_OUTPUT)
    p.add_argument('--numerical',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--interface',type=Path,default=repair.ROOT)
    p.add_argument('--output',type=Path,default=OUTPUT)
    p.add_argument('--cases',type=int,default=6)
    p.add_argument('--max-new-tokens',type=int,default=48)
    p.add_argument('--skip-decode',action='store_true',help='Fast geometry-only pass; no free-running generation')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        run(device=args.device,base=args.baseline,numerical=args.numerical,
            interface=args.interface,output=args.output,cases=args.cases,budget=args.max_new_tokens,
            skip_decode=args.skip_decode,allow_download=args.allow_model_download)
        return 0
    except (AssertionError,RuntimeError,ValueError,KeyError,FileNotFoundError,OSError) as exc:
        print('HYBRID_GEOMETRY_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':sys.exit(main())
