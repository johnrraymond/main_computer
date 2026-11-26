"""0122 frozen-backbone readout laboratory. Research only; no adapter or weight updates."""
from __future__ import annotations
import argparse, collections, hashlib, importlib.util, json, math, random, subprocess, sys, tempfile
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file
from tools import nanojev_gamer_v1_backbone_preserving_0121 as P
from tools import nanojev_gamer_v1_backbone_competence_0120 as C

TAG='G12_0122_CONTEXT'; MODULE='tools.nanojev_gamer_v1_contextual_readout_0122'
ARMS=('A_bare','B_fixed_R','C_learned_R','D_SR','E_attention','F_R_attention')
SEED=1222026; KINDS=P.KINDS; RELS=P.RELS; LAYERS=P.LAYERS
GRAPH=('fit','validation','historical_test','exposed0117','exposed0121_graph','fresh_graph')
BIND=('contrast_fit','contrast_validation','preserve_dev','preserve_0118','preserve_0119','preserve_0120','exposed0121_binding','fresh_binding')
EVAL=GRAPH[2:]+BIND[2:]; SPLITS=GRAPH+BIND+('preserve_fit',)
OTS=(-4.,-2.,-1.,-.5,0.,.5,1.,2.); ETS=(-3.,-2.,-1.,0.,.5,1.,2.,3.)
need=P.need; sha=P.sha; digest=P.digest; read=P.read; imm=P.imm; ph=P.ph

def emit(k,x): print(TAG+'_'+k+' '+json.dumps(x,sort_keys=True,allow_nan=False),flush=True)

def context(a):
    root=Path(a.root).resolve(); old=root/'g1-g2-backbone-preserving-0121'; out=Path(a.output).resolve() if a.output else root/'g1-g2-contextual-readout-0122'
    ident=read(old/'identity.json'); result=read(old/'result.json'); sel=read(old/'selection.json')
    need(result['identity']==digest(ident)==sel['identity'] and result['selection']==sel and not result['production_modified'],'0121_PARENT')
    need(ident['source']==sha(P.__file__) and ident['0120_source']==sha(C.__file__),'PARENT_SOURCE_DRIFT')
    model=read(root/'g1-g2-backbone-competence-0120'/'model-receipt.json');need(model==ident['model'],'MODEL_RECEIPT_DRIFT')
    spec=importlib.util.find_spec('tools.nanojev_gamer_v1_g1_semantic_fuzz_lab_0110');need(spec is not None and spec.origin,'0110_GENERATOR_REQUIRED');need(sha(spec.origin)==ident['generator_source'],'GENERATOR_DRIFT')
    paths=[old/'identity.json',old/'selection.json',old/'result.json',*(old/s/'seal.json' for s in P.SPLITS)]
    for lab in ('g1-g2-semantic-contrast-0118','g1-g2-semantic-addressability-0119'):
        paths.extend(root/lab/s/'seal.json' for s in ('fit','validation'))
    config={k:getattr(a,k) for k in ('device','epochs','min_epochs','patience','width','reader_lr','register_lr','preservation_weight','max_kl','span_limit','fresh_bases')}
    return root,out,{'source':sha(__file__),'parent_source':sha(P.__file__),'competence_source':sha(C.__file__),'model':model,'generator':sha(spec.origin),'parents':{str(p.relative_to(root)):sha(p) for p in paths},'config':config,'arms':list(ARMS),'seed':SEED,'layers':list(LAYERS)}

def fresh_bindings():
    specs=(('refers_to','action','action',('Lichen','Clover'),('Magenta','Silver'),'aliases','original options'),('owns','actor','action',('dyer','carver'),('Maroon','Azure'),'owners','available actions'),('applies_to','quantity','action',('57','94'),('Umber','Violet'),'costs','options'),('measures','quantity','outcome',('36','89'),('turnover','shortfall'),'values','outcomes'))
    for rel,lk,rk,ls,rs,ll,rl in specs:
        for t in range(3):
            for para in (False,True):
                for flip in (False,True):
                    prompt='Register of '+ll+': ';anchors=[]
                    for side,items,kind in [('l',ls,lk),('r',rs,rk)]:
                        if side=='r':prompt+='; register of '+rl+': '
                        for i,w in enumerate(items):
                            if i:prompt+=' / ' if para else ', '
                            start=len(prompt);prompt+=w;anchors.append({'id':side+str(i),'kind':kind,'span':[start,len(prompt)]})
                    x,y=rs[int(flip)],rs[1-int(flip)];u,v=ls
                    cues=((f'A draft linked {u} with {y}. The audit rejected that link and assigned {u} to {x} instead; each partner is unique.',f'The old link {u}—{y} is void. Its replacement is {u}—{x}, leaving one unused partner for {v}.'),
                          (f'Read the following entries column by column: {u}, {v}; {x}, {y}. Entries at equal column positions are partners.',f'The two columns are headed {u} and {v}; their respective contents are {x} and {y}. The heading and content form a pair.'),
                          (f'Of the two possible complete pairings, retain the one in which {y} is paired with {v}, not {u}.',f'The final record rules out {x} as the partner of {v}. No entry can have two partners.'))
                    prompt+='. '+cues[t][int(para)];h=ph(prompt);mode=('reverse' if flip else 'direct')+('_paraphrase' if para else '');base=f'0122-{rel}-{t}';case='0122-'+h[:24]
                    yield {'case_id':case,'base_id':base,'mode':mode,'structure':t,'relation':rel,'prompt':prompt,'prompt_sha256':h,'anchors':anchors},{'case_id':case,'prompt_sha256':h,'base_id':base,'mode':mode,'positive':[int((i!=j) if flip else i==j) for i in range(2) for j in range(2)]}

