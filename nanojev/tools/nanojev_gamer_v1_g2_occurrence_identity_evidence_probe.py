"""G2-0028: diagnostic-only occurrence evidence probes; no G2 promotion."""
from __future__ import annotations
from collections import Counter,defaultdict
from copy import deepcopy
from pathlib import Path
import gzip,hashlib,json,math,random
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_competitor_context_intervention as context18
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as ceiling21
from tools import nanojev_gamer_v1_g2_competition_aware_learning as parent22
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24
from tools import nanojev_gamer_v1_g2_context_utilization_intervention as parent25
from tools import nanojev_gamer_v1_g2_competitor_identity_discrimination as parent26
from tools import nanojev_gamer_v1_g2_relationship_evidence_sufficiency_audit as parent27
SCHEMA='nanojev.gamer.graph.g2.occurrence-identity-evidence-probe.v1'
REPORT='g2_occurrence_identity_evidence_probe_report.json'
CASES='g2_occurrence_identity_evidence_probe_per_case.jsonl.gz'
WEIGHTS='g2_occurrence_identity_evidence_probe_diagnostic.safetensors'
SEED=460036;STEPS=600;LR=7e-5;BATCH_SEED=460037;TOP_K=24
GROUPS_PER_CASE=4;BATCH_GROUPS=16;POS_PER_GROUP=4;NEG_PER_GROUP=6
POS_DIM=10;STRUCT_DIM=10;AUX_DIM=20;HIDDEN=96
ARMS=('frozen_features','occurrence_position','local_structure',
      'position_and_structure','shuffled_evidence_control')
REFERENCE='identity_peer_0026'
REPLAY=parent27.NEW
NEW=('new_independent_dev_0028','new_topology_dev_0028')
SPLITS=(*REPLAY,*NEW)


