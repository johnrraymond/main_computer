"""0125: radius calibration + bounded matched continuations of 0124. Research only."""
from __future__ import annotations
import argparse, copy, gc, hashlib, json, math, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace
import torch
from torch.nn import functional as F
from tools import nanojev_gamer_v1_compensatory_rewrite_0124 as R
TAG='G12_0125_RADIUS'; MODULE='tools.nanojev_gamer_v1_rewrite_radius_0125'
RADII=(0.,.0001,.001,.0025,.01,.025,.1,.25)
METHODS=('B_fixed','C_joint','F_sequential_fuzz','C_adaptive')
AUTHOR=Path(__file__).with_name('nanojev_gamer_v1_rewrite_radius_author_0125.py')
sha,read,imm,digest=R.sha,R.read,R.imm,R.digest

def need(x,msg):
    if not x:raise RuntimeError(TAG+'_'+msg)
def emit(k,v):print(TAG+'_'+k+' '+json.dumps(v,sort_keys=True,allow_nan=False),flush=True)

def context(a):
    root=Path(a.root).resolve();parent=root/'g1-g2-compensatory-rewrite-0124';ident=read(parent/'identity.json');cfg=ident['config'];need(cfg['device']==a.device,'PARENT_DEVICE_MISMATCH');pa=SimpleNamespace(root=str(root),output=str(parent),**cfg);pctx=R.context(pa);need(pctx[2]==ident,'PARENT_IDENTITY');R.validate_reference(pctx)
    out=Path(a.output).resolve() if a.output else root/'g1-g2-rewrite-radius-0125';need(out!=root and out!=parent and parent not in out.parents and out not in parent.parents,'OUTPUT_OVERLAP');need(out.name.startswith('g1-g2-rewrite-radius-0125'),'OUTPUT_NAME_REQUIRED')
    identity={'source':sha(__file__),'author':sha(AUTHOR),'parent_source':sha(R.__file__),'parent_identity':digest(ident),'reference_lock':sha(parent/'reference'/'lock.json'),'device':a.device,'radii':list(RADII),'steps':a.steps,'max_radii':a.max_radii,'max_kl':a.max_kl,'seed':1252026,'methods':list(METHODS),'research_only':True,'stage':2,'dtype':'fp32','selection':'0124_fit_dev_only','fresh_confirmation':'0125_separate_locked'}
    return pctx,out,identity

def sc(ctx):return (ctx[0][0],ctx[1],ctx[2])
def valid_identity(ctx):need(read(ctx[1]/'identity.json')==ctx[2],'IDENTITY_DRIFT')
def prepare(ctx):
    from tools import nanojev_gamer_v1_rewrite_radius_author_0125 as A
    pctx,out,ident=ctx
    if out.exists() and any(out.iterdir()):need((out/'identity.json').is_file() and read(out/'identity.json')==ident,'OUTPUT_NOT_OURS')
    imm(out/'identity.json',ident);z=A.rows();need(len(z)==36 and len({r['id'] for r,g in z})==len(z),'AUTHOR_COUNT');old=set()
    for n in R.all_names():old.update(r['prompt'] for r in R.rows(pctx,n))
    need(not old&{r['prompt'] for r,g in z},'PROMPT_COLLISION');R.seal(sc(ctx),'confirmation',[r for r,g in z],[g for r,g in z]);emit('PREPARED',{'new_math':20,'new_preservation':16,'prior_prompts':len(old),'old_blind_reused_only_as_regression':True})

def load(a,ctx):return R.load(a,ctx[0])
def setup(a,ctx):
    model,tok,net=load(a,ctx);model.eval().requires_grad_(True);net.eval().requires_grad_(False);ps=list(model.parameters());need(all(p.requires_grad and p.dtype==torch.float32 for p in ps),'THAW');labels=R.C.label_ids(tok,('A','B'),True);pctx=ctx[0];train=R.deck(tok,R.rows(pctx,'cards_2_fit',True));dev=R.deck(tok,R.rows(pctx,'cards_2_dev',True));pres,teach=R.preserved(pctx,tok,'fit',labels,2);dp,dt=R.preserved(pctx,tok,'dev',labels,2);gnet,gt=R.graph_reader(a,pctx,model);grows=R.rows(pctx,'graph_exposed');refgen=read(pctx[1]/'reference'/'cards_2_dev-free.json');return SimpleNamespace(model=model,tok=tok,net=net,ps=ps,labels=labels,train=train,dev=dev,pres=pres,teach=teach,dp=dp,dt=dt,gnet=gnet,gt=gt,grows=grows,refgen=refgen,device=a.device,pctx=pctx)

