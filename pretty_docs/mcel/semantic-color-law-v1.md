# MCEL Semantic Color Law v1 — foundation and first consumer

**Status:** The resolver remains pure and additive. E/W/G is now the first real semantic color consumer. Calculator control surfaces, actions, desktop clusters, and the default Theme reconciliation remain separate future migrations.

## Authority

Applications declare semantic meaning and state. Theme is the only future authority for physical color decisions. Form owns visual geometry, and Layout owns placement. The implementation is hosted in the existing `McelLabStyleLaw` as `resolveColor()`, `semanticPaletteFor()`, `semanticColorRoles`, `semanticColorStates`, `semanticColorSurfaces`, and `semanticPaletteTokens`.

`resolveColor({theme, role, state, surface, palette})` returns `foreground`, `background`, `border`, and `accent` with a `schema`, resolved theme, and status. This is **a pure decision function**: no DOM queries, CSS injection, writes to the canonical IR, or interaction with operational state. Unknown themes, meanings, states, or surfaces are explicitly unresolved. A supplied incomplete palette is refused. When `palette` is omitted, the resolver returns CSS custom-property expressions to be resolved by the selected Theme in the eventual consumer; when provided, `palette` must come from Theme's existing computed CSS, e.g. `semanticPaletteFor(element, getComputedStyle)`.

Roles cover surfaces, ordinary text, navigation, actions, and diagnostics. Roles and state are *declared inputs*: `diagnostic.warning`, `diagnostic.error`, `diagnostic.success`, `action.start`, `action.stop`, `action.inspect`, etc. This patch does **not** infer an action from a button's label or CSS class.

## Palette and diagnostics

The existing six Theme palette tokens are unchanged. Three CSS custom properties per legacy Theme declare error, warning, and success colors (`--mcel-theme-error`, `--mcel-theme-warning`, `--mcel-theme-success`). The actual shades are owned by Theme CSS; no application or widget chooses them.

The MCEL Lab's **Semantic Color Law — read-only** inspector is collapsed by default inside the existing compatibility diagnostic. Opening it shows the selected Theme's semantic role decisions, using its live computed palette. The inspector itself does not apply decisions or create canonical bindings. The E/W/G consumer independently receives Theme-owned semantic colors, as described below. The original color projector remains intact for ordinary controls and text. A narrow diagnostic-role exception prevents broad `span` recoloring from overriding semantic E/W/G signals.

## E/W/G consumer migration (Patch 2)

E/W/G retains its existing counts, refresh, click-to-copy, active-state, and operational lifecycle. It now declares `data-mcel-color-role="diagnostic.error"`, `.warning`, or `.success` on each count segment, and declares the currently dominant diagnostic role on its indicator container. The widget contains no physical color choices. Delimiters remain ordinary neutral presentation characters, not diagnostic signals.

`mcel-semantic-color-diagnostics.css` is Theme-owned: it supplies the existing standalone diagnostic palette via CSS custom properties and binds semantic roles to those properties. The stylesheet is included both in the application shell and the promoted standalone Calculator. It preserves existing unthemed E/W/G hues, backgrounds, and geometry. The Lab's existing eight Themes already own their own diagnostic palette tokens; the semantic Theme projector now takes all nine tokens and calls the pure `McelLabStyleLaw.resolveColor()` for each diagnostic role, so selected Theme colors win over generic type selectors. The Lab preview does not infer meaning from the HTML tag or from the numeric counts.

This is a narrowly scoped consumer migration, not a change to the global Default Theme: the unthemed standalone baseline is preserved; Lab-selected themes govern only the opt-in isolated preview. The unresolved difference between Lab's `theme-machine` and the standalone default remains explicit and separate.

## Remaining migration boundary

The next consumers are semantic action controls, then surfaces and icon clusters. Each migration removes physical palette authority from the application, retains Form and Layout ownership, and requires independent computed-style, contrast, and operational acceptance. No theme selection is changed by this patch.

## Visual-state foundation (Patch 3)

See [Semantic Color Law v2](semantic-color-visual-state-law-v2.md) for the additive state-composition resolver, CSS compiler, read-only Lab state inspector, contrast proof and activation boundary. The E/W/G v1 consumer remains unchanged; no application action has been migrated.