def _hash(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()


def verify_parent27(directory,rows,hashes,train_hash):
    """Reconstruct saved G2-0027 aggregates and refuse provenance drift."""
    directory=Path(directory)
    rp=directory/parent27.REPORT;cp=directory/parent27.CASES;fp=directory/parent27.FRONTIER
    report=json.loads(rp.read_text(encoding='utf-8'))
    need={'schema':parent27.SCHEMA,
        'status':'G2_EVIDENCE_SUFFICIENCY_FROZEN_DEV_AUDIT_NOT_CERTIFIED',
        'parent_hashes':hashes,'train_dataset_sha256':train_hash,
        'datasets_sha256':{k:density.sha_rows(rows[k]) for k in parent27.SPLITS},
        'new_prompt_sha256':{k:[base._sha(v['prompt']) for v in rows[k]] for k in parent27.NEW},
        'predeclared':{'new_dev_seeds':[parent27.SEED,parent27.SEED+1],
            'replay_cohorts':list(parent27.REPLAY),'new_cohorts':list(parent27.NEW),
            'frozen_arms':list(parent27.ARMS),'candidate_top_k':TOP_K,
            'fixed_threshold':parent27.THRESHOLD,
            'cosine_thresholds':list(parent27.COSINE_THRESHOLDS),
            'frontier_per_case':parent27.FRONTIER_PER_CASE,
            'scope':'posthoc_exact_G1_coverage_pair_and_dst_cosine_proxies',
            'author_labels_enter_only_offline_diagnostics':True,
            'no_text_reintroduced_into_scorer':True},
        'new_training':False,'no_optimizer_created':True,'new_trainable_parameters':0,
        'candidate_selection_uses_gold':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
        'frozen_backbone':True,'frozen_g1':True,'frozen_rank_head':True,
        'frozen_parent_weights':True}
    for k,v in need.items():
        if type(report.get(k)) is not type(v) or report[k]!=v:
            raise ValueError('GAMER_G2_0028_PARENT_0027_CONTRACT:'+k)
    if report.get('cases_sha256')!=backbone.sha256(cp) or report.get('frontier_sha256')!=backbone.sha256(fp):
        raise ValueError('GAMER_G2_0028_PARENT_0027_HASH_DRIFT')
    found={s:[] for s in parent27.SPLITS}
    with gzip.open(cp,'rt',encoding='utf-8') as f:
        for line in f:
            item=json.loads(line);key=item.get('group')
            if key not in found or len(found[key])>=len(rows[key]):
                raise ValueError('GAMER_G2_0028_PARENT_0027_EXTRA_ROW')
            i=len(found[key]);row=rows[key][i]
            if (item.get('index')!=i+1 or item.get('prompt_sha256')!=base._sha(row['prompt'])
                or item.get('family')!=row['family'] or
                item.get('structural_topology')!=row.get('structural_topology','native') or
                item.get('coverage',{}).get('gold_total')!=len(base.gold_binding_edges(row)) or
                set(item.get('metrics',{}))!=set(parent27.ARMS) or
                set(item.get('competition',{}))!=set(parent27.ARMS)):
                raise ValueError('GAMER_G2_0028_PARENT_0027_ROW_IDENTITY')
            found[key].append(item)
    if any(len(found[s])!=len(rows[s]) for s in parent27.SPLITS):
        raise ValueError('GAMER_G2_0028_PARENT_0027_ROW_COUNT')
    cohorts={'replayed_0026_dev':[x for s in parent27.REPLAY for x in found[s]],
             'new_0027_dev':[x for s in parent27.NEW for x in found[s]]}
    check={'by_split':{s:parent27.aggregate(v) for s,v in found.items()},
           'evaluation':{s:parent27.aggregate(v) for s,v in cohorts.items()},
           'by_family':{s:{f:parent27.aggregate([x for x in v if x['family']==f])
                           for f in sorted({x['family'] for x in v})} for s,v in cohorts.items()},
           'by_density':{s:{f:parent27.aggregate([x for x in v if x['density_bucket']==f])
                            for f in sorted({x['density_bucket'] for x in v})} for s,v in cohorts.items()},
           'by_topology':{s:{f:parent27.aggregate([x for x in v if x['structural_topology']==f])
                             for f in sorted({x['structural_topology'] for x in v})} for s,v in cohorts.items()}}
    for k,v in check.items():
        if report.get(k)!=v:raise ValueError('GAMER_G2_0028_PARENT_0027_AGGREGATE:'+k)
    return report,found,{'g2_0027_report_sha256':backbone.sha256(rp),
                         'g2_0027_cases_sha256':backbone.sha256(cp),
                         'g2_0027_frontier_sha256':backbone.sha256(fp)}


def fresh_cohorts(history,training):
    indie=general16.independent_rows((history,),limit=320,seed=SEED)
    topology=general16.heldout_topology_rows((history,indie),limit=128,seed=SEED+1)
    combined=[*history,*indie,*topology]
    if len(indie)!=320 or len(topology)!=128 or len({_hash(r['prompt']) for r in combined})!=len(combined):
        raise ValueError('GAMER_G2_0028_COHORT_OVERLAP')
    if {r.get('structural_topology') for r in topology}!={'2x4','4x2','4x4'} or any(r.get('split')!='dev' for r in [*indie,*topology]):
        raise ValueError('GAMER_G2_0028_COHORT_PROTOCOL')
    return dict(zip(NEW,(indie,topology)))


def evidence_features(case,prompt,inv,width):
    """Build *label-blind* occurrence and neighbor features using predicted nodes."""
    edges=inv['edges'];x=inv['features'].detach().float().clone()
    if x.shape!=(len(edges),4*width+source13.EXTRAS) or not torch.isfinite(x).all():
        raise ValueError('GAMER_G2_0028_FROZEN_WIDTH')
    nodes=case['anchors'];L=max(1,len(prompt))
    if len(set(nodes))!=len(nodes) or any(not (0<=a[0]<a[1]<=len(prompt)) for a in nodes):
        raise ValueError('GAMER_G2_0028_BAD_PREDICTED_SPANS')
    ordered=sorted(nodes,key=lambda a:(a[0],a[1],a[2]));by_text=defaultdict(list)
    for anchor in ordered:by_text[(anchor[2],prompt[anchor[0]:anchor[1]].strip().casefold())].append(anchor)
    ordinal={node:(i,len(group)) for group in by_text.values() for i,node in enumerate(group)}
    loc={node:i for i,node in enumerate(ordered)};n=max(1,len(ordered));span=max(1,n-1)
    pp=[];ss=[]
    for src,dst,relation in edges:
        if src not in loc or dst not in loc or src==dst:raise ValueError('GAMER_G2_0028_UNKNOWN_NODE')
        si,di=loc[src],loc[dst];so,sn=ordinal[src];do,dn=ordinal[dst]
        pp.append([src[0]/L,src[1]/L,dst[0]/L,dst[1]/L,
            (dst[0]-src[0])/L,so/max(1,sn-1),do/max(1,dn-1),
            math.log1p(sn)/8.,math.log1p(dn)/8.,(do-so)/max(1,sn+dn-2)])
        def gap(node,i,side):
            if side<0:return (node[0]-ordered[i-1][1])/L if i else 1.
            return (ordered[i+1][0]-node[1])/L if i+1<n else 1.
        s_near=sum(a[2]==src[2] and abs(a[0]-src[0])<=64 for a in ordered)
        d_near=sum(a[2]==dst[2] and abs(a[0]-dst[0])<=64 for a in ordered)
        ss.append([si/span,di/span,(di-si)/span,
                   gap(src,si,-1),gap(src,si,1),gap(dst,di,-1),gap(dst,di,1),
                   s_near/n,d_near/n,float(abs(src[0]-dst[0])<=64)])
    pos=torch.tensor(pp,dtype=torch.float32).reshape(len(edges),10).clamp(-1,1)
    struct=torch.tensor(ss,dtype=torch.float32).reshape(len(edges),10).clamp(-1,1)
    core=torch.cat((x,(inv['base'].detach().float()/8).clamp(-5,5).unsqueeze(-1)),dim=-1)
    return core,pos,struct


def feature_arms(core,pos,struct,groups,prompt_hash):
    missing_pos=torch.zeros_like(pos);missing_struct=torch.zeros_like(struct)
    shuffled=torch.cat((pos,struct),dim=1).clone();bygroup=defaultdict(list)
    for i,g in enumerate(groups):bygroup[g].append(i)
    for g,indices in bygroup.items():
        if len(indices)<2:continue
        ix=list(range(len(indices)))
        random.Random(int(_hash(prompt_hash+':'+str(g))[:12],16)).shuffle(ix)
        shuffled[indices]=shuffled[[indices[j] for j in ix]]
    data={'frozen_features':torch.cat((core,missing_pos,missing_struct),1),
          'occurrence_position':torch.cat((core,pos,missing_struct),1),
          'local_structure':torch.cat((core,missing_pos,struct),1),
          'position_and_structure':torch.cat((core,pos,struct),1),
          'shuffled_evidence_control':torch.cat((core,shuffled),1)}
    if set(data)!=set(ARMS) or len({tuple(v.shape) for v in data.values()})!=1:
        raise ValueError('GAMER_G2_0028_PROBE_SHAPES')
    return data


class Probe(nn.Module):
    def __init__(self,dim):
        super().__init__();self.layers=nn.Sequential(nn.Linear(dim,HIDDEN),nn.GELU(),nn.Linear(HIDDEN,1))
    def forward(self,x):return self.layers(x).squeeze(-1)


def training_groups(case,row,head,allowed,device):
    with torch.inference_mode():
        logits=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,logits,allowed,top_k=TOP_K,train_gold=False)
    core,p,s=evidence_features(case,row['prompt'],inv,case['vectors'].shape[-1])
    variants=feature_arms(core,p,s,inv['groups'],case['prompt_sha256'])
    grouped=defaultdict(list)
    for i,g in enumerate(inv['groups']):grouped[g].append(i)
    result=[]
    for group,indices in grouped.items():
        pos=[i for i in indices if inv['edges'][i] in case['gold']]
        neg=[i for i in indices if inv['edges'][i] not in case['gold']]
        if not pos or not neg:continue
        pos=sorted(pos,key=lambda i:(-float(inv['base'][i]),i))[:POS_PER_GROUP]
        neg=sorted(neg,key=lambda i:(-float(inv['base'][i]),i))[:NEG_PER_GROUP]
        selected=pos+neg;labels=torch.tensor([1.]*len(pos)+[0.]*len(neg))
        result.append((group,{a:variants[a][selected] for a in ARMS},labels))
    random.Random(SEED+case['index']).shuffle(result)
    return result[:GROUPS_PER_CASE]


