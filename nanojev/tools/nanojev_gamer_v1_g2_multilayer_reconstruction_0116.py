"""G2-0116: conditional G1 acceptance, cached multilayer binding readers and graph reconstruction.
Research only. No canonical mutations, no G3 unlock. 0113 cached states are development;
12 fresh base problems (48 prompts) are the default diagnostic, not certification.
The historical 0103 raw reranker is NOT silently replaced by the unrelated 0103
admission-stability module. Seven exact 0095/0096 experts are direct comparators.
"""
from __future__ import annotations
import argparse, ast, collections as co, copy, json, math, random, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_g1_integrated_reconstruction_0115 as g1
from tools import nanojev_gamer_v1_g2_hierarchical_moe_0097 as moe

f,m,T,P,B,C,GH,A=g1.f,g1.m,g1.T,g1.P,g1.B,g1.C,g1.GH,g1.A
read,write,sha,digest=g1.read,g1.write,g1.sha,g1.digest
TAG='G2_0116_MULTILAYER'; SCHEMA='nanojev.gamer.g2.multilayer-reconstruction-0116.v1'
MODULE='tools.nanojev_gamer_v1_g2_multilayer_reconstruction_0116'
SEED=1161026; FRESH_SEED=1164026
KINDS=tuple(GH.SPAN_KINDS); CLASSES=tuple(GH.BIND_CLASSES); R=len(CLASSES)
EXPERTS=('0096__warm_supervised','0096__warm_consistency','0096__inherited_consistency','0096__champion_consistency','0095__B_fresh_seeded_G2_cutover__reptile','0095__A_reset_optimizer_from_0092_B__jacobian_inverse','0095__A_reset_optimizer_from_0092_B__reptile')
ARMS={'embedding':('original',(0,),False,False)}
for src in ('original','S'):
    for ls in ((7,),(14,),(21,),(28,),(14,21),(7,14,21,28)):
        ARMS[src+'_L'+'_'.join(map(str,ls))]=(src,ls,False,False)
ARMS['original_all_specialist']=('original',(7,14,21,28),False,True)
ARMS['original_all_lexical']=('original',(7,14,21,28),True,False)
G1_NAMES=('original_frozen','0114_full','0115_selected')
BASE_SPEC={'rule':'architecture6','weight':'consensus','arms':list(f.ARMS)}
MODES=('control','alias+forward','pronoun+forward','caps')
ALT_MODES=('base','role_swap','added_distractor','action_removal')

def need(ok,msg):
    if not ok: raise ValueError(TAG+'_'+msg)
def emit(ev,x): print(TAG+'_'+ev+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)
def immutable(path,x):
    if path.exists(): need(read(path)==x,'IMMUTABLE_DRIFT_'+path.name)
    else: write(path,x)
def names(x): return list(x)
def edge(a,b,t): return (a[2],tuple(a[:2]),b[2],tuple(b[:2]),t)
def edge_set(x): return g1.edge_set(x)
def jsedges(x): return g1.json_edges(x)
def gold(row): return g1.edges(row['graph'])
def as_objects(spans,p):
    out=[]
    for sp in spans:
        bounds=GH.token_span_char_bounds(sp,p);need(bounds is not None,'BAD_SPAN');out.append((*bounds,sp['kind']))
    return out

def parent(a):
    base=g1.check_parent(a); folder,old,r,paths,g2,ident=base
    d=Path(a.root)/'g1-integrated-reconstruction-0115'; fixed=g1.sealed_parent(d,base)
    sel=g1.selection(d,fixed[-1])
    _,_,_,refdir,h95,h96=moe._verify(a.root)
    registry=moe._expert_registry(h95,h96,refdir,expert_set='all')
    need(set(EXPERTS)<=set(registry),'SEVEN_EXPERTS_UNAVAILABLE')
    reg={k:registry[k] for k in EXPERTS}
    identity={'schema':SCHEMA,'implementation':sha(__file__),'parent_0114':sha(old/'result.json'),'parent_0113_cache':ident['parent_0114_identity']['parent_cache_sha256'],'parent_0113_selection':ident['parent_0114_identity']['parent_selection_sha256'],'g1_identity':ident,'g1_0115_selection':sha(d/'development'/'selection.json'),'experts':reg,'expert_reference':sha(refdir/C.HEAD_FILE),'expert_sources':{Path(x.__file__).name:sha(x.__file__) for x in (moe,moe.old,moe.exp,GH,C)},'config':{'epochs':a.epochs,'lr':a.lr,'bases':a.bases,'device':a.device,'seed':SEED,'fresh_seed':FRESH_SEED,'arms':{k:[v[0],list(v[1]),v[2],v[3]] for k,v in ARMS.items()}},'production_promotion':False,'g3_unlocked':False}
    return base,sel['policy'],reg,refdir,identity

def load_experts(ctx,device):
    base,_,registry,refdir,_=ctx
    head,_=C._load_checkpoint(refdir,device=device); moe.old._mutable(head); frozen=moe.old._frozen_hash(head)
    out={}
    for name in EXPERTS:
        h=copy.deepcopy(head);entry=registry[name]
        moe.exp._load_state_to_head(h,entry['path'],frozen_hash=frozen,expected_hash=entry['sha256'])
        out[name]=h.eval().requires_grad_(False)
    h,manifest=C._load_checkpoint(base[4],device=device);need(manifest['head_sha256']==base[-1]['g2_0087_head_sha256'],'0087_DRIFT')
    out['historical_0087']=h.eval().requires_grad_(False)
    return out

def endpoint_views(prompt,ev,policy,p):
    tab=g1.make_table(ev,f.ARMS); orig=f.objects([s['object'] for s in ev[f.BASE]['selected']])
    balanced,bt=g1.predict_objects(prompt,ev,BASE_SPEC); recall,rt=g1.predict_objects(prompt,ev,policy)
    return {k:{'objects':f.sorted_objects(o),'spans':g1.token_assignment(o,t,p)} for k,o,t in [('original_frozen',orig,tab),('0114_full',balanced,bt),('0115_selected',recall,rt)]}

def oracle_view(row,p,max_span):
    # Exact char coordinates only; unalignable author objects are reported, not rounded.
    wanted=f.gold(row); lookup=co.defaultdict(list)
    for k in wanted: lookup[k[:2]].append(k)
    offsets=p['offsets']; start=p['prompt_start']; text=p['prompt']; eligible=p['eligible'].tolist(); tab={}
    for i,ok in enumerate(eligible):
        if not ok:continue
        for j in range(i,min(i+max_span,len(eligible))):
            if not eligible[j]:break
            a,b=offsets[i][0]-start,offsets[j][1]-start
            if not 0<=a<b<=len(text):continue
            while a<b and text[a].isspace():a+=1
            while b>a and text[b-1].isspace():b-=1
            for k in lookup.get((a,b),[]):tab.setdefault(k,{'options':[]})['options'].append((i,j+1))
    if set(tab)!=wanted:return None, f.sorted_objects(wanted-set(tab))
    try:spans=g1.token_assignment(wanted,tab,p)
    except ValueError as exc:
        if 'NO_COMPATIBLE_TOKEN_ASSIGNMENT' not in str(exc):raise
        return None,f.sorted_objects(wanted)
    return {'objects':f.sorted_objects(wanted),'spans':spans},[]

