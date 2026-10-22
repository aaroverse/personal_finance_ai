# SQLite 초안 검토와 확정 저장의 원자성을 테스트합니다.

from datetime import date
from pathlib import Path

import pytest

from finance_bot.database import DraftNotPendingError, FinanceRepository
from finance_bot.models import ExpenseExtraction


def expense() -> ExpenseExtraction:
    return ExpenseExtraction(
        transaction_date=date(2026, 10, 22),
        amount="8.90",
        currency="MYR",
        merchant="Mamak",
        category="dining",
        payment_account="MY Maybank",
        note=None,
        confidence=0.98,
        needs_review=False,
        ambiguities=[],
    )


def repository(tmp_path: Path) -> FinanceRepository:
    repo = FinanceRepository(tmp_path / "finance.db")
    repo.initialize()
    return repo


def test_confirm_moves_one_draft_to_one_transaction(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft = repo.create_draft(42, "mamak RM8.90 maybank", expense())

    transaction = repo.confirm_draft(draft.id, 42)

    assert transaction.draft_id == draft.id
    assert transaction.expense.amount == expense().amount
    assert repo.get_draft(draft.id).status == "confirmed"
    assert repo.count_transactions() == 1

    with pytest.raises(DraftNotPendingError):
        repo.confirm_draft(draft.id, 42)
    assert repo.count_transactions() == 1


def test_other_user_cannot_confirm_or_cancel_draft(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft = repo.create_draft(42, "private expense", expense())

    with pytest.raises(DraftNotPendingError):
        repo.confirm_draft(draft.id, 84)
    with pytest.raises(DraftNotPendingError):
        repo.cancel_draft(draft.id, 84)

    assert repo.get_draft(draft.id).status == "pending"
    assert repo.count_transactions() == 0


def test_cancelled_draft_is_not_persisted_as_transaction(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft = repo.create_draft(42, "cancel this", expense())

    cancelled = repo.cancel_draft(draft.id, 42)

    assert cancelled.status == "cancelled"
    assert repo.count_transactions() == 0