def _loss(scores,labels):
    p=scores[labels==1];n=scores[labels==0]
    if not len(p) or not len(n):raise ValueError('GAMER_G2_0028_UNMIXED_BATCH')
    bce=(F.binary_cross_entropy_with_logits(p,torch.ones_like(p))+
         F.binary_cross_entropy_with_logits(n,torch.zeros_like(n)))/2
    return bce+0.5*F.softplus(0.5-(p[:,None]-n[None,:])).mean()


def train_probes(groups,dim,device='cpu',steps=STEPS):
    if not groups or steps<1:raise ValueError('GAMER_G2_0028_NO_TRAIN_GROUPS')
    torch.manual_seed(SEED);prototype=Probe(dim)
    models={a:deepcopy(prototype).to(device) for a in ARMS}
    opts={a:torch.optim.AdamW(models[a].parameters(),lr=LR,weight_decay=1e-4) for a in ARMS}
    rng=random.Random(BATCH_SEED);final={}
    for step in range(1,steps+1):
        batch=rng.sample(groups,min(BATCH_GROUPS,len(groups)))
        for arm,model in models.items():
            opts[arm].zero_grad(set_to_none=True)
            sizes=[len(label) for _,_,label in batch]
            outputs=model(torch.cat([x[arm] for _,x,_ in batch]).to(device))
            losses=[];offset=0
            for (_,_,label),n in zip(batch,sizes):
                losses.append(_loss(outputs[offset:offset+n],label.to(device)));offset+=n
            loss=torch.stack(losses).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0028_NONFINITE_LOSS')
            loss.backward();opts[arm].step();final[arm]=float(loss.detach())
        if step==1 or step%60==0 or step==steps:
            print(f'GAMER_G2_0028_TRAIN {step}/{steps} '+
                  ' '.join(f'{a}={final[a]:.5f}' for a in ARMS),flush=True)
    for net in models.values():net.eval().requires_grad_(False)
    return models,final


