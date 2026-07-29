from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE_VIEWER = (
    ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js"
)


class Shuttle3DYellowBarCleanupTests(unittest.TestCase):
    """Patch U.6: remove three elevated yellow/amber legacy bars only."""

    def test_three_targeted_legacy_beams_are_absent(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")

        removed_beams = (
            'builder.beam([-1.12, 0.14, -8.82], [1.12, 0.14, -8.82], 0.024, amber);',
            'builder.beam([-2.55, 0.55, -12.0], [2.55, 0.55, -12.0], 0.03, amber);',
            'builder.beam([5.4, 1.15, -21.1], [5.4, 2.45, -21.1], 0.06, this.shipState?.power === "online" ? green : amber);',
        )
        for beam in removed_beams:
            with self.subTest(beam=beam):
                self.assertNotIn(beam, source)

    def test_nearby_structures_and_floor_markings_remain(self) -> None:
        source = SCENE_VIEWER.read_text(encoding="utf-8")

        retained_geometry = (
            'builder.box([-2.62, -1.05, -12.3], [-1.48, 0.2, -11.7], bulkhead);',
            'builder.box([1.48, -1.05, -12.3], [2.62, 0.2, -11.7], bulkhead);',
            'builder.ellipsoid([5.4, 0.05, -21.1], [0.74, 1.15, 0.74], 14, 8, builder.color("#115e59"));',
            'builder.beam([-1.55, -0.76, -5.25], [0.0, -0.76, -8.75], 0.024, blue);',
        )
        for geometry in retained_geometry:
            with self.subTest(geometry=geometry):
                self.assertIn(geometry, source)


if __name__ == "__main__":
    unittest.main()
