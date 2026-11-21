"""G2-0094 weight-edit contenders: inverse Jacobian, constrained rank-1, and
local RBF surface compiled into the actual G2 binding weights.

All targets derive from annotated discovery data only. The surfaces operate on
real, frozen-G1, G2 pair activations; synthetic interpolation points are
regularizers, not claimed to be labeled natural-language observations.
"""
from __future__ import annotations

import math
import torch

TECHNIQUES = ('reptile', 'jacobian_inverse', 'binding_rank_one',
              'local_ridge', 'rbf_surface')


def capture(head, item, forward, decode, device):
    record = {}
    def store(_module, args, _output):
        record['x'] = args[0].detach()[:len(item['objects']), :len(item['objects'])].float().cpu()
    handle = head.binding.register_forward_hook(store)
    try:
        with torch.no_grad():
            logits = forward(head, item, device).detach().float().cpu()
            predicted, _ = decode(item, logits, None)  # caller wrapper closes over decoder spec
    finally:
        handle.remove()
    if 'x' not in record:
        raise ValueError('G2_0094_BINDING_FEATURE_CAPTURE_MISSING')
    return record['x'], logits, predicted


def _semantic_pair_key(item, i, j):
    """Map predicted G1 spans to stable IDs in authored training annotations.

    Labels and IDs are used only by the training editor, not as G2 inputs.
    """
    lookup={(n['kind'],tuple(n['span'])):str(n['id'])
            for n in item['row'].get('graph',{}).get('nodes',[]) if 'span' in n}
    a=item['objects'][i];b=item['objects'][j]
    left=lookup.get((a[2],(a[0],a[1])))
    right=lookup.get((b[2],(b[0],b[1])))
    if left is None or right is None:
        return None
    return (item['row'].get('base_id',item['row']['case_id']),left,right)


