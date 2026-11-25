# G1-0114 — semantic reader complementarity and reconstruction forensics

Status (2026-11-24): completed actual saved-checkpoint CUDA run; validation-selected architecture_6: 157/192 core, 87/192 strict, 5,452 TP, 36 FN, 117 FP; 98.62% F1. Follow-up: [G1-0115](G1_0115_INTEGRATED_SEMANTIC_RECONSTRUCTION_CERTIFICATION.md). See [current research guide](GAMER_V1_BACKBONE_RESEARCH_CURRENT.md) and [evidence ledger](GAMER_V1_BACKBONE_RESEARCH_EVIDENCE_0103_0114.md).

## Why this experiment exists

G1-0113 compared 17 readers on 192 held-out prompts (24 independent bases). The validation-selected unconditioned four-layer reader recovered all required objects in 129 cases. The retrospectively strongest unconditioned L14+21 reader recovered all in 136. The union of whole-reader core successes covered all 192. Strict extraction remained substantially lower: 73 and 92 respectively. This means the ensemble has observed coverage that no single reader operationally attains. We need to discover whether a usable selection or reconstruction rule can exploit that competence without the answer.

## Design and safeguards

The executable is `tools/nanojev_gamer_v1_g1_reader_forensics_0114.py`. It uses all 17 0113 readers, both original and S-conditioned, from the 0113 `fit` directory. It verifies the source report, seal, cache manifest, selection and per-arm checkpoint hashes and test prediction identity. It replays validation and saved test predictions and obtains native score evidence from cached hidden states. No new learning or backbone execution. Do not treat 0114 as a fresh blind test: its policy selection uses validation, but the 0113 test outcomes informed the research direction and have been examined.

For every candidate, retain its typed character span, token coordinates, native confidence/margins and reader support. Composite-decoder selection and native neural confirmation are distinct signals. Examine unique true and false contributions, reader-pair complementarity, confidence AUC/reliability, agreement frequency and precision by object kind. Classify false positives by observable exact-boundary wrong type, wrong boundary/same kind, overlapping wrong kind, or disjoint extra; these are failure descriptions, not causal proof. Investigate whether apparent extras in alias/pronoun notes are valid linguistic objects outside the current required-object annotation contract.

The implementation compares 88 policies: individual readers, whole-reader consensus/confidence and object-level policies using voting, architecture/source groups, confidence rescue, native confirmation and subsets. Do not assume correlated reader votes are independent. The compatibility solver rejects conflicting token and character spans and maximizes admitted candidate weight. Its feasibility assumptions must be checked against gold annotations in the diagnostic oracle; genuine overlapping objects would require revisiting those assumptions, not silently dropping them.

Validation selection uses complete core, strict exact, then F1. Test results for all policies are retrospective comparisons; report the validation-locked selection separately. The following ceilings are evaluated using gold only offline:

- Whole-reader core: some reader has every required object (extras allowed).
- Whole-reader strict: some reader predicts exactly the required set.
- Union core: every required object occurs somewhere in the candidate union.
- Compatible union strict: a gold-guided compatible subset exactly recovers the gold set under the solver's constraints.

A 100% ceiling cannot be claimed as deployable accuracy. Gold may not guide runtime selection, thresholds, or tie breaking on the test cohort.

## Run

Apply the 0114 source patch at repository root; from `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_reader_forensics_0114 audit
& $py -u -m tools.nanojev_gamer_v1_g1_reader_forensics_0114 selftest
& $py -u -m tools.nanojev_gamer_v1_g1_reader_forensics_0114 run --device cuda
```

Use the same source and output flags when resuming. Defaults read `runtime/reports/gamer-v1/g1-multilayer-reconstruction-0113/fit/` and the adjacent parent cache/seal, and write `runtime/reports/gamer-v1/g1-reader-forensics-0114/`. Optional `--source`, `--fit-name`, `--output`, `--root` are defined by the executable. Output must not be inside the parent experiment.

| Output | Purpose |
|---|---|
| `result.json` | Selected policies, retrospective test comparison, oracle and unique-reader metrics, lineage and scope flags |
| `selection.json` | Validation scores and selected policies |
| `test-forensics.json` | Agreement, confidence, object categories, pairwise evidence and false-positive examples |
| `validation-forensics.json` | Corresponding validation diagnostics |
| `object-evidence.json` | Candidate-level evidence including missed targets for offline analysis |
| `selected-case-level.json` | Selected reconstruction, missed and extra objects, oracle readers |

## Read the result and decide

Compare the locked policy with original frozen G1 (68 core/44 strict), frozen S (85/46), the prior validation winner (129/73), and the retrospective L14+21 unconditioned comparator (136/92). Report gains and harms, precision and recall separately, boundary errors, G2 reachability if available, and confidence/consensus failures. Do not use a retrospective winner for certification. If core improves while strict or control retention deteriorates, diagnose the exact tradeoff. If object-level integration fails, examine candidate admission, conflict optimization and correlated votes separately. If none of the policies succeeds, preserve the reader inventory and use failure evidence to design the next test, not a blind larger training run.

Completion record to fill from the operator's actual output: report SHA-256; selected policy; validation and test metrics; four oracle ceilings; top unique contributions; remaining failure classes; paired base-cluster gains/harms; decision and next experiment. No results have been entered prematurely. Canonical checkpoints unchanged; G3 locked.

## Measured outcome and successor

See the updated evidence ledger for actual 0114 outcomes, including the whole-consensus comparator and strict oracle. Preserve all 0114 artifacts; G1-0115 reuses the exact per-reader evidence and checkpoints and treats this cohort as exposed development. No need to rerun 0114 if the saved evidence is intact.
