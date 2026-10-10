"""Offline invariants for the Captain Jacket Matrix seed-corpus builder."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import nanojev_captain_jacket_database_smoke as gen
from nanojev_captain_jacket_store import CaptainJacketStore, DIMENSIONS


class CJMDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="cjm-tests-")
        cls.output = Path(cls.tmp.name) / "set1"
        cls.result = gen.generate(output_dir=cls.output, jacket_count=24, variants=3, seed=1701)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_source_is_live_deterministic_replay(self):
        stages = gen.get_authoritative_stages()
        self.assertEqual(7, len(stages))
        self.assertEqual(4, sum(s["source"] == "authoritative-deterministic-encounter" for s in stages))
        self.assertEqual(3, sum(s["source"] == "constructed-temporal-commitment-state" for s in stages))
        self.assertEqual({"deploying", "deployed", "recalling", "abandoning", "idle", "recovered", "abandoned"},
                         {s["observation"]["boarding"]["phase"] for s in stages})

    def test_matrix_dimensions_and_values(self):
        self.assertEqual(8, len(DIMENSIONS))
        with CaptainJacketStore(self.output / "captain-jackets.sqlite") as db:
            self.assertEqual(24, db.stats()["jackets"])
            for row in db.conn.execute("SELECT traits_json FROM jackets"):
                traits = json.loads(row[0]); self.assertEqual(set(traits), {d[0] for d in DIMENSIONS})
                self.assertTrue(all(0 <= x <= 1 for x in traits.values()))

    def test_valid_actions_in_all_states(self):
        for stage in gen.get_authoritative_stages():
            state = stage["observation"]["boarding"]["phase"]
            allowed = gen.legal_actions(stage["observation"])
            self.assertTrue({"approach", "hold-inner", "hold-outer", "coast"}.issubset(set(allowed)))
            if state in ("deploying", "deployed", "recalling", "abandoning"):
                self.assertNotIn("withdraw", allowed)
            if state in ("deploying", "recalling", "abandoning"):
                self.assertFalse(set(allowed) & {"initiate", "recall", "abandon"})
            if state == "deployed":
                self.assertIn("recall", allowed); self.assertIn("abandon", allowed)

    def test_boarding_not_possible_outside_range_or_at_high_speed(self):
        obs = next(s["observation"] for s in gen.get_authoritative_stages()
                   if s["observation"]["boarding"]["phase"] == "idle")
        outer = gen.counterfactual(obs, 2)
        self.assertGreater(outer["rangeM"], outer["transporterMaxRangeM"])
        self.assertNotIn("initiate", gen.legal_actions(outer))
        inner = gen.counterfactual(obs, 3)
        self.assertIn("initiate", gen.legal_actions(inner))
        outer["boarding"]["phase"] = "deployed"
        self.assertNotIn("recall", gen.legal_actions(outer))
        self.assertIn("abandon", gen.legal_actions(outer))

    def test_provenance_and_provisional_gold(self):
        with closing(sqlite3.connect(self.output / "captain-jackets.sqlite")) as db:
            kinds = {r[0] for r in db.execute("SELECT DISTINCT source_kind FROM scenes")}
            self.assertIn("authoritative-deterministic-encounter", kinds)
            self.assertIn("constructed-temporal-commitment-state", kinds)
            self.assertTrue(any(k.startswith("counterfactual-derived-") for k in kinds))
            self.assertEqual({gen.TEACHER}, {r[0] for r in db.execute("SELECT DISTINCT teacher FROM preferences")})
            self.assertGreater(db.execute("SELECT COUNT(*) FROM preferences WHERE status='ambiguous'").fetchone()[0], 0)
            self.assertFalse(self.result["labelsAreMeasuredOutcomes"])

    def test_splits_disjoint_on_jackets_and_scenes(self):
        with closing(sqlite3.connect(self.output / "captain-jackets.sqlite")) as db:
            bad = db.execute("""SELECT COUNT(*) FROM cases c
                              JOIN jackets j USING (jacket_id) JOIN scenes s USING (scene_id)
                              WHERE c.split<>j.split OR c.split<>s.split""").fetchone()[0]
            self.assertEqual(0, bad)
            self.assertEqual({"train", "dev", "test"},
                             {r[0] for r in db.execute("SELECT DISTINCT split FROM cases")})
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())

    def test_split_reservations_fail_closed(self):
        with CaptainJacketStore(self.output / "captain-jackets.sqlite") as db:
            self.assertEqual(db.stats()["preference_reserve"], db.stats()["labeled_preferences"])
            train = db.conn.execute("SELECT preference_id FROM preference_reserve WHERE split='train' LIMIT 1").fetchone()[0]
            test = db.conn.execute("SELECT preference_id FROM preference_reserve WHERE split='test' LIMIT 1").fetchone()[0]
            with self.assertRaises(ValueError):
                db.claim_preference(train, "eval")
            with self.assertRaises(ValueError):
                db.claim_preference(test, "train")
            with self.assertRaises(KeyError):
                db.claim_preference("not-a-preference", "train")
            db.claim_preference(train, "train")
            db.claim_preference(test, "eval")
            self.assertEqual(1, db.conn.execute("SELECT train_count FROM preference_reserve WHERE preference_id=?", (train,)).fetchone()[0])
            self.assertEqual(1, db.conn.execute("SELECT eval_count FROM preference_reserve WHERE preference_id=?", (test,)).fetchone()[0])

    def test_rom_raw_examples_aligned_and_position_balanced(self):
        for split in ("train", "dev", "test"):
            rom = [json.loads(t) for t in (self.output/f"rom-{split}.jsonl").read_text().splitlines()]
            raw = [json.loads(t) for t in (self.output/f"raw-{split}.jsonl").read_text().splitlines()]
            self.assertEqual(len(rom), len(raw)); self.assertGreater(len(rom), 0)
            self.assertEqual(len(rom)//2, sum(r["completion"] == "A" for r in raw))
            self.assertEqual(len(rom)//2, sum(r["completion"] == "B" for r in raw))
            for s, t in zip(rom, raw):
                self.assertEqual(s["sampleId"], t["sampleId"])
                self.assertEqual(s["groupId"], t["groupId"])
                self.assertEqual(s["output"]["choice"], t["completion"])
                self.assertEqual(s["output"]["actionId"], t["targetActionId"])
                self.assertEqual("provisional-utility-not-simulated-outcome", s["output"]["teacherEvidence"]["evidenceKind"])
                self.assertEqual(8, len(s["output"]["teacherEvidence"]["optionA"]["features"]))
            self.assertEqual(2, len([x for x in raw if x["groupId"] == raw[0]["groupId"]]))

    def test_malformed_observation_fails_fast(self):
        o = gen.get_authoritative_stages()[0]["observation"]
        for mutate in (
            lambda z: z.pop("transporterMaxRangeM"),
            lambda z: z.update(rangeM=20000),
            lambda z: z.update(relativeSpeedMps=float("nan")),
            lambda z: z["boarding"].update(phase="teleported"),
        ):
            import copy
            corrupt = copy.deepcopy(o); mutate(corrupt)
            with self.assertRaises(RuntimeError):
                gen.validate_observation(corrupt)

    def test_utility_changes_with_jacket(self):
        stages = gen.get_authoritative_stages()
        obs = next(s["observation"] for s in stages if s["observation"]["boarding"]["phase"] == "deployed")
        recall = gen.utility_features(obs, "recall")
        abandon = gen.utility_features(obs, "abandon")
        low = {d[0]: .5 for d in DIMENSIONS}; low["crew_loyalty"] = 0
        high = dict(low); high["crew_loyalty"] = 1
        diff_low = gen.utility(low,recall) - gen.utility(low,abandon)
        diff_high = gen.utility(high,recall) - gen.utility(high,abandon)
        self.assertGreater(diff_high, diff_low + 5)

    def test_rebuild_same_jsonl_hashes_and_no_implicit_overwrite(self):
        with self.assertRaises(FileExistsError):
            gen.generate(output_dir=self.output, jacket_count=24, variants=3, seed=1701)
        other = Path(self.tmp.name) / "set2"
        again = gen.generate(output_dir=other, jacket_count=24, variants=3, seed=1701)
        self.assertEqual(self.result["jsonlSha256"], again["jsonlSha256"])
        for name in self.result["jsonlSha256"]:
            self.assertEqual((self.output/name).read_bytes(), (other/name).read_bytes())


if __name__ == "__main__":
    unittest.main()
