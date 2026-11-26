"""0123: contrast-only microscopic ownership mechanism, frozen backbone, research only."""
from __future__ import annotations
import argparse, hashlib, math, random, subprocess, sys, tempfile
from pathlib import Path
import torch
from torch.nn import functional as F
from tools import nanojev_gamer_v1_contextual_readout_0122 as T
from tools import nanojev_gamer_v1_backbone_preserving_0121 as P
from tools import nanojev_gamer_v1_backbone_competence_0120 as C

TAG='G12_0123_ELEMENTARY';MODULE='tools.nanojev_gamer_v1_elementary_binding_0123'
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_elementary_binding_author_0123.py')
SPLITS=('one','eight','names','paraphrase','structure'); COUNTS=(2,16,40,40,40); SEED=1232026
sha,read,imm,digest=C.sha,C.read,C.imm,C.digest

def need(ok,msg):
    if not ok:raise RuntimeError(TAG+'_'+msg)
def emit(k,v):
    import json
    print(TAG+'_'+k+' '+json.dumps(v,sort_keys=True,allow_nan=False),flush=True)

def context(a):
    root=Path(a.root).resolve();out=Path(a.output).resolve() if a.output else root/'g1-g2-elementary-binding-0123';parent=root/'g1-g2-contextual-readout-0122';prior=read(parent/'identity.json');report=read(parent/'result-with-oracles.json');model=read(root/'g1-g2-backbone-competence-0120'/'model-receipt.json')
    need(report['identity']==digest(prior) and not report['production_modified'] and prior['model']==model,'PARENT_IDENTITY');need(prior['source']==sha(T.__file__) and prior['parent_source']==sha(P.__file__) and prior['competence_source']==sha(C.__file__),'PARENT_SOURCE')
    need(report['operational_result_sha256']==sha(parent/'result.json'),'PARENT_OPERATIONAL_DRIFT');need(report['summary']['E_attention']['fresh_binding_reversals']==0,'PARENT_CONTRAST_RESULT')
    ident={'source':sha(__file__),'author':sha(AUTHOR),'0122_source':sha(T.__file__),'0121_source':sha(P.__file__),'0120_source':sha(C.__file__),'parent_result':sha(parent/'result-with-oracles.json'),'model':model,'device':a.device,'steps':a.steps,'lr':a.lr,'seed':SEED,'width':192,'span_limit':16,'reader':'0122_E_attention_contrast_only','stages':list(SPLITS),'gates':{'one':[1.,1.],'eight':[1.,1.],'names':[.95,.95],'paraphrase':[.9,.9],'structure':[.8,.7]},'training_margin':.1}
    return root,out,ident

def seal(ctx,name,rr,gg):
    _,out,ident=ctx;d=out/name;imm(d/'prompts.json',rr);imm(d/'answers.SECRET.json',gg);imm(d/'seal.json',{'identity':digest(ident),'split':name,'cases':len(rr),'prompts':sha(d/'prompts.json'),'answers':sha(d/'answers.SECRET.json')})
def cohort(ctx,name,answers=False):return P.cohort(ctx,name,answers)
def grouped(rr,gg):return [dict(r,base_id=g['base_id'],mode=g['mode']) for r,g in zip(rr,gg)]
def blank(r):return dict(r,prompt=r['prompt'][:max(x['span'][1] for x in r['anchors'])])
def prepare(ctx):
    from tools import nanojev_gamer_v1_elementary_binding_author_0123 as A
    root,out,ident=ctx;known=set();parent_fit=set();hold=set()
    for name,count in zip(SPLITS,COUNTS):
        data=A.rows(name);rr=[r for r,g in data];gg=[g for r,g in data];need(len(rr)==count,'COUNT');C.validate_rows(grouped(rr,gg),gg)
        hashes={r['prompt_sha256'] for r in rr}
        if name=='one':parent_fit=hashes
        elif name=='eight':need(parent_fit<=hashes,'ONE_NOT_IN_EIGHT');parent_fit=hashes
        else:need(not hashes&(parent_fit|hold),'HOLDOUT_COLLISION');hold|=hashes
        seal(ctx,name,rr,gg)
    # Exact hash exclusion versus prior public prompt manifests. One/eight overlap is intentional.
    for f in root.glob('*/**/prompts.json'):
        if out in f.parents:continue
        try:rs=read(f)
        except (ValueError,OSError):continue
        if isinstance(rs,list):known.update(r['prompt_sha256'] for r in rs if isinstance(r,dict) and 'prompt_sha256' in r)
    need(not known&(hold|parent_fit),'PRIOR_PROMPT_COLLISION');imm(out/'identity.json',ident);emit('PREPARED',{'counts':dict(zip(SPLITS,COUNTS)),'prior_hashes':len(known)})

