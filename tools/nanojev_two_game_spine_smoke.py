#!/usr/bin/env python3
"""NanoJev diagnostic: immediate Hawk-Dove incentives + repeated reciprocity.

Source-only oracle is the default; a mathematical oracle is NOT a neural PASS.
The optional read-only model modes evaluate the existing frozen Gamer LoRA,
original C0 Wide head, or 100-vector expanded head. No training or mutation.

    python -m tools.nanojev_two_game_spine_smoke
    python -m tools.nanojev_two_game_spine_smoke --deck runtime/reports/two-game-deck.jsonl
    python -m tools.nanojev_two_game_spine_smoke --mode score --responses answers.jsonl
    python -m tools.nanojev_two_game_spine_smoke --mode wide --run RUN --device cuda
    python -m tools.nanojev_two_game_spine_smoke --mode expanded --run RUN --expanded-run EXPANDED_RUN --device cuda
    python -m tools.nanojev_two_game_spine_smoke --mode live --adapter-dir PATH --device cuda --limit 24

Model replies must be one unadorned numeric answer; a text explanation, even if
it contains the right number, is not scored as a numerical answer. All problems
include their complete bounded rules. There is NO claim that a payoff ordering
proves a psychological motive, that tit-for-tat is a game, or that these 2x2
profiles cover the whole game catalog.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

SCHEMA = 'nanojev.two-game-spine-smoke.v1'
ACTION_C, ACTION_D = 'C', 'D'
# Ordinal exemplars, not empirically calibrated utilities. The names of some
# permutations differ across game-theory taxonomies; the ORDER is authoritative.
PAYOFF_ORDERS = {
    'chicken_hawk_dove': 'TRSP',
    'battle_leader': 'TSRP',
    'hero': 'TSPR',
    'prisoners_dilemma': 'TRPS',
    'deadlock': 'TPRS',
    'compromise': 'TPSR',
    'stag_hunt': 'RTPS',
    'assurance': 'RPTS',
    'coordination': 'RPST',
    'peace': 'RSPT',
    'harmony': 'RSTP',
    'concord': 'RTSP',
}
FAMILY_ID = {'prisoners_dilemma': 'defection_dominates',
             'chicken_hawk_dove': 'anti_coordination',
             'stag_hunt': 'coordination', 'harmony': 'cooperation_dominates'}


def fstr(value: Fraction | int) -> str:
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f'{value.numerator}/{value.denominator}'


def fnum(value: str | Fraction | int) -> Fraction:
    if isinstance(value, bool):
        raise ValueError('BOOLEAN_NOT_NUMERIC')
    return Fraction(str(value))


@dataclass(frozen=True)
class Stage:
    R: Fraction
    S: Fraction
    T: Fraction
    P: Fraction

    def value(self, own: str, opponent: str) -> Fraction:
        return {(ACTION_C, ACTION_C): self.R, (ACTION_C, ACTION_D): self.S,
                (ACTION_D, ACTION_C): self.T, (ACTION_D, ACTION_D): self.P}[(own, opponent)]

    def gaps(self) -> tuple[Fraction, Fraction]:
        return self.T - self.R, self.P - self.S

    def margin(self, q: Fraction) -> Fraction:
        if not 0 <= q <= 1:
            raise ValueError('INVALID_COOPERATION_BELIEF')
        a, b = self.gaps()
        # Independent from expected-payoff computation in validate_oracle.
        return q * a + (1 - q) * b

    def family(self) -> str:
        a, b = self.gaps()
        if a == 0 or b == 0:
            return 'boundary'
        return ('defection_dominates' if a > 0 and b > 0 else
                'anti_coordination' if a > 0 and b < 0 else
                'coordination' if a < 0 and b > 0 else
                'cooperation_dominates')


def ordinal_stage(order: str, *, shift: int = 0, scale: int = 1) -> Stage:
    if len(order) != 4 or set(order) != set('RSTP') or scale <= 0:
        raise ValueError('INVALID_ORDINAL_PROFILE')
    values = dict(zip(order, (8 * scale + shift, 5 * scale + shift,
                               2 * scale + shift, -3 * scale + shift)))
    return Stage(*(Fraction(values[k]) for k in 'RSTP'))


def tft_move(opponent_history: list[str]) -> str:
    return opponent_history[-1] if opponent_history else ACTION_C


def repeated(stage: Stage, *, rounds: int, discount: Fraction,
             opponent_policy: str = 'tft', error_a_round: int = -1,
             reset_round: int = -1) -> dict:
    """A plays TFT; B plays TFT or unconditional D; one forced A mistake.

    Round indices start at 0; reset is a bilateral, explicitly agreed choice.
    The reset does not silently erase previous consequences from total utility.
    """
    if rounds < 1 or not 0 <= discount <= 1 or opponent_policy not in ('tft', 'always_defect'):
        raise ValueError('INVALID_REPEATED_GAME')
    ah: list[str] = []
    bh: list[str] = []
    scores = [Fraction(0), Fraction(0)]
    history = []
    for t in range(rounds):
        a = tft_move(bh)
        b = tft_move(ah) if opponent_policy == 'tft' else ACTION_D
        if t == error_a_round:
            a = ACTION_D
        if t == reset_round:
            a, b = ACTION_C, ACTION_C
        pa, pb = stage.value(a, b), stage.value(b, a)
        scores[0] += discount ** t * pa
        scores[1] += discount ** t * pb
        history.append({'round': t + 1, 'A': a, 'B': b,
                        'payoff_A': fstr(pa), 'payoff_B': fstr(pb)})
        ah.append(a)
        bh.append(b)
    return {'A': scores[0], 'B': scores[1], 'history': history}


def signed_best_response(stage: Stage, q: Fraction) -> Fraction:
    # 1: defect; 0: cooperate, including exact zero ties.
    return Fraction(int(stage.margin(q) > 0))


def required_deterrent(stage: Stage) -> Fraction:
    """Uniform cost k on the defector's payoff, regardless of other action.

    A >= 0 cost is sufficient to make cooperation a WEAK best response to both
    opponent actions; does not establish real-world enforceability or justice.
    """
    return max(Fraction(0), *stage.gaps())


def with_deterrent(stage: Stage, k: Fraction) -> Stage:
    if k < 0:
        raise ValueError('NEGATIVE_DETERRENT')
    return Stage(stage.R, stage.S, stage.T - k, stage.P - k)


@dataclass(frozen=True)
class Case:
    case_id: str
    game: str
    layer: str
    skill: str
    prompt: str
    expected: Fraction

    def public(self) -> dict:
        # NEVER include gold in the inference deck. Only the smoke process
        # computes targets; reported failures may disclose them afterward.
        return {'schema': SCHEMA, 'id': self.case_id, 'game': self.game,
                'layer': self.layer, 'skill': self.skill, 'prompt': self.prompt}

    def oracle_identity(self) -> dict:
        return {**self.public(), 'expected': fstr(self.expected)}


def add_case(cases: list[Case], game: str, layer: str, skill: str,
             text: str, expected: Fraction | int) -> None:
    key = f'{game}.{skill}.{sum(c.game == game and c.skill == skill for c in cases):02d}'
    cases.append(Case(key, game, layer, skill,
                      text + '\nAnswer with ONE number only. Use a reduced fraction if needed.',
                      Fraction(expected)))


def payoff_text(s: Stage) -> str:
    return ('Two actions: C (cooperate) or D (defect). Your payoffs: '
            f'CC={fstr(s.R)}, CD={fstr(s.S)}, DC={fstr(s.T)}, DD={fstr(s.P)}. '
            'The first letter is your action. No assumptions about motives or identities.')


def critical_q(s: Stage) -> Fraction | None:
    a, b = s.gaps()
    if not ((a > 0 > b) or (a < 0 < b)):
        return None
    return b / (b - a)


def build_cases() -> list[Case]:
    cases: list[Case] = []
    for game, order in PAYOFF_ORDERS.items():
        for v, stage in enumerate((ordinal_stage(order), ordinal_stage(order, shift=7, scale=3))):
            base = payoff_text(stage)
            a, b = stage.gaps()
            add_case(cases, game, 'immediate', 'temptation_gap',
                     f'{base} Compute T-R (defection gain when the other cooperates).', a)
            add_case(cases, game, 'immediate', 'defensive_gap',
                     f'{base} Compute P-S (defection gain when the other defects).', b)
            for q in (Fraction(0), Fraction(1, 2), Fraction(1)):
                margin = stage.margin(q)
                add_case(cases, game, 'immediate', 'expected_margin',
                         f'{base} You estimate that the other cooperates with probability {fstr(q)}. '
                         'Find expected payoff(D) minus expected payoff(C).', margin)
                add_case(cases, game, 'immediate', 'best_response',
                         f'{base} Other cooperates with probability {fstr(q)}. '
                         'Choose 1 for D if it strictly pays more; otherwise choose 0 for C.',
                         signed_best_response(stage, q))
            threshold = critical_q(stage)
            if threshold is not None:
                for q in (threshold - Fraction(1, 100), threshold,
                          threshold + Fraction(1, 100)):
                    if not 0 <= q <= 1:
                        continue
                    add_case(cases, game, 'immediate', 'zero_line_margin',
                             f'{base} Other cooperates with probability {fstr(q)}. '
                             'Calculate q*(T-R)+(1-q)*(P-S).', stage.margin(q))
            k = required_deterrent(stage)
            changed = with_deterrent(stage, k)
            add_case(cases, game, 'institution', 'deterrent_threshold',
                     f'{base} A rule subtracts the same nonnegative penalty k from both D payoffs (DC and DD); '
                     'C payoffs remain unchanged. What is the smallest k that makes C at least as good as D '
                     'against BOTH opponent actions?', k)
            add_case(cases, game, 'institution', 'post_rule_gap',
                     f'{base} A rule subtracts k={fstr(k)} from DC and DD only. '
                     'Compute the new value of (DD-CD), i.e. the defensive gap after the rule.',
                     changed.P - changed.S)
    # Pair dynamics and exact zero-line transitions, isolated from payoff-profile labels.
    stage = ordinal_stage(PAYOFF_ORDERS['prisoners_dilemma'])
    for rounds in (4, 7):
        gamma = Fraction(3, 4)
        tft = repeated(stage, rounds=rounds, discount=gamma, opponent_policy='tft')
        defect = repeated(stage, rounds=rounds, discount=gamma, opponent_policy='always_defect')
        common = f'{payoff_text(stage)} There are {rounds} rounds, discount factor {fstr(gamma)}. '
        add_case(cases, 'reciprocity', 'repeated', 'tft_tft_value',
                 common + 'Both play tit-for-tat: C in the first round, then copy the other player’s prior action. '
                 'What is player A’s discounted payoff sum?', tft['A'])
        add_case(cases, 'reciprocity', 'repeated', 'tft_vs_always_d',
                 common + 'Player A plays tit-for-tat, opponent B always D. '
                 'What is player A’s discounted payoff sum?', defect['A'])
    for reset in (-1, 4):
        result = repeated(stage, rounds=8, discount=Fraction(1), error_a_round=1,
                          reset_round=reset)
        add_case(cases, 'reciprocity', 'repeated',
                 'single_error_no_reset' if reset < 0 else 'single_error_with_reset',
                 f'{payoff_text(stage)} Eight rounds, no discount. Both start C and copy the other\'s previous '
                 'action (tit-for-tat); A accidentally plays D in round 2. '
                 + ('There is no reset. ' if reset < 0 else
                    'At round 5, both explicitly agree to play C once as a reset. ')
                 + 'What is A\'s total payoff over eight rounds?', result['A'])
    # The mixed Hawk frequency is from a PARTICULAR symmetric payoff model,
    # not inferred from the ordinal shape of arbitrary Chicken profiles.
    for prize, cost in ((6, 10), (4, 16)):
        add_case(cases, 'canonical_hawk_dove', 'immediate', 'mixed_hawk_fraction',
                 f'Canonical Hawk-Dove: resource value V={prize}, fighting cost C={cost}, C>V. '
                 'H vs H pays (V-C)/2 each; H vs D pays V to H and 0 to D; '
                 'D vs D pays V/2 each. At the symmetric interior mixed equilibrium, '
                 'what fraction of players choose Hawk?', Fraction(prize, cost))
    # Other games can use the two-layer questions without pretending their
    # mathematical structures reduce to symmetric 2x2 normal form.
    add_case(cases, 'trust', 'repeated_extension', 'trust_net',
             'A trustee receives 12 credits from a trusting partner and returns 17. '
             'The partner\'s net received credits minus entrusted credits is?', 5)
    add_case(cases, 'public_goods', 'repeated_extension', 'public_goods_gain',
             'Four people each can contribute 3. One person contributes 3; each contributed credit '
             'produces 2 shared credits, distributed equally across the four people. '
             'What is that contributor\'s immediate payoff from the public fund minus contribution cost?',
             Fraction(-3, 2))
    add_case(cases, 'ultimatum', 'immediate_extension', 'reject_outside_payoff',
             'In one round of a specified ultimatum game, accepting gives responder 4 points and '
             'rejecting gives responder 0. What is the responder payoff difference accept minus reject?', 4)
    add_case(cases, 'war_of_attrition', 'immediate_extension', 'one_step_wait_margin',
             'An actor can stop now for 4 points, or wait one period for a certain reward of 9 '
             'minus a waiting cost of 7. What is the wait-payoff minus stop-payoff?', -2)
    add_case(cases, 'signaling', 'belief_extension', 'posterior',
             'A hidden state has prior probability 1/4. An observed signal occurs with probability '
             '3/4 in that state and 1/4 otherwise. What is the posterior probability of that state?',
             Fraction(1, 2))
    return cases



_STAGE = re.compile(r'CC=([-\d/]+), CD=([-\d/]+), DC=([-\d/]+), DD=([-\d/]+)')
_PROBABILITY = re.compile(r'probability ([-\d/]+)')
_K = re.compile(r'k=([-\d/]+)')
_ROUNDS = re.compile(r'There are (\d+) rounds, discount factor ([-\d/]+)')


def independent_answer(case: Case) -> Fraction:
    """Recompute labels from displayed inputs, not the generator's hidden stage.

    This second path deliberately parses the visible problem. A wrong displayed
    value, reversed payoff role, or stale target must fail the smoke.
    """
    m = _STAGE.search(case.prompt)
    stage = Stage(*(fnum(x) for x in m.groups())) if m else None
    skill = case.skill
    if stage is not None:
        a = stage.T - stage.R
        b = stage.P - stage.S
        if skill == 'temptation_gap': return a
        if skill == 'defensive_gap': return b
        if skill in ('expected_margin','best_response','zero_line_margin'):
            q = fnum(_PROBABILITY.search(case.prompt).group(1))
            d_expected = q * stage.T + (1-q) * stage.P
            c_expected = q * stage.R + (1-q) * stage.S
            delta = d_expected - c_expected
            return Fraction(int(delta > 0)) if skill == 'best_response' else delta
        if skill == 'deterrent_threshold': return max(Fraction(0),a,b)
        if skill == 'post_rule_gap':
            k=fnum(_K.search(case.prompt).group(1))
            return stage.P-k-stage.S
        if skill in ('tft_tft_value','tft_vs_always_d'):
            n, gamma = _ROUNDS.search(case.prompt).groups()
            n, gamma=int(n),fnum(gamma)
            if skill == 'tft_tft_value':
                return sum((gamma**t*stage.R for t in range(n)),Fraction())
            return stage.S + sum((gamma**t*stage.P for t in range(1,n)),Fraction())
        if skill == 'single_error_no_reset':
            return stage.R+4*stage.T+3*stage.S
        if skill == 'single_error_with_reset':
            return 5*stage.R+2*stage.T+stage.S
    if skill == 'mixed_hawk_fraction':
        prize, cost=re.search(r'value V=(\d+), fighting cost C=(\d+)',case.prompt).groups()
        return Fraction(int(prize),int(cost))
    if skill == 'trust_net':return Fraction(17-12)
    if skill == 'public_goods_gain':return Fraction(3*2,4)-3
    if skill == 'reject_outside_payoff':return Fraction(4-0)
    if skill == 'one_step_wait_margin':return Fraction(9-7-4)
    if skill == 'posterior':
        return (Fraction(1,4)*Fraction(3,4))/(
            Fraction(1,4)*Fraction(3,4)+Fraction(3,4)*Fraction(1,4))
    raise AssertionError('NO_INDEPENDENT_ORACLE_FOR_CASE: '+case.case_id)

def validate_oracle(cases: list[Case]) -> dict:
    if len({c.case_id for c in cases}) != len(cases):
        raise AssertionError('DUPLICATE_CASE_ID')
    for case in cases:
        if independent_answer(case) != case.expected:
            raise AssertionError('PUBLIC_PROMPT_ORACLE_MISMATCH: '+case.case_id)
    expected = {'immediate', 'institution', 'repeated', 'repeated_extension',
                'immediate_extension', 'belief_extension'}
    if not expected.issubset({c.layer for c in cases}):
        raise AssertionError('MISSING_LAYER')
    families = Counter()
    for game, order in PAYOFF_ORDERS.items():
        stage = ordinal_stage(order)
        actual_order = ''.join(sorted('RSTP', key=lambda k: getattr(stage, k), reverse=True))
        if actual_order != order:
            raise AssertionError('PAYOFF_ORDER_MISMATCH')
        families[stage.family()] += 1
        for scale in (1, 3):
            s = ordinal_stage(order, scale=scale, shift=7)
            if s.family() != stage.family():
                raise AssertionError('POSITIVE_AFFINE_CLASSIFICATION_CHANGED')
            a, b = s.gaps()
            for q in (Fraction(0), Fraction(1, 3), Fraction(1, 2), Fraction(1)):
                independent = (q * s.T + (1-q) * s.P) - (q * s.R + (1-q) * s.S)
                if s.margin(q) != independent:
                    raise AssertionError('EXPECTED_PAYOFF_NOT_INDEPENDENTLY_VERIFIED')
            penalized = with_deterrent(s, required_deterrent(s))
            if any(x > 0 for x in penalized.gaps()):
                raise AssertionError('DETERRENT_DOES_NOT_WEAKLY_DOMINATE')
            if a > 0 or b > 0:
                k = required_deterrent(s)
                if all(x <= 0 for x in with_deterrent(s, k - Fraction(1, 100)).gaps()):
                    raise AssertionError('DETERRENT_NOT_MINIMAL')
            threshold = critical_q(s)
            if threshold is not None and s.margin(threshold) != 0:
                raise AssertionError('ZERO_LINE_MISSED')
    if sum(families.values()) != 12 or len(families) != 4:
        raise AssertionError('TWELVE_PROFILE_FOUR_FAMILY_COVERAGE_MISSING')
    for name in FAMILY_ID:
        if ordinal_stage(PAYOFF_ORDERS[name]).family() != FAMILY_ID[name]:
            raise AssertionError('ANCHOR_CLASSIFICATION_MISMATCH')
    stage = ordinal_stage(PAYOFF_ORDERS['prisoners_dilemma'])
    gamma = Fraction(3, 4)
    n = 7
    coop = repeated(stage, rounds=n, discount=gamma)
    expected_coop = stage.R * (1 - gamma ** n) / (1 - gamma)
    if coop['A'] != expected_coop or coop['B'] != expected_coop:
        raise AssertionError('TFT_MUTUAL_COOPERATION_FORMULA_MISMATCH')
    defect = repeated(stage, rounds=n, discount=gamma, opponent_policy='always_defect')
    expected_defect = stage.S + stage.P * gamma * (1 - gamma ** (n - 1)) / (1 - gamma)
    if defect['A'] != expected_defect:
        raise AssertionError('TFT_ALWAYS_DEFECT_FORMULA_MISMATCH')
    no_reset = repeated(stage, rounds=8, discount=Fraction(1), error_a_round=1)
    reset = repeated(stage, rounds=8, discount=Fraction(1), error_a_round=1, reset_round=4)
    if any(x['A'] == x['B'] for x in no_reset['history'][2:]):
        raise AssertionError('TFT_RECIPROCAL_ERROR_DID_NOT_PERSIST')
    if any(x['A'] != 'C' or x['B'] != 'C' for x in reset['history'][4:]):
        raise AssertionError('BILATERAL_RESET_DID_NOT_RECOVER')
    if not any(c.expected == 0 and c.skill == 'zero_line_margin' for c in cases):
        raise AssertionError('ZERO_LINE_CASES_MISSING')
    if not any(c.expected < 0 for c in cases) or not any(c.expected > 0 for c in cases):
        raise AssertionError('SIGNED_BALANCE_MISSING')
    return {'cases':len(cases), 'profile_count':len(PAYOFF_ORDERS),
            'incentive_families':dict(sorted(families.items())),
            'layers':dict(sorted(Counter(c.layer for c in cases).items())),
            'skills':dict(sorted(Counter(c.skill for c in cases).items())),
            'zero_answers':sum(c.expected == 0 for c in cases),
            'negative_answers':sum(c.expected < 0 for c in cases),
            'positive_answers':sum(c.expected > 0 for c in cases),
            'oracle_independent_checks':'PASS_ALL_CASES'}


def deck_sha(cases: list[Case]) -> str:
    # Bind the oracle AND prompts, without revealing the answer to the learner.
    raw = json.dumps([c.oracle_identity() for c in cases], sort_keys=True,
                     separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def write_deck(path: Path, cases: list[Case]) -> None:
    fingerprint = deck_sha(cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError('DECK_ALREADY_EXISTS: '+str(path))
    with path.open('x', encoding='utf-8', newline='\n') as f:
        for case in cases:
            f.write(json.dumps({**case.public(), 'deck_sha256':fingerprint},
                               sort_keys=True, ensure_ascii=False) + '\n')


REPLY_PATTERN = re.compile(r'^\s*(?:FINAL:\s*)?([+-]?\d+(?:\.\d+)?(?:/\d+)?)\s*$', re.I)


def parse_reply(value: str) -> Fraction | None:
    match = REPLY_PATTERN.fullmatch(value)
    if not match:
        return None
    try:
        return fnum(match.group(1))
    except (ValueError, ZeroDivisionError):
        return None


def score(cases: list[Case], replies: dict[str, str]) -> dict:
    case_map = {c.case_id:c for c in cases}
    unknown = set(replies) - set(case_map)
    if unknown:
        raise ValueError('UNKNOWN_RESPONSE_IDS: '+','.join(sorted(unknown)[:5]))
    by_skill: dict[str, Counter] = {}
    failures = []
    for c in cases:
        if c.case_id not in replies:
            continue
        parsed = parse_reply(replies[c.case_id])
        ok = parsed is not None and parsed == c.expected
        counts = by_skill.setdefault(c.skill, Counter())
        counts['scored'] += 1
        counts['correct'] += int(ok)
        counts['invalid'] += int(parsed is None)
        if not ok:
            failures.append({'id': c.case_id, 'skill':c.skill,
                             'expected':fstr(c.expected), 'observed':replies[c.case_id]})
    scored = sum(v['scored'] for v in by_skill.values())
    correct = sum(v['correct'] for v in by_skill.values())
    return {'status': 'PASS_MODEL' if scored == len(cases) and correct == scored
            else 'INCOMPLETE_MODEL' if scored < len(cases) else 'FAIL_MODEL',
            'scored':scored, 'total':len(cases), 'correct':correct,
            'invalid':sum(v['invalid'] for v in by_skill.values()),
            'by_skill':{k:dict(v) for k,v in sorted(by_skill.items())},
            'failures':failures}


def read_responses(path: Path, fingerprint: str) -> dict[str, str]:
    answers = {}
    for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('answer'), str):
            raise ValueError(f'INVALID_RESPONSE_LINE_{n}')
        if row.get('deck_sha256') != fingerprint:
            raise ValueError(f'RESPONSE_DECK_FINGERPRINT_MISMATCH_LINE_{n}')
        if row['id'] in answers:
            raise ValueError(f'DUPLICATE_RESPONSE_ID_LINE_{n}')
        answers[row['id']] = row['answer']
    return answers


def select_live_cases(cases: list[Case], limit: int) -> list[Case]:
    """Stratify a small neural smoke across games, signs, and time dynamics.

    Whole-deck oracle is ALWAYS run before selecting. This is diagnostic
    coverage, not a claim that a short smoke certifies complete retention.
    """
    if limit == 0 or limit >= len(cases):
        return list(cases)
    chosen: list[Case] = []
    seen: set[str] = set()

    def pick(predicate) -> None:
        for c in cases:
            if len(chosen) >= limit:
                return
            if c.case_id not in seen and predicate(c):
                seen.add(c.case_id)
                chosen.append(c)
                return

    # Guarantee breadth before repeated examples of one game.
    for game in PAYOFF_ORDERS:
        pick(lambda c, game=game: c.game == game and
             c.skill == 'zero_line_margin' and c.expected == 0)
        if game not in {c.game for c in chosen}:
            pick(lambda c, game=game: c.game == game and
                 c.skill == 'expected_margin' and 'probability 1/2' in c.prompt)
    for skill in ('tft_tft_value', 'tft_vs_always_d', 'single_error_no_reset',
                  'single_error_with_reset', 'best_response', 'temptation_gap',
                  'defensive_gap', 'deterrent_threshold', 'post_rule_gap',
                  'mixed_hawk_fraction', 'posterior', 'public_goods_gain',
                  'one_step_wait_margin', 'trust_net', 'reject_outside_payoff'):
        pick(lambda c, skill=skill: c.skill == skill)
    # Round-robin remaining examples across games to avoid a prefix bias.
    for index in range(32):
        if len(chosen) >= limit:
            break
        for game in dict.fromkeys(c.game for c in cases):
            remaining = [c for c in cases if c.game == game and c.case_id not in seen]
            if index < len(remaining):
                candidate = remaining[index]
                chosen.append(candidate)
                seen.add(candidate.case_id)
                if len(chosen) >= limit:
                    break
    return chosen


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def adapter_identity(path: Path) -> str:
    from tools.nanojev_gamer_v1_handoff import file_tree_sha256
    if not (path / 'adapter_config.json').is_file():
        raise ValueError('GAMER_ADAPTER_CONFIG_MISSING')
    if not any((path / f'adapter_model.{suffix}').is_file() for suffix in ('safetensors','bin')):
        raise ValueError('GAMER_ADAPTER_WEIGHTS_MISSING')
    return file_tree_sha256(path)


def cache_policy(allow_download: bool) -> None:
    if not allow_download:
        import os
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'


def wide_metrics(cases: list[Case], predictions: list[float], *, head_kind='wide') -> dict:
    """Bounded numerical-head diagnostic, emphasizing zero and signed offsets."""
    import math
    if len(cases) != len(predictions) or not cases:
        raise ValueError('WIDE_PREDICTION_COUNT_MISMATCH')
    stats = Counter()
    errors = []
    per_skill: dict[str, Counter] = {}
    per_sign: dict[str, Counter] = {}
    bad = []
    for case, pred in zip(cases,predictions):
        if not math.isfinite(pred):
            raise ValueError('WIDE_NONFINITE_PREDICTION')
        truth = float(case.expected)
        err = abs(pred-truth)
        group = ('zero' if truth == 0 else 'near_negative' if -1 <= truth < 0
                 else 'near_positive' if 0 < truth <= 1 else
                 'outer_negative' if truth < 0 else 'outer_positive')
        tolerance = .05 * max(1,abs(truth))
        correct = err <= tolerance
        exact = err <= 1e-6
        signed = (0 if abs(pred) <= .05 else (1 if pred>0 else -1))
        target_sign = 0 if truth == 0 else (1 if truth>0 else -1)
        for counter in (stats, per_skill.setdefault(case.skill,Counter()),
                        per_sign.setdefault(group,Counter())):
            counter['n']+=1
            counter['within_5pct']+=int(correct)
            counter['exact']+=int(exact)
            counter['correct_sign']+=int(signed==target_sign)
            counter['within_1']+=int(err<=1)
        errors.append(err)
        if not correct or signed!=target_sign:
            bad.append({'id':case.case_id,'skill':case.skill,'target':fstr(case.expected),
                        'predicted':pred,'absolute_error':err,'class':group})
    errors.sort()
    def summarize(c: Counter) -> dict:
        return {'n':c['n'],'within_5pct':c['within_5pct'],
                'exact':c['exact'],'correct_sign':c['correct_sign'],
                'within_1':c['within_1']}
    if head_kind not in ('wide','expanded'):
        raise ValueError('INVALID_HEAD_KIND')
    passed=stats['within_5pct']==stats['n'] and stats['correct_sign']==stats['n']
    return {'status':('PASS_BOUNDED_' if passed else 'FAIL_BOUNDED_') +
            ('WIDE' if head_kind=='wide' else 'EXPANDED'),
            'scope':('FROZEN_GAMER_LORA_AND_C0_WIDE_NUMERIC_HEAD' if head_kind=='wide'
                     else 'FROZEN_GAMER_LORA_AND_100_VECTOR_EXPANDED_HEAD'),
            'total':stats['n'], 'within_5pct':stats['within_5pct'],
            'exact':stats['exact'], 'correct_sign':stats['correct_sign'],
            'within_1':stats['within_1'],
            'mean_absolute_error':sum(errors)/len(errors),
            'median_absolute_error':errors[(len(errors)-1)//2],
            'p90_absolute_error':errors[(9*(len(errors)-1)+9)//10],
            'by_skill':{k:summarize(v) for k,v in sorted(per_skill.items())},
            'by_signed_class':{k:summarize(v) for k,v in sorted(per_sign.items())},
            'failures':bad}


def wide_predictions(args: argparse.Namespace, cases: list[Case], expected_adapter_sha: str) -> list[float]:
    """Use the exact production Gamer input path: frozen Qwen/LoRA hidden states
    from the original question, then the Wide signed-log numerical head.
    No LoRA generation or source-label extraction is used for predictions.
    """
    import torch
    from tools.nanojev_gamer_v1_handoff import model_components
    from tools.nanojev_qwen_c0_compressive_train import VARIANTS, capture_features, make_network, eval_heads
    if not args.wide_checkpoint or not args.wide_checkpoint.is_file():
        raise FileNotFoundError('WIDE_MODE_REQUIRES_EXISTING_WIDE_PT')
    cp = torch.load(args.wide_checkpoint, map_location='cpu', weights_only=True)
    if (cp.get('variant') != 'wide' or cp.get('hidden') != list(VARIANTS['wide'])
        or cp.get('normalization') != 'signed_log1p_exact_target'):
        raise ValueError('WIDE_ARCHITECTURE_OR_NORMALIZATION_MISMATCH')
    if (cp.get('frozen_adapter_sha256') != expected_adapter_sha or
        cp.get('backbone_model') != args.model_id):
        raise ValueError('WIDE_CHECKPOINT_FROZEN_BACKBONE_MISMATCH')
    if not isinstance(cp.get('model'),dict) or not isinstance(cp.get('input_width'),int):
        raise ValueError('WIDE_CHECKPOINT_INVALID')
    torch, tok, model = model_components(args.model_id,args.device,adapter_dir=args.adapter_dir)
    for case in cases:
        if len(tok.encode(case.prompt,add_special_tokens=True)) > args.max_input_tokens:
            raise ValueError('WIDE_PROMPT_WOULD_BE_TRUNCATED: '+case.case_id)
    cap_args = argparse.Namespace(device=args.device, capture_batch=args.capture_batch,
                                  max_length=args.max_input_tokens)
    features = capture_features(torch, model, tok,
                                [{'prompt':case.prompt} for case in cases], cap_args)
    if features.shape[1] != cp['input_width']:
        raise ValueError('WIDE_FEATURE_INPUT_WIDTH_MISMATCH')
    net = make_network(torch,cp['input_width'],VARIANTS['wide']).to(args.device)
    net.load_state_dict(cp['model'],strict=True)
    predicted, _ = eval_heads(torch,net,features,None,0.,1.,args.device,args.wide_batch)
    return [float(v) for v in predicted]


def resolve_model_paths(args: argparse.Namespace) -> None:
    """Read-only discovery with identity binding, never an arbitrary adapter guess."""
    if not getattr(args,'run',None) or args.mode not in ('wide','expanded'):
        return
    root=Path(args.run)
    staircase=root/'staircase'
    cycles=sorted(staircase.glob('head_cycle_*'),reverse=True) if staircase.is_dir() else []
    candidates=cycles+[root]
    if args.mode=='wide':
        if args.adapter_dir and args.wide_checkpoint:
            return
        for cycle in candidates:
            head=cycle/'to_numerical'/'wide.pt'
            adapter=cycle/'lora'/'best_adapter'
            if head.is_file() and adapter.is_dir():
                if not args.wide_checkpoint: args.wide_checkpoint=head
                if not args.adapter_dir: args.adapter_dir=adapter
                return
        raise FileNotFoundError('NO_COMPLETED_WIDE_CHECKPOINT_UNDER_RUN: '+str(root)+
            ' (look for staircase/head_cycle_NNNN/to_numerical/wide.pt)')
    if args.mode=='expanded' and not args.adapter_dir:
        if not args.expanded_run:
            raise ValueError('EXPANDED_MODE_REQUIRES_EXPANDED_RUN')
        path=args.expanded_run/'provenance.json'
        if not path.is_file():
            raise FileNotFoundError('EXPANDED_PROVENANCE_NOT_FOUND: '+str(path))
        source=json.loads(path.read_text(encoding='utf-8'))
        fingerprint=source.get('adapter_sha256')
        for cycle in candidates:
            adapter=cycle/'lora'/'best_adapter'
            if (adapter.is_dir() and (adapter/'adapter_config.json').is_file()
                and any((adapter/f'adapter_model.{ext}').is_file() for ext in ('bin','safetensors'))
                and adapter_identity(adapter)==fingerprint):
                args.adapter_dir=adapter
                return
        raise FileNotFoundError('EXPANDED_MATCHING_FROZEN_ADAPTER_NOT_FOUND_UNDER_RUN: '+str(root))


def expanded_metadata(run: Path, adapter_sha: str, model_id: str) -> dict:
    """Verify exported 100-vector run metadata before loading costly model weights.

    A legacy `wide.pt` is NOT interchangeable with `best_model.pt`. This
    mode intentionally uses the head-100 token capture and architecture.
    """
    from tools.nanojev_gamer_v1_expanded_head import SCHEMA
    run=Path(run)
    metadata=run/'provenance.json'
    weights=run/'best_model.pt'
    if not weights.is_file():
        raise FileNotFoundError('EXPANDED_BEST_MODEL_NOT_FOUND: '+str(weights))
    if not metadata.is_file():
        raise FileNotFoundError('EXPANDED_PROVENANCE_NOT_FOUND: '+str(metadata))
    source=json.loads(metadata.read_text(encoding='utf-8'))
    hp=source.get('configuration') or {}
    if (source.get('schema')!=SCHEMA or source.get('adapter_sha256')!=adapter_sha
        or source.get('model')!=model_id):
        raise ValueError('EXPANDED_FROZEN_ADAPTER_OR_MODEL_MISMATCH')
    if (hp.get('samples')!=100 or hp.get('input_width')!=102400 or
        hp.get('feature_policy')!='100_NORMALIZED_EXPONENTIAL_FULL_TOKEN_HIDDEN_LATEST_LAYER' or
        not isinstance(hp.get('hidden'),list) or len(hp['hidden'])!=4 or
        any(not isinstance(v,int) or v < 1 for v in hp['hidden']) or
        not isinstance(hp.get('max_length'),int) or hp['max_length']<1 or
        not isinstance(hp.get('curvature'),(float,int))):
        raise ValueError('EXPANDED_FEATURE_CONTRACT_MISMATCH')
    return source


def expanded_predictions(args: argparse.Namespace, cases: list[Case], expected_adapter_sha: str) -> list[float]:
    """Evaluate frozen 100-vector head on real original-prompt LoRA activations.

    Capture first; release Qwen from GPU before loading the ~419M-parameter
    100-vector head.  No training, no oracle supplied to the model.
    """
    import gc
    import numpy as np
    import torch
    from tools.nanojev_gamer_v1_handoff import model_components
    from tools.nanojev_gamer_v1_expanded_head import (_capture, make_expanded_head, SCHEMA)
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    source=expanded_metadata(args.expanded_run, expected_adapter_sha, args.model_id)
    hp=source['configuration']
    maximum=min(args.max_input_tokens,hp['max_length'])
    _,tok,model=model_components(args.model_id,args.device,adapter_dir=args.adapter_dir)
    model.eval()
    features, capture_info=_capture(torch,model,tok,
        [{'prompt_08':c.prompt} for c in cases],device=args.device,
        max_length=maximum,batch_size=args.capture_batch,
        samples=100,curvature=hp['curvature'])
    if features.shape!=(len(cases),hp['input_width']):
        raise ValueError('EXPANDED_FEATURE_CAPTURE_SHAPE_MISMATCH')
    del model,tok
    gc.collect()
    if torch.cuda.is_available(): torch.cuda.empty_cache()
    checkpoint_path=Path(args.expanded_run)/'best_model.pt'
    fingerprint=file_sha(checkpoint_path)
    state=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
    if (state.get('schema')!=SCHEMA or state.get('identity')!=source or
        not isinstance(state.get('model'),dict) or not isinstance(state.get('step'),int)):
        raise ValueError('EXPANDED_CHECKPOINT_LINEAGE_MISMATCH')
    net=make_expanded_head(torch,hp['input_width'],tuple(hp['hidden']))
    net.load_state_dict(state['model'],strict=True)
    del state
    gc.collect()
    if args.device.startswith('cuda'):
        net=net.to(args.device,dtype=torch.float16)
        dtype=torch.float16
    else:
        net=net.to(args.device,dtype=torch.float32)
        dtype=torch.float32
    net.eval()
    output=[]
    with torch.inference_mode():
        for begin in range(0,len(cases),args.wide_batch):
            part=torch.as_tensor(features[begin:begin+args.wide_batch],
                                 device=args.device,dtype=dtype)
            raw,_=net(part)
            prediction=inverse_signed_log(raw.float().cpu().numpy())
            output.extend(float(x) for x in prediction)
    if file_sha(checkpoint_path)!=fingerprint:
        raise ValueError('EXPANDED_CHECKPOINT_CHANGED_DURING_PROBE')
    if adapter_identity(args.adapter_dir)!=expected_adapter_sha:
        raise ValueError('EXPANDED_FROZEN_ADAPTER_CHANGED_DURING_PROBE')
    return output


def live_responses(args: argparse.Namespace, cases: list[Case]) -> dict[str, str]:
    if not args.adapter_dir or not args.adapter_dir.is_dir():
        raise ValueError('LIVE_MODE_REQUIRES_EXISTING_FROZEN_ADAPTER_DIR')
    # Existing Gamer loader preserves the backbone + PEFT inference contract.
    # Neither the tokenizer nor the model is downloaded unless already cached
    # by the standard loader. Model identity is always explicit in the report.
    cache_policy(args.allow_model_download)
    from tools.nanojev_gamer_v1_handoff import model_components
    torch, tok, model = model_components(args.model_id, args.device,
                                        adapter_dir=args.adapter_dir)
    model.eval()
    replies = {}
    with torch.inference_mode():
        for i, case in enumerate(cases, 1):
            enc = tok(case.prompt, return_tensors='pt', truncation=False)
            if enc['input_ids'].shape[1] > args.max_input_tokens:
                raise ValueError('LIVE_INPUT_EXCEEDS_MAX_TOKENS: '+case.case_id)
            inputs = {k:v.to(args.device) for k,v in enc.items()}
            output = model.generate(**inputs, do_sample=False,
                                    max_new_tokens=args.max_new_tokens,
                                    pad_token_id=tok.pad_token_id,
                                    eos_token_id=tok.eos_token_id)
            reply = tok.decode(output[0, enc['input_ids'].shape[1]:],
                               skip_special_tokens=True).strip()
            replies[case.case_id] = reply
            print(f'NUMERIC_SMOKE {i}/{len(cases)} {case.case_id}: '
                  f'expected={fstr(case.expected)} got={reply[:120]!r}', flush=True)
    return replies


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode', choices=('oracle', 'score', 'live', 'wide', 'expanded'), default='oracle')
    ap.add_argument('--deck', type=Path, help='Write a NEW, immutable JSONL problem deck')
    ap.add_argument('--responses', type=Path, help='JSONL: {id, answer, deck_sha256} for --mode score')
    ap.add_argument('--report', type=Path, help='Write a NEW JSON diagnostic report')
    ap.add_argument('--model-id', default='Qwen/Qwen3-0.6B')
    ap.add_argument('--run', type=Path, help='Gamer source run; automatically find matching head-cycle adapter and checkpoint')
    ap.add_argument('--adapter-dir', type=Path, help='Frozen PEFT adapter dir for --mode live or wide')
    ap.add_argument('--wide-checkpoint', type=Path, help='Gamer staircase/head_cycle_NNNN/to_numerical/wide.pt for --mode wide')
    ap.add_argument('--expanded-run', type=Path, help='Output directory containing head-100 best_model.pt and provenance.json')
    ap.add_argument('--device', default='cpu')
    ap.add_argument('--allow-model-download', action='store_true',
                    help='Otherwise live mode requires existing locally cached model files')
    ap.add_argument('--limit', type=int, default=24,
                    help='Live only: number of cases; 0 means the full deck')
    ap.add_argument('--max-input-tokens', type=int, default=1024)
    ap.add_argument('--max-new-tokens', type=int, default=64)
    ap.add_argument('--capture-batch', type=int, default=4)
    ap.add_argument('--wide-batch', type=int, default=32)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if (args.limit < 0 or args.max_input_tokens < 1 or args.max_new_tokens < 1
        or args.capture_batch < 1 or args.wide_batch < 1):
        raise ValueError('INVALID_PROBE_LIMIT')
    resolve_model_paths(args)
    cases = build_cases()
    oracle = validate_oracle(cases)
    fingerprint = deck_sha(cases)
    report = {'schema':SCHEMA, 'deck_sha256':fingerprint, 'oracle':oracle,
              'mode':args.mode, 'model_status':'NOT_EVALUATED'}
    if args.deck is not None:
        write_deck(args.deck, cases)
        report['deck'] = str(args.deck)
    if args.mode == 'score':
        if args.responses is None:
            raise ValueError('SCORE_MODE_REQUIRES_RESPONSES')
        report['model'] = score(cases, read_responses(args.responses, fingerprint))
        report['model_status'] = report['model']['status']
    elif args.mode in ('live', 'wide', 'expanded'):
        if not args.adapter_dir or not args.adapter_dir.is_dir():
            raise ValueError('MODEL_MODE_REQUIRES_EXISTING_ADAPTER_DIR')
        selected = select_live_cases(cases, args.limit)
        original_sha = adapter_identity(args.adapter_dir)
        cache_policy(args.allow_model_download)
        if args.mode == 'live':
            report['model'] = score(selected,live_responses(args,selected))
            status = report['model']['status']
            if status == 'PASS_MODEL' and len(selected) < len(cases):
                status = 'PASS_BOUNDED_MODEL'
        else:
            if args.mode=='wide' and (not args.wide_checkpoint or not args.wide_checkpoint.is_file()):
                raise FileNotFoundError('WIDE_MODE_REQUIRES_EXISTING_WIDE_PT: '+str(args.wide_checkpoint)+
                    ' (look in RUN/staircase/head_cycle_NNNN/to_numerical/wide.pt; '+
                    'pair it with the matching lora/best_adapter)')
            if args.mode=='expanded' and not args.expanded_run:
                raise ValueError('EXPANDED_MODE_REQUIRES_EXPANDED_RUN')
            if args.mode=='wide':
                wide_sha = file_sha(args.wide_checkpoint)
                predictions=wide_predictions(args,selected,original_sha)
                report['model']=wide_metrics(selected,predictions,head_kind='wide')
                if file_sha(args.wide_checkpoint) != wide_sha:
                    raise ValueError('WIDE_CHECKPOINT_CHANGED_DURING_PROBE')
                report['wide_checkpoint_sha256']=wide_sha
                report['wide_checkpoint']=str(args.wide_checkpoint.resolve())
            else:
                predictions=expanded_predictions(args,selected,original_sha)
                report['model']=wide_metrics(selected,predictions,head_kind='expanded')
                report['expanded_run']=str(args.expanded_run.resolve())
                report['expanded_checkpoint_sha256']=file_sha(args.expanded_run/'best_model.pt')
            status = report['model']['status']
        if adapter_identity(args.adapter_dir) != original_sha:
            raise ValueError('FROZEN_ADAPTER_CHANGED_DURING_PROBE')
        report['model_status'] = status
        report['model_id'] = args.model_id
        report['adapter_sha256'] = original_sha
        report['adapter_dir'] = str(args.adapter_dir.resolve())
        report['live_subset'] = len(selected)
        report['unmeasured_cases'] = len(cases)-len(selected)
        report['live_skills'] = dict(sorted(Counter(c.skill for c in selected).items()))
        report['live_games'] = sorted({c.game for c in selected})
        report['live_zero_answers'] = sum(c.expected == 0 for c in selected)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if args.report.exists():
            raise FileExistsError('REPORT_ALREADY_EXISTS: '+str(args.report))
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, sort_keys=True, ensure_ascii=False), flush=True)
    return 0 if report['model_status'] in ('NOT_EVALUATED','PASS_MODEL','PASS_BOUNDED_MODEL','PASS_BOUNDED_WIDE','PASS_BOUNDED_EXPANDED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
