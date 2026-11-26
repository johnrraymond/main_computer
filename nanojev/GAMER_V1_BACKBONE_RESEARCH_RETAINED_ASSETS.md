# Gamer v1 — retained research assets and evidence register

Record date: 2026-11-25 Pacific. This is an inventory of reported artifacts, identities and retention requirements, not a physical backup of model weights or the user's runtime directory. The uploaded reports were inspected directly. Their referenced checkpoints, manifests and locks reside on the operator machine; individual file existence and bytes there cannot be independently confirmed in this environment. Preserve the entire research directory, not only selected weights, because parent seals and hashes are required by later experiments.

## Operator storage and preservation policy

From repository root, retain `runtime/reports/gamer-v1/` as an immutable evidence tree, including all source manifests, seeds, configuration/identity records, checkpoints, per-case numerical features, selection receipts, prediction locks, scored results and generated RESEARCH_RECORD.md. Keep public prompts and secret gold files separate; access the latter only in the designated fit/scoring phase. Preserve the pinned HF model cache separately (model commit below). Do not merge the historical adapter into the bare model, delete failed arms, rewrite old seals, or use exposed answers for selection in the same lineage. For a new experimental configuration, use a distinct output directory. No production changes authorized by this register.

## Core historical model assets

| Asset | Identifier or location |
|---|---|
| Bare backbone | `Qwen/Qwen3-0.6B` |
| Resolved checkpoint commit | `c1899de289a04d12100db370d81485cdf75e47ca` |
| Tokenizer fingerprint | `41e00eccf531cffc2e562d38bdd879d41e5044ea279af5b73c6a32aabcc8fe04` |
| Chat template fingerprint | `41d5929bf73796beb66809ac700b2cf3ff81694f933e5c14d52b7fd6963c947d` |
| Historical LoRA | `transition-005/lora/best_adapter/` (relative to gamer-v1) |
| Adapter tree fingerprint | `9aa17c5e369a52b7fd045255284f939e2247b8084400543c764aff1731982915` |
| Historical S checkpoint | `g1-s-first-reference-0111/research-reference.safetensors` |
| S checkpoint SHA-256 | `90abd62da1e09758fc578f2a17ff25e472dcca98df7edeea0cb3af547bd7febe` |
| S vector fingerprint | `117fc21d50b7956000caa46ca0e949585ee0176023290377d1e5dee31e73d74f` |
| 0120 calibration prediction lock | `c5a300660fa086136b825f77425a9dc48a09ef9aeefc36e8ea989697cdeed870` |

Live receipt environment: torch 2.14.0+cu126, transformers 5.17.0, PEFT 0.21.2, float16, hidden width 1024. Preserve model receipt itself. Adapter and S fingerprints are historical comparator identities, not required active elements of the bare acquisition route.

## Retained research directories

All paths relative to `runtime/reports/gamer-v1/`. These are research roots, not release destinations.

| Experiment | Root | Status |
|---|---|---|
| 0111 | `g1-s-first-reference-0111/` | Historical S and reference evidence |
| 0113 | `g1-multilayer-reconstruction-0113/` | Cohort seal, 17 reader observations/fit evidence |
| 0114 | `g1-reader-forensics-0114/` | Completed historical forensics |
| 0115 | Preserve its integrated reconstruction output and resume shards | Full campaign incomplete; do not discard partial evidence |
| 0116 | `g2-multilayer-reconstruction-0116/` | Completed generator-family reconstruction |
| Integrity probe | Preserve complete semantic-integrity output | Completed diagnostic; failed generalization |
| 0117 | `g1-g2-joint-reconstruction-0117/` | Completed diagnostic |
| 0118 | `g1-g2-semantic-contrast-0118/` | Completed diagnostic |
| 0119 | `g1-g2-semantic-addressability-0119/` | Completed diagnostic |
| 0120 | `g1-g2-backbone-competence-0120/` | Calibration completed; other four-condition results not supplied |
| 0121 | `g1-g2-backbone-preserving-0121/` | Completed six-arm bakeoff |
| 0122 | `g1-g2-contextual-readout-0122/` | Completed six-arm bakeoff and oracles |
| 0123 | `g1-g2-elementary-binding-0123/` | Protocol delivered; live result pending |

## Selected per-arm checkpoints (reported SHA-256)

Within each 0121/0122 root the path is `arms/<arm>/selected.safetensors`. Keep adjacent `training.json`, per-arm `prediction-lock.json`, `predictions/`, root `selection.json` and all cohort seals. The following hashes are from the actual completed results, not recomputed from checkpoint bytes in this environment. Selection is per-arm validation selection and does not imply certification.

