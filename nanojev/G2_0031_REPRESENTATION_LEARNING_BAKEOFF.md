# G2-0031 — Token-Occurrence Representation-Learning Bake-Off

## Objective

Replace the frozen G2 **relationship representation** as the learned input to
new relation models. This is a targeted follow-on to G2-0029/0030, where
predicate–argument scoring plus TRAIN-fitted precision selection raised graph
F1, but did not meet the +2 percentage-point improvement over the properly
recalibrated G2-0026 reference and lost exact-graph accuracy.

**The objective is correct game-graph construction, not diagnostic loss.**
The models must improve graph F1 against a hard G2-0030 champion while
preserving relationship ranking, exact graph rate, and withheld-topology F1.

## Research foundations

- Zhong and Chen (2021), [*A Frustratingly Easy Approach for Entity and
  Relation Extraction*](https://aclanthology.org/2021.naacl-main.5/):
  relation-specific contextual representations, distinct from entity encoders.
- Wadden et al. (2019), [*Entity, Relation, and Event Extraction with
  Contextualized Span Representations*](https://aclanthology.org/D19-1585/):
  contextual span representations and neighborhood-aware graph updates.
- Lin et al. (2020), [*A Joint Neural Model for Information Extraction with
  Global Features*](https://aclanthology.org/2020.acl-main.713/):
  graph construction rather than independent edge prediction alone.

These are **bounded original implementations inspired by the literature**,
not reproductions of PURE, DyGIE++, or OneIE.

## Three challenger architectures

All models receive the **same** frozen backbone token states, predicted G1
spans, G2 top-24 candidate inventory, relation identifiers, and frozen
G2-0026 reference logits. They learn new contextual occurrence representations:

1. **Role-span biaffine**: trainable occurrence-token aggregation, distinct
   predicate/argument projections and a relation-conditioned biaffine scorer.
2. **Cross-occurrence attention**: trainable query/key/value cross-attention
   over source/destination token windows, with relation-conditioned readout.
3. **Occurrence graph**: trainable span encoders and two graph-message rounds.
   Message passing sees the **full candidate topology** in TRAIN and DEV,
   although the TRAIN loss uses fixed hard-negative sampled groups.

A predicted occurrence's input consists of the mean of its own frozen Qwen
token embeddings and two preceding and two following token embeddings.
Positions are aligned through tokenizer offset mappings, not string search.
No special-case game parser or game-specific relation shortcut is added.

The **language backbone, LoRA, G1, the original G2 rank head, G2-0026,
and G2-0029 remain frozen**. The new occurrence representation encoders,
role projections, attention, graph updates and readouts are the trainable
components. This does **not** fine-tune Qwen itself. Distinguishing these
scopes is necessary to interpret a negative result.

## Controls and protocol

- Three predeclared seeds: `460331`, `460332`, `460333`.
- Same original **640 TRAIN** cases as G2-0029, **480 optimizer steps per
  seed**, same case sampling order, shared ranking/BCE loss. Different model
  capacities and parameter counts are explicitly reported; the models are
  compute-matched by updates, not guaranteed parameter-matched.
- `120` new, prompt-disjoint TRAIN cases fit calibration policies and `60`
  additional TRAIN cases audit them. No development example selects weights,
  calibrations, architecture, threshold, or seed.
- `320` new independent development cases plus `128` new withheld-topology
  cases. The G2-0030 development cohort is replayed unchanged to detect
  parent-scoring drift. The sealed TEST/challenge remains untouched.
- The reference policies and checkpoints are the exact immutable outputs of
  G2-0030. The fixed parent champion is
  `predicate_argument_s460038`. An additional G2-0031 TRAIN-only
  recalibrated G2-0026 baseline prevents attributing a threshold gain to
  the architecture.
- Training gold supervises only TRAIN losses/group sampling. Author graphs
  are used offline to score development outcomes. No gold enters candidate
  generation, span windows, message passing, model input or inference policy.
- Multiple correct destinations in one source/relation group remain legal.
  Existing global graph cap remains; no one-destination assumption is added.

## Predeclared acceptance criteria

All three seeds of an architecture must satisfy **every** condition on the
brand-new G2-0031 development set:

1. At least **+2.00 percentage points micro graph F1** over the **strongest**
   of original G2-0026, frozen G2-0030 recalibrated G2-0026, fresh TRAIN-
   recalibrated G2-0026, and frozen calibrated G2-0030 predicate–argument.
2. No decline in **exact graph accuracy** against the strongest control.
3. No increase in **gold-versus-hardest-negative ranking failures** against
   the best control.
4. No decrease in withheld-topology **graph F1** or ranking quality.

All comparison metrics derive from the same candidate inventory per prompt.
The candidate oracle is an offline label-informed ceiling, **never** an
inference rule. DEV success does not authorize production or certification.

## Run

From `nanojev` in the Main Computer repository:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-representation-bakeoff --device cuda
```

Default immutable output directory:

`runtime/reports/gamer-v1/graph-g2-representation-bakeoff-0031/`

Outputs:

- `g2_representation_bakeoff_report.json`
- `g2_representation_bakeoff_per_case.jsonl.gz`
- `g2_representation_bakeoff_development.safetensors`
- `g2_representation_bakeoff_frozen_policies.json`

## Limits and exit decision

Even if all three fail, that does **not** disprove the published model
families or the existence of sufficient lexical information. The backbone
itself is frozen; G1 predicted spans and top-24 inventory still determine an
upper bound. A failure does rule out these *specific* occurrence-level
adapters as a demonstrable improvement under the stated training budget.

No automatic retries, DEV-driven checkpoint choice, release, activation, or
promotion. The verified original training and source identity remain intact.