def seal(ctx,name,rr,gg):
    _,out,ident=ctx;need(len({r['prompt_sha256'] for r in rr})==len(rr) and all(ph(r['prompt'])==r['prompt_sha256'] for r in rr),'PROMPT_INTEGRITY_'+name)
    d=out/name;imm(d/'prompts.json',rr)
    if gg is not None:imm(d/'answers.SECRET.json',gg)
    imm(d/'seal.json',{'identity':digest(ident),'split':name,'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json') if gg is not None else None,'cases':len(rr)});emit('SEALED',{'split':name,'cases':len(rr)})

def prepare(a,ctx):
    root,out,ident=ctx;old=root/'g1-g2-backbone-preserving-0121';oldctx=(root,old,read(old/'identity.json'));groups={}
    for name in ('fit','validation','historical_test','exposed0117','preserve_dev','preserve_0118','preserve_0119','preserve_0120'):groups[name]=P.cohort(oldctx,name,True)
    for dst,src in [('exposed0121_graph','fresh_graph'),('exposed0121_binding','fresh_binding')]:groups[dst]=P.cohort(oldctx,src,True)
    groups['preserve_fit']=(P.cohort(oldctx,'preserve_fit'),None)
    for dest,src in [('contrast_fit','fit'),('contrast_validation','validation')]:
        rr=[];gg=[]
        for lab in ('g1-g2-semantic-contrast-0118','g1-g2-semantic-addressability-0119'):
            r,g=P.parent_rows(root/lab/src);C.validate_rows(r,g);rr+=r;gg+=g
        groups[dest]=(rr,gg)
    need(not {r['base_id'] for r in groups['contrast_fit'][0]} & {r['base_id'] for r in groups['contrast_validation'][0]},'CONTRAST_BASE_REUSE')
    known={r['prompt_sha256'] for rr,_ in groups.values() for r in rr}
    # Exact hash exclusion against accessible older prompt manifests; not proof of semantic novelty.
    for f in root.glob('*/**/prompts.json'):
        if out in f.parents:continue
        try:
            rows=read(f)
            if isinstance(rows,list):known.update(r['prompt_sha256'] for r in rows if isinstance(r,dict) and 'prompt_sha256' in r)
        except (ValueError, OSError):continue
    from tools import nanojev_gamer_v1_g1_semantic_fuzz_lab_0110 as lab
    generated=[]
    for i in range(a.fresh_bases):
        b=lab._base(i,seed=1224026);b['split']='test'
        for mode in ('control','alias+forward','pronoun+forward','caps'):
            r=lab._variant(b,mode);generated.append(dict(r,case_id='0122-'+str(r['case_id']),base_id='0122-'+str(r['base_id'])))
    groups['fresh_graph']=([P.public_graph(r) for r in generated],[P.ganswer(r) for r in generated])
    z=list(fresh_bindings());rr=[r for r,g in z];gg=[g for r,g in z];C.validate_rows(rr,gg)
    # Reversal grouping and mode are withheld from the fresh public binding records.
    groups['fresh_binding']=([{k:v for k,v in r.items() if k not in ('base_id','mode')} for r in rr],gg)
    for name in ('fresh_graph','fresh_binding'):
        hashes={r['prompt_sha256'] for r in groups[name][0]};need(not known&hashes,'FRESH_COLLISION_'+name);known.update(hashes)
    for name in SPLITS:seal(ctx,name,*groups[name])
    imm(out/'identity.json',ident)

def bind_rows(rr,gg):return [dict(r,base_id=g.get('base_id',r.get('base_id')),mode=g.get('mode',r.get('mode'))) for r,g in zip(rr,gg)]