def pooled(states,spans):
    # Precompute start / mean / end features once. No learned extraction or gold here.
    if not spans:return states.new_empty((0,3,states.shape[-1]),dtype=torch.float32)
    h=states.float();a=torch.tensor([s['token_first'] for s in spans],device=h.device);b=torch.tensor([s['token_last'] for s in spans],device=h.device)
    cum=torch.cat((h.new_zeros((1,h.shape[-1])),h.cumsum(0)),0)
    return torch.stack((h[a],(cum[b+1]-cum[a])/(b-a+1)[:,None],h[b]),1)

def features(states,view,p):
    obj=as_objects(view['spans'],p);need(obj==[tuple(x) for x in view['objects']],'ENDPOINT_ORDER_OR_COORDINATES')
    n=len(obj); size=max(1,len(p['prompt'])); a=torch.tensor([x[0]/size for x in obj]);b=torch.tensor([x[1]/size for x in obj]);kind=torch.tensor([KINDS.index(x[2]) for x in obj],dtype=torch.long)
    loc=torch.stack((a,b,b-a),-1); delta=a[None,:]-a[:,None]
    geom=torch.stack((delta,delta.abs(),(a[:,None]>a[None,:]).float(),(b[:,None]<=a[None,:]).float()),-1)
    words=[' '.join(p['prompt'][x[0]:x[1]].casefold().split()) for x in obj];equal=torch.tensor([[float(x==y) for y in words] for x in words]).reshape(n,n,1)
    return {'objects':obj,'states':{k:pooled(v,view['spans']) for k,v in states.items()},'kind':kind,'loc':loc,'geom':geom,'lex':equal}

def labels(obj,g):
    n=len(obj); y=torch.zeros((n,n),dtype=torch.long);seen={}
    for i,a in enumerate(obj):
        for j,b in enumerate(obj):
            if i==j:continue
            hits=[k for k,t in enumerate(CLASSES) if k and edge(a,b,t) in g]
            need(len(hits)<=1,'AUTHOR_MULTILABEL_PAIR_CONFLICT')
            if hits:y[i,j]=hits[0]
    return y

class Reader(nn.Module):
    """Matched context-aware endpoint readout; same width per reading surface.
    The lexical arm is an explicit input intervention, not evidence about Qwen alone.
    Specialist arm trains independent binding-vs-none margins with BCE.
    """
    def __init__(self,dim,arm,width=96):
        super().__init__();self.arm=arm;self.src,self.layers,self.lexical,self.specialist=ARMS[arm]
        self.mix=nn.Parameter(torch.zeros(len(self.layers)))
        self.project=nn.Linear(dim*3,width);self.kinds=nn.Embedding(len(KINDS),16)
        self.node=nn.Linear(width+19,width);self.context=nn.TransformerEncoderLayer(width,4,width*2,dropout=0.,batch_first=True)
        self.pair=nn.Sequential(nn.Linear(width*4+5,width),nn.GELU(),nn.Linear(width,R))
    def forward(self,feat):
        dev=self.project.weight.device; xs=[feat['states'][f'{self.src}_{k}'].to(dev).float() for k in self.layers]
        n=xs[0].shape[0]
        if not n:return self.project.weight.new_zeros((0,0,R))
        norm=[F.layer_norm(x,(x.shape[-1],)) for x in xs]
        z=sum(w*x for w,x in zip(self.mix.softmax(0),norm));z=F.gelu(self.project(z.flatten(1)))
        z=self.node(torch.cat((z,self.kinds(feat['kind'].to(dev)),feat['loc'].to(dev)),dim=-1))
        z=self.context(z.unsqueeze(0))[0];left=z[:,None,:].expand(n,n,-1);right=z[None,:,:].expand(n,n,-1)
        lex=feat['lex'].to(dev) if self.lexical else torch.zeros_like(feat['lex'],device=dev)
        return self.pair(torch.cat((left,right,left*right,left-right,feat['geom'].to(dev),lex),-1))

def loss(net,feat,y):
    raw=net(feat);n=len(y);mask=~torch.eye(n,dtype=torch.bool,device=raw.device);y=y.to(raw.device)
    if not bool(mask.any()):return raw.sum()*0
    if net.specialist:
        z=raw[...,1:]-raw[...,0:1];t=F.one_hot(y,R)[...,1:].float();return F.binary_cross_entropy_with_logits(z[mask],t[mask],pos_weight=z.new_full((R-1,),8.))
    return F.cross_entropy(raw[mask],y[mask],weight=raw.new_tensor([1.]+[8.]*(R-1)))

def caps_from(rows):
    maxima={}
    for row in rows:
        counts=co.Counter()
        for a,x,b,y,t in gold(row):
            counts[('out',a,x,b,t)]+=1;counts[('in',b,y,a,t)]+=1
        for (side,kind,span,other,t),v in counts.items():
            key='|'.join((side,kind,other,t));maxima[key]=max(maxima.get(key,0),v)
    return maxima

def choose_edges(obj,scores,threshold,margin=0.,caps=None):
    # Scores are probabilities (not calibrated guarantees). One label per ordered pair.
    n=len(obj);need(tuple(scores.shape)==(n,n,R) and bool(torch.isfinite(scores).all()),'SCORE_SHAPE')
    if not n:return set()
    vals,idx=scores[...,1:].max(-1);diff=vals-scores[...,0];chosen=[]
    for i,j in ((vals>=threshold)&(diff>=margin)&~torch.eye(n,dtype=torch.bool)).nonzero().tolist():
        t=int(idx[i,j])+1;chosen.append((float(vals[i,j]),i,j,t))
    return assemble(obj,chosen,caps)

def assemble(obj,candidates,caps=None):
    used=set();counts=co.Counter();out=set()
    for _,i,j,k in sorted(candidates,key=lambda x:(-x[0],x[1],x[2],x[3])):
        if i==j or (i,j) in used:continue
        a,b=obj[i],obj[j];t=CLASSES[k];keys=(('out',i,a[2],b[2],t),('in',j,b[2],a[2],t))
        if caps is not None and any(counts[key]>=caps.get('|'.join((key[0],*key[2:])),128) for key in keys):continue
        used.add((i,j));out.add(edge(a,b,t));counts.update(keys)
    return out

def counts(row,obj,pred):
    g=gold(row); anchors={(k[2],tuple(k[:2])) for k in obj};reach={e for e in g if (e[0],e[1]) in anchors and (e[2],e[3]) in anchors}
    need(all((e[0],e[1]) in anchors and (e[2],e[3]) in anchors for e in pred),'OUTSIDE_ENDPOINTS')
    return co.Counter(cases=1,tp=len(pred&g),fp=len(pred-g),fn=len(g-pred),exact=int(pred==g),strict_surface_graph=int(pred==g and set(map(tuple,obj))==f.gold(row)),gold=len(g),reachable=len(reach),upstream_fn=len(g-reach),reachable_fn=len(reach-pred))

