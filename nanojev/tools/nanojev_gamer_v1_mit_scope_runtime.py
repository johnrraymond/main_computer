"""Live Gamer G2 MIT query using the validated frozen scope decoder.

Reuses the exact WSP weight file evaluated in MIT-SCOPE-0003. No new weight
editing, training, oracle answers, or full G1-G7 certification. Incomplete
checkpoint lineage fails closed; select MIT-0001/legacy explicitly to roll back.
"""
from __future__ import annotations

from pathlib import Path
import torch

from tools import nanojev_gamer_v1_mit as mit
from tools import nanojev_gamer_v1_mit_wsp as wsp
from tools import nanojev_gamer_v1_mit_scope_decoder as scope
from tools import nanojev_gamer_v1_mit_decoder as projector
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural


def _serialize(edges):
    return [[source, list(src_span), destination, list(dst_span), label]
            for source, src_span, destination, dst_span, label in sorted(edges)]


def query(*, prompt, root=mit.ROOT_DEFAULT, output=None, device='cuda'):
    """Gold-blind active G2 partial graph, bound to the audited WSP lineage."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('MIT_SCOPE_EMPTY_PROMPT')
    if output is not None:
        raise ValueError('MIT_SCOPE_CHECKPOINT_OVERRIDE_NOT_SUPPORTED')
    # Verify the *same* audited, no-gradient checkpoint as experiment 0003.
    dest = wsp.location(root, None)
    if not (Path(dest) / 'checkpoint').is_dir():
        raise FileNotFoundError(
            'MIT_SCOPE_VERIFIED_WSP_CHECKPOINT_REQUIRED: '
            'select --version MIT-0001 or --version legacy to roll back')
    _commit, mit_dest, _base_file, paths, _parent, cfg, edit = wsp.check_edit(dest, root)
    spec = cfg['winner']
    if spec.get('method') != 'joint':
        raise ValueError('MIT_SCOPE_FROZEN_JOINT_DECODER_REQUIRED')
    network, tokenizer, composite, _base, _manifest = structural._load_runtime(
        paths, device)
    head = wsp.load_edited_head(mit_dest / 'checkpoint', Path(dest) / 'checkpoint',
                                edit, device=device)
    head.eval().requires_grad_(False)
    with torch.inference_mode():
        prepared, hidden = legacy.previous._capture_inference(
            network, tokenizer, prompt, device, 2048)
        g1 = composite.predict(hidden, prepared,
                               family='unlabeled', native_stage='G1')
        surface = legacy._infer(head, hidden, prepared['eligible'].to(device),
                                g1['token_spans'], prepared, spec,
                                with_margins=True)
        edges, details = scope.decode(surface, prompt, spec, return_diagnostics=True)
        unconstrained, control = projector.decode(surface, spec, return_diagnostics=True)
    return {
        'version': 'MIT', 'decoder': scope.DECODER,
        'scope_evidence': scope.EVIDENCE,
        'status': 'MIT_SCOPE_G2_PARTIAL_GRAPH_NOT_CERTIFIED',
        'model_head_sha256': edit['edited_head_sha256'],
        'weight_edit_active': True, 'model_training': False,
        'g1_objects': surface['g1_objects'],
        'edges': _serialize(edges), 'baseline_edges': _serialize(unconstrained),
        'solver': details, 'baseline_solver': control,
        'rescued_vs_unconstrained': len(edges - unconstrained),
        'removed_vs_unconstrained': len(unconstrained - edges),
        'labels_used_at_inference': False,
        'g3_unlocked': False, 'production_promotion': False,
        'independent_semantic_scope_certification': False,
    }