class Reader(nn.Module):
    def __init__(self,dim,arm,rms,width):
        super().__init__();self.arm=arm;self.width=width;self.attn=arm in ARMS[4:];self.mix=nn.Parameter(torch.zeros(3));self.project=nn.Linear(dim,width);self.span=nn.Sequential(nn.Linear(4*width,width),nn.GELU(),nn.Linear(width,width),nn.GELU());self.objects=nn.Linear(width,len(KINDS)+1);self.kind=nn.Embedding(len(KINDS),16);self.node=nn.Linear(width+16,width)
        # Identical non-contextual branch initialization across all arms.
        self.binding=nn.Sequential(nn.Linear(5*width,width),nn.GELU(),nn.Linear(width,len(RELS)))
        if self.attn:
            self.query=nn.Linear(4*width,2*width);self.key=nn.Linear(width,2*width);self.value=nn.Linear(width,2*width);self.contextual=nn.Sequential(nn.Linear(7*width,width),nn.GELU(),nn.Linear(width,len(RELS)))
        self.register_buffer('rms',torch.tensor(float(rms)));gen=torch.Generator().manual_seed(SEED+77);r=torch.randn(dim,generator=gen)*.02
        if arm==ARMS[1]:self.register_buffer('R',r)
        elif arm in (ARMS[2],ARMS[3],ARMS[5]):self.R=nn.Parameter(r)
        if arm==ARMS[3]:self.S=nn.Parameter(torch.randn(dim,generator=gen)*.02)
    reg=P.Readers.reg
    def spans(self,h,idx):
        z=F.gelu(self.project(sum(w*F.layer_norm(x.float(),(x.shape[-1],)) for w,x in zip(self.mix.softmax(0),h))));g=z[-1]
        if not len(idx):return z.new_empty((0,self.width)),(g,z)
        a,b=idx.T;cum=torch.cat((z.new_zeros((1,self.width)),z.cumsum(0)),0);mean=(cum[b+1]-cum[a])/(b-a+1)[:,None]
        # Only prompt tokens in attention memory; final suffix state separately available.
        memory=z[int(a.min()):int(b.max())+1];return self.span(torch.cat((z[a],mean,z[b],g.expand(len(a),-1)),1)),(g,memory)
    def pairs(self,feat,context,indices,kinds):
        n=len(indices)
        if not n:return feat.new_empty((0,0,len(RELS)))
        g,memory=context;z=F.gelu(self.node(torch.cat((feat[indices],self.kind(kinds)),1)));d=self.width;outputs=[]
        if self.attn:k=self.key(memory).reshape(-1,2,d).transpose(0,1);v=self.value(memory).reshape(-1,2,d).transpose(0,1)
        for start in range(0,n*n,256):
            ix=torch.arange(start,min(start+256,n*n),device=z.device);x=z[ix//n];y=z[ix%n];pair=torch.cat((x,y,x*y,x-y),-1);base=torch.cat((pair,g.expand(len(ix),-1)),-1);out=self.binding(base)
            if self.attn:
                q=self.query(pair).reshape(-1,2,d).transpose(0,1);att=(q@k.transpose(-1,-2)/math.sqrt(d)).softmax(-1);c=(att@v).transpose(0,1).reshape(-1,2*d);c1,c2=c.chunk(2,-1);out=out+self.contextual(torch.cat((pair,c1,c2,c1*c2),-1))
            outputs.append(out)
        return torch.cat(outputs).reshape(n,n,len(RELS))

def setup(a,ctx,arm):
    torch.manual_seed(SEED);model,tok=P.load_model(a,ctx,'A_bare_readers');need(not hasattr(model,'peft_config'),'ADAPTER_FORBIDDEN');rms=float(model.get_input_embeddings().weight.detach().float().square().mean().sqrt());torch.manual_seed(SEED+1);net=Reader(model.config.hidden_size,arm,rms,a.width).to(a.device);P.freeze_check(model,None);return model,tok,net

def feature(model,net,p,device,cache=None,key=None):return P.feature(model,net,p,device,cache,key)

def object_targets(keys,answer,device):
    gold,_=P.gold_graph(answer);lookup={x:i for i,x in enumerate(keys)};target=torch.zeros(len(keys),dtype=torch.long,device=device);sel=[]
    for x,y,k in sorted(gold):
        if (x,y) in lookup and k in KINDS:
            i=lookup[x,y];j=KINDS.index(k);need(int(target[i]) in (0,j+1),'MULTITYPE');target[i]=j+1;sel.append((i,j))
    return target,sel

def hard_mean(loss,mask,cap):
    z=loss[mask];return z.topk(min(cap,z.numel())).values.mean() if z.numel() else loss.sum()*0

def graph_loss(net,f,g,keys,answer):
    raw=net.objects(f);targets,s=object_targets(keys,answer,f.device);need(len(keys)>0,'EMPTY_SPANS');loss=F.cross_entropy(raw,targets,reduction='none');pos=targets>0;np=int(pos.sum());l1=hard_mean(loss,pos,max(np,1))+hard_mean(loss,~pos,max(32,4*np))
    # All mapped gold nodes plus false proposals, including low-margin hard proposals.
    s=sorted(set(s)|set(P.choose(raw,keys,-4.,limit=32)));objs=P.as_obj(keys,s);z=P.pair_scores(net,f,g,s);_,edges=P.gold_graph(answer);y=torch.zeros_like(z);lookup={x:i for i,x in enumerate(objs)}
    for x,w,t in edges:
        if x in lookup and w in lookup:y[lookup[x],lookup[w],RELS.index(t)]=1
    mask=(~torch.eye(len(s),dtype=torch.bool,device=f.device))[:,:,None].expand_as(y);bce=F.binary_cross_entropy_with_logits(z,y,reduction='none');terms=[]
    for k in range(len(RELS)):
        p=(y[:,:,k]>0)&mask[:,:,k];neg=(~p)&mask[:,:,k];count=int(p.sum());terms.append(hard_mean(bce[:,:,k],p,max(count,1))+hard_mean(bce[:,:,k],neg,max(16,4*count)))
    return l1+torch.stack(terms).mean()

def contrast_scores(net,f,g,keys,r):
    lookup={x:i for i,x in enumerate(keys)};anchors={x['id']:x for x in r['anchors']};s=[]
    for aid in ('l0','l1','r0','r1'):
        x=anchors[aid];k=tuple(x['span']);
        if k not in lookup:return None
        s.append((lookup[k],KINDS.index(x['kind'])))
    z=P.pair_scores(net,f,g,s);return z[:2,2:4,RELS.index(r['relation'])].reshape(-1)

def contrast_loss(z,answer):
    y=z.new_tensor(answer['positive']);p=z[y.bool()];n=z[~y.bool()];return F.softplus(n[None,:]-p[:,None]).mean()+.5*F.binary_cross_entropy_with_logits(z,y)

def contrast_pred(net,f,g,keys,r):
    z=contrast_scores(net,f,g,keys,r);return {'mapped':z is not None,'scores':z.detach().cpu().tolist() if z is not None else [0.]*4}

def inventory(net,f,keys):
    raw=net.objects(f);v,k=(raw[:,1:]-raw[:,:1]).max(1);return [[*key,KINDS[int(j)],float(m)] for key,j,m in zip(keys,k.detach().cpu(),v.detach().cpu())]

def inventory_metrics(answers,preds):
    total=mapped=typed=admitted=0
    for a,p in zip(answers,preds):
        gold,_=P.gold_graph(a);inv={(x,y):(k,m) for x,y,k,m in p['inventory']};out={tuple(x) for x in p['objects']};total+=len(gold)
        for x,y,k in gold:
            mapped+= (x,y) in inv;typed+= (x,y) in inv and inv[x,y][0]==k;admitted+=(x,y,k) in out
    return {'gold':total,'span_mapped':mapped,'correct_top_kind_before_admission':typed,'admitted':admitted}

def detached(f,g):return f.detach().cpu(),tuple(t.detach().cpu() for t in g)
def transfer(f,g,device):return f.to(device),tuple(t.to(device) for t in g)

def validate(a,model,net,fitprep,val,vg,cv,cg,cache):
    objects={t:[] for t in OTS};stored=[];cp=[]
    with torch.no_grad():
        for r in val:
            f,g,keys=feature(model,net,fitprep[r['case_id']],a.device,cache,r['case_id']);raw=net.objects(f);stored.append((*detached(f,g),keys))
            for t in OTS:objects[t].append({'objects':P.as_obj(keys,P.choose(raw,keys,t)), 'edges':[]})
        objgrid={t:P.metrics(vg,p) for t,p in objects.items()};ot=max(OTS,key=lambda t:(objgrid[t]['g1_f1'],objgrid[t]['g1_recall'],objgrid[t]['g1_precision']));edges={t:[] for t in ETS};oracle={t:[] for t in ETS};inv=[]
        for (f,g,keys),ans in zip(stored,vg):
            f,g=transfer(f,g,a.device);raw=net.objects(f);s=P.choose(raw,keys,ot,limit=257);over=len(s)>256;s=s[:256];obj=P.as_obj(keys,s);p=(obj,P.pair_scores(net,f,g,s),over);_,gold=object_targets(keys,ans,f.device);op=(P.as_obj(keys,gold),P.pair_scores(net,f,g,gold),False)
            for t in ETS:edges[t].append(P.decode(net,f,g,keys,ot,t,p));oracle[t].append(P.decode(net,f,g,keys,ot,t,op))
            inv.append({'objects':obj,'inventory':inventory(net,f,keys)})
        edgegrid={t:P.metrics(vg,p) for t,p in edges.items()};oraclegrid={t:P.metrics(vg,p) for t,p in oracle.items()};et=max(ETS,key=lambda t:edgegrid[t]['g2_f1']);ort=max(ETS,key=lambda t:oraclegrid[t]['g2_f1'])
        for r in cv:
            f,g,keys=feature(model,net,fitprep[r['case_id']],a.device,cache,r['case_id']);cp.append(contrast_pred(net,f,g,keys,r))
    m=C.ranks(cv,cg,[p['scores'] for p in cp]);m['mapped']=sum(p['mapped'] for p in cp)
    return {'thresholds':[ot,et],'oracle_threshold':ort,'graph':edgegrid[et],'oracle':oraclegrid[ort],'inventory':inventory_metrics(vg,inv),'contrast':m,'object_grid':[{ 'threshold':t,'g1_f1':v['g1_f1'],'recall':v['g1_recall']} for t,v in objgrid.items()],'edge_grid':[{'threshold':t,'g2_f1':v['g2_f1'],'oracle_g2_f1':oraclegrid[t]['g2_f1']} for t,v in edgegrid.items()]}

def quality(v):
    g=v['graph'];c=v['contrast'];rev=c['reversal_pairs_correct']/max(1,c['reversal_pairs']);return ((g['g1_f1']+g['g2_f1']+rev)/3,c['pairwise_accuracy'])

def arm_train(a,ctx,arm):
    _,out,_=ctx;folder=out/'arms'/arm;done=folder/'training.json';selected=folder/'selected.safetensors'
    if done.exists():need(read(done)['identity']==digest(ctx[2]) and read(done)['checkpoint']==sha(selected),'TRAINING_DRIFT');emit('REUSED_ARM',{'arm':arm});return
    model,tok,net=setup(a,ctx,arm);fit,fg=P.cohort(ctx,'fit',True);val,vg=P.cohort(ctx,'validation',True);cf,cfg=P.cohort(ctx,'contrast_fit',True);cv,cg=P.cohort(ctx,'contrast_validation',True);prep={r['case_id']:P.token_prepare(tok,r['prompt'],a.span_limit) for r in fit+val+cf+cv};cache={} if arm in (ARMS[0],ARMS[1],ARMS[4]) else None
    reg=[p for n,p in net.named_parameters() if n in ('S','R')];heads=[p for n,p in net.named_parameters() if n not in ('S','R')];groups=[{'params':heads,'lr':a.reader_lr}]+([{'params':reg,'lr':a.register_lr}] if reg else []);opt=torch.optim.AdamW(groups,weight_decay=0.);params=heads+reg
    ref=read(out/'bare-preservation.json');teacher=load_file(str(out/'teacher.safetensors'));need(ref['teacher_sha256']==sha(out/'teacher.safetensors'),'TEACHER_DRIFT');history=[];best=None;stale=0;initial={n:p.detach().clone() for n,p in net.named_parameters() if n in ('S','R')};static_gate=None
    for epoch in range(1,a.epochs+1):
        net.train();order=list(range(len(fit)));random.Random(SEED+epoch).shuffle(order);co=list(range(len(cf)));random.Random(SEED+100+epoch).shuffle(co);tot=ct=pk=0.;mapped=0
        # Match graph/contrast update counts across arms. Each lane contributes one loss per step.
        steps=max(len(order),len(co))
        for j in range(steps):
            opt.zero_grad(set_to_none=True);i=order[j%len(order)];r=fit[i];f,g,keys=feature(model,net,prep[r['case_id']],a.device,cache,r['case_id']);loss=graph_loss(net,f,g,keys,fg[i]);need(bool(torch.isfinite(loss)),'GRAPH_NONFINITE');loss.backward();tot+=float(loss.detach());i=co[j%len(co)];r=cf[i];f,g,keys=feature(model,net,prep[r['case_id']],a.device,cache,r['case_id']);z=contrast_scores(net,f,g,keys,r)
            if z is not None:
                loss=contrast_loss(z,cfg[i]);need(bool(torch.isfinite(loss)),'CONTRAST_NONFINITE');loss.backward();ct+=float(loss.detach());mapped+=1
            if arm==ARMS[3] and j%4==0:
                k=((epoch-1)*steps+j)//4%len(ref['texts']);v=P.kl(P.native_logits(model,tok,ref['texts'][k],net,a.device),teacher[f'fit{k}']);(a.preservation_weight*v).backward();pk+=float(v.detach())
            norm=torch.nn.utils.clip_grad_norm_(params,1.);need(bool(torch.isfinite(norm)),'NONFINITE_GRADIENT');P.freeze_check(model,None);opt.step()
            if j==0 or (j+1)%24==0 or j+1==steps:emit('LEARN',{'arm':arm,'epoch':epoch,'done':j+1,'of':steps,'graph_loss':tot/(j+1),'contrast_loss':ct/max(1,mapped),'mapped_contrast':mapped})
        opt.zero_grad(set_to_none=True);net.eval();v=validate(a,model,net,prep,val,vg,cv,cg,cache)
        if static_gate is None or arm==ARMS[3]:static_gate=P.gate(a,ctx,model,tok,net,teacher,ref)
        pg=static_gate;rec={'epoch':epoch,'validation':v,'preservation':pg,'graph_loss':tot/steps,'contrast_loss':ct/max(1,mapped),'contrast_mapped':mapped,'teacher_kl':pk/max(1,math.ceil(steps/4)),'register_delta_rms':{n:float((p.detach()-initial[n]).square().mean().sqrt()) for n,p in net.named_parameters() if n in initial}};history.append(rec);emit('EPOCH',{'arm':arm,**rec});key=(pg['passed'],*quality(v))
        if best is None or key>best[0]:P.save(selected,P.changes(net,None));best=(key,rec);stale=0
        else:stale+=1
        if epoch>=a.min_epochs and stale>=a.patience:break
    P.freeze_check(model,None);imm(done,{'identity':digest(ctx[2]),'arm':arm,'checkpoint':sha(selected),'selected':best[1],'history':history,'stop_reason':'validation_plateau' if len(history)<a.epochs else 'epoch_budget','trainable_backbone_parameters':0,'trainable_reader_parameters':sum(p.numel() for p in net.parameters())})

def selection(a,ctx):
    _,out,ident=ctx;recs={k:read(out/'arms'/k/'training.json') for k in ARMS};need(all(v['identity']==digest(ident) and v['checkpoint']==sha(out/'arms'/k/'selected.safetensors') for k,v in recs.items()),'SELECTION_DRIFT');eligible=[k for k,v in recs.items() if v['selected']['preservation']['passed']];winner=max(eligible,key=lambda k:quality(recs[k]['selected']['validation'])) if eligible else None
    s={'identity':digest(ident),'eligible':eligible,'winner':winner,'training':{k:sha(out/'arms'/k/'training.json') for k in ARMS}};imm(out/'selection.json',s);emit('SELECTED',s);return s

def lineage(ctx,arm):
    _,out,ident=ctx;s=read(out/'selection.json');folder=out/'arms'/arm;r=read(folder/'training.json');need(s['identity']==digest(ident) and s['training'][arm]==sha(folder/'training.json') and r['checkpoint']==sha(folder/'selected.safetensors'),'LINEAGE');return folder,r

def header(ctx,rec,split):
    out=ctx[1];return {'selection':sha(out/'selection.json'),'seal':sha(out/split/'seal.json'),'checkpoint':rec['checkpoint']}

def predict(a,ctx,arm):
    folder,rec=lineage(ctx,arm);C.guard();model,tok,net=setup(a,ctx,arm);P.restore(net,None,folder/'selected.safetensors');net.eval();manifest={}
    with torch.no_grad():
        for split in EVAL:
            rr=P.cohort(ctx,split);d=folder/'predictions'/split;head=header(ctx,rec,split);shards=[]
            for i,r in enumerate(rr):
                p=d/f'{i:04d}.json'
                if p.exists():z=read(p);need(z['header']==head and z['prompt']==r['prompt_sha256'] and z['digest']==digest(z['body']),'SHARD_DRIFT')
                else:
                    f,g,keys=feature(model,net,P.token_prepare(tok,r['prompt'],a.span_limit),a.device)
                    if split in GRAPH:
                        body=P.decode(net,f,g,keys,*rec['selected']['validation']['thresholds']);body['inventory']=inventory(net,f,keys)
                    else:
                        body={'conditional':contrast_pred(net,f,g,keys,r),'native':P.native(model,tok,r,net,a.device)}
                        if split=='fresh_binding':
                            # Both members of every reversal now have the same text, not merely shuffled labels.
                            blank=dict(r,prompt=r['prompt'][:max(x['span'][1] for x in r['anchors'])]);bf,bg,bk=feature(model,net,P.token_prepare(tok,blank['prompt'],a.span_limit),a.device);body['conditional_no_cue']=contrast_pred(net,bf,bg,bk,blank)
                    imm(p,{'header':head,'prompt':r['prompt_sha256'],'body':body,'digest':digest(body)})
                shards.append(sha(p))
                if i==0 or (i+1)%16==0 or i+1==len(rr):emit('PREDICT',{'arm':arm,'split':split,'done':i+1,'of':len(rr)})
            imm(d/'lock.json',{'header':head,'shards':shards});manifest[split]=sha(d/'lock.json')
    imm(folder/'prediction-lock.json',manifest);P.freeze_check(model,None)

def verify_all(ctx):
    for arm in ARMS:
        folder,rec=lineage(ctx,arm);lock=read(folder/'prediction-lock.json')
        for split in EVAL:
            d=folder/'predictions'/split;rr=P.cohort(ctx,split);z=read(d/'lock.json');need(lock[split]==sha(d/'lock.json') and z['header']==header(ctx,rec,split) and z['shards']==[sha(d/f'{i:04d}.json') for i in range(len(rr))],'PREDICTION_LOCK')

def score(a,ctx):
    verify_all(ctx);out=ctx[1];res={}
    for arm in ARMS:
        folder,rec=lineage(ctx,arm);v={'development':rec,'cohorts':{}}
        for split in EVAL:
            rr,gg=P.cohort(ctx,split,True);pp=[]
            for i,r in enumerate(rr):
                z=read(folder/'predictions'/split/f'{i:04d}.json');need(z['prompt']==r['prompt_sha256'] and z['digest']==digest(z['body']),'BODY_DRIFT');pp.append(z['body'])
            if split in GRAPH:m=P.metrics(gg,pp)|{'inventory':inventory_metrics(gg,pp)}
            else:
                rr=bind_rows(rr,gg);m={'conditional':C.ranks(rr,gg,[p['conditional']['scores'] for p in pp]),'mapped':sum(p['conditional']['mapped'] for p in pp),'native':C.ranks(rr,gg,[p['native']['scores'] for p in pp]),'order':C.order_metrics(gg,[{'normal':{'choice':p['native']['choice']}} for p in pp],'wide')}
            if split in BIND:
                m['by_relation']={}
                for rel in RELS:
                    idx=[i for i,r in enumerate(rr) if r['relation']==rel];m['by_relation'][rel]={k:C.ranks([rr[i] for i in idx],[gg[i] for i in idx],[pp[i][k]['scores'] for i in idx]) for k in ('conditional','native')}
                if split=='fresh_binding':
                    m['conditional_no_cue']=C.ranks(rr,gg,[p['conditional_no_cue']['scores'] for p in pp]);m['no_cue_mapped']=sum(p['conditional_no_cue']['mapped'] for p in pp)
            v['cohorts'][split]=m;emit('COHORT',{'arm':arm,'split':split,**m})
        res[arm]=v
    for arm,v in res.items():
        v['heldout_preservation']={s:sum(x and not y for x,y in zip(res[ARMS[0]]['cohorts'][s]['native']['exact_flags'],v['cohorts'][s]['native']['exact_flags'])) for s in BIND[2:]};v['preservation_review_passed']=v['development']['selected']['preservation']['passed'] and not any(v['heldout_preservation'].values())
    imm(out/'result.json',{'identity':digest(ctx[2]),'selection':read(out/'selection.json'),'results':res,'certification':False,'production_modified':False,'status':'RESEARCH_REVIEW_REQUIRED'});emit('RESULT',{'path':str(out/'result.json'),'certification':False})

def oracle(a,ctx,arm):
    # Explicitly post-lock and separate from operational predictions or checkpoint selection.
    out=ctx[1];need((out/'result.json').is_file(),'SCORE_REQUIRED');verify_all(ctx);folder,rec=lineage(ctx,arm);p=folder/'oracle-fresh-graph.json';head={'result':sha(out/'result.json'),'checkpoint':rec['checkpoint'],'predictions':sha(folder/'prediction-lock.json')}
    if p.exists():
        prior=read(p);need(prior['header']==head and prior['metrics_digest']==digest(prior['metrics']),'ORACLE_DRIFT');return
    model,tok,net=setup(a,ctx,arm);P.restore(net,None,folder/'selected.safetensors');net.eval();rr,gg=P.cohort(ctx,'fresh_graph',True);pp=[];threshold=rec['selected']['validation']['oracle_threshold']
    with torch.no_grad():
        for r,ans in zip(rr,gg):
            f,g,keys=feature(model,net,P.token_prepare(tok,r['prompt'],a.span_limit),a.device);_,s=object_targets(keys,ans,f.device);obj=P.as_obj(keys,s);pp.append(P.decode(net,f,g,keys,0.,threshold,(obj,P.pair_scores(net,f,g,s),False)))
    m=P.metrics(gg,pp);imm(p,{'header':head,'scope':'postlock_gold_endpoints_not_operational','threshold':threshold,'metrics':m,'metrics_digest':digest(m)});P.freeze_check(model,None);emit('ORACLE',{'arm':arm,**m})

def report(a,ctx):
    out=ctx[1];verify_all(ctx);result=read(out/'result.json');need(result['identity']==digest(ctx[2]) and result['selection']==read(out/'selection.json'),'RESULT_DRIFT');result_hash=sha(out/'result.json');summary={}
    for arm in ARMS:
        folder,rec=lineage(ctx,arm);p=folder/'oracle-fresh-graph.json';v=read(p);need(v['header']=={'result':result_hash,'checkpoint':rec['checkpoint'],'predictions':sha(folder/'prediction-lock.json')} and v['metrics_digest']==digest(v['metrics']),'ORACLE_REPORT_DRIFT');result['results'][arm]['postlock_gold_endpoint_diagnostic']=v
        r=result['results'][arm];g=r['cohorts']['fresh_graph'];b=r['cohorts']['fresh_binding'];summary[arm]={'epochs':len(rec['history']),'selected_epoch':rec['selected']['epoch'],'g1_f1':g['g1_f1'],'g2_f1':g['g2_f1'],'g2_gold_endpoint_f1':v['metrics']['g2_f1'],'fresh_binding_accuracy':b['conditional']['pairwise_accuracy'],'fresh_binding_reversals':b['conditional']['reversal_pairs_correct'],'fresh_binding_reversal_total':b['conditional']['reversal_pairs'],'no_cue_reversals':b['conditional_no_cue']['reversal_pairs_correct'],'native_preservation':r['preservation_review_passed']}
    result['operational_result_sha256']=result_hash;result['summary']=summary;imm(out/'result-with-oracles.json',result);emit('COMPLETE',{'path':str(out/'result-with-oracles.json'),'selection':result['selection'],'summary':summary,'certification':False})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','run','prepare','baseline','train','select','predict','score','oracle','report'),nargs='?',default='audit');p.add_argument('--root',default='../runtime/reports/gamer-v1');p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--epochs',type=int,default=6);p.add_argument('--min-epochs',type=int,default=3);p.add_argument('--patience',type=int,default=2);p.add_argument('--width',type=int,default=192);p.add_argument('--reader-lr',type=float,default=.0003);p.add_argument('--register-lr',type=float,default=.0003);p.add_argument('--preservation-weight',type=float,default=1.);p.add_argument('--max-kl',type=float,default=.1);p.add_argument('--fresh-bases',type=int,default=12);p.add_argument('--span-limit',type=int,default=16);p.add_argument('--arm',choices=ARMS);a=p.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':return selftest()
    need(a.epochs>=a.min_epochs>=1 and a.patience>=1 and a.width>=16 and a.fresh_bases>=4 and a.span_limit>=1 and min(a.reader_lr,a.register_lr)>0 and a.max_kl>=0 and a.preservation_weight>=0,'CONFIG');ctx=context(a);root,out,ident=ctx
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':ident,'backbone_trainable':False,'adapter_used':False});return
    if a.command=='run':
        args=['--root',str(root),'--output',str(out)]
        for k,v in ident['config'].items():args+=['--'+k.replace('_','-'),str(v)]
        def phase(cmd,arm=None):subprocess.run([sys.executable,'-u','-m',MODULE,cmd,*args,*(['--arm',arm] if arm else [])],check=True)
        phase('prepare');phase('baseline')
        for arm in ARMS:phase('train',arm)
        phase('select')
        for arm in ARMS:phase('predict',arm)
        phase('score')
        for arm in ARMS:phase('oracle',arm)
        phase('report');return
    if a.command=='prepare':return prepare(a,ctx)
    need(read(out/'identity.json')==ident,'RUN_IDENTITY_DRIFT')
    if a.command=='baseline':return P.baseline(a,ctx)
    if a.command=='select':return selection(a,ctx)
    if a.command=='score':return score(a,ctx)
    if a.command=='report':return report(a,ctx)
    need(a.arm is not None,'ARM_REQUIRED');return {'train':arm_train,'predict':predict,'oracle':oracle}[a.command](a,ctx,a.arm)

