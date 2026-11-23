"""G2-0102: encounter reconstructability ceiling and targeted proposal bakeoff.

Research-only. Run from main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_reconstructability_0102 audit
  python -u -m tools.nanojev_gamer_v1_g2_reconstructability_0102 selftest
  python -u -m tools.nanojev_gamer_v1_g2_reconstructability_0102 run --device cuda

Oracles use gold only for retrospective upper bounds. Inference never sees gold,
family, case ID or fold membership. Folds match sealed G2-0100/0101 base splits;
inner validation alone selects proposal thresholds. All previously trained models
and production routes remain unchanged. No G3 certification.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import copy
import json
import random
from pathlib import Path
import tempfile

import torch
from torch.nn import functional as F
from safetensors.torch import save_file, load_file
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG='G2_0102_RECONSTRUCTABILITY'
SCHEMA='nanojev.g2.reconstructability-0102.v1'
SEED=r.SEED
SUPPORTS=('expert_decoded','raw_top1_positive','raw_top2_positive',
          'raw_any_positive','raw_top2_unthresholded','raw_any_unthresholded')
THRESHOLDS=(.70,.85,.95,.99)
ARMS=('frozen_0100','targeted_proposer')


def emit(stage,payload):
    print(TAG+'_'+stage+' '+json.dumps(payload,sort_keys=True,allow_nan=False),flush=True)


def require(ok,reason):
    if not ok:raise ValueError(TAG+'_'+reason)


def _source(root,output):
    source,experts,r99,r100,p100=r._source(root,output)
    reportfile=source[8]/'graph-reconstruction-0101'/'result.json'
    require(reportfile.is_file(),'0101_REPORT_MISSING')
    report=old.read(reportfile)
    require(report.get('schema')==r.SCHEMA and report.get('accepted_on_reused_fit_cv') is False,
            '0101_INVALID_RESEARCH_REPORT')
    require(report.get('source_0100_sha256')==old.digest(p100),
            '0100_0101_SOURCE_SHA_MISMATCH')
    require(report.get('confirmation_used') is False and
            report.get('dev_used') is False and report.get('production_promoted') is False,
            '0101_POLICY_DRIFT')
    require(len(report['folds'])==3,'0101_FOLD_COUNT_CHANGED')
    for i in range(3):
        require(report['folds'][i]['base_counts']==r100['folds'][i]['base_counts'] and
                report['folds'][i]['fold']==i,'0100_0101_FOLD_DRIFT')
    return source,experts,r99,r100,report,reportfile


def _supports(rec,experts,device):
    """Seven frozen experts' *inference-only* candidate sets, independent of gold."""
    margins,pred=b._inputs(rec,experts,device)
    # Return CPU for deterministic structured-oracle diagnostics.
    margins=margins.detach().cpu();pred=pred.detach().cpu()
    e,n,n2,types=margins.shape
    require(e==len(experts)==b.E and n==n2 and types==b.R,'EXPERT_SURFACE_DRIFT')
    result={k:torch.zeros((n,n,b.R+1),dtype=torch.bool) for k in SUPPORTS}
    for expert in range(e):
        result['expert_decoded'].scatter_(-1,pred[expert,...,None].long(),True)
        top1=margins[expert].argmax(-1)
        topk=margins[expert].topk(min(2,b.R),dim=-1).indices
        for k in range(b.R):
            positive=margins[expert,:,:,k]>0
            result['raw_any_positive'][:,:,k+1]|=positive
            result['raw_top1_positive'][:,:,k+1]|=positive&(top1==k)
            result['raw_top2_positive'][:,:,k+1]|=positive&(topk==k).any(-1)
            result['raw_top2_unthresholded'][:,:,k+1]|=(topk==k).any(-1)
    result['raw_any_unthresholded'][:,:,:]=True
    for mask in result.values():
        mask[:,:,0]=False
        mask.diagonal(dim1=0,dim2=1).fill_(False)
    return result,margins,pred


def _forced(gold,support,spec,objects):
    """Oracle, never deployable: set gold edges to +100 ONLY when supported.

    All nongold edges/relations become -100. A failure here proves that no
    label selection from this candidate inventory can reproduce the graph
    through the SAME structured decoder with the frozen capacities.
    """
    gold=gold.cpu().long();n=gold.shape[0]
    require(support.shape==(n,n,b.R+1),'ORACLE_SUPPORT_SHAPE')
    score=torch.full((n,n,b.R+1),-100.,dtype=torch.float)
    score[:,:,0]=0.
    for k in range(1,b.R+1):
        enable=(gold==k)&support[:,:,k]
        score[:,:,k][enable]=100.
    predicted=r._decode(score,objects,spec,'cpu')
    return predicted


