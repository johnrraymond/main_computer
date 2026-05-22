from __future__ import annotations

import argparse
import sys
from pathlib import Path

from main_computer.document_pdf_pixel_proof import default_pixel_proof_bundle_name, write_pixel_proof_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a Main Computer PDF pixel-proof bundle from a Save PDF Smoke ZIP. "
            "The proof bundle embeds the smoke page PNGs into a production-style raster PDF candidate."
        )
    )
    parser.add_argument("smoke_zip", help="Path to a *-pdf-smoke.zip bundle from the document editor")
    parser.add_argument(
        "output_zip",
        nargs="?",
        help="Optional output ZIP path. Defaults to <smoke-name>-pixel-proof.zip next to the smoke ZIP.",
    )
    args = parser.parse_args(argv)

    smoke_zip = Path(args.smoke_zip)
    if not smoke_zip.is_file():
        parser.error(f"smoke ZIP not found: {smoke_zip}")

    output_zip = Path(args.output_zip) if args.output_zip else smoke_zip.with_name(default_pixel_proof_bundle_name(smoke_zip))
    try:
        written = write_pixel_proof_bundle(smoke_zip, output_zip)
    except Exception as exc:
        print(f"pixel proof failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {written}")
    print("open candidates/pixel-raster-css96.pdf from that ZIP for the production-style raster candidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
