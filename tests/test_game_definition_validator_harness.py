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
SUPPORTED_SYSTEM_TARGETS = {"enemyShip", "currentSystemPlanet"}
SUPPORTED_DISPLAY_PROGRAMS = {"enemyShipTactical", "systemPlanet"}
MOTHER_SHIP_INTERIOR_SCHEMA = "game.motherShipInterior.v1"
MOTHER_SHIP_INTERIOR_DEFINITION_VERSION = "game.motherShipInterior.definition.v2"
MOTHER_SHIP_INTERIOR_STATE_VERSION = "game.motherShipInterior.state.v1"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bounds_are_valid(bounds: dict[str, Any] | None) -> bool:
    if not isinstance(bounds, dict):
        return False
    values = [bounds.get("minX"), bounds.get("maxX"), bounds.get("minZ"), bounds.get("maxZ")]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        return False
    return bounds["minX"] <= bounds["maxX"] and bounds["minZ"] <= bounds["maxZ"]


def _point_inside_bounds(x: float, z: float, bounds: dict[str, Any] | None) -> bool:
    return (
        _bounds_are_valid(bounds)
        and bounds["minX"] <= x <= bounds["maxX"]
        and bounds["minZ"] <= z <= bounds["maxZ"]
    )


def _bounds_inside_bounds(inner: dict[str, Any], outer: dict[str, Any]) -> bool:
    return (
        _bounds_are_valid(inner)
        and _bounds_are_valid(outer)
        and outer["minX"] <= inner["minX"] <= inner["maxX"] <= outer["maxX"]
        and outer["minZ"] <= inner["minZ"] <= inner["maxZ"] <= outer["maxZ"]
    )


def _position_xz(value: Any, *, spawn: bool = False) -> tuple[float, float] | None:
    if not isinstance(value, list):
        return None
    if spawn and len(value) >= 3:
        return (float(value[0]), float(value[2]))
    if len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