@torch.no_grad()
def measure(d,which):
    train=which=='fit';return R.measure(d.model,d.net,2,d.train if train else d.dev,[],d.pres if train else d.dp,d.teach if train else d.dt,d.device,d.labels)
@torch.no_grad()
def evaluate(d):
    fit=measure(d,'fit');dev=measure(d,'dev');gen=[{'id':x['r']['id'],'generation':R.C.generate(d.model,d.tok,R.free_prompt(x['r']),d.device,None,20)} for x in d.dev];free=R.grade_free(d.dev,gen,d.refgen);g=R.graph_review(d.pctx,R.graph_preds(SimpleNamespace(device=d.device),d.model,d.tok,d.gnet,d.gt,d.grows));return {'fit':fit,'dev':dev,'free':free,'g1':{k:v for k,v in g.items() if k!='details'}}

def safe(v,max_kl):
    return all(not v[k]['preservation']['lost'] and v[k]['preservation']['mean_kl']<=max_kl for k in ('fit','dev')) and not v['free']['lost'] and v['g1']['passed']
def rank(v):return (v['free']['current_correct'],v['dev']['acquisition']['correct'],-v['dev']['loss'],-v['dev']['preservation']['mean_kl'])
def improvement(v,b):return v['free']['current_correct']>b['free']['current_correct'] or v['dev']['acquisition']['correct']>b['dev']['acquisition']['correct'] or v['dev']['loss']<b['dev']['loss']-1e-6

def summary(v):return {'fit_loss':v['fit']['loss'],'dev_loss':v['dev']['loss'],'dev_choice':v['dev']['acquisition']['correct'],'dev_free':v['free']['current_correct'],'dev_free_baseline':v['free'].get('baseline_correct'),'dev_free_gains':len(v['free'].get('gained',[])),'fit_kl':v['fit']['preservation']['mean_kl'],'dev_kl':v['dev']['preservation']['mean_kl'],'dev_max_kl':v['dev']['preservation']['max_kl'],'fit_lost':len(v['fit']['preservation']['lost']),'dev_lost':len(v['dev']['preservation']['lost']),'free_lost':len(v['free']['lost']),'g1_lost':v['g1']['lost_correct_objects'],'g1_pass':v['g1']['passed']}

def direction(d,joint=False):
    R.clear(d.ps);R.acquisition(d.model,d.net,2,d.train,[],d.device,d.labels,True)
    if joint:R.preservation_loss(d.model,d.pres,d.teach,d.device)
    v,n=R.unit(R.gradvec(d.ps));R.clear(d.ps);need(math.isfinite(n),'NONFINITE_GRADIENT');return v,n

def displacement(ps,ss):
    # Measure stored FP32 movement, rather than assuming radius == effective change.
    q=0.;count=0;total=0
    for p,x in zip(ps,ss):
        v=p.detach().cpu().reshape(-1);u=x.reshape(-1);total+=u.numel()
        for i,j in zip(v.split(1048576),u.split(1048576)):
            t=i-j;count+=int(torch.count_nonzero(t));q+=float(t.double().square().sum())
    return {'actual_l2':math.sqrt(q),'changed_parameters':count,'eligible_parameters':total}

def exact_restore(ps,ss):
    R.restore(ps,ss);need(all(torch.equal(p.detach().cpu(),x) for p,x in zip(ps,ss)),'RESTORE_NOT_EXACT')

def radius_name(r):return ('%.8f'%r).rstrip('0').rstrip('.').replace('.','p') or '0'
def choose_radii(records,max_radii,max_kl):
    base=records[0]['metrics'];eligible=[x for x in records[1:] if safe(x['metrics'],max_kl)];useful=[x for x in eligible if improvement(x['metrics'],base)]
    # Keep the best local candidate and the largest safe radius (if distinct), then other useful ones.
    ordered=sorted(useful or eligible,key=lambda x:(rank(x['metrics']),x['radius']),reverse=True);res=[]
    if ordered:res.append(ordered[0]['radius'])
    if eligible:
        largest=max(x['radius'] for x in eligible)
        if largest not in res:res.append(largest)
    for x in ordered:
        if x['radius'] not in res:res.append(x['radius'])
    return res[:max_radii]