def setup(a,ctx):
    torch.manual_seed(SEED);model,tok=P.load_model(a,ctx,'A_bare_readers');need(not hasattr(model,'peft_config'),'ADAPTER');torch.manual_seed(T.SEED+1);rms=float(model.get_input_embeddings().weight.detach().float().square().mean().sqrt());net=T.Reader(model.config.hidden_size,'E_attention',rms,192).to(a.device);P.freeze_check(model,None);return model,tok,net

def capture(model,tok,net,r,device):
    ids,keys,idx=P.token_prepare(tok,r['prompt'],16)
    with torch.no_grad():h=[v.detach() for v in P.hidden(model,ids,net,device)]
    need(all(bool(torch.isfinite(v).all()) for v in h),'HIDDEN_NONFINITE');lookup={k:i for i,k in enumerate(keys)};need(all(tuple(x['span']) in lookup for x in r['anchors']),'ANCHOR_UNMAPPED');return (h,idx.to(device),keys)
def logits(net,r,cap):
    h,idx,keys=cap;f,g=net.spans(h,idx);z=T.contrast_scores(net,f,g,keys,r);need(z is not None and z.shape==(4,) and bool(torch.isfinite(z).all()),'CONTRAST_SCORES');return z

def pairs(rr,gg):
    groups={}
    for i,g in enumerate(gg):groups.setdefault(g['base_id'],[]).append(i)
    need(all(len(v)==2 and gg[v[0]]['positive']==[1-x for x in gg[v[1]]['positive']] for v in groups.values()),'PAIR_CONTRACT');return list(groups.values())
def margin(z,g):
    p=z[z.new_tensor(g['positive']).bool()];n=z[~z.new_tensor(g['positive']).bool()];return float((p[:,None]-n[None,:]).detach().min())
def interaction(z):return float(z[0]+z[3]-z[1]-z[2])
def stats(rr,gg,scores):
    m=C.ranks(grouped(rr,gg),gg,scores);m['min_all_competitor_margin']=min(margin(torch.tensor(z),g) for z,g in zip(scores,gg));m['assignment_interactions']=[interaction(z) for z in scores];return m

def delta_diagnostics(rr,gg,caps):
    out=[]
    for i,j in pairs(rr,gg):
        ha,ia,ka=caps[i];hb,ib,kb=caps[j];a,b=rr[i],rr[j];need(a['anchors']==b['anchors'],'ANCHOR_DRIFT');rows=[]
        for layer,x,y in zip(P.LAYERS,ha,hb):
            ex=[]
            for q in a['anchors']:
                k=tuple(q['span']);s,t=ia[ka.index(k)].tolist();u,v=ib[kb.index(k)].tolist();ex.append(float((x[s:t+1].float().mean(0)-y[u:v+1].float().mean(0)).square().mean().sqrt()))
            start_a=max(int(ia[ka.index(tuple(q['span']))][1]) for q in a['anchors'])+1;start_b=max(int(ib[kb.index(tuple(q['span']))][1]) for q in b['anchors'])+1;xa,ya=x[start_a:].float(),y[start_b:].float();delta=(xa-ya).square().mean(-1).sqrt() if len(xa)==len(ya) and len(xa) else None
            rows.append({'layer':layer,'endpoint_rms_deltas':ex,'last_state_rms_delta':float((x[-1].float()-y[-1].float()).square().mean().sqrt()),'token_counts':[len(x),len(y)],'post_anchor_token_counts':[len(xa),len(ya)],'aligned_post_anchor_mean_token_rms_delta':float(delta.mean()) if delta is not None else None,'aligned_post_anchor_max_token_rms_delta':float(delta.max()) if delta is not None else None})
        out.append({'base_id':gg[i]['base_id'],'layers':rows})
    return out

