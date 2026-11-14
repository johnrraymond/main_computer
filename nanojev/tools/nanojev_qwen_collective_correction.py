#!/usr/bin/env python3
"""Five-fold held-population-out low-rank activation correction experiment.

Uses saved Qwen activations. Never updates Qwen, loads no model or checkpoint.
The local teacher has labels on reference examples only, not query labels.
A shared readout and shared low-rank student are fitted on four populations.
No component is fit, normalized or tuned with the fifth population.
"""
from __future__ import annotations
import argparse, csv, hashlib, json
from pathlib import Path
import numpy as np

POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')

def signed_log(y):
    return np.sign(y)*np.log1p(np.abs(y))

def inverse_signed_log(t):
    return np.sign(t)*np.expm1(np.clip(np.abs(t),0,30))

def metrics(y, p):
    d=np.abs(np.asarray(y)-np.asarray(p)); lp=np.abs(signed_log(y)-signed_log(p))
    return dict(mae=float(np.mean(d)),median_ae=float(np.median(d)),signed_log_mae=float(np.mean(lp)),
                within_1=float(np.mean(d<=1)),within_5=float(np.mean(d<=5)))

def read_pop(root,pop):
    folder=root/pop
    if not (folder/'activations.npy').is_file() or not (folder/'probes.jsonl').is_file():
        raise FileNotFoundError(f'{pop}: expected activations.npy and probes.jsonl in {folder}')
    X=np.load(folder/'activations.npy',mmap_mode='r')
    with (folder/'probes.jsonl').open(encoding='utf8') as fp:
        rows=[json.loads(line) for line in fp if line.strip()]
    y=np.asarray([r['answer'] for r in rows],dtype=np.float64)
    if X.ndim!=2 or len(X)!=len(y) or not np.isfinite(y).all():
        raise ValueError(f'{pop}: malformed activation/answer alignment')
    return X,y

def partitions(n, seed, pop, test_count, ref_fraction=.72):
    rng=np.random.default_rng(seed+1009*POPS.index(pop))
    perm=rng.permutation(n)
    if n<=test_count+60:raise ValueError('population too small for split')
    held=perm[:test_count]; remaining=perm[test_count:]
    nref=int(len(remaining)*ref_fraction)
    nval=max(30,int(len(remaining)*.10))
    if nref<100 or len(remaining)-nref-nval<20:raise ValueError('insufficient training partitions')
    return dict(test=held,ref=remaining[:nref],train=remaining[nref:-nval],val=remaining[-nval:])

def local_teacher(query, reference, ref_y, k, penalty, batch=128):
    from sklearn.neighbors import NearestNeighbors
    from tools.nanojev_qwen_vector_gradient import local_targets
    if k>=len(reference):raise ValueError('k exceeds references')
    pred, grad, scale=local_targets(query,reference,ref_y,k,penalty,batch)
    return pred

def transform_arrays(pop_data, train_pops, seed, dimension):
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    # PCA fitted ONLY to reference vectors from four allowed populations.
    refs=np.concatenate([np.asarray(pop_data[p]['X'][pop_data[p]['split']['ref']],dtype=np.float32) for p in train_pops])
    dimension=min(dimension,refs.shape[1],len(refs)-1)
    pca=PCA(n_components=dimension,svd_solver='randomized',random_state=seed)
    pca.fit(refs)
    scalar=StandardScaler().fit(pca.transform(refs))
    return pca,scalar

