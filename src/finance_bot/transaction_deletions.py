# 거래를 복구 가능한 휴지통으로 옮기고 UOB 가져오기 철회 범위를 검증합니다.
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from .database import RecordConflictError, active_transaction_filter, record_revision


def backup_ledger(repo, label):
    folder=repo._path.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(repo._path.stem+'-'+label+'-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.db')
    with closing(repo._connect()) as conn, closing(sqlite3.connect(path)) as backup:
        conn.backup(backup)
    path.chmod(0o600)
    return path


def prepare_deletions(repo):
    with closing(repo._connect()) as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns={r[1] for r in conn.execute('PRAGMA table_info(transactions)')}
    if {'deleted_transactions','transaction_deletions'}<=tables and 'lifecycle_version' in columns:return
    backup_ledger(repo,'before-recoverable-delete')
    with closing(repo._connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        if 'lifecycle_version' not in columns:
            conn.execute('ALTER TABLE transactions ADD COLUMN lifecycle_version INTEGER NOT NULL DEFAULT 0')
        conn.execute('''CREATE TABLE IF NOT EXISTS transaction_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_user_id INTEGER NOT NULL,
            transaction_id INTEGER NOT NULL REFERENCES transactions(id),
            action TEXT NOT NULL CHECK(action IN ('delete','restore')),
            reason TEXT NOT NULL,expense_json TEXT NOT NULL,created_at TEXT NOT NULL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS deleted_transactions (
            transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id),telegram_user_id INTEGER NOT NULL,
            deletion_id INTEGER NOT NULL REFERENCES transaction_deletions(id),
            reason TEXT NOT NULL,deleted_at TEXT NOT NULL
        )''')


def _delete(conn, owner, row, reason):
    stamp=datetime.now(UTC).isoformat(timespec='microseconds')
    audit=conn.execute('''INSERT INTO transaction_deletions
        (telegram_user_id,transaction_id,action,reason,expense_json,created_at) VALUES(?,?,'delete',?,?,?)''',
        (owner,row['id'],reason,row['expense_json'],stamp))
    conn.execute('INSERT INTO deleted_transactions VALUES(?,?,?,?,?)',(row['id'],owner,audit.lastrowid,reason,stamp))
    conn.execute('UPDATE transactions SET lifecycle_version=lifecycle_version+1 WHERE id=? AND telegram_user_id=?',(row['id'],owner))


def change_deletion(repo, owner, transaction_id, revision, action):
    if action not in {'delete','restore'} or type(transaction_id) is not int:
        raise ValueError('操作无效。')
    with closing(repo._connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM transactions WHERE id=? AND telegram_user_id=?',(transaction_id,owner)).fetchone()
        if not row or not isinstance(revision,str) or revision!=record_revision(row):
            raise RecordConflictError('记录已更新，请关闭并刷新后重试。')
        if action=='delete':
            if not conn.execute('SELECT 1 FROM transactions WHERE id=? AND '+active_transaction_filter(conn),(transaction_id,)).fetchone():
                raise RecordConflictError('记录已删除或撤销，请刷新。')
            _delete(conn,owner,row,'user_delete')
        else:
            deleted=conn.execute('SELECT * FROM deleted_transactions WHERE transaction_id=? AND telegram_user_id=?',(transaction_id,owner)).fetchone()
            voided=conn.execute("SELECT 1 FROM sqlite_master WHERE name='voided_transactions'").fetchone() and conn.execute(
                'SELECT 1 FROM voided_transactions WHERE transaction_id=?',(transaction_id,)).fetchone()
            if not deleted or voided:raise RecordConflictError('记录已恢复或已撤销，不能重复恢复。')
            conn.execute('''INSERT INTO transaction_deletions
                (telegram_user_id,transaction_id,action,reason,expense_json,created_at) VALUES(?,?,'restore',?,?,?)''',
                (owner,transaction_id,deleted['reason'],row['expense_json'],datetime.now(UTC).isoformat(timespec='microseconds')))
            conn.execute('DELETE FROM deleted_transactions WHERE transaction_id=? AND telegram_user_id=?',(transaction_id,owner))
            conn.execute('UPDATE transactions SET lifecycle_version=lifecycle_version+1 WHERE id=? AND telegram_user_id=?',(transaction_id,owner))
    return {'ok':True,'action':action,'transaction_id':transaction_id}


def uob_import_plan(repo, owner):
    with closing(repo._connect()) as conn:
        return _uob_import_plan(conn,owner)


def _uob_import_plan(conn, owner):
    return {row['id']:record_revision(row) for row in conn.execute('''SELECT t.* FROM transactions t
        JOIN statement_links s ON s.transaction_id=t.id AND s.telegram_user_id=t.telegram_user_id
        WHERE t.telegram_user_id=? AND s.action='import' AND t.source_text LIKE 'UOB SG statement %'
        AND '''+active_transaction_filter(conn,'t'),(owner,))}


def withdraw_uob_imports(repo, owner, expected):
    # Caller must take a backup and inspect this owner-scoped plan before invoking the mutation.
    with closing(repo._connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        actual=_uob_import_plan(conn,owner)
        if actual!=expected:raise RecordConflictError('UOB记录范围已改变，未进行清理，请重新检查。')
        for transaction_id in actual:
            row=conn.execute('SELECT * FROM transactions WHERE id=? AND telegram_user_id=?',(transaction_id,owner)).fetchone()
            _delete(conn,owner,row,'uob_reset')
    return len(actual)
