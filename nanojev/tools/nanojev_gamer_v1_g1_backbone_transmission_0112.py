"""G1-0112: frozen backbone transmission laboratory. Research only.
Requires the installed 0110 and 0111 modules and completed 0111 report/checkpoint.
No canonical weights, adapters, checkpoints or stage gates are modified.
Commands: audit, selftest, probe (frozen measurements), run (factorial learning).
"""
from __future__ import annotations
import argparse, collections, hashlib, json, math, random
from pathlib import Path
from types import SimpleNamespace
import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_gamer_v1_g1_s_first_reference_0111 as prior

B=prior.backbone; C=prior.curriculum; GH=prior.gh
TAG='G1_0112_TRANSMISSION'; SCHEMA='nanojev.gamer.g1.backbone-transmission-0112.v1'
ROOT=prior.ROOT
SEEDS={'probe':{'train':1121026,'test':1122026},'run':{'train':1123026,'test':1124026}}
MODES=prior.TRAIN_MODES; TEST_MODES=prior.TEST_MODES
ARMS=('A_frozen_S','B_writer','C_reader','D_joint')
KINDS=('background',*GH.SPAN_KINDS)

def need(ok,why):
    if not ok: raise ValueError(TAG+'_'+why)

def emit(event,data):
    print(TAG+'_'+event+' '+json.dumps(data,sort_keys=True,allow_nan=False),flush=True)

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return B.sha256(Path(p))
def write(p,o):prior.write_new(p,o)
def number(x):return float(x.detach().float().cpu())
def rms(x):return number(x.float().square().mean().sqrt())
def table_rms(x):
    total=sum(float(v.float().square().sum().cpu()) for v in x.split(4096,dim=0))
    return math.sqrt(total/x.numel())

def cohorts(root,train_bases,test_bases,purpose='probe'):
    need(train_bases>=4 and test_bases>=4 and train_bases%2==test_bases%2==0,'BASE_COUNTS_MUST_BE_EVEN_AT_LEAST_FOUR')
    lab=prior.fuzz(); groups={}
    for split,n,modes in [('train',train_bases,MODES),('test',test_bases,TEST_MODES)]:
        rows=[]
        for i in range(n):
            base=lab._base(i,seed=SEEDS[purpose][split]);base['split']=split
            for mode in modes:
                row=lab._variant(base,mode)
                need(row['prompt_sha256']==prior.digest_text(row['prompt']),'PROMPT_HASH')
                rows.append(row)
        groups[split]=rows
    allrows=groups['train']+groups['test']; keys=[r['prompt_sha256'] for r in allrows]
    need(len(set(keys))==len(keys),'DUPLICATE_PROMPTS')
    need({r['base_id'] for r in groups['train']}.isdisjoint({r['base_id'] for r in groups['test']}),'BASE_LEAKAGE')
    oldseal=prior.check_prior_seal(root,groups)
    return groups,oldseal

def lineage(root,groups):
    paths,record,sig,historical=prior.source(root)
    d=Path(root)/'g1-s-first-reference-0111'; report=d/'result.json'; ck=d/'research-reference.safetensors'; seal=d/'cohort-seal.json'
    need(all(p.is_file() for p in (report,ck,seal)),'COMPLETED_0111_REQUIRED')
    r=read(report); s=read(seal)
    need(r['schema']==prior.SCHEMA and r['frozen_g1_sha256']==sig and s['frozen_g1']==sig,'0111_FROZEN_LINEAGE')
    need(sha(ck)==r['reference_sha256'] and sha(seal)==r['cohort_seal_sha256'],'0111_CHECKPOINT_OR_SEAL_DRIFT')
    old={x['prompt_sha256'] for rows in s['groups'].values() for x in rows}
    need(not any(x['prompt_sha256'] in old for rows in groups.values() for x in rows),'0111_PROMPT_REUSE')
    return paths,record,sig,ck,{'report_sha256':sha(report),'checkpoint_sha256':sha(ck),'seal_sha256':sha(seal),'historical':historical}

def load(root,groups,device):
    paths,rec,sig,ck,lin=lineage(root,groups)
    model,tok=B.model_components(rec['model_id'],paths['adapter'],device)
    composite=prior.native.load_composite(paths['composite'],device=device)
    model.eval().requires_grad_(False);composite.head.eval().requires_grad_(False)
    from safetensors.torch import load_file
    weights=load_file(str(ck),device='cpu');dim=model.get_input_embeddings().weight.shape[-1]
    ctrl=prior.Reference(dim,float(weights['embedding_rms']),bank_size=weights['bank'].shape[0],rank=weights['router_down.weight'].shape[0]).to(device)
    ctrl.load_state_dict(weights,strict=True);ctrl.eval().requires_grad_(False)
    s=ctrl.static().detach().float();need(s.norm()>0,'ZERO_LEARNED_S')
    need(prior.frozen_ok(model,composite.head),'FROZEN_VIOLATION')
    return model,tok,composite,ctrl,s,sig,lin