def train_student(z, target, popids, zv, targetv, popidsv, *, rank=8, epochs=120, seed=47, lr=.005, device='cpu'):
    """Shared low-rank *nonlinear* residual; zero-initialized output projection.

    Validation determines early stop. Weighted loss gives each population equal
    contribution, irrespective of its sampled count. Outputs are bounded by a
    tanh head and global correction scale based ONLY on training targets.
    """
    import torch
    torch.manual_seed(seed)
    torch.set_num_threads(min(torch.get_num_threads(),4))
    class Adapter(torch.nn.Module):
        def __init__(self,d,r,scale):
            super().__init__()
            self.a=torch.nn.Linear(d,r)
            self.b=torch.nn.Linear(r,d,bias=False)
            torch.nn.init.zeros_(self.b.weight)
            self.register_buffer('scale',torch.tensor(float(scale)))
        def forward(self,x):
            return self.scale*torch.tanh(self.b(torch.tanh(self.a(x))))
    scale=max(float(np.median(np.linalg.norm(target,axis=1))),1e-4)
    model=Adapter(z.shape[1],rank,scale).to(device)
    xt=torch.as_tensor(z,dtype=torch.float32,device=device)
    yt=torch.as_tensor(target,dtype=torch.float32,device=device)
    xv=torch.as_tensor(zv,dtype=torch.float32,device=device)
    yv=torch.as_tensor(targetv,dtype=torch.float32,device=device)
    groups=[torch.as_tensor(np.flatnonzero(popids==j),device=device) for j in sorted(set(popids))]
    vgroups=[torch.as_tensor(np.flatnonzero(popidsv==j),device=device) for j in sorted(set(popidsv))]
    opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.01)
    # Gradient accumulation over all four populations: one optimizer step per epoch.
    history=[];best=float('inf');state=None;best_epoch=-1
    for epoch in range(epochs):
        model.train();opt.zero_grad(set_to_none=True)
        pred=model(xt)
        loss=torch.stack([torch.mean(torch.sum((pred[idx]-yt[idx])**2,dim=1)) for idx in groups]).mean()
        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
        opt.step()
        model.eval()
        with torch.no_grad():
            pv=model(xv)
            val=float(torch.stack([torch.mean(torch.sum((pv[idx]-yv[idx])**2,dim=1)) for idx in vgroups]).mean().item())
        if not np.isfinite(val):raise ValueError('non-finite student validation loss')
        history.append(dict(epoch=epoch,train=float(loss.item()),val=val))
        if val<best-1e-8:
            best=val;best_epoch=epoch
            state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
        if epoch-best_epoch>=22:break
    model.load_state_dict(state);model.eval()
    def predict(x):
        with torch.no_grad():return model(torch.as_tensor(x,dtype=torch.float32,device=device)).cpu().numpy().astype(np.float64)
    return model,predict,history,best_epoch

def fold(root,output,held_pop, *, seed=47, holdout=900, rank=32, adapter_rank=8, k=32,
         penalty=1., epochs=120, max_train=1200, max_val=300, batch=128, device='cpu'):
    from sklearn.linear_model import Ridge
    within = held_pop is None
    train_pops=list(POPS) if within else [p for p in POPS if p!=held_pop]
    data={}
    for p in POPS:
        X,y=read_pop(root,p)
        data[p]=dict(X=X,y=y,split=partitions(len(y),seed,p,holdout))
    # For within mode, only each population's reference/train/validation partitions fit parameters.
    # For leave-one-population-out, the excluded population fits nothing.
    pca,scalar=transform_arrays(data,train_pops,seed,rank)
    def project(X,indices):
        return scalar.transform(pca.transform(np.asarray(X[indices],dtype=np.float32))).astype(np.float64)
    refs={p:project(data[p]['X'],data[p]['split']['ref']) for p in train_pops}
    yrefs={p:signed_log(data[p]['y'][data[p]['split']['ref']]) for p in train_pops}
    # One shared numerical readout, fit on labeled reference data across the four populations.
    reg=Ridge(alpha=100.).fit(np.concatenate(list(refs.values())),np.concatenate(list(yrefs.values())))
    beta=np.asarray(reg.coef_,np.float64)
    b2=float(beta@beta)
    if b2<1e-15:raise ValueError('shared readout has no usable gradient')
    rng=np.random.default_rng(seed)
    train_z=[];train_t=[];train_ids=[];val_z=[];val_t=[];val_ids=[];validation_by_pop={};val_y=[]
    for j,p in enumerate(train_pops):
        d=data[p]
        tr=d['split']['train'];va=d['split']['val']
        if max_train and len(tr)>max_train:tr=np.sort(rng.choice(tr,max_train,replace=False))
        if max_val and len(va)>max_val:va=np.sort(rng.choice(va,max_val,replace=False))
        for idx,zz,tt,ids in [(tr,train_z,train_t,train_ids),(va,val_z,val_t,val_ids)]:
            z=project(d['X'],idx)
            proposed=local_teacher(z,refs[p],yrefs[p],k,penalty,batch)
            raw=reg.predict(z)
            # Min norm displacement in shared readout direction; no query answer used.
            delta=((proposed-raw)/b2)[:,None]*beta
            zz.append(z);tt.append(delta);ids.append(np.full(len(idx),j,dtype=np.int32))
            if zz is val_z:val_y.append(signed_log(d['y'][idx]))
        validation_by_pop[p]=dict(count=len(va))
    z=np.concatenate(train_z);delta=np.concatenate(train_t);pi=np.concatenate(train_ids)
    zv=np.concatenate(val_z);dv=np.concatenate(val_t);piv=np.concatenate(val_ids)
    model,predict,history,bestepoch=train_student(z,delta,pi,zv,dv,piv,rank=adapter_rank,epochs=epochs,seed=seed,device=device)
    # Student selection uses only the four training-population validation sets.
    return _score_and_save(root,output,held_pop,data,train_pops,pca,scalar,reg,model,predict,history,bestepoch,
                           z,delta,pi,zv,dv,piv,np.concatenate(val_y),project,holdout,seed)

