# MCEL State-Aware Presentation Planner (fixture vertical slice)

This patch implements phases A–D of state-aware **fixture** presentation planning as
an extension of the existing MCEL semantic and presentation generator. It does not
change any live application, website builder, backend operation, source adapter, or
MCEL component registration.

## Input and policy

`tools/mcel_presentation_state.py` normalizes the schema
`mcel.presentation-state.v1`. Observations retain stable IDs, source, availability
(`ready`, `stale`, `unavailable`, `unknown`, `error`) and their original data. An
operation records state and whether its terminal outcome is verified. An unverified
`succeeded`, `failed` or `canceled` becomes `unknown` in the **presentation
projection only**, never in the source of truth. No observation is never interpreted
as healthy. Missing observations are not represented as zeros.

The pure policy produces `mcel.state-presentation-plan.v1`: a per-region visibility
and emphasis decision (`prominent`, `normal`, `compact`, `absent`) and an accessible
summary with the source observation IDs. An authored `require` constraint that
would be hidden is refused. Registered semantic elements are **not deleted**.

## Runtime composition and state changes

The existing generated MCEL runtime now consumes **precompiled** state decisions
from the same pure Python policy. In this patch, they are produced only for
explicitly labeled synthetic fixtures. The DOM continues to mount the full
registered MCEL component tree, while the canonical MCEL layout evaluator chooses
only the regions required by the current presentation. Absent regions relinquish
their grid areas. A selected record reveals its inspector; the default interface
does not assume the user selected anything. A user-view change clears stale
selection. The search field survives fixture transitions. Consequential controls
remain disabled.

This is not yet real-time runtime-state parsing or an authoritative worker / AI /
process source connection. The precompiled synthetic profiles are `idle`,
`unavailable`, `running`, `failure` (with a **synthetic verified receipt**),
`unknown`, and `empty`.

## Run

```text
python tools/mcel_layout_frob.py --mode semantic
python tools/mcel_layout_frob.py --mode semantic-compare
python -m pytest -q tests/test_mcel_presentation_state.py tests/test_mcel_presentation_planner.py tests/test_mcel_semantic_generation.py
```

The `semantic-compare` command uses the **same compressed/pattern-selected plan**
on both sides. `before` is the prior always-present presentation; `after` is the
new state-aware presentation. Both are generated, compiled through MCEL and
verified in Chromium. The existing shared PNG evidence system generates galleries,
manifests, full-page captures and human review checklists for default views and
synthetic idle/unavailable/active/unknown/failure states.

Output is in `runtime/reports/mcel-layout-frob/presentation-comparison/`.
All screenshots are synthetic and **not automatically approved as beautiful**.

## Gates

- All 11 Task Manager rows and their 54 declared semantic fields are retained;
  deferred fields remain inspectable. All registered component instances mount.
- Idle/unavailable => no permanent activity region; unselected => no empty inspector.
- Unknown terminal outcome => no fabricated success or verified failure.
- Verified failure => promoted to visible attention; unresolved unknown remains visible.
- The actual canonical MCEL layout must reflow without leaving empty named areas.
- Native search, keyboard/view/selection and disabled effects survive state transitions.
- Screenshots, source fingerprints, browser evidence and independent holdout
  application remain separate from human quality approval.

## Limitations

State is currently fixture-only, with no streaming producer or authorized operation
binding. Some pattern aesthetics and summary labels still need human review.
The policy is deliberately general-purpose and does not recognize application IDs.
