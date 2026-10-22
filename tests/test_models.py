# 지출 구조의 금액과 통화 검증 규칙을 테스트합니다.

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finance_bot.models import ExpenseExtraction


def valid_expense(**overrides: object) -> ExpenseExtraction:
    values: dict[str, object] = {
        "transaction_date": date(2026, 10, 22),
        "amount": "12.50",
        "currency": "sgd",
        "merchant": "Kopitiam",
        "category": "dining",
        "payment_account": "SG DBS",
        "note": None,
        "confidence": 0.95,
        "needs_review": False,
        "ambiguities": [],
    }
    values.update(overrides)
    return ExpenseExtraction.model_validate(values)


def test_normalizes_currency_and_preserves_decimal() -> None:
    expense = valid_expense()

    assert expense.currency == "SGD"
    assert expense.amount == Decimal("12.50")


@pytest.mark.parametrize("amount", ["0", "-2", "12.345"])
def test_rejects_invalid_amount(amount: str) -> None:
    with pytest.raises(ValidationError):
        valid_expense(amount=amount)


def test_rejects_extra_model_fields() -> None:
    with pytest.raises(ValidationError):
        valid_expense(unexpected="unsafe")