def analyze_case(case,row,head,models,allowed,reference,device,split,index,prior=None):
    if case['prompt_sha256']!=base._sha(row['prompt']):
        raise ValueError('GAMER_G2_0028_CASE_IDENTITY')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        core,p,s=evidence_features(case,row['prompt'],inv,case['vectors'].shape[-1])
        feats=feature_arms(core,p,s,inv['groups'],case['prompt_sha256'])
        scores={a:models[a](feats[a].to(device)).detach().cpu() for a in ARMS}
        scores[REFERENCE]=reference(inv['features'].to(device),inv['base'].to(device),
                                    inv['groups'],with_competition=True,contextual=True).detach().cpu()
    gold=case['gold'];reachable=sum(e in gold for e in inv['edges'])
    if prior is not None and (prior.get('index')!=index or prior.get('group')!=split or
        prior.get('prompt_sha256')!=case['prompt_sha256'] or
        prior['coverage']['candidates']!=len(inv['edges']) or
        prior['coverage']['candidate_gold']!=reachable):
        raise ValueError('GAMER_G2_0028_PARENT_0027_INVENTORY_DRIFT')
    group=defaultdict(list)
    for i,g in enumerate(inv['groups']):group[g].append(i)
    counters={a:Counter() for a in (*ARMS,REFERENCE)}
    subsets={a:defaultdict(Counter) for a in (*ARMS,REFERENCE)}
    normalized=F.normalize(core[:,:4*case['vectors'].shape[-1]],dim=-1)
    for indices in group.values():
        yes=[i for i in indices if inv['edges'][i] in gold]
        no=[i for i in indices if inv['edges'][i] not in gold]
        if not yes or not no:continue
        for i in yes:
            dest=inv['edges'][i][1]
            surface=row['prompt'][dest[0]:dest[1]].casefold().strip()
            same=any(row['prompt'][inv['edges'][j][1][0]:inv['edges'][j][1][1]].casefold().strip()==surface for j in no)
            close=bool((normalized[no]@normalized[i]).max()>=.99)
            for arm in (*ARMS,REFERENCE):
                a=scores[arm]
                failure=int(float(a[i])<=max(float(a[j]) for j in no))
                c=counters[arm];c['competing_gold']+=1;c['ranking_failures']+=failure
                c['pairwise_comparisons']+=len(no)
                c['pairwise_wins']+=sum(float(a[i])>float(a[j]) for j in no)
                for bucket in (('same_surface' if same else 'different_surface'),
                               ('high_pair_cosine' if close else 'lower_pair_cosine')):
                    subsets[arm][bucket]['count']+=1;subsets[arm][bucket]['failures']+=failure
    for arm in (*ARMS,REFERENCE):
        chosen=source13.decode_scores(inv,scores[arm],.5 if arm in ARMS else parent26.THRESHOLD)
        counters[arm]['correct']=len(chosen&gold)
        counters[arm]['missing']=len(gold-chosen)
        counters[arm]['false_positive']=len(chosen-gold)
    return {'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
            'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
            'structural_topology':row.get('structural_topology','native'),
            'gold_total':len(gold),'candidate_gold':reachable,'candidate_count':len(inv['edges']),
            'probes':{k:dict(v) for k,v in counters.items()},
            'subgroups':{k:{group:dict(v) for group,v in sub.items()} for k,sub in subsets.items()}}


