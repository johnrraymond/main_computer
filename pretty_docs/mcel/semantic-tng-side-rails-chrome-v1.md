# MCEL TNG Side Rails Chrome — Lab Pilot v3

`chrome-tng-side-rails` is the first opt-in Chrome profile that paints a structural
accent around a **modern** MCEL semantic working unit. It appears in MCEL Lab's
Chrome selector. The other Chrome profiles and the standalone Calculator are
unchanged. The legacy rendered-site frame intentionally receives an unchanged
HTML projection when this modern-only profile is selected.

## Source and ownership

The Lab's isolated Calculator contains canonical `mcel.application-ir.v1` data.
`McelSemanticChromePresentationLaw` requires the shared semantic presentation
bridge to verify exact IR→DOM source binding. It selects the unique, primary
compute input region inside a semantic `data-mcel-working-unit` with intrinsic
sizing and a canonical workspace scroll owner. No Calculator-specific source ID,
button label, DOM index, or text search is used.

Chrome attaches two inert structural side rails to that working unit. The rails
have no user-interaction handlers, semantic application nodes, or control state.
They do not wrap, replace, or remount original application children. They occupy
only previously unused inline-axis space; there are **no horizontal top or
bottom chrome bars**, no mode-panel or Activity-panel chrome, and no full-app
surround.

The existing Layout cross-axis flex alignment centers the intrinsically sized
working unit using a profile-scoped `margin-inline:auto`. CSS container units
calculate rail width from the unused width and clamp it to 82px per side. At
workspace widths below 480px, the rails disappear rather than overlap tools.
No JavaScript geometry polling or post-render relocation is performed.

The rail is gold with **flat rectangular purple insets** at its midsection,
separated by transparent gaps. Only its outer top/bottom corners are rounded.
The physical gold and purple palette tokens are supplied exclusively by the
selected Theme in `mcel-style-law.js`:
`--mcel-theme-chrome-primary` and `--mcel-theme-chrome-inset`.
The existing rails now receive modest molded-material lighting, with a smooth
edge highlight and low-strength shadow. Purple inlays remain **flat, square-ended
and visibly recessed**, not pill-shaped or weathered. These are material
light-response treatments on the *existing two rails*: no new application nodes,
rails, top bands, bottom bands, or interactive elements are introduced.

## Tactile control and rail finish (v3)

The same opt-in `chrome-tng-side-rails` profile now adds **material lighting only**
to the canonical Calculator's existing working-unit buttons and MODE navigation
buttons. The scope is limited to this Chrome profile; no new Calculator source,
control binding, wrapper, style profile, event handler, or sizing algorithm is
introduced. Button faces receive a translucent top-edge sheen and subdued
recessed shadow. Native `:hover` strengthens the sheen, `:active` switches to an
inset/pressed treatment, and selected MODE items retain their own Color Law
palette. Submit and keypad-equals controls have a faint halo derived from
`--mcel-tng-phosphor` (itself bound to the selected Theme's chrome-phosphor
token). The MODE group and its surrounding panel never receive button-material
selectors. All foreground/background/border colors remain resolved by the
existing semantic Color Law, not by Chrome.

The preexisting gold side rails gain a stronger narrow specular rolloff and a
slightly deeper drop shadow. Purple insets remain clean, square-ended, and
visibly recessed, with no added grime or pill geometry. Rail segment positions,
widths, height, and rounded-corner geometry remain unchanged; the existing
black-glass pixel grid is unmodified. Browser native CSS owns hover and press;
reduced-motion skips the material transition, while forced-colors suppresses
the button overlays to preserve system colors and focus indication. Removing
TNG Chrome removes every material rule without mutating the original controls.

## Directional material lighting (v4)

The TNG Chrome now derives raised-button, hovered-button, and pressed-button
*lighting overlays* from the same upper-left source, via the Chrome-scoped
`--mcel-tng-light-angle` (default `145deg`). The face is brightest at its
upper-left; raised bevels catch light on upper/left edges and deepen toward
lower/right edges. Pressing a button reverses its bevel response without
moving the external light source. Native CSS selectors remain in charge of
interaction transitions; Color Law retains button foreground, background,
border, focus and state authority.

Existing gold rails retain their Theme-owned segmented base gradient. Masked
specular overlays now use the same 145-degree source and broad highlights
along the curved gold shoulders, with reduced illumination on the right rail.
The purple insert background and inset geometry are unchanged. Critically,
Chrome does **not** replace the rails with a white/black background shorthand
or blacken the display panels: a previous experimental CSS twiddle did so and
visually suppressed the selected Theme palette. This revision deliberately
changes only translucent light/shadow gradients and bevel shadows, preserving
the accepted black-glass field and pixel grid exactly.

The angle may be changed locally at runtime using the root's CSS custom
property; its default is stable and no browser event listener, geometry pass,
or new semantic binding is involved. Browser regression checks all eight
palettes, source-color preservation when rotating from 145 to 125 degrees,
rail/purple palette stability, native button geometry, and profile cleanup.

## Black-glass display material

When (and only when) this Chrome profile is selected, it applies a restrained
black-green/black-blue glass treatment behind the Calculator's existing regions.
The treatment also targets the existing full-application layout surface because
the semantic Theme otherwise paints that surface opaquely, concealing a root-only
glass background. No application panel, input, result, graph, or control receives
its own color override. The glass material is composed of a dark foundation,
small phosphor-color pixels (5px pitch, tiny dots), a faint radial luminance
variation, and a mild glass sheen. It is stationary, uses CSS gradients rather
than raster assets or animation, and adds no input/pointer handlers or layout
measurements.

The Theme now supplies four TNG material palette tokens in each of the eight
canonical palettes:
`--mcel-theme-chrome-primary`, `--mcel-theme-chrome-inset`,
`--mcel-theme-chrome-glass`, and `--mcel-theme-chrome-phosphor`.
Chrome's light and shadow mixtures derive from these resolved Theme values.
The new glass/pixel tokens are inert for all other Chrome profiles. No Chrome
application source declares physical colors; Form and Layout remain unchanged.

## Reversibility and acceptance

Selecting another Chrome restores the original working-unit alignment,
removes both generated rails, the profile's semantic marker, and its theme
variables. The CSS remains inert because it is scoped to the selected profile.
Theme switching re-reads the shared Color Law palette, including both material
tokens. Removing TNG Chrome removes the glass and dot-matrix treatment without
removing the independent Theme projection. Native browser scroll,
mode switches, E/W/G, calculation, and input focus continue unchanged.

Acceptance checks verify source-bound identity, two and only two rails, horizontal
location beside the primary tool, no vertical chrome, the square purple stripe, the black-glass pixel field
and clean rail highlights,
mode and Activity geometry preservation, desktop centering, no overflow at phone
sizes, profile reversibility, theme-supplied colors, and unchanged Evaluate
behavior. The standalone Calculator has no Chrome script or paint activation.