def _score_and_save(root,output,held_pop,data,train_pops,pca,scalar,reg,model,predict,history,bestepoch,
                    z,delta,pi,zv,dv,piv,val_y,project,holdout,seed):
    import torch
    # Validation score against teacher displacements is NOT an answer accuracy gate.
    # Do not silently use held-population results to activate/deactivate student.
    predicted_val=predict(zv)
    val_teacher=float(np.mean(np.sum((predicted_val-dv)**2,axis=1)))
    identity_teacher=float(np.mean(np.sum(dv**2,axis=1)))
    # Gate by actual validation numerical loss, balanced across the four
    # permitted populations. This guards against imitating a bad teacher.
    raw_v=reg.predict(zv)
    new_v=reg.predict(zv+predicted_val)
    old_loss=[];new_loss=[];validation_by_pop={}
    for j,p in enumerate(train_pops):
        mask=piv==j
        o=float(np.mean(np.abs(raw_v[mask]-val_y[mask])))
        n=float(np.mean(np.abs(new_v[mask]-val_y[mask])))
        old_loss.append(o);new_loss.append(n)
        validation_by_pop[p]=dict(raw_signed_log_mae=o,corrected_signed_log_mae=n,
                                  improvement_pct=100*(o-n)/max(o,1e-12))
    enabled=bool(np.mean(new_loss)<np.mean(old_loss)-1e-8 and
                 all(n<=o*1.05 for n,o in zip(new_loss,old_loss)))
    gate={'student_val_teacher_mse':val_teacher,'identity_val_teacher_mse':identity_teacher,
          'student_enabled':enabled,'validation_by_training_population':validation_by_pop,
          'validation_balanced_raw':float(np.mean(old_loss)),
          'validation_balanced_corrected':float(np.mean(new_loss)),
          'retention_guard':'No training-population validation loss may deteriorate >5%'}
    within = held_pop is None
    held_pops = list(POPS) if within else [held_pop]
    dest = output / 'within-population' if within else output / held_pop
    dest.mkdir(parents=True,exist_ok=True)
    evaluations = {}
    for test_pop in held_pops:
        d=data[test_pop]
        idx=d['split']['test'] if within else np.arange(len(d['y']))
        zz=project(d['X'],idx)
        raw_t=reg.predict(zz)
        updates=predict(zz) if enabled else np.zeros_like(zz)
        changed_t=reg.predict(zz+updates)
        true=d['y'][idx]
        raw=inverse_signed_log(raw_t);changed=inverse_signed_log(changed_t)
        orig=metrics(true,raw);new=metrics(true,changed)
        improvement=100*(orig['mae']-new['mae'])/max(orig['mae'],1e-12)
        evaluations[test_pop]=dict(raw=orig,corrected=new,improvement_pct=improvement,
            signed_log_improvement_pct=100*(orig['signed_log_mae']-new['signed_log_mae'])/max(orig['signed_log_mae'],1e-12),
            holdout_count=int(len(idx)),split_indices_sha256=hashlib.sha256(np.asarray(idx,dtype=np.int64).tobytes()).hexdigest())
        predictions_file=dest / (f'{test_pop}_holdout_predictions.csv' if within else 'holdout_predictions.csv')
        with predictions_file.open('w',newline='',encoding='utf8') as fp:
            writer=csv.writer(fp);writer.writerow(['index','answer','raw','corrected','raw_absolute_error','corrected_absolute_error','correction_norm'])
            for i,y,a,b,v in zip(idx,true,raw,changed,updates):
                writer.writerow([int(i),float(y),float(a),float(b),float(abs(y-a)),float(abs(y-b)),float(np.linalg.norm(v))])
        print(f'{test_pop}: raw_MAE={orig["mae"]:.6g} corrected_MAE={new["mae"]:.6g} change={improvement:+.2f}% enabled={enabled}',flush=True)
    report=dict(schema='nanojev.collective-vector-correction.v2',split='within-population' if within else 'leave-one-population-out',
                held_population=held_pop,trained_populations=train_pops,
                readout='pooled Ridge on signed_log(answer), reference partition only',
                teacher='within-pop local numerical gradient (reference labels only); no query labels',
                student='one shared zero-initialized nonlinear low-rank residual; one balanced update/epoch',
                output='activation-space residual on shared train-only PCA coordinates, NOT Qwen LoRA weights',
                holdout_excluded_from_all_fitting=True,seed=seed,holdout_count=holdout if within else len(data[held_pop]['y']),
                reference_count={p:int(len(data[p]['split']['ref'])) for p in train_pops},
                student_training_count=int(len(z)),student_validation_count=int(len(zv)),
                student_best_epoch=bestepoch,gate=gate,evaluations=evaluations,
                student_teacher_validation_mse=val_teacher,
                experimental_warning='No Qwen token generation tested. Validation gate cannot guarantee holdout improvement; unchanged residual is fallback.')
    if not within:
        single=evaluations[held_pop]
        report.update(raw=single['raw'],corrected=single['corrected'],improvement_pct=single['improvement_pct'],
                      signed_log_improvement_pct=single['signed_log_improvement_pct'])
    (dest/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    # Save all parameters sufficient for reproduction without Qwen (non-executable NPZ).
    st=model.state_dict()
    np.savez_compressed(dest/'adapter.npz',pca_mean=pca.mean_,pca_components=pca.components_,
                        scaler_mean=scalar.mean_,scaler_scale=scalar.scale_,
                        readout_coef=reg.coef_,readout_intercept=reg.intercept_,
                        adapter_A_weight=st['a.weight'].numpy(),adapter_A_bias=st['a.bias'].numpy(),
                        adapter_B_weight=st['b.weight'].numpy(),adapter_scale=st['scale'].numpy(),
                        student_enabled=np.array(int(enabled)))
    (dest/'training_history.json').write_text(json.dumps(history,indent=2),encoding='utf8')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    p.add_argument('--output',default=None)
    p.add_argument('--holdout-population',choices=('all',)+POPS,default='all')
    p.add_argument('--split',choices=('leave-one-population-out','within-population'),default='leave-one-population-out')
    p.add_argument('--seed',type=int,default=47);p.add_argument('--holdout',type=int,default=900)
    p.add_argument('--rank',type=int,default=32);p.add_argument('--adapter-rank',type=int,default=8)
    p.add_argument('--k',type=int,default=32);p.add_argument('--penalty',type=float,default=1.)
    p.add_argument('--epochs',type=int,default=120);p.add_argument('--max-train',type=int,default=1200)
    p.add_argument('--max-val',type=int,default=300);p.add_argument('--batch',type=int,default=128)
    p.add_argument('--device',default='cpu')
    a=p.parse_args()
    if a.rank<1 or a.adapter_rank<1 or a.k<3 or a.penalty<=0 or a.holdout<1 or a.epochs<1:
        p.error('invalid numeric argument')
    root=Path(a.input)
    out=Path(a.output) if a.output else root/'collective-correction'
    out.mkdir(parents=True,exist_ok=True)
    if a.split=='within-population':
        if a.holdout_population!='all':
            p.error('--holdout-population must be all in --split within-population mode')
        reports=[fold(root,out,None,seed=a.seed,holdout=a.holdout,rank=a.rank,adapter_rank=a.adapter_rank,
                      k=a.k,penalty=a.penalty,epochs=a.epochs,max_train=a.max_train,max_val=a.max_val,
                      batch=a.batch,device=a.device)]
        dest=out/'within-population'
        with (dest/'summary.csv').open('w',newline='',encoding='utf8') as fp:
            writer=csv.writer(fp);writer.writerow(['population','holdout_count','raw_mae','corrected_mae','improvement_pct','signed_log_improvement_pct','student_enabled'])
            for pop,res in reports[0]['evaluations'].items():
                writer.writerow([pop,res['holdout_count'],res['raw']['mae'],res['corrected']['mae'],
                              res['improvement_pct'],res['signed_log_improvement_pct'],reports[0]['gate']['student_enabled']])
        print(json.dumps({'output':str(dest),'split':a.split,'held_out':list(reports[0]['evaluations'])},indent=2))
    else:
        reports=[]
        for pop in POPS if a.holdout_population=='all' else (a.holdout_population,):
            reports.append(fold(root,out,pop,seed=a.seed,holdout=a.holdout,rank=a.rank,adapter_rank=a.adapter_rank,
                                k=a.k,penalty=a.penalty,epochs=a.epochs,max_train=a.max_train,max_val=a.max_val,
                                batch=a.batch,device=a.device))
        with (out/'summary.csv').open('w',newline='',encoding='utf8') as fp:
            writer=csv.writer(fp);writer.writerow(['held_population','raw_mae','corrected_mae','improvement_pct','signed_log_improvement_pct','student_enabled'])
            for r in reports:writer.writerow([r['held_population'],r['raw']['mae'],r['corrected']['mae'],r['improvement_pct'],r['signed_log_improvement_pct'],r['gate']['student_enabled']])
        print(json.dumps({'output':str(out),'held_out':[r['held_population'] for r in reports]},indent=2))
if __name__=='__main__':main()