def context(model,p,ref,device,layers=False):
    if not layers:
        if ref is None:return B.frozen_context(model,p,device).float()
        return prior.with_reference(model,p,ref,device)
    # Layerwise survey of the exact deployed inputs_embeds path. The returned
    # input is the actual embedding tensor (not the first residual block).
    ids=p['ids'].to(device); mask=p['attention'].to(device)
    with torch.no_grad():
        emb=model.get_input_embeddings()(ids)
        prefix=int(ref is not None)
        if prefix:
            inp=torch.cat((ref.to(emb).reshape(1,1,-1),emb),1)
            att=torch.cat((torch.ones_like(mask[:,:1]),mask),1)
        else:inp,att=emb,mask
        maxpos=getattr(model.config,'max_position_embeddings',None)
        need(maxpos is None or inp.shape[1]<=maxpos,'POSITION_LIMIT')
        out=model(inputs_embeds=inp,attention_mask=att,output_hidden_states=True,use_cache=False)
        hs=[h[0,prefix:].float().cpu() for h in out.hidden_states]
        return emb[0].float().cpu(),hs

def prepare(tok,rows):
    return [(r,B.prepare_prompt(tok,r['prompt'])) for r in rows]

def labels_for(p,r):return C.aligned_labels(p,r)

def token_labels(p,r):
    # Gold is used strictly for training/evaluation of a diagnostic readout.
    lab=labels_for(p,r); y=torch.zeros(p['eligible'].numel(),dtype=torch.long)
    for a in lab['spans']:y[a['token_first']:a['token_last']+1]=KINDS.index(a['kind'])
    return y[p['eligible']]

def profile(x):
    x=x.float().cpu(); centered=x-x.mean(0); sv=torch.linalg.svdvals(centered); e=sv.square(); total=e.sum(); w=e/total.clamp_min(1e-20)
    return {'rows':len(x),'dimension':x.shape[1],'rms':rms(x),'mean_coordinate_rms':rms(x.mean(0)),
            'coordinate_std_mean':number(x.std(0,unbiased=False).mean()),'token_norm_mean':number(x.norm(dim=1).mean()),
            'effective_rank_participation':number(1/w.square().sum().clamp_min(1e-20)) if number(total)>0 else 0.,
            'top_1_variance_share':number(w[0]),'top_8_variance_share':number(w[:8].sum()),
            'top_32_variance_share':number(w[:32].sum()),'singular_values_top32':sv[:32].tolist()}

def ridge_fit(x,y,alpha=1.0):
    # Train-only normalization. No target-dependent data enter backbone input.
    x=x.float().cpu(); y=y.float().cpu(); mu=x.mean(0); sd=x.std(0,unbiased=False).clamp_min(1e-4)
    z=(x-mu)/sd; ym=y.mean(0); q=y-ym
    if len(z)<z.shape[1]:
        w=z.T@torch.linalg.solve(z@z.T+alpha*torch.eye(len(z)),q)
    else:w=torch.linalg.solve(z.T@z+alpha*torch.eye(z.shape[1]),z.T@q)
    return mu,sd,w,ym

def ridge_apply(fit,x):
    mu,sd,w,ym=fit;return ((x.float().cpu()-mu)/sd)@w+ym

def classify_probe(x,y,xt,yt,seed=112):
    # Balanced train subsample per kind; independent test rows are never fit.
    rng=torch.Generator().manual_seed(seed); idx=[]; supported=[]
    for k in range(len(KINDS)):
        ids=(y==k).nonzero().flatten()
        if len(ids):
            supported.append(k);idx.extend(ids[torch.randperm(len(ids),generator=rng)[:384]].tolist())
    need(len(supported)>1,'INSUFFICIENT_OBSERVED_KINDS')
    x=x[idx];y=y[idx]; target=F.one_hot(y,len(KINDS)).float()
    fit=ridge_fit(x,target,alpha=10.)
    shuffle=target[torch.randperm(len(target),generator=rng)]
    sham=ridge_fit(x,shuffle,alpha=10.)
    def score(f):
        scores=ridge_apply(f,xt);scores[:,[k for k in range(len(KINDS)) if k not in supported]]=-float('inf')
        pred=scores.argmax(1); by={}
        for k,name in enumerate(KINDS):
            mask=yt==k;by[name]={'n':int(mask.sum()),'accuracy':number((pred[mask]==yt[mask]).float().mean()) if mask.any() else None}
        return {'accuracy':number((pred==yt).float().mean()),'balanced_accuracy':sum(v['accuracy'] for v in by.values() if v['accuracy'] is not None)/sum(v['accuracy'] is not None for v in by.values()),'by_kind':by}
    return {'trained':score(fit),'shuffled_train_labels':score(sham),'train_tokens':len(y),'test_tokens':len(yt),'supported_train_kinds':[KINDS[k] for k in supported],'unsupported_test_tokens':int(sum((yt==k).sum() for k in range(len(KINDS)) if k not in supported))}