def calibration(a,ctx):
    out=ctx[1];f=out/'calibration.json'
    if f.exists():v=read(f);need(v['identity']==digest(ctx[2]),'CALIBRATION_DRIFT');return v
    d=setup(a,ctx);ss=R.snapshot(d.ps);v,n=direction(d);need(n>0,'ZERO_ACQUISITION_GRADIENT');records=[]
    for r in RADII:
        exact_restore(d.ps,ss)
        if r:R.move(d.ps,v,-r)
        val=evaluate(d);need(r!=0. or safe(val,a.max_kl),'ZERO_CONTROL_REGRESSION');rec={'radius':r,'movement':displacement(d.ps,ss),'metrics':val,'safe':safe(val,a.max_kl)};records.append(rec);emit('PROBE',{'radius':r,'safe':rec['safe'],**rec['movement'],**summary(val)})
    exact_restore(d.ps,ss);chosen=choose_radii(records,a.max_radii,a.max_kl);report={'identity':digest(ctx[2]),'gradient_norm':n,'records':records,'selected_radii':chosen,'restored_exactly':True,'selection_basis':'dev preservation and acquisition, no blind labels'};imm(f,report);emit('CALIBRATED',{'selected_radii':chosen,'safe_radii':[x['radius'] for x in records if x['safe']]});return report

def fit_path(ctx,r,method):return ctx[1]/'trials'/radius_name(r)/method

def adaptive(d,r,max_kl):
    before=measure(d,'fit');v,n=direction(d,True);ss=R.snapshot(d.ps);attempts=[];chosen=0.
    for f in (1.,.5,.25,.1):
        exact_restore(d.ps,ss);R.move(d.ps,v,-r*f);q=measure(d,'fit');p=q['preservation'];accepted=not p['lost'] and p['mean_kl']<=max_kl and q['loss']<before['loss']-1e-7;attempts.append({'scale':f,'loss':q['loss'],'kl':p['mean_kl'],'lost':len(p['lost']),'accepted':accepted})
        if accepted:chosen=f;break
    if not chosen:exact_restore(d.ps,ss)
    return {'gradient_norm':n,'selected_scale':chosen,'attempts':attempts}

def trial(a,ctx,r,method):
    p=fit_path(ctx,r,method);rec=p/'fit.json';cal=read(ctx[1]/'calibration.json');need(cal['identity']==digest(ctx[2]),'CAL_IDENTITY');need(r in cal['selected_radii'] and method in METHODS,'NOT_SELECTED_TRIAL');calhash=sha(ctx[1]/'calibration.json')
    if rec.exists():
        q=read(rec);need(q['identity']==digest(ctx[2]) and q['calibration']==calhash and (q['checkpoint'] is None or q['checkpoint']==sha(p/'checkpoint.pt')),'TRIAL_DRIFT');return q
    d=setup(a,ctx);ss=R.snapshot(d.ps);initial=evaluate(d);need(safe(initial,a.max_kl),'TRIAL_ZERO_CONTROL_REGRESSION');history=[];start=time.monotonic();repairs=[]
    for step in range(1,a.steps+1):
        if method=='C_adaptive':info=adaptive(d,r,a.max_kl)
        else:
            v,n=direction(d,method=='C_joint');R.move(d.ps,v,-r);del v;info={'gradient_norm':n,'selected_scale':1.}
        q=measure(d,'fit');history.append({'step':step,'update':info,'loss':q['loss'],'acquisition':q['acquisition'],'preservation':q['preservation']});emit('UPDATE',{'method':method,'radius':r,'step':step,'loss':q['loss'],'correct':q['acquisition']['correct'],'kl':q['preservation']['mean_kl'],'lost':len(q['preservation']['lost']),'scale':info['selected_scale']})
    after_acquisition=evaluate(d)
    if method=='F_sequential_fuzz':
        q=R.fuzz_repair(d.model,d.net,d.ps,2,d.train,[],d.pres,d.teach,d.device,d.labels,r,ctx[2]['seed'],a);repairs.append(q);emit('REPAIR',{'radius':r,'before_kl':q['before']['preservation']['mean_kl'],'after_kl':q['after']['preservation']['mean_kl'],'scale':q['selected_scale']})
    final=evaluate(d);good=safe(final,a.max_kl);better=improvement(final,initial);ck=None
    # Store locally safe candidates with positive measured acquisition signal only.
    if good and better:
        d.model.requires_grad_(False);R.atomic_pt(p/'checkpoint.pt',{'model':d.model.state_dict()});ck=sha(p/'checkpoint.pt')
    result={'identity':digest(ctx[2]),'calibration':calhash,'radius':r,'method':method,'checkpoint':ck,'safe':good,'improved':better,'initial':initial,'history':history,'after_acquisition':after_acquisition,'repairs':repairs,'final':final,'movement':displacement(d.ps,ss),'seconds':time.monotonic()-start};imm(rec,result);emit('TRIAL',{'radius':r,'method':method,'safe':good,'improved':better,'checkpoint_saved':bool(ck),**summary(final)});return result