def _relaxed(spec,objects,gold):
    """Counterfactual gold-induced cap relaxation, strictly for diagnosis."""
    cfg=copy.deepcopy(spec)
    require(cfg.get('method') in ('joint','source_topk'), 'UNSUPPORTED_DECODER_METHOD')
    n=len(objects)
    if cfg['method']=='source_topk':cfg['k']=max(n,int(cfg.get('k',2)))
    else:
        out=cfg.setdefault('out_caps',{});incoming=cfg.setdefault('in_caps',{})
        for i in range(n):
            for j in range(n):
                if i==j or int(gold[i,j])==0:continue
                name=b.BIND_CLASSES[int(gold[i,j])]
                key='|'.join((objects[i][2],objects[j][2],name))
                rev='|'.join((objects[j][2],objects[i][2],name))
                needed_out=sum(int(gold[i,k])==int(gold[i,j]) and objects[k][2]==objects[j][2]
                               for k in range(n) if k!=i)
                needed_in=sum(int(gold[k,j])==int(gold[i,j]) and objects[k][2]==objects[i][2]
                              for k in range(n) if k!=j)
                out[key]=max(int(out.get(key,2)),needed_out)
                incoming[rev]=max(int(incoming.get(rev,2)),needed_in)
    return cfg


def _cap_violations(spec,objects,gold):
    src=Counter();dst=Counter();n=len(objects)
    for i in range(n):
        for j in range(n):
            if i==j or not int(gold[i,j]):continue
            kind=b.BIND_CLASSES[int(gold[i,j])]
            src[(i,'|'.join((objects[i][2],objects[j][2],kind)))]+=1
            dst[(j,'|'.join((objects[j][2],objects[i][2],kind)))]+=1
    out=[];incoming=[]
    if spec['method']=='joint':
        out=[{'node':i,'typed_key':key,'gold_count':cnt,'cap':int(spec.get('out_caps',{}).get(key,2))}
             for (i,key),cnt in src.items() if cnt>int(spec.get('out_caps',{}).get(key,2))]
        incoming=[{'node':j,'typed_key':key,'gold_count':cnt,'cap':int(spec.get('in_caps',{}).get(key,2))}
             for (j,key),cnt in dst.items() if cnt>int(spec.get('in_caps',{}).get(key,2))]
    elif spec['method']=='source_topk':
        out=[{'node':i,'typed_key':key,'gold_count':cnt,'cap':int(spec.get('k',2))}
             for (i,key),cnt in src.items() if cnt>int(spec.get('k',2))]
    return {'out':out,'in':incoming}


