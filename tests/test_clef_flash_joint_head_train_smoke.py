from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import math

import torch
import torch.nn.functional as F


TOOL = Path(__file__).resolve().parents[1] / "tools" / "clef_flash_joint_head_train_smoke.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("clef_flash_smoke", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EncodedQuestion:
    def __init__(self, question_id, question_type, question_span, option_spans, option_ids):
        self.question_id = question_id
        self.question_type = question_type
        self.question_span = question_span
        self.option_spans = option_spans
        self.option_ids = option_ids


class EncodedRecord:
    def __init__(self, input_ids, questions, record_id, media=None):
        self.input_ids = input_ids
        self.questions = questions
        self.record_id = record_id
        self.media = media


class EvidenceRoutingLayer(torch.nn.Module):
    def __init__(self, width, heads, feedforward, dropout=0.0):
        super().__init__()
        self.query_norm = torch.nn.LayerNorm(width)
        self.memory_norm = torch.nn.LayerNorm(width)
        self.attention = torch.nn.MultiheadAttention(
            width, heads, dropout=dropout, batch_first=True
        )
        self.attention_dropout = torch.nn.Dropout(dropout)
        self.feedforward_norm = torch.nn.LayerNorm(width)
        self.feedforward = torch.nn.Sequential(
            torch.nn.Linear(width, feedforward),
            torch.nn.GELU(),
            torch.nn.Dropout(dropout),
            torch.nn.Linear(feedforward, width),
            torch.nn.Dropout(dropout),
        )

    def forward(self, queries, memory):
        normalized_queries = self.query_norm(queries)
        routed, _ = self.attention(
            normalized_queries,
            self.memory_norm(memory),
            self.memory_norm(memory),
            need_weights=False,
        )
        queries = queries + self.attention_dropout(routed)
        return queries + self.feedforward(self.feedforward_norm(queries))


class TinyJointSchemaHead(torch.nn.Module):
    """Cloudflare JointSchemaHead topology at tiny dimensions for local tests."""

    def __init__(
        self,
        hidden_size=16,
        width=8,
        routing_layers=1,
        layers=1,
        heads=2,
        feedforward=16,
        dropout=0.0,
    ):
        super().__init__()
        self.hidden_norm = torch.nn.LayerNorm(hidden_size)
        self.memory_projection = torch.nn.Linear(hidden_size, width, bias=False)
        self.question_projection = torch.nn.Linear(hidden_size, width, bias=False)
        self.option_question_projection = torch.nn.Linear(hidden_size, width, bias=False)
        self.global_projection = torch.nn.Linear(hidden_size, width, bias=False)
        self.option_context_projection = torch.nn.Linear(hidden_size, width, bias=False)
        self.option_lexical_projection = torch.nn.Linear(hidden_size, width, bias=False)
        self.type_embedding = torch.nn.Embedding(3, width)
        self.evidence_layers = torch.nn.ModuleList(
            [EvidenceRoutingLayer(width, heads, feedforward, dropout) for _ in range(routing_layers)]
        )
        self.option_summary_norm = torch.nn.LayerNorm(width)
        self.layers = torch.nn.ModuleList(
            [
                torch.nn.TransformerDecoderLayer(
                    d_model=width,
                    nhead=heads,
                    dim_feedforward=feedforward,
                    dropout=dropout,
                    activation="gelu",
                    batch_first=True,
                    norm_first=True,
                )
                for _ in range(layers)
            ]
        )
        self.field_norm = torch.nn.LayerNorm(width)
        self.option_norm = torch.nn.LayerNorm(width)
        self.residual_scorer = torch.nn.Sequential(
            torch.nn.Linear(width * 4, width),
            torch.nn.GELU(),
            torch.nn.Dropout(dropout),
            torch.nn.Linear(width, 1),
        )
        self.prior_logit_scale = torch.nn.Parameter(torch.zeros(()))
        self.joint_logit_scale = torch.nn.Parameter(torch.zeros(()))
        self.residual_gate = torch.nn.Parameter(torch.zeros(()))

    @staticmethod
    def _mean_span(values, span):
        start, end = span
        return values[start:end].mean(dim=0)

    def forward(self, hidden_states, input_ids, attention_mask, records, output_embedding_weight):
        results = []
        normalized_hidden = self.hidden_norm(hidden_states)
        for batch_index, record in enumerate(records):
            sequence_length = int(attention_mask[batch_index].sum().item())
            sequence_hidden = normalized_hidden[batch_index, :sequence_length]
            memory = self.memory_projection(sequence_hidden).unsqueeze(0)
            global_vector = sequence_hidden[-1]
            question_vectors = torch.stack(
                [self._mean_span(sequence_hidden, question.question_span) for question in record.questions]
            )
            type_ids = torch.tensor(
                [question.question_type for question in record.questions],
                device=hidden_states.device,
            )
            option_contexts = []
            lexical_options = []
            option_counts = []
            for question in record.questions:
                context_vectors = torch.stack(
                    [self._mean_span(sequence_hidden, span) for span in question.option_spans]
                )
                lexical_vectors = []
                for start, end in question.option_spans:
                    token_ids = input_ids[batch_index, start:end]
                    lexical_vectors.append(output_embedding_weight[token_ids].mean(dim=0))
                lexical = torch.stack(lexical_vectors)
                option_contexts.append(context_vectors)
                lexical_options.append(lexical)
                option_counts.append(len(question.option_spans))

            option_queries = []
            for question_index, (context_vectors, lexical) in enumerate(
                zip(option_contexts, lexical_options)
            ):
                option_queries.append(
                    self.option_context_projection(context_vectors)
                    + self.option_lexical_projection(lexical)
                    + self.option_question_projection(question_vectors[question_index]).unsqueeze(0)
                )
            routed_options = torch.cat(option_queries, dim=0).unsqueeze(0)
            for layer in self.evidence_layers:
                routed_options = layer(routed_options, memory)
            routed_options = routed_options[0]
            split_options = list(torch.split(routed_options, option_counts, dim=0))

            base_fields = self.question_projection(question_vectors)
            option_summaries = []
            for field, options in zip(base_fields, split_options):
                routing_weights = torch.softmax(
                    torch.matmul(options, field) / math.sqrt(options.shape[-1]), dim=0
                )
                option_summaries.append(
                    torch.sum(routing_weights.unsqueeze(-1) * options, dim=0)
                )
            fields = (
                base_fields
                + self.option_summary_norm(torch.stack(option_summaries))
                + self.global_projection(global_vector).unsqueeze(0)
                + self.type_embedding(type_ids)
            )
            fields = fields.unsqueeze(0)
            for layer in self.layers:
                fields = layer(fields, memory)
            fields = self.field_norm(fields[0])

            record_logits = []
            for field, question, lexical, routed in zip(
                fields, record.questions, lexical_options, split_options
            ):
                anchor = F.normalize(
                    question_vectors[len(record_logits)] + global_vector, dim=-1
                )
                lexical_anchor = F.normalize(lexical, dim=-1)
                prior_scale = self.prior_logit_scale.clamp(max=math.log(100.0)).exp()
                prior = prior_scale * torch.matmul(lexical_anchor, anchor)
                options = self.option_norm(routed)
                repeated_field = field.unsqueeze(0).expand_as(options)
                cosine = F.cosine_similarity(repeated_field, options, dim=-1)
                features = torch.cat(
                    [
                        repeated_field,
                        options,
                        repeated_field * options,
                        torch.abs(repeated_field - options),
                    ],
                    dim=-1,
                )
                residual = self.residual_scorer(features).squeeze(-1)
                joint_scale = self.joint_logit_scale.clamp(max=math.log(100.0)).exp()
                joint = joint_scale * cosine + residual
                record_logits.append(prior + torch.sigmoid(self.residual_gate) * joint)
            results.append(record_logits)
        return results


