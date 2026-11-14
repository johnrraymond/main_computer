# Calculator semantic Color Law — migrated source contract

## Authority

The promoted `semantic-calculator-expanded` Calculator is the first complete
first-party source-authored MCEL Color Law application. The paired authored
DSL and HTML declare the semantic color role, surface, context, and (where
relevant) consequence of each of its 14 DSL nodes and 13 HTML interface
regions. The application sources do not select physical colors. The shared
`McelLabStyleLaw` owns every named palette, and
`McelSemanticColorPresentation` compiles those meanings into browser-native
CSS visual states for standalone hosts and the MCEL Lab.

The generic canonical renderer knows the intent and function of the native
controls it creates. It marks them semantically: expression input, mathematical
input and operator keys, Evaluate, Clear, Reset, graph editor keys, mode tabs,
and the mode select element. It never derives action meaning from the visible
button label or from the Calculator application ID. Generic named action roles
include `action.input`, `action.operator`, `action.evaluate`, `action.clear`,
and `action.reset`; roles remain available to other MCEL applications.

All Calculator-owned control and presentation DOM elements are source-bound or
renderer-generated semantic color consumers. The `source-complete` signal is
available on the canonical color presentation report, including counts for
authored and generated bindings. A partial source contract does **not** opt
into bulk descendant recoloring: it retains legacy compatibility until the
application has explicitly declared all interface regions.

## Independent semantic owners

The Calculator embeds the *shared Chat Console* and the independent E/W/G
health widget. Their internal semantics and Color Law rules remain their own;
the Calculator only owns their containing semantic regions. The Chat Console
is not reclassified as a Calculator control. The diagnostic indicator's
contrast and surface colors remain Theme-owned, including in the expert host.

The generated mathematical graph gives its primary curve the semantic series
identity `primary`. Its stroke resolves through the containing plot's Theme
accent; axes use the Theme foreground with opacity, rather than private
Calculator palettes.

## CSS compatibility and remaining system-wide work

The shared expert stylesheet still contains legacy physical color defaults
used by other, unmigrated MCEL applications. The Calculator's semantic Color
Law treatment takes precedence on its owned elements. Removing the shared
legacy defaults is a separate system-wide migration and enforcement task, not
an excuse to break other applications here. Likewise, the Lab's old projector
remains for applications without a complete source-authored color contract.
The fully migrated Calculator delegates Theme changes directly to the
canonical renderer in both Lab and standalone mode.

This patch changes color authority and native interaction colors, not the
Calculator's mathematical operations, app identity, semantic Layout decisions,
Form geometry, event handlers, or scroll ownership.

## Acceptance

- Canonical compile: 13/13 regions and 14/14 nodes have valid semantic colors.
- Every generated Calculator-native control is bound; 0 legacy Calculator nodes.
- Basic, Graphing, Symbolic, Help, and independent E/W/G keep their behavior.
- Real Evaluate request returns `2+3=5`; graph `x*x` renders with Theme accent.
- All eight Themes resolve the same Calculator semantics in both hosts.
- Normal/hover/pressed/focus are native CSS state treatments.
- No control geometry or node identity changes during Theme switching.
- Semantic color contrast is checked on the rendered panel/text relationships.
- Shared Chat Console internals and other unmigrated applications remain isolated.