def _diagnose(rec,experts,spec,device,baseline=None):
    item=rec['item'];objects=item['objects'];gold=item['targets'].cpu().long()
    n=len(objects);valid=~torch.eye(n,dtype=torch.bool)
    supports,margins,pred=_supports(rec,experts,device)
    unrestricted=torch.ones_like(next(iter(supports.values())))
    oracle=_forced(gold,unrestricted,spec,objects)
    relaxed_spec=_relaxed(spec,objects,gold)
    relaxed=_forced(gold,unrestricted,relaxed_spec,objects)
    oracle_exact=bool(torch.equal(oracle,gold))
    relaxed_exact=bool(torch.equal(relaxed,gold))
    require(relaxed_exact,'RELAXED_GOLD_GRAPH_UNREPRESENTABLE')
    capacity=_cap_violations(spec,objects,gold)
    mode_stats={}
    gold_pos=valid&(gold>0)
    relation_summary=defaultdict(Counter)
    for name,support in supports.items():
        gold_supported=support.gather(-1,gold[...,None]).squeeze(-1)&gold_pos
        pred_oracle=_forced(gold,support,spec,objects)
        mode_stats[name]={'gold_supported':int(gold_supported.sum()),
            'gold_missing':int((gold_pos&~gold_supported).sum()),
            'fully_covered':bool((gold_pos&~gold_supported).sum()==0),
            'oracle_exact_under_caps':bool(torch.equal(pred_oracle,gold)),
            'oracle_tp':int(((pred_oracle==gold)&gold_pos).sum()),
            'oracle_fn':int(((pred_oracle!=gold)&gold_pos).sum())}
        for label in range(1,b.R+1):
            mask=gold==label
            relation_summary[b.BIND_CLASSES[label]][name+'_supported']+=int((mask&gold_supported).sum())
            if name==SUPPORTS[0]:
                relation_summary[b.BIND_CLASSES[label]]['gold']+=int(mask.sum())
    if baseline is not None:
        actual=m._count(rec,baseline)
    else:actual=None
    missing=(gold_pos&~supports['expert_decoded'].gather(-1,gold[...,None]).squeeze(-1))
    raw_covered=supports['raw_any_positive'].gather(-1,gold[...,None]).squeeze(-1)
    # This is a mutually exclusive causal classification of EXTERNAL obstacles.
    if not oracle_exact:classification='structural_decoder_constraint'
    elif mode_stats['expert_decoded']['gold_missing']:
        classification=('expert_decoder_suppression_or_ranking' if
            int((missing&~raw_covered).sum())==0 else
            'raw_candidate_signal_missing')
    elif actual and actual['exact']==0:classification='routing_ranking_or_verification'
    elif actual is None:classification='expert_candidates_complete_not_checked'
    else:classification='already_exact'
    result={'family':item['row']['family'],'base_id':str(item['row']['base_id']),
        'case_id':str(item['row'].get('case_id',item['row'].get('id',''))),
        'objects':n,'gold_edges':int(gold_pos.sum()),
        'gold_oracle_under_caps_exact':oracle_exact,
        'gold_oracle_relaxed_caps_exact':relaxed_exact,
        'gold_oracle_under_caps_tp':int(((oracle==gold)&gold_pos).sum()),
        'cap_violations':capacity,'supports':mode_stats,
        'decoding_obstacle':classification,
        'missing_decoded_but_raw_positive':int((missing&raw_covered).sum()),
        'missing_decoded_and_raw_nonpositive':int((missing&~raw_covered).sum()),
        'gold_relation_coverage':{k:dict(v) for k,v in relation_summary.items()}}
    if actual:result['observed_graph']={k:actual[k] for k in ('tp','fp','fn','exact')}
    return result


def _summary(rows):
    totals=Counter();family=defaultdict(Counter);byrelation=defaultdict(Counter)
    for item in rows:
        f=item['family'];a=family[f]
        a['cases']+=1;a['gold_edges']+=item['gold_edges']
        a['structural_oracle_exact']+=item['gold_oracle_under_caps_exact']
        a['relaxed_oracle_exact']+=item['gold_oracle_relaxed_caps_exact']
        a['structural_gold_tp']+=item['gold_oracle_under_caps_tp']
        a['out_cap_violations']+=len(item['cap_violations']['out'])
        a['in_cap_violations']+=len(item['cap_violations']['in'])
        a['raw_latent_missing']+=item['missing_decoded_but_raw_positive']
        a['raw_unsupported_missing']+=item['missing_decoded_and_raw_nonpositive']
        a['observed_exact']+=item.get('observed_graph',{}).get('exact',0)
        a['cause_'+item['decoding_obstacle']]+=1
        for mode,data in item['supports'].items():
            a[mode+'_gold_supported']+=data['gold_supported']
            a[mode+'_oracle_exact']+=int(data['oracle_exact_under_caps'])
            a[mode+'_full_coverage']+=int(data['fully_covered'])
        for rel,stat in item['gold_relation_coverage'].items():
            for key,value in stat.items():byrelation[(f,rel)][key]+=value
    return {'by_family':{f:dict(v) for f,v in family.items()},
        'by_relation':{f+'|'+rel:dict(v) for (f,rel),v in byrelation.items()},
        'candidate_definitions':{
           'expert_decoded':'Union of seven structured-decoded expert labels, per ordered pair',
           'raw_top1_positive':'Raw winner must have positive edge-vs-none margin',
           'raw_top2_positive':'Gold relation in top two raw labels with positive margin',
           'raw_any_positive':'Any frozen expert has positive raw margin for gold relation',
           'raw_top2_unthresholded':'Gold relation in top two raw labels, regardless of margin; generous oracle',
           'raw_any_unthresholded':'Any label permitted, regardless of score; mathematical ceiling'},
        'interpretation':'Oracles use gold for upper bounds only; not an inference-time policy'}


