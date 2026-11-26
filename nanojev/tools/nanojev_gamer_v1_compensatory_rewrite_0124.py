"""0124 — bounded full-parameter mathematical rewrite and compensation pilot.
Run from nanojev. Original HF weights and prior runtime evidence are read-only.
"""
from __future__ import annotations
import argparse, gc, hashlib, importlib.util, json, math, os, random, subprocess, sys, tempfile, time
from pathlib import Path
import torch
from torch.nn import functional as F
from tools import nanojev_gamer_v1_backbone_competence_0120 as C
from tools import nanojev_gamer_v1_backbone_preserving_0121 as P
from tools import nanojev_gamer_v1_contextual_readout_0122 as T
from tools import nanojev_gamer_v1_elementary_binding_0123 as E
TAG='G12_0124_REWRITE'; MODULE='tools.nanojev_gamer_v1_compensatory_rewrite_0124'
ARMS=('A_reference_reader_only','B_unrestricted','C_joint','D_sequential','E_orthogonal','F_directional_fuzz','G_iterative')
SEED=1242026; sha,read,imm,digest=C.sha,C.read,C.imm,C.digest
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_compensatory_rewrite_author_0124.py')
def need(x,msg):
    if not x:raise RuntimeError(TAG+'_'+msg)
def emit(k,v):print(TAG+'_'+k+' '+json.dumps(v,sort_keys=True,allow_nan=False),flush=True)
def source(name):
    s=importlib.util.find_spec('tools.'+name);need(s and s.origin,'SOURCE_'+name);return sha(s.origin)
def context(a):
    root=Path(a.root).resolve();out=Path(a.output).resolve() if a.output else root/'g1-g2-compensatory-rewrite-0124';parent=root/'g1-g2-elementary-binding-0123';one=parent/'one';ident=read(parent/'identity.json');result=read(parent/'result.json');fit=read(one/'fit.json');lock=read(one/'prediction-lock.json');model=read(root/'g1-g2-backbone-competence-0120'/'model-receipt.json')
    need(result['identity']==digest(ident) and result['status']=='STOPPED_AT_ONE' and not result['certification'] and not result['production_modified'],'0123_PARENT');need(ident['source']==sha(E.__file__) and ident['0122_source']==sha(T.__file__) and ident['0121_source']==sha(P.__file__) and ident['0120_source']==sha(C.__file__) and ident['model']==model,'PARENT_SOURCE');need(fit['checkpoint']==sha(one/'reader.safetensors') and fit['identity']==digest(ident) and not fit['passed'],'FAILED_READER_REQUIRED');need(lock['checkpoint']==fit['checkpoint'] and lock['predictions']==sha(one/'predictions.json') and lock['seal']==sha(one/'seal.json') and lock['identity']==digest(ident),'PARENT_LOCK');need(read(one/'result.json')['prediction_lock']==sha(one/'prediction-lock.json'),'PARENT_RESULT_LOCK')
    deps={n:source(n) for n in ('nanojev_game_math_registry','nanojev_qwen_zero_line_backbone','nanojev_qwen_hdb_frontier','nanojev_qwen_lm_radius_ladder','nanojev_qwen_problem_answer_stage1','nanojev_hawk_dove_million','nanojev_gamer_v1_elementary_binding_author_0123')};cfg={k:getattr(a,k) for k in ('device','steps','radius','reader_lr','repair_steps','max_kl','seed')};paths=[parent/'identity.json',parent/'result.json',one/'fit.json',one/'prediction-lock.json',one/'reader.safetensors',one/'seal.json',root/'g1-g2-backbone-competence-0120'/'model-receipt.json',root/'g1-g2-backbone-competence-0120'/'calibration'/'seal.json']
    gctx=graph_context(root);gd,gr=T.lineage(gctx,T.ARMS[0]);paths.extend([gctx[1]/'identity.json',gctx[1]/'selection.json',gd/'training.json',gd/'selected.safetensors',gctx[1]/'fresh_graph'/'seal.json']);need(out!=root and parent not in out.parents and out!=parent and out!=gctx[1] and gctx[1] not in out.parents,'OUTPUT_OVERLAP');return root,out,{'source':sha(__file__),'author':sha(AUTHOR),'dependencies':deps,'parent_files':{str(p.relative_to(root)):sha(p) for p in paths},'model':model,'config':cfg,'arms':list(ARMS),'stages':[1,2,3,4],'dtype':'float32_from_pinned_checkpoint','research_only':True}
def graph_context(root):
    d=root/'g1-g2-contextual-readout-0122';return root,d,read(d/'identity.json')
def graph_reader(a,ctx,model):
    d,rec=T.lineage(graph_context(ctx[0]),T.ARMS[0]);net=T.Reader(model.config.hidden_size,'A_bare',float(model.get_input_embeddings().weight.detach().square().mean().sqrt()),rec_width(ctx)).to(a.device);P.restore(net,None,d/'selected.safetensors');net.eval().requires_grad_(False);return net,rec['selected']['validation']['thresholds'][0]
def rec_width(ctx):return graph_context(ctx[0])[2]['config']['width']
@torch.no_grad()
def graph_preds(a,model,tok,net,threshold,rr):
    res=[]
    for r in rr:
        ids,keys,idx=P.token_prepare(tok,r['prompt'],16);h=P.hidden(model,ids,net,a.device);f,g=net.spans(h,idx.to(a.device));s=P.choose(net.objects(f),keys,threshold,limit=257);res.append({'case_id':r['case_id'],'objects':P.as_obj(keys,s[:256]),'overflow':len(s)>256})
    return res

def graph_review(ctx,pp):
    pairs=rows(ctx,'graph_exposed',True);base=read(ctx[1]/'reference'/'g1.json');need(len(base)==len(pp)==len(pairs),'G1_COUNT');lost=0;gain=0;fp0=fp1=tp0=tp1=gold=0;details=[]
    for (r,g),b,c in zip(pairs,base,pp):
        need(r['case_id']==b['case_id']==c['case_id'],'G1_ALIGN');truth,_=P.gold_graph(g);x={tuple(z) for z in b['objects']};y={tuple(z) for z in c['objects']};l=(x&truth)-y;gg=(y&truth)-x;lost+=len(l);gain+=len(gg);fp0+=len(x-truth);fp1+=len(y-truth);tp0+=len(x&truth);tp1+=len(y&truth);gold+=len(truth);details.append({'case_id':r['case_id'],'lost':sorted(l),'gained':sorted(gg),'new_false_objects':sorted((y-truth)-x)})
    return {'cases':len(pp),'gold':gold,'baseline_tp':tp0,'current_tp':tp1,'baseline_fp':fp0,'current_fp':fp1,'lost_correct_objects':lost,'gained_correct_objects':gain,'details':details,'passed':lost==0 and fp1<=fp0 and not any(x['overflow'] for x in pp),'scope':'exposed_0122_graph_diagnostic_not_fresh'}

def save(path,values):P.save(path,{k:v.detach().cpu().contiguous().clone() for k,v in values.items()})
def seal(ctx,name,rr,gg):
    _,out,ident=ctx;d=out/name;imm(d/'prompts.json',rr);imm(d/'answers.SECRET.json',gg);imm(d/'seal.json',{'identity':digest(ident),'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json'),'cases':len(rr)})
