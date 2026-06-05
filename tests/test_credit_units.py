from __future__ import annotations

import pytest

from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT,
    CreditUnitError,
    credit_decimal_text_to_wei,
    credit_wei_product,
    credit_wei_to_decimal_text,
)


def test_credit_decimal_text_to_wei_is_exact_for_small_fractional_prices() -> None:
    assert credit_decimal_text_to_wei("0.001") == 1_000_000_000_000_000
    assert credit_decimal_text_to_wei("1.024") == 1_024_000_000_000_000_000
    assert credit_wei_to_decimal_text(1_024_000_000_000_000_000) == "1.024"


def test_credit_wei_product_uses_integer_math_for_large_authorizations() -> None:
    tokens = 128_000
    per_token = credit_decimal_text_to_wei("1000000")
    total = credit_wei_product(tokens, per_token)
    assert total == 128_000 * 1_000_000 * CREDIT_WEI_PER_CREDIT
    assert credit_wei_to_decimal_text(total) == "128000000000"


def test_credit_decimal_text_to_wei_rounds_up_sub_wei_amounts_when_allowed() -> None:
    assert credit_decimal_text_to_wei("0.0000000000000000001") == 1
    with pytest.raises(CreditUnitError):
        credit_decimal_text_to_wei("0.0000000000000000001", round_up=False)
