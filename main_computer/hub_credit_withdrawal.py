from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from main_computer.hub_credit_models import positive_int


@dataclass(frozen=True)
class BridgeWithdrawalReconciliation:
    """Pure requester withdrawal reconciliation for bridge escrow accounting.

    The hub ledger remains the durable source for finalized private spend and
    active holds.  The escrow contract remains the durable source for already
    rectified spend and already released withdrawals.
    """

    deposit_units: int
    finalized_spend_units: int
    active_hold_units: int
    already_rectified_units: int
    unrectified_units: int
    already_withdrawn_units: int
    withdrawable_units: int
    can_withdraw: bool
    block_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_bridge_withdrawal_reconciliation(
    *,
    deposit_units: int,
    finalized_spend_units: int,
    active_hold_units: int = 0,
    already_rectified_units: int = 0,
    already_withdrawn_units: int = 0,
) -> BridgeWithdrawalReconciliation:
    """Return the exact withdrawal checkpoint state for one requester.

    All values are integer credit/base units.  ``finalized_spend_units`` must be
    derived from completed charges only, not active holds, budgets, leased work,
    or pending requests.
    """

    deposit = positive_int(deposit_units)
    finalized = positive_int(finalized_spend_units)
    active_holds = positive_int(active_hold_units)
    rectified = positive_int(already_rectified_units)
    withdrawn = positive_int(already_withdrawn_units)

    block_reason = ""
    unrectified = 0
    withdrawable = 0

    if active_holds > 0:
        block_reason = "active holds block withdrawal reconciliation"
    elif rectified > finalized:
        block_reason = "contract rectified spend exceeds hub finalized spend"
    elif withdrawn > deposit:
        block_reason = "contract withdrawn units exceed deposited units"
    elif finalized + withdrawn > deposit:
        block_reason = "hub finalized spend plus contract withdrawals exceed deposited units"
    else:
        unrectified = finalized - rectified
        withdrawable = deposit - finalized - withdrawn
        if withdrawable <= 0:
            block_reason = "no withdrawable balance remains"

    return BridgeWithdrawalReconciliation(
        deposit_units=deposit,
        finalized_spend_units=finalized,
        active_hold_units=active_holds,
        already_rectified_units=rectified,
        unrectified_units=unrectified,
        already_withdrawn_units=withdrawn,
        withdrawable_units=withdrawable,
        can_withdraw=not block_reason,
        block_reason=block_reason,
    )


def sum_finalized_charge_units(charges: list[dict[str, Any]]) -> int:
    """Sum finalized charge rows exactly in integer units."""

    total = 0
    for charge in charges:
        if not isinstance(charge, dict):
            continue
        total += positive_int(charge.get("charged_credits"))
    return total


def sum_active_hold_units(holds: list[dict[str, Any]]) -> int:
    """Sum active held rows exactly in integer units."""

    total = 0
    for hold in holds:
        if not isinstance(hold, dict):
            continue
        if str(hold.get("status", "")).strip() == "held":
            total += positive_int(hold.get("credits"))
    return total
