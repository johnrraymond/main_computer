# MCEL Design Quality Frob v4 — fixture and score causality repair

## Boundary

The Frob remains a **research-only** browser evaluator. This change does not promote the optimizer, change the application's semantic truth, alter production MCEL selection, or require ChatGPT/Gemma. Production application packages and the isolated `mcel_designs/task-manager/` fixtures stay separate.

## Repairs

1. **Painted long text.** The synthetic runtime restores base record-field text on each fixture transition, resolves visibility after state/filter changes, injects the long command into a genuinely painted record field, and refuses if no such field exists. The validity auditor requires a visible record containing the synthetic command and a >20 character length increase versus same-candidate, same-viewport control. It does not accept the `visual_state` flag alone.
2. **Viewport-stable tools cost.** Experimental quality policy `mcel.experimental-design-quality.v2-viewport-normalized-tools` replaces `tools_area_fraction_of_root` with `tools_area_fraction_of_viewport` measured as `tools_width * tools_height / (root_width * viewport_height)` (clipped to [0,1]). `root_height`, `document_height`, record count, and tools absolute Y position never appear in that denominator. Original task weighting is unchanged. Versioning and field rename avoid silently interpreting a v1 value as v2.
3. **Metamorphic invariance.** A crowded collection with unchanged primary target, tool size, and tool content must keep its browsing utility unchanged; growing the **tools themselves** incurs a larger measured cost, and additional visible rail content remains independently witnessed. The causal audit checks utility shifts rather than relying on the obsolete metric name.
4. **Refusal authority.** When every candidate violates a hard constraint, the report retains `REFUSED` even for tasks with no score objective. An unsupported scoring objective cannot mask infeasibility.

## Run

```text
python tools/mcel_layout_frob.py --mode design-quality-stress
python -m unittest tests.test_mcel_design_quality_validity tests.test_mcel_design_quality_frob tests.test_mcel_design_quality_stress tests.test_mcel_quality_v4_browser_fixture -q
python tools/mcel_application_packages.py
```

Read `runtime/reports/mcel-layout-frob/design-quality-stress/validity-report.json`, `stress-report.json`, and `stress-gallery.html`. A `STRESS_COMPLETED_WITH_DEFECTS` summary indicates the Frob completed and **observed actual weaknesses**; it is not a certification of design quality.

## v4 experimental observations (54 fresh Chromium captures)

- Both formerly ineffective long-text scenario fixtures are **VALID** (visible rendered record text grows).
- Crowded mobile no longer produces artificial utility improvement; the score-causality finding disappears.
- The long command now triggers `unbroken_content_exceeds_card_width` on candidate B and C at desktop width and on all three candidates in combined mobile. These **must remain hard failures** until a separate responsive typography/content policy repairs them.
- Tablet layouts remain spatially equivalent, and inspection/unknown state objectives remain unsupported.
- Hard-feasibility counts may worsen because the fixture actually exercises the previously missing condition. This is a truer measurement, not a regression of production rendering.

## Limitations and remaining work

The 240px card width floor and score weights are experimental. The geometry probe is not an accessibility or visual beauty certification. Actual hard failure on long unbroken tokens should be addressed in a later general rendering patch; v4 intentionally does not rewrite presentation rules or silently reflow tokens. The stress inputs remain synthetic and require human visual review before any production promotion.