def gradient_probe(net,rr,gg,caps):
    i,j=pairs(rr,gg)[0];params=[(n,p) for n,p in net.named_parameters() if p.requires_grad];gr=[]
    for k in (i,j):
        loss=T.contrast_loss(logits(net,rr[k],caps[k]),gg[k]);v=torch.autograd.grad(loss,[p for _,p in params],allow_unused=True);gr.append([torch.zeros_like(p) if g is None else g.detach() for (_,p),g in zip(params,v)])
    x,y=[torch.cat([t.flatten() for t in v]) for v in gr];den=float(x.norm()*y.norm());return {'base_id':gg[i]['base_id'],'individual_norms':[float(x.norm()),float(y.norm())],'cosine':float(x.dot(y))/den if den else None,'paired_gradient_norm':float(((x+y)/2).norm()),'by_parameter':{n:[float(u.norm()),float(v.norm())] for (n,_),u,v in zip(params,*gr)}}

def evaluated(net,rr,gg,caps):
    with torch.no_grad():z=[logits(net,r,c) for r,c in zip(rr,caps)];return {'loss':sum(float(T.contrast_loss(v,g)) for v,g in zip(z,gg))/len(z),'metrics':stats(rr,gg,[v.tolist() for v in z]),'scores':[v.tolist() for v in z]}

def optimize(a,model,net,rr,gg,caps):
    initial=evaluated(net,rr,gg,caps);probe=gradient_probe(net,rr,gg,caps);opt=torch.optim.AdamW(net.parameters(),lr=a.lr,weight_decay=0.);order=pairs(rr,gg);history=[];best=None;net.train()
    for step in range(1,a.steps+1):
        opt.zero_grad(set_to_none=True);gradloss=0.
        # Full balanced batch of complete pairs; no sequential optimizer updates between opposite labels.
        for pair in order:
            for i in pair:
                v=T.contrast_loss(logits(net,rr[i],caps[i]),gg[i])/len(rr);v.backward();gradloss+=float(v.detach())
        norm=float(torch.nn.utils.clip_grad_norm_(net.parameters(),1.));need(math.isfinite(norm),'GRADIENT_NONFINITE');P.freeze_check(model,None);opt.step()
        if step==1 or step%10==0 or step==a.steps:
            q=evaluated(net,rr,gg,caps);m=q['metrics'];rec={'step':step,'loss':q['loss'],'batch_loss_before_update':gradloss,'gradient_norm':norm,'pairwise':m['pairwise_accuracy'],'exact':m['both_bindings_correct'],'reversals':m['reversal_pairs_correct'],'min_margin':m['min_all_competitor_margin']};history.append(rec);emit('FIT_STEP',rec)
            # Final state on success; on failure preserve best measured fit for forensics only.
            key=(m['both_bindings_correct']==len(rr) and m['min_all_competitor_margin']>=.1,m['reversal_pairs_correct'],m['both_bindings_correct'],m['pairwise_accuracy'],-q['loss']);
            if best is None or key>best[0]:best=(key,{k:v.detach().cpu().clone() for k,v in net.state_dict().items()},step)
            if m['both_bindings_correct']==len(rr) and m['min_all_competitor_margin']>=.1:break
    need(best is not None,'NO_STEPS');net.load_state_dict(best[1]);net.eval();final=evaluated(net,rr,gg,caps);P.freeze_check(model,None);return {'initial':initial,'gradient_probe':probe,'final_gradient_probe':gradient_probe(net,rr,gg,caps),'activation_deltas':delta_diagnostics(rr,gg,caps),'history':history,'best_step':best[2],'final':final,'passed':final['metrics']['both_bindings_correct']==len(rr) and final['metrics']['min_all_competitor_margin']>=.1}

