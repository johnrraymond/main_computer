# GAMER v1 — Oracle-Fabric cutover: architecture, evidence, and blocked G1 boundary

**Evidence checkpoint:** 2026-11-18  
**Campaign:** `oracle-fabric-0001`  
**Status:** `G1 DEVELOPMENT_GATE_BLOCKED`; **legacy remains authoritative**  
**Scope:** Deterministic default weighting, oracle-guided stage coupling, the first coordinated retraining attempt, and frozen G1 decoder attribution. This is a research record and nonregression contract, **not** a release certificate.

## 1. Executive finding

We constructed new deterministic trainable defaults, verified their numerical/default-behavior oracles, and trained the integrated GAMER G1 head for 400 steps. Training converged in loss, but G1 failed certification and the campaign stopped before G2. Read-only comparisons then established that both the legacy and new frozen G1 heads benefit from a whole-span-aware decoder, without weight updates. The remaining accuracy gap is large and concentrated around complete-span selection, null admission, and actor recall.

The evidence supports **preserving deterministic default weighting and investigating the output interface**. It does **not** support declaring G1 certified, resuming G2–G7, claiming that deterministic weights solved numerical learning, or promoting an inference decoder based on an oracle-assisted ceiling.

**Invariant:** Retain every learned lesson, including negative results. Replace numerical defaults only with explicitly verified behavior. The legacy system, trained checkpoints, dataset fingerprints, and historical diagnostic reports remain comparison evidence and rollback sources.

## 2. Governing architecture and what actually exists

### 2.1 Intended system design

The design objective is to eliminate randomly sampled **new trainable weight initialization** across coupled components. Oracle-defined numerical partitions, scales, offsets, default mappings, and semantic contracts establish a known initial computation before training. Independently supervised stages connect through interfaces whose information preservation is explicitly measured. A full-system cutover is atomic and only follows end-to-end acceptance.

A useful conceptual decomposition is `W = W_default + ΔW`: `W_default` implements the specified initial mapping; `ΔW` is the subsequently learned correction. This is a **design principle**, not a claim that every parameter in the existing implementation is stored as an immutable base plus a separate delta.

### 2.2 Implemented in the inspected cutover source

- `tools/nanojev_deterministic_fabric.py` supplies a base-2 van der Corput coordinate, exact dyadic bit/residual oracle, signed/zero-anchored numerical encoding, deterministic phase/spectral tensor fills, and deliberate positive class-0 (null/abstention) biases.
- `DyadicDecisionManifold` uses base-2 partitions and offset sinusoidal channels; its discrete bits are **not differentiable gradients**. The eight-bit, 256-point numerical reference oracle observed **128/128** first-bit balance and **256 distinct binary codes**, and passed a sign/zero check. This establishes behavior **on that reference grid only**, not uniform real-world inputs or arithmetic competence.
- The numerical magnitude channel `|x|/(1+|x|)` is bounded; large-magnitude precision remains an explicit risk. Deterministic offsets do not create Shannon information, establish globally smooth loss, or guarantee the trained network will cover every numerical regime.
- `new_graph_head` and `new_triple_set` use meta-device construction plus deterministic parameter filling. The preparation verified matching repeated graph-head state digests without changing PyTorch's RNG state. The **new trainable heads** do not require sampled random defaults; the pretrained Qwen backbone and source LoRA are frozen legacy-trained components and are **not retroactively nonrandom**.
- The integrated graph head includes six explicit interface couplers between G1–G7: identity base mapping and an initially zero trainable correction, where dimensions permit identity. This provides a defined starting interface, **not proof** that separately trained hidden manifolds are semantically aligned.
- The separate historical G2-0062 `TripleSetPredictor` has a prepared deterministic **comparison default**, but the cutover campaign does **not** train that predictor on newly captured G1 output. Its old capture cannot be relabeled as new-system integration.
- The runnable campaign follows the existing *integrated* `GamerGraphHead` G1→G7, stage-by-stage oracle curriculum. “One coordinated retraining campaign” means a common architecture, initialization lineage, and stage-gated run; it does **not** mean that all seven stages were simultaneously optimized. The actual campaign stopped at G1.

