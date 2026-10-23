# 검토 세션과 명세서 출처를 보존하며 중복 없는 대조 및 개인 지출 귀속을 저장합니다.
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime

from pydantic import TypeAdapter

from .database import FinanceRepository, RecordConflictError
from .models import ExpenseCategory, ExpenseExtraction


class PreviewStore:
    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def put(self, session: str, data: dict) -> str:
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[0] > time.monotonic()}
            # Bound parsed metadata in memory; never store PDF bytes.
            if len(self._items) >= 8:
                self._items.pop(next(iter(self._items)))
            token = secrets.token_urlsafe(32)
            self._items[token] = (time.monotonic() + 1800, session, copy.deepcopy(data))
            return token

    def get(self, token: str, session: str) -> dict:
        with self._lock:
            value = self._items.get(token) if isinstance(token, str) else None
            if not value or value[0] <= time.monotonic() or not secrets.compare_digest(value[1], session):
                raise RecordConflictError('预览已过期，请重新上传后核对。')
            return copy.deepcopy(value[2])


def prepare_links(repo: FinanceRepository) -> None:
    with closing(repo._connect()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_links'").fetchone():
            return
        backup_dir = repo._path.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / (repo._path.stem + '-before-statement-links-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f') + '.db')
        with closing(sqlite3.connect(backup_path)) as backup:
            conn.backup(backup)
        backup_path.chmod(0o600)
        conn.execute('''CREATE TABLE IF NOT EXISTS statement_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            row_key TEXT NOT NULL,
            transaction_id INTEGER NOT NULL REFERENCES transactions(id),
            action TEXT NOT NULL CHECK(action IN ('match','import')),
            source_sha256 TEXT NOT NULL,
            statement_date TEXT NOT NULL,
            statement_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(telegram_user_id,row_key),
            UNIQUE(telegram_user_id,transaction_id)
        )''')
        conn.commit()


def identify_rows(preview: dict, source: bytes) -> None:
    preview['source_sha256'] = hashlib.sha256(source).hexdigest()
    occurrences = Counter()
    for row in preview['rows']:
        # Page order and statement filename are not identities. Keep repeated identical purchases distinct.
        facts = {key: row.get(key) for key in ('card','posting_date','transaction_date','merchant','amount','currency','kind','signed_amount')}
        canonical = json.dumps(facts, sort_keys=True, ensure_ascii=False)
        occurrences[canonical] += 1
        row['row_key'] = hashlib.sha256((canonical + '\n' + str(occurrences[canonical])).encode()).hexdigest()


def saved_links(conn, owner: int) -> dict:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_links'").fetchone():
        return {}
    return {row['row_key']: dict(row) for row in conn.execute('''
        SELECT s.row_key,s.transaction_id,s.action,t.expense_json
        FROM statement_links s JOIN transactions t ON t.id=s.transaction_id
        WHERE s.telegram_user_id=?''', (owner,))}


def confirm_row(repo: FinanceRepository, owner: int, preview: dict, body: dict) -> dict:
    row = next((r for r in preview['rows'] if r['row_key'] == body.get('row_key')), None)
    if not row or row['kind'] != 'charge' or preview['warnings'] or not all(c['balance_ok'] for c in preview['cards']):
        raise ValueError('只能确认余额校验通过的消费行；还款和贷记暂不支持补录。')
    if type(body.get('is_personal')) is not bool:
        raise ValueError('请明确是否计入个人支出。')
    category = TypeAdapter(ExpenseCategory).validate_python(body.get('category'))
    if category in {'transfer', 'cash_withdrawal'}:
        raise ValueError('转账与取现不是消费，请保留该行待后续核对。')
    action = body.get('action')
    if action not in {'match', 'import'}:
        raise ValueError('请选择匹配已有记录或补录。')
    with closing(repo._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT transaction_id FROM statement_links WHERE telegram_user_id=? AND row_key=?',
            (owner, row['row_key'])).fetchone()
        if existing:
            raise RecordConflictError('这笔账单明细已处理，请重新预览；不会重复入账。')
        now = datetime.now(UTC).isoformat(timespec='seconds')
        if action == 'match':
            candidate = next((c for c in row['candidates'] if c['id'] == body.get('transaction_id')), None)
            if not candidate or body.get('revision') != candidate['revision']:
                raise RecordConflictError('候选不在这次预览中，请重新上传核对。')
            target = conn.execute('SELECT * FROM transactions WHERE id=? AND telegram_user_id=?',
                (candidate['id'], owner)).fetchone()
            repo._check_revision(target, candidate['revision'])
            expense = ExpenseExtraction.model_validate_json(target['expense_json'])
            if expense.amount != row_amount(row) or expense.currency != row['currency']:
                raise RecordConflictError('记录金额或币种已改变，请重新核对。')
            if conn.execute('SELECT 1 FROM statement_links WHERE telegram_user_id=? AND transaction_id=?',
                    (owner, target['id'])).fetchone():
                raise RecordConflictError('这笔记账已关联另一条账单明细，不能重复匹配。')
            expense.is_personal, expense.category = body['is_personal'], category
            updated = expense.model_dump_json()
            conn.execute('UPDATE transactions SET expense_json=? WHERE id=?', (updated, target['id']))
            conn.execute('''INSERT INTO ledger_edits(record_type,record_id,telegram_user_id,previous_json,updated_json,created_at)
                VALUES('transactions',?,?,?,?,?)''', (target['id'], owner, target['expense_json'], updated, now))
            transaction_id = target['id']
        else:
            # Re-check against current ledger in the write transaction, including new Telegram records.
            from .statements import eligible_match
            possible = [dict(r) for r in conn.execute('SELECT id,expense_json FROM transactions WHERE telegram_user_id=?', (owner,))
                if eligible_match(row, json.loads(r['expense_json']))]
            if possible and body.get('acknowledge_possible_duplicate') is not True:
                raise RecordConflictError('已有可能重复的记录，请重新预览并明确确认这是另一笔消费。')
            expense = ExpenseExtraction(transaction_date=row['transaction_date'], amount=row['amount'], currency=row['currency'],
                merchant=row['merchant'], payment_account=row['card'], category=category, is_personal=body['is_personal'],
                confidence=1, needs_review=False, ambiguities=[])
            source_text = f"UOB SG statement {preview['statement_date']} · {row['card']} · {row['merchant']}"
            draft = conn.execute('''INSERT INTO drafts(telegram_user_id,source_text,expense_json,status,created_at,updated_at)
                VALUES(?,?,?,'confirmed',?,?)''', (owner, source_text, expense.model_dump_json(), now, now))
            transaction = conn.execute('''INSERT INTO transactions(draft_id,telegram_user_id,source_text,expense_json,created_at)
                VALUES(?,?,?,?,?)''', (draft.lastrowid, owner, source_text, expense.model_dump_json(), now))
            transaction_id = transaction.lastrowid
        # Store the bank facts, not transient candidate copies or a raw PDF.
        bank_row = {k: v for k, v in row.items() if k not in {'candidates','candidate_count','processed'}}
        conn.execute('''INSERT INTO statement_links(telegram_user_id,row_key,transaction_id,action,source_sha256,statement_date,statement_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)''', (owner, row['row_key'], transaction_id, action, preview['source_sha256'],
                preview['statement_date'], json.dumps(bank_row, ensure_ascii=False), now))
    return {'ok': True, 'transaction_id': transaction_id, 'is_personal': expense.is_personal, 'action': action}


def row_amount(row):
    from decimal import Decimal
    return Decimal(row['amount'])
