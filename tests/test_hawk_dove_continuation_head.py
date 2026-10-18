import json
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.hawk_dove_continuation_head import (
    CANONICAL_EXPRESSION, PAYOFF_ROLES, Node, PayoffBindings, BeliefDefinitions, game_tree,
    certify, collapse_rows, direct_oracle, evaluate, evaluate_fragment, expand_macros, fragments, parse, prompt,
    render, structure, symbolic_signature, variant,
)


class HawkDoveContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = ROOT / "examples" / "hawk_dove_DEMO_ONLY.json"
        cls.bindings = PayoffBindings.load(cls.manifest)
        cls.root = parse(CANONICAL_EXPRESSION)

    def test_expression_round_trip(self):
        self.assertEqual(parse(render(self.root)), self.root)

    def test_each_payoff_occurs_once_no_payoff_binding_declarations(self):
        for tag in PAYOFF_ROLES:
            self.assertEqual(CANONICAL_EXPRESSION.count(tag), 1, tag)
        self.assertNotIn("BIND", CANONICAL_EXPRESSION)
        self.assertEqual(CANONICAL_EXPRESSION.count("(x,"), 2)
        self.assertEqual(CANONICAL_EXPRESSION.count("(y,"), 2)
        self.assertEqual(len(structure(self.root)[0]), 2)
        self.assertEqual(len(structure(self.root)[1]), 2)

    def test_hawk_wins(self):
        r = evaluate(self.root, ".2", ".8", self.bindings, probability_mode=True)
        self.assertEqual(r["hawk_expected"], "2.2")
        self.assertEqual(r["dove_expected"], "1.2")
        self.assertEqual(r["argmax"], "HAWK")
        self.assertEqual([t["contribution"] for t in r["hawk_add_until_with"]], ["-0.2", "2.4"])
        self.assertEqual([t["contribution"] for t in r["dove_add_until_end"]], ["0", "1.2"])

    def test_dove_wins(self):
        r = evaluate(self.root, ".8", ".2", self.bindings, probability_mode=True)
        self.assertEqual(r["hawk_expected"], "-0.2")
        self.assertEqual(r["dove_expected"], "0.3")
        self.assertEqual(r["argmax"], "DOVE")

    def test_tie_exact(self):
        r = evaluate(self.root, ".6", ".4", self.bindings, probability_mode=True)
        self.assertEqual(r["hawk_expected"], "0.6")
        self.assertEqual(r["dove_expected"], "0.6")
        self.assertEqual(r["argmax"], "TIE")

    def test_beliefs_are_independent_weights_in_default_mode(self):
        result = evaluate(self.root, ".8", "1.5", self.bindings)
        self.assertEqual(result["hawk_expected"], "3.7")
        self.assertEqual(result["dove_expected"], "2.25")
        self.assertEqual(result["argmax"], "HAWK")

    def test_exact_high_precision_decimal(self):
        x, y = Decimal("4.54444440000"), Decimal("4.44444444444")
        result = evaluate(self.root, x, y, self.bindings)
        a, b = direct_oracle(x, y, self.bindings)
        self.assertEqual(Decimal(result["hawk_expected"]), a)
        self.assertEqual(Decimal(result["dove_expected"]), b)

    def test_four_independent_swaps_are_symbolically_equivalent(self):
        sig = symbolic_signature(self.root)
        for swap_hawk in (False, True):
            for swap_dove in (False, True):
                v = variant(swap_hawk, swap_dove)
                self.assertEqual(symbolic_signature(v), sig)
                self.assertEqual(certify(v, self.bindings)["numerical_probes"], 48)
                self.assertEqual(parse(render(v)), v)

    def test_row_collapse_is_executable_and_expands_to_same_tree(self):
        macro_tree = collapse_rows(self.root)
        round_trip = parse(render(macro_tree))
        self.assertEqual(macro_tree, round_trip)
        expanded = expand_macros(round_trip)
        self.assertEqual(expanded, self.root)
        self.assertTrue(certify(expanded, self.bindings)["passed"])
        self.assertTrue(certify(macro_tree, self.bindings)["passed"])
        for x, y in [("0.2", "0.8"), ("0.8", "0.2")]:
            self.assertEqual(evaluate(round_trip, x, y, self.bindings)["argmax"], evaluate(self.root, x, y, self.bindings)["argmax"])
        self.assertIn("HAWK_EXPECTED_PAYOFF", prompt(self.root, "0.2", "0.8", "rows"))

    def test_cannot_move_payoff_into_other_add_scope(self):
        broken = CANONICAL_EXPRESSION.replace("HAWK_HAWK_PAYOFF", "DOVE_HAWK_PAYOFF", 1)
        with self.assertRaises(ValueError):
            structure(parse(broken))

    def test_cannot_misbind_belief(self):
        broken = CANONICAL_EXPRESSION.replace("HAWK_DOVE_PAYOFF(y,", "HAWK_DOVE_PAYOFF(x,", 1)
        with self.assertRaises(ValueError):
            structure(parse(broken))

    def test_cannot_remove_with_boundary(self):
        broken = CANONICAL_EXPRESSION.replace("With(", "Add(", 1)
        with self.assertRaises(ValueError):
            structure(parse(broken))

    def test_rejects_nonfinite_negative_or_invalid_probability_beliefs(self):
        for x, y in [("NaN", "1"), ("Infinity", "1"), ("-0.1", "1"), ("0", "0")]:
            with self.subTest(x=x, y=y), self.assertRaises(ValueError):
                evaluate(self.root, x, y, self.bindings)
        with self.assertRaises(ValueError):
            evaluate(self.root, ".8", ".8", self.bindings, probability_mode=True)

    def test_binding_hash_refuses_reassignment(self):
        with self.assertRaisesRegex(ValueError, "relearning required"):
            evaluate(self.root, ".5", ".5", self.bindings, expected_binding_hash="0" * 64)
        ok = evaluate(self.root, ".5", ".5", self.bindings, expected_binding_hash=self.bindings.sha256())
        self.assertEqual(ok["binding_sha256"], self.bindings.sha256())

    def test_isolated_fragments_compute(self):
        units = fragments(self.root, Decimal(".2"), Decimal(".8"), self.bindings)
        self.assertEqual(len(units), 12)
        self.assertEqual([u["stage"] for u in units[:4]], ["binding"] * 4)
        for u in units:
            self.assertIn("Hawk–Dove", u["model_prompt"])
            self.assertNotIn("BIND", u["model_prompt"])
        for u in units[:10]:
            xx, yy = u["beliefs"]["x"], u["beliefs"]["y"]
            self.assertEqual(Decimal(u["target"]), evaluate_fragment(parse(u["unit"]), xx, yy, self.bindings))
        single = next(u for u in units if u["stage"] == "weighted_payoff" and u["unit"].startswith("DOVE_DOVE_PAYOFF("))
        self.assertEqual(single["target"], "1.2")
        dove_sum = next(u for u in units if u["stage"] == "add_scope" and u["group"] == "DOVE")
        self.assertEqual(dove_sum["target"], "1.2")
        self.assertEqual(units[-1]["winner_diagnostic"], "HAWK")
        self.assertEqual(units[-2]["hawk_target"], "2.2")
        self.assertEqual(units[-1]["dove_target"], "1.2")

    def test_prompt_does_not_disclose_fixed_payoffs(self):
        expanded = prompt(self.root, ".2", ".8", "full")
        self.assertIn("HAWK_HAWK_PAYOFF(x", expanded)
        self.assertIn("x = 0.2 [belief weight that the opponent plays Hawk]", expanded)
        self.assertIn("y = 0.8 [belief weight that the opponent plays Dove]", expanded)
        self.assertNotIn("-1", expanded)
        self.assertNotIn("1.5", expanded)
        self.assertIn("::", expanded)
        for level in ("rows", "game", "unitary"):
            self.assertNotIn("HAWK_HAWK_PAYOFF", prompt(self.root, ".2", ".8", level))
        bare = prompt(self.root, ".2", ".8", "unitary")
        self.assertNotIn("0.2", bare)
        self.assertNotIn("0.8", bare)
        self.assertNotIn("opponent plays Hawk", bare)
        self.assertNotIn("opponent plays Dove", bare)

    def test_manifest_is_a_demo_not_a_claim_about_learned_model(self):
        self.assertTrue(self.bindings.demo_only)
        self.assertIn("not-learned", self.bindings.binding_version)
        payload = json.loads(self.manifest.read_text())
        self.assertEqual(len(payload["payoffs"]), 4)

    def test_definitions_loaded_and_reused_without_payoff_declarations(self):
        spec = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_probability.json")
        root = game_tree(spec)
        result = evaluate(root, "0.2", "0.8", self.bindings, belief_definitions=spec)
        self.assertEqual(result["argmax"], "HAWK")
        self.assertEqual(result["hawk_expected"], "2.2")
        self.assertEqual(result["belief_definitions"]["variables"]["x"]["event"], "opponent_hawk")
        generated = prompt(root, "0.2", "0.8", "full", spec)
        self.assertIn("your probability that the opponent plays Hawk", generated)
        self.assertIn("your probability that the opponent plays Dove", generated)
        self.assertEqual(generated.count("x = 0.2"), 1)
        self.assertEqual(generated.count("y = 0.8"), 1)
        for payoff in PAYOFF_ROLES:
            self.assertEqual(generated.count(payoff), 1)
        self.assertNotIn("BIND", generated)
        self.assertNotIn("-1", generated)
        self.assertNotIn("1.5", generated)
        self.assertNotIn("your probability", prompt(root, "0.2", "0.8", "unitary", spec))
        self.assertEqual(len(fragments(root, Decimal(".2"), Decimal(".8"), self.bindings, spec)), 12)

    def test_swapped_definitions_reassign_references_without_rebinding_payoffs(self):
        spec = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_swapped_probability.json")
        root = game_tree(spec)
        expression = render(root)
        for payoff in PAYOFF_ROLES:
            self.assertEqual(expression.count(payoff), 1)
        self.assertIn("HAWK_HAWK_PAYOFF(y,", expression)
        self.assertIn("HAWK_DOVE_PAYOFF(x,", expression)
        self.assertIn("DOVE_HAWK_PAYOFF(y,", expression)
        self.assertIn("DOVE_DOVE_PAYOFF(x,", expression)
        r = evaluate(root, "0.2", "0.8", self.bindings, belief_definitions=spec)
        self.assertEqual((r["hawk_expected"], r["dove_expected"], r["argmax"]), ("-0.2", "0.3", "DOVE"))
        self.assertEqual((Decimal(r["hawk_expected"]), Decimal(r["dove_expected"])),
                         direct_oracle(Decimal("0.2"), Decimal("0.8"), self.bindings, spec))
        self.assertEqual(expand_macros(collapse_rows(root, spec), spec), root)
        self.assertTrue(certify(root, self.bindings, spec)["passed"])
        for hs in (False, True):
            for ds in (False, True):
                self.assertEqual(symbolic_signature(root, spec), symbolic_signature(variant(hs, ds, spec), spec))

    def test_probabilities_are_constrained_weights_are_independent(self):
        probability = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_probability.json")
        weights = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_weights.json")
        with self.assertRaisesRegex(ValueError, r"x \+ y = 1"):
            evaluate(game_tree(probability), "0.8", "1.5", self.bindings, belief_definitions=probability)
        r = evaluate(game_tree(weights), "0.8", "1.5", self.bindings, belief_definitions=weights)
        self.assertEqual((r["hawk_expected"], r["dove_expected"]), ("3.7", "2.25"))
        self.assertIn("nonnegative belief weights", prompt(game_tree(weights), ".8", "1.5", "full", weights))

    def test_definition_contract_cannot_be_changed_silently(self):
        standard = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_probability.json")
        swapped = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_swapped_probability.json")
        self.assertNotEqual(standard.sha256(), swapped.sha256())
        self.assertEqual(evaluate(game_tree(standard), ".2", ".8", self.bindings,
                                  belief_definitions=standard,
                                  expected_belief_definition_hash=standard.sha256())["argmax"], "HAWK")
        with self.assertRaisesRegex(ValueError, "Belief meanings changed"):
            evaluate(game_tree(swapped), ".2", ".8", self.bindings,
                     belief_definitions=swapped,
                     expected_belief_definition_hash=standard.sha256())
        with self.assertRaises(ValueError):
            structure(game_tree(standard), swapped)
        self.assertEqual(standard.sha256(), BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_probability.json").sha256())

    def test_invalid_definition_contract_rejected(self):
        invalid = [
            BeliefDefinitions("bad", "probabilities", "opponent_hawk", "hawk", "opponent_hawk", "dove"),
            BeliefDefinitions("bad", "weights", "opponent_hawk", "hawk", "invalid", "dove"),
            BeliefDefinitions("bad", "random", "opponent_hawk", "hawk", "opponent_dove", "dove"),
            BeliefDefinitions("", "weights", "opponent_hawk", "hawk", "opponent_dove", "dove"),
            BeliefDefinitions("bad", "weights", "opponent_hawk", "", "opponent_dove", "dove"),
        ]
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                spec.validate()

    def test_swap_does_not_change_payoff_binding_fingerprint(self):
        standard = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_probability.json")
        swapped = BeliefDefinitions.load(ROOT / "examples" / "hawk_dove_beliefs_swapped_probability.json")
        binding_before = self.bindings.sha256()
        evaluate(game_tree(standard), ".2", ".8", self.bindings, belief_definitions=standard)
        evaluate(game_tree(swapped), ".2", ".8", self.bindings, belief_definitions=swapped)
        self.assertEqual(binding_before, self.bindings.sha256())


if __name__ == "__main__":
    unittest.main()
