# G1→G2-0052: Raw neural endpoint recovery and loss localization

## What actually failed in G2-0051

G2-0051 exactly replayed G2-0050 and improved graph-level candidate reachability only
from 7/32 to 8/32 on the 32 TRAIN-domain counterfactual probes. The G1 **positive-margin
catalog** supplied 840 of 864 required endpoints; 24 were missing. The corresponding
96 TRAIN graphs had 2,520/2,592 endpoints available. Those numbers are an offline
label-based audit, not evidence that G1's raw logits never represent the spans.

The previous proposal constructor could not inspect G1 proposals excluded by its
confidence filter. Consequently it could not distinguish a neural-score failure,
a downstream admission failure, or a token-to-character boundary mismatch.

## Code changes

- The G2-0040 frozen capture hook exposes the **raw G1 span logits and valid mask**
  only to this new research path. No author graphs or gold node spans reach the
  candidate builder. The G2-0051 path is unchanged when the hook is omitted.
- A bounded (192 occurrence) G1→G2 constructor preserves all G2-0051 endpoints
  and adds typed alternatives selected from frozen neural margins across each
  object kind. It accepts lower-confidence alternatives as competing candidates,
  not as asserted facts. Generic Unicode boundary trimming is considered while
  preserving internal punctuation. Type-compatible directed edges are enumerated
  without a legacy top-24 destination cutoff.
- Every new endpoint retains its original *token span*, semantic kind, character
  interval, and G1 neural margin for subsequent occurrence-level G2 learning.
- Gold annotation is accessed only **after** construction. The per-case audit
  records missing endpoint text, kind, span and cause, including whether the
  corresponding exact character boundary is expressible by any valid G1 token
  span. It measures candidate reachability, not selected-graph accuracy.
- The completed G2-0051 report, its ledger digest, its 8/32 reachability and its
  1/16 paired-reachability are checked before the new run. G2-0050's exact
  per-prompt replay check is still enforced inside G2-0051's shared source.

This is an **interface repair in an isolated research path**, not an update to
G1's deployed head or a claimed final solution. The raw neural G1 score may still
be too weak to rank missing objects within the bounded inventory; if a gold
endpoint has no matching token-aligned valid span, no change in confidence
threshold can recover it. The report will make that distinction explicit.

## Run

Apply the repository-root-relative patch at `main_computer`, then from `nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g1-g2-endpoint-repair --device cuda
```

Output: `runtime/reports/gamer-v1/graph-g2-endpoint-interface-repair-0052/`.
The per-case gzip ledger has an `interface_repair` record with each unresolved
endpoint and its cause. The compact console output includes the before/after
endpoint and graph coverage and a next-step decision.

## Acceptance

Engineering targets: >=99.5% endpoint coverage, >=31/32 reachable probe graphs
and >=15/16 complete counterfactual pairs, all on the unchanged TRAIN-domain
probe cohort. These are **targets, not guaranteed outcomes**. Candidate generation
must remain label-blind, monotone relative to G2-0051, and frozen-parent-safe.

No DEV/TEST gold is used; no production weights change; no G2 scorer retrains;
no certification or production promotion is authorized. A fresh sealed cohort
will be required before any generalization or release claim.