def metrics(c):
    d=dict(c);tp=c['tp'];p=tp/max(1,tp+c['fp']);r=tp/max(1,tp+c['fn']);d.update(precision=p,recall=r,f1=2*p*r/max(1e-12,p+r),exact_rate=c['exact']/max(1,c['cases']),reachable_recall=tp/max(1,c['reachable']));return d

def measure(rows,objs,preds):
    c=co.Counter()
    for row,obj,pred in zip(rows,objs,preds):c.update(counts(row,obj,pred))
    return metrics(c)
def quality(x):return (x['f1'],x['exact'],x['precision'])

def individual(obj,scores,spec,caps):return choose_edges(obj,scores,spec['threshold'],spec['margin'],caps if spec['caps'] else None)
def individual_grid():
    return [{'threshold':t,'margin':d,'caps':c} for t in (.20,.35,.50,.65,.80) for d in (0.,-.20) for c in (False,True)]

def policy_predict(obj,probs,decoders,spec,caps):
    arms=spec['arms']; stack=torch.stack([probs[a] for a in arms]);avg=stack.mean(0);maximum=stack.max(0).values
    if spec['rule'] in ('mean','max','relation_mean'):
        if spec['rule']=='relation_mean':
            w=torch.tensor([spec['relation_weights'][a] for a in arms],dtype=stack.dtype)[:,None,None,:]
            q=(stack*w).sum(0)/w.sum(0);q=q/q.sum(-1,keepdim=True).clamp_min(1e-9)
        else:q=avg if spec['rule']=='mean' else maximum
        return choose_edges(obj,q,spec['threshold'],spec['margin'],caps if spec['caps'] else None)
    vote=co.Counter(e for a in arms for e in decoders[a]);ix={(k[2],tuple(k[:2])):i for i,k in enumerate(obj)};candidates=[]
    def group(a):return a.removeprefix('original_').removeprefix('S_') if a in ARMS else 'legacy_experts'
    groups={group(a) for a in arms}
    for e,v in vote.items():
        a,x,b,y,t=e;i,j=ix[a,x],ix[b,y];k=CLASSES.index(t);n=len(arms);confidence=float(avg[i,j,k]);strength=v/n
        if spec['rule']=='architecture_vote':strength=len({group(a) for a in arms if e in decoders[a]})/len(groups)
        if strength>=spec['threshold'] or (spec['rule']=='rescue' and strength>=1/n and float(maximum[i,j,k])>=.9 and confidence>=.20):candidates.append((strength+confidence*.2,i,j,k))
    return assemble(obj,candidates,caps if spec['caps'] else None)

def policy_grid(arms,relation_weights=None):
    out=[]
    for rule in ('mean','max','vote','rescue','architecture_vote','relation_mean'):
        if rule=='relation_mean' and relation_weights is None:continue
        for t in (.20,.35,.50,.65,.80):
            for cap in (False,True):out.append({'arms':list(arms),'rule':rule,'threshold':t,'margin':0.,'caps':cap}|({'relation_weights':relation_weights} if rule=='relation_mean' else {}))
    return out

