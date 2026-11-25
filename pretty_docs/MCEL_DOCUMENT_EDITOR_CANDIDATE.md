# Native MCEL Document Editor candidate

Requires the verified MCEL Document Surface Element and Native Language Integration patches. Do not apply the superseded host-first Phase 1 patch.

The paired source is `mcel_apps/document/application.html` and `application.js`. It consumes `dsl.surface.documentSurface` and uses its public interface for editing, selection, document loading and export. The candidate route is `/applications/mcel-semantic-document` on the running local Main Computer viewport. Restart the viewport after applying the patch. The incumbent `/applications/document` route remains unchanged.

Open Scratchpad or use List Documents, select a Pretty Docs entry, and Open Document. Candidate saves require confirmation and are isolated in `runtime/mcel_document_candidate_drafts`. Candidate drafts take precedence when reopening; otherwise the incumbent draft or read-only source is imported. An unsaved active document must be saved before switching. Unsupported rich markup is rejected rather than silently stripped. Save conflicts refuse rather than overwrite a newer candidate draft.

The canvas offers basic formatting, headings, lists, math, undo/redo and page layout. Document AI locks one supported selection, requests a text-only replacement, and applies it only after explicit confirmation and revision verification. PDF export uses the content-page backend, not the live-vector fidelity path. No original Pretty Docs source or incumbent draft is overwritten.

Acceptance command:

```powershell
python -m pytest -q tests/test_mcel_document_candidate.py tests/test_mcel_document_surface_native.py tests/test_mcel_document_surface_element.py mcel_apps/document/tests/test_source_contract.py
```

Browser tests mount the actual compiled application with deterministic HTTP responses; candidate persistence and conflict checks are tested separately. Live authenticated viewport, live AI, actual PDF output and full incumbent feature parity require additional acceptance before route adoption. Full legacy markup, scene objects, autosave, EPUB and other incumbent-specific functionality are not certified. Chat Console is unchanged.