def _target_loss(model,rec,experts,device,bundle):
    gold,valid=b._gold_mask(rec,device)
    with torch.no_grad():
        score,margins,pred,_,_=r._base_scores(rec,experts,device,bundle)
        selected=(pred==gold[None]).any(0)
        unknown=valid&(gold>0)&~selected
    logits=model(rec['item']['objects'],margins,pred,score)
    truth=F.one_hot(gold.clamp_min(0).long(),b.R+1)[...,1:].to(logits.dtype)
    targets=truth*unknown[...,None].float()
    # Train proposer specifically to fill gaps not decoded by any expert,
    # against all competing negative pairs and relation labels.
    weight=valid.float()*(1.+20.*unknown.float()+2.*((pred>0).any(0)&(gold==0)).float())
    return (F.binary_cross_entropy_with_logits(logits,targets,reduction='none')*
            weight[...,None]).sum()/(weight.sum()*b.R).clamp_min(1.)


def _train(train,experts,device,bundle,steps,seed,lr):
    require(train and 1<=steps<=3000,'BAD_TRAINING_ARGS')
    torch.manual_seed(seed)
    model=r.TypedPredecoder().to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.0001)
    rand=random.Random(seed)
    for step in range(1,steps+1):
        batch=rand.sample(train,min(4,len(train)))
        opt.zero_grad(set_to_none=True)
        loss=torch.stack([_target_loss(model,x,experts,device,bundle) for x in batch]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_TARGET_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);opt.step()
        if step in (1,steps) or step%100==0:
            emit('TRAIN',{'step':step,'steps':steps,'loss':round(float(loss.detach()),6)})
    model.eval();return model


def _inference(rec,experts,spec,device,bundle,mode='frozen_0100',model=None,threshold=.85):
    """Gold-free. Candidate proposal only when 0100 left a pair empty.

    Scores use +16 on proposed edges, and original verifier remains active for
    the original edges. Proposed edges must pass the independently learned
    proposal threshold, so no use of case family or gold in inference.
    """
    require(mode in ARMS,'UNKNOWN_INFERENCE_ARM')
    if mode=='frozen_0100':return r._infer(rec,experts,spec,device,bundle,'frozen_0100')[0]
    require(model is not None and not model.training,'PROPOSER_NOT_FROZEN')
    objects=rec['item']['objects']
    with torch.no_grad():
        base, _=r._infer(rec,experts,spec,device,bundle,'frozen_0100')
        score,margins,pred,_,_=r._base_scores(rec,experts,device,bundle)
        probs=model(objects,margins,pred,score).sigmoid()
        conf,label=probs.max(-1)
        label=label+1
        # A proposed class cannot be one already emitted by a frozen expert.
        novelty=(pred!=label[None]).all(0)
        valid=~torch.eye(len(objects),dtype=torch.bool,device=device)
        propose=valid & (base.to(device)==0) & (conf>=threshold) & novelty
        new=score.clone()
        if bool(propose.any()):
            forced=torch.full_like(new,-16.)
            forced.scatter_(-1,label[...,None],16.)
            new=torch.where(propose[...,None],forced,new)
        decoded=r._decode(new,objects,spec,device)
        # Decode the FULL graph under frozen structural capacities. Rejection
        # is applied only after that global selection, as in the 0100 control.
        # Newly proposed edges are admitted on the proposer confidence gate;
        # the verifier continues to judge previously selected edges.
        if bundle['selected_variant'] in ('assembly_only','router_assembly',
                           'specialist_assembly','all_three'):
            probs=bundle['verifier'](objects,margins,pred,decoded,new).sigmoid()
            original=(~propose)&(decoded>0)
            valid=~torch.eye(len(objects),dtype=torch.bool,device=device)
            rejected=valid&original&(probs<bundle['v_threshold'])
            decoded=decoded.masked_fill(rejected,0)
        result=decoded
    return result.cpu()


def _evaluate(records,experts,spec,device,bundle,mode='frozen_0100',model=None,threshold=.85):
    totals=Counter();families=defaultdict(Counter);added=Counter()
    for rec in records:
        got=_inference(rec,experts,spec,device,bundle,mode,model,threshold)
        count=m._count(rec,got)
        fam=rec['item']['row']['family']
        totals.update(count);families[fam].update(count)
        if mode!='frozen_0100':
            base=_inference(rec,experts,spec,device,bundle)
            gold=rec['item']['targets'].cpu()
            mask=(got>0)&(base==0)
            added['proposed']+=int(mask.sum())
            added['proposed_correct']+=int((mask&(got==gold)).sum())
            added['proposed_wrong']+=int((mask&(got!=gold)).sum())
    return {'overall':m._metric(totals),
            'by_family':{k:m._metric(v) for k,v in families.items()},
            'proposal_diagnostics':dict(added)}


