"""G2-0053: frozen G1/0048 occurrence evidence -> learned bounded graph ranking.

The parent 0052 constructor is reused unchanged, as are G2-0050's exact TRAIN
counterfactual pairs. Weights are research-only. No model selection on the
previously inspected probe; no sealed DEV/TEST capture and no production route.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA='nanojev.gamer.graph.g2.repaired-graph-learning.v1'
STATUS='G2_0053_TRAIN_ONLY_SCORING_AUDITED_NOT_CERTIFIED'
REPORT='g2_repaired_graph_learning_report.json'
LEDGER='g2_repaired_graph_learning_per_case.jsonl.gz'
WEIGHTS='g2_0053_scoring_development.safetensors'
POLICY='g2_0053_frozen_policy.json'
SEED=460553
STEPS=240
BATCH=4
LR=2e-4
MAX_STEPS=800
RANK_BUDGET=512
DECODE_BUDGET=96
NEGATIVES=192
EVAL_CHUNK=2048
EMBED_WIDTH=96
RELATIONS=tuple(r for r in BIND_CLASSES if r!='none')
REL_INDEX={relation:i for i,relation in enumerate(RELATIONS)}
KIND_INDEX={kind:i for i,kind in enumerate(SPAN_KINDS)}


def refuse(reason):
    raise ValueError('GAMER_G2_0053_'+reason)


def verify_parent52(directory):
    """Proof that the frozen 0052 inputs met their predeclared gate."""
    folder=Path(directory)
    report=json.loads((folder/p52.REPORT).read_text(encoding='utf-8'))
    sections=report.get('interface_repair',{}).get('sections',{})
    probe=sections.get('counterfactual_probe',{})
    train=sections.get('counterfactual_train',{})
    if (report.get('schema')!=p52.SCHEMA or report.get('status')!=p52.STATUS or
        report.get('production_promotion') is not False or
        report.get('sealed_test_used') is not False or
        report.get('parent50_replay_diagnostic',{}).get('exact_parent50_legacy_replay') is not True or
        report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True or
        probe.get('repaired_reachable')!=31 or probe.get('repaired_both_pairs')!=15 or
        probe.get('repaired_endpoints_present')!=863 or probe.get('required_endpoints')!=864 or
        train.get('repaired_reachable')!=96 or train.get('repaired_both_pairs')!=48 or
        train.get('missing_gold_edges')!=0 or
        p52.p51.SOURCE.backbone.sha256(folder/p52.LEDGER)!=report.get('ledger_sha256')):
        refuse('PARENT52_CONTRACT_OR_LEDGER')
    hashes={'g2_0052_report_sha256':p52.p51.SOURCE.backbone.sha256(folder/p52.REPORT),
            'g2_0052_ledger_sha256':p52.p51.SOURCE.backbone.sha256(folder/p52.LEDGER)}
    return report,hashes


class EdgeRanker(nn.Module):
    """Trainable G2 edge scorer over *frozen* G2-0048 occurrence embeddings."""
    def __init__(self):
        super().__init__()
        self.relation=nn.Embedding(len(RELATIONS),16)
        self.kind=nn.Embedding(len(SPAN_KINDS),8)
        dim=4*EMBED_WIDTH+16+2*8+2+5
        self.head=nn.Sequential(nn.LayerNorm(dim),nn.Linear(dim,128),nn.GELU(),
                                nn.Linear(128,1))

    def forward(self,sample,select):
        # No gold exists in the inference sample. All indices are constructed
        # from G1-predicted occurrences and permitted type signatures.
        device=self.relation.weight.device
        # All captured evidence is CPU-resident. Index there BEFORE sending
        # bounded edge batches to CUDA; CPU tensors cannot require CUDA indices.
        idx=select.detach().to(device='cpu',dtype=torch.long)
        n=sample['node'].to(device=device,dtype=torch.float32)
        src=sample['src'].index_select(0,idx).to(device=device)
        dst=sample['dst'].index_select(0,idx).to(device=device)
        rel=sample['relation'].index_select(0,idx).to(device=device)
        kinds=sample['kinds'].to(device=device)
        margin=sample['margin'].to(device=device)
        a=n[src];b=n[dst]
        x=torch.cat((a,b,a*b,a-b,self.relation(rel),
              self.kind(kinds[src]),self.kind(kinds[dst]),
              margin[src,None].expand(-1,1),margin[dst,None].expand(-1,1),
              sample['geometry'].index_select(0,idx).to(device=device)),dim=1)
        return self.head(x).flatten()


def frozen_features(raw,expanded,encoder,section):
    """Called before gold lookup: only prepared prompt, frozen hidden, spans and typed edges.

    Encoder is G2-0048's actual learned contextual encoder, with immutable
    weights. The new edge head is separately trained; its scores must not be
    advertised as G2-0048 original frozen graph logits.
    """
    if section not in ('counterfactual_train','counterfactual_probe',
                       'fresh_counterfactual_probe'):
        refuse('COHORT_NAME')
    hidden=raw.get('hidden')
    if not isinstance(hidden,torch.Tensor) or hidden.ndim!=2 or \
            hidden.shape[-1]!=encoder.input_width:
        refuse('FROZEN_HIDDEN_REQUIRED')
    anchors=expanded['anchors']
    if len(anchors)>p52.MAX_ANCHORS or not anchors or len(set(anchors))!=len(anchors):
        refuse('ANCHOR_INVARIANT')
    prepared=raw['prepared']
    if 'eligible' not in prepared:refuse('PREPARED_ELIGIBILITY_MISSING')
    windows,mask=d48.x31.occurrence_windows(hidden,prepared,anchors)
    target=next(encoder.parameters()).device
    with torch.inference_mode():
        embeddings,_=encoder.encoder(windows.to(device=target,dtype=torch.float32),
                                    mask.to(device=target))
    if embeddings.shape!=(len(anchors),EMBED_WIDTH) or not torch.isfinite(embeddings).all():
        refuse('PARENT48_ENCODER_ALIGNMENT')
    mapping={a:i for i,a in enumerate(anchors)}
    edge_list=sorted(expanded['typed_edges'])
    if len(edge_list)!=len(set(edge_list)) or not edge_list:refuse('EDGE_INVENTORY')
    src=[];dst=[];relations=[];geo=[]
    total=len(prepared['prompt'])
    for source,dest,relation in edge_list:
        if source not in mapping or dest not in mapping or relation not in REL_INDEX:
            refuse('TYPE_SIGNATURE_OR_ENDPOINT')
        src.append(mapping[source]);dst.append(mapping[dest]);relations.append(REL_INDEX[relation])
        geo.append([math.tanh((dest[0]-source[0])/128.),
                    math.tanh((dest[1]-source[1])/128.),
                    math.tanh((source[1]-source[0])/32.),
                    math.tanh((dest[1]-dest[0])/32.),
                    (source[0]+dest[0])/max(1.,2.*total)])
    lattice=expanded['raw_lattice'];raw_logits=expanded['logits']
    prov=expanded['new_endpoint_provenance']
    margins=[]
    for anchor in anchors:
        matches=lattice.get(anchor[:2],())
        k=KIND_INDEX[anchor[2]]+1
        if matches:
            margin=max(float(raw_logits[t,w,k]-raw_logits[t,w,0]) for t,w in matches)
        elif anchor in prov:
            margin=float(prov[anchor]['neural_margin'])
        else:
            margin=-8.0
        margins.append(max(-12.,min(12.,margin))/12.)
    return {'section':section,'edges':edge_list,
        'node':embeddings.cpu().to(torch.float16).contiguous(),
        'src':torch.tensor(src,dtype=torch.long),
        'dst':torch.tensor(dst,dtype=torch.long),
        'relation':torch.tensor(relations,dtype=torch.long),
        'kinds':torch.tensor([KIND_INDEX[a[2]] for a in anchors],dtype=torch.long),
        'margin':torch.tensor(margins,dtype=torch.float32),
        'geometry':torch.tensor(geo,dtype=torch.float32)}


def mark_truth(sample):
    """Called only AFTER all label-blind candidate features are constructed."""
    gold=sample.pop('labels')
    if not isinstance(gold,set):gold=set(gold)
    edges=sample.pop('edges')
    lookup={edge:i for i,edge in enumerate(edges)}
    if len(lookup)!=len(edges):refuse('DUPLICATE_EDGE')
    present=[lookup[e] for e in gold if e in lookup]
    sample['positive']=torch.tensor(sorted(present),dtype=torch.long)
    sample['truth_count']=len(gold)
    sample['edge_count']=len(edges)
    if len(present)>len(gold):refuse('LABEL_ARITHMETIC')
    return sample


@torch.no_grad()
def score_all(net,sample,chunk=EVAL_CHUNK):
    net.eval()
    out=[]
    for start in range(0,sample['edge_count'],chunk):
        ids=torch.arange(start,min(start+chunk,sample['edge_count']))
        out.append(net(sample,ids).detach().cpu())
    return torch.cat(out)


def evaluate(net,samples):
    """Fixed 512-edge rank budget and fixed sigmoid(0)=0.5 decoding cutoff."""
    rows=[]
    for sample in samples:
        scores=score_all(net,sample)
        order=torch.argsort(scores,descending=True,stable=True)
        ranked=order[:RANK_BUDGET]
        chosen=ranked[scores[ranked]>=0][:DECODE_BUDGET]
        gold=set(sample['positive'].tolist())
        ranked_set=set(ranked.tolist());chosen_set=set(chosen.tolist())
        row={'section':sample['section'],'prompt_sha256':sample['prompt_sha256'],
             'pair':sample['pair'],'family':sample['family'],'mode':sample['mode'],
             'gold_edges':sample['truth_count'],'reachable_gold_edges':len(gold),
             'candidate_edges':sample['edge_count'],
             'ranked_edges':len(ranked),'selected_edges':len(chosen_set),
             'rank_missing':sample['truth_count']-len(gold&ranked_set),
             'missing':sample['truth_count']-len(gold&chosen_set),
             'extra':len(chosen_set-gold),
             'reachable':len(gold)==sample['truth_count'],
             'exact':len(gold)==sample['truth_count'] and chosen_set==gold}
        rows.append(row)
    return rows


def aggregate(rows,pairs):
    shas={x['prompt_sha256']:x for x in rows}
    if len(rows)!=len(shas):refuse('DUPLICATE_EVALUATION')
    out={'cases':len(rows),'reachable':sum(x['reachable'] for x in rows),
         'rank_coverage':sum(x['rank_missing']==0 for x in rows),
         'exact':sum(x['exact'] for x in rows),
         'missing':sum(x['missing'] for x in rows),
         'extra':sum(x['extra'] for x in rows),
         'rank_missing':sum(x['rank_missing'] for x in rows),
         'mean_inventory':round(sum(x['candidate_edges'] for x in rows)/max(1,len(rows)),1),
         'max_inventory':max((x['candidate_edges'] for x in rows),default=0),
         'mean_rank_budget':round(sum(x['ranked_edges'] for x in rows)/max(1,len(rows)),1),
         'families':{family:{'cases':sum(x['family']==family for x in rows),
                 'exact':sum(x['family']==family and x['exact'] for x in rows),
                 'rank_coverage':sum(x['family']==family and x['rank_missing']==0 for x in rows)}
             for family in ('allocation','encounter')}}
    out['both_rank_reachable_pairs']=sum(all(shas[sha]['rank_missing']==0 for sha in p['sha256']) for p in pairs)
    out['both_exact_pairs']=sum(all(shas[sha]['exact'] for sha in p['sha256']) for p in pairs)
    return out


def _batch_indices(model,sample,generator):
    """Hard negatives from full inventory plus independent random competitors."""
    pos=sample['positive'];count=sample['edge_count']
    if len(pos)==0:refuse('TRAINING_WITHOUT_POSITIVE')
    scores=score_all(model,sample)
    scores[pos]=-float('inf')
    hardness=min(NEGATIVES//2,count-len(pos))
    ranked=torch.topk(scores,k=hardness).indices
    # Random negatives carry no label-derived rank information; filter gold.
    perm=torch.randperm(count,generator=generator)
    positive_set=set(pos.tolist())
    random_neg=[int(i) for i in perm.tolist() if i not in positive_set][:NEGATIVES-hardness]
    return torch.tensor(sorted(set(pos.tolist()+ranked.tolist()+random_neg)),dtype=torch.long)


def fit_ranker(samples,pairs,device='cpu',steps=STEPS,batch=BATCH):
    if not samples or not 1<=steps<=MAX_STEPS or not 1<=batch<=8 or device not in ('cpu','cuda'):
        refuse('TRAIN_BUDGET')
    torch.manual_seed(SEED)
    generator=torch.Generator().manual_seed(SEED)
    model=EdgeRanker().to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=LR,weight_decay=1e-4)
    mine={};history=[]
    for step in range(1,steps+1):
        if step==1 or (step-1)%80==0:
            for sample in samples:
                mine[sample['prompt_sha256']]=_batch_indices(model,sample,generator)
            model.train()
        picks=torch.randperm(len(samples),generator=generator)[:batch].tolist()
        opt.zero_grad(set_to_none=True)
        losses=[]
        for i in picks:
            sample=samples[i];idx=mine[sample['prompt_sha256']]
            score=model(sample,idx)
            pos_set=set(sample['positive'].tolist())
            mask=torch.tensor([int(x) in pos_set for x in idx.tolist()],device=device)
            if not bool(mask.any()) or not bool((~mask).any()):refuse('BAD_TRAINING_BATCH')
            # Balanced edge classification and source-/relation-aware hard competition.
            positive=F.softplus(-score[mask]).mean()
            negative=F.softplus(score[~mask]).mean()
            hardest=torch.topk(score[~mask],k=min(8,int((~mask).sum()))).values.mean()
            rank=F.softplus(.5+hardest-score[mask].mean())
            losses.append(positive+negative+.25*rank)
        loss=torch.stack(losses).mean()
        if not bool(torch.isfinite(loss)):refuse('NONFINITE_TRAIN_LOSS')
        loss.backward()
        grad=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(grad)):refuse('NONFINITE_TRAIN_GRADIENT')
        opt.step()
        if step==1 or step%80==0 or step==steps:
            entry={'step':step,'loss':round(float(loss.detach().cpu()),6)}
            history.append(entry)
            print('GAMER_G2_0053_TRAIN '+json.dumps(entry,sort_keys=True),flush=True)
    model.eval().requires_grad_(False)
    return model,history


def _pairs_for_names(pairs,selected):
    return [p for p in pairs if p['id'] in selected]


def finalize(samples,pairs,replay,report,*,device,steps,batch,parent_hashes,parent50,
             parent48_sha):
    if not replay['exact_parent50_legacy_replay'] or \
            not report['interface_repair']['decision']['coverage_targets_met']:
        refuse('COVERAGE_OR_REPLAY_GATE')
    samples=[mark_truth(s) for s in samples]
    if len(samples)!=2*sum(map(len,pairs.values())) or len({s['prompt_sha256'] for s in samples})!=len(samples):
        refuse('CAPTURE_COHORT_COUNT')
    train_pairs=pairs['counterfactual_train'];probe_pairs=pairs['counterfactual_probe']
    fresh_pairs=pairs.get('fresh_counterfactual_probe',[])
    # Keep entire counterfactual pairs together; reserve two pairs from each
    # family/mode cell. An index-based 1-in-6 split would wrongly hold out
    # only encounter pairs because family alternates with pair index.
    by_cell={}
    for pair in train_pairs:
        cell=(pair['family'],pair['mode'])
        by_cell.setdefault(cell,[]).append(pair)
    expected_cells={(fam,mode) for fam in ('allocation','encounter')
                    for mode in ('role_swap','renaming')}
    if set(by_cell)!=expected_cells or any(len(v)!=12 for v in by_cell.values()):
        refuse('TRAIN_PAIR_CELL_BALANCE')
    fit_ids={p['id'] for cell in sorted(by_cell) for p in by_cell[cell][:2]}
    train_ids={p['id'] for p in train_pairs if p['id'] not in fit_ids}
    trained=[s for s in samples if s['section']=='counterfactual_train' and s['pair'] in train_ids]
    fit=[s for s in samples if s['section']=='counterfactual_train' and s['pair'] in fit_ids]
    probe=[s for s in samples if s['section']=='counterfactual_probe']
    fresh=[s for s in samples if s['section']=='fresh_counterfactual_probe']
    if (len(trained)!=80 or len(fit)!=16 or len(probe)!=32 or
            len(fresh)!=2*len(fresh_pairs)):
        refuse('PAIR_SPLIT_COUNTS')
    model,history=fit_ranker(trained,_pairs_for_names(train_pairs,train_ids),
                             device=device,steps=steps,batch=batch)
    metrics={};ledger=[]
    for name,cohort,pair_list in (
        ('train',trained,_pairs_for_names(train_pairs,train_ids)),
        ('train_fit',fit,_pairs_for_names(train_pairs,fit_ids)),
        ('counterfactual_probe',probe,probe_pairs),
        *([('fresh_counterfactual_probe',fresh,fresh_pairs)] if fresh_pairs else [])):
        rows=evaluate(model,cohort);ledger+=rows
        metrics[name]=aggregate(rows,pair_list)
    benchmark=parent50['results']['replay_bce']['counterfactual_probe']['all']
    # The historical G2-0050 and the inspected 0052 probe are diagnostic data,
    # not a sealed final test. We report comparison without selection on probe.
    if benchmark.get('cases')!=32 or type(benchmark.get('exact')) is not int:
        refuse('HISTORICAL_SCORER_METRIC_SCHEMA')
    current=metrics['counterfactual_probe'];delta=current['exact']-benchmark['exact']
    rank_gate=current['rank_coverage']>=31 and current['both_rank_reachable_pairs']>=15
    benefit=delta>=4 and current['missing']+current['extra']<benchmark['missing']+benchmark['extra']
    independent=metrics.get('fresh_counterfactual_probe')
    fresh_gate=bool(independent and independent['rank_coverage']>=31 and
                    independent['both_rank_reachable_pairs']>=15 and
                    independent['exact']>=12 and independent['both_exact_pairs']>=5)
    evaluation={'schema':SCHEMA,'status':STATUS,
      'parent_hashes':parent_hashes,'frozen_encoder_parent48_sha256':parent48_sha,
      'training':{'steps':steps,'batch_prompts':batch,'learning_rate':LR,'seed':SEED,
          'optimizer':'AdamW','training_graphs':len(trained),'train_fit_graphs':len(fit),
          'probe_graphs':len(probe),'fresh_holdout_graphs':len(fresh),
          'fit_pairs_per_family_mode':2,'loss_history':history,
          'hard_negative_mining_uses_training_gold_only':True,
          'frozen_g1_and_frozen_0048_encoder':True,
          'probe_labels_never_used_in_training_or_model_selection':True},
      'selection':{'rank_budget':RANK_BUDGET,'max_output_edges':DECODE_BUDGET,
          'fixed_score_threshold':0.0,'ranker_scores_all_candidates_in_chunks':EVAL_CHUNK,
          'decoder':'fixed_threshold_no_gold_counts','gold_used_in_candidate_generation':False},
      'results':metrics,'historical_g2_0050_replay_bce_probe':{
          'exact':benchmark['exact'],'cases':benchmark['cases'],
          'missing':benchmark['missing'],'extra':benchmark['extra']},
      'probe_exact_gain_over_historical':delta,
      'gates':{'candidate_inventory_preserved':True,
          'rank_recall_preserved':rank_gate,
          'demonstrated_graph_accuracy_gain':benefit,
          'fresh_synthetic_holdout_passed':fresh_gate,
          'production_promotion':False,'g2_certified':False,
          'fresh_sealed_generalization_verified':False,
          'next_step':('REPAIR_NEURAL_RANKING_WITHOUT_CHANGING_G1' if not rank_gate else
              'REPAIR_SCORE_CALIBRATION_OR_DECODER' if not benefit else
              'REPAIR_FRESH_GENERALIZATION' if not fresh_gate else
              'REQUEST_SEALED_TEST_AND_PRODUCTION_GATE')}}
    return evaluation,{k:v.detach().cpu().clone() for k,v in model.state_dict().items()}, {
      'enabled':False,'reason':'G2-0053 research-only; not production-promoted',
      'frozen_threshold':0.0,'ranking_budget':RANK_BUDGET,'max_output_edges':DECODE_BUDGET}


def run(*,candidate52_run,candidate51_run,occurrence48_run,counterfactual50_run,output,
        device='cuda',steps=STEPS,batch_prompts=BATCH,**kwargs):
    if kwargs.get('train_pairs',48)!=48 or kwargs.get('probe_pairs',16)!=16:
        refuse('FIXED_PARENT50_COHORT_COUNTS')
    previous,parent_hashes=verify_parent52(candidate52_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0053_IMMUTABLE_OUTPUT')
    weight_path=Path(occurrence48_run)/d48.WEIGHTS
    parent48_report=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    sha=d48.backbone.sha256(weight_path)
    if sha!=parent48_report.get('weights_sha256') or parent48_report.get('schema')!=d48.SCHEMA:
        refuse('PARENT48_WEIGHT_HASH')
    weight=load_file(str(weight_path),device='cpu')
    width=weight['encoder.norm.weight'].shape[0]
    frozen=d48.OccurrenceEvidenceScorer(width).eval().requires_grad_(False)
    frozen.load_state_dict(weight,strict=True)
    frozen.to(device)
    for v in frozen.state_dict().values():
        if not torch.isfinite(v).all():refuse('NONFINITE_PARENT48')
    frozen_snapshot={k:v.detach().clone() for k,v in frozen.state_dict().items()}
    parent50=json.loads((Path(counterfactual50_run)/p52.p51.p50.REPORT).read_text(encoding='utf-8'))
    if parent50.get('schema')!=p52.p51.p50.SCHEMA:refuse('PARENT50_SCHEMA')
    def feature_callback(raw,extra,section):
        return frozen_features(raw,extra,frozen,section)
    def finish(samples,pairs,replay,report):
        # Direct architectural parent is the verified endpoint interface 0052.
        report['source_parent_schema']=p52.SCHEMA
        result=finalize(samples,pairs,replay,report,device=device,steps=steps,
            batch=batch_prompts,parent_hashes=parent_hashes,parent50=parent50,parent48_sha=sha)
        if any(not torch.equal(v,frozen.state_dict()[k]) for k,v in frozen_snapshot.items()):
            refuse('FROZEN_PARENT48_MUTATED')
        return result
    result=p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=feature_callback,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,**kwargs)
    return result
