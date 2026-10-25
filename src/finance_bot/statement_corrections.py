# 잘못된 명세서 연결과 보충 입력을 감사 이력을 보존하며 취소합니다.
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime

from .database import RecordConflictError
from .statement_reviews import check_version, touch_review


def prepare_corrections(repo):
    with closing(repo._connect()) as conn:
        if all(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone()
               for table in ('statement_corrections','voided_transactions')):
            return
        folder=repo._path.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(repo._path.stem+'-before-statement-corrections-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.db')
        with closing(sqlite3.connect(path)) as backup:conn.backup(backup)
        path.chmod(0o600)
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS statement_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_user_id INTEGER NOT NULL,
                row_key TEXT NOT NULL, transaction_id INTEGER NOT NULL REFERENCES transactions(id),
                operation TEXT NOT NULL CHECK(operation IN ('unlink','void_import')),
                link_json TEXT NOT NULL, expense_json TEXT NOT NULL, created_at TEXT NOT NULL
            )''')
            conn.execute('''CREATE INDEX IF NOT EXISTS idx_statement_corrections_owner
                ON statement_corrections(telegram_user_id,row_key,id)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS voided_transactions (
                transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id),
                telegram_user_id INTEGER NOT NULL,
                correction_id INTEGER NOT NULL REFERENCES statement_corrections(id),
                created_at TEXT NOT NULL
            )''')


def correction_history(conn, owner):
    history=defaultdict(list)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='statement_corrections'").fetchone():
        return history
    for record in conn.execute('''SELECT id,row_key,transaction_id,operation,created_at
            FROM statement_corrections WHERE telegram_user_id=? ORDER BY id DESC''',(owner,)):
        history[record['row_key']].append(dict(record))
    return history


def correct_row(repo, owner, preview, body):
    row=next((r for r in preview['rows'] if r['row_key']==body.get('row_key')),None)
    if not row or not row.get('processed'):
        raise ValueError('请选择已保存的账单明细。')
    processed=row['processed']
    operation=body.get('operation')
    if operation != ('unlink' if processed['action']=='match' else 'void_import'):
        raise ValueError('更正方式与原入账方式不一致。')
    if any(type(body.get(key)) is not int for key in ('transaction_id','link_id')):
        raise ValueError('请选择当前关联的记录。')
    if not isinstance(body.get('revision'),str) or len(body['revision'])!=64:
        raise ValueError('记录版本无效，请重新打开账单。')
    with closing(repo._connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        check_version(conn,owner,preview)
        link=conn.execute('SELECT * FROM statement_links WHERE telegram_user_id=? AND row_key=?',
            (owner,row['row_key'])).fetchone()
        if (not link or link['id']!=processed['link_id'] or link['id']!=body['link_id']
                or link['transaction_id']!=processed['transaction_id'] or link['transaction_id']!=body['transaction_id']):
            raise RecordConflictError('这笔关联已改变，请重新打开账单后核对。')
        transaction=conn.execute('SELECT * FROM transactions WHERE id=? AND telegram_user_id=?',
            (link['transaction_id'],owner)).fetchone()
        repo._check_revision(transaction,body.get('revision'))
        if body.get('revision')!=processed['revision']:
            raise RecordConflictError('记录已改变，请重新打开账单后核对。')
        if link['action']!=processed['action']:
            raise RecordConflictError('原入账方式已改变，请重新打开账单。')
        stamp=datetime.now(UTC).isoformat(timespec='microseconds')
        audit=conn.execute('''INSERT INTO statement_corrections
            (telegram_user_id,row_key,transaction_id,operation,link_json,expense_json,created_at)
            VALUES(?,?,?,?,?,?,?)''',(owner,row['row_key'],transaction['id'],operation,
                json.dumps(dict(link),ensure_ascii=False),transaction['expense_json'],stamp))
        if operation=='void_import':
            conn.execute('INSERT INTO voided_transactions VALUES(?,?,?,?)',
                (transaction['id'],owner,audit.lastrowid,stamp))
        conn.execute('DELETE FROM statement_links WHERE id=? AND telegram_user_id=?',(link['id'],owner))
        touch_review(conn,owner,preview)
    return {'ok':True,'operation':operation,'transaction_id':transaction['id'],'correction_id':audit.lastrowid}