def rows(ctx,name,answers=False):
    d=ctx[1]/name;s=read(d/'seal.json');need(s['identity']==digest(ctx[2]) and s['prompts']==sha(d/'prompts.json'),'SEAL_'+name);rr=read(d/'prompts.json');need(len(rr)==s['cases'],'COUNT');
    if not answers:return rr
    need(s['answers']==sha(d/'answers.SECRET.json'),'ANSWER_SEAL');gg=read(d/'answers.SECRET.json');need(len(rr)==len(gg) and all(r.get('id',r.get('case_id'))==g.get('id',g.get('case_id')) for r,g in zip(rr,gg)),'ALIGNMENT');return list(zip(rr,gg))
def prepare(ctx):
    from tools import nanojev_gamer_v1_compensatory_rewrite_author_0124 as A
    from tools import nanojev_gamer_v1_elementary_binding_author_0123 as B
    root,out,ident=ctx;need(not out.exists() or not any(out.iterdir()) or (out/'identity.json').is_file() and read(out/'identity.json')==ident or (out/'binding_fit'/'seal.json').is_file() and read(out/'binding_fit'/'seal.json')['identity']==digest(ident),'OUTPUT_NOT_OURS');old=root/'g1-g2-elementary-binding-0123';rr,gg=P.cohort((root,old,read(old/'identity.json')),'one',True);need(len(rr)==2,'PAIR');seal(ctx,'binding_fit',rr,gg)
    # Native preservation includes the exact 0123 pair and six independent 0120 calibration cases.
    cal=root/'g1-g2-backbone-competence-0120'/'calibration';ss=read(cal/'seal.json');need(ss['prompts']==sha(cal/'prompts.json') and ss['answers']==sha(cal/'answers.SECRET.json'),'CAL_SEAL');rp,gp=read(cal/'prompts.json'),read(cal/'answers.SECRET.json');need(len(rp)==len(gp)==16,'CAL_COUNT');
    # Divide by complete reversal pair, not individual cases.
    groups={}
    for r,g in zip(rp,gp):groups.setdefault(r['base_id'],[]).append((r,g))
    groups=list(groups.values());need(len(groups)==8 and all(len(x)==2 for x in groups),'CAL_PAIRS')
    for s,subset in zip(('fit','dev','blind'),(groups[:3],groups[3:5],groups[5:])):
        z=[x for group in subset for x in group];seal(ctx,'native_'+s,[x[0] for x in z],[x[1] for x in z])
    allrows=A.math_rows()+A.language_rows();seen=set()
    for s in ('fit','dev','blind'):
        for stage in (0,2,3,4):
            z=[(r,g) for r,g in allrows if r['stage']==stage and r['split']==s];need(z,'EMPTY_POPULATION');h={r['prompt'] for r,g in z};need(len(h)==len(z) and not seen&h,'PROMPT_COLLISION');seen|=h;seal(ctx,f'cards_{stage}_{s}',[r for r,g in z],[g for r,g in z])
    gr,ga=P.cohort(graph_context(root),'fresh_graph',True);seal(ctx,'graph_exposed',gr[:3],ga[:3]);imm(out/'identity.json',ident);emit('PREPARED',{'card_cases':len(allrows),'elementary':2,'native_calibration':16,'splits':{str(s):sum(r['stage']==s for r,g in allrows) for s in (0,2,3,4)}})
def load(a,ctx):
    from transformers import AutoModelForCausalLM,AutoTokenizer
    r=ctx[2]['model'];kw=dict(revision=r['commit'],trust_remote_code=False,local_files_only=True);tok=AutoTokenizer.from_pretrained(r['model_id'],use_fast=True,**kw);model=AutoModelForCausalLM.from_pretrained(r['model_id'],dtype=torch.float32,**kw).to(a.device).eval();need(not hasattr(model,'peft_config') and getattr(model.config,'_commit_hash',None)==r['commit'] and P.ph(tok.backend_tokenizer.to_str())==r['tokenizer_sha256'] and digest(tok.chat_template)==r['chat_template_sha256'] and model.config.hidden_size==1024,'MODEL_IDENTITY');need(all(p.dtype==torch.float32 for p in model.parameters()),'FP32_REQUIRED');model.requires_grad_(False);net=T.Reader(model.config.hidden_size,'E_attention',float(model.get_input_embeddings().weight.detach().square().mean().sqrt()),192).to(a.device);P.restore(net,None,ctx[0]/'g1-g2-elementary-binding-0123'/'one'/'reader.safetensors');net.eval();return model,tok,net

def prompts(r):
    if 'anchors' in r:return C.questions(r)[1]
    return [r['prompt']+'\nA: '+r['choices'][i]+'\nB: '+r['choices'][1-i]+'\nReply only A or B.' for i in (0,1)]
def target(r,g):return int(not g['positive'][0]) if 'anchors' in r else g['correct']
def tokens(tok,r):return [C.chat(tok,p) for p in prompts(r)]
def logits(model,ids,device):return C.forward_first(model,ids,device)[0].logits[0,-1].float()
def margin(z,labels):return torch.logsumexp(z[labels[0]],0)-torch.logsumexp(z[labels[1]],0)
def canonical(z,labels):return (margin(z[0],labels)-margin(z[1],labels))/2

def readout(model,net,r,device,pre):
    ids,keys,idx=pre;h=P.hidden(model,ids,net,device);return E.logits(net,r,(h,idx.to(device),keys))
def norm(v):return math.sqrt(sum(float(c.double().square().sum()) for x in v if x is not None for c in x.reshape(-1).split(1048576)))
def dot(a,b):return sum(float((u.double()*v.double()).sum()) for x,y in zip(a,b) if x is not None and y is not None for u,v in zip(x.reshape(-1).split(1048576),y.reshape(-1).split(1048576)))
def unit(v):
    n=norm(v);return [x.div_(n) if x is not None and n else x for x in v],n
def gradvec(ps):return [None if p.grad is None else p.grad.detach().cpu().clone() for p in ps]
def snapshot(ps):return [p.detach().cpu().clone() for p in ps]
@torch.no_grad()
def restore(ps,ss):
    for p,x in zip(ps,ss):p.copy_(x)
@torch.no_grad()
def move(ps,vs,alpha):
    for p,v in zip(ps,vs):
        if v is not None:p.add_(v.to(p),alpha=float(alpha))
def clear(ps):
    for p in ps:p.grad=None

def teacher_for(model,deck,device):
    with torch.no_grad():return [[logits(model,x,device).log_softmax(-1).cpu() for x in item['ids']] for item in deck]
def free_prompt(r):return r['prompt']+'\nReturn only the exact answer.'
def deck(tok,z):
    out=[]
    for r,g in z:
        d={'r':r,'g':g,'ids':tokens(tok,r)}
        if 'answer' in g and r.get('stage',0)>0:
            pre=C.chat(tok,free_prompt(r));enc=tok(str(g['answer']),add_special_tokens=False,return_tensors='pt')['input_ids'].reshape(-1);eos=getattr(tok,'eos_token_id',None)
            if isinstance(eos,int):enc=torch.cat((enc,torch.tensor([eos],dtype=torch.long)))
            need(len(enc)>0,'EMPTY_ANSWER');d['free_ids']=torch.cat((pre,enc[:-1][None,:]),1);d['free_targets']=enc
        out.append(d)
    return out
def free_loss(model,item,device):
    ids=item['free_ids'].to(device);y=item['free_targets'].to(device);kw=C.logits_kw(model);kw={k:len(y) for k in kw};z=model(input_ids=ids,attention_mask=torch.ones_like(ids),use_cache=False,**kw).logits[0,-len(y):].float();return F.cross_entropy(z,y)