**Source references:** [`nanojev_deterministic_fabric.py`](../../tools/nanojev_deterministic_fabric.py), [`nanojev_oracle_fabric_cutover.py`](../../tools/nanojev_oracle_fabric_cutover.py), [`ORACLE_FABRIC_GAMER_CUTOVER.md`](../../ORACLE_FABRIC_GAMER_CUTOVER.md), and [`GAMER_V1_STAGE_CONTRACTS.md`](GAMER_V1_STAGE_CONTRACTS.md).

### 2.3 Default behavior is a measured initial contract

Before training, the prepared numerical oracle returned `signed_zero_oracle=PASS`, the G1 graph-head zero-hidden-input oracle returned `G1 ZERO-INPUT ABSTENTION: PASS`, and the G2 comparator zero-node oracle returned `G2 ZERO-NODE ABSTENTION: PASS`. These are deliberately **narrow smoke tests**. They do not certify real-prompt semantic behavior or interface equivalence.

| Immutable preparation item | Recorded SHA-256 |
|---|---|
| Graph-head **default state digest** | `bf2a773bba9fb823b7ccc9e8eb1ea5d978332a576065eae33549f616bf4fcc88` |
| Separate G2 comparator **default state digest** | `d9f494fa4218f62cf83d8636d27aa85010836016aa32e2c8c6e0b1ce6c818a1a` |
| Legacy G1 **weights file** selected for the campaign | `114150ae0ef351a2bd365b63c6a7fd70ac53d973d673577c5a910f6b8e1a7c90` |
| Legacy G2-0062 **step-4900 weights file** | `16cdc9c5937befe08fc87ba53a13f2b2e6f442676377cad729d235ae08fabddd` |
| G1/GAMER dataset used | `24415964d7e1de2cbd7dab24717a8f8bdb18e05aecac35fb161b40985d591eb2` |
| Frozen Qwen/LoRA source-adapter fingerprint | `9aa17c5e369a52b7fd045255284f939e2247b8084400543c764aff1731982915` |

*The default state digest is a canonical digest of the model state; it is not the SHA-256 of the serialized `.safetensors` file. The latter is pinned separately in `campaign.json`.*

## 3. Campaign discovery, preparation, and training

### 3.1 Provenance and ambiguity protection

`discover` found **21** plausible historical G1 checkpoints and **one** matching step-4900 G2 checkpoint. `prepare-auto` refused the ambiguous G1 choice (`CUTOVER_AMBIGUOUS_LEGACY_SELECTION`), which is correct fail-closed behavior.

The explicit campaign selected:

- Reference G1: `runtime/reports/gamer-v1/graph-g1-retention-retained-0007`.
- Dataset: `runtime/reports/gamer-v1/graph-span-curriculum-0005.jsonl`.
- Frozen adapter: `runtime/reports/gamer-v1/transition-005/lora/best_adapter`.
- Reference G2: `runtime/reports/gamer-v1/graph-g2-0062-5000-4/checkpoint-000004900.safetensors`.
- New campaign: `runtime/reports/gamer-v1/oracle-fabric-cutover-0001`.

This is a *lineage-coherent* G1 reference, not proof that it is the best among 21 historical G1 candidates. The legacy G1 weights were **not used to initialize** the new head.

The actual outputs were `CUTOVER_PREPARED ... PREPARED_NOT_TRAINED` and `CUTOVER_VERIFIED oracle-fabric-0001`.

### 3.2 First coordinated retraining attempt

