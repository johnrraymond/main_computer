from __future__ import annotations

import hashlib
from pathlib import Path

from huggingface_hub import snapshot_download


REPO_ID = "C-Tianyu/NanoJev"
REVISION = "unified-games-v1"
EXPECTED_WEIGHTS_SHA256 = "f68c47d66998231b86b7e91b4ed5e82ae23acf104c8b7cd6d165c3ac7b7ffe1b"
DESTINATION = Path("/opt/nanojev")


snapshot_download(
    repo_id=REPO_ID,
    revision=REVISION,
    token=False,
    local_dir=DESTINATION,
    allow_patterns=[
        "best.safetensors",
        "config.json",
        "backbone_config/*",
        "tokenizer/*",
        "source/scripts/*",
        "source/requirements-toy.txt",
        "SHA256_MANIFEST.json",
    ],
)

weights = DESTINATION / "best.safetensors"
if not weights.is_file():
    raise SystemExit(f"NanoJev release did not contain {weights}")

sha256 = hashlib.sha256()
with weights.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        sha256.update(chunk)
actual = sha256.hexdigest()
if actual != EXPECTED_WEIGHTS_SHA256:
    raise SystemExit(
        "NanoJev unified-games-v1 weight SHA256 mismatch: "
        f"expected {EXPECTED_WEIGHTS_SHA256}, got {actual}"
    )

requirements = DESTINATION / "source" / "requirements-toy.txt"
server = DESTINATION / "source" / "scripts" / "serve_decisions.py"
if not requirements.is_file() or not server.is_file():
    raise SystemExit("NanoJev unified-games-v1 release is missing its matching inference source")

print(f"NanoJev release ready: {REVISION} {actual}")