def preservation_loss(model,deck,teacher,device,anchor=False):
    # Accumulate backward per prompt to avoid holding the whole corpus graph.
    n=2*len(deck);total=0.
    for item,ref in zip(deck,teacher):
        for ids,t in zip(item['ids'],ref):
            s=logits(model,ids,device).log_softmax(-1);t=t.to(s);v=F.kl_div(s,t,reduction='sum',log_target=True)
            if anchor:v=v-s[t.argmax()] # nonzero first-order preservation proxy at W0
            (v/n).backward();total+=float(v.detach())/n
    return total

def acquisition(model,net,stage,items,bind,device,labels,backward=False):
    total=0.;z=[]
    if stage==1:
        for r,g,pre in bind:
            v=readout(model,net,r,device,pre);loss=T.contrast_loss(v,g);total+=float(loss.detach())/len(bind);z.append(v.detach().cpu().tolist());
            if backward:(loss/len(bind)).backward()
        rr=[r for r,g,pre in bind];gg=[g for r,g,pre in bind];m=E.stats(rr,gg,z);return total,{'correct':m['both_bindings_correct'],'cases':len(bind),'margin':m['min_all_competitor_margin'],'reversals':m['reversal_pairs_correct']}
    for item in items:
        g=target(item['r'],item['g']);sign=1 if g==0 else -1;ms=[]
        # Separate backward on two presentation orders; balanced label training.
        for i,ids in enumerate(item['ids']):
            v=logits(model,ids,device);m=margin(v,labels)*sign*(1 if i==0 else -1);loss=F.softplus(-m);total+=float(loss.detach())/(2*len(items));ms.append(float(m.detach()));
            if backward:(loss/(2*len(items))).backward()
        loss=free_loss(model,item,device);total+=float(loss.detach())/len(items)
        if backward:(loss/len(items)).backward()
        z.append(sum(ms)/2)
    return total,{'correct':sum(x>0 for x in z),'cases':len(z),'margin':min(z),'mean_margin':sum(z)/len(z)}

@torch.no_grad()
def measure(model,net,stage,items,bind,preserve,teacher,device,labels):
    loss,acq=acquisition(model,net,stage,items,bind,device,labels);drifts=[];lost=[];gained=[];rms=[];scores=[]
    for item,ref in zip(preserve,teacher):
        zs=[logits(model,x,device).log_softmax(-1) for x in item['ids']];ts=[x.to(zs[0]) for x in ref];kls=[max(0.,float(F.kl_div(s,t,reduction='sum',log_target=True))) for s,t in zip(zs,ts)];drifts.extend(kls);sign=1 if target(item['r'],item['g'])==0 else -1;b=float(canonical(ts,labels))*sign;c=float(canonical(zs,labels))*sign;key=item['r'].get('id',item['r'].get('case_id'));scores.append({'id':key,'baseline_margin':b,'current_margin':c,'kl':sum(kls)/2,'family':item['r'].get('family','native_binding')});
        if b>0 and c<=0:lost.append(key)
        if b<=0 and c>0:gained.append(key)
        rms.extend(float((s-t).square().mean().sqrt()) for s,t in zip(zs,ts))
    return {'loss':loss,'acquisition':acq,'preservation':{'mean_kl':sum(drifts)/len(drifts),'max_kl':max(drifts),'logprob_rms':sum(rms)/len(rms),'lost':lost,'gained':gained,'cases':len(preserve),'scores':scores}}

def passed(v,a,stage,threshold=1.):
    m=v['acquisition'];p=v['preservation'];return m['correct']/m['cases']>=threshold and (stage!=1 or m['margin']>=.1) and not p['lost'] and p['mean_kl']<=a.max_kl

def scorekey(v,a,stage):
    p=v['preservation'];m=v['acquisition'];safe=not p['lost'] and p['mean_kl']<=a.max_kl;return (safe,m['correct']/m['cases'],m['margin'] if stage==1 else -v['loss'],-p['mean_kl'])

@torch.no_grad()
def residual(model,preserve,teacher,device):
    # Fisher-weighted full vocabulary log-probability residual, no top-k truncation.
    x=[]
    for item,ref in zip(preserve,teacher):
        for ids,t in zip(item['ids'],ref):
            s=logits(model,ids,device).log_softmax(-1).cpu();x.append((s-t)*torch.exp(t/2))
    return torch.cat(x)/math.sqrt(len(x))
def random_direction(ps,seed):
    gen=torch.Generator().manual_seed(seed);n=math.sqrt(sum(p.numel() for p in ps));return [torch.empty(p.shape,dtype=torch.float32).bernoulli_(.5,generator=gen).mul_(2).sub_(1).div_(n) for p in ps]

def fuzz_repair(model,net,ps,stage,items,bind,preserve,teacher,device,labels,radius,seed,a):
    # Matrix-free directional finite differences with exact restoration between probes.
    clear(ps);preservation_loss(model,preserve,teacher,device);u,ng=unit(gradvec(ps));clear(ps);acquisition(model,net,stage,items,bind,device,labels,True);v,na=unit(gradvec(ps));clear(ps);clear(list(net.parameters()));dirs=[x for x,n in ((u,ng),(v,na)) if n>1e-12];dirs.append(random_direction(ps,seed));ss=snapshot(ps);e=residual(model,preserve,teacher,device);cols=[];h=radius*.2
    for d in dirs:
        move(ps,d,h);rp=residual(model,preserve,teacher,device);restore(ps,ss);move(ps,d,-h);rm=residual(model,preserve,teacher,device);restore(ps,ss);cols.append((rp-rm)/(2*h))
    J=torch.stack(cols,1).double();G=J.T@J;reg=max(1e-12,float(G.diag().mean())*1e-3);coef=torch.linalg.solve(G+reg*torch.eye(len(dirs),dtype=torch.float64),-J.T@e.double());coef=coef.clamp(-radius,radius);before=measure(model,net,stage,items,bind,preserve,teacher,device,labels);best=before;chosen=0.
    # Nonlinear trust-region line search, preserving fitted acquisition count and not increasing loss materially.
    for f in (1.,.5,.25):
        for d,c in zip(dirs,coef):move(ps,d,float(c)*f)
        q=measure(model,net,stage,items,bind,preserve,teacher,device,labels);restore(ps,ss)
        if q['acquisition']['correct']>=before['acquisition']['correct'] and (stage!=1 or before['acquisition']['margin']<.1 or q['acquisition']['margin']>=.1) and q['loss']<=before['loss']+.03 and (len(q['preservation']['lost']),q['preservation']['mean_kl'])<(len(best['preservation']['lost']),best['preservation']['mean_kl']):best=q;chosen=f
    if chosen:
        for d,c in zip(dirs,coef):move(ps,d,float(c)*chosen)
    return {'method':'full_weight_directional_fd','directions':len(dirs),'full_weight_dimensions':sum(p.numel() for p in ps),'sampled_not_exhaustive':True,'radius':h,'coefficients':coef.tolist(),'selected_scale':chosen,'residual_norm':float(e.norm()),'linear_prediction_norm':float((e.double()+J@coef*chosen).norm()),'actual_residual_norm_after':float(residual(model,preserve,teacher,device).norm()),'before':before,'after':best}

def all_names():return ['binding_fit']+[f'native_{s}' for s in ('fit','dev','blind')]+[f'cards_{k}_{s}' for k in (0,2,3,4) for s in ('fit','dev','blind')]
def atomic_pt(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=p.parent,suffix='.tmp');os.close(fd)
    try:torch.save(obj,tmp);os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def teacher_path(ctx,name):return ctx[1]/'reference'/f'{name}.pt'
