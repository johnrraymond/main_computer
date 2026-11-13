# MCEL Lab — Active Calculator Theme Preview (visual integration)

## Purpose

The MCEL Lab's original rendered-site iframe and the modern Calculator preview
previously appeared as two independent, vertically stacked views. Theme/Chrome
controls were above a large legacy site frame; the Calculator appeared below
it, making it difficult to inspect theme effects.

This patch keeps the existing eight Style Law themes and the existing canonical
Calculator semantic projection. It changes **only the MCEL Lab** presentation:

- The Calculator experiment is adjacent to the Theme and Chrome selectors.
- On opt-in, the Calculator becomes the active preview and the legacy site
  frame is hidden (but is not destroyed or unregistered).
- Disabling Calculator preview restores the original rendered site.
- Only the isolated Calculator frame receives semantic theme colors. The
  existing semantic bridge still determines the source-bound controls and
  regions; no label-based or CSS-class-based action inference is added.
- A readout reports observed control/region coverage and colors computed in
  the isolated frame. It distinguishes actual computed color from declared
  theme-token selection; transparent/unstyled targets are counted for review.
- Form-field and text colors follow the theme as descendants of the canonical
  Calculator root; this does **not** create new semantic bindings.
- Chrome is visibly labeled **not connected to modern MCEL**. Its legacy-site
  behavior is unchanged, including chrome switching and the rendered-site
  iframe's internal source synchronization.

## Safety boundary

No production Calculator CSS, layout code, semantics, action handlers, enabled
states, routing, or MCEL Chrome Law was modified. All modifications to the
Calculator occur exclusively in the opt-in iframe. Only color-bearing CSS
properties are projected onto the Calculator; no geometry, display or
pointer-event rules are added.

## Validation

Run:

```
python -m pytest -q tests/test_mcel_semantic_theme_preview.py tests/test_mcel_semantic_theme_lab_visual.py tests/test_mcel_semantic_presentation_bridge.py tests/test_mcel_lab_presentation_workspace.py
```

Browser acceptance uses the actual Calculator compiled host markup and the
same Style Law and presentation bridge in the repository. At 390, 950, and
1440 pixel viewports it checks active-preview switching, distinct computed
colors for Original MCEL/Civic/Accessible themes, semantic IDs, disabled
states, unchanged control geometry, evidence text, and restoration of legacy
preview on Disable.

The broader application suite may require the separate game export omitted
from the main-only snapshot. Do not treat a missing game manifest as an MCEL
Lab regression; use the bounded suite above for this patch.

## Mounted-preview readiness correction (2026-11-12)

The first active-preview integration could display a loaded Calculator while
reporting `Preview not ready: canonical Calculator root or semantic source
unavailable.` The guard conflated origin access, source availability, DOM
mounting, and projection dependency failures. The browser tests also bypassed
the actual iframe readiness sequence by injecting the completed host and IR.

