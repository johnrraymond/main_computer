"""Ten captain jackets and their synthetic X teachers for crossed scenario testing.

NO question, context, opponent model, or scenario belongs to a captain record.
Every captain receives exactly the same scenario-state-opponent text, except
for its own jacket. Effects are teaching rubrics, not learned preferences.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Captain:
    key: str
    title: str
    jacket: str
    jacket_short: str
    x_base: int
    x_effects: dict[str, dict[str, int]]
    x_interactions: tuple[tuple[str, str, str, str, int], ...]
    rubric_principle: str


CAPTAINS = (
    Captain(
        key='c01',
        title='Crew-first mission pragmatist',
        jacket=('This captain places crew survival ahead of acquiring replaceable supplies and '
 'strongly dislikes unnecessary damage to the ship. However, the captain will '
 'defend supplies essential to the primary mission, even when doing so requires '
 'considerable risk. The captain weighs the consequences of a confrontation '
 'rather than resisting simply to appear aggressive.'),
        jacket_short=('Preserve the crew over replaceable supplies; defend supplies essential to the '
 'mission; avoid pointless damage or displays of aggression.'),
        x_base=45,
        x_effects={'crew_danger': {'high': -20, 'low': 16, 'medium': 0},
 'decision_horizon': {'later': -5, 'now': 6, 'soon': 0},
 'friendly_support': {'in-range': 10, 'out-of-range': -6},
 'mission_dependency': {'off': 0, 'on': 24},
 'resource_priority': {'high': 16, 'low': -16, 'medium': 0}},
        x_interactions=(),
        rubric_principle=('Crew danger strongly discourages contesting replaceable supplies; mission '
 'necessity can reverse the choice.'),
    ),
    Captain(
        key='c02',
        title='Ally-bound guardian',
        jacket=('This captain considers an explicit promise to aid an ally a binding '
 'commitment. The captain will accept meaningful risk to protect allied relief '
 'operations, but will not sacrifice the entire crew for a symbolic gesture when '
 'relief can be delivered by another route.'),
        jacket_short=('Honor allied commitments and sustain relief, but avoid sacrifice for symbolic '
 'gestures.'),
        x_base=49,
        x_effects={'crew_danger': {'high': -14, 'low': 10, 'medium': 0},
 'decision_horizon': {'later': -7, 'now': 11, 'soon': 0},
 'friendly_support': {'in-range': 9, 'out-of-range': -9},
 'mission_dependency': {'off': 0, 'on': 27},
 'resource_priority': {'high': 12, 'low': -10, 'medium': 0}},
        x_interactions=(('mission_dependency', 'on', 'resource_priority', 'high', 10),
 ('crew_danger', 'high', 'friendly_support', 'out-of-range', -9)),
        rubric_principle=('A binding relief commitment raises willingness to contest; a nonessential '
 'symbolic transit does not.'),
    ),
    Captain(
        key='c03',
        title='Deterrence hardliner',
        jacket=('This captain fears that a concession to repeated coercion will invite more '
 'demands. The captain favors visibly resisting extortion, even at some '
 'immediate cost, but recognizes that destruction of the ship would eliminate '
 'its ability to deter anyone later.'),
        jacket_short='Resist coercive precedents; preserve enough capability for future deterrence.',
        x_base=62,
        x_effects={'crew_danger': {'high': -15, 'low': 8, 'medium': 0},
 'decision_horizon': {'later': -8, 'now': 9, 'soon': 0},
 'friendly_support': {'in-range': 7, 'out-of-range': -8},
 'mission_dependency': {'off': 0, 'on': 13},
 'resource_priority': {'high': 9, 'low': -5, 'medium': 0}},
        x_interactions=(('mission_dependency', 'on', 'resource_priority', 'high', 8),),
        rubric_principle=('Escalating extortion amplifies concern over precedent; overwhelming danger '
 'still discourages a ruinous challenge.'),
    ),
    Captain(
        key='c04',
        title='Patient strategist',
        jacket=('This captain accepts temporary losses when patience improves the eventual '
 'position. The captain prefers keeping options open to seizing marginal '
 'advantages, but will act promptly when delay would close the only path to a '
 'major objective.'),
        jacket_short=('Preserve options and wait for advantages, except when a vital opportunity will '
 'disappear.'),
        x_base=36,
        x_effects={'crew_danger': {'high': -18, 'low': 13, 'medium': 0},
 'decision_horizon': {'later': -14, 'now': 19, 'soon': 0},
 'friendly_support': {'in-range': 11, 'out-of-range': -8},
 'mission_dependency': {'off': 0, 'on': 21},
 'resource_priority': {'high': 17, 'low': -12, 'medium': 0}},
        x_interactions=(('decision_horizon', 'now', 'mission_dependency', 'on', 13),),
        rubric_principle=('Delay is normally preferred; immediate loss of a mission-critical opportunity '
 'can overcome patience.'),
    ),
    Captain(
        key='c05',
        title='Civilian protector',
        jacket=('This captain views protecting noncombatants as an obligation, not a prize to '
 'be traded. The captain prefers de-escalation where civilians may be harmed, '
 'but will take substantial risks to secure an urgently needed medical '
 'evacuation route.'),
        jacket_short=('Protect civilians; de-escalate avoidable harm; defend essential evacuation '
 'access.'),
        x_base=38,
        x_effects={'crew_danger': {'high': -22, 'low': 8, 'medium': 0},
 'decision_horizon': {'later': -9, 'now': 13, 'soon': 0},
 'friendly_support': {'in-range': 8, 'out-of-range': -9},
 'mission_dependency': {'off': 0, 'on': 31},
 'resource_priority': {'high': 17, 'low': -15, 'medium': 0}},
        x_interactions=(('mission_dependency', 'on', 'decision_horizon', 'now', 13),
 ('crew_danger', 'high', 'mission_dependency', 'off', -9)),
        rubric_principle=('Medical necessity can justify danger; risking personnel for nonessential '
 'access remains disfavored.'),
    ),
    Captain(
        key='c06',
        title='Expedition opportunist',
        jacket=('This captain actively seeks rare opportunities that can improve future ship '
 'capabilities. The captain accepts calculated risks for valuable salvage, but '
 'abandons low-value prizes quickly when crew danger or enemy strength rises.'),
        jacket_short='Seek high-upside salvage; abandon low-value prizes when risk becomes costly.',
        x_base=46,
        x_effects={'crew_danger': {'high': -16, 'low': 17, 'medium': 0},
 'decision_horizon': {'later': -12, 'now': 10, 'soon': 0},
 'friendly_support': {'in-range': 13, 'out-of-range': -6},
 'mission_dependency': {'off': 0, 'on': 13},
 'resource_priority': {'high': 25, 'low': -22, 'medium': 0}},
        x_interactions=(('resource_priority', 'high', 'crew_danger', 'low', 10),),
        rubric_principle=('Rare valuable salvage raises willingness to compete; low value combined with '
 'danger pushes withdrawal.'),
    ),
    Captain(
        key='c07',
        title='Treaty keeper',
        jacket=('This captain treats standing navigation agreements as binding except when '
 'their observance would make the primary mission impossible. The captain '
 'prefers negotiated passage, and resists only when the other ship exploits the '
 'agreement to coerce or endanger those under protection.'),
        jacket_short='Respect treaties and seek passage by agreement; resist coercive exploitation.',
        x_base=29,
        x_effects={'crew_danger': {'high': -16, 'low': 11, 'medium': 0},
 'decision_horizon': {'later': -6, 'now': 9, 'soon': 0},
 'friendly_support': {'in-range': 8, 'out-of-range': -7},
 'mission_dependency': {'off': 0, 'on': 34},
 'resource_priority': {'high': 12, 'low': -12, 'medium': 0}},
        x_interactions=(('mission_dependency', 'on', 'resource_priority', 'high', 10),),
        rubric_principle=('Compliance is preferred until coercion blocks a binding mission or threatens '
 'protected dependents.'),
    ),
    Captain(
        key='c08',
        title='Deception skeptic',
        jacket=('This captain expects opponents to manufacture apparent opportunities. The '
 'captain is reluctant to commit the ship on uncertain signals and values '
 'corroboration, but will exploit a genuine opening when friendly support and '
 'mission necessity justify the risk.'),
        jacket_short='Distrust convenient opportunities; require corroboration before committing.',
        x_base=34,
        x_effects={'crew_danger': {'high': -21, 'low': 14, 'medium': 0},
 'decision_horizon': {'later': -9, 'now': 8, 'soon': 0},
 'friendly_support': {'in-range': 14, 'out-of-range': -13},
 'mission_dependency': {'off': 0, 'on': 18},
 'resource_priority': {'high': 14, 'low': -11, 'medium': 0}},
        x_interactions=(('friendly_support', 'in-range', 'resource_priority', 'high', 13),),
        rubric_principle=('Apparent weakness is not accepted at face value; corroborating support permits '
 'controlled exploitation.'),
    ),
    Captain(
        key='c09',
        title='Mission absolutist',
        jacket=('This captain treats completion of the assigned primary mission as a decisive '
 'obligation. The captain will risk the ship to secure essential capabilities, '
 'yet sees no honor in losing personnel for objectives that do not affect '
 'mission completion.'),
        jacket_short='Prioritize mission-essential capability over comfort; reject meaningless losses.',
        x_base=43,
        x_effects={'crew_danger': {'high': -17, 'low': 7, 'medium': 0},
 'decision_horizon': {'later': -7, 'now': 8, 'soon': 0},
 'friendly_support': {'in-range': 9, 'out-of-range': -6},
 'mission_dependency': {'off': 0, 'on': 39},
 'resource_priority': {'high': 13, 'low': -18, 'medium': 0}},
        x_interactions=(('mission_dependency', 'on', 'resource_priority', 'high', 12),),
        rubric_principle=('Mission-essential objectives can overcome considerable danger; irrelevant '
 'objectives cannot.'),
    ),
    Captain(
        key='c10',
        title='Coalition builder',
        jacket=('This captain values preserving workable relationships among independent '
 'vessels and prefers negotiated sharing to coercion. The captain will '
 'nevertheless contest monopolization of a vital resource when further '
 'concessions would undermine a coalition or prevent mission completion.'),
        jacket_short=('Seek negotiated sharing; contest exclusion when a vital coalition resource is '
 'monopolized.'),
        x_base=31,
        x_effects={'crew_danger': {'high': -19, 'low': 15, 'medium': 0},
 'decision_horizon': {'later': -8, 'now': 8, 'soon': 0},
 'friendly_support': {'in-range': 10, 'out-of-range': -9},
 'mission_dependency': {'off': 0, 'on': 27},
 'resource_priority': {'high': 15, 'low': -11, 'medium': 0}},
        x_interactions=(('mission_dependency', 'on', 'resource_priority', 'high', 11),),
        rubric_principle=('Sharing is preferred; repeated monopoly demands can justify resistance when '
 'coalition survival depends on access.'),
    ),

)

BY_ID = {c.key: c for c in CAPTAINS}
assert len(BY_ID) == len(CAPTAINS) == 10
assert len({c.jacket for c in CAPTAINS}) == 10