| Experiment / arm | Epoch | Checkpoint SHA-256 | Role |
|---|---:|---|---|
| 0121 / `A_bare_readers` | 2 | `d765ef6ea83a78c36576f54f6cb4d99f092696cea425ed31d278a59a05088027` | Matched bare comparator |
| 0121 / `B_bare_S` | 2 | `3db18c73158bb240c003c4f5f385678038886a284aca2df694a6935edb599816` | Diagnostic comparator |
| 0121 / `C_bare_SR` | 2 | `baf5271c3364bc36ddb7bd5b9b6d8bfb3be7c311a74e94451769e2e53293e138` | 0121 validation winner; preservation failed |
| 0121 / `D_direct_q` | 2 | `6c633f7082fc3872dc41260252ed6b4bdfc0a9b26b94dcf43d987bffa052d82b` | Diagnostic comparator |
| 0121 / `E_new_low_rank_q` | 2 | `03e9646db1d0f2fcf8856244f304c7d90560114fe4cc7a24cd09cc7f30d58187` | Diagnostic comparator |
| 0121 / `H_historical_adapter` | 2 | `b096046009a17c8f6a428b90b683607c8c1109aa981952c05b4aa5f83a50ae83` | Historical LoRA comparator only |
| 0122 / `A_bare` | 3 | `5b78ba60eb2dcf83ef06b83c0b27d3dc43bf9631021bbcab65933862762f58fc` | Retain primary G1 reference |
| 0122 / `B_fixed_R` | 2 | `7bfb24d88fd2a08128470ff8c009133102bc690b0f3cebb190b8d4a7bb6d8a94` | Diagnostic comparator |
| 0122 / `C_learned_R` | 6 | `9a6ad31ff84376f20160a1d85ce783982b9b35e471fb128634e02177cf492f0c` | Retain strongest 0122 G2 graph comparator |
| 0122 / `D_SR` | 6 | `89af361d716653295a2f00ad3928ad1ff1afd7b12b7639ed8f7da45d712daf99` | Validation winner, preservation failed |
| 0122 / `E_attention` | 4 | `a1c57a77033b08ba1ed32bde0ce54195fbd07c5eb4ff81bbd0d82b580d30d3a3` | Retain architecture/weights as comparator; 0123 fresh initialization |
| 0122 / `F_R_attention` | 4 | `7c4387e33a0d67c0f715e0364f5a13f8893f4f7f58cca46378196ee1ca6d5924` | Diagnostic comparator |

Report identities:

- 0121: `fb01e3b7cbb774af834807eaa1d79c566e2364a9062eba9fde91a6947b86eb7e`; selected `C_bare_SR`.
- 0122: `28b05e3453ff621957c184774778caa76a70169dc5fa64c1a0b1270ebf041d41`; selected `D_SR`.
- 0122 original operational report SHA-256 (as reported): `be1a3877c6b68a95669c4ecea1453972a381dca0b5698fc9461ad6bcdf68ccf7`.

## Supplied evidence sources

The following uploaded text reports were parsed or inspected in producing this record. SHA-256 is computed over the supplied file bytes, which may differ from the corresponding on-disk JSON due to transport or formatting. These are source citations for the ledger, not hashes of the original runtime records. Reports are not copied into this documentation-only patch; keep the original files and runtime reports.

| Scope | Supplied filename | SHA-256 |
|---|---|---|
| 0114 | `Pasted text (2)(20261124-201910).txt` | `cb623df48e52828e0ffe73ab62fc27e1a4a35681de43d902e88f8ffc9f5a5d8a` |
| 0116 | `Pasted text (2)(20261124-225321).txt` | `138a3a74155217b21155319f7a54fce9819ba4dc3c2ea626776b526bb9161373` |
| Integrity | `Pasted text (2)(20261124-231727).txt` | `f021661dbc8e1f4daccfbf7369523bed7d7aca015929f9724bd97e411dc8ceaa` |
| 0117 | `Pasted text(20261125-011923).txt` | `eea0a6a9d90d87396b8ae3f6d57e3d1b1c8b8d5fbf2b031f7bf50685f973ec07` |
| 0118 | `Pasted text(20261125-033509).txt` | `cd8bb76637160b9aabf395aa2ac06b3db8f427d7180bb0484582c368154c5907` |
| 0119 | `Pasted text (2)(20261125-081650).txt` | `ada000043015e137b98c639280368767574bddd9ff929dc5348e5f996a49e46b` |
| 0120 calibration | `Pasted text (2)(20261125-224751).txt` | `f56e0d0c81e3cbb2ed08cf00dd4b855f65c2cb33fb28957a26b1b17422764783` |
| 0121 | `Pasted text(20261126-003132).txt` | `0de5b456cb33e8711a34989bdfb037208349d2f278311fef2240cbdce57640fe` |
| 0122 | `Pasted text(20261126-033642).txt` | `fb189f4b62d027e74c2329084307623672853332786ec4d0755bd5355dc5cc9c` |

