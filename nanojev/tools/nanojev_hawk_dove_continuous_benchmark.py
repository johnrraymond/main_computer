"""Read-only T02 output-coordinate ablations on frozen NanoJev V3 decoder features.

Methods: direct scaled regression, symlog regression, symlog two-hot, and an
explicit numerical-operand linear control. Qwen, parent, pretrained V3 trunk,
and the scientific-v2 checkpoint are never updated. The held-out test split
is sealed until the user explicitly names a winning method.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile

import numpy as np

from tools import nanojev_hawk_dove_million as hd

SCHEMA='nanojev.hawk-dove.continuous-t02.v1'
METHODS=('direct','symlog','twohot','operands')
SCALE=1_000_000.0
LOG_SCALE=100_000.0
GRID_MIN=-4.0
GRID_MAX=4.0
GRID_SIZE=257


def digest(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as stream:
        for c in iter(lambda:stream.read(1<<20),b''):h.update(c)
    return h.hexdigest()


def write_json(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False,suffix='.json') as f:
        temp=Path(f.name)
        json.dump(data,f,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n')
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def scientific_identity(args):
    import torch
    from tools import nanojev_hawk_dove_scientific_train as science
    cp_path=args.donor/'latest.pt'
    if not cp_path.is_file():raise FileNotFoundError('DONOR_CHECKPOINT_MISSING:'+str(cp_path))
    cp=torch.load(cp_path,map_location='cpu',weights_only=True)
    cfg=json.loads((args.donor/'config.json').read_text(encoding='utf-8'))
    if cp.get('schema')!=science.CHECKPOINT_SCHEMA or cp.get('config')!=cfg:
        raise ValueError('DONOR_CONFIG_OR_SCHEMA_DRIFT')
    if cfg.get('trainer_sha256')!=digest(Path(science.__file__)):
        raise ValueError('DONOR_TRAINER_SHA_DRIFT')
    if cfg.get('source_sha256')!=digest(args.source):raise ValueError('SOURCE_SHA_DRIFT')
    if cfg.get('teacher_sha256')!=hd.sha(args.teacher):raise ValueError('TEACHER_SHA_DRIFT')
    if cp.get('decoder') is None:raise ValueError('DONOR_DECODER_MISSING')
    return {'schema':SCHEMA,'experiment_source_sha256':digest(Path(__file__)),
            'donor_checkpoint_sha256':digest(cp_path),'donor_step':int(cp['step']),
            'source_sha256':digest(args.source),'teacher_sha256':hd.sha(args.teacher),
            'scientific_trainer_sha256':cfg['trainer_sha256'],
            'baseline':'pretrained_V3_decoder_same_frozen_features',
            'methods':list(METHODS),'tier':'T02','test_policy':'sealed_until_explicit_winner',
            'features':'pretrained_V3_decoder_penultimate_body_features_frozen',
            'log_scale':LOG_SCALE,'linear_scale':SCALE,
            'twohot_grid':[GRID_MIN,GRID_MAX,GRID_SIZE]}


def initialize(args):
    args.root.mkdir(parents=True,exist_ok=True)
    path=args.root/'manifest.json'
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        current=scientific_identity(args)
        if old!=current:raise ValueError('BENCHMARK_IDENTITY_DRIFT_OR_DONOR_MOVED_STOP_TRAINING')
    else:
        # Do not reuse a directory containing any unverified experiment files.
        if list(args.root.iterdir()):raise ValueError('NONEMPTY_UNINITIALIZED_BENCHMARK_DIR')
        write_json(path,scientific_identity(args))
    return json.loads(path.read_text(encoding='utf-8'))


def verify(args):
    path=args.root/'manifest.json'
    if not path.is_file():raise FileNotFoundError('RUN_PREPARE_FIRST')
    old=json.loads(path.read_text(encoding='utf-8'))
    if old!=scientific_identity(args):raise ValueError('BENCHMARK_IDENTITY_DRIFT_OR_DONOR_MOVED_STOP_TRAINING')
    return old


def operands_from_t02(prompt):
    """Extract ONLY input counts from the two first-branch MUL nodes.

    The target X and Y are never used to construct the operand-control inputs.
    """
    stripped=hd.strip_and_validate_bars(prompt,representation=hd.REP_SCIENCE)
    a=re.findall(r'\(MUL\s+([0-9]+)\s+HAWK_HAWK_PAYOFF\s*\)',stripped)
    b=re.findall(r'\(MUL\s+([0-9]+)\s+HAWK_DOVE_PAYOFF\s*\)',stripped)
    if len(a)!=1 or len(b)!=1:raise ValueError('INPUT_COUNT_EXTRACTION_FAILED')
    av,bv=int(a[0]),int(b[0])
    if av<0 or bv<0 or av+bv!=1_000_000:raise ValueError('INPUT_COUNT_CONSERVATION_FAILED')
    return [av/SCALE,bv/SCALE]


def dataset_rows(teacher,split):
    if split not in ('train','dev','test'):raise ValueError('INVALID_SPLIT')
    rows=hd.read_rows(teacher,split,'T02')
    if not rows:raise ValueError('MISSING_T02_SPLIT:'+split)
    # Assert unique IDs and masks balanced per instance. No dev/test labels enter training.
    if len({r['case_id'] for r in rows})!=len(rows):raise ValueError('REPEATED_CASE_ID')
    masks={}
    for r in rows:masks.setdefault(r['instance'],set()).add(int(r['mask']))
    if any(v!=set(range(8)) for v in masks.values()):raise ValueError('T02_MASK_LATTICE_INCOMPLETE')
    return rows


def expected_npz_metadata(args,split):
    return {'schema':SCHEMA,'split':split,'identity_sha256':digest(args.root/'manifest.json'),
            'original_checkpoint_sha256':digest(args.donor/'latest.pt'),'tier':'T02'}


def save_dataset(args,split,features,labels,inputs,original,masks,instances,case_ids):
    import numpy as np
    target=args.root/f'features-{split}.npz'
    if target.exists():raise FileExistsError('IMMUTABLE_FEATURES_ALREADY_EXIST')
    arrays={'features':np.asarray(features,dtype=np.float32),
            'targets':np.asarray(labels,dtype=np.float32),
            'operands':np.asarray(inputs,dtype=np.float32),
            'native':np.asarray(original,dtype=np.float32),
            'masks':np.asarray(masks,dtype=np.int16),
            'instances':np.asarray(instances,dtype=np.int32),
            'case_ids':np.asarray(case_ids,dtype='U48'),
            'metadata':np.asarray(json.dumps(expected_npz_metadata(args,split),sort_keys=True))}
    with tempfile.NamedTemporaryFile('wb',dir=args.root,delete=False,suffix='.npz') as f:
        temp=Path(f.name)
        np.savez_compressed(f,**arrays)
    try:os.replace(temp,target)
    finally:temp.unlink(missing_ok=True)
    write_json(args.root/f'features-{split}.json',{'sha256':digest(target),
               'n':len(labels),'split':split,'cases':len(set(instances)),
               'features_shape':list(arrays['features'].shape),
               'teacher_sha256':hd.sha(args.teacher)})
    return target


def load_dataset(args,split):
    path=args.root/f'features-{split}.npz'
    audit=args.root/f'features-{split}.json'
    if not path.is_file() or not audit.is_file():raise FileNotFoundError('EXTRACT_SPLIT_FIRST:'+split)
    if digest(path)!=json.loads(audit.read_text(encoding='utf-8'))['sha256']:
        raise ValueError('FEATURE_DATASET_SHA_DRIFT:'+split)
    with np.load(path,allow_pickle=False) as db:
        rows={k:np.array(db[k]) for k in db.files}
    if json.loads(rows['metadata'].item())!=expected_npz_metadata(args,split):
        raise ValueError('FEATURE_DATASET_IDENTITY_DRIFT:'+split)
    n=len(rows['targets'])
    if rows['features'].shape[:2]!=(n,2) or rows['targets'].shape!=(n,2) or rows['native'].shape!=(n,2) or rows['operands'].shape!=(n,2):
        raise ValueError('FEATURE_DATASET_SHAPE_DRIFT')
    if not all(np.isfinite(rows[key]).all() for key in ('features','targets','operands','native')):
        raise ValueError('NONFINITE_CAPTURED_DATA')
    return rows


def extract(args,split):
    import torch
    from tools import nanojev_game_transform_experiment as ex
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    verify(args)
    outfile=args.root/f'features-{split}.npz'
    if outfile.exists():
        data=load_dataset(args,split)
        print(json.dumps({'event':'features_reused','split':split,'n':len(data['targets'])}),flush=True)
        return
    rows=dataset_rows(args.teacher,split)
    cp=torch.load(args.donor/'latest.pt',map_location='cpu',weights_only=True)
    model=ex.load_current(args.source,args.device,'float16' if args.device=='cuda' else 'float32',False,int(cp['config']['seed']))
    model['decoder'].load_state_dict(cp['decoder'],strict=True)
    model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
    model['decoder'].eval();model['gamehead'].eval()
    for layer in (model['decoder'],model['parent']):
        for p in layer.parameters():p.requires_grad_(False)
    selected=[r for r in rows if rt.token_eligible(model,r['prompt'])]
    if len(selected)!=len(rows):
        raise RuntimeError('T02_EXCLUDED_PROMPTS_USE_AN_EXPLICIT_PROBE_POLICY')
    feats=[];truth=[];op=[];native=[];masks=[];instances=[];ids=[]
    capture=[]
    def hook(_module,_inputs,outputs):
        capture.append(outputs.detach().cpu().float().clone())
    handle=model['decoder'].body.register_forward_hook(hook)
    try:
        with torch.inference_mode():
            for i in range(0,len(selected),args.capture_batch_size):
                batch=selected[i:i+args.capture_batch_size]
                capture.clear()
                out,_=ex.forward(model,[r['prompt'] for r in batch])
                if len(capture)!=1 or capture[0].ndim!=3 or capture[0].shape[:2]!=(len(batch),2):
                    raise RuntimeError('PRETRAINED_V3_PENULTIMATE_HOOK_DRIFT')
                feats.extend(capture[0].numpy())
                preds=model['v3'].predict(out,model['base'],'attention').cpu().float().numpy()
                native.extend(preds)
                for row in batch:
                    truth.append([float(row['x']),float(row['y'])])
                    op.append(operands_from_t02(row['prompt']))
                    masks.append(int(row['mask']))
                    instances.append(int(row['instance']))
                    ids.append(row['case_id'])
                if (i//args.capture_batch_size+1)%25==0:
                    print(json.dumps({'event':'feature_capture_progress','split':split,
                           'completed':min(i+len(batch),len(selected)),'total':len(selected)}),flush=True)
    finally:handle.remove()
    if digest(args.donor/'latest.pt')!=json.loads((args.root/'manifest.json').read_text())['donor_checkpoint_sha256']:
        raise RuntimeError('DONOR_MOVED_DURING_CAPTURE')
    result=save_dataset(args,split,feats,truth,op,native,masks,instances,ids)
    print(json.dumps({'event':'features_saved','split':split,'n':len(ids),'file':str(result),'sha256':digest(result)}),flush=True)


def symlog(x):
    import torch
    return torch.sign(x)*torch.log1p(torch.abs(x)/LOG_SCALE)


def symexp(x):
    import torch
    return torch.sign(x)*torch.expm1(torch.abs(x))*LOG_SCALE


def target_for(y,method):
    if method=='direct':return y/SCALE
    if method in ('symlog','twohot'):return symlog(y)
    raise ValueError('UNKNOWN_SCHEME')


def twohot_loss(logits,targets):
    import torch
    import torch.nn.functional as F
    if not bool(((targets>=GRID_MIN)&(targets<=GRID_MAX)).all()):
        raise ValueError('TWOHOT_TARGET_OUT_OF_RANGE')
    frac=((targets-GRID_MIN)/(GRID_MAX-GRID_MIN)*(GRID_SIZE-1)).clamp(0,GRID_SIZE-1)
    low=frac.floor().long()
    high=(low+1).clamp(max=GRID_SIZE-1)
    w=frac-low.float()
    logprob=F.log_softmax(logits,dim=-1)
    return -(((1-w)*logprob.gather(-1,low.unsqueeze(-1)).squeeze(-1))+
             (w*logprob.gather(-1,high.unsqueeze(-1)).squeeze(-1))).mean()


def decode(output,method):
    import torch
    if method=='direct':return output.squeeze(-1)*SCALE
    if method=='symlog':return symexp(output.squeeze(-1).clamp(-15,15))
    if method=='twohot':
        bins=torch.linspace(GRID_MIN,GRID_MAX,GRID_SIZE,device=output.device)
        logsum=(torch.softmax(output,dim=-1)*bins).sum(-1)
        return symexp(logsum)
    if method=='operands':return output*SCALE
    raise ValueError('UNKNOWN_SCHEME')


def create_model(dim,method):
    import torch.nn as nn
    if method=='operands':return nn.Linear(2,2)
    count=GRID_SIZE if method=='twohot' else 1
    return nn.Sequential(nn.LayerNorm(dim),nn.Linear(dim,96),nn.GELU(),nn.Linear(96,count))


def training_loss(output,y,method):
    import torch.nn.functional as F
    if method=='operands':return F.mse_loss(output,y/SCALE)
    transformed=target_for(y,method)
    if method=='twohot':return twohot_loss(output,transformed)
    return F.smooth_l1_loss(output.squeeze(-1),transformed,beta=.05)


def accuracy(pred,true):
    import numpy as np
    rel=np.abs(pred-true)/np.maximum(1,np.abs(true))
    return {'joint_5pct':float(np.mean(np.all(rel<=.05,axis=-1))),
            'x_5pct':float(np.mean(rel[:,0]<=.05)),
            'y_5pct':float(np.mean(rel[:,1]<=.05)),
            'mae_x':float(np.mean(np.abs(pred[:,0]-true[:,0]))),
            'mae_y':float(np.mean(np.abs(pred[:,1]-true[:,1]))),
            'rmse_x':float(np.sqrt(np.mean(np.square(pred[:,0]-true[:,0])))),
            'rmse_y':float(np.sqrt(np.mean(np.square(pred[:,1]-true[:,1])))),
            'both_within_5000':float(np.mean(np.all(np.abs(pred-true)<=5000,axis=-1)))}


def per_mask_accuracy(pred,rows):
    return {str(mask):accuracy(pred[rows['masks']==mask],rows['targets'][rows['masks']==mask])
            for mask in sorted(np.unique(rows['masks']))}


def infer(model,rows,method,device):
    import torch
    model.eval()
    x=rows['operands'] if method=='operands' else rows['features']
    outputs=[]
    with torch.inference_mode():
        for i in range(0,len(x),256):
            t=torch.from_numpy(x[i:i+256]).to(device)
            outputs.append(decode(model(t),method).cpu().numpy())
    return np.concatenate(outputs,axis=0)


def fit(args):
    import torch
    verify(args)
    train=load_dataset(args,'train')
    dev=load_dataset(args,'dev')
    dim=train['features'].shape[-1]
    for method in METHODS:
        target=args.root/f'{method}.pt'
        if target.exists():
            model=load_model(args,method,dim)
            print(json.dumps({'event':'already_trained','method':method}),flush=True)
            continue
        torch.manual_seed(args.seed)
        model=create_model(dim,method).to(args.train_device)
        optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=0.0001)
        x=train['operands'] if method=='operands' else train['features']
        xt=torch.from_numpy(x).to(args.train_device)
        yt=torch.from_numpy(train['targets']).to(args.train_device)
        gen=torch.Generator(device='cpu').manual_seed(args.seed)
        if method=='operands':
            # Algebraic least squares is the appropriate negative control for
            # a linear task. Using the same tiny Adam LR as nonlinear heads
            # could make this control fail for lack of convergence alone.
            matrix=np.column_stack((train['operands'],np.ones(len(train['operands']))))
            coefs=np.linalg.lstsq(matrix,train['targets']/SCALE,rcond=None)[0]
            with torch.no_grad():
                model.weight.copy_(torch.as_tensor(coefs[:2,:].T,dtype=torch.float32,device=args.train_device))
                model.bias.copy_(torch.as_tensor(coefs[2,:],dtype=torch.float32,device=args.train_device))
            print(json.dumps({'event':'operand_linear_control_fitted',
                    'solver':'training_only_least_squares','n':len(train['operands'])}),flush=True)
        else:
            for epoch in range(1,args.epochs+1):
                model.train()
                indices=torch.randperm(len(x),generator=gen)
                losses=[]
                for inds in indices.split(args.train_batch_size):
                    out=model(xt[inds.to(args.train_device)])
                    loss=training_loss(out,yt[inds.to(args.train_device)],method)
                    if not torch.isfinite(loss):raise RuntimeError('NONFINITE_TRAINING_LOSS:'+method)
                    optimizer.zero_grad(set_to_none=True)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(),10.0)
                    optimizer.step()
                    losses.append(float(loss.detach()))
                if epoch%max(1,args.epochs//6)==0 or epoch==args.epochs:
                    print(json.dumps({'event':'benchmark_train_progress','method':method,'epoch':epoch,
                       'epochs':args.epochs,'train_loss':float(np.mean(losses))}),flush=True)
        model.eval()
        state={'schema':SCHEMA,'method':method,'epochs':args.epochs,
               'learning_rate':args.learning_rate,'seed':args.seed,
               'feature_dataset_sha256':digest(args.root/'features-train.npz'),
               'feature_dim':dim,'weights':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()},
               'identity_sha256':digest(args.root/'manifest.json')}
        # One checkpoint per method, never overwrite existing results.
        import tempfile
        with tempfile.NamedTemporaryFile('wb',dir=args.root,delete=False,suffix='.pt') as f:tmp=Path(f.name)
        try:
            torch.save(state,tmp)
            if target.exists():raise FileExistsError('SCHEME_ALREADY_TRAINED')
            tmp.replace(target)
        finally:tmp.unlink(missing_ok=True)
        print(json.dumps({'event':'method_fitted','method':method,'step_epochs':args.epochs,
                          'dev_metrics':accuracy(infer(model,dev,method,args.train_device),dev['targets'])}),flush=True)


def load_model(args,method,dim):
    import torch
    ckpt=torch.load(args.root/f'{method}.pt',map_location='cpu',weights_only=True)
    if ckpt['schema']!=SCHEMA or ckpt['method']!=method or ckpt['feature_dim']!=dim or ckpt['identity_sha256']!=digest(args.root/'manifest.json') or ckpt['feature_dataset_sha256']!=digest(args.root/'features-train.npz'):
        raise ValueError('METHOD_CHECKPOINT_IDENTITY_DRIFT:'+method)
    m=create_model(dim,method).to(args.train_device)
    m.load_state_dict(ckpt['weights'],strict=True)
    m.eval()
    return m


def report(args,split,methods=METHODS):
    verify(args)
    data=load_dataset(args,split)
    results={'original_V3':{'overall':accuracy(data['native'],data['targets']),
                            'per_mask':per_mask_accuracy(data['native'],data)}}
    for method in methods:
        model=load_model(args,method,data['features'].shape[-1])
        pred=infer(model,data,method,args.train_device)
        results[method]={'overall':accuracy(pred,data['targets']),
                         'per_mask':per_mask_accuracy(pred,data)}
    outcome={'schema':SCHEMA,'split':split,'n':len(data['targets']),
             'num_instances':int(len(set(data['instances'].tolist()))),
             'source_checkpoint_sha256':digest(args.donor/'latest.pt'),
             'features_sha256':digest(args.root/f'features-{split}.npz'),
             'report':results,
             'protocol':'fixed epochs; dev never used for hyperparameter selection; test requires winner'}
    filename=args.root/f'results-{split}.json'
    if filename.exists() and split=='test':raise FileExistsError('TEST_ALREADY_SEALED')
    write_json(filename,outcome)
    print(json.dumps({'event':'benchmark_results','split':split,'n':len(data['targets']),
                      'scores':{m:values['overall'] for m,values in results.items()}},sort_keys=True),flush=True)
    return outcome


def test_once(args):
    if args.winner not in METHODS:raise ValueError('EXPLICIT_WINNER_REQUIRED_FOR_TEST')
    seal_path=args.root/'test-sealed.json'
    if (args.root/'results-test.json').exists():
        raise ValueError('TEST_ALREADY_COMPLETED_NO_RETROACTIVE_WINNER_CHANGE')
    if not (args.root/'results-dev.json').exists():raise FileNotFoundError('RUN_DEV_EVALUATION_FIRST')
    # Seal choice before reading held-out test rows; retrying a failed execution
    # is allowed only with the originally locked winner.
    sealed={'winner':args.winner,'schema':SCHEMA,
            'identity_sha256':digest(args.root/'manifest.json')}
    if seal_path.exists():
        if json.loads(seal_path.read_text(encoding='utf-8'))!=sealed:
            raise ValueError('TEST_WINNER_ALREADY_SEALED_CANNOT_SWITCH')
    else:
        if (args.root/'features-test.npz').exists():
            raise ValueError('TEST_FEATURES_EXIST_WITHOUT_SEALED_WINNER')
        write_json(seal_path,sealed)
    extract(args,'test')
    report(args,'test',(args.winner,))


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('all','prepare','train','probe','test'))
    p.add_argument('--source',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--teacher',type=Path,default=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3'))
    p.add_argument('--donor',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_million_scientific_v2')
    p.add_argument('--root',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_t02_continuous_benchmark_v1')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--train-device',choices=('cuda','cpu'),default='cpu')
    p.add_argument('--capture-batch-size',type=int,default=4)
    p.add_argument('--train-batch-size',type=int,default=128)
    p.add_argument('--epochs',type=int,default=120)
    p.add_argument('--seed',type=int,default=20261019)
    p.add_argument('--learning-rate',type=float,default=.001)
    p.add_argument('--winner',choices=METHODS)
    return p


def main(argv=None):
    args=make_parser().parse_args(argv)
    if not (1<=args.capture_batch_size<=32 and 1<=args.train_batch_size<=8192 and 1<=args.epochs<=3000 and 0<args.learning_rate<=.1):
        raise ValueError('INVALID_TRAINING_ARGUMENTS')
    args.root=args.root.expanduser().resolve()
    args.donor=args.donor.expanduser().resolve()
    args.source=args.source.expanduser().resolve()
    args.teacher=args.teacher.expanduser().resolve()
    if args.root==args.donor or args.root in args.donor.parents or args.donor in args.root.parents:
        raise ValueError('INDEPENDENT_EXPERIMENT_ROOT_REQUIRED')
    if args.stage in ('all','prepare'):
        initialize(args)
        extract(args,'train');extract(args,'dev')
    if args.stage in ('all','train'):fit(args)
    if args.stage in ('all','probe'):report(args,'train');report(args,'dev')
    if args.stage=='test':test_once(args)


if __name__=='__main__':main()
