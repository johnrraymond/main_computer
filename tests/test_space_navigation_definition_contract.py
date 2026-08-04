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
SPACE_NAVIGATION_DEFINITION_VERSION = "game.spaceNavigation.definition.v2"
SPACE_NAVIGATION_STATE_VERSION = "game.spaceNavigation.state.v2"
START_SYSTEM = "system.solace-reach"
CAPTAIN_SCRAWL = "Congratulations, Captain, on picking the better of the two systems."
ABSORBED_SYSTEMS = {
    "system.solace-reach": (
        "system.osprey",
        "system.bellatrix",
        "system.lyra",
        "system.talon",
    ),
    "system.vela-gate": (
        "system.antares",
        "system.seraph",
        "system.bastion",
        "system.chiron",
    ),
}
REMOVED_SYSTEM_IDS = frozenset(
    system_id
    for system_ids in ABSORBED_SYSTEMS.values()
    for system_id in system_ids
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _space_navigation(project: dict[str, Any]) -> dict[str, Any]:
    navigation = (project.get("metadata") or {}).get("spaceNavigation")
    if not isinstance(navigation, dict):
        raise AssertionError("missing project.metadata.spaceNavigation")
    return navigation


def _system_planets(system: dict[str, Any]) -> list[dict[str, Any]]:
    return [system["primaryPlanet"], *(system.get("additionalPlanets") or [])]


class SpaceNavigationDefinitionContractTests(unittest.TestCase):
    """Flat thirty-two-system graph with two dense binary systems."""

    def test_machine_readable_schema_declares_v2_definition_and_state_contract(self) -> None:
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
        self.assertIn("captainScrawl", schema["required"])
        self.assertEqual(schema["properties"]["captainScrawl"]["minLength"], 1)
        self.assertEqual(schema["$defs"]["policy"]["properties"]["jumpMode"]["const"], "adjacent-only")
        self.assertFalse(schema["$defs"]["policy"]["properties"]["mapPositionAuthoritative"]["const"])
        self.assertIn("primaryPlanet", schema["$defs"]["system"]["required"])
        self.assertEqual(
            schema["$defs"]["system"]["properties"]["stars"]["items"]["$ref"],
            "#/$defs/star",
        )
        self.assertEqual(
            schema["$defs"]["system"]["properties"]["additionalPlanets"]["items"]["$ref"],
            "#/$defs/planet",
        )
        self.assertFalse(schema["$defs"]["star"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["planet"]["additionalProperties"])
        self.assertIn("habitable", schema["$defs"]["planet"]["properties"])
        self.assertIn("inhabited", schema["$defs"]["planet"]["properties"])
        self.assertIn("formerSystemId", schema["$defs"]["planet"]["properties"])
        self.assertEqual(schema["$defs"]["planet"]["properties"]["moonCount"]["minimum"], 0)
        self.assertEqual(
            schema["$defs"]["system"]["properties"]["localDestinations"]["items"]["$ref"],
            "#/$defs/localDestination",
        )
        self.assertEqual(
            schema["$defs"]["system"]["properties"]["localRoutes"]["items"]["$ref"],
            "#/$defs/localRoute",
        )
        self.assertIn("arrivalDestinationId", schema["$defs"]["system"]["dependentRequired"])
        self.assertIn("currentLocalDestinationId", schema["$defs"]["stateDefaults"]["required"])
        self.assertIn("discoveredLocalDestinations", schema["$defs"]["stateDefaults"]["required"])
        self.assertFalse(schema["$defs"]["localDestination"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["localRoute"]["additionalProperties"])
        self.assertEqual(
            schema["$defs"]["localRoute"]["properties"]["presentationDurationMs"]["minimum"],
            250,
        )

    def test_default_projects_share_one_thirty_two_system_definition(self) -> None:
        definitions = [_space_navigation(_load_json(path)) for path in PROJECT_PATHS]

        self.assertEqual(definitions[1:], definitions[:-1])
        for definition in definitions:
            self.assertEqual(definition["schema"], SPACE_NAVIGATION_SCHEMA)
            self.assertEqual(definition["definitionVersion"], SPACE_NAVIGATION_DEFINITION_VERSION)
            self.assertEqual(definition["stateVersion"], SPACE_NAVIGATION_STATE_VERSION)
            self.assertEqual(definition["startSystem"], START_SYSTEM)
            self.assertEqual(definition["captainScrawl"], CAPTAIN_SCRAWL)
            self.assertEqual(len(definition["systems"]), 32)
            self.assertEqual(len(definition["routes"]), 42)

    def test_graph_is_connected_and_has_no_dead_ends(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        systems = definition["systems"]
        routes = definition["routes"]

        system_ids = [system["id"] for system in systems]
        route_ids = [route["id"] for route in routes]
        local_space_ids = [system["localSpaceId"] for system in systems]
        planets = [planet for system in systems for planet in _system_planets(system)]
        planet_ids = [planet["id"] for planet in planets]
        map_positions = [tuple(system["mapPosition"]) for system in systems]

        self.assertEqual(len(system_ids), 32)
        self.assertEqual(len(system_ids), len(set(system_ids)))
        self.assertTrue(REMOVED_SYSTEM_IDS.isdisjoint(system_ids))
        self.assertEqual(len(route_ids), len(set(route_ids)))
        self.assertEqual(len(local_space_ids), len(set(local_space_ids)))
        self.assertEqual(len(planet_ids), 40)
        self.assertEqual(len(planet_ids), len(set(planet_ids)))
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
            self.assertNotIn(route["from"], REMOVED_SYSTEM_IDS)
            self.assertNotIn(route["to"], REMOVED_SYSTEM_IDS)
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

    def test_first_two_systems_are_dense_binary_systems(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        systems = {system["id"]: system for system in definition["systems"]}

        self.assertEqual(list(systems)[:2], list(ABSORBED_SYSTEMS))
        for parent_id, absorbed_ids in ABSORBED_SYSTEMS.items():
            system = systems[parent_id]
            planets = _system_planets(system)
            stars = system["stars"]
            self.assertEqual(2, len(stars))
            self.assertEqual({"primary", "companion"}, {star["role"] for star in stars})
            self.assertEqual(5, len(planets))
            self.assertGreaterEqual(sum(planet.get("habitable") is True for planet in planets), 2)
            self.assertTrue(all(planet.get("inhabited") is True for planet in planets))
            self.assertEqual(
                absorbed_ids,
                tuple(planet["formerSystemId"] for planet in system["additionalPlanets"]),
            )

    def test_first_two_systems_author_connected_local_navigation_contracts(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        systems = {system["id"]: system for system in definition["systems"]}

        expected = {
            "system.solace-reach": {
                "arrival": "destination.solace-reach.haven-orbit",
                "bodyIds": {
                    "planet.haven",
                    "planet.osprey",
                    "planet.bellara",
                    "planet.lyria",
                    "planet.talon",
                },
            },
            "system.vela-gate": {
                "arrival": "destination.vela-gate.velaris-orbit",
                "bodyIds": {
                    "planet.velaris",
                    "planet.antares",
                    "planet.seraph",
                    "planet.bastion",
                    "planet.chiron",
                },
            },
        }

        all_destination_ids: list[str] = []
        all_route_ids: list[str] = []
        for system_id, contract in expected.items():
            system = systems[system_id]
            destinations = system["localDestinations"]
            routes = system["localRoutes"]
            destination_ids = {destination["id"] for destination in destinations}
            body_ids = {planet["id"] for planet in _system_planets(system)}

            self.assertEqual(system["arrivalDestinationId"], contract["arrival"])
            self.assertEqual(len(destinations), 5)
            self.assertEqual(len(routes), 5)
            self.assertEqual({destination["parentBodyId"] for destination in destinations}, contract["bodyIds"])
            self.assertEqual(body_ids, contract["bodyIds"])
            self.assertTrue(all(destination["kind"] == "planet-orbit" for destination in destinations))
            self.assertTrue(all(destination["discoveredByDefault"] for destination in destinations))
            self.assertTrue(all(destination["availableByDefault"] for destination in destinations))
            self.assertTrue(all(len(destination["position"]) == 2 for destination in destinations))

            graph = {destination_id: set() for destination_id in destination_ids}
            for route in routes:
                self.assertIn(route["from"], destination_ids)
                self.assertIn(route["to"], destination_ids)
                self.assertNotEqual(route["from"], route["to"])
                graph[route["from"]].add(route["to"])
                if route["bidirectional"]:
                    graph[route["to"]].add(route["from"])

            reached = {system["arrivalDestinationId"]}
            queue = deque(reached)
            while queue:
                current = queue.popleft()
                for destination_id in graph[current]:
                    if destination_id not in reached:
                        reached.add(destination_id)
                        queue.append(destination_id)
            self.assertEqual(reached, destination_ids)

            all_destination_ids.extend(destination_ids)
            all_route_ids.extend(route["id"] for route in routes)

        self.assertEqual(len(all_destination_ids), 10)
        self.assertEqual(len(all_destination_ids), len(set(all_destination_ids)))
        self.assertEqual(len(all_route_ids), 10)
        self.assertEqual(len(all_route_ids), len(set(all_route_ids)))

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
        self.assertEqual(
            state_defaults["currentLocalDestinationId"],
            systems[0]["arrivalDestinationId"],
        )
        self.assertEqual(
            set(state_defaults["discoveredLocalDestinations"]["system.solace-reach"]),
            {destination["id"] for destination in systems[0]["localDestinations"]},
        )
        self.assertEqual(
            set(state_defaults["discoveredLocalDestinations"]["system.vela-gate"]),
            {destination["id"] for destination in systems[1]["localDestinations"]},
        )
        self.assertEqual(set(state_defaults["discoveredSystems"]), system_ids)
        self.assertTrue(REMOVED_SYSTEM_IDS.isdisjoint(state_defaults["discoveredSystems"]))
        self.assertTrue(all(system["arrivalProfile"] in arrival_profiles for system in systems))
        self.assertTrue(route_ids)

    def test_definition_keeps_routes_authoritative_and_ui_flat(self) -> None:
        definition = _space_navigation(_load_json(PROJECT_PATHS[0]))
        policy = definition["policy"]

        self.assertEqual(policy["jumpMode"], "adjacent-only")
        self.assertTrue(policy["directJumpRequiresRoute"])
        self.assertFalse(policy["mapPositionAuthoritative"])
        self.assertTrue(policy["allowDirectedRoutes"])
        self.assertTrue(all("neighbors" not in system for system in definition["systems"]))
        self.assertTrue(all("subsystems" not in system for system in definition["systems"]))


if __name__ == "__main__":
    unittest.main()
