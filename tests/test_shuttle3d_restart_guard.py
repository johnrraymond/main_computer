from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENE_VIEWER_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "scene-viewer.js"


class Shuttle3dRestartGuardTests(unittest.TestCase):
    def test_r_key_only_requests_restart_after_defeat(self) -> None:
        source = SCENE_VIEWER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'if (!event.repeat && shuttle?.gameOver) shuttle?.resetCombat?.();',
            source,
        )

    def test_reset_combat_is_a_no_op_until_game_over(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is required for the restart guard smoke")
        script = textwrap.dedent(
            f"""
            const fs = require('fs');
            const source = fs.readFileSync({json.dumps(str(SCENE_VIEWER_PATH))}, 'utf8');
            const start = source.indexOf('        resetCombat(nowMs = performance.now()) {{');
            const end = source.indexOf('\\n        bindGeometryBuffer(', start);
            if (start < 0 || end < 0) throw new Error('resetCombat method not found');
            const methodSource = source.slice(start, end).trim();
            const resetCombat = Function(`return ({{${{methodSource}}}}).resetCombat;`)();

            const forbidden = () => {{ throw new Error('healthy player was reset'); }};
            const healthy = {{
              gameOver: false,
              pilot: {{active: true}},
              setPilotMode: forbidden,
              clearMovementKeys: forbidden,
              resetFlightState: forbidden,
              emitCombatState: forbidden
            }};
            if (resetCombat.call(healthy, 1000) !== false) throw new Error('healthy restart did not return false');
            if (healthy.gameOver !== false) throw new Error('healthy state changed');

            const calls = [];
            const defeated = {{
              gameOver: true,
              pilot: {{active: true}},
              combat: {{
                player: {{startingHealth: 100}},
                transport: {{initialDelayMs: 1500}}
              }},
              playerHealth: 0,
              aliens: [{{id: 'alien'}}],
              transportSequence: 4,
              kills: 2,
              phaserBeam: {{}},
              lastPhaserShotAt: 999,
              combatClockMs: 999,
              nextTransportAtMs: 999,
              setPilotMode() {{ calls.push('pilot'); this.pilot.active = false; }},
              clearMovementKeys() {{ calls.push('movement'); }},
              resetFlightState() {{ calls.push('flight'); }},
              emitCombatState(force) {{ calls.push(`emit:${{force}}`); }}
            }};
            if (resetCombat.call(defeated, 2000) !== true) throw new Error('defeated restart did not return true');
            if (defeated.gameOver !== false) throw new Error('game over was not cleared');
            if (defeated.playerHealth !== 100) throw new Error('health was not restored');
            if (defeated.aliens.length !== 0) throw new Error('aliens were not cleared');
            if (defeated.nextTransportAtMs !== 2900) throw new Error('transport schedule was not reset');
            if (calls.join(',') !== 'pilot,movement,flight,emit:true') throw new Error(`unexpected reset calls: ${{calls.join(',')}}`);
            console.log('restart-guard-ok');
            """
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("restart-guard-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
