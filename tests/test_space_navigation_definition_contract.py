from __future__ import annotations

import json
import math
import unittest
from collections import deque
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PATHS = (
    ROOT / "game_projects" / "webgl-demo" / "project.json",
    ROOT / "game_projects" / "starter-game" / "project.json",
    ROOT / "game_projects" / "new-game" / "project.json",
)
SCHEMA_PATH = ROOT / "game_projects" / "schema" / "space-navigation.v1.schema.json"
SPACE_NAVIGATION_SCHEMA = "game.spaceNavigation.v1"
SPACE_NAVIGATION_DEFINITION_VERSION = "game.spaceNavigation.definition.v1"
SPACE_NAVIGATION_STATE_VERSION = "game.spaceNavigation.state.v1"
START_SYSTEM = "system.solace-reach"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _space_navigation(project: dict[str, Any]) -> dict[str, Any]:
    navigation = (project.get("metadata") or {}).get("spaceNavigation")
    if not isinstance(navigation, dict):
        raise AssertionError("missing project.metadata.spaceNavigation")
    return navigation


class SpaceNavigationDefinitionContractTests(unittest.TestCase):
    """Warp Patch 1: authored forty-system graph and machine-readable contract."""

    def test_machine_readable_schema_declares_v1_contract(self) -> None:
        schema = _load_json(SCHEMA_PATH)

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["$id"], SPACE_NAVIGATION_SCHEMA)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schema"]["const"], SPACE_NAVIGATION_SCHEMA)
        self.assertEqual(
            schema["properties"]["definitionVersion"]["const"],
            SPACE_NAVIGATION_DEFINITION_VERSION,
        )
        self.assertEqual(
            schema["properties"]["stateVersion"]["const"],
            SPACE_NAVIGATION_STATE_VERSION,
        )
        self.assertEqual(schema["$defs"]["policy"]["properties"]["jumpMode"]["const"], "adjacent-only")
        self.assertFalse(schema["$defs"]["policy"]["properties"]["mapPositionAuthoritative"]["const"])
        self.assertIn("primaryPlanet", schema["$defs"]["system"]["required"])
        self.assertFalse(schema["$defs"]["planet"]["additionalProperties"])
        self.assertEqual(schema["$defs"]["planet"]["properties"]["moonCount"]["minimum"], 0)

    def test_default_projects_share_one_forty_system_definition(self) -> None:
        definitions = [_space_navigation(_load_json(path)) for path in PROJECT_PATHS]

        self.assertEqual(definitions[1:], definitions[:-1])
        for definition in definitions:
            self.assertEqual(definition["schema"], SPACE_NAVIGATION_SCHEMA)
            self.assertEqual(definition["definitionVersion"], SPACE_NAVIGATION_DEFINITION_VERSION)
            self.assertEqual(definition["stateVersion"], SPACE_NAVIGATION_STATE_VERSION)
            self.assertEqual(definition["startSystem"], START_SYSTEM)
            self.assertEqual(len(definition["systems"]), 40)
            self.assertEqual(len(definition["routes"]), 50)

    def test_forty_system_graph_is_connected_and_has_no_dead_ends(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        systems = definition["systems"]
        routes = definition["routes"]

        system_ids = [system["id"] for system in systems]
        route_ids = [route["id"] for route in routes]
        local_space_ids = [system["localSpaceId"] for system in systems]
        planets = [system["primaryPlanet"] for system in systems]
        planet_ids = [planet["id"] for planet in planets]
        planet_labels = [planet["label"] for planet in planets]
        map_positions = [tuple(system["mapPosition"]) for system in systems]

        self.assertEqual(len(system_ids), len(set(system_ids)))
        self.assertEqual(len(route_ids), len(set(route_ids)))
        self.assertEqual(len(local_space_ids), len(set(local_space_ids)))
        self.assertEqual(len(planet_ids), 40)
        self.assertEqual(len(planet_ids), len(set(planet_ids)))
        self.assertEqual(len(planet_labels), len(set(planet_labels)))
        self.assertTrue(all(planet["description"] for planet in planets))
        self.assertTrue(all(planet["rings"]["outerRadius"] > planet["rings"]["innerRadius"] for planet in planets))
        self.assertEqual(len(map_positions), len(set(map_positions)))
        self.assertTrue(all(len(position) == 2 for position in map_positions))
        self.assertTrue(all(math.isfinite(value) for position in map_positions for value in position))

        known_systems = set(system_ids)
        graph = {system_id: set() for system_id in system_ids}
        for route in routes:
            self.assertIn(route["from"], known_systems)
            self.assertIn(route["to"], known_systems)
            self.assertNotEqual(route["from"], route["to"])
            graph[route["from"]].add(route["to"])
            if route["bidirectional"]:
                graph[route["to"]].add(route["from"])

        self.assertGreaterEqual(min(len(destinations) for destinations in graph.values()), 2)

        reached = {definition["startSystem"]}
        queue = deque(reached)
        while queue:
            current = queue.popleft()
            for destination in graph[current]:
                if destination not in reached:
                    reached.add(destination)
                    queue.append(destination)
        self.assertEqual(reached, known_systems)

    def test_state_and_arrival_references_resolve(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        systems = definition["systems"]
        system_ids = {system["id"] for system in systems}
        route_ids = {route["id"] for route in definition["routes"]}
        arrival_profiles = definition["arrivalProfiles"]
        state_defaults = definition["stateDefaults"]

        self.assertIn(definition["startSystem"], system_ids)
        self.assertEqual(state_defaults["currentSystemId"], definition["startSystem"])
        self.assertIsNone(state_defaults["plottedRouteId"])
        self.assertEqual(state_defaults["travelPhase"], "in-system")
        self.assertEqual(state_defaults["elapsedWorldTime"], 0)
        self.assertEqual(set(state_defaults["discoveredSystems"]), system_ids)
        self.assertTrue(all(system["arrivalProfile"] in arrival_profiles for system in systems))
        self.assertTrue(route_ids)

    def test_definition_keeps_routes_authoritative(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        policy = definition["policy"]

        self.assertEqual(policy["jumpMode"], "adjacent-only")
        self.assertTrue(policy["directJumpRequiresRoute"])
        self.assertFalse(policy["mapPositionAuthoritative"])
        self.assertTrue(policy["allowDirectedRoutes"])
        self.assertTrue(all("neighbors" not in system for system in definition["systems"]))


if __name__ == "__main__":
    unittest.main()