class MotherShipDefinitionHarnessTests(unittest.TestCase):
    """Patch R: direct project JSON validation for the mother-ship architecture line."""

    def test_project_mother_ship_definitions_are_internally_consistent(self) -> None:
        problems: list[str] = []
        for project_path in PROJECT_PATHS:
            project = _load_json(project_path)
            scenes = project.get("scenes", [])
            interiors = []
            for scene in scenes:
                interior = (
                    ((scene.get("metadata") or {}).get("shuttle3d") or {}).get("motherShipInterior")
                    if isinstance(scene, dict)
                    else None
                )
                if isinstance(interior, dict):
                    interiors.append((str(scene.get("id") or "<scene>"), interior))
            if not interiors:
                problems.append(f"{project_path.relative_to(ROOT)}: missing metadata.shuttle3d.motherShipInterior")
                continue
            for scene_id, interior in interiors:
                problems.extend(
                    self._validate_interior(
                        project_name=project_path.relative_to(ROOT).as_posix(),
                        scene_id=scene_id,
                        interior=interior,
                    )
                )
        self.assertFalse(problems, "\n".join(problems))

    def _validate_interior(self, *, project_name: str, scene_id: str, interior: dict[str, Any]) -> list[str]:
        label = f"{project_name}:{scene_id}"
        problems: list[str] = []

        rooms = interior.get("rooms", [])
        exits = interior.get("exits", [])
        movement = interior.get("movement", {})
        movement_bounds = movement.get("bounds") if isinstance(movement, dict) else None
        objectives = interior.get("objectives", {})
        doors = interior.get("doors", {})
        terminals = interior.get("terminals", {})
        interactables = interior.get("interactables", [])
        interactions = interior.get("interactions", {})
        props = interior.get("props", [])

        if interior.get("schema") != MOTHER_SHIP_INTERIOR_SCHEMA:
            problems.append(f"{label}: motherShipInterior.schema must be {MOTHER_SHIP_INTERIOR_SCHEMA}")
        if interior.get("definitionVersion") != MOTHER_SHIP_INTERIOR_DEFINITION_VERSION:
            problems.append(f"{label}: motherShipInterior.definitionVersion must be {MOTHER_SHIP_INTERIOR_DEFINITION_VERSION}")
        if interior.get("stateVersion") != MOTHER_SHIP_INTERIOR_STATE_VERSION:
            problems.append(f"{label}: motherShipInterior.stateVersion must be {MOTHER_SHIP_INTERIOR_STATE_VERSION}")
        validation = interior.get("validation", {})
        if not isinstance(validation, dict) or validation.get("requireDefinitionVersion") is not True:
            problems.append(f"{label}: validation.requireDefinitionVersion must be true")

        if not isinstance(rooms, list) or not rooms:
            return [f"{label}: motherShipInterior.rooms must be a non-empty list"]
        if not isinstance(exits, list):
            problems.append(f"{label}: exits must be a list")
            exits = []
        if not isinstance(objectives, dict):
            problems.append(f"{label}: objectives must be a map")
            objectives = {}
        if not isinstance(doors, dict):
            problems.append(f"{label}: doors must be a map")
            doors = {}
        if not isinstance(terminals, dict):
            problems.append(f"{label}: terminals must be a map")
            terminals = {}
        if not isinstance(interactables, list):
            problems.append(f"{label}: interactables must be a list")
            interactables = []
        if not isinstance(interactions, dict):
            problems.append(f"{label}: interactions must be a map")
            interactions = {}
        if not isinstance(props, list):
            problems.append(f"{label}: props must be a list")
            props = []

        room_by_id: dict[str, dict[str, Any]] = {}
        for index, room in enumerate(rooms):
            room_id = str(room.get("id") or "").strip() if isinstance(room, dict) else ""
            if not room_id:
                problems.append(f"{label}: rooms[{index}] is missing id")
                continue
            if room_id in room_by_id:
                problems.append(f"{label}: duplicate room id {room_id}")
            room_by_id[room_id] = room
            if not _bounds_are_valid(room.get("bounds")):
                problems.append(f"{label}: room {room_id} has invalid bounds")
            elif _bounds_are_valid(movement_bounds) and not _bounds_inside_bounds(room["bounds"], movement_bounds):
                problems.append(f"{label}: room {room_id} is outside movement bounds")

        if not _bounds_are_valid(movement_bounds):
            problems.append(f"{label}: movement.bounds is invalid")

        def rooms_for_ref(ref: Any) -> list[dict[str, Any]]:
            wanted = str(ref or "").strip()
            if not wanted:
                return []
            return [
                room
                for room in rooms
                if isinstance(room, dict)
                and (str(room.get("id") or "") == wanted or str(room.get("location") or "") == wanted)
            ]

        def room_ids_for_ref(ref: Any) -> set[str]:
            return {str(room.get("id")) for room in rooms_for_ref(ref)}

        def point_inside_declared_room(ref: Any, position: Any, *, spawn: bool = False) -> bool:
            xz = _position_xz(position, spawn=spawn)
            if xz is None:
                return False
            x, z = xz
            return any(_point_inside_bounds(x, z, room.get("bounds")) for room in rooms_for_ref(ref))

        def point_inside_movement(position: Any, *, spawn: bool = False) -> bool:
            xz = _position_xz(position, spawn=spawn)
            if xz is None:
                return False
            return _point_inside_bounds(xz[0], xz[1], movement_bounds)

        # Reachability graph: a location alias may intentionally resolve to more than one room
        # (for example corridor.main and its trunk segment), so each exit connects all matches.
        graph = {room_id: set() for room_id in room_by_id}
        exit_ids: set[str] = set()
        for index, exit_def in enumerate(exits):
            exit_id = str(exit_def.get("id") or f"exit[{index}]")
            exit_ids.add(exit_id)
            from_rooms = room_ids_for_ref(exit_def.get("from"))
            to_rooms = room_ids_for_ref(exit_def.get("to"))
            if not from_rooms:
                problems.append(f"{label}: {exit_id} starts at missing room/location {exit_def.get('from')}")
            if not to_rooms:
                problems.append(f"{label}: {exit_id} ends at missing room/location {exit_def.get('to')}")
            for source in from_rooms:
                for target in to_rooms:
                    graph[source].add(target)
                    graph[target].add(source)
            if exit_def.get("door") and exit_def["door"] not in doors:
                problems.append(f"{label}: {exit_id} references missing door {exit_def['door']}")
            if not _bounds_are_valid(exit_def.get("bounds")):
                problems.append(f"{label}: {exit_id} has invalid bounds")

        start_rooms = room_ids_for_ref(interior.get("initialLocation"))
        if not start_rooms:
            problems.append(f"{label}: initialLocation {interior.get('initialLocation')} does not resolve to a room")
        else:
            reached = set(start_rooms)
            queue = deque(start_rooms)
            while queue:
                current = queue.popleft()
                for next_room in graph[current]:
                    if next_room not in reached:
                        reached.add(next_room)
                        queue.append(next_room)
            missing = sorted(set(room_by_id) - reached)
            if missing:
                problems.append(f"{label}: rooms are not reachable from {interior.get('initialLocation')}: {missing}")
            if "bridge.deck" not in reached:
                problems.append(f"{label}: bridge.deck is not reachable from {interior.get('initialLocation')}")

        initial_objective = str(interior.get("initialObjective") or "").strip()
        if initial_objective and initial_objective not in objectives:
            problems.append(f"{label}: initialObjective {initial_objective} is missing")

        for objective_id, objective in objectives.items():
            location = objective.get("location") if isinstance(objective, dict) else None
            if location and not rooms_for_ref(location):
                problems.append(f"{label}: objective {objective_id} points at missing location {location}")

        for door_id, door in doors.items():
            if str(door.get("state") or "").lower() == "locked":
                problems.append(f"{label}: door {door_id} is locked; mother-ship doors must remain traversal-open")
            for side in ("from", "to"):
                if door.get(side) and not rooms_for_ref(door[side]):
                    problems.append(f"{label}: door {door_id} {side} references missing location {door[side]}")

        interactable_ids = {
            str(interactable.get("id") or "")
            for interactable in interactables
            if isinstance(interactable, dict)
        }
        prop_targets = {
            str(prop.get("target") or "")
            for prop in props
            if isinstance(prop, dict) and str(prop.get("target") or "").strip()
        }
        prop_ids = {
            str(prop.get("id") or "")
            for prop in props
            if isinstance(prop, dict)
        }

        for terminal_id, terminal in terminals.items():
            location = terminal.get("location") if isinstance(terminal, dict) else None
            if location and not rooms_for_ref(location):
                problems.append(f"{label}: terminal {terminal_id} points at missing location {location}")
            if terminal_id not in interactable_ids and terminal_id not in prop_targets and terminal_id not in prop_ids:
                problems.append(f"{label}: terminal {terminal_id} has no visible prop or hotspot")

        for interaction_id, interaction in interactions.items():
            handler = str(interaction.get("handler") or "").strip() if isinstance(interaction, dict) else ""
            if not handler:
                problems.append(f"{label}: interaction {interaction_id} has no handler")
            changes_state = _string_list(interaction.get("changesState") if isinstance(interaction, dict) else None)
            if not changes_state:
                problems.append(f"{label}: interaction {interaction_id} declares no changesState expectations")
            success_status = str(
                (interaction.get("successStatus") or interaction.get("status") or "")
                if isinstance(interaction, dict)
                else ""
            ).strip()
            if not success_status:
                problems.append(f"{label}: interaction {interaction_id} declares no successStatus")
            for objective_id in _string_list(interaction.get("nextObjective") if isinstance(interaction, dict) else None):
                if objective_id not in objectives:
                    problems.append(f"{label}: interaction {interaction_id} nextObjective references missing {objective_id}")

        for interactable in interactables:
            interactable_id = str(interactable.get("id") or "interactable")
            location = interactable.get("location")
            position = interactable.get("position")
            if not rooms_for_ref(location):
                problems.append(f"{label}: interactable {interactable_id} points at missing location {location}")
            elif not point_inside_declared_room(location, position):
                problems.append(f"{label}: interactable {interactable_id} is outside its declared room/location bounds")
            if not point_inside_movement(position):
                problems.append(f"{label}: interactable {interactable_id} is outside playable movement bounds")
            if not str(interactable.get("prompt") or "").strip():
                problems.append(f"{label}: interactable {interactable_id} has no prompt")
            action = str(interactable.get("action") or "").strip()
            if not action:
                problems.append(f"{label}: interactable {interactable_id} has no action id")
            elif action not in interactions:
                problems.append(f"{label}: interactable {interactable_id} references missing interaction {action}")
            elif not str(interactions[action].get("handler") or "").strip():
                problems.append(f"{label}: interactable {interactable_id} action {action} has no handler")
            if not isinstance(interactable.get("range"), (int, float)) or interactable["range"] <= 0:
                problems.append(f"{label}: interactable {interactable_id} has invalid range")

        known_targets = (
            set(room_by_id)
            | {str(room.get("location")) for room in rooms if isinstance(room, dict) and room.get("location")}
            | set(terminals)
            | set(doors)
            | set(objectives)
            | interactable_ids
            | SUPPORTED_SYSTEM_TARGETS
        )
        viewscreens = []
        for prop in props:
            prop_id = str(prop.get("id") or "prop")
            room_ref = prop.get("room") or prop.get("location")
            position = prop.get("position")
            if not rooms_for_ref(room_ref):
                problems.append(f"{label}: prop {prop_id} points at missing room {room_ref}")
            elif not point_inside_declared_room(room_ref, position):
                problems.append(f"{label}: prop {prop_id} is outside its declared room/location bounds")
            if not point_inside_movement(position):
                problems.append(f"{label}: prop {prop_id} is outside playable movement bounds")
            if not str(prop.get("kind") or "").strip():
                problems.append(f"{label}: prop {prop_id} is missing render kind")
            target = str(prop.get("target") or "").strip()
            if target and target not in known_targets:
                problems.append(f"{label}: prop {prop_id} targets missing content {target}")
            display = str(prop.get("display") or "").strip()
            if display:
                if display not in SUPPORTED_DISPLAY_PROGRAMS:
                    problems.append(f"{label}: prop {prop_id} references unsupported display {display}")
                viewscreens.append(prop)
                if display == "enemyShipTactical" and target != "enemyShip":
                    problems.append(f"{label}: prop {prop_id} enemyShipTactical display must target enemyShip")
                if display == "systemPlanet" and target != "currentSystemPlanet":
                    problems.append(f"{label}: prop {prop_id} systemPlanet display must target currentSystemPlanet")

        bridge_viewscreen = [
            prop
            for prop in viewscreens
            if prop.get("room") == "bridge.deck"
            and prop.get("kind") == "viewscreen"
            and prop.get("display") == "systemPlanet"
        ]
        if not bridge_viewscreen:
            problems.append(f"{label}: bridge.deck has no data-defined systemPlanet viewscreen")

        for spawn_id, spawn in (interior.get("spawns") or {}).items():
            room_ref = spawn.get("room") or spawn.get("location")
            position = spawn.get("position")
            if not rooms_for_ref(room_ref):
                problems.append(f"{label}: spawn {spawn_id} points at missing room {room_ref}")
            elif not point_inside_declared_room(room_ref, position, spawn=True):
                problems.append(f"{label}: spawn {spawn_id} is outside its declared room/location bounds")
            if not point_inside_movement(position, spawn=True):
                problems.append(f"{label}: spawn {spawn_id} is outside playable movement bounds")

        if "fireBridgeTacticalConsole" not in interactions:
            problems.append(f"{label}: missing fireBridgeTacticalConsole interaction")
        if "trackEnemyShipOnViewscreen" not in interactions:
            problems.append(f"{label}: missing trackEnemyShipOnViewscreen interaction")

        return problems