class FakeModule:
    EncodedQuestion = EncodedQuestion
    EncodedRecord = EncodedRecord


def test_decision_loss_has_gradient():
    tool = load_tool()
    logits = torch.tensor([[2.0, -1.0], [-1.0, 2.0]], requires_grad=True)
    labels = torch.tensor([0, 1])
    loss, metrics = tool.decision_loss(logits, labels, label_smoothing=0.05, brier_weight=0.25)
    loss.backward()
    assert metrics["accuracy"] == 1.0
    assert logits.grad is not None
    assert float(logits.grad.abs().sum()) > 0.0


def test_synthetic_batch_is_balanced_and_structured():
    tool = load_tool()
    batch = tool.make_synthetic_batch(
        module=FakeModule,
        hidden_size=16,
        count=8,
        sequence_length=16,
        seed=123,
        device=torch.device("cpu"),
    )
    assert batch["labels"].tolist() == [0, 1, 0, 1, 0, 1, 0, 1]
    assert tuple(batch["hidden_states"].shape) == (8, 16, 16)
    assert len(batch["records"]) == 8
    assert batch["records"][0].questions[0].option_spans == ((6, 7), (8, 9))


def test_train_head_changes_parameters_and_reduces_loss(tmp_path):
    tool = load_tool()
    torch.manual_seed(123)
    head = TinyJointSchemaHead(hidden_size=16)
    batch = tool.make_synthetic_batch(
        module=FakeModule,
        hidden_size=16,
        count=8,
        sequence_length=16,
        seed=123,
        device=torch.device("cpu"),
    )
    result = tool.train_head(
        head=head,
        batch=batch,
        steps=30,
        learning_rate=0.01,
        label_smoothing=0.05,
        brier_weight=0.25,
        events_path=tmp_path / "events.jsonl",
    )
    assert result["maximum_grad_norm"] > 0.0
    assert result["final"]["loss"] < result["initial"]["loss"]
    assert result["tracked_parameter_delta_l2"] > 0.0
    assert (tmp_path / "events.jsonl").is_file()


def test_sha256_file(tmp_path):
    tool = load_tool()
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    assert tool.sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_expected_public_config_is_release_shape():
    tool = load_tool()
    assert tool.EXPECTED_CONFIG == {
        "hidden_size": 4096,
        "width": 1024,
        "routing_layers": 2,
        "layers": 4,
        "heads": 16,
        "feedforward": 4096,
    }



def test_main_persists_runtime_error(monkeypatch, tmp_path):
    tool = load_tool()
    output_dir = tmp_path / "forced-failure"

    def boom(_args):
        raise RuntimeError("forced smoke failure")

    monkeypatch.setattr(tool, "run", boom)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(TOOL), "--output-dir", str(output_dir)],
    )
    assert tool.main() == 1
    error_path = output_dir / "error.json"
    assert error_path.is_file()
    payload = __import__("json").loads(error_path.read_text(encoding="utf-8"))
    assert payload["exception_type"] == "RuntimeError"
    assert payload["exception"] == "forced smoke failure"
    assert "Traceback" in payload["traceback"]
