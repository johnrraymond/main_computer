#!/usr/bin/env python3
"""Persistent, versioned Captain Jacket Matrix (CJM) corpus store.

This is a *data* boundary, not a CLEF trainer.  Gold labels in v1 are
provisional, deterministic preference-oracle labels, not model judgments or
measured counterfactual combat outcomes.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

SCHEMA_VERSION = "nanojev-captain-jacket-store-v1"
DIMENSIONS = (
    ("mission_commitment", "survival before objective", "objective before survival"),
    ("ship_preservation", "accept loss of ship", "preserve ship"),
    ("crew_loyalty", "crew expendable", "crew protected"),
    ("risk_tolerance", "avoid uncertain gambles", "accept uncertain gambles"),
    ("initiative", "wait and react", "seize opportunities"),
    ("patience", "favor immediate outcomes", "invest time for future advantage"),
    ("adaptability", "maintain original course", "revise plans on new evidence"),
    ("evidence_discipline", "act on weak information", "demand strong evidence"),
)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class CaptainJacketStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dimensions(
                dimension_id TEXT PRIMARY KEY,
                low_anchor TEXT NOT NULL, high_anchor TEXT NOT NULL,
                min_value REAL NOT NULL DEFAULT 0, max_value REAL NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS jackets(
                jacket_id TEXT PRIMARY KEY, label TEXT NOT NULL,
                traits_json TEXT NOT NULL, split TEXT NOT NULL
                    CHECK(split IN ('train','dev','test')));
            CREATE TABLE IF NOT EXISTS missions(
                mission_id TEXT PRIMARY KEY, objective TEXT NOT NULL, source TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scenes(
                scene_id TEXT PRIMARY KEY, family_id TEXT NOT NULL,
                split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
                source_kind TEXT NOT NULL, source_stage TEXT NOT NULL,
                observation_json TEXT NOT NULL, observation_sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS actions(
                action_id TEXT PRIMARY KEY, action_type TEXT NOT NULL,
                action_text TEXT NOT NULL, duration_seconds REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS cases(
                case_id TEXT PRIMARY KEY,
                jacket_id TEXT NOT NULL REFERENCES jackets(jacket_id),
                scene_id TEXT NOT NULL REFERENCES scenes(scene_id),
                mission_id TEXT NOT NULL REFERENCES missions(mission_id),
                split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
                UNIQUE(jacket_id,scene_id,mission_id));
            CREATE TABLE IF NOT EXISTS case_actions(
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                action_id TEXT NOT NULL REFERENCES actions(action_id),
                utility REAL NOT NULL, features_json TEXT NOT NULL,
                PRIMARY KEY(case_id,action_id));
            CREATE TABLE IF NOT EXISTS preferences(
                preference_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                left_action TEXT NOT NULL REFERENCES actions(action_id),
                right_action TEXT NOT NULL REFERENCES actions(action_id),
                left_utility REAL NOT NULL, right_utility REAL NOT NULL,
                preferred_action TEXT REFERENCES actions(action_id),
                status TEXT NOT NULL CHECK(status IN ('labeled','ambiguous')),
                teacher TEXT NOT NULL, label_margin REAL NOT NULL,
                CHECK(left_action<>right_action));
            CREATE TABLE IF NOT EXISTS preference_reserve(
                preference_id TEXT PRIMARY KEY REFERENCES preferences(preference_id),
                split TEXT NOT NULL CHECK(split IN ('train','dev','test')),
                train_count INTEGER NOT NULL DEFAULT 0 CHECK(train_count>=0),
                eval_count INTEGER NOT NULL DEFAULT 0 CHECK(eval_count>=0),
                CHECK((split='train' AND eval_count=0) OR (split<>'train' AND train_count=0)));
            CREATE INDEX IF NOT EXISTS idx_preference_reserve_split ON preference_reserve(split,train_count,eval_count);
            CREATE INDEX IF NOT EXISTS idx_cases_split ON cases(split);
            CREATE INDEX IF NOT EXISTS idx_scenes_split ON scenes(split);
            CREATE INDEX IF NOT EXISTS idx_preferences_case ON preferences(case_id);
        """)
        existing = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if existing is not None and existing[0] != SCHEMA_VERSION:
            raise RuntimeError(f"CJM_SCHEMA_MISMATCH: {existing[0]}")
        if existing is None:
            self.conn.execute("INSERT INTO meta VALUES('schema_version', ?)", (SCHEMA_VERSION,))
            self.conn.executemany("INSERT INTO dimensions(dimension_id,low_anchor,high_anchor) VALUES(?,?,?)", DIMENSIONS)
            self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def add(self, table: str, columns: tuple[str, ...], values: tuple):
        if table not in {"meta", "jackets", "missions", "scenes", "actions", "cases", "case_actions", "preferences", "preference_reserve"}:
            raise ValueError("invalid table")
        if not all(c.replace('_', '').isalnum() for c in columns):
            raise ValueError("invalid column")
        self.conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)

    def stats(self) -> dict[str, Any]:
        counts = {table: int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                  for table in ("dimensions", "jackets", "missions", "scenes", "actions", "cases", "case_actions", "preferences", "preference_reserve")}
        counts["labeled_preferences"] = int(self.conn.execute("SELECT COUNT(*) FROM preferences WHERE status='labeled'").fetchone()[0])
        counts["ambiguous_preferences"] = counts["preferences"] - counts["labeled_preferences"]
        counts["splits"] = {r[0]: r[1] for r in self.conn.execute("SELECT split, COUNT(*) FROM cases GROUP BY split")}
        return counts

    def claim_preference(self, preference_id: str, purpose: str) -> None:
        """Reserve a labeled example for a future trainer without holdout leakage.

        A training consumer may only mark train examples; dev/test evaluation
        consumers may only mark held-out examples.  A separate future trainer
        decides whether to cycle samples and when to advance the counters.
        """
        if purpose not in ("train", "eval"):
            raise ValueError("CJM_INVALID_CONSUMPTION_PURPOSE")
        row = self.conn.execute(
            "SELECT split FROM preference_reserve WHERE preference_id=?", (preference_id,)
        ).fetchone()
        if row is None:
            raise KeyError("CJM_PREFERENCE_NOT_RESERVED")
        if (purpose == "train") != (row["split"] == "train"):
            raise ValueError("CJM_SPLIT_RESERVE_VIOLATION")
        col = "train_count" if purpose == "train" else "eval_count"
        with self.conn:
            self.conn.execute(
                f"UPDATE preference_reserve SET {col}={col}+1 WHERE preference_id=?",
                (preference_id,),
            )

    def export_records(self, split: str):
        """Identical canonical preferences expressed for ROM and raw text consumers.

        Emitting both orientations ensures no gold answer position correlation.
        Data records can later be converted to the trainer's own ObjectQuestion API.
        """
        if split not in ("train", "dev", "test"):
            raise ValueError(split)
        for r in self.conn.execute("""
            SELECT p.*, c.split, j.jacket_id, j.traits_json, m.objective,
                   s.observation_json, s.source_kind, s.observation_sha256
            FROM preferences p JOIN cases c ON p.case_id=c.case_id
            JOIN jackets j ON c.jacket_id=j.jacket_id
            JOIN scenes s ON c.scene_id=s.scene_id
            JOIN missions m ON c.mission_id=m.mission_id
            WHERE c.split=? AND p.status='labeled' ORDER BY p.preference_id
        """, (split,)):
            actions = {a["action_id"]: dict(a) for a in self.conn.execute("SELECT * FROM actions")}
            case_action_evidence = {e["action_id"]: dict(e)
                for e in self.conn.execute("SELECT * FROM case_actions WHERE case_id=?", (r["case_id"],))}
            jacket = json.loads(r["traits_json"])
            observation = json.loads(r["observation_json"])
            for orientation, a, b in (("AB", r["left_action"], r["right_action"]),
                                      ("BA", r["right_action"], r["left_action"])):
                gold = "A" if r["preferred_action"] == a else "B"
                action_a, action_b = actions[a], actions[b]
                common = {
                    "schema": "cjm.pairwise-example.v1", "sampleId": f"{r['preference_id']}:{orientation}",
                    "groupId": r["preference_id"], "split": split,
                    "jacket": {"id": r["jacket_id"], "traits": jacket},
                    "mission": r["objective"], "observation": observation,
                    "observationProvenance": r["source_kind"],
                    "observationSha256": r["observation_sha256"],
                    "options": {"A": {"id": a, "text": action_a["action_text"]},
                                "B": {"id": b, "text": action_b["action_text"]}},
                    "target": gold, "targetActionId": r["preferred_action"],
                    "teacher": r["teacher"], "labelStatus": "provisional",
                    "margin": r["label_margin"], "orientation": orientation,
                }
                # Structured input/output is fixed ROM material, *not* a model-generated ROM.
                rom = {"schema": "cjm.rom-supervision.v1", "sampleId": common["sampleId"],
                       "split": split, "input": {k: common[k] for k in (
                           "jacket", "mission", "observation", "options", "observationProvenance")},
                       "output": {"choice": gold, "actionId": r["preferred_action"],
                                  "teacherEvidence": {
                                    "optionA": {"utility": case_action_evidence[a]["utility"],
                                                "features": json.loads(case_action_evidence[a]["features_json"])},
                                    "optionB": {"utility": case_action_evidence[b]["utility"],
                                                "features": json.loads(case_action_evidence[b]["features_json"])},
                                    "absoluteMargin": r["label_margin"],
                                    "evidenceKind": "provisional-utility-not-simulated-outcome"}},
                       "teacher": r["teacher"], "groupId": r["preference_id"]}
                traits_text = ", ".join(f"{k}={v:.3f}" for k, v in jacket.items())
                obs = observation
                state = obs["boarding"]["phase"]
                prompt = (
                    "You are the captain. Choose the better action given this jacket and observed tactical situation.\n"
                    f"Jacket (0..1): {traits_text}\n"
                    f"Mission: {r['objective']}\n"
                    f"Time: {obs['simulationSeconds']:.2f}s; range: {obs['rangeM']:.2f}m; "
                    f"relative speed: {obs['relativeSpeedMps']:.2f}m/s; "
                    f"hull: {obs['targetHullPercent']:.1f}%; "
                    f"boarding: {state}; transporter range: {obs['transporterMaxRangeM']:.0f}m.\n"
                    f"A: {action_a['action_text']}\nB: {action_b['action_text']}\nAnswer A or B:")
                raw = {"schema": "cjm.raw-pairwise.v1", "sampleId": common["sampleId"],
                       "split": split, "prompt": prompt, "completion": gold,
                       "targetActionId": r["preferred_action"], "groupId": r["preference_id"],
                       "teacher": r["teacher"], "observationProvenance": r["source_kind"]}
                yield common, rom, raw
