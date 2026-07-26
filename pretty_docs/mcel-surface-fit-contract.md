# MCEL surface fit contract

Patch 22 introduces a shared MCEL surface fit-policy contract.

The contract separates two layout questions:

```text
macro fit:
  does the app surface / region fit in its owner?

micro fit:
  when that region shrinks, what happens to its readable text and controls?
```

A semantic-runtime app is not conformant when required readable/control content silently clips.
Every shrinkable region must declare how required content survives constrained width or height.

## Fit policies

The shared policy module defines these policy tokens:

```text
wrap
truncate
scroll
compact
compact-icon
collapse-optional
overlay
decorative
ignore-hidden
```

The policies are declared with:

```html
data-mcel-fit-policy="wrap"
data-mcel-fit-policy="truncate"
data-mcel-fit-policy="scroll"
data-mcel-fit-policy="compact-icon"
```

Optional metadata can be added with:

```html
data-mcel-fit-role="document-library-header-control"
data-mcel-fit-required="true"
```

## Required rule

```text
Required readable/control content may not silently clip.

It must either:
  wrap,
  truncate with explicit ellipsis,
  scroll in a scroll owner,
  compact while retaining an accessible label,
  collapse only when optional,
  become an overlay,
  or be declared decorative/ignored when it is not user-facing content.
```

## Why this exists

File Explorer exposed long-path clipping in a roots/list surface.
Document Editor exposed shrink-first rail clipping in the Pretty Docs header.

Those are different app shapes, but the same contract failure:

```text
a region was allowed to shrink without a declared content-fit policy for its children
```

Patch 22 turns that lesson into reusable MCEL vocabulary and diagnostics support.
