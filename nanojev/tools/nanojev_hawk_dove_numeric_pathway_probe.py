"""Read-only T02 numerical information pathway experiment (Qwen -> V3).

Reuses the v1 continuous benchmark's immutable labels, split, and penultimate
features. Captures Qwen/V3 *intermediate tensors*, never supplies labels during
feature capture, then probes recovery of literal operand counts with identical
train-only supervised readouts. The existing test split remains sealed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile

import numpy as np
from tools import nanojev_hawk_dove_continuous_benchmark as prior
from tools import nanojev_hawk_dove_million as hd

SCHEMA = 'nanojev.hawk-dove.numeric-pathway-probe.v1'
PLANES = ('qwen_last4', 'qwen_mean', 'qwen_operand_tokens', 'v3_input', 'v3_output')
READOUTS = ('linear', 'mlp')
COUNT_SCALE = 1_000_000.0


def digest(path):
    return prior.digest(path)


def jsonwrite(path, data):
    prior.write_json(path, data)


def identify(args):
    # Verify the prior baseline and the donor rather than working from orphaned
    # or stale features. Important: do not modify the original benchmark module;
    # its SHA is recorded in the original experiment's manifest.
    previous = args.previous
    if previous == args.root or previous in args.root.parents or args.root in previous.parents:
        raise ValueError('INDEPENDENT_PATHWAY_ROOT_REQUIRED')
    if 'test' in args.splits:
        raise ValueError('HELD_OUT_TEST_SPLIT_MUST_REMAIN_SEALED')
    import types
    oldargs=types.SimpleNamespace(root=previous,donor=args.donor,source=args.source,teacher=args.teacher)
    prior.verify(oldargs)
    for split in args.splits:
        prior.load_dataset(oldargs, split)
    return {
        'schema':SCHEMA, 'source_sha256':digest(Path(__file__)),
        'prior_module_sha256':digest(Path(prior.__file__)),
        'previous_manifest_sha256':digest(previous/'manifest.json'),
        'previous_train_features_sha256':digest(previous/'features-train.npz'),
        'previous_dev_features_sha256':digest(previous/'features-dev.npz'),
        'donor_sha256':digest(args.donor/'latest.pt'),
        'teacher_sha256':digest(args.teacher), 'source_checkpoint_sha256':digest(args.source),
        'splits':['train','dev'], 'planes':list(PLANES), 'readouts':list(READOUTS),
        'cap_corrected':10_000_000, 'no_backbone_training':True, 'test_split_read':False,
        'epochs':args.epochs, 'learning_rate':args.learning_rate,
        'batch_size':args.train_batch_size, 'seed':args.seed,
    }


def prepare(args):
    args.root.mkdir(parents=True,exist_ok=True)
    manifest=args.root/'manifest.json'
    identity=identify(args)
    if manifest.exists():
        old=json.loads(manifest.read_text(encoding='utf8'))
        if old!=identity:raise ValueError('PATHWAY_MANIFEST_IDENTITY_DRIFT')
    else:
        if any(args.root.iterdir()):raise ValueError('NONEMPTY_UNINITIALIZED_PATHWAY_ROOT')
        jsonwrite(manifest,identity)
    return identity


def verify(args):
    path=args.root/'manifest.json'
    if not path.exists():raise FileNotFoundError('RUN_PREPARE_FIRST')
    saved=json.loads(path.read_text(encoding='utf8'))
    if saved!=identify(args):raise ValueError('PATHWAY_MANIFEST_IDENTITY_DRIFT')
    return saved


def literal_spans(prompt):
    """Locate the T02 input operands *in the unmodified text* by syntax only.

    No target value, oracle result or other teacher field participates. The
    annotations following an integer do not change the integer's offsets.
    """
    # The role must be verified from the full unannotated expression, even
    # though raw prompt annotations separate literals from their payoff tags.
    stripped=hd.strip_and_validate_bars(prompt,hd.REP_SCIENCE)
    validated=prior.operands_from_t02(prompt)
    matches=list(re.finditer(r'\(MUL\s+(\d+)',prompt))
    if len(matches)!=2:raise ValueError('T02_LITERAL_SPAN_COUNT_DRIFT')
    starts=[(int(m.group(1)),m.span(1)) for m in matches]
    if any(starts[i][0]/COUNT_SCALE!=validated[i] for i in range(2)):
        raise ValueError('T02_LITERAL_ROLE_OR_VALUE_MISMATCH')
    if 'HAWK_HAWK_PAYOFF' not in stripped or 'HAWK_DOVE_PAYOFF' not in stripped:
        raise ValueError('T02_PAYOFF_SYMBOL_MISSING')
    return [span for _value,span in starts]


def token_span_indices(tokenizer,prompt,expected_length):
    try:
        enc=tokenizer(prompt,add_special_tokens=False,return_offsets_mapping=True)
    except (TypeError, NotImplementedError, ValueError) as exc:
        raise RuntimeError('TOKENIZER_OFFSETS_REQUIRED_FOR_EXACT_OPERAND_PROBE') from exc
    offsets=enc['offset_mapping']
    if len(offsets)!=expected_length:
        raise RuntimeError('TOKENIZER_IDS_LENGTH_MISMATCH')
    indices=[]
    for start,end in literal_spans(prompt):
        which=[i for i,(left,right) in enumerate(offsets)
               if right>left and left<end and right>start]
        if not which:raise RuntimeError('NO_QWEN_TOKENS_OVERLAP_LITERAL')
        indices.append(which)
    if len(indices)!=2 or indices[0]==indices[1]:
        raise RuntimeError('T02_OPERAND_TOKEN_SPANS_COLLAPSED')
    return indices


def pools_from_seq(seq,mask,tokenizer,prompts):
    import torch
    mask=mask.bool()
    w=mask.unsqueeze(-1).to(seq.dtype)
    pooled=(seq*w).sum(1)/w.sum(1).clamp(min=1)
    numeric=[]
    for row,prompt in enumerate(prompts):
        indices=token_span_indices(tokenizer,prompt,int(mask[row].sum().item()))
        n=torch.stack([seq[row,index,:].float().mean(0) for index in indices],dim=0)
        numeric.append(n.flatten())
    return pooled.cpu().float().numpy(),torch.stack(numeric).cpu().float().numpy()


def capture(args,split):
    import torch
    from tools import nanojev_game_transform_experiment as ex
    from tools import nanojev_altreg_math_v3_decoder_experiment as v3
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    verify(args)
    if split not in ('train','dev'):raise ValueError('TEST_CAPTURE_FORBIDDEN')
    file=args.root/f'pathway-{split}.npz'
    if file.exists():
        load_capture(args,split)
        print(json.dumps({'event':'pathway_capture_reused','split':split}),flush=True)
        return
    oldargs=arg_namespace(args)
    reference=prior.load_dataset(oldargs,split)
    rows=prior.dataset_rows(args.teacher,split)
    if [r['case_id'] for r in rows]!=reference['case_ids'].tolist():
        raise ValueError('PREVIOUS_FEATURE_ROW_ORDER_DRIFT')
    checkpoint=torch.load(args.donor/'latest.pt',map_location='cpu',weights_only=True)
    model=ex.load_current(args.source,args.device,'float16' if args.device=='cuda' else 'float32',False,int(checkpoint['config']['seed']))
    model['decoder'].load_state_dict(checkpoint['decoder'],strict=True)
    model['gamehead'].load_state_dict(checkpoint['game_operator_head'],strict=True)
    model['decoder'].eval();model['gamehead'].eval()
    for p in model['decoder'].parameters():p.requires_grad_(False)
    arrays={plane:[] for plane in PLANES}
    corrected=[]
    model_input=[];model_output=[]
    def before(_module,inputs):model_input.append(inputs[0].detach().float().cpu().clone())
    def after(_module,_inputs,out):model_output.append(out.detach().float().cpu().clone())
    hookin=model['decoder'].body.register_forward_pre_hook(before)
    hookout=model['decoder'].body.register_forward_hook(after)
    try:
        with torch.inference_mode():
            for pos in range(0,len(rows),args.capture_batch_size):
                batch=rows[pos:pos+args.capture_batch_size]
                prompts=[r['prompt'] for r in batch]
                if not all(rt.token_eligible(model,p) for p in prompts):
                    raise RuntimeError('INELIGIBLE_T02_PROMPT')
                flat,seq,mask=v3.full_capture(model['backbone'],model['tokenizer'],args.device,
                    prompts,model['config']['max_prompt_tokens'])
                arrays['qwen_last4'].append(flat.numpy())
                mean,number=pools_from_seq(seq,mask,model['tokenizer'],prompts)
                arrays['qwen_mean'].append(mean)
                arrays['qwen_operand_tokens'].append(number)
                model_input.clear();model_output.clear()
                h=model['parent'].extract(flat.to(args.device))
                pxy=model['parent'].xy_coord(h)
                ops=torch.full((len(prompts),),model['base'].OP_IDS['written_sub'],device=args.device,dtype=torch.long)
                out=model['decoder'](h,pxy,ops,seq.to(args.device),mask.to(args.device))
                if len(model_input)!=1 or len(model_output)!=1:
                    raise RuntimeError('V3_BODY_HOOK_COUNT_DRIFT')
                arrays['v3_input'].append(model_input[0].flatten(1).numpy())
                arrays['v3_output'].append(model_output[0].flatten(1).numpy())
                correct=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000)
                corrected.append(correct.detach().cpu().float().numpy())
                if (pos//args.capture_batch_size+1)%25==0:
                    print(json.dumps({'event':'pathway_capture_progress','split':split,
                        'completed':pos+len(batch),'total':len(rows)}),flush=True)
    finally:
        hookin.remove();hookout.remove()
    saved={name:np.concatenate(part,axis=0).astype(np.float32) for name,part in arrays.items()}
    prediction=np.concatenate(corrected,axis=0)
    if not np.allclose(saved['v3_output'].reshape(reference['features'].shape),reference['features'],atol=2e-4,rtol=2e-4):
        raise RuntimeError('EXISTING_PRETRAINED_V3_FEATURES_DO_NOT_MATCH_CAPTURE')
    if not np.all(np.isfinite(prediction)):
        raise RuntimeError('NONFINITE_CORRECTED_V3_PREDICTIONS')
    saved['corrected_v3']=prediction
    saved['metadata']=np.asarray(json.dumps({'schema':SCHEMA,'split':split,
        'identity_sha256':digest(args.root/'manifest.json'),
        'prior_features_sha256':digest(args.previous/f'features-{split}.npz')},sort_keys=True))
    with tempfile.NamedTemporaryFile('wb',dir=args.root,delete=False,suffix='.npz') as f:
        temporary=Path(f.name)
        np.savez_compressed(f,**saved)
    try:
        if file.exists():raise FileExistsError('PATHWAY_CAPTURE_EXISTS')
        os.replace(temporary,file)
    finally:temporary.unlink(missing_ok=True)
    jsonwrite(args.root/f'pathway-{split}.json',{'sha256':digest(file),
        'n':len(rows),'dimensions':{p:int(saved[p].shape[1]) for p in PLANES},
        'baseline_cap':10_000_000})
    print(json.dumps({'event':'pathway_features_saved','split':split,'n':len(rows),
          'dimensions':{p:int(saved[p].shape[1]) for p in PLANES}}),flush=True)


def arg_namespace(args):
    from types import SimpleNamespace
    return SimpleNamespace(root=args.previous,donor=args.donor,teacher=args.teacher,
                           source=args.source)


def load_capture(args,split):
    file=args.root/f'pathway-{split}.npz'
    audit=args.root/f'pathway-{split}.json'
    if not file.exists() or not audit.exists():
        raise FileNotFoundError('CAPTURE_PATHWAY_FIRST:'+split)
    if digest(file)!=json.loads(audit.read_text(encoding='utf8'))['sha256']:
        raise ValueError('PATHWAY_CAPTURE_DIGEST_MISMATCH')
    with np.load(file,allow_pickle=False) as z:
        data={k:np.array(z[k]) for k in z.files}
    meta=json.loads(data['metadata'].item())
    if meta!={'schema':SCHEMA,'split':split,'identity_sha256':digest(args.root/'manifest.json'),
             'prior_features_sha256':digest(args.previous/f'features-{split}.npz')}:
        raise ValueError('PATHWAY_CAPTURE_PROVENANCE_MISMATCH')
    n=len(prior.load_dataset(arg_namespace(args),split)['targets'])
    if any(data[p].ndim!=2 or data[p].shape[0]!=n for p in PLANES):
        raise ValueError('PATHWAY_CAPTURE_INVALID_PLANES')
    if data['corrected_v3'].shape!=(n,2):raise ValueError('INVALID_CORRECTED_V3_SHAPE')
    if not all(np.isfinite(data[p]).all() for p in (*PLANES,'corrected_v3')):
        raise ValueError('NONFINITE_PATHWAY_DATA')
    return data


def model_for(dim,kind):
    from torch import nn
    if kind=='linear':return nn.Linear(dim,2)
    if kind=='mlp':return nn.Sequential(nn.Linear(dim,96),nn.GELU(),nn.Dropout(p=.1),nn.Linear(96,2))
    raise ValueError('INVALID_NUMERICAL_PROBE_KIND')


def standardize_params(x):
    mean=x.mean(0)
    std=x.std(0)
    std=np.where(std<1e-4,1.,std)
    return mean.astype(np.float32),std.astype(np.float32)


def learn(args,plane,kind):
    import torch
    import torch.nn.functional as F
    target=args.root/f'probe-{plane}-{kind}.pt'
    if target.exists():
        checked=load_probe(args,plane,kind)
        print(json.dumps({'event':'pathway_probe_reused','plane':plane,'kind':kind}),flush=True)
        return
    alltrain=load_capture(args,'train')
    train=prior.load_dataset(arg_namespace(args),'train')
    torch.manual_seed(args.seed)
    model=model_for(alltrain[plane].shape[1],kind).to(args.train_device)
    mean,std=standardize_params(alltrain[plane])
    x=np.clip((alltrain[plane]-mean)/std,-20,20).astype(np.float32)
    xt=torch.from_numpy(x).to(args.train_device)
    yt=torch.from_numpy(train['operands']).to(args.train_device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=.003)
    gen=torch.Generator().manual_seed(args.seed)
    for epoch in range(1,args.epochs+1):
        model.train()
        losses=[]
        for ids in torch.randperm(len(xt),generator=gen).split(args.train_batch_size):
            ids=ids.to(args.train_device)
            guess=model(xt[ids])
            loss=F.smooth_l1_loss(guess,yt[ids],beta=.05)
            if not bool(torch.isfinite(loss)):raise RuntimeError('NONFINITE_PATHWAY_LOSS')
            optimizer.zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),10)
            optimizer.step()
            losses.append(loss.detach().item())
        if epoch%max(1,args.epochs//4)==0:
            print(json.dumps({'event':'pathway_train_progress','plane':plane,'kind':kind,
                  'epoch':epoch,'total':args.epochs,'loss':float(np.mean(losses))}),flush=True)
    state={'schema':SCHEMA,'plane':plane,'kind':kind,'epochs':args.epochs,
         'identity_sha256':digest(args.root/'manifest.json'),
         'capture_train_sha256':digest(args.root/'pathway-train.npz'),
         'mean':torch.from_numpy(mean),'std':torch.from_numpy(std),
         'weights':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()}}
    with tempfile.NamedTemporaryFile('wb',dir=args.root,delete=False,suffix='.pt') as f:tmp=Path(f.name)
    try:
        torch.save(state,tmp)
        if target.exists():raise FileExistsError('PATHWAY_PROBE_ALREADY_EXISTS')
        os.replace(tmp,target)
    finally:tmp.unlink(missing_ok=True)
    print(json.dumps({'event':'pathway_probe_fitted','plane':plane,'kind':kind}),flush=True)


def load_probe(args,plane,kind):
    import torch
    file=args.root/f'probe-{plane}-{kind}.pt'
    saved=torch.load(file,map_location='cpu',weights_only=True)
    if (saved['schema']!=SCHEMA or saved['plane']!=plane or saved['kind']!=kind
        or saved['epochs']!=args.epochs or saved['identity_sha256']!=digest(args.root/'manifest.json')
        or saved['capture_train_sha256']!=digest(args.root/'pathway-train.npz')):
        raise ValueError('PATHWAY_PROBE_IDENTITY_DRIFT')
    model=model_for(saved['mean'].numel(),kind).to(args.train_device)
    model.load_state_dict(saved['weights'],strict=True);model.eval()
    return model,saved['mean'].numpy(),saved['std'].numpy()


def predicted_operands(args,plane,kind,split):
    import torch
    data=load_capture(args,split)
    model,mean,std=load_probe(args,plane,kind)
    x=np.clip((data[plane]-mean)/std,-20,20).astype(np.float32)
    results=[]
    with torch.inference_mode():
        for idx in range(0,len(x),256):
            results.append(model(torch.from_numpy(x[idx:idx+256]).to(args.train_device)).cpu().numpy())
    return np.concatenate(results,axis=0)


def operand_metrics(pred,truth):
    delta=np.abs(pred-truth)*COUNT_SCALE
    return {'mae_a_count':float(np.mean(delta[:,0])),
            'mae_b_count':float(np.mean(delta[:,1])),
            'both_within_5000_counts':float(np.mean(np.all(delta<=5000,axis=1))),
            'both_within_25000_counts':float(np.mean(np.all(delta<=25000,axis=1))),
            'max_count_error':float(np.max(delta))}


def fit_all(args):
    verify(args)
    load_capture(args,'train');load_capture(args,'dev')
    for plane in PLANES:
        for kind in READOUTS:learn(args,plane,kind)


def learned_arithmetic_map(train):
    # The target arithmetic is fit ONLY from the previous TRAIN rows, not
    # hardcoded from the known game rule or inferred from held-out data.
    m=np.column_stack([train['operands'].astype(np.float64),np.ones(len(train['operands']))])
    return np.linalg.lstsq(m,train['targets'].astype(np.float64)/COUNT_SCALE,rcond=None)[0]


def report(args):
    verify(args)
    train=prior.load_dataset(arg_namespace(args),'train')
    coeffs=learned_arithmetic_map(train)
    record={'schema':SCHEMA,'benchmark_feature_policy':'same_old_train_dev_ids_and_masks',
            'baseline_decode_cap':10_000_000,
            'arithmetic_map':'training_only_least_squares_no_oracle_at_inference',
            'development_labels_never_used_for_training':True,'test_split_sealed':True,
            'report':{}}
    for split in ('train','dev'):
        data=prior.load_dataset(arg_namespace(args),split)
        captured=load_capture(args,split)
        evaluation={
            'old_V3_cap':prior.accuracy(data['native'],data['targets']),
            'corrected_V3_10M':prior.accuracy(captured['corrected_v3'],data['targets']),
            'numerical_channel_affine':prior.accuracy(
                np.column_stack([data['operands'].astype(np.float64),
                                 np.ones(len(data['operands']))])@coeffs*COUNT_SCALE,
                data['targets']),
            'planes':{},
        }
        for plane in PLANES:
            evaluation['planes'][plane]={}
            for kind in READOUTS:
                estimates=predicted_operands(args,plane,kind,split)
                # When the true input count has been recovered, what numerical
                # performance could be achieved by the *learned* arithmetic map?
                numeric=np.column_stack([estimates.astype(np.float64),
                                         np.ones(len(estimates))])@coeffs*COUNT_SCALE
                evaluation['planes'][plane][kind]={
                    'operands':operand_metrics(estimates,data['operands']),
                    'projected_XY':prior.accuracy(numeric,data['targets']),
                }
        record['report'][split]=evaluation
        print(json.dumps({'event':'pathway_probe_results','split':split,
              'baseline':{k:evaluation[k]['joint_5pct'] for k in
                ('old_V3_cap','corrected_V3_10M','numerical_channel_affine')},
              'operand_mae':{plane:{kind:evaluation['planes'][plane][kind]['operands']['mae_a_count']
                         for kind in READOUTS} for plane in PLANES}},sort_keys=True),flush=True)
    result=args.root/'pathway-results.json'
    if result.exists():
        if json.loads(result.read_text(encoding='utf8'))!=record:
            raise ValueError('PATHWAY_REPORT_EXISTS_WITH_DIFFERENT_CONTENT')
    else:jsonwrite(result,record)
    return record


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('all','prepare','capture','fit','report'))
    p.add_argument('--previous',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_t02_continuous_benchmark_v1')
    p.add_argument('--root',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_numeric_pathway_probe_v1')
    p.add_argument('--donor',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_million_scientific_v2')
    p.add_argument('--source',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--teacher',type=Path,default=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3'))
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--train-device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--capture-batch-size',type=int,default=4)
    p.add_argument('--train-batch-size',type=int,default=128)
    p.add_argument('--epochs',type=int,default=80)
    p.add_argument('--seed',type=int,default=20261019)
    p.add_argument('--learning-rate',type=float,default=.001)
    return p


def main(argv=None):
    args=build_parser().parse_args(argv)
    if not (1<=args.capture_batch_size<=32 and 1<=args.train_batch_size<=8192
            and 1<=args.epochs<=3000 and 0<args.learning_rate<=0.1):
        raise ValueError('INVALID_PATHWAY_PROBE_ARGUMENTS')
    for attr in ('previous','root','donor','source','teacher'):
        setattr(args,attr,getattr(args,attr).expanduser().resolve())
    args.splits=('train','dev')
    if args.stage in ('prepare','all'):prepare(args)
    if args.stage in ('capture','all'):
        verify(args)
        for split in args.splits:capture(args,split)
    if args.stage in ('fit','all'):fit_all(args)
    if args.stage in ('report','all'):report(args)


if __name__=='__main__':main()
