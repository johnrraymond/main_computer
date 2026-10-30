# MCEL Patch 14.7.7d — Original Row Rail, Pointer Ownership, Stable Telemetry

**Base:** Patch 14.7.7c installed. **Install:** `python new_patch.py "C:\Users\subsi\Downloads\mcel_patch14_7_7d_stable_hover_and_telemetry.zip"`. Manifest-free, file-replacement ZIP. Documentation resides in `pretty_docs/`.

## Failure reproduced from user cursor trace

At the first hover, the row's measured right boundary was x=471.33 while the externally created rail painted at x=767.33. The initial application set a correct `left` style, but the external native `popover=manual` scrollbar lacked a `:popover-open` CSS rule. Chromium's user-agent popover `margin:auto` and default right/bottom inset centered it anyway. Consequently the pointer was falsely outside both disjoint interaction regions while crossing the row boundary.

The trace also contained 700 row-detachment notifications across three-second source refreshes. L18-01's orthogonal identity factor is *unverified*: recreating a row on each observation is the correct *unverified-source* policy, but unsuitable for the **separate operator gallery's synthetic live telemetry demonstration**.

## Changes

1. Add explicit top-layer popover geometry and box styling for `.mcel-unclip-row-scroll-track:popover-open`, resetting `inset`, `margin`, width and border. Rail is adjacent to the row's border, never detached by native popover defaults.
2. Capture **painted rail/row coordinates and actual gap** in hover host diagnostics; append `railGap` to plugin paint frames.
3. Preserve original record and all existing fields, with a single scrollable `mcel-row` and scrollbar outside it. The existing 15% scroll-handoff algorithm remains.
4. Implement a narrow opt-in `incrementalValues` update path. It activates only when all row IDs and their generation tokens, field contracts, topology and order match and no retired records exist. Value changes update retained original nodes without unmounting the layout. The universal wrapper skips canonical tree remount only when the tree contract is unchanged, and the gallery skips duplicate tree validation only for an already validated successful value-only update. Structural/generation changes retain the existing full path.
5. The **operator gallery** now explicitly opts into synthetic stable-generation telemetry, preserving real row identity across its three-second sample updates. The 18-case L18 orthogonal screening still runs each **unchanged** identity factor (including L18-01 unverified). The UI discloses this separation.
6. The plugin's DOM observer deduplicates row removals and checks connection after the mutation queue settles, rather than reporting temporary moves as permanent loss.
7. Adopt the useful semantic principle from Claude's 14.7.8 work: the hover extension adds only hidden or truly truncated fields, uses canonical formatted display values, and does not repeat the visible `Selected: python.exe` header. Collapsed source/evidence disclosure and copy actions remain. Correct the 14.7.7c detail-refresh selector to target the real extension structure (not the removed nested viewport).

## Focused validation

Run:

```
python -m tools.mcel_hover_live_geometry_frob
python -m tools.mcel_hover_pointer_plugin_frob
python -m tools.mcel_row_atomic_hover_20_frob
```

The new live gate checks two viewport widths (390 and 1100), actual **rendered rail adjacency**, pointer crossing into the rail, uninterrupted popover over repeated three-second live telemetry ticks, unchanged original field positions, no row-detachment notifications, and successful value-only update retention. See produced reports under `runtime/reports/mcel-hover-live-geometry/`.

This patch is an offline browser fixture validation, **not** proof that the operator's full installed Windows environment has no other UI problems. The existing 14.7.4 retirement tests include two failures not covered by this patch; assess separately before promoting full lifecycle clearance. No diagnostic cursor data leaves the browser without explicit export.
