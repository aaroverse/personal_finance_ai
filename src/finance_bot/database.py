# SQLite에서 검토 초안과 확정 거래를 원자적으로 저장합니다.

import sqlite3
import hashlib
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import ExpenseExtraction


class DraftNotPendingError(RuntimeError):
    """Raised when a closed or missing draft is changed."""


class RecordConflictError(DraftNotPendingError):
    """A browser action was based on an older version of the record."""


def record_revision(row: sqlite3.Row | dict) -> str:
    status = row["status"] if "status" in row.keys() else "confirmed"
    value = row["expense_json"] + "\n" + row["source_text"] + "\n" + status
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class DraftRecord:
    id: int
    telegram_user_id: int
    source_text: str
    expense: ExpenseExtraction
    status: str
    created_at: str
    updated_at: str
    stored_revision: str | None = None

    @property
    def revision(self) -> str:
        return self.stored_revision or record_revision({"expense_json": self.expense.model_dump_json(), "source_text": self.source_text, "status": self.status})


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

    def prepare_web_edits(self) -> None:
        """Back up the existing ledger before adding the backwards-compatible audit table."""
        with closing(self._connect()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_edits'"
            ).fetchone()
            if exists:
                return
            backup_dir = self._path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            backup_path = backup_dir / f"{self._path.stem}-before-web-edits-{stamp}.db"
            with closing(sqlite3.connect(backup_path)) as backup:
                connection.backup(backup)
            backup_path.chmod(0o600)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS ledger_edits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type TEXT NOT NULL CHECK(record_type IN ('drafts','transactions')),
                    record_id INTEGER NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    previous_json TEXT NOT NULL,
                    updated_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.commit()

    def edit_record(
        self, record_type: str, record_id: int, owner: int,
        expense: ExpenseExtraction, revision: str,
    ) -> str:
        if record_type not in {"drafts", "transactions"}:
            raise ValueError("Invalid record type")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT * FROM {record_type} WHERE id=? AND telegram_user_id=?",
                (record_id, owner),
            ).fetchone()
            self._check_revision(row, revision)
            updated = expense.model_dump_json()
            if record_type == 'transactions' and connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='statement_links'"
            ).fetchone() and connection.execute(
                'SELECT 1 FROM statement_links WHERE telegram_user_id=? AND transaction_id=?', (owner, record_id)
            ).fetchone():
                previous = ExpenseExtraction.model_validate_json(row['expense_json'])
                if any(getattr(previous, key) != getattr(expense, key) for key in (
                    'amount', 'currency', 'transaction_date', 'payment_account'
                )):
                    raise RecordConflictError('Matched statement facts are locked.')
            connection.execute(
                f"UPDATE {record_type} SET expense_json=? WHERE id=? AND telegram_user_id=?",
                (updated, record_id, owner),
            )
            if record_type == "drafts":
                connection.execute("UPDATE drafts SET updated_at=? WHERE id=?", (_utc_now(), record_id))
            connection.execute(
                "INSERT INTO ledger_edits(record_type,record_id,telegram_user_id,previous_json,updated_json,created_at) VALUES(?,?,?,?,?,?)",
                (record_type, record_id, owner, row["expense_json"], updated, _utc_now()),
            )
            row = connection.execute(f"SELECT * FROM {record_type} WHERE id=?", (record_id,)).fetchone()
            return record_revision(row)

    @staticmethod
    def _check_revision(row: sqlite3.Row | None, revision: str | None) -> None:
        if row is None or ("status" in row.keys() and row["status"] != "pending"):
            raise DraftNotPendingError("Record is unavailable or already processed.")
        if revision is not None and (len(revision) not in {16, 64} or record_revision(row)[:len(revision)] != revision):
            raise RecordConflictError("Record changed since it was displayed.")

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
        revision: str | None = None,
    ) -> DraftRecord:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM drafts WHERE id=? AND telegram_user_id=?", (draft_id, telegram_user_id)
            ).fetchone()
            self._check_revision(row, revision)
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

    def cancel_draft(self, draft_id: int, telegram_user_id: int, revision: str | None = None) -> DraftRecord:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM drafts WHERE id=? AND telegram_user_id=?", (draft_id, telegram_user_id)
            ).fetchone()
            self._check_revision(row, revision)
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

    def confirm_draft(self, draft_id: int, telegram_user_id: int, revision: str | None = None) -> TransactionRecord:
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
            self._check_revision(row, revision)

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
        stored_revision=record_revision(row),
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
