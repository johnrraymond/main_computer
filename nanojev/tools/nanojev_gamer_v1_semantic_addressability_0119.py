"""0119: native binding competence, passive full-width and active numerical readout.
Conditional candidate ranking only; no changes to existing backbone/readers.
"""
from __future__ import annotations
import argparse, collections as co, hashlib, inspect, json, os, re, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from tools import nanojev_gamer_v1_semantic_contrast_0118 as C
read,sha,digest,imm,need=C.read,C.sha,C.digest,C.imm,C.need
TAG='G12_0119_ADDRESS'; MODULE='tools.nanojev_gamer_v1_semantic_addressability_0119'; SEED=1192026
RELS=C.RELS; LAYERS=(0,7,14,21,28); SPLITS=('fit','validation','fresh','exposed0118')
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_address_author_0119.py')

def emit(k,v):print(TAG+'_'+k+' '+json.dumps(v,allow_nan=False,sort_keys=True),flush=True)
def compact(q):return C.compact(q)
def path(a):return Path(a.root).resolve()/'g1-g2-semantic-addressability-0119'
def setup(a):
    parent=C.setup(SimpleNamespace(root=a.root,device=a.device,command='predict' if a.command in ('predict','native_predict') else a.command));_,old,identity,_=parent;s=C.validate_selection(old,identity);d,_,pub=C.cohort(old,identity,'fresh');lock=read(d/'prediction-lock.json');need(lock['seal']==sha(d/'seal.json') and lock['selection']==sha(old/'selection.json') and lock['shards']==[sha(d/'predictions'/f'{i:04d}.json') for i in range(len(pub))],'0118_LOCK');out=path(a);out.mkdir(exist_ok=True)
    ident={'source':sha(__file__),'author':sha(AUTHOR),'parent_lock':sha(d/'prediction-lock.json'),'parent_selection':sha(old/'selection.json'),'parent':identity,'device':a.device,'seed':SEED,'register_epochs':a.register_epochs}
    return parent,out,ident

def prepare(a,st):
    from tools.nanojev_gamer_v1_address_author_0119 import rows
    parent,out,ident=st;_,old,identity,_=parent;d,_,pub,ans=C.cohort(old,identity,'fresh',True);new=list(rows());need(len(new)==144,'AUTHOR_COUNT');prior=set()
    for split in ('fit','validation','fresh'):
        _,_,rr=C.cohort(old,identity,split);prior.update(r['prompt_sha256'] for r in rr)
    need(len({r['prompt_sha256'] for r,g in new})==144 and not any(r['prompt_sha256'] in prior for r,g in new),'PROMPT_REUSE');new.extend((dict(r,split='exposed0118'),g) for r,g in zip(pub,ans))
    for split in SPLITS:
        rr=[r for r,g in new if r['split']==split];gg=[g for r,g in new if r['split']==split];folder=out/split;folder.mkdir(exist_ok=True)
        groups=co.defaultdict(dict)
        for r,g in zip(rr,gg):groups[r['base_id']][r['mode']]=(r,g)
        for modes in groups.values():
            for mode in ('direct','direct_paraphrase'):
                r,g=modes[mode];p,h=modes[mode.replace('direct','reverse')];need(r['anchors']==p['anchors'] and g['positive']==[1-x for x in h['positive']],'REVERSAL_CONTRACT')
            need(modes['direct'][1]['positive']==modes['direct_paraphrase'][1]['positive'],'PARAPHRASE_CONTRACT')
        imm(folder/'prompts.json',rr);imm(folder/'answers.SECRET.json',gg);imm(folder/'seal.json',{'identity':ident,'prompts':sha(folder/'prompts.json'),'answers':sha(folder/'answers.SECRET.json'),'cases':len(rr),'structures':sorted({r['structure'] for r in rr})});emit('SEALED',{'split':split,'cases':len(rr),'structures':sorted({r['structure'] for r in rr})})

def cohort(st,split,answers=False):
    _,out,ident=st;d=out/split;s=read(d/'seal.json');need(s['identity']==ident and s['prompts']==sha(d/'prompts.json'),'SEAL');rr=read(d/'prompts.json');need(len(rr)==s['cases'] and all(hashlib.sha256(r['prompt'].encode()).hexdigest()==r['prompt_sha256'] for r in rr),'PROMPTS');
    if not answers:return d,rr
    need(s['answers']==sha(d/'answers.SECRET.json'),'ANSWERS');gg=read(d/'answers.SECRET.json');need(len(rr)==len(gg) and all((r['case_id'],r['prompt_sha256'])==(g['case_id'],g['prompt_sha256']) for r,g in zip(rr,gg)),'ANSWER_ALIGNMENT');return d,rr,gg

