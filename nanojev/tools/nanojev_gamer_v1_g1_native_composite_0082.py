"""G1-0082: hash-bound composite G1 -> native graph-head consumer.

The native G2 head *only* receives semantic spans predicted by the frozen G1
composite. Gold data and candidate oracle labels never enter inference.
Changing an existing graph-head forward call without this explicit adapter has
no composite semantics. Old behavior is deliberately unchanged by default.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import torch

from tools import nanojev_gamer_v1_g1_certification_audit_0080 as audit
from tools import nanojev_gamer_v1_g1_certification_completion_0081 as prior
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural
from tools import nanojev_gamer_v1_g1_complete_object_bakeoff_0072 as complete
from tools import nanojev_gamer_v1_g1_actor_rescue_bakeoff_0071 as actor
from tools import nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 as quantity
from tools import nanojev_gamer_v1_graph_curriculum as curriculum
from tools import nanojev_gamer_v1_graph_head as graph_head
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set

SCHEMA = 'nanojev.gamer.graph.g1.native-composite-interface-0082.v1'
# Review-controlled, opt-in native G2 extension. The old G1-0081 curriculum
# checksum remains pinned; this is NOT permission for arbitrary drift.
ORIGINAL_CURRICULUM_SHA256 = '0f6c5639385aab8227abe127b1e57ec675c2b82d90ef4df7e2f381ef56aee6eb'
NATIVE_CURRICULUM_SHA256 = '3101a0c9a7152897926c11ef6d7ba0fbee9673a01e6a3e1aae4c09dc86957b04'


def require(value, message):
    if not value:
        raise ValueError('G1_0082_'+message)


def _sha_text(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _checked_source(record):
    """Detect unreviewed changes to *every* inference component from G1-0081."""
    root=Path(__file__).resolve().parent
    source=record.get('source_code_sha256',{})
    require(isinstance(source,dict) and len(source)>=8, 'MISSING_SOURCE_LOCK')
    for name,sha in source.items():
        module=('nanojev_gamer_v1_g1_certification_completion_0081' if name=='G1_0081_runner' else name)
        file=root/(module+'.py')
        if name=='nanojev_gamer_v1_graph_curriculum':
            require(sha==ORIGINAL_CURRICULUM_SHA256 and
                    file.is_file() and audit.sha(file)==NATIVE_CURRICULUM_SHA256,
                    'UNAPPROVED_NATIVE_CURRICULUM_DRIFT')
        else:
            require(file.is_file() and audit.sha(file)==sha,'SOURCE_DRIFT_'+name)


def load_composite(checkpoint, *, device='cpu', validate_code=True):
    """Recreate the actual frozen 0081 head + learned interface (never fit)."""
    root=Path(checkpoint).resolve()
    record=audit.read_json(root/'composite-interface.json')
    require(record.get('schema')==prior.SCHEMA and
            record.get('checkpoint_kind')=='COMPOSITE_G1_HEAD_PLUS_TRAINED_INFERENCE_INTERFACE' and
            record.get('status')=='FROZEN_RESEARCH_CHECKPOINT_NOT_NATIVE_G1_CERTIFICATE' and
            record.get('selected_arm')==prior.SELECTED_CUTOVER and
            record.get('extra_candidate_budget')==64 and
            record.get('selected_not_retrained') is True and
            record.get('model_weights_mutated') is False and
            record.get('g2_unlocked') is False, 'COMPOSITE_MANIFEST_INVALID')
    if validate_code:
        _checked_source(record)
    from safetensors.torch import load_file
    manifest=audit.read_json(root/'head-manifest.json')
    require(manifest.get('schema')==curriculum.SCHEMA and manifest.get('stage')=='G1' and
            manifest.get('status')=='TRAINED_NOT_CERTIFIED' and
            manifest.get('head_sha256')==record['head_sha256'] and
            audit.sha(root/'gamer_graph_head.safetensors')==record['head_sha256'] and
            manifest.get('source_adapter_sha256')==record['source_adapter_sha256'],
            'HEAD_LINEAGE_INVALID')
    fabric=manifest.get('deterministic_fabric')
    require(isinstance(fabric,dict) and fabric.get('new_trainable_random_init') is False,
            'DETERMINISTIC_FABRIC_REQUIRED')
    from tools import nanojev_deterministic_fabric as df
    h=manifest['head']
    head=df.new_graph_head(hidden_size=h['hidden_size'],width=h['width'],
        layers=h['layers'],latent_slots=h['latent_slots'],memory_slots=h['memory_slots'],
        max_operands=h['max_operands'],max_span_tokens=h['max_span_tokens'],
        oracle_coupling=h['oracle_coupling'])
    require(df.state_digest(head)==fabric['initial_state_sha256'],'INITIAL_HEAD_DRIFT')
    head.load_state_dict(load_file(str(root/'gamer_graph_head.safetensors'),device='cpu'),strict=True)
    head=head.to(device).eval().requires_grad_(False)
    bundles=record.get('coefficient_bundles',{})
    require(set(bundles)=={'quantity_control_interface','historical_continued_interface'},
            'BUNDLE_SET_INVALID')
    for name,b in bundles.items():
        audit.bundle_integrity(b,name)
    initial=quantity._load_model(bundles['quantity_control_interface'])
    continued=quantity._load_model(bundles['historical_continued_interface'])
    prior_name=record.get('candidate_prior')
    require(isinstance(prior_name,str) and any(x.name==prior_name and
            x.method in ('runnerup_joint','actor_prior','control') for x in actor.ARMS),
            'PRIOR_NAME_INVALID')
    weights=record['structured_reference_weights']
    structural._safe_weights(coverage.CONTROL,weights)
    return NativeCompositeG1(root,record,manifest,head,initial,continued,prior_name,weights)


class NativeCompositeG1:
    def __init__(self,root,record,manifest,head,initial,continued,prior_name,weights):
        self.root=root; self.record=record; self.manifest=manifest
        self.head=head; self.initial=initial; self.continued=continued
        self.prior_name=prior_name; self.weights=weights

    def _lattice(self, prepared, first_output, family='unlabeled'):
        logits=first_output['span_logits']; valid=first_output['span_valid']
        options,ctl,n=learned._lattice(logits,valid,prepared,self.prior_name)
        originals,_=complete._decode(logits,valid,self.prior_name)
        require(ctl==originals['action_rescue_2p00_cond0p50'], 'CONTROL_LATTICE_DRIFT')
        case=learned.Case('cutover','native',family,_sha_text(prepared['prompt']),
            n,options,_char_set(ctl,prepared),set(),ctl)
        old=structural._encode(case)
        expanded=coverage._extend_lattice(old,logits,valid,prepared,coverage.MAX_OPTIONS)
        coverage._prepare_scores(expanded,self.weights)
        require(not expanded.base.case.gold and not expanded.added.case.gold,
                'GOLD_REACHED_INFERENCE')
        return expanded

    @staticmethod
    def _tokens(selected,data,prepared):
        """Recover exact native token boundaries of the *selected* char objects."""
        wanted=sorted(selected,key=lambda key:(key[0],key[1],key[2]))
        require(all(x is not None and len(x)==3 for x in wanted),'INVALID_SELECTED_KEY')
        for a,b,k in wanted:
            require(type(a) is int and type(b) is int and 0<=a<b<=len(prepared['prompt']) and
                    k in graph_head.SPAN_KINDS, 'INVALID_SELECTED_OBJECT')
        require(all(wanted[i][1]<=wanted[i+1][0] for i in range(len(wanted)-1)),
                'OVERLAPPING_CHAR_OBJECTS')
        options=data.added.case.options
        by_key={}
        for opt in options:
            if opt.char_key in selected:
                by_key.setdefault(opt.char_key,[]).append(opt)
        require(set(by_key)==set(wanted),'UNMAPPABLE_CHAR_OBJECT')
        # Some tokenizer representations contain the same author-space object
        # with different leading-whitespace token spans. Find an *ordered,
        # disjoint* assignment, not an arbitrary first match.
        order=[sorted(by_key[key],key=lambda o:(o.end-o.start,o.start,o.end,o.kind))
               for key in wanted]
        path=[]
        def assign(i, prev_end):
            if i==len(order):return True
            for o in order[i]:
                if o.start<prev_end:continue
                path.append(o)
                if assign(i+1,o.end):return True
                path.pop()
            return False
        require(assign(0,0),'UNMAPPABLE_NONOVERLAP_TOKEN_SPANS')
        result=[{'kind':graph_head.SPAN_KINDS[o.kind-1],
                 'token_first':o.start,'token_last':o.end-1} for o in path]
        require(len(result)==len(wanted)<=128,'TOO_MANY_NATIVE_SPANS')
        for x,key in zip(result,wanted):
            bounds=graph_head.token_span_char_bounds(x,prepared)
            require((*bounds,x['kind'])==key,'NATIVE_OBJECT_BOUNDARY_DRIFT')
        return result

    def predict(self, hidden, prepared, *, family='unlabeled', mode='full',native_stage='G2'):
        """One gold-blind inference call; returns output and native G2 readouts."""
        require(mode in ('full','zero_hidden','stage_component'),'BAD_ABLATION')
        require(native_stage in ('G1','G2'),'UNSUPPORTED_UNTRAINED_NATIVE_STAGE')
        device=next(self.head.parameters()).device
        x=hidden.to(device=device,dtype=torch.float32)
        eligible=prepared['eligible'].to(device)
        with torch.inference_mode():
            raw=self.head(x if mode!='zero_hidden' else torch.zeros_like(x),
                          eligible=eligible,active_stage='G1')
            if mode=='stage_component':
                raw=dict(raw)
                raw['span_logits']=torch.zeros_like(raw['span_logits'])
            data=self._lattice(prepared,raw,family)
            selected=quantity._predict(data,prior.SELECTED_CUTOVER,self.weights,
                                      self.initial,self.continued,{})
            token_spans=self._tokens(selected,data,prepared)
            if native_stage=='G1':
                output={'spans':token_spans,'surface_count':len(token_spans)}
            else:
                output=self.head(x,eligible=eligible,active_stage=native_stage,
                                 inference_spans=token_spans)
            native={(*graph_head.token_span_char_bounds(s,prepared),s['kind'])
                    for s in output['spans']}
            require(native==selected and output['surface_count']==len(selected),
                    'NATIVE_G1_G2_CONSUMPTION_PARITY')
            return {'objects':selected,'native_objects':native,'output':output,
                    'token_spans':token_spans,'mode':mode}
