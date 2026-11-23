"""The environmental CRT patina is mathematically continuous at its repeats.

This test inspects the embedded encoded assets actually shipped to browsers,
not the intermediate Python images. The aperture grille remains independent.
"""
import base64
import io
from pathlib import Path
import re

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LAW = ROOT / "main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js"


def test_crt_wear_tiles_join_without_visible_rectangular_seams():
    source = LAW.read_text(encoding="utf-8")
    images = {}
    for name in ("ambient", "dark", "glint"):
        match = re.search(
            rf"\b{name}: 'url\(\"data:image/webp;base64,([A-Za-z0-9+/=]+)\"\)'",
            source,
        )
        assert match is not None, f"Missing embedded {name} texture"
        image = Image.open(io.BytesIO(base64.b64decode(match.group(1)))).convert("RGBA")
        assert image.size == (1024, 768)
        alpha = np.asarray(image, dtype=np.uint8)[:, :, 3]
        images[name] = alpha

        # No abrupt alpha jump on repeated X/Y tile boundaries at all.
        assert np.array_equal(alpha[:, 0], alpha[:, -1]), f"Horizontal seam in {name}"
        assert np.array_equal(alpha[0, :], alpha[-1, :]), f"Vertical seam in {name}"

        # The patch must not solve this by erasing the texture or creating
        # a blank border. Texture remains active and covers every tile edge.
        assert alpha.std() > 2, f"Flat/inactive wear texture: {name}"
        assert np.mean(alpha[:, 0]) > 1, f"Faded edge band: {name}"
        assert np.mean(alpha[0, :]) > 1, f"Faded edge band: {name}"

        # The neighboring samples on each side of the join should be
        # comparably smooth to samples elsewhere in the tile.
        local_x = np.abs(np.diff(alpha.astype(np.float32), axis=1)).mean()
        local_y = np.abs(np.diff(alpha.astype(np.float32), axis=0)).mean()
        join_neighborhood_x = np.abs(alpha[:, 1].astype(float) - alpha[:, -2]).mean()
        join_neighborhood_y = np.abs(alpha[1, :].astype(float) - alpha[-2, :]).mean()
        assert join_neighborhood_x < max(4.0, 6 * local_x), (name, join_neighborhood_x, local_x)
        assert join_neighborhood_y < max(4.0, 6 * local_y), (name, join_neighborhood_y, local_y)

    assert "--mcel-tng-aperture-display" in source
    assert "--mcel-tng-aperture-background" in source
    assert 'data-mcel-tng-gold-text' in source
    assert "background-size: 1024px 768px" in source