def train(a,ctx,stage):
    _,out,ident=ctx;d=out/stage;rec=d/'fit.json';ck=d/'reader.safetensors'
    if stage=='eight':previous_gate(ctx,stage)
    if rec.exists():need(read(rec)['identity']==digest(ident) and read(rec)['checkpoint']==sha(ck) and read(rec)['seal']==sha(d/'seal.json'),'FIT_DRIFT');emit('REUSED_FIT',{'stage':stage});return
    model,tok,net=setup(a,ctx);rr,gg=cohort(ctx,stage,True);parent=None
    if stage=='eight':
        parent=read(out/'one'/'fit.json');need(parent['passed'] and parent['checkpoint']==sha(out/'one'/'reader.safetensors') and parent['identity']==digest(ident),'ONE_GATE');P.restore(net,None,out/'one'/'reader.safetensors')
    caps=[capture(model,tok,net,r,a.device) for r in rr];result=optimize(a,model,net,rr,gg,caps);P.save(ck,P.changes(net,None));imm(rec,dict(result,identity=digest(ident),checkpoint=sha(ck),seal=sha(d/'seal.json'),parent_checkpoint=parent['checkpoint'] if parent else None));emit('FIT',{'stage':stage,'passed':result['passed'],'metrics':C.compact(result['final']['metrics']),'loss':result['final']['loss']})

def selected(ctx,stage):
    _,out,ident=ctx;fit='one' if stage=='one' else 'eight';d=out/fit;r=read(d/'fit.json');need(r['identity']==digest(ident) and r['checkpoint']==sha(d/'reader.safetensors') and r['seal']==sha(d/'seal.json'),'CHECKPOINT_DRIFT');
    if fit=='eight':need(r.get('parent_checkpoint')==sha(out/'one'/'reader.safetensors'),'PARENT_CHECKPOINT_DRIFT')
    return d/'reader.safetensors',r

def lock_expected(ctx,stage):
    _,out,ident=ctx;d=out/stage;ck,_=selected(ctx,stage);return {'identity':digest(ident),'seal':sha(d/'seal.json'),'checkpoint':sha(ck),'predictions':sha(d/'predictions.json')}
def previous_gate(ctx,stage):
    pos=SPLITS.index(stage)
    if not pos:return
    _,out,ident=ctx;prev=SPLITS[pos-1];d=out/prev;r=read(d/'result.json');need(r['identity']==digest(ident) and r['stage']==prev and r['passed'] and r['prediction_lock']==sha(d/'prediction-lock.json') and read(d/'prediction-lock.json')==lock_expected(ctx,prev),'PREDECESSOR_GATE_'+prev)

