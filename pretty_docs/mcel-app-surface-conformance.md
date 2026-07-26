# MCEL App Surface Conformance Baseline

Patch 18 turns the File Explorer pilot lesson into a reusable MCEL app-surface
baseline. A surface can be semantically valid and still be bad at runtime if the
important text is clipped, hidden, overlapped, or if the diagnostic probe itself
throws before reporting measurements.

The reusable baseline has five layers:

1. Semantic validity: MCEL ridges extract into a valid `SemanticSurfaceIR`.
2. Layout validity: extracted layout ridges build a valid `SharedLayoutGrammar`.
3. Runtime ownership validity: the primary app surface exists, is visible, is
   large enough for its contract, and is not ambiguous.
4. Runtime visual-fit/readability validity: layout, content-fit, and visual
   integrity probes find no unreadable or colliding surface regions.
5. Diagnostic no-throw reliability: diagnostics complete and retain useful
   measurements.

File Explorer is the first non-editor app to exercise the full baseline. Document
Editor now exercises the same baseline for a richer authoring/page surface. The
Code Editor remains the first host/workbench, but this conformance module is not
editor-specific.

The diagnostic counter copy payload now includes the `appSurfaceConformance`
summary when it is available. That keeps copied reports from only saying
`errors/warnings/green`; the report can also say which layer failed.

Important distinction:

```text
semantic surface valid
  does not automatically imply
runtime app surface usable
```

Going forward, MCEL-powered apps should be judged against both the reusable
semantic/layout pathway and runtime visual-fit diagnostics.


## Registry policy layer

Patch 19 adds `McelAppSurfaceRegistry` so conformance can distinguish
surface-aware apps from legacy apps that have not been converted yet.

The first required app entries are:

```text
file-explorer
document
website-builder
code-editor
calculator
```

The registry also records which layers are currently required for each app. File
Explorer and Document Editor require the full semantic/runtime baseline. Website Builder, Code
Editor, and Calculator require the runtime baseline first, while their full
static semantic surface conversions can progress separately.

## Surface fit-policy contract

Semantic-runtime app conformance now depends on the shared MCEL surface fit-policy contract.

The runtime visual-fit layer treats required readable/control clipping as a contract failure unless the candidate declares an explicit fit policy such as `wrap`, `truncate`, `scroll`, `compact-icon`, `overlay`, or `decorative`.

This keeps app promotion from stopping at macro region fit. A region may shrink only when its readable/control children declare how they remain usable.

## Patch 22b policy-scoped baseline

Runtime-baseline and host-workbench apps are judged by their registered required
layers.  Static semantic/layout layers may be unavailable for a plain source
file or a runtime-only app without making the whole app fail.  Semantic-runtime
apps still require the semantic-surface and layout-grammar layers explicitly,
so this policy does not weaken File Explorer or Document Editor conformance.