def anchors(r):
    return {q['id']:r['prompt'][q['span'][0]:q['span'][1]] for q in r['anchors']}
def statement(rel,l,r):
    return {'refers_to':f'{l} denotes {r}', 'owns':f'{r} is an available action of {l}', 'applies_to':f'{l} is the cost of {r}', 'measures':f'{l} is the recorded value of {r}'}[rel]
def questions(r,blank=False):
    a=anchors(r);text=r['prompt'];last=max(q['span'][1] for q in r['anchors']);text=text[:last] if blank else text
    stm=[statement(r['relation'],a[f'l{i}'],a[f'r{j}']) for i in range(2) for j in range(2)]
    # Four hypotheses generated symmetrically, no label or mode access.
    pair=[text+'\nDoes the record establish this binding? '+s+'. Reply Yes or No.' for s in stm]
    maps=[stm[0]+'; '+stm[3],stm[1]+'; '+stm[2]]
    choice=[text+'\nWhich complete pairing is specified?\nA: '+maps[k]+'.\nB: '+maps[1-k]+'.\nReply only A or B.' for k in (0,1)]
    return pair,choice

def chat_ids(tok,text):
    msgs=[{'role':'user','content':text}]
    try:s=tok.apply_chat_template(msgs,tokenize=False,add_generation_prompt=True,enable_thinking=False)
    except TypeError:s=tok.apply_chat_template(msgs,tokenize=False,add_generation_prompt=True)
    return tok(s,add_special_tokens=False,return_tensors='pt')['input_ids'][0]
def single_ids(tok,labels):
    result=[]
    for label in labels:
        ids=tok.encode(label,add_special_tokens=False);need(len(ids)==1,'NON_SINGLE_NATIVE_LABEL_'+label);result.append(ids[0])
    need(len(set(result))==len(labels),'NATIVE_LABEL_COLLISION');return result

def logits_kw(model):
    target=model.get_base_model() if hasattr(model,'get_base_model') else model
    params=inspect.signature(target.forward).parameters
    return {'logits_to_keep':1} if 'logits_to_keep' in params else {'num_logits_to_keep':1} if 'num_logits_to_keep' in params else {}

def unit(x):
    x=x.detach().float().cpu().numpy();return x/np.maximum(np.sqrt(np.mean(x*x,axis=-1,keepdims=True)),1e-6)
def forward(model,ids,device,ref=None,reg=None):
    ids=ids.to(device)
    if ref is None and reg is None:
        return model(input_ids=ids.unsqueeze(0),attention_mask=torch.ones_like(ids.unsqueeze(0)),output_hidden_states=True,use_cache=False,**logits_kw(model))
    e=model.get_input_embeddings()(ids.unsqueeze(0));pieces=[]
    if ref is not None:pieces.append(ref.to(e).reshape(1,1,-1))
    pieces.append(e)
    if reg is not None:pieces.append(reg.to(e).reshape(1,1,-1))
    x=torch.cat(pieces,dim=1);mask=torch.ones(x.shape[:2],dtype=torch.long,device=x.device)
    return model(inputs_embeds=x,attention_mask=mask,output_hidden_states=True,use_cache=False,**logits_kw(model))

def native(model,tok,r,device,blank=False):
    pair,choice=questions(r,blank);ab=single_ids(tok,('A','B'));yn=single_ids(tok,('Yes','No'));vals=[];cand=[];features={f'query_{k}':[] for k in LAYERS}
    with torch.no_grad():
        for text in choice:
            q=forward(model,chat_ids(tok,text),device);z=q.logits[0,-1].float();vals.append(float(z[ab[0]]-z[ab[1]]))
        for text in pair:
            q=forward(model,chat_ids(tok,text),device);z=q.logits[0,-1].float();cand.append(float(z[yn[0]]-z[yn[1]]))
            for k in LAYERS:features[f'query_{k}'].append(unit(q.hidden_states[k][0,-1]))
    # Average opposite option orders; not a generated answer or free-form accuracy.
    v=(vals[0]-vals[1])/2
    return {'native_choice':np.array([v,-v,-v,v],np.float32),'native_candidate':np.array(cand,np.float32)}, {k:np.stack(v) for k,v in features.items()},vals