The Lab now distinguishes those conditions. It reads the canonical Calculator
IR from the same-origin mounted iframe when available (falling back to the
parent's compiled canonical source), verifies the declared `#calculator-app`
root, and checks that declared semantic controls and regions have actually
mounted before applying the Style Law's color tokens. If the iframe loads
before its canonical application, a bounded MutationObserver watches only DOM
child changes and reapplies once the source bindings exist. It is stopped on
success, failure/deadline, or Disable Preview. It never infers semantics from
button text, modifies layout, or crosses an inaccessible frame boundary.

A new browser regression deliberately loads the iframe without an application,
checks the distinct missing-source diagnostic, supplies the compiled canonical
IR inside the frame, inserts an incomplete root, then completes the real host
markup. Only then may the theme be reported as applied. The test verifies
computed colors for Original MCEL, Civic, and Debug Wireframe and preserves
the existing cross-width, no-geometry-change and source-bound tests.

This is a source-binding/readiness repair, not a Chrome integration. Actual
hosted-service behavior still requires checking in the deployed environment;
a main-only export does not carry the separate game bundle.

## Promoted Calculator binding correction (2026-11-12)

The deployed `copy(debug())` report revealed a second, architectural mismatch:
`/applications/calculator` now returns the canonical Applications shell by default,
while its native child is the *promoted semantic Calculator*, whose mounted root
is `#mcel-expanded-root` and whose embedded canonical IR declares app ID
`semantic-calculator-expanded`. The Lab had requested the shell-facing URL
without `mcel-shell-child=1` and then waited for the **old** Calculator's
`#calculator-app`. The old and new Calculator IRs cannot be substituted.

The Lab now requests the explicit existing native-child route, reads and
validates `#mcel-expanded-ir` **inside the mounted child**, and binds real
`[data-mcel-node]` controls/forms and `[data-mcel-region]` regions by exact
canonical IDs. Missing or duplicate bindings refuse presentation rather than
fall back to text, CSS class, or inferred intent. The legacy-host path remains
supported only when an actual `#calculator-app` with its own source IR is
present, preserving isolated oracle fixtures without mistaking it for modern
production.

The Theme Law still provides the same eight palettes. Its tokens are applied
only to the isolated Lab iframe; the generated Calculator's semantic IR,
operational handlers, enabled states, geometry, and the Chrome Law are
unchanged. `copy(debug())` now distinguishes the promoted runtime, original
host, and accidental shell mount, and reports the active mounted source.

The new browser test mounts the **actual generated promoted Calculator page**
through `srcdoc` in a same-origin sandboxed frame at three viewport widths,
verifies source-bound semantic targets, visible computed color changes across
three existing themes, preserved bindings and geometry, and fails-closed
behavior for missing/ambiguous bindings. The native route query is also
checked against the existing canonical shell-child contract. Hosted deployment
with the user's running viewport server still requires verification.

## Semantic color coherence correction (2026-11-12)

The initial modern theme projection colored canonical regions and controls but
left painted visual descendants using their original application CSS. On a light
theme this produced a white Calculator surrounded by dark Mode and Activity
panels, inconsistent borders, and barely readable labels. It also ignored the
full presentation relationship between control state and background contrast.

The isolated Lab projector now uses the source-bound region hierarchy to
propagate a coherent set of tokens: region surface, nested painted surface,
foreground, field, interactive control, selected, disabled, and focus/hover
states. A generic visual-paint discovery step identifies already-painted
descendant elements *inside exact canonical regions*. It does not turn those
descendants into semantic nodes, infer operations from text or CSS classes, or
change their handlers or authorization. On each theme change, discovery runs
with the prior Lab style sheet disabled, so themed elements are not
progressively reclassified as authored surfaces.

The original eight Theme Law palettes remain authoritative. Palette resolution
still takes place exclusively within the opt-in Lab Calculator iframe. No
production Calculator CSS, semantic IR, geometry, layout, event handling,
Chrome Law, or other application is changed. The projector adjusts background,
foreground, border colors, and cosmetic shadows/background images only.
Focus outlines are visual and do not alter element bounds.

The Lab now reports how many painted descendants were recolored and audits
computed text/background contrast across visible direct-text elements in
canonical regions. The oracle checks at least 4.5:1 for those samples across
all eight themes at 390, 950, and 1440 px, including state selection/disable
and exact semantic and geometry preservation. This is a sampled computed-color
oracle, **not** a claim of comprehensive WCAG conformance: images, pseudo-
elements, all future dynamic content, and focus paths still need separate
checks. A transparent semantic layout wrapper is not itself a contrast failure
when its effective backdrop remains readable.

Run the bounded regression:

```
python -m pytest -q tests/test_mcel_semantic_theme_coherence.py tests/test_mcel_semantic_theme_preview.py tests/test_mcel_semantic_theme_live_route_binding.py tests/test_mcel_semantic_theme_lab_visual.py tests/test_mcel_semantic_presentation_bridge.py tests/test_mcel_lab_presentation_workspace.py tests/test_mcel_lab_semantic_adapter.py
```

Testing was performed with the real promoted Calculator document in a
same-origin frame. A separate navigation-to-URL browser check was unavailable
because this execution environment blocks all test-host navigations; hosted
production integration still requires deployment verification.
