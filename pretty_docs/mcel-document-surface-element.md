# MCEL Document Surface element

## Authority

`element.authoring.document-surface` is a mountable, instance-scoped MCEL registry element. The existing document pagination, caret, block editing, layout normalization and math-object routines are implemented once in `mcel-document-engine.js`. The existing Pretty Docs application uses an adapter to those same routines; its application shell, persistence, export and AI pathways remain in place. ONLYOFFICE is independent.

The element itself neither retrieves nor saves files, nor invokes an AI provider. Its consumer owns storage, identity, access policy and conversation state. The Lab uses in-memory content only.

## Mount and interface

```js
const surface = McelElementRegistry.mountElement(
  "element.authoring.document-surface", host,
  {label: "Draft", html: "<p>Initial draft</p>", layout: {view: {mode: "endless"}}}
);
const state = surface.getState();
surface.mutate({operation: "append", expectedRevision: state.revision, html: "<p>New text</p>"});
const anchor = surface.getSelection();
if (anchor && anchor.text) {
  surface.mutate({operation: "replace-selection", expectedRevision: anchor.revision, anchor, text: "Revised text"});
}
const unsubscribe = surface.subscribe(event => { /* Consumer observes content/layout changes. */ });
// Consumer must release its mount when its workspace is destroyed.
unsubscribe();
surface.destroy();
```

The `getState()` snapshot contains instance identity, monotonically increasing content revision, serialized HTML, a copy of layout, and history availability. `mutate()` accepts `replace-document`, `append`, and `replace-selection`. The revision is checked before every mutation; a selection anchor also carries the issuing instance ID, block position, text offsets and selected text. Changes to content including manual edits and undo/redo invalidate old anchors. Layout-only changes do not change the content revision. No file or network side effects are permitted by the element. Consumers must independently authorize AI mutations and decide when to persist accepted content.

Other interface methods: `format(command, value)`, `insertMath(latex, layout)`, `setLayout(layout)`, `undo()`, `redo()`, `subscribe(fn)` and `destroy()`. Undo/redo currently applies to content, not layout. History is bounded to 100 content snapshots. Mounts have independent history, layout, selections, events and object registries. The caller must supply a bounded host and explicitly destroy the mount.

## Lab verification

Open MCEL Lab, Element Library Acid Test, then **Document Surface**. The two specimens have separate editable content. Use the buttons to append through the public interface, lock and replace selected text, insert math, switch between paged and endless mode, and undo/redo. Rerunning the Lab destroys the old specimens and creates fresh mounts on demand.

`python -m pytest -q tests/test_mcel_document_surface_element.py` exercises the actual browser engine and Lab specimen without a running Pretty Docs or AI backend.

## Scope of this milestone

The renderer is registered and usable both through the MCEL element registry and through the paired-source MCEL DSL compiler. The standalone document application is not yet reconstructed as a consumer of the mount interface. Chat integration, durable persistence, export, scene/game objects, hidden-plugin UI, full document import fidelity, cross-block selection transactions, and broader presentation/signal-scheduler certification remain future acceptance gates. The new element accepts a deliberately restricted HTML vocabulary (text structure, basic formatting, tables and math objects); external images, arbitrary attributes/styles, links and executable markup are not admitted through its HTML mutation boundary. Paste is plain text. It does not yet constitute a lossless importer of arbitrary legacy documents. The legacy application retains its existing import/persistence/export pathways.


## Native paired-source MCEL declaration

```js
const html = dsl.state.rendererLocal("draft-html", dsl.field.text(), {initial: "<p>Draft</p>"});
const mode = dsl.state.rendererLocal("page-mode", dsl.field.text().oneOf(["paged", "endless"]), {initial: "paged"});
const document = dsl.surface.documentSurface("draft", {value: html, mode, label: "Draft"});
```

Bind `document` to a semantic region with `dsl.surface.bindHtml`. The compiler emits a `document-surface` node with the canonical registry element ID, content state reference and optional mode state reference. This is not the Monaco `documentEditor` primitive. Content must be an initialized, unrestricted renderer-local string; the element is its sole writer. Other intents may read that state as a capability argument. A second editable element, mirrored form or selection writer may not own the same draft. The optional mode state is a renderer-local `paged`/`endless` enum, controlled by the ordinary `dsl.intent.selectView` mechanism. The `when` option supports existing visibility conditions.

The operational runtime mounts the registered renderer, publishes sanitized initial HTML, and synchronizes content changes (including manual edits, undo/redo and revision-checked mutations) to the bound state before emitting a bubbling `mcel:document-surface-change` event. Its detail contains `nodeId`, `stateId`, and the engine snapshot including `kind`, `instanceId`, `revision`, `html`, `layout` and history availability. Selection is interrogated by the instance handle; selection-only movement is not a content event. Ordinary state notifications must not replace or remount the editor. Layout changes from the declared mode state do not alter content revision.

The host obtains an instance-scoped handle from the mounted runtime using `app.getDocumentSurface("surface-node:<app-id>.draft")`. The handle exposes `getState`, `getSelection`, `mutate`, `format`, `insertMath`, `setLayout`, `undo`, and `redo`. Its mutations are in-memory; the consumer is responsible for permission and explicit review of AI edits. When mode is state-bound, the handle cannot bypass that state. Runtime unmount destroys its element instances; retained handles cannot edit destroyed instances. No source-file or backend authority is implied. An absent renderer is a preflight refusal.

Hosts using this node must load `mcel-document-engine.js`, `mcel-element-registry.js`, `mcel-elements-core.js`, and `mcel-document-surface-element.js` before calling the operational runtime mount, plus `document.css` and `mcel-document-surface-element.css`. The isolated paired-source probe provides an example; production hosts are not changed at this milestone. Automatic unpaired composition, portable packaging and full presentation/signal certification are not established by these tests.

Run `python -m pytest -q tests/test_mcel_document_surface_native.py` for compiler and real browser tests, or `python -m tools.mcel_document_surface_native_preview --open` for the generated local in-memory preview. The preview compiles the fixture on each run, does not rely on a stored IR JSON download, and creates its HTML in `runtime/reports/mcel_document_surface_native/`.
