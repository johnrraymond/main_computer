# G1 Oracle-Fabric frozen-decoder cutover bake-off

The G1 Oracle-Fabric cutover remains **blocked**. This is a research-only comparison of the existing trained head and the new deterministically initialized/retrained head. It does not train, promote, rewrite a decoder, issue a certificate, or unlock G2. All supplied trained weights and the Qwen/LoRA feature source are read-only.

Run from `main_computer\nanojev` after applying the changed-files-only patch at repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob --dataset ..\runtime\reports\gamer-v1\graph-span-curriculum-0005.jsonl --legacy ..\runtime\reports\gamer-v1\graph-g1-retention-retained-0007 --cutover ..\runtime\reports\gamer-v1\oracle-fabric-cutover-0001\stages\G1 --output ..\runtime\reports\gamer-v1\oracle-fabric-g1-decoder-bakeoff-0001 --device cuda
```

## Comparisons

The run checks dataset, adapter, and weight hashes for both G1 checkpoints and requires one legacy head and one deterministic-fabric head. It performs one frozen-Qwen feature capture per prompt and runs both frozen heads against that same representation. A stable catalogue of seven decoders receives only the frozen neural logits and valid-mask; no prompt text or gold enters any decoder.

- `global`: the exact existing maximum-sum interval decoder (baseline, with an internal parity check).
- `whole_linear`, `whole_sqrt`, `whole_linear_penalty_1p5`: existing tested whole-span and object-count-aware interval selection objectives.
- `type_actor_guard`, `type_action_whole`, `type_joint_whole`: predeclared actor/action-aware interval ranking. These are exploratory read-only candidates, not proven repairs.

Each head's decoder is selected **using development cases only** and only if exact-set rate, F1, and required actor/action/quantity/outcome recall do not regress relative to its own baseline. Otherwise the `global` decoder remains selected. Selection completes before historical challenge is read. The challenge was previously used in development certification and is **not a pristine independent holdout**. The sealed `test` split is never inspected.

## Oracle-only diagnostic upper bounds

The `oracle_assisted_ceilings` sections are **not inference algorithms**. They use gold labels only after all prediction choices have been calculated:

- `oracle_eligible`: an already eligible top predicted typed span matches a gold object, even if the global interval decoder rejected it.
- `oracle_nonnull_type`: the valid gold span's best non-null type is correct, even if null or confidence prevented admission.
- `oracle_forced_type`: a valid, character-aligned gold token span exists regardless of neural type or null score. This forces the gold identity and is strictly a structural coverage ceiling.

The gaps between these ceilings help distinguish decoder competition from null suppression, wrong type, and candidate coverage. Because they are gold-assisted, they **must never be used as deployed predictions or gate evidence**.

## Outputs

`report.json`: full dev and historical challenge metrics for all decoder variants and all upper bounds, selected variant per checkpoint, source hashes, and explicit `certification_authorized=false`, `production_promotion=false`, `g2_unlocked=false` flags.

`cases.jsonl`: individual differences, gold-spans recoverable under each upper bound, lost previously correct spans, and new false positives. It logs only hashed prompt identifiers and character-span coordinates.

The output directory must not exist already and cannot be inside either protected checkpoint. Both checkpoint hashes are reverified after running; the corpus and adapter are also reverified. A failed source or decoder check does not unlock the cutover.

## Limitations

This diagnoses whether existing scored candidates support better extraction. It cannot prove that any particular numerical default caused an error, cannot restore genuinely absent upstream information, and cannot certify a G1→G2 interface. No real checkpoint inference was run in the patch-build environment; execute the command on the machine containing the frozen Qwen/LoRA weights and data.