def selftest():
    from types import SimpleNamespace
    import contextlib, io, re
    torch.set_num_threads(1);torch.manual_seed(SEED)
    rows=list(fresh_bindings());C.validate_rows([r for r,g in rows],[g for r,g in rows]);need(len(rows)==48,'FRESH_COUNT')
    # Actual gradient flow through the causal fixture, not a fake gradient on R.
    ids=torch.tensor([[2,3,4,5,6]]);keys=[(0,1),(2,3),(4,5),(6,7)];idx=torch.arange(4).repeat(2,1).T.contiguous();registers={}
    for arm in ARMS:
        torch.manual_seed(SEED);model=P.fixture_backbone();torch.manual_seed(SEED+1);net=Reader(8,arm,.1,16);f,g,_=feature(model,net,(ids,keys,idx),'cpu');z=P.pair_scores(net,f,g,[(i,i%2) for i in range(4)]);(z.square().mean()+f.square().mean()).backward();P.freeze_check(model,None)
        for name in ('S','R'):
            v=getattr(net,name,None)
            if isinstance(v,nn.Parameter):need(v.grad is not None and bool(torch.isfinite(v.grad).all()) and float(v.grad.norm())>0,'REGISTER_GRADIENT_'+arm+'_'+name)
            if name=='R' and v is not None:registers[arm]=v.detach().clone()
        if arm in ARMS[4:]:need(net.query.weight.grad is not None and float(net.query.weight.grad.norm())>0 and net.key.weight.grad is not None and float(net.key.weight.grad.norm())>0,'ATTENTION_GRADIENT')
        if arm==ARMS[1]:need('R' not in dict(net.named_parameters()),'FIXED_R_TRAINABLE')
        if arm==ARMS[0]:need(not hasattr(net,'S') and not hasattr(net,'R'),'BARE_REGISTER')
    need(all(torch.equal(registers[ARMS[1]],v) for v in registers.values()),'REGISTER_INITIALIZATION_MISMATCH')
    # A training signal must distinguish competing positive and negative assignments.
    z=torch.tensor([.1,.2,.3,.4],requires_grad=True);contrast_loss(z,{'positive':[1,0,0,1]}).backward();need(bool((z.grad[[0,3]]<0).all()) and bool((z.grad[[1,2]]>0).all()),'CONTRAST_GRADIENT_DIRECTION')
    # Explicit positive and negative threshold behavior.
    logits=torch.tensor([[1.,.5,0.,0.,0.,0.,0.,0.],[1.,0.,.5,0.,0.,0.,0.,0.]])
    need(len(P.choose(logits,[(0,1),(2,3)],0.))==0 and len(P.choose(logits,[(0,1),(2,3)],-1.))==2,'NEGATIVE_ADMISSION')
    P.fixture_guard()
    def fake_setup(a,ctx,arm):
        torch.manual_seed(SEED);model=P.fixture_backbone();torch.manual_seed(SEED+1);return model,None,Reader(8,arm,.1,a.width)
    def fake_prepare(tok,text,limit):
        keys=[m.span() for m in re.finditer(r'[A-Za-z0-9]+',text)];ids=torch.tensor([[sum(map(ord,text[a:b]))%32 for a,b in keys]]);idx=torch.arange(len(keys)).repeat(2,1).T.contiguous();return ids,keys,idx
    def fake_logits(model,tok,text,net,device):
        ids=torch.tensor([[ord(x)%32 for x in text]]);return C.forward_first(model,ids,device,net.reg('S') if net else None)[0].logits[0,-1].float()
    def fake_native(model,tok,r,net,device):
        vs=[]
        for q in C.questions(r)[1]:
            x=fake_logits(model,tok,q,net,device);vs.append({'wide':{'margin':float((x[0]-x[1]).detach()),'answer_mass':float(x.softmax(-1)[:2].sum().detach())}})
        m=(vs[0]['wide']['margin']-vs[1]['wide']['margin'])/2;return {'scores':[m,-m,-m,m],'choice':vs}
    oldsetup=globals()['setup'];old=(P.token_prepare,P.native_logits,P.native,C.guard)
    try:
        globals()['setup']=fake_setup;P.token_prepare=fake_prepare;P.native_logits=fake_logits;P.native=fake_native;C.guard=lambda:None # real guard independently tested in child
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            out=Path(td);ctx=(out,out,{'fixture':True});a=SimpleNamespace(device='cpu',width=16,epochs=2,min_epochs=1,patience=1,reader_lr=.001,register_lr=.001,preservation_weight=1.,max_kl=.1,span_limit=16)
            r={'case_id':'fixture','base_id':'fixture','prompt':'actor action','prompt_sha256':ph('actor action')};g={'case_id':'fixture','prompt_sha256':r['prompt_sha256'],'graph':{'nodes':[{'id':'a','kind':'actor','span':[0,5]},{'id':'b','kind':'action','span':[6,12]}],'edges':[{'src':'a','dst':'b','type':'owns'}]}}
            rr=[r for r,g in rows[:2]];gg=[g for r,g in rows[:2]]
            for name in SPLITS:
                rs,gs=([r],[g]) if name in GRAPH else (rr,gg)
                if name=='fresh_binding':rs=[{k:v for k,v in r.items() if k not in ('mode','base_id')} for r in rs]
                seal(ctx,name,rs,gs)
            model,tok,net=fake_setup(a,ctx,ARMS[0]);texts=[q for r in rr for q in C.questions(r)[1]]
            with torch.no_grad():
                teacher={f'{k}{i}':fake_logits(model,tok,x,None,'cpu') for k in ('fit','dev') for i,x in enumerate(texts)};pr=[fake_native(model,tok,r,None,'cpu') for r in rr]
            P.save(out/'teacher.safetensors',teacher);imm(out/'bare-preservation.json',{'teacher_sha256':sha(out/'teacher.safetensors'),'texts':texts,'devtexts':texts,'native':C.ranks(rr,gg,[x['scores'] for x in pr]),'answers_mass':sum(x['wide']['answer_mass'] for b in pr for x in b['choice'])/(2*len(pr))})
            for arm in ARMS:arm_train(a,ctx,arm)
            s=selection(a,ctx);need(ARMS[0] in s['eligible'],'FIXTURE_ELIGIBILITY')
            for arm in ARMS:predict(a,ctx,arm)
            score(a,ctx)
            for arm in ARMS:oracle(a,ctx,arm)
            report(a,ctx);need(len(read(out/'result-with-oracles.json')['summary'])==6,'ORACLE_REPORT')
            result=read(out/'result.json');need(set(result['results'])==set(ARMS) and not result['certification'],'FIXTURE_RESULT');need(all(result['results'][k]['cohorts']['fresh_binding']['conditional_no_cue']['reversal_pairs_correct']==0 for k in ARMS),'NO_CUE_REVERSAL');need(all(result['results'][k]['preservation_review_passed'] for k in ARMS if k!=ARMS[3]),'R_ONLY_NATIVE_PRESERVATION')
            arm_train(a,ctx,ARMS[0]);predict(a,ctx,ARMS[0]);score(a,ctx);oracle(a,ctx,ARMS[0])
            p=out/'arms'/ARMS[0]/'predictions'/'fresh_graph'/'0000.json';p.write_text('{}',encoding='utf-8')
            try:score(a,ctx)
            except (RuntimeError,ValueError,KeyError):pass
            else:raise AssertionError('TAMPER_ACCEPTED')
    finally:
        globals()['setup']=oldsetup;P.token_prepare,P.native_logits,P.native,C.guard=old
    emit('SELFTEST',{'six_arm_causal_gradient_fixture':True,'fixed_R_matches_learned_initialization':True,'frozen_backbone':True,'pair_attention_gradient':True,'balanced_contrast_gradient':True,'negative_admission':True,'fresh_binding_contract':True,'fresh_binding_no_cue_control':True,'answer_guard_subprocess':True,'six_arm_train_select_predict_score_oracle_resume_tamper_fixture':True,'R_only_native_preservation_fixture':True,'actual_qwen_cuda':False})

if __name__=='__main__':main()
