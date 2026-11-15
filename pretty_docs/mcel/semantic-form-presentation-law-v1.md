# MCEL Form Presentation Law v1

**Status:** Implemented for MCEL Lab's isolated Calculator preview and legacy rendered-site preview. No production application Form switch has been enabled.

## Ownership

MCEL's canonical semantic IR remains the authority for regions, controls, intent, relationships, and operations. Layout continues to own box allocation, placement, scroll ownership, clipping, and content-fit decisions. Theme owns colors, backgrounds, foregrounds, and contrast. **Form Presentation Law** owns element-level appearance decisions: control/panel/input/badge radii, shape-aware painted group corners, and non-layout surface elevation. Chrome owns structural site transformations and does not choose element shapes.

This module is **`mcel-semantic-form-presentation-law.js`** (`McelSemanticFormPresentationLaw`), and is **not** `mcel-form-law.js` (`McelLabFormLaw`), which already governs validation, submission, dirty state, and error behavior.

Form does not infer operation identity from element labels, CSS classes, order, or shape. The Calculator runtime requires exact canonical node/region bindings. Native interactive elements inside a source-bound region may inherit a purely physical shape without thereby acquiring a new semantic intent or authorization. If source binding fails, Form refuses application.

## Migration

Form now governs all legacy site element corner geometry through scoped `--mcel-form-*` tokens. Existing Theme-owned `--site-radius`, `--site-radius-sm`, and `--site-hero-ornament-radius` declarations were transferred into the Form compatibility mapping. The original eight Theme baselines are retained by the `form-current` profile. Chrome's structural HTML transformations, content fitting, region placement, typography, and accessibility behavior remain intact. Flat frames that have no visible paint retain their zero-radius treatment.

The corner-aware background treatment previously implemented inside the semantic Theme preview belongs to Form. It rounds painted group backgrounds only when existing control edges demonstrate the need; it does not use `overflow:hidden` or `clip-path` to hide containment failures.

### Coextensive semantic paint surfaces

A rounded semantic region may share its exact box with another source-bound region that paints an opaque background. If one layer has rounded corners and the other has square corners, the square layer obscures the curved background at the corners (as with the Calculator's inspection/Activity surfaces). Form now assigns the already-established radii to the unrounded, coextensive painted peer. This is a generic source-bound shape-continuity rule, not an Activity selector, an application-owned color, or a clipping workaround. The parent region continues to own the necessary Layout clearance; Form does not apply duplicate clearance to that coextensive paint layer. The rule is idempotent across Form and Theme changes, and the `form-wireframe` profile remains square.

## Deterministic Form–Layout contract

Form supplies the selected radius geometry for source-bound panels and groups. The existing `McelSemanticLayoutInference` owns a one-pass `applyFormShapeLayout` solver: it resolves each framed container's CSS corner radii (including elliptic corner pairs), computes the minimum insets that place a rectangular normal-flow content box inside those quarter ellipses, and commits only necessary padding increases. The geometric clearance factor is `1 - 1/sqrt(2)` on both axes. Borders contribute to clearance. Root viewport layouts, scroll owners, controls, and transformed or out-of-flow children are not blindly adjusted.

The operation starts from the authored padding every time a Form profile changes. `clearFormShapeLayout` restores exactly the owned inline padding properties, avoiding cumulative drift and preserving semantic identities and live operations. No render–measure–nudge retry loop is used. An unsupported corner/content geometry or impossible content area produces an explicit diagnostic rather than clipped content. Intrinsic text overflow and arbitrary paint effects remain subject to the independent browser oracle, not a claim of unconditional shape-fit proof.

The `Form` selector is independent from the existing `Theme` and `Chrome` selectors. It applies to the mounted Calculator through the canonical presentation bridge without reconstructing its DOM. The existing legacy rendered-site iframe rebuilds on a Form change as part of its normal preview lifecycle. The currently selected Form profile is also included in the Lab project snapshot; older snapshots default to `form-current`.

## Profiles

- `form-current`: Preserve the selected Theme's existing legacy form and the modern Calculator's existing shapes.
- `form-angular`: Small, rectangular edge radii.
- `form-soft`: Softly rounded controls and panels.
- `form-pill`: Capsule-shaped controls with restrained panel treatment, not unbounded pill radii on every container.
- `form-wireframe`: Squared-off visual geometry and no legacy decorative elevation.

Color is never assigned by Form. Existing Theme tokens remain authoritative. Form does not directly change width, height, margins, gap, grid/flex rules, overflow, clipping, focusable targets, semantic state, or the action runtime. Layout may increase safe interior padding when a Form geometry contract requires it; the resize/reflow is Layout-owned and must be feasible.

## Acceptance boundary

The regression suite compares shapes and computed styles for all eight themes and six legacy Chromes, checks modern Calculator Basic/Graphing bindings at narrow and wide viewports, and ensures Form switches preserve node identity, operational state, legitimate scroll ownership, and safe containment even when the negotiated interior geometry changes. Incompatible or absent semantic bindings are rejected, not guessed. The current implementation is still Lab-only; applying Form to production apps requires additional per-app semantic and operational acceptance.
