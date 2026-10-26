# MCEL Content-Aware Composition v1 — Experimental

## Purpose

This research patch repairs two visual failures discovered by Design Quality Frob v4 without changing production MCEL selection, operational applications, application-package validation, or the quality scoring weights.

1. **Complete, unbroken record text is displayed rather than truncated or allowed to escape.** Shared generated-record CSS uses `overflow-wrap: anywhere`, normal whitespace and bounded flex-item sizing. No semantic values or component nodes are removed.
2. **Column choice depends on the available record-list width and measured record font size.** The experimental two-column candidate C collapses to one track if two readable records cannot fit inside the actual parent content box. Its `ResizeObserver` also responds to font-size changes in testing without requiring a viewport resize. The measured experimental readable floor remains 240px at baseline; this is not a universal assertion about every application.
3. **Quality measurement distinguishes intrinsic long tokens from actual rendered overflow.** The probe measures DOM text fragments and scroll widths after browser wrapping. A successful wrap is retained as `wrapped_long_tokens` evidence rather than a hard failure. An intentionally disabled-wrapping negative control must still be rejected.

## Run

    python tools/mcel_layout_frob.py --mode design-quality-stress

See `runtime/reports/mcel-layout-frob/design-quality-stress/` for `stress-report.json`, `validity-report.json`, `stress-gallery.html`, and `stress-review.zip`.

Run focused tests:

    python -m pytest -q tests/test_mcel_content_aware_composition.py tests/test_mcel_design_quality_frob.py tests/test_mcel_design_quality_stress.py tests/test_mcel_design_quality_validity.py tests/test_mcel_quality_v4_browser_fixture.py

## Scope and caveats

- This changes the **generic synthetic MCEL application generator and Frob measurement**, not the live Task Manager, Website Builder, or production selection engine.
- Do not alter catalog validation; Task Manager design fixtures remain outside `mcel_apps/` under `mcel_designs/task-manager/`.
- The optimizer's weights and unsupported-task abstentions remain unchanged.
- Complete browser wrapping does not certify typography, accessibility, usefulness, or human visual approval.
- The generated candidate preview must be recompiled before its screenshots can reflect this fix. A previously created `stress-review.zip` is immutable evidence of the earlier renderer.
