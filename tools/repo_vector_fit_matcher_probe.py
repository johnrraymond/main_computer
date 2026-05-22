from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CandidateSummary:
    id: str
    path: str
    mean_fit_score: float
    max_fit_score: float
    page_count: int
    source: str

    def as_console_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "meanFitScore": self.mean_fit_score,
            "maxFitScore": self.max_fit_score,
        }


def _candidate_rank_key(candidate: CandidateSummary) -> tuple[float, float, str]:
    return (candidate.mean_fit_score, candidate.max_fit_score, candidate.id)


def _as_float(value: Any, default: float = 1_000_000.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        with archive.open(name, "r") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except KeyError as exc:
        raise SystemExit(f"Smoke ZIP is missing required file: {name}") from exc


def _zip_member_exists(archive: zipfile.ZipFile, name: str) -> bool:
    try:
        archive.getinfo(name)
    except KeyError:
        return False
    return True


def _load_repo_matcher() -> Any:
    from main_computer.viewport_routes_docs import ViewportDocsRoutesMixin

    return object.__new__(ViewportDocsRoutesMixin)


def _source_page_count(metrics: dict[str, Any]) -> int:
    count = metrics.get("sourcePageCount")
    if isinstance(count, int):
        return count
    pages = metrics.get("pages")
    if isinstance(pages, list):
        return len(pages)
    render = metrics.get("renderComparison")
    if isinstance(render, dict):
        candidates = render.get("candidates")
        if isinstance(candidates, list) and candidates:
            first_pages = candidates[0].get("pages") if isinstance(candidates[0], dict) else None
            if isinstance(first_pages, list):
                return len(first_pages)
    return 0


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("id")
        or candidate.get("candidateId")
        or (candidate.get("settings") or {}).get("id")
        or candidate.get("path")
        or ""
    )


def _stored_page_scores(candidate: dict[str, Any]) -> list[float]:
    pages = candidate.get("pages")
    if not isinstance(pages, list):
        return []
    scores: list[float] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        if "fitScore" in page:
            scores.append(_as_float(page.get("fitScore")))
    return scores


def _candidate_summary_from_scores(candidate: dict[str, Any], scores: list[float], source: str) -> CandidateSummary:
    candidate_id = _candidate_id(candidate)
    path = str(candidate.get("path") or "")
    if not scores:
        mean_score = _as_float(candidate.get("meanFitScore"))
        max_score = _as_float(candidate.get("maxFitScore"))
        page_count = 0
    else:
        mean_score = statistics.fmean(scores)
        max_score = max(scores)
        page_count = len(scores)
    return CandidateSummary(
        id=candidate_id,
        path=path,
        mean_fit_score=mean_score,
        max_fit_score=max_score,
        page_count=page_count,
        source=source,
    )


def _recompute_candidate_from_pngs(
    *,
    archive: zipfile.ZipFile,
    matcher: Any,
    candidate: dict[str, Any],
) -> tuple[CandidateSummary, list[dict[str, Any]]]:
    pages = candidate.get("pages")
    if not isinstance(pages, list):
        return _candidate_summary_from_scores(candidate, [], "stored-candidate-summary"), []

    recomputed_pages: list[dict[str, Any]] = []
    scores: list[float] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        source_path = str(page.get("sourcePath") or "")
        rendered_path = str(page.get("renderedPath") or "")
        index = int(page.get("index") or len(recomputed_pages) + 1)
        if not source_path or not rendered_path:
            continue
        if not _zip_member_exists(archive, source_path) or not _zip_member_exists(archive, rendered_path):
            continue
        source_png = archive.read(source_path)
        rendered_png = archive.read(rendered_path)
        page_metrics, _diff_png = matcher._compare_document_pdf_text_mask_fit(
            source_png=source_png,
            rendered_png=rendered_png,
        )
        page_metrics["index"] = index
        page_metrics["sourcePath"] = source_path
        page_metrics["renderedPath"] = rendered_path
        recomputed_pages.append(page_metrics)
        scores.append(_as_float(page_metrics.get("fitScore")))

    if scores:
        return _candidate_summary_from_scores(candidate, scores, "repo-matcher-png-recompute"), recomputed_pages
    return _candidate_summary_from_scores(candidate, _stored_page_scores(candidate), "stored-page-metrics"), []


