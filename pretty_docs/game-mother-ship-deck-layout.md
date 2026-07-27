# Mother Ship Deck Layout

Contract: `game.mother-ship-deck-layout.v1`

This document maps the first explorable part of the mother ship. It is a design source for later renderer and project metadata changes.

## Coordinate intent

The current game uses a compact first-person WebGL scene with hand-authored geometry, movement bounds, and colliders. The mother ship should begin as one connected deck using the same approach.

Use a simple local deck coordinate system for planning:

```text
X axis: port / starboard
Z axis: aft / forward
Y axis: vertical height
```

The shuttle bay sits aft. The bridge sits forward. Engineering is deeper and lower in feel, even if the first implementation remains on a single flat deck.

## First-deck overview

```text
                    [ BRIDGE - locked first ]
                           |
                    [ Command Access Door ]
                           |
 [ Medbay ] -- [ Main Corridor Hub ] -- [ Science / Ops Lab ]
                           |
                    [ Security Checkpoint ]
                           |
                    [ Shuttle Bay Inner Door ]
                           |
                    [ Mother Ship Shuttle Bay ]
                           |
                    [ Docked Shuttle ]
```

## Zone list

| Zone ID | Display name | First pass status | Purpose |
| --- | --- | --- | --- |
| `bay.shuttle` | Mother Ship Shuttle Bay | Build first | Arrival, docked shuttle, movement proof |
| `bay.ops` | Bay Operations Alcove | Build first | First terminal and door override |
| `corridor.security` | Security Checkpoint | Build first | Transition from bay to ship |
| `corridor.hub` | Main Corridor Hub | Build first | Navigation branch point |
| `engineering.access` | Engineering Access | Build first | Power objective |
| `medbay.entry` | Medbay Entrance | Visible stub | Health/status destination |
| `science.entry` | Science/Ops Lab Entrance | Visible stub | Future systems destination |
| `bridge.entry` | Command Access Door | Visible stub | Long-term objective gate |
| `bridge` | Bridge | Later | Command and mission control |
| `engineering.core` | Engineering Core | Later | Power, hazards, repair loop |
| `cargo` | Cargo Hold | Later | Optional exploration and supplies |
| `crew` | Crew Quarters | Later | Narrative/environmental detail |
| `brig` | Brig / Security | Later | Security mechanics and containment |

## First playable spaces

### Mother Ship Shuttle Bay

The shuttle bay should be the largest initial room. It should include:
- the docked shuttle as a visible static landmark;
- a lowered ramp or disembark path;
- bay floor stripes that lead to the inner door;
- a Bay Operations terminal near the exit;
- readable status lights above the inner door.

Suggested gameplay:
- player spawns clear of the shuttle ramp, facing the inner bay door;
- the docked shuttle remains behind the player;
- the room is safe and combat-paused;
- the HUD shows `Mother Ship Shuttle Bay`.

### Bay Operations Alcove

This can be part of the shuttle bay at first rather than a separate room. It provides the first post-docking interaction.

Props:
- `bay-ops-terminal`
- `bay-pressure-status`
- `bay-inner-door-control`
- optional wall label: `BAY OPS`

Terminal behavior:
- if security is in quarantine, the terminal explains the lock;
- once used, it can unlock the inner bay door for the first slice.

### Security Checkpoint

A small threshold room between the bay and the main corridor.

Props:
- security arch;
- wall scanner;
- caution stripes;
- one locked side panel for future security mechanics.

Gameplay:
- first pass can be purely visual;
- later it can scan the player, warn about boarders, or enable shipboard combat.

### Main Corridor Hub

The corridor hub teaches that the ship is larger.

Required landmarks:
- forward sign: `BRIDGE`;
- left sign: `MEDBAY`;
- right sign: `SCIENCE / OPS`;
- aft sign: `SHUTTLE BAY`;
- lower or side sign: `ENGINEERING`.

First pass should make Engineering Access reachable and make the bridge door visible but locked.

### Engineering Access

This is a small control vestibule, not the full engineering core yet.

Props:
- `engineering-power-console`;
- a flickering power conduit;
- a door or hatch labeled `ENGINEERING CORE`;
- light strips that change after partial power is restored.

Gameplay:
- player interacts with the power console;
- ship power changes from `emergency` to `partial`;
- HUD/objective updates;
- bridge remains locked until later milestones.

## Door plan

| Door ID | Connects | Initial state | Unlock condition |
| --- | --- | --- | --- |
| `door.bay-inner` | Shuttle Bay → Security Checkpoint | locked | use Bay Operations terminal |
| `door.security-hub` | Security Checkpoint → Main Corridor Hub | open | none after bay door opens |
| `door.engineering-access` | Hub → Engineering Access | open or weakly locked | optional terminal prompt |
| `door.medbay` | Hub → Medbay | locked visible stub | future health objective |
| `door.science` | Hub → Science/Ops Lab | locked visible stub | future science objective |
| `door.bridge` | Hub → Bridge | locked | future command access objective |

## Collision and movement expectations

### Shuttle Bay

The player must not collide with the invisible cutscene path. Once bay player control starts, movement bounds should describe the gameplay room, not the cinematic camera volume.

Suggested bay bounds:

```javascript
{
  minX: -5.5,
  maxX: 5.5,
  minZ: -5.5,
  maxZ: 6.5
}
```

Suggested colliders:
- docked shuttle hull;
- left bay wall equipment;
- right bay cargo rack;
- rear bay barrier behind shuttle.

### Corridor Hub

Use narrower bounds and simple door rectangles. Avoid maze geometry until basic interaction is reliable.

Suggested corridor widths:
- main corridor width: 2.2 to 2.8 world units;
- door depth: 0.4 to 0.8 world units;
- room height feel: 2.4 to 3.2 world units.

## Signage

Every branch should have text that is both visible and duplicated in the HUD when the player hovers it.

Required signs:
- `SHUTTLE BAY`
- `BAY OPS`
- `MAIN CORRIDOR`
- `ENGINEERING`
- `MEDBAY`
- `SCIENCE / OPS`
- `BRIDGE`

## Lighting progression

| Ship state | Lighting |
| --- | --- |
| `emergency` | low blue base, amber warnings, occasional flicker |
| `partial` | stronger cyan corridor strips, fewer amber warnings |
| `nominal` | stable white/cyan panels with status displays |

The first slice only needs `emergency` and `partial`.

## Area transition rules

Initial implementation can use one continuous scene and simply update `shipLocation` based on the camera position. Later, area modules can stream or rebuild geometry as the player moves.

Position-based transition is acceptable for the first pass:

```text
camera inside shuttle bay bounds → Mother Ship Shuttle Bay
camera near bay door threshold → Security Checkpoint
camera inside hub corridor bounds → Main Corridor Hub
camera inside engineering vestibule → Engineering Access
```

## Future deck expansion

After the first deck works, add:
- turbolift access;
- upper observation deck;
- lower engineering core;
- cargo/service tunnels;
- crew deck;
- bridge interior;
- external viewport set pieces.

Do not add these before first-person movement, doors, and terminals are reliable in the first deck.