def lock_trials(ctx):
    out=ctx[1];cal=read(out/'calibration.json');need(cal['identity']==digest(ctx[2]),'CAL_IDENTITY');records=[];files={}
    for r in cal['selected_radii']:
        for m in METHODS:
            p=fit_path(ctx,r,m);q=read(p/'fit.json');need(q['identity']==digest(ctx[2]) and q['calibration']==sha(out/'calibration.json') and q['radius']==r and q['method']==m,'TRIAL_IDENTITY');need(q['checkpoint'] is None or q['checkpoint']==sha(p/'checkpoint.pt'),'CHECKPOINT_DRIFT');files[str(p.relative_to(out))]=sha(p/'fit.json');records.append(q)
    candidates=[q for q in records if q['checkpoint'] and q['safe'] and q['improved']];winner=max(candidates,key=lambda q:(rank(q['final']),-q['movement']['actual_l2'])) if candidates else None
    lock={'identity':digest(ctx[2]),'calibration':sha(out/'calibration.json'),'trials':files,'winner':None if winner is None else {'radius':winner['radius'],'method':winner['method'],'checkpoint':winner['checkpoint']},'confirmation_seal':sha(out/'confirmation'/'seal.json')};imm(out/'selection.json',lock);emit('SELECTED',lock['winner']);return lock

def validate_selection(ctx):
    out=ctx[1];v=read(out/'selection.json');need(v['identity']==digest(ctx[2]) and v['calibration']==sha(out/'calibration.json') and v['confirmation_seal']==sha(out/'confirmation'/'seal.json'),'SELECTION_DRIFT')
    for p,h in v['trials'].items():need(sha(out/p/'fit.json')==h,'FIT_DRIFT')
    if v['winner']:
        w=v['winner'];need(w['checkpoint']==sha(fit_path(ctx,w['radius'],w['method'])/'checkpoint.pt'),'WINNER_DRIFT')
    return v

@torch.no_grad()
def capture(model,tok,r,device,labels,generate):
    zz=[R.logits(model,x,device).log_softmax(-1).cpu() for x in R.tokens(tok,r)];ans={'id':r.get('id',r.get('case_id')),'margin':float(R.canonical(zz,labels)),'orders':[float(R.margin(x,labels)) for x in zz]}
    if generate:ans['generation']=R.C.generate(model,tok,R.free_prompt(r),device,None,20)
    return ans,zz

def prediction_lock(ctx):return {'identity':digest(ctx[2]),'selection':sha(ctx[1]/'selection.json'),'predictions':sha(ctx[1]/'predictions.json')}

