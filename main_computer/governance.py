from __future__ import annotations

from typing import Any


def bridge_governance_status() -> dict[str, Any]:
    return {
        "model": "xlag-byzantine-bridge-governance",
        "council_size": 4,
        "offices": {
            "O0": {"title": "Captain", "location": 0, "compartment": "Alpha"},
            "O1": {"title": "First Officer", "location": 1, "compartment": "Alpha"},
            "O2": {"title": "Second Officer", "location": 2, "compartment": "Beta"},
            "O3": {"title": "Third Officer", "location": 3, "compartment": "Beta"},
        },
        "compartments": {
            "Alpha": [0, 1],
            "Beta": [2, 3],
        },
        "adversary": {
            "any_pair_can_collude": True,
            "cross_compartment_collusion_possible": True,
        },
        "safety": {
            "no_pair_can_execute": True,
            "no_single_office_can_execute": True,
            "any_office_can_contest": True,
            "contested_default": "non-execution",
            "outside_consensus_required_on_contest": True,
        },
        "execution": {
            "reserve_execution_enabled": False,
            "native_transfer_enabled": False,
            "wallet_mapping_enabled": False,
        },
        "bridge_order_flow": {
            "model": "xlag-bridge-order-flow",
            "captain_touches_computer_required": False,
            "first_officer_touches_computer_required": False,
            "console_operator_role": "conn",
            "seconding_station": "helm",
            "states": [
                "CAPTAIN_INTENT",
                "CONN_RELAY",
                "BELAY_WINDOW",
                "HELM_SECOND",
                "EXECUTION_ELIGIBLE",
                "HELD",
            ],
            "rules": [
                "Captain gives command intent.",
                "Conn relays command into the computer.",
                "First Officer may belay.",
                "If not belayed, Helm may second/carry.",
                "Computer records authority path separately from keyboard operator.",
                "Console handoff does not transfer council authority.",
            ],
            "execution": {
                "direct_execution_enabled": False,
                "serious_outcomes_require_governance": True,
            },
        },
        "invariants": [
            "Alpha/Beta is grammar, not trust.",
            "Any pair of offices can collude.",
            "No pair can execute serious outcomes.",
            "No single office can execute.",
            "Any single office can contest.",
            "Contest freezes internal execution.",
            "Contest requires outside consensus.",
            "Default under contest is non-execution.",
            "Console handoff does not transfer council authority.",
        ],
    }
