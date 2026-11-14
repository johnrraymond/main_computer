"""One shared, non-autoregressive GAMER architecture for all seven curriculum stages.

Every head is constructed on day one. Stage gates determine which losses can
update parameters and which outputs are allowed into execution. Inference has
NO regex/lexical/game family router, no oracle graph, and no token generator.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F
from tools.nanojev_gamer_v1_graph_contract import (
    SPAN_KINDS, NODE_KINDS, BINDINGS, RELATIONS, OPERATORS, OBJECTIVES,
    MAX_OPERANDS, STAGES, empty_graph, exact_number,
)

# G1 scores complete object spans jointly.  The maximum span length is a
# tunable compute bound, never an action-name/word inventory.
SPAN_CLASS_COUNT = 1 + len(SPAN_KINDS)  # 0 = not an object
DEFAULT_MAX_SPAN_TOKENS = 24
BIND_CLASSES = ('none',) + BINDINGS
REL_CLASSES = ('none',) + RELATIONS
OP_CLASSES = ('none',) + OPERATORS


def decode_greedy_spans(span_logits, span_valid, *, confidence=0.50):
    """Original decoder, retained only for controlled read-only comparison."""
    if span_logits.ndim != 3 or span_logits.shape[-1] != SPAN_CLASS_COUNT or \
            span_valid.shape != span_logits.shape[:2]:
        raise ValueError('GAMER_GRAPH_SPAN_SCORE_SHAPE')
    if not 0 <= confidence <= 1:
        raise ValueError('GAMER_GRAPH_SPAN_CONFIDENCE')
    with torch.no_grad():
        probs = span_logits.softmax(dim=-1)
        score, label = probs[..., 1:].max(dim=-1)
        eligible = span_valid.bool() & (score >= confidence) & (score > probs[..., 0])
        candidates = []
        for start, length_index in eligible.nonzero(as_tuple=False).tolist():
            end = start + length_index
            margin = (span_logits[start, length_index, label[start, length_index]+1]
                      - span_logits[start, length_index, 0]).item()
            candidates.append((margin, score[start, length_index].item(),
                               end - start, start, end, int(label[start, length_index])))
        candidates.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
        accepted = []
        occupied = set()
        for _margin, _confidence, _width, first, last, kind_index in candidates:
            if any(t in occupied for t in range(first, last+1)):
                continue
            accepted.append({'kind': SPAN_KINDS[kind_index],
                             'token_first': first, 'token_last': last})
            occupied.update(range(first, last+1))
            if len(accepted) > 128:
                raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
        return sorted(accepted, key=lambda x: x['token_first'])


def _optimal_span_indices(span_logits, span_valid, *, confidence=0.50,
                          gold=None, cost_augmented=False):
    """Exact max-sum non-overlapping typed spans; O(T*W*K + T*W).

    Gold labels are optional and used ONLY for loss-augmented training.
    They never enter ordinary decoding, query or evaluation.
    Returns (start, width_index, class_index) tuples.
    """
    if span_logits.ndim != 3 or span_logits.shape[-1] != SPAN_CLASS_COUNT or \
            span_valid.shape != span_logits.shape[:2]:
        raise ValueError('GAMER_GRAPH_SPAN_SCORE_SHAPE')
    if not 0 <= confidence <= 1:
        raise ValueError('GAMER_GRAPH_SPAN_CONFIDENCE')
    if cost_augmented and gold is None:
        raise ValueError('GAMER_GRAPH_LOSS_AUGMENTATION_REQUIRES_GOLD')
    with torch.no_grad():
        logits=span_logits.detach().float().cpu()
        valid=span_valid.detach().bool().cpu().numpy()
        margins=(logits[...,1:]-logits[...,0:1]).numpy()
        probs=logits.softmax(-1)[...,1:].numpy() if not cost_augmented else None
        size,width=valid.shape
        by_end=[[] for _ in range(size+1)]
        for first in range(size):
            for length_index in range(min(width,size-first)):
                if not valid[first,length_index]:
                    continue
                if cost_augmented:
                    # Loss: false positives + false negatives.  For a selected
                    # correct span subtract one, otherwise add one.  The
                    # omitted constant len(gold) is added in stage_loss.
                    options=[(float(margins[first,length_index,k-1])+
                              (-1. if (first,length_index,k) in gold else 1.),k)
                             for k in range(1,SPAN_CLASS_COUNT)]
                    score,label=max(options,key=lambda x:x[0])
                else:
                    k=int(margins[first,length_index].argmax())
                    label=k+1
                    score=float(margins[first,length_index,k])
                    if score <= 0 or probs[first,length_index,k] < confidence:
                        continue
                by_end[first+length_index+1].append((first,length_index,label,score))
        dp=[0.]*(size+1)
        previous=[None]*(size+1)
        for end in range(1,size+1):
            dp[end]=dp[end-1]
            previous[end]=(end-1,None)
            for first,length_index,label,score in by_end[end]:
                candidate=dp[first]+score
                if candidate > dp[end]+1e-8:
                    dp[end]=candidate
                    previous[end]=(first,(first,length_index,label))
        chosen=[];at=size
        while at>0:
            prev,item=previous[at]
            if item is not None:
                chosen.append(item)
            at=prev
        return list(reversed(chosen))


def decode_neural_spans(span_logits, span_valid, *, confidence=0.50):
    """Globally select neural object spans, with no text or game-specific rules."""
    selection=_optimal_span_indices(span_logits,span_valid,confidence=confidence)
    if len(selection)>128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    return [{'kind':SPAN_KINDS[label-1], 'token_first':first,
             'token_last':first+length_index}
            for first,length_index,label in selection]


class GamerGraphHead(nn.Module):
    """General typed-graph constructor, initialized in full from the outset.

    G1 scores complete surface object spans. G2 binds referents. G3 predicts relations.
    G4 adds implicit operation nodes/operand pointers and numeric grounding.
    G5 selects the objective/candidates. G6 learns and reads latent memory.
    G7 jointly verifies end-to-end use. None generates language tokens.
    """
    def __init__(self, hidden_size=1024, width=192, layers=1,
                 latent_slots=16, memory_slots=32, max_operands=MAX_OPERANDS,
                 max_span_tokens=DEFAULT_MAX_SPAN_TOKENS):
        super().__init__()
        if hidden_size < 1 or width < 16 or width % 4 or layers < 1 or latent_slots < 1 or memory_slots < 1 or not 1 <= max_operands <= MAX_OPERANDS or not 1 <= max_span_tokens <= 128:
            raise ValueError('GAMER_GRAPH_INVALID_DIMENSIONS')
        self.hidden_size = hidden_size
        self.width = width
        self.layers = layers
        self.latent_slots_count = latent_slots
        self.memory_slots_count = memory_slots
        self.max_operands = max_operands
        self.max_span_tokens = max_span_tokens
        self.project = nn.Sequential(nn.LayerNorm(hidden_size), nn.Linear(hidden_size, width))
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=width, nhead=4,
                dim_feedforward=width * 2, dropout=0.0, activation='gelu',
                batch_first=True), num_layers=layers, enable_nested_tensor=False)
        # Joint start/end/whole-span semantic scorer; unlike independent BIO
        # tags, interior subwords cannot become separate objects by accident.
        self.span_start = nn.Linear(width, width)
        self.span_end = nn.Linear(width, width)
        self.span_pool = nn.Linear(width, width)
        self.span_width_embed = nn.Embedding(max_span_tokens, width)
        self.span_classifier = nn.Linear(width, SPAN_CLASS_COUNT)
        self.latent_query = nn.Parameter(torch.randn(latent_slots, width) * .02)
        self.latent_attention = nn.MultiheadAttention(width, num_heads=4, batch_first=True)
        self.latent_presence = nn.Linear(width, 2)
        self.latent_kind = nn.Linear(width, len(NODE_KINDS))
        self.pair_projection = nn.Linear(width * 4, width)
        self.binding = nn.Linear(width, len(BIND_CLASSES))
        self.relationship = nn.Linear(width, len(REL_CLASSES))
        self.operator = nn.Linear(width, len(OP_CLASSES))
        self.operand_query = nn.Linear(width, width * max_operands)
        self.operand_key = nn.Linear(width, width)
        self.operand_stop = nn.Parameter(torch.zeros(max_operands))
        self.objective = nn.Linear(width, len(OBJECTIVES))
        self.objective_candidate = nn.Linear(width * 2, 1)
        self.memory_query = nn.Linear(width, width)
        self.memory_key = nn.Linear(width, width)
        self.memory_value = nn.Linear(width, width)
        self.memory_write = nn.GRUCell(width, width)
        self.memory_write_gate = nn.Linear(width, 1)

    def encode(self, hidden, memory=None):
        if hidden.ndim != 2 or hidden.shape[1] != self.hidden_size or hidden.shape[0] < 1:
            raise ValueError('GAMER_GRAPH_HIDDEN_SHAPE')
        tokens = self.encoder(self.project(hidden.float()).unsqueeze(0)).squeeze(0)
        global_state = tokens[-1]
        if memory is not None:
            if memory.ndim != 2 or memory.shape != (self.memory_slots_count, self.width):
                raise ValueError('GAMER_GRAPH_MEMORY_SHAPE')
            query = self.memory_query(global_state)
            attention = torch.softmax(self.memory_key(memory) @ query / self.width ** .5, dim=0)
            context = attention @ self.memory_value(memory)
            global_state = global_state + context
            tokens = tokens + context.unsqueeze(0)
        return tokens, global_state

    def scored_spans(self, tokens, eligible):
        """Joint (start,end,type) scores in O(T*max_span_tokens).

        Tokenization is only the coordinate system.  Contextual vectors from
        the complete prompt determine whether a candidate is an entity.
        """
        size, width = tokens.shape
        if eligible is None:
            raise ValueError('GAMER_GRAPH_ELIGIBILITY_REQUIRED')
        eligible = torch.as_tensor(eligible, device=tokens.device, dtype=torch.bool)
        if eligible.shape != (size,):
            raise ValueError('GAMER_GRAPH_ELIGIBILITY_SHAPE')
        starts = torch.arange(size, device=tokens.device)[:, None]
        lengths = torch.arange(1, self.max_span_tokens + 1,
                               device=tokens.device)[None, :]
        ends = starts + lengths - 1
        clamped_ends = ends.clamp(max=size - 1)
        prefix = torch.cat((tokens.new_zeros((1, width)),
                            tokens.cumsum(dim=0)), dim=0)
        pooled = (prefix[(ends + 1).clamp(max=size)] - prefix[starts]) / lengths.unsqueeze(-1)
        span_repr = torch.tanh(
            self.span_start(tokens)[:, None, :] +
            self.span_end(tokens[clamped_ends]) +
            self.span_pool(pooled) +
            self.span_width_embed((lengths - 1).expand(size, -1)))
        logits = self.span_classifier(span_repr)
        excluded = (~eligible).long()
        excluded_prefix = torch.cat((excluded.new_zeros(1),
                                     excluded.cumsum(0)), dim=0)
        valid = (ends < size) & (excluded_prefix[(ends + 1).clamp(max=size)]
                                 == excluded_prefix[starts])
        return logits, valid

    def _node_readouts(self, tokens, global_state, spans):
        representations = []
        for span in spans:
            a, b = span['token_first'], span['token_last']
            if not (0 <= a <= b < len(tokens)):
                raise ValueError('GAMER_GRAPH_SPAN_OUT_OF_BOUNDS')
            representations.append(tokens[a:b+1].mean(0))
        latent, _ = self.latent_attention(
            self.latent_query.unsqueeze(0), tokens.unsqueeze(0), tokens.unsqueeze(0),
            need_weights=False)
        latent = latent.squeeze(0) + global_state.unsqueeze(0)
        return representations, latent

    def forward(self, hidden, *, eligible=None, training_spans=None, memory=None, active_stage='G7'):
        tokens, global_state = self.encode(hidden, memory=memory)
        span_logits, span_valid = self.scored_spans(tokens, eligible)
        if active_stage not in STAGES:
            raise ValueError('GAMER_GRAPH_UNKNOWN_STAGE')
        if active_stage == 'G1':
            # No random early span proposals, O(N^2) pair matrix, or other
            # untrained downstream computations during the object stage.
            return {'span_logits':span_logits, 'span_valid':span_valid, 'global_state':global_state}
        if training_spans is None:
            spans = decode_neural_spans(span_logits, span_valid)
        else:
            spans = training_spans
        if len(spans) > 128:
            raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
        surface_nodes, latent = self._node_readouts(tokens, global_state, spans)
        # All latent slots exist in the graph network even when gates prohibit
        # them from being decoded. Teacher-forced node slots are never inference inputs.
        values = torch.stack(surface_nodes + list(latent), 0) if surface_nodes else latent
        n = len(values)
        left = values.unsqueeze(1).expand(n, n, self.width)
        right = values.unsqueeze(0).expand(n, n, self.width)
        pair = F.gelu(self.pair_projection(torch.cat((left, right,
                                                      left * right, left - right), -1)))
        op_queries = self.operand_query(values).reshape(n, self.max_operands, self.width)
        pointers = torch.einsum('npd,md->npm', op_queries, self.operand_key(values)) / self.width ** .5
        pointers = torch.cat((pointers,
            self.operand_stop.to(pointers).view(1, -1, 1).expand(n, -1, 1)), -1)
        objective_choices = self.objective_candidate(torch.cat((values,
                   global_state.expand(n, -1)), -1)).squeeze(-1)
        return {'span_logits': span_logits, 'span_valid': span_valid, 'spans': spans,
                'surface_count': len(surface_nodes), 'node_vectors': values,
                'slot_presence_logits': self.latent_presence(latent),
                'slot_kind_logits': self.latent_kind(latent),
                'binding_logits': self.binding(pair),
                'relationship_logits': self.relationship(pair),
                'operator_logits': self.operator(values),
                'operand_logits': pointers,
                'objective_logits': self.objective(global_state),
                'candidate_logits': objective_choices,
                'memory_write_proposal': self.memory_write(global_state, global_state),
                'memory_write_probability': torch.sigmoid(self.memory_write_gate(global_state)),
                'global_state': global_state}

    def updated_memory(self, old, output, *, authorized=False, slot=None):
        """No memory mutation before G6; explicit acquisition only."""
        if not authorized:
            raise PermissionError('GAMER_GRAPH_MEMORY_G6_LOCKED')
        if old.shape != (self.memory_slots_count, self.width):
            raise ValueError('GAMER_GRAPH_MEMORY_SHAPE')
        if type(slot) is not int or not 0 <= slot < self.memory_slots_count:
            raise ValueError('GAMER_GRAPH_MEMORY_SLOT')
        new = old.clone()
        new[slot] = self.memory_write(output['global_state'], old[slot])
        return new


def _edge_logits_to_predictions(logits, classes, threshold=0.5):
    indices = logits.argmax(-1).tolist()
    return [(i, j, classes[label])
            for i, row in enumerate(indices) for j, label in enumerate(row)
            if label > 0 and torch.softmax(logits[i, j], dim=-1)[label].item() >= threshold]


def token_span_char_bounds(span, prepared, *, normalize_whitespace=True):
    """Map tokenizer offsets back into the original prompt's character space.

    Qwen's fast tokenizer may encode a word together with a leading space.
    Joint neural spans identify tokens covering the object, so a token can
    precede the author-marked object by one whitespace character. Strip only
    boundary whitespace; never infer an action, quantity, or game from text.
    """
    offsets = prepared['offsets']
    start = prepared['prompt_start']
    prompt = prepared['prompt']
    first, last = span['token_first'], span['token_last']
    if not (0 <= first <= last < len(offsets)):
        return None
    a = offsets[first][0] - start
    b = offsets[last][1] - start
    if not (0 <= a < b <= len(prompt)):
        return None
    if normalize_whitespace:
        while a < b and prompt[a].isspace():
            a += 1
        while b > a and prompt[b - 1].isspace():
            b -= 1
    return (a, b) if a < b else None


def decode_graph(output, prepared, *, unlocked='G1', confidence=0.50):
    """Build a partial IR from neural predictions; no interpretation of names.

    Later components CANNOT silently fill missing graph pieces. Until G7
    certification, this returns a partial graph, never a strategic decision.
    """
    if unlocked not in STAGES:
        raise ValueError('GAMER_GRAPH_UNKNOWN_UNLOCKED_STAGE')
    stage = STAGES.index(unlocked)
    eligible = prepared['eligible'].tolist()
    spans = output['spans']
    prompt = prepared['prompt']
    graph = empty_graph()
    surfaces = []
    selected_indices = []
    for i, span in enumerate(spans):
        bounds = token_span_char_bounds(span, prepared)
        if bounds is None:
            continue
        a, b = bounds
        node = {'id': f'n{len(surfaces)}', 'kind': span['kind'], 'span': [a, b]}
        if stage >= 3 and node['kind'] == 'quantity':
            # General numeric value conversion only AFTER neural span selection;
            # never a regex/semantic or game-specific number extractor.
            try: node['value'] = str(exact_number(prompt[a:b]))
            except ValueError: pass   # unknown numeric expression, fail closed downstream
        graph['nodes'].append(node)
        surfaces.append({'id': node['id'], 'kind': node['kind'], 'surface': prompt[a:b], 'span': [a,b]})
        selected_indices.append(i)
    # Latent computational objects are neural proposals, not game-fixed slots.
    if stage >= 3:
        presence = output['slot_presence_logits'].softmax(-1)[:, 1]
        slot_kinds = output['slot_kind_logits'].argmax(-1).tolist()
        for i, prob in enumerate(presence.tolist()):
            if prob >= confidence and len(graph['nodes']) < 256:
                node = {'id': f'n{len(graph["nodes"])}', 'kind': NODE_KINDS[slot_kinds[i]]}
                if node['kind'] == 'operation':
                    label = int(output['operator_logits'][output['surface_count']+i].argmax())
                    if label:
                        node['operator'] = OP_CLASSES[label]
                graph['nodes'].append(node)
                selected_indices.append(output['surface_count'] + i)
    if stage >= 1 and selected_indices:
        bind_logits = output['binding_logits']
        for a in selected_indices:
            for b in selected_indices:
                if a != b:
                    label = int(bind_logits[a,b].argmax())
                    if label and bind_logits[a,b].softmax(-1)[label].item() >= confidence:
                        graph['edges'].append({'src': f'n{selected_indices.index(a)}',
                                              'dst': f'n{selected_indices.index(b)}',
                                              'type': BIND_CLASSES[label]})
    if stage >= 2 and selected_indices:
        rel_logits = output['relationship_logits']
        for i, a in enumerate(selected_indices):
            for j, b in enumerate(selected_indices):
                if a != b:
                    label = int(rel_logits[a,b].argmax())
                    if label and rel_logits[a,b].softmax(-1)[label].item() >= confidence:
                        graph['edges'].append({'src':f'n{i}','dst':f'n{j}',
                                               'type':REL_CLASSES[label]})
    if stage >= 3:
        for i, idx in enumerate(selected_indices):
            if graph['nodes'][i]['kind'] != 'operation':
                continue
            for arg in range(output['operand_logits'].shape[1]):
                choices = output['operand_logits'][idx,arg]
                index = int(choices.argmax())
                if index == output['operand_logits'].shape[-1]-1:
                    break
                if index in selected_indices:
                    target = selected_indices.index(index)
                    graph['edges'].append({'src':f'n{i}','dst':f'n{target}',
                                           'type':'operand','position':arg})
    if stage >= 4:
        objective = OBJECTIVES[int(output['objective_logits'].argmax())]
        graph['objective'] = {'kind':objective,
                 'candidates':[f'n{i}' for i, idx in enumerate(selected_indices)
                                if graph['nodes'][i]['kind']=='action'
                                and output['candidate_logits'][idx].sigmoid().item() >= confidence]}
    return graph, surfaces