def predict(a,ctx,stage):
    previous_gate(ctx,stage);C.guard();_,out,_=ctx;d=out/stage;ck,fit=selected(ctx,stage)
    if (d/'prediction-lock.json').exists():need(read(d/'prediction-lock.json')==lock_expected(ctx,stage),'PREDICTION_DRIFT');emit('REUSED_PREDICTION',{'stage':stage});return
    rr=cohort(ctx,stage);model,tok,net=setup(a,ctx);P.restore(net,None,ck);net.eval();pred=[];cache={};header={'identity':digest(ctx[2]),'seal':sha(d/'seal.json'),'checkpoint':sha(ck)}
    with torch.no_grad():
        for i,r in enumerate(rr):
            path=d/'prediction-shards'/f'{i:04d}.json'
            if path.exists():
                item=read(path);need(item['header']==header and item['body_digest']==digest(item['body']) and item['body']['case_id']==r['case_id'] and item['body']['prompt_sha256']==r['prompt_sha256'],'PREDICTION_SHARD_DRIFT');pred.append(item['body']);key=blank(r)['prompt'];need(key not in cache or cache[key]==item['body']['no_cue_scores'],'SHARD_CONTROL_DRIFT');cache[key]=item['body']['no_cue_scores'];continue
            cap=capture(model,tok,net,r,a.device);v=logits(net,r,cap).tolist();b=blank(r);key=b['prompt']
            if key not in cache:
                bc=capture(model,tok,net,b,a.device);cache[key]=logits(net,b,bc).tolist()
            native=P.native(model,tok,r,None,a.device);body={'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],'scores':v,'no_cue_scores':cache[key],'native':native};imm(path,{'header':header,'body':body,'body_digest':digest(body)});pred.append(body);emit('PREDICT',{'stage':stage,'done':len(pred),'of':len(rr)})
    P.freeze_check(model,None);imm(d/'predictions.json',pred);imm(d/'prediction-lock.json',lock_expected(ctx,stage));emit('LOCKED',{'stage':stage,'cases':len(rr)})

def score(ctx,stage):
    previous_gate(ctx,stage);_,out,ident=ctx;d=out/stage;need(read(d/'prediction-lock.json')==lock_expected(ctx,stage),'PREDICTION_DRIFT');rr,gg=cohort(ctx,stage,True);pr=read(d/'predictions.json');need(len(pr)==len(rr) and all((p['case_id'],p['prompt_sha256'])==(r['case_id'],r['prompt_sha256']) for p,r in zip(pr,rr)),'PREDICTION_ALIGNMENT');z=[p['scores'] for p in pr];b=[p['no_cue_scores'] for p in pr];n=[p['native']['scores'] for p in pr];m=stats(rr,gg,z);no=stats(rr,gg,b);native=stats(rr,gg,n);drift=max(abs(x-y) for i,j in pairs(rr,gg) for x,y in zip(b[i],b[j]));control=drift<=1e-7 and no['reversal_pairs_correct']==0 and abs(no['pairwise_accuracy']-.5)<=1e-8;cg,rg=ident['gates'][stage];_,fit=selected(ctx,stage);passed=m['both_bindings_correct']/len(rr)>=cg and m['reversal_pairs_correct']/len(pairs(rr,gg))>=rg and control and (fit['passed'] if stage in ('one','eight') else True)
    # Heldout cases and thresholds never used by optimizer. Native is a comparator, not training data.
    v={'identity':digest(ident),'stage':stage,'prediction_lock':sha(d/'prediction-lock.json'),'metrics':m,'native':native,'no_cue':no,'no_cue_max_pair_drift':drift,'control_passed':control,'passed':passed,'failure_reason':None if passed else 'NO_CUE_CONTROL' if not control else 'FIT_MARGIN_OR_ACCURACY' if stage in ('one','eight') and not fit['passed'] else 'CASE_OR_REVERSAL_GATE','required_case_fraction':cg,'required_reversal_fraction':rg,'details':[dict(r,answer=g,prediction=p) for r,g,p in zip(rr,gg,pr)]};imm(d/'result.json',v);emit('STAGE',{'stage':stage,'passed':passed,'reader':C.compact(m),'native':C.compact(native),'control_passed':control});return v

def finish(ctx):
    _,out,ident=ctx;stages={}
    for s in SPLITS:
        d=out/s
        if not (d/'result.json').exists():break
        r=read(d/'result.json');need(r['identity']==digest(ident) and r['prediction_lock']==sha(d/'prediction-lock.json') and read(d/'prediction-lock.json')==lock_expected(ctx,s),'REPORT_DRIFT');stages[s]={k:r[k] for k in ('passed','metrics','native','no_cue','control_passed','failure_reason')}
        if not r['passed']:break
    need(stages,'NO_RESULTS');last=list(stages)[-1];status='ELEMENTARY_GATES_PASSED_NOT_CERTIFIED' if last=='structure' and stages[last]['passed'] else 'STOPPED_AT_'+last.upper() if not stages[last]['passed'] else 'INCOMPLETE';v={'identity':digest(ident),'status':status,'stages':stages,'certification':False,'production_modified':False};imm(out/'result.json',v);emit('RESULT',{'status':status,'stages':{k:{'passed':v['passed'],'exact':v['metrics']['both_bindings_correct'],'reversals':v['metrics']['reversal_pairs_correct'],'native_exact':v['native']['both_bindings_correct'],'control_passed':v['control_passed']} for k,v in stages.items()}})

def selftest():
    import contextlib,io,re
    from types import SimpleNamespace
    from tools import nanojev_gamer_v1_elementary_binding_author_0123 as A
    torch.set_num_threads(1);torch.manual_seed(SEED)
    allrows={s:A.rows(s) for s in SPLITS}
    for s,count in zip(SPLITS,COUNTS):
        rr=[r for r,g in allrows[s]];gg=[g for r,g in allrows[s]];need(len(rr)==count,'FIXTURE_COUNT');C.validate_rows(grouped(rr,gg),gg);need(all('mode' not in r and 'base_id' not in r for r in rr),'PUBLIC_METADATA')
    need(set(A.NAMES[:16]).isdisjoint(A.NAMES[16:]),'LEXICAL_HOLDOUT')
    # A tiny actual causal decoder exercises the inherited span/attention mapping.
    class Tok:
        def apply_chat_template(self,m,**kw):return m[0]['content']
        def __call__(self,text,**kw):
            spans=[m.span() for m in re.finditer(r'\w+|[^\w\s]',text)];ids=[sum(text[x:y].encode())%32 for x,y in spans];return {'input_ids':torch.tensor([ids]),'offset_mapping':torch.tensor([spans])}
    model=P.fixture_backbone();tok=Tok();net=T.Reader(8,'E_attention',.1,16);rr=[r for r,g in allrows['one']];gg=[g for r,g in allrows['one']];caps=[capture(model,tok,net,r,'cpu') for r in rr];need(len(delta_diagnostics(rr,gg,caps))==1,'DELTA_FIXTURE');probe=gradient_probe(net,rr,gg,caps);need(probe['paired_gradient_norm']>0,'GRADIENT_FIXTURE');P.freeze_check(model,None)
    # Construct an explicitly separable hidden-state fixture to test positive optimization,
    # without claiming that the tiny causal decoder (or Qwen) has the semantic capability.
    def synthetic(r):
        sign=1. if gg[rr.index(r)]['positive'][0] else -1.;base=torch.eye(8)[:6].clone();base[4,0]=sign*3.;base[4,1]=-sign*3.;base[5,2]=sign*4.;base[5,3]=-sign*4.;idx=torch.arange(6).repeat(2,1).T.contiguous();keys=[tuple(x['span']) for x in r['anchors']]+[(80,81),(82,83)];return ([base.clone() for _ in P.LAYERS],idx,keys)
    a=SimpleNamespace(steps=200,lr=.003,device='cpu');initial_state={k:v.clone() for k,v in net.state_dict().items()}
    with contextlib.redirect_stdout(io.StringIO()):fit=optimize(a,model,net,rr,gg,[synthetic(r) for r in rr])
    need(fit['passed'] and fit['final']['metrics']['reversal_pairs_correct']==1,'SEPARABLE_FIT_FIXTURE')
    P.fixture_guard()
    # Exercise actual seal/fit checkpoint, prediction lock, score, predecessor and stop logic.
    # Model predictions are fixture values; no actual Qwen run is implied.
    with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
        root=Path(td);out=root/'out';ident={'gates':{s:[1.,1.] for s in SPLITS},'fixture':True};ctx=(root,out,ident)
        prepare(ctx)
        # Train one via the real lifecycle below; create the second checkpoint as a fixture after that gate.
        def fake_prediction(s,good):
            r,g=cohort(ctx,s,True);d=out/s;pred=[]
            for x,y in zip(r,g):
                z=[1. if k else -1. for k in y['positive']] if good else [0.]*4;pred.append({'case_id':x['case_id'],'prompt_sha256':x['prompt_sha256'],'scores':z,'no_cue_scores':[0.]*4,'native':{'scores':[0.]*4}})
            imm(d/'predictions.json',pred);imm(d/'prediction-lock.json',lock_expected(ctx,s));return score(ctx,s)
        try:previous_gate(ctx,'eight')
        except (RuntimeError,FileNotFoundError):pass
        else:raise AssertionError('GATE_BYPASS')
        orig=(globals()['setup'],globals()['capture'],P.native,C.guard)
        try:
            def fixture_setup(a,ctx):
                n=T.Reader(8,'E_attention',.1,16);n.load_state_dict(initial_state);return model,tok,n
            globals()['setup']=fixture_setup
            def fixture_capture(m,t,n,r,device):
                if r['prompt'] in [x['prompt'] for x in rr]:return synthetic(next(x for x in rr if x['prompt']==r['prompt']))
                return orig[1](m,t,n,r,device)
            globals()['capture']=fixture_capture;P.native=lambda *args:{'scores':[0.]*4};C.guard=lambda:None # tested above in separate real subprocess
            train(a,ctx,'one');train(a,ctx,'one');predict(a,ctx,'one');predict(a,ctx,'one');need(score(ctx,'one')['passed'],'ONE_SCORE_FIXTURE')
        finally:globals()['setup'],globals()['capture'],P.native,C.guard=orig
        d=out/'eight';P.save(d/'reader.safetensors',P.changes(net,None));imm(d/'fit.json',{'identity':digest(ident),'checkpoint':sha(d/'reader.safetensors'),'seal':sha(d/'seal.json'),'passed':True,'parent_checkpoint':sha(out/'one'/'reader.safetensors')});need(fake_prediction('eight',True)['passed'],'EIGHT_SCORE_FIXTURE');need(not fake_prediction('names',False)['passed'],'NAMES_FAILURE_FIXTURE');finish(ctx);need(read(out/'result.json')['status']=='STOPPED_AT_NAMES','STOP_FIXTURE');finish(ctx)
        try:previous_gate(ctx,'paraphrase')
        except RuntimeError:pass
        else:raise AssertionError('FAILED_GATE_BYPASS')
        (out/'names'/'predictions.json').write_text('[]',encoding='utf-8')
        try:score(ctx,'names')
        except RuntimeError:pass
        else:raise AssertionError('TAMPER_ACCEPTED')
    emit('SELFTEST',{'authored_cases':sum(COUNTS),'matched_pair_contract':True,'lexically_disjoint_holdout':True,'actual_0122_reader_causal_fixture':True,'separable_hidden_state_pair_fit':True,'gradient_probe':True,'frozen_backbone':True,'answer_guard_subprocess':True,'stage_stop_resume_and_tamper_fixture':True,'actual_first_stage_train_predict_fixture':True,'live_qwen_cuda':False})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','run','prepare','train','predict','score','finish'),nargs='?',default='audit');p.add_argument('--root',default='../runtime/reports/gamer-v1');p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--steps',type=int,default=200);p.add_argument('--lr',type=float,default=.001);p.add_argument('--stage',choices=SPLITS);a=p.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':return selftest()
    need(a.steps>=1 and math.isfinite(a.lr) and a.lr>0,'CONFIG');ctx=context(a);root,out,ident=ctx
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':ident,'model_loaded':False,'training_cases_max':16,'trainable_backbone_parameters':0,'adapter_used':False});return
    if a.command=='prepare':return prepare(ctx)
    if a.command=='run':
        args=['--root',str(root),'--output',str(out),'--device',a.device,'--steps',str(a.steps),'--lr',str(a.lr)]
        def phase(cmd,stage=None):subprocess.run([sys.executable,'-u','-m',MODULE,cmd,*args,*(['--stage',stage] if stage else [])],check=True)
        phase('prepare')
        for s in SPLITS:
            if s in ('one','eight'):phase('train',s)
            phase('predict',s);phase('score',s)
            if not read(out/s/'result.json')['passed']:break
        phase('finish');return
    need(read(out/'identity.json')==ident,'IDENTITY_DRIFT')
    if a.command=='finish':return finish(ctx)
    need(a.stage is not None,'STAGE_REQUIRED')
    if a.command=='train':need(a.stage in ('one','eight'),'TRAIN_HOLDOUT_FORBIDDEN');return train(a,ctx,a.stage)
    if a.command=='predict':return predict(a,ctx,a.stage)
    return score(ctx,a.stage)

if __name__=='__main__':main()
