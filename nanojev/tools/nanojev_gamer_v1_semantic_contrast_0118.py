"""0118: controlled semantic contrast discrimination with frozen Qwen/readers.
Candidate-anchor lane is conditional, not operational graph extraction. 0117
forensics and G1 candidate diagnostics are retrospective. Fresh prediction sees
only prompts, symmetric candidate anchors and frozen parameters, never labels.
"""
from __future__ import annotations
import argparse, collections as co, hashlib, json, math, os, re, sys, subprocess, tempfile
from pathlib import Path
from types import SimpleNamespace
os.environ.setdefault("OPENBLAS_NUM_THREADS","2")
os.environ.setdefault("MKL_NUM_THREADS","2")
import numpy as np
import torch
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_joint_reconstruction_0117 as J
I,L,f,m,T,B,P=J.I,J.L,J.f,J.m,J.T,J.B,J.P
read,write,sha,digest=J.read,J.write,J.sha,J.digest
TAG='G12_0118_CONTRAST'; MODULE='tools.nanojev_gamer_v1_semantic_contrast_0118'; SEED=1182026
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_contrast_author_0118.py')
RELS=('refers_to','owns','applies_to','measures'); KEYS=('original_0',)+tuple(f'{s}_{k}' for s in ('original','S') for k in (7,14,21,28))
SCOPES=('endpoint','bridge','suffix','all'); SURFACES=('geometry','lexical','text_lexical','reader_scores')+tuple(f'{k}:{s}' for k in KEYS for s in SCOPES)+('original_multi:all','S_multi:all')
CONDS=('normal','zero_all','shuffle_tokens','embedding_only','S_replaced','suffix_zero')

def need(x,msg):
    if not x:raise ValueError(TAG+'_'+msg)
def emit(k,x):print(TAG+'_'+k+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def imm(p,x):J.imm(p,x)
def output(a):return Path(a.root).resolve()/'g1-g2-semantic-contrast-0118'
def setup(a):
    # 0117's verified parent setup includes frozen checkpoint and prediction lineage.
    q=SimpleNamespace(root=a.root,device=a.device,command=a.command)
    st=J.setup(q);x,old,ident,pr=st
    s,d,z,public=J.check_fresh(old,ident);lock=read(d/'prediction-lock.json')
    need(lock['seal']==sha(d/'seal.json') and lock['shards']==[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(public))],'0117_LOCK')
    out=output(a);out.mkdir(exist_ok=True)
    ident={'source':sha(__file__),'author':sha(AUTHOR),'parent':ident,'0117_lock':sha(d/'prediction-lock.json'),'0117_selection':sha(old/'selection.json'),'seed':SEED,'device':a.device}
    return st,out,ident,public

