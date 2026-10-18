"""Verify the fixed-payoff continuation game under explicit x/y semantic definitions."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from tools.hawk_dove_continuation_head import (
    BeliefDefinitions, PayoffBindings, certify, direct_oracle, evaluate,
    game_tree, variant,
)

CASES = (
    ("0.2", "0.8", "HAWK"),
    ("0.6", "0.4", "TIE"),
    ("0.8", "0.2", "DOVE"),
    ("0.8", "1.5", "HAWK"),
    ("4.54444440000", "4.44444444444", "HAWK"),
)


def run(manifest: str | Path, belief_manifest: str | Path | None = None) -> dict:
    bindings = PayoffBindings.load(manifest)
    beliefs = (BeliefDefinitions.load(belief_manifest) if belief_manifest is not None
               else BeliefDefinitions.default())
    baseline = game_tree(beliefs)
    certificates = []
    for swap_hawk in (False, True):
        for swap_dove in (False, True):
            tree = variant(swap_hawk, swap_dove, beliefs)
            certificates.append({"hawk_swapped": swap_hawk, "dove_swapped": swap_dove,
                                 **certify(tree, bindings, beliefs)})
    cases = []
    for x, y, expected in CASES:
        if beliefs.mode == "probabilities" and Decimal(x) + Decimal(y) != 1:
            continue
        result = evaluate(baseline, x, y, bindings, belief_definitions=beliefs)
        hawk_ref, dove_ref = direct_oracle(Decimal(x), Decimal(y), bindings, beliefs)
        assert (Decimal(result["hawk_expected"]), Decimal(result["dove_expected"])) == (hawk_ref, dove_ref)
        if bindings.demo_only and beliefs.x_event == "opponent_hawk" and beliefs.y_event == "opponent_dove":
            assert result["argmax"] == expected, (x, y, result)
        for hs in (False, True):
            for ds in (False, True):
                other = evaluate(variant(hs, ds, beliefs), x, y, bindings, belief_definitions=beliefs)
                assert (other["hawk_expected"], other["dove_expected"], other["argmax"]) == (
                    result["hawk_expected"], result["dove_expected"], result["argmax"]
                )
        cases.append(result)
    return {
        "game": "Hawk-Dove",
        "manifest_version": bindings.binding_version,
        "demo_only": bindings.demo_only,
        "binding_sha256": bindings.sha256(),
        "belief_definitions": beliefs.as_dict(),
        "belief_definition_sha256": beliefs.sha256(),
        "certificate": certificates,
        "cases": cases,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--belief-definitions", type=Path)
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()
    report = run(args.manifest, args.belief_definitions)
    print("x       y         Hawk      Dove       ARGMAX")
    for c in report["cases"]:
        print(f"{c['beliefs']['x']:<7} {c['beliefs']['y']:<9} {c['hawk_expected']:<9} {c['dove_expected']:<10} {c['argmax']}")
    print(f"PASS: 4 exact-equivalent rearrangements; {sum(c['numerical_probes'] for c in report['certificate'])} teacher-vs-matrix probes; {len(report['cases'])} simulated scenarios")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Wrote:", args.output)


if __name__ == "__main__":
    main()
