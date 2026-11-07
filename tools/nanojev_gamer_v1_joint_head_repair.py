#!/usr/bin/env python3
"""Disposable retention-aware Gamer numerical-head repair v3; never promotion.

Starts at the FROZEN step-500 head's numerical predictions and fits a small,
zero-initialized *head-side residual* on its LoRA feature interface. Jointly
trains on independently verified ORIGINAL TRAIN inputs and sign/zero synthetic
TRAIN groups. The original TRAIN predictions supervise an explicit preservation
penalty for previously correct original cases. Cross-format classification and
signed-value consistency is trained on mathematically equivalent six-case
groups, with an exactly matched no-consistency ablation. Previously correct
original TRAIN zeros receive extra margin protection. Original DEV is exclusively a
retention/selection evaluator. Synthetic DEV selects candidates; synthetic TEST
is an already-examined, non-virgin diagnostic, never a promotion certificate.

Example:
  python -m tools.nanojev_gamer_v1_joint_head_repair --mode train \
    --run runtime/reports/gamer-v1/transition-005 \
    --expanded-run runtime/reports/gamer-v1/transition-005-expanded-head-100-0001 \
    --input runtime/reports/gamer-v1/transition-005-sign-information-probe-0001 \
    --source-features runtime/reports/gamer-v1/transition-005-head-retention-0001 \
    --output runtime/reports/gamer-v1/transition-005-joint-head-lab-0001 \
    --device cuda

NO Qwen execution, NO LoRA updates, NO original head optimizer, NO source
checkpoint writes, NO promotions. Candidate weights are stored only inside the
explicitly new output directory with exact source fingerprints.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random

from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.joint-head-repair.v3'
ZERO_MARGIN_FLOOR=1.25
FEATURES=('final_mean100','final_last')
REQUIRED_DATASET_SPLITS=('train','dev','test')


def _slog(x):
    import numpy as np
    a=np.asarray(x,dtype=np.float64)
    return np.sign(a)*np.log1p(np.abs(a))


def _validate_original_splits(run,identity):
    """Load certified original split; do not use dev for any fit/normalization."""
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as spine
    root=Path(run)
    manifest=root/'handoff_manifest.json'
    corpus=root/'certified_pairs.jsonl'
    if not manifest.is_file() or not corpus.is_file():
        raise FileNotFoundError('JOINT_SOURCE_MANIFEST_OR_CORPUS_MISSING')
    m=json.loads(manifest.read_text(encoding='utf-8'))
    rows,meta=gamer.load_cases(corpus,
        allow_partial=bool(m.get('allow_partial_smoke')),
        include_catalog_extensions=bool(m.get('bounded_catalog_extensions')),
        include_transition_games=bool(m.get('bounded_transition_games')))
    for k in ('dataset_sha256','original_prompt_corpus_sha256'):
        if meta.get(k)!=identity.get(k) or m.get(k)!=identity.get(k):
            raise ValueError('JOINT_ORIGINAL_CORPUS_PROVENANCE_MISMATCH: '+k)
    if spine.file_sha(corpus)!=identity['dataset_sha256']:
        raise ValueError('JOINT_ORIGINAL_CORPUS_BYTES_CHANGED')
    parts={k:gamer.by_split(rows,k) for k in REQUIRED_DATASET_SPLITS}
    if not all(parts.values()):
        raise ValueError('JOINT_MISSING_ORIGINAL_DATA_SPLIT')
    hashes={k:{hashlib.sha256(gamer.original_prompt(r).encode('utf-8')).hexdigest()
               for r in v} for k,v in parts.items()}
    if any(hashes[a]&hashes[b] for a,b in [('train','dev'),('train','test'),('dev','test')]):
        raise ValueError('JOINT_ORIGINAL_PROMPT_LEAKAGE_BETWEEN_SPLITS')
    return parts,hashes


def _validated_train_cache(expanded,identity,train):
    """Same exact raster and provenance as original expanded-head training."""
    from tools import nanojev_gamer_v1_expanded_head as exp
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as spine
    p=Path(expanded)/'feature_cache'/'train.npz'
    if not p.is_file():raise FileNotFoundError('JOINT_ORIGINAL_TRAIN_FEATURE_CACHE_MISSING')
    expected={**identity,'split':'train',
       'prompt_sha256':hashlib.sha256(gamer.canonical(
           [gamer.original_prompt(r) for r in train]).encode('utf-8')).hexdigest()}
    x,summary=exp._read_cache(p,expected,len(train),identity['configuration']['input_width'])
    if x.shape!=(len(train),102400):
        raise ValueError('JOINT_TRAIN_CACHE_NOT_100_VECTORS')
    return x,{'sha256':spine.file_sha(p),'path':str(p.resolve()),'capture_summary':summary,
              'prompt_sha256':expected['prompt_sha256'],'cases':len(train)}


def _same_certified_target(captured, original):
    """Compare *exact* source targets, including rational and sub-epsilon values.

    Captured controls serialize targets as Fraction strings (e.g. '-1/5000000000000000'),
    while the certified corpus records decimal numbers. Both originate from
    Fraction(str(original)), so comparing their exact rational values avoids
    float() rejecting fractions AND avoids a near-zero atol concealing changed
    signs or turning tiny nonzero targets into zero.
    """
    try:
        actual = Fraction(str(captured))
        expected = Fraction(str(original))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('JOINT_MALFORMED_CERTIFIED_TARGET') from exc
    # Decimal 'NaN'/'Infinity' are not Fraction values; no float tolerance.
    return actual == expected


def _load_verified(args):
    """Integrity check before any fitting; never accept offline/partial evidence."""
    from tools import nanojev_two_game_spine_smoke as spine
    from tools import nanojev_gamer_v1_handoff as gamer
    import numpy as np
    if not all((args.run,args.expanded_run,args.input,args.source_features,args.output)):
        raise ValueError('JOINT_REQUIRES_RUN_EXPANDED_INPUT_RETENTION_OUTPUT')
    p=Path(args.input)
    info,arrays=probe._verify_archive(
        (p/probe.CAPTURE_NAME) if p.is_dir() else p,require_source=True)
    retention,source=lab._verified_source_archive(args.source_features,
                                                   info['lineage'],require_source=True)
    if not retention['source_complete'] or retention['source_count']!=retention['source_available']:
        raise ValueError('JOINT_INCOMPLETE_ORIGINAL_DEV_RETENTION')
    lineage=info['lineage']
    if Path(lineage['source_run']).resolve()!=Path(args.run).resolve() or \
       Path(lineage['expanded_run']).resolve()!=Path(args.expanded_run).resolve():
        raise ValueError('JOINT_USER_PATHS_DIFFER_FROM_CAPTURE_LINEAGE')
    expanded_id=spine.expanded_metadata(args.expanded_run,lineage['adapter_sha256'],lineage['model_id'])
    if lineage['configuration']!=expanded_id['configuration'] or \
       lineage['checkpoint_step']!=retention['lineage']['checkpoint_step']:
        raise ValueError('JOINT_EXACT_STEP_CONFIG_MISMATCH')
    root=Path(args.run)
    parts,hashes=_validate_original_splits(root,expanded_id)
    train,cache=_validated_train_cache(args.expanded_run,expanded_id,parts['train'])
    if cache['sha256']==retention['source_feature_cache']['sha256']:
        raise ValueError('JOINT_TRAIN_CACHE_IS_DEV_CACHE')
    # Ensure the SOURCE RETENTION archive is exactly the original dev split,
    # not merely 438 arbitrary examples with plausible source labels.
    dev=parts['dev']
    if len(retention['dataset'])!=len(dev):
        raise ValueError('JOINT_RETENTION_NOT_COMPLETE_ORIGINAL_DEV')
    for row in retention['dataset']:
        i=row['source_index']
        if i<0 or i>=len(dev) or row['prompt_sha256']!=gamer.original_prompt_fingerprint(dev[i]) or \
            not _same_certified_target(row['target'],dev[i]['answer']):
            raise ValueError('JOINT_RETENTION_ROW_NOT_ORIGINAL_DEV')
    if set(row['source_index'] for row in retention['dataset'])!=set(range(len(dev))):
        raise ValueError('JOINT_ORIGINAL_DEV_COVERAGE_NOT_COMPLETE')
    if set(row['prompt_sha256'] for row in info['dataset']) & hashes['train']:
        raise ValueError('JOINT_SYNTHETIC_PROMPT_OVERLAPS_ORIGINAL_TRAIN')
    if set(row['prompt_sha256'] for row in info['dataset']) & hashes['dev']:
        raise ValueError('JOINT_SYNTHETIC_PROMPT_OVERLAPS_ORIGINAL_DEV')
    lab._check_archive_groups(info['dataset'])
    partitions=probe._test_partitions(info['dataset'])
    dev_x=np.asarray(source['lora__final_flat'],dtype=np.float32)
    if dev_x.shape!=(len(dev),102400):raise ValueError('JOINT_ORIGINAL_DEV_WIDTH_INVALID')
    # Trainer uses the same original head and original inference batch contract.
    old_train=lab._original_head_predictions(lineage,train,device=args.device,
                                             head_batch=int(lineage['configuration']['batch_size']))
    old_dev=np.asarray(source['predictions'],dtype=np.float64)
    if not np.isfinite(old_train).all() or not np.isfinite(old_dev).all():
        raise ValueError('JOINT_PRODUCTION_HEAD_NONFINITE')
    if spine.file_sha(Path(cache['path']))!=cache['sha256'] or \
       spine.file_sha(Path(lineage['checkpoint_path']))!=lineage['checkpoint_sha256'] or \
       spine.adapter_identity(Path(lineage['adapter_dir']))!=lineage['adapter_sha256']:
        raise ValueError('JOINT_SOURCE_CHANGED_DURING_TRAIN_BASELINE')
    return {'probe_info':info,'probe_arrays':arrays,'retention_info':retention,
            'retention_arrays':source,'original_train':parts['train'],
            'original_train_features':train,'original_train_predictions':old_train,
            'original_dev_predictions':old_dev,'train_cache':cache,'partitions':partitions}


def _profile(flat,feature):
    import numpy as np
    x=np.asarray(flat,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=102400:
        raise ValueError('JOINT_NOT_ORIGINAL_HEAD_INPUT')
    y=x.reshape(len(x),100,1024)
    if feature=='final_last':return np.asarray(y[:,-1,:],dtype=np.float32)
    if feature=='final_mean100':return np.asarray(y.mean(axis=1,dtype=np.float32),dtype=np.float32)
    raise ValueError('JOINT_UNSUPPORTED_FEATURE')


def _normalization(source_x,synthetic_x):
    import numpy as np
    # Training rows ONLY. Dev/test/original dev must never affect normalization.
    joined=np.concatenate([source_x,synthetic_x],axis=0).astype(np.float64)
    mu=joined.mean(axis=0)
    sigma=np.maximum(joined.std(axis=0),1e-3)
    return mu.astype(np.float32),sigma.astype(np.float32)


def _to_input(x,base,mu,sigma):
    import numpy as np
    a=(np.asarray(x,dtype=np.float32)-mu)/sigma
    b=np.clip(_slog(base),-6.,6.).astype(np.float32)[:,None]
    features=np.concatenate([np.clip(a,-16,16),b],axis=1)
    if not np.isfinite(features).all():raise ValueError('JOINT_NONFINITE_INPUT')
    return features


def _baseline_logits(old):
    import numpy as np
    cls=lab._classify_continuous(old)
    z=np.zeros((len(cls),3),dtype=np.float32)
    z[np.arange(len(cls)),cls+1]=1.5
    return z


def _build_model(torch,width=1025,hidden=128):
    class Residual(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.body=torch.nn.Sequential(torch.nn.Linear(width,hidden),
                torch.nn.GELU(),torch.nn.Linear(hidden,4))
            torch.nn.init.zeros_(self.body[-1].weight)
            torch.nn.init.zeros_(self.body[-1].bias)
        def forward(self,x):
            a=self.body(x)
            return torch.cat([2.0*torch.tanh(a[:,:1]/2.0),
                              3.0*torch.tanh(a[:,1:]/3.0)],dim=1)
    return Residual()


def _decode_correction(delta,old):
    """Numerical values AND explicit -/zero/+ class; no free near-zero credit."""
    import numpy as np
    out=np.asarray(delta,dtype=np.float64)
    base=np.asarray(old,dtype=np.float64)
    if out.shape!=(len(base),4) or not np.isfinite(out).all():
        raise ValueError('JOINT_BAD_RESIDUAL_OUTPUT')
    cls=np.argmax(_baseline_logits(base)+out[:,1:],axis=1).astype(np.int8)-1
    sl=np.clip(_slog(base)+out[:,0],-6.0,6.0)
    magnitude=np.maximum(np.expm1(np.abs(sl)),1e-6)
    val=np.where(cls==0,0.0,cls*magnitude)
    return cls,val


def _original_correct_mask(y,baseline):
    """ALL previously correct original sign classes, as defined by the gate.

    Do not require a small numerical error: correct -/0/+ predictions with
    imperfect magnitudes are acquired competence and must be protected too.
    """
    import numpy as np
    y=np.asarray(y,dtype=np.float64)
    old=np.asarray(baseline,dtype=np.float64)
    if y.shape!=old.shape or not np.isfinite(y).all() or not np.isfinite(old).all():
        raise ValueError('JOINT_ANCHOR_SHAPE_OR_NONFINITE')
    return lab._classify_continuous(old)==np.sign(y)


def _accurate_numeric_mask(y,baseline):
    """Smaller precision anchors, separate from *all* correct signs."""
    import numpy as np
    y=np.asarray(y,dtype=np.float64);old=np.asarray(baseline,dtype=np.float64)
    return _original_correct_mask(y,old) & (
        np.abs(old-y)<=np.maximum(0.05,0.05*np.abs(y)))


def _head_logits(torch,old,residual):
    from torch.nn import functional as F
    labels=torch.where(old>0.05,torch.full_like(old,2,dtype=torch.long),
        torch.where(old < -0.05,torch.zeros_like(old,dtype=torch.long),
                    torch.ones_like(old,dtype=torch.long)))
    return F.one_hot(labels,3).to(residual.dtype)*1.5+residual[:,1:]


def _preservation_loss(torch,residual,old,target,correct,numeric,*,zero_margin_weight=2.0):
    """Defend all acquired sign classes; defend numerical precision separately.

    Source DEV never contributes here. The class loss is class-balanced, and
    source TRAIN supplies additional examples of each originally correct class
    every update. Incorrect original predictions get no teacher preservation.
    """
    from torch.nn import functional as F
    if bool((numeric & ~correct).any()):
        raise ValueError('JOINT_NUMERIC_ANCHOR_NOT_ORIGINALLY_CORRECT')
    zero=residual.new_zeros(())
    classes=torch.sign(target).long()+1
    logits=_head_logits(torch,old,residual)
    if bool(correct.any()):
        chosen=logits[correct]
        yc=classes[correct]
        # Class-balanced even if fewer protected samples of one sign remain.
        c_losses=[]
        for c in (0,1,2):
            m=yc==c
            if bool(m.any()):
                # Cross-entropy explicitly protects the baseline's correct
                # classification, rather than just nudging logits toward zero.
                c_losses.append(F.cross_entropy(chosen[m],yc[m]))
        class_loss=torch.stack(c_losses).mean()
    else:class_loss=zero
    precision_loss=residual[numeric,0].square().mean() if bool(numeric.any()) else zero
    # At the frozen correct-zero baseline the class margin is 1.5. Protect
    # that margin (not merely the current argmax); use original TRAIN only.
    zmask=correct & (classes==1)
    if bool(zmask.any()):
        zlogits=logits[zmask]
        competitor=torch.maximum(zlogits[:,0],zlogits[:,2])
        zero_margin_loss=F.relu(ZERO_MARGIN_FLOOR-(zlogits[:,1]-competitor)).square().mean()
    else:zero_margin_loss=zero
    return class_loss+0.5*precision_loss+zero_margin_weight*zero_margin_loss,{
        'preserve_sign':float(class_loss.detach()),
        'preserve_numeric':float(precision_loss.detach()),
        'preserve_zero_margin':float(zero_margin_loss.detach())}


def _training_loss(torch,net,x,old,target,anchor,*,weight,source,numeric_anchor=None,zero_margin_weight=2.0):
    """Joint source/synthetic supervision plus source-only acquired competence."""
    from torch.nn import functional as F
    residual=net(x)
    cls=torch.sign(target).long()+1
    logits=_head_logits(torch,old,residual)
    ce=F.cross_entropy(logits,cls)
    raw=torch.sign(old)*torch.log1p(torch.abs(old))+residual[:,0]
    truth=torch.sign(target)*torch.log1p(torch.abs(target))
    numeric=F.smooth_l1_loss(raw,truth,beta=0.15)
    preserve=raw.new_zeros(())
    if source:
        nm=anchor if numeric_anchor is None else numeric_anchor
        preserve,_=_preservation_loss(torch,residual,old,target,anchor,nm,
                                      zero_margin_weight=zero_margin_weight)
    return weight*(ce+numeric)+preserve,{
        'ce':float(ce.detach()),'numeric':float(numeric.detach()),
        'preserve':float(preserve.detach())}


def _tensor_set(data,device,torch):
    x,base,y,sign_anchors,numeric_anchors=data
    return (torch.from_numpy(x).to(device),
            torch.as_tensor(base,dtype=torch.float32,device=device),
            torch.as_tensor(y,dtype=torch.float32,device=device),
            torch.as_tensor(sign_anchors,dtype=torch.bool,device=device),
            torch.as_tensor(numeric_anchors,dtype=torch.bool,device=device))


def _complete_training_groups(rows,targets):
    """Require both wordings and all {-epsilon,0,+epsilon} in every group."""
    from fractions import Fraction
    group=defaultdict(lambda:defaultdict(dict))
    if len(rows)!=len(targets):
        raise ValueError('JOINT_TRAIN_GROUP_ROWS_MISMATCH')
    for index,row in enumerate(rows):
        if not isinstance(row,dict) or not all(k in row for k in ('group','surface','target')):
            raise ValueError('JOINT_TRAIN_GROUP_MISSING_METADATA')
        value=Fraction(str(row['target']))
        if not _same_certified_target(value,targets[index]):
            raise ValueError('JOINT_TRAIN_GROUP_TARGET_MISMATCH')
        sign=-1 if value<0 else 1 if value>0 else 0
        if sign in group[row['group']][row['surface']]:
            raise ValueError('JOINT_TRAIN_GROUP_DUPLICATE_SIGN')
        group[row['group']][row['surface']][sign]=index
    result=[]
    for key,surfaces in sorted(group.items()):
        if len(surfaces)!=2 or any(set(cases)!={-1,0,1} for cases in surfaces.values()):
            raise ValueError('JOINT_TRAIN_GROUP_INCOMPLETE: '+str(key))
        # Verify mathematical equivalence, not merely three label categories.
        first,second=list(surfaces.values())
        for sign in (-1,0,1):
            if not _same_certified_target(rows[first[sign]]['target'],
                                          rows[second[sign]]['target']):
                raise ValueError('JOINT_TRAIN_CROSS_SURFACE_TARGET_MISMATCH: '+str(key))
        if (Fraction(str(rows[first[-1]]['target'])) !=
                -Fraction(str(rows[first[1]]['target'])) or
                Fraction(str(rows[first[0]]['target'])) != 0):
            raise ValueError('JOINT_TRAIN_SIGN_REVERSAL_TARGET_MISMATCH: '+str(key))
        group_positions=[];triples=[]
        for surface,cases in sorted(surfaces.items()):
            triple=tuple(cases[c] for c in (-1,0,1))
            triples.append(triple);group_positions.extend(triple)
        result.append((tuple(group_positions),tuple(triples)))
    if not result:raise ValueError('JOINT_TRAIN_GROUPS_EMPTY')
    return result


def _triple_loss(torch,net,x,old,triples):
    """Joint zero and reversal supervision for intact three-way families."""
    from torch.nn import functional as F
    residual=net(x)
    logits=_head_logits(torch,old,residual)
    n,z,p=map(lambda col:torch.as_tensor([t[col] for t in triples],
                                      dtype=torch.long,device=x.device),range(3))
    # Scores encode direction, independently of the base head's near-zero bias.
    direction=logits[:,2]-logits[:,0]
    margin=0.75
    order=(F.relu(margin-(direction[z]-direction[n])).square().mean()+
           F.relu(margin-(direction[p]-direction[z])).square().mean())
    # Make zero a distinct third answer, not an interval of indecision.
    zero_margin=(F.relu(margin-(logits[z,1]-logits[z,0])).square().mean()+
                 F.relu(margin-(logits[z,1]-logits[z,2])).square().mean())
    reversal=F.relu(margin-(direction[p]-direction[n])).square().mean()
    return (order+zero_margin+reversal)/5.0


def _matched_surface_pairs(groups,chosen):
    """Indices into a concatenated six-case minibatch, aligned by exact target.

    _complete_training_groups has already checked each group's signs, exact
    target equality, and two complete surfaces. Each six-case group is in
    (-,0,+) order for surface A, followed by (-,0,+) for surface B.
    """
    import numpy as np
    if not len(groups) or not len(chosen):
        raise ValueError('JOINT_NO_CROSS_SURFACE_GROUPS')
    pairs=[]
    for k,selection in enumerate(chosen):
        group,surfaces=groups[int(selection)]
        if len(group)!=6 or len(surfaces)!=2 or any(len(t)!=3 for t in surfaces):
            raise ValueError('JOINT_CROSS_SURFACE_PAIR_INCOMPLETE')
        for j in range(3):pairs.append((6*k+j,6*k+3+j))
    return np.asarray(pairs,dtype=np.int64)


def _cross_format_loss(torch,net,x,old,pairs):
    """Supervise mathematical invariance across two equivalent wordings.

    Equal answers imply equal distributions over -/0/+ AND equal signed
    numerical values. This term is not sufficient by itself (all-zero would
    satisfy it); supervised CE and signed-triple losses remain mandatory.
    """
    from torch.nn import functional as F
    if len(pairs)==0:raise ValueError('JOINT_CROSS_FORMAT_PAIRS_EMPTY')
    pair=torch.as_tensor(pairs,dtype=torch.long,device=x.device)
    if pair.ndim!=2 or pair.shape[1]!=2 or bool((pair<0).any()) or \
       bool((pair>=len(x)).any()) or bool((pair[:,0]==pair[:,1]).any()):
        raise ValueError('JOINT_INVALID_CROSS_FORMAT_PAIR')
    delta=net(x)
    logits=_head_logits(torch,old,delta)
    probability=F.softmax(logits,dim=-1)
    a,b=pair[:,0],pair[:,1]
    probability_mse=(probability[a]-probability[b]).square().sum(dim=1).mean()
    signed_log=torch.sign(old)*torch.log1p(torch.abs(old))+delta[:,0]
    numerical=F.smooth_l1_loss(signed_log[a],signed_log[b],beta=0.15)
    loss=probability_mse+numerical
    return loss,{'class_probability':float(probability_mse.detach()),
                 'signed_log_value':float(numerical.detach())}


def _paired_agreement(rows,targets,predictions,classes,partition):
    """Strict paired evaluation, including *correctness*, not just agreement.

    Constant class predictions would score 100% on mere agreement. Requiring
    both correct, and all six correct per group, exposes that collapse.
    """
    import numpy as np
    part=np.asarray(partition,dtype=np.int64)
    subsets=[rows[int(i)] for i in part]
    target=np.asarray(targets,dtype=np.float64)[part]
    predicted=np.asarray(predictions,dtype=np.float64)[part]
    decoded=np.asarray(classes,dtype=np.int8)[part]
    groups=_complete_training_groups(subsets,target)
    pairs=np.asarray([(indices[j],indices[j+3]) for indices,_ in groups
                      for j in range(3)],dtype=np.int64)
    a,b=pairs[:,0],pairs[:,1]
    expected=np.sign(target).astype(np.int8)
    if not np.array_equal(expected[a],expected[b]):
        raise ValueError('JOINT_PAIRED_EXPECTED_SIGN_MISMATCH')
    both=(decoded[a]==expected[a]) & (decoded[b]==expected[b])
    signed_gap=np.abs(_slog(predicted[a])-_slog(predicted[b]))
    if not np.isfinite(signed_gap).all():
        raise ValueError('JOINT_NONFINITE_PAIRED_GAP')
    completed=sum(all(decoded[i]==expected[i] for i in group)
                  for group,_ in groups)
    return {'paired_cases':int(len(pairs)),'groups':len(groups),
        'class_agreement':float(np.mean(decoded[a]==decoded[b])),
        'both_correct_fraction':float(np.mean(both)),
        'both_correct_cases':int(both.sum()),
        'complete_six_case_groups':int(completed),
        'complete_six_case_fraction':completed/len(groups),
        'mean_abs_signed_log_gap':float(signed_gap.mean()),
        'max_abs_signed_log_gap':float(signed_gap.max())}


def _correct_original_zero_margins(old,y,delta):
    """DEV-only monitor, never used in fit. A prior zero must keep its margin."""
    import numpy as np
    old=np.asarray(old,dtype=np.float64)
    target=np.asarray(y,dtype=np.float64)
    residual=np.asarray(delta,dtype=np.float64)
    m=_original_correct_mask(target,old) & (target==0)
    if not bool(m.any()):
        return {'originally_correct_zero_count':0,'below_floor_count':0,
                'minimum_margin':None,'mean_margin':None,'margin_floor':ZERO_MARGIN_FLOOR}
    logits=_baseline_logits(old).astype(np.float64)+residual[:,1:]
    gap=logits[m,1]-np.maximum(logits[m,0],logits[m,2])
    if not np.isfinite(gap).all():raise ValueError('JOINT_ZERO_MARGIN_NONFINITE')
    return {'originally_correct_zero_count':int(m.sum()),
            'below_floor_count':int(np.sum(gap<ZERO_MARGIN_FLOOR)),
            'minimum_margin':float(gap.min()),'mean_margin':float(gap.mean()),
            'margin_floor':ZERO_MARGIN_FLOOR}


def _balanced_protected_indices(rng,targets,correct,per_class=8):
    import numpy as np
    classes=np.sign(targets)
    batches=[]
    for sign in (-1,0,1):
        available=np.flatnonzero(correct & (classes==sign))
        if len(available):
            batches.append(rng.choice(available,size=per_class,replace=True))
    if not batches:raise ValueError('JOINT_NO_ORIGINALLY_CORRECT_TRAIN_CASES')
    return np.concatenate(batches)


def _retention_details(retention):
    """Show the EXACT class that fails; no aggregate-only reporting."""
    by_class={}
    failures=[]
    threshold=float(retention.get('minimum_retention_fraction',0.95))
    for name,rec in retention['by_class'].items():
        n=int(rec['original_correct']);v=rec['preserved_fraction']
        keep=int(round(n*v)) if v is not None else None
        ok=v is None or v+1e-12>=threshold
        by_class[name]={'original_correct':n,'kept':keep,
                        'lost':n-keep if keep is not None else None,
                        'preserved_fraction':v,'passed':ok}
        if not ok:failures.append('CLASS_RETENTION_'+name.upper())
    kept=retention['original_correct_kept_fraction']
    if kept is None or kept+1e-12<threshold:
        failures.append('OVERALL_CORRECT_RETENTION')
    old_mae=retention['original']['signed_mae']
    new_mae=retention['candidate']['signed_mae']
    if new_mae>old_mae*(1+retention['max_mae_regression_fraction']):
        failures.append('SOURCE_MAE_REGRESSION')
    if retention['status']=='FAIL' and not failures:
        failures.append('UNCLASSIFIED_RETENTION_FAILURE')
    return {'by_class':by_class,'failed_gates':failures,
            'status':retention['status']}


def _predict(net,feature,base,mu,sigma,device,batch=128,*,return_deltas=False):
    import numpy as np
    import torch
    x=_to_input(feature,base,mu,sigma)
    net.eval();outputs=[]
    with torch.no_grad():
        for i in range(0,len(x),batch):
            v=net(torch.from_numpy(x[i:i+batch]).to(device)).cpu().numpy()
            outputs.append(v)
    delta=np.concatenate(outputs)
    result=_decode_correction(delta,base)
    return (*result,delta) if return_deltas else result


def _dataset_metrics(data,dev_original,feature,mu,sigma,net,device):
    import numpy as np
    src_meta,src_y,src_old,src_vec=dev_original
    sc,sv,sd=_predict(net,src_vec,src_old,mu,sigma,device,return_deltas=True)
    preservation=lab._retention(src_y,sv,sc,src_old,src_meta)
    preservation['correct_zero_margin_monitor']=_correct_original_zero_margins(src_old,src_y,sd)
    syn_meta,syn_y,syn_old,syn_vec,syn_part=data
    cls,pred=_predict(net,syn_vec,syn_old[syn_part],mu,sigma,device)
    all_pred=np.asarray(syn_old,dtype=np.float64).copy()
    all_cls=lab._classify_continuous(all_pred)
    all_pred[syn_part]=pred
    all_cls[syn_part]=cls
    syn=lab._group_scores(syn_meta,syn_y,all_pred,all_cls,syn_part)
    syn['cross_format']=_paired_agreement(syn_meta,syn_y,all_pred,all_cls,syn_part)
    return preservation,syn


def _cross_format_gate(metrics,threshold=0.95):
    """Additional gate: invariance must also be CORRECT in both formats.

    Class agreement alone can be trivially perfect when a model predicts one
    constant sign. Require correct matched pairs and complete correct six-case
    groups; do not relax any existing acceptance condition.
    """
    return bool(metrics['both_correct_fraction']>=threshold and
                metrics['complete_six_case_fraction']>=threshold)


def _rank(retention,synthetic):
    m=synthetic['all']
    pair=synthetic.get('cross_format') or {}
    # Dev-only ranking: retention is a hard priority. Among preserved models,
    # select mathematical correctness in BOTH wordings before aggregate sign.
    # Mere class agreement is deliberately excluded: all-positive collapse
    # would have perfect agreement but no correctly completed six-case groups.
    return (int(retention['status']=='PASS'),
            round(pair.get('both_correct_fraction',0),10),
            round(pair.get('complete_six_case_fraction',0),10),
            round(m['balanced_sign_accuracy'],10),
            round(m['triple_accuracy'] or 0,10),
            round(m['zero_recall'] or 0,10),
            -round(m['signed_mae'],10))


def _history_entry(step,loss,retention,dev,rank,loss_parts=None):
    details=_retention_details(retention)
    m=dev['all']
    return {'step':step,'loss':loss,'loss_parts':loss_parts,
            'source_retention':retention['status'],
            'source_correct_kept':retention['original_correct_kept_fraction'],
            'source_mae':retention['candidate']['signed_mae'],
            'source_retention_by_class':details['by_class'],
            'source_retention_failed_gates':details['failed_gates'],
            'synthetic_dev_balanced_sign':m['balanced_sign_accuracy'],
            'synthetic_dev_zero_recall':m['zero_recall'],
            'synthetic_dev_triples':m['triple_accuracy'],
            'synthetic_dev_near_nonzero_sign':m['near_nonzero_strict_sign'],
            'synthetic_dev_cross_format':dev.get('cross_format'),
            'source_correct_zero_margin':retention.get('correct_zero_margin_monitor'),
            'synthetic_dev_family':{k:{'sign':v['balanced_sign_accuracy'],
                                      'triples':v['triple_accuracy']}
                                    for k,v in dev['by_family'].items()},
            'rank':list(rank)}


def _fit_one(torch,*,feature,source,synthetic,original_dev,synthetic_dev,
             device,steps,lr,source_weight,anchor_weight,seed,eval_every,
             consistency_weight=2.0,zero_margin_weight=2.0,decoder_hidden=128):
    import numpy as np
    torch.manual_seed(seed);random.seed(seed)
    source_x,source_old,source_y=source
    if len(synthetic)!=4:
        raise ValueError('JOINT_SYNTHETIC_TRAIN_NEEDS_COMPLETE_GROUP_METADATA')
    synth_x,synth_old,synth_y,synth_rows=synthetic
    groups=_complete_training_groups(synth_rows,synth_y)
    mu,sigma=_normalization(source_x,synth_x)
    sx=_to_input(source_x,source_old,mu,sigma)
    tx=_to_input(synth_x,synth_old,mu,sigma)
    sign_anchors=_original_correct_mask(source_y,source_old)
    precision_anchors=_accurate_numeric_mask(source_y,source_old)
    assert not np.any(precision_anchors&~sign_anchors)
    a=_tensor_set((sx,source_old,source_y,sign_anchors,precision_anchors),device,torch)
    b=_tensor_set((tx,synth_old,synth_y,
                   np.zeros(len(synth_y),dtype=bool),
                   np.zeros(len(synth_y),dtype=bool)),device,torch)
    if not isinstance(decoder_hidden,int) or decoder_hidden<8 or decoder_hidden>1024:
        raise ValueError('JOINT_INVALID_DECODER_HIDDEN')
    net=_build_model(torch,width=sx.shape[1],hidden=decoder_hidden).to(device)
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    generator=np.random.default_rng(seed)
    if len(groups)<2:raise ValueError('JOINT_TOO_FEW_INDEPENDENT_TRAIN_GROUPS')
    by_class={str(c):int(np.sum(sign_anchors & (np.sign(source_y)==c)))
              for c in (-1,0,1)}
    best=None;history=[]
    # Start with the unchanged frozen head; never choose a damaging correction
    # when no correction satisfies original preservation.
    initial_retention,initial_dev=_dataset_metrics(synthetic_dev,original_dev,
                                                  feature,mu,sigma,net,device)
    initial_rank=_rank(initial_retention,initial_dev)
    best={'rank':initial_rank,'step':0,'dev':initial_dev,'retention':initial_retention,
          'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}}
    history.append(_history_entry(0,None,initial_retention,initial_dev,initial_rank))
    for step in range(1,steps+1):
        net.train()
        si=generator.integers(0,len(source_y),size=min(32,len(source_y)))
        pi=_balanced_protected_indices(generator,source_y,sign_anchors,per_class=8)
        # Four complete groups: each contains BOTH surfaces and intact
        # negative/zero/positive triples. Never sample unrelated signs.
        chosen=generator.choice(len(groups),size=4,replace=True)
        ti=np.concatenate([groups[int(g)][0] for g in chosen])
        local_triples=[(6*k+j*3,6*k+j*3+1,6*k+j*3+2)
                       for k in range(len(chosen)) for j in range(2)]
        matched_pairs=_matched_surface_pairs(groups,chosen)
        optimizer.zero_grad(set_to_none=True)
        sloss,sinfo=_training_loss(torch,net,*(v[si] for v in a[:4]),
                                   weight=source_weight,source=True,
                                   numeric_anchor=a[4][si],
                                   zero_margin_weight=zero_margin_weight)
        tloss,tinfo=_training_loss(torch,net,*(v[ti] for v in b[:4]),
                                   weight=1.,source=False)
        triple=_triple_loss(torch,net,b[0][ti],b[1][ti],local_triples)
        consistency,cinfo=_cross_format_loss(torch,net,b[0][ti],b[1][ti],matched_pairs)
        protected=net(a[0][pi])
        preserve,pinfo=_preservation_loss(torch,protected,a[1][pi],a[2][pi],
                                          a[3][pi],a[4][pi],
                                          zero_margin_weight=zero_margin_weight)
        # Separate objectives. anchor_weight directly multiplies ALL acquired
        # sign classes; precision anchors receive an additional narrower term.
        loss=sloss+tloss+2.0*triple+consistency_weight*consistency+anchor_weight*preserve
        if not torch.isfinite(loss):raise ValueError('JOINT_NONFINITE_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.0)
        optimizer.step()
        if step%eval_every==0 or step==steps:
            retention,dev=_dataset_metrics(synthetic_dev,original_dev,
                                          feature,mu,sigma,net,device)
            rank=_rank(retention,dev)
            losses={'source':sinfo,'synthetic':tinfo,
                    'triple':float(triple.detach()),'cross_format':cinfo,
                    'balanced_protect':pinfo}
            history.append(_history_entry(step,float(loss.detach()),
                                           retention,dev,rank,losses))
            if rank>best['rank']:
                best={'rank':rank,'step':step,'dev':dev,'retention':retention,
                      'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}}
    net.load_state_dict(best['state']);net.eval()
    counts={'anchored_train_count':int(sign_anchors.sum()),
            'original_sign_correct_train_by_class':by_class,
            'numerically_accurate_train_count':int(precision_anchors.sum()),
            'original_train_count':len(source_y),
            'synthetic_train_count':len(synth_y),
            'complete_train_groups':len(groups),
            'triples_per_update':8,
            'protected_examples_per_class_per_update':8,
            'cross_format_pairs_per_update':len(matched_pairs),
            'cross_format_weight':consistency_weight,
            'original_zero_margin_weight':zero_margin_weight,
            'decoder_hidden':decoder_hidden}
    return net,mu,sigma,best,history,counts


def _prepare(args):
    import numpy as np
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as spine
    data=_load_verified(args)
    a=data['probe_info'];d=data['retention_info'];
    rows=a['dataset'];part=data['partitions']
    y=lab._targets(rows)
    old=np.asarray(data['probe_arrays']['production_head_predictions'],dtype=np.float64)
    src_y=np.asarray([float(r['answer']) for r in data['original_train']],dtype=np.float64)
    source_dev_y=lab._targets(d['dataset'])
    source_dev_old=np.asarray(data['original_dev_predictions'],dtype=np.float64)
    prepared={}
    for feature in FEATURES:
        k='lora__'+feature
        synthetic=np.asarray(data['probe_arrays'][k],dtype=np.float32)
        source_train=_profile(data['original_train_features'],feature)
        source_dev=np.asarray(data['retention_arrays'][k],dtype=np.float32)
        prepared[feature]={
            'train_source':(source_train,data['original_train_predictions'],src_y),
            'train_synthetic':(synthetic[part['train']],old[part['train']],y[part['train']],
                               [rows[int(i)] for i in part['train']]),
            'dev_source':(d['dataset'],source_dev_y,source_dev_old,source_dev),
            'dev_synthetic':(rows,y,old,synthetic[part['dev']],part['dev']),
            'test_synthetic':(rows,y,old,synthetic[part['test']],part['test'])}
    return data,prepared


def _verify_after(data):
    from tools import nanojev_two_game_spine_smoke as spine
    p=data['probe_info']['lineage']
    if spine.file_sha(Path(p['checkpoint_path']))!=p['checkpoint_sha256'] or \
       spine.adapter_identity(Path(p['adapter_dir']))!=p['adapter_sha256'] or \
       spine.file_sha(Path(data['train_cache']['path']))!=data['train_cache']['sha256'] or \
       spine.file_sha(Path(data['retention_info']['source_feature_cache']['path']))!=\
           data['retention_info']['source_feature_cache']['sha256']:
        raise ValueError('JOINT_ORIGINAL_SOURCE_MUTATED_DURING_EXPERIMENT')


def train(args):
    import numpy as np
    import torch
    if (args.steps<1 or args.eval_every<1 or args.lr<=0 or args.source_weight<=0
            or args.anchor_weight<1 or getattr(args,'cross_format_weight',2.0)<=0
            or getattr(args,'zero_margin_weight',2.0)<0 or not all(map(math.isfinite,
                (args.lr,args.source_weight,args.anchor_weight,
                 getattr(args,'cross_format_weight',2.0),
                 getattr(args,'zero_margin_weight',2.0))))):
        raise ValueError('JOINT_INVALID_OPTIMIZER_CONFIGURATION')
    if args.steps>20000:raise ValueError('JOINT_BOUNDED_STEPS_REQUIRED')
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('JOINT_CUDA_UNAVAILABLE')
    protected=[Path(x) for x in (args.run,args.expanded_run,args.input,args.source_features) if x]
    out=lab._output(args.output,protected)
    data,features=_prepare(args)
    print(f'JOINT_HEAD_REPAIR source_train={len(data["original_train"])}; source_dev={len(data["retention_info"]["dataset"])}; synthetic={len(data["probe_info"]["dataset"])}; no Qwen or LoRA training',flush=True)
    records=[];winner=None
    # Exactly matched ablation of cross-format invariance: identical training
    # data, zero protection, optimizer, seeds, feature and head weights.
    # Only the cross-format coefficient changes between pairs of candidates.
    cross_weight=float(getattr(args,'cross_format_weight',2.0))
    zero_weight=float(getattr(args,'zero_margin_weight',2.0))
    for feature in FEATURES:
        for source_weight,anchor_weight in ((args.source_weight,args.anchor_weight),
                                            (2*args.source_weight,(10./3.)*args.anchor_weight)):
          for consistency_weight in (0.0,cross_weight):
            spec=features[feature]
            id=f'{feature}.source{source_weight:g}.anchor{anchor_weight:g}.cross{consistency_weight:g}'
            net,mu,sigma,best,history,counts=_fit_one(torch,feature=feature,
                source=spec['train_source'],synthetic=spec['train_synthetic'],
                original_dev=spec['dev_source'],synthetic_dev=spec['dev_synthetic'],
                device=args.device,steps=args.steps,lr=args.lr,
                source_weight=source_weight,anchor_weight=anchor_weight,
                seed=args.seed,eval_every=args.eval_every,
                consistency_weight=consistency_weight,zero_margin_weight=zero_weight)
            # Synthetic TEST is evaluated ONCE, after all checkpoint selection.
            m=spec['test_synthetic']
            cls,pred=_predict(net,m[3],m[2][m[4]],mu,sigma,args.device)
            all_pred=np.asarray(m[2],dtype=np.float64).copy()
            all_cls=lab._classify_continuous(all_pred)
            all_pred[m[4]]=pred;all_cls[m[4]]=cls
            overall=lab._group_scores(m[0],m[1],all_pred,all_cls,m[4])
            baseline_old=m[2][m[4]]
            baseline=lab._group_scores(m[0],m[1],m[2],
                                      lab._classify_continuous(m[2]),m[4])
            overall['cross_format']=_paired_agreement(m[0],m[1],all_pred,all_cls,m[4])
            baseline['cross_format']=_paired_agreement(m[0],m[1],m[2],
                                      lab._classify_continuous(m[2]),m[4])
            gates=lab._gates(overall,best['retention'],baseline['all'])
            gates['correct_cross_format_invariance']=_cross_format_gate(overall['cross_format'])
            gates['all_bounded_gates_pass']=bool(
                gates['all_bounded_gates_pass'] and gates['correct_cross_format_invariance'])
            record={'id':id,'feature':feature,'source_weight':source_weight,
                'cross_format_weight':consistency_weight,
                'zero_margin_weight':zero_weight,
                'anchor_weight':anchor_weight,'dev_step':best['step'],
                'dev':best['dev'],'original_dev_retention':best['retention'],
                'test':overall,'production_test':baseline,'gates':gates,
                'train_counts':counts,'history':history,
                'candidate_never_promoted':True,
                'selected_retention_failures':_retention_details(best['retention'])['failed_gates']}
            records.append(record)
            print(probe.canonical({'candidate':id,
                'original_dev_retention':best['retention']['status'],
                'original_correct_kept':best['retention']['original_correct_kept_fraction'],
                'dev_sign':best['dev']['all']['balanced_sign_accuracy'],
                'test_sign':overall['all']['balanced_sign_accuracy'],
                'test_triples':overall['all']['triple_accuracy'],
                'gates':gates['all_bounded_gates_pass'],
                'retention_failed_gates':record['selected_retention_failures'],
                'retention_by_class':{name:v['preserved_fraction'] for name,v in
                                      best['retention']['by_class'].items()},
                'selected_step':best['step'],
                'dev_both_surface_correct':best['dev']['cross_format']['both_correct_fraction'],
                'test_both_surface_correct':overall['cross_format']['both_correct_fraction'],
                'cross_format_weight':consistency_weight}),flush=True)
            if winner is None or best['rank']>winner['rank']:
                winner={'rank':best['rank'],'record':record,'weights':best['state'],
                    'mu':mu.copy(),'sigma':sigma.copy()}
            del net
            if torch.cuda.is_available():torch.cuda.empty_cache()
    _verify_after(data)
    ablation=[]
    # Match each control to its treatment by the ONLY causal intervention:
    # cross-format weight 0 versus the user-specified nonzero weight.
    for feature in FEATURES:
        for source_weight,anchor_weight in ((args.source_weight,args.anchor_weight),
                                            (2*args.source_weight,(10./3.)*args.anchor_weight)):
            cells=[r for r in records if r['feature']==feature and
                   r['source_weight']==source_weight and r['anchor_weight']==anchor_weight]
            if len(cells)!=2 or {r['cross_format_weight'] for r in cells}!={0.,cross_weight}:
                raise ValueError('JOINT_ABLATION_ARMS_INCOMPLETE')
            control=next(r for r in cells if r['cross_format_weight']==0.)
            treatment=next(r for r in cells if r['cross_format_weight']!=0.)
            def difference(path):
                def get(v):
                    for key in path:v=v[key]
                    return float(v)
                return get(treatment)-get(control)
            ablation.append({'feature':feature,'source_weight':source_weight,
                'anchor_weight':anchor_weight,'control_id':control['id'],
                'treatment_id':treatment['id'],
                'dev_both_correct_delta':difference(('dev','cross_format','both_correct_fraction')),
                'dev_six_case_correct_delta':difference(('dev','cross_format','complete_six_case_fraction')),
                'dev_balanced_sign_delta':difference(('dev','all','balanced_sign_accuracy')),
                'test_both_correct_delta':difference(('test','cross_format','both_correct_fraction')),
                'test_six_case_correct_delta':difference(('test','cross_format','complete_six_case_fraction')),
                'test_balanced_sign_delta':difference(('test','all','balanced_sign_accuracy')),
                'original_correct_retained_delta':difference(('original_dev_retention','original_correct_kept_fraction')),
                'control_retention':control['original_dev_retention']['status'],
                'treatment_retention':treatment['original_dev_retention']['status'],
                'test_not_virgin':True})
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'model_lineage':data['probe_info']['lineage'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'original_dev_sha256':data['retention_info']['dataset_sha256'],
        'original_train_cache':data['train_cache'],
        'selection_policy':'retention hard priority (all originally correct classes), then synthetic DEV correctly matched cross-surface pairs/six-case groups, sign/triples/zero/mae',
        'matched_consistency_ablation':True,
        'matched_ablation_comparisons':ablation,
        'cross_format_acceptance_gate':'both-matched-correct>=0.95 AND complete-six-case-correct>=0.95',
        'cross_format_weight':cross_weight,
        'original_zero_margin_weight':zero_weight,
        'training_objective':'all-correct class-balanced preservation + original TRAIN zero margins + separate accurate-numeric anchors + six-case signed triples + equivalent-surface classification and signed-value consistency',
        'synthetic_test_policy':'PREVIOUSLY_EXAMINED_NOT_A_VIRGIN_TEST',
        'source_original_dev_fitted':False,'source_original_train_fitted':True,
        'lora_frozen':True,'production_head_frozen':True,'production_promotion':False,
        'candidate_count':len(records),'selected_candidate':winner['record']['id'],
        'selected_bounded_gates_pass':winner['record']['gates']['all_bounded_gates_pass'],
        'independent_acceptance_required':True,'candidates':records}
    out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    with (out/'candidate_scores.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['id','feature','cross_format_weight','zero_margin_weight',
            'dev_step','original_retention','original_correct_kept',
            'original_mae','source_zero_margin_min','source_correct_zeros_below_margin',
            'dev_sign','dev_triples','dev_cross_format_agreement','dev_cross_format_both_correct',
            'test_sign','test_triples','test_zero','test_cross_format_agreement',
            'test_cross_format_both_correct','test_cross_format_complete_groups',
            'test_mae','all_bounded_gates_pass','selected_retention_failures'])
        writer.writeheader()
        for r in records:
            writer.writerow({'id':r['id'],'feature':r['feature'],
                'cross_format_weight':r['cross_format_weight'],
                'zero_margin_weight':r['zero_margin_weight'],
                'dev_step':r['dev_step'],
                'original_retention':r['original_dev_retention']['status'],
                'original_correct_kept':r['original_dev_retention']['original_correct_kept_fraction'],
                'original_mae':r['original_dev_retention']['candidate']['signed_mae'],
                'source_zero_margin_min':r['original_dev_retention']['correct_zero_margin_monitor']['minimum_margin'],
                'source_correct_zeros_below_margin':r['original_dev_retention']['correct_zero_margin_monitor']['below_floor_count'],
                'dev_sign':r['dev']['all']['balanced_sign_accuracy'],
                'dev_triples':r['dev']['all']['triple_accuracy'],
                'dev_cross_format_agreement':r['dev']['cross_format']['class_agreement'],
                'dev_cross_format_both_correct':r['dev']['cross_format']['both_correct_fraction'],
                'test_sign':r['test']['all']['balanced_sign_accuracy'],
                'test_triples':r['test']['all']['triple_accuracy'],
                'test_zero':r['test']['all']['zero_within_0_05'],
                'test_cross_format_agreement':r['test']['cross_format']['class_agreement'],
                'test_cross_format_both_correct':r['test']['cross_format']['both_correct_fraction'],
                'test_cross_format_complete_groups':r['test']['cross_format']['complete_six_case_groups'],
                'test_mae':r['test']['all']['signed_mae'],
                'all_bounded_gates_pass':r['gates']['all_bounded_gates_pass'],
                'selected_retention_failures':';'.join(r['selected_retention_failures'])})
    with (out/'ablation_comparisons.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['feature','source_weight','anchor_weight','control_id','treatment_id',
                'dev_both_correct_delta','dev_six_case_correct_delta','dev_balanced_sign_delta',
                'test_both_correct_delta','test_six_case_correct_delta','test_balanced_sign_delta',
                'original_correct_retained_delta','control_retention','treatment_retention','test_not_virgin']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        writer.writerows(ablation)
    with (out/'checkpoint_history.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['candidate','step','retention','failed_gates','retained_negative',
            'retained_zero','retained_positive','lost_negative','lost_zero',
            'lost_positive','retained_overall','source_mae','dev_balanced_sign',
            'dev_zero_recall','dev_triples','dev_near_nonzero_sign',
            'dev_cross_format_agreement','dev_cross_format_both_correct',
            'dev_cross_format_six_case_correct','source_zero_min_margin',
            'source_correct_zeros_below_margin']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for record in records:
            for event in record['history']:
                by=event['source_retention_by_class']
                writer.writerow({'candidate':record['id'],'step':event['step'],
                    'retention':event['source_retention'],
                    'failed_gates':';'.join(event['source_retention_failed_gates']),
                    **{'retained_'+c:by[c]['preserved_fraction'] for c in ('negative','zero','positive')},
                    **{'lost_'+c:by[c]['lost'] for c in ('negative','zero','positive')},
                    'retained_overall':event['source_correct_kept'],
                    'source_mae':event['source_mae'],
                    'dev_balanced_sign':event['synthetic_dev_balanced_sign'],
                    'dev_zero_recall':event['synthetic_dev_zero_recall'],
                    'dev_triples':event['synthetic_dev_triples'],
                    'dev_near_nonzero_sign':event['synthetic_dev_near_nonzero_sign'],
                    'dev_cross_format_agreement':(event['synthetic_dev_cross_format'] or {}).get('class_agreement'),
                    'dev_cross_format_both_correct':(event['synthetic_dev_cross_format'] or {}).get('both_correct_fraction'),
                    'dev_cross_format_six_case_correct':(event['synthetic_dev_cross_format'] or {}).get('complete_six_case_fraction'),
                    'source_zero_min_margin':(event['source_correct_zero_margin'] or {}).get('minimum_margin'),
                    'source_correct_zeros_below_margin':(event['source_correct_zero_margin'] or {}).get('below_floor_count')})
    # Saved candidate is not a standalone replacement model; it is an additive
    # residual tied to exact checkpoint and adapter identities, and is only
    # for future diagnostic reuse. The existing head weights never move.
    torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
                'lineage':data['probe_info']['lineage'],
                'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
                'original_train_cache_sha256':data['train_cache']['sha256'],
                'feature':winner['record']['feature'],
                'model':winner['weights'],
                'mean':torch.from_numpy(winner['mu']),
                'std':torch.from_numpy(winner['sigma']),
                'candidate_id':winner['record']['id'],
                'cross_format_weight':winner['record']['cross_format_weight'],
                'original_zero_margin_weight':zero_weight,
                'promotion_authorized':False},out/'disposable_candidate.pt')
    print(probe.canonical({'status':report['status'],'selected':report['selected_candidate'],
        'bounded_gates_pass':report['selected_bounded_gates_pass'],
        'source_dev_used_for_fitting':False,'production_weights_modified':False,
        'report':str(out/'report.json'),'candidate_checkpoint':str(out/'disposable_candidate.pt')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('oracle','train'),default='oracle')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=300)
    p.add_argument('--eval-every',type=int,default=30)
    p.add_argument('--lr',type=float,default=0.001)
    p.add_argument('--source-weight',type=float,default=1.)
    p.add_argument('--anchor-weight',type=float,default=3.)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--cross-format-weight',type=float,default=2.0,
                   help='Paired six-case consistency weight; matched zero-weight control runs automatically')
    p.add_argument('--zero-margin-weight',type=float,default=2.0,
                   help='Extra protection of original TRAIN correct-zero class margins')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.mode=='oracle':
        deck=probe.make_deck()
        print(probe.canonical({'status':'ORACLE_ONLY','synthetic':probe.validate_deck(deck),
            'candidate_configs':8,'matched_cross_format_ablation':True,
            'source_split':'original TRAIN cache',
            'retention_split':'original DEV (never fitted)','no_model_training':True}))
    else:train(args)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