def predict(a,ctx):
    out=ctx[1];v=validate_selection(ctx);w=v['winner'];need(w is not None,'NO_WINNER')
    if (out/'prediction-lock.json').exists():need(read(out/'prediction-lock.json')==prediction_lock(ctx),'PREDICTION_DRIFT');return
    R.C.guard();model,tok,net=load(a,ctx);labels=R.C.label_ids(tok,('A','B'),True);pctx=ctx[0];names=('binding_fit','native_blind','cards_0_blind','cards_2_blind');decks={n:R.rows(pctx,n) for n in names};decks['confirmation']=R.rows(sc(ctx),'confirmation');base={};refs={}
    for n,rr in decks.items():
        base[n]=[];refs[n]=[]
        for r in rr:
            gen=n=='cards_2_blind' or (n=='confirmation' and r['kind'] in ('math','preserve_math'));p,t=capture(model,tok,r,a.device,labels,gen);base[n].append(p);refs[n].append(t)
    ck=torch.load(fit_path(ctx,w['radius'],w['method'])/'checkpoint.pt',map_location='cpu',weights_only=True);model.load_state_dict(ck['model'],strict=True);del ck;model.eval().requires_grad_(False);pred={}
    for n,rr in decks.items():
        arr=[]
        for r,b,t in zip(rr,base[n],refs[n]):
            p,s=capture(model,tok,r,a.device,labels,'generation' in b);p['reference']=b;p['kl']=[max(0.,float(F.kl_div(x,y,reduction='sum',log_target=True))) for x,y in zip(s,t)];arr.append(p)
        pred[n]=arr;emit('PREDICT',{'deck':n,'cases':len(arr)})
    gnet,gt=R.graph_reader(a,pctx,model);pred['graph_exposed']=R.graph_preds(a,model,tok,gnet,gt,R.rows(pctx,'graph_exposed'));imm(out/'predictions.json',pred);imm(out/'prediction-lock.json',prediction_lock(ctx));emit('LOCKED',{'decks':list(pred)})

def evaluate_predictions(ctx,pred):
    need(set(pred)=={'binding_fit','native_blind','cards_0_blind','cards_2_blind','confirmation','graph_exposed'},'PRED_DECKS');pctx=ctx[0];groups={};pres=[];newmath=[];oldmath=[];details=[]
    for name,pp in pred.items():
        if name=='graph_exposed':continue
        pairs=R.rows(sc(ctx) if name=='confirmation' else pctx,name,True);need(len(pp)==len(pairs),'PRED_COUNT')
        for (r,g),p in zip(pairs,pp):
            need(p['id']==r.get('id',r.get('case_id'))==p['reference']['id'],'PRED_ALIGN');sign=1 if R.target(r,g)==0 else -1;row={'id':p['id'],'deck':name,'family':r.get('family','native_binding'),'baseline_correct':p['reference']['margin']*sign>0,'correct':p['margin']*sign>0,'mean_kl':sum(p['kl'])/2,'prediction':p}
            if 'generation' in p:
                row['baseline_free']=R.exact_generated(p['reference']['generation']['text'],g['answer']);row['free']=R.exact_generated(p['generation']['text'],g['answer'])
            if name=='confirmation' and r['kind']=='math':newmath.append(row)
            if name=='cards_2_blind':oldmath.append(row)
            if name in ('binding_fit','native_blind') or (name=='cards_0_blind' and not row['family'].startswith('preserve_math_')) or (name=='confirmation' and r['kind']=='preserve_language') or row['baseline_correct'] or row.get('baseline_free',False):pres.append(row)
            details.append(row)
    def agg(items):
        need(bool(items),'EMPTY_SCORE_GROUP');return {'cases':len(items),'baseline_correct':sum(x['baseline_correct'] for x in items),'correct':sum(x['correct'] for x in items),'lost_choice':[x['id'] for x in items if x['baseline_correct'] and not x['correct']],'gained_choice':[x['id'] for x in items if x['correct'] and not x['baseline_correct']],'baseline_free':sum(x.get('baseline_free',False) for x in items),'free':sum(x.get('free',False) for x in items),'lost_free':[x['id'] for x in items if x.get('baseline_free',False) and not x.get('free',False)],'gained_free':[x['id'] for x in items if x.get('free',False) and not x.get('baseline_free',False)],'mean_kl':sum(x['mean_kl'] for x in items)/len(items),'max_case_mean_kl':max(x['mean_kl'] for x in items)}
    groups={'fresh_math':agg(newmath),'exposed_0124_math':agg(oldmath),'all_preservation':agg(pres),'fresh_preservation':agg([x for x in pres if x['deck']=='confirmation'])};g1=R.graph_review(pctx,pred['graph_exposed']);q=groups['all_preservation'];fresh=groups['fresh_math'];g=groups['fresh_preservation'];ok=not q['lost_choice'] and not q['lost_free'] and q['mean_kl']<=ctx[2]['max_kl'] and g['mean_kl']<=ctx[2]['max_kl'] and g1['passed'];gain=bool(fresh['gained_choice'] or fresh['gained_free']);gate=ok and fresh['correct']/fresh['cases']>=.8 and fresh['free']/fresh['cases']>=.6 and gain
    return {'groups':groups,'g1':g1,'preservation_passed':ok,'fresh_gain':gain,'fresh_gate_passed':gate,'details':details}

