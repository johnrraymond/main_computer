"""Shared, captain-independent scenarios for the crossed Hawk–Dove smoke.

A scenario supplies the same question, context, and categorical state to all
captains. No scoring weights are hidden in the narrative. The teachers are
synthetic and do not establish the optimal decision in the real game.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    asset: str
    context: str
    question: str
    default_state: tuple[tuple[str, str], ...]


SCENARIOS = (
    Scenario(
        key='s01', title='Contested supply cache', asset='supply cache',
        context='A rival vessel is contesting control of a supply cache.',
        question='Should this captain contest the supply cache or yield it?',
        default_state=(
            ('resource_priority', 'low'), ('crew_danger', 'low'),
            ('mission_dependency', 'off'), ('friendly_support', 'in-range'),
            ('decision_horizon', 'soon'), ('opponent_pressure', 'medium'),
            ('opponent_escalating', 'on'), ('opponent_support', 'out-of-range'),
        ),
    ),
    Scenario(
        key='s02', title='Disputed emergency docking corridor', asset='emergency docking corridor',
        context='A rival vessel is contesting access to an emergency docking corridor.',
        question='Should this captain contest access to the docking corridor or yield it?',
        default_state=(
            ('resource_priority', 'high'), ('crew_danger', 'high'),
            ('mission_dependency', 'on'), ('friendly_support', 'out-of-range'),
            ('decision_horizon', 'now'), ('opponent_pressure', 'medium'),
            ('opponent_escalating', 'on'), ('opponent_support', 'in-range'),
        ),
    ),
    Scenario(
        key='s03', title='Contested sensor relay', asset='sensor relay',
        context='A rival vessel is contesting control of a sensor relay.',
        question='Should this captain contest the sensor relay or yield it?',
        default_state=(
            ('resource_priority', 'low'), ('crew_danger', 'low'),
            ('mission_dependency', 'off'), ('friendly_support', 'out-of-range'),
            ('decision_horizon', 'later'), ('opponent_pressure', 'low'),
            ('opponent_escalating', 'off'), ('opponent_support', 'in-range'),
        ),
    ),
)
BY_ID = {s.key: s for s in SCENARIOS}
assert len(BY_ID) == len(SCENARIOS)
