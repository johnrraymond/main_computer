# Multimodal RAG

Start here: [Agent and RAG Overview](agent-rag-overview.md)

Status: experimental/operator smoke surfaces.

## Purpose

Multimodal RAG combines images with logs, structured summaries, and source evidence so a model can diagnose or answer from more than text alone.

## High-level operation

**Analyze multimodal evidence** — Accept a bounded target and a collection of typed evidence references, send supported media to a vision-capable provider, and return a grounded result with per-item provenance.

## Evidence contract

A multimodal item should carry:

```json
{
  "id": "plot-a",
  "media_type": "image/png",
  "path": "repo/relative/or/external/fixture/path",
  "sha256": "...",
  "role": "diagnostic_plot",
  "caption": "host-supplied description",
  "source_relationships": []
}
```

Text logs, JSON summaries, and source snippets should remain separate evidence items rather than being flattened into an untraceable prompt.

## Current harnesses

```text
main_computer/rag_gemma4_image_recognition_smoke.py
main_computer/rag_profile_space_latest_png_rag_smoke.py
```

The image-recognition smoke tests a direct Ollama vision request. The profile-space smoke builds a bounded evidence bundle containing two diagnostic PNGs, recent/surprise logs, JSON summaries, and source hierarchy snippets.

## Safety and correctness

- Validate file type, size, and scope before encoding media.
- Record the exact provider/model and whether vision input is supported.
- Preserve hashes for every media and text item.
- Distinguish what is visible in an image from what is asserted by logs or source.
- Do not treat image interpretation as runtime verification.
- Do not write or replace media files without using the normal proposal/artifact boundary.

## Desired result fields

A result should include observations by evidence item, grounded conclusions, contradictions, uncertainties, missing evidence, provider metadata, and any recommended next diagnostic operation.

## Provenance

- Source snapshot: `main_computer_test-20260730-145547.zip`
- Evidence status: source-inspected documentation.
- Model-backed verification: not run for documentation generation.
- Authority rule: mounted implementation and focused tests override this guide when they disagree.