The executed command was:

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover run --campaign ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001 --device cuda --steps 400 --accum 4 --g1-lr 0.0003 --lr 0.0001
```

The frozen Qwen/LoRA capture completed **1,200/1,200** prompts; G1 training completed **400/400** optimizer steps. The logged loss fell from **17.439333 at step 1** to **0.196641 at step 400**. Low training loss did not imply correct complete-span decoding or certification.

The campaign gate printed:

```text
CUTOVER_GATE {"next_gate": false, "stage": "G1", "status": "DEVELOPMENT_GATE_BLOCKED"}
CUTOVER_BLOCKED G1; legacy remains authoritative
```

No G2–G7 training, production promotion, or sealed test is documented for this cutover. The G1 certificate contains four blockers:

`dev:object_exact_rate_below_threshold`; `dev:missing_or_inaccurate_actor`; `challenge:object_exact_rate_below_threshold`; `challenge:missing_or_inaccurate_actor`.

### 3.3 Original G1 evaluation (before decoder bake-off)

| Metric | New G1 development (80) | New G1 historical challenge (32) |
|---|---:|---:|
| Exact object sets | **13/80 = 16.25%** | **12/32 = 37.50%** |
| Object micro-F1 | **94.72%** | **95.56%** |
| Object macro-F1 | 91.59% | 92.09% |
| Actor recall | **86/120 = 71.67%** | **36/48 = 75.00%** |
| Action recall | 929/1039 = 89.41% | 366/401 = 91.27% |
| Quantity recall | 580/583 = 99.49% | 227/227 = 100% |
| Outcome recall | 484/484 = 100% | 188/188 = 100% |

Existing advancement thresholds: exact object sets **at least 80% on development and 70% on historical challenge**, and **at least 82% recall per required kind**. Both exact-set and actor-recall gates failed on both splits. The development-versus-challenge ordering must not be interpreted as evidence of generalization; the challenge cohort is already historically exposed.

**Primary evidence:** `runtime/reports/gamer-v1/oracle-fabric-cutover-0001/certificates/G1/certification.json` and `evaluation/G1-G1-{dev,challenge}/report.json` inside that campaign.

## 4. Frozen-model failure attribution (no retraining)

Read-only `diagnose-g1` compared the **same 80 development cases** and **2,226 expected objects** for the frozen legacy and cutover G1 heads.

| Attribution / metric | Legacy G1 | New G1 |
|---|---:|---:|
| Gold token candidates supported | 2,226 | 2,226 |
| Gold candidates whose top type was correct | 2,131 | 2,136 |
| Correct reconstructed objects (diagnostic threshold sweep) | 2,084 | 2,080 |
| Object F1 (diagnostic threshold sweep, 0.05–0.40) | 94.96% | 94.74% |
| Exact object sets | 13/80 | 13/80 |
| `null_wins` missed objects | 95 | 83 |
| `overlap_blocked` missed objects | 46 | 56 |
| `wrong_type` missed objects | 0 | 7 |
| `below_threshold` missed objects | 2 | 1 |

*These diagnostic sweep metrics are not exactly the official `.5`-threshold evaluation values in §3; compare like with like.* Lowering the confidence threshold from 0.40 to 0.05 did **not** change reconstructed sets in either model; threshold tuning alone was therefore not a plausible repair.

The initial span-mismatch review identified a systematic tendency to shorten complete spans, especially actions, rather than random displacement. A whole-span selection rule was therefore a testable *decoder* hypothesis. Attribution counts describe observable failure categories, not a proven causal location of every lost object.

**Read-only reports:**

- `runtime/reports/gamer-v1/oracle-fabric-g1-diagnosis-0001/summary.json` and `misses.jsonl`.
- `runtime/reports/gamer-v1/oracle-fabric-legacy-g1-diagnosis-0001/summary.json` and `misses.jsonl`.

**Interpretation:** The new deterministic head reproduces much of the legacy system's G1 problem. It does not establish that the defaults caused it, nor does the small F1 difference establish parity on every semantic subgroup.

## 5. Frozen decoder bake-off: what is now established

The read-only experiment `tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob` used **unchanged weights**, the same frozen Qwen/LoRA hidden-state capture for each prompt, and seven predeclared decoders:

`global`, `whole_linear`, `whole_sqrt`, `whole_linear_penalty_1p5`, `type_actor_guard`, `type_action_whole`, `type_joint_whole`.

The original `global` output was explicitly checked for exact parity with the existing decoder. `whole_linear` multiplies each eligible span's positive margin by its width in the weighted nonoverlap optimization. Both checkpoint-specific **development-only** selectors chose `whole_linear`. Neither oracle-assisted ceilings nor challenge scores were used to select that rule.

### 5.1 Ordinary label-blind inference results

| Frozen checkpoint / metric | Original `global` | DEV-selected `whole_linear` | Change |
|---|---:|---:|---:|
| **New G1 DEV** object F1 | 94.715% | **96.283%** | +1.568 pp |
| **New G1 DEV** exact sets | 13/80 | **21/80** | +8 |
| **New G1 DEV** actor recall | 86/120 = 71.67% | **97/120 = 80.83%** | +11 objects |
| **New G1 DEV** action recall | 929/1039 = 89.41% | **950/1039 = 91.43%** | +21 objects |
| **New G1 historical challenge** object F1 | 95.556% | **96.842%** | +1.287 pp |
| **New G1 historical challenge** exact sets | 12/32 | **13/32** | +1 |
| **New G1 historical challenge** actor recall | 36/48 = 75.00% | **43/48 = 89.58%** | +7 objects |
| **Legacy G1 DEV** object F1 | 94.941% | **96.572%** | +1.631 pp |
| **Legacy G1 DEV** exact sets | 13/80 | **19/80** | +6 |
| **Legacy G1 historical challenge** object F1 | 95.361% | **96.653%** | +1.292 pp |
| **Legacy G1 historical challenge** exact sets | 7/32 | **9/32** | +2 |

For the new G1 on development, correctly identified objects increased from **2,079 to 2,111**, while aggregate false-positive objects fell from **85 to 48**. These are aggregate counts; they do not prove absence of *individual* new false objects.

**Key result:** Improving final-span selection without changing weights recovers measurable information from **both** trained heads. The original decoder was a shared weakness. The fact that candidate information is present does **not** mean all missing objects are recoverable by any label-blind decoder.

### 5.2 Oracle-assisted ceilings — diagnostic only

| Oracle-assisted DEV condition | New G1 | Legacy G1 | Meaning |
|---|---:|---:|---|
| Oracle selects **eligible** neural proposals | **28/80** exact; 2,135/2,226 correct | 20/80; 2,129/2,226 | Ceiling *within unchanged eligible typed proposals* |
| Oracle uses valid gold spans, highest **non-null** type | **71/80** exact; 2,217/2,226 correct | 75/80; 2,221/2,226 | Shows potential after removing null dominance, **with gold span coordinates supplied** |
| Oracle **forces** the gold span type | 80/80; 2,226/2,226 | 80/80; 2,226/2,226 | Architectural/label-assisted ceiling; correct by construction, **not learned inference** |

For the new G1, the ordinary eligible ceiling leaves **91** of 2,226 gold objects unrecovered. Moving from eligible proposals to a gold-span, correct-non-null-type diagnostic changes the result dramatically, but the latter uses inaccessible inference-time information and cannot be used as a deployable rule or as evidence that the decoder alone can reach 71/80.

The challenge corpus has already been inspected and is **not a new sealed generalization test**. The experiment's reported status is `FROZEN_DECODER_DEV_EXPERIMENT_NOT_CERTIFICATION`, with `training_performed=false`, `weights_modified=false`, `g2_unlocked=false`, `certification_authorized=false`, `production_promotion=false`, and `sealed_test_used=false`.

**Primary evidence:** `runtime/reports/gamer-v1/oracle-fabric-g1-decoder-bakeoff-0001/report.json`, `cases.jsonl`; implementation in `tools/nanojev_gamer_v1_g1_oracle_decoder_cutover_frob.py`.

### 5.3 What did **not** change

The official G1 certificate still fails. The selected decoder yields **21/80 = 26.25%** exact development sets, below the required 80%; development actor recall **80.83%**, below the required 82%; and historical challenge exact sets **13/32 = 40.625%**, below the required 70%. No G2 gate is unlocked. The decoder experiment is not a production edit.

## 6. Historical G2 lessons retained during the cutover

The full-system objective is graph and decision correctness, not local F1 in isolation. Preserve these prior findings in the next design:

| Experiment | Supported observation | Do **not** infer |
|---|---|---|
| G2-0062 frozen step 4900 | 81.23% edge F1; **4/96 exact graphs** | Successful complete-graph reasoning |
| G2-0064 | Oracle-corrected endpoints expose a substantial upper bound (both endpoints ~91.82% F1) | Oracle endpoint selection is deployable |
| G2-0065 | Learned endpoint/admission alternatives offered at most small gains (~+0.98 pp in the tested arm) | Endpoint reconstruction is solved |
| G2-0066 | Under gold-aware slot alignment, correct endpoints often rank top-1 (source ~93.1%, destination ~90.2%) | These are valid label-blind endpoint accuracies |
| G2-0067 | Joint graph assembly interventions largely reverted to baseline; conservative degree consistency yielded ~+0.27 pp | Graph coordination alone closes the gap |
| G2-0068 | Stronger null supervision reduced training-set unmatched false admissions but did not improve held-out graph F1; all continuation arms regressed or failed to show a robust gain | A lower null rate means globally better graphs |
| G2-0069 | Rate **5×10⁻⁴** degraded the continued model; **3×10⁻⁵** avoided most regression; restoring AdamW did not rescue the high rate; best CAL-selected evaluation ~**81.60%**, +0.37 pp with interval spanning zero | Proven better-than-frozen continuation or optimizer-reset root cause |

On G2-0069's historical EVAL, **93/96** graphs were fully candidate-reachable; this is below the preexisting **98%** candidate-reachability target. Earlier real-capture tests also found reachability gaps. Missing candidates cannot be corrected by downstream edge scoring.

Additionally preserve prior NanoJev numerical sign/zero-line and boundary failures as explicit future tests: signed magnitude, exact zero, nearby sign reversals, carries, numerical precision, and actor/role reversals. The presence of deterministic numerical weights does not certify any of these capabilities.

G2-0067–0069 JSON evidence was hash-pinned in the campaign when available. The G2-0062 set predictor is a **different model family** from the integrated G1–G7 graph-head stages; compare outcomes only under their actual contracts.

## 7. Locked acceptance ledger and next experiment

### 7.1 Decisions now locked

1. **Keep the current campaign blocked at G1.** No threshold reductions, gate bypass, stage advancement, sealed-test opening, or production promotion.
2. **Preserve immutable references.** The original G1 and G2 weights, cutover default digests, source/data hashes, trained but uncertified G1 checkpoint, certification record, and decoder reports remain intact.
3. **Keep deterministic default weighting as the design baseline.** Preserve explicit abstention, 50/50 oracle-grid partition, prescribed offsets, nonrandom parameter filling, and measured default behavior. Do not claim the entire pretrained stack lacks stochastic history.
4. **Preserve `whole_linear` as a demonstrated research decoder candidate**, not an automatically approved production replacement. Its benefit on both models is real on the measured cohorts; its certification status is unchanged.
5. **Maintain oracle separation.** Gold labels are permitted in offline attribution/ceilings but must never enter ordinary inference, decoder selection, or the supposed model input manifold.
6. **Do not call the system accepted until each implemented interface and the actual downstream consumer are exercised.** In particular, the separate historical G2 predictor requires new G1-conditioned recapture and interface-lineage verification; a prepared comparator is not that evidence.

### 7.2 Recommended next read-only experiment

Investigate **candidate admission and null suppression** on the frozen logits **before retraining**. Predeclare label-blind admission and span-selection arms; retain original global and `whole_linear` as controls; calculate which of the 91 missing eligible proposals are null-dominated versus wrong-type, invalid, or otherwise unsupported. Separate oracle-assisted ceilings from deployable predictions. Choose a candidate only on development and measure actor/action recall, exact object sets, false positives, and collision behavior; use a *newly reserved* holdout for genuine prospective generalization after design decisions are fixed.

Only after this analysis should we decide whether to change the deterministic default weighting, supervision, or interface coupler in the next **full, consistently versioned** retraining campaign. Do not interpret short-term decoder gains as reason to resume the already-blocked G2 branch.

### 7.3 Release gates not yet met

| Gate | Current evidence | Status |
|---|---|---|
| Default numerical construction reproducible | G1/G2 default digests and reference-grid oracles | **Passed (bounded scope)** |
| Initial default abstention | Zero-input G1 and zero-node G2 oracles | **Passed (bounded scope)** |
| G1 complete-object DEV gate | 21/80 with research decoder, 13/80 original; required ≥64/80 | **Blocked** |
| G1 actor DEV recall | 97/120 with research decoder; required ≥82% | **Blocked** |
| G1 historical-challenge exact sets | 13/32 with research decoder; required ≥70% | **Blocked** |
| New G1→separate G2 predictor coupling | No fresh lineage-verified capture/consumer evaluation | **Unverified** |
| G2–G7 coordinated-stage certification | Not run after G1 block | **Not started** |
| Independent sealed end-to-end test | Not accessed | **Not certified** |
| Production promotion | Forbidden by campaign and bake-off | **Not promoted** |

The new defaults are retained as an **experimental foundation**; a higher-dimensional representation, a low training loss, a high candidate-type rate, or an oracle-assisted ceiling cannot substitute for the complete system's acceptance.

## 8. Reproducibility and artifact map

Commands below are **read-only diagnostics**; they do not train, modify checkpoints, or open sealed tests. Commands must be run from `main_computer\nanojev` using the NanoJev Python interpreter `$py`. Use new output directories on reruns because the bake-off output is immutable.

```powershell
& $py -u -m tools.nanojev_oracle_fabric_cutover verify --campaign ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001

