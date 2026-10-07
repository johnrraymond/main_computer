from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


GAME_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECT = GAME_ROOT / "project.json"
RUNTIME = GAME_ROOT / "web" / "scripts" / "space-gravity-runtime.js"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the gravity-first local-space runtime.")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--system", default="system.solace-reach")
    parser.add_argument("--physics-seconds", type=float, default=86400.0)
    args = parser.parse_args()

    node = shutil.which("node")
    if not node:
        print(json.dumps({"ok": False, "error": "node executable not found"}, indent=2))
        return 2

    project = args.project.resolve()
    if not project.exists():
        print(json.dumps({"ok": False, "error": f"project not found: {project}"}, indent=2))
        return 2

    js = r"""
const fs = require('fs');
const runtimeApi = require(process.argv[1]);
const project = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const systemId = process.argv[3];
const physicsSeconds = Number(process.argv[4]);
const runtime = runtimeApi.create(project.metadata.spacePhysics, {projectId: project.id});
if (!runtime.hasSystem(systemId)) throw new Error(`physical system unavailable: ${systemId}`);
runtime.setActiveSystem(systemId);
const before = runtime.snapshot();
function byId(snapshot, id) {
  const body = snapshot.bodies.find((candidate) => candidate.id === id);
  if (!body) throw new Error(`missing smoke body ${id}`);
  return body;
}
function distance(a, b) {
  return Math.hypot(
    a.positionM[0] - b.positionM[0],
    a.positionM[1] - b.positionM[1],
    a.positionM[2] - b.positionM[2]
  );
}
const initialShipRadiusM = distance(byId(before, 'ship.mother'), byId(before, 'planet.haven'));
const initialBinarySeparationM = distance(byId(before, 'star.solace-reach-a'), byId(before, 'star.solace-reach-b'));
runtime.advancePhysicsSeconds(physicsSeconds);
const after = runtime.snapshot();
const finalShipRadiusM = distance(byId(after, 'ship.mother'), byId(after, 'planet.haven'));
const finalBinarySeparationM = distance(byId(after, 'star.solace-reach-a'), byId(after, 'star.solace-reach-b'));
const finalMoonRadiusM = distance(byId(after, 'moon.haven-i'), byId(after, 'planet.haven'));
const energy0 = before.diagnostics.totalEnergyJ;
const energy1 = after.diagnostics.totalEnergyJ;
const energyRelativeDrift = Math.abs((energy1 - energy0) / energy0);
const momentum0 = Math.hypot(...before.diagnostics.momentumKgMps);
const momentumDelta = Math.hypot(
  after.diagnostics.momentumKgMps[0] - before.diagnostics.momentumKgMps[0],
  after.diagnostics.momentumKgMps[1] - before.diagnostics.momentumKgMps[1],
  after.diagnostics.momentumKgMps[2] - before.diagnostics.momentumKgMps[2]
);
const momentumRelativeDrift = momentum0 > 0 ? momentumDelta / momentum0 : momentumDelta;
const shipRadiusRelativeDrift = Math.abs(finalShipRadiusM - initialShipRadiusM) / initialShipRadiusM;
const binarySeparationRelativeDrift = Math.abs(finalBinarySeparationM - initialBinarySeparationM) / initialBinarySeparationM;
const mother = byId(after, 'ship.mother');
const checks = {
  validation: before.validation.ok === true,
  bodyCount: before.bodyCount === 9,
  shipPassive: mother.massKg === 0,
  energyConserved: energyRelativeDrift < 1e-8,
  momentumConserved: momentumRelativeDrift < 1e-8,
  binaryStable: binarySeparationRelativeDrift < 1e-3,
  shipOrbitBound: shipRadiusRelativeDrift < 0.01,
  moonOrbitBound: finalMoonRadiusM > 3.5e8 && finalMoonRadiusM < 4.2e8
};
const ok = Object.values(checks).every(Boolean);
console.log(JSON.stringify({
  ok,
  systemId,
  physicsSeconds,
  fixedStepSeconds: runtime.fixedStepSeconds,
  timeScale: runtime.timeScale,
  bodyCount: before.bodyCount,
  checks,
  metrics: {
    energyRelativeDrift,
    momentumRelativeDrift,
    initialBinarySeparationM,
    finalBinarySeparationM,
    binarySeparationRelativeDrift,
    initialShipRadiusM,
    finalShipRadiusM,
    shipRadiusRelativeDrift,
    finalMoonRadiusM
  }
}));
process.exitCode = ok ? 0 : 1;
"""
    result = subprocess.run(
        [node, "-e", js, str(RUNTIME.resolve()), str(project), args.system, str(args.physics_seconds)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        print(json.dumps({"ok": False, "error": result.stderr.strip() or result.stdout.strip()}, indent=2))
        return result.returncode
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": result.stderr.strip() or result.stdout.strip()}, indent=2))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
