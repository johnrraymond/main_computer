"""G2-0009: recall-weighted selective RETENTION of frozen G2-0005 edges.

Only a new retention scorer is trained, on TRAIN labels and G1-predicted
objects. Frozen G2-0008 models are inputs, not trainable parameters. The
frozen G2-0005 edge set is an immutable upper bound: the candidate can remove
an edge, but can never introduce one. Calibration uses the *previously
inspected* G2-0008 DEV cohort; the next prompt-disjoint DEV cohort is sealed
until thresholds are chosen. No production promotion or graph execution.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_g2_learned_graph_context as learned8
from tools import nanojev_gamer_v1_backbone_first as backbone

SCHEMA = 'nanojev.gamer.graph.g2.recall-preserving-graph.v1'
REPORT = 'g2_recall_preserving_graph_report.json'
CASES = 'g2_recall_preserving_graph_per_case.jsonl'
WEIGHTS = 'g2_recall_preserving_graph_development.safetensors'
SEED = 390009
VALIDATION_LIMIT = 320
STEPS = 240
BATCH = 512
LR = 3e-4
# Predeclared thresholds operate on the probability that an EXISTING edge is
# correct. An edge not selected by G2-0005 is never available to add.
THRESHOLDS = (.10, .20, .30, .40, .50, .60, .70)
PARENT = learned8.PARENT
TRAIN_WEIGHTS = (4., 8.)


def new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,
                        *,limit=VALIDATION_LIMIT,seed=SEED):
    cohorts=(train,first,second,third,fourth,fifth,sixth,seventh,eighth)
    if type(limit) is not int or limit<2 or limit%2 or not all(cohorts):
        raise ValueError('GAMER_G2_0009_BAD_COHORT')
    earlier=[row for cohort in cohorts for row in cohort]
    fresh=base.novel_dev_rows(earlier,limit=limit,seed=seed)
    old_hashes={base._sha(row['prompt']) for row in earlier}
    fresh_hashes=[base._sha(row['prompt']) for row in fresh]
    if (len(set(fresh_hashes))!=len(fresh_hashes) or set(fresh_hashes)&old_hashes
        or Counter(row['family'] for row in fresh)!={'encounter':limit//2,'allocation':limit//2}):
        raise ValueError('GAMER_G2_0009_PROMPT_LEAKAGE_OR_IMBALANCE')
    return fresh


def _metrics_from_records(rows,names):
    return {name:base.metrics([{**row['configurations'][name],'family':row['family']}
                               for row in rows]) for name in names}


def verify_learned8_parent(directory,eighth,expected_parent_hashes):
    """Recompute every G2-0008 case metric, selection, weight digest, and lineage."""
    directory=Path(directory)
    report_path=directory/learned8.REPORT
    cases_path=directory/learned8.CASES
    weights_path=directory/learned8.WEIGHTS
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={
        'schema':learned8.SCHEMA,
        'g1_frozen':True,
        'g2_0004_rank_weights_frozen':True,
        'g2_0005_rule_frozen':True,
        'g2_0007_replay_pass':True,
        'g2_0007_screen_pass':False,
        'g2_certified':False,
        'g2_production_unlocked':False,
        'production_promotion':False,
        'certification_authorized':False,
        'graph_execution_authorized':False,
        'sealed_test_used':False,
        'challenge_or_test_split_used':False,
        'inference_uses_predicted_g1_only':True,
        'no_gold_graph_at_inference':True,
        'gold_used_for_train_supervision_and_offline_scoring_only':True,
        'selected_rule':None,
        'selected_independent_screen_pass':False,
        'selected_beats_trained_no_neighborhood_f1':False,
        'g1_development_candidate':base.G1_ARM,
        'g1_head_sha256':base.G1_HEAD_SHA,
        'independent_cases':len(eighth),
        'independent_seed':learned8.SEED,
        'independent_dataset_sha256':context7.sha_rows(eighth),
        'thresholds_predeclared':list(learned8.THRESHOLDS),
        'training_scoring_only_context_parameters':True,
        'no_fixed_cardinality':True,
        'no_iterative_graph_completion':True,
    }
    for name,expected in required.items():
        if type(report.get(name)) is not type(expected) or report[name]!=expected:
            raise ValueError('GAMER_G2_0009_G2_0008_CONTRACT:'+name)
    if report.get('parent_hashes')!=expected_parent_hashes:
        raise ValueError('GAMER_G2_0009_G2_0008_LINEAGE')
    if report.get('context_weights_sha256')!=backbone.sha256(weights_path):
        raise ValueError('GAMER_G2_0009_G2_0008_WEIGHTS_DRIFT')
    weight_map=load_file(str(weights_path),device='cpu')
    for prefix,count_field in (('context.','g2_context_trainable_parameters'),
                               ('no_neighborhood.','control_trainable_parameters')):
        subset={key[len(prefix):]:value for key,value in weight_map.items() if key.startswith(prefix)}
        if not subset or sum(value.numel() for value in subset.values())!=report.get(count_field):
            raise ValueError('GAMER_G2_0009_G2_0008_BAD_WEIGHT_SET')
    if set(weight_map)!={key for key in weight_map if key.startswith(('context.','no_neighborhood.'))}:
        raise ValueError('GAMER_G2_0009_G2_0008_UNEXPECTED_WEIGHTS')
    rows=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    calibration_cases=report.get('calibration_cases')
    if type(calibration_cases) is not int or calibration_cases<1 or len(rows)!=calibration_cases+len(eighth):
        raise ValueError('GAMER_G2_0009_G2_0008_CASE_COUNT')
    names={PARENT,*[f'learned_p{round(t*100):02d}' for t in learned8.THRESHOLDS],
           *[f'no_neighborhood_p{round(t*100):02d}' for t in learned8.THRESHOLDS]}
    for index,row in enumerate(rows):
        group='previously_inspected_calibration' if index<calibration_cases else 'new_independent_validation'
        local=index+1 if index<calibration_cases else index-calibration_cases+1
        if (row.get('group')!=group or row.get('index')!=local or
            set(row.get('configurations',{}))!=names):
            raise ValueError('GAMER_G2_0009_G2_0008_CASE_SCHEMA')
        if index>=calibration_cases:
            original=eighth[index-calibration_cases]
            if (row.get('prompt_sha256')!=base._sha(original['prompt']) or
                row.get('family')!=original['family'] or
                row.get('gold_binding_edges')!=len(base.gold_binding_edges(original))):
                raise ValueError('GAMER_G2_0009_G2_0008_CASE_IDENTITY')
    for field,subset in (('calibration_metrics',rows[:calibration_cases]),
                         ('independent_metrics',rows[calibration_cases:])):
        if _metrics_from_records(subset,names)!=report.get(field):
            raise ValueError('GAMER_G2_0009_G2_0008_METRICS:'+field)
    chosen,selection=learned8.choose_rule(report['calibration_metrics'])
    if chosen is not None or selection!=report.get('calibration_selection'):
        raise ValueError('GAMER_G2_0009_G2_0008_SELECTION')
    return report,rows[calibration_cases:],{
        'g2_0008_report_sha256':backbone.sha256(report_path),
        'g2_0008_cases_sha256':backbone.sha256(cases_path),
        'g2_0008_weights_sha256':backbone.sha256(weights_path),
    }


def load_frozen_scorers(path,width,expected_sha):
    path=Path(path)
    if backbone.sha256(path)!=expected_sha:raise ValueError('GAMER_G2_0009_G2_0008_WEIGHTS_DRIFT')
    checkpoint=load_file(str(path),device='cpu')
    frozen=[]
    for prefix in ('context.','no_neighborhood.'):
        state={key[len(prefix):]:tensor for key,tensor in checkpoint.items() if key.startswith(prefix)}
        model=learned8.GraphContextScorer(width)
        model.load_state_dict(state,strict=True)
        model.eval().requires_grad_(False)
        frozen.append(model)
    if set(checkpoint)!={prefix+key for prefix,net in zip(('context.','no_neighborhood.'),frozen)
                         for key in net.state_dict()}:
        raise ValueError('GAMER_G2_0009_UNEXPECTED_PARENT_TENSOR')
    return frozen


class RetentionScorer(nn.Module):
    """Trainable correction of frozen G2-0005 odds for an existing edge only.

    Frozen G2-0008 score is an input (detached), not a trainable parameter.
    Zero-initialized residual ensures initial decoding retains every parent
    edge at a threshold <= .50.
    """
    def __init__(self,width,hidden=64):
        super().__init__()
        if type(width) is not int or width<4 or hidden<8:
            raise ValueError('GAMER_G2_0009_BAD_WIDTH')
        self.width=width
        self.network=nn.Sequential(nn.Linear(6*width+learned8.EXTRA+1,hidden),nn.GELU(),
                                   nn.Linear(hidden,hidden//2),nn.GELU(),
                                   nn.Linear(hidden//2,1))
        nn.init.zeros_(self.network[-1].weight)
        nn.init.zeros_(self.network[-1].bias)

    def forward(self,features,base_odds,prior_logits):
        if (features.ndim!=2 or features.shape[1]!=6*self.width+learned8.EXTRA or
            base_odds.shape!=(len(features),) or prior_logits.shape!=(len(features),)):
            raise ValueError('GAMER_G2_0009_BAD_MODEL_INPUT')
        return base_odds+self.network(torch.cat((features,prior_logits.unsqueeze(1)),dim=1)).squeeze(-1)


def encode_retention(cases,context,control,*,device,group):
    """Extract only selected G2-0005 edges; gold is never a feature."""
    cache=[]
    context=context.to(device).eval().requires_grad_(False)
    control=control.to(device).eval().requires_grad_(False)
    with torch.inference_mode():
        for index,item in enumerate(cases,1):
            selected=[i for i,edge in enumerate(item['edges']) if edge in item['parent']]
            if len(selected)!=len(item['parent']):
                raise ValueError('GAMER_G2_0009_PARENT_EDGE_OUTSIDE_CANDIDATES')
            features=item['features'][selected].detach().cpu()
            absent=item['without_context'][selected].detach().cpu()
            odds=item['base_scores'][selected].detach().cpu()
            priors=context(features.to(device),odds.to(device)).detach().cpu()
            control_priors=control(absent.to(device),odds.to(device)).detach().cpu()
            cache.append({'index':item['index'],'prompt_sha256':item['prompt_sha256'],
                          'family':item['family'],'anchors':item['anchors'],'gold':item['gold'],
                          'parent':item['parent'],'features':features,'without_context':absent,
                          'base_scores':odds,'prior':priors,'control_prior':control_priors,
                          'retention_edges':[item['edges'][i] for i in selected]})
            if index==1 or index%40==0 or index==len(cases):
                print(f'GAMER_G2_RECALL_ENCODE {group} {index}/{len(cases)}',flush=True)
    return cache


def train_retention(net,cases,*,positive_weight,steps=STEPS,batch=BATCH,lr=LR,seed=SEED,
                    device='cpu',feature_name='features',prior_name='prior'):
    if (not cases or type(steps) is not int or steps<1 or type(batch) is not int or batch<2 or
        not math.isfinite(positive_weight) or positive_weight<=1 or
        (feature_name,prior_name) not in (('features','prior'),('without_context','control_prior'))):
        raise ValueError('GAMER_G2_0009_BAD_TRAIN_CONFIG')
    features=[];odds=[];prior=[];labels=[];counts=Counter()
    rng=random.Random(seed)
    for item in cases:
        pos=[i for i,e in enumerate(item['retention_edges']) if e in item['gold']]
        neg=[i for i,e in enumerate(item['retention_edges']) if e not in item['gold']]
        # Every positive is protected. Negatives include high-confidence hard
        # mistakes and a reproducible random tail. No DEV answers enter here.
        neg.sort(key=lambda i:(-float(item['base_scores'][i]),i))
        top=neg[:max(8,5*len(pos))]
        tail=neg[len(top):]
        top+=rng.sample(tail,min(len(tail),max(4,len(pos))))
        chosen=pos+top
        if not chosen:continue
        features.append(item[feature_name][chosen]);odds.append(item['base_scores'][chosen])
        prior.append(item[prior_name][chosen])
        labels.extend([1.]*len(pos)+[0.]*len(top))
        counts['positive']+=len(pos);counts['negative']+=len(top)
    if not counts['positive'] or not counts['negative']:
        raise ValueError('GAMER_G2_0009_NO_TRAINING_SIGNAL')
    x=torch.cat(features);o=torch.cat(odds);p=torch.cat(prior)
    y=torch.tensor(labels,dtype=torch.float32)
    if not all(torch.isfinite(v).all() for v in (x,o,p,y)):
        raise ValueError('GAMER_G2_0009_NONFINITE_INPUT')
    net=net.to(device).train()
    optim=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=0.001)
    gen=torch.Generator().manual_seed(seed)
    n=len(y);last=None
    for step in range(steps):
        idx=torch.randint(n,(min(batch,n),),generator=gen)
        a=x[idx].to(device);b=o[idx].to(device);c=p[idx].to(device);target=y[idx].to(device)
        logits=net(a,b,c)
        # Loss is asymmetric *by design*: a false rejection of an actual
        # relationship is much more expensive than a retained false edge.
        weights=torch.where(target>.5,positive_weight,1.)
        loss=F.binary_cross_entropy_with_logits(logits,target,weight=weights)
        if not torch.isfinite(loss):raise ValueError('GAMER_G2_0009_NONFINITE_LOSS')
        optim.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        optim.step();last=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_RECALL_TRAIN {feature_name} posw={positive_weight:g} '
                  f'{step+1}/{steps} loss={last:.6f}',flush=True)
    net.eval().requires_grad_(False)
    return {'steps':steps,'last_loss':last,'train_cases':len(cases),
            'samples':dict(counts),'positive_retention_weight':positive_weight,
            'feature_name':feature_name,'prior_name':prior_name}


def _rule_names():
    return [f'retain_w{w:02d}_p{round(t*100):02d}'
            for w in (4,8) for t in THRESHOLDS]


def evaluate(cases,weighted4,weighted8,no_context4,no_context8,*,device,group,thresholds=THRESHOLDS):
    names=[f'retain_w{w:02d}_p{round(t*100):02d}' for w in (4,8) for t in thresholds]
    names += [f'no_context_w{w:02d}_p{round(t*100):02d}' for w in (4,8) for t in thresholds]
    summaries=defaultdict(list);records=[]
    with torch.inference_mode():
        for item in cases:
            a=item['features'].to(device);b=item['base_scores'].to(device)
            c=item['prior'].to(device)
            out4=weighted4(a,b,c).detach().cpu()
            out8=weighted8(a,b,c).detach().cpu()
            control4=no_context4(item['without_context'].to(device),b,
                                 item['control_prior'].to(device)).detach().cpu()
            control8=no_context8(item['without_context'].to(device),b,
                                 item['control_prior'].to(device)).detach().cpu()
            if not all(torch.isfinite(v).all() for v in (out4,out8,control4,control8)):
                raise ValueError('GAMER_G2_0009_NONFINITE_EVALUATION')
            edges=item['retention_edges'];parent=item['parent']
            if set(edges)!=parent:raise ValueError('GAMER_G2_0009_PARENT_EDGE_MISMATCH')
            metrics={PARENT:sel.score_set(parent,item['gold'])}
            for score,variant in ((out4,'retain_w04'),(out8,'retain_w08'),
                                  (control4,'no_context_w04'),(control8,'no_context_w08')):
                for threshold in thresholds:
                    cutoff=math.log(threshold/(1-threshold))
                    found={e for e,value in zip(edges,score) if float(value)>=cutoff}
                    if not found.issubset(parent):raise ValueError('GAMER_G2_0009_NEW_EDGE_CREATED')
                    metrics[f'{variant}_p{round(threshold*100):02d}']=sel.score_set(found,item['gold'])
            for name,result in metrics.items():
                summaries[name].append({**result,'family':item['family']})
            records.append({'group':group,'index':item['index'],'family':item['family'],
                            'prompt_sha256':item['prompt_sha256'],'gold_binding_edges':len(item['gold']),
                            'g1_predicted_nodes':len(item['anchors']),
                            'density_bucket':density._bucket(len(item['anchors'])),
                            'parent_edges':len(parent),'configurations':metrics})
    return {name:base.metrics(rows) for name,rows in summaries.items()},records


def choose_rule(calibration):
    """Only predeclared, recall-safe rules are eligible; never use independent gold."""
    parent=calibration[PARENT]
    floor=max(.60,parent['recall']-.01)
    choices=[name for name in _rule_names() if name in calibration and
             calibration[name]['recall']>=floor and
             calibration[name]['false_positive']<parent['false_positive'] and
             calibration[name]['f1']>parent['f1']]
    if not choices:return None,{'eligible_count':0,'recall_floor':floor,
                                 'reason':'NO_RECALL_SAFE_RULE_BEATS_PARENT'}
    best=max(choices,key=lambda name:(calibration[name]['f1'],
                                      calibration[name]['recall'],
                                      -calibration[name]['false_positive'],name))
    return best,{'eligible_count':len(choices),'recall_floor':floor,
                 'reason':'MAX_F1_WITH_001_RECALL_LOSS_BOUND'}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,output,device='cuda',max_length=2048,
        validation_limit=VALIDATION_LIMIT,steps=STEPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0009_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,rel.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(item['prompt']) for item in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0009_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0009_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,
                                       limit=learned8.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    motifs,motif_audit=context7.learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection_dir=Path(selection_run)
    selection=json.loads((selection_dir/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0009_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
    bootstrap_head=deepcopy(head)
    _,_,fourth_hashes=density.verify_parent(relational_run,fourth,head,
                                            {**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,
                                           {**hashes,**disc_hashes,**fourth_hashes})
    _,_,sixth_hashes=context7.verify_graph6_parent(consistency_run,sixth,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},
                       sel._hash_json(train_evidence))
    seventh_report,_,seventh_hashes=learned8.verify_context7_parent(context_run,seventh,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},
                       sel._hash_json(motif_audit))
    eighth_report,eighth_saved,eighth_hashes=verify_learned8_parent(learned_run,eighth,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,
                        **seventh_hashes})
    fresh=new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,
                              limit=validation_limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={key:value.detach().cpu().clone() for key,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=bootstrap_head.to(device).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError('GAMER_G2_0009_BACKBONE_UNFROZEN')
    captured_train=rel.capture(bootstrap_head,model,tok,train,device=device,
                               max_length=max_length,group='train')
    captured_cal=rel.capture(bootstrap_head,model,tok,eighth,device=device,
                             max_length=max_length,group='g2_0008_replay')
    captured_fresh=rel.capture(bootstrap_head,model,tok,fresh,device=device,
                               max_length=max_length,group='new_independent')
    with torch.inference_mode():
        encoded_train=learned8.encode_cases(captured_train,head,allowed,rule,device=device,group='train')
        encoded_cal=learned8.encode_cases(captured_cal,head,allowed,rule,device=device,group='calibration')
        encoded_fresh=learned8.encode_cases(captured_fresh,head,allowed,rule,device=device,group='new_independent')
    width=encoded_train[0]['vectors'].shape[1]
    frozen_context,frozen_control=load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,width,
                                                      eighth_report['context_weights_sha256'])
    # Actual replay of frozen G2-0008 predictions on its prior independent cohort.
    replay_metrics,replay_cases=learned8.evaluate(encoded_cal,frozen_context.to(device),
                              frozen_control.to(device),device=device,
                              group='new_independent_validation')
    if replay_metrics!=eighth_report['independent_metrics'] or len(replay_cases)!=len(eighth_saved):
        raise ValueError('GAMER_G2_0009_G2_0008_PREDICTION_REPLAY')
    for new,prior in zip(replay_cases,eighth_saved):
        if (new['configurations']!=prior['configurations'] or
            new['prompt_sha256']!=prior['prompt_sha256'] or
            new['g1_predicted_nodes']!=prior['g1_predicted_nodes']):
            raise ValueError('GAMER_G2_0009_G2_0008_CASE_REPLAY')
    train_cases=encode_retention(encoded_train,frozen_context,frozen_control,
                                 device=device,group='train')
    calibration_cases=encode_retention(encoded_cal,frozen_context,frozen_control,
                                       device=device,group='calibration')
    independent_cases=encode_retention(encoded_fresh,frozen_context,frozen_control,
                                       device=device,group='new_independent')
    # All three arms start identically; only positive-retention cost and
    # neighborhood input differ. G1/G2 and both G2-0008 scorers remain frozen.
    torch.manual_seed(SEED)
    w4=RetentionScorer(width)
    w8=deepcopy(w4)
    control4=deepcopy(w4)
    control8=deepcopy(w4)
    train4=train_retention(w4,train_cases,positive_weight=4.,steps=steps,device=device)
    train8=train_retention(w8,train_cases,positive_weight=8.,steps=steps,device=device)
    train_control4=train_retention(control4,train_cases,positive_weight=4.,steps=steps,device=device,
                                   feature_name='without_context',prior_name='control_prior')
    train_control8=train_retention(control8,train_cases,positive_weight=8.,steps=steps,device=device,
                                   feature_name='without_context',prior_name='control_prior')
    calibration,cal_cases=evaluate(calibration_cases,w4,w8,control4,control8,device=device,
                                   group='previously_inspected_calibration')
    selected,selection_info=choose_rule(calibration)
    independent,fresh_cases=evaluate(independent_cases,w4,w8,control4,control8,device=device,
                                     group='new_independent_validation')
    rel.validate_original_state(head,original)
    parent=independent[PARENT]
    buckets=density.bucket_metrics(fresh_cases,tuple(independent))
    crowded=buckets['crowded_49_128']
    has_crowded=crowded.get(PARENT,{}).get('cases',0)>0
    ablation_name=(selected.replace('retain_w','no_context_w',1) if selected else None)
    gates={name:{
        'f1_improved':independent[name]['f1']>parent['f1'],
        'false_positives_reduced':independent[name]['false_positive']<parent['false_positive'],
        'recall_at_least_060':independent[name]['recall']>=.60,
        'recall_at_least_parent_minus_001':independent[name]['recall']>=parent['recall']-.01,
        'crowded_f1_improved':bool(has_crowded and crowded[name]['f1']>crowded[PARENT]['f1']),
        'crowded_recall_at_least_parent_minus_002':bool(has_crowded and
             crowded[name]['recall']>=crowded[PARENT]['recall']-.02),
        'no_new_edges_generated':True,
    } for name in _rule_names()}
    screen=gates.get(selected)
    report={
        'schema':SCHEMA,'status':'G2_RECALL_PRESERVING_GRAPH_DEV_NOT_CERTIFIED',
        'g1_development_candidate':base.G1_ARM,'g1_head_sha256':manifest['head_sha256'],
        'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0008_replay_pass':True,
        'g2_0008_calibration_pass':False,'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'certification_authorized':False,
        'graph_execution_authorized':False,'memory_writes':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'inference_uses_predicted_g1_only':True,
        'no_gold_graph_at_inference':True,
        'gold_used_for_training_and_offline_scoring_only':True,
        'train_cases':len(train),'training_seed':SEED,'training_arm_w04':train4,
        'training_arm_w08':train8,'training_control_no_context_w04':train_control4,
        'training_control_no_context_w08':train_control8,
        'trainable_parameters_per_arm':sum(p.numel() for p in w4.parameters()),
        'calibration_cases':len(eighth),'calibration_seed':learned8.SEED,
        'calibration_dataset_sha256':context7.sha_rows(eighth),
        'independent_cases':len(fresh),'independent_seed':SEED,
        'independent_dataset_sha256':context7.sha_rows(fresh),
        'thresholds_predeclared':list(THRESHOLDS),'positive_weights_predeclared':list(TRAIN_WEIGHTS),
        'calibration_metrics':calibration,'calibration_selection':selection_info,
        'selected_rule':selected,'independent_metrics':independent,
        'independent_gates_by_rule':gates,'independent_by_node_density':buckets,
        'selected_independent_screen_pass':bool(screen and all(screen.values()) and
           ablation_name and independent[selected]['f1']>independent[ablation_name]['f1']),
        'selected_beats_matched_no_context':bool(ablation_name and
           independent[selected]['f1']>independent[ablation_name]['f1']),
        'no_new_edges_allowed':True,'no_fixed_cardinality':True,
        'parent_hashes':{**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,
                         **sixth_hashes,**seventh_hashes,**eighth_hashes},
    }
    # Refuse overwrite, even when an empty directory appears concurrently.
    output.mkdir(parents=True,exist_ok=False)
    safe={'w04.'+key:tensor.detach().cpu().contiguous() for key,tensor in w4.state_dict().items()}
    safe.update({'w08.'+key:tensor.detach().cpu().contiguous() for key,tensor in w8.state_dict().items()})
    safe.update({'no_context_w04.'+key:tensor.detach().cpu().contiguous()
                 for key,tensor in control4.state_dict().items()})
    safe.update({'no_context_w08.'+key:tensor.detach().cpu().contiguous()
                 for key,tensor in control8.state_dict().items()})
    save_file(safe,str(output/WEIGHTS))
    report['retention_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(cal_cases+fresh_cases))
    return report