def aggregate(records):
    if not records:raise ValueError('GAMER_G2_0028_EMPTY_AGGREGATE')
    names=(*ARMS,REFERENCE)
    counts={a:Counter() for a in names};subgroups={a:defaultdict(Counter) for a in names}
    for row in records:
        for a in names:
            counts[a].update(row['probes'][a])
            for k,v in row['subgroups'][a].items():subgroups[a][k].update(v)
    metrics={}
    for a,c in counts.items():
        tp=c['correct'];fp=c['false_positive'];fn=c['missing'];rg=c['competing_gold']
        metrics[a]={'correct':tp,'false_positive':fp,'missing':fn,
            'precision':tp/max(1,tp+fp),'recall':tp/max(1,tp+fn),
            'f1':2*tp/max(1,2*tp+fp+fn),'competing_gold':rg,
            'ranking_failures':c['ranking_failures'],
            'ranking_failure_rate':c['ranking_failures']/max(1,rg),
            'pairwise_win_rate':c['pairwise_wins']/max(1,c['pairwise_comparisons']),
            'subgroups':{k:{'count':v['count'],'failures':v['failures'],
                'failure_rate':v['failures']/max(1,v['count'])} for k,v in subgroups[a].items()}}
    return {'cases':len(records),'gold_total':sum(r['gold_total'] for r in records),
            'candidate_gold':sum(r['candidate_gold'] for r in records),'probes':metrics}


