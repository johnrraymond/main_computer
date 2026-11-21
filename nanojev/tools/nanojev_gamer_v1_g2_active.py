"""Active Gamer G2 selector. MIT defaults to validated WSP + scope decoding.

  python -m tools.nanojev_gamer_v1_g2_active version
  python -m tools.nanojev_gamer_v1_g2_active query --prompt "..." --device cuda
  python -m tools.nanojev_gamer_v1_g2_active query --version legacy --prompt "..."

MIT-0001 and legacy are explicit rollback paths. All variants remain uncertified.
Neither output is a certified full G1-G7 game decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from tools import nanojev_gamer_v1_mit as mit
from tools import nanojev_gamer_v1_mit_scope_runtime as scope_runtime
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy

ACTIVE_VERSION='MIT'
VERSIONS=('MIT','MIT-0001','legacy')


def query(*,prompt,root=mit.ROOT_DEFAULT,output=None,device='cuda',version=ACTIVE_VERSION):
    if version=='MIT':
        return scope_runtime.query(prompt=prompt,root=root,output=output,device=device)
    if version=='MIT-0001':
        old=mit.query(prompt=prompt,root=root,output=output,device=device)
        return {**old,'version':'MIT-0001','decoder':'unscoped_exact_MILP',
                'rollback_active':True}
    if version!='legacy':raise ValueError('GAMER_G2_UNKNOWN_VERSION')
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError('GAMER_G2_EMPTY_PROMPT')
    paths,manifest,spec=mit.sources(root)
    network,tok,composite,head,_=structural._load_runtime(paths,device)
    head.eval().requires_grad_(False)
    prepared,hidden=legacy.previous._capture_inference(network,tok,prompt,device,2048)
    g1=composite.predict(hidden,prepared,family='unlabeled',native_stage='G1')
    raw=legacy._infer(head,hidden,prepared['eligible'].to(device),
        g1['token_spans'],prepared,spec,with_margins=True)
    edges=legacy.decode(raw,spec)
    return {'version':'legacy','status':'LEGACY_G2_PARTIAL_GRAPH_NOT_CERTIFIED',
        'model_head_sha256':manifest['head_sha256'],
        'g1_objects':raw['g1_objects'],
        'edges':[[a,list(sa),b,list(sb),t] for a,sa,b,sb,t in sorted(edges)],
        'labels_used_at_inference':False,
        'g3_unlocked':False,'production_promotion':False,
        'weight_edit_active':False}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('version')
    p=sub.add_parser('query')
    p.add_argument('--prompt',required=True)
    p.add_argument('--version',choices=VERSIONS,default=ACTIVE_VERSION)
    p.add_argument('--root',default=mit.ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--device',default='cuda')
    a=ap.parse_args(argv)
    if a.cmd=='version':
        value={'active_version':ACTIVE_VERSION,'active_decoder':'identity_scope',
               'fallback_version':'legacy',
               'rollback_versions':['MIT-0001','legacy'],
               'g3_unlocked':False,'production_promotion':False}
    else:
        value=query(prompt=a.prompt,root=a.root,output=a.output,device=a.device,
                    version=a.version)
    print(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False))
    return value


if __name__=='__main__':main()
