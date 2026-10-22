# 서비스 계층이 AI 호출 전에 허용 목록을 강제하는지 테스트합니다.

from datetime import date
from pathlib import Path

import pytest

from finance_bot.config import Settings
from finance_bot.database import FinanceRepository
from finance_bot.models import ExpenseExtraction
from finance_bot.service import FinanceService, UnauthorizedUserError


class FakeExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, text: str, today: date) -> ExpenseExtraction:
        self.calls.append(text)
        return ExpenseExtraction(
            transaction_date=today,
            amount="4.20",
            currency="SGD",
            merchant="MRT",
            category="transport",
            payment_account="SG DBS",
            note=None,
            confidence=0.99,
            needs_review=False,
            ambiguities=[],
        )


def make_service(tmp_path: Path) -> tuple[FinanceService, FinanceRepository, FakeExtractor]:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="123456:valid-test-token",
        telegram_allowed_user_ids="42",
        deepseek_api_key="sk-valid-test-key",
    )
    repository = FinanceRepository(tmp_path / "finance.db")
    repository.initialize()
    extractor = FakeExtractor()
    service = FinanceService(settings, repository, extractor)  # type: ignore[arg-type]
    return service, repository, extractor


@pytest.mark.asyncio
async def test_unauthorized_user_never_reaches_ai_or_database(tmp_path: Path) -> None:
    service, repository, extractor = make_service(tmp_path)

    with pytest.raises(UnauthorizedUserError):
        await service.submit_expense(99, "private message")

    assert extractor.calls == []
    assert repository.count_transactions() == 0


@pytest.mark.asyncio
async def test_submit_revise_and_confirm_flow(tmp_path: Path) -> None:
    service, repository, extractor = make_service(tmp_path)

    draft = await service.submit_expense(42, "MRT 4.20")
    revised = await service.revise_expense(42, draft.id, "Yesterday MRT 4.20 DBS")
    saved = service.confirm(42, revised.id)

    assert extractor.calls == ["MRT 4.20", "Yesterday MRT 4.20 DBS"]
    assert saved.expense.merchant == "MRT"
    assert repository.count_transactions() == 1