def paired_bootstrap(rows,a,b,draws=500,seed=SEED):
    if not rows or a==b:raise ValueError('GAMER_G2_0028_INVALID_BOOTSTRAP')
    rng=random.Random(seed);df=[];dr=[]
    for _ in range(draws):
        c=Counter();d=Counter()
        for _ in rows:
            row=rows[rng.randrange(len(rows))]
            c.update(row['probes'][a]);d.update(row['probes'][b])
        f=lambda x:2*x['correct']/max(1,2*x['correct']+x['missing']+x['false_positive'])
        r=lambda x:x['ranking_failures']/max(1,x['competing_gold'])
        df.append(f(c)-f(d));dr.append(r(c)-r(d))
    def ci(x):
        x.sort();return [x[int(.025*(draws-1))],x[int(.975*(draws-1))]]
    return {'challenger':a,'control':b,'draws':draws,'f1_delta_CI95':ci(df),
            'ranking_failure_rate_delta_CI95':ci(dr),'method':'paired_case_bootstrap'}


def save_cases(path,rows):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as gz:
            for r in rows:gz.write((json.dumps(r,sort_keys=True,ensure_ascii=False,
                                        allow_nan=False,separators=(',',':'))+'\n').encode('utf-8'))


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,competition22_run,
        context23_run,audit24_run,intervention25_run,identity26_run,evidence27_run,
        output,device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0028_IMMUTABLE_OUTPUT')
    if max_length!=2048 or steps!=STEPS:raise ValueError('GAMER_G2_0028_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0028_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0028_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0028_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,
        specialization_run=specialization_run)
    training=original+augmented
    report16,rec16,h16=ceiling17.verify_frozen_0016(general_run,source_run,
        routing_robustness_run,augmented,training,calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256']!=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256']!=parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0028_PARENT_0016_LINEAGE')
    exposed=dict(zip(ceiling20.EXPOSED,(independent,withheld)))
    rec17,h17=context18.verify_parent_0017(ceiling_run,exposed,h16)
    h18=train19.verify_parent_0018(context18_run,exposed,h16,h17,rec17)
    _,h19=ceiling20.verify_parent_0019(train19_run,{'calibration':calibration,**exposed},rec16,
        {**h16,**h17,**h18},expected_train_sha=density.sha_rows(training),
        expected_datasets={s:density.sha_rows(rows) for s,rows in
                           (('calibration',calibration),*exposed.items())})
    prior=[r for batch in (*history,augmented,calibration,independent,withheld) for r in batch]
    fresh20=ceiling20.fresh_cohorts(prior)
    _,h20=ceiling21.verify_parent_0020(ceiling20_run,{**exposed,**fresh20},
                                       {**h16,**h17,**h18,**h19})
    fresh21=ceiling21._cohort_rows(prior,fresh20)
    _,_,h21=parent22._read_parent21(ceiling21_run,{**fresh20,**fresh21},
                                   {**h16,**h17,**h18,**h19,**h20})
    fresh22=parent22.fresh_cohorts(prior,fresh20,fresh21)
    _,_,h22=parent23._verify_parent22(competition22_run,{**fresh21,**fresh22},
        {**h16,**h17,**h18,**h19,**h20,**h21},density.sha_rows(training))
    fresh23=parent23.fresh_cohorts(prior,fresh20,fresh21,fresh22)
    previous={**fresh22,**fresh23}
    if len(training)!=640:raise ValueError('GAMER_G2_0028_TRAIN_COUNT')
    all_previous=[training,calibration,independent,withheld,*fresh20.values(),
                  *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for batch in all_previous for r in batch})!=sum(map(len,all_previous)):
        raise ValueError('GAMER_G2_0028_PARENT_DEV_OVERLAP')
    parent23_report,old23,h23=audit24.verify_parent23(context23_run,previous,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22},density.sha_rows(training))
    parent24_report,old24,h24=parent26.verify_parent24(audit24_run,fresh23,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23})
    history25=[r for batch in all_previous for r in batch]
    fresh25=parent25.fresh_cohorts(history25,training)
    old25_parent_rows={**fresh23,**fresh25}
    parent25_report,old25,h25=parent26.verify_parent25(intervention25_run,old25_parent_rows,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24},
        density.sha_rows(training))
    history26=[*history25,*(r for batch in fresh25.values() for r in batch)]
    fresh26=parent26.fresh_cohorts(history26,training)
    all_new=[*history26,*fresh26[parent26.NEW[0]],*fresh26[parent26.NEW[1]]]
    if len({base._sha(r['prompt']) for r in all_new})!=len(all_new):
        raise ValueError('GAMER_G2_0028_NEW_DEV_OVERLAP')
    parent26_report,old26,h26=parent27.verify_parent26(identity26_run,{**fresh25,**fresh26},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25},
        density.sha_rows(training))
    history27=[*history26,*(r for batch in fresh26.values() for r in batch)]
    fresh27=parent27.fresh_cohorts(history27,training)
    if len({base._sha(r['prompt']) for r in (*history27,*fresh27[parent27.NEW[0]],*fresh27[parent27.NEW[1]])})!=len(history27)+448:
        raise ValueError('GAMER_G2_0028_FRESH_PROMPT_OVERLAP')

    report27,old27,h27=verify_parent27(evidence27_run,{**fresh26,**fresh27},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26},
        density.sha_rows(training))
    history28=[*history27,*(r for batch in fresh27.values() for r in batch)]
    fresh28=fresh_cohorts(history28,training)
    evaluate={**fresh27,**fresh28}


    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
         parent15['parent_hashes']['g2_0004_weights_sha256'])
    before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0028_BACKBONE_UNFROZEN')
    allowed=sel.compatibility_from_train(original)
    train_capture=rel.capture(head,backbone_model,tokenizer,training,
        device=device,max_length=max_length,group='train_0028')
    train_groups=[]
    for row,case in zip(training,train_capture):
        train_groups.extend(training_groups(case,row,head,allowed,device))
    if len(train_capture)!=640 or len(train_groups)<128:
        raise ValueError('GAMER_G2_0028_INSUFFICIENT_TRAIN_GROUPS')
    width=train_capture[0]['vectors'].shape[-1]
    if any(c['vectors'].shape[-1]!=width for c in train_capture):
        raise ValueError('GAMER_G2_0028_WIDTH_DRIFT')
    dim=4*width+source13.EXTRAS+1+AUX_DIM
    del train_capture
    models,loss=train_probes(train_groups,dim,device,steps)
    trained={a:{k:v.detach().cpu().clone() for k,v in n.state_dict().items()}
             for a,n in models.items()}
    old19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
    guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                 old19['new_weights_sha256'])
    frozen23=audit24.load_parent23_models(context23_run,guard,
               parent23_report['new_weights_sha256'])['relational_context_0023']
    reference=parent27.load_parent26_models(identity26_run,frozen23,
               parent26_report['new_weights_sha256'])[REFERENCE]
    reference_before={k:v.detach().cpu().clone() for k,v in reference.state_dict().items()}
    reference=reference.to(device).eval().requires_grad_(False)
    saved={}
    for split,rows in evaluate.items():
        examples=rel.capture(head,backbone_model,tokenizer,rows,
            device=device,max_length=max_length,group=split)
        outcomes=[]
        for i,(case,row) in enumerate(zip(examples,rows),1):
            prior=old27[split][i-1] if split in REPLAY else None
            outcomes.append(analyze_case(case,row,head,models,allowed,reference,
                device,split,i,prior=prior))
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_0028_EVALUATE {split} {i}/{len(rows)}',flush=True)
        if len(outcomes)!=len(rows):raise ValueError('GAMER_G2_0028_EVALUATION_COUNT')
        saved[split]=outcomes
    rel.validate_original_state(head,before)
    if any(not torch.equal(v,reference.state_dict()[k].detach().cpu())
           for k,v in reference_before.items()):
        raise ValueError('GAMER_G2_0028_REFERENCE_MUTATED')
    for arm,model in models.items():
        if any(not torch.equal(v,model.state_dict()[k].detach().cpu())
               for k,v in trained[arm].items()):
            raise ValueError('GAMER_G2_0028_DIAGNOSTIC_MUTATED')
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0028_BACKBONE_TRAINED')
    cohorts={'replayed_0027_dev':[v for s in REPLAY for v in saved[s]],
             'new_0028_dev':[v for s in NEW for v in saved[s]]}
    report={'schema':SCHEMA,'status':'G2_OCCURRENCE_PROBES_DIAGNOSTIC_TRAIN_ONLY_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26,**h27},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(v) for k,v in evaluate.items()},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in fresh28[k]] for k in NEW},
        'predeclared':{'train_seed':SEED,'new_dev_seeds':[SEED,SEED+1],
            'steps':STEPS,'lr':LR,'batch_seed':BATCH_SEED,'candidate_top_k':TOP_K,
            'groups_per_case':GROUPS_PER_CASE,'batch_groups':BATCH_GROUPS,
            'positive_per_group':POS_PER_GROUP,'negative_per_group':NEG_PER_GROUP,
            'input_dim':dim,'hidden_dim':HIDDEN,'arms':list(ARMS),
            'reference':REFERENCE,'probe_cutoff':.5,
            'reference_cutoff':parent26.THRESHOLD,
            'replay':list(REPLAY),'fresh':list(NEW),'bootstrap_draws':500},
        'train':{'examples':len(training),'mixed_groups':len(train_groups),
            'parameter_count_per_arm':sum(p.numel() for p in next(iter(models.values())).parameters()),
            'loss':loss,'same_initialization':True,'same_batches':True,
            'same_loss':True,'same_train_groups':True,
            'train_labels_enter_only_after_label_blind_candidate_selection':True},
        'evaluation':{k:aggregate(v) for k,v in cohorts.items()},
        'paired_intervals':{k:{name:paired_bootstrap(v,'position_and_structure',control,seed=SEED+i)
                       for i,(name,control) in enumerate((('vs_frozen','frozen_features'),
                            ('vs_scrambled','shuffled_evidence_control')))}
                       for k,v in cohorts.items()},
        'by_split':{k:aggregate(v) for k,v in saved.items()},
        'by_family':{k:{f:aggregate([r for r in v if r['family']==f])
                        for f in sorted({r['family'] for r in v})} for k,v in cohorts.items()},
        'by_density':{k:{f:aggregate([r for r in v if r['density_bucket']==f])
                         for f in sorted({r['density_bucket'] for r in v})} for k,v in cohorts.items()},
        'by_topology':{k:{f:aggregate([r for r in v if r['structural_topology']==f])
                          for f in sorted({r['structural_topology'] for r in v})} for k,v in cohorts.items()},
        'interpretation_limits':{
            'probe_success_demonstrates_recoverability_not_production_readiness':True,
            'probe_failure_does_not_prove_information_absent':True,
            'local_structure_is_predicted_node_neighborhood_not_gold_topology':True,
            'fixed_0p5_probe_cutoff_is_not_fairly_calibrated_production_comparison':True},
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_parent_weights':True,'candidate_selection_uses_gold':False,
        'same_probe_parameter_count':True,'training_on_train_only':True,
        'dev_labels_used_for_training':False,'model_selection':False,
        'threshold_selection':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'certification_authorized':False,
        'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    save_cases(output/CASES,[r for k in SPLITS for r in saved[k]])
    save_file({a+'.'+k:v.contiguous() for a,state in trained.items() for k,v in state.items()},
        str(output/WEIGHTS))
    report['cases_sha256']=backbone.sha256(output/CASES)
    report['probe_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    return report