def ref_lock(ctx):
    d=ctx[1]/'reference';return {'identity':digest(ctx[2]),'decks':{s:{'seal':sha(ctx[1]/s/'seal.json'),'teacher':sha(teacher_path(ctx,s))} for s in all_names()},'labels':sha(d/'labels.json'),'activation':sha(d/'activation.json'),'g1':sha(d/'g1.json'),'graph_seal':sha(ctx[1]/'graph_exposed'/'seal.json'),'free_math':{s:sha(d/(s+'-free.json')) for s in all_names() if s.startswith(('cards_2_','cards_3_','cards_4_')) and not s.endswith('_fit')}}
def validate_reference(ctx):need(read(ctx[1]/'reference'/'lock.json')==ref_lock(ctx),'REFERENCE_DRIFT')
def get_teacher(ctx,s):return torch.load(teacher_path(ctx,s),map_location='cpu',weights_only=True)

@torch.no_grad()
def activation_fuzz(model,tok,rr,device,seed):
    # Capture both prompt activations at all hidden layers, all coordinates.
    ids=[C.chat(tok,r['prompt']).to(device) for r in rr];hs=[]
    for x in ids:
        h=model.model(input_ids=x,attention_mask=torch.ones_like(x),output_hidden_states=True,use_cache=False,return_dict=True).hidden_states;hs.append([v[0].float().cpu() for v in h])
    report=[];q=tokens(tok,rr[0])[0] if 'anchors' in rr[0] else C.chat(tok,rr[0]['prompt']);base=logits(model,q,device).cpu();gen=torch.Generator().manual_seed(seed)
    for i,(x,y) in enumerate(zip(*hs)):
        report.append({'hidden_layer':i,'dimensions':x.shape[-1],'last_rms_delta':float((x[-1]-y[-1]).square().mean().sqrt()),'last_coordinate_deltas':(x[-1]-y[-1]).tolist(),'aligned_coordinate_rms':(x-y).square().mean(0).sqrt().tolist() if x.shape==y.shape else None,'all_aligned_rms_delta':float((x-y).square().mean().sqrt()) if x.shape==y.shape else None})
    for i,layer in enumerate(model.model.layers):
        direction=torch.randn(hs[0][0].shape[-1],generator=gen);direction/=direction.square().mean().sqrt()
        def hook(mod,args,out):
            z=out[0] if isinstance(out,tuple) else out;v=z.clone();v[:,-1,:]+=direction.to(v)*z[:,-1,:].float().square().mean().sqrt()*1e-3;return (v,*out[1:]) if isinstance(out,tuple) else v
        handle=layer.register_forward_hook(hook)
        try:current=logits(model,q,device).cpu()
        finally:handle.remove()
        report[i+1]['last_token_directional_logit_rms']=float((current-base).square().mean().sqrt())
    need(torch.equal(base,logits(model,q,device).cpu()),'ACTIVATION_HOOK_RESTORE');return {'prompts':[r['prompt'] for r in rr],'layers':report,'direction_per_layer':1,'relative_rms':.001,'reference_restored':True,'scope':'full_width_directional_probe_not_coordinate_localization'}
def reference(a,ctx):
    out=ctx[1];d=out/'reference';lock=d/'lock.json'
    if lock.exists():validate_reference(ctx);emit('REFERENCE_REUSED',{});return
    C.guard();model,tok,net=load(a,ctx);imm(d/'labels.json',C.label_ids(tok,('A','B'),True));need(not any(p.requires_grad for p in model.parameters()),'REFERENCE_TRAINABLE');
    for s in all_names():
        rr=rows(ctx,s);items=[{'r':r,'ids':tokens(tok,r)} for r in rr];path=teacher_path(ctx,s);header=path.with_suffix('.json')
        if header.exists():need(read(header)=={'identity':digest(ctx[2]),'seal':sha(out/s/'seal.json'),'sha':sha(path)},'REFERENCE_SHARD_DRIFT');continue
        atomic_pt(path,teacher_for(model,items,a.device));imm(header,{'identity':digest(ctx[2]),'seal':sha(out/s/'seal.json'),'sha':sha(path)});emit('REFERENCE_DECK',{'name':s,'cases':len(rr)})
    for name in all_names():
        if not name.startswith(('cards_2_','cards_3_','cards_4_')) or name.endswith('_fit'):continue
        path=d/(name+'-free.json')
        if path.exists():continue
        with torch.no_grad():v=[{'id':r['id'],'generation':C.generate(model,tok,free_prompt(r),a.device,None,20)} for r in rows(ctx,name)]
        imm(path,v);emit('REFERENCE_GENERATION',{'split':name,'cases':len(v)})
    v={'binding':activation_fuzz(model,tok,rows(ctx,'binding_fit'),a.device,a.seed),'math':activation_fuzz(model,tok,[{'prompt':f'Calculate exactly: (1234) + (-1234 + ({x})).'} for x in (0,1)],a.device,a.seed+1),'language':activation_fuzz(model,tok,[{'prompt':f'Nora put the key {x} the box. Where is the key?'} for x in ('inside','outside')],a.device,a.seed+2)};imm(d/'activation.json',v);gnet,gt=graph_reader(a,ctx,model);imm(d/'g1.json',graph_preds(a,model,tok,gnet,gt,rows(ctx,'graph_exposed')));imm(lock,ref_lock(ctx));emit('REFERENCE_LOCKED',{'decks':len(all_names()),'layers':len(v['binding']['layers'])})

def bind_deck(ctx,tok,answers=True):
    if answers:return [(r,g,P.token_prepare(tok,r['prompt'],16)) for r,g in rows(ctx,'binding_fit',True)]
    return [(r,None,P.token_prepare(tok,r['prompt'],16)) for r in rows(ctx,'binding_fit')]
def preserved(ctx,tok,split,labels,stage):
    items=[];refs=[]
    names=['binding_fit','native_'+split,'cards_0_'+split]+[f'cards_{k}_{split}' for k in range(2,stage+1)]
    for name in names:
        ds=deck(tok,rows(ctx,name,True));ts=get_teacher(ctx,name)
        for item,t in zip(ds,ts):
            # Preserve all language and native distributions, and initially correct math.
            if name.startswith('cards_') and (not name.startswith('cards_0_') or item['r'].get('family','').startswith('preserve_math_')):
                sign=1 if target(item['r'],item['g'])==0 else -1
                if float(canonical(t,labels))*sign<=0:continue
            items.append(item);refs.append(t)
    return items,refs

def repair_gradient(model,net,ps,stage,items,bind,preserve,teacher,device,labels,radius):
    clear(ps);loss=preservation_loss(model,preserve,teacher,device);v,n=unit(gradvec(ps));clear(ps);before=measure(model,net,stage,items,bind,preserve,teacher,device,labels);best=before;chosen=0.
    if n>1e-12:
        ss=snapshot(ps)
        for f in (1.,.5,.25):
            move(ps,v,-radius*f);q=measure(model,net,stage,items,bind,preserve,teacher,device,labels);restore(ps,ss)
            if q['acquisition']['correct']>=before['acquisition']['correct'] and (stage!=1 or before['acquisition']['margin']<.1 or q['acquisition']['margin']>=.1) and q['loss']<=before['loss']+.03 and (len(q['preservation']['lost']),q['preservation']['mean_kl'])<(len(best['preservation']['lost']),best['preservation']['mean_kl']):best=q;chosen=f
        if chosen:move(ps,v,-radius*chosen)
    return {'method':'sequential_kl_gradient','gradient_norm':n,'selected_scale':chosen,'before':before,'after':best}

