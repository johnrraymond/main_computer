"""Fail-closed, one-campaign G1..G7 GAMER deterministic-default cutover.

prepare: immutable evidence+default weights+stage oracle contract. Does not train.
run: sequentially retrains ALL seven stages from ONE newly initialized head,
     evaluates dev/challenge and previous-stage retention, certifies each gate.
     Stops at first failed gate. Never loads an old model as a new trainable
     parent, touches sealed test, modifies production, or promotes weights.

The historical G2-0062 set predictor is an independent comparison baseline,
not a hidden dependency of the G1..G7 GamerGraphHead. Reusing its old G1
capture as though it came from new G1 is explicitly forbidden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import torch
from safetensors.torch import save_file, load_file

from tools import nanojev_deterministic_fabric as fabric

SCHEMA='nanojev.gamer.oracle-fabric-cutover.v1'
LEGACY_G2_0062_STEP4900='16cdc9c5937befe08fc87ba53a13f2b2e6f442676377cad729d235ae08fabddd'


def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(1<<20),b''):
            h.update(data)
    return h.hexdigest()


def _write_new(path,obj):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        f.write(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')


def _load_legacy_g1(directory):
    from tools import nanojev_gamer_v1_graph_curriculum as g
    directory=Path(directory).resolve()
    head,manifest=g._load_checkpoint(directory,device='cpu')
    if manifest['stage']!='G1' or manifest.get('deterministic_fabric'):
        raise ValueError('CUTOVER_LEGACY_G1_INVALID')
    return directory,manifest,file_sha(directory/g.HEAD_FILE)


def discover(*,root):
    """Read-only discovery; never silently chooses among multiple legacy G1s."""
    root=Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError('CUTOVER_LEGACY_ROOT_NOT_FOUND:'+str(root))
    g1=[];g2=[]
    for item in sorted(root.rglob('manifest.json')):
        try:
            c=json.loads(item.read_text(encoding='utf-8'))
        except (ValueError,UnicodeDecodeError,OSError):
            continue
        if c.get('stage')=='G1' and c.get('status')=='TRAINED_NOT_CERTIFIED' and not c.get('deterministic_fabric'):
            if (item.parent/'gamer_graph_head.safetensors').is_file():
                g1.append({'path':str(item.parent),'head_sha256':c.get('head_sha256'),
                           'adapter':c.get('source_adapter'),'dataset_sha256':c.get('dataset_sha256')})
    for item in sorted(root.rglob('*4900*.safetensors')):
        if file_sha(item)==LEGACY_G2_0062_STEP4900:
            g2.append(str(item))
    # Match G1 dataset only by SHA rather than guessing from a filename.
    sources=[]
    for candidate in g1:
        root_dataset_candidates=(root.rglob('*.jsonl'))
        matches=[]
        for item in root_dataset_candidates:
            if file_sha(item)==candidate['dataset_sha256']:
                matches.append(str(item))
        sources.append({'g1':candidate['path'],'matching_datasets':matches})
    return {'legacy_g1_candidates':g1,'legacy_g2_step4900_candidates':g2,
            'dataset_matches':sources,'selection':'must be unique or specified explicitly'}


def prepare_auto(*,root,output,cutover_id='oracle-fabric-0001'):
    found=discover(root=root)
    g1=found['legacy_g1_candidates'];g2=found['legacy_g2_step4900_candidates']
    if len(g1)!=1 or len(g2)!=1:
        raise ValueError('CUTOVER_AMBIGUOUS_LEGACY_SELECTION; run discover then explicit prepare; found G1=%d G2=%d' %(len(g1),len(g2)))
    matches=next(x['matching_datasets'] for x in found['dataset_matches'] if x['g1']==g1[0]['path'])
    if len(matches)!=1:
        raise ValueError('CUTOVER_AMBIGUOUS_G1_DATASET; run discover then explicit prepare; matches=%d' %len(matches))
    return prepare(output=output,dataset=matches[0],source_adapter=g1[0]['adapter'],
                   legacy_g1=g1[0]['path'],legacy_g2=g2[0],cutover_id=cutover_id)


def _research_evidence():
    root=Path(__file__).resolve().parents[2]/'runtime'/'reports'/'gamer-v1'
    entries={}
    for name in ('graph-g2-0067-4900.json','graph-g2-0068-4900.json','graph-g2-0069-4900.json'):
        p=root/name
        if not p.is_file():
            entries[name]={'status':'NOT_AVAILABLE_FOR_PINNING'}
            continue
        r=json.loads(p.read_text(encoding='utf-8'))
        expected=r.get('checkpoint_sha256') or r.get('lineage',{}).get('checkpoint_sha256')
        if expected!=LEGACY_G2_0062_STEP4900:
            raise ValueError('CUTOVER_RESEARCH_EVIDENCE_CHECKPOINT_DRIFT:'+name)
        if r.get('production_promotion') is True or r.get('sealed_test_used') is True:
            raise ValueError('CUTOVER_RESEARCH_EVIDENCE_UNSAFE:'+name)
        entries[name]={'path':str(p.resolve()),'sha256':file_sha(p),
                       'checkpoint_sha256':expected,'status':'PINNED'}
    return entries


def _legacy_evidence(g1,g2):
    g1_dir,legacy_g1,g1_sha=_load_legacy_g1(g1)
    g2_path=Path(g2).resolve()
    g2_sha=file_sha(g2_path)
    if g2_sha!=LEGACY_G2_0062_STEP4900:
        raise ValueError('CUTOVER_LEGACY_G2_STEP4900_HASH_MISMATCH')
    return {'g1_checkpoint':str(g1_dir),'g1_weights_sha256':g1_sha,
            'g1_manifest_sha256':file_sha(g1_dir/'manifest.json'),
            'g1_dataset_sha256':legacy_g1['dataset_sha256'],
            'g1_source_adapter_sha256':legacy_g1['source_adapter_sha256'],
            'g2_checkpoint':str(g2_path),'g2_weights_sha256':g2_sha,
            'g2_reference_edge_f1_percent':81.23,
            'g2_reference_exact_graphs':'4/96',
            'g2_checkpoint_step':4900,
            'distinct_model_families':True}


def _checked_source(dataset,source_adapter):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    ds=Path(dataset).resolve()
    adapter=Path(source_adapter).resolve()
    if not ds.is_file() or not adapter.exists():
        raise FileNotFoundError('CUTOVER_TRAINING_SOURCE_NOT_FOUND')
    return {'dataset':str(ds),'dataset_sha256':file_sha(ds),
        'source_adapter':str(adapter),
        'source_adapter_sha256':backbone.adapter_fingerprint(adapter)}


def _default_models_config(legacy_g1):
    from tools import nanojev_gamer_v1_graph_curriculum as g
    manifest=json.loads((Path(legacy_g1)/g.MANIFEST).read_text(encoding='utf-8'))
    raw=manifest['head']
    return {key:int(raw[key]) for key in (
        'hidden_size','width','layers','latent_slots','memory_slots',
        'max_operands','max_span_tokens')}


def prepare(*,output,dataset,source_adapter,legacy_g1,legacy_g2,cutover_id='oracle-fabric-0001'):
    from tools import nanojev_gamer_v1_graph_curriculum as g
    if not cutover_id or len(cutover_id)>128:
        raise ValueError('CUTOVER_ID')
    output=Path(output).resolve()
    if output.exists():
        raise FileExistsError('CUTOVER_OUTPUT_ALREADY_EXISTS')
    inputs=_checked_source(dataset,source_adapter)
    reference=_legacy_evidence(legacy_g1,legacy_g2)
    config=_default_models_config(legacy_g1)
    model=fabric.new_graph_head(**config)
    set_model=fabric.new_triple_set()
    before=torch.random.get_rng_state().clone()
    second=fabric.new_graph_head(**config)
    if not torch.equal(before,torch.random.get_rng_state()) or fabric.state_digest(model)!=fabric.state_digest(second):
        raise RuntimeError('CUTOVER_DEFAULT_NONDETERMINISTIC')
    numerical=fabric.numerical_oracle()
    g1_smoke=fabric.graph_default_oracle(model,hidden_size=config['hidden_size'])
    g2_smoke=fabric.set_default_oracle(set_model)
    imports=(('graph_head',g),('fabric',fabric))
    pins={name:file_sha(Path(mod.__file__)) for name,mod in imports}
    pins['graph_model']=file_sha(Path(__file__).with_name('nanojev_gamer_v1_graph_head.py'))
    pins['cutover_driver']=file_sha(Path(__file__))
    if not inputs['source_adapter_sha256']:
        raise ValueError('CUTOVER_SOURCE_ADAPTER_UNFINGERPRINTED')
    legacy_manifest=json.loads((Path(legacy_g1)/'manifest.json').read_text(encoding='utf-8'))
    campaign={'schema':SCHEMA,'status':'PREPARED_NOT_EXECUTED',
        'cutover_id':cutover_id,'fabric_version':fabric.VERSION,
        'production_promotion':False,'sealed_test_used':False,
        'training_lineage':inputs,'legacy_reference':reference,
        'research_evidence':_research_evidence(),
        'graph_head_config':config,'model_id':legacy_manifest['model_id'],
        'init_graph_digest_sha256':fabric.state_digest(model),
        'init_g2_comparator_digest_sha256':fabric.state_digest(set_model),
        'oracles':{'dyadic':numerical,'g1_default':g1_smoke,'g2_comparator_default':g2_smoke},
        'pin_source':pins,'acceptance':fabric.contract_ledger(),
        'supported_training_pipeline':'GamerGraphHead G1..G7 stage-gated curriculum',
        'unsupported':'historical stand-alone G2-0062 cannot be retrained on old-G1 captures and called new-G1 integration',
        'all_stages_from_new_defaults':True,
        'legacy_weights_used_as_initialization':False}
    output.mkdir(parents=True,exist_ok=False)
    save_file({k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()},
              str(output/'default-graph-head.safetensors'))
    save_file({k:v.detach().cpu().contiguous() for k,v in set_model.state_dict().items()},
              str(output/'default-g2-set-comparator.safetensors'))
    campaign['default_graph_file_sha256']=file_sha(output/'default-graph-head.safetensors')
    campaign['default_g2_file_sha256']=file_sha(output/'default-g2-set-comparator.safetensors')
    _write_new(output/'campaign.json',campaign)
    _write_new(output/'oracle-contract.json',fabric.contract_ledger())
    print('CUTOVER_PREPARED '+json.dumps({'output':str(output),'id':cutover_id,
        'g1_default_digest':campaign['init_graph_digest_sha256'],
        'g2_default_digest':campaign['init_g2_comparator_digest_sha256'],
        'legacy_g2_reference_sha256':reference['g2_weights_sha256'],
        'status':'PREPARED_NOT_TRAINED'},sort_keys=True),flush=True)
    return campaign


def verify_prepared(folder):
    from tools import nanojev_gamer_v1_graph_curriculum as g
    folder=Path(folder).resolve()
    c=json.loads((folder/'campaign.json').read_text(encoding='utf-8'))
    if c.get('schema')!=SCHEMA or c['status']!='PREPARED_NOT_EXECUTED' or \
       c['production_promotion'] is not False or c['sealed_test_used'] is not False:
        raise ValueError('CUTOVER_MANIFEST_INVALID')
    for name,path in [('default_graph_file_sha256','default-graph-head.safetensors'),
                      ('default_g2_file_sha256','default-g2-set-comparator.safetensors')]:
        if file_sha(folder/path)!=c[name]:
            raise ValueError('CUTOVER_DEFAULT_TAMPERED:'+path)
    for label,path in [('graph_head',Path(g.__file__)),
                       ('fabric',Path(fabric.__file__)),
                       ('graph_model',Path(__file__).with_name('nanojev_gamer_v1_graph_head.py')),
                       ('cutover_driver',Path(__file__))]:
        if file_sha(path)!=c['pin_source'][label]:
            raise ValueError('CUTOVER_SOURCE_DRIFT:'+label)
    src=c['training_lineage'];ref=c['legacy_reference']
    if file_sha(src['dataset'])!=src['dataset_sha256']:
        raise ValueError('CUTOVER_DATASET_DRIFT')
    from tools import nanojev_gamer_v1_backbone_first as backbone
    if backbone.adapter_fingerprint(src['source_adapter'])!=src['source_adapter_sha256']:
        raise ValueError('CUTOVER_SOURCE_ADAPTER_DRIFT')
    if file_sha(ref['g1_checkpoint']+'/gamer_graph_head.safetensors')!=ref['g1_weights_sha256']:
        raise ValueError('CUTOVER_LEGACY_G1_DRIFT')
    if file_sha(ref['g2_checkpoint'])!=ref['g2_weights_sha256']:
        raise ValueError('CUTOVER_LEGACY_G2_DRIFT')
    for name,rec in c.get('research_evidence',{}).items():
        if rec['status']=='PINNED' and file_sha(rec['path'])!=rec['sha256']:
            raise ValueError('CUTOVER_EXPERIMENT_EVIDENCE_DRIFT:'+name)
    new=fabric.new_graph_head(**c['graph_head_config'])
    if fabric.state_digest(new)!=c['init_graph_digest_sha256']:
        raise ValueError('CUTOVER_INIT_DRIFT')
    # Verify immutable prepared state against rederived default tensors.
    stored=load_file(str(folder/'default-graph-head.safetensors'),device='cpu')
    if any(not torch.equal(v,stored[k]) for k,v in new.state_dict().items()):
        raise ValueError('CUTOVER_PREPARED_WEIGHTS_MISMATCH')
    return c


def _evidence_path(folder,stage,split,prior=None):
    name=f'{stage}-{prior or stage}-{split}'
    return folder/'evaluation'/name


def _load_or_run_evaluate(g,*,dataset,checkpoint,path,split,stage,device):
    if path.exists():
        r=json.loads((path/'report.json').read_text(encoding='utf-8'))
        manifest=json.loads((checkpoint/'manifest.json').read_text(encoding='utf-8'))
        if (r['checkpoint_head_sha256']!=manifest['head_sha256'] or
            r['stage']!=stage or r['split']!=split):
            raise ValueError('CUTOVER_EXISTING_EVAL_LINEAGE')
        return r
    return g.evaluate(dataset=dataset,checkpoint=checkpoint,output=path,
                      split=split,device=device,target_stage=stage)


def run(*,campaign,device='cuda',steps=400,accum=4,lr=0.0001,g1_lr=0.0003,max_stage='G7'):
    from tools import nanojev_gamer_v1_graph_curriculum as g
    if steps<1 or accum<1 or not 0<lr<=0.005 or not 0<g1_lr<=0.005:
        raise ValueError('CUTOVER_TRAIN_SETTINGS')
    if max_stage not in g.STAGES:
        raise ValueError('CUTOVER_STAGE')
    if device=='cuda' and not torch.cuda.is_available():
        raise ValueError('CUTOVER_CUDA_UNAVAILABLE')
    folder=Path(campaign).resolve()
    c=verify_prepared(folder)
    task={'schema':SCHEMA,'cutover_id':c['cutover_id'],
          'steps':steps,'accum':accum,'g1_lr':g1_lr,'later_stage_lr':lr,
          'device':device,'production_promotion':False,'sealed_test_used':False}
    task_path=folder/'run_config.json'
    if task_path.exists():
        if json.loads(task_path.read_text(encoding='utf-8'))!=task:
            raise ValueError('CUTOVER_TRAINING_CONFIGURATION_DRIFT')
    else:
        _write_new(task_path,task)
    src=c['training_lineage'];parent=None;cert=None
    prefix=g.STAGES[:g.STAGES.index(max_stage)+1]
    for stage in prefix:
        output=folder/'stages'/stage
        if not output.exists():
            print('CUTOVER_TRAINING '+stage,flush=True)
            g.train(dataset=src['dataset'],output=output,
                source_adapter=src['source_adapter'],stage=stage,
                parent_checkpoint=parent,prerequisite_cert=cert,
                device=device,steps=steps,accum=accum,
                lr=(g1_lr if stage=='G1' else lr),
                model_id=c['model_id'],
                width=c['graph_head_config']['width'],
                layers=c['graph_head_config']['layers'],
                latent_slots=c['graph_head_config']['latent_slots'],
                memory_slots=c['graph_head_config']['memory_slots'],
                max_operands=c['graph_head_config']['max_operands'],
                max_span_tokens=c['graph_head_config']['max_span_tokens'],
                cutover_id=c['cutover_id'],
                family_balanced=(stage=='G1'),
                counterfactual_fraction=0.5,
                consistency_weight=0.15)
        model_manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        fm=model_manifest.get('deterministic_fabric')
        if not fm or fm['cutover_id']!=c['cutover_id'] or \
           fm['initial_state_sha256']!=c['init_graph_digest_sha256'] or \
           model_manifest['stage']!=stage or model_manifest['production_promotion'] is not False or \
           model_manifest['dataset_sha256']!=src['dataset_sha256'] or \
           model_manifest['steps']!=steps or model_manifest['accum']!=accum or \
           model_manifest['lr']!=(g1_lr if stage=='G1' else lr):
            raise ValueError('CUTOVER_TRAINED_STAGE_LINEAGE_OR_SOURCE_DRIFT:'+stage)
        for split in ('dev','challenge'):
            _load_or_run_evaluate(g,dataset=src['dataset'],checkpoint=output,
                                  path=_evidence_path(folder,stage,split),
                                  split=split,stage=stage,device=device)
        retentions={split:{} for split in ('dev','challenge')}
        for prior in g.STAGES[:g.STAGES.index(stage)]:
            for split in ('dev','challenge'):
                path=_evidence_path(folder,stage,split,prior)
                _load_or_run_evaluate(g,dataset=src['dataset'],checkpoint=output,
                                      path=path,split=split,stage=prior,device=device)
                retentions[split][prior]=str(path/'report.json')
        cert_dir=folder/'certificates'/stage
        if not cert_dir.exists():
            outcome=g.certify(checkpoint=output,
                dev_report=_evidence_path(folder,stage,'dev')/'report.json',
                challenge_report=_evidence_path(folder,stage,'challenge')/'report.json',
                output=cert_dir,
                retention_dev=retentions['dev'],
                retention_challenge=retentions['challenge'])
        else:
            outcome=json.loads((cert_dir/'certification.json').read_text(encoding='utf-8'))
        print('CUTOVER_GATE '+json.dumps({'stage':stage,'status':outcome['status'],
            'next_gate':outcome['status']=='DEVELOPMENT_GATE_PASSED'},sort_keys=True),flush=True)
        if outcome['status']!='DEVELOPMENT_GATE_PASSED':
            print('CUTOVER_BLOCKED '+stage+'; legacy remains authoritative',flush=True)
            return {'status':'BLOCKED','blocked_stage':stage,'production_promotion':False}
        parent=output
        cert=cert_dir/'certification.json'
    print('CUTOVER_RESEARCH_GATES_COMPLETE; NO PRODUCTION PROMOTION OR SEALED TEST',flush=True)
    return {'status':'RESEARCH_GATES_COMPLETE_NOT_PRODUCTION_CERTIFIED',
            'stages':prefix,'production_promotion':False,'sealed_test_used':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    di=sub.add_parser('discover',help='Locate legacy checkpoints, dataset and source adapter without selecting ambiguous matches')
    di.add_argument('--root',type=Path,default=(Path(__file__).resolve().parents[2]/'runtime'/'reports'/'gamer-v1'))
    au=sub.add_parser('prepare-auto',help='Prepare only when all baseline source paths are uniquely hash-identified')
    au.add_argument('--root',type=Path,default=(Path(__file__).resolve().parents[2]/'runtime'/'reports'/'gamer-v1'))
    au.add_argument('--output',required=True,type=Path)
    au.add_argument('--cutover-id',default='oracle-fabric-0001')
    pr=sub.add_parser('prepare',help='Preserve evidence, build default weights and verify oracles')
    for name in ('dataset','source-adapter','legacy-g1','legacy-g2','output'):
        pr.add_argument('--'+name,required=True,type=Path)
    pr.add_argument('--cutover-id',default='oracle-fabric-0001')
    ve=sub.add_parser('verify',help='Reopen original baselines and verify prepared immutable defaults')
    ve.add_argument('--campaign',required=True,type=Path)
    ru=sub.add_parser('run',help='Execute entire G1..G7 stage-gated retraining campaign')
    ru.add_argument('--campaign',required=True,type=Path)
    ru.add_argument('--device',default='cuda',choices=('cpu','cuda'))
    ru.add_argument('--steps',type=int,default=400)
    ru.add_argument('--accum',type=int,default=4)
    ru.add_argument('--lr',type=float,default=1e-4,help='Learning rate for G2 through G7; original G2-0062 belongs to a different model family')
    ru.add_argument('--g1-lr',type=float,default=3e-4,help='Original G1 fresh-training rate by default')
    ru.add_argument('--max-stage',choices=('G1','G2','G3','G4','G5','G6','G7'),default='G7')
    args=p.parse_args(argv)
    if args.action=='discover':
        print(json.dumps(discover(root=args.root),indent=2))
    elif args.action=='prepare-auto':
        prepare_auto(root=args.root,output=args.output,cutover_id=args.cutover_id)
    elif args.action=='prepare':
        prepare(output=args.output,dataset=args.dataset,source_adapter=args.source_adapter,
                legacy_g1=args.legacy_g1,legacy_g2=args.legacy_g2,cutover_id=args.cutover_id)
    elif args.action=='verify':
        record=verify_prepared(args.campaign)
        print('CUTOVER_VERIFIED '+record['cutover_id'])
    else:
        result=run(campaign=args.campaign,device=args.device,
                   steps=args.steps,accum=args.accum,lr=args.lr,g1_lr=args.g1_lr,max_stage=args.max_stage)
        if result['status']=='BLOCKED':
            raise SystemExit(2)

if __name__=='__main__':
    main()
