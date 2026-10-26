# 검토 세션과 명세서 출처를 보존하며 중복 없는 대조 및 개인 지출 귀속을 저장합니다.
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import TypeAdapter

from .database import FinanceRepository, RecordConflictError, active_transaction_filter
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
                raise RecordConflictError('核对会话已过期，请从账单列表重新打开；已保存的进度不会丢失。')
            return copy.deepcopy(value[2])


def prepare_links(repo: FinanceRepository) -> None:
    with closing(repo._connect()) as conn:
        links_exist = conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_links'").fetchone()
        if links_exist and conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_card_defaults'").fetchone():
            return
        backup_dir = repo._path.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / (repo._path.stem + ('-before-statement-batch-' if links_exist else '-before-statement-links-') + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f') + '.db')
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
        conn.execute('''CREATE TABLE IF NOT EXISTS statement_card_defaults (
            telegram_user_id INTEGER NOT NULL,
            card TEXT NOT NULL,
            is_personal INTEGER NOT NULL CHECK(is_personal IN (0,1)),
            PRIMARY KEY(telegram_user_id,card)
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
        SELECT s.id AS link_id,s.row_key,s.transaction_id,s.action,s.statement_json,t.expense_json,t.source_text,t.lifecycle_version
        FROM statement_links s JOIN transactions t ON t.id=s.transaction_id
        WHERE s.telegram_user_id=? AND ''' + active_transaction_filter(conn,'t'), (owner,))}


def save_card_default(repo: FinanceRepository, owner: int, preview: dict, body: dict) -> None:
    card = body.get('card')
    if not isinstance(card,str) or card not in {c['account'] for c in preview['cards']} or type(body.get('is_personal')) is not bool:
        raise ValueError('请选择这份账单中的卡片及个人支出默认值。')
    with closing(repo._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        from .statement_reviews import check_version
        check_version(conn, owner, preview)
        conn.execute('''INSERT INTO statement_card_defaults VALUES(?,?,?)
            ON CONFLICT(telegram_user_id,card) DO UPDATE SET is_personal=excluded.is_personal''',
            (owner, card, int(body['is_personal'])))


def confirm_row(repo: FinanceRepository, owner: int, preview: dict, body: dict) -> dict:
    return confirm_rows(repo, owner, preview, [body])['results'][0]


def confirm_rows(repo: FinanceRepository, owner: int, preview: dict, bodies: list[dict]) -> dict:
    if not isinstance(bodies, list) or not 1 <= len(bodies) <= 1000 or any(not isinstance(b, dict) for b in bodies):
        raise ValueError('请选取 1–1000 笔明细。')
    keys = [b.get('row_key') for b in bodies]
    if any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('同一批次不能重复选择同一明细。')
    with closing(repo._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        from .statement_reviews import check_version, touch_review
        check_version(conn, owner, preview)
        expense_index = defaultdict(list)
        for record in conn.execute('SELECT expense_json FROM transactions WHERE telegram_user_id=? AND '+active_transaction_filter(conn), (owner,)):
            expense = json.loads(record['expense_json'])
            expense_index[(expense['currency'], Decimal(expense['amount']))].append(expense)
        results = [_confirm_row(conn, repo, owner, preview, body, expense_index) for body in bodies]
        touch_review(conn, owner, preview)
    return {'ok': True, 'results': results, 'count': len(results)}


def _confirm_row(conn, repo: FinanceRepository, owner: int, preview: dict, body: dict, expense_index: dict) -> dict:
    row = next((r for r in preview['rows'] if r['row_key'] == body.get('row_key')), None)
    if not row or row['kind'] != 'charge' or preview['warnings'] or not all(c['balance_ok'] for c in preview['cards']):
        raise ValueError('只能确认余额校验通过的消费行；还款和贷记暂不支持补录。')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_corrections'").fetchone():
        latest=conn.execute('SELECT id FROM statement_corrections WHERE telegram_user_id=? AND row_key=? ORDER BY id DESC LIMIT 1',
            (owner,row['row_key'])).fetchone()
        if latest and (row.get('latest_correction_id')!=latest['id'] or body.get('acknowledge_correction') is not True):
            raise RecordConflictError('这笔明细曾更正，请重新打开账单并明确重新核对后再入账。')
    if type(body.get('is_personal')) is not bool:
        raise ValueError('请明确是否计入个人支出。')
    category = TypeAdapter(ExpenseCategory).validate_python(body.get('category'))
    if category in {'transfer', 'cash_withdrawal'}:
        raise ValueError('转账与取现不是消费，请保留该行待后续核对。')
    action = body.get('action')
    if action not in {'match', 'import'}:
        raise ValueError('请选择匹配已有记录或补录。')
    existing = conn.execute('SELECT transaction_id FROM statement_links WHERE telegram_user_id=? AND row_key=?',
        (owner, row['row_key'])).fetchone()
    if existing:
        raise RecordConflictError('这笔账单明细已处理，请重新预览；不会重复入账。')
    now = datetime.now(UTC).isoformat(timespec='seconds')
    if action == 'match':
        if type(body.get('transaction_id')) is not int:
            raise ValueError('匹配记录编号无效。')
        candidate = next((c for c in row['candidates'] if c['id'] == body.get('transaction_id')), None)
        if not candidate or body.get('revision') != candidate['revision']:
            raise RecordConflictError('候选不在这次预览中，请重新上传核对。')
        target = conn.execute('SELECT * FROM transactions WHERE id=? AND telegram_user_id=? AND '+active_transaction_filter(conn),
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
        from .statements import eligible_match
        possible = any(eligible_match(row, expense) for expense in expense_index[(row['currency'], row_amount(row))])
        if (possible or row.get('intra_statement_duplicate')) and body.get('acknowledge_possible_duplicate') is not True:
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
    bank_row = {k:row[k] for k in ('id','card','posting_date','transaction_date','merchant','amount','currency',
        'kind','signed_amount','page','row_key','original_currency','original_amount') if k in row}
    conn.execute('''INSERT INTO statement_links(telegram_user_id,row_key,transaction_id,action,source_sha256,statement_date,statement_json,created_at)
        VALUES(?,?,?,?,?,?,?,?)''', (owner, row['row_key'], transaction_id, action, preview['source_sha256'],
            preview['statement_date'], json.dumps(bank_row, ensure_ascii=False), now))
    return {'ok': True, 'transaction_id': transaction_id, 'is_personal': expense.is_personal, 'action': action}


def row_amount(row):
    return Decimal(row['amount'])
