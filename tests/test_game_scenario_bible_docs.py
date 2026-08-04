from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRETTY_DOCS = ROOT / "pretty_docs"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"

BIBLE_PATH = PRETTY_DOCS / "game-forty-system-scenario-bible.md"
ROUTE_PATH = PRETTY_DOCS / "game-forty-system-route-scenarios.md"
DESIGN_INDEX_PATH = PRETTY_DOCS / "game-mother-ship-design-index.md"
PRETTY_INDEX_PATH = PRETTY_DOCS / "index.json"

REGION_DOCS = {
    "region.origin": PRETTY_DOCS / "game-scenarios-origin-region.md",
    "region.meridian": PRETTY_DOCS / "game-scenarios-meridian-region.md",
    "region.helix": PRETTY_DOCS / "game-scenarios-helix-region.md",
    "region.crown": PRETTY_DOCS / "game-scenarios-crown-region.md",
    "region.verge": PRETTY_DOCS / "game-scenarios-verge-region.md",
}

EXPECTED_SYSTEMS_PER_REGION = {
    "region.origin": 8,
    "region.meridian": 8,
    "region.helix": 7,
    "region.crown": 3,
    "region.verge": 6,
}
EXPECTED_SCENARIO_BLOCKS_PER_REGION = {
    "region.origin": 16,  # Eight warp systems plus eight absorbed local-world threads.
    "region.meridian": 8,
    "region.helix": 7,
    "region.crown": 3,
    "region.verge": 6,
}

SCENARIO_DOC_NAMES = {
    "game-mother-ship-design-index.md",
    "game-warp-navigation-definition.md",
    "game-warp-navigation-runtime.md",
    "game-star-system-density-and-choice-contract.md",
    "game-forty-system-scenario-bible.md",
    "game-forty-system-route-scenarios.md",
    "game-scenarios-origin-region.md",
    "game-scenarios-meridian-region.md",
    "game-scenarios-helix-region.md",
    "game-scenarios-crown-region.md",
    "game-scenarios-verge-region.md",
}


class GameScenarioBibleDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
        cls.navigation = project["metadata"]["spaceNavigation"]
        cls.systems = cls.navigation["systems"]
        cls.routes = cls.navigation["routes"]

    def test_all_scenario_documents_exist_and_are_indexed(self) -> None:
        for name in SCENARIO_DOC_NAMES:
            self.assertTrue((PRETTY_DOCS / name).is_file(), name)

        design_index = DESIGN_INDEX_PATH.read_text(encoding="utf-8")
        pretty_index = json.loads(PRETTY_INDEX_PATH.read_text(encoding="utf-8"))
        indexed_paths = {entry["path"] for entry in pretty_index["documents"]}

        for name in SCENARIO_DOC_NAMES:
            self.assertIn(name, indexed_paths)
            if name != DESIGN_INDEX_PATH.name:
                self.assertIn(name, design_index)

    def test_every_authored_system_has_one_regional_dossier(self) -> None:
        self.assertEqual(32, len(self.systems))
        self.assertEqual(set(REGION_DOCS), {system["region"] for system in self.systems})

        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in REGION_DOCS.values()
        )
        for region_id, region_path in REGION_DOCS.items():
            regional = region_path.read_text(encoding="utf-8")
            region_systems = [
                system for system in self.systems if system["region"] == region_id
            ]
            self.assertEqual(EXPECTED_SYSTEMS_PER_REGION[region_id], len(region_systems), region_id)
            for system in region_systems:
                heading = (
                    f"## {system['label']} — "
                    f"{system['primaryPlanet']['label']}"
                )
                self.assertEqual(1, regional.count(heading), heading)
                self.assertEqual(1, combined.count(heading), heading)

    def test_every_authored_route_has_a_story_seed(self) -> None:
        self.assertEqual(42, len(self.routes))
        route_doc = ROUTE_PATH.read_text(encoding="utf-8")
        labels = {system["id"]: system["label"] for system in self.systems}
        for route in self.routes:
            route_label = f"{labels[route['from']]} ↔ {labels[route['to']]}"
            self.assertEqual(1, route_doc.count(route_label), route["id"])

    def test_bible_declares_design_not_implementation(self) -> None:
        bible = BIBLE_PATH.read_text(encoding="utf-8")
        self.assertIn("not implementation proof", bible)
        self.assertIn("documented idea ≠ implemented feature", bible)
        self.assertIn("## System dossier contract", bible)
        self.assertIn("## Route storytelling contract", bible)
        self.assertIn("## Scenario-state model for future implementation", bible)

    def test_regional_dossiers_cover_required_design_dimensions(self) -> None:
        required_labels = (
            "**Identity:**",
            "**Backstory:**",
            "**System structure:**",
            "**Present order and factions:**",
            "**Active scenario:**",
            "**Player entry:**",
            "**Primary gameplay:**",
            "**Local rule:**",
            "**Decision:**",
            "**Persistent consequences:**",
            "**Route ties:**",
        )
        for path in REGION_DOCS.values():
            text = path.read_text(encoding="utf-8")
            region_id = next(key for key, value in REGION_DOCS.items() if value == path)
            for label in required_labels:
                self.assertEqual(
                    EXPECTED_SCENARIO_BLOCKS_PER_REGION[region_id],
                    text.count(label),
                    f"{path.name}: {label}",
                )


if __name__ == "__main__":
    unittest.main()