def fit_reader(a,out,arm,train,val,dim,identity,caps):
    folder=out/'readers'/arm;folder.mkdir(parents=True,exist_ok=True);checkpoint=folder/'selected.safetensors';receipt=folder/'receipt.json'
    if receipt.exists():
        r=read(receipt);need(r['identity']==digest(identity) and sha(checkpoint)==r['checkpoint_sha256'],'READER_DRIFT_'+arm)
        net=Reader(dim,arm).to(a.device);net.load_state_dict(load_file(str(checkpoint)),strict=True);return net.eval().requires_grad_(False),r
    torch.manual_seed(SEED);net=Reader(dim,arm).to(a.device);opt=torch.optim.AdamW(net.parameters(),lr=a.lr);history=[];best=None;state=None
    rows=[r for r,x,y in val];objs=[x['objects'] for r,x,y in val]
    for epoch in range(a.epochs):
        order=list(range(len(train)));random.Random(SEED+epoch).shuffle(order);ls=[];net.train()
        for i in order:
            _,x,y=train[i]
            if len(y)<2:continue
            opt.zero_grad(set_to_none=True);l=loss(net,x,y);need(bool(torch.isfinite(l)),'NONFINITE_LOSS');l.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),5.);opt.step();ls.append(float(l.detach()))
        net.eval()
        with torch.inference_mode():probs=[net(x).softmax(-1).cpu() for r,x,y in val]
        scored=[]
        for spec in individual_grid():scored.append((measure(rows,objs,[individual(o,p,spec,caps) for o,p in zip(objs,probs)]),spec))
        metric,spec=max(scored,key=lambda z:quality(z[0]));record={'epoch':epoch+1,'loss':sum(ls)/max(1,len(ls)),'validation':metric,'decoder':spec};history.append(record)
        if best is None or quality(metric)>quality(best['validation']):best=record;state={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
        emit('LEARN',{'arm':arm,**record})
    need(state is not None,'NO_READER_FIT');m.save(checkpoint,state);r={'identity':digest(identity),'checkpoint_sha256':sha(checkpoint),'selected':best,'history':history};immutable(receipt,r);net.load_state_dict(state);return net.eval().requires_grad_(False),r

def expert_scores(experts,states,view,p,device):
    h=states['original_28'].to(device,dtype=torch.float32);n=len(view['spans']);out={}
    for name,net in experts.items():
        if n==0:out[name]=torch.empty(0,0,R);continue
        with torch.inference_mode():raw=net(h,eligible=p['eligible'].to(device),inference_spans=view['spans'],active_stage='G2')['binding_logits'][:n,:n]
        out[name]=raw.softmax(-1).cpu()
    return out

def decode_case(obj,probs,selection):
    caps=selection['caps'];ind={k:individual(obj,p,selection['decoders'][k],caps) for k,p in probs.items()};out=dict(ind)
    for k,spec in selection['policies'].items():out[k]=policy_predict(obj,probs,ind,spec,caps)
    return out

def dev_data(a,ctx):
    base,policy,_,_,_=ctx;folder,old,r,paths,*_=base
    root=folder.parent;seal=read(root/'cohort-seal.json');seal['groups']={s:seal['groups'][s] for s in ('train','validation','test')}
    _,data=m.cached_data(root,seal,sha(root/'cohort-seal.json'));dev,_=g1.load_development(a,folder,old,r)
    evs={row['case_id']:ev for split,pairs in dev.items() for row,ev in pairs}
    # Gold-anchor training is explicit. Add observed 0114 endpoints on 8 exposed test bases
    # to teach the no-binding class on false objects; all 0113 test is now development.
    selected_bases={bid for fam in ('allocation','encounter') for bid in sorted({r['base_id'] for r,p,path in data['test'] if r['family']==fam})[:4]};train=[];val=[];bad=[];rawval=[];maxspan=128
    for split in ('train','test','validation'):
        for row,p,path in data[split]:
            if split=='test' and row['base_id'] not in selected_bases:continue
            states=load_file(str(path));
            if split=='train':
                view,missing=oracle_view(row,p,maxspan)
                if view is None:bad.append({'case':row['case_id'],'objects':missing});continue
            else:view=endpoint_views(row['prompt'],evs[row['case_id']],policy,p)['0114_full']
            feat=features(states,view,p);y=labels(feat['objects'],gold(row));item=(row,feat,y)
            if split=='validation':val.append(item);rawval.append((row,p,path,view))
            else:train.append(item)
    need(len(train)>=80 and len(val)>=32,'INSUFFICIENT_DEVELOPMENT');need({r['base_id'] for r,x,y in train}.isdisjoint({r['base_id'] for r,x,y in val}),'BASE_LEAKAGE')
    return train,val,rawval,bad

def validate_selection(out,identity):
    s=read(out/'selection.json');need(s['identity']==identity,'SELECTION_IDENTITY_DRIFT')
    need(sha(out/'development.json')==s['development_sha256'],'DEVELOPMENT_DRIFT')
    for arm,h in s['checkpoints'].items():need(sha(out/'readers'/arm/'selected.safetensors')==h,'SELECTED_READER_DRIFT_'+arm)
    return s

def develop(a,out,ctx):
    identity=ctx[-1]
    if (out/'selection.json').exists():validate_selection(out,identity);emit('DEVELOPMENT_REUSED',{});return
    train,val,rawval,bad=dev_data(a,ctx);dim=next(iter(train[0][1]['states'].values())).shape[-1];caps=caps_from([r for r,x,y in train]);rows=[r for r,x,y in val];objs=[x['objects'] for r,x,y in val];allprobs=[{} for _ in val];receipts={};decoders={};scores={}
    emit('DEVELOPMENT_START',{'training':len(train),'validation':len(val),'gold_unalignable':bad,'backbone_loaded':False,'readers':len(ARMS),'frozen_experts':len(EXPERTS)})
    experts=load_experts(ctx,a.device)
    for i,(row,p,path,view) in enumerate(rawval):allprobs[i].update(expert_scores(experts,load_file(str(path)),view,p,a.device))
    for name in (*EXPERTS,'historical_0087'):
        metric,spec=max(((measure(rows,objs,[individual(o,p[name],s,caps) for o,p in zip(objs,allprobs)]),s) for s in individual_grid()),key=lambda z:quality(z[0]));decoders[name]=spec;scores[name]=metric
    emit('FROZEN_BASELINE',scores)
    del experts
    for arm in ARMS:
        net,receipt=fit_reader(a,out,arm,train,val,dim,identity,caps);receipts[arm]=receipt;decoders[arm]=receipt['selected']['decoder'];scores[arm]=receipt['selected']['validation']
        with torch.inference_mode():
            for i,(r,x,y) in enumerate(val):allprobs[i][arm]=net(x).softmax(-1).cpu()
        del net
    decoded=[{k:individual(o,p[k],decoders[k],caps) for k in p} for o,p in zip(objs,allprobs)]
    # Type-specific weights are computed only from exposed validation labels.
    # This is a development heuristic, not an independently calibrated probability.
    relation_weights={}
    for name in decoders:
        c=co.defaultdict(co.Counter)
        for row,d in zip(rows,decoded):
            g=gold(row);p=d[name]
            for e in g&p:c[e[4]]['tp']+=1
            for e in p-g:c[e[4]]['fp']+=1
            for e in g-p:c[e[4]]['fn']+=1
        relation_weights[name]=[1.]+[.1+2*c[t]['tp']/max(1,2*c[t]['tp']+c[t]['fp']+c[t]['fn']) for t in CLASSES[1:]]
    policies={};trials=[]
    for name,arms in [('expert_integrated',EXPERTS),('new_integrated',tuple(ARMS)),('combined_integrated',(*EXPERTS,*ARMS)),('original_only',tuple(k for k,v in ARMS.items() if v[0]=='original'))]:
        candidates=[]
        for spec in policy_grid(arms,relation_weights):
            met=measure(rows,objs,[policy_predict(o,p,d,spec,caps) for o,p,d in zip(objs,allprobs,decoded)]);candidates.append((met,spec));trials.append({'group':name,'policy':spec,'metrics':met})
        metric,spec=max(candidates,key=lambda x:quality(x[0]));scores[name]=metric;policies[name]=spec
    # Greedy reduction on validation (development only). No claim of globally optimal subset.
    current=list(policies['combined_integrated']['arms']);frontier=[];base=policies['combined_integrated'];best= scores['combined_integrated'];winner=base
    while len(current)>1:
        options=[]
        for remove in current:
            spec=dict(base,arms=[x for x in current if x!=remove]);met=measure(rows,objs,[policy_predict(o,p,d,spec,caps) for o,p,d in zip(objs,allprobs,decoded)]);options.append((met,spec,remove))
        met,spec,remove=max(options,key=lambda z:quality(z[0]));current=spec['arms'];frontier.append({'removed':remove,'policy':spec,'metrics':met});
        if met['f1']>=best['f1']-.001 and met['exact']>=best['exact']-1:winner=spec
    policies['reduced_integrated']=winner;scores['reduced_integrated']=measure(rows,objs,[policy_predict(o,p,d,winner,caps) for o,p,d in zip(objs,allprobs,decoded)])
    chosen=max(scores,key=lambda k:quality(scores[k]));report={'schema':SCHEMA,'identity':identity,'training_cases':len(train),'validation_cases':len(val),'training_bases':sorted({r['base_id'] for r,x,y in train}),'validation_bases':sorted({r['base_id'] for r,x,y in val}),'unalignable_training':bad,'scores':scores,'trials':trials,'reduction':frontier,'selected':chosen,'historical_0103_direct':'UNAVAILABLE_SOURCE_NOT_SUBSTITUTED'};write(out/'development.json',report)
    s={'identity':identity,'development_sha256':sha(out/'development.json'),'dimension':dim,'caps':caps,'decoders':decoders,'policies':policies,'selected':chosen,'checkpoints':{k:r['checkpoint_sha256'] for k,r in receipts.items()},'fresh_not_used':True};immutable(out/'selection.json',s);emit('DEVELOPMENT_SELECTED',{'selected':chosen,'metrics':scores[chosen],'all_metrics':scores,'reduced_readers':len(winner['arms'])})

def prepare(a,out,ctx):
    s=validate_selection(out,ctx[-1]);d=out/'fresh';d.mkdir(exist_ok=True);sealpath=d/'seal.json'
    if sealpath.exists():check_seal(out,ctx[-1],answers=True);emit('SEAL_REUSED',{'cases':read(sealpath)['cases']});return
    folder,old,r,paths,*_=ctx[0];root=Path(a.root);known=A._known_prompts(paths,a.root)
    for path in (root/'g1-semantic-fuzz-0110'/'pilot'/'seal.jsonl',root/'g1-semantic-fuzz-0110'/'expansion'/'seal.jsonl'):
        if path.exists():
            with path.open(encoding='utf-8') as stream:known.update(json.loads(l)['prompt_sha256'] for l in stream if l.strip())
    for path in (root/'g1-s-first-reference-0111'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'probe'/'cohort-seal.json',root/'g1-backbone-transmission-0112'/'run'/'cohort-seal.json',folder.parent/'cohort-seal.json'):
        known.update(r['prompt_sha256'] for rows in read(path)['groups'].values() for r in rows)
    prior=root/'g1-integrated-reconstruction-0115'/'certification'/'prompts.json'
    if prior.exists():known.update(r['prompt_sha256'] for r in read(prior))
    lab=P.fuzz();rng=random.Random(FRESH_SEED);suites=[];serial=0;initial=len(known)
    for i in range(a.bases):
        generator=i%2;family=('allocation','encounter')[(i//2)%2];bid=f'g2-0116-{i:04d}'
        for attempt in range(200):
            if generator==0:
                serial+=1
                k=2*serial+int(family=='encounter');base=lab._base(k,seed=FRESH_SEED);base['split']='test';suite=[lab._variant(base,x) for x in MODES]
                need(all(r['family']==family for r in suite),'PRIMARY_FAMILY_DRIFT')
                cohort='0110_generator'
            else:
                z=A._controlled_suite(family,rng.randrange(2**31),116000000+i*200+attempt);suite=[dict(z[x],mode=x,prompt_sha256=P.digest_text(z[x]['prompt'])) for x in ALT_MODES];cohort='0093_generator'
            hashes=[r['prompt_sha256'] for r in suite]
            if len(set(hashes))==len(hashes) and not set(hashes)&known:break
        else:raise ValueError(TAG+'_FRESHNESS_EXHAUSTED')
        known.update(hashes);suites.append([dict(r,case_id=bid+'-'+r['mode'],base_id=bid,family=family,cohort=cohort) for r in suite])
    # Across independent bases first, then linguistic variants; useful early research signal.
    rows=[suite[j] for j in range(4) for suite in suites];public=[];secret=[]
    for r in rows:
        need(r['prompt_sha256']==P.digest_text(r['prompt']),'PROMPT_DIGEST');public.append({k:r[k] for k in ('case_id','base_id','family','cohort','mode','prompt','prompt_sha256')});secret.append({'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'graph':r['graph']})
    immutable(d/'prompts.json',public);immutable(d/'answers.SECRET.json',secret)
    seal={'schema':SCHEMA,'identity':digest(ctx[-1]),'selection':sha(out/'selection.json'),'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'cases':len(rows),'bases':a.bases,'prior_prompts_excluded':initial,'round_robin':True,'gold_read_in_predict':False};immutable(sealpath,seal);emit('SEALED',seal)

def check_seal(out,identity,answers=False):
    s=validate_selection(out,identity);d=out/'fresh';seal=read(d/'seal.json');need(seal['identity']==digest(identity) and seal['selection']==sha(out/'selection.json') and seal['prompts']==sha(d/'prompts.json') and seal['gold_read_in_predict'] is False,'SEAL_DRIFT')
    if answers:need(seal['answers']==sha(d/'answers.SECRET.json'),'ANSWER_DRIFT')
    public=read(d/'prompts.json');need(len(public)==seal['cases'] and len({r['prompt_sha256'] for r in public})==len(public),'PUBLIC_BIJECTION')
    for r in public:need(set(r)=={'case_id','base_id','family','cohort','mode','prompt','prompt_sha256'} and P.digest_text(r['prompt'])==r['prompt_sha256'],'PUBLIC_SCHEMA')
    return s,seal,public

def readers_for(a,out,sel):
    outnets={}
    for arm in ARMS:
        p=out/'readers'/arm/'selected.safetensors';need(sha(p)==sel['checkpoints'][arm],'CHECKPOINT_DRIFT');net=Reader(sel['dimension'],arm).to(a.device);net.load_state_dict(load_file(str(p)),strict=True);outnets[arm]=net.eval().requires_grad_(False)
    return outnets

def evaluate_view(a,states,p,view,readers,experts,sel):
    x=features(states,view,p);obj=x['objects'];probs=expert_scores(experts,states,view,p,a.device)
    with torch.inference_mode():
        for k,net in readers.items():probs[k]=net(x).softmax(-1).cpu()
    predictions=decode_case(obj,probs,sel);predictions['historical_0087_native']=choose_edges(obj,probs['historical_0087'],.5,0.,None);ind={k:predictions[k] for k in probs};union=set().union(*ind.values());vote=co.Counter(e for pp in ind.values() for e in pp)
    # Capture complete union with support and numeric confidence; never add a gold candidate.
    ix={(k[2],tuple(k[:2])):i for i,k in enumerate(obj)};entries=[]
    for e,v in sorted(vote.items()):
        i,j=ix[e[0],e[1]],ix[e[2],e[3]];k=CLASSES.index(e[4]);support=[name for name,pp in ind.items() if e in pp];conf=[float(probs[name][i,j,k]) for name in support];entries.append({'edge':jsedges([e])[0],'votes':v,'readers':support,'mean_probability':sum(conf)/len(conf),'max_probability':max(conf)})
    # Raw rank ceiling is intentionally separate from admitted-reader union.
    raw_top1=set()
    for name,q in probs.items():
        if not len(obj):continue
        best=q[...,1:].argmax(-1)
        for i in range(len(obj)):
            for j in range(len(obj)):
                if i!=j:raw_top1.add(edge(obj[i],obj[j],CLASSES[int(best[i,j])+1]))
    return {'objects':view['objects'],'predictions':{k:jsedges(v) for k,v in predictions.items()},'union':jsedges(union),'raw_top1_positive_union':jsedges(raw_top1),'evidence':entries,'individual_readers':list(ind)}

def shard(d,i,row,sealhash):
    path=d/'predictions'/f'{i:06d}.json';cache=d/'cache'/f'{i:06d}.safetensors'
    if not path.exists():return None
    r=read(path);need(r['case_id']==row['case_id'] and r['prompt_sha256']==row['prompt_sha256'] and r['seal']==sealhash and r['body_sha256']==digest(r['body']) and sha(cache)==r['cache_sha256'],'SHARD_DRIFT_'+row['case_id']);return r

def predict(a,out,ctx):
    sel,seal,public=check_seal(out,ctx[-1]);d=out/'fresh';sealhash=sha(d/'seal.json');(d/'predictions').mkdir(exist_ok=True);(d/'cache').mkdir(exist_ok=True)
    existing=[shard(d,i,r,sealhash) for i,r in enumerate(public)];done=sum(x is not None for x in existing)
    if (d/'prediction-lock.json').exists():need(done==len(public) and read(d/'prediction-lock.json')=={'seal':sealhash,'shards':[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(public))],'gold_read':False},'PREDICTION_LOCK_DRIFT');emit('PREDICTIONS_REUSED',{'cases':done});return
    # Model is needed only for uncached fresh prompts. No fresh answers loaded here.
    model,tok,comp,ctrl,S,sig,lin=T.load(a.root,{'fresh':public},a.device);need(sig==ctx[0][-1]['frozen_g1'],'FROZEN_G1_DRIFT')
    folder=ctx[0][0];g1readers={}
    for arm in f.ARMS:
        _,ls,kind=m.ARMS[arm];net=m.Reader(comp.head,ls,kind).to(a.device);net.load_state_dict(load_file(str(folder/arm/'selected.safetensors')),strict=True);g1readers[arm]=net.eval().requires_grad_(False)
    readers=readers_for(a,out,sel);experts=load_experts(ctx,a.device)
    for i,row in enumerate(public):
        if existing[i] is not None:continue
        p=B.prepare_prompt(tok,row['prompt']);original=m.capture(model,p,None,a.device);ref=m.capture(model,p,S,a.device);states={f'original_{k}':v for k,v in original.items()};states.update({f'S_{k}':v for k,v in ref.items() if k!=0});ev={}
        for arm,net in g1readers.items():
            src,ls,_=m.ARMS[arm];hs=[states[f'{src}_{k}'].to(a.device,dtype=torch.float32) for k in ls];ev[arm]=f.capture_one(comp,net,({'case_id':row['case_id']},p,None),hs)
        views=endpoint_views(row['prompt'],ev,ctx[1],p);body={'prepared':m.pack(p),'views':{},'endpoint_views':views,'g1_decode_errors':{k:v['decode_error'] for k,v in ev.items() if v['decode_error'] is not None}}
        # Avoid repeated inference when two G1 policies yield exactly the same objects/mapping.
        memo={}
        for name,view in views.items():
            key=digest(view)
            if key not in memo:memo[key]=evaluate_view(a,states,p,view,readers,experts,sel)
            body['views'][name]=memo[key]
        cache=d/'cache'/f'{i:06d}.safetensors';m.save(cache,states,{'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256']});record={'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],'seal':sealhash,'cache_sha256':sha(cache),'body':body,'body_sha256':digest(body)};immutable(d/'predictions'/f'{i:06d}.json',record);done+=1;emit('PREDICT_PROGRESS',{'done':done,'of':len(public),'base':row['base_id'],'mode':row['mode']})
    need(all(not p.requires_grad and p.grad is None for net in [model,comp.head,*g1readers.values(),*readers.values(),*experts.values()] for p in net.parameters()),'FROZEN_WEIGHT_VIOLATION')
    need(parent(a)[-1]==ctx[-1],'PARENT_CHANGED');validate_selection(out,ctx[-1]);need(all(shard(d,i,r,sealhash) is not None for i,r in enumerate(public)),'MISSING_SHARD')
    immutable(d/'prediction-lock.json',{'seal':sealhash,'shards':[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(public))],'gold_read':False});emit('PREDICTIONS_LOCKED',{'cases':len(public)})

def paired_invariance(rows,views,selected):
    groups=co.defaultdict(list)
    for row,v in zip(rows,views):
        ids={(n['kind'],tuple(n['span'])):n['id'] for n in row['graph']['nodes'] if 'span' in n}
        def remap(es):return {(ids[e[0],e[1]],ids[e[2],e[3]],e[4]) for e in es if (e[0],e[1]) in ids and (e[2],e[3]) in ids}
        groups[row['base_id']].append((row['mode'],remap(gold(row)),remap(edge_set(v['predictions'][selected]))))
    out=co.defaultdict(co.Counter)
    for items in groups.values():
        control=next((x for x in items if x[0] in ('base','control')),None)
        if control is None:continue
        _,g0,p0=control
        for mode,g,p in items:
            if mode in ('base','control'):continue
            shared=g0&g;c=out[mode];c.update(pairs=1,shared_gold=len(shared),retained_correct=len(shared&p0&p),lost_correct=len((shared&p0)-p),rescued_correct=len((shared&p)-p0),gold_added=len(g-g0),gold_removed=len(g0-g))
    return {'scope':'author node ID alignment of shared gold edges only; not inference and not equivalence of every transformation','by_mode':{k:dict(v) for k,v in out.items()}}

def summarize(rows,views,selected):
    need(len(rows)==len(views)>0,'SUMMARY_ROWS');keys=list(views[0]['predictions']);res={k:co.Counter() for k in keys};oracle=co.Counter();agreement=co.defaultdict(co.Counter);confidence=co.defaultdict(co.Counter);cross_surface=co.defaultdict(co.Counter);relation=co.defaultdict(co.Counter);unique=co.defaultdict(co.Counter);fail=[];strata=co.defaultdict(co.Counter);base=co.defaultdict(co.Counter)
    for row,view in zip(rows,views):
        g=gold(row);obj=view['objects'];pred={k:edge_set(v) for k,v in view['predictions'].items()};u=edge_set(view['union']);raw=edge_set(view['raw_top1_positive_union']);p=pred[selected];anchors={(k[2],tuple(k[:2])) for k in obj};reach={e for e in g if (e[0],e[1]) in anchors and (e[2],e[3]) in anchors};individuals=view['individual_readers'];oracle.update({'cases':1,'gold':len(g),'reachable':len(reach),'union_tp':len(g&u),'union_fp':len(u-g),'union_complete':int(g<=u),'union_reachable_complete':int(reach<=u),'whole_strict':int(any(pred[k]==g for k in individuals)),'whole_core':int(any(g<=pred[k] for k in individuals)),'raw_top1_positive_union_tp':len(g&raw),'not_in_inventory':len(reach-u),'selected_missing_from_inventory':len((reach-p)-u),'available_rejected':len((reach-p)&u),'selected_extras':len(p-g)})
        for k,v in pred.items():res[k].update(counts(row,obj,v))
        for e in view['evidence']:
            key=next(iter(edge_set([e['edge']])));truth=key in g;agreement[e['votes']]['true' if truth else 'false']+=1
            if e['votes']==1:unique[e['readers'][0]]['true' if truth else 'false']+=1
            label='true' if truth else 'false'
            if 'mean_probability' in e:confidence[f"votes={e['votes']}|probability_bin={min(9,int(e['mean_probability']*10))}"][label]+=1
            readers=e['readers'];sources={ARMS[k][0] if k in ARMS else 'original' for k in readers};layers={j for k in readers if k in ARMS for j in ARMS[k][1]}
            cross_surface[f'sources={len(sources)}|layer_count={len(layers)}'][label]+=1
        for e in g& p:relation[e[4]]['tp']+=1
        for e in p-g:relation[e[4]]['fp']+=1
        for e in g-p:relation[e[4]]['upstream_fn' if e not in reach else 'absent_union_fn' if e not in u else 'rejected_fn']+=1
        for field in ('family','mode','cohort'):strata[field+'|'+row[field]].update(counts(row,obj,p))
        base[row['base_id']].update(counts(row,obj,p))
        if p!=g:
            wrong_pair={e[:4] for e in p-g}&{e[:4] for e in g-p};fail.append({'case_id':row['case_id'],'base_id':row['base_id'],'prompt':row['prompt'],'missing_endpoints':jsedges(g-reach),'absent_inventory':jsedges((reach-p)-u),'union_absent_including_rescued':jsedges(reach-u),'available_rejected':jsedges((reach-p)&u),'extra':jsedges(p-g),'wrong_label_pairs':len(wrong_pair)})
    # Per-edge compatibility: gold union can be assembled iff there is no annotation
    # multi-label ordered pair. Labels are checked during training; check fresh separately.
    compatible=sum(len({e[:4] for e in gold(r)})==len(gold(r)) and gold(r)<=edge_set(v['union']) for r,v in zip(rows,views))
    oracle['union_compatible_gold_filtered_exact']=compatible
    return {'metrics':{k:metrics(v) for k,v in res.items()},'oracle':dict(oracle),'agreement':{str(k):dict(v)|{'precision':v['true']/max(1,v['true']+v['false'])} for k,v in sorted(agreement.items())},'unique':{k:dict(v) for k,v in unique.items()},'confidence_within_agreement':{k:dict(v) for k,v in confidence.items()},'cross_surface_support':{k:dict(v) for k,v in cross_surface.items()},'g1':f.stats(rows,[v['objects'] for v in views]),'paired_identity_invariance':paired_invariance(rows,views,selected),'by_relation':{k:dict(v) for k,v in relation.items()},'strata':{k:metrics(v) for k,v in strata.items()},'by_base':{k:metrics(v) for k,v in base.items()},'failures':fail}

def score(a,out,ctx):
    sel,seal,public=check_seal(out,ctx[-1],answers=True);d=out/'fresh';sealhash=sha(d/'seal.json');lock=read(d/'prediction-lock.json');need(lock=={'seal':sealhash,'shards':[sha(d/'predictions'/f'{i:06d}.json') for i in range(len(public))],'gold_read':False},'PREDICTION_COMMITMENT');answers=read(d/'answers.SECRET.json');need(len(answers)==len(public),'ANSWER_COUNT')
    if (out/'result.json').exists():
        r=read(out/'result.json');need(r['identity']==ctx[-1] and r['prediction_lock_sha256']==sha(d/'prediction-lock.json') and r['selection_sha256']==sha(out/'selection.json'),'RESULT_DRIFT');emit('RESULT_REUSED',{'report':str(out/'result.json'),'sha256':sha(out/'result.json')});return
    rows=[];bodies=[]
    for i,(row,g) in enumerate(zip(public,answers)):
        need(row['case_id']==g['case_id'] and row['prompt_sha256']==g['prompt_sha256'],'GOLD_BIJECTION');rows.append(dict(row,graph=g['graph']));bodies.append(shard(d,i,row,sealhash)['body'])
    summaries={k:summarize(rows,[b['views'][k] for b in bodies],sel['selected']) for k in G1_NAMES}
    # Explicit diagnostic intervention AFTER gold-free predictions are locked.
    # Oracle endpoints are never part of model or policy selection.
    readers=readers_for(a,out,sel);experts=load_experts(ctx,a.device);maxspan=min(x.max_span_tokens for x in experts.values());oracle_rows=[];oracle_views=[];unalignable=[];od=d/'oracle';od.mkdir(exist_ok=True)
    for i,(r,b) in enumerate(zip(rows,bodies)):
        p=m.unpack(b['prepared']);view,missing=oracle_view(r,p,maxspan)
        if view is None:unalignable.append({'case_id':r['case_id'],'objects':missing});continue
        path=od/f'{i:06d}.json';cache=d/'cache'/f'{i:06d}.safetensors';source=digest({'seal':sealhash,'selection':sha(out/'selection.json'),'cache':sha(cache),'oracle':view})
        if path.exists():record=read(path);need(record['source']==source and digest(record['view'])==record['view_sha256'],'ORACLE_SHARD_DRIFT');v=record['view']
        else:
            v=evaluate_view(a,load_file(str(cache)),p,view,readers,experts,sel);immutable(path,{'source':source,'view':v,'view_sha256':digest(v)})
        oracle_rows.append(r);oracle_views.append(v)
        if len(oracle_rows)%8==0:emit('ORACLE_PROGRESS',{'done':len(oracle_rows),'of':len(rows)})
    oracle=summarize(oracle_rows,oracle_views,sel['selected']) if oracle_rows else None
    bal=summaries['0114_full'];q=bal['oracle'];other=summaries['0115_selected'];selected=bal['metrics'][sel['selected']];alt=other['metrics'][sel['selected']]
    # Decisions based on causal error counts, not fabricated certification gates.
    revisit=(selected['upstream_fn']>selected['reachable_fn'] or (alt['f1']-selected['f1']>=.02 and alt['upstream_fn']<selected['upstream_fn']) or (oracle is not None and len(oracle_rows)==len(rows) and oracle['metrics'][sel['selected']]['f1']-max(selected['f1'],alt['f1'])>=.02))
    primary='G1_ENDPOINT_OR_RELEVANCE_REVIEW' if revisit else 'G2_READER_OBSERVABILITY' if q['selected_missing_from_inventory']>q['available_rejected'] else 'G2_RECONSTRUCTION_ADMISSION' if selected['reachable_fn'] or selected['fp'] else 'DOWNSTREAM_RESEARCH_CANDIDATE'
    result={'schema':SCHEMA,'identity':ctx[-1],'selection_sha256':sha(out/'selection.json'),'prediction_lock_sha256':sha(d/'prediction-lock.json'),'cases':len(rows),'independent_bases':seal['bases'],'selected':sel['selected'],'operational':summaries,'gold_endpoint_diagnostic':oracle,'gold_endpoint_unalignable':unalignable,'research_decision':{'primary':primary,'revisit_G1':revisit,'rule':'upstream misses exceed reachable misses, or recall route improves F1 by >=2pp while reducing upstream misses, or same-cohort perfect endpoints improve F1 over both by >=2pp; otherwise compare absent vs rejected edges','conditional_not_certification':True},'scope':'G2 binding labels only; G3 relational/latent reasoning not unlocked. Synthetic two-generator diagnostic. Exact historical raw-reranker 0103 direct comparison unavailable. Seven constituent frozen experts evaluated with validation-calibrated decoders; not the 0103 reranker.','production_promotion':False,'g3_unlocked':False};immutable(out/'result.json',result)
    lines=['# G2-0116 measured research record','','Research only. G1 conditionally accepted; no production promotion or G3 unlock.','',f"Cases: {len(rows)} from {seal['bases']} independent bases. Selected by development: `{sel['selected']}`.",'','| G1 endpoints | Selected G2 F1 | Exact binding graphs | Strict surface graphs | Upstream FN | Reachable FN |','|---|---:|---:|---:|---:|---:|']
    for name,v in [*summaries.items(),*([('gold_endpoint_oracle',oracle)] if oracle else [])]:
        z=v['metrics'][sel['selected']];lines.append(f"| {name} | {z['f1']:.6f} | {z['exact']}/{z['cases']} | {z['strict_surface_graph']}/{z['cases']} | {z['upstream_fn']} | {z['reachable_fn']} |")
    lines+=['','## Forensic decision','',f'Primary: `{primary}`. Revisit G1: {revisit}.',f"Balanced route: union correct {q['union_tp']}/{q['gold']}; absent among reachable {q['not_in_inventory']}; available but rejected {q['available_rejected']}; selected extra {q['selected_extras']}.",'','See result.json for per-reader, per-type, agreement, per-base and case-level failures. Development.json contains actual validation scores and the greedy reduction frontier. Gold endpoints are a diagnostic only; unalignable cases are explicitly excluded from that comparison. The exact 0103 raw reranker is not replaced with a different model.','',f"Result SHA-256: `{sha(out/'result.json')}`."]
    (out/'RESEARCH_RECORD.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');emit('RESULT',{'selected':sel['selected'],'cases':len(rows),'bases':seal['bases'],'metrics':{k:v['metrics'][sel['selected']] for k,v in summaries.items()},'oracle':q,'decision':result['research_decision'],'report':str(out/'result.json'),'sha256':sha(out/'result.json')})

def selftest():
    torch.manual_seed(SEED);torch.set_num_threads(1)
    p={'prompt':'Alpha 95 Beta 70','prompt_start':0,'offsets':[(0,5),(6,8),(9,13),(14,16)],'eligible':torch.ones(4,dtype=torch.bool)}
    obj=[(0,5,'action'),(6,8,'quantity'),(9,13,'action'),(14,16,'quantity')];row={'prompt':p['prompt'],'case_id':'fixture','base_id':'b','family':'allocation','mode':'control','cohort':'fixture','graph':{'nodes':[{'id':str(i),'kind':k[2],'span':list(k[:2])} for i,k in enumerate(obj)],'edges':[{'src':'1','dst':'0','type':'applies_to'},{'src':'3','dst':'2','type':'applies_to'}]}}
    v,missing=oracle_view(row,p,4);need(v is not None and not missing and as_objects(v['spans'],p)==obj,'SELFTEST_ORACLE_MAPPING')
    states={f'{src}_{k}':torch.randn(4,16) for src in ('original','S') for k in m.LAYERS if src!='S' or k!=0};x=features(states,v,p);y=labels(obj,gold(row));need(int((y>0).sum())==2,'SELFTEST_GOLD_BINDINGS')
    for arm in ARMS:
        net=Reader(16,arm,width=32);l=loss(net,x,y);l.backward();need(bool(torch.isfinite(l)) and net.project.weight.grad is not None and bool(torch.isfinite(net.project.weight.grad).all()),'SELFTEST_READER_'+arm)
    # Actual repository G2 architecture exercised, not a fake binding scorer.
    h=GH.GamerGraphHead(hidden_size=16,width=16,layers=1,max_span_tokens=4).eval().requires_grad_(False);ep=expert_scores({'actual':h},states,v,p,'cpu');need(ep['actual'].shape==(4,4,R),'SELFTEST_ACTUAL_G2_HEAD')
    probs=torch.zeros(4,4,R);probs[...,0]=.98
    for i,j in ((1,0),(3,2)):probs[i,j,0]=.01;probs[i,j,CLASSES.index('applies_to')]=.99
    pred=choose_edges(obj,probs,.5);need(pred==gold(row),'SELFTEST_DECODE');cap=caps_from([row]);need(choose_edges(obj,probs,.5,caps=cap)==pred,'SELFTEST_CAPS')
    spec={'rule':'vote','arms':['one','two'],'threshold':.5,'caps':False};need(policy_predict(obj,{'one':probs,'two':probs},{'one':pred,'two':pred},spec,cap)==pred,'SELFTEST_INTEGRATION')
    view={'objects':f.sorted_objects(obj),'predictions':{'one':jsedges(pred),'two':jsedges(pred),'selected':jsedges(pred)},'union':jsedges(pred),'raw_top1_positive_union':jsedges(pred),'individual_readers':['one','two'],'evidence':[{'edge':jsedges([e])[0],'votes':2,'readers':['one','two']} for e in pred]};summary=summarize([row],[view],'selected');need(summary['oracle']['union_compatible_gold_filtered_exact']==1 and summary['metrics']['selected']['strict_surface_graph']==1,'SELFTEST_ORACLE_METRICS')
    # A missing endpoint is distinct from an admitted-but-rejected relationship.
    obj2=obj[:-1];z=counts(row,obj2,{edge(obj[1],obj[0],'applies_to')});need(z['upstream_fn']==1 and z['reachable_fn']==0,'SELFTEST_CAUSAL_ACCOUNTING')
    # Check real training/selection/checkpoint resume, not only a gradient smoke.
    with tempfile.TemporaryDirectory() as td:
        args=SimpleNamespace(device='cpu',epochs=1,lr=.001);data=[(row,x,y)];root=Path(td);identity={'fixture':True};n,r=fit_reader(args,root,'original_L14_21',data,data,16,identity,cap);n2,r2=fit_reader(args,root,'original_L14_21',data,data,16,identity,cap);need(r==r2 and torch.equal(n(x),n2(x)),'SELFTEST_RESUME')
    emit('SELFTEST',{'checkpoint_fit_and_resume':True,'actual_G2_head_cpu':True,'reader_gradients':len(ARMS),'exact_gold_mapping':True,'integration':True,'pair_exclusivity':True,'upstream_vs_reachable':True,'oracle_accounting':True,'actual_runtime_checkpoint':False,'actual_qwen_cuda':False})

def raw_reranker_source_receipt():
    # This is discovery of the installed source interface, not execution or an
    # unverified guessed call. It does not hold up the primary new reader lab.
    path=Path(__file__).with_name('nanojev_gamer_v1_g2_raw_graph_reranking_0103.py')
    if not path.is_file():return {'available':False}
    tree=ast.parse(path.read_text(encoding='utf-8-sig'));wanted={'_source','load_candidate','_raw_features','_infer','_full_fit_bundle','_candidate_scores'}
    return {'available':True,'sha256':sha(path),'signatures':{n.name:ast.unparse(n.args) for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in wanted},'scope':'source discovery only; direct raw reranker adapter not yet verified'}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('audit','selftest','develop','prepare','predict','score','run'));parser.add_argument('--root',default=m.ROOT);parser.add_argument('--output');parser.add_argument('--device',default='cuda');parser.add_argument('--bases',type=int,default=12);parser.add_argument('--epochs',type=int,default=3);parser.add_argument('--lr',type=float,default=.001);a=parser.parse_args(argv)
    need(a.bases>=4 and a.bases%4==0 and 1<=a.epochs<=20 and 0<a.lr<.1,'CONFIG');torch.set_num_threads(min(torch.get_num_threads(),4))
    if a.command=='selftest':selftest();return
    root=Path(a.root).resolve();out=Path(a.output).resolve() if a.output else root/'g2-multilayer-reconstruction-0116';need(out.parent==root and out.name.startswith('g2-multilayer-reconstruction-0116'),'OUTPUT_MUST_BE_0116_RESEARCH_FOLDER')
    if a.command=='run':
        for phase in ('develop','prepare','predict','score'):
            subprocess.run([sys.executable,'-u','-m',MODULE,phase,'--root',str(root),'--output',str(out),'--device',a.device,'--bases',str(a.bases),'--epochs',str(a.epochs),'--lr',str(a.lr)],check=True)
        return
    ctx=parent(a);emit('AUDIT',{'ready':True,'phase':a.command,'fresh_bases':a.bases,'fresh_cases':4*a.bases,'new_readers':len(ARMS),'frozen_experts':list(EXPERTS),'g1_conditionally_accepted':True,'0103_direct_comparator':'NOT_WIRED_UNVERIFIED_SOURCE_INTERFACE','0103_installed_source':raw_reranker_source_receipt(),'production_promotion':False,'g3_unlocked':False})
    if a.command=='audit':return
    out.mkdir(parents=True,exist_ok=True);{'develop':develop,'prepare':prepare,'predict':predict,'score':score}[a.command](a,out,ctx)
if __name__=='__main__':main()