def finish(ctx):
    out=ctx[1];v=validate_selection(ctx);cal=read(out/'calibration.json');tr=[]
    for p in v['trials']:
        q=read(out/p/'fit.json');tr.append({'radius':q['radius'],'method':q['method'],'safe':q['safe'],'improved':q['improved'],'checkpoint':q['checkpoint'],'movement':q['movement'],'seconds':q['seconds'],'initial':summary(q['initial']),'before_repair':summary(q['after_acquisition']),'final':summary(q['final']),'repairs':[{'selected_scale':x['selected_scale'],'before_kl':x['before']['preservation']['mean_kl'],'after_kl':x['after']['preservation']['mean_kl']} for x in q['repairs']]})
    status='NO_SAFE_IMPROVING_CANDIDATE';confirm=None
    if v['winner']:
        need(read(out/'prediction-lock.json')==prediction_lock(ctx),'PREDICTION_DRIFT');confirm=evaluate_predictions(ctx,read(out/'predictions.json'));imm(out/'confirmation-result.json',confirm);status='FRESH_GATE_PASSED_RESEARCH_ONLY' if confirm['fresh_gate_passed'] else 'FRESH_GATE_NOT_PASSED'
    report={'identity':digest(ctx[2]),'status':status,'probes':[{'radius':x['radius'],'safe':x['safe'],'movement':x['movement'],**summary(x['metrics'])} for x in cal['records']],'selected_radii':cal['selected_radii'],'trials':tr,'winner':v['winner'],'confirmation':None if confirm is None else {k:x for k,x in confirm.items() if k not in ('details','g1')}|{'g1':{k:x for k,x in confirm['g1'].items() if k!='details'}},'certification':False,'production_modified':False};imm(out/'result.json',report);emit('RESULT',report)