def _candidate_summaries(
    *,
    archive: zipfile.ZipFile,
    metrics: dict[str, Any],
    recompute: bool,
) -> tuple[list[CandidateSummary], dict[str, list[dict[str, Any]]]]:
    render = metrics.get("renderComparison")
    candidates = render.get("candidates") if isinstance(render, dict) else None
    if not isinstance(candidates, list):
        candidates = []

    matcher = _load_repo_matcher() if recompute else None
    summaries: list[CandidateSummary] = []
    page_metrics_by_id: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("status") not in (None, "scored"):
            continue
        candidate_id = _candidate_id(candidate)
        if recompute:
            summary, page_metrics = _recompute_candidate_from_pngs(
                archive=archive,
                matcher=matcher,
                candidate=candidate,
            )
            if page_metrics:
                page_metrics_by_id[candidate_id] = page_metrics
        else:
            summary = _candidate_summary_from_scores(candidate, _stored_page_scores(candidate), "stored-page-metrics")
            stored_pages = candidate.get("pages")
            if isinstance(stored_pages, list):
                page_metrics_by_id[candidate_id] = [page for page in stored_pages if isinstance(page, dict)]
        if summary.id:
            summaries.append(summary)

    return summaries, page_metrics_by_id


def _bundle_best_summary(metrics: dict[str, Any]) -> dict[str, Any] | None:
    best = metrics.get("bestCandidate")
    if not isinstance(best, dict):
        render = metrics.get("renderComparison")
        if isinstance(render, dict):
            best = render.get("bestCandidate")
    return best if isinstance(best, dict) else None


def _best_with_bundle_tie_compatibility(
    *,
    summaries: list[CandidateSummary],
    bundle_best: dict[str, Any] | None,
) -> tuple[CandidateSummary | None, CandidateSummary | None, list[CandidateSummary], bool]:
    if not summaries:
        return None, None, [], False

    deterministic_best = min(summaries, key=_candidate_rank_key)
    best_mean = deterministic_best.mean_fit_score
    best_max = deterministic_best.max_fit_score
    exact_ties = [
        candidate
        for candidate in summaries
        if candidate.mean_fit_score == best_mean and candidate.max_fit_score == best_max
    ]
    exact_ties.sort(key=lambda candidate: candidate.id)

    selected_best = deterministic_best
    selected_from_bundle_tie = False
    if bundle_best:
        bundle_id = str(bundle_best.get("id") or (bundle_best.get("settings") or {}).get("id") or "")
        for candidate in exact_ties:
            if candidate.id == bundle_id:
                selected_best = candidate
                selected_from_bundle_tie = candidate.id != deterministic_best.id
                break

    return selected_best, deterministic_best, exact_ties, selected_from_bundle_tie


def _ranked_candidates(summaries: list[CandidateSummary]) -> list[CandidateSummary]:
    return sorted(summaries, key=_candidate_rank_key)


def _candidate_is_near_best(candidate: CandidateSummary, best: CandidateSummary | None) -> bool:
    if best is None:
        return True
    if candidate.mean_fit_score == best.mean_fit_score and candidate.max_fit_score == best.max_fit_score:
        return True
    mean_tolerance = max(0.01, abs(best.mean_fit_score) * 0.25)
    max_tolerance = max(0.01, abs(best.max_fit_score) * 0.25)
    return (
        candidate.mean_fit_score <= best.mean_fit_score + mean_tolerance
        and candidate.max_fit_score <= best.max_fit_score + max_tolerance
    )


def _high_xor_diagnostics(
    *,
    page_metrics_by_id: dict[str, list[dict[str, Any]]],
    summaries: list[CandidateSummary],
    selected_best: CandidateSummary | None,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    notes: list[str] = []
    summaries_by_id = {candidate.id: candidate for candidate in summaries}
    for candidate_id, pages in sorted(page_metrics_by_id.items()):
        candidate = summaries_by_id.get(candidate_id)
        near_best = _candidate_is_near_best(candidate, selected_best) if candidate is not None else True
        for page in pages:
            xor_percent = _as_float(page.get("xorInkPercentOfUnion"), 0.0)
            bbox_score = _as_float(page.get("bboxScore"), 1.0)
            centroid_score = _as_float(page.get("centroidScore"), 1.0)
            if xor_percent >= 20.0 and bbox_score <= 0.02 and centroid_score <= 0.02:
                message = (
                    f"{candidate_id} page {page.get('index')}: high XOR with nearly matching bounding boxes; "
                    "the matcher is probably scoring antialias/stroke raster differences more than layout."
                )
                if near_best:
                    warnings.append(message)
                else:
                    notes.append(f"{message} This candidate is not near-best, so this is diagnostic noise rather than a blocker.")
    return warnings, notes


def _write_csv(path: Path, summaries: list[CandidateSummary]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "path", "meanFitScore", "maxFitScore", "pageCount", "source"],
        )
        writer.writeheader()
        for candidate in _ranked_candidates(summaries):
            writer.writerow(
                {
                    "id": candidate.id,
                    "path": candidate.path,
                    "meanFitScore": f"{candidate.mean_fit_score:.17g}",
                    "maxFitScore": f"{candidate.max_fit_score:.17g}",
                    "pageCount": candidate.page_count,
                    "source": candidate.source,
                }
            )