def update(a,model,net,ps,arm,stage,items,bind,preserve,teacher,labels,opt):
    clear(ps);clear(list(net.parameters()));loss,_=acquisition(model,net,stage,items,bind,a.device,labels,True);projection=None
    if arm==ARMS[2]:preservation_loss(model,preserve,teacher,a.device)
    if ps:
        v,n=unit(gradvec(ps));clear(ps)
        if arm==ARMS[4]:
            preservation_loss(model,preserve,teacher,a.device,anchor=True);u,np=unit(gradvec(ps));clear(ps);d=dot(v,u)
            if np>1e-12 and d<0: # -v would increase preservation proxy
                for i,(x,y) in enumerate(zip(v,u)):
                    if y is not None:
                        if x is None:v[i]=y.mul(-d)
                        else:x.add_(y,alpha=-d)
                v,nproj=unit(v)
            else:nproj=1.
            projection={'acquisition_gradient_norm':n,'preservation_proxy_gradient_norm':np,'cosine':d,'projected_norm':nproj,'scope':'one_aggregate_anchor_nll_plus_kl_gradient'}
            del u
        need(math.isfinite(n),'NONFINITE_GRADIENT');move(ps,v,-a.radius);del v
    else:n=0.
    if opt is not None:
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.);opt.step()
    clear(ps);clear(list(net.parameters()));return {'loss_before':loss,'weight_gradient_norm':n,'projection':projection}

def gate_dir(ctx,arm,stage):return ctx[1]/'arms'/arm/f'gate-{stage}'
def verify_fit(ctx,arm,stage):
    d=gate_dir(ctx,arm,stage);r=read(d/'fit.json');need(r['identity']==digest(ctx[2]) and r['checkpoint']==sha(d/'checkpoint.pt') and r['reference']==sha(ctx[1]/'reference'/'lock.json'),'FIT_DRIFT');return r
def verify_stage(ctx,arm,stage):
    d=gate_dir(ctx,arm,stage);r=read(d/'result.json');f=verify_fit(ctx,arm,stage);need(r['identity']==digest(ctx[2]) and r['fit']==sha(d/'fit.json') and r['prediction_lock']==sha(d/'prediction-lock.json') and read(d/'prediction-lock.json')==prediction_lock(ctx,arm,stage),'RESULT_DRIFT');return r
def load_checkpoint(model,net,ctx,arm,stage):
    verify_fit(ctx,arm,stage);d=torch.load(gate_dir(ctx,arm,stage)/'checkpoint.pt',map_location='cpu',weights_only=True)
    if arm!=ARMS[0]:model.load_state_dict(d['model'],strict=True)
    else:need('model' not in d,'REFERENCE_MODEL_WRITTEN')
    net.load_state_dict(d['reader'],strict=True);del d

def bind_pass(m):return m['correct']==2 and m['margin']>=.1