def atomic_npz(path,arrays):
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.tmp');os.close(fd)
    try:
        with open(tmp,'wb') as h:np.savez_compressed(h,**arrays)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def prepare(a,st):
    from tools.nanojev_gamer_v1_contrast_author_0118 import rows
    _,out,ident,oldpub=st;allrows=rows();need(len(allrows)==80,'AUTHOR_COUNT')
    prior={r['prompt_sha256'] for r in oldpub+st[0][0][6]+st[0][3]}
    # Exact prompt uniqueness versus known sealed ancestors, not proof of semantic novelty.
    for r,g in allrows:
        need(r['prompt_sha256'] not in prior,'PROMPT_COLLISION');prior.add(r['prompt_sha256'])
        need(sum(g['positive'])==2 and len(r['anchors'])==4,'AUTHOR_PAIR')
    for split in ('fit','validation','fresh'):
        d=out/split;d.mkdir(exist_ok=True);rr=[r for r,g in allrows if r['split']==split];gg=[g for r,g in allrows if r['split']==split]
        groups=co.defaultdict(dict)
        for r,g in zip(rr,gg):groups[r['base_id']][r['mode']]=(r,g)
        for b,z in groups.items():
            for mode in ('direct','direct_paraphrase'):
                rev=mode.replace('direct','reverse');p,y=z[mode];q,v=z[rev];need(p['anchors']==q['anchors'] and [1-i for i in y['positive']]==v['positive'],'CONTRAST_NOT_MATCHED')
            need(z['direct'][1]['positive']==z['direct_paraphrase'][1]['positive'],'PARAPHRASE_LABEL_DRIFT')
        imm(d/'prompts.json',rr);imm(d/'answers.SECRET.json',gg);imm(d/'seal.json',{'identity':ident,'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'cases':len(rr),'structures':sorted({r['structure'] for r in rr}),'scope':'conditional_symmetric_anchors_not_full_graph'})
    emit('SEALED',{'fit':32,'validation':16,'fresh':32,'structures':5,'fresh_structures':2,'relations':list(RELS)})

def cohort(out,ident,split,answers=False):
    d=out/split;s=read(d/'seal.json');need(s['identity']==ident and s['prompts']==sha(d/'prompts.json'),'SEAL_DRIFT_'+split);rows=read(d/'prompts.json');need(len(rows)==s['cases'],'COUNT')
    for r in rows:need(P.digest_text(r['prompt'])==r['prompt_sha256'],'PROMPT_DRIFT')
    if not answers:return d,s,rows
    need(s['answers']==sha(d/'answers.SECRET.json'),'ANSWER_DRIFT');gold=read(d/'answers.SECRET.json');need(len(gold)==len(rows),'ANSWER_COUNT');need(all((r['case_id'],r['prompt_sha256'])==(g['case_id'],g['prompt_sha256']) for r,g in zip(rows,gold)),'ANSWER_ALIGNMENT');return d,s,rows,gold

def anchor_view(r,p):
    # Exact token alignment for public candidate anchors; none is a gold edge.
    row={'graph':{'nodes':r['anchors'],'edges':[]}};view,missing=L.oracle_view(row,p,int(L.GH.DEFAULT_MAX_SPAN_TOKENS));need(view is not None,'UNALIGNABLE_PUBLIC_ANCHOR_'+r['case_id']+'_'+str(missing));ix={tuple(k):i for i,k in enumerate(view['objects'])};ids={n['id']:ix[(*n['span'],n['kind'])] for n in r['anchors']};pairs=[(ids[f'l{i}'],ids[f'r{j}']) for i in range(2) for j in range(2)];return view,pairs

_PROJ={}
def project(h):
    h=h.detach().float().cpu();dim=h.shape[-1]
    if dim not in _PROJ:
        gen=torch.Generator().manual_seed(SEED+dim);_PROJ[dim]=torch.randn(dim,min(128,dim),generator=gen)/math.sqrt(dim)
    # Fixed label-independent projection, same coordinates across layers/S.
    h=h-h.mean(-1,keepdim=True);h=h/(h.square().mean(-1,keepdim=True).sqrt()+1e-6);return (h@_PROJ[dim]).numpy().astype(np.float32)
def average(h,ix):return h[ix].mean(0) if len(ix) else np.zeros(h.shape[-1],np.float32)
def pair_surface(h,spans,pairs,eligible,scope):
    rows=[];last=max(s['token_last'] for s in spans)
    for i,j in pairs:
        a,b=spans[i],spans[j];ia=np.arange(a['token_first'],a['token_last']+1);ib=np.arange(b['token_first'],b['token_last']+1);u=average(h,ia);v=average(h,ib)
        lo=min(a['token_last'],b['token_last'])+1;hi=max(a['token_first'],b['token_first']);between=eligible[(eligible>=lo)&(eligible<hi)]
        ix=between if scope=='bridge' else eligible[eligible>last] if scope=='suffix' else eligible
        # Candidate-conditioned context, not an identical appended global vector.
        if scope=='endpoint':rows.append(np.r_[u,v,u*v,u-v]);continue
        z=average(h,ix)
        def attend(q):
            if not len(ix):return np.zeros_like(q)
            x=h[ix];scores=(x@q)/(np.linalg.norm(x,axis=1)*max(np.linalg.norm(q),1e-6)+1e-6);w=np.exp(4*(scores-scores.max()));return (w[:,None]*x).sum(0)/w.sum()
        cu,cv=attend(u),attend(v)
        # Signed rank coordinates are public geometry, not labels; interaction lets
        # a later instruction condition the candidate score. Geometry-only control.
        ri=1 if i==pairs[0][0] else -1;rj=1 if j==pairs[0][1] else -1
        rows.append(np.r_[u,v,u*v,u-v,cu,cv,cu*cv,z*u,z*v,z*(ri*rj),z*ri,z*rj])
    return np.asarray(rows,np.float32)

def reader_matrix(states,p,view,pairs,rel,nets):
    feat=L.features(states,view,p);k=L.CLASSES.index(rel);arr=[]
    with torch.inference_mode():
        for name,net in nets.items():
            q=net(feat).float().cpu();arr.append([float(q[i,j,k]-q[i,j,0]) for i,j in pairs])
    return np.asarray(arr,np.float32).T

def features(r,p,states,nets,condition='normal'):
    view,pairs=anchor_view(r,p);spans=view['spans'];eligible=np.where(p['eligible'].cpu().numpy().reshape(-1))[0]
    if condition=='suffix_zero':
        last=max(s['token_last'] for s in spans);states={k:v.clone() for k,v in states.items()}
        for v in states.values():v[eligible[eligible>last]]=0
    elif condition!='normal':states=I.transform(states,p,condition,SEED+int(r['structure']))
    geo=[];lex=[];textlex=[];text=r['prompt'];n=max(1,max(z[1] for z in view['objects']));words=[text[a:b].casefold() for a,b,k in view['objects']]
    for x,(i,j) in enumerate(pairs):
        a,b,k=view['objects'][i];c,d,l=view['objects'][j];gi,gj=divmod(x,2)
        g=[a/n,b/n,c/n,d/n,(c-a)/n,abs(c-a)/n,gi,gj,float(gi==gj)];geo.append(g);lex.append(g+[float(words[i]==words[j]),len(words[i]),len(words[j])])
        suffix=text[n:].casefold();tokens=re.findall(r'\w+|[^\w\s]',suffix);bag=np.zeros(128,np.float32)
        for token in tokens+[a+' '+b for a,b in zip(tokens,tokens[1:])]:bag[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4],'little')%len(bag)]+=1
        bag/=max(1,len(tokens));aocc=[k for k,t in enumerate(tokens) if t==words[i]];bocc=[k for k,t in enumerate(tokens) if t==words[j]];dist=min((abs(a-b) for a in aocc for b in bocc),default=len(tokens)+1);ri=1-2*gi;rj=1-2*gj
        textlex.append(np.r_[g,bag*ri,bag*rj,bag*ri*rj,len(aocc),len(bocc),dist/max(1,len(tokens))])
    arr={'geometry':np.array(geo,np.float32),'lexical':np.array(lex,np.float32),'text_lexical':np.array(textlex,np.float32),'reader_scores':reader_matrix(states,p,view,pairs,r['relation'],nets)}
    for key in KEYS:
        h=project(states[key]);
        for scope in SCOPES:arr[f'{key}:{scope}']=pair_surface(h,spans,pairs,eligible,scope)
    for src in ('original','S'):arr[src+'_multi:all']=np.concatenate([arr[f'{src}_{k}:all'] for k in (7,14,21,28)],axis=1)
    need(all(np.isfinite(v).all() and v.shape[0]==4 for v in arr.values()),'FEATURE_NONFINITE');return arr