def distribution_survey(model,prepared,s,device):
    # Use controls and pronouns from every selected base, not a cherry-picked
    # subset. Same tokens across no-reference and S. Report selected layers.
    buf=collections.defaultdict(list); labs=collections.defaultdict(list); coverage={}
    for split,rows in prepared.items():
        used=0;bad=[]
        for r,p in rows:
            if r['mode'] not in ('control','pronoun'):continue
            try:y=token_labels(p,r)
            except ValueError as e:
                if str(e)!='GAMER_GRAPH_GOLD_TOKEN_ALIGNMENT_FAILURE':raise
                bad.append(r['case_id']);continue
            mask=p['eligible'];labs[split].append(y);used+=1
            for name,ref in [('original',None),('S',s)]:
                emb,hs=context(model,p,ref,device,layers=True)
                stages={'embedding_input':emb}
                for k in sorted({0,len(hs)//4,len(hs)//2,3*len(hs)//4,len(hs)-1}):stages['hidden_'+str(k)]=hs[k]
                for layer,x in stages.items():buf[(split,name,layer)].append(x[mask])
            if used%8==0:emit('DISTRIBUTION_PROGRESS',{'split':split,'cases':used})
        coverage[split]={'cases':used,'unalignable':bad}
    ys={k:torch.cat(v) for k,v in labs.items()}; out={}
    for _,arm,layer in sorted(k for k in buf if k[0]=='train'):
        x=torch.cat(buf['train',arm,layer]);xt=torch.cat(buf['test',arm,layer]); y=ys['train'];yt=ys['test']
        # Same deterministic test sample for each arm and layer.
        ids=torch.randperm(len(xt),generator=torch.Generator().manual_seed(42))[:256]
        stats=profile(xt[ids]); stats['by_kind']={name:{'tokens':int((yt==k).sum()),'rms':rms(xt[yt==k]),'mean_coordinate_rms':rms(xt[yt==k].mean(0))} for k,name in enumerate(KINDS) if (yt==k).any()}
        obs=classify_probe(x,y,xt,yt);out[arm+'/'+layer]={'distribution':stats,'observability':obs}
        emit('OBSERVABILITY',{'arm':arm,'layer':layer,'balanced_accuracy':obs['trained']['balanced_accuracy'],'shuffled':obs['shuffled_train_labels']['balanced_accuracy'],'effective_rank':stats['effective_rank_participation']})
    return {'coverage':coverage,'layers':out,'scope':'token-level gold kind vs background; linearly decodable association, not proof of causal semantic use'}

def orthogonal(s,k,seed):
    gen=torch.Generator().manual_seed(seed);v=torch.randn(s.numel(),k,generator=gen, dtype=torch.float32)
    u=F.normalize(s.detach().cpu(),dim=0);v=v-u[:,None]*(u@v)[None,:]
    q=torch.linalg.qr(v,mode='reduced').Q.T.contiguous();need(k<s.numel(),'TOO_MANY_DIRECTIONS')
    return q.to(s.device)

def payload_probe(model,prepared,s,device,bits=4):
    # Independent random messages, assigned AFTER prompts are generated. Same
    # prompt receives complementary messages. Reader only sees backbone output
    # at ORIGINAL eligible positions, not the reference state or the message.
    q=orthogonal(s,bits,11205); strength=s.norm()*.5/math.sqrt(bits)
    features=collections.defaultdict(list);targets=collections.defaultdict(list)
    for split,rows in prepared.items():
        rng=torch.Generator().manual_seed(11206 if split=='train' else 11207)
        for r,p in rows:
            if r['mode']!='control':continue
            with torch.no_grad():
                h0=context(model,p,s,device)[p['eligible'].to(device)].mean(0).cpu()
                for _ in range(2):
                    z=torch.randint(0,2,(bits,),generator=rng).float()*2-1
                    for z1 in (z,-z):
                        ref=s+strength*(z1.to(s)@q)
                        h=context(model,p,ref,device)[p['eligible'].to(device)].mean(0).cpu()
                        features[split,'encoded'].append(h);features[split,'no_payload'].append(h0);targets[split].append(z1)
    y,yt=map(torch.stack,(targets['train'],targets['test'])); out={}
    for arm in ('encoded','no_payload'):
        x,xt=map(torch.stack,(features['train',arm],features['test',arm]));fit=ridge_fit(x,y,10.)
        score=ridge_apply(fit,xt); correct=(score>=0)==(yt>=0)
        out[arm]={'bit_accuracy':number(correct.float().mean()),'whole_message_accuracy':number(correct.all(1).float().mean()),'by_bit':correct.float().mean(0).tolist(),'train_messages':len(y),'test_messages':len(yt)}
    # Exact chance controls: identical no-payload state sees complementary labels.
    emit('PAYLOAD',out);return {'bits':bits,'relative_payload_norm':.5,'arms':out,'chance_bit':.5,'chance_whole_message':2**(-bits),'scope':'controlled numerical channel, not semantic generalization'}

def raw(model,head,p,ref,device):return head(context(model,p,ref,device),eligible=p['eligible'].to(device),active_stage='G1')

def target_specs(rawout,p,row):
    # Select output coordinates at a fixed operating point. Gold only defines
    # the diagnostic oracle; not used by any deployed or evaluated writer.
    logits=rawout['span_logits'].detach().float();valid=rawout['span_valid'];lab=labels_for(p,row)
    specs=[];positive=set()
    for a in lab['spans']:
        i=a['token_first'];j=a['token_last']-i;k=KINDS.index(a['kind'])
        need(j<logits.shape[1] and bool(valid[i,j]),'INVALID_GOLD_SPAN')
        positive.add((i,j)); z=logits[i,j].clone();z[k]=-float('inf'); rival=int(z.argmax());specs.append((i,j,k,rival))
    margins=logits[...,1:].amax(-1)-logits[...,0]; mask=valid.clone()
    for i,j in positive:mask[i,j]=False
    ids=mask.nonzero();scores=margins[mask]
    for n in scores.topk(min(8,len(scores))).indices.tolist():
        i,j=ids[n].tolist();k=int(logits[i,j,1:].argmax())+1;specs.append((i,j,0,k))
    return specs

def margins(out,specs):
    l=out['span_logits'].float();return torch.stack([l[i,j,k]-l[i,j,r] for i,j,k,r in specs])

def fit_delta(J,y,ridge=.05,cap=2.):
    # Fit only deficient margins; protect already-correct margins by assigning
    # desired change zero. Coordinates and rival labels frozen before fit.
    target=(1.-y).clamp_min(0);scale=J.square().sum(0).mean().clamp_min(1e-8)
    a=torch.linalg.solve(J.T@J+ridge*scale*torch.eye(J.shape[1],device=J.device),J.T@target)
    a=a*min(1.,cap/max(number(a.norm()),1e-12));return a,target

def balanced_selection(rows,mode,bases):
    by={f:[(r,p) for r,p in rows if r['family']==f and r['mode']==mode] for f in ('allocation','encounter')}
    out=[]
    for i in range(max(map(len,by.values()),default=0)):
        for f in by:
            if i<len(by[f]):out.append(by[f][i])
    return out[:bases]

def control_survey(model,comp,prepared,s,device,directions=8,bases=4):
    q=orthogonal(s,directions,11208); eps=.25*s.norm(); basis=q*eps
    rows=balanced_selection(prepared['test'],'pronoun',bases)
    results=[]
    for r,p in rows:
        with torch.no_grad():
            o=raw(model,comp.head,p,s,device)
            try:spec=target_specs(o,p,r)
            except ValueError as e:
                if str(e) not in ('GAMER_GRAPH_GOLD_TOKEN_ALIGNMENT_FAILURE',TAG+'_INVALID_GOLD_SPAN'):raise
                results.append({'case_id':r['case_id'],'skipped':str(e)});continue
            y=margins(o,spec);cols=[]
            for v in basis:
                cols.append((margins(raw(model,comp.head,p,s+v,device),spec)-margins(raw(model,comp.head,p,s-v,device),spec))/2)
            J=torch.stack(cols,1);need(bool(torch.isfinite(J).all()),'NONFINITE_CONTROL')
            a,t=fit_delta(J,y);delta=a@basis;pred=y+J@a
            # Norm-matched random direction in the SAME subspace, fixed seed.
            rr=torch.randn(directions,generator=torch.Generator().manual_seed(11209)).to(s);rnd=(F.normalize(rr,dim=0)*a.norm())@basis
            actual=margins(raw(model,comp.head,p,s+delta,device),spec); sham=margins(raw(model,comp.head,p,s+rnd,device),spec)
            def loss(v):return number((1-v).clamp_min(0).square().mean())
            sv=torch.linalg.svdvals(J); total=sv.square().sum();rank=number(total.square()/sv.pow(4).sum().clamp_min(1e-20)) if number(total)>0 else 0.
            def decode_ref(ref):return prior.count(r,prior.decoded(comp,context(model,p,ref,device),p,r['family']))
            item={'case_id':r['case_id'],'directions':directions,'margin_count':len(spec),'singular_values':sv.cpu().tolist(),'effective_rank':rank,
                'baseline_deficit':loss(y),'predicted_deficit':loss(pred),'actual_deficit':loss(actual),'random_deficit':loss(sham),
                'local_target_residual_ratio':number((t-J@a).norm()/t.norm().clamp_min(1e-8)) if number(t.norm())>0 else None,
                'linear_prediction_rmse':rms(pred-actual),'relative_correction_norm':number(delta.norm()/s.norm()),
                'margin_sign_rescued':int(((y<=0)&(actual>0)).sum()),'margin_sign_harmed':int(((y>0)&(actual<=0)).sum()),
                'decoded':{'S':decode_ref(s),'oracle_delta':decode_ref(s+delta),'random_delta':decode_ref(s+rnd)}}
            results.append(item);emit('LOCAL_CONTROL',item)
    return {'method':'central finite differences in a restricted reference subspace, not full Jacobian; oracle uses gold only for diagnostic target','cases':results}

def amplitude_survey(model,comp,prepared,s,device,bases=4):
    q=orthogonal(s,2,11210);a=q[0]*s.norm();b=q[1]*s.norm()
    refs={'original':None,'zero':torch.zeros_like(s),'S':s,'minus_S':-s,'orthogonal_1':a,'orthogonal_2':b}
    refs.update({f'S_times_{2.**k:g}':s*(2.**k) for k in (-3,-2,-1,1,2,3)})
    # Equal-norm rotations isolate direction better than simple summation.
    refs['rotation_45']=(s+a)/math.sqrt(2);refs['rotation_minus_45']=(s-a)/math.sqrt(2)
    rows=[];chosen={r['base_id'] for r,p in balanced_selection(prepared['test'],'control',bases)}
    for r,p in prepared['test']:
        if r['base_id'] not in chosen or r['mode'] not in ('control','pronoun'):continue
        arms={}
        with torch.no_grad():
            for name,ref in refs.items():
                h=context(model,p,ref,device);arms[name]=prior.count(r,prior.decoded(comp,h,p,r['family']))
        rows.append({'case_id':r['case_id'],'base_id':r['base_id'],'mode':r['mode'],'arms':arms})
    summary={k:prior.summarize([r['arms'][k] for r in rows]) for k in refs};emit('AMPLITUDE',summary)
    return {'summary':summary,'cases':rows,'scope':'exploratory small cohort; no best amplitude selection for held-out factorial'}

class Reader(nn.Module):
    """Zero-initialized residual reader; only sees the backbone hidden sequence."""
    def __init__(self,d,rank=16):
        super().__init__();self.down=nn.Linear(d,min(rank,d),bias=False);self.up=nn.Linear(min(rank,d),d,bias=False);nn.init.zeros_(self.up.weight)
    def forward(self,h):
        x=h.float();return x+0.1*self.up(torch.tanh(self.down(F.layer_norm(x,(x.shape[-1],)))))

class Interface(nn.Module):
    def __init__(self,s,writer,reader):
        super().__init__();self.register_buffer('anchor',s.detach().clone().float());self.delta=nn.Parameter(torch.zeros_like(self.anchor),requires_grad=writer)
        self.reader=Reader(s.numel()).to(s.device);self.reader.requires_grad_(reader);self.use_reader=reader;self.use_writer=writer
    def ref(self):return self.anchor+self.delta*self.anchor.norm().detach()/math.sqrt(self.anchor.numel())
    def output(self,h):return self.reader(h) if self.use_reader else h.float()

def loss_one(model,head,p,r,interface,device,cached=None):
    h=context(model,p,interface.ref(),device) if interface.use_writer else cached
    need(h is not None,'MISSING_FROZEN_CACHE');h=interface.output(h)
    out=head(h,eligible=p['eligible'].to(device),active_stage='G1')
    return C.stage_loss(head,out,labels_for(p,r),r,'G1')

def train_arm(model,head,rows,s,device,arm,steps,micro,lr,cache):
    w=arm in ('B_writer','D_joint'); rd=arm in ('C_reader','D_joint')
    torch.manual_seed(11211); it=Interface(s,w,rd);params=[p for p in it.parameters() if p.requires_grad]; opt=torch.optim.AdamW(params,lr=lr,weight_decay=0.)
    def evaluate_loss(rows):
        with torch.no_grad():return sum(number(loss_one(model,head,p,r,it,device,cache[r['case_id']].to(device) if not w else None)) for r,p in rows)/len(rows)
    # Fixed, identical examples/order across all three trainable arms.
    small=[]; seen=set()
    for r,p in rows:
        if r['base_id'] not in seen:
            small.append((r,p));seen.add(r['base_id'])
        if len(small)==4:break
    hist=[]; before=evaluate_loss(small)
    rng=random.Random(11212)
    for phase,seq,n in [('micro',small,micro),('fit',rows,steps)]:
        order=list(range(len(seq)))
        for i in range(n):
            if i%len(seq)==0:rng.shuffle(order)
            r,p=seq[order[i%len(seq)]];opt.zero_grad(set_to_none=True)
            loss=loss_one(model,head,p,r,it,device,cache[r['case_id']].to(device) if not w else None)
            need(bool(torch.isfinite(loss)),'NONFINITE_LOSS_'+arm);loss.backward();g=[x.grad for x in params if x.grad is not None]
            need(g and all(bool(torch.isfinite(v).all()) for v in g),'BAD_GRADIENT_'+arm)
            norm=torch.nn.utils.clip_grad_norm_(params,1.);need(bool(torch.isfinite(norm)),'GRADIENT_NORM');opt.step()
            need(prior.frozen_ok(model,head),'FROZEN_GRADIENT_VIOLATION')
            if i==0 or (i+1)%12==0 or i+1==n:
                rec={'arm':arm,'phase':phase,'step':i+1,'loss':number(loss),'grad_norm':number(norm)};hist.append(rec);emit('LEARN',rec)
        if phase=='micro':after=evaluate_loss(small);emit('MICROSET',{'arm':arm,'before':before,'after':after})
    for p in params:p.grad=None
    it.eval().requires_grad_(False)
    return it,{'micro_loss_before':before,'micro_loss_after':after,'micro_reduction':(before-after)/max(before,1e-12),'history':hist,'trainable_parameters':sum(p.numel() for p in params),'relative_writer_delta':number((it.ref()-s).norm()/s.norm())}

def edge_availability(row,objects):
    found=set(map(tuple,objects));gold={n['id']:tuple(n['span'])+(n['kind'],) for n in row['graph']['nodes'] if 'span' in n}
    # Handles either edge convention; refuse an unrecognized schema.
    total=avail=0
    for e in row['graph']['edges']:
        if 'src' in e and 'dst' in e:a,b=e['src'],e['dst']
        elif 'source' in e and 'target' in e:a,b=e['source'],e['target']
        else:raise ValueError(TAG+'_UNKNOWN_EDGE_SCHEMA')
        if a in gold and b in gold:total+=1;avail+=gold[a] in found and gold[b] in found
    return {'surface_edges':total,'reachable_surface_edges':avail}

def factorial(model,comp,prepared,s,device,dest,steps,micro,lr):
    rows=[];bad=[];cache={}
    for r,p in prepared['train']:
        try:labels_for(p,r)
        except ValueError as e:
            if str(e)!='GAMER_GRAPH_GOLD_TOKEN_ALIGNMENT_FAILURE':raise
            bad.append(r['case_id']);continue
        rows.append((r,p))
        with torch.no_grad():cache[r['case_id']]=context(model,p,s,device).detach().cpu()
    need(len(rows)>=4 and len(rows)>=.8*len(prepared['train']),'TOO_MANY_UNALIGNABLE_TRAIN_ROWS')
    from safetensors.torch import save_file
    fitted={};history={}
    for arm in ARMS[1:]:
        it,rep=train_arm(model,comp.head,rows,s,device,arm,steps,micro,lr,cache);fitted[arm]=it;history[arm]=rep
        path=dest/(arm+'.safetensors');save_file({k:v.detach().cpu().contiguous() for k,v in it.state_dict().items()},str(path));rep['checkpoint_sha256']=sha(path)
        write(dest/(arm+'-training.json'),rep)
    cases=[];names=('original',*ARMS)
    with (dest/'evaluation.jsonl').open('x',encoding='utf-8') as journal:
        with torch.no_grad():
            for i,(r,p) in enumerate(prepared['test'],1):
                h=context(model,p,s,device)
                outputs={'original':context(model,p,None,device),'A_frozen_S':h,'C_reader':fitted['C_reader'].output(h)}
                for arm in ('B_writer','D_joint'):
                    it=fitted[arm];outputs[arm]=it.output(context(model,p,it.ref(),device))
                item={'case_id':r['case_id'],'base_id':r['base_id'],'family':r['family'],'mode':r['mode'],'arms':{}}
                for name,h1 in outputs.items():
                    objects=prior.decoded(comp,h1,p,r['family']);item['arms'][name]={'counts':prior.count(r,objects),'edge_reachability':edge_availability(r,objects),'predicted':objects}
                cases.append(item);journal.write(json.dumps(item,sort_keys=True)+'\n');journal.flush()
                if i==1 or i%8==0 or i==len(prepared['test']):emit('EVAL',{'done':i,'of':len(prepared['test'])})
    def summary(name,rows):
        x=prior.summarize([r['arms'][name]['counts'] for r in rows]); e=collections.Counter()
        for r in rows:e.update(r['arms'][name]['edge_reachability'])
        x.update(e);return x
    out={}
    for name in names:
        out[name]={'overall':summary(name,cases),'by_mode':{m:summary(name,[r for r in cases if r['mode']==m]) for m in TEST_MODES},
          'by_family':{f:summary(name,[r for r in cases if r['family']==f]) for f in ('allocation','encounter')}}
        clusters=collections.defaultdict(int);g=h=0
        for r in cases:
            a=r['arms']['A_frozen_S']['counts']['core_exact'];b=r['arms'][name]['counts']['core_exact'];clusters[r['base_id']]+=b-a;g+=not a and b;h+=a and not b
        out[name]['paired_vs_frozen_S']={'core_gain':g,'core_harm':h,'base_net_gain':sum(v>0 for v in clusters.values()),'base_net_harm':sum(v<0 for v in clusters.values()),'base_tie':sum(v==0 for v in clusters.values())}
    emit('FACTORIAL',{n:r['overall'] for n,r in out.items()})
    return {'training':history,'training_unalignable':bad,'arms':out,'case_level':cases,'note':'edge reachability is an upper bound for a graph assembler that requires exact G1 endpoints, not measured G2 accuracy'}

def audit(args):
    groups,old=cohorts(args.root,args.train_bases,args.test_bases,'probe');_,record,sig,_,lin=lineage(args.root,groups)
    other,_=cohorts(args.root,args.train_bases,args.test_bases,'run');lineage(args.root,other)
    a={r['prompt_sha256'] for rows in groups.values() for r in rows};b={r['prompt_sha256'] for rows in other.values() for r in rows}
    need(a.isdisjoint(b),'PROBE_FACTORIAL_PROMPT_LEAKAGE')
    need({r['base_id'] for rows in groups.values() for r in rows}.isdisjoint({r['base_id'] for rows in other.values() for r in rows}),'PROBE_FACTORIAL_BASE_LEAKAGE')
    emit('AUDIT',{'ready':True,'model_id':record['model_id'],'frozen_g1':sig,'prior_0110_seal':old,'prior_0111':lin,'train_bases':args.train_bases,'test_bases':args.test_bases,'train_cases':len(groups['train']),'test_cases':len(groups['test']),'arms':ARMS,'probe_factorial_disjoint':True,'production_promotion':False,'g3_unlocked':False})

def run(args):
    torch.manual_seed(112);torch.set_num_threads(min(4,torch.get_num_threads()))
    groups,old=cohorts(args.root,args.train_bases,args.test_bases,args.command); paths,rec,sig,_,lin=lineage(args.root,groups)
    dest=Path(args.output) if args.output else Path(args.root)/'g1-backbone-transmission-0112'/args.command
    need(not dest.exists(),'OUTPUT_EXISTS_USE_NEW_OUTPUT');dest.mkdir(parents=True)
    write(dest/'cohort-seal.json',{'schema':SCHEMA,'groups':groups,'frozen_g1':sig,'prior_0111':lin,'prior_0110_seal':old,'config':vars(args)})
    model,tok,comp,ctrl,s,sig,lin=load(args.root,groups,args.device)
    prepared={k:prepare(tok,v) for k,v in groups.items()}
    maxpos=getattr(model.config,'max_position_embeddings',None)
    need(maxpos is None or all(p['ids'].shape[1]+1<=maxpos for rows in prepared.values() for r,p in rows),'POSITION_LIMIT')
    emb=model.get_input_embeddings().weight.detach();embedding_rms=table_rms(emb);info={'model_id':rec['model_id'],'embedding_dimension':s.numel(),'embedding_table_rows':emb.shape[0],'embedding_table_rms':embedding_rms,'learned_S_rms':rms(s),'learned_S_norm':number(s.norm()),'reference_over_embedding_rms':rms(s)/embedding_rms,'dtype':str(emb.dtype),'train_bases':args.train_bases,'test_bases':args.test_bases}
    emit('START',info);write(dest/'instrument.json',info)
    report={'schema':SCHEMA,'instrument':info,'frozen_g1':sig,'prior_0111':lin,'production_promotion':False,'g3_unlocked':False,'test_used_for_training':False,'test_used_for_selection':False,'probe_and_factorial_seeds':SEEDS,'diagnostic_oracle_uses_probe_test_labels':args.command=='probe'}
    if args.command=='probe':
        for name,fn in [('distributions',lambda:distribution_survey(model,prepared,s,args.device)),('payload',lambda:payload_probe(model,prepared,s,args.device)),('local_control',lambda:control_survey(model,comp,prepared,s,args.device,args.directions,args.control_bases)),('amplitude',lambda:amplitude_survey(model,comp,prepared,s,args.device,args.control_bases))]:
            report[name]=fn();write(dest/(name+'.json'),report[name]);emit('PHASE_SAVED',{'phase':name,'path':str(dest/(name+'.json'))})
    else:report['factorial']=factorial(model,comp,prepared,s,args.device,dest,args.steps,args.micro_steps,args.lr)
    need(prior.frozen_ok(model,comp.head) and prior.fuzz()._source_signature(args.root)==sig,'FROZEN_SOURCE_CHANGED')
    need(lineage(args.root,groups)[4]==lin,'PRIOR_REFERENCE_CHANGED')
    report['cohort_seal_sha256']=sha(dest/'cohort-seal.json');write(dest/'result.json',report);emit('SAVED',{'report':str(dest/'result.json'),'sha256':sha(dest/'result.json'),'production_promotion':False})

def selftest():
    torch.manual_seed(13);torch.set_num_threads(1)
    class Tiny(nn.Module):
        def __init__(self):
            super().__init__();self.emb=nn.Embedding(20,16);self.mix=nn.Linear(16,16);self.config=SimpleNamespace(max_position_embeddings=128)
        def get_input_embeddings(self):return self.emb
        def forward(self,*,inputs_embeds=None,input_ids=None,attention_mask=None,output_hidden_states=True,use_cache=False):
            x=self.emb(input_ids) if inputs_embeds is None else inputs_embeds;n=torch.arange(1,x.shape[1]+1,device=x.device).view(1,-1,1)
            return SimpleNamespace(hidden_states=(x,self.mix(x+x.cumsum(1)/n).tanh()))
    model=Tiny().eval().requires_grad_(False);head=GH.GamerGraphHead(hidden_size=16,width=16,layers=1,max_span_tokens=4).eval().requires_grad_(False)
    p={'ids':torch.tensor([[1,2,3,4,5,6]]),'attention':torch.ones(1,6,dtype=torch.long),'eligible':torch.tensor([False,True,True,True,True,False]),'offsets':[(0,0),(0,5),(6,8),(9,13),(14,16),(0,0)],'prompt_start':0,'prompt':'Alpha 95 cost 70'}
    row={'case_id':'fixture','base_id':'fixture','family':'allocation','mode':'control','graph':{'nodes':[{'id':'a','kind':'action','span':[0,5]},{'id':'q','kind':'quantity','span':[6,8]},{'id':'b','kind':'outcome','span':[9,13]},{'id':'r','kind':'quantity','span':[14,16]}],'edges':[{'src':'q','dst':'a','type':'applies_to'}]}}
    s=torch.randn(16)*.1;h=context(model,p,s,'cpu').detach();need(torch.equal(Reader(16)(h),h),'READER_NOT_IDENTITY')
    for name,w,rd in [('B_writer',True,False),('C_reader',False,True),('D_joint',True,True)]:
        it=Interface(s,w,rd);need(torch.equal(it.ref(),s) and torch.equal(it.output(h),h),'INTERFACE_NOT_NOOP')
        l=loss_one(model,head,p,row,it,'cpu',h);l.backward()
        if w:need(it.delta.grad is not None and it.delta.grad.abs().sum()>0,'NO_WRITER_GRADIENT')
        if rd:need(it.reader.up.weight.grad is not None and it.reader.up.weight.grad.abs().sum()>0,'NO_READER_GRADIENT')
        need(prior.frozen_ok(model,head),'FROZEN_GRADIENT')
    x=torch.randn(80,16);target=x[:,:2];fit=ridge_fit(x,target,.01);need(rms(ridge_apply(fit,x)-target)<.01,'RIDGE_FAIL')
    q=orthogonal(s,4,1);need(rms(q@F.normalize(s,dim=0))<1e-5,'ORTHOGONAL_FAIL');need(rms(q@q.T-torch.eye(4))<1e-5,'BASIS_FAIL')
    j=torch.eye(4);a,t=fit_delta(j,torch.zeros(4),cap=3);need((t-j@a).norm()<.2,'CONTROL_SOLVER_FAIL')
    o=raw(model,head,p,s,'cpu');spec=target_specs(o,p,row);need(len(spec)>0 and margins(o,spec).ndim==1,'TARGET_COORDINATES_FAIL')
    e=edge_availability(row,[[0,5,'action'],[6,8,'quantity']]);need(e=={'surface_edges':1,'reachable_surface_edges':1},'EDGE_REACHABILITY_FAIL')
    inp,hs=context(model,p,s,'cpu',layers=True);need(inp.shape==hs[-1].shape==h.shape and torch.allclose(hs[-1],h),'LAYER_ALIGNMENT_FAIL')
    emit('SELFTEST',{'actual_G1_head_and_loss':True,'writer_reader_joint_gradients':True,'zero_initialization':True,'frozen_weights':True,'ridge_reconstruction':True,'local_control_solver':True,'orthogonal_reference_basis':True,'layer_alignment':True,'edge_reachability':True,'actual_qwen_cuda':False})

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','probe','run'),nargs='?',default='audit');p.add_argument('--root',default=ROOT);p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--train-bases',type=int,default=12);p.add_argument('--test-bases',type=int,default=16);p.add_argument('--steps',type=int,default=96);p.add_argument('--micro-steps',type=int,default=24);p.add_argument('--lr',type=float,default=.002);p.add_argument('--directions',type=int,default=8);p.add_argument('--control-bases',type=int,default=4)
    a=p.parse_args(argv);need(a.steps>0 and a.micro_steps>0 and a.lr>0 and a.directions>0 and a.control_bases>0,'INVALID_CONFIG')
    if a.command=='selftest':selftest()
    elif a.command=='audit':audit(a)
    else:run(a)

if __name__=='__main__':main()