def _rank(stat,control):
    enc=stat['by_family']['encounter'];alloc=stat['by_family']['allocation']
    adm=(stat['overall']['edge_f1']>=control['overall']['edge_f1'] and
         alloc['edge_f1']>=control['by_family']['allocation']['edge_f1']-.01 and
         enc['edge_f1']>=control['by_family']['encounter']['edge_f1'])
    return (int(adm),enc['exact'] if adm else -1,
            stat['overall']['exact'] if adm else -1,
            enc['edge_f1'] if adm else -1.,
            stat['overall']['edge_f1'] if adm else -1.)


def _save(folder,model,experts,report_hash,configuration):
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'targeted-proposer.safetensors'
    save_file({k:v.detach().contiguous().cpu() for k,v in model.state_dict().items()},str(path))
    manifest={'schema':SCHEMA,'research_only':True,'production_promoted':False,
      'g3_unlocked':False,'dev_used':False,'confirmation_used':False,
      'source_0101_sha256':report_hash,'expert_order':list(experts),
      'expert_hashes':{k:v['sha256'] for k,v in experts.items()},
      'model':{'path':str(path),'sha256':old.digest(path)},
      'configuration':configuration}
    old.write(folder/'manifest.json',manifest)
    return manifest


def load_candidate(folder,experts,device,hash101):
    manifest=old.read(Path(folder)/'manifest.json')
    require(manifest['schema']==SCHEMA and manifest['research_only'] is True and
       manifest['production_promoted'] is False and manifest['g3_unlocked'] is False and
       manifest['dev_used'] is False and manifest['confirmation_used'] is False,
       'CANDIDATE_SECURITY_DRIFT')
    require(manifest['source_0101_sha256']==hash101,'SOURCE_0101_CHANGED')
    require(manifest['expert_order']==list(experts),'EXPERT_ORDER_CHANGED')
    require(all(manifest['expert_hashes'][k]==v['sha256'] and
                old.digest(Path(v['path']))==v['sha256'] for k,v in experts.items()),
                'FROZEN_EXPERT_CHANGED')
    require(old.digest(manifest['model']['path'])==manifest['model']['sha256'],
            'PROPOSER_CHECKPOINT_CHANGED')
    net=r.TypedPredecoder().to(device)
    net.load_state_dict(load_file(manifest['model']['path'],device='cpu'),strict=True)
    net.eval()
    return net,manifest


