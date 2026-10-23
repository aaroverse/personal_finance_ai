# 권한 검사와 지출 검토 생명주기를 하나의 서비스로 조정합니다.

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .database import DraftNotPendingError, DraftRecord, FinanceRepository, TransactionRecord, RecordConflictError
from .extractor import DeepSeekExpenseExtractor


class UnauthorizedUserError(PermissionError):
    """Raised before any finance data or AI request is accessed."""


class FinanceService:
    def __init__(
        self,
        settings: Settings,
        repository: FinanceRepository,
        extractor: DeepSeekExpenseExtractor,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._extractor = extractor

    def is_allowed(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self._settings.allowed_user_ids

    async def submit_expense(self, telegram_user_id: int, text: str) -> DraftRecord:
        self._require_allowed(telegram_user_id)
        today = datetime.now(ZoneInfo(self._settings.app_timezone)).date()
        expense = await self._extractor.extract(text, today)
        return self._repository.create_draft(telegram_user_id, text.strip(), expense)

    async def revise_expense(
        self, telegram_user_id: int, draft_id: int, text: str
    ) -> DraftRecord:
        draft = self._require_owned_pending_draft(telegram_user_id, draft_id)
        today = datetime.now(ZoneInfo(self._settings.app_timezone)).date()
        expense = await self._extractor.extract(text, today)
        expense.is_personal = draft.expense.is_personal
        return self._repository.update_draft(
            draft_id, telegram_user_id, text.strip(), expense, draft.revision
        )

    def prepare_edit(self, telegram_user_id: int, draft_id: int, revision: str | None = None) -> DraftRecord:
        draft = self._require_owned_pending_draft(telegram_user_id, draft_id)
        if revision is not None and draft.revision[:len(revision)] != revision:
            raise RecordConflictError("Draft changed since it was displayed.")
        return draft

    def confirm(self, telegram_user_id: int, draft_id: int, revision: str | None = None) -> TransactionRecord:
        self._require_allowed(telegram_user_id)
        return self._repository.confirm_draft(draft_id, telegram_user_id, revision)

    def cancel(self, telegram_user_id: int, draft_id: int, revision: str | None = None) -> DraftRecord:
        self._require_allowed(telegram_user_id)
        return self._repository.cancel_draft(draft_id, telegram_user_id, revision)

    def recent(self, telegram_user_id: int, limit: int = 10) -> list[TransactionRecord]:
        self._require_allowed(telegram_user_id)
        return self._repository.recent_transactions(telegram_user_id, limit)

    def _require_allowed(self, telegram_user_id: int) -> None:
        if not self.is_allowed(telegram_user_id):
            raise UnauthorizedUserError("Telegram user is not allowlisted.")

    def _require_owned_pending_draft(
        self, telegram_user_id: int, draft_id: int
    ) -> DraftRecord:
        self._require_allowed(telegram_user_id)
        draft = self._repository.get_draft(draft_id)
        if draft.telegram_user_id != telegram_user_id or draft.status != "pending":
            raise DraftNotPendingError("Draft is not pending or does not belong to this user.")
        return draft