def selftest():
    from tools import nanojev_gamer_v1_rewrite_radius_author_0125 as A
    import tempfile
    z=A.rows();need(len(z)==36 and len({r['id'] for r,g in z})==36 and all(r['choices'][g['correct']]==g['answer'] for r,g in z),'AUTHOR_FIXTURE');need(sum(r['kind']=='math' for r,g in z)==20,'MATH_COUNT')
    def fake(loss,kl,correct=5,free=0,lost=False):
        q={'loss':loss,'acquisition':{'correct':correct},'preservation':{'mean_kl':kl,'max_kl':kl,'lost':['x'] if lost else []}};return {'fit':copy.deepcopy(q),'dev':copy.deepcopy(q),'free':{'current_correct':free,'lost':[]},'g1':{'passed':True,'lost_correct_objects':0}}
    records=[{'radius':r,'metrics':fake(1.-r,.001 if r<=.01 else 1.)} for r in RADII];need(choose_radii(records,2,.1)==[.01,.0025],'RADIUS_SELECTION');records[-1]['metrics']=fake(.001,.001,10,10,True);need(.25 not in choose_radii(records,2,.1),'LOST_DECISION_REJECTED');need(choose_radii([records[0]]+[{'radius':r,'metrics':fake(.1,1.)} for r in RADII[1:]],2,.1)==[],'NO_SAFE_RADIUS')
    # Use actual inherited tensor update/restore, not a scalar stand-in.
    ps=[torch.nn.Parameter(torch.tensor([1.,-2.])),torch.nn.Parameter(torch.tensor([3.]))];ss=R.snapshot(ps);vv=[torch.tensor([.6,.0]),torch.tensor([.8])];R.move(ps,vv,-.01);mv=displacement(ps,ss);need(abs(mv['actual_l2']-.01)<1e-6 and mv['changed_parameters']==2,'DISPLACEMENT');exact_restore(ps,ss);need(displacement(ps,ss)['actual_l2']==0.,'RESTORE')
    # Exercise nonlinear backtracking with actual tensor movement, deterministic fixture objective.
    orig=(globals()['measure'],globals()['direction']);d=SimpleNamespace(ps=ps)
    try:
        def fixture_measure(d,which):
            t=float(d.ps[0][0].detach());return {'loss':t,'preservation':{'mean_kl':(1-t)**2*100.,'lost':[]}}
        globals()['measure']=fixture_measure;globals()['direction']=lambda d,joint:(vv,1.)
        q=adaptive(d,.25,.1);need(q['selected_scale']==.1 and len(q['attempts'])==4,'ADAPTIVE_BACKTRACK');exact_restore(ps,ss);q=adaptive(d,.25,0.);need(q['selected_scale']==0. and displacement(ps,ss)['actual_l2']==0.,'ADAPTIVE_REJECT_RESTORE')
    finally:globals()['measure'],globals()['direction']=orig
    # Lock verification with actual file/checkpoint operations (no model inference).
    with tempfile.TemporaryDirectory() as td:
        out=Path(td);ctx=((out,out,{}),out,{'fixture':True});R.seal(sc(ctx),'confirmation',[r for r,g in z],[g for r,g in z]);imm(out/'calibration.json',{'identity':digest(ctx[2]),'selected_radii':[.01]});h=sha(out/'calibration.json')
        for m in METHODS:
            p=fit_path(ctx,.01,m);checkpoint=None
            if m=='C_joint':R.atomic_pt(p/'checkpoint.pt',{'model':{'w':torch.ones(1)}});checkpoint=sha(p/'checkpoint.pt')
            imm(p/'fit.json',{'identity':digest(ctx[2]),'calibration':h,'radius':.01,'method':m,'checkpoint':checkpoint,'safe':True,'improved':True,'final':fake(.5,.01,6,1),'movement':{'actual_l2':.01}})
        v=lock_trials(ctx);need(v['winner']['method']=='C_joint' and validate_selection(ctx)==v,'LOCK_FIXTURE');(fit_path(ctx,.01,'B_fixed')/'fit.json').write_text('{}')
        try:validate_selection(ctx)
        except RuntimeError:pass
        else:raise AssertionError('TAMPER_NOT_DETECTED')
    emit('SELFTEST',{'author':True,'radius_selection':True,'unsafe_radius_rejection':True,'actual_tensor_displacement_and_restore':True,'adaptive_backtracking_and_rejection':True,'selection_lock_and_tamper':True,'live_qwen_cuda':False,'parent_integration_live':False})

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('audit','selftest','run','prepare','calibrate','trial','select','predict','finish'),nargs='?',default='audit');p.add_argument('--root',default='../runtime/reports/gamer-v1');p.add_argument('--output');p.add_argument('--device',default='cuda');p.add_argument('--steps',type=int,default=12);p.add_argument('--max-radii',type=int,default=2);p.add_argument('--max-kl',type=float,default=.1);p.add_argument('--radius',type=float);p.add_argument('--method',choices=METHODS);a=p.parse_args();torch.set_num_threads(min(4,torch.get_num_threads()))
    if a.command=='selftest':return selftest()
    need(a.steps>=1 and 1<=a.max_radii<=len(RADII)-1 and math.isfinite(a.max_kl) and 0<=a.max_kl<=.1,'CONFIG');ctx=context(a);out=ctx[1]
    if a.command=='audit':emit('AUDIT',{'ready':True,'identity':ctx[2],'parent_reference_verified':True,'original_weights_immutable':True,'training_dimensions':'all','maximum_saved_candidates':a.max_radii*len(METHODS),'model_loaded':False});return
    if a.command=='prepare':return prepare(ctx)
    if a.command=='run':
        args=['--root',str(ctx[0][0]),'--output',str(out),'--device',a.device,'--steps',str(a.steps),'--max-radii',str(a.max_radii),'--max-kl',str(a.max_kl)]
        def phase(cmd,extra=()):subprocess.run([sys.executable,'-u','-m',MODULE,cmd,*args,*extra],check=True)
        phase('prepare');phase('calibrate')
        for r in read(out/'calibration.json')['selected_radii']:
            for m in METHODS:phase('trial',('--radius',str(r),'--method',m))
        phase('select')
        if read(out/'selection.json')['winner']:phase('predict')
        phase('finish');return
    valid_identity(ctx)
    if a.command=='calibrate':return calibration(a,ctx)
    if a.command=='trial':need(a.radius is not None and a.method is not None,'TRIAL_ARGUMENTS');return trial(a,ctx,a.radius,a.method)
    if a.command=='select':return lock_trials(ctx)
    if a.command=='predict':return predict(a,ctx)
    return finish(ctx)
if __name__=='__main__':main()