def model_load(a,st,rows):return J.load_models(a,st[0][0],rows)

def capture_rows(a,st,split):
    _,out,ident,_=st;d,s,rows=cohort(out,ident,split);
    if split=='fresh':validate_selection(out,ident)
    cache=d/'features';cache.mkdir(exist_ok=True);models=None
    for i,r in enumerate(rows):
        path=cache/f'{i:04d}.npz';receipt=cache/f'{i:04d}.json';conditions=CONDS if split=='fresh' else ('normal',)
        if receipt.exists():
            z=read(receipt);need(z['seal']==sha(d/'seal.json') and z['prompt']==r['prompt_sha256'] and z['file']==sha(path),'FEATURE_DRIFT');continue
        if models is None:models=model_load(a,st,rows)
        comp,rr,nets,model,tok,S,ctrl=models;p=B.prepare_prompt(tok,r['prompt']);ori=m.capture(model,p,None,a.device);ref=m.capture(model,p,S,a.device);states={f'original_{k}':v for k,v in ori.items()};states.update({f'S_{k}':v for k,v in ref.items() if k!=0})
        arr={};
        for condition in conditions:
            arr.update({condition+'|'+k:v for k,v in features(r,p,states,nets,condition).items()})
        # Real G1 reader lattice, extracted from prompt alone. Score only focal
        # anchor coverage (not precision against incomplete graph annotations).
        ev=J.capture_g1(a,models,states,p) if split=='fresh' else {};tab=J.G.make_table(ev,f.ARMS) if ev else {};views=L.endpoint_views(p['prompt'],ev,st[0][0][3][1],p) if ev else {}
        anchor_meta={'node_features':node_features([{'object':list(k),'x':J.node_vector(tab,k),'options':[list(z) for z in tab[k]['options']]} for k in sorted(tab)],p,states) if split=='fresh' else [],'inventory':f.sorted_objects(tab),'selected':{k:v['objects'] for k,v in views.items()},'decode_errors':{k:v['decode_error'] for k,v in ev.items() if v['decode_error']}}
        atomic_npz(path,arr);imm(receipt,{'seal':sha(d/'seal.json'),'prompt':r['prompt_sha256'],'file':sha(path),'g1':anchor_meta});emit('CAPTURE',{'split':split,'done':i+1,'of':len(rows),'case':r['case_id'],'g1_decode_errors':len(anchor_meta['decode_errors'])})
    if models is not None:J.frozen(models)
    imm(d/'feature-lock.json',{'seal':sha(d/'seal.json'),'records':[sha(cache/f'{i:04d}.json') for i in range(len(rows))], 'selection':sha(out/'selection.json') if split=='fresh' else None})

def load_features(d,rows):
    lock=read(d/'feature-lock.json');need(lock['seal']==sha(d/'seal.json') and lock['records']==[sha(d/'features'/f'{i:04d}.json') for i in range(len(rows))],'FEATURE_LOCK');result=[];g1=[]
    for i,r in enumerate(rows):
        p=d/'features'/f'{i:04d}.npz';z=read(d/'features'/f'{i:04d}.json');need(z['prompt']==r['prompt_sha256'] and z['file']==sha(p),'FEATURE_HASH');
        with np.load(p,allow_pickle=False) as h:result.append({k:h[k] for k in h.files})
        g1.append(z['g1'])
    return result,g1

