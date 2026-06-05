from __future__ import annotations

"""Exact compute-credit unit helpers.

The ledger stores fractional request pricing as integer atomic units so Python
never depends on floats and the browser can use BigInt. Human decimal credit
strings only live at API/UI boundaries.
"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any


CREDIT_WEI_PER_CREDIT = 10**18
CREDIT_WEI_DECIMALS = 18


class CreditUnitError(ValueError):
    """Raised when a credit amount cannot be represented safely."""


def positive_credit_wei(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


def credit_count_to_wei(credits: Any) -> int:
    try:
        parsed = int(credits)
    except (TypeError, ValueError):
        parsed = 0
    return max(0, parsed) * CREDIT_WEI_PER_CREDIT


def credit_wei_to_whole_credits_floor(credit_wei: Any) -> int:
    return positive_credit_wei(credit_wei) // CREDIT_WEI_PER_CREDIT


def credit_wei_to_decimal_text(credit_wei: Any) -> str:
    amount = positive_credit_wei(credit_wei)
    whole, remainder = divmod(amount, CREDIT_WEI_PER_CREDIT)
    if remainder == 0:
        return str(whole)
    frac = str(remainder).rjust(CREDIT_WEI_DECIMALS, "0").rstrip("0")
    return f"{whole}.{frac}"


def credit_decimal_text_to_wei(
    value: Any,
    *,
    default: str = "0",
    minimum_wei: int | None = None,
    maximum_wei: int | None = None,
    round_up: bool = True,
) -> int:
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        number = Decimal(str(default))
    scaled = number * Decimal(CREDIT_WEI_PER_CREDIT)
    if scaled != scaled.to_integral_value():
        if not round_up:
            raise CreditUnitError(f"Credit amount has more than {CREDIT_WEI_DECIMALS} decimal places: {value!r}")
        scaled = scaled.to_integral_value(rounding=ROUND_CEILING)
    amount = int(scaled)
    if minimum_wei is not None:
        amount = max(int(minimum_wei), amount)
    if maximum_wei is not None:
        amount = min(int(maximum_wei), amount)
    return max(0, amount)


def credit_wei_product(tokens: Any, credits_per_token_wei: Any) -> int:
    try:
        clean_tokens = int(tokens)
    except (TypeError, ValueError):
        clean_tokens = 0
    return max(0, clean_tokens) * positive_credit_wei(credits_per_token_wei)
