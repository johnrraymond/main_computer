#!/usr/bin/env python3
"""Three-backbone candidate feature provider for NanoJev.

For every ObjectPath, independently compute terminal hidden output + mean
teacher-forced continuation logP from frozen:
  Qwen/Qwen3-0.6B | EleutherAI/pythia-70m | roneneldan/TinyStories-33M
Then mean-pool paths per semantic candidate and concatenate in that fixed order.
Only the NanoJev head is trainable.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, Sequence

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("nanojev_qwen_logp_base_for_three", TOOLS / "nanojev_code_direct_qwen_logp_train.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load direct Qwen logP base")
_base = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = _base; _spec.loader.exec_module(_base)

# Re-export the mature trainer helpers used by the curriculum scripts.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

PYTHIA_MODEL = "EleutherAI/pythia-70m"
TINYSTORIES_MODEL = "roneneldan/TinyStories-33M"
PYTHIA_WIDTH = 512
TINYSTORIES_WIDTH = 768
AUX_FEATURE_WIDTH = (PYTHIA_WIDTH + 1) + (TINYSTORIES_WIDTH + 1)
TOTAL_FEATURE_WIDTH = (1024 + 1) + AUX_FEATURE_WIDTH
HEAD_PREFIXES = tuple(_base.HEAD_PREFIXES) + ("aux_scalar.", "aux_project.")


def build_direct_model_class(BaseDecisionModel, *, max_answer_tokens: int):
    import torch
    import torch.nn as nn
    Base = _base.build_direct_model_class(BaseDecisionModel, max_answer_tokens=max_answer_tokens)

    class ThreeBackboneDecisionModel(Base):
        def __init__(self, backbone, set_head):
            super().__init__(backbone, set_head)
            self.aux_scalar = nn.Linear(AUX_FEATURE_WIDTH, 1, bias=False)
            self.aux_project = nn.Linear(AUX_FEATURE_WIDTH, 128, bias=False)
            nn.init.zeros_(self.aux_scalar.weight)
            nn.init.zeros_(self.aux_project.weight)
            self.pythia_backbone = None
            self.pythia_output = None
            self.tinystories_backbone = None
            self.tinystories_output = None
            self.pythia_tokenizer = None
            self.tinystories_tokenizer = None

        def attach_auxiliary_backbones(self, *, pythia_lm, pythia_tokenizer, tinystories_lm, tinystories_tokenizer):
            self.pythia_backbone = getattr(pythia_lm, "gpt_neox", None) or pythia_lm.base_model
            self.pythia_output = pythia_lm.get_output_embeddings()
            self.tinystories_backbone = getattr(tinystories_lm, "transformer", None) or tinystories_lm.base_model
            self.tinystories_output = tinystories_lm.get_output_embeddings()
            if self.pythia_output is None or self.tinystories_output is None:
                raise RuntimeError("auxiliary causal LM missing output embeddings")
            self.pythia_tokenizer = pythia_tokenizer
            self.tinystories_tokenizer = tinystories_tokenizer
            for module in (self.pythia_backbone, self.pythia_output, self.tinystories_backbone, self.tinystories_output):
                for p in module.parameters(): p.requires_grad_(False)
                module.eval()

        @staticmethod
        def _encode_aux_prompt_tail(tokenizer, text: str, max_tokens: int) -> list[int]:
            """Keep the prompt tail without asking an aux tokenizer to materialize 32 KiB of tokens.

            Qwen keeps the mature encoder unchanged.  Auxiliary tokenizers have
            much smaller advertised context windows, so tokenize the same bounded
            raw suffix with left truncation enabled at the actual prompt budget.
            """
            if max_tokens <= 0:
                raise ValueError("auxiliary prompt budget must be positive")
            if not text:
                return []
            tail = text[-_base.MAX_PREFIX_TAIL_CHARS:]
            old_side = getattr(tokenizer, "truncation_side", "right")
            try:
                tokenizer.truncation_side = "left"
                return list(tokenizer.encode(
                    tail,
                    add_special_tokens=False,
                    truncation=True,
                    max_length=int(max_tokens),
                ))
            finally:
                tokenizer.truncation_side = old_side

        def _encode_aux_questions(self, questions, tokenizer, max_prompt_tokens: int,
                                  pad_token_id: int, max_positions: int, label: str):
            """Encode the same ObjectPaths in an auxiliary model's native token space.

            The source corpus is bounded with Qwen's tokenizer.  A 128-token Qwen
            answer is not necessarily <=128 Pythia/TinyStories tokens.  Preserve the
            complete semantic answer for native logP and spend whatever context is
            left on the prompt tail.  Only an answer that cannot fit by itself is an
            error.
            """
            paths: list[tuple[list[int], list[int]]] = []
            path_question: list[int] = []
            occurrence_path_index: list[int] = []
            occurrence_candidate_global: list[int] = []
            candidate_question: list[int] = []
            candidate_local: list[int] = []
            candidate_counts: list[int] = []
            path_index_by_key: dict[tuple[int, tuple[int, ...], tuple[int, ...]], int] = {}
            answer_cache: dict[str, list[int]] = {}
            prompt_cache: dict[tuple[str, int], list[int]] = {}
            global_candidate = 0

            for qi, q in enumerate(questions):
                candidate_counts.append(len(q.candidates))
                for ci, candidate in enumerate(q.candidates):
                    candidate_question.append(qi)
                    candidate_local.append(ci)
                    for path in candidate.paths:
                        answer_ids = answer_cache.get(path.answer)
                        if answer_ids is None:
                            answer_ids = list(tokenizer.encode(path.answer, add_special_tokens=False))
                            answer_cache[path.answer] = answer_ids
                        if not answer_ids:
                            raise RuntimeError(f"empty {label} object answer after tokenization: {q.question_id}")
                        if len(answer_ids) >= max_positions:
                            raise RuntimeError(
                                f"{label} object answer cannot fit native context: "
                                f"question={q.question_id} candidate={candidate.candidate_id} "
                                f"answer_tokens={len(answer_ids)} max_positions={max_positions}"
                            )

                        # Reserve the entire answer first.  Prompt context is suffix-oriented,
                        # so reducing only its tail cannot change the candidate completion.
                        prompt_budget = min(int(max_prompt_tokens), max_positions - len(answer_ids))
                        prompt_key = (path.prompt, prompt_budget)
                        prompt_ids = prompt_cache.get(prompt_key)
                        if prompt_ids is None:
                            prompt_ids = self._encode_aux_prompt_tail(tokenizer, path.prompt, prompt_budget)
                            prompt_cache[prompt_key] = prompt_ids
                        if not prompt_ids:
                            raise RuntimeError(f"empty {label} object prompt after tokenization: {q.question_id}")

                        key = (qi, tuple(prompt_ids), tuple(answer_ids))
                        path_index = path_index_by_key.get(key)
                        if path_index is None:
                            path_index = len(paths)
                            path_index_by_key[key] = path_index
                            paths.append((prompt_ids, answer_ids))
                            path_question.append(qi)
                        occurrence_path_index.append(path_index)
                        occurrence_candidate_global.append(global_candidate)
                    global_candidate += 1

            if not paths:
                raise RuntimeError(f"{label} object-stream batch has no paths")

            device = self.scalar.weight.device
            lengths = [len(prompt) + len(answer) for prompt, answer in paths]
            width = max(lengths)
            if width > max_positions:
                raise RuntimeError(f"{label} encoded width {width} exceeds native context {max_positions}")
            tokens = torch.full((len(paths), width), int(pad_token_id), dtype=torch.long, device=device)
            attention = torch.zeros((len(paths), width), dtype=torch.bool, device=device)
            prompt_lengths = torch.empty(len(paths), dtype=torch.long, device=device)
            answer_lengths = torch.empty(len(paths), dtype=torch.long, device=device)
            for i, (prompt_ids, answer_ids) in enumerate(paths):
                seq = prompt_ids + answer_ids
                tokens[i, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
                attention[i, :len(seq)] = True
                prompt_lengths[i] = len(prompt_ids)
                answer_lengths[i] = len(answer_ids)

            return {
                "tokens": tokens,
                "attention": attention,
                "prompt_lengths": prompt_lengths,
                "answer_lengths": answer_lengths,
                "path_question": torch.tensor(path_question, dtype=torch.long, device=device),
                "occurrence_path_index": torch.tensor(occurrence_path_index, dtype=torch.long, device=device),
                "occurrence_candidate_global": torch.tensor(occurrence_candidate_global, dtype=torch.long, device=device),
                "candidate_question": torch.tensor(candidate_question, dtype=torch.long, device=device),
                "candidate_local": torch.tensor(candidate_local, dtype=torch.long, device=device),
                "candidate_counts": candidate_counts,
                "candidate_count": global_candidate,
                "path_occurrence_count": len(occurrence_path_index),
            }

        def _generic_pass(self, backbone, encoded, label):
            max_positions = int(getattr(backbone.config, "max_position_embeddings", encoded["tokens"].shape[1]))
            if encoded["tokens"].shape[1] > max_positions:
                raise RuntimeError(f"{label} path exceeds max positions: {encoded['tokens'].shape[1]} > {max_positions}")
            with torch.no_grad():
                out = backbone(input_ids=encoded["tokens"], attention_mask=encoded["attention"], use_cache=False)
                return out.last_hidden_state

        def _generic_candidate_vectors(self, hidden, encoded, output_weight):
            width = int(hidden.shape[-1])
            terminal = encoded["prompt_lengths"] + encoded["answer_lengths"] - 1
            row = torch.arange(hidden.shape[0], device=hidden.device)
            path_hidden = hidden[row, terminal]
            path_logp = _base.continuation_mean_logp_from_hidden(
                hidden=hidden, tokens=encoded["tokens"], prompt_lengths=encoded["prompt_lengths"],
                answer_lengths=encoded["answer_lengths"], output_weight=output_weight,
            ).to(path_hidden.dtype)
            path_features = torch.cat([path_hidden, path_logp.unsqueeze(-1)], dim=-1)
            occurrence = path_features.index_select(0, encoded["occurrence_path_index"])
            candidate = encoded["occurrence_candidate_global"]
            count = int(encoded["candidate_count"])
            sums = path_features.new_zeros((count, width + 1)).index_add(0, candidate, occurrence)
            counts = torch.bincount(candidate, minlength=count).to(path_features.dtype)
            return sums / counts.clamp_min(1).unsqueeze(-1)

        def _all_candidate_vectors(self, questions, qwen_tokenizer, max_prompt_tokens, qwen_pad):
            if self.pythia_backbone is None or self.tinystories_backbone is None:
                raise RuntimeError("auxiliary backbones not attached")
            qe = self._encode_questions(questions, qwen_tokenizer, max_prompt_tokens, qwen_pad)
            qh = self._qwen_pass(qe)
            qv = self._candidate_vectors(qh, qe)
            del qh
            parts = [qv]
            for label, backbone, output, tok in (
                ("pythia", self.pythia_backbone, self.pythia_output, self.pythia_tokenizer),
                ("tinystories", self.tinystories_backbone, self.tinystories_output, self.tinystories_tokenizer),
            ):
                pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
                if pad is None:
                    raise RuntimeError(f"{label} tokenizer has no pad/eos token")
                max_positions = int(getattr(backbone.config, "max_position_embeddings", 0))
                if max_positions <= 1:
                    raise RuntimeError(f"{label} backbone has invalid max_position_embeddings={max_positions}")
                e = self._encode_aux_questions(
                    questions, tok, max_prompt_tokens, int(pad), max_positions, label
                )
                # Candidate/path geometry is semantic and must agree despite independent tokenization.
                for key in ("candidate_count", "candidate_counts", "path_occurrence_count"):
                    if e[key] != qe[key] if not hasattr(e[key], "shape") else False:
                        raise RuntimeError(f"{label} semantic geometry mismatch: {key}")
                h = self._generic_pass(backbone, e, label)
                parts.append(self._generic_candidate_vectors(h, e, output.weight))
                del h, e
            out = torch.cat(parts, dim=-1)
            if out.shape[-1] != TOTAL_FEATURE_WIDTH:
                raise RuntimeError(f"three-backbone feature width {out.shape[-1]} != {TOTAL_FEATURE_WIDTH}")
            return qe, out

        def _score_dense_candidate_vectors(self, candidate_features, valid):
            if candidate_features.shape[-1] != TOTAL_FEATURE_WIDTH:
                raise RuntimeError(f"candidate feature width {candidate_features.shape[-1]} != {TOTAL_FEATURE_WIDTH}")
            q = candidate_features[..., :1025]
            aux = candidate_features[..., 1025:]
            raw_hidden = q[..., :1024]
            logp = q[..., 1024:1025]
            h = self.norm(raw_hidden)
            z = (self.scalar(h) + self.logp_scalar(logp.to(h.dtype)) + self.aux_scalar(aux.to(h.dtype))).squeeze(-1).float()
            kmax = candidate_features.shape[1]
            log_k = valid.sum(-1).float().log()[:, None, None].expand(-1, kmax, 1)
            u = self.set_project(torch.cat([h, log_k.to(h.dtype)], dim=-1))
            u = u + self.logp_project(logp.to(h.dtype)) + self.aux_project(aux.to(h.dtype))
            mixed, _ = self.set_attention(u, u, u, key_padding_mask=~valid, need_weights=False)
            z = z + self.set_output(torch.tanh(u + mixed)).squeeze(-1).float()
            return z.masked_fill(~valid, -1e9), valid

        def cache_object_questions(self, questions, tokenizer, max_prompt_tokens: int, pad_token_id: int):
            encoded, vectors = self._all_candidate_vectors(questions, tokenizer, max_prompt_tokens, pad_token_id)
            cached=[]; offset=0
            for q in questions:
                count=len(q.candidates)
                cached.append(CachedObjectQuestion(q.question_id, q.task, tuple(c.candidate_id for c in q.candidates), int(q.gold_index), vectors[offset:offset+count].detach().float().cpu().contiguous()))
                offset += count
            return cached

        def forward_object_questions(self, questions, tokenizer, max_prompt_tokens: int, pad_token_id: int):
            encoded, vectors = self._all_candidate_vectors(questions, tokenizer, max_prompt_tokens, pad_token_id)
            return self._score_candidate_vectors(vectors, encoded, questions)

        def score_cached_questions(self, questions):
            if not questions: raise RuntimeError("cannot score empty cached batch")
            device=self.scalar.weight.device; kmax=max(len(q.candidate_ids) for q in questions)
            h=torch.zeros((len(questions),kmax,TOTAL_FEATURE_WIDTH),dtype=torch.float32,device=device)
            valid=torch.zeros((len(questions),kmax),dtype=torch.bool,device=device)
            for qi,q in enumerate(questions):
                h[qi,:len(q.candidate_ids)] = q.candidate_vectors.to(device=device,dtype=h.dtype,non_blocking=True)
                valid[qi,:len(q.candidate_ids)] = True
            return self._score_dense_candidate_vectors(h,valid)

    ThreeBackboneDecisionModel.__name__ = "ThreeBackboneLogPDecisionModel"
    return ThreeBackboneDecisionModel


def materialize_cached_questions(*, model, tokenizer, questions: Sequence, max_prompt_tokens: int,
                                 pad_token_id: int, precision: str, qwen_batch_questions: int):
    import time, torch
    cached=[]; started=time.perf_counter()
    model.eval(); model.backbone.eval(); model.pythia_backbone.eval(); model.tinystories_backbone.eval()
    with torch.no_grad():
        for start in range(0,len(questions),qwen_batch_questions):
            batch=list(questions[start:start+qwen_batch_questions])
            with torch.autocast("cuda",dtype=torch.bfloat16,enabled=precision=="bf16"):
                cached.extend(model.cache_object_questions(batch,tokenizer,max_prompt_tokens,pad_token_id))
    return cached,{"questions":len(cached),"candidate_feature_width":TOTAL_FEATURE_WIDTH,"backbones":["Qwen/Qwen3-0.6B",PYTHIA_MODEL,TINYSTORIES_MODEL],"seconds":time.perf_counter()-started}


def load_own_checkpoint(model, path: Path) -> None:
    """Load a three-backbone head checkpoint/cutover artifact strictly."""
    from safetensors.torch import load_file
    path = Path(path)
    weights = load_file(str(path / "head.safetensors"), device="cpu")
    expected = {k for k in model.state_dict() if k.startswith(HEAD_PREFIXES)}
    if set(weights) != expected:
        raise RuntimeError(
            f"three-backbone head mismatch: missing={sorted(expected-set(weights))} extra={sorted(set(weights)-expected)}"
        )
    incompatible = model.load_state_dict(weights, strict=False)
    bad_missing = [k for k in incompatible.missing_keys if k.startswith(HEAD_PREFIXES)]
    if bad_missing or incompatible.unexpected_keys:
        raise RuntimeError(f"three-backbone checkpoint load mismatch: missing={bad_missing} unexpected={incompatible.unexpected_keys}")


def checkpoint_head_state(model):
    return {
        key: value.detach().cpu().contiguous()
        for key, value in model.state_dict().items()
        if key.startswith(HEAD_PREFIXES)
    }
