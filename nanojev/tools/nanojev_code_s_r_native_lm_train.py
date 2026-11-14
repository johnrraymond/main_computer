#!/usr/bin/env python3
"""Fresh headless NanoJev experiment: train S+R through Qwen's native LM answer surface.

Purpose
-------
This is a deliberately new lineage.  It does NOT inherit a NanoJev decision head,
S token, R register, optimizer, or RNG state from an older experiment.

Architecture (one question at a time):

    prompt + fresh learned S  -> frozen Qwen sensor pass -> terminal hidden h1
    h1 -> fresh learned sparse R1 router
    prompt + learned (S + R1) -> frozen Qwen decision pass -> terminal hidden h2
    h2 dot frozen Qwen LM rows for A/B[/C/D] -> cross entropy

There is no NanoJev decision head, no R2, no recurrent/third pass, and no task-
specific classifier.  Qwen and the selected LM rows stay frozen.  Only S, its
strength, the shared K-wide control bank, and the R1 router are trainable.

Every existing task is projected into the same finite answer alphabet.  The
semantic-to-letter mapping is randomized during training so the controller
cannot solve the curriculum by learning a permanent A/B/C/D prior.

Default curriculum follows the current six-task lane:
    20% ordered legacy continuation
     5% mutation preservation
     5% AST equivalence
    15% direct A/B/C/NONE consensus
    20% relation-balanced pairwise triad
    35% dictionary definition matching
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
from typing import Any, Sequence


SCHEMA = "main-computer-nanojev-s-r-native-lm-experiment-v1"
STATE_SCHEMA = "main-computer-nanojev-s-r-native-lm-state-v1"
CONFIG_SCHEMA = "main-computer-nanojev-s-r-native-lm-config-v1"
CHECKPOINT_SCHEMA = "main-computer-nanojev-s-r-native-lm-checkpoint-v1"

DEFAULT_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_s_r_native_lm_v1"
DEFAULT_LEGACY_EXPERIMENT = r"C:\Users\subsi\NanoJev\runs\main_computer_code_lexeme_v1"
DEFAULT_DICTIONARY_CACHE = r"C:\Users\subsi\NanoJev\cache\english-wordnet-2025.zip"
DEFAULT_DICTIONARY_URL = "https://en-word.net/static/english-wordnet-2025.zip"

TASK_ORDER = ("legacy", "mutation", "ast", "consensus", "triad", "dictionary")
DEFAULT_CURRICULUM = {
    "legacy": 20.0,
    "mutation": 5.0,
    "ast": 5.0,
    "consensus": 15.0,
    "triad": 20.0,
    "dictionary": 35.0,
}
ANSWER_SYMBOLS = ("A", "B", "C", "D")
DEFAULT_CONTROL_SPACE_SIZE = 1000
DEFAULT_REGISTER_RANK = 32
DEFAULT_STRENGTH_INIT = 0.25
DEFAULT_SEED_OFFSET = 91000
ORDERED_LEGACY_DEV_PAIRS = 64
ORDERED_LEGACY_DEV_SEED_OFFSET = 78500


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def stable_seed(*parts: Any) -> int:
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def largest_remainder_budgets(total: int, weights: dict[str, float]) -> dict[str, int]:
    if total <= 0:
        raise ValueError("total must be positive")
    if set(weights) != set(TASK_ORDER):
        raise ValueError("weights must cover the six current tasks exactly")
    if abs(sum(float(weights[t]) for t in TASK_ORDER) - 100.0) > 1e-9:
        raise ValueError("curriculum percentages must sum to 100")
    exact = {task: total * float(weights[task]) / 100.0 for task in TASK_ORDER}
    out = {task: int(math.floor(exact[task])) for task in TASK_ORDER}
    remaining = total - sum(out.values())
    order = sorted(TASK_ORDER, key=lambda t: (exact[t] - out[t], -TASK_ORDER.index(t)), reverse=True)
    for task in order[:remaining]:
        out[task] += 1
    return out


def next_task_mix(*, weights: dict[str, float], credits: dict[str, float], slots: int) -> tuple[list[str], dict[str, float]]:
    if slots <= 0:
        raise ValueError("slots must be positive")
    acc = {task: float(credits.get(task, 0.0)) for task in TASK_ORDER}
    tasks: list[str] = []
    for _ in range(slots):
        for task in TASK_ORDER:
            acc[task] += float(weights[task])
        chosen = max(TASK_ORDER, key=lambda t: (acc[t], float(weights[t]), -TASK_ORDER.index(t)))
        acc[chosen] -= 100.0
        tasks.append(chosen)
    return tasks, acc


def record_question(record: dict) -> tuple[str, dict, list[str], str]:
    questions = record.get("questions")
    gold = record.get("gold")
    if not isinstance(questions, dict) or len(questions) != 1:
        raise RuntimeError(f"record must contain exactly one question: {record.get('id')}")
    if not isinstance(gold, dict) or len(gold) != 1:
        raise RuntimeError(f"record must contain exactly one gold target: {record.get('id')}")
    question_id, question = next(iter(questions.items()))
    if question_id not in gold:
        raise RuntimeError(f"gold/question mismatch: {record.get('id')}: {question_id}")
    criteria = question.get("criteria")
    if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 4:
        raise RuntimeError(f"question needs 2-4 criteria: {record.get('id')}")
    labels = [str(label) for label in criteria.keys()]
    raw_gold = gold[question_id]
    if isinstance(raw_gold, bool):
        gold_label = "true" if raw_gold else "false"
    else:
        gold_label = str(raw_gold)
    if gold_label not in labels:
        raise RuntimeError(
            f"gold label {gold_label!r} absent from criteria {labels}: {record.get('id')}"
        )
    return question_id, question, labels, gold_label


def build_answer_prompt(record: dict, *, permutation_seed: int | None = None, rng: random.Random | None = None) -> tuple[str, int, tuple[str, ...]]:
    """Map one semantic question onto A/B/C/D and return prompt + gold answer index.

    Training passes an RNG, so every presentation may use a different semantic-to-letter
    mapping.  Evaluation passes a stable seed so the ruler is deterministic.
    """
    if (permutation_seed is None) == (rng is None):
        raise ValueError("provide exactly one of permutation_seed or rng")
    _qid, question, labels, gold_label = record_question(record)
    semantic = labels.copy()
    local_rng = rng if rng is not None else random.Random(int(permutation_seed))
    local_rng.shuffle(semantic)
    symbols = ANSWER_SYMBOLS[: len(semantic)]
    criteria = question["criteria"]
    option_lines = [f"{symbol}: {criteria[label]}" for symbol, label in zip(symbols, semantic)]
    gold_index = semantic.index(gold_label)
    allowed = ", ".join(symbols)
    prompt = (
        str(record["state"]).rstrip()
        + "\n\nQuestion:\n"
        + str(question["instructions"]).strip()
        + "\n\nAnswer options:\n"
        + "\n".join(option_lines)
        + f"\n\nReturn exactly one letter from: {allowed}.\nAnswer:"
    )
    return prompt, gold_index, symbols


def answer_token_ids(tokenizer) -> tuple[int, int, int, int]:
    ids = []
    for symbol in ANSWER_SYMBOLS:
        encoded = tokenizer.encode(" " + symbol, add_special_tokens=False)
        if len(encoded) != 1:
            raise RuntimeError(
                f"canonical answer symbol {symbol!r} is not a single token after a space: {encoded}"
            )
        ids.append(int(encoded[0]))
    if len(set(ids)) != 4:
        raise RuntimeError(f"canonical answer token ids are not distinct: {ids}")
    return tuple(ids)  # type: ignore[return-value]


def module_sha256(module) -> str:
    h = hashlib.sha256()
    for name, tensor in module.state_dict().items():
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(tensor.detach().float().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()


def controller_state_dict_cpu(controller) -> dict[str, Any]:
    return {name: tensor.detach().cpu().contiguous() for name, tensor in controller.state_dict().items()}


def save_rng(path: Path) -> None:
    import torch
    payload = {
        "python": random.getstate(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
    }
    torch.save(payload, path)


def load_rng(path: Path) -> None:
    import torch
    payload = torch.load(path, map_location="cpu", weights_only=False)
    random.setstate(payload["python"])
    torch.set_rng_state(payload["torch_cpu"])
    if torch.cuda.is_available() and payload.get("torch_cuda") is not None:
        torch.cuda.set_rng_state_all(payload["torch_cuda"])


def build_controller_class(*, control_space_size: int, register_rank: int, strength_init: float, active_epsilon: float):
    import torch
    from torch import nn
    import torch.nn.functional as F

    class SRController(nn.Module):
        def __init__(self, hidden_size: int, embedding_rms: float):
            super().__init__()
            self.hidden_size = int(hidden_size)
            self.control_space_size = int(control_space_size)
            self.register_rank = int(register_rank)
            self.active_epsilon = float(active_epsilon)
            self.s = nn.Parameter(torch.empty(self.hidden_size))
            self.strength_logit = nn.Parameter(
                torch.tensor(math.log(float(strength_init) / (1.0 - float(strength_init))))
            )
            self.control_bank = nn.Parameter(torch.empty(self.control_space_size, self.hidden_size))
            self.router_down = nn.Linear(self.hidden_size, self.register_rank, bias=False)
            self.router_coeff = nn.Linear(self.register_rank, self.control_space_size, bias=False)
            self.register_buffer("embedding_rms", torch.tensor(float(embedding_rms)), persistent=True)
            self.last_coefficients = None
            self.last_regularization: dict[str, Any] = {}
            self.reset_parameters()

        def reset_parameters(self) -> None:
            if self.control_space_size > self.hidden_size:
                raise RuntimeError(
                    f"control space K={self.control_space_size} exceeds hidden size H={self.hidden_size}; "
                    "fresh orthonormal row initialization requires K <= H"
                )
            with torch.no_grad():
                # A nonzero fresh S avoids a dead zero-normalization operating point.
                self.s.normal_(mean=0.0, std=0.02)
                q, _ = torch.linalg.qr(
                    torch.randn(self.hidden_size, self.control_space_size, dtype=self.control_bank.dtype),
                    mode="reduced",
                )
                self.control_bank.copy_(q.transpose(0, 1).contiguous())
            self.router_down.reset_parameters()
            nn.init.zeros_(self.router_coeff.weight)

        def normalized_bank(self):
            return F.normalize(self.control_bank.float(), p=2.0, dim=1).to(self.control_bank.dtype)

        def orthogonality_penalty(self):
            bank = F.normalize(self.control_bank.float(), p=2.0, dim=1)
            gram = bank @ bank.transpose(0, 1)
            eye = torch.eye(self.control_space_size, device=gram.device, dtype=gram.dtype)
            denom = max(self.control_space_size * (self.control_space_size - 1), 1)
            return (gram - eye).square().sum() / denom

        def _scaled_token(self, raw):
            direction = torch.tanh(raw)
            rms = torch.sqrt(direction.float().square().mean(dim=-1, keepdim=True) + 1e-8).to(direction.dtype)
            strength = torch.sigmoid(self.strength_logit).to(direction.dtype)
            return direction / rms * self.embedding_rms.to(direction.dtype) * strength

        def static_token(self, batch_size: int):
            raw = self.s.view(1, -1).expand(batch_size, -1)
            return self._scaled_token(raw)

        def residual(self, first_leaf):
            # No trainable decision head and no task id: R sees only Qwen's S-conditioned leaf.
            normalized = F.layer_norm(first_leaf.float(), (self.hidden_size,)).to(first_leaf.dtype)
            code = torch.tanh(self.router_down(normalized))
            coefficients = torch.tanh(self.router_coeff(code))
            residual = coefficients.to(self.control_bank.dtype) @ self.normalized_bank()
            self.last_coefficients = coefficients.detach().float()
            self.last_regularization = {
                "sparsity": coefficients.float().abs().mean(),
                "residual_mse": residual.float().square().mean(),
                "orthogonality": self.orthogonality_penalty(),
            }
            return residual

        def combined_token(self, first_leaf):
            residual = self.residual(first_leaf)
            raw = self.s.view(1, -1).expand(first_leaf.shape[0], -1) + residual
            return self._scaled_token(raw), residual

    return SRController


class TaskPool:
    def __init__(self, rows: Sequence[dict], rng: random.Random):
        if not rows:
            raise RuntimeError("cannot build empty task pool")
        self.rows = list(rows)
        self.rng = rng
        self.order = list(range(len(self.rows)))
        self.rng.shuffle(self.order)
        self.cursor = 0

    def draw(self) -> dict:
        if self.cursor >= len(self.order):
            self.rng.shuffle(self.order)
            self.cursor = 0
        row = self.rows[self.order[self.cursor]]
        self.cursor += 1
        return row


def make_model_class():
    import torch
    from torch import nn
    import torch.nn.functional as F

    class SRNativeLMModel(nn.Module):
        def __init__(self, backbone, controller, answer_weight, answer_bias=None):
            super().__init__()
            self.backbone = backbone
            self.controller = controller
            self.register_buffer("answer_weight", answer_weight.detach().clone(), persistent=True)
            if answer_bias is None:
                self.answer_bias = None
            else:
                self.register_buffer("answer_bias", answer_bias.detach().clone(), persistent=True)

        def _encode(self, tokenizer, prompt: str, max_prompt_tokens: int):
            ids = tokenizer.encode(prompt, add_special_tokens=False)
            if not ids:
                raise RuntimeError("prompt tokenized to empty sequence")
            if len(ids) > max_prompt_tokens:
                raise RuntimeError(
                    f"prompt is {len(ids)} tokens, above --max-prompt-tokens={max_prompt_tokens}; "
                    "raise the limit or reduce source budgets"
                )
            device = self.controller.s.device
            return torch.tensor(ids, dtype=torch.long, device=device).unsqueeze(0)

        def _answer_logits(self, leaf):
            weight = self.answer_weight.to(device=leaf.device, dtype=leaf.dtype)
            bias = None if self.answer_bias is None else self.answer_bias.to(device=leaf.device, dtype=leaf.dtype)
            return F.linear(leaf, weight, bias).float()

        def native_logits(self, tokenizer, prompt: str, max_prompt_tokens: int):
            ids = self._encode(tokenizer, prompt, max_prompt_tokens)
            attention = torch.ones_like(ids, dtype=torch.bool)
            with torch.no_grad():
                hidden = self.backbone(input_ids=ids, attention_mask=attention, use_cache=False).last_hidden_state
                leaf = hidden[:, -1, :]
                return self._answer_logits(leaf)

        def forward_prompt(self, tokenizer, prompt: str, max_prompt_tokens: int):
            ids = self._encode(tokenizer, prompt, max_prompt_tokens)
            attention = torch.ones_like(ids, dtype=torch.bool)
            with torch.no_grad():
                token_embeds = self.backbone.get_input_embeddings()(ids)
                s = self.controller.static_token(1).to(dtype=token_embeds.dtype).unsqueeze(1)
                first_inputs = torch.cat([s, token_embeds], dim=1)
                first_attention = torch.cat(
                    [torch.ones((1, 1), dtype=attention.dtype, device=attention.device), attention], dim=1
                )
                first_hidden = self.backbone(
                    inputs_embeds=first_inputs, attention_mask=first_attention, use_cache=False
                ).last_hidden_state
                first_leaf = first_hidden[:, ids.shape[1], :].detach()

            helper, residual = self.controller.combined_token(first_leaf)
            # Token embeddings are frozen; do not retain their embedding-lookup graph.
            with torch.no_grad():
                token_embeds = self.backbone.get_input_embeddings()(ids)
            helper = helper.to(dtype=token_embeds.dtype).unsqueeze(1)
            second_inputs = torch.cat([helper, token_embeds], dim=1)
            second_attention = torch.cat(
                [torch.ones((1, 1), dtype=attention.dtype, device=attention.device), attention], dim=1
            )
            hidden = self.backbone(
                inputs_embeds=second_inputs, attention_mask=second_attention, use_cache=False
            ).last_hidden_state
            leaf = hidden[:, ids.shape[1], :]
            logits = self._answer_logits(leaf)
            with torch.no_grad():
                coeff = self.controller.last_coefficients
                active = 0
                coeff_rms = 0.0
                if coeff is not None:
                    active = int((coeff.abs().mean(dim=0) >= self.controller.active_epsilon).sum().item())
                    coeff_rms = float(torch.sqrt(coeff.square().mean() + 1e-30).item())
                stats = {
                    "active_slots": active,
                    "coefficient_rms": coeff_rms,
                    "residual_rms": float(torch.sqrt(residual.detach().float().square().mean() + 1e-30).item()),
                    "strength": float(torch.sigmoid(self.controller.strength_logit.detach().float()).item()),
                }
            return logits, stats

    return SRNativeLMModel


def task_metrics_template() -> dict[str, Any]:
    return {"questions": 0, "correct": 0, "nll_sum": 0.0, "label_n": defaultdict(int), "label_correct": defaultdict(int)}


def finish_metrics(bucket: dict[str, Any]) -> dict[str, Any]:
    n = int(bucket["questions"])
    label_accuracy = {
        label: bucket["label_correct"][label] / count
        for label, count in sorted(bucket["label_n"].items()) if count
    }
    return {
        "questions": n,
        "accuracy": bucket["correct"] / n if n else None,
        "mean_nll": bucket["nll_sum"] / n if n else None,
        "label_accuracy": label_accuracy,
    }


def evaluate_records(*, model, tokenizer, records: Sequence[dict], task: str, seed: int,
                     max_prompt_tokens: int, precision: str, native: bool = False) -> dict[str, Any]:
    import torch
    import torch.nn.functional as F

    bucket = task_metrics_template()
    model.controller.eval()
    model.backbone.eval()
    with torch.no_grad():
        for row in records:
            _, _, _labels, gold_semantic = record_question(row)
            prompt_seed = stable_seed(seed, task, row.get("id"), "eval-map")
            prompt, gold_index, symbols = build_answer_prompt(row, permutation_seed=prompt_seed)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16"):
                logits = (
                    model.native_logits(tokenizer, prompt, max_prompt_tokens)
                    if native else model.forward_prompt(tokenizer, prompt, max_prompt_tokens)[0]
                )
                scores = logits[0, : len(symbols)].float()
                loss = F.cross_entropy(scores.unsqueeze(0), torch.tensor([gold_index], device=scores.device))
            pred = int(torch.argmax(scores).item())
            bucket["questions"] += 1
            bucket["correct"] += int(pred == gold_index)
            bucket["nll_sum"] += float(loss.item())
            bucket["label_n"][gold_semantic] += 1
            bucket["label_correct"][gold_semantic] += int(pred == gold_index)
    return finish_metrics(bucket)


def validate_generic_records(rows: Sequence[dict], expected_max_options: int = 4) -> None:
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not isinstance(row.get("state"), str):
            raise RuntimeError("malformed training record")
        _qid, _question, labels, _gold = record_question(row)
        if len(labels) > expected_max_options:
            raise RuntimeError(f"record exceeds {expected_max_options} answer options: {row.get('id')}")


def build_dictionary_split(dictionary, cache: Path, url: str, holdout_pairs: int, holdout_seed: int):
    archive = dictionary.download_dictionary(url, cache)
    synsets = dictionary.parse_wordnet_archive(archive)
    dev_pairs = dictionary.build_pairs(synsets, holdout_pairs, holdout_seed)
    reserved = set()
    for _headword, positive, negative in dev_pairs:
        reserved.add(str(positive["id"]))
        reserved.add(str(negative["id"]))
    training_synsets = [row for row in synsets if str(row["id"]) not in reserved]
    if len(training_synsets) < 1000:
        raise RuntimeError("dictionary holdout left too few training synsets")
    return dev_pairs, training_synsets


def dictionary_records(dictionary, pairs: Sequence[tuple[str, dict, dict]], *, split: str, prefix: str) -> list[dict]:
    rows: list[dict] = []
    for pair_index, (headword, positive, negative) in enumerate(pairs):
        family_id = f"{prefix}-{pair_index:05d}"
        for truth, candidate, side in ((False, negative, "false"), (True, positive, "true")):
            row = dictionary.make_record(
                pair_index=pair_index, headword=headword, positive=positive, candidate=candidate, truth=truth
            )
            row["split"] = split
            row["family_id"] = family_id
            row["id"] = f"{family_id}-{side}"
            row["state_id"] = row["id"]
            rows.append(row)
    return rows


def save_generation(*, exp: Path, controller, optimizer, cycle: int, global_step: int,
                    state: dict, training_config: dict, metrics: dict | None, keep: int) -> Path:
    import torch
    from safetensors.torch import save_file

    generations = exp / "checkpoints" / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    final = generations / f"cycle-{cycle:06d}"
    temp = generations / f".cycle-{cycle:06d}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    save_file(controller_state_dict_cpu(controller), str(temp / "controller.safetensors"))
    torch.save(optimizer.state_dict(), temp / "optimizer.pt")
    save_rng(temp / "rng_state.pt")
    atomic_json(temp / "config.json", {
        "schema_version": CHECKPOINT_SCHEMA,
        "cycle": cycle,
        "global_step": global_step,
        "controller_sha256": module_sha256(controller),
        "training_config": training_config,
    })
    atomic_json(temp / "meta.json", {"cycle": cycle, "global_step": global_step, "metrics": metrics})
    if final.exists():
        shutil.rmtree(final)
    os.replace(temp, final)

    committed = dict(state)
    committed["cycle"] = cycle
    committed["global_step"] = global_step
    committed["latest_generation"] = str(final)
    atomic_json(exp / "training_state.json", committed)

    retained = sorted((p for p in generations.glob("cycle-*") if p.is_dir()), key=lambda p: p.name)
    for old in retained[:-keep]:
        shutil.rmtree(old)
    return final


def load_controller_checkpoint(controller, checkpoint: Path) -> None:
    from safetensors.torch import load_file
    weights = load_file(str(checkpoint / "controller.safetensors"), device="cpu")
    incompatible = controller.load_state_dict(weights, strict=True)
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise RuntimeError(f"controller checkpoint mismatch: {incompatible}")


def self_test() -> None:
    binary = {
        "id": "b",
        "state": "state",
        "questions": {"q": {"type": "boolean", "instructions": "truth?", "criteria": {"false": "No", "true": "Yes"}}},
        "gold": {"q": True},
    }
    four = {
        "id": "f",
        "state": "state",
        "questions": {"q": {"type": "choice", "instructions": "pick", "criteria": {"a": "one", "b": "two", "c": "three", "none": "none"}}},
        "gold": {"q": "c"},
    }
    p1, g1, s1 = build_answer_prompt(binary, permutation_seed=1)
    p2, g2, s2 = build_answer_prompt(four, permutation_seed=2)
    assert len(s1) == 2 and 0 <= g1 < 2 and "Answer:" in p1
    assert len(s2) == 4 and 0 <= g2 < 4 and "Answer:" in p2
    budgets = largest_remainder_budgets(100, DEFAULT_CURRICULUM)
    assert budgets == {"legacy": 20, "mutation": 5, "ast": 5, "consensus": 15, "triad": 20, "dictionary": 35}
    tasks, credits = next_task_mix(weights=DEFAULT_CURRICULUM, credits={}, slots=100)
    assert {task: tasks.count(task) for task in TASK_ORDER} == budgets
    assert set(credits) == set(TASK_ORDER)

    import torch
    Controller = build_controller_class(control_space_size=4, register_rank=2, strength_init=0.25, active_epsilon=0.01)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(123)
        c = Controller(8, 0.02)
    leaf = torch.randn(2, 8)
    helper, residual = c.combined_token(leaf)
    assert helper.shape == (2, 8) and residual.shape == (2, 8)
    loss = helper.square().mean() + residual.square().mean()
    loss.backward()
    assert c.s.grad is not None
    assert c.router_coeff.weight.grad is not None
    assert not hasattr(c, "r2_gain")
    emit(
        "self_test_ok",
        architecture="fresh_s_plus_r1_native_lm_no_head",
        no_decision_head=True,
        no_r2=True,
        backbone_reads_per_query=2,
        curriculum=budgets,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--legacy-experiment-dir", default=DEFAULT_LEGACY_EXPERIMENT)
    parser.add_argument("--dictionary-cache", default=DEFAULT_DICTIONARY_CACHE)
    parser.add_argument("--dictionary-url", default=DEFAULT_DICTIONARY_URL)
    parser.add_argument("--dictionary-holdout-pairs", type=int, default=32)
    parser.add_argument("--dictionary-holdout-seed", type=int, default=20260927)
    parser.add_argument("--cycles-this-run", type=int, default=100)
    parser.add_argument("--cycle-seconds", type=float, default=75.0)
    parser.add_argument("--train-files-per-cycle", type=int, default=40)
    parser.add_argument("--train-units-per-cycle", type=int, default=128)
    parser.add_argument("--batch-questions", type=int, default=4)
    parser.add_argument("--max-prompt-tokens", type=int, default=768)
    parser.add_argument("--mutation-max-code-tokens", type=int, default=96)
    parser.add_argument("--consensus-max-code-tokens", type=int, default=72)
    parser.add_argument("--dev-files", type=int, default=40)
    parser.add_argument("--binary-dev-pairs", type=int, default=32)
    parser.add_argument("--consensus-dev-records", type=int, default=64)
    parser.add_argument("--triad-dev-units", type=int, default=32)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--control-space-size", type=int, default=DEFAULT_CONTROL_SPACE_SIZE)
    parser.add_argument("--register-rank", type=int, default=DEFAULT_REGISTER_RANK)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--strength-init", type=float, default=DEFAULT_STRENGTH_INIT)
    parser.add_argument("--register-sparsity-weight", type=float, default=0.01)
    parser.add_argument("--register-residual-weight", type=float, default=0.10)
    parser.add_argument("--register-orthogonality-weight", type=float, default=0.01)
    parser.add_argument("--register-active-epsilon", type=float, default=0.01)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--keep-generations", type=int, default=2)
    parser.add_argument("--disable-gradient-checkpointing", action="store_true")
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--seed-offset", type=int, default=DEFAULT_SEED_OFFSET)
    parser.add_argument("--legacy-training-percent", type=float, default=DEFAULT_CURRICULUM["legacy"])
    parser.add_argument("--mutation-training-percent", type=float, default=DEFAULT_CURRICULUM["mutation"])
    parser.add_argument("--ast-training-percent", type=float, default=DEFAULT_CURRICULUM["ast"])
    parser.add_argument("--consensus-training-percent", type=float, default=DEFAULT_CURRICULUM["consensus"])
    parser.add_argument("--triad-training-percent", type=float, default=DEFAULT_CURRICULUM["triad"])
    parser.add_argument("--dictionary-training-percent", type=float, default=DEFAULT_CURRICULUM["dictionary"])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    weights = {
        "legacy": float(args.legacy_training_percent),
        "mutation": float(args.mutation_training_percent),
        "ast": float(args.ast_training_percent),
        "consensus": float(args.consensus_training_percent),
        "triad": float(args.triad_training_percent),
        "dictionary": float(args.dictionary_training_percent),
    }
    if any(not math.isfinite(v) or v < 0.0 for v in weights.values()) or abs(sum(weights.values()) - 100.0) > 1e-9:
        parser.error("six curriculum percentages must be finite, nonnegative, and sum to exactly 100")
    positive_ints = (
        args.cycles_this_run, args.train_files_per_cycle, args.train_units_per_cycle, args.batch_questions,
        args.max_prompt_tokens, args.mutation_max_code_tokens, args.consensus_max_code_tokens,
        args.dev_files, args.binary_dev_pairs, args.consensus_dev_records, args.triad_dev_units,
        args.eval_every, args.control_space_size, args.register_rank, args.keep_generations,
        args.dictionary_holdout_pairs,
    )
    if min(positive_ints) <= 0 or args.cycle_seconds <= 0:
        parser.error("cycle/data/model/evaluation settings must be positive")
    if args.control_space_size > 1024:
        parser.error("current Qwen3-0.6B hidden size is 1024; K must be <= H for orthonormal fresh initialization")
    for name in ("lr", "strength_init", "register_active_epsilon", "grad_clip"):
        if not math.isfinite(float(getattr(args, name))) or float(getattr(args, name)) <= 0.0:
            parser.error(f"--{name.replace('_','-')} must be finite and positive")
    if not 0.0 < args.strength_init < 1.0:
        parser.error("--strength-init must be strictly between 0 and 1")
    for name in ("register_sparsity_weight", "register_residual_weight", "register_orthogonality_weight", "weight_decay"):
        if not math.isfinite(float(getattr(args, name))) or float(getattr(args, name)) < 0.0:
            parser.error(f"--{name.replace('_','-')} must be finite and nonnegative")

    tools_dir = Path(__file__).resolve().parent
    legacy = load_local_module("nanojev_code_train_for_sr_native_lm", tools_dir / "nanojev_code_train.py")
    data = load_local_module("nanojev_code_lexeme_data_for_sr_native_lm", tools_dir / "nanojev_code_lexeme_data.py")
    mutation = load_local_module("nanojev_code_mutation_for_sr_native_lm", tools_dir / "nanojev_code_mutation_train.py")
    source = load_local_module(
        "nanojev_current_six_task_helpers_for_sr_native_lm",
        tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    )
    dictionary = load_local_module("nanojev_dictionary_for_sr_native_lm", tools_dir / "nanojev_dictionary_smoke.py")

    legacy_exp = Path(args.legacy_experiment_dir).expanduser().resolve(strict=True)
    legacy_experiment = read_json(legacy_exp / "experiment.json")
    repo_root = Path(legacy_experiment["repo_root"]).resolve(strict=True)
    train_manifest = read_json(Path(legacy_experiment["manifests"]["train"]).resolve(strict=True))
    dev_manifest = read_json(Path(legacy_experiment["manifests"]["dev"]).resolve(strict=True))
    base_seed = int(legacy_experiment["seed"]) + int(args.seed_offset)

    exp = Path(args.experiment_dir).expanduser()
    if exp.exists() and not exp.is_dir():
        raise RuntimeError(f"experiment path is not a directory: {exp}")
    existing = (exp / "experiment.json").is_file()
    if existing:
        experiment = read_json(exp / "experiment.json")
        config = read_json(exp / "training_config.json")
        state = read_json(exp / "training_state.json")
        if experiment.get("schema_version") != SCHEMA or config.get("schema_version") != CONFIG_SCHEMA:
            raise RuntimeError(f"existing directory is not this fresh S+R native-LM experiment: {exp}")
        established_weights = {task: float(config["curriculum"][task]) for task in TASK_ORDER}
        if established_weights != weights:
            raise RuntimeError(f"resume curriculum mismatch: established={established_weights} requested={weights}")
        for key, requested in (
            ("control_space_size", args.control_space_size), ("register_rank", args.register_rank),
            ("max_prompt_tokens", args.max_prompt_tokens),
        ):
            if int(config[key]) != int(requested):
                raise RuntimeError(f"resume {key} mismatch: established={config[key]} requested={requested}")
        seed = int(experiment["seed"])
    else:
        exp.mkdir(parents=True, exist_ok=True)
        for rel in ("probes", "shards/legacy", "shards/mutation", "shards/ast", "shards/consensus", "shards/triad", "shards/dictionary", "checkpoints/generations"):
            (exp / rel).mkdir(parents=True, exist_ok=True)
        seed = base_seed
        experiment = {
            "schema_version": SCHEMA,
            "created_unix": time.time(),
            "experiment_dir": str(exp.resolve()),
            "legacy_experiment": str(legacy_exp),
            "repo_root": str(repo_root),
            "model": legacy_experiment["model"],
            "resolved_model_revision": legacy_experiment["resolved_model_revision"],
            "seed": seed,
            "fresh_trainables": True,
            "parent_checkpoint": None,
            "decision_head": False,
            "r2": False,
            "backbone_reads_per_query": 2,
            "architecture": "S-conditioned frozen-Qwen sensor -> R1 -> S+R1 frozen-Qwen decision -> native LM A/B/C/D rows",
        }
        config = {
            "schema_version": CONFIG_SCHEMA,
            "curriculum": weights,
            "control_space_size": args.control_space_size,
            "register_rank": args.register_rank,
            "max_prompt_tokens": args.max_prompt_tokens,
            "lr": args.lr,
            "weight_decay": args.weight_decay,
            "strength_init": args.strength_init,
            "register_sparsity_weight": args.register_sparsity_weight,
            "register_residual_weight": args.register_residual_weight,
            "register_orthogonality_weight": args.register_orthogonality_weight,
            "register_active_epsilon": args.register_active_epsilon,
            "precision": args.precision,
            "train_units_per_cycle": args.train_units_per_cycle,
            "train_files_per_cycle": args.train_files_per_cycle,
            "answer_symbols": list(ANSWER_SYMBOLS),
            "semantic_answer_mapping": "randomized_every_training_presentation",
            "first_pass_gradient": "detached_sensor_pass",
            "decision_head": False,
            "r2": False,
        }
        state = {
            "schema_version": STATE_SCHEMA,
            "cycle": 0,
            "global_step": 0,
            "latest_generation": None,
            "legacy_source_cursor": 0,
            "mutation_source_cursor": 0,
            "ast_source_cursor": 0,
            "consensus_source_cursor": 0,
            "triad_source_cursor": 0,
            "mix_credits": {task: 0.0 for task in TASK_ORDER},
        }
        atomic_json(exp / "experiment.json", experiment)
        atomic_json(exp / "training_config.json", config)
        atomic_json(exp / "training_state.json", state)

    if args.dry_run:
        emit(
            "dry_run_ok",
            experiment=str(exp), fresh=not existing, decision_head=False, r2=False,
            backbone_reads_per_query=2, curriculum=weights,
            helper_source=str(tools_dir / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py"),
        )
        return

    import torch
    import torch.nn.functional as F
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if args.disable_native_triton:
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("bf16 requested but unsupported by CUDA device")
    torch.backends.cuda.matmul.allow_tf32 = False

    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    canonical_token_ids = answer_token_ids(tokenizer)
    emit("answer_tokens_resolved", symbols=list(ANSWER_SYMBOLS), token_ids=list(canonical_token_ids))

    # Build/cached held-out rulers before loading the model.
    probes: dict[str, list[dict]] = {}
    legacy_probe_path = exp / "probes" / "legacy_ordered.jsonl"
    if legacy_probe_path.is_file():
        probes["legacy"] = read_jsonl(legacy_probe_path)
    else:
        canonical = read_jsonl(Path(legacy_experiment["dev_probe"]).resolve(strict=True))
        rows = data.sample_ordered_continuation_from_canonical_probe(
            probe_records=canonical, repo_root=repo_root, tokenizer=tokenizer, split="dev",
            pair_count=ORDERED_LEGACY_DEV_PAIRS,
            max_prefix_tokens=legacy_experiment["max_prefix_tokens"],
            seed=int(legacy_experiment["seed"]) + ORDERED_LEGACY_DEV_SEED_OFFSET,
            max_lexeme_tokens=legacy_experiment["max_lexeme_tokens"],
        )
        validate_generic_records(rows)
        write_jsonl(legacy_probe_path, rows)
        probes["legacy"] = rows

    dev_python_0, _ = mutation.cyclic_filtered_slice(dev_manifest, 0, args.dev_files, lambda row: row.get("language") == "python")
    dev_python_1, _ = mutation.cyclic_filtered_slice(dev_manifest, args.dev_files, args.dev_files, lambda row: row.get("language") == "python")
    dev_python_2, _ = mutation.cyclic_filtered_slice(dev_manifest, args.dev_files * 2, args.dev_files, lambda row: row.get("language") == "python")
    if not dev_python_0 or not dev_python_1 or not dev_python_2:
        raise RuntimeError("dev manifest contains insufficient Python files")

    mutation_probe_path = exp / "probes" / "mutation.jsonl"
    if mutation_probe_path.is_file():
        probes["mutation"] = read_jsonl(mutation_probe_path)
    else:
        docs = data.load_docs(dev_python_0, repo_root)
        rows = mutation.sample_mutation_records(
            docs=docs, data=data, tokenizer=tokenizer, split="dev", pair_count=args.binary_dev_pairs,
            max_code_tokens=args.mutation_max_code_tokens, max_length=legacy_experiment["max_length"],
            seed=seed + 1001,
        )
        validate_generic_records(rows)
        write_jsonl(mutation_probe_path, rows)
        probes["mutation"] = rows

    ast_probe_path = exp / "probes" / "ast.jsonl"
    if ast_probe_path.is_file():
        probes["ast"] = read_jsonl(ast_probe_path)
    else:
        docs = data.load_docs(dev_python_1, repo_root)
        rows = source.sample_ast_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            pair_count=args.binary_dev_pairs, max_code_tokens=args.mutation_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=seed + 1002,
        )
        validate_generic_records(rows)
        write_jsonl(ast_probe_path, rows)
        probes["ast"] = rows

    consensus_probe_path = exp / "probes" / "consensus.jsonl"
    if consensus_probe_path.is_file():
        probes["consensus"] = read_jsonl(consensus_probe_path)
    else:
        docs = data.load_docs(dev_python_2, repo_root)
        rows = source.sample_consensus_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            record_count=args.consensus_dev_records, max_code_tokens=args.consensus_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=seed + 1003,
        )
        validate_generic_records(rows)
        write_jsonl(consensus_probe_path, rows)
        probes["consensus"] = rows

    triad_probe_path = exp / "probes" / "triad.jsonl"
    if triad_probe_path.is_file():
        probes["triad"] = read_jsonl(triad_probe_path)
    else:
        docs = data.load_docs(dev_python_2, repo_root)
        rows = source.sample_relation_balanced_pairwise_records(
            docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="dev",
            unit_count=args.triad_dev_units, max_code_tokens=args.consensus_max_code_tokens,
            max_length=legacy_experiment["max_length"], seed=seed + 1004,
        )
        validate_generic_records(rows)
        write_jsonl(triad_probe_path, rows)
        probes["triad"] = rows

    dictionary_cache = Path(args.dictionary_cache).expanduser()
    dictionary_dev_pairs, dictionary_training_synsets = build_dictionary_split(
        dictionary, dictionary_cache, args.dictionary_url, args.dictionary_holdout_pairs, args.dictionary_holdout_seed
    )
    dictionary_probe_path = exp / "probes" / "dictionary.jsonl"
    if dictionary_probe_path.is_file():
        probes["dictionary"] = read_jsonl(dictionary_probe_path)
    else:
        rows = dictionary_records(dictionary, dictionary_dev_pairs, split="dev", prefix="dictionary-dev")
        validate_generic_records(rows)
        write_jsonl(dictionary_probe_path, rows)
        probes["dictionary"] = rows

    emit("probes_ready", **{task: len(rows) for task, rows in probes.items()})

    emit("frozen_causal_lm_load_start", model=legacy_experiment["model"], revision=legacy_experiment["resolved_model_revision"])
    causal = AutoModelForCausalLM.from_pretrained(
        legacy_experiment["model"], revision=legacy_experiment["resolved_model_revision"], dtype=torch.float32,
        attn_implementation="sdpa", trust_remote_code=False, local_files_only=args.local_files_only,
    )
    backbone = causal.model if hasattr(causal, "model") else causal.base_model
    output = causal.get_output_embeddings()
    if output is None or not hasattr(output, "weight"):
        raise RuntimeError("causal LM does not expose a vocabulary output projection")
    with torch.no_grad():
        ids_tensor = torch.tensor(canonical_token_ids, dtype=torch.long)
        answer_weight = output.weight.detach().index_select(0, ids_tensor).clone()
        answer_bias = None
        if getattr(output, "bias", None) is not None:
            answer_bias = output.bias.detach().index_select(0, ids_tensor).clone()
    # We intentionally retain only the backbone and four frozen LM rows.
    del output
    del causal

    hidden_size = int(backbone.config.hidden_size)
    if args.control_space_size > hidden_size:
        raise RuntimeError(f"K={args.control_space_size} exceeds actual hidden size H={hidden_size}")
    with torch.no_grad():
        sample = backbone.get_input_embeddings().weight[: min(4096, backbone.get_input_embeddings().weight.shape[0])]
        embedding_rms = float(torch.sqrt(sample.detach().float().square().mean() + 1e-30).item())

    Controller = build_controller_class(
        control_space_size=args.control_space_size, register_rank=args.register_rank,
        strength_init=args.strength_init, active_epsilon=args.register_active_epsilon,
    )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        controller = Controller(hidden_size, embedding_rms)
    Model = make_model_class()
    model = Model(backbone, controller, answer_weight, answer_bias)
    model.backbone.config.use_cache = False
    for param in model.backbone.parameters():
        param.requires_grad_(False)
    for param in model.controller.parameters():
        param.requires_grad_(True)
    model.answer_weight.requires_grad_(False)
    if model.answer_bias is not None:
        model.answer_bias.requires_grad_(False)

    if not args.disable_gradient_checkpointing:
        if not hasattr(model.backbone, "gradient_checkpointing_enable"):
            raise RuntimeError("frozen Qwen backbone does not expose gradient_checkpointing_enable")
        model.backbone.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        active_dropouts = [m for m in model.backbone.modules() if isinstance(m, torch.nn.Dropout) and float(m.p) != 0.0]
        if active_dropouts:
            raise RuntimeError("gradient checkpointing requires deterministic zero-dropout frozen Qwen")

    trainable_names = [name for name, p in model.named_parameters() if p.requires_grad]
    if not trainable_names or any(name.startswith("backbone.") for name in trainable_names):
        raise RuntimeError(f"trainable parameter contract violated: {trainable_names[:20]}")
    if any("head" in name.lower() for name in trainable_names):
        raise RuntimeError(f"fresh headless experiment unexpectedly contains a trainable head: {trainable_names}")

    model.cuda()
    optimizer = torch.optim.AdamW(model.controller.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    if state.get("latest_generation"):
        checkpoint = Path(state["latest_generation"]).resolve(strict=True)
        load_controller_checkpoint(model.controller, checkpoint)
        optimizer.load_state_dict(torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False))
        for opt_state in optimizer.state.values():
            for key, value in list(opt_state.items()):
                if torch.is_tensor(value):
                    opt_state[key] = value.cuda()
        load_rng(checkpoint / "rng_state.pt")
        emit("resume_loaded", checkpoint=str(checkpoint), cycle=state["cycle"], global_step=state["global_step"])
    else:
        random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    emit(
        "trainer_ready",
        gpu=torch.cuda.get_device_name(0), hidden_size=hidden_size,
        trainable_params=sum(p.numel() for p in model.controller.parameters()),
        trainable_parameter_names=trainable_names,
        decision_head=False, r2=False, backbone_reads_per_query=2,
        frozen_answer_rows_shape=list(model.answer_weight.shape),
        control_space_size=args.control_space_size, register_rank=args.register_rank,
        gradient_checkpointing=not args.disable_gradient_checkpointing,
    )

    # Native-Qwen baseline is immutable, so measure it once for this experiment.
    baseline_path = exp / "native_baseline.json"
    if not baseline_path.is_file():
        baseline = {}
        for task in TASK_ORDER:
            baseline[task] = evaluate_records(
                model=model, tokenizer=tokenizer, records=probes[task], task=task, seed=seed,
                max_prompt_tokens=args.max_prompt_tokens, precision=args.precision, native=True,
            )
        atomic_json(baseline_path, {"seed": seed, "metrics": baseline})
        emit("native_baseline", metrics=baseline)

    unit_budgets = largest_remainder_budgets(args.train_units_per_cycle, weights)

    for _ in range(args.cycles_this_run):
        cycle = int(state["cycle"]) + 1
        shard_seed = seed + cycle * 10007
        cycle_start = time.perf_counter()

        legacy_start = int(state.get("legacy_source_cursor", 0)) % len(train_manifest)
        legacy_rows = legacy.cyclic_slice(train_manifest, legacy_start, args.train_files_per_cycle)
        mutation_rows, mutation_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state.get("mutation_source_cursor", 0)), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        ast_rows, ast_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state.get("ast_source_cursor", 0)), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        consensus_rows, consensus_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state.get("consensus_source_cursor", 0)), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        triad_rows, triad_next = mutation.cyclic_filtered_slice(
            train_manifest, int(state.get("triad_source_cursor", 0)), args.train_files_per_cycle,
            lambda row: row.get("language") == "python",
        )
        if not all((legacy_rows, mutation_rows, ast_rows, consensus_rows, triad_rows)):
            raise RuntimeError("train manifest contains insufficient source files")

        pools_raw: dict[str, list[dict]] = {}
        if unit_budgets["legacy"]:
            docs = data.load_docs(legacy_rows, repo_root)
            pools_raw["legacy"] = data.sample_ordered_continuation_paired_records(
                docs=docs, tokenizer=tokenizer, split="train", pair_count=max(unit_budgets["legacy"], 1),
                max_prefix_tokens=legacy_experiment["max_prefix_tokens"], seed=shard_seed,
                max_lexeme_tokens=legacy_experiment["max_lexeme_tokens"], min_symbols=3, max_symbols=5,
            )
        if unit_budgets["mutation"]:
            docs = data.load_docs(mutation_rows, repo_root)
            pools_raw["mutation"] = mutation.sample_mutation_records(
                docs=docs, data=data, tokenizer=tokenizer, split="train", pair_count=max(unit_budgets["mutation"], 1),
                max_code_tokens=args.mutation_max_code_tokens, max_length=legacy_experiment["max_length"],
                seed=shard_seed + 1,
            )
        if unit_budgets["ast"]:
            docs = data.load_docs(ast_rows, repo_root)
            pools_raw["ast"] = source.sample_ast_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                pair_count=max(unit_budgets["ast"], 1), max_code_tokens=args.mutation_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 2,
            )
        if unit_budgets["consensus"]:
            docs = data.load_docs(consensus_rows, repo_root)
            pools_raw["consensus"] = source.sample_consensus_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                record_count=max(unit_budgets["consensus"], 4), max_code_tokens=args.consensus_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 3,
            )
        if unit_budgets["triad"]:
            docs = data.load_docs(triad_rows, repo_root)
            pools_raw["triad"] = source.sample_relation_balanced_pairwise_records(
                docs=docs, mutation=mutation, data=data, tokenizer=tokenizer, split="train",
                unit_count=max(unit_budgets["triad"], 1), max_code_tokens=args.consensus_max_code_tokens,
                max_length=legacy_experiment["max_length"], seed=shard_seed + 4,
            )
        if unit_budgets["dictionary"]:
            pairs = dictionary.build_pairs(dictionary_training_synsets, max(unit_budgets["dictionary"], 1), shard_seed + 5)
            pools_raw["dictionary"] = dictionary_records(
                dictionary, pairs, split="train", prefix=f"dictionary-train-c{cycle:06d}"
            )

        for task, rows in pools_raw.items():
            validate_generic_records(rows)
            path = exp / "shards" / task / f"cycle-{cycle:06d}.jsonl"
            write_jsonl(path, rows)
        pools = {task: TaskPool(rows, random.Random(shard_seed + 100 + TASK_ORDER.index(task))) for task, rows in pools_raw.items()}
        if any(weights[task] > 0 and task not in pools for task in TASK_ORDER):
            raise RuntimeError("nonzero curriculum task failed to build a pool")

        emit(
            "cycle_start", cycle=cycle, global_step=state["global_step"],
            requested_training_seconds=args.cycle_seconds, curriculum=weights,
            unit_budgets=unit_budgets, pool_questions={task: len(rows) for task, rows in pools_raw.items()},
        )

        cycle_metrics = {task: task_metrics_template() for task in TASK_ORDER}
        cycle_steps = 0
        last_grad_norm = 0.0
        last_stats: dict[str, Any] = {}
        mix_credits = {task: float(state.get("mix_credits", {}).get(task, 0.0)) for task in TASK_ORDER}
        mapping_rng = random.Random(seed + cycle * 7919)
        started = time.perf_counter()

        while True:
            if cycle_steps > 0 and time.perf_counter() - started >= args.cycle_seconds:
                break
            task_slots, mix_credits = next_task_mix(weights=weights, credits=mix_credits, slots=args.batch_questions)
            optimizer.zero_grad(set_to_none=True)
            batch_loss_value = 0.0

            model.controller.train()
            if args.disable_gradient_checkpointing:
                model.backbone.eval()
            else:
                model.backbone.train()

            for task in task_slots:
                row = pools[task].draw()
                prompt, gold_index, symbols = build_answer_prompt(row, rng=mapping_rng)
                _qid, _question, _labels, gold_semantic = record_question(row)
                target = torch.tensor([gold_index], dtype=torch.long, device="cuda")
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=args.precision == "bf16"):
                    logits, stats = model.forward_prompt(tokenizer, prompt, args.max_prompt_tokens)
                    scores = logits[:, : len(symbols)].float()
                    ce = F.cross_entropy(scores, target)
                    reg = model.controller.last_regularization
                    loss = (
                        ce
                        + args.register_sparsity_weight * reg["sparsity"]
                        + args.register_residual_weight * reg["residual_mse"]
                        + args.register_orthogonality_weight * reg["orthogonality"]
                    )
                    scaled = loss / float(args.batch_questions)
                scaled.backward()
                batch_loss_value += float(loss.detach().item()) / float(args.batch_questions)
                pred = int(torch.argmax(scores[0]).item())
                bucket = cycle_metrics[task]
                bucket["questions"] += 1
                bucket["correct"] += int(pred == gold_index)
                bucket["nll_sum"] += float(ce.detach().item())
                bucket["label_n"][gold_semantic] += 1
                bucket["label_correct"][gold_semantic] += int(pred == gold_index)
                last_stats = stats

            last_grad_norm = float(torch.nn.utils.clip_grad_norm_(model.controller.parameters(), args.grad_clip).item())
            optimizer.step()
            cycle_steps += 1
            state["global_step"] = int(state["global_step"]) + 1

        state["legacy_source_cursor"] = (legacy_start + len(legacy_rows)) % len(train_manifest)
        state["mutation_source_cursor"] = mutation_next
        state["ast_source_cursor"] = ast_next
        state["consensus_source_cursor"] = consensus_next
        state["triad_source_cursor"] = triad_next
        state["mix_credits"] = mix_credits

        train_metrics = {task: finish_metrics(bucket) for task, bucket in cycle_metrics.items()}
        dev_metrics = None
        if cycle == 1 or cycle % args.eval_every == 0:
            dev_metrics = {}
            for task in TASK_ORDER:
                dev_metrics[task] = evaluate_records(
                    model=model, tokenizer=tokenizer, records=probes[task], task=task, seed=seed,
                    max_prompt_tokens=args.max_prompt_tokens, precision=args.precision, native=False,
                )

        checkpoint = save_generation(
            exp=exp, controller=model.controller, optimizer=optimizer, cycle=cycle,
            global_step=int(state["global_step"]), state=state, training_config=config,
            metrics={"train": train_metrics, "dev": dev_metrics}, keep=args.keep_generations,
        )
        # save_generation commits cycle/global_step/latest_generation to disk; mirror in memory.
        state = read_json(exp / "training_state.json")
        emit(
            "cycle_result", cycle=cycle, global_step=state["global_step"], steps=cycle_steps,
            elapsed_seconds=time.perf_counter() - cycle_start,
            train=train_metrics, dev=dev_metrics,
            controller_sha256=module_sha256(model.controller),
            grad_norm_last_step=last_grad_norm, controller_stats=last_stats,
            checkpoint=str(checkpoint), decision_head=False, r2=False,
        )


if __name__ == "__main__":
    main()