def audit(root=old.ROOT_DEFAULT,output=None):
    source,experts,r99,r100,r101,p101=_source(root,output)
    for i in range(3):
        x=r._bundle_from_0100(i,r100['folds'][i],r99,experts,'cpu')
        require(x['selected_variant']==r100['folds'][i]['selected_variant'],
                '0100_FROZEN_FOLD_CHANGED')
        path=Path(r101['folds'][i]['manifest']).parent
        _,entry=r.load_candidate(path,experts,'cpu',old.digest(source[8]/'encounter-bakeoff-0100'/'result.json'))
        require(entry['configuration']['mode']==r101['folds'][i]['selected']['mode'],
                '0101_FROZEN_FOLD_CHANGED')
    emit('AUDIT',{'ready':True,'expert_count':len(experts),'source_0101_sha256':old.digest(p101),
         'graph_methods':['gold_forced_frozen_caps','gold_forced_relaxed_caps'],
         'candidate_inventories':list(SUPPORTS),
         'trained_arm':'targeted_candidate_proposer',
         'source_fold_count':3,'no_dev_or_confirmation':True,'g3_unlocked':False,
         'production_promotion':False,'cross_validation':'BASE_DISJOINT_REUSED_FIT'})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',seed=SEED,
        steps=250,lr=.002,force=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'CUDA_UNAVAILABLE')
    require(1<=steps<=3000 and 0<lr<.1,'BAD_TRAIN_PARAMS')
    source,experts,r99,r100,r101,p101=_source(root,output)
    folder=source[8]/'reconstructability-0102'
    target=folder/'result.json'
    require(force or not target.exists(),'REPORT_EXISTS_USE_FORCE_TO_REPLACE')
    emit('START',{'device':device,'folds':3,'candidate_inventories':list(SUPPORTS),
         'frozen_control':'G2_0100_AND_0101_REFERENCE','oracle_gold_only_for_diagnostic':True,
         'new_training':'targeted_missing_candidate_proposer','steps':steps,
         'test_only_after_inner_validation':True,
         'reused_fit_not_independent_certification':True})
    records,spec=b.parent._records(source,experts,device)
    require(spec['method'] in ('joint','source_topk'),'UNKNOWN_DECODER_METHOD')
    fold_results=[];all_diagnostics=[]
    for fold,(fi,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,seed)):
        require(fi==fold and counts==r100['folds'][fold]['base_counts'] and
                counts==r101['folds'][fold]['base_counts'],
                'BASE_DISJOINT_PARTITION_DRIFT')
        bundle=r._bundle_from_0100(fold,r100['folds'][fold],r99,experts,device)
        # Diagnose train/validation only before choosing policy. No outer test.
        inner=[_diagnose(rec,experts,spec,device,
                     _inference(rec,experts,spec,device,bundle)) for rec in val]
        inner_summary=_summary(inner)
        candidate=_train(train,experts,device,bundle,steps,seed+fold*911,lr)
        baseline_val=_evaluate(val,experts,spec,device,bundle)
        options=[('frozen_0100',None,baseline_val)]
        for cut in THRESHOLDS:
            stat=_evaluate(val,experts,spec,device,bundle,'targeted_proposer',candidate,cut)
            options.append(('targeted_proposer',cut,stat))
        choice=max(enumerate(options),key=lambda x:(_rank(x[1][2],baseline_val),-x[0]))[1]
        variant,cut,_=choice
        # Baseline test must exactly reproduce 0100 saved fold acceptance data.
        control=_evaluate(test,experts,spec,device,bundle)
        prior=r100['folds'][fold]['outer_test']['overall']
        require(all(control['overall'][k]==prior[k] for k in ('tp','fp','fn','exact')),
                'FROZEN_0100_TEST_REPRODUCTION_FAILED')
        selected=_evaluate(test,experts,spec,device,bundle,variant,candidate,cut or .85)
        prior101=r101['folds'][fold]
        freeze101,manifest101=r.load_candidate(Path(prior101['manifest']).parent,
            experts,device,old.digest(source[8]/'encounter-bakeoff-0100'/'result.json'))
        frozen101=r._evaluate(test,experts,spec,device,bundle,
             prior101['selected']['mode'],freeze101,**prior101['selected']['params'])
        require(all(frozen101['overall'][k]==prior101['outer_test']['overall'][k]
               for k in ('tp','fp','fn','exact')),
               'FROZEN_0101_TEST_REPRODUCTION_FAILED')
        case_rows=[]
        for ix,rec in enumerate(test):
            observed=_inference(rec,experts,spec,device,bundle,variant,candidate,cut or .85)
            control_pred=_inference(rec,experts,spec,device,bundle)
            row=_diagnose(rec,experts,spec,device,control_pred)
            row['candidate_graph']={k:m._count(rec,observed)[k] for k in ('tp','fp','fn','exact')}
            row['fold']=fold;row['test_index']=ix
            case_rows.append(row)
        all_diagnostics.extend(case_rows)
        directory=folder/('fold-'+str(fold))
        _save(directory,candidate,experts,old.digest(p101),
              {'arm':variant,'threshold':cut,'base_counts':counts,
               'source_0100_manifest':bundle['manifest_path'],
               'validation_selected_only':True,'checkpoint_research_only':True})
        reloaded,_=load_candidate(directory,experts,device,old.digest(p101))
        for rec in test[:1]:
            original=_inference(rec,experts,spec,device,bundle,variant,candidate,cut or .85)
            restored=_inference(rec,experts,spec,device,bundle,variant,reloaded,cut or .85)
            require(torch.equal(original,restored),'PROPOSER_RELOAD_DRIFT')
        folds={'fold':fold,'base_counts':counts,'selected_on':'INNER_VALIDATION_ONLY',
               'selected_arm':variant,'threshold':cut,'control':control,
               'frozen_0101_reference':frozen101,'candidate':selected,
               'validation_summary':inner_summary,
               'validation_options':[{'arm':a,'threshold':t,'rank':list(_rank(s,baseline_val)),
                   'encounter_exact':s['by_family']['encounter']['exact'],
                   'encounter_f1':s['by_family']['encounter']['edge_f1'],
                   'overall_f1':s['overall']['edge_f1'],
                   'total_exact':s['overall']['exact']} for a,t,s in options],
               'manifest':str(directory/'manifest.json')}
        fold_results.append(folds)
        summary=_summary(case_rows)['by_family']
        emit('FOLD',{'fold':fold,'selected':variant,'threshold':cut,
           'encounter_exact':[control['by_family']['encounter']['exact'],
                               selected['by_family']['encounter']['exact']],
           'structural_ceiling':summary['encounter']['structural_oracle_exact'],
           'expert_decoded_candidate_ceiling':summary['encounter']['expert_decoded_oracle_exact'],
           'encounter_cases':summary['encounter']['cases'],
           '0101_f1':frozen101['by_family']['encounter']['edge_f1'],
           'selected_f1':selected['by_family']['encounter']['edge_f1']})
    def total_metric(key,family=None):
        c=Counter()
        for entry in fold_results:
            v=entry[key]['by_family'][family] if family else entry[key]['overall']
            c.update({k:v[k] for k in ('tp','fp','fn','exact','cases','g1_exact')})
        return m._metric(c)
    c=total_metric('control');n=total_metric('candidate')
    q=total_metric('frozen_0101_reference')
    byfam={f:{'control':total_metric('control',f),
              'candidate':total_metric('candidate',f)} for f in ('allocation','encounter')}
    gates={'encounter_exact_progress':byfam['encounter']['candidate']['exact']>0,
       'encounter_f1_not_worse':byfam['encounter']['candidate']['edge_f1']>=byfam['encounter']['control']['edge_f1'],
       'allocation_f1_within_1pp':byfam['allocation']['candidate']['edge_f1']>=byfam['allocation']['control']['edge_f1']-.01,
       'overall_f1_not_worse':n['edge_f1']>=c['edge_f1'],
       'overall_f1_not_worse_than_0101':n['edge_f1']>=q['edge_f1'],
       'overall_exact_not_worse':n['exact']>=c['exact']}
    upper=_summary(all_diagnostics)
    near=sorted(({'fold':q['fold'],'test_index':q['test_index'],
                  'base_id':q['base_id'],**q['observed_graph'],
                  'diagnosis':q['decoding_obstacle']} for q in all_diagnostics
                    if q['family']=='encounter'),
           key=lambda q:(q['fp']+q['fn'],q['fp'],q['fn']))[:15]
    report={'schema':SCHEMA,'accepted_on_reused_fit_cv':all(gates.values()),'gates':gates,
       'control':c,'reference_0101':q,'candidate':n,'by_family':byfam,'upper_bounds':upper,
       'case_diagnostics':all_diagnostics,'nearest_encounter_failures':near,
       'folds':fold_results,
       'source_0101_sha256':old.digest(p101),
       'data_is_reused_fit_not_independent_certification':True,
       'counterfactual_oracles_are_gold_guided_not_deployable':True,
       'research_only':True,'production_promoted':False,'g3_unlocked':False,
       'dev_used':False,'confirmation_used':False}
    folder.mkdir(parents=True,exist_ok=True)
    old.write(target,report)
    emit('CEILINGS',upper['by_family'])
    emit('CROSS_VALIDATED',{'accepted':all(gates.values()),'gates':gates,
       'control':c,'reference_0101':q,'candidate':n,'by_family':byfam,
       'diagnostic_main_causes':{f:{k:v for k,v in d.items() if k.startswith('cause_')}
           for f,d in upper['by_family'].items()}})
    emit('SAVED',{'path':str(target),'sha256':old.digest(target),
                  'production_promoted':False,'g3_unlocked':False})