## Reproducible code archives

Preserve the delivered patches as well as their installed source files. The latest 0120 correction supersedes its original executing module; the original author source remains necessary. These are hashes of the locally available archives, not claims of installed source parity.

| Archive | SHA-256 |
|---|---|
| `G1-0115-Endpoint-Serialization-and-Seal-Resume-Fix.zip` | `5972e105a14322679ba4cfb8435827400dc9b009d5e7b301a97a62430baaafee` |
| `G1-0115-Integrated-Semantic-Reconstruction-Certification-Laboratory.zip` | `d065f168e4fba80307ec5ff35a13103fee3add4106f4c3f41550a2e10ef7df43` |
| `G1-G2-0117-Joint-Semantic-Reconstruction-Laboratory.zip` | `68620bf159dec72aa1cfa8ad6167f6460ed5edaa8def645834838472a8c56302` |
| `G1-G2-0118-Semantic-Contrast-Discrimination-Laboratory.zip` | `db40fb21c4115968446c6df9228535e317a119ff0d3bdfaa3b070f310ff5614b` |
| `G1-G2-0119-Semantic-Addressability-Laboratory.zip` | `eaab661f54d3359780ecb0cb910ee8f99c27c363664d504c517e2bcacb3ac906` |
| `G1-G2-0120-Backbone-Competence-and-Adapter-Interference-Laboratory.zip` | `0e523ddc0ce919144f4d4fbeaa7b7d1027dceac310e8b22d9eaca97c949f4ede` |
| `G1-G2-0120-Frozen-Adapter-Resume-Fix.zip` | `7b3fd7615916a7c34d09a0ae59882cec765bf7af98fb1f67f42f3a3140c314c9` |
| `G1-G2-0121-Backbone-Preserving-Semantic-Acquisition-Bakeoff.zip` | `449580a160d7a0674959bed9e5fd943e170e8c2b1bbc0f3e5ade1da3c209fcba` |
| `G1-G2-0122-Frozen-Backbone-Contextual-Readout-Bakeoff.zip` | `b6785feadd80131425830d0234a461934fa9a1a46516ee89d3e7b62ff85f502d` |
| `G1-G2-0123-Elementary-Semantic-Binding-Mechanism-Laboratory.zip` | `3302c182d3c560de888e20f0a8296d050c94ee1b769ae6fe7391e52e2679a5f4` |
| `G2-0116-Multilayer-Relationship-Reconstruction-Laboratory.zip` | `ff2d0acf28f566f6d88dbe144840ed5c6416d926ec5290f5b139e8f5ea362d50` |
| `NanoJEV-Backbone-Research-Documentation-20261124.zip` | `2c0b0f529d6e69f3d151d479402e3288014c08135492c03f78ba874980da2498` |
| `NanoJEV-G1-0115-G2-0116-Semantic-Integrity-Verification.zip` | `eeea9c900b11affe25bd98cf86c835e67b35e94838f62362cc5ecac736aa700e` |
| `NanoJEV-Semantic-Integrity-Answer-Guard-Fix.zip` | `e5a4a41db5ead6243f6d72eb71b79923c57c1aad06dbc4aa7e3f55c4fb80a8c8` |
| `NanoJEV-Semantic-Integrity-Frozen-Decoder-Failure-Fix.zip` | `460684f0ca8b3d08115d7f081801b49125a593f5a709c2619a6b3c78e280444b` |
| `NanoJEV-Semantic-Integrity-Historical-Provenance-Fix.zip` | `799841bdead64d6163b0417312a47bd9aaca767787075ad3ce7238fbbe1f0611` |

## Pending work and limits

No 0123 live fit or heldout result has been provided. No complete 0115 or remaining 0120 generation campaign is documented here. Preserve these incomplete states; never silently fill their gaps. A completed research result is not a production checkpoint promotion. No general G2 semantic mechanism is established. G3 remains locked under the existing release contract. The document patch neither exports nor modifies any model/checkpoint/runtime evidence.