& $py -u -m tools.nanojev_gamer_v1_graph_curriculum diagnose-g1 --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl --checkpoint ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-diagnosis-0002 --device cuda

& $py -u -m tools.nanojev_gamer_v1_graph_curriculum diagnose-g1 --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl --checkpoint ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 --output ..\runtime\reports\gamer-v1\oracle-fabric-legacy-g1-diagnosis-0002 --device cuda

& $py -u -m tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-decoder-bakeoff-0002 --device cuda
```

Source and evidence of record:

- `nanojev/ORACLE_FABRIC_GAMER_CUTOVER.md` — executable cutover command and safety contract.
- `nanojev/tools/nanojev_deterministic_fabric.py` — default-weight, numerical-grid and coupler construction.
- `nanojev/tools/nanojev_oracle_fabric_cutover.py` — discovery, prepared-state verification, stage-run and fail-closed certification gate.
- `nanojev/tools/nanojev_gamer_v1_g1_oracle_decoder_cutover_frob.py` — frozen research decoding and independent oracle ceilings.
- `runtime/reports/gamer-v1/oracle-fabric-cutover-0001/campaign.json` — default digests, frozen source/reference hashes, and research-evidence pins.
- `runtime/reports/gamer-v1/oracle-fabric-cutover-0001/certificates/G1/certification.json` — actual G1 rejection and exact gate thresholds.
- `runtime/reports/gamer-v1/oracle-fabric-g1-decoder-bakeoff-0001/report.json` — selected decoders, dev/challenge evaluation, ceilings and no-mutation flags.
- `runtime/reports/gamer-v1/oracle-fabric-g1-decoder-bakeoff-0001/cases.jsonl` — per-case recovered, lost, and new-false object evidence.

**Documentation integrity rule:** If a result changes, append a dated evidence revision with its checkpoint and report hashes; do not silently rewrite past results as though they had always been true. A future passing campaign supersedes this **blocked** status only with a separately preserved acceptance record.