def passive(r,p,states):
    view,pairs=C.anchor_view(r,p);spans=view['spans'];eligible=np.where(p['eligible'].cpu().numpy().reshape(-1))[0];last=max(s['token_last'] for s in spans);suffix=eligible[eligible>last];out={}
    words=[r['prompt'][a:b] for a,b,k in view['objects']];char_end=max(b for a,b,k in view['objects']);start=p['prompt_start'];offsets=p['offsets']
    # Exact repeated-name alignment, not assumed positive pair identity.
    occurrences=[]
    for w in words:
        hits=[]
        for match in re.finditer(r'(?<!\w)'+re.escape(w)+r'(?!\w)',r['prompt'][char_end:],re.I):
            a=start+char_end+match.start();b=start+char_end+match.end();hits.extend(i for i in suffix if offsets[i][0]<b and offsets[i][1]>a)
        occurrences.append(np.array(sorted(set(hits)),dtype=int))
    for key,h0 in states.items():
        h=unit(h0);out[key+':endpoint']=C.pair_surface(h,spans,pairs,eligible,'endpoint');out[key+':suffix']=C.pair_surface(h,spans,pairs,eligible,'suffix')
        data=[];bins=np.array_split(suffix,4)
        for k,(i,j) in enumerate(pairs):
            u=C.average(h,occurrences[i]);v=C.average(h,occurrences[j]);ri,rj=1-2*(k//2),1-2*(k%2)
            data.append(np.concatenate([u,v,u*v,u-v]+[C.average(h,b)*ri*rj for b in bins]+[C.average(h,b)*ri for b in bins]+[C.average(h,b)*rj for b in bins]))
        out[key+':ordered_mentions']=np.stack(data).astype(np.float32)
    return out

def fixed_register(model,tok,device):
    e=model.get_input_embeddings().weight;g=torch.Generator().manual_seed(SEED);v=torch.randn(e.shape[-1],generator=g);rms=float(e.detach()[:2048].float().square().mean().sqrt());return (v/v.square().mean().sqrt()*rms).to(device)

def query_extra(model,tok,r,device,S,R):
    rows,_=questions(r);features={f'{s}_{k}':[] for s in ('Squery','fixedR') for k in LAYERS}
    with torch.no_grad():
        for text in rows:
            ids=chat_ids(tok,text)
            for src,ref,reg in [('Squery',S,None),('fixedR',None,R)]:
                q=forward(model,ids,device,ref,reg)
                for k in LAYERS:features[f'{src}_{k}'].append(unit(q.hidden_states[k][0,-1]))
    return {k:np.stack(v) for k,v in features.items()}

def load(a,st,rr):
    parent=st[0];x=parent[0][0];root,_,_,ctx,*_=x
    model,tok,comp,ctrl,S,sig,_=C.T.load(str(root),{'fresh':rr},a.device)
    need(sig==ctx[0][-1]['frozen_g1'],'FROZEN_G1_DRIFT');models=(comp,{},{},model,tok,S,ctrl);C.J.frozen(models);return models

def early_native_predict(a,st):
    d,rr=cohort(st,'exposed0118');models=load(a,st,rr);model,tok=models[3:5];folder=d/'native';folder.mkdir(exist_ok=True);seal=sha(d/'seal.json')
    for i,r in enumerate(rr):
        p=folder/f'{i:04d}.npz';j=folder/f'{i:04d}.json'
        if j.exists():
            z=read(j);need(z['seal']==seal and z['prompt']==r['prompt_sha256'] and z['file']==sha(p),'NATIVE_CACHE');continue
        scores,query,order=native(model,tok,r,a.device);blank,_,bo=native(model,tok,r,a.device,True);arr={**scores,**query,**{k+'_no_cue':v for k,v in blank.items()},'native_option_order':np.array(order,np.float32),'native_option_order_no_cue':np.array(bo,np.float32)};need(all(np.isfinite(v).all() for v in arr.values()),'NATIVE_FINITE');C.atomic_npz(p,arr);imm(j,{'seal':seal,'prompt':r['prompt_sha256'],'file':sha(p)});emit('NATIVE_PROGRESS',{'done':i+1,'of':len(rr),'case':r['case_id'],'margin':float(scores['native_choice'][0])})
    C.J.frozen(models);imm(d/'native-lock.json',{'seal':seal,'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]})

def early_native_score(st):
    _,out,_=st;d,rr=cohort(st,'exposed0118');folder=d/'native';lock=read(d/'native-lock.json');need(lock['seal']==sha(d/'seal.json') and lock['shards']==[sha(folder/f'{i:04d}.json') for i in range(len(rr))],'NATIVE_LOCK');xx=[]
    for i,r in enumerate(rr):
        j=read(folder/f'{i:04d}.json');p=folder/f'{i:04d}.npz';need(j['seal']==lock['seal'] and j['prompt']==r['prompt_sha256'] and j['file']==sha(p),'NATIVE_SHARD')
        with np.load(p,allow_pickle=False) as h:xx.append({k:h[k] for k in ('native_choice','native_candidate','native_choice_no_cue','native_candidate_no_cue')})
    _,_,gg=cohort(st,'exposed0118',True);metrics={}
    for rel in (*RELS,'all'):
        ix=[i for i,r in enumerate(rr) if rel=='all' or r['relation']==rel];metrics[rel]={k:C.ranks([rr[i] for i in ix],[gg[i] for i in ix],[xx[i][k] for i in ix]) for k in xx[0]};emit('NATIVE_RESULT',{'relation':rel,**{k:compact(v) for k,v in metrics[rel].items()}})
    imm(out/'native-exposed0118.json',{'scope':'retrospective_native_likelihood','native_lock':sha(d/'native-lock.json'),'metrics':metrics});emit('NATIVE_COMPLETE',{'path':str(out/'native-exposed0118.json')})

def capture(a,st,split,models):
    d,rr=cohort(st,split);model,tok,S=models[3:6];R=fixed_register(model,tok,a.device);folder=d/'features';folder.mkdir(exist_ok=True)
    for i,r in enumerate(rr):
        file=folder/f'{i:04d}.npz';receipt=folder/f'{i:04d}.json';seal=sha(d/'seal.json')
        if receipt.exists():
            z=read(receipt);need(z['seal']==seal and z['prompt']==r['prompt_sha256'] and z['file']==sha(file),'CACHE_DRIFT');continue
        p=C.B.prepare_prompt(tok,r['prompt']);states={}
        for name,ref in [('original',None),('S',S)]:
            v=C.m.capture(model,p,ref,a.device);states.update({f'{name}_{k}':h for k,h in v.items()})
        arr=passive(r,p,states)
        if split=='exposed0118' and (d/'native-lock.json').exists():
            nd=d/'native';lock=read(d/'native-lock.json');j=nd/f'{i:04d}.json';z=read(j);nfile=nd/f'{i:04d}.npz';need(lock['seal']==seal and lock['shards'][i]==sha(j) and z['prompt']==r['prompt_sha256'] and z['file']==sha(nfile),'NATIVE_REUSE')
            with np.load(nfile,allow_pickle=False) as h:arr.update({k:h[k] for k in h.files})
        else:
            base,query,order=native(model,tok,r,a.device);arr.update(base);arr.update(query);blank,_,bo=native(model,tok,r,a.device,True);arr.update({k+'_no_cue':v for k,v in blank.items()});arr['native_option_order']=np.array(order,np.float32);arr['native_option_order_no_cue']=np.array(bo,np.float32)
        arr.update(query_extra(model,tok,r,a.device,S,R));arr['geometry']=np.array([[i,j,int(i==j)] for i in range(2) for j in range(2)],np.float32)
        need(all(np.isfinite(v).all() for v in arr.values()),'NONFINITE_CAPTURE');C.atomic_npz(file,arr);imm(receipt,{'seal':seal,'prompt':r['prompt_sha256'],'file':sha(file)});emit('CAPTURE',{'split':split,'done':i+1,'of':len(rr),'case':r['case_id'],'native_margin':float(arr['native_choice'][0])})
    imm(d/'feature-lock.json',{'seal':sha(d/'seal.json'),'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]})

def features(st,split):
    d,rr=cohort(st,split);folder=d/'features';lock=read(d/'feature-lock.json');need(lock['seal']==sha(d/'seal.json') and lock['shards']==[sha(folder/f'{i:04d}.json') for i in range(len(rr))],'FEATURE_LOCK');out=[]
    for i,r in enumerate(rr):
        z=read(folder/f'{i:04d}.json');p=folder/f'{i:04d}.npz';need(z['seal']==lock['seal'] and z['prompt']==r['prompt_sha256'] and z['file']==sha(p),'FEATURE_FILE')
        with np.load(p,allow_pickle=False) as x:out.append({k:x[k] for k in x.files})
    return rr,out

# Candidate-specific numerical register. The same learned vector is appended to
# all hypotheses of one relation. Only register and readout parameters train.
def reg_hidden(model,tok,r,device,reg,blank=False):
    pair,_=questions(r,blank);seq=[];length=[]
    for text in pair:
        ids=chat_ids(tok,text).to(device);x=model.get_input_embeddings()(ids);seq.append(torch.cat([x,reg.to(x).reshape(1,-1)],0));length.append(len(x)+1)
    x=torch.nn.utils.rnn.pad_sequence(seq,batch_first=True);mask=(torch.arange(x.shape[1],device=x.device)[None,:]<torch.tensor(length,device=x.device)[:,None]).long();q=model(inputs_embeds=x,attention_mask=mask,output_hidden_states=True,use_cache=False,**logits_kw(model));h=q.hidden_states[-1][torch.arange(4,device=x.device),torch.tensor(length,device=x.device)-1].float();return h/(h.square().mean(-1,keepdim=True).sqrt()+1e-6)
def reg_scores(model,tok,r,device,spec,blank=False):
    with torch.no_grad():
        reg=torch.tensor(spec['register'],device=device);w=torch.tensor(spec['weight'],device=device);return (reg_hidden(model,tok,r,device,reg,blank)@w).cpu().numpy()
def train_register(a,models,rr,gg,vr,vg,rel,learned):
    model,tok=models[3:5];init=fixed_register(model,tok,a.device);scale=init.square().mean().sqrt();delta=torch.nn.Parameter(torch.zeros_like(init),requires_grad=learned);w=torch.nn.Parameter(torch.zeros_like(init));opt=torch.optim.AdamW([w]+([delta] if learned else []),lr=.003,weight_decay=.02);best=None
    for epoch in range(a.register_epochs):
        for i in np.random.default_rng(SEED+epoch).permutation(len(rr)):
            r,g=rr[i],gg[i];reg=init+scale*delta;z=reg_hidden(model,tok,r,a.device,reg)@w;y=g['positive'];ds=torch.stack([z[j]-z[k] for j in range(4) for k in range(4) if y[j] and not y[k] and (j//2==k//2 or j%2==k%2)]);loss=torch.nn.functional.softplus(-ds).mean()+.001*delta.square().mean();opt.zero_grad();loss.backward();need(all(p.grad is None for p in model.parameters()),'BACKBONE_GRADIENT');need(all(torch.isfinite(p.grad).all() for p in [w]+([delta] if learned else [])),'NONFINITE_REGISTER_GRAD');torch.nn.utils.clip_grad_norm_([w]+([delta] if learned else []),1.);opt.step()
        spec={'register':(init+scale*delta).detach().cpu().tolist(),'weight':w.detach().cpu().tolist(),'learned':learned,'epoch':epoch+1,'relative_register_delta_rms':float(delta.detach().square().mean().sqrt())};v=C.ranks(vr,vg,[reg_scores(model,tok,r,a.device,spec) for r in vr]);emit('REGISTER',{'relation':rel,'learned':learned,'epoch':epoch+1,'relative_register_delta_rms':spec['relative_register_delta_rms'],**compact(v)})
        if best is None or C.quality(v)>C.quality(best[1]):best=(spec,v)
    return best[0],compact(best[1])

def develop(a,st):
    _,out,ident=st;dest=out/'selection.json'
    if dest.exists():selection(st);emit('SELECTION_REUSED',{'sha256':sha(dest)});return
    _,fit,fg=cohort(st,'fit',True);_,val,vg=cohort(st,'validation',True);models=load(a,st,fit+val)
    for split in ('fit','validation'):capture(a,st,split,models)
    _,tx=features(st,'fit');_,vx=features(st,'validation');emit('NATIVE_VALIDATION', {k:compact(C.ranks(val,vg,[q[k] for q in vx])) for k in ('native_choice','native_candidate','native_choice_no_cue','native_candidate_no_cue')});surfaces=sorted(k for k,v in tx[0].items() if v.ndim==2);trials={};specs={};fl={k:sha(out/k/'feature-lock.json') for k in ('fit','validation')};parts=out/'relation-selection';parts.mkdir(exist_ok=True)
    for rel in RELS:
        rp=parts/(rel+'.json')
        if rp.exists():
            cached=read(rp);need(cached['identity']==ident and cached['features']==fl and cached['digest']==digest(cached['body']),'RELATION_CACHE');specs[rel]=cached['body']['spec'];trials[rel]=cached['body']['trial'];emit('RELATION_REUSED',{'relation':rel});continue
        ti=[i for i,r in enumerate(fit) if r['relation']==rel];vi=[i for i,r in enumerate(val) if r['relation']==rel];rr=[fit[i] for i in ti];gg=[fg[i] for i in ti];vr=[val[i] for i in vi];vv=[vg[i] for i in vi];yy=[g['positive'] for g in gg];bank={};results={}
        for k in surfaces:
            z=C.linear_fit([tx[i][k] for i in ti],yy);bank[k]=z;results[k]=compact(C.ranks(vr,vv,[C.apply(z,vx[i][k]) for i in vi]))
        winner=max(surfaces,key=lambda k:C.quality(results[k]));native_results={k:compact(C.ranks(vr,vv,[vx[i][k] for i in vi])) for k in ('native_choice','native_candidate','native_choice_no_cue','native_candidate_no_cue')}
        regs={};regv={}
        for learn in (False,True):
            key='learnedR' if learn else 'fixedR_trained_head';regs[key],regv[key]=train_register(a,models,rr,gg,vr,vv,rel,learn)
        # Select deployable numerical arm on validation only; native choice stays control.
        choices={k:v for k,v in results.items() if k!='geometry'};choices.update(regv);selected=max(choices,key=lambda k:C.quality(choices[k]));specs[rel]={'linear':bank,'register':regs,'selected':selected};trials[rel]={'linear':results,'native':native_results,'register':regv,'best_linear':winner,'selected':selected};body={'spec':specs[rel],'trial':trials[rel]};imm(rp,{'identity':ident,'features':fl,'body':body,'digest':digest(body)});emit('SELECTED',{'relation':rel,'surface':selected,'validation':choices[selected],'native':native_results['native_choice']})
    C.J.frozen(models);imm(out/'development.json',{'identity':ident,'trials':trials,'feature_locks':{k:sha(out/k/'feature-lock.json') for k in ('fit','validation')}});imm(dest,{'identity':ident,'development':sha(out/'development.json'),'relations':specs});emit('SELECTION_LOCKED',{'sha256':sha(dest)})

def selection(st):
    _,out,ident=st;s=read(out/'selection.json');need(s['identity']==ident and s['development']==sha(out/'development.json'),'SELECTION');d=read(out/'development.json');need(d['feature_locks']=={k:sha(out/k/'feature-lock.json') for k in ('fit','validation')},'DEVELOPMENT_FEATURE_LOCK');return s

def predict(a,st):
    _,out,_=st;s=selection(st);models=load(a,st,cohort(st,'fresh')[1]+cohort(st,'exposed0118')[1]);model,tok=models[3:5];sel=sha(out/'selection.json')
    for split in ('fresh','exposed0118'):
        capture(a,st,split,models);d,rr=cohort(st,split);_,xx=features(st,split);folder=d/'predictions';folder.mkdir(exist_ok=True)
        for i,(r,x) in enumerate(zip(rr,xx)):
            dest=folder/f'{i:04d}.json';seal=sha(d/'seal.json');key=r['relation'];z=s['relations'][key]
            if dest.exists():
                old=read(dest);need(old['seal']==seal and old['selection']==sel and old['prompt']==r['prompt_sha256'] and old['digest']==digest(old['body']),'PREDICTION_DRIFT');continue
            scores={k:C.apply(v,x[k]).tolist() for k,v in z['linear'].items()};scores.update({k:x[k].tolist() for k in ('native_choice','native_candidate','native_choice_no_cue','native_candidate_no_cue')})
            for k,v in z['register'].items():scores[k]=reg_scores(model,tok,r,a.device,v).tolist();scores[k+'_no_cue']=reg_scores(model,tok,r,a.device,v,True).tolist()
            scores['selected']=scores[z['selected']];body={'scores':scores,'native_option_order':x['native_option_order'].tolist(),'selected':z['selected']};imm(dest,{'seal':seal,'selection':sel,'prompt':r['prompt_sha256'],'body':body,'digest':digest(body)});emit('PREDICT',{'split':split,'done':i+1,'of':len(rr),'case':r['case_id']})
        imm(d/'prediction-lock.json',{'seal':sha(d/'seal.json'),'selection':sel,'features':sha(d/'feature-lock.json'),'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]})
    C.J.frozen(models);need(selection(st)==s,'SELECTION_CHANGED')

def score(st):
    _,out,ident=st;s=selection(st);report={'identity':ident,'selection':sha(out/'selection.json'),'scope':'conditional_binding_not_full_graph','certification':False,'g3_unlocked':False,'production_modified':False,'cohorts':{}}
    for split in ('fresh','exposed0118'):
        d,rr=cohort(st,split);lock=read(d/'prediction-lock.json');folder=d/'predictions';need(lock=={'seal':sha(d/'seal.json'),'selection':sha(out/'selection.json'),'features':sha(d/'feature-lock.json'),'shards':[sha(folder/f'{i:04d}.json') for i in range(len(rr))]},'PREDICTION_LOCK');bb=[]
        for i,r in enumerate(rr):
            z=read(folder/f'{i:04d}.json');need(z['seal']==lock['seal'] and z['selection']==lock['selection'] and z['prompt']==r['prompt_sha256'] and z['digest']==digest(z['body']),'SHARD');bb.append(z['body'])
        _,_,gg=cohort(st,split,True);results={}
        for rel in (*RELS,'all'):
            ix=[i for i,r in enumerate(rr) if rel=='all' or r['relation']==rel];r=[rr[i] for i in ix];g=[gg[i] for i in ix];results[rel]={k:C.ranks(r,g,[bb[i]['scores'][k] for i in ix]) for k in bb[0]['scores']};emit('RESULT_COHORT',{'split':split,'relation':rel,**{k:compact(results[rel][k]) for k in ('native_choice','native_candidate','native_choice_no_cue','native_candidate_no_cue','selected','learnedR','fixedR_trained_head')}})
        report['cohorts'][split]={'metrics':results,'prediction_lock':sha(d/'prediction-lock.json'),'native_option_order_disagreement':sum((b['native_option_order'][0]>0)==(b['native_option_order'][1]>0) for b in bb)}
    report['status']='DIAGNOSTIC_REVIEW_REQUIRED';report['limitations']=['Native next-token A/B and Yes/No likelihood is not unrestricted generated-answer accuracy.','Public candidate anchors and type; no graph-extraction claim.','Fresh three instruction constructions shared across relations; no broad generalization claim.','Exposed 0118 is retrospective. No-cue control removes suffix instructions, changing distribution.','Frozen Qwen with historical adapter, not raw base model. Register gradients only through appended register, no backbone or existing reader update.'];imm(out/'result.json',report)
    lines=['# 0119 semantic addressability','', 'Conditional binding, not graph certification. Native option likelihood uses both option orders.','', '| Cohort | Arm | Pairwise | Reversals |','|---|---|---:|---:|']
    for split,v in report['cohorts'].items():
        for k in ('native_choice','native_candidate','native_choice_no_cue','selected','learnedR','fixedR_trained_head'):
            z=v['metrics']['all'][k];lines.append(f"| {split} | {k} | {z['pairwise_accuracy']:.4f} | {z['reversal_pairs_correct']}/{z['reversal_pairs']} |")
    lines.extend(['','Limitations:',*['- '+v for v in report['limitations']]]);(out/'RESEARCH_RECORD.md').write_text('\n'.join(lines)+'\n',encoding='utf-8');emit('RESULT',{'path':str(out/'result.json'),'status':report['status'],'certification':False})

def selftest():
    from tools.nanojev_gamer_v1_address_author_0119 import rows
    rr=list(rows());need(len(rr)==144,'AUTHOR');need(len({r['prompt_sha256'] for r,g in rr})==144,'UNIQUE');groups=co.defaultdict(dict)
    for r,g in rr:groups[r['base_id']][r['mode']]=(r,g)
    for z in groups.values():
        for mode in ('direct','direct_paraphrase'):
            r,g=z[mode];p,h=z[mode.replace('direct','reverse')];need(r['anchors']==p['anchors'] and g['positive']==[1-x for x in h['positive']],'PAIRED');need(anchors(r)==anchors(p),'ANCHOR_TEXT');need(questions(r,True)==questions(p,True),'CUE_CONTROL')
    # Tiny differentiable causal fixture with identical forward interface; not Qwen.
    class Tok:
        def encode(self,s,**kw):return [{'A':1,'B':2,'Yes':3,'No':4}[s]] if s in ('A','B','Yes','No') else [5]
        def apply_chat_template(self,msgs,**kw):return msgs[0]['content']
        def __call__(self,s,**kw):return {'input_ids':torch.tensor([[5+ord(c)%25 for c in s]],dtype=torch.long)}
    class Net(torch.nn.Module):
        def __init__(self):super().__init__();self.emb=torch.nn.Embedding(32,16);self.proj=torch.nn.Linear(16,32)
        def get_input_embeddings(self):return self.emb
        def forward(self,attention_mask,output_hidden_states,use_cache,inputs_embeds=None,input_ids=None):
            if inputs_embeds is None:inputs_embeds=self.emb(input_ids)
            h=inputs_embeds.cumsum(1)/(torch.arange(inputs_embeds.shape[1],device=inputs_embeds.device)[None,:,None]+1);return SimpleNamespace(hidden_states=[h]*29,logits=self.proj(h))
    model=Net().eval().requires_grad_(False);tok=Tok();r,g=rr[0];base,query,order=native(model,tok,r,'cpu');need(base['native_choice'].shape==(4,) and query['query_28'].shape==(4,16),'NATIVE_SHAPE');R=fixed_register(model,tok,'cpu').requires_grad_();h=reg_hidden(model,tok,r,'cpu',R);h[:,0].sum().backward();need(R.grad is not None and R.grad.abs().sum()>0 and all(p.grad is None for p in model.parameters()),'REGISTER_GRADIENT');
    a=SimpleNamespace(device='cpu',register_epochs=1);mods=(None,None,None,model,tok,None,None);r2,g2=rr[1];spec,v=train_register(a,mods,[r,r2],[g,g2],[r,r2],[g,g2],r['relation'],True);need(np.isfinite(reg_scores(model,tok,r,'cpu',spec)).all(),'TRAINED_REGISTER')
    # Full-width surface on actual 0118 anchor mapper with character token fixture.
    text=r['prompt'];p={'prompt':text,'prompt_start':0,'offsets':[(i,i+1) for i in range(len(text))],'eligible':torch.ones(len(text),dtype=torch.bool),'ids':torch.zeros(1,len(text),dtype=torch.long),'attention':torch.ones(1,len(text),dtype=torch.long),'formatted':text};xx=passive(r,p,{'original_14':torch.randn(len(text),16)});need(xx['original_14:endpoint'].shape==(4,64) and xx['original_14:ordered_mentions'].shape==(4,256),'FULL_WIDTH');
    # End-to-end persistence, selection and scoring fixture; no actual checkpoint.
    import tempfile, contextlib, io
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
        out=Path(temp);ident={'fixture':True};st=(None,out,ident);mods=(SimpleNamespace(head=model),{},{},model,tok,None,model)
        for split,t in zip(SPLITS,(0,4,6,7)):
            data=[(dict(q,split=split),g) for q,g in rr if q['structure']==t and q['mode'] in ('direct','reverse')];folder=out/split;folder.mkdir();pub=[q for q,g in data];gold=[g for q,g in data];imm(folder/'prompts.json',pub);imm(folder/'answers.SECRET.json',gold);imm(folder/'seal.json',{'identity':ident,'prompts':sha(folder/'prompts.json'),'answers':sha(folder/'answers.SECRET.json'),'cases':len(pub)})
        def fixture_capture(a,st,split,models):
            folder,pub=cohort(st,split);d=folder/'features';d.mkdir(exist_ok=True);seal=sha(folder/'seal.json')
            for i,q in enumerate(pub):
                dest=d/f'{i:04d}.npz';receipt=d/f'{i:04d}.json'
                if receipt.exists():continue
                sc,features,order=native(model,tok,q,'cpu');blank,_,bo=native(model,tok,q,'cpu',True);arr={**sc,**features,**{k+'_no_cue':v for k,v in blank.items()},'native_option_order':np.array(order),'geometry':np.array([[i,j,int(i==j)] for i in range(2) for j in range(2)],np.float32)};C.atomic_npz(dest,arr);imm(receipt,{'seal':seal,'prompt':q['prompt_sha256'],'file':sha(dest)})
            imm(folder/'feature-lock.json',{'seal':seal,'shards':[sha(d/f'{i:04d}.json') for i in range(len(pub))]})
        with patch.dict(globals(),{'load':lambda *x:mods,'capture':fixture_capture}):
            early_native_predict(a,st);early_native_score(st);develop(a,st);predict(a,st);score(st);first=sha(out/'result.json');develop(a,st);predict(a,st);score(st);need(sha(out/'result.json')==first,'FIXTURE_RESUME');need(read(out/'result.json')['cohorts']['fresh']['metrics']['all']['selected']['cases']==8,'FIXTURE_SCORE')
    guard=subprocess.run([sys.executable,'-c','from tools.nanojev_gamer_v1_semantic_integrity_probe import answer_guard; answer_guard();\ntry: open("answers.SECRET.json","rb")\nexcept PermissionError: print("blocked")\nelse: raise SystemExit(1)'],check=True,capture_output=True,text=True);need(guard.stdout.strip()=='blocked','GUARD');emit('SELFTEST',{'authored_cases':len(rr),'matched_reversals':len(groups)*2,'native_likelihood_fixture':True,'full_width_anchor_mapper':True,'actual_register_gradient_fixture':True,'register_fit_fixture':True,'sealed_lifecycle_and_resume_fixture':True,'answer_guard':True,'qwen_checkpoint_tested':False,'cuda_tested':False})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','prepare','native','native_predict','native_score','develop','predict','score','run'));p.add_argument('--root',default=C.m.ROOT);p.add_argument('--device',default='cuda');p.add_argument('--register-epochs',type=int,default=2);a=p.parse_args();need(1<=a.register_epochs<=20,'EPOCHS');torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':selftest();return
    if a.command in ('run','native'):
        phases=('prepare','native_predict','native_score') if a.command=='native' else ('prepare','native_predict','native_score','develop','predict','score')
        for phase in phases:subprocess.run([sys.executable,'-u','-m',MODULE,phase,'--root',str(Path(a.root).resolve()),'--device',a.device,'--register-epochs',str(a.register_epochs)],check=True)
        return
    if a.command in ('predict','native_predict'):C.I.answer_guard()
    st=setup(a)
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':st[2],'fit':64,'validation':32,'fresh':48,'exposed0118':32,'scope':'conditional_binding'});return
    if a.command=='prepare':prepare(a,st)
    elif a.command=='native_predict':early_native_predict(a,st)
    elif a.command=='native_score':early_native_score(st)
    elif a.command=='develop':develop(a,st)
    elif a.command=='predict':predict(a,st)
    else:score(st)
if __name__=='__main__':main()
