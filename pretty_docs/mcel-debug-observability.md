# MCEL Debug Observability

MCEL cannot be trustworthy if a failed page makes the user guess. The debug layer exists so every serious MCEL use can leave behind a study packet: what root was inspected, what styles won, what layout contract failed, what generated/runtime boundaries were present, and what could not be inspected.

The rule is simple: **a debug packet is evidence, not trust**. It does not prove the system is correct by itself. It gives the next person enough structured evidence to decide what failed and where to look.

## Public surfaces

Use these from the browser console, app code, or a regression harness:

```js
const envelope = MCEL.captureDebug({
  name: "code-studio-layout",
  rootSelector: "#code-editor-app",
  reason: "layout failure study",
  expected: {
    rootBackground: "rgb(30, 30, 30)",
    displayGridSelectors: [
      ".code-studio-shell",
      ".code-studio-body"
    ],
    stackedChildrenSelectors: [
      ".code-studio-body"
    ],
    collapsedSelectors: [
      "#code-studio-bottom-panel"
    ],
    maxDocumentHeightRatio: 1.6,
    maxRootHeightRatio: 1.2,
    maxCollapsedHeight: 80,
    forbidYellowGlobalThemeLeak: true
  }
});

console.log(envelope.issues);
console.log(MCEL.exportDebugPacket());
```

`MCEL.compile`, `MCEL.serialize`, `MCEL.repair`, `MCEL.audit`, and `MCEL.inspect` now attach a debug envelope to their returned result and append it to `MCEL.getDebugTimeline()`.

## What the envelope captures

The envelope is JSON-safe and designed for copy/paste into issue reports. It includes:

- viewport and document height metrics
- resolved root selector and computed style
- inspected selectors and skipped selectors
- largest rendered elements under the MCEL root
- source/runtime boundary counts
- generated part counts
- issues with severity, mechanism id, and contract clause where possible

## Mechanisms that catch real failures

The first mechanisms intentionally target the exact kind of failure that made Code Studio unusable:

```text
mcel.debug.css.not-winning.v1
mcel.debug.theme-leak.v1
mcel.debug.grid-contract.v1
mcel.debug.stacked-children.v1
mcel.debug.page.metrics.v1
mcel.debug.scroll-boundary.v1
mcel.debug.collapsed-dock.v1
mcel.debug.dominant-element.v1
```

A VS Code-like app should not need a human to wonder whether the CSS loaded, whether the body became a grid, whether global buttons leaked, or whether the file map dominated the page. MCEL should capture those facts automatically.

## Failure example

For a broken editor shell, a useful issue should look like this:

```json
{
  "id": "mcel.debug.layout.grid-missing",
  "severity": "critical",
  "message": "A selector that is required to be a grid is not display:grid at runtime.",
  "data": {
    "selector": ".code-studio-body",
    "actualDisplay": "block",
    "mechanism": "mcel.debug.grid-contract.v1"
  },
  "contractClause": "mcel.user.validation-is-evidence-not-trust.v1"
}
```

That is the difference between “the layout is ass” and “the Code Studio body lost its grid contract because scoped CSS did not win.”

## Non-guarantees

The debug layer does not guarantee that MCEL is a good platform. It guarantees that MCEL does not get to fail silently.

It also does not replace browser DevTools, screenshots, or user judgment. It prepares the evidence so those tools can be used quickly.

## Minimum standard for MCEL apps

Every MCEL app should define a capture profile:

```js
const CODE_STUDIO_DEBUG_PROFILE = {
  name: "code-studio",
  rootSelector: "#code-editor-app",
  expected: {
    displayGridSelectors: [".code-studio-shell", ".code-studio-body"],
    stackedChildrenSelectors: [".code-studio-body"],
    collapsedSelectors: ["#code-studio-bottom-panel"],
    forbidYellowGlobalThemeLeak: true
  }
};
```

If the app cannot explain its own runtime state, it is not yet an MCEL-quality app.
