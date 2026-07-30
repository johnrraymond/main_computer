# MCEL Authored Surface Document

`mcel.authored-surface-document.v1` currently analyzes authored text without
depending on a specific host.

It classifies HTML, SVG, JSON ridge records, JSON surface bundles, and plain
text. Documents with no MCEL ridges are `not-applicable` rather than failures.

The current API is:

```javascript
McelAuthoredSurfaceDocument.analyzeText(sourceText)
```

The analyzer uses the existing MCEL surface pathway:

```text
authored text
  -> surface ridges
  -> SemanticSurfaceIR
  -> SharedLayoutGrammar
  -> diagnostics
```

This module is reusable by the Code Editor, MCEL Lab, Website Builder, tests,
and future visual editors.

## Patch 24a: Document Editor authored-document contract

Patch 24a specifies a second, host-independent model for the document being
edited inside Document Editor. This is a specification boundary; it does not
claim that `mcel-authored-surface-document.js` already extracts rich document
content.

The authored-document model is:

```text
document
  -> page
  -> section
  -> heading
  -> block
  -> paragraph
  -> object

selection -> authored node or text range
annotation -> authored node or text range
export target -> document or selected authored range
```

The model must support these object kinds:

```text
document
page
section
heading
block
paragraph
object
selection
annotation
export-target
```

### Identity and hierarchy

Every authored node that can be navigated, selected, annotated, or referenced
by the companion MUST have stable identity for the life of the editing session.

Heading identity MUST NOT be derived from heading text alone. Duplicate heading
text is valid. The left document outline uses authored heading identity and
heading level to project hierarchy.

A page is a layout/projection boundary and a section is a logical structure
boundary. Implementations MUST NOT infer that every page break creates a new
section or that every section starts a new page.

### Mutability and synchronization

The authored-document model is the semantic source for:

- the center editing projection;
- the left document outline;
- current selection/caret context;
- annotations;
- Document AI context;
- export targeting.

Outline updates SHOULD be incremental or debounced. Rebuilding the outline must
not rewrite document content, reset selection, or lose undo history.

The authored-document adapter MUST expose enough heading identity and ordering
for the application projection to maintain one active outline entry. Document
caret, selection, or viewport movement may change that active heading. The
outline may scroll its own list to keep the active entry visible, but that
projection scroll state is not authored-document content and MUST NOT mutate the
document or its undo history. Activating an outline entry is the action that
requests document scroll/focus for the referenced heading.

Opening a different Pretty Doc replaces the current authored-document root only
after unsaved-change policy and load success are resolved.

### Application-surface boundary

Application-shell objects and authored-document objects are different layers.

```text
application surface:
  outline container, file-picker modal, page viewport, companion container

authored document:
  headings, paragraphs, objects, selections, annotations
```

Dynamic authored nodes MAY be projected into the page and outline. They MUST
NOT be copied into the static application-layout grammar as an unbounded set of
per-heading, per-paragraph, or per-file-row nodes.

Static app-surface conformance should verify the containers, ownership, fit
policies, and projection contract. Authored-document conformance should verify
identity, hierarchy, references, and synchronization.

### Planned reusable operations (not implemented in this snapshot)

A later implementation patch should provide host-independent
operations equivalent to:

```javascript
extractAuthoredDocument(root)
listDocumentHeadings(authoredDocument)
resolveAuthoredNode(authoredDocument, nodeId)
describeSelection(authoredDocument, selection)
```

Names may change during implementation, but the capabilities and layer boundary
are normative.

## Current implementation boundary

Patch 24a updates the specification and contract tests only. The existing v1
analyzer behavior remains unchanged in this snapshot; the host-independent rich
authored-document operations listed above do not exist yet.
