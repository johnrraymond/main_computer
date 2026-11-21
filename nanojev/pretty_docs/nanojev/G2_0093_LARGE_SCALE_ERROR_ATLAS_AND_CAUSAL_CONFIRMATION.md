# G2-0093 — Large-Scale Error Atlas and Causal Confirmation

This experiment answers a narrow question: what systematic G2 errors occur across
1,000 fresh author-annotated base graphs, and which error signatures replicate
on a separate confirmation cohort? It makes **no** repairs or promotions.

## Design

- 500 allocation and 500 encounter base graphs. Each base has four modifications:
  renaming, role swapping, added unrelated distractor, and action removal.
  All four transformations come from one base rather than four unrelated samples.
- Total 5,000 inputs: 700 base graphs (3,500 inputs) for diagnostic discovery,
  300 base graphs (1,500 inputs) reserved for confirmation. Counts are family balanced.
- Fixed G1-0082 identity, frozen G2-0087 and frozen G2-0092 Arm B stage-4
  checkpoint. Both heads receive the **same** predicted G1 objects per input.
- Decoder is the G2-0088 selected joint decoder. No thresholds, learned weights,
  checkpoint selection, hyperparameters, or graph constraints are fitted here.
- `prepare` commits separate discovery and confirmation gold files before
  inference. `predict` reads only prompt JSONL, not current experiment gold.
  `discover` opens only the discovery gold file, freezes error classes, then
  `confirm` opens the reserved confirmation gold. The scorer checks the blind
  prediction SHA locks and original lineage.
- Each blind case is an atomic compressed shard. `predict` resumes incomplete
  inference by skipping completed shards, checking their structure and hashes,
  and writing a final index lock only when all inputs are present.
- Errors are grouped by G1 reachability, G2 omission, extra edge, wrong type,
  source/destination object kinds, binding type, and graph family. Complete
  relationship sets, within-kind destination ranking, G1 reachability and
  counterfactual deltas are retained per case.
- A discovered error class must have at least 30 events, 20 affected inputs and
  10 affected **distinct base graphs** before it can be selected. The confirmation
  report measures affected base graphs (not correlated fuzz variants) with Wilson
  intervals and predeclared replication criteria. Inference, selection and
  confirmation are distinct phases.
- Limited causal probes on **both** cohorts use frozen, score-selected targets:
  a sham, pair-projection zero, encoder-source zero, coupler-source zero, and an
  unrelated-object control. For each probe, the scorer reports correction or
  damage to TP/FP decisions and observational layer separation. These are
  diagnostic, destructive interventions, not deployable fixes. They cannot
  by themselves prove a uniquely faulty layer.
- The test data still come from the same author generator: this is not
  independent-distribution certification. G2 is not certified; G3 is locked.

## Apply

Extract `G2_0093_LARGE_SCALE_ERROR_ATLAS_CAUSAL_CONFIRMATION_PATCH.zip`
into the **main_computer** repository root. No existing implementation or
checkpoint files are overwritten; only three files under `nanojev/` are added.

From `main_computer\\nanojev` using the existing `$py` interpreter:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_error_atlas_0093 run --device cuda
```

For a disposable pipeline-smoke rehearsal with a smaller fresh experiment and
an alternate output location, use the `--bases 20` and `--output` arguments.
This does not replace the full 1,000-base experiment.

## Recover or inspect individual phases

The `run` command invokes each phase in a fresh Python process. An interrupted
prediction phase retains completed per-case shards, but its final lock remains
absent until all 5,000 cases have been inferred. Resume from the same directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_error_atlas_0093 predict --device cuda
& $py -u -m tools.nanojev_gamer_v1_g2_error_atlas_0093 discover
& $py -u -m tools.nanojev_gamer_v1_g2_error_atlas_0093 confirm
& $py -u -m tools.nanojev_gamer_v1_g2_error_atlas_0093 probe --device cuda
```

The commands above are a recovery sequence; do not rerun a phase that has
already committed its lock. If `run` fails before preparation commits, inspect
the error rather than replacing existing committed evidence.

## Output

`../runtime/reports/gamer-v1/oracle-fabric-g2-large-scale-error-atlas-0093/`

- `seal/commit.json`, `prompts.jsonl`, separate discovery/confirmation gold
- `blind/shards/*.json.gz`, `blind/index.json`, `blind/lock.json`
- `discovery/hypotheses.json`, `summary.json`, `cases.jsonl.gz`, `lock.json`
- `confirmation/summary.json`, `cases.jsonl.gz`, `lock.json`
- `causal/probes.jsonl`, `summary.json`, `lock.json`
- `report.json` with lineage checks and remaining limitations

The final report is not a G2 certificate or authorization to train G3.