def fit_stage(a,ctx,arm,stage):
    d=gate_dir(ctx,arm,stage);validate_reference(ctx)
    if stage>2:need(verify_stage(ctx,arm,stage-1)['passed'],'PREDECESSOR_GATE')
    if (d/'fit.json').exists():verify_fit(ctx,arm,stage);emit('FIT_REUSED',{'arm':arm,'stage':stage});return
    model,tok,net=load(a,ctx)
    if stage>2:load_checkpoint(model,net,ctx,arm,stage-1)
    labels=C.label_ids(tok,('A','B'),True);bind=bind_deck(ctx,tok);allps=list(model.parameters());ps=allps if arm!=ARMS[0] else [];model.requires_grad_(bool(ps));net.requires_grad_(stage==1);need(all(p.requires_grad==(arm!=ARMS[0]) for p in allps),'THAW');opt=torch.optim.AdamW(net.parameters(),lr=a.reader_lr,weight_decay=0.) if stage==1 else None
    preserve,teacher=preserved(ctx,tok,'fit',labels,stage);dp,dt=preserved(ctx,tok,'dev',labels,stage)
    train=deck(tok,[z for k in range(2,stage+1) for z in rows(ctx,f'cards_{k}_fit',True)]) if stage>1 else []
    val=deck(tok,rows(ctx,f'cards_{stage}_dev',True)) if stage>1 else []
    # Matched cumulative mathematical acquisition examples. Preserve original correct
    # math even if included in acquisition; conflicts are explicitly measurable.
    initial=measure(model,net,stage,train,bind,preserve,teacher,a.device,labels);initial_dev=measure(model,net,stage,val,bind,dp,dt,a.device,labels);history=[];repairs=[]
    for step in range(1,a.steps+1):
        if arm==ARMS[0] and stage>1:break
        info=update(a,model,net,ps,arm,stage,train,bind,preserve,teacher,labels,opt);q=measure(model,net,stage,train,bind,preserve,teacher,a.device,labels);history.append({'step':step,'update':info,'metrics':q});emit('UPDATE',{'arm':arm,'stage':stage,'step':step,'loss':q['loss'],'correct':q['acquisition']['correct'],'cases':q['acquisition']['cases'],'kl':q['preservation']['mean_kl'],'lost':len(q['preservation']['lost'])})
        if arm==ARMS[6] and (step%max(1,a.steps//3)==0 or step==a.steps):
            r=fuzz_repair(model,net,ps,stage,train,bind,preserve,teacher,a.device,labels,a.radius,a.seed+stage*100+step,a);repairs.append(r);emit('REPAIR',{'arm':arm,'stage':stage,'step':step,'before_kl':r['before']['preservation']['mean_kl'],'after_kl':r['after']['preservation']['mean_kl'],'scale':r['selected_scale']})
    after_acq=measure(model,net,stage,train,bind,preserve,teacher,a.device,labels)
    if arm in (ARMS[3],ARMS[5]):
        for i in range(a.repair_steps):
            if arm==ARMS[3]:r=repair_gradient(model,net,ps,stage,train,bind,preserve,teacher,a.device,labels,a.radius)
            else:r=fuzz_repair(model,net,ps,stage,train,bind,preserve,teacher,a.device,labels,a.radius,a.seed+stage*100+i,a)
            repairs.append(r);emit('REPAIR',{'arm':arm,'stage':stage,'before_kl':r['before']['preservation']['mean_kl'],'after_kl':r['after']['preservation']['mean_kl'],'scale':r['selected_scale']})
    final=measure(model,net,stage,train,bind,preserve,teacher,a.device,labels);dev=measure(model,net,stage,val,bind,dp,dt,a.device,labels)
    with torch.no_grad():_,ret=acquisition(model,net,1,[],bind,a.device,labels)
    # Fixed end-of-budget candidate; no held-out checkpoint selection.
    devfree=None
    if stage>1:
        with torch.no_grad():gen=[{'id':x['r']['id'],'generation':C.generate(model,tok,free_prompt(x['r']),a.device,None,20)} for x in val]
        devfree=grade_free(val,gen,read(ctx[1]/'reference'/f'cards_{stage}_dev-free.json'))
    eligible=passed(dev,a,stage,1. if stage==1 else .8) and (bind_pass(ret) if stage==1 else devfree['current_correct']/devfree['cases']>=.6 and not devfree['lost']);model.requires_grad_(False);net.requires_grad_(False);clear(allps);clear(list(net.parameters()));ck={'reader':net.state_dict()}
    if ps:ck['model']=model.state_dict()
    atomic_pt(d/'checkpoint.pt',ck);rec={'identity':digest(ctx[2]),'reference':sha(ctx[1]/'reference'/'lock.json'),'checkpoint':sha(d/'checkpoint.pt'),'arm':arm,'stage':stage,'backbone_parameters':sum(p.numel() for p in allps),'eligible_weight_dimensions':sum(p.numel() for p in ps),'initial':initial,'initial_dev':initial_dev,'history':history,'after_acquisition':after_acq,'repairs':repairs,'final':final,'dev':dev,'binding_retention':ret,'dev_free_generation':devfree,'validation_passed':eligible,'elapsed_training_updates':len(history),'repair_attempts':len(repairs)};imm(d/'fit.json',rec);emit('FIT',{'arm':arm,'stage':stage,'dev':dev['acquisition'],'preservation_kl':dev['preservation']['mean_kl'],'validation_passed':eligible})

def blind_names(stage):return ['binding_fit','native_blind','cards_0_blind']+[f'cards_{k}_blind' for k in range(2,stage+1)]
def prediction_lock(ctx,arm,stage):
    d=gate_dir(ctx,arm,stage);return {'identity':digest(ctx[2]),'fit':sha(d/'fit.json'),'checkpoint':sha(d/'checkpoint.pt'),'reference':sha(ctx[1]/'reference'/'lock.json'),'predictions':sha(d/'predictions.json'),'seals':{n:sha(ctx[1]/n/'seal.json') for n in blind_names(stage)+['graph_exposed']}}
@torch.no_grad()
def predict(a,ctx,arm,stage):
    validate_reference(ctx);verify_fit(ctx,arm,stage);d=gate_dir(ctx,arm,stage)
    if (d/'prediction-lock.json').exists():need(read(d/'prediction-lock.json')==prediction_lock(ctx,arm,stage),'PREDICTION_DRIFT');return
    C.guard();model,tok,net=load(a,ctx);load_checkpoint(model,net,ctx,arm,stage);labels=C.label_ids(tok,('A','B'),True);out={}
    for name in blind_names(stage):
        rr=rows(ctx,name);tt=get_teacher(ctx,name);items=[]
        for r,ref in zip(rr,tt):
            zs=[logits(model,x,a.device).log_softmax(-1) for x in tokens(tok,r)];ts=[t.to(zs[0]) for t in ref];v={'id':r.get('id',r.get('case_id')),'reference_margin':float(canonical(ts,labels)),'margin':float(canonical(zs,labels)),'reference_orders':[float(margin(x,labels)) for x in ts],'orders':[float(margin(x,labels)) for x in zs],'kl':[max(0.,float(F.kl_div(s,t,reduction='sum',log_target=True))) for s,t in zip(zs,ts)],'answer_mass':[float(torch.cat([s[labels[0]],s[labels[1]]]).exp().sum()) for s in zs]}
            if name=='binding_fit':v['reader_scores']=readout(model,net,r,a.device,P.token_prepare(tok,r['prompt'],16)).cpu().tolist()
            # Avoid generating on later mathematical gates before they are reached.
            if name.startswith(('cards_2_','cards_3_','cards_4_')):v['generation']=C.generate(model,tok,free_prompt(r),a.device,None,20)
            items.append(v)
        out[name]=items;emit('PREDICT',{'arm':arm,'stage':stage,'split':name,'cases':len(items)})
    gnet,gt=graph_reader(a,ctx,model);out['graph_exposed']=graph_preds(a,model,tok,gnet,gt,rows(ctx,'graph_exposed'));imm(d/'predictions.json',out);imm(d/'prediction-lock.json',prediction_lock(ctx,arm,stage))

def exact_generated(text,answer):
    from fractions import Fraction
    x=text.rsplit('</think>',1)[-1].strip();y=str(answer).strip()
    if x==y:return True
    try:return Fraction(x)==Fraction(y)
    except (ValueError,ZeroDivisionError):return False

def grade_free(items,current,reference):
    need(len(items)==len(current)==len(reference),'FREE_COUNT');details=[]
    for item,c,b in zip(items,current,reference):
        need(item['r']['id']==c['id']==b['id'],'FREE_ALIGN');answer=item['g']['answer'];x=exact_generated(b['generation']['text'],answer);y=exact_generated(c['generation']['text'],answer);details.append({'id':c['id'],'baseline_correct':x,'correct':y,'baseline':b['generation'],'current':c['generation']})
    return {'cases':len(details),'baseline_correct':sum(x['baseline_correct'] for x in details),'current_correct':sum(x['correct'] for x in details),'lost':[x['id'] for x in details if x['baseline_correct'] and not x['correct']],'gained':[x['id'] for x in details if not x['baseline_correct'] and x['correct']],'details':details}
def score(a,ctx,arm,stage):
    validate_reference(ctx);d=gate_dir(ctx,arm,stage);f=verify_fit(ctx,arm,stage);need(read(d/'prediction-lock.json')==prediction_lock(ctx,arm,stage),'PREDICTION_DRIFT');pred=read(d/'predictions.json');cohorts={};pres=[]
    for name in blind_names(stage):
        pairs=rows(ctx,name,True);pp=pred[name];need(len(pairs)==len(pp),'PRED_COUNT');details=[]
        for (r,g),p in zip(pairs,pp):
            need(p['id']==r.get('id',r.get('case_id')),'PRED_ALIGN');sign=1 if target(r,g)==0 else -1;b=p['reference_margin']*sign>0;c=p['margin']*sign>0;row={**p,'baseline_correct':b,'correct':c,'family':r.get('family','native_binding')};
            if 'generation' in p:row['generation_exact']=exact_generated(p['generation']['text'],g['answer'])
            details.append(row)
            if name in ('binding_fit','native_blind') or (name=='cards_0_blind' and not row['family'].startswith('preserve_math_')) or b:pres.append(row)
        cohorts[name]={'cases':len(details),'baseline_correct':sum(x['baseline_correct'] for x in details),'correct':sum(x['correct'] for x in details),'lost':sum(x['baseline_correct'] and not x['correct'] for x in details),'gained':sum(not x['baseline_correct'] and x['correct'] for x in details),'both_orders_correct':sum((x['orders'][0]>0 and x['orders'][1]<0) if x['margin']>0 else (x['orders'][0]<0 and x['orders'][1]>0) for x in details if x['correct']),'mean_kl':sum(sum(x['kl']) for x in details)/(2*len(details)),'generation_exact':sum(x.get('generation_exact',False) for x in details) if any('generation' in x for x in details) else None,'details':details}
    for k in range(2,stage+1):
        name=f'cards_{k}_blind';cohorts[name]['free_generation']=grade_free([{'r':r,'g':g} for r,g in rows(ctx,name,True)],pred[name],read(ctx[1]/'reference'/(name+'-free.json')))
    z=pred['binding_fit'];pairs=rows(ctx,'binding_fit',True);m=E.stats([r for r,g in pairs],[g for r,g in pairs],[p['reader_scores'] for p in z]);ret={'correct':m['both_bindings_correct'],'cases':2,'margin':m['min_all_competitor_margin'],'reversals':m['reversal_pairs_correct']};kl=sum(sum(x['kl']) for x in pres)/(2*len(pres));lost=[x['id'] for x in pres if x['baseline_correct'] and not x['correct']];prior_lost=[]
    if stage>2:
        old=verify_stage(ctx,arm,stage-1)['cohorts']
        for k in range(2,stage):
            name=f'cards_{k}_blind';prior_lost.extend(x['id'] for x,y in zip(old[name]['details'],cohorts[name]['details']) if x['correct'] and not y['correct']);prior_lost.extend(x['id'] for x,y in zip(old[name]['free_generation']['details'],cohorts[name]['free_generation']['details']) if x['correct'] and not y['correct'])
    acq=ret if stage==1 else cohorts[f'cards_{stage}_blind'];g1=graph_review(ctx,pred['graph_exposed']);ok=g1['passed'] and f['validation_passed'] and (bind_pass(ret) if stage==1 else acq['free_generation']['current_correct']/acq['cases']>=.6 and not any(cohorts[f'cards_{k}_blind']['free_generation']['lost'] for k in range(2,stage+1))) and acq['correct']/acq['cases']>=(1. if stage==1 else .8) and not lost and not prior_lost and kl<=a.max_kl
    v={'identity':digest(ctx[2]),'fit':sha(d/'fit.json'),'prediction_lock':sha(d/'prediction-lock.json'),'arm':arm,'stage':stage,'passed':ok,'acquisition':{k:v if k!='free_generation' else {a:b for a,b in v.items() if a!='details'} for k,v in acq.items() if k!='details'},'binding_retention':ret,'blind_preservation':{'mean_kl':kl,'lost':lost,'prior_acquisition_lost':prior_lost,'cases':len(pres)},'cohorts':cohorts,'g1_preservation':g1,'certification':False};imm(d/'result.json',v);emit('GATE',{'arm':arm,'stage':stage,'passed':ok,'acquisition':v['acquisition'],'blind_preservation':v['blind_preservation']})

def finish(ctx):
    validate_reference(ctx);report={}
    for arm in ARMS:
        stages={}
        for stage in (1,2,3,4):
            d=gate_dir(ctx,arm,stage)
            if not (d/'result.json').exists():break
            v=verify_stage(ctx,arm,stage);f=verify_fit(ctx,arm,stage);r=f['repairs'];repairs=[]
            for x in r:
                b=x['before'];c=x['after'];repairs.append({'method':x['method'],'kl_before':b['preservation']['mean_kl'],'kl_after':c['preservation']['mean_kl'],'restored_ids':sorted(set(b['preservation']['lost'])-set(c['preservation']['lost'])),'newly_lost_ids':sorted(set(c['preservation']['lost'])-set(b['preservation']['lost'])),'acquisition_before':b['acquisition'],'acquisition_after':c['acquisition'],'scale':x['selected_scale']})
            stages[str(stage)]={'passed':v['passed'],'acquisition':v['acquisition'],'validation':f['dev']['acquisition'],'blind_preservation':v['blind_preservation'],'g1_preservation':{k:x for k,x in v['g1_preservation'].items() if k!='details'},'repairs':repairs,'checkpoint':f['checkpoint'],'weight_dimensions':f['eligible_weight_dimensions'],'training_updates':f['elapsed_training_updates'],'repair_attempts':f['repair_attempts']}
            if stage>1 and not v['passed']:break
        need('1' in stages and '2' in stages and (not stages['2']['passed'] or '3' in stages) and ('3' not in stages or not stages['3']['passed'] or '4' in stages),'ARM_INCOMPLETE_'+arm);report[arm]=stages
    labels=read(ctx[1]/'reference'/'labels.json');baseline={}
    for stage in (2,3,4):
        name=f'cards_{stage}_blind';rs=rows(ctx,name,True);tt=get_teacher(ctx,name);gg=read(ctx[1]/'reference'/(name+'-free.json'));m=[float(canonical(t,labels))*(1 if target(r,g)==0 else -1) for (r,g),t in zip(rs,tt)];free=grade_free([{'r':r,'g':g} for r,g in rs],gg,gg);baseline[str(stage)]={'cases':len(rs),'choice_correct':sum(x>0 for x in m),'free_exact':free['baseline_correct']}
    v={'identity':digest(ctx[2]),'reference':sha(ctx[1]/'reference'/'lock.json'),'reference_math':baseline,'arms':report,'status':'RESEARCH_REVIEW_REQUIRED','certification':False,'production_modified':False};imm(ctx[1]/'result.json',v);emit('COMPLETE',v)

def lifecycle_fixture(z,tok):
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_elementary_binding_author_0123 as B
    import contextlib,io
    a=SimpleNamespace(device='cpu',radius=.05,max_kl=.1,reader_lr=.001,steps=1,repair_steps=1,seed=SEED)
    def load_fixture(a,ctx):
        torch.manual_seed(SEED);m=P.fixture_backbone();n=T.Reader(8,'E_attention',.1,16);return m,tok,n
    def gpred(a,m,t,n,threshold,rr):return [{'case_id':r['case_id'],'objects':[],'overflow':False} for r in rr]
    def gen(*args,**kw):return {'text':'0','token_count':1,'stopped':True,'limit':20,'thinking':False}
    old=(globals()['load'],globals()['graph_reader'],globals()['graph_preds'],globals()['activation_fuzz'],C.guard,C.label_ids,C.generate)
    try:
        globals()['load']=load_fixture;globals()['graph_reader']=lambda *args:(None,0.);globals()['graph_preds']=gpred;globals()['activation_fuzz']=lambda *args:{'layers':[{'fixture':True}]};C.guard=lambda:None;C.label_ids=lambda *args:[[2],[3]];C.generate=gen
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            ctx=(Path(tmp),Path(tmp)/'out',{'fixture':True});b=B.rows('one')
            for name in all_names():
                if name.startswith(('binding','native')):q=b
                else:
                    _,k,split=name.split('_');q=[x for x in z if x[0]['stage']==int(k) and x[0]['split']==split][:2]
                seal(ctx,name,[r for r,g in q],[g for r,g in q])
            seal(ctx,'graph_exposed',[{'case_id':'fixture','prompt':'plain text'}],[{'case_id':'fixture','graph':{'nodes':[],'edges':[]}}]);reference(a,ctx);reference(a,ctx)
            for stage in (1,2):
                arm=ARMS[3];fit_stage(a,ctx,arm,stage);fit_stage(a,ctx,arm,stage);predict(a,ctx,arm,stage);predict(a,ctx,arm,stage);score(a,ctx,arm,stage);verify_stage(ctx,arm,stage)
            d=gate_dir(ctx,ARMS[3],2);(d/'predictions.json').write_text('{}')
            try:verify_stage(ctx,ARMS[3],2)
            except RuntimeError:pass
            else:raise AssertionError('PRED_TAMPER_ACCEPTED')
    finally:globals()['load'],globals()['graph_reader'],globals()['graph_preds'],globals()['activation_fuzz'],C.guard,C.label_ids,C.generate=old

def selftest():
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_compensatory_rewrite_author_0124 as A
    torch.set_num_threads(1);torch.manual_seed(SEED);z=A.math_rows()+A.language_rows();need(len(z)==231 and len({r['prompt'] for r,g in z})==len(z),'CARD_FIXTURE_COUNT');need(all(r['choices'][g['correct']]==g['answer'] for r,g in z),'CARD_ORACLE_ALIGNMENT')
    from tools import nanojev_gamer_v1_elementary_binding_author_0123 as B
    import re,contextlib,io
    class Tok:
        def apply_chat_template(self,m,**kw):return m[0]['content']
        def __call__(self,text,**kw):
            spans=[m.span() for m in re.finditer(r'\w+|[^\w\s]',text)];ids=[sum(text[x:y].encode())%32 for x,y in spans];return {'input_ids':torch.tensor([ids]),'offset_mapping':torch.tensor([spans])}
    tok=Tok();a=SimpleNamespace(device='cpu',radius=.05,max_kl=.1,reader_lr=.001,seed=SEED);labels=([2],[3]);m=P.fixture_backbone();initial=snapshot(list(m.parameters()));net=T.Reader(8,'E_attention',.1,16);ns={k:v.clone() for k,v in net.state_dict().items()};b=[(r,g,P.token_prepare(tok,r['prompt'],16)) for r,g in B.rows('one')];p=deck(tok,[x for x in z if x[0]['stage']==0 and x[0]['split']=='fit'][:2]);t=teacher_for(m,p,'cpu');items=deck(tok,[x for x in z if x[0]['stage']==2 and x[0]['split']=='fit'][:2]);results={}
    for arm in ARMS:
        restore(list(m.parameters()),initial);net.load_state_dict(ns);m.requires_grad_(arm!=ARMS[0]);net.requires_grad_(True);ps=list(m.parameters()) if arm!=ARMS[0] else [];opt=torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=0);q=update(a,m,net,ps,arm,1,[],b,p,t,labels,opt);need(math.isfinite(q['loss_before']),'FIXTURE_UPDATE');v=measure(m,net,1,[],b,p,t,'cpu',labels);need(math.isfinite(v['loss']),'FIXTURE_MEASURE')
        if arm==ARMS[0]:need(all(torch.equal(x,y) for x,y in zip(initial,snapshot(list(m.parameters())))),'FROZEN_CHANGED')
        else:
            need(any(not torch.equal(x,y) for x,y in zip(initial,snapshot(list(m.parameters())))),'FULL_WEIGHT_UPDATE_NOT_OBSERVED');net.requires_grad_(False);q2=update(a,m,net,ps,arm,2,items,b,p,t,labels,None);need(math.isfinite(q2['loss_before']),'MATH_UPDATE')
            if arm==ARMS[3]:r=repair_gradient(m,net,ps,2,items,b,p,t,'cpu',labels,.05);need(r['after']['preservation']['mean_kl']<=r['before']['preservation']['mean_kl']+1e-8 or len(r['after']['preservation']['lost'])<len(r['before']['preservation']['lost']),'REPAIR_RULE')
            if arm in (ARMS[5],ARMS[6]):r=fuzz_repair(m,net,ps,2,items,b,p,t,'cpu',labels,.05,SEED,a);need(len(r['coefficients'])>=1 and all(math.isfinite(x) for x in r['coefficients']),'FINITE_DIFFERENCE_FIXTURE')
        results[arm]=True
    # Check actual restoration and sensitivity algebra independently.
    ps=list(m.parameters());ss=snapshot(ps);d=random_direction(ps,SEED);need(abs(norm(d)-1)<1e-5 and all(x.numel()==p.numel() for x,p in zip(d,ps)),'DENSE_DIRECTION');move(ps,d,.1);restore(ps,ss);need(all(torch.equal(p,s) for p,s in zip(ps,ss)),'EXACT_RESTORE')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'checkpoint.pt';atomic_pt(path,{'model':m.state_dict(),'reader':net.state_dict()});h=sha(path);q=torch.load(path,map_location='cpu',weights_only=True);m.load_state_dict(q['model'],strict=True);net.load_state_dict(q['reader'],strict=True);need(sha(path)==h,'ROUNDTRIP');
        # Seal and tamper check on real author rows.
        ctx=(Path(tmp),Path(tmp)/'out',{'fixture':True});rr=[x[0] for x in z[:2]];gg=[x[1] for x in z[:2]];seal(ctx,'test',rr,gg);need(rows(ctx,'test',True)==list(zip(rr,gg)),'SEAL_FIXTURE');(ctx[1]/'test'/'prompts.json').write_text('[]')
        try:rows(ctx,'test')
        except RuntimeError:pass
        else:raise AssertionError('TAMPER_NOT_DETECTED')
    lifecycle_fixture(z,tok);P.fixture_guard();need(exact_generated(' 2/4 ','1/2') and not exact_generated('not 1/2','1/2'),'GENERATION_PARSE');emit('SELFTEST',{'arms':results,'actual_registry_zero_and_hdb_author':True,'math_cards_and_preservation_cases':len(z),'full_weight_direction_fixture':True,'exact_probe_restore':True,'checkpoint_roundtrip':True,'seal_tamper_refusal':True,'answer_guard_subprocess':True,'actual_qwen_cuda':False,'causal_fixture_gate_lifecycle':True,'live_gate_lifecycle':False})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','run','prepare','reference','fit','predict','score','finish'),nargs='?',default='audit');p.add_argument('--root',default='../runtime/reports/gamer-v1');p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--steps',type=int,default=12);p.add_argument('--radius',type=float,default=.25,help='global L2 norm of an acquisition update, not per-weight LR');p.add_argument('--reader-lr',type=float,default=.001);p.add_argument('--repair-steps',type=int,default=1);p.add_argument('--max-kl',type=float,default=.1);p.add_argument('--seed',type=int,default=SEED);p.add_argument('--arm',choices=ARMS);p.add_argument('--stage',type=int,choices=(1,2,3,4));a=p.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()));
    if a.command=='selftest':return selftest()
    need(a.steps>=1 and a.repair_steps>=1 and all(math.isfinite(x) and x>0 for x in (a.radius,a.reader_lr)) and math.isfinite(a.max_kl) and a.max_kl>=0,'CONFIG');ctx=context(a);root,out,ident=ctx
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':ident,'model_loaded':False,'backbone_adapter':False,'fp32_all_parameters_eligible_for_B_through_G':True,'research_only':True,'original_checkpoint_immutable':True,'estimated_checkpoint_bytes_per_mutable_gate':2500000000});return
    if a.command=='prepare':return prepare(ctx)
    if a.command=='run':
        args=['--root',str(root),'--output',str(out),'--device',a.device,'--steps',str(a.steps),'--radius',str(a.radius),'--reader-lr',str(a.reader_lr),'--repair-steps',str(a.repair_steps),'--max-kl',str(a.max_kl),'--seed',str(a.seed)]
        def phase(cmd,arm=None,stage=None):subprocess.run([sys.executable,'-u','-m',MODULE,cmd,*args,*(['--arm',arm,'--stage',str(stage)] if arm else [])],check=True)
        phase('prepare');phase('reference')
        # Every arm attempts gate one before any advances. A failed gate never trains later gates.
        for stage in (1,2,3,4):
            for arm in ARMS:
                if stage>2 and (not (gate_dir(ctx,arm,stage-1)/'result.json').exists() or not verify_stage(ctx,arm,stage-1)['passed']):continue
                d=gate_dir(ctx,arm,stage)
                if (d/'result.json').exists():verify_stage(ctx,arm,stage);continue
                phase('fit',arm,stage);phase('predict',arm,stage);phase('score',arm,stage)
        phase('finish');return
    need(read(out/'identity.json')==ident,'IDENTITY_DRIFT')
    if a.command=='reference':return reference(a,ctx)
    if a.command=='finish':return finish(ctx)
    need(a.arm is not None and a.stage is not None,'ARM_STAGE_REQUIRED')
    if a.stage>2:need(verify_stage(ctx,a.arm,a.stage-1)['passed'],'PREDECESSOR_GATE')
    if a.command=='fit':return fit_stage(a,ctx,a.arm,a.stage)
    if a.command=='predict':return predict(a,ctx,a.arm,a.stage)
    return score(a,ctx,a.arm,a.stage)
if __name__=='__main__':main()