def differences(x,y):
    # Within-row and within-column competing bindings; no cross-prompt labels.
    result=[]
    for i in range(4):
        for j in range(4):
            if y[i] and not y[j] and (i//2==j//2 or i%2==j%2):result.append(x[i]-x[j])
    return result

def linear_fit(xx,yy):
    raw=np.concatenate(xx);scale=np.maximum(raw.std(0),.05);mu=raw.mean(0);diff=np.asarray([d/scale for x,y in zip(xx,yy) for d in differences(x,y)],np.float64);need(len(diff)>0,'NO_CONTRASTS');
    # Dual ridge on signed margins, relative candidate score. No intercept needed.
    a=diff@diff.T;lam=max(1.,float(np.trace(a)/len(a))*.1);w=diff.T@np.linalg.solve(a+np.eye(len(a))*lam,np.ones(len(a)));return {'kind':'linear','mu':mu.tolist(),'scale':scale.tolist(),'w':w.tolist()}

def nonlinear_fit(xx,yy):
    raw=np.concatenate(xx);mu=raw.mean(0);scale=np.maximum(raw.std(0),.05);x=torch.tensor(np.stack([(q-mu)/scale for q in xx]),dtype=torch.float32);y=torch.tensor(np.stack(yy),dtype=torch.bool)
    torch.manual_seed(SEED);net=torch.nn.Sequential(torch.nn.Linear(x.shape[-1],32),torch.nn.Tanh(),torch.nn.Linear(32,1));opt=torch.optim.AdamW(net.parameters(),lr=.005,weight_decay=.02)
    links=[(i,j) for i in range(4) for j in range(4) if i!=j and (i//2==j//2 or i%2==j%2)]
    for _ in range(100):
        z=net(x).squeeze(-1);loss=torch.cat([torch.nn.functional.softplus(-(z[:,i]-z[:,j])[y[:,i]&~y[:,j]]) for i,j in links]).mean();opt.zero_grad();loss.backward();opt.step()
    return {'kind':'nonlinear','mu':mu.tolist(),'scale':scale.tolist(),'params':[p.detach().tolist() for p in net.parameters()]}

def apply(model,x):
    z=(x-np.asarray(model['mu']))/np.asarray(model['scale']);
    if model['kind']=='linear':return z@np.asarray(model['w'])
    a,b,c,d=map(np.asarray,model['params']);return (np.tanh(z@a.T+b)@c.T+d).reshape(-1)

def ranks(rows,answers,scores):
    need(len(rows)==len(answers)==len(scores),'RANK_COUNT')
    by=co.defaultdict(dict);wins=ties=total=exact=0;ms=[];rowcorrect=0;cases=[]
    for r,g,z in zip(rows,answers,scores):
        y=g['positive'];z=np.asarray(z);need(z.shape==(4,) and np.isfinite(z).all(),'SCORE_SHAPE');ds=[float(d) for d in differences(z,y)];wins+=sum(d>1e-7 for d in ds);ties+=sum(abs(d)<=1e-7 for d in ds);total+=len(ds);ms.extend(ds);ok=all(d>1e-7 for d in ds);exact+=ok;rowcorrect+=sum(z[i if y[i] else i+1]>z[i+1 if y[i] else i]+1e-7 for i in (0,2));by[r['base_id']][r['mode']]=(y,z,ok);cases.append({'case_id':r['case_id'],'scores':z.tolist(),'positive':y,'margins':ds,'both_bindings_correct':bool(ok)})
    switches=paraphrases=pairtotal=paratotal=0;changes=[]
    for base,b in sorted(by.items()):
        for mode in ('direct','direct_paraphrase'):
            rev=mode.replace('direct','reverse');
            if mode in b and rev in b:
                a,z,ok=b[mode];c,q,ok2=b[rev];pairtotal+=1;switches+=int(ok and ok2);changes.append({'base':base,'mode':mode,'both_directions_correct':bool(ok and ok2),'signed_margin_change':float((z-q)@np.asarray([1 if i else -1 for i in a]))})
        for mode in ('direct','reverse'):
            if mode in b and mode+'_paraphrase' in b:paratotal+=1;paraphrases+=int(b[mode][2] and b[mode+'_paraphrase'][2])
    return {'cases':len(rows),'bases':len(by),'pairwise_accuracy':float((wins+.5*ties)/max(1,total)),'ties':int(ties),'comparisons':total,'mean_margin':float(np.mean(ms)) if ms else 0.,'both_bindings_correct':int(exact),'row_accuracy':float(rowcorrect/max(1,2*len(rows))),'reversal_pairs_correct':int(switches),'reversal_pairs':pairtotal,'paraphrase_pairs_correct':int(paraphrases),'paraphrase_pairs':paratotal,'changes':changes,'details':cases}

def quality(q):return (q['reversal_pairs_correct'],q['both_bindings_correct'],q['pairwise_accuracy'])
def compact(q):return {k:v for k,v in q.items() if k not in ('details','changes')}

# A separate G1 candidate discrimination lane. Training uses exposed 0117
# development lattices, never the new contrast corpus's annotation answers.
NODE_SURFACES=('reader','geometry')+KEYS+('original_multi','S_multi')
def node_features(nodes,p,states):
    h={k:project(v) for k,v in states.items() if k in KEYS};out=[];n=max(1,len(p['prompt']))
    for q in nodes:
        a,b,kind=q['object'];i,j=min(q['options'],key=lambda z:(z[1],z[0]));need(0<=i<j<=len(p['offsets']),'NODE_TOKEN_RANGE');z={'reader':q['x'],'geometry':[a/n,b/n,(b-a)/n,j-i]}
        for key,v in h.items():z[key]=np.r_[v[i],v[i:j].mean(0),v[j-1]].tolist()
        for src in ('original','S'):z[src+'_multi']=sum([z[f'{src}_{k}'] for k in (7,14,21,28)],[])
        out.append({'object':q['object'],'features':z})
    return out

def node_predict(nodes,models,baseline):
    # Independent admission only; this is a candidate set, not a valid graph.
    selected=set();scores=[]
    for q in nodes:
        k=tuple(q['object']);spec=models.get(k[2]);s=None
        if spec is None:
            if k in baseline:selected.add(k)
        else:
            s=J.model_score(q['features'][spec['surface']],spec['model']);
            if s>=spec['threshold']:selected.add(k)
        scores.append({'object':list(k),'score':s})
    return selected,scores

def node_stats(records,models):
    tp=fp=fn=0;rank=ties=total=0;overlap=0;correct=0
    for row,nodes,baseline in records:
        gold=f.gold(row);pred,scores=node_predict(nodes,models,baseline);tp+=len(gold&pred);fn+=len(gold-pred);fp+=len(pred-gold);correct+=gold==pred;overlap+=sum(f.overlap(a,b) for a in pred for b in pred if a<b)
        sc={tuple(q['object']):q['score'] for q in scores if q['score'] is not None}
        for a in gold&set(sc):
            for b in set(sc)-gold:
                if a[2]==b[2] and f.overlap(a,b):
                    total+=1;rank+=sc[a]>sc[b]+1e-7;ties+=abs(sc[a]-sc[b])<=1e-7
    return {'cases':len(records),'tp':tp,'fp':fp,'fn':fn,'f1':2*tp/max(1,2*tp+fp+fn),'precision':tp/max(1,tp+fp),'recall':tp/max(1,tp+fn),'exact_candidate_sets':int(correct),'overlapping_selected_pairs':overlap,'overlap_rank_accuracy':float((rank+.5*ties)/max(1,total)),'overlap_rank_comparisons':total}

def old_records(a,st):
    jst,out,ident,oldpub=st;x,old,ji,pr=jst;records=J.development_rows(x,pr);by=co.defaultdict(list)
    for i,(row,p,path,part,views,_) in enumerate(records):
        q=read(old/'development-cache'/f'{i:06d}.json');h=q['header'];need(h['identity']==ji and h['prompt']==row['prompt_sha256'] and h['part']==part and h['cache']==sha(path) and q['digest']==digest(q['body']),'OLD_DEV_CACHE');d=q['body'];nodes=node_features(d['nodes'],p,load_file(str(path)));by[part].append((row,nodes,f.objects(d['fixed']['0115_selected']['view']['objects'])))
    _,d,z,pub=J.check_fresh(old,ji,True);answers=read(d/'answers.SECRET.json');need(len(answers)==len(pub),'0117_ANSWER_COUNT');nets=L.readers_for(a,x[1],x[4]);forensics=[]
    for i,(p,g) in enumerate(zip(pub,answers)):
        need((p['case_id'],p['prompt_sha256'])==(g['case_id'],g['prompt_sha256']),'OLD_ANSWER_ALIGNMENT');row=dict(p,graph=g['graph']);b=J.fresh_shard(d/'predictions'/f'{i:06d}.json',p,sha(d/'seal.json'));cache=d/'cache'/f'{i:06d}.safetensors';need(b['cache_sha256']==sha(cache),'0117_STATE_DRIFT');states=load_file(str(cache));prep=m.unpack(b['prepared']);nodes=node_features(b['normal']['nodes'],prep,states);by['exposed_0117'].append((row,nodes,f.objects(b['normal']['fixed']['0115_selected']['view']['objects'])))
        # Gold endpoint diagnostic, after the original 0117 lock; unfiltered
        # logits for all same-type alternatives, including rejected candidates.
        view,missing=L.oracle_view(row,prep,int(L.GH.DEFAULT_MAX_SPAN_TOKENS));rec={'case_id':p['case_id'],'base':p['base_id'],'cohort':p['cohort'],'prompt':p['prompt'],'unalignable':missing,'comparisons':[]}
        if view is not None:
            feat=L.features(states,view,prep);obj=[tuple(k) for k in view['objects']];gold=L.gold(row);ix={k:j for j,k in enumerate(obj)}
            with torch.inference_mode():probs={k:n(feat).float().cpu() for k,n in nets.items()}
            for e in sorted(gold):
                a1,b1=J.endpoints(e);rel=e[4];k=L.CLASSES.index(rel);i1,j1=ix[a1],ix[b1]
                for a2,b2 in [(a1,t) for t in obj if t!=a1 and t!=b1 and t[2]==b1[2]]+[(t,b1) for t in obj if t!=a1 and t!=b1 and t[2]==a1[2]]:
                    if L.edge(a2,b2,rel) in gold:continue
                    i2,j2=ix[a2],ix[b2];margins={name:float(q[i1,j1,k]-q[i1,j1,0]-q[i2,j2,k]+q[i2,j2,0]) for name,q in probs.items()};rec['comparisons'].append({'positive':L.jsedges([e])[0],'competitor':L.jsedges([L.edge(a2,b2,rel)])[0],'relation':rel,'margins':margins})
        forensics.append(rec);emit('EXPOSED_FORENSICS',{'case':row['case_id'],'done':i+1,'of':len(pub),'comparisons':len(rec['comparisons'])})
    return by,forensics

def g1_develop(a,st):
    _,out,ident,_=st;dest=out/'g1-selection.json';report=out/'g1-forensics.json'
    if dest.exists():z=read(dest);need(z['identity']==ident and z['report']==sha(report),'G1_SELECTION_DRIFT');return z
    by,forensics=old_records(a,st);fit=by['fit'];val=by['validation'];models={};bake={}
    for kind in L.KINDS:
        pool=[(q,int(tuple(q['object']) in f.gold(r))) for r,nodes,_ in fit for q in nodes if q['object'][2]==kind];vpool=[(q,int(tuple(q['object']) in f.gold(r))) for r,nodes,_ in val for q in nodes if q['object'][2]==kind]
        if len({y for _,y in pool})<2 or len({y for _,y in vpool})<2:continue
        trials=[];best=None
        for surface in NODE_SURFACES:
            model=J.fit_logistic([(q['features'][surface],y) for q,y in pool]);scores=np.array([J.model_score(q['features'][surface],model) for q,y in vpool]);y=np.array([y for q,y in vpool],bool)
            for threshold in (-1.,-.5,0.,.5,1.,1.5):
                pred=scores>=threshold;tp=int((pred&y).sum());fp=int((pred&~y).sum());fn=int((~pred&y).sum());v={'surface':surface,'threshold':threshold,'f1':2*tp/max(1,2*tp+fp+fn),'tp':tp,'fp':fp,'fn':fn};trials.append(v)
                if best is None or (v['f1'],-fp)>(best[0]['f1'],-best[0]['fp']):best=(v,model)
        models[kind]={**best[0],'model':best[1]};bake[kind]=trials;emit('G1_KIND',{'kind':kind,'surface':best[0]['surface'],'validation_f1':best[0]['f1']})
    agg=co.defaultdict(lambda:co.Counter())
    for r in forensics:
        for q in r['comparisons']:
            for k,v in q['margins'].items():
                z=agg[(r['cohort'],q['relation'],k)];z['n']+=1;z['wins']+=v>1e-7;z['ties']+=abs(v)<=1e-7
    summary=[{'cohort':c,'relation':rel,'reader':k,'n':v['n'],'rank_accuracy':(v['wins']+.5*v['ties'])/v['n']} for (c,rel,k),v in sorted(agg.items())];r={'identity':ident,'scope':'exposed_0117_only_not_independent_certification','g1_bakeoff':bake,'g1':{k:{'baseline':node_stats(v,{}),'selected_candidate_admission':node_stats(v,models)} for k,v in by.items()},'g2_existing_reader_rank':summary,'g2_comparisons':forensics};imm(report,r);z={'identity':ident,'report':sha(report),'models':models};imm(dest,z);return z

def select(a,st):
    _,out,ident,_=st;dest=out/'selection.json'
    if dest.exists():validate_selection(out,ident);emit('SELECTION_REUSED',{'relations':list(RELS)});return
    g1=g1_develop(a,st);sets={}
    for part in ('fit','validation'):
        d,s,r,g=cohort(out,ident,part,True);xs,_=load_features(d,r);sets[part]=(r,g,xs)
    result={};trials={}
    for rel in RELS:
        def subset(part):
            r,g,xs=sets[part];ix=[i for i,q in enumerate(r) if q['relation']==rel];return [r[i] for i in ix],[g[i] for i in ix],[xs[i] for i in ix]
        tr,tg,tx=subset('fit');vr,vg,vx=subset('validation');y=[g['positive'] for g in tg];rr=[];models={}
        for surface in SURFACES:
            z=linear_fit([q['normal|'+surface] for q in tx],y);v=ranks(vr,vg,[apply(z,q['normal|'+surface]) for q in vx]);rr.append({'surface':surface,'kind':'linear',**compact(v)});models[surface]=z
        # Fixed lexical/geometry controls are separately reported. Only numerical
        # reader/hidden surfaces eligible for the semantic-head winner.
        candidates=[q for q in rr if q['surface'] not in ('geometry','lexical','text_lexical')];best=max(candidates,key=quality);surface=best['surface'];chosen=models[surface]
        nonlinear=nonlinear_fit([q['normal|'+surface] for q in tx],y);v=ranks(vr,vg,[apply(nonlinear,q['normal|'+surface]) for q in vx]);trial={'surface':surface,'kind':'nonlinear',**compact(v)};rr.append(trial)
        if quality(trial)>quality(best):best=trial;chosen=nonlinear
        result[rel]={'surface':surface,'model':chosen,'validation':best,'controls':{k:models[k] for k in ('geometry','lexical','text_lexical')},'linear_surfaces':models};trials[rel]=rr;emit('G2_SELECTED',{'relation':rel,**best})
    report={'identity':ident,'g1_selection':sha(out/'g1-selection.json'),'trials':trials,'development_feature_locks':{k:sha(out/k/'feature-lock.json') for k in ('fit','validation')},'selection_rule':'per relation: validation both-directions exact, then both-bindings exact, then pairwise accuracy; nonlinear only on best linear surface'};imm(out/'development.json',report);imm(dest,{'identity':ident,'development':sha(out/'development.json'),'g1_selection':sha(out/'g1-selection.json'),'relations':result});emit('SELECTION_LOCKED',{'sha256':sha(dest),'relations':list(RELS)})

def validate_selection(out,ident):
    s=read(out/'selection.json');need(s['identity']==ident and s['development']==sha(out/'development.json') and s['g1_selection']==sha(out/'g1-selection.json') and set(s['relations'])==set(RELS),'SELECTION_DRIFT');return s

def predict(a,st):
    _,out,ident,_=st;s=validate_selection(out,ident);capture_rows(a,st,'fresh');d,seal,rows=cohort(out,ident,'fresh');xx,g1=load_features(d,rows);need(read(d/'feature-lock.json')['selection']==sha(out/'selection.json'),'FRESH_FEATURE_SELECTION_DRIFT');g1spec=read(out/'g1-selection.json')['models'];folder=d/'predictions';folder.mkdir(exist_ok=True)
    for i,(r,x,meta) in enumerate(zip(rows,xx,g1)):
        rel=r['relation'];z=s['relations'][rel];body={'scores':{},'surface_scores':{},'reader_scores':{},'g1':meta}
        for condition in CONDS:
            body['scores'][condition]=apply(z['model'],x[condition+'|'+z['surface']]).tolist();body['reader_scores'][condition]={k:x[condition+'|reader_scores'][:,j].tolist() for j,k in enumerate(L.ARMS)}
        for surface,model in z['linear_surfaces'].items():body['surface_scores'][surface]=apply(model,x['normal|'+surface]).tolist()
        if 'node_features' in meta:
            nodes=meta['node_features'];sel,_=node_predict(nodes,g1spec,f.objects(meta['selected']['0115_selected']));body['g1']['new_admitted']=f.sorted_objects(sel);body['g1'].pop('node_features')
        path=folder/f'{i:04d}.json';imm(path,{'seal':sha(d/'seal.json'),'selection':sha(out/'selection.json'),'case_id':r['case_id'],'prompt':r['prompt_sha256'],'body':body,'digest':digest(body)});emit('PREDICT',{'done':i+1,'of':len(rows),'case':r['case_id']})
    need(validate_selection(out,ident)==s,'SELECTION_CHANGED_DURING_PREDICT')
    imm(d/'prediction-lock.json',{'seal':sha(d/'seal.json'),'selection':sha(out/'selection.json'),'features':sha(d/'feature-lock.json'),'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rows))]});emit('PREDICTIONS_LOCKED',{'cases':len(rows)})

def score(a,st):
    _,out,ident,_=st;s=validate_selection(out,ident);d,seal,rows,answers=cohort(out,ident,'fresh',True);lock=read(d/'prediction-lock.json');need(lock['seal']==sha(d/'seal.json') and lock['selection']==sha(out/'selection.json') and lock['features']==sha(d/'feature-lock.json') and lock['shards']==[sha(d/'predictions'/f'{i:04d}.json') for i in range(len(rows))],'PREDICTION_LOCK');bodies=[]
    for i,r in enumerate(rows):
        q=read(d/'predictions'/f'{i:04d}.json');need(q['case_id']==r['case_id'] and q['prompt']==r['prompt_sha256'] and q['selection']==lock['selection'] and q['seal']==lock['seal'] and q['digest']==digest(q['body']),'PREDICTION_DRIFT');bodies.append(q['body'])
    report={'identity':ident,'scope':'conditional_binding_probe_not_full_graph','certification':False,'g3_unlocked':False,'production_promotion':False,'selection':lock['selection'],'prediction_lock':sha(d/'prediction-lock.json'),'relations':{},'g1_anchor_recovery':{},'limitations':['all competing endpoint anchors and relation type are public in conditional lane; no true pair supplied','only two fresh shared instruction structures; eight relation-structure groups are not eight independent instruction templates; validation has only one structure per relation','controlled list and named assignment tasks test binding, not general unrestricted semantic understanding','Up-to-128-dimensional fixed random projection may discard information; failed readout does not prove absent representation','suffix_zero and shuffle are inference-time distribution shifts, not retrained ablations','fresh G1 anchor coverage is not full-graph precision; new admission output may contain overlaps','existing 0117 full-graph evidence is retrospective; no operational integration or G3 authorization']}
    for rel in (*RELS,'all'):
        ids=[i for i,r in enumerate(rows) if rel=='all' or r['relation']==rel];rr=[rows[i] for i in ids];gg=[answers[i] for i in ids];bb=[bodies[i] for i in ids];z={'selected':{k:ranks(rr,gg,[b['scores'][k] for b in bb]) for k in CONDS},'linear_surfaces':{k:ranks(rr,gg,[b['surface_scores'][k] for b in bb]) for k in SURFACES},'frozen_readers':{k:ranks(rr,gg,[b['reader_scores']['normal'][k] for b in bb]) for k in L.ARMS}};report['relations'][rel]=z;emit('FRESH_RESULT',{'relation':rel,'selected':{k:compact(v) for k,v in z['selected'].items()},'best_frozen_reader':max(z['frozen_readers'],key=lambda k:quality(z['frozen_readers'][k])),'geometry':compact(z['linear_surfaces']['geometry']),'lexical':compact(z['linear_surfaces']['lexical']),'text_lexical':compact(z['linear_surfaces']['text_lexical'])})
    for kind in ('all',*L.KINDS):
        wanted=[{(*q['span'],q['kind']) for q in r['anchors'] if kind=='all' or q['kind']==kind} for r in rows];total=sum(map(len,wanted));z={'anchors':total,'inventory':sum(len(g&f.objects(b['g1']['inventory'])) for g,b in zip(wanted,bodies))}
        for route in (*I.ROUTES,'new_admitted'):
            z[route]=sum(len(g&f.objects(b['g1'].get(route,b['g1']['selected'].get(route,[])))) for g,b in zip(wanted,bodies))
        report['g1_anchor_recovery'][kind]=z
    report['by_structure']={str(t):{rel:ranks([rows[i] for i in range(len(rows)) if rows[i]['structure']==t and rows[i]['relation']==rel],[answers[i] for i in range(len(rows)) if rows[i]['structure']==t and rows[i]['relation']==rel],[bodies[i]['scores']['normal'] for i in range(len(rows)) if rows[i]['structure']==t and rows[i]['relation']==rel]) for rel in RELS} for t in sorted({r['structure'] for r in rows})};report['g1_candidate_counts']={route:sum(len(b['g1'].get(route,b['g1']['selected'].get(route,[]))) for b in bodies) for route in (*I.ROUTES,'new_admitted','inventory')};report['normal_g1_decoder_errors']=sum(bool(b['g1']['decode_errors']) for b in bodies);report['status']='DIAGNOSTIC_REVIEW_REQUIRED';imm(out/'result.json',report);z=report['relations']['all']['selected']['normal'];(out/'RESEARCH_RECORD.md').write_text('# 0118 semantic contrast discrimination\n\nConditional candidate ranking, not full graph certification.\n\nPairwise accuracy: '+str(z['pairwise_accuracy'])+'; reversal pairs: '+str(z['reversal_pairs_correct'])+'/'+str(z['reversal_pairs'])+'; paraphrase pairs: '+str(z['paraphrase_pairs_correct'])+'/'+str(z['paraphrase_pairs'])+'.\n\nSee result.json, development.json, and g1-forensics.json for frozen selection, per-relation scores and causal controls. No G3 unlock.\n',encoding='utf-8');emit('RESULT',{'status':report['status'],'path':str(out/'result.json'),'g1':report['g1_anchor_recovery']['all'],'normal':compact(z),'certification':False})

def selftest():
    from tools.nanojev_gamer_v1_contrast_author_0118 import rows
    data=rows();need(len(data)==80 and len({r['prompt_sha256'] for r,g in data})==80,'SELFTEST_AUTHOR');need({r['structure'] for r,g in data if r['split']=='fit'}.isdisjoint({r['structure'] for r,g in data if r['split']=='fresh'}),'SPLIT')
    for rel in RELS:
        rr=[(r,g) for r,g in data if r['relation']==rel and r['structure']==0];need(rr[0][0]['anchors']==rr[1][0]['anchors'] and rr[0][1]['positive']==[1-y for y in rr[1][1]['positive']],'AUTHOR_REVERSAL')
    r,g=data[0];text=r['prompt'];offsets=[(i,i+1) for i in range(len(text))];p={'prompt':text,'prompt_start':0,'offsets':offsets,'eligible':torch.ones(len(text),dtype=torch.bool),'ids':torch.zeros(1,len(text),dtype=torch.long),'attention':torch.ones(1,len(text),dtype=torch.long),'formatted':text};states={k:torch.randn(len(text),16) for k in KEYS};nets={k:L.Reader(16,k).eval().requires_grad_(False) for k in L.ARMS};x=features(r,p,states,nets);need(set(x)==set(SURFACES),'SURFACES');need(features(r,p,states,nets,'zero_all')['original_14:endpoint'].max()==0,'ZERO');need(np.array_equal(x['original_14:endpoint'],features(r,p,states,nets,'suffix_zero')['original_14:endpoint']),'SUFFIX_ENDPOINT_INVARIANT')
    y=np.array([1,0,0,1]);w=np.array([[1.,0.],[-1.,0.],[-1.,1.],[1.,1.]]);xx=[w,-w];yy=[y,1-y];model=linear_fit(xx,yy);nn=nonlinear_fit(xx,yy);need(all(np.all(np.array(differences(apply(z,q),v))>0) for z in (model,nn) for q,v in zip(xx,yy)),'RANK_FIT');a,b=data[:2];v=ranks([a[0],b[0]],[a[1],b[1]],[np.array(a[1]['positive']),np.array(b[1]['positive'])]);need(v['reversal_pairs_correct']==1 and v['pairwise_accuracy']==1,'RANK_METRICS');tie=ranks([a[0]],[a[1]],[np.zeros(4)]);need(tie['pairwise_accuracy']==.5 and tie['both_bindings_correct']==0,'TIE')
    # Guard tested in a child so the selftest can still inspect ordinary files.
    code='from tools.nanojev_gamer_v1_semantic_integrity_probe import answer_guard; import secrets; answer_guard();\ntry: open("answers.SECRET.json","rb")\nexcept PermissionError: print("blocked")\nelse: raise SystemExit(1)'
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True);need(result.stdout.strip()=='blocked','ANSWER_GUARD')
    nr={'object':[0,1,'action'],'features':{'reader':[1.,0.]}};bad={'object':[0,2,'action'],'features':{'reader':[-1.,0.]}};mdl=J.fit_logistic([([1.,0.],1),([-1.,0.],0)]);chosen,_=node_predict([nr,bad],{'action':{'surface':'reader','model':mdl,'threshold':0.}},set());need(chosen=={(0,1,'action')},'G1_ADMISSION')
    emit('SELFTEST',{'authored_cases':80,'actual_frozen_reader_forward':True,'anchor_mapping':True,'linear_and_nonlinear_rank_fit':True,'suffix_ablation':True,'reversal_and_tie_scoring':True,'g1_admission':True,'answer_guard':True,'cuda_tested':False,'runtime_checkpoints_tested':False})

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('audit','selftest','prepare','develop','predict','score','run'));ap.add_argument('--root',default=m.ROOT);ap.add_argument('--device',default='cuda');a=ap.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    if a.command=='run':
        for phase in ('prepare','develop','predict','score'):subprocess.run([sys.executable,'-u','-m',MODULE,phase,'--root',str(Path(a.root).resolve()),'--device',a.device],check=True)
        return
    if a.command=='predict':I.answer_guard()
    st=setup(a)
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':st[2],'existing_0117_cases':28,'fit':32,'validation':16,'fresh':32,'frozen_backbone_readers':True,'scope':'research_only'});return
    if a.command=='prepare':prepare(a,st)
    elif a.command=='develop':
        for split in ('fit','validation'):capture_rows(a,st,split)
        select(a,st)
    elif a.command=='predict':predict(a,st)
    else:score(a,st)
if __name__=='__main__':main()