def _observations(head, support, guards, anchors, forward, decode, device,
                  max_wrong=5, max_right=10):
    """Choose errors on support; reserve right decisions in every replay guard.

    Build pair features X, desired changes to LOGITS and comparable margin
    constraints. No oracle spans or confirmation gold are input to the head.
    """
    items = {it['row']['case_id']: it for it in support + guards}
    support_ids = {it['row']['case_id'] for it in support}
    observations, misses = [], []
    for cid, item in items.items():
        x, logits, matrix = capture(head, item, forward, decode, device)
        target = item['targets'].cpu()
        n = len(target)
        valid = ~torch.eye(n, dtype=torch.bool)
        wrong = ((matrix != target) & valid).nonzero(as_tuple=False).tolist()
        # Include missed positive bindings first, then false positives and wrong bindings.
        wrong.sort(key=lambda ij: (int(target[ij[0],ij[1]]) == 0,
                                    ij[0],ij[1]))
        right = anchors[cid]['preserve'] & valid
        pos_right = ((target > 0) & right).nonzero(as_tuple=False).tolist()
        neg_right = ((target == 0) & right).nonzero(as_tuple=False).tolist()
        neg_right.sort(key=lambda ij: -float((logits[ij[0],ij[1],1:] -
                                                logits[ij[0],ij[1],0]).max()))
        selected = [(i,j,True) for i,j in wrong[:max_wrong]] if cid in support_ids else []
        selected += [(i,j,False) for i,j in
                     (pos_right[:max_right//2]+neg_right[:max_right//2])]
        for i,j,is_wrong in selected:
            gold = int(target[i,j])
            # The structured decoder can disagree with argmax(logits): take
            # the actual WRONG graph decision as rival. Without this, a false
            # positive selected by joint decoding might receive a zero edit.
            decoded_rival=int(matrix[i,j])
            if is_wrong:
                competitor=decoded_rival
            else:
                competitor=int(torch.cat((logits[i,j,:gold],
                                          logits[i,j,gold+1:])).argmax())
                if competitor>=gold:
                    competitor+=1
            current_margin = float(logits[i,j,gold]-logits[i,j,competitor])
            required = max(1.0, 1.0-current_margin) if is_wrong else 0.
            delta = torch.zeros_like(logits[i,j])
            if is_wrong:
                delta[gold] = required/2.
                delta[competitor] = -required/2.
            obs = {'x': x[i,j].clone(), 'delta':delta, 'gold':gold,
                   'competitor':competitor,'required':required,
                   'weight':1. if is_wrong else 4.,
                   'wrong':bool(is_wrong), 'item':item, 'ij':(i,j),
                   'margin':current_margin,'case_id':cid,
                   'semantic_pair':_semantic_pair_key(item,i,j),
                   'relation_label':gold,'fuzz_mode':item['row'].get('mode','base')}
            observations.append(obs)
            if is_wrong:
                misses.append(obs)
    return observations, misses


def _salient(observations, head, k=24):
    """Backpropagate the output margin to pair activation X analytically.

    For the final linear binding head the input Jacobian is W_gold - W_rival.
    Select X coordinates that affect wrong margins, never based on held-out
    correction labels or confirmation performance.
    """
    wrong = [v for v in observations if v['wrong']]
    dim = head.binding.in_features
    if not wrong:
        return torch.arange(min(k,dim),dtype=torch.long)
    W = head.binding.weight.detach().float().cpu()
    gradient = torch.stack([W[v['gold']]-W[v['competitor']] for v in wrong])
    # Weight by the needed margin movement, not by absolute activation size.
    scores = (gradient.abs() * torch.tensor([v['required'] for v in wrong])[:,None]).mean(0)
    return torch.argsort(scores,descending=True,stable=True)[:min(k,dim)]


def _basis(observations, selected):
    # Include a column of ones to permit bias editing; center around the actual
    # observed neighbors rather than projecting them through an arbitrary PCA.
    X = torch.stack([v['x'][selected] for v in observations]).double()
    scale = X.std(0,unbiased=False).clamp(min=0.05)
    mu = X.mean(0)
    normalized = (X-mu)/scale
    return X, normalized, mu, scale


def _ridge(a, y, weights, damping):
    """Minimal-norm dual damped least-squares inverse for multioutput values."""
    sqrt_w = weights.sqrt().unsqueeze(-1)
    aw, yw = a*sqrt_w, y*sqrt_w
    gram = aw@aw.T
    reg = float(damping)*torch.eye(len(aw),device=gram.device,dtype=gram.dtype)
    update = torch.linalg.solve(gram+reg, yw)
    return update.T@aw


def _apply_binding_update(head, selected, updated):
    # Updated is [num_classes, salient_dims + bias] in original X coordinates.
    with torch.no_grad():
        head.binding.weight[:,selected.to(head.binding.weight.device)] += updated[:,:-1].to(
            device=head.binding.weight.device,dtype=head.binding.weight.dtype)
        head.binding.bias += updated[:,-1].to(
            device=head.binding.bias.device,dtype=head.binding.bias.dtype)


def local_ridge_edit(head, observations, selected, damping=4.0):
    X = torch.stack([v['x'][selected] for v in observations]).double()
    A = torch.cat((X, torch.ones(len(X),1,dtype=torch.float64)),dim=-1)
    D = torch.stack([v['delta'] for v in observations]).double()
    weights = torch.tensor([v['weight'] for v in observations],dtype=torch.float64)
    update = _ridge(A,D,weights,damping)
    _apply_binding_update(head,selected,update)
    residual = ((A@update.T-D).square()*weights[:,None]).mean().sqrt()
    return {'fit_rmse':float(residual),'observations':len(observations),
            'edit_weight_norm':float(update.norm()),'salient_dimensions':selected.tolist()}


def rank_one_edit(head, observations, selected, damping=2.0):
    wrong = [v for v in observations if v['wrong']]
    if not wrong:
        return {'skipped':'NO_WRONG_OBSERVATIONS'}
    # Constrained rank-one edit: choose highest-margin deficit; make the key
    # orthogonal to nearby correct-answer keys as much as ridge geometry permits.
    target = max(wrong,key=lambda r:r['required'])
    x = torch.cat((target['x'][selected].double(),torch.ones(1,dtype=torch.float64)))
    anchors = [v for v in observations if not v['wrong']]
    if anchors:
        A = torch.stack([torch.cat((v['x'][selected].double(),
                                    torch.ones(1,dtype=torch.float64))) for v in anchors])
        co = torch.linalg.solve(A@A.T+damping*torch.eye(len(A),dtype=A.dtype), A@x)
        direction = x - A.T@co
    else:
        direction = x
    denom = float(x@direction)
    if denom < 1e-8:
        return {'skipped':'NO_SAFE_RANK_ONE_DIRECTION'}
    update = torch.outer(target['delta'].double(),direction)/denom
    _apply_binding_update(head,selected,update)
    anchor_shift=float((A@update.T).norm()) if anchors else 0.
    return {'observations':len(observations),'anchor_logit_shift':anchor_shift,
            'edit_weight_norm':float(update.norm()),'salient_dimensions':selected.tolist()}


def rbf_surface_edit(head, observations, selected, damping=1.5, smooth=.03):
    """Fit a smooth correction surface in salient X coordinates; compile to W.

    The RBF interpolant is NOT the final predictor. It supplies target deltas at
    real observed and synthetic between-neighbor Xs, which are projected back
    through a regularized linear weight edit. Evaluate the actual graph head.
    """
    X, Z, mu, scale = _basis(observations,selected)
    D = torch.stack([v['delta'] for v in observations]).double()
    importance = torch.tensor([v['weight'] for v in observations],dtype=torch.float64)
    distance = torch.cdist(Z,Z,p=2).square()
    positive = distance[(distance>1e-12)]
    radius = max(1., float(positive.median())) if len(positive) else 1.
    kernel = (-distance/(2*radius)).exp()
    # Correct-point constraints have higher weight in the surface fit.
    regularizer = torch.diag(smooth/importance)
    alpha = torch.linalg.solve(kernel+regularizer,D)
    surface_fit = kernel@alpha
    fit_rmse = ((surface_fit-D).square()*importance[:,None]).mean().sqrt()
    # Only interpolate actual fuzz variants with the SAME author-identity pair
    # and gold relationship. Do not invent semantic equivalence between unrelated
    # graph objects; action-removal discontinuities are excluded from interpolation.
    midpoints=[]
    aligned_pairs=0
    for i,v in enumerate(observations):
        if v['semantic_pair'] is None or v['fuzz_mode']=='action_removal':
            continue
        eligible=[j for j,u in enumerate(observations) if j!=i
                  and u['semantic_pair']==v['semantic_pair']
                  and u['relation_label']==v['relation_label']
                  and u['fuzz_mode']!='action_removal']
        if not eligible:
            continue
        j=min(eligible,key=lambda j:float(distance[i,j]))
        if j<i:  # use each unordered fuzz pair once
            continue
        aligned_pairs+=1
        for t in (0.25,0.5,0.75):
            midpoints.append((1-t)*Z[i]+t*Z[j])
    full_Z = torch.cat((Z,torch.stack(midpoints)),dim=0) if midpoints else Z
    r2 = torch.cdist(full_Z,Z).square()
    smooth_targets = (-r2/(2*radius)).exp()@alpha
    # Anchor correct labels get an explicit zero target instead of relying on
    # approximate RBF interpolation at the original observations.
    smooth_targets[:len(D)] = D
    real_X = full_Z*scale+mu
    basis = torch.cat((real_X,torch.ones(len(real_X),1,dtype=torch.float64)),dim=1)
    sample_weight = torch.cat((importance,torch.full((len(full_Z)-len(Z),),0.25,
                                 dtype=torch.float64)))
    update = _ridge(basis,smooth_targets,sample_weight,damping)
    _apply_binding_update(head,selected,update)
    compiled_rmse = ((basis@update.T-smooth_targets).square()*sample_weight[:,None]).mean().sqrt()
    return {'surface_fit_rmse':float(fit_rmse),'compiled_fit_rmse':float(compiled_rmse),
            'surface_radius_sq':radius,'observations':len(Z),
            'unlabeled_interpolants':len(full_Z)-len(Z),
            'aligned_fuzz_pairs':aligned_pairs,
            'edit_weight_norm':float(update.norm()), 'salient_dimensions':selected.tolist()}


def jacobian_inverse_edit(head, observations, selected, forward, device, damping=20.0,
                          norm_cap=1.0, max_points=24):
    """Linearize corrected output margins around current actual G2 weights.

    J = d(margin)/d(theta). The damped pseudoinverse solves J delta ~= desired
    margin changes while anchoring correct margins at zero. Restrict edits to
    G2 parameters physically downstream of salient pair-output coordinates.
    """
    active = [(n,p) for n,p in head.named_parameters() if p.requires_grad]
    if not active:
        raise ValueError('G2_0094_NO_TRAINABLE_G2_WEIGHTS')
    params = [p for n,p in active]
    size = sum(p.numel() for p in params)
    # Keep individual function constraints from several fuzz variants, plus
    # preserved correct edges. Avoid dozens of backward passes per episode.
    wrong = sorted((o for o in observations if o['wrong']),
                   key=lambda x:-x['required'])[:max_points//2]
    correct = [o for o in observations if not o['wrong']][:max_points-len(wrong)]
    chosen = wrong+correct
    by_case = {}
    for obs in chosen:
        by_case.setdefault(obs['case_id'],[]).append(obs)
    rows, goals, weights = [], [], []
    salient=set(selected.tolist())
    for group in by_case.values():
        item=group[0]['item']
        logits = forward(head,item,device)
        for ix, obs in enumerate(group):
            i,j=obs['ij']
            margin=logits[i,j,obs['gold']]-logits[i,j,obs['competitor']]
            grad=torch.autograd.grad(margin,params,retain_graph=(ix<len(group)-1),
                                      allow_unused=True)
            pieces=[]
            for (name,p),g in zip(active,grad):
                if g is None:
                    pieces.append(torch.zeros(p.numel(),dtype=torch.float32,device='cpu'))
                    continue
                g=g.detach().float().clone()
                if name=='binding.weight':
                    mask=torch.zeros(g.shape[1],device=g.device,dtype=g.dtype)
                    mask[selected.to(g.device)]=1.
                    g*=mask[None,:]
                elif name=='pair_projection.weight':
                    mask=torch.zeros(g.shape[0],device=g.device,dtype=g.dtype)
                    mask[selected.to(g.device)]=1.
                    g*=mask[:,None]
                elif name=='pair_projection.bias':
                    mask=torch.zeros(g.shape[0],device=g.device,dtype=g.dtype)
                    mask[selected.to(g.device)]=1.
                    g*=mask
                elif not name.startswith('binding.'):
                    g.zero_()  # isolate G2 X-to-output edit path
                pieces.append(g.reshape(-1).cpu())
            rows.append(torch.cat(pieces).double())
            goals.append(obs['required'])
            weights.append(obs['weight'])
    if not rows:
        return {'skipped':'NO_EDITS'}
    J=torch.stack(rows)
    y=torch.tensor(goals,dtype=torch.float64)
    w=torch.tensor(weights,dtype=torch.float64).sqrt()
    Jw=J*w[:,None]
    yw=y*w
    dual=torch.linalg.solve(Jw@Jw.T+damping*torch.eye(len(Jw),dtype=torch.float64),yw)
    delta=Jw.T@dual
    length=float(delta.norm())
    if length>norm_cap:
        delta*=norm_cap/length
    offset=0
    with torch.no_grad():
        for _,p in active:
            part=delta[offset:offset+p.numel()].reshape(p.shape)
            p.add_(part.to(p.device,dtype=p.dtype))
            offset+=p.numel()
    predicted=J@delta
    return {'observations':len(rows),'jacobian_columns':size,
            'linearized_fit_rmse':float((predicted-y).square().mean().sqrt()),
            'edit_weight_norm':float(delta.norm()), 'salient_dimensions':selected.tolist()}


def propose(head, support, guards, anchors, forward, decode, device, method,
            *, salient_k=24):
    if method not in TECHNIQUES[1:]:
        raise ValueError('G2_0094_UNKNOWN_NEUROEDIT_METHOD_'+method)
    observations, misses = _observations(head,support,guards,anchors,forward,decode,device)
    if not misses:
        return {'skipped':'NO_CURRENT_ERROR','observations':len(observations)}
    selected = _salient(observations,head,k=salient_k)
    if method == 'local_ridge':
        report=local_ridge_edit(head,observations,selected)
    elif method == 'binding_rank_one':
        report=rank_one_edit(head,observations,selected)
    elif method == 'rbf_surface':
        report=rbf_surface_edit(head,observations,selected)
    elif method == 'jacobian_inverse':
        report=jacobian_inverse_edit(head,observations,selected,forward,device)
    else:
        raise AssertionError(method)
    return {'method':method,'wrong_samples':len(misses),
            'correct_samples':len(observations)-len(misses),**report}
