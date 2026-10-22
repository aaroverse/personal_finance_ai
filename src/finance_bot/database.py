# SQLite에서 검토 초안과 확정 거래를 원자적으로 저장합니다.

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import ExpenseExtraction


class DraftNotPendingError(RuntimeError):
    """Raised when a closed or missing draft is changed."""


@dataclass(frozen=True)
class DraftRecord:
    id: int
    telegram_user_id: int
    source_text: str
    expense: ExpenseExtraction
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TransactionRecord:
    id: int
    draft_id: int
    telegram_user_id: int
    source_text: str
    expense: ExpenseExtraction
    created_at: str


class FinanceRepository:
    def __init__(self, database_path: Path) -> None:
        self._path = database_path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    source_text TEXT NOT NULL,
                    expense_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'confirmed', 'cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id INTEGER NOT NULL UNIQUE REFERENCES drafts(id),
                    telegram_user_id INTEGER NOT NULL,
                    source_text TEXT NOT NULL,
                    expense_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_drafts_user_status
                    ON drafts(telegram_user_id, status);
                CREATE INDEX IF NOT EXISTS idx_transactions_user_date
                    ON transactions(telegram_user_id, created_at DESC);
                """
            )

    def create_draft(
        self, telegram_user_id: int, source_text: str, expense: ExpenseExtraction
    ) -> DraftRecord:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO drafts (
                    telegram_user_id, source_text, expense_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (telegram_user_id, source_text, expense.model_dump_json(), now, now),
            )
            draft_id = int(cursor.lastrowid)
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: int) -> DraftRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise DraftNotPendingError("Draft does not exist.")
        return _draft_from_row(row)

    def update_draft(
        self,
        draft_id: int,
        telegram_user_id: int,
        source_text: str,
        expense: ExpenseExtraction,
    ) -> DraftRecord:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE drafts
                SET source_text = ?, expense_json = ?, updated_at = ?
                WHERE id = ? AND telegram_user_id = ? AND status = 'pending'
                """,
                (source_text, expense.model_dump_json(), now, draft_id, telegram_user_id),
            )
            if cursor.rowcount != 1:
                raise DraftNotPendingError("Draft is not pending or does not belong to this user.")
        return self.get_draft(draft_id)

    def cancel_draft(self, draft_id: int, telegram_user_id: int) -> DraftRecord:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE drafts SET status = 'cancelled', updated_at = ?
                WHERE id = ? AND telegram_user_id = ? AND status = 'pending'
                """,
                (now, draft_id, telegram_user_id),
            )
            if cursor.rowcount != 1:
                raise DraftNotPendingError("Draft is not pending or does not belong to this user.")
        return self.get_draft(draft_id)

    def confirm_draft(self, draft_id: int, telegram_user_id: int) -> TransactionRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM drafts
                WHERE id = ? AND telegram_user_id = ? AND status = 'pending'
                """,
                (draft_id, telegram_user_id),
            ).fetchone()
            if row is None:
                raise DraftNotPendingError("Draft is not pending or does not belong to this user.")

            now = _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    draft_id, telegram_user_id, source_text, expense_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (draft_id, telegram_user_id, row["source_text"], row["expense_json"], now),
            )
            connection.execute(
                "UPDATE drafts SET status = 'confirmed', updated_at = ? WHERE id = ?",
                (now, draft_id),
            )
            connection.commit()
            transaction_id = int(cursor.lastrowid)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return self.get_transaction(transaction_id)

    def get_transaction(self, transaction_id: int) -> TransactionRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Transaction does not exist.")
        return _transaction_from_row(row)

    def recent_transactions(
        self, telegram_user_id: int, limit: int = 10
    ) -> list[TransactionRecord]:
        safe_limit = max(1, min(limit, 50))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM transactions
                WHERE telegram_user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (telegram_user_id, safe_limit),
            ).fetchall()
        return [_transaction_from_row(row) for row in rows]

    def count_transactions(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM transactions").fetchone()
        return int(row["count"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _draft_from_row(row: sqlite3.Row) -> DraftRecord:
    return DraftRecord(
        id=int(row["id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        source_text=str(row["source_text"]),
        expense=ExpenseExtraction.model_validate_json(row["expense_json"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _transaction_from_row(row: sqlite3.Row) -> TransactionRecord:
    return TransactionRecord(
        id=int(row["id"]),
        draft_id=int(row["draft_id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        source_text=str(row["source_text"]),
        expense=ExpenseExtraction.model_validate_json(row["expense_json"]),
        created_at=str(row["created_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
