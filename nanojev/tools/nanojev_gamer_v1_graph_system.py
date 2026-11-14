"""GAMER neural graph inference: full prompt -> Qwen hidden -> structured head.

No game-specific parser, externally selected game, autoregressive generation,
training label, or hidden answer oracle. Uncertified stages emit partial graphs
and cannot make decisions. Certified complete graphs may use the generic exact
executor. Memory writes require an independently gated G6 certificate.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_contract import STAGES, validate_graph, execute_graph
from tools.nanojev_gamer_v1_graph_head import decode_graph
from tools.nanojev_gamer_v1_graph_curriculum import (
    _load_checkpoint, SCHEMA, _verify_certification, json_text,
)


def _verified_head(checkpoint, device):
    path=Path(checkpoint)
    head, manifest=_load_checkpoint(path,device=device)
    if manifest.get('backbone_frozen') is not True or manifest.get('lora_frozen') is not True:
        raise ValueError('GAMER_GRAPH_BACKBONE_LINEAGE_INVALID')
    if backbone.adapter_fingerprint(manifest['source_adapter']) != manifest['source_adapter_sha256']:
        raise ValueError('GAMER_GRAPH_SOURCE_ADAPTER_LINEAGE_INVALID')
    return head.eval(), manifest


def query(*,checkpoint,prompt,device='cuda',max_length=2048,
          memory=None,certificate=None):
    import torch
    head,manifest=_verified_head(checkpoint,device)
    stage=manifest['stage']
    if certificate is not None:
        _verify_certification(certificate,checkpoint,manifest,stage)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    prepared=backbone.prepare_prompt(tok,prompt,max_input_tokens=max_length)
    hidden=backbone.frozen_context(model,prepared,device=device)
    bank=None
    if memory is not None:
        if STAGES.index(stage)<5:
            raise PermissionError('GAMER_GRAPH_MEMORY_REQUIRES_G6')
        from safetensors.torch import load_file
        bank_data=load_file(str(memory),device='cpu')
        if set(bank_data)!={'slots'}:
            raise ValueError('GAMER_GRAPH_MEMORY_BAD_FIELDS')
        bank=bank_data['slots'].to(device=device,dtype=torch.float32)
    with torch.inference_mode():
        result=head(hidden.to(device=device,dtype=torch.float32),
                    eligible=prepared['eligible'].to(device),memory=bank,
                    active_stage=stage)
    if stage=='G1':
        from tools.nanojev_gamer_v1_graph_head import decode_neural_spans
        spans=decode_neural_spans(result['span_logits'],result['span_valid'])
        # Share the same tokenizer-to-character conversion as G1 evaluation
        # and all later graph stages. Tokenizers may include leading spaces.
        graph,surfaces=decode_graph({'spans':spans},prepared,unlocked='G1')
    else:
        graph,surfaces=decode_graph(result,prepared,unlocked=stage)
    validate_graph(graph)
    response={'status':'NEURAL_GRAPH_INCOMPLETE_UNCERTIFIED',
          'stage':stage,'objects':surfaces,'graph':graph,'generated_tokens':0,
          'backbone_frozen':True,'lora_frozen':True,
          'game_selected_externally':False,'semantic_parser_used':False,
          'labels_used_at_inference':False,
          'head_sha256':manifest['head_sha256'],'production_promotion':False}
    if stage=='G7' and certificate is not None:
        # Even G7 certification cannot invent absent learned relationships.
        try:
            response['evaluation']=execute_graph(graph)
            response['status']='GRAPH_EXECUTED_CERTIFIED_STAGE'
        except ValueError as exc:
            response['status']='INCOMPLETE_NEURAL_GRAPH'
            response['error']=str(exc)
    return response


def acquire(*, checkpoint, certificate, teaching_prompt, previous_memory=None,
            output, device='cuda', max_length=2048):
    """Acquire a neural memory without a game identifier or preselected rules.

    Memory is an append-only detached tensor artifact. It does not replace a
    Qwen/LoRA checkpoint, and it cannot be used before G6 certification.
    """
    import torch
    from safetensors.torch import load_file, save_file
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_GRAPH_MEMORY_IMMUTABLE')
    head,manifest=_verified_head(checkpoint,device)
    if STAGES.index(manifest['stage'])<5:
        raise PermissionError('GAMER_GRAPH_MEMORY_G6_LOCKED')
    _verify_certification(certificate,checkpoint,manifest,manifest['stage'])
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    prepared=backbone.prepare_prompt(tok,teaching_prompt,max_input_tokens=max_length)
    hidden=backbone.frozen_context(model,prepared,device=device)
    if previous_memory:
        prev=load_file(str(previous_memory),device='cpu')
        if set(prev)!={'slots'}:raise ValueError('GAMER_GRAPH_MEMORY_FIELDS')
        bank=prev['slots'].to(device=device,dtype=torch.float32)
    else:
        bank=torch.zeros((head.memory_slots_count,head.width),device=device)
    open_slots=(bank.norm(dim=-1)==0).nonzero(as_tuple=False).flatten().tolist()
    if not open_slots:
        raise ValueError('GAMER_GRAPH_NEURAL_MEMORY_CAPACITY_REACHED')
    slot=open_slots[0]
    with torch.inference_mode():
        state=head(hidden.float(),eligible=prepared['eligible'].to(device),
                   memory=bank,active_stage=manifest['stage'])
        next_bank=head.updated_memory(bank,state,authorized=True,slot=slot)
    output.parent.mkdir(parents=True,exist_ok=True)
    save_file({'slots':next_bank.detach().cpu().contiguous()},str(output))
    return {'status':'NEURAL_MEMORY_ACQUIRED_UNCERTIFIED_GAME_RECALL',
            'path':str(output),'slot':slot,'head_sha256':manifest['head_sha256'],
            'generated_tokens':0,'game_name_key_used':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    q=sub.add_parser('query')
    q.add_argument('--checkpoint',required=True);q.add_argument('--prompt',required=True)
    q.add_argument('--certificate');q.add_argument('--memory')
    q.add_argument('--device',default='cuda');q.add_argument('--max-length',type=int,default=2048)
    a=sub.add_parser('acquire')
    a.add_argument('--checkpoint',required=True);a.add_argument('--certificate',required=True)
    a.add_argument('--teaching-prompt',required=True);a.add_argument('--output',required=True)
    a.add_argument('--previous-memory')
    a.add_argument('--device',default='cuda');a.add_argument('--max-length',type=int,default=2048)
    args=p.parse_args(argv)
    if args.cmd=='query':r=query(checkpoint=args.checkpoint,prompt=args.prompt,
       certificate=args.certificate,memory=args.memory,device=args.device,
       max_length=args.max_length)
    else:r=acquire(checkpoint=args.checkpoint,certificate=args.certificate,
       teaching_prompt=args.teaching_prompt,output=args.output,
       previous_memory=args.previous_memory,device=args.device,
       max_length=args.max_length)
    print(json_text(r))
    return r

if __name__=='__main__':main()
