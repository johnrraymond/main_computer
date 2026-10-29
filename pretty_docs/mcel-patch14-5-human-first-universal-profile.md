# Patch 14.5 — Human-first composition within the single universal profile

## Scope and decision

Patch 14.4 engineering acceptance generated 36 real Chromium component PNGs and 12 contact sheets (390/1440px, normal/synthetic-stressed states, three existing application domains). The independent screenshot review judged the same recurring weakness across arms: exact semantic evidence is preserved, but prominent machine identities and canonical JSON reduce the utility of the human-facing collection and inspector.

In a blinded 12-set subjective ranking, after freezing the ranks and unblinding, the universal semantic candidate ranked first in seven, the baseline in three, and the frozen blind ordering in two. The semantic arm outranked each competing arm in eight of 12 matched sets. Those counts are **not** a user-study result, statistical quality certification, or warrant for another layout profile. Relative ordering was often marginal and the same inspector defect affected all arms.

This patch changes the *presentation of the experimental one-profile universal lab*, not the canonical knowledge model, source records, authority, or production application routes. It does not create additional profiles, application-specific branches, network access, actions, or unverified parent-child relationships.

## Repairs

1. Human-readable semantic fields precede the composite machine identity in the universal composed collection specification. The required canonical identity stays present, primary under the v2 value contract, and available for exact inspection.
2. The universal lab explicitly enables human-first presentation when mounting the existing v2 collection. Ordinary consumers of that renderer retain their previous layout/inspector behavior.
3. The selected inspector initially presents a readable name or short discriminating identity plus bounded, labeled status, measurements and other facts. Its exact source, mcelCanonicalRecord and value-state provenance are exposed through native expandable disclosure, not automatically dumped as primary content.
4. The observed-subset summary remains carefully scoped and textually explicit, with more readable labels and less line noise.
5. Lab-only typography, selected-row distinction, compact mobile formatting and focus-visible native disclosure improve hierarchy without changing the canonical tree interpreter.

## What this does NOT claim

Static image geometry cannot validate interaction usability, semantic superiority, faithful production adoption, keyboard/screen-reader usability or active continuity under a running producer. No production promotion is authorized. Observed counts must never be described as population-wide counts. An exact machine identity is not automatically a useful name; a useful human name is not evidence of stable identity or authority.

The title selector prefers registered readable scalar text, then discriminating primary identifiers; it never creates an inferred identity. Full canonical identity is still the selection key. Where only a multi-part technical identity exists, the inspector uses the most concise suitable registered identifier in the observed subset, not an invented label.

## Verification

Local targeted acceptance on the exported repository:

```powershell
python -m pytest -q tests/test_mcel_patch14_5_human_first.py
python -m pytest -q tests/test_mcel_patch14_universal_composition.py tests/test_mcel_patch14_visual_quality.py tests/test_mcel_patch14_5_human_first.py tests/test_mcel_patch14_canonical_tree_continuity.py tests/test_mcel_patch14_active_continuity.py tests/test_mcel_patch14_decision_foundations.py tests/test_mcel_knowledge_ir_bridge.py tests/test_mcel_patch13_completion.py
```

Checks use real Chromium at 390px and 1440px for all three domains, normal and synthetic-stressed observations. They check row counts, single profile, no overflow, selected record identity, exact canonical object recovery, native disclosure opening, keyboard selection and focus preservation, and update refusal to claim continuity when source generation is unverified. A fresh blind screenshot review and actual human task-completion tests remain necessary before changing the status of the G6/G7 quality gates.

## Next experiment

Keep the same 12 matched test cells and source fixtures. Re-run the current lab and baseline with the repaired universal renderer. Use new blinded screenshots plus timed record-finding tasks (identification, state comparison, memory/size availability, exact source recovery). Record concrete errors, time-to-answer, disclosure count and percent of viewport used for canonical evidence. Add live keyboard/continuity probes before any quality or production-promotion decision. No second profile should be inferred from a deficit that can be corrected by the shared composition contract.