def _write_report(
    path: Path,
    *,
    smoke_zip: Path,
    source_pages: int,
    bundle_best: dict[str, Any] | None,
    selected_best: CandidateSummary | None,
    deterministic_best: CandidateSummary | None,
    exact_ties: list[CandidateSummary],
    selected_from_bundle_tie: bool,
    summaries: list[CandidateSummary],
    warnings: list[str],
    notes: list[str],
) -> None:
    payload = {
        "smokeZip": str(smoke_zip),
        "sourcePages": source_pages,
        "bundleBest": bundle_best,
        "recomputedBest": selected_best.as_console_dict() if selected_best else None,
        "deterministicBest": deterministic_best.as_console_dict() if deterministic_best else None,
        "selection": {
            "rankOrder": ["meanFitScore", "maxFitScore", "candidateId"],
            "tieBreak": "candidateId",
            "exactTieCount": len(exact_ties),
            "exactTies": [candidate.as_console_dict() for candidate in exact_ties],
            "selectedFromBundleExactTie": selected_from_bundle_tie,
        },
        "candidates": [
            candidate.as_console_dict() | {"path": candidate.path, "source": candidate.source}
            for candidate in _ranked_candidates(summaries)
        ],
        "warnings": warnings,
        "notes": notes,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe a Main Computer vector-fit smoke ZIP with the current repo matcher. "
            "Exact best-score ties are treated as equivalent so older bundles do not emit "
            "a false bundle-best mismatch warning. Losing-candidate antialias/stroke diagnostics "
            "are reported as notes instead of warnings."
        )
    )
    parser.add_argument("smoke_zip", help="Path to a Save Vector Fit Smoke ZIP")
    parser.add_argument("--out-csv", default=None, help="Optional CSV path for candidate fit scores")
    parser.add_argument(
        "--stored-metrics",
        action="store_true",
        help="Use metrics.json page fitScore values instead of recomputing from pages/rendered PNGs",
    )
    args = parser.parse_args(argv)

    smoke_zip = Path(args.smoke_zip)
    if not smoke_zip.exists():
        print(f"error: smoke ZIP not found: {smoke_zip}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(smoke_zip, "r") as archive:
        metrics = _read_json_member(archive, "metrics.json")
        source_pages = _source_page_count(metrics)
        bundle_best = _bundle_best_summary(metrics)
        summaries, page_metrics_by_id = _candidate_summaries(
            archive=archive,
            metrics=metrics,
            recompute=not bool(args.stored_metrics),
        )

    selected_best, deterministic_best, exact_ties, selected_from_bundle_tie = _best_with_bundle_tie_compatibility(
        summaries=summaries,
        bundle_best=bundle_best,
    )

    warnings, diagnostic_notes = _high_xor_diagnostics(
        page_metrics_by_id=page_metrics_by_id,
        summaries=summaries,
        selected_best=selected_best,
    )
    notes: list[str] = list(diagnostic_notes)
    if selected_from_bundle_tie and selected_best is not None and deterministic_best is not None:
        notes.append(
            f"bundle best {selected_best.id} is exact-tied with deterministic best {deterministic_best.id}; "
            "treating them as equivalent instead of warning about a mismatch."
        )

    if bundle_best and selected_best is not None:
        bundle_id = str(bundle_best.get("id") or (bundle_best.get("settings") or {}).get("id") or "")
        if bundle_id and bundle_id != selected_best.id:
            warnings.append(f"bundle best {bundle_id} differs from recomputed best {selected_best.id}")

    default_report = smoke_zip.with_name(f"{smoke_zip.stem}-repo-matcher-report.json")
    if args.out_csv:
        _write_csv(Path(args.out_csv), summaries)
    _write_report(
        default_report,
        smoke_zip=smoke_zip,
        source_pages=source_pages,
        bundle_best=bundle_best,
        selected_best=selected_best,
        deterministic_best=deterministic_best,
        exact_ties=exact_ties,
        selected_from_bundle_tie=selected_from_bundle_tie,
        summaries=summaries,
        warnings=warnings,
        notes=notes,
    )

    print(f"Smoke ZIP: {smoke_zip}")
    print(f"Source pages: {source_pages}")
    print(f"Bundle best:     {bundle_best}")
    print(f"Recomputed best: {selected_best.as_console_dict() if selected_best else None}")
    if deterministic_best is not None and selected_best is not None and deterministic_best.id != selected_best.id:
        print(f"Deterministic best: {deterministic_best.as_console_dict()}")
    print()
    print("Candidate means:")
    for candidate in _ranked_candidates(summaries):
        print(f"  {candidate.id}: mean={candidate.mean_fit_score:.9f} max={candidate.max_fit_score:.9f}")

    if notes:
        print()
        print("Notes:")
        for note in notes:
            print(f"  - {note}")
    if warnings:
        print()
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    print()
    print(f"Wrote: {default_report}")
    if args.out_csv:
        print(f"Wrote: {args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
