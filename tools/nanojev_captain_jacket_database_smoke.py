#!/usr/bin/env python3
"""Build an offline, reproducible Captain Jacket Matrix (CJM) SQLite corpus.

Sources: real deterministic first-encounter observations, plus *explicitly marked*
constructed/counterfactual variants.  Labels: provisional deterministic utility
teacher, never presented as measured game outcomes or trained model behavior.
Exports: paired, position-counterbalanced JSONL for structured ROM and raw LM.
No NanoJev, Docker, model downloads, or training are invoked.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

from nanojev_captain_jacket_store import CaptainJacketStore, DIMENSIONS, canonical, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SMOKE = ROOT / "game_projects/webgl-demo/tools/space_captain_boarding_commitment_smoke.py"
DEFAULT_OUTPUT = ROOT / "runtime/captain_jacket_matrix/v1"
DATA_SCHEMA = "cjm.dataset.v1"
TEACHER = "cjm-provisional-utility-oracle-v1"
MISSION = "pursue-main-ship-and-seek-boarding-range"
ACTIONS = (
    ("approach", "helm", "Close separation toward the target ship", 0),
    ("hold-inner", "helm", "Match target relative velocity, holding transporter maximum range minus 450 meters", 0),
    ("hold-outer", "helm", "Match target relative velocity, holding transporter maximum range minus 100 meters", 0),
    ("withdraw", "helm", "Withdraw and increase separation from the target ship", 0),
    ("coast", "helm", "Cease commanded thrust and retain momentum", 0),
    ("initiate", "boarding", "Initiate boarding: 30 seconds of deployment commitment", 30),
    ("recall", "boarding", "Recover deployed boarders: requires 24 seconds within range", 24),
    ("abandon", "boarding", "Abandon deployed boarders: resolution requires 12 seconds", 12),
)
ACTION_MAP = {a[0]: a for a in ACTIONS}
# These are illustrative seed jackets, not a psychometric taxonomy or validated policy.
JACKET_ARCHETYPES = (
    ("professional", (0.8, 0.8, 0.9, 0.4, 0.7, 0.7, 0.8, 0.8)),
    ("raider", (0.9, 0.3, 0.2, 0.9, 0.9, 0.3, 0.6, 0.2)),
    ("fanatic", (1.0, 0.1, 0.1, 1.0, 1.0, 0.5, 0.1, 0.1)),
    ("survivor", (0.3, 1.0, 0.7, 0.1, 0.3, 0.5, 0.8, 0.8)),
    ("guardian", (0.7, 0.7, 1.0, 0.3, 0.6, 0.9, 0.6, 0.8)),
    ("opportunist", (0.8, 0.5, 0.3, 0.8, 1.0, 0.2, 0.9, 0.3)),
    ("cautious", (0.5, 0.9, 0.8, 0.1, 0.2, 0.9, 0.7, 1.0)),
    ("steadfast", (0.9, 0.6, 0.8, 0.5, 0.6, 0.9, 0.1, 0.8)),
)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_observation(obs: dict) -> None:
    if not isinstance(obs, dict) or obs.get("schema") != "game.bridgeCaptainObservation.v1":
        raise RuntimeError("CJM_OBSERVATION_SCHEMA_INVALID")
    if (obs.get("captainId"), obs.get("shipId"), obs.get("mission")) != (
        "captain.beta", "ship.beta", MISSION):
        raise RuntimeError("CJM_OBSERVATION_AUTHORITY_INVALID")
    for key in ("simulationSeconds", "rangeM", "radialVelocityMps", "relativeSpeedMps",
                "targetHullPercent", "transporterMaxRangeM"):
        v = obs.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise RuntimeError("CJM_OBSERVATION_NUMBER_INVALID: " + key)
    if not (0 <= obs["targetHullPercent"] <= 100 and obs["transporterMaxRangeM"] > 0 and
            obs["rangeM"] >= 0 and obs["relativeSpeedMps"] >= 0 and obs["simulationSeconds"] >= 0):
        raise RuntimeError("CJM_OBSERVATION_PHYSICS_INVALID")
    for key in ("relativePositionM", "relativeVelocityMps"):
        v = obs.get(key)
        if not isinstance(v, list) or len(v) != 2 or any(
                isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in v):
            raise RuntimeError("CJM_OBSERVATION_VECTOR_INVALID: " + key)
    if abs(math.hypot(*obs["relativePositionM"]) - obs["rangeM"]) > max(.01, obs["rangeM"]*1e-6):
        raise RuntimeError("CJM_OBSERVATION_RANGE_MISMATCH")
    if abs(math.hypot(*obs["relativeVelocityMps"]) - obs["relativeSpeedMps"]) > max(.01, obs["relativeSpeedMps"]*1e-6):
        raise RuntimeError("CJM_OBSERVATION_SPEED_MISMATCH")
    if not isinstance(obs.get("boarding"), dict) or obs["boarding"].get("phase") not in (
            "idle", "deploying", "deployed", "recalling", "recovered", "abandoning", "abandoned"):
        raise RuntimeError("CJM_OBSERVATION_BOARDING_INVALID")


def get_authoritative_stages() -> list[dict]:
    process = subprocess.run([sys.executable, str(SOURCE_SMOKE)], capture_output=True, text=True, timeout=50)
    if process.returncode:
        raise RuntimeError("CJM_DETERMINISTIC_SOURCE_FAILED: " + process.stderr[-900:])
    data = json.loads(process.stdout)
    if data.get("ok") is not True or data.get("failedChecks"):
        raise RuntimeError("CJM_DETERMINISTIC_SCENARIO_NOT_GREEN")
    stages = []
    # Opening stage is identical in both branches; intentionally store it once.
    for strategy in ("recall", "abandon"):
        for stage in data["metrics"][strategy]["decisionObservations"]:
            obs = stage["observation"]
            validate_observation(obs)
            if any(d["observation"] == obs for d in stages):
                continue
            stages.append({"source": "authoritative-deterministic-encounter",
                           "stage": f"{strategy}/{stage['stage']}",
                           "observation": obs})
    if len(stages) != 4:
        raise RuntimeError(f"CJM_UNEXPECTED_STAGE_COUNT: {len(stages)}")
    deployed = next(s["observation"] for s in stages if s["observation"]["boarding"]["phase"] == "deployed")
    for phase, action, complete in (("deploying", "initiate", 51),
                                    ("recalling", "recall", 87),
                                    ("abandoning", "abandon", 75)):
        obs = copy.deepcopy(deployed)
        if phase == "deploying":
            obs["simulationSeconds"] = 36
            obs["targetHullPercent"] = 100
        else:
            obs["simulationSeconds"] = 69
        obs["boarding"] = {"phase": phase, "boarders": "aboard" if phase == "deploying" else "deployed",
                           "action": action, "startedAtSeconds": complete-ACTION_MAP[action][3],
                           "completeAtSeconds": complete, "decisionId": f"cjm-synthetic-{action}",
                           "source": "cjm-constructed-state-v1"}
        validate_observation(obs)
        stages.append({"source": "constructed-temporal-commitment-state",
                       "stage": f"constructed/{phase}", "observation": obs})
    return stages


def counterfactual(obs: dict, variant: int) -> dict:
    """Explicit constructed alternatives, not claims about observed game history."""
    out = copy.deepcopy(obs)
    if variant == 0:
        return out
    if variant == 1:
        out["targetHullPercent"] = 35 if out["targetHullPercent"] > 40 else 90
    else:
        target_range = out["transporterMaxRangeM"] + (650 if variant % 2 == 0 else -120)
        p = out["relativePositionM"]
        d = math.hypot(*p)
        out["relativePositionM"] = [v * target_range / d for v in p]
        out["rangeM"] = math.hypot(*out["relativePositionM"])
        v = out["relativeVelocityMps"]
        s = math.hypot(*v)
        speed = 15 if variant % 2 == 0 else 2
        out["relativeVelocityMps"] = [x * speed / s for x in v]
        out["relativeSpeedMps"] = math.hypot(*out["relativeVelocityMps"])
        out["radialVelocityMps"] = (sum(a*b for a,b in zip(out["relativePositionM"],out["relativeVelocityMps"]))
                                      / max(out["rangeM"], 1e-9))
    validate_observation(out)
    return out


def legal_actions(obs: dict) -> list[str]:
    phase = obs["boarding"]["phase"]
    items = ["approach", "hold-inner", "hold-outer"]
    if phase in ("idle", "recovered", "abandoned"):
        items.append("withdraw")
    items.append("coast")
    if phase == "idle" and obs["rangeM"] <= obs["transporterMaxRangeM"] and obs["relativeSpeedMps"] <= 5:
        items.append("initiate")
    if phase == "deployed":
        if obs["rangeM"] <= obs["transporterMaxRangeM"]:
            items.append("recall")
        items.append("abandon")
    return items


def utility_features(obs: dict, action: str) -> dict[str, float]:
    """Interpretable synthetic one-step preference features, NOT predicted physics."""
    phase = obs["boarding"]["phase"]
    d = obs["rangeM"]
    in_range = d <= obs["transporterMaxRangeM"]
    threatened = 1 - obs["targetHullPercent"] / 100
    committed = phase in ("deploying", "deployed", "recalling", "abandoning")
    mission = {
        "approach": .8 if not in_range else .4,
        "hold-inner": .5 if in_range else .2,
        "hold-outer": .45 if in_range else .1,
        "withdraw": -1.0, "coast": -.2,
        "initiate": 1.0, "recall": -.15, "abandon": -.5,
    }[action]
    crew = {"approach": -.15 if committed else 0., "hold-inner": .05,
            "hold-outer": .1, "withdraw": .4, "coast": 0., "initiate": -.4,
            "recall": 1., "abandon": -1.}[action]
    ship = {"approach": -.7, "hold-inner": -.25, "hold-outer": -.1,
            "withdraw": 1., "coast": .05, "initiate": -.45,
            "recall": -.35, "abandon": .65}[action]
    risk = {"approach": .8, "hold-inner": .2, "hold-outer": .05, "withdraw": -.6,
            "coast": -.2, "initiate": 1., "recall": .4, "abandon": .1}[action]
    initiative = {"approach": .7, "hold-inner": .1, "hold-outer": -.1, "withdraw": .5,
                  "coast": -.5, "initiate": 1., "recall": .25, "abandon": .7}[action]
    patience = {"approach": -.2, "hold-inner": .6, "hold-outer": .7, "withdraw": -.15,
                "coast": .1, "initiate": .4, "recall": .8, "abandon": -.7}[action]
    adapt = {"approach": -.35 if threatened > .4 else .1,
             "hold-inner": .1, "hold-outer": .2, "withdraw": 1. if threatened > .4 else -.2,
             "coast": .2, "initiate": -.25 if threatened > .4 else .5,
             "recall": .8 if threatened > .4 else -.1,
             "abandon": .9 if threatened > .4 else -.5}[action]
    evidence = {"approach": -.6 if threatened else .2, "hold-inner": .5,
                "hold-outer": .8, "withdraw": .3 if threatened > .4 else -.3,
                "coast": .5, "initiate": .4 if in_range else -.7,
                "recall": .7 if threatened > .4 else -.2,
                "abandon": -.4 if threatened <= .4 else .4}[action]
    return {"mission_commitment": mission, "ship_preservation": ship*threatened,
            "crew_loyalty": crew, "risk_tolerance": risk,
            "initiative": initiative, "patience": patience,
            "adaptability": adapt, "evidence_discipline": evidence}


def utility(traits: dict, features: dict) -> float:
    # Sign on risk dimension makes risk aversion materially different from risk seeking.
    return round(
        3.0*traits["mission_commitment"]*features["mission_commitment"] +
        2.4*traits["ship_preservation"]*features["ship_preservation"] +
        3.0*traits["crew_loyalty"]*features["crew_loyalty"] +
        1.5*(2*traits["risk_tolerance"]-1)*features["risk_tolerance"] +
        1.0*traits["initiative"]*features["initiative"] +
        1.0*traits["patience"]*features["patience"] +
        1.3*traits["adaptability"]*features["adaptability"] +
        1.0*traits["evidence_discipline"]*features["evidence_discipline"], 8)


def split_members(n: int, seed: int, label: str) -> list[str]:
    if n < 9:
        raise ValueError("at least 9 entries required for isolated splits")
    # Arrange independently for jackets and scenes. Do not reuse jackets or
    # scenario states across the train, dev and test partitions.
    indices = list(range(n))
    random.Random(int(digest([seed, label])[:15], 16)).shuffle(indices)
    result = [""] * n
    train_n = max(1, round(n * .70)); dev_n = max(1, round(n * .15))
    for idx, source_idx in enumerate(indices):
        result[source_idx] = "train" if idx < train_n else "dev" if idx < train_n + dev_n else "test"
    return result


def jackets(count: int, seed: int) -> list[tuple[str, str, dict]]:
    if count < len(JACKET_ARCHETYPES) + 1:
        raise ValueError("--jackets must be at least 9")
    names = tuple(d[0] for d in DIMENSIONS)
    out = [(f"cjm-j{idx:04d}", name, dict(zip(names, vals)))
           for idx, (name, vals) in enumerate(JACKET_ARCHETYPES)]
    rng = random.Random(seed)
    for i in range(len(out), count):
        # Half low/high anchor mixtures, half intermediate continuous jackets.
        values = ([round(rng.choice((.05, .25, .75, .95)), 3) for _ in names] if i % 2 == 0
                  else [round(rng.uniform(.0, 1.0), 3) for _ in names])
        out.append((f"cjm-j{i:04d}", f"seeded-{i:04d}", dict(zip(names, values))))
    return out


def generate(*, output_dir: Path, jacket_count: int = 24, variants: int = 3,
             seed: int = 20261009, min_margin: float = .15) -> dict:
    if jacket_count < 9 or variants < 2 or variants > 8 or not (0 <= min_margin <= 10):
        raise ValueError("invalid generation parameters")
    if output_dir.exists():
        raise FileExistsError(f"CJM_OUTPUT_ALREADY_EXISTS: {output_dir}; select a new --output-dir")
    source = get_authoritative_stages()
    scenes = []
    for sidx, item in enumerate(source):
        for variant in range(variants):
            observation = counterfactual(item["observation"], variant)
            kind = item["source"] if variant == 0 else "counterfactual-derived-from-" + item["source"]
            scenes.append((f"cjm-s{sidx:02d}-v{variant:02d}",
                           f"cjm-source-{sidx:02d}", kind, item["stage"], observation))
    jacket_rows = jackets(jacket_count, seed)
    jacket_splits = split_members(len(jacket_rows), seed, "jackets")
    scene_splits = split_members(len(scenes), seed, "scenes")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cjm-build-", dir=output_dir.parent) as tmp:
        temp = Path(tmp)
        with CaptainJacketStore(temp / "captain-jackets.sqlite") as store:
            store.add("meta", ("key", "value"), ("dataset_schema", DATA_SCHEMA))
            store.add("meta", ("key", "value"), ("seed", str(seed)))
            store.add("meta", ("key", "value"), ("teacher", TEACHER))
            store.add("meta", ("key", "value"), ("source_smoke", str(SOURCE_SMOKE.relative_to(ROOT).as_posix())))
            store.add("missions", ("mission_id", "objective", "source"),
                      ("first-encounter", MISSION, "production-encounter-observation"))
            for action_id, action_type, action_text, duration in ACTIONS:
                store.add("actions", ("action_id", "action_type", "action_text", "duration_seconds"),
                          (action_id, action_type, action_text, duration))
            for (jid, label, traits), split in zip(jacket_rows, jacket_splits):
                store.add("jackets", ("jacket_id", "label", "traits_json", "split"),
                          (jid, label, canonical(traits), split))
            for (sid, family, kind, stage, obs), split in zip(scenes, scene_splits):
                store.add("scenes", ("scene_id", "family_id", "split", "source_kind", "source_stage",
                                     "observation_json", "observation_sha256"),
                          (sid, family, split, kind, stage, canonical(obs), digest(obs)))
            for (jid, label, traits), j_split in zip(jacket_rows, jacket_splits):
                for (sid, family, kind, stage, obs), s_split in zip(scenes, scene_splits):
                    # Conservative split: nothing from a dev/test jacket OR dev/test
                    # observation can enter training. Cross-split mixtures reserved.
                    if j_split != s_split:
                        continue
                    case_id = f"cjm-case:{jid}:{sid}"
                    store.add("cases", ("case_id", "jacket_id", "scene_id", "mission_id", "split"),
                              (case_id, jid, sid, "first-encounter", j_split))
                    candidates = legal_actions(obs)
                    scores = {}
                    for action in candidates:
                        features = utility_features(obs, action)
                        score = utility(traits, features)
                        scores[action] = score
                        store.add("case_actions", ("case_id", "action_id", "utility", "features_json"),
                                  (case_id, action, score, canonical(features)))
                    for a, b in combinations(candidates, 2):
                        delta = scores[a] - scores[b]
                        labeled = abs(delta) >= min_margin
                        preference_id = "cjm-p-" + digest([case_id, a, b])[:24]
                        store.add("preferences", ("preference_id", "case_id", "left_action", "right_action",
                                                  "left_utility", "right_utility", "preferred_action",
                                                  "status", "teacher", "label_margin"),
                                  (preference_id, case_id, a, b, scores[a], scores[b],
                                   (a if delta > 0 else b) if labeled else None,
                                   "labeled" if labeled else "ambiguous", TEACHER, abs(delta)))
                        if labeled:
                            store.add("preference_reserve", ("preference_id", "split"),
                                      (preference_id, j_split))
            store.conn.commit()
            if store.conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("CJM_FOREIGN_KEY_INTEGRITY_FAILED")
            stats = store.stats()
            if not all(stats["splits"].get(part, 0) for part in ("train", "dev", "test")):
                raise RuntimeError("CJM_SPLIT_EMPTY")
            split_exports = {}
            for split in ("train", "dev", "test"):
                paths = {kind: temp / f"{kind}-{split}.jsonl" for kind in ("rom", "raw")}
                counts = Counter()
                with paths["rom"].open("w", encoding="utf-8", newline="\n") as structured, paths["raw"].open("w", encoding="utf-8", newline="\n") as raw:
                    for _, rom, plain in store.export_records(split):
                        structured.write(canonical(rom) + "\n")
                        raw.write(canonical(plain) + "\n")
                        counts["samples"] += 1
                        counts["gold_"+plain["completion"]] += 1
                if not counts["samples"] or counts["gold_A"] != counts["gold_B"]:
                    raise RuntimeError("CJM_POSITION_BALANCE_FAILED: " + split)
                split_exports[split] = dict(counts)
            checks = {"db_integrity": store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                      "no_cross_split_case": store.conn.execute("""
                            SELECT COUNT(*) FROM cases c JOIN jackets j ON j.jacket_id=c.jacket_id
                            JOIN scenes s ON s.scene_id=c.scene_id
                            WHERE c.split<>j.split OR c.split<>s.split""").fetchone()[0] == 0,
                      "no_illegal_withdraw": store.conn.execute("""
                            SELECT COUNT(*) FROM case_actions a JOIN cases c ON c.case_id=a.case_id
                            JOIN scenes s ON s.scene_id=c.scene_id
                            WHERE a.action_id='withdraw' AND
                              (json_extract(s.observation_json,'$.boarding.phase') IN
                              ('deploying','deployed','recalling','abandoning'))""").fetchone()[0] == 0,
                      "all_gold_in_case_actions": store.conn.execute("""
                            SELECT COUNT(*) FROM preferences p WHERE preferred_action IS NOT NULL
                            AND NOT EXISTS(SELECT 1 FROM case_actions ca
                                WHERE ca.case_id=p.case_id AND ca.action_id=p.preferred_action)""").fetchone()[0] == 0}
            if not all(checks.values()):
                raise RuntimeError("CJM_SELF_TEST_FAILED: " + str(checks))
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(temp.glob("*.jsonl"))}
        manifest = {"schema": DATA_SCHEMA, "storeSchema": SCHEMA_VERSION,
                    "ok": True, "seed": seed, "jacketsRequested": jacket_count,
                    "variationsPerSource": variants, "minimumLabelMargin": min_margin,
                    "sourceObservationCount": len(source),
                    "sourceStagesSha256": digest(source),
                    "sourceSmokeSha256": hashlib.sha256(SOURCE_SMOKE.read_bytes()).hexdigest(),
                    "sourceProvenance": "4 authoritative observations and 3 explicitly constructed temporal states",
                    "labelProvenance": TEACHER, "labelsAreMeasuredOutcomes": False,
                    "containsLiveModelDecisions": False, "counts": stats, "exports": split_exports,
                    "checks": checks, "jsonlSha256": hashes,
                    "holdoutPolicy": "disjoint jacket IDs and disjoint scene IDs across train/dev/test; cross-split combinations omitted",
                    "positionPolicy": "one AB and one BA example per labeled pair, in the same split"}
        (temp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, output_dir)
    return {**manifest, "outputDir": str(output_dir), "files": sorted(p.name for p in output_dir.iterdir())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--jackets", type=int, default=24)
    parser.add_argument("--variants", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--minimum-margin", type=float, default=.15)
    parser.add_argument("--self-test", action="store_true", help="generate and verify in a temporary directory")
    args = parser.parse_args()
    try:
        if args.self_test:
            with tempfile.TemporaryDirectory(prefix="cjm-self-test-") as temp:
                result = generate(output_dir=Path(temp) / "dataset", jacket_count=args.jackets,
                                  variants=args.variants, seed=args.seed, min_margin=args.minimum_margin)
                result["outputDir"] = "<temporary-self-test>"
                result["files"] = sorted(result["files"])
        else:
            result = generate(output_dir=args.output_dir, jacket_count=args.jackets,
                              variants=args.variants, seed=args.seed, min_margin=args.minimum_margin)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"schema": DATA_SCHEMA, "ok": False,
                          "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