def selftest():
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    kinds=b.SPAN_KINDS
    objects=[[i*3,i*3+2,kinds[0]] for i in range(5)]
    n=len(objects);gold=torch.zeros((n,n),dtype=torch.long)
    gold[0,1]=1;gold[0,2]=1;gold[0,3]=1
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    full=torch.ones((n,n,b.R+1),dtype=torch.bool)
    require(not torch.equal(_forced(gold,full,spec,objects),gold),'CAPACITY_FIXTURE_FAILED')
    require(torch.equal(_forced(gold,full,_relaxed(spec,objects,gold),objects),gold),
            'RELAXED_CAPACITY_ORACLE_FAILED')
    few=torch.zeros_like(full)
    few[0,1,1]=True;few[0,2,1]=True
    missing=_forced(gold,few,_relaxed(spec,objects,gold),objects)
    require(not torch.equal(missing,gold),'MISSING_CANDIDATE_ORACLE_FAILED')
    # All oracle evaluation happens independently of inference, whose payload
    # has NO targets, no row/family and no authored edge list.
    margins=torch.randn(b.E,n,n,b.R)*.1
    predictions=torch.zeros(b.E,n,n,dtype=torch.long)
    experts={b.m.ANCHOR:{'path':'','sha256':'0'}}
    experts.update({f'synthetic_{i}':{'path':'','sha256':str(i)} for i in range(1,b.E)})
    item={'objects':objects,'targets':gold,'full_gold':{
       format_edge(objects,i,j,int(gold[i,j])) for i,j in (gold>0).nonzero().tolist()},
       'g1_exact':True,'row':{'family':'encounter','base_id':'synth'}}
    record={'item':item,'expert_margins':margins,
       'predictions':{k:predictions[i] for i,k in enumerate(experts)}}
    outcome=_diagnose(record,experts,spec,'cpu')
    require(not outcome['gold_oracle_under_caps_exact'] and
        outcome['gold_oracle_relaxed_caps_exact'] and
        outcome['decoding_obstacle']=='structural_decoder_constraint',
        'STRUCTURAL_CAUSE_CLASSIFICATION_FAILED')
    bundle={'router':b.m.CompetenceRouter(b.E).eval(),
            'specialist':None,'verifier':b.EdgeVerifier().eval(),
            'policy':'pin_ungated','v_threshold':.5,
            's_threshold':.7,'selected_variant':'frozen_0099'}
    model=r.TypedPredecoder()
    loss=_target_loss(model,record,experts,'cpu',bundle)
    loss.backward()
    require(bool(torch.isfinite(loss)) and
            all(p.grad is not None for p in model.parameters()),'TARGETED_TRAIN_GRADIENT_MISSING')
    no_gold={'item':{'objects':objects},'expert_margins':margins,
       'predictions':record['predictions']}
    model.eval()
    base=_inference(record,experts,spec,'cpu',bundle)
    bare=_inference(no_gold,experts,spec,'cpu',bundle)
    require(torch.equal(base,bare),'GOLD_LEAK_IN_BASELINE_INFERENCE')
    result=_inference(no_gold,experts,spec,'cpu',bundle,'targeted_proposer',model,.99)
    actual=_inference(record,experts,spec,'cpu',bundle,'targeted_proposer',model,.99)
    require(torch.equal(result,actual),'GOLD_LEAK_IN_PROPOSER_INFERENCE')
    trained=_train([record],experts,'cpu',bundle,2,SEED,.002)
    stat=_evaluate([record],experts,spec,'cpu',bundle,'targeted_proposer',trained,.85)
    require(stat['overall']['cases']==1,'TARGETED_EVAL_FAILED')
    allocation={**record,'item':{**item,'row':{'family':'allocation','base_id':'synth2'}}}
    ctrl=_evaluate([record,allocation],experts,spec,'cpu',bundle)
    require(len(_rank(_evaluate([record,allocation],experts,spec,'cpu',bundle,
                 'targeted_proposer',trained,.85),ctrl))==5,
            'VALIDATION_SELECTION_FAILED')
    with tempfile.TemporaryDirectory(prefix='g2_0102_') as temp:
        path=Path(temp)
        for i,(key,value) in enumerate(experts.items()):
            p=path/('ex'+str(i)+'.bin');p.write_bytes(str(i).encode());
            value['path']=str(p);value['sha256']=old.digest(p)
        _save(path/'candidate',trained,experts,'test-hash',{'arm':'targeted_proposer'})
        loaded,_=load_candidate(path/'candidate',experts,'cpu','test-hash')
        require(torch.equal(_inference(no_gold,experts,spec,'cpu',bundle,'targeted_proposer',trained,.85),
                 _inference(no_gold,experts,spec,'cpu',bundle,'targeted_proposer',loaded,.85)),
                 'RELOAD_INFERENCE_DRIFT')
        Path(next(iter(experts.values()))['path']).write_bytes(b'mutated')
        try:load_candidate(path/'candidate',experts,'cpu','test-hash')
        except ValueError as ex:require('FROZEN_EXPERT_CHANGED' in str(ex),'TAMPER_WRONG_ERROR')
        else:raise ValueError(TAG+'_TAMPER_ACCEPTED')
    emit('SELFTEST',{'gold_oracle_detects_cap_violation':True,
       'gold_oracle_relaxed_cap_reconstructs':True,'missing_candidate_detected':True,
       'targeted_proposer_has_gradients':True,'gold_free_inference':True,
       'checkpoint_roundtrip':True,'tamper_rejected':True,
       'production_unchanged':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=('audit','selftest','run'),default='run')
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--steps',type=int,default=250)
    parser.add_argument('--lr',type=float,default=.002)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='audit':return audit(args.root,args.output)
    if args.command=='selftest':return selftest()
    return run(args.root,args.output,args.device,args.seed,args.steps,args.lr,args.force)

if __name__=='__main__':main()
